"""Explicit retained-evidence selections for the existing assessment writer.

No collection, mission creation, judgment, or second storage path. Native tool
receipts hold the prepared input; the final writer still validates every span.
"""
from copy import deepcopy
import json
import re

KIND = 'forsic_assessment_basis_v2'
REF = re.compile(r'C1:([0-9a-f]{32}):(0|[1-9][0-9]*)\Z')


def schemas():
    from .investigation_state import compact_input_schemas
    legacy = compact_input_schemas()['assess']['properties']
    string = {'type': 'string', 'minLength': 1}
    basis = {'mission_id': deepcopy(string), 'result_id': deepcopy(string),
             'citation_refs': {'type': 'array', 'items': deepcopy(string),
                 'description': 'Explicit C1 references returned with selected source matches or exact text pages. An empty list selects no body evidence.'}}
    assess = {key: deepcopy(legacy[key]) for key in (
        'operation_id', 'outcome', 'reasoning_summary', 'limitations', 'next_check', 'question_update')}
    assess['basis_receipt_id'] = {**string, 'description': 'evidence_id of the successful forsic_assessment_basis return; use it unchanged.'}
    required = ['operation_id', 'basis_receipt_id', 'outcome', 'reasoning_summary', 'limitations', 'next_check']
    return {'assessment_basis': {'type': 'object', 'properties': basis,
                'required': list(basis), 'additionalProperties': False},
            'assess_basis': {'type': 'object', 'properties': assess,
                'required': required, 'additionalProperties': False}}


def _validate(payload, name):
    from .investigation_state import input_problems
    schema = schemas()[name]
    problems = input_problems(payload, schema)
    if problems:
        raise ValueError('Invalid ' + name + ' input: ' + json.dumps(problems, ensure_ascii=False)
                         + '. No assessment was saved.')
    for key in schema['required']:
        if isinstance(payload[key], str) and not payload[key].strip():
            raise ValueError(key + ' must be a nonempty string')
    if name == 'assessment_basis' and len(set(payload['citation_refs'])) != len(payload['citation_refs']):
        raise ValueError('Select each citation_ref once; repeated references are not independent evidence')


def _receipt(case, eid, action):
    event = case.event(eid) if isinstance(eid, str) else None
    if not event or event['kind'] != 'tool_result':
        raise ValueError('Unknown or foreign retained reporting receipt')
    data = event['data']
    start = case.event(data.get('started_id', ''))
    if (data.get('tool') != 'forsic_reporting' or 'error' in data or data.get('execution_rejected')
            or not start or start['kind'] != 'tool_start'
            or start['data'].get('tool') != 'forsic_reporting'
            or start['session'] != event['session']
            or not isinstance(start['data'].get('arguments'), dict)
            or start['data']['arguments'].get('action') != action
            or start['data'].get('execution_rejected')):
        raise ValueError('Use a successful native reporting receipt for action=' + action)
    return data, start['data']['arguments']


def _citation(case, state, citation):
    from .host import find, source_record
    from .model_pages import select
    from .measurement import field_role
    keys = {'source_id', 'source_version', 'pointer', 'literal', 'byte_start'}
    if not isinstance(citation, dict) or set(citation) != keys:
        raise ValueError('Retained citation has an invalid structure')
    source = find(state, 'sources', citation['source_id'])
    event = case.event(source['id'])
    if source['version'] != citation['source_version'] or source_record(event)['version'] != source['version']:
        raise ValueError('Stale citation source version; review the original retained source again')
    value = select(event['data'], citation['pointer'])
    literal, start = citation['literal'], citation['byte_start']
    if not isinstance(literal, str) or not literal or type(start) is not int or start < 0:
        raise ValueError('Select an actual nonempty retained string span')
    if isinstance(value, str):
        raw = value.encode('utf-8')
    else:
        from .retained_scalar import number_literal
        raw = number_literal(value).encode('utf-8')
        if start != 0 or literal.encode('utf-8') != raw:
            raise ValueError('Numeric citations must select the whole canonical JSON number at byte_start=0')
    fragment = literal.encode('utf-8')
    end = start + len(fragment)
    if raw[start:end] != fragment:
        raise ValueError('Citation is not the exact retained UTF-8 field span')
    role = field_role(event['data'], citation['pointer']) if isinstance(value, str) else 'metadata'
    view = ('full_field' if start == 0 and end == len(raw) else 'exact_excerpt') if role == 'body' else 'metadata'
    return source, {'source_id': source['id'], 'pointer': citation['pointer'],
                    'byte_start': start, 'byte_end': end, 'presented_view': view}


def _source_choices(case, state, eid):
    """Replay a native source page; presentation may derive its exact source only."""
    from .host import find, source_record, encoded
    from .model_pages import source_view
    data, args = _receipt(case, eid, 'source')
    from .source_handles import resolve_source_handle
    args = resolve_source_handle(case, args)
    if state is None:
        # Source identity is the complete retained evidence return, independent
        # of questions, missions and report projections. Use current()'s exact
        # source membership predicate; the receipt and every selected span are
        # still revalidated below against the original event.
        from ..notes import EVIDENCE_TOOLS
        selected = case.event(args['source_id']) if isinstance(args.get('source_id'), str) else None
        sources = ([source_record(selected)] if selected and selected['kind'] == 'tool_result'
                   and selected['data'].get('tool') in EVIDENCE_TOOLS else [])
        state = {'sources': sources}
    source = find(state, 'sources', args.get('source_id'))
    event = case.event(source['id'])
    if source_record(event)['version'] != source['version']:
        raise ValueError('Retained source changed; review it before selecting a citation')
    contract = data.get('presentation_contract')
    if contract not in (None, 'acquisition-context-v1'):
        raise ValueError('Unknown retained source presentation contract')
    paging = data.get('paging_contract')
    if paging not in (None, 'source-continuation-v1'):
        raise ValueError('Unknown retained source paging contract')
    coordinates = data.get('source_coordinate_contract')
    if coordinates not in (None, 'source-coordinates-v1'):
        raise ValueError('Unknown retained source coordinate contract')
    expected = source_view(source, event['data'], args, include_acquisition_context=contract is not None,
                           include_continuation=paging is not None, include_coordinate_calls=coordinates is not None,
                           case=case if 'source_handle' in data else None)
    actual = {key: value for key, value in data.items() if key not in ('tool', 'started_id')}
    # Python equality aliases True/1/1.0; replay must preserve retained JSON types.
    if actual != expected or encoded(actual) != encoded(expected):
        raise ValueError('Source receipt does not match its retained source request and version')
    choices = []
    if expected.get('view_kind') == 'retained_literal_locations':
        for match in expected['matches']:
            citation = deepcopy(match['citation'])
            _citation(case, state, citation)
            choices.append(citation)
    elif (isinstance(expected.get('text'), str) and expected['text']
          and isinstance(expected.get('pointer'), str) and type(expected.get('byte_start')) is int):
        citation = {'source_id': source['id'], 'source_version': source['version'],
                    'pointer': expected['pointer'], 'literal': expected['text'],
                    'byte_start': expected['byte_start']}
        _citation(case, state, citation)
        choices.append(citation)
    return expected, choices


def citation_choices(case, result):
    """Add navigable refs only to the model copy, after the raw return is saved."""
    view = deepcopy(result)
    if result.get('error') or result.get('tool') != 'forsic_reporting' or 'source_ref' not in result:
        return view
    try:
        expected, choices = _source_choices(case, None, result.get('evidence_id'))
    except (ValueError, KeyError, TypeError) as exc:
        view['citation_ref_unavailable'] = str(exc)
        return view
    if expected.get('view_kind') == 'retained_literal_locations':
        for index, match in enumerate(view['matches']):
            # The literal and displayed range stay visible; the legacy input
            # object remains in the ledger, not a second model handoff format.
            match.pop('citation', None)
            match['citation_ref'] = f"C1:{result['evidence_id']}:{index}"
    elif choices:
        view['citation_ref'] = f"C1:{result['evidence_id']}:0"
    return view


def _selection(case, state, value):
    match = REF.fullmatch(value) if isinstance(value, str) else None
    if not match:
        raise ValueError('Use an unchanged C1 citation_ref returned with a source match or exact text page')
    eid, index = match.group(1), int(match.group(2))
    _, choices = _source_choices(case, state, eid)
    if index >= len(choices):
        raise ValueError('citation_ref does not select a presented span; zero matches and index previews have no citation')
    return choices[index]


def _targets(case, state, payload, goal_id, session_id):
    from .host import find, ref, source_record
    from .investigation_state import check_question_revision, pending_result
    mission = find(state, 'missions', payload['mission_id'])
    question = find(state, 'questions', mission['question_ref']['id'])
    check_question_revision(question, {'expected_revision': question['revision']},
                            goal_id=goal_id, session_id=session_id)
    if mission.get('question_definition_version') != question['definition_version']:
        raise ValueError('Mission belongs to a superseded question definition')
    result = find(state, 'sources', payload['result_id'])
    if mission['reuse_result_refs']:
        owned = ref('source', result) in mission['reuse_result_refs']
    else:
        owned = result['id'] in mission['result_ids']
    if not owned:
        raise ValueError('Result has no native execution receipt for this mission. Explicitly create a retained-result mission with the selected result_ids; no mission or result binding was inferred.')
    event = case.event(result['id'])
    if source_record(event)['version'] != result['version']:
        raise ValueError('Stale result version; review its retained source')
    if pending_result(event['data']):
        raise ValueError('Queued/running work is not a returned result; inspect the same native job')
    return mission, question, result


def _binding(state, mission):
    from .investigation_state import hash_value
    test = next((test for test in state['tests'] if test['id'] == 'T-' + mission['id']), None)
    return hash_value({'mission_version': mission['version'], 'state': mission['state'],
        'execution_start_ids': mission['execution_start_ids'], 'result_ids': mission['result_ids'],
        'reuse_result_refs': mission['reuse_result_refs'], 'input_refs': mission['input_refs'],
        'counterevidence_refs': mission['preserved_counterevidence_refs'],
        'test': {key: deepcopy(test.get(key)) for key in (
            'id', 'version', 'result_ref', 'physical_job_ref', 'execution_state', 'assessment_state')} if test else None})


def judgment_contract(mission, result_data, citations):
    """Describe the selected mission's existing prerequisites, without judging it."""
    from .investigation_state import failed_result
    allowed = ['inconclusive', 'no_match_in_scope', 'partial', 'unavailable']
    blocked = {}
    for outcome, field in (('supports', 'support_rule'), ('refutes', 'refute_rule'), ('found', None)):
        missing = []
        if field and not mission.get(field):
            missing.append(field)
        if not citations:
            missing.append('selected exact source observations')
        if missing:
            blocked[outcome] = missing
        else:
            allowed.append(outcome)
    failed = failed_result(result_data)
    if failed:
        for outcome in allowed:
            if outcome != 'unavailable':
                blocked[outcome] = ['original result failed; this is not a negative forensic finding']
        allowed = ['unavailable']
    return {key: deepcopy(mission.get(key)) for key in (
        'design_timing', 'target_proposition', 'required_view', 'support_rule', 'refute_rule', 'inconclusive_rule')} | {
        'eligible_outcomes': allowed, 'unmet_prerequisites': blocked,
        'failed_result': failed, 'selected_citation_count': len(citations),
        'semantic_approval': False,
        'meaning': 'These are declared input prerequisites, not a recommended conclusion or proof of entailment. Choose a justified local outcome. Do not change the meaning or invent a rule to obtain acceptance.'}


def prepare(case, state, payload, goal_id='', session_id=''):
    """Resolve only explicit selections. Case.invoke persists the returned basis."""
    from .host import find, ref, check_citation_view
    _validate(payload, 'assessment_basis')
    mission, question, result = _targets(case, state, payload, goal_id, session_id)
    allowed = mission['reuse_result_refs'] + mission['input_refs'] + mission['preserved_counterevidence_refs']
    allowed += [ref('source', find(state, 'sources', rid)) for rid in mission['result_ids']]
    allowed_sources = {item['id']: item['version'] for item in allowed if item['kind'] == 'source'}
    citations, ranges = [], []
    sources = {result['id']: result}
    for selected in payload['citation_refs']:
        from .grounded_judgment import selection
        citation = selection(case, state, selected)
        source, span = _citation(case, state, citation)
        if allowed_sources.get(source['id']) != source['version']:
            raise ValueError('Selected citation was not part of this mission input; use an explicit retained-result mission for additional results')
        check_citation_view(mission['required_view'],span['presented_view'],len(citations))
        citations.append(citation)
        ranges.append({'citation_ref': selected, **span})
        sources[source['id']] = source
    basis = {'kind': KIND, 'case_id': case.config.get('case_id'),
             'scope_ref': {'id': state['scope']['id'], 'version': state['scope']['version']},
             'owner': {'session_id': session_id, 'goal_id': goal_id},
             'question': {key: deepcopy(question[key]) for key in ('id', 'revision', 'definition_version', 'work_state')},
             'mission': {'id': mission['id'], 'version': mission['version']},
             'result': {'id': result['id'], 'version': result['version']},
             'citation_refs': deepcopy(payload['citation_refs']), 'citations': citations,
             'source_refs': [{'id': source['id'], 'version': source['version']} for source in sources.values()],
             'binding_signature': _binding(state, mission), 'retained_ranges': ranges,
             'judgment_contract': judgment_contract(mission, case.event(result['id'])['data'], citations),
             'retained_sources': [{key: deepcopy(source[key]) for key in (
                 'id', 'coverage', 'source_generation_group')} for source in sources.values()]}
    return {'assessment_basis': basis, 'semantic_approval': False,
            'meaning': 'Prepared explicit retained selections only. No assessment, new collection, mission, or question judgment was saved.'}


def validate_commit(case, state, basis_receipt_id, goal_id='', session_id=''):
    """Revalidate under the existing writer's lock before inserting any mutation."""
    data, request = _receipt(case, basis_receipt_id, 'assessment_basis')
    basis = data.get('assessment_basis')
    if not isinstance(basis, dict) or basis.get('kind') != KIND:
        raise ValueError('Use a current assessment basis receipt, not a source/result/state receipt. A basis from an earlier contract must be explicitly prepared again; it is not upgraded automatically.')
    if basis.get('case_id') != case.config.get('case_id'):
        raise ValueError('Assessment basis belongs to another case')
    if basis.get('owner') != {'session_id': session_id, 'goal_id': goal_id}:
        raise ValueError('Assessment basis belongs to another native conversation or goal')
    payload = request.get('payload')
    expected = prepare(case, state, payload, goal_id=goal_id, session_id=session_id)
    if basis != expected['assessment_basis']:
        raise ValueError('Stale assessment basis: question revision/definition, mission state/binding, test, or selected source changed. Review the current state and explicitly prepare again; versions were not upgraded.')
    return deepcopy(basis)


def expand(case, state, payload, goal_id='', session_id=''):
    """Produce legacy compact input; host.assess remains the final strict validator."""
    _validate(payload, 'assess_basis')
    basis = validate_commit(case, state, payload['basis_receipt_id'], goal_id=goal_id, session_id=session_id)
    contract = basis['judgment_contract']
    if payload['outcome'] not in contract['eligible_outcomes']:
        raise ValueError('Outcome prerequisites not met: ' + json.dumps({
            'outcome': payload['outcome'], 'missing': contract['unmet_prerequisites'].get(payload['outcome']),
            'judgment_contract': contract}, ensure_ascii=False) + '. No assessment was saved. Review the actual rule and evidence; do not switch conclusions only to pass validation.')
    proposal = {key: deepcopy(value) for key, value in payload.items() if key != 'basis_receipt_id'}
    proposal.update(expected_revision=basis['question']['revision'],
                    mission_id=basis['mission']['id'], mission_version=basis['mission']['version'],
                    result_id=basis['result']['id'], result_version=basis['result']['version'],
                    citations=deepcopy(basis['citations']))
    return proposal


def basis_view(result):
    """Keep the durable basis internal; the model copies only its receipt ID."""
    if result.get('error') or not isinstance(result.get('assessment_basis'), dict):
        return deepcopy(result)
    basis = result['assessment_basis']
    return {key: deepcopy(value) for key, value in result.items() if key != 'assessment_basis'} | {
        'basis_receipt_id': result['evidence_id'], 'mission_id': basis['mission']['id'],
        'result_id': basis['result']['id'], 'question_id': basis['question']['id'],
        'question_revision': basis['question']['revision'], 'citation_refs': deepcopy(basis['citation_refs']),
        'retained_sources': deepcopy(basis['retained_sources']),
        'judgment_contract': deepcopy(basis['judgment_contract']),
        'semantic_approval': False, 'next_tool': 'forsic_assess',
        'required_judgment_fields': ['operation_id', 'outcome', 'reasoning_summary', 'limitations', 'next_check'],
        'next_step': 'Call forsic_assess with basis_receipt_id and your explicit local judgment. This basis neither evaluates meaning nor closes the question.'}


def selected_schema():
    legacy=schemas()['assess_basis']
    properties={k:deepcopy(v) for k,v in legacy['properties'].items() if k!='basis_receipt_id'}
    properties.update(deepcopy(schemas()['assessment_basis']['properties']))
    properties['expected_revision']={'type':'integer','minimum':1,'description':'Current owning question revision; do not increment it yourself.'}
    return {'type':'object','properties':properties,'required':[k for k in legacy['required'] if k!='basis_receipt_id']+['mission_id','result_id','citation_refs','expected_revision'],'additionalProperties':False}


def expand_selected(case,state,payload,goal_id='',session_id=''):
    """One call for an optional planned test; old basis receipts remain readable."""
    from .investigation_state import input_problems
    problems=input_problems(payload,selected_schema())
    if problems:raise ValueError('Invalid test evaluation: '+str(problems)+'. No assessment was saved.')
    selected={k:payload[k] for k in ('mission_id','result_id','citation_refs')}
    basis=prepare(case,state,selected,goal_id,session_id)['assessment_basis']
    if payload['expected_revision']!=basis['question']['revision']:
        raise ValueError('Question revision changed; review its current answer before evaluating')
    proposal={k:deepcopy(v) for k,v in payload.items() if k not in ('citation_refs',)}
    proposal.update(mission_version=basis['mission']['version'],result_version=basis['result']['version'],citations=basis['citations'])
    return proposal
