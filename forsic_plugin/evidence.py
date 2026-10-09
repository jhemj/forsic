"""Small read-only evidence adapter. Hermes owns the agent, sessions and retries."""
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
        return {**{k: self.config.get(k) for k in ("case_id", "label", "question", "evidence_kind", "scope", "synthetic")},
                'evidence_root': str(self.root), 'selected_files': self.config.get('selected_files', []),
                'output_root': str(self.output),
                'capabilities': {'allocated_image_files': True, 'image_text_search': True,
                                 'ewf_verify': True, 'carving': False, 'deleted_recovery': False,
                                 'reports': ['executive', 'analyst']}}

    def read_bytes(self, args):
        import base64
        path = self.path(args['path'])
        offset = max(0, int(args.get('offset', 0)))
        with path.open('rb') as stream:
            stream.seek(offset)
            raw = stream.read(min(12000, max(1, int(args.get('length', 4096)))))
        end = offset + len(raw)
        return {'path': str(path.relative_to(self.root)), 'byte_start': offset, 'byte_end': end,
                'base64': base64.b64encode(raw).decode(), 'text': raw.decode('utf-8', errors='replace'),
                'sha256_slice': hashlib.sha256(raw).hexdigest(), 'next_offset': end if end < path.stat().st_size else None}

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
        offset = max(0, int(args.get("offset", 0)))
        page = []
        for i, path in enumerate(self._files(args.get("path", "."))):
            if i < offset:
                continue
            if len(page) == 100:
                return {"files": page, "next_offset": offset + 100, "complete": False}
            page.append({"path": str(path.relative_to(self.root)), "bytes": path.stat().st_size})
        return {"files": page, "next_offset": None, "complete": True}

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
        return {"path": str(path.relative_to(self.root)), "tool": "ewfinfo", "exit_code": proc.returncode,
                "stdout": proc.stdout.decode(errors="replace")[:24000],
                "stderr": proc.stderr.decode(errors="replace")[:2000], "is_integrity_verification": False}

    def image_files(self, args):
        path = self.path(args['path'])
        reader = Path(__file__).with_name('image_reader.py')
        search = Path(__file__).with_name('literal_search.py')
        container = 'forsic-read-' + uuid.uuid4().hex
        command = ['docker', 'run', '--rm', '--name', container, '--network', 'none', '--read-only', '--cap-drop', 'ALL',
                   '--security-opt', 'no-new-privileges', '--user', f'{os.getuid()}:{os.getgid()}',
                   '--memory', '8g', '--pids-limit', '64',
                   '--mount', f'type=bind,src={self.root},dst=/evidence,readonly',
                   '--mount', f'type=bind,src={reader},dst=/reader.py,readonly',
                   '--mount', f'type=bind,src={search},dst=/literal_search.py,readonly',
                   'forsic-dissect:3.25.1', 'python', '-B', '/reader.py', '/evidence/' + str(path.relative_to(self.root)), json.dumps(args)]
        try:
            proc = subprocess.run(command, capture_output=True, timeout=300, check=False)
        except subprocess.TimeoutExpired:
            subprocess.run(['docker', 'rm', '-f', container], capture_output=True, timeout=15, check=False)
            raise
        if proc.returncode:
            raise ValueError('Read-only image parser failed: ' + proc.stderr.decode(errors='replace')[-2000:])
        data = json.loads(proc.stdout)
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

    def invoke(self, tool, args, session="", *, request_context=None):
        if self.read_only:
            raise ValueError('Read-only case projections cannot invoke tools')
        request_context = request_context or {}
        conversation_id = request_context.get('conversation_id') or request_context.get('session_id') or session
        # Model-only linkage fields never become evidence-parser arguments.
        call_args = {k: v for k, v in args.items() if k not in ('mission_id', 'mission_version') and not k.startswith('_')}
        mission_context, admission_error = None, None
        writes_question = (tool == 'forsic_note' and args.get('action') == 'save') or (
            tool == 'forsic_reporting' and args.get('action') in ('question', 'mission', 'assess', 'gap', 'requirement'))
        if request_context.get('status') == 'unavailable' and (writes_question or args.get('mission_id') or args.get('mission_version')):
            admission_error = 'Native session/goal binding is unavailable; retry this state change only after that binding can be read. No unrelated goal was selected.'
        started = uuid.uuid4().hex
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                if not admission_error and (args.get('mission_id') or args.get('mission_version')):
                    from .report_driven.investigation_state import validate_execution_context
                    mission_context = validate_execution_context(
                        self, args.get('mission_id'), args.get('mission_version'), tool, call_args,
                        goal_id=request_context.get('goal_id', ''), session_id=conversation_id)
            except (ValueError, KeyError, TypeError, StopIteration) as exc:
                admission_error = str(exc)
            start_data = {"tool": tool, "reason": args.get("reason", ""),
                          "path": args.get("file_path") or args.get("path", ""),
                          "text": args.get("text", ""), 'arguments': call_args}
            if mission_context:
                start_data['mission_context'] = mission_context
            if admission_error:
                start_data['execution_rejected'] = True
            db.execute('INSERT INTO events VALUES (?,?,?,?,?)',
                       (started, time.time(), 'tool_start', session, json.dumps(start_data, ensure_ascii=False)))
        handlers = {"forsic_case": self.info, "forsic_list": self.list_files, "forsic_read": self.read,
                    "forsic_search": self.search, "forsic_hash": self.hash_file,
                    "forsic_image_info": self.image_info, "forsic_report": self.report}
        handlers['forsic_image_files'] = self.image_files
        handlers.update(forsic_note=self.note, forsic_verify=self.verify, forsic_read_bytes=self.read_bytes, forsic_cases=self.cases)
        if tool == 'forsic_intel':
            from .intelligence import lookup
            handlers['forsic_intel'] = lambda args: lookup(self, args)
        if tool == 'forsic_reporting':
            from forsic_plugin.report_driven.host import invoke as reporting
            handlers['forsic_reporting'] = lambda args: reporting(self, args)
        if tool == 'forsic_indicators':
            from forsic_plugin.indicators import invoke as indicators
            handlers['forsic_indicators'] = lambda args: indicators(self, args)
        try:
            if admission_error:
                raise ValueError(admission_error)
            if args.get('cursor') is not None and (tool == 'forsic_search' or (tool == 'forsic_image_files' and args.get('action') == 'search')):
                # A cursor carries unfinished text. Only reuse one actually returned
                # in this case; accepting model-authored preview/matched state would
                # turn a fabricated cursor into apparent source text.
                cursor = args['cursor']
                if not isinstance(cursor, dict):
                    raise ValueError('Use a next_cursor returned by this case search')
                with self.connect() as db:
                    saved = db.execute("SELECT data FROM events WHERE kind='tool_result' AND json_extract(data,'$.tool')=? AND json_extract(data,'$.next_cursor.checksum')=?", (tool, cursor.get('checksum'))).fetchall()
                if not any(json.loads(row['data']).get('next_cursor') == cursor for row in saved):
                    raise ValueError('Use the unchanged next_cursor from a saved search result in this case')
            if tool in ('forsic_note', 'forsic_reporting'):
                call_args.update(_goal_id=request_context.get('goal_id', ''), _session_id=conversation_id)
            result = handlers[tool](call_args)
        except (OSError, ValueError, KeyError, TypeError, StopIteration, ImportError, sqlite3.Error, subprocess.SubprocessError) as exc:
            result = {"error": str(exc), "error_type": type(exc).__name__, "next_step": "Explain the limitation; change the path/scope/tool if supported. Do not repeat the identical failed call."}
            if isinstance(exc, FileNotFoundError) and tool in ('forsic_read', 'forsic_read_bytes', 'forsic_search', 'forsic_list'):
                result.update(outcome='not_found', path=args.get('path', ''),
                              meaning='Requested path was not found in the selected evidence; deletion history is not established.')
        result = {"tool": tool, "started_id": started, **result}
        if mission_context:
            result['mission_context'] = mission_context
        if admission_error:
            result['execution_rejected'] = True
        eid = self.record("tool_result", result, session)
        if tool == 'forsic_intel':
            from .intelligence import model_view
            result = model_view(result)
        from .report_driven.measurement import measurement_view
        measurement=measurement_view(result)
        # Presentation only: retained source bytes/version remain unchanged.
        return json.dumps({**({'measurement':measurement} if measurement else {}), "evidence_id": eid, **result}, ensure_ascii=False)
