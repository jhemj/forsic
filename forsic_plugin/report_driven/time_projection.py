"""One view of selected time evidence and the analyst's separate time inference."""
from copy import deepcopy
import hashlib
import json

from ..temporal import temporal_projection


def event_ref(question_id, local_ref):
    return 'TE-' + hashlib.sha256(question_id.encode()).hexdigest()[:12] + '-' + local_ref.removeprefix('TE-')


def project(case, question, row, index, source_map):
    """No new inference, source reads, models or writes beyond basis revalidation."""
    qid = question['id']
    context = temporal_projection(row, case)
    local_ref = row['event_ref']
    scoped_ref = event_ref(qid, local_ref)
    context['local_event_ref'] = local_ref
    context['event_ref'] = scoped_ref
    context['question_id'] = qid
    selected_basis = ([context['literal_timestamp_ref']] if context.get('literal_timestamp_ref') else []) + context['temporal_basis_refs']
    selected_basis += [basis for relation in context.get('temporal_relations', []) for basis in relation['basis_refs']]
    if any(ref.get('source_role') == 'derived_command_output' for ref in selected_basis):
        context['temporal_limitations'].append('파생 명령 출력에 근거한 시간 해석; 원본 직접 관측이나 실제 입력·검사 범위의 보장이 아님')
    for relation in context.get('temporal_relations', []):
        owner = 'Q-' + relation['target_note_id'] if relation.get('target_note_id') else qid
        relation['scoped_target_event_ref'] = event_ref(owner, relation['target_event_ref'])
    observations, seen = [], set()
    invalid_sources = {hint.get('source_id') for hint in context.get('conflicting_constraints', [])
                       if hint.get('reason') == 'temporal_basis_changed_or_unavailable'}
    context['source_basis_current'] = not invalid_sources
    for basis in selected_basis:
        source = source_map.get(basis['evidence_id'])
        if source is None or source['version'] != basis['source_version'] or basis['evidence_id'] in invalid_sources:
            context['source_basis_current'] = False
            continue  # The frozen old selection remains in temporal, visibly stale.
        literal = basis.get('literal')
        if literal is None:
            literal = str(basis.get('value'))
        start = basis.get('byte_start', 0)
        key = json.dumps({'question_id': qid, 'basis': basis}, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        if key in seen:
            continue
        seen.add(key)
        observations.append(dict(id='O-time-'+hashlib.sha256(key.encode()).hexdigest()[:24],
            version=source['version'], source_ref={'kind':'source','id':source['id'],'version':source['version']},
            statement='시각 해석에 선택한 보존 근거', field_pointer=basis['pointer'], literal=literal,
            canonical_sha256=hashlib.sha256(literal.encode()).hexdigest(),
            presented_view='exact_excerpt' if 'literal' in basis else 'metadata',
            coordinate_basis='retained result scalar UTF-8 bytes; not original image offset',
            byte_start=start, byte_end=start+len(literal.encode()),
            interpretation_limit=('파생 명령 출력; 원본 직접 관측이나 검사 범위 보장이 아님' if
                basis['source_role']=='derived_command_output' else '선택한 필드의 관측; 사건 시점은 별도의 분석가 추정')))
    if not context['source_basis_current']:
        context['temporal_requires_recheck'] = True
        if not invalid_sources:
            context['conflicting_constraints'].append({'event_refs': [scoped_ref],
                'reason': 'temporal_basis_not_in_current_source_projection', 'requires_recheck': True})
    # Match current()'s content identities before exposing selectable generated
    # references. Frozen context refs are deliberately not refreshed afterward.
    from .host import content_version
    for observation in observations:
        observation['version'] = content_version(observation)
    # Use normal observation references inside the existing context envelope so
    # selected-report reference closure retains every selected basis source.
    context['observation_refs'] = [{'kind': 'observation', 'id': item['id'], 'version': item['version']}
                                   for item in observations]
    model = deepcopy(row['temporal'])
    inferred = model['observed_or_inferred'] == 'inferred'
    origin = row.get('time_source') or {}
    raw = row.get('raw_time')
    # Do not sort an inferred interval as one observed instant. Both reports
    # display its bounds, clock domain and assumptions explicitly.
    normalized = ([] if inferred or raw is None or not context['source_basis_current'] else
                  [row['time']] if row.get('comparable', True) else [])
    candidates = model.get('candidates', [])
    shape = 'candidates' if candidates else 'interval' if model.get('earliest') or model.get('latest') else 'unknown'
    if not inferred and normalized:
        shape = 'point'
    primary = observations[0] if observations else None
    result = dict(id='TM-'+qid+'-'+local_ref, version=str(question['revision']),
        event_ref=scoped_ref, question_id=qid, temporal=model, temporal_context=context,
        observation_ref={'kind':'observation','id':primary['id'],'version':primary['version']} if primary else None,
        claim_refs=[], time_kind='event' if model['timestamp_role']=='event_occurrence' else
            'file_metadata' if origin.get('field') in ('ctime','mtime','atime') else 'record',
        shape=shape, raw_values=[str(raw)] if raw is not None else [], normalized_values=normalized,
        timezone_basis=model.get('timezone_basis'), year_basis=model.get('year_basis'),
        comparable=bool(normalized), explanation=row['description'],
        limitation=('선택한 시간 근거의 버전·좌표가 바뀌거나 확인되지 않음; 재검토 전 현재 관측 시각으로 사용하지 않음. '
                    if not context['source_basis_current'] else '') + ('; '.join(context['temporal_limitations']) or
            '근거와 가정 아래의 시간 해석; 직접 관측 시각과 사건 발생 추정을 구별'),
        file_time_type=origin.get('field') if origin.get('field') in ('ctime','mtime','atime') else None)
    return result, observations
