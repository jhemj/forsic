"""Case tools preserve originals; Hermes owns the agent, sessions and retries."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import time
import uuid


class Case:
    def __init__(self, manifest, *, read_only=False):
        self.read_only = read_only
        self.manifest = Path(manifest).resolve(strict=True)
        self.config = json.loads(self.manifest.read_text())
        from .tenants import current_scope, validate_case
        scope = current_scope()
        if scope:
            validate_case(scope, self.manifest)
        self.root = Path(self.config["evidence_root"]).resolve(strict=True)
        self.output = Path(self.config["output_root"]).resolve()
        if self.output == self.root or self.output.is_relative_to(self.root):
            raise ValueError("Results must be outside the evidence directory")
        self.db = self.output / "activity.sqlite3"
        if read_only:
            if not self.db.is_file():
                raise ValueError('The existing case ledger is unavailable')
        else:
            self.output.mkdir(parents=True, exist_ok=True)
            with self.connect() as db:
                db.execute("CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, time REAL, kind TEXT, session TEXT, data TEXT)")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.db.as_uri() + '?mode=ro', uri=True, timeout=15) if self.read_only else sqlite3.connect(self.db, timeout=15)
        db.row_factory = sqlite3.Row
        if self.read_only:
            db.execute('PRAGMA query_only=ON')
        try:
            # sqlite's transaction context commits/rolls back, but does not close.
            with db:
                yield db
        finally:
            db.close()

    def record(self, kind, data, session=""):
        eid = uuid.uuid4().hex
        with self.connect() as db:
            db.execute("INSERT INTO events VALUES (?,?,?,?,?)", (eid, time.time(), kind, session, json.dumps(data, ensure_ascii=False)))
        return eid

    def events(self, limit=40):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM events ORDER BY time DESC LIMIT ?", (limit,)).fetchall()
        return [{**dict(row), "data": json.loads(row["data"])} for row in rows]

    def event(self, eid):
        with self.connect() as db:
            row = db.execute("SELECT * FROM events WHERE id=?", (eid,)).fetchone()
        return {**dict(row), "data": json.loads(row["data"])} if row else None

    def path(self, name):
        path = (self.root / name).resolve()
        if not path.is_relative_to(self.root):
            if path == self.output or path.is_relative_to(self.output):
                raise ValueError('Generated reports are outside the evidence root. Use forsic_reporting(action=state) for current answers and the report viewer for rendered files; legacy forsic_report(action=list) lists old artifacts only.')
            raise ValueError("Path is outside the selected evidence root")
        selected = self.config.get('selected_files', [])
        if selected and path != self.root and str(path.relative_to(self.root)) not in selected:
            raise ValueError('This file was not included in the user-selected evidence')
        return path.resolve(strict=True)

    def _files(self, directory="."):
        base = self.path(directory)
        selected = self.config.get('selected_files', [])
        if base.is_file():
            yield base
            return
        if selected:
            for name in selected:
                yield self.path(name)
            return
        for root, dirs, files in os.walk(base, followlinks=False):
            dirs[:] = sorted(d for d in dirs if not (Path(root) / d).is_symlink())
            for name in sorted(files):
                path = Path(root) / name
                if path.is_file() and not path.is_symlink():
                    yield path

    def info(self, args):
        from .shell_workspace import workspace_info
        return {**{k: self.config.get(k) for k in ("case_id", "label", "question", "evidence_kind", "scope", "synthetic")},
                'evidence_root': str(self.root), 'selected_files': self.config.get('selected_files', []),
                'output_root': str(self.output),
                'shell_workspace': workspace_info(),
                'capabilities': {'allocated_image_files': True, 'image_text_search': True, 'container_shell':True,
                                 'image_archive_inspection': ['tar', 'tar.gz'],
                                 'ewf_verify': True, 'carving': True, 'deleted_recovery': True,
                                 'recovery_backend': 'forsic_shell; declared image capabilities, not a completed recovery',
                                 'recovery_reference': 'source-triage/references/deleted-recovery.md',
                                 'reports': ['executive', 'analyst']}}

    def _pending_shell(self, db, conversation_id):
        """All composed commands can mutate this workspace, even with new arguments."""
        from .execution_contract import ExecutionFailure, lookup_call
        starts=db.execute("SELECT id,data FROM events WHERE kind='tool_start' AND session=? "
                          "AND json_extract(data,'$.tool')='forsic_shell' ORDER BY rowid DESC", (conversation_id,))
        for row in starts:
            if json.loads(row['data']).get('execution_rejected'):continue
            returned=db.execute("SELECT data FROM events WHERE kind='tool_result' "
                                "AND json_extract(data,'$.started_id')=? ORDER BY rowid DESC LIMIT 1", (row['id'],)).fetchone()
            execution=json.loads(returned['data']).get('execution',{}) if returned else {}
            if (execution.get('state')=='not_started' or execution.get('state') in ('returned','failed','cancelled')
                    and execution.get('termination_confirmed') is True):
                continue
            raise ExecutionFailure('This conversation has unfinished or uncertain shell workspace work. Inspect its exact execution before another command.',
                error_code='execution_completion_unknown',pending_started_id=row['id'],execution_call=lookup_call(row['id']))

    def read_bytes(self, args):
        from .image_reader import byte_preview
        path = self.path(args['path'])
        offset = max(0, int(args.get('offset', 0)))
        with path.open('rb') as stream:
            stream.seek(offset)
            raw = stream.read(min(12000, max(1, int(args.get('length', 4096)))))
        preview = byte_preview(raw, offset, path.stat().st_size)
        return {'path': str(path.relative_to(self.root)), **preview, 'complete_file': preview['complete_content']}

    def verify(self, args):
        path = self.path(args['path'])
        if path.suffix.lower() != '.e01':
            raise ValueError('EWF verification requires the first E01 segment')
        from forsic_plugin.verification import verify
        return verify(self, path, args.get('action', 'start'))

    def note(self, args):
        from forsic_plugin.notes import note
        return note(self, args)

    def cases(self, args):
        from forsic_plugin.case_library import CaseLibrary
        return CaseLibrary(self).invoke(args)

    def list_files(self, args):
        from .image_reference import binding
        offset = max(0, int(args.get("offset", 0)))
        page, targets = [], []
        for i, path in enumerate(self._files(args.get("path", "."))):
            if i < offset:
                continue
            if len(page) == 100:
                return {"files": page, "next_offset": offset + 100, "complete": False, '_image_targets': targets}
            page.append({"path": str(path.relative_to(self.root)), "bytes": path.stat().st_size})
            if path.suffix.lower() == '.e01':
                try:
                    target = binding(self, path)
                except (OSError, ValueError) as exc:
                    page[-1]['image_selection_error'] = str(exc)
                    target = None
                if target is not None:targets.append(target)
        return {"files": page, "next_offset": None, "complete": True, '_image_targets': targets}

    def read(self, args):
        path = self.path(args["path"])
        start = max(1, int(args.get("start_line", 1)))
        count = min(160, max(1, int(args.get("line_count", 80))))
        rows, used, more = [], 0, False
        with path.open("rb") as stream:
            for line_no in range(1, start + count + 1):
                raw = stream.readline(16385)
                if not raw:
                    break
                if b"\x00" in raw:
                    raise ValueError("Binary content: use a format-specific forensic tool")
                if len(raw) > 16384 and not raw.endswith(b"\n"):
                    raise ValueError("Long record: use a format-specific parser; record was not truncated")
                if line_no < start:
                    continue
                if len(rows) >= count or used + len(raw) > 24000:
                    more = True
                    break
                rows.append({"line": line_no, "text": raw.decode("utf-8", errors="replace").rstrip("\r\n")})
                used += len(raw)
        return {"path": str(path.relative_to(self.root)), "lines": rows,
                "line_start": rows[0]['line'] if rows else None,
                "line_end": rows[-1]['line'] if rows else None,
                "next_line": start + len(rows) if more else None, "complete_file": start == 1 and not more,
                "encoding": "UTF-8; undecodable bytes shown as replacement characters"}

    def search(self, args):
        from forsic_plugin.literal_search import file_identity, search_entries
        base = self.path(args.get('path', '.'))
        selected = self.config.get('selected_files', [])

        def entries():
            pending = [base] if not selected or base.is_file() else [self.path(p) for p in reversed(sorted(selected))]
            while pending:
                path = pending.pop()
                name = str(path.relative_to(self.root))
                try:
                    if path.is_dir() and not path.is_symlink():
                        yield name, path.lstat, None, 'directory'
                        children = []
                        with os.scandir(path) as listing:
                            for item in listing:
                                if len(children) >= 10000:
                                    yield name, None, None, 'directory_entry_limit; narrow the path'
                                    break
                                children.append(Path(item.path))
                        pending.extend(reversed(sorted(children)))
                        continue
                except OSError as exc:
                    yield name, None, None, 'directory_' + type(exc).__name__
                    continue
                yield name, path.lstat, lambda p=path: p.open('rb'), None

        scope = {'root': str(self.root), 'path': str(base.relative_to(self.root)),
                 'selected_files': sorted(selected), 'identity': file_identity(base.lstat())}
        result = search_entries(entries(), args, scope)
        return {**result, 'search_path': args.get('path', '.'),
                'meaning': 'No match only describes the files and bytes searched, not absence of execution.'}

    def hash_file(self, args):
        path = self.path(args["path"])
        before = path.stat()
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while block := stream.read(1024 * 1024):
                digest.update(block)
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError("Evidence changed while hashing")
        return {"path": str(path.relative_to(self.root)), "sha256": digest.hexdigest(), "bytes": after.st_size,
                "meaning": "Calculated now; acquisition-time integrity requires comparison with a trusted original hash."}

    def image_info(self, args):
        path = self.path(args["path"])
        proc = subprocess.run(["ewfinfo", str(path)], capture_output=True, timeout=60, check=False)
        return {"path": str(path.relative_to(self.root)), "parser": "ewfinfo", "exit_code": proc.returncode,
                "stdout": proc.stdout.decode(errors="replace")[:24000],
                "stderr": proc.stderr.decode(errors="replace")[:2000], "is_integrity_verification": False}

    def image_files(self, args, *, archive_selection=None, native_execution=None):
        from .image_reader import ARCHIVE_ORIGIN_VERSION, archive_origin, image_segments, validate_image_action
        validate_image_action(args)
        from .image_reference import ImageSelectionError, binding
        try:
            path = self.path(args['path'])
        except (OSError, ValueError) as exc:
            raise ImageSelectionError('Image path is unavailable in the selected evidence. path selects the E01; file_path selects its internal file.', 'image_path_unavailable') from exc
        archive_action = args['action'] in ('archive_list', 'archive_read')
        host_origin = archive_origin(image_segments(path), args.get('volume_offset')) if archive_action else None
        if archive_selection is not None:
            if archive_selection.get('archive_origin_version') != ARCHIVE_ORIGIN_VERSION:
                raise ValueError('Archive listing lacks a host-bound origin; explicitly re-list before reading')
            if host_origin != archive_selection['archive_origin']:
                raise ValueError('Image segments or selected volume changed since listing; explicitly re-list before reading')
        image_binding = binding(self, path)
        if image_binding is None:
            raise ImageSelectionError('path must select an existing first E01 segment, not an internal image path or directory.', 'image_path_unavailable')
        from .shell_workspace import _execution_snapshot
        snapshot = _execution_snapshot(self)
        reader = Path(snapshot['helpers']['image_reader.py']['path'])
        search = Path(snapshot['helpers']['literal_search.py']['path'])
        container = 'forsic-read-' + uuid.uuid4().hex
        parser_args = {k: v for k, v in args.items() if not k.startswith('_')}
        if archive_selection is not None:
            parser_args['_archive_selection'] = archive_selection
        from .image_process import bind_mount
        command = ['docker', 'run', '--pull=never', '--name', container, '--network', 'none', '--read-only', '--cap-drop', 'ALL',
                   '--security-opt', 'no-new-privileges', '--user', f'{os.getuid()}:{os.getgid()}',
                   '--memory', '8g', '--pids-limit', '64',
                   '--mount', bind_mount(self.root, '/evidence', True),
                   '--mount', bind_mount(reader, '/reader.py', True),
                   '--mount', bind_mount(search, '/literal_search.py', True),
                   snapshot['images']['parser']['image_id'], 'python', '-B', '/reader.py', '/evidence/' + str(path.relative_to(self.root)), json.dumps(parser_args)]
        from .image_process import run_parser
        from .environments import EnvironmentManager
        with EnvironmentManager().execution(self,expected_snapshot=snapshot):
            data = run_parser(command, container, native_execution, cleanup_on_return=True)
        data['execution_policy'] = {'environment': snapshot, 'image_id': snapshot['images']['parser']['image_id']}
        data.pop('_image_targets', None)
        if archive_action:
            # Parser output cannot assert trusted host identity or its protocol.
            data.pop('archive_origin', None)
            data.pop('archive_origin_version', None)
            if host_origin != archive_origin(image_segments(path), args.get('volume_offset')):
                raise ValueError('Image segments changed during archive inspection; discard this result')
            if not data.get('error'):
                volume = data.get('volume_offset')
                if type(volume) is not int or args.get('volume_offset') is not None and volume != args['volume_offset']:
                    raise ValueError('Selected volume differs from the requested archive volume')
                data.update(archive_origin={**host_origin, 'volume_offset': volume},
                            archive_origin_version=ARCHIVE_ORIGIN_VERSION)
        if not data.get('error'):
            if binding(self, path) != image_binding:
                raise ValueError('Image file or segment metadata changed during inspection; discard this result')
            data['_image_targets'] = [image_binding]
        if data.get('error_type') == 'FileNotFoundError':
            data.update(outcome='not_found', meaning='Requested path was not found in this allocated filesystem view; deletion history is not established.')
        if 'times' in data:
            from forsic_plugin.notes import epoch_time
            data['times_kst'] = {k: epoch_time(v).strftime('%Y-%m-%d %H:%M:%S KST') for k, v in data['times'].items()}
        if 'lines' in data:
            data.update(line_start=data['lines'][0]['line'] if data['lines'] else None,
                        line_end=data['lines'][-1]['line'] if data['lines'] else None)
        return {'path': str(path.relative_to(self.root)), 'file_path': args.get('file_path', '/'),
                'volume_offset': args.get('volume_offset'), 'parser': 'dissect.target',
                'scope': 'Allocated filesystem entries only; no carving or deleted-file coverage.', **data}

    def report(self, args):
        if args.get('action') == 'review':
            from forsic_plugin.review import review
            return review(self, args)
        if args.get('action') == 'list':
            files = sorted(p for p in self.output.iterdir() if p.is_file() and not p.is_symlink()
                           and p.name.startswith(('report-', 'snapshot-'))
                           and p.suffix in ('.html', '.docx', '.json', '.md'))
            offset = max(0, int(args.get('offset', 0)))
            page = files[offset:offset + 100]
            return {'output_root': str(self.output), 'total': len(files),
                    'files': [{'name': p.name, 'path': str(p), 'bytes': p.stat().st_size} for p in page],
                    'next_offset': offset + 100 if offset + 100 < len(files) else None,
                    'meaning': 'Generated deliverables only, not original evidence or proof of analytical completeness.'}
        if 'markdown' not in args:
            from forsic_plugin.reports import render
            return render(self, args)
        citations = args.get("evidence_ids", [])
        if not citations:
            raise ValueError("A report requires at least one saved evidence result")
        from forsic_plugin.notes import sources
        results = sources(self, citations)
        name = "report-" + uuid.uuid4().hex + ".md"
        body = args["markdown"]
        if len(body) > 100000:
            raise ValueError("Report exceeds 100,000 characters")
        appendix = "\n\n## 확인에 사용한 원문 결과\n\n" + "\n".join(f"- [E:{event['id']}] · {event['data'].get('tool')} · {event['data'].get('path', '')}" for event in results)
        (self.output / name).write_text(body + appendix, encoding="utf-8")
        return {"report": name, "evidence_ids": citations, "meaning": "Saved model-authored report; citations resolve to recorded results, not independent analyst approval."}

    def invoke(self, tool, args, session="", *, request_context=None, tool_schemas=None, native_execution=None):
        if self.read_only:
            raise ValueError('Read-only case projections cannot invoke tools')
        request_context = request_context or {}
        submitted_tool = tool
        reference_entry = tool=='forsic_image_read_mission'
        direct_read_entry = tool=='forsic_image_read'
        direct_search_entry = tool=='forsic_image_search'
        archive_entry = tool=='forsic_archive_read'
        archive_relist_entry = tool=='forsic_archive_relist'
        archive_selection = None
        if reference_entry or direct_read_entry or direct_search_entry or archive_entry or archive_relist_entry:
            tool='forsic_image_files'
        conversation_id = request_context.get('conversation_id') or request_context.get('session_id') or session
        execution_session = conversation_id if tool=='forsic_shell' else session
        # Model-only linkage fields never become evidence-parser arguments.
        call_args = {k: v for k, v in args.items() if k not in ('mission_id', 'mission_version', 'retry_of') and not k.startswith('_')}
        if direct_search_entry:
            call_args['action'] = 'search'
        mission_context, admission_error, admission_details = None, None, {}
        reference = reference_entry or (tool=='forsic_image_files' and args.get('action')=='from_mission')
        argument_resolution = None
        supplied_mission = 'mission_id' in args or 'mission_version' in args
        writes_question = (tool == 'forsic_note' and args.get('action') == 'save') or (
            tool == 'forsic_reporting' and (args.get('action') in ('question', 'review_response', 'mission', 'mission_from_results', 'assess', 'assess_basis', 'assess_selected', 'assessment_basis', 'gap', 'requirement') or
                                            args.get('action') == 'compose' and 'draft' in args))
        if request_context.get('status') == 'unavailable' and (writes_question or supplied_mission or reference):
            admission_error = 'Native session/goal binding is unavailable; retry this state change only after that binding can be read. No unrelated goal was selected.'
        started = uuid.uuid4().hex
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                if native_execution and native_execution.interrupted():
                    from .execution_contract import ExecutionFailure
                    raise ExecutionFailure('Native cancellation was requested before admission.',
                        error_code='cancelled_before_start',
                        execution={'state':'not_started','termination_confirmed':True})
                if tool=='forsic_shell':
                    from .shell_workspace import schema as shell_schema, validate as validate_shell
                    from .report_driven.investigation_state import input_problems
                    from .image_reader import InvalidArguments
                    contract=shell_schema()
                    contract['properties'].update({k:{'type':'string'} for k in ('reason','mission_id','mission_version')})
                    problems=input_problems(args,contract,'arguments')
                    if problems:raise InvalidArguments(problems,contract)
                    try:
                        validate_shell({k:v for k,v in call_args.items() if k!='reason'})
                    except ValueError as exc:
                        raise InvalidArguments([{'path':'arguments','error':str(exc)}],contract) from exc
                    from .execution_contract import ExecutionFailure
                    if (request_context.get('status')!='ready' or not session or
                            request_context.get('requested_session_id')!=session or
                            request_context.get('case_id')!=self.config['case_id'] or
                            not request_context.get('conversation_id')):
                        raise ExecutionFailure('Current native case/conversation binding is unavailable or inconsistent. No shell command started.',
                            error_code='shell_binding_unavailable')
                    goal_status=request_context.get('goal_status')
                    if goal_status not in (None,'active') or request_context.get('goal_id') and goal_status!='active':
                        raise ExecutionFailure('The owning native goal is not active. A shell command cannot resume paused or unknown work.',
                            error_code='shell_goal_inactive')
                    self._pending_shell(db,conversation_id)
                if 'cursor_ref' in args:
                    from .search_selection import resolve
                    call_args, argument_resolution = resolve(self, tool, call_args, session, request_context)
                if archive_relist_entry:
                    from .archive_selection import resolve_relist
                    call_args, argument_resolution = resolve_relist(self, args, session, request_context)
                if archive_entry:
                    from .archive_selection import resolve
                    call_args, archive_selection, argument_resolution = resolve(self, args, session, request_context)
                if tool=='forsic_image_files' and not reference and not archive_entry and not archive_relist_entry:
                    from .image_reference import resolve as resolve_image
                    from .image_reader import validate_image_action
                    # Validate all ordinary fields together before path repair. A verified
                    # selection alone may supply a missing/blank outer image path.
                    if 'image_ref' not in call_args:
                        validate_image_action(call_args)
                    call_args, image_resolution = resolve_image(self, call_args,
                        allow_trim=not supplied_mission and 'cursor_ref' not in args and 'cursor' not in args)
                    if image_resolution:
                        argument_resolution = image_resolution
                if not admission_error and (reference_entry or direct_read_entry or direct_search_entry):
                    from .report_driven.investigation_state import image_read_reference_schema, input_problems
                    from .image_reader import InvalidArguments, image_read_entry_schema, image_search_entry_schema
                    entry_schema=image_read_reference_schema() if reference_entry else image_search_entry_schema(allow_image_ref=True) if direct_search_entry else image_read_entry_schema(allow_image_ref=True)
                    entry_args={**args,'path':call_args['path']} if not reference_entry and 'image_ref' in args else args
                    problems=input_problems(entry_args,entry_schema,'arguments')
                    if problems:
                        raise InvalidArguments(problems,entry_schema)
                if not admission_error and tool=='forsic_reporting' and args.get('action')=='mission':
                    from .report_driven.investigation_state import validate_native_plan
                    validate_native_plan(args.get('payload'),tool_schemas)
                    alias = args.get('payload',{}).get('tool_name')
                    if alias in ('forsic_image_read','forsic_image_search'):
                        payload = {**args['payload'],'tool_name':'forsic_image_files'}
                        if alias=='forsic_image_search':
                            payload['tool_arguments']={**payload['tool_arguments'],'action':'search'}
                        call_args={**call_args,'payload':payload}
                        argument_resolution={'kind':'public_tool_alias','submitted_tool_name':alias,'canonical_tool_name':'forsic_image_files'}
                if not admission_error and (supplied_mission or reference):
                    from .image_reader import InvalidArguments
                    pair=('mission_id','mission_version')
                    problems=[{'path':'arguments.'+key,'expected_type':'nonempty_string',
                               **({'missing_fields':[key]} if key not in args else {})}
                              for key in pair if not isinstance(args.get(key),str) or not args[key]]
                    if problems:
                        raise InvalidArguments(problems,{'required':list(pair),
                            'properties':{key:{'type':'string','minLength':1} for key in pair}})
                if not admission_error and args.get('retry_of') and (not reference_entry or request_context.get('goal_status')!='active'):
                    raise ValueError('Explicit read retry requires an active owning native goal and the mission reference entry; paused or unknown goals are not resumed')
                if not admission_error and reference:
                    from .report_driven.investigation_state import resolve_image_read_reference, hash_value, normalized_tool_arguments
                    call_args=resolve_image_read_reference(self,args,tool_schemas)
                    argument_resolution={'kind':'saved_mission','mission_id':args['mission_id'],
                        'mission_version':args['mission_version'],'arguments_hash':hash_value(normalized_tool_arguments(call_args))}
                if not admission_error and tool=='forsic_image_files':
                    from .image_reader import validate_image_action
                    validate_image_action(call_args)
                    from .execution_contract import pending_image_read
                    pending_image_read(db, call_args, session)
                if not admission_error and tool=='forsic_search':
                    from .literal_search import search_limit_problems, search_limit_schema
                    from .image_reader import InvalidArguments
                    problems=search_limit_problems(call_args)
                    query = call_args.get('text')
                    if not isinstance(query, str) or not query or len(query) > 512 or '\n' in query or '\r' in query:
                        problems.append({'path':'arguments.text','expected_type':'nonempty_single_line_literal','max_length':512})
                    if problems:
                        raise InvalidArguments(problems,{'required':['text'],'properties':{
                            **search_limit_schema(),'text':{'type':'string','minLength':1,'maxLength':512}}})
                if not admission_error and (supplied_mission or reference):
                    from .report_driven.investigation_state import validate_execution_context
                    mission_context = validate_execution_context(
                        self, args.get('mission_id'), args.get('mission_version'), tool, call_args,
                        goal_id=request_context.get('goal_id', ''), session_id=conversation_id, retry_of=args.get('retry_of'))
                    if args.get('retry_of'):
                        mission_context['retry_of']=args['retry_of']
            except (ValueError, KeyError, TypeError, StopIteration) as exc:
                admission_error = str(exc)
                admission_details = getattr(exc,'details',{})
                if tool=='forsic_image_files' and (args.get('path') is None or isinstance(args.get('path'),str) and not args['path'].strip(' \t\n\r\v\f')) and 'image_ref' not in args:
                    admission_details.update(recovery={'tool':'forsic_list','arguments':{'path':'.'}},
                        next_step='The outer image path is missing or blank. Select image_ref or copy the E01 path from the current evidence inventory; file_path names a file inside that image. No image backend started.')
            start_data = {"tool": tool, "reason": args.get("reason", ""),
                          "path": call_args.get("file_path") or call_args.get("path", ""),
                          "text": call_args.get("text", ""), 'arguments': call_args}
            if request_context.get('extension_dispatch'):
                start_data['extension'] = request_context['extension_dispatch']['identity']
            if submitted_tool != tool or reference or argument_resolution:
                start_data['submitted_tool']=submitted_tool
                start_data['submitted_arguments']=args
            if argument_resolution:
                start_data['argument_resolution']=argument_resolution
            if mission_context:
                start_data['mission_context'] = mission_context
            from .capabilities import capability
            start_data['capability_effects'] = capability(tool, call_args)
            if native_execution:
                start_data['native_call'] = native_execution.record()
            if admission_error:
                start_data['execution_rejected'] = True
                start_data['backend_started'] = False
            db.execute('INSERT INTO events VALUES (?,?,?,?,?)',
                       (started, time.time(), 'tool_start', execution_session, json.dumps(start_data, ensure_ascii=False)))
        handlers = {"forsic_case": self.info, "forsic_list": self.list_files, "forsic_read": self.read,
                    "forsic_search": self.search, "forsic_hash": self.hash_file,
                    "forsic_image_info": self.image_info, "forsic_report": self.report}
        handlers['forsic_image_files'] = self.image_files
        image_options = {}
        if archive_selection is not None:
            image_options['archive_selection'] = archive_selection
        if native_execution is not None:
            image_options['native_execution'] = native_execution
        if image_options:
            handlers['forsic_image_files'] = lambda arguments: self.image_files(arguments, **image_options)
        handlers.update(forsic_note=self.note, forsic_verify=self.verify, forsic_read_bytes=self.read_bytes, forsic_cases=self.cases)
        if tool == 'forsic_note' and request_context.get('model_note_read') is True:
            from .notes import model_read
            handlers['forsic_note'] = lambda arguments: model_read(self, arguments)
        if tool=='forsic_shell':
            extension = request_context.get('extension_dispatch')
            if extension:
                from .shell_workspace import invoke_extension
                handlers['forsic_shell'] = lambda arguments: invoke_extension(self, conversation_id,
                    extension['argv'], extension['pack_root'], extension['timeout_seconds'],
                    native_execution=native_execution, extension=extension['identity'],
                    max_output_bytes=extension.get('max_output_bytes', 12000))
            else:
                from .shell_workspace import invoke as shell_invoke
                handlers['forsic_shell']=lambda arguments: shell_invoke(self,{k:v for k,v in arguments.items() if k!='reason'},
                    conversation_id,native_execution=native_execution)
        if tool == 'forsic_intel':
            from .intelligence import lookup
            handlers['forsic_intel'] = lambda args: lookup(self, args)
        if tool == 'forsic_reporting':
            from .report_driven.host import invoke as reporting
            handlers['forsic_reporting'] = lambda args: reporting(self, args)
        if tool == 'forsic_indicators':
            from .indicators import invoke as indicators
            handlers['forsic_indicators'] = lambda args: indicators(self, args)
        try:
            if admission_error:
                raise ValueError(admission_error)
            if call_args.get('cursor') is not None and (tool == 'forsic_search' or (tool == 'forsic_image_files' and call_args.get('action') == 'search')):
                # A cursor carries unfinished text. Only reuse one actually returned
                # in this case; accepting model-authored preview/matched state would
                # turn a fabricated cursor into apparent source text.
                cursor = call_args['cursor']
                if not isinstance(cursor, dict):
                    raise ValueError('Use a next_cursor returned by this case search')
                with self.connect() as db:
                    saved = db.execute("SELECT data FROM events WHERE kind='tool_result' AND json_extract(data,'$.tool')=? AND json_extract(data,'$.next_cursor.checksum')=?", (tool, cursor.get('checksum'))).fetchall()
                if not any(json.loads(row['data']).get('next_cursor') == cursor for row in saved):
                    raise ValueError('Use the unchanged next_cursor from a saved search result in this case')
            if tool in ('forsic_note', 'forsic_reporting'):
                call_args.update(_goal_id=request_context.get('goal_id', ''), _session_id=conversation_id)
                call_args['_session_lineage'] = request_context.get('session_lineage', [conversation_id])
                if request_context.get('independent_review_options'):
                    call_args['_independent_review_options'] = request_context['independent_review_options']
                if request_context.get('question_contract_version'):
                    call_args['_question_contract'] = request_context['question_contract_version']
            result = handlers[tool](call_args)
        except (OSError, ValueError, KeyError, TypeError, StopIteration, ImportError, sqlite3.Error, subprocess.SubprocessError) as exc:
            result = {"error": str(exc), "error_type": type(exc).__name__, "failure_stage": "admission" if admission_error else "execution", "next_step": "Explain the limitation; change the path/scope/tool if supported. Do not repeat the identical failed call."}
            from .report_driven.model_pages import RetainedSourceError
            if isinstance(exc,RetainedSourceError):
                result.update(exc.details)
            from .report_driven.grounded_judgment import CitationSelectionError
            if isinstance(exc, CitationSelectionError):
                result.update(exc.details)
            from .report_driven.report_reference import ReportReferenceError
            if isinstance(exc, ReportReferenceError):
                result.update(exc.result)
            from .execution_contract import ExecutionFailure
            if isinstance(exc, ExecutionFailure):
                result.update(exc.details)
            from .image_reference import ImageSelectionError
            if isinstance(exc, ImageSelectionError):
                result.update(exc.details)
            from .report_driven.investigation_state import QuestionInputError
            if isinstance(exc, QuestionInputError):
                result.update(exc.details)
                result['next_step']='Repair the named fields in the original forsic_question request using recovery. No question was changed; do not substitute a new answer, inferred disposition or citation reference.'
            from .report_driven.review_actions import ReviewInputError
            if isinstance(exc, ReviewInputError):
                result.update(exc.details)
                result['next_step']='Repair the named fields in the original review_response payload using recovery. No question was changed.'
            from .report_driven.investigation_state import MutationConflictError
            if isinstance(exc, MutationConflictError):
                result.update(exc.details)
            from .report_driven.report_research import DuplicateWorkError
            if isinstance(exc, DuplicateWorkError):
                result.update(exc.details)
            if (not admission_error and mission_context and tool=='forsic_image_files'
                    and call_args.get('action') in ('read','read_bytes')
                    and result.get('error_code') == 'parser_timeout'
                    and result.get('execution',{}).get('termination_confirmed') is True):
                result['retryable_read_failure']=True
            if isinstance(exc, FileNotFoundError) and tool in ('forsic_read', 'forsic_read_bytes', 'forsic_search', 'forsic_list'):
                result.update(outcome='not_found', path=args.get('path', ''),
                              meaning='Requested path was not found in the selected evidence; deletion history is not established.')
        except KeyboardInterrupt as exc:
            from .execution_contract import commit_result
            partial = getattr(exc,'forsic_shell_result',{}) if tool=='forsic_shell' else {}
            cancelled = {**(partial if isinstance(partial,dict) else {}),
                         'tool':tool, 'started_id':started, 'error':'Native execution interrupted',
                         'error_type':'KeyboardInterrupt', 'failure_stage':'execution',
                         'execution':getattr(exc,'forsic_execution',{'state':'completion_unknown'})}
            if mission_context:
                cancelled['mission_context'] = mission_context
            try:
                commit_result(self, cancelled, execution_session)
            finally:
                raise exc
        result = {**result, "tool": tool, "started_id": started}
        if request_context.get('extension_dispatch'):
            result['extension'] = request_context['extension_dispatch']['identity']
            from .extension_runtime import parse_output
            result = parse_output(result, request_context['extension_dispatch'].get('output', {}))
        if argument_resolution:
            result['argument_resolution']=argument_resolution
        if archive_selection is not None and result.get('error'):
            from .archive_selection import recovery_calls
            result.update(recovery_calls(argument_resolution['listing_ref']))
            result['next_step']='Inspect this retained failure and source_call. If the archive changed, explicitly select relist_call and then a member from that new listing. Do not replay unknown work or resume a paused goal.'
        if mission_context:
            result['mission_context'] = mission_context
        if admission_error:
            result['execution_rejected'] = True
            result.update(backend_started=False,**admission_details)
            if admission_details.get('problems'):
                result['next_step']='Correct the named argument fields before retrying. No evidence backend started; this is not a missing-file observation. For a planned check, copy its exact saved next_call; for discovery use the current evidence inventory. Do not infer a path or mission.'
            if admission_details.get('recovery'):
                result['next_step']=admission_details['next_step']
            if ((tool == 'forsic_search' or tool == 'forsic_image_files' and call_args.get('action') == 'search')
                    and ('text' not in args or args['text'] == '') and 'cursor_ref' not in args):
                result['query_recovery'] = {'field':'text','required':'nonempty_single_line_literal',
                    'continuation_selector':'cursor_ref from a successful retained search page',
                    'automatic_retry':False}
                result['next_step'] += (' New search requires an explicit nonempty text literal. '
                    'For continuation, select the exact retained page cursor_ref and preserve its image/path/scope; '
                    'the program restores that page query. To inspect names use the inventory tool; '
                    'to read contents use the read tool. An empty search is neither operation.')
            if archive_entry:
                result['next_step']='Use listing_ref and a displayed member_index with the current forsic_archive_read contract. Follow source_call/relist_call only when returned; do not substitute a target or resume a paused goal.'
        if not result.get('error') and (tool == 'forsic_list' or
                tool == 'forsic_image_files' and call_args.get('action') in ('list', 'archive_list')):
            from .filename_observations import observe_listing
            observations = observe_listing(result)
            if observations['observations'] or observations['scope'].get('omitted_observations'):
                result['filename_observations'] = observations
        from .execution_contract import commit_result
        eid = commit_result(self, result, execution_session)
        if result.get('retryable_read_failure') and not args.get('retry_of'):
            result['retry_call']={'tool':'forsic_image_read_mission','arguments':{
                'path':call_args['path'],'mission_id':args['mission_id'],
                'mission_version':args['mission_version'],'retry_of':eid}}
            result['next_step']='A transient read failure was committed. You may select retry_call once without changing this plan. Persistent parser/input failures require diagnosis; this does not resume a paused goal.'
        retained_result = result
        try:
            if tool == 'forsic_intel':
                from .intelligence import model_view
                result = model_view(result)
            from .report_driven.measurement import measurement_view
            measurement=measurement_view(result)
            from .image_reference import result_view as image_result_view
            result=image_result_view({'evidence_id':eid,**result}, self)
            # Presentation only: retained source bytes/version remain unchanged.
            return json.dumps({**({'measurement':measurement} if measurement else {}), "evidence_id": eid, **result}, ensure_ascii=False)
        except (ValueError, TypeError) as exc:
            return json.dumps({"evidence_id":eid, **retained_result, 'presentation_warning':{
                'failure_stage':'presentation', 'error_type':type(exc).__name__,
                'next_step':'Use the retained evidence_id. Do not repeat execution or intake for a display failure.'}},ensure_ascii=False)
