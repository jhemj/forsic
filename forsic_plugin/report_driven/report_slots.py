"""Bounded analyst-authored report declarations owned by the existing note.

Binding verifies ownership and retained references, never the truth of a brief or
its chosen state. Frozen declarations survive judgment changes for rechecking.
"""
from copy import deepcopy

SLOTS = (
    ('compromise_time', '사고 발생 및 인지 경과'),
    ('entry_route', '최초 유입 및 침입 경로'),
    ('attacker_actions', '주요 침해 행위'),
    ('personal_data_presence', '개인정보 보유 여부'),
    ('external_disclosure', '외부 노출 및 반출 여부'),
    ('decisive_evidence', '판단의 핵심 근거'),
    ('incident_timeline', '주요 사건 경과'),
)
STATES = ('confirmed', 'inferred', 'not_confirmed', 'unavailable', 'not_applicable')


def schema():
    text = {'type': 'string', 'minLength': 1, 'maxLength': 1200, 'pattern': r'\S'}
    return {'type': 'array', 'maxItems': 7,
        'description': 'Optional editorial declarations owned by this question. Supplying this array replaces its report declarations; omit the field to preserve them, or use [] to clear them. One row per selected slot, with no completion obligation. claim_indices select this note\'s current or replacement judgment claims in their returned order (zero-based). State is the analyst\'s judgment, never semantic approval.',
        'items': {'type': 'object', 'properties': {
            'slot': {'type': 'string', 'enum': [key for key, _ in SLOTS]},
            'state': {'type': 'string', 'enum': list(STATES)},
            'brief': {**text, 'maxLength': 700},
            'claim_indices': {'type': 'array', 'maxItems': 32, 'uniqueItems': True,
                'items': {'type': 'integer', 'minimum': 0}},
            'basis': text,
            'limitations': {'type': 'array', 'maxItems': 8, 'items': text}},
            'required': ['slot', 'state', 'brief'], 'additionalProperties': False}}


def _value(question, key, default=None):
    return question.get('investigation', {}).get(key, question.get(key, default))


def _qid(question):
    return question.get('id') or 'Q-' + question['note_id']


def _registry(state):
    return {(kind, row['id']): row for field, kind in
        (('claims', 'claim'), ('observations', 'observation'), ('sources', 'source'))
        for row in state.get(field, [])}


def _ref(kind, record):
    return {'kind': kind, 'id': record['id'], 'version': record['version']}


def _judgment_basis(state, question):
    from .investigation_state import definition_version, hash_value
    registry = _registry(state)
    claims = [_ref('claim', registry[('claim', ref['id'])]) if ('claim', ref['id']) in registry else ref
              for ref in _value(question, 'claim_refs', [])]
    return {'question_id': _qid(question),
        'definition_version': question.get('definition_version') or definition_version(question),
        'answer_sha256': hash_value(question['answer']), 'claim_refs': claims}


def _ancestry(registry, references):
    selected = []; seen = set(); pending = list(reversed(references))
    while pending:
        reference = pending.pop(); key = (reference['kind'], reference['id'])
        if key in seen:
            continue
        seen.add(key)
        record = registry.get(key)
        if record is None:
            raise ValueError('Missing retained report reference: ' + '/'.join(key))
        if key[0] not in ('source', 'observation'):
            raise ValueError('Report claim ancestry must select observations or sources')
        selected.append(_ref(key[0], record))
        if key[0] == 'observation':
            pending.append(record['source_ref'])
    return selected


def bind(state, question, rows):
    """Called only under the existing CAS/ownership note transaction."""
    from .investigation_state import input_problems
    problems = input_problems(rows, schema(), 'report_slots')
    if problems:
        raise ValueError('Invalid report_slots: ' + str(problems))
    if len({row['slot'] for row in rows}) != len(rows):
        raise ValueError('report_slots selects each slot at most once per question')
    registry = _registry(state); own = _value(question, 'claim_refs', [])
    basis_identity = _judgment_basis(state, question)
    bound = []
    for submitted in rows:
        row = {'claim_indices': [], **submitted}
        if row['state'] in ('confirmed', 'inferred', 'not_confirmed') and not row['claim_indices']:
            raise ValueError('Report state requires this question\'s chosen grounded claims: ' + row['slot'])
        if row['state'] in ('unavailable', 'not_applicable') and not row.get('basis', '').strip():
            raise ValueError('Unavailable/not_applicable report slots require a meaningful basis')
        claims = []
        for index in row['claim_indices']:
            if type(index) is not int or index < 0 or index >= len(own):
                raise ValueError('report_slots claim_indices must select this note\'s current judgment claims')
            claim = registry.get(('claim', own[index]['id']))
            if claim is None or claim.get('status') != 'adopted' or not claim.get('source_refs'):
                raise ValueError('report_slots requires an adopted grounded claim owned by this question')
            claims.append(claim)
        evidence = _ancestry(registry, [ref for c in claims for ref in c['source_refs']])
        counter = _ancestry(registry, [ref for c in claims for ref in c.get('counterevidence_refs', [])])
        bound.append({**deepcopy(row), 'scope': question['scope'],
            'basis': row.get('basis', ''), 'judgment_basis': deepcopy(basis_identity),
            'claim_refs': [_ref('claim', c) for c in claims],
            'evidence_refs': evidence, 'counterevidence_refs': counter,
            'assumptions': list(dict.fromkeys(s for c in claims for s in c.get('assumptions', []))),
            'limitations': list(dict.fromkeys([*row.get('limitations', []),
                *(s for c in claims for s in c.get('limitations', []))])),
            'claim_details': [{key: deepcopy(c[key]) for key in ('statement', 'assertion_kind',
                'inference_strength', 'reasoning_summary', 'assumptions', 'limitations',
                'declared_analysis_fields') if key in c} for c in claims]})
    return bound


def project_report_slots(state):
    """Read-only seven-axis projection; absence is editorial, never evidence absence."""
    registry = _registry(state); slots = []
    for key, label in SLOTS:
        entries = []
        for question in state.get('questions', []):
            for row in question.get('report_slots', []):
                if row['slot'] != key:
                    continue
                reasons = []
                if row['judgment_basis'] != _judgment_basis(state, question):
                    reasons.append('Owning question definition, answer or grounded claims changed')
                for reference in [*row['claim_refs'], *row['evidence_refs'], *row['counterevidence_refs']]:
                    current = registry.get((reference['kind'], reference['id']))
                    if current is None or current['version'] != reference['version']:
                        reasons.append('Selected retained reference changed or is unavailable')
                        break
                entries.append({**deepcopy(row), 'question_id': question['id'],
                    'needs_recheck': bool(reasons), 'recheck_reasons': reasons, 'semantic_approval': False})
        slots.append({'slot': key, 'label': label, 'entries': entries, 'missing': not entries})
    return {'schema': 'forsic-report-slots-1', 'snapshot_id': state.get('meta', {}).get('snapshot_id', ''),
            'slots': slots}
