"""Read-only authoring support and declared completion checks.

The agent selects evidence, temporal premises and useful investigations. This
module checks their representation and exposes exact current sources; it never
infers a case fact, starts a tool/model, or substitutes a second judgment ledger.
"""
from copy import deepcopy


RESOLUTION_NODES = (
    'report_identity', 'major_findings.timing', 'major_findings.entry',
    'major_findings.personal_data', 'information_impact.presence',
    'information_impact.exposure', 'information_impact.transfer',
)
RESOLUTION_OUTCOMES = ('answered', 'estimated', 'limited', 'needs_investigation')
TEXT = {'type': 'string', 'minLength': 1, 'maxLength': 1500, 'pattern': r'\S'}
KEYS = {'type': 'array', 'maxItems': 32, 'uniqueItems': True, 'items': {'type': 'string'}}


def time_basis_schema():
    return {'type': 'object', 'additionalProperties': False, 'properties': {
        'basis_keys': {**KEYS, 'minItems': 1},
        'timestamp_role': {'type': 'string', 'enum': ['event', 'record', 'metadata', 'acquisition', 'analysis']},
        **{key: deepcopy(TEXT) for key in ('premise', 'alternative', 'year_basis', 'timezone_basis')},
    }, 'required': ['basis_keys', 'timestamp_role', 'premise', 'alternative', 'year_basis', 'timezone_basis']}


def resolution_schema():
    return {'type': 'array', 'minItems': len(RESOLUTION_NODES), 'maxItems': len(RESOLUTION_NODES),
        'items': {'type': 'object', 'additionalProperties': False, 'properties': {
            'node_id': {'type': 'string', 'enum': list(RESOLUTION_NODES)},
            'outcome': {'type': 'string', 'enum': list(RESOLUTION_OUTCOMES)},
            'basis_keys': deepcopy(KEYS), 'checked': deepcopy(TEXT), 'reason': deepcopy(TEXT),
            'constraint': {'type': 'string', 'maxLength': 1500},
            'next_check': {'type': 'string', 'maxLength': 1500},
        }, 'required': ['node_id', 'outcome', 'basis_keys', 'checked', 'reason', 'constraint', 'next_check']}}


def validate_resolutions(checks, choices):
    if len(checks) != len(RESOLUTION_NODES) or {r['node_id'] for r in checks} != set(RESOLUTION_NODES):
        raise ValueError('Answerability review must cover each required node exactly once')
    for row in checks:
        if any(key not in choices for key in row['basis_keys']):
            raise ValueError('Answerability review requires current source keys')
        if row['outcome'] in ('answered', 'estimated') and not any(
                choices[key]['kind'] in ('claim', 'observation', 'source', 'timeline')
                and choices[key]['citation_refs'] for key in row['basis_keys']):
            raise ValueError('Answered/estimated facts require selected evidence, not only an aggregate answer or gap')
        if row['outcome'] == 'limited' and not row['constraint'].strip():
            raise ValueError('A limited answer requires the actual constraint after retained reads, inference and feasible checks')
        if row['outcome'] in ('limited', 'needs_investigation') and not row['next_check'].strip():
            raise ValueError('Unresolved scope requires a discriminating next check or explicit reopening condition')


def resolutions_complete(review):
    rows = (review or {}).get('answerability_checks', [])
    return (len(rows) == len(RESOLUTION_NODES)
            and {r.get('node_id') for r in rows} == set(RESOLUTION_NODES)
            and all(r.get('outcome') in ('answered', 'estimated', 'limited') for r in rows))


def validate_support(entry, choices):
    for keys in entry.get('cell_basis_keys', []):
        if entry['status'] == 'saved' and not any(choices[key]['kind'] != 'question' for key in keys):
            raise ValueError('Each saved value requires its own selected support or scope record; Q context alone is not support')
    window = entry.get('assessment', {}).get('time_range')
    if window:
        if not all(window.get(k) for k in ('subject', 'qualifier', 'reasoning')):
            raise ValueError('time_range requires subject, qualifier and reasoning: date the selected event, not a different activity')
        keys = window['reasoning']['basis_keys']
        if not set(keys) <= set(entry['basis_keys']):
            raise ValueError('Time reasoning must select this entry\'s current support keys')
        if not any(choices[k]['kind'] in ('claim', 'observation', 'source', 'timeline')
                   and choices[k]['citation_refs'] for k in keys):
            raise ValueError('Time reasoning requires retained evidence, not only gap or question context')


def source_cards(items, selected):
    """Separate analyst propositions, scope records and exact originals.

    Closure is by explicit versioned references, never topic words or case names.
    All originals remain in the existing reader and its bounded paging contract.
    """
    lookup = {(i['kind'], i['id']): i['key'] for i in items}
    observations = {o['id']: o for o in selected.get('observations', [])}
    result = {}
    for item in items:
        if item['kind'] == 'question':
            continue
        card = deepcopy(item)
        refs = card.pop('citation_refs', [])
        card['evidence_role'] = ('scope_record_not_proof_of_presence_or_absence' if item['kind'] == 'gap'
            else 'analyst_proposition_compare_with_originals' if item['kind'] in ('claim', 'hypothesis', 'timeline')
            else 'retained_record_not_semantic_approval')
        card['direct_keys'] = list(dict.fromkeys(lookup[(r['kind'], r['id'])] for r in refs
            if (r['kind'], r['id']) in lookup and lookup[(r['kind'], r['id'])] != item['key']))
        originals = []
        for ref in refs:
            obs = observations.get(ref['id'], {}) if ref['kind'] == 'observation' else {}
            source = obs.get('source_ref') or ref
            if source.get('kind') not in ('source', None) and not obs:
                continue
            original = {'reference': deepcopy(ref), 'source_identity': deepcopy(source),
                'read_call': {'tool': 'forsic_reporting', 'arguments': {'action': 'source',
                    'source_id': source['id'], 'source_version': source['version'],
                    **({'pointer': obs['field_pointer']} if obs else {})}}}
            if obs:
                original.update({k: deepcopy(obs[k]) for k in ('literal', 'field_pointer', 'byte_start',
                    'byte_end', 'canonical_sha256', 'coordinate_basis', 'interpretation_limit') if k in obs})
            originals.append(original)
        card['originals'] = originals
        card['independence'] = 'Compare source_identity and original coordinates; repeated reads or handles are not independent corroboration.'
        result[item['key']] = card
    return result


def work_state(progress):
    needed = [r for r in (progress.get('review') or {}).get('answerability_checks', [])
              if r['outcome'] == 'needs_investigation']
    stage = ('reconcile_judgment' if progress['judgment_review_questions'] else
             'complete_fields' if not progress['content_complete'] else
             'investigate_targeted_scope' if needed else
             'render_current_version' if progress['ready'] else 'review_current_version')
    return {'stage': stage, 'same_content_rewrite_needed': False,
        'investigation_required': deepcopy(needed),
        'meaning': 'Structural/declared-work state, not factual certification or native goal completion.',
        'closing_rule': 'Resolve material errors in changed nodes, retain cosmetic findings as nonblocking, review the resulting content once, then render/verify/select that version. Reuse unchanged checks. Missing external data may remain limited; feasible authorized discriminating checks may not be silently called unavailable.'}
