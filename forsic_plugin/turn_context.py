"""Bound the complete per-turn envelope without clipping judgments or source records."""
import hashlib
import json

from .runtime_clock import current_context


def section_id(procedure):
    return 'forsic.principles.' + hashlib.sha256(procedure.encode()).hexdigest()[:16]


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def assemble(procedure, *, section_ids=(), max_chars=10000, intake='', case=None,
             projection=None, advice='', memory_update=None):
    """Only native-confirmed frozen sections suppress the legacy principle fallback.

    Keep a complete projection first. Drop optional sections as complete values,
    with retrieval instructions, instead of spilling its middle into an inaccessible file.
    """
    limit = max_chars if isinstance(max_chars, int) and not isinstance(max_chars, bool) and max_chars > 0 else 10000
    pinned = section_id(procedure) in section_ids
    prefix = '' if pinned else procedure + '\n'
    lookup = {'tool': 'forsic_reporting', 'arguments': {'action': 'state'}}
    body = {'kind': 'forsic_turn_context', 'principles': 'session_section' if pinned else 'legacy_fallback',
            'runtime_clock': current_context(), 'projection': projection, 'omitted': {}}
    if memory_update:
        body['memory_update'] = memory_update
    if projection and projection.get('status') == 'ready':
        # Decode once, rather than quote escaped JSON inside another JSON value.
        state = json.loads(projection['context'])
        # The current envelope supersedes the earlier compaction clock.
        state.pop('runtime_clock', None)
        body['projection'] = {**projection, 'context': state}

    def fits():
        return len(prefix) + len(_json(body)) <= limit

    # Reserve omission lookups before deciding whether the projection fits.
    # Otherwise optional-section metadata can overflow an admitted projection
    # and erase its navigation in the final unavailable fallback.
    case_lookup = {'tool': 'forsic_reporting', 'arguments': {'action': 'case'}}
    review_lookup = {'tool': 'forsic_reporting', 'arguments': {'action': 'review_inbox'}}
    options = [('review_advice', advice, review_lookup), ('intake', intake, case_lookup)]
    if case is not None:
        selected = case.config.get('selected_files', [])
        options.append(('case', {'case_id': case.config.get('case_id'), 'evidence_root': str(case.root),
                                'selected_files': selected, 'selected_file_count': len(selected)},
                        case_lookup))
    body['omitted'].update({name: {'lookup': call} for name, value, call in options if value})

    if not fits() and body['projection']:
        original = body['projection']
        state = original.get('context', {})
        body['projection'] = {k: original[k] for k in ('provider', 'status', 'revision', 'reason') if k in original}
        body['projection'].update(status='omitted', original_status=original.get('status'), lookup=lookup)
        if isinstance(state, dict):
            for key in ('case_id', 'goal_id', 'conversation_id', 'content_revision', 'completion',
                        'work_inventory', 'answer_updates', 'independent_review', 'omitted'):
                if key in state:
                    body['projection'][key] = state[key]
        body['omitted']['projection_body'] = True

    # Optional sections are admitted whole, including omission metadata in the budget.
    for name, value, call in options:
        if not value:
            continue
        body[name] = value
        body['omitted'].pop(name, None)
        if not fits():
            body.pop(name)
            body['omitted'][name] = {'lookup': call}

    if fits():
        return prefix + _json(body)
    # A deliberately tiny native cap cannot carry the minimum complete snapshot.
    # Do not return a clipped fact or imply an empty investigation.
    minimal = {'status': 'unavailable', 'reason': 'turn_context_budget', 'lookup': lookup,
               'principles': body['principles'], 'runtime_clock': body['runtime_clock']}
    if memory_update:
        minimal['memory_update'] = {key: memory_update[key] for key in (
            'loaded_snapshot_revision', 'current_revision', 'mode')}
        minimal['memory_lookup'] = 'Historical snapshot; memory list/get before reuse; off disables reuse.'
    if len(prefix) + len(_json(minimal)) <= limit:
        return prefix + _json(minimal)
    # This is an explicit unavailable handoff, not a silent drop of the fallback.
    minimal['principles'] = 'omitted_budget; forsic_skill(name=forensic-investigation)'
    value = _json(minimal)
    if len(value) <= limit:
        return value
    # Below this size no meaningful status fits; let native spill this explicit
    # diagnostic instead of manufacturing a truncated status token.
    return 'unavailable; forsic_reporting(action=state)' if limit >= 42 else 'unavailable'
