"""Registered native recovery and review contracts; only synthetic case storage."""
from copy import deepcopy
import json
import unittest
import uuid
from unittest.mock import Mock, patch

from jsonschema import Draft202012Validator
from tools.schema_sanitizer import sanitize_tool_schemas

from forsic_plugin import register
from forsic_plugin.capabilities import capability
from forsic_plugin.investigation_context import native_goal_binding
from forsic_plugin.report_driven import host, investigation_state
from forsic_plugin.report_driven.grounded_judgment import read_refs
import test_investigation_context as fixtures


class RecoveryNativePathsTests(unittest.TestCase):
    setUp = fixtures.InvestigationContextTests.setUp
    bind = fixtures.InvestigationContextTests.bind
    session = fixtures.InvestigationContextTests.session
    save_note = fixtures.InvestigationContextTests.save_note

    def registry(self):
        ctx = Mock()
        ctx.get_config.side_effect = {'intake_root': str(self.intake)}.get
        self.stack.enter_context(patch('forsic_plugin.intake.Intake.case', return_value=self.case))
        self.stack.enter_context(patch('socket.create_connection', side_effect=AssertionError('Offline test')))
        self.stack.enter_context(patch('agent.auxiliary_client.call_llm', side_effect=AssertionError('No model call')))
        register(ctx)
        return {c.kwargs['name']: c.kwargs for c in ctx.register_tool.call_args_list}

    def binding(self):
        return {**native_goal_binding(self.sid, intake_root=self.intake, native_db=self.native),
            'question_contract_version': 'v2', 'independent_review_options': {
                'session_id': self.sid, 'intake_root': str(self.intake), 'native_db': str(self.native)}}

    def test_actual_sanitized_schemas_expose_review_payload_and_shell_discovery(self):
        registered = self.registry()
        def schema(name):
            return sanitize_tool_schemas([{'type': 'function', 'function': registered[name]['schema']}])[0]['function']['parameters']
        reporting = schema('forsic_reporting')
        self.assertIn('review_inbox', reporting['properties']['action']['enum'])
        self.assertFalse(list(Draft202012Validator(reporting).iter_errors(
            {'action': 'review_inbox', 'review_id': 'review', 'finding_id': 'finding'})))
        response = schema('forsic_review_response')
        validator = Draft202012Validator(response)
        payload = {'operation_id': 'one-operation', 'question_id': 'Q-current', 'expected_revision': 1,
            'review_id': 'review', 'finding_id': 'finding', 'disposition': 'scope_limited',
            'reason': 'Acquisition lacks history.', 'limitation': 'Execution remains unknown.', 'citation_refs': []}
        self.assertFalse(list(validator.iter_errors(payload)))
        for changed in ({'disposition': 'accepted'}, {'expected_revision': '1'}, {'reason': ''}, {'extra': 'unknown'}):
            with self.subTest(changed=changed):
                self.assertTrue(list(validator.iter_errors({**payload, **changed})))
        for field in response['required']:
            missing = deepcopy(payload); missing.pop(field)
            with self.subTest(missing=field):self.assertTrue(list(validator.iter_errors(missing)))
        self.assertTrue(list(validator.iter_errors({'action':'review_response','payload':payload})))
        missing = deepcopy(payload); missing.pop('limitation')
        self.assertTrue(list(validator.iter_errors(missing)))
        shell = schema('forsic_shell')
        self.assertIn('capabilities', shell['properties']['action']['enum'])
        self.assertFalse(list(Draft202012Validator(shell).iter_errors({'action': 'capabilities'})))
        self.assertTrue(capability('forsic_reporting', {'action': 'review_inbox'})['comparison_readonly'])
        self.assertFalse(capability('forsic_reporting', {'action': 'review_response'})['comparison_readonly'])

    def assert_recovery(self, result):
        self.assertEqual(result['error_code'], 'stale_report_reference')
        self.assertEqual(result['delivery_status'], 'stale_report_reference')
        self.assertFalse(result['finalized']); self.assertFalse(result['published'])
        self.assertTrue(result['draft_preserved'])
        self.assertEqual(result['recovery'], {'tool': 'forsic_reporting', 'arguments': {
            'action': 'render', 'question_ids': ['Q-'+self.note['note_id']]}})
        self.assertEqual(result['report_reference']['bundle_id'], self.rendered['bundle_id'])
        self.assertEqual(result['next_step'][:7], 'Run the')
        retained = self.case.event(result['evidence_id'])['data']
        self.assertEqual(retained['recovery'], result['recovery'])
        self.assertEqual(retained['error_code'], result['error_code'])

    def stale_report(self):
        self.rendered = host.invoke(self.case, {'action': 'render', 'allow_partial': True, '_session_id': self.sid,
            '_goal_id': self.binding()['goal_id']})
        corrected = deepcopy(self.note)
        corrected['revision'] += 1
        corrected['answer'] = 'A retained correction changes the answer represented by the draft.'
        self.case.record('note', corrected, self.sid)
        return {'action': 'finalize', 'bundle_id': self.rendered['bundle_id']}

    def test_case_invoke_preserves_structured_report_recovery_in_retained_result(self):
        request = self.stale_report()
        before = host.current(self.case)['questions']
        result = json.loads(self.case.invoke('forsic_reporting', request, self.sid,
            request_context=self.binding()))
        self.assert_recovery(result)
        self.assertEqual(host.current(self.case)['questions'], before)
        with self.case.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM events WHERE kind='report_finalized'").fetchone()[0], 0)

    def test_registered_handler_keeps_case_invoke_recovery_fields(self):
        handler = self.registry()['forsic_reporting']['handler']
        result = json.loads(handler(self.stale_report(), session_id=self.sid))
        self.assert_recovery(result)

    def test_registered_inbox_resolves_trusted_binding_without_snapshot_replay(self):
        handler = self.registry()['forsic_reporting']['handler']
        with patch.object(host, 'current', side_effect=AssertionError('No report state replay')):
            result = json.loads(handler({'action': 'review_inbox'}, session_id=self.sid))
        self.assertNotIn('error', result, result)
        self.assertEqual(result['kind'], 'independent_review_inbox')
        self.assertEqual(result['conversation_id'], self.sid)
        self.assertEqual(result['goal_id'], self.binding()['goal_id'])
        self.assertEqual(result['questions'][0]['id'], 'Q-'+self.note['note_id'])
        self.assertEqual(result['questions'][0]['revision'], self.note['revision'])

    def test_registered_shell_capabilities_reaches_existing_backend_without_target_or_command(self):
        handler = self.registry()['forsic_shell']['handler']
        observed = {'action': 'capabilities', 'representation': 'derived_command_output',
            'original_evidence': False, 'stdout': 'Synthetic bounded executable discovery', 'stderr': '',
            'execution': {'state': 'returned', 'exit_code': 0, 'termination_confirmed': True},
            'runtime_capabilities': {'runtime_verified': True, 'state': 'observed',
                'target_format_support': 'not_checked', 'recovery_execution': 'not_attempted'}}
        with patch('forsic_plugin.shell_workspace.invoke', return_value=observed) as backend:
            result = json.loads(handler({'action': 'capabilities'}, session_id=self.sid))
        self.assertNotIn('error', result, result)
        self.assertEqual(backend.call_args.args, (self.case, {'action': 'capabilities'}, self.sid))
        self.assertEqual(result['runtime_capabilities']['recovery_execution'], 'not_attempted')
        retained = self.case.event(result['evidence_id'])['data']
        self.assertEqual(retained['action'], 'capabilities')
        self.assertEqual(retained['runtime_capabilities'], observed['runtime_capabilities'])

    def test_unavailable_binding_rejects_review_response_before_writer(self):
        args = {'action': 'review_response', 'payload': {'operation_id': uuid.uuid4().hex}}
        before = host.current(self.case)['questions']
        with patch('forsic_plugin.report_driven.review_actions.respond', side_effect=AssertionError('Writer must not run')):
            result = json.loads(self.case.invoke('forsic_reporting', args, self.sid,
                request_context={'status': 'unavailable'}))
            self.assertEqual(result['failure_stage'], 'admission')
            self.assertIn('binding is unavailable', result['error'])
            with patch('forsic_plugin.investigation_context.native_goal_binding', return_value={'status': 'unavailable'}):
                registered = self.registry()
                via_handler = json.loads(registered['forsic_reporting']['handler'](args, session_id=self.sid))
            self.assertEqual(via_handler['failure_stage'], 'admission')
            self.assertIn('binding is unavailable', via_handler['error'])
        self.assertEqual(host.current(self.case)['questions'], before)

    def test_registered_question_writer_receives_current_case_goal_and_explicit_scope(self):
        handler = self.registry()['forsic_question']['handler']
        payload = {'operation_id': uuid.uuid4().hex, 'question': 'What is established in this acquisition?',
            'scope': 'Only the supplied synthetic request and process records.',
            'answer': 'The common execution identity is unconfirmed.'}
        with patch.object(investigation_state, 'update_question', wraps=investigation_state.update_question) as writer:
            result = json.loads(handler(payload, session_id=self.sid))
        self.assertNotIn('error', result, result)
        passed = writer.call_args.args
        self.assertEqual(passed[2]['scope'], payload['scope'])
        self.assertEqual(passed[3]['_session_id'], self.sid)
        self.assertEqual(passed[3]['_goal_id'], self.binding()['goal_id'])
        self.assertEqual(passed[3]['_session_lineage'], [self.sid])
        question = next(q for q in host.current(self.case)['questions'] if q['question'] == payload['question'])
        self.assertEqual(question['scope'], payload['scope'])
        self.assertEqual(question['session_id'], self.sid)

    def test_registered_review_response_uses_same_owned_question_writer(self):
        handler = self.registry()['forsic_reporting']['handler']
        data = json.loads(self.case.invoke('forsic_read', {'path': 'request.log'}, self.sid))
        cite = read_refs(self.case, data)['presented_spans'][0]['citation_ref']
        question = next(q for q in host.current(self.case)['questions'] if q['id'] == 'Q-'+self.note['note_id'])
        payload = {'operation_id': uuid.uuid4().hex, 'question_id': question['id'],
            'expected_revision': question['revision'], 'review_id': 'synthetic-retained-review',
            'finding_id': 'synthetic-finding', 'disposition': 'investigated',
            'reason': 'The retained request is observed; execution success remains unconfirmed.', 'citation_refs': [cite]}
        # Immutable advice parsing is independently tested; this fixture isolates
        # native registration, trusted binding and the real atomic writer.
        projected = {'status': 'received', 'findings': [{
            'review_id': payload['review_id'], 'finding_id': payload['finding_id'], 'impact': 'material'}]}
        with patch('forsic_plugin.independent_review.review_state', return_value=projected), \
                patch.object(investigation_state, 'update_question', wraps=investigation_state.update_question) as writer:
            result = json.loads(handler({'action': 'review_response', 'payload': payload}, session_id=self.sid))
        self.assertNotIn('error', result, result)
        passed = writer.call_args.args
        self.assertEqual(passed[2]['expected_revision'], question['revision'])
        self.assertEqual(passed[3]['_session_id'], self.sid)
        self.assertEqual(passed[3]['_goal_id'], self.binding()['goal_id'])
        saved = next(q for q in host.current(self.case)['questions'] if q['id'] == question['id'])
        self.assertEqual(saved['scope'], question['scope'])
        self.assertEqual(saved['review_responses'][0]['reason'], payload['reason'])


if __name__ == '__main__': unittest.main()
