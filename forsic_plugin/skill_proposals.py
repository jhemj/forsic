"""Private, reviewed procedural drafts; publication uses immutable extension packs.

No model calls, scripts, installations, or default activation happen here. A
proposal is a scoped management record, never a learned case fact.
"""
from contextlib import contextmanager
from copy import deepcopy
import difflib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import uuid
import zipfile

from . import extension_hub as hub
from .dashboard.management_store import ManagementStore, digest

_ID = re.compile(r'[a-f0-9]{32}\Z')
_DRAFT = {'pack_id', 'version', 'name', 'description', 'skill_md', 'requires_tools'}
# These checks reject common accidental disclosures; they are not a semantic
# anonymizer. The analyst must review generalization before publishing.
_PRIVATE = re.compile(r'https?://|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])|(?:[A-Za-z]:\\Users\\|/(?:home|root)/)', re.I)
_INSTALL = re.compile(r'\b(?:pip3?\s+install|uv\s+(?:pip\s+)?(?:add|install)|apt(?:-get)?\s+install|npm\s+(?:i|install)|curl\b.*\|\s*(?:ba)?sh|wget\b.*\|\s*(?:ba)?sh|sudo\b)|(?:설치|인스톨)(?:하|해|한다|하세요)', re.I)


def receipt_hash(event):
    return digest(event['data'])


def _strings(value):
    if isinstance(value, dict):
        for item in value.values(): yield from _strings(item)
    elif isinstance(value, list):
        for item in value: yield from _strings(item)
    elif isinstance(value, str): yield value


def _sensitive_values(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if re.search(r'account|user(?:name)?|password|secret|credential|token|email|phone|case_id|label|selected_files|filename|path|(?:evidence|output)_root', key, re.I):
                for scalar in _strings(item):
                    # These are general locations, not case-root identities.
                    # Case-specific roots and selected names are checked separately.
                    if re.fullmatch(r'/var/log(?:/[A-Za-z0-9_.-]+)*', scalar): continue
                    if scalar.startswith(('/work/${', '/evidence/${')): continue
                    yield scalar
            else: yield from _sensitive_values(item)
    elif isinstance(value, list):
        for item in value: yield from _sensitive_values(item)


def _review_hash(record):
    return digest({key: record[key] for key in ('draft', 'target', 'target_revision', 'origin', 'reason', 'applicability', 'limitations')})


def _fingerprint(manifest, target, draft):
    return digest({'case': str(manifest), 'target': target, 'procedure': draft['name'],
                   'content': draft['skill_md'], 'requires_tools': draft['requires_tools']})


class WorkflowSkillProposals:
    def __init__(self, home, store=None):
        self.home = Path(home).resolve()
        self.store = store or ManagementStore(self.home)
        self.base = self.store.root / 'workflow-skills'
        from .tenants import current_scope
        tenant = current_scope()
        self.owner = {'tenant_id': tenant['tenant_id'] if tenant else None,
                      'profile_home_sha256': digest(str(self.home))}

    def _check(self):
        self.store._scope_check()
        from .tenants import current_scope
        tenant = current_scope()
        if tenant and Path(tenant['profile_home']).resolve() != self.home:
            raise PermissionError('Skill proposal profile changed')
        for path in (self.base, *self.base.parents):
            if path.is_symlink(): raise PermissionError('Skill proposal storage cannot contain links')

    @contextmanager
    def _lock(self):
        self._check()
        self.base.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self.base / '.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            self._check()
            yield
        finally: os.close(fd)

    def _path(self, proposal_id):
        if not isinstance(proposal_id, str) or not _ID.fullmatch(proposal_id):
            raise ValueError('Invalid skill proposal identity')
        return self.base / (proposal_id + '.json')

    def get(self, proposal_id):
        self._check()
        record = self.store.read(self._path(proposal_id))
        if record is None: raise FileNotFoundError('Skill proposal unavailable')
        if record['owner'] != self.owner: raise PermissionError('Skill proposal owner changed')
        return record

    def _save(self, record):
        record['revision'] = record.get('revision', 0) + 1
        record['draft_sha256'] = _review_hash(record)
        self.store.write(self._path(record['id']), record)
        return self.public(record)

    def catalog(self, case_id=''):
        self._check()
        items = [self.get(path.stem) for path in sorted(self.base.glob('*.json'))]
        return [self.public(item) for item in items if not case_id or item['origin']['case_id'] == case_id]

    def public(self, record):
        value = deepcopy(record)
        value['origin'].pop('manifest_path', None)
        original = record.get('target_content', '')
        value.pop('target_content', None)
        value['diff'] = ''.join(difflib.unified_diff(original.splitlines(True),
            record['draft']['skill_md'].splitlines(True), fromfile='reviewed-target/SKILL.md', tofile='draft/SKILL.md'))
        value['privacy'] = {'review_required': True, 'status': 'known_values_checked',
                            'limitation': 'Checks do not establish complete anonymization.'}
        return value

    def _receipts(self, case, selections):
        if not isinstance(selections, list) or not 1 <= len(selections) <= 12:
            raise ValueError('Select 1 to 12 successful execution receipts')
        refs, values = [], []
        protected = list(_sensitive_values(case.config))
        for selected in selections:
            if not isinstance(selected, dict) or set(selected) - {'evidence_id', 'sha256'}:
                raise ValueError('Select receipt identities only, never raw results')
            event = case.event(selected.get('evidence_id'))
            if not event or event['kind'] != 'tool_result': raise ValueError('Select a retained tool_result receipt')
            result = event['data']
            started = case.event(result.get('started_id'))
            if (not started or started['kind'] != 'tool_start' or started['session'] != event['session']
                    or started['data'].get('tool') != result.get('tool') or result.get('error')
                    or result.get('execution_rejected') or result.get('exit_code', 0) != 0
                    or result.get('execution', {}).get('state') in ('failed', 'cancelled', 'completion_unknown', 'not_started')):
                raise ValueError('Receipt does not establish a successful tool return')
            failure_states = {'failed', 'error', 'unsupported', 'partial', 'incomplete', 'cancelled'}
            for projection in (result, result.get('scan'), result.get('coverage')):
                if isinstance(projection, dict) and (projection.get('success') is False
                        or projection.get('supported') is False
                        or projection.get('status') in failure_states or projection.get('state') in failure_states):
                    raise ValueError('Failed, unsupported or partial results are not successful workflow receipts')
            execution = result.get('execution', {})
            if execution and (execution.get('state') != 'returned' or execution.get('termination_confirmed') is not True
                              or execution.get('exit_code', 0) != 0):
                raise ValueError('Execution completion is not confirmed successful')
            sha = receipt_hash(event)
            if selected.get('sha256', sha) != sha: raise ValueError('Origin receipt revision changed')
            refs.append({'evidence_id': event['id'], 'sha256': sha, 'started_id': result['started_id'],
                         'session_id': event['session'], 'tool': result['tool'],
                         'observation': 'retained_tool_return_without_failure_flag'})
            # Read selected results only. Keep original values out of proposal records.
            # Long raw passages remain disallowed. Short control/schema strings
            # such as returned, complete or text/plain are not case identities.
            values.extend(value for value in _strings(result) if len(value) >= 40)
            protected.extend(_sensitive_values(started['data'].get('arguments', {})))
            protected.extend(_sensitive_values(result))
        if len({ref['evidence_id'] for ref in refs}) != len(refs): raise ValueError('Duplicate receipt selection')
        return refs, {'scalars': values, 'protected': protected}

    def _target(self, target, draft):
        from .capability_policy import SKILLS
        if target is None:
            if draft['name'] in SKILLS:
                raise ValueError('Select the exact builtin revision for an improvement')
            duplicates = [p for p in hub.list_packs(self.home) if any(
                s['name'] == draft['name'] for s in p['manifest'].get('skills', []))
                and (p['manifest']['id'], p['manifest']['version']) != (draft['pack_id'], draft['version'])]
            if duplicates: raise ValueError('An existing skill has this name; propose an improvement with its exact revision')
            return ''
        if isinstance(target, dict) and set(target) == {'builtin', 'sha256'}:
            if target['builtin'] not in SKILLS or draft['name'] != target['builtin']:
                raise ValueError('Select an existing builtin skill by name')
            raw = (Path(__file__).parent / 'skills' / target['builtin'] / 'SKILL.md').read_bytes()
            if hashlib.sha256(raw).hexdigest() != target['sha256']:
                raise ValueError('Builtin target revision changed')
            return raw.decode()
        if not isinstance(target, dict) or set(target) != {'pack_id', 'version', 'sha256', 'skill_name'}:
            raise ValueError('Target needs exact pack, version, hash and skill identity')
        loaded = hub.load_pack(target['pack_id'], target['version'], self.home, target['sha256'])
        manifest = loaded['manifest']
        if manifest['kind'] != 'skill' or manifest.get('dependencies') or len(manifest['skills']) != 1:
            raise ValueError('Workflow improvement requires a single procedural skill pack without installations')
        skill = manifest['skills'][0]
        if (skill['name'] != target['skill_name'] or draft['name'] != skill['name']
                or draft['pack_id'] != target['pack_id'] or draft['version'] == target['version']):
            raise ValueError('Improve the same skill in a new immutable version')
        if set(manifest['files']) != {skill['path']}:
            raise ValueError('This workflow only revises self-contained SKILL.md packs; use the hub for richer packs')
        return (Path(loaded['path']) / skill['path']).read_text()

    def _target_revision(self, target, draft):
        if target and 'builtin' in target: return target['sha256']
        identity = draft['pack_id']
        # Exclude the proposed immutable destination so a lost publication receipt
        # can recover through the hub's identical-content import, never overwrite.
        versions = [(p['manifest']['version'], p['sha256']) for p in hub.list_packs(self.home)
                    if p['manifest']['id'] == identity and p['manifest']['version'] != draft['version']]
        choice = hub.selection_snapshot(self.home)['selection']['packs'].get(identity)
        return digest({'versions': versions, 'selection': choice})

    def _validate(self, draft, values, prose):
        if not isinstance(draft, dict) or set(draft) != _DRAFT:
            raise ValueError('Draft must contain pack_id, version, name, description, skill_md and requires_tools only')
        hub._identity(draft['pack_id'], draft['version'])
        if not isinstance(draft['name'], str) or not re.fullmatch(r'[a-z][a-z0-9-]{0,47}', draft['name']):
            raise ValueError('Use a short lowercase skill name')
        content = draft['skill_md']
        if not isinstance(content, str) or not 1 <= len(content) <= 24000: raise ValueError('Draft SKILL.md is empty or oversized')
        from tools.skill_manager_tool import _validate_frontmatter
        from agent.skill_utils import parse_frontmatter
        from tools.skill_linter import lint_content
        error = _validate_frontmatter(content, new_skill=True)
        if error: raise ValueError(error)
        metadata, _ = parse_frontmatter(content)
        if set(metadata) - {'name', 'description'} or metadata.get('name') != draft['name'] or metadata.get('description') != draft['description']:
            raise ValueError('Draft frontmatter must match its name/description and cannot carry installation settings')
        from .capability_policy import TOOLS
        required = draft['requires_tools']
        if not isinstance(required, list) or len(required) > 24 or any(name not in TOOLS for name in required):
            raise ValueError('Skills may refer only to existing Forsic tools')
        combined = '\n'.join([content, draft['description'], draft['pack_id'], draft['version'], *prose])
        from agent.redact import redact_sensitive_text
        if redact_sensitive_text(combined, force=True) != combined or _PRIVATE.search(combined):
            raise ValueError('Generalize private values and paths before saving the draft')
        if _INSTALL.search(combined):
            raise ValueError('Procedural drafts cannot include installation directives')
        # Reject exact known source values, not just regex-shaped secrets.
        ignored = {draft['name'], *TOOLS}
        for value in values['scalars']:
            if len(value) >= 8 and value not in ignored and value in combined:
                raise ValueError('Draft repeats a known case/source value; replace it with a procedural placeholder')
        for value in values['protected']:
            if value and value not in ('.', '/', '/work', '/evidence') and value in combined:
                raise ValueError('Draft repeats a known private identifier or path')
        return [{'severity': f.severity, 'rule': f.rule, 'message': f.message} for f in lint_content(content)]

    def _origin(self, record):
        from .evidence import Case
        origin = record['origin']
        case = Case(origin['manifest_path'], read_only=True)
        if digest(case.config) != origin['manifest_sha256'] or case.config.get('case_id') != origin['case_id']:
            raise ValueError('Origin case revision changed; create a newly reviewed proposal')
        refs, values = self._receipts(case, [{'evidence_id': r['evidence_id'], 'sha256': r['sha256']} for r in origin['receipts']])
        if refs != origin['receipts']: raise ValueError('Origin execution identity changed')
        return case, values

    def propose(self, case, payload, *, operation_id, allowed_session_ids=None):
        from .tenants import current_scope, validate_case
        tenant = current_scope()
        if tenant: validate_case(tenant, case.manifest)
        if not isinstance(operation_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', operation_id):
            raise ValueError('A bounded operation_id is required')
        if not isinstance(payload, dict) or set(payload) != {'reason', 'applicability', 'limitations', 'receipts', 'draft', 'target'}:
            raise ValueError('Provide reason, applicability, limitations, receipts, draft and target')
        for key in ('reason', 'applicability', 'limitations'):
            if not isinstance(payload[key], str) or not 1 <= len(payload[key]) <= 2000:
                raise ValueError('Provide bounded rationale, applicability and limitations')
        payload = deepcopy(payload)
        payload['receipts'] = [{'evidence_id': item} if isinstance(item, str) else item for item in payload['receipts']]
        refs, values = self._receipts(case, payload['receipts'])
        if allowed_session_ids is not None and any(r['session_id'] not in allowed_session_ids for r in refs):
            raise PermissionError('Select successful receipts from the current conversation only')
        lint = self._validate(payload['draft'], values, [payload[k] for k in ('reason', 'applicability', 'limitations')])
        fingerprint = _fingerprint(case.manifest, payload['target'], payload['draft'])
        with self._lock():
            request_key = 'workflow.skill.propose:' + operation_id
            previous = self.store.operation(request_key)
            if previous:
                if previous['fingerprint'] != digest(payload):
                    raise ValueError('operation_id was already used for a different proposal')
                return self.public(self.get(previous['response']['proposal_id']))
            for existing in self.catalog():
                if fingerprint in existing.get('fingerprints', [existing['fingerprint']]):
                    self.store.save_operation(request_key, digest(payload), {'proposal_id': existing['id']})
                    return existing
            target_content = self._target(payload['target'], payload['draft'])
            record = {'id': uuid.uuid4().hex, 'owner': self.owner, 'status': 'review_pending',
                'fingerprint': fingerprint, 'fingerprints': [fingerprint], 'operation_id': operation_id,
                'request_sha256': digest(payload),
                'target_revision': self._target_revision(payload['target'], payload['draft']),
                **{key: deepcopy(payload[key]) for key in ('reason', 'applicability', 'limitations', 'draft', 'target')},
                'origin': {'case_id': case.config.get('case_id', ''), 'manifest_path': str(case.manifest),
                           'manifest_sha256': digest(case.config), 'receipts': refs},
                'target_content': target_content, 'validation': {'structure': 'draft_checked', 'functional': 'not_observed', 'lint': lint},
                'publication': {'status': 'not_saved'}, 'activation': {'status': 'not_requested'}}
            if payload['target'] and 'builtin' in payload['target']:
                record['publication']['limitation'] = 'Builtin improvements are review-only; publishing a replacing overlay is not supported.'
            result = self._save(record)
            self.store.save_operation(request_key, digest(payload), {'proposal_id': result['id']})
            return result

    def _current(self, proposal_id, expected_revision):
        record = self.get(proposal_id)
        if record['revision'] != expected_revision: raise ValueError('Proposal revision conflict; review the current draft')
        return record

    def update(self, proposal_id, draft, *, expected_revision, operation_id):
        with self._lock():
            record = self._current(proposal_id, expected_revision)
            if record['status'] != 'review_pending': raise ValueError('Only a pending draft can be edited')
            _, values = self._origin(record)
            lint = self._validate(draft, values, [record[k] for k in ('reason', 'applicability', 'limitations')])
            record['target_content'] = self._target(record['target'], draft)
            record['draft'] = deepcopy(draft)
            record['fingerprint'] = _fingerprint(record['origin']['manifest_path'], record['target'], draft)
            record.setdefault('fingerprints', []).append(record['fingerprint'])
            record['target_revision'] = self._target_revision(record['target'], draft)
            record['validation']['lint'] = lint
            record['operation_id'] = operation_id
            return self._save(record)

    def decide(self, proposal_id, decision, *, expected_revision, operation_id):
        if decision not in ('reject', 'withdraw'): raise ValueError('Use reject or withdraw')
        with self._lock():
            record = self.get(proposal_id)
            status = {'reject': 'rejected', 'withdraw': 'withdrawn'}[decision]
            if record['status'] == status: return self.public(record)
            if record['revision'] != expected_revision or record['status'] != 'review_pending':
                raise ValueError('Only the current pending proposal can be rejected or withdrawn')
            record.update(status=status, operation_id=operation_id)
            return self._save(record)

    def publish(self, proposal_id, reviewed_sha256, *, expected_revision, operation_id):
        with self._lock():
            record = self.get(proposal_id)
            if record['draft_sha256'] != reviewed_sha256 or _review_hash(record) != reviewed_sha256:
                raise ValueError('Reviewed draft hash changed; review the new diff')
            if record['status'] == 'published':
                pub = record['publication']
                hub.load_pack(pub['pack_id'], pub['version'], self.home, pub['sha256'])
                return self.public(record)
            if record['revision'] != expected_revision or record['status'] != 'review_pending':
                raise ValueError('Only the current reviewed pending draft can be published')
            if record['target'] and 'builtin' in record['target']:
                raise ValueError('Builtin improvements are review-only; replacing overlays are not supported')
            _, values = self._origin(record)
            self._target(record['target'], record['draft'])
            if record['target_revision'] != self._target_revision(record['target'], record['draft']):
                raise ValueError('Target versions or selection changed; review and save a fresh draft')
            self._validate(record['draft'], values, [record[k] for k in ('reason', 'applicability', 'limitations')])
            draft = record['draft']; path = 'skills/' + draft['name'] + '/SKILL.md'
            raw = draft['skill_md'].encode()
            manifest = {'format_version': 1, 'id': draft['pack_id'], 'version': draft['version'],
                'display_name': draft['name'], 'description': draft['description'], 'kind': 'skill',
                'source': {'type': 'local-authored', 'reviewed_draft_sha256': reviewed_sha256},
                'compatibility': {'extension_api': 1, 'platforms': ['linux/amd64']}, 'dependencies': [], 'tools': [],
                'skills': [{'name': draft['name'], 'path': path, 'requires_tools': draft['requires_tools']}],
                'files': {path: hashlib.sha256(raw).hexdigest()}}
            # An ephemeral ZIP exercises the same native pack inspection/import
            # boundary as user-authored extension packs. No alternate direct write.
            with tempfile.TemporaryDirectory(prefix='.draft-', dir=self.base) as stage:
                archive = Path(stage) / 'skill.zip'
                with zipfile.ZipFile(archive, 'w') as bundle:
                    bundle.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False))
                    bundle.writestr(path, raw)
                preview = hub.preview_pack(archive)
                saved = hub.import_pack(archive, self.home, preview['sha256'])
            record.update(status='published', operation_id=operation_id)
            record['publication'] = {'status': 'saved', 'pack_id': manifest['id'], 'version': manifest['version'],
                                     'sha256': saved['sha256'], 'reviewed_sha256': reviewed_sha256}
            record['validation']['structure'] = 'verified'
            return self._save(record)

    def record_activation(self, proposal_id, *, scope, status, operation_id):
        """Record only the result observed by the existing management selector."""
        if scope not in ('case', 'default') or status not in ('applied', 'pending'):
            raise ValueError('Invalid observed activation state')
        with self._lock():
            record = self.get(proposal_id)
            if record['status'] != 'published': raise ValueError('Publish a reviewed version before activation')
            record['activation'] = {'status': status, 'scope': scope, 'operation_id': operation_id}
            return self._save(record)


def proposal_schema():
    """Proposal-only model surface. Approval and publication are UI actions."""
    text = {'type': 'string', 'minLength': 1, 'maxLength': 2000}
    return {'type': 'object', 'additionalProperties': False,
        'properties': {
            'reason': text, 'applicability': text, 'limitations': text,
            'receipts': {'type': 'array', 'minItems': 1, 'maxItems': 12,
                         'items': {'type': 'string', 'description': 'Successful retained current-case tool_result evidence_id.'}},
            'target': {'anyOf': [{'type': 'null'}, {'type': 'object', 'additionalProperties': False,
                'properties': {key: {'type': 'string'} for key in ('pack_id', 'version', 'sha256', 'skill_name')},
                'required': ['pack_id', 'version', 'sha256', 'skill_name']},
                {'type': 'object', 'additionalProperties': False,
                 'properties': {'builtin': {'type': 'string'}, 'sha256': {'type': 'string'}},
                 'required': ['builtin', 'sha256']}]},
            'draft': {'type': 'object', 'additionalProperties': False,
                'properties': {**{key: {'type': 'string'} for key in ('pack_id', 'version', 'name', 'description', 'skill_md')},
                               'requires_tools': {'type': 'array', 'items': {'type': 'string'}}},
                'required': sorted(_DRAFT)}},
        'required': ['reason', 'applicability', 'limitations', 'receipts', 'draft', 'target']}
