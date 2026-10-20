"""Synthetic review repair through the actual case boundary and native schema."""
from copy import deepcopy
from unittest.mock import Mock, patch
import json
import unittest
import uuid

from jsonschema import Draft202012Validator
from forsic_plugin.context_results import summarize
from forsic_plugin.report_driven import host
from forsic_plugin.report_driven.investigation_state import input_problems
from forsic_plugin.report_driven.review_actions import response_schema, ReviewInputError
import test_review_actions as fixtures
from test_schema_presentation import registered
from tools.schema_sanitizer import sanitize_tool_schemas


class ReviewRecoveryContractTests(unittest.TestCase):
    # Reuse only fixture helpers, not the original suite's test methods.
    for _name in ('setUp', 'bind', 'session', 'save_note', 'setup_review', 'add_sources',
                  'kwargs', 'response', 'request_paths', 'resolved', 'prepare', 'call',
                  'args', 'start', 'cite', 'response_args'):
        locals()[_name] = getattr(fixtures.ReviewActionsTests, _name)
    del _name

    def native_call(self, args):
        return json.loads(self.case.invoke('forsic_reporting', args, self.sid,
            request_context={'goal_id': args['_goal_id'], 'conversation_id': self.sid,
                'session_lineage': [self.sid], 'question_contract_version': 'v2',
                'independent_review_options': args['_independent_review_options']}))

    def adapter_call(self, args, tool='forsic_review_response'):
        if not hasattr(self, 'handlers'):
            from forsic_plugin import register
            ctx = Mock()
            ctx.get_config.side_effect = lambda key: str(self.intake) if key == 'intake_root' else None
            register(ctx)
            self.handlers = {c.kwargs['name']: c.kwargs['handler'] for c in ctx.register_tool.call_args_list}
        return json.loads(self.handlers[tool](args, session_id=self.sid))

    def test_native_registration_is_flat_and_inbox_advertises_the_same_contract(self):
        tools = registered()
        response = tools['forsic_review_response']['parameters']
        self.assertEqual(set(response['required']), {'review_id', 'finding_id', 'disposition', 'reason',
            'citation_refs', 'operation_id', 'question_id', 'expected_revision', 'limitation'})
        self.assertNotIn('action', response['properties'])
        self.assertNotIn('payload', response['properties'])
        self.assertFalse(response['additionalProperties'])
        reporting = tools['forsic_reporting']['parameters']['properties']
        self.assertNotIn('review_response', reporting['action']['enum'])
        self.assertNotIn('payload', reporting)
        inbox = self.start()
        before = deepcopy(host.current(self.case)['questions'])
        reread = self.adapter_call({'action': 'review_inbox'}, 'forsic_reporting')
        for key, value in inbox.items():
            self.assertEqual(reread[key], value)
        self.assertEqual(host.current(self.case)['questions'], before)
        contract = inbox['response_contract']
        self.assertEqual(contract['tool'], 'forsic_review_response')
        self.assertEqual(contract['input_location'], 'top_level')
        self.assertEqual(contract['required'], response['required'])
        self.assertEqual(contract['conditional_fields']['scope_limited']['required'], ['limitation'])

    def test_eight_missing_native_fields_then_exact_repair_atomic_write_and_same_retry(self):
        item = self.start()['findings'][0]
        payload = self.response_args(item)['payload']
        before = deepcopy(host.current(self.case)['questions'])
        calls = len(self.calls)
        failed = self.adapter_call({})
        self.assertEqual(failed['error_code'], 'invalid_review_response')
        self.assertFalse(failed['write_applied'])
        missing = next(p for p in failed['invalid_fields'] if 'missing_fields' in p)
        self.assertEqual(missing['path'], 'top_level')
        self.assertEqual(set(missing['missing_fields']), set(response_schema()['required']))
        self.assertEqual(failed['recovery']['tool'], 'forsic_review_response')
        self.assertEqual(failed['recovery']['input_location'], 'top_level')
        self.assertEqual(failed['input_contract']['field_location'], 'top_level')
        self.assertEqual(host.current(self.case)['questions'], before)
        compact = json.loads(summarize('forsic_review_response', {}, json.dumps({**failed, 'padding': 'x' * 10000})))
        self.assertEqual(compact['invalid_fields'], failed['invalid_fields'])
        self.assertEqual(compact['recovery']['input_location'], 'top_level')
        from forsic_plugin.loop_comparison import project
        self.assertEqual(project('forsic_review_response', payload), {'deduplicate': False})
        accepted = self.adapter_call(payload)
        self.assertNotIn('error', accepted, accepted)
        after = host.current(self.case)['questions']
        self.assertEqual(len(after), len(before))
        question = next(q for q in after if q['id'] == payload['question_id'])
        self.assertEqual(question['revision'], payload['expected_revision'] + 1)
        self.assertEqual(len(question['review_responses']), 1)
        self.assertEqual(question['review_responses'][0]['citation_refs'], payload['citation_refs'])
        replay = self.adapter_call(payload)
        self.assertNotIn('error', replay, replay)
        self.assertEqual(host.current(self.case)['questions'], after)
        self.assertEqual(replay['question'], accepted['question'])
        self.assertEqual(len(self.calls), calls)

    def combined_update(self, finding, **changes):
        response = self.response_args(finding)['payload']
        item = {key: response[key] for key in
                ('review_id', 'finding_id', 'disposition', 'reason', 'citation_refs')}
        return {key: response[key] for key in ('operation_id', 'question_id', 'expected_revision')} | {
            'answer': '설정 기록은 확인했으나 정상 실행 여부는 아직 확인되지 않았다.',
            'reason': '반환 원문이 뒷받침하는 범위로 잠정 판단과 검토 답변을 함께 갱신한다.',
            'judgment': {'citation_refs': response['citation_refs'], 'assertion_kind': 'interpretation',
                'reasoning_summary': '설정의 존재와 실행 결과를 구별한다.',
                'limitations': ['실행 결과는 확인되지 않았다.']},
            'review_responses': [item], **changes}

    def test_native_question_atomically_updates_provisional_answer_and_review_response(self):
        from forsic_plugin.investigation_context import post_context_compaction
        initial = self.start()
        payload = self.combined_update(initial['findings'][0])
        calls = len(self.calls)
        before = deepcopy(host.current(self.case)['questions'])
        result = self.adapter_call(payload, 'forsic_question')
        self.assertNotIn('error', result, result)
        after = deepcopy(host.current(self.case)['questions'])
        self.assertEqual(len(after), len(before))
        question = next(q for q in after if q['id'] == payload['question_id'])
        self.assertEqual(question['revision'], payload['expected_revision'] + 1)
        self.assertEqual(question['answer'], payload['answer'])
        self.assertEqual(question['review_responses'][0]['citation_refs'],
                         payload['judgment']['citation_refs'])
        self.assertEqual(question['review_responses'][0]['citations'][0]['literal'],
                         'Observed configuration only; successful execution is unconfirmed.')
        replay = self.adapter_call(payload, 'forsic_question')
        self.assertNotIn('error', replay, replay)
        self.assertEqual(host.current(self.case)['questions'], after)
        inbox = self.adapter_call({'action': 'review_inbox'}, 'forsic_reporting')
        self.assertEqual(inbox['pending_count'], initial['pending_count'] - 1)
        projection = post_context_compaction(session_id=self.sid, intake_root=self.intake,
                                             native_db=self.native)
        self.assertIn(payload['question_id'], projection['context'])
        projected = json.loads(projection['context'])
        navigation = next(q for q in projected['answer_updates']['questions']
                          if q['question_id'] == payload['question_id'])
        self.assertEqual(navigation['expected_revision'], question['revision'])
        self.assertEqual(navigation['adopted_claim_count'], len(question['claim_refs']))
        self.assertGreater(navigation['adopted_claim_count'], 0)
        self.assertEqual(host.current(self.case)['questions'], after)
        self.assertEqual(len(self.calls), calls, 'Read/update/projection must not invoke another reviewer')

    def test_native_combined_update_rejects_invalid_review_evidence_without_partial_answer(self):
        item = self.start()['findings'][0]
        payload = self.combined_update(item)
        payload['review_responses'][0]['citation_refs'] = ['C2:invalid']
        before = deepcopy(host.current(self.case)['questions'])
        rejected = self.adapter_call(payload, 'forsic_question')
        self.assertIn('error', rejected)
        self.assertEqual(host.current(self.case)['questions'], before)
        repaired = deepcopy(payload)
        repaired['operation_id'] = uuid.uuid4().hex
        repaired['review_responses'][0]['citation_refs'] = repaired['judgment']['citation_refs']
        accepted = self.adapter_call(repaired, 'forsic_question')
        self.assertNotIn('error', accepted, accepted)
        after = deepcopy(host.current(self.case)['questions'])
        stale = {**repaired, 'operation_id': uuid.uuid4().hex, 'answer': '변경 시도'}
        conflict = self.adapter_call(stale, 'forsic_question')
        self.assertIn('error', conflict)
        self.assertEqual(host.current(self.case)['questions'], after)

    def test_native_conditional_repair_stale_revision_and_changed_retry_are_explicit(self):
        item = self.start()['findings'][0]
        payload = self.response_args(item, disposition='scope_limited', citation_refs=[])['payload']
        before = deepcopy(host.current(self.case)['questions'])
        for disposition in ('investigated', 'refuted'):
            failed = self.adapter_call({**payload, 'disposition': disposition})
            self.assertEqual(failed['error_code'], 'invalid_review_response')
            self.assertEqual(failed['invalid_fields'][0]['path'], 'citation_refs')
            self.assertEqual(host.current(self.case)['questions'], before)
        failed = self.adapter_call(payload)
        self.assertEqual(failed['invalid_fields'][0]['path'], 'limitation')
        self.assertEqual(host.current(self.case)['questions'], before)
        payload['limitation'] = 'Execution history is not present in acquired records.'
        accepted = self.adapter_call(payload)
        self.assertNotIn('error', accepted, accepted)
        after = deepcopy(host.current(self.case)['questions'])
        changed = {**payload, 'reason': 'A different explicit interpretation.'}
        conflict = self.adapter_call(changed)
        self.assertEqual(conflict['error_code'], 'mutation_payload_conflict')
        self.assertFalse(conflict['write_applied'])
        changed['operation_id'] = uuid.uuid4().hex
        stale = self.adapter_call(changed)
        self.assertEqual(stale['error_code'], 'invalid_review_response')
        self.assertEqual(stale['recovery']['input_location'], 'top_level')
        self.assertTrue(any(p.get('current_revision') == after[0]['revision'] for p in stale['invalid_fields']))
        self.assertEqual(host.current(self.case)['questions'], after)

    def test_native_writer_rejects_foreign_case_and_foreign_evidence_without_adopting_them(self):
        from test_report_driven import fixture
        item = self.start()['findings'][0]
        payload = self.response_args(item)['payload']
        before = deepcopy(host.current(self.case)['questions'])
        other, _, _ = fixture(self.intake / 'cases' / 'different-case')
        other_before = deepcopy(host.current(other)['questions'])
        # The native session resolves the original case, so a mismatched adapter case must fail.
        with patch('forsic_plugin.intake.Intake.case', return_value=other):
            rejected = self.adapter_call(payload)
        self.assertIn('another case', rejected['error'])
        self.assertEqual(host.current(other)['questions'], other_before)
        foreign = {**payload, 'citation_refs': [self.cite('different-conversation')]}
        rejected = self.adapter_call(foreign)
        self.assertIn('another conversation', rejected['error'])
        self.assertEqual(host.current(self.case)['questions'], before)

    def test_legacy_reporting_handler_still_accepts_pinned_response_calls(self):
        inbox = self.start()
        item = inbox['findings'][0]
        payload = self.response_args(item)['payload']
        fallback = inbox['response_contract']['legacy_input']
        self.assertIn('Only if forsic_review_response is absent', fallback['when'])
        self.assertEqual(fallback['tool'], 'forsic_reporting')
        legacy_args = {**fallback['arguments'], fallback['input_location']: payload}
        self.assertEqual(legacy_args, {'action': 'review_response', 'payload': payload})
        legacy = self.adapter_call(legacy_args, fallback['tool'])
        self.assertNotIn('error', legacy, legacy)
        after = deepcopy(host.current(self.case)['questions'])
        replay = self.adapter_call(legacy_args, fallback['tool'])
        self.assertNotIn('error', replay, replay)
        self.assertEqual(host.current(self.case)['questions'], after)
        self.assertEqual(replay['question'], legacy['question'])
        native_replay = self.adapter_call(payload)
        self.assertNotIn('error', native_replay, native_replay)
        self.assertEqual(host.current(self.case)['questions'], after)

    def test_conditional_fields_are_in_public_schema_and_runtime_validation(self):
        item = self.start()['findings'][0]
        valid = self.response_args(item)['payload']
        registered_tool = registered()['forsic_review_response']
        published = registered_tool['parameters']
        wire = sanitize_tool_schemas([{'type': 'function', 'function': deepcopy(registered_tool)}])[0]
        wire_payload = wire['function']['parameters']
        # Native transport drops top-level combinators for strict providers. It must retain
        # an explicit limitation input; the writer enforces nonblank scoped limits.
        self.assertEqual(wire_payload['required'], response_schema(native=True)['required'])
        self.assertNotIn('anyOf', wire_payload)
        self.assertIn('Required for scope_limited', wire_payload['properties']['limitation']['description'])
        self.assertIn('at least one', wire_payload['properties']['citation_refs']['description'])
        wire_validator = Draft202012Validator(wire_payload)
        self.assertFalse(wire_validator.is_valid(valid))
        self.assertTrue(wire_validator.is_valid({**valid, 'limitation': ''}))
        self.assertFalse(wire_validator.is_valid({}))
        for changes, accepted in (({}, True), ({'citation_refs': []}, False),
                ({'disposition': 'scope_limited', 'citation_refs': []}, False),
                ({'disposition': 'scope_limited', 'citation_refs': [],
                  'limitation': 'History is not present in the acquired scope.'}, True),
                ({'disposition': 'refuted', 'citation_refs': []}, False)):
            payload = {**valid, **changes}
            with self.subTest(changes=changes):
                self.assertEqual(not input_problems(payload, response_schema()), accepted)
                self.assertEqual(Draft202012Validator(response_schema()).is_valid(payload), accepted)
                native_payload = {**payload, 'limitation': payload.get('limitation', '')}
                # The published combinator still checks citation requirements. Empty
                # scoped limits remain a stricter canonical validation responsibility.
                published_accepted = accepted or changes == {'disposition': 'scope_limited', 'citation_refs': []}
                self.assertEqual(Draft202012Validator(published).is_valid(native_payload), published_accepted)

    def test_sanitized_required_limitation_preserves_legacy_and_scoped_writer_contracts(self):
        tool = registered()['forsic_review_response']
        wire = sanitize_tool_schemas([{'type': 'function', 'function': deepcopy(tool)}])[0]['function']
        validator = Draft202012Validator(wire['parameters'])
        item = self.start()['findings'][0]
        payload = self.response_args(item)['payload']
        original = deepcopy(payload)
        before = deepcopy(host.current(self.case)['questions'])
        self.assertNotIn('limitation', payload)
        self.assertFalse(validator.is_valid(payload))
        for disposition in ('investigated', 'refuted', 'scope_limited'):
            with self.subTest(disposition=disposition):
                candidate = {**payload, 'disposition': disposition}
                self.assertFalse(validator.is_valid(candidate))
                self.assertTrue(validator.is_valid({**candidate, 'limitation': ''}))
        scoped = {**payload, 'disposition': 'scope_limited', 'citation_refs': []}
        for limitation in (None, '', '   '):
            candidate = {**scoped, **({'limitation': limitation} if limitation is not None else {})}
            rejected = self.adapter_call(candidate)
            self.assertEqual(rejected['error_code'], 'invalid_review_response')
            self.assertFalse(rejected['write_applied'])
            self.assertEqual(host.current(self.case)['questions'], before)
        native_payload = {**payload, 'limitation': ''}
        accepted = self.adapter_call(native_payload)
        self.assertNotIn('error', accepted, accepted)
        after = deepcopy(host.current(self.case)['questions'])
        question = next(q for q in after if q['id'] == payload['question_id'])
        response = question['review_responses'][0]
        self.assertEqual(response['reason'], payload['reason'])
        self.assertEqual(response['disposition'], payload['disposition'])
        self.assertEqual(response.get('limitation', ''), '')
        prior = next(q for q in before if q['id'] == question['id'])
        for field in ('question', 'scope', 'answer'):
            self.assertEqual(question[field], prior[field])
        self.assertEqual(payload, original)
        self.assertEqual(native_payload['limitation'], '')
        # Legacy clients may still omit the non-applicable field and update via
        # the same writer, without generating a limitation or changing meaning.
        legacy = {**payload, 'operation_id': uuid.uuid4().hex,
                  'expected_revision': question['revision']}
        result = self.adapter_call({'action': 'review_response', 'payload': legacy}, 'forsic_reporting')
        self.assertNotIn('error', result, result)
        saved = next(q for q in host.current(self.case)['questions'] if q['id'] == question['id'])
        self.assertEqual(saved['review_responses'], question['review_responses'])

    def test_limitation_description_does_not_alias_other_response_fields(self):
        from forsic_plugin.report_driven.review_responses import schema
        for shape in (response_schema(), registered()['forsic_review_response']['parameters']):
            fields = shape['properties']
            description = fields['limitation']['description']
            self.assertIn('Required for scope_limited', description)
            for name in ('review_id', 'finding_id', 'reason'):
                self.assertNotEqual(fields[name].get('description'), description)
            self.assertNotEqual(fields['citation_refs']['items'].get('description'), description)
        fields = schema()['items']['properties']
        fields['limitation']['description'] = 'synthetic limitation description'
        for name in ('review_id', 'finding_id', 'reason'):
            self.assertNotIn('description', fields[name])
        self.assertNotIn('description', fields['citation_refs']['items'])

    def test_legacy_missing_limitation_guides_canonical_repair_with_pinned_fallback(self):
        item = self.start()['findings'][0]
        payload = self.response_args(item, disposition='scope_limited', citation_refs=[])['payload']
        legacy_args = {'action': 'review_response', 'payload': payload}
        before = deepcopy(host.current(self.case)['questions'])
        calls = len(self.calls)
        for unused in range(3):
            failed = self.adapter_call(legacy_args, 'forsic_reporting')
            self.assertEqual(failed['error_code'], 'invalid_review_response')
            self.assertFalse(failed['write_applied'])
            self.assertEqual(host.current(self.case)['questions'], before)
        recovery = failed['recovery']
        self.assertEqual(recovery['tool'], 'forsic_review_response')
        self.assertEqual(recovery['input_location'], 'top_level')
        self.assertNotIn('action', recovery)
        fallback = recovery['legacy_input']
        self.assertIn('absent from the pinned tool menu', fallback['when'])
        self.assertEqual(fallback['tool'], 'forsic_reporting')
        self.assertEqual(fallback['arguments'], {'action': 'review_response'})
        self.assertEqual(fallback['input_location'], 'payload')
        field = failed['invalid_fields'][0]
        self.assertEqual(field['path'], 'limitation')
        self.assertEqual(field['missing_fields'], ['limitation'])
        self.assertEqual(field['field_schema'], response_schema()['properties']['limitation'])
        self.assertEqual(failed['input_contract']['tool'], recovery['tool'])
        self.assertIn('limitation', failed['input_contract']['allowed_fields'])
        compact = json.loads(summarize('forsic_reporting', legacy_args,
            json.dumps({**failed, 'padding': 'x' * 10000})))
        self.assertEqual(compact['invalid_fields'], failed['invalid_fields'])
        self.assertEqual(compact['recovery'], recovery)
        self.assertLessEqual(len(json.dumps(compact, ensure_ascii=False, separators=(',', ':'))), 2000)
        # A failed request has not acquired a limitation or changed its payload.
        self.assertNotIn('limitation', payload)
        retained = self.case.event(failed['evidence_id'])['data']
        self.assertEqual(retained['invalid_fields'][0]['path'], 'payload.limitation')
        self.assertNotIn('limitation', self.case.event(retained['started_id'])['data']['arguments']['payload'])
        payload['limitation'] = 'The acquired records do not cover execution history.'
        accepted = self.adapter_call(payload, recovery['tool'])
        self.assertNotIn('error', accepted, accepted)
        after = deepcopy(host.current(self.case)['questions'])
        question = next(q for q in after if q['id'] == payload['question_id'])
        self.assertEqual(question['revision'], payload['expected_revision'] + 1)
        self.assertEqual(question['review_responses'][0]['limitation'], payload['limitation'])
        legacy_replay = self.adapter_call(legacy_args, 'forsic_reporting')
        self.assertNotIn('error', legacy_replay, legacy_replay)
        self.assertEqual(host.current(self.case)['questions'], after)
        self.assertEqual(len(self.calls), calls)

    def test_native_missing_limitation_keeps_exact_repair_schema_after_pruning(self):
        item = self.start()['findings'][0]
        payload = self.response_args(item, disposition='scope_limited', citation_refs=[])['payload']
        before = deepcopy(host.current(self.case)['questions'])
        failed = self.adapter_call(payload)
        self.assertEqual(failed['invalid_fields'][0]['path'], 'limitation')
        self.assertEqual(failed['invalid_fields'][0]['field_schema'],
                         response_schema()['properties']['limitation'])
        self.assertEqual(failed['recovery']['tool'], 'forsic_review_response')
        self.assertIn('limitation', failed['input_contract']['required_fields'])
        compact = json.loads(summarize('forsic_review_response', payload,
            json.dumps({**failed, 'padding': 'x' * 10000})))
        self.assertEqual(compact['invalid_fields'], failed['invalid_fields'])
        self.assertEqual(compact['recovery'], failed['recovery'])
        self.assertEqual(host.current(self.case)['questions'], before)
        self.assertNotIn('limitation', payload)
        payload['limitation'] = 'The acquired scope contains no execution history.'
        accepted = self.adapter_call(payload)
        self.assertNotIn('error', accepted, accepted)

    def test_missing_limitation_returns_exact_field_and_same_payload_repair(self):
        item = self.start()['findings'][0]
        args = self.response_args(item, disposition='scope_limited', citation_refs=[])
        before = deepcopy(host.current(self.case)['questions'])
        failed = self.native_call(args)
        self.assertEqual(failed['error_code'], 'invalid_review_response')
        self.assertFalse(failed['write_applied'])
        self.assertEqual(failed['invalid_fields'][0]['path'], 'payload.limitation')
        self.assertEqual(failed['recovery']['input_location'], 'payload')
        self.assertEqual(failed['recovery']['read_call']['arguments'], {'action': 'review_inbox'})
        self.assertEqual(host.current(self.case)['questions'], before)
        retained = self.case.event(failed['evidence_id'])['data']
        for key in ('invalid_fields', 'recovery', 'error_code'):
            self.assertEqual(retained[key], failed[key])
        args['payload']['limitation'] = 'The acquired records do not cover execution history.'
        accepted = self.native_call(args)
        self.assertNotIn('error', accepted)
        answered = host.invoke(self.case, self.args())
        self.assertEqual(answered['pending_count'], 4)

    def test_stale_revision_and_idempotency_conflict_do_not_retry_writes(self):
        item = self.start()['findings'][0]
        args = self.response_args(item)
        accepted = host.invoke(self.case, args)
        unchanged = self.case.db.read_bytes()
        self.assertEqual(host.invoke(self.case, args), accepted)
        self.assertEqual(self.case.db.read_bytes(), unchanged)
        before = deepcopy(host.current(self.case)['questions'])
        altered = deepcopy(args)
        altered['payload']['reason'] = 'A changed payload must be reviewed separately.'
        conflict = self.native_call(altered)
        self.assertEqual(conflict['error_code'], 'mutation_payload_conflict')
        self.assertFalse(conflict['write_applied'])
        self.assertEqual(conflict['recovery']['read_call']['arguments'], {'action': 'state'})
        altered['payload']['operation_id'] = uuid.uuid4().hex
        stale = self.native_call(altered)
        self.assertEqual(stale['error_code'], 'invalid_review_response')
        self.assertTrue(any(p.get('current_revision') == before[0]['revision']
                            for p in stale['invalid_fields']))
        self.assertEqual(stale['recovery']['action'], 'review_response')
        self.assertEqual(host.current(self.case)['questions'], before)
        for result in (conflict, stale):
            compact = json.loads(summarize('forsic_reporting', args,
                json.dumps({**result, 'padding': 'x' * 10000})))
            self.assertEqual(compact['error_code'], result['error_code'])
            self.assertEqual(compact['recovery']['read_call'], result['recovery']['read_call'])

    def test_large_invalid_diagnostics_are_omitted_whole(self):
        exc = ReviewInputError([{'path': 'payload', 'extra_fields': ['x' * 20000]}])
        self.assertEqual(exc.details['invalid_fields'], [])
        self.assertEqual(exc.details['omitted_invalid_fields'], 1)
        self.assertLess(len(str(exc)), 1000)


if __name__ == '__main__':
    unittest.main()
