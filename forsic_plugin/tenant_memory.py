"""Tenant provenance around native MEMORY.md; no second active fact store."""
from contextlib import closing, contextmanager
from contextvars import ContextVar
from copy import deepcopy
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import uuid

SCHEMA = 'forsic-tenant-memory-1'
LIMIT = 2200
_PREFIX = re.compile(r'^\[(M-[a-f0-9]{12}) r([1-9][0-9]*)\] ')
_operation = ContextVar('forsic_tenant_memory_operation', default=None)
_notices = {}
_notice_lock = threading.RLock()


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()


def _sha(raw):
    return hashlib.sha256(raw if isinstance(raw, bytes) else raw.encode()).hexdigest()


def _now():
    return datetime.now(timezone.utc).isoformat()


def _snapshot_entry(entry, item):
    return (entry + '\n  분석가 설명 · 적용: ' + item['applicability'] +
            ' · 효력: ' + (item['valid_from'] or 'unknown') + ' ~ ' + (item['valid_until'] or 'unknown'))


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError('Memory metadata path is a symlink')
    tmp = path.with_name('.' + path.name + '-' + uuid.uuid4().hex)
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as out:
            out.write(_json(value)); out.flush(); os.fsync(out.fileno())
        os.replace(tmp, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        tmp.unlink(missing_ok=True)


def _read(path, default):
    if path.is_symlink():
        raise ValueError('Memory metadata path is a symlink')
    return json.loads(path.read_text()) if path.exists() else deepcopy(default)


def _scope(scope):
    from .tenants import TenantRegistry, current_scope
    fresh = TenantRegistry().resolve(tenant_id=scope['tenant_id'])
    for key in ('tenant_id', 'profile_name', 'profile_home'):
        if str(fresh[key]) != str(scope[key]):
            raise ValueError('Tenant memory owner mapping changed')
    active = current_scope(required=False)
    if active and active['tenant_id'] != fresh['tenant_id']:
        raise ValueError('Memory operation belongs to another tenant')
    return fresh


class _Source:
    """Opaque server-created provenance, never deserialized from model arguments."""
    def __init__(self, tenant_id, value, *, ui=False):
        self.tenant_id, self.value, self.ui = tenant_id, value, ui


def trusted_ui_source(scope, actor_id='authenticated-local-operator'):
    scope = _scope(scope)
    return _Source(scope['tenant_id'], {'source_type': 'user_attested', 'origin': 'management_ui',
        'actor': str(actor_id)[:160], 'asserted_at': _now(), 'explicit_request': True}, ui=True)


def _native_source(scope, request, context):
    from .tenants import assert_session_scope, native_db
    if not isinstance(context, dict) or not context.get('session_id') or not context.get('tool_call_id'):
        raise ValueError('Native memory needs an actual session and tool-call identity')
    sid = context['session_id']
    agent = context.get('agent')
    if agent is not None and (getattr(agent, 'side_agent', False) or getattr(agent, '_skip_memory', False)):
        raise ValueError('Side/background agents cannot attest tenant operational knowledge')
    db_path = getattr(getattr(agent, '_session_db', None), 'db_path', None) or native_db(scope)
    verified = assert_session_scope(scope, sid, native_db=db_path)
    non_interactive = {'tool', 'cron', 'curator', 'background_review', 'background-review', 'delegate', 'delegation'}
    if verified.get('source') in non_interactive or getattr(agent, 'platform', None) in non_interactive:
        raise ValueError('Scheduled/background origins are not direct analyst attestation')
    allowed = [sid]
    with closing(sqlite3.connect(Path(db_path).as_uri() + '?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        child = verified
        from .investigation_context import _compression_link
        while child.get('parent_session_id'):
            parent_id = child['parent_session_id']
            if parent_id in allowed:
                raise ValueError('Native memory session ancestry contains a cycle')
            parent = assert_session_scope(scope, parent_id, native_db=db_path)
            if not _compression_link(child, parent):
                break
            allowed.append(parent_id); child = parent
        selector = request.get('source_message_id')
        placeholders = ','.join('?' for _ in allowed)
        if selector is None:
            row = db.execute(f"SELECT * FROM messages WHERE session_id IN ({placeholders}) AND role='user' ORDER BY id DESC LIMIT 1", allowed).fetchone()
        else:
            row = db.execute(f"SELECT * FROM messages WHERE session_id IN ({placeholders}) AND id=?", [*allowed, selector]).fetchone()
        if row is None or row['role'] != 'user':
            raise ValueError('Source is not an owned native direct-user message')
        raw = row['content']
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError('Native user source has no direct text')
        from agent.context_compressor import MAX_ITERATIONS_SUMMARY_REQUEST
        if row['_compressed_summary'] or row['display_kind'] == 'hidden' or raw == MAX_ITERATIONS_SUMMARY_REQUEST:
            raise ValueError('Generated/hidden context is not a direct analyst confirmation')
        # Native-generated user-role envelopes are not human confirmations.
        excerpt = request.get('source_excerpt')
        if excerpt is None:
            if '```' in raw or any(line.lstrip().startswith('>') for line in raw.splitlines()):
                raise ValueError('Mixed quoted/imported source needs an explicit direct-user excerpt')
            excerpt = raw
        if not isinstance(excerpt, str) or not excerpt.strip() or excerpt not in raw:
            raise ValueError('Copy a literal excerpt from the actual user source')
        from agent.redact import redact_sensitive_text
        if redact_sensitive_text(excerpt, force=True) != excerpt:
            raise ValueError('A memory source excerpt must not copy credentials')
        if raw.lstrip().startswith(('[System note:', '[Auto-continue', '<memory-context>', '<tool_result', 'Context from ', '[Background review')):
            raise ValueError('Generated context is not a direct analyst confirmation')
        if excerpt.lstrip().startswith(('>', '```', '<memory-context>', '<tool_result')):
            raise ValueError('Quoted/imported content is not direct analyst attestation')
        position = raw.find(excerpt)
        if raw[:position].count('```') % 2 or raw[raw.rfind('\n', 0, position)+1:position].lstrip().startswith('>'):
            raise ValueError('Excerpt is inside quoted/imported material, not direct analyst text')
        question_id = request.get('question_message_id')
        question = None
        if question_id is not None:
            question = db.execute(f"SELECT * FROM messages WHERE session_id IN ({placeholders}) AND id=?", [*allowed, question_id]).fetchone()
            if (question is None or question['role'] != 'assistant' or question['id'] >= row['id']
                    or not isinstance(question['content'], str) or not question['content'].strip()):
                raise ValueError('Confirmation question must be an earlier owned assistant message')
        if len(excerpt.strip()) < 5 and question is None:
            raise ValueError('A short confirmation needs its exact earlier question')
        basis = request.get('basis_kind')
        if basis not in ('direct_confirmation', 'explicit_request'):
            raise ValueError('Uncertain answers, inference and imported text are not attested memory')
        return _Source(scope['tenant_id'], {'source_type': 'user_attested', 'origin': 'native_user',
            'session_id': sid, 'message_session_id': row['session_id'], 'message_id': row['id'],
            'message_sha256': _sha(raw), 'excerpt': excerpt, 'excerpt_sha256': _sha(excerpt),
            'question_message_id': question_id, 'question_sha256': _sha(question['content']) if question else None,
            'tool_call_id': context['tool_call_id'], 'turn_id': context.get('turn_id', ''),
            'api_request_id': context.get('api_request_id', ''), 'native_db': str(db_path),
            'asserted_at': datetime.fromtimestamp(float(row['timestamp']), timezone.utc).isoformat(),
            'explicit_request': basis == 'explicit_request'})


class MemoryService:
    def __init__(self, scope):
        self.scope = deepcopy(scope)
        self.home = Path(scope['profile_home']).resolve()
        self.root = self.home / 'forsic' / 'tenant-memory'
        self.meta_path = self.root / 'metadata.json'
        self.journal_path = self.root / 'prepared.json'
        self.memory_path = self.home / 'memories' / 'MEMORY.md'

    def _fresh(self, *, writing=False):
        scope = _scope(self.scope)
        if scope.get('memory_policy', {}).get('mode') == 'off' and writing:
            raise ValueError('Tenant operational memory is off; nothing was saved')
        for path in (self.home / 'forsic', self.root, self.home / 'memories', self.memory_path):
            if path.is_symlink():
                raise ValueError('Managed memory path is a symlink')
        return scope

    def _blank(self):
        return {'schema': SCHEMA, 'tenant_id': self.scope['tenant_id'], 'revision': 0,
                'file_sha256': _sha(b''), 'items': {}, 'history': {}, 'operations': {}}

    def _meta(self):
        value = _read(self.meta_path, self._blank())
        if value.get('schema') != SCHEMA or value.get('tenant_id') != self.scope['tenant_id']:
            raise ValueError('Memory sidecar owner or format mismatch')
        return value

    def _raw(self):
        return self.memory_path.read_bytes() if self.memory_path.exists() else b''

    @contextmanager
    def _lock(self):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self.root / '.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def _recover(self):
        pending = _read(self.journal_path, None)
        if pending is None:
            return
        actual = _sha(self._raw())
        meta = self._meta()
        if meta['operations'].get(pending['operation_id'], {}).get('input_sha256') == pending['input_sha256']:
            self.journal_path.unlink(); return
        if actual == pending['after_sha256'] and (pending['after_sha256'] != pending['before_sha256'] or pending.get('phase') == 'native_applied'):
            if meta['revision'] != pending['before_revision']:
                raise ValueError('Prepared memory metadata revision no longer matches')
            _write(self.meta_path, pending['metadata'])
            self.journal_path.unlink(); return
        if actual == pending['before_sha256']:
            # No observable body update, or metadata-only operation before its
            # native-return marker. Retry the same operation; never invent success.
            self.journal_path.unlink(); return
        raise ValueError('Memory recovery is ambiguous; provenance/synchronization review required')

    def recover(self):
        self._fresh()
        if not self.journal_path.exists():
            return
        with self._lock():
            self._recover()

    def _view(self):
        from tools.memory_tool_store import MemoryStore, ENTRY_DELIMITER
        scope = self._fresh()
        raw = self._raw()
        entries = MemoryStore._parse_entries(raw.decode('utf-8'))
        meta = self._meta()
        pending = self.journal_path.exists()
        active, untracked = [], []
        matched = set()
        for mid, item in meta['items'].items():
            if item['status'] != 'active':
                continue
            found = next((entry for entry in entries if _sha(entry) == item['native_sha256']), None)
            if found is None or pending:
                active.append({**deepcopy(item), 'synchronization': 'review_required', 'content': None})
                continue
            matched.add(found)
            parsed = _PREFIX.match(found)
            if not parsed or parsed.group(1) != mid or int(parsed.group(2)) != item['revision']:
                raise ValueError('Memory identifier binding mismatch')
            active.append({**deepcopy(item), 'content': found[parsed.end():], 'synchronization': 'verified'})
        for entry in entries:
            if entry not in matched:
                untracked.append({'memory_id': None, 'content': entry, 'content_sha256': _sha(entry),
                                  'source_type': 'unverified', 'synchronization': 'review_required'})
        injected = [x for x in active if x['synchronization'] == 'verified']
        return scope, meta, raw, entries, active, untracked, injected

    def list(self, query='', offset=0, limit=50):
        scope, meta, raw, entries, items, unknown, _ = self._view()
        for mid, record in meta['items'].items():
            if record['status'] != 'active':
                history = meta['history'].get(mid, [])
                old_text = next((x.get('content') for x in reversed(history) if x.get('content') is not None), None)
                items.append({**deepcopy(record), 'content': old_text, 'content_not_active': True,
                              'synchronization': 'verified'})
        query = str(query).casefold(); offset = max(0, int(offset)); limit = max(1, min(50, int(limit)))
        all_items = [x for x in items + unknown if query in json.dumps(x, ensure_ascii=False).casefold()]
        return {'tenant_id': scope['tenant_id'], 'profile_name': scope['profile_name'],
                'mode': scope['memory_policy']['mode'], 'revision': meta['revision'], 'sidecar_revision': meta['revision'],
                'items': all_items[offset:offset+limit], 'total': len(all_items),
                'next_offset': offset+limit if offset+limit < len(all_items) else None,
                'capacity': {'used': len(raw.decode('utf-8')), 'limit': LIMIT},
                'synchronization': 'review_required' if unknown or self.journal_path.exists() else 'verified',
                'current_case_evidence': False, 'reference_only': True}

    def get(self, memory_id, revision=None):
        _, meta, _, _, items, _, _ = self._view()
        item = meta['items'].get(memory_id)
        if item is None:
            raise ValueError('Unknown memory ID in this tenant')
        if revision is None or revision == item['revision']:
            value = next((x for x in items if x.get('memory_id') == memory_id), None)
            if value is None:
                value = deepcopy(item)
                history = meta['history'].get(memory_id, [])
                value.update(content=next((x.get('content') for x in reversed(history) if x.get('content') is not None), None),
                             content_not_active=True)
            return {**value, 'history': deepcopy(meta['history'].get(memory_id, [])),
                    'reference_only': True, 'current_case_evidence': False}
        old = next((x for x in meta['history'].get(memory_id, []) if x['revision'] == revision), None)
        if old is None:
            raise ValueError('Unknown historical memory revision')
        return {**deepcopy(old), 'historical_context': True, 'reference_only': True, 'current_case_evidence': False}

    def snapshot(self):
        from tools.memory_tool_store import ENTRY_DELIMITER, _scan_memory_content
        current = self._fresh()
        if current['memory_policy']['mode'] == 'off':
            return {'tenant_id': current['tenant_id'], 'revision': self._meta()['revision'], 'mode': 'off',
                    'entries': [], 'refs': [], 'reference_only': True, 'current_case_evidence': False}
        scope, meta, _, entries, _, _, verified = self._view()
        if scope['memory_policy']['mode'] == 'off':
            verified = []
        allowed = {_sha(f"[{x['memory_id']} r{x['revision']}] {x['content']}") for x in verified}
        by_hash = {x['native_sha256']: x for x in verified}
        selected_native = [e for e in entries if _sha(e) in allowed and not _scan_memory_content(e)]
        selected = [_snapshot_entry(e, by_hash[_sha(e)]) for e in selected_native]
        verified = [by_hash[_sha(e)] for e in selected_native]
        # Never truncate a fact's scope/exception. Oversized external files are
        # not a reason to emit a partial statement or a larger prompt.
        budget_store = __import__('tools.memory_tool_store', fromlist=['MemoryStore']).MemoryStore(
            home=self.home, owner_policy=native_policy(scope), memory_char_limit=LIMIT)
        if len(budget_store._render_block('memory', selected)) > LIMIT:
            selected = []
        if not selected:
            verified = []
        return {'tenant_id': scope['tenant_id'], 'revision': meta['revision'], 'mode': scope['memory_policy']['mode'],
                'entries': selected, 'refs': [{'memory_id': x['memory_id'], 'revision': x['revision'],
                    'source_type': x['source']['source_type'], 'applicability': x['applicability'],
                    'valid_from': x['valid_from'], 'valid_until': x['valid_until']} for x in verified],
                'reference_only': True, 'current_case_evidence': False}

    def changes(self, since_revision):
        scope, meta, *_ = self._view()
        return {'tenant_id': scope['tenant_id'], 'revision': meta['revision'], 'sidecar_revision': meta['revision'], 'mode': scope['memory_policy']['mode'],
                'changed': meta['revision'] != since_revision,
                'changes': [dict(value['result'], operation_id=operation_id)
                            for operation_id, value in meta['operations'].items()
                            if value['result'].get('sidecar_revision', 0) > since_revision],
                'items': [{'memory_id': x['memory_id'], 'revision': x['revision'], 'status': x['status']}
                          for x in meta['items'].values() if x.get('sidecar_revision', 0) > since_revision]}

    def mutate(self, request, *, source=None, native_store=None, _approved=False):
        scope = self._fresh(writing=True)
        if not isinstance(source, _Source) or source.tenant_id != scope['tenant_id']:
            raise ValueError('A verified server source is required; caller source strings are not trusted')
        if scope['memory_policy']['mode'] == 'manual' and not source.value.get('explicit_request'):
            raise ValueError('Manual memory needs an explicit analyst request')
        if not isinstance(request, dict) or not isinstance(request.get('operation_id'), str) or not request['operation_id'].strip():
            raise ValueError('A stable nonempty operation_id is required')
        if len(request['operation_id']) > 200:
            raise ValueError('operation_id is too long')
        canonical = {k: v for k, v in request.items() if k not in ('source_type', 'source', 'confirmed', 'target')}
        operation_id = request['operation_id']; identity = _sha(_json(canonical))
        if not source.ui and not _approved and not (scope['memory_policy']['mode'] == 'confirmed_auto' and not source.value.get('explicit_request')):
            from tools import write_approval as wa
            decision = wa.evaluate_gate(wa.MEMORY, inline_summary='tenant operational memory', inline_detail=str(request.get('content') or request.get('reason') or ''))
            if not decision.allow:
                if decision.blocked:
                    return {'success': False, 'status': 'failed', 'error': decision.message}
                payload = {'action': 'tenant_memory', 'tenant_id': scope['tenant_id'], 'request': canonical,
                           'verified_source': source.value}
                staged = wa.stage_write(wa.MEMORY, payload, summary='Tenant operational memory', origin=wa.current_origin())
                return {'success': False, 'status': 'pending', 'staged': True, 'pending_id': staged['id'], 'message': decision.message}
        from tools.memory_tool_store import MemoryStore, ENTRY_DELIMITER
        if native_store is None:
            native_store = MemoryStore(home=self.home, owner_policy=native_policy(scope), memory_char_limit=LIMIT,
                                       memory_enabled=True, user_profile_enabled=False)
        with self._lock():
            self._fresh(writing=True); self._recover(); meta = self._meta(); raw = self._raw()
            old_operation = meta['operations'].get(operation_id)
            if old_operation:
                if old_operation['input_sha256'] != identity:
                    raise ValueError('operation_id was used for different memory input')
                return deepcopy(old_operation['result'])
            if _sha(raw) != meta['file_sha256']:
                raise ValueError('Native MEMORY changed outside its provenance transaction; review synchronization')
            entries = MemoryStore._parse_entries(raw.decode('utf-8'))
            if raw.decode('utf-8') != ENTRY_DELIMITER.join(entries):
                raise ValueError('Native MEMORY is not a canonical entry list')
            work = deepcopy(meta); planned = list(entries); native_ops = []
            requests = request.get('operations') or [request]
            if not isinstance(requests, list) or not 1 <= len(requests) <= 32:
                raise ValueError('Memory operations must be a bounded list')
            returned = []
            for change in requests:
                action = change.get('action'); action = 'remove' if action == 'exclude' else action
                content = change.get('content', change.get('new_text'))
                old = None; old_native = None; restored = None
                if action != 'add':
                    old = work['items'].get(change.get('memory_id'))
                    if old is None or change.get('expected_revision') != old['revision'] or change.get('expected_content_sha256') != old['content_sha256']:
                        raise ValueError('Memory ID, current revision and exact content hash must match')
                    old_native = next((e for e in planned if _sha(e) == old['native_sha256']), None)
                    if old['status'] == 'active' and old_native is None:
                        raise ValueError('Native memory entry changed or is absent')
                if action == 'restore':
                    record = next((h for h in work['history'].get(old['memory_id'], []) if h['revision'] == change.get('from_revision')), None)
                    if record is None:
                        raise ValueError('Choose an existing historical memory revision')
                    content = record['content']
                    restored = record
                if action not in ('add', 'replace', 'remove', 'restore'):
                    raise ValueError('Unsupported tenant memory action')
                if action != 'remove':
                    if not isinstance(content, str) or not content.strip() or ENTRY_DELIMITER in content:
                        raise ValueError('Memory content must be a nonempty single native entry')
                    content = content.strip()
                    from agent.redact import redact_sensitive_text
                    if redact_sensitive_text(content, force=True) != content:
                        raise ValueError('Secret or private credential content is not operational memory')
                    from tools.memory_tool_store import _scan_memory_content
                    if error := _scan_memory_content(content):
                        raise ValueError(error)
                    basis = restored or old or {}
                    applicability = change.get('applicability', basis.get('applicability', 'unknown')) or 'unknown'
                    if not isinstance(applicability, str) or len(applicability) > 500:
                        raise ValueError('Keep applicability a short explicit description or unknown')
                    if redact_sensitive_text(applicability, force=True) != applicability:
                        raise ValueError('Applicability cannot contain credentials')
                    valid_from = change.get('valid_from', basis.get('valid_from'))
                    valid_until = change.get('valid_until', basis.get('valid_until'))
                    for stamp in (valid_from, valid_until):
                        if stamp is not None:
                            parsed = datetime.fromisoformat(stamp)
                            if parsed.tzinfo is None:
                                raise ValueError('Memory validity needs a timezone; unknown dates must remain null')
                    if valid_from and valid_until and datetime.fromisoformat(valid_until) < datetime.fromisoformat(valid_from):
                        raise ValueError('Memory validity end precedes its start')
                    duplicate = next((x for x in work['items'].values() if x['status'] == 'active' and x['content_sha256'] == _sha(content) and x['applicability'] == applicability and x['valid_from'] == valid_from and x['valid_until'] == valid_until), None)
                    if duplicate and (old is None or duplicate['memory_id'] == old['memory_id']):
                        returned.append(deepcopy(duplicate)); continue
                if old:
                    historical = {**deepcopy(old), 'content': old_native[_PREFIX.match(old_native).end():] if old_native else self.get(old['memory_id'], old['revision']).get('content')}
                    if historical['content'] is not None:
                        work['history'].setdefault(old['memory_id'], []).append(historical)
                mid = old['memory_id'] if old else 'M-' + uuid.uuid4().hex[:12]
                revision = old['revision'] + 1 if old else 1
                item = {'memory_id': mid, 'tenant_id': scope['tenant_id'], 'revision': revision,
                        'status': 'retracted' if action == 'remove' else 'active',
                        'content_sha256': old['content_sha256'] if action == 'remove' else _sha(content),
                        'source': deepcopy(source.value), 'recorded_at': _now(),
                        'asserted_at': source.value['asserted_at'], 'valid_from': old.get('valid_from') if action == 'remove' else valid_from,
                        'valid_until': old.get('valid_until') if action == 'remove' else valid_until,
                        'applicability': old.get('applicability') if action == 'remove' else applicability,
                        'replaces': {'memory_id': mid, 'revision': old['revision']} if old else None,
                        'reason': str(change.get('reason') or '')[:1000], 'sidecar_revision': meta['revision'] + 1}
                new_native = f'[{mid} r{revision}] {content}' if action != 'remove' else None
                item['native_sha256'] = _sha(new_native) if new_native else old['native_sha256']
                work['items'][mid] = item; returned.append(deepcopy(item))
                if old_native:
                    index = planned.index(old_native)
                    if action == 'remove':planned.pop(index)
                    else:planned[index] = new_native
                    native_ops.append({'action': 'remove' if action == 'remove' else 'replace', 'old_text': old_native, 'matched_entry': old_native, 'content': new_native})
                else:
                    if action == 'remove':raise ValueError('Memory is already excluded')
                    planned.append(new_native); native_ops.append({'action': 'add', 'content': new_native})
            snapshot_entries = [_snapshot_entry(e, work['items'][_PREFIX.match(e).group(1)]) for e in planned]
            snapshot_used = len(native_store._render_block('memory', snapshot_entries))
            if len(ENTRY_DELIMITER.join(planned)) > LIMIT or snapshot_used > LIMIT:
                return {'success': False, 'status': 'failed', 'error': 'Tenant memory capacity exceeded; no entries changed',
                        'capacity': {'used': len(raw.decode('utf-8')), 'requested': len(ENTRY_DELIMITER.join(planned)),
                                     'snapshot_requested': snapshot_used, 'limit': LIMIT}}
            work['revision'] += 1
            after = ENTRY_DELIMITER.join(planned).encode(); work['file_sha256'] = _sha(after)
            response = {'success': True, 'status': 'committed' if native_ops else 'noop', 'durable': True,
                        'tenant_id': scope['tenant_id'], 'sidecar_revision': work['revision'],
                        'items': returned, 'item': returned[0] if len(returned) == 1 else None,
                        'capacity': {'used': len(after.decode()), 'limit': LIMIT},
                        'message': '이 테넌트의 운영기억에 저장했어요.' if native_ops else '이미 같은 운영기억이 있습니다.',
                        'reference_only': True, 'current_case_evidence': False}
            work['operations'][operation_id] = {'input_sha256': identity, 'result': response}
            prepared = {'schema': SCHEMA, 'operation_id': operation_id, 'input_sha256': identity,
                        'before_sha256': _sha(raw), 'after_sha256': _sha(after),
                        'before_revision': meta['revision'], 'metadata': work, 'phase': 'prepared'}
            _write(self.journal_path, prepared)
            token = _operation.set({'service': self, 'expected_entries': planned, 'source': source})
            try:
                if not native_ops:
                    result = {'success': True}
                elif len(native_ops) == 1:
                    op = native_ops[0]
                    if op['action'] == 'add':result = native_store.add('memory', op['content'])
                    elif op['action'] == 'replace':result = native_store.replace('memory', op['old_text'], op['content'], op['matched_entry'])
                    else:result = native_store.remove('memory', op['old_text'], op['matched_entry'])
                else:
                    result = native_store.apply_batch('memory', native_ops)
                if not result.get('success') or result.get('staged'):
                    self.journal_path.unlink(missing_ok=True)
                    return {**result, 'success': False, 'status': 'pending' if result.get('staged') else 'failed'}
                if _sha(self._raw()) != _sha(after):
                    raise ValueError('Native memory write returned without the expected durable body')
                prepared['phase'] = 'native_applied'; _write(self.journal_path, prepared)
                _write(self.meta_path, work); self.journal_path.unlink()
                return response
            except Exception as exc:
                changed = _sha(self._raw()) != _sha(raw)
                return {'success': False, 'status': 'recovery_required' if changed else 'failed',
                        'body_changed': changed, 'operation_id': operation_id, 'error': str(exc)}
            finally:
                _operation.reset(token)


class NativeMemoryPolicy:
    def __init__(self, scope):
        self.service = MemoryService(scope); self.home = self.service.home

    def flags(self):
        return self.service._fresh()['memory_policy']['mode'] != 'off', False

    def load(self, store):
        if self.flags()[0]:
            self.service.recover()
        value = self.service.snapshot()
        store.memory_entries = value['entries']; store.user_entries = []
        store._system_prompt_snapshot = {'memory': store._render_block('memory', value['entries']), 'user': ''}
        store._tenant_snapshot = value
        if store.session_id:
            with _notice_lock:
                _notices[(value['tenant_id'], store.session_id)] = {'loaded_revision': value['revision'], 'notified_revision': value['revision'], 'mode': value['mode']}

    def mutate(self, store, target, mutate, *, skip_drift=False):
        self.service._fresh(writing=True)
        context = _operation.get()
        if target != 'memory' or not context or context['service'].home != self.home:
            return {'success': False, 'error': 'Managed memory writes require their tenant provenance transaction'}
        def checked(entries, limit):
            result = mutate(entries, min(limit, LIMIT))
            if not isinstance(result, dict) and result[0] != context['expected_entries']:
                return {'success': False, 'error': 'Native memory mutation differs from the reviewed operation'}
            return result
        return store._mutate_native(target, checked, skip_drift=skip_drift)

    def tool(self, store, request, *, native_context=None):
        if request.get('target', 'memory') != 'memory':
            raise ValueError('Personal USER memory is disabled for tenants')
        action = request.get('action')
        if action in ('list', 'get'):
            if not self.flags()[0]:raise ValueError('Tenant memory is off')
            if action == 'get' and not request.get('memory_id'):
                return {'success': False, 'status': 'invalid_arguments',
                        'error': 'get requires memory_id from list. First list current entries with no query, then pass one exact ID.',
                        'next_call': {'name': 'memory', 'arguments': {'target': 'memory', 'action': 'list'}}}
            return self.service.get(request['memory_id'], request.get('revision')) if action == 'get' else self.service.list(request.get('query', ''))
        scope = self.service._fresh(writing=True)
        source = _native_source(scope, request, native_context)
        return self.service.mutate(request, source=source, native_store=store)

    def pending(self, store, payload):
        try:
            scope = self.service._fresh(writing=True)
            if payload.get('action') != 'tenant_memory' or payload.get('tenant_id') != scope['tenant_id']:
                raise ValueError('Pending memory lacks this tenant provenance transaction')
            old = payload['verified_source']
            request = {**payload['request'], 'source_message_id': old['message_id'], 'source_excerpt': old['excerpt'],
                       'question_message_id': old.get('question_message_id'),
                       'basis_kind': 'explicit_request' if old['explicit_request'] else 'direct_confirmation'}
            source = _native_source(scope, request, {'session_id': old['session_id'], 'tool_call_id': old['tool_call_id'],
                'turn_id': old.get('turn_id', ''), 'api_request_id': old.get('api_request_id', '')})
            if source.value['message_sha256'] != old['message_sha256']:
                raise ValueError('Pending source message changed')
            return self.service.mutate(payload['request'], source=source, native_store=store, _approved=True)
        except (ValueError, OSError, LookupError, sqlite3.Error) as exc:
            return {'success': False, 'status': 'failed', 'error': str(exc)}

    def schema(self):
        props = {
            'target': {'type': 'string', 'enum': ['memory']},
            'action': {'type': 'string', 'enum': ['list', 'get', 'add', 'replace', 'remove', 'restore']},
            'operation_id': {'type': 'string'}, 'memory_id': {'type': 'string', 'description': 'Required for get, replace, remove and restore. Copy an exact current ID from list; get without an ID is invalid.'},
            'expected_revision': {'type': 'integer'}, 'expected_content_sha256': {'type': 'string'},
            'content': {'type': 'string'}, 'reason': {'type': 'string'}, 'query': {'type': 'string', 'description': 'Optional literal substring. Omit to discover existing entries before editing; changed words may not match the old text.'},
            'source_message_id': {'type': 'integer', 'description': 'Optional owned native user row; omitted means latest user. The server resolves and verifies provenance.'}, 'source_excerpt': {'type': 'string', 'description': 'Optional exact literal excerpt. Prefer omitting: server binds actual native user text. Explicit paraphrases are rejected, never repaired.'},
            'question_message_id': {'type': 'integer'}, 'basis_kind': {'type': 'string', 'enum': ['direct_confirmation', 'explicit_request', 'unconfirmed']},
            'applicability': {'type': 'string'}, 'valid_from': {'type': ['string', 'null']},
            'valid_until': {'type': ['string', 'null']}, 'from_revision': {'type': 'integer'}, 'revision': {'type': 'integer'},
        }
        props['operations'] = {'type': 'array', 'maxItems': 32, 'items': {'type': 'object', 'properties': {k:v for k,v in props.items() if k not in ('target', 'source_message_id', 'source_excerpt', 'question_message_id', 'basis_kind')}, 'required': ['action']}}
        return {'description': 'Tenant operational context, never current-case evidence or a normality/allowlist rule. USER is off. Use list (omit query) then get(memory_id) for exact current IDs, revisions and content hashes. Writes need operation_id; the SERVER verifies actual native user provenance. Prefer omitting source selectors/excerpt: latest native user text is selected automatically. If selecting an older owned user row or an excerpt, use its exact literal text; paraphrases are rejected. Short confirmations need question_message_id. Decide semantic confirmation and utility yourself: uncertain answers, inference, quoted evidence/docs and save refusals are not attestation. In manual mode use only explicit requests. Preserve unknown scope/period as unknown/null; recorded time is not validity start. Do not invent fields, merge/delete others to fit capacity, or repeat completed saves. replace/remove/restore require memory_id+expected_revision+expected_content_sha256; restore uses from_revision.',
                'parameters': {'type': 'object', 'properties': props, 'required': ['target', 'action']}}


def native_policy(scope):
    return NativeMemoryPolicy(scope)


def memory_notice(scope, session_id):
    service = MemoryService(scope); fresh = service._fresh(); meta = service._meta()
    current = {'revision': meta['revision'], 'mode': fresh['memory_policy']['mode']}
    key = (scope['tenant_id'], session_id)
    with _notice_lock:
        old = _notices.get(key)
    if old is None:
        # Native compaction rotates the conversation ID while carrying its
        # frozen prefix. Recover only the notice bookkeeping through verified
        # compression ancestry; never infer a new snapshot was loaded.
        from .tenants import assert_session_scope
        from .investigation_context import _compression_link
        child = assert_session_scope(fresh, session_id)
        visited = {session_id}
        for _ in range(64):
            parent_id = child.get('parent_session_id')
            if not parent_id:
                return None
            if parent_id in visited:
                raise ValueError('Native memory notice ancestry contains a cycle')
            visited.add(parent_id)
            parent = assert_session_scope(fresh, parent_id)
            if not _compression_link(child, parent):
                return None
            with _notice_lock:
                ancestor = _notices.get((scope['tenant_id'], parent_id))
                if ancestor is not None:
                    old = _notices.setdefault(key, deepcopy(ancestor))
                    break
            child = parent
        else:
            raise ValueError('Native memory notice ancestry exceeds its bound')
    with _notice_lock:
        if old is None:
            return None
        if old['notified_revision'] == current['revision'] and old['mode'] == current['mode']:
            return None
        changed = [{'memory_id': x['memory_id'], 'revision': x['revision'], 'status': x['status']}
                   for x in meta['items'].values() if x.get('sidecar_revision', 0) > old['notified_revision']]
        old.update(notified_revision=current['revision'], mode=current['mode'])
    return {'tenant_id': scope['tenant_id'], 'loaded_snapshot_revision': old['loaded_revision'],
            'current_revision': current['revision'], 'mode': current['mode'], 'changed': changed,
            'context': 'Tenant operational memory changed. Earlier prompt/context entries are historical; do not use withdrawn/old revisions as current facts. Current mode: '+current['mode']+'. Read memory list/get for current IDs before reuse. This notice does not replace the frozen startup snapshot and is not evidence.'}
