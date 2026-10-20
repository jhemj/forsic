"""One bounded reader explanation stored on its owning canonical question note.

Authored prose is an interpretation, never a new source or semantic approval.
Bindings and exact references are produced by the existing CAS writer, not input.
"""
from copy import deepcopy
import hashlib
import json

MAX_BYTES = 16 * 1024


def schema():
    nonblank = {'type': 'string', 'minLength': 1, 'pattern': r'\S'}
    return {'type': 'object', 'properties': {
        'title': {**nonblank, 'maxLength': 160},
        'paragraphs': {'type': 'array', 'minItems': 1, 'maxItems': 8, 'items': {
            'type': 'object', 'properties': {
                'text': {**nonblank, 'maxLength': 1600},
                'claim_indices': {'type': 'array', 'maxItems': 16, 'uniqueItems': True,
                    'items': {'type': 'integer', 'minimum': 0},
                    'description': "Zero-based indices of this question's currently selected claims. The host supplies exact references."}},
            'required': ['text'], 'additionalProperties': False}}},
        'required': ['paragraphs'], 'additionalProperties': False,
        'description': 'Concise evolving explanation of the current answer; preserve alternatives and limits separately. Optional, not required per tool return.'}


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(',', ':')).encode()).hexdigest()


def _strings(value):
    return [x for x in value if isinstance(x, str)] if isinstance(value, list) else []


def _own(note):
    return note.get('investigation', note).get('claim_refs', [])


def _selected(note, claims):
    from .host import content_version
    registry = {c['id']: c for c in claims}
    selected = []
    for reference in _own(note):
        claim = registry.get(reference.get('id'))
        # Selected IDs come only from the owning canonical note writer. Native
        # projection versions are refreshed; fingerprints below bind content.
        if not claim or claim.get('status') != 'adopted':
            raise ValueError('reader_explanation owning claim is changed or unavailable')
        selected.append(claim)
    return selected


def _basis(note, selected):
    from .host import content_version
    from .investigation_state import definition_version
    return {'question_id': 'Q-' + note['note_id'] if 'note_id' in note else note['id'],
        'definition_version': definition_version(note),
        'answer_sha256': _hash(note['answer']),
        'claim_fingerprints': [{'id': c['id'], 'sha256': content_version(c)} for c in selected],
        'context_sha256': _hash({k: note.get(k, []) for k in
            ('alternatives', 'limitations', 'critical_gaps', 'gaps')})}


def _ref(kind, record):
    from .host import content_version
    return {'kind': kind, 'id': record['id'], 'version': content_version(record)}


def _unique(rows):
    return list({json.dumps(r, sort_keys=True): deepcopy(r) for r in rows}.values())


def bind(state, note, authored):
    """Validate and bind inside save_note_locked's existing write transaction."""
    from .investigation_state import input_problems
    problems = input_problems(authored, schema(), 'reader_explanation')
    if problems or len(json.dumps(authored, ensure_ascii=False).encode()) > MAX_BYTES:
        raise ValueError('Invalid bounded reader_explanation: ' + str(problems or '16 KiB limit'))
    selected = _selected(note, state.get('claims', []))
    paragraphs = []
    for paragraph in authored['paragraphs']:
        chosen = []
        for index in paragraph.get('claim_indices', []):
            if type(index) is not int or index < 0 or index >= len(selected):
                raise ValueError('reader_explanation claim_indices must select this question\'s current claims')
            chosen.append(selected[index])
        paragraphs.append({'text': paragraph['text'],
            'claim_refs': [_ref('claim', c) for c in chosen],
            'source_refs': _unique([r for c in chosen for r in c.get('source_refs', [])]),
            'counterevidence_refs': _unique([r for c in chosen for r in c.get('counterevidence_refs', [])])})
    return {'schema': 'forsic.reader-explanation.v1', 'basis': _basis(note, selected),
        **({'title': authored['title']} if 'title' in authored else {}), 'paragraphs': paragraphs}


def project(note, claims):
    """Shared read-only sidebar/report projection; stale prose is never current.

    Pass the current owning note and currently selected claim records. This does
    not infer truth, rewrite prose, mutate history, or fetch additional evidence.
    """
    result = {'status': 'missing', 'canonical_answer': note.get('answer', ''), 'paragraphs': [],
        'counterarguments': list(dict.fromkeys(_strings(note.get('alternatives')))),
        'limitations': list(dict.fromkeys(_strings(note.get('limitations')) +
            _strings(note.get('critical_gaps')) + _strings(note.get('gaps')))),
        'semantic_approval': False}
    try:
        selected = _selected(note, claims)
    except (ValueError, KeyError, TypeError):
        selected = []
        if note.get('reader_explanation'):
            result['status'] = 'stale'
    for claim in selected:
        result['counterarguments'] += _strings(claim.get('counterarguments'))
        result['limitations'] += _strings(claim.get('limitations'))
    for field in ('counterarguments', 'limitations'):
        result[field] = list(dict.fromkeys(result[field]))
    saved = note.get('reader_explanation')
    if not saved:
        return result
    if (result['status'] == 'stale' or not isinstance(saved, dict) or
            saved.get('schema') != 'forsic.reader-explanation.v1' or
            saved.get('basis') != _basis(note, selected)):
        return {**result, 'status': 'stale'}
    # Native projections refresh reference versions from the exact retained
    # records. Bindings compare claim content (including reference IDs), then
    # emit those current exact references, never authored or guessed IDs.
    by_id = {c['id']: c for c in selected}
    paragraphs = []
    for paragraph in saved['paragraphs']:
        chosen = [by_id[r['id']] for r in paragraph['claim_refs']]
        paragraphs.append({'text': paragraph['text'],
            'claim_refs': [_ref('claim', c) for c in chosen],
            'source_refs': _unique([r for c in chosen for r in c.get('source_refs', [])]),
            'counterevidence_refs': _unique([r for c in chosen for r in c.get('counterevidence_refs', [])])})
    result.update(status='current' if selected else 'provisional', paragraphs=paragraphs)
    if saved.get('title'):
        result['title'] = saved['title']
    return result
