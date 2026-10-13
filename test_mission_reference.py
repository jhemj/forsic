"""Offline native-handler reference contracts; synthetic ledger and stub image I/O only."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import base64
import json
import socket
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import test_efficiency_contracts as efficiency
from test_report_driven import fixture
from forsic_plugin.report_driven.host import current, find
from forsic_plugin.report_driven.investigation_state import hash_value, normalized_tool_arguments


class AbruptExit(BaseException):
    pass


class MissionReferenceTests(unittest.TestCase):
    def setUp(self):
        for target in ('socket.socket.connect', 'socket.create_connection'):
            guard = patch(target, side_effect=AssertionError('offline reference test forbids network'))
            guard.start(); self.addCleanup(guard.stop)
        self.x = efficiency.EfficiencyContracts()
        self.x.setUp()
        self.addCleanup(self.x.doCleanups)
        branches = self.x.schemas['forsic_mission']['parameters']['properties']['tool_arguments']['anyOf']
        self.native_schemas = {branch['title']: {k: v for k, v in branch.items() if k != 'title'}
                               for branch in branches}
        q = find(current(self.x.case), 'questions', self.x.qid)
        bound = self.x.call('forsic_question', {
            'operation_id': 'bind-reference-test', 'question_id': q['id'],
            'expected_revision': q['revision'], 'answer': q['answer'],
            'reason': 'Bind this synthetic question to its native owner'})
        self.assertNotIn('error', bound, bound)
        self.effective = {'path': 'synthetic.e01', 'action': 'read',
                          'file_path': '/records/request.log', 'start_line': 1, 'length': 1200}
        self.body = {'path': 'synthetic.e01', 'file_path': '/records/request.log',
                     'lines': [{'line': 1, 'text': 'job=maintenance requested target=/opt/task'}],
                     'line_start': 1, 'line_end': 1, 'coverage_complete': True}

    def plan(self, arguments=None, **changes):
        payload = {**self.x.plan(), 'tool_name': 'forsic_image_files',
                   'tool_arguments': deepcopy(self.effective if arguments is None else arguments), **changes}
        saved = self.x.call('forsic_mission', payload)
        self.assertNotIn('error', saved, saved)
        return saved

    @staticmethod
    def reference(saved):
        return {'path': 'synthetic.e01',
                'mission_id': saved['mission']['id'], 'mission_version': saved['mission']['version']}

    def invoke(self, args, **context):
        return json.loads(self.x.case.invoke('forsic_image_read_mission', args, 'synthetic-session',
            request_context={'goal_id': 'synthetic-goal', 'conversation_id': 'synthetic-session', **context},
            tool_schemas=self.native_schemas))

    def rejected(self, args, **context):
        with patch.object(self.x.case, 'image_files') as backend:
            out = self.invoke(args, **context)
            self.assertIn('error', out, out)
            self.assertTrue(out['execution_rejected'], out)
            self.assertFalse(out['backend_started'], out)
            backend.assert_not_called()
            start = self.x.case.event(out['started_id'])['data']
            self.assertTrue(start['execution_rejected'])
            self.assertNotIn('mission_context', start)
            return out

    def test_exact_reference_reads_once_and_preserves_both_argument_origins(self):
        saved = self.plan()
        submitted = {**self.reference(saved), 'reason': 'Read the exact saved plan'}
        original = deepcopy(submitted)
        with patch.object(self.x.case, 'image_files', return_value=self.body) as backend:
            result = self.x.call('forsic_image_read_mission', submitted)
            self.assertNotIn('error', result, result)
            backend.assert_called_once_with(self.effective)
        self.assertEqual(submitted, original)
        start = self.x.case.event(result['started_id'])['data']
        self.assertEqual(start['arguments'], self.effective)
        self.assertEqual(start['submitted_arguments'], submitted)
        self.assertEqual(start['submitted_tool'], 'forsic_image_read_mission')
        self.assertEqual(start['tool'], 'forsic_image_files')
        self.assertEqual(start['argument_resolution'], {
            'kind': 'saved_mission', 'mission_id': saved['mission']['id'],
            'mission_version': saved['mission']['version'],
            'arguments_hash': hash_value(normalized_tool_arguments(self.effective))})
        retained = self.x.case.event(result['evidence_id'])['data']
        self.assertEqual(retained['mission_context'], start['mission_context'])
        m = find(current(self.x.case), 'missions', saved['mission']['id'])
        self.assertEqual(m['state'], 'unassessed')
        self.assertEqual(m['result_ids'], [result['evidence_id']])

    def test_read_bytes_and_exact_source_assessment_use_existing_ledger(self):
        saved = self.plan({**self.effective, 'action': 'read_bytes', 'offset': 2})
        text = self.body['lines'][0]['text']
        byte_body = {'path': 'synthetic.e01', 'file_path': '/records/request.log',
                     'text': text, 'base64': base64.b64encode(text.encode()).decode(),
                     'byte_start': 2, 'byte_end': 2 + len(text.encode())}
        with patch.object(self.x.case, 'image_files', return_value=byte_body):
            result = self.x.call('forsic_image_read_mission', self.reference(saved))
        self.assertNotIn('error', result, result)
        self.assertNotIn('base64', result)
        self.assertEqual(result['omitted_fields'], ['base64'])
        self.assertEqual(self.x.case.event(result['evidence_id'])['data']['base64'], byte_body['base64'])
        located = self.x.call('forsic_reporting', {
            'action': 'source', 'source_id': result['evidence_id'], 'query': 'requested'})
        match = next(match for match in located['matches'] if match['role'] == 'body')
        citation = next(m['citation'] for m in self.x.case.event(located['evidence_id'])['data']['matches'] if m['role']=='body')
        q = find(current(self.x.case), 'questions', self.x.qid)
        basis = self.x.call('forsic_assessment_basis', {
            'mission_id': saved['mission']['id'], 'result_id': result['evidence_id'],
            'citation_refs': [match['citation_ref']]})
        self.assertNotIn('error', basis, basis)
        assessed = self.x.call('forsic_assess', {
            'operation_id': 'assess-reference', 'basis_receipt_id': basis['evidence_id'],
            'outcome': 'inconclusive', 'reasoning_summary': 'A request is observed; execution is not established',
            'limitations': ['One synthetic acquired record'], 'next_check': 'Seek an independent execution record'})
        self.assertNotIn('error', assessed, assessed)
        state = current(self.x.case)
        assessment = find(state, 'assessments', assessed['assessment']['id'])
        test = find(state, 'tests', assessment['test_ref']['id'])
        observation = find(state, 'observations', assessment['observation_refs'][0]['id'])
        self.assertEqual(observation['literal'], citation['literal'])
        self.assertEqual(observation['byte_start'], citation['byte_start'])
        self.assertEqual(test['result_ref']['id'], result['evidence_id'])
        self.assertEqual(find(state, 'questions', self.x.qid)['answer'], q['answer'])

    def test_missing_or_invalid_reference_never_selects_the_latest_draft(self):
        saved = self.plan(); ref = self.reference(saved)
        bad = [{k: v for k, v in ref.items() if k != missing}
               for missing in ('path', 'mission_id', 'mission_version')]
        bad += [{**ref, key: value} for key in ('path', 'mission_id', 'mission_version')
                for value in ('', None, 0, False, [], {})]
        bad += [{**ref, 'mission_id': 'M-nonexistent'}, {**ref, 'mission_version': 'old'},
                {**ref, 'path': 'another.e01'}]
        for args in bad:
            with self.subTest(args=args): self.rejected(args)
        self.assertEqual(find(current(self.x.case), 'missions', saved['mission']['id'])['state'], 'draft')

    def test_reference_rejects_all_explicit_argument_overrides(self):
        ref = self.reference(self.plan())
        for key, value in {'file_path': self.effective['file_path'], 'length': 1200,
                           'offset': 0, 'volume_offset': 0, 'member_index': 0,
                           'cursor': {}, 'text': 'requested', 'action': 'from_mission'}.items():
            with self.subTest(key=key): self.rejected({**ref, key: value})

    def test_other_session_goal_case_and_unavailable_binding_reject_before_io(self):
        saved = self.plan(); ref = self.reference(saved)
        self.rejected(ref, conversation_id='another-session')
        self.rejected(ref, goal_id='another-goal')
        self.rejected(ref, goal_id='')
        self.rejected(ref, status='unavailable')
        with tempfile.TemporaryDirectory() as root:
            other, _, _ = fixture(Path(root))
            with patch.object(other, 'image_files') as backend:
                result = json.loads(other.invoke('forsic_image_read_mission', ref, 'synthetic-session',
                    request_context={'goal_id': 'synthetic-goal', 'conversation_id': 'synthetic-session'},
                    tool_schemas=self.native_schemas))
                self.assertTrue(result['execution_rejected']); backend.assert_not_called()

    def test_reference_without_registered_schema_cannot_execute_legacy_plan(self):
        ref = self.reference(self.plan())
        with patch.object(self.x.case, 'image_files') as backend:
            out = json.loads(self.x.case.invoke('forsic_image_read_mission', ref, 'synthetic-session',
                request_context={'goal_id': 'synthetic-goal', 'conversation_id': 'synthetic-session'}))
            self.assertTrue(out['execution_rejected']); backend.assert_not_called()

    def test_failed_physical_result_does_not_become_a_reference_retry(self):
        saved = self.plan(); ref = self.reference(saved)
        with patch.object(self.x.case, 'image_files', return_value={'error': 'synthetic parser failure'}) as backend:
            out = self.x.call('forsic_image_read_mission', ref)
            self.assertEqual(out.get('error'), 'synthetic parser failure', out)
            backend.assert_called_once()
        self.assertEqual(find(current(self.x.case), 'missions', saved['mission']['id'])['state'], 'failed')
        self.rejected(ref)

    def test_assessed_legacy_partial_mission_cannot_execute_again_by_reference(self):
        from forsic_plugin.report_driven.investigation_state import expand_mission
        state = current(self.x.case)
        gap = deepcopy(find(state, 'gaps', self.x.plan()['gap_id']))
        gap.update(feasible_next_action='design_test', reason='A new bounded native read can discriminate the question')
        changed = self.x.call('forsic_reporting', {'action': 'gap',
            'snapshot_id': state['meta']['snapshot_id'], 'payload': gap})
        self.assertNotIn('error', changed, changed)
        state = current(self.x.case)
        payload = {**self.x.plan(), 'tool_name': 'forsic_image_files',
                   'tool_arguments': deepcopy(self.effective)}
        legacy = expand_mission(state, payload, goal_id='synthetic-goal', session_id='synthetic-session')
        legacy['compact_contract'] = False
        legacy['mission_kind'] = find(state, 'gaps', legacy['gap_ref']['id'])['feasible_next_action']
        saved = json.loads(self.x.case.invoke('forsic_reporting', {
            'action': 'mission', 'snapshot_id': state['meta']['snapshot_id'],
            'operation_id': 'legacy-before-result-plan', 'payload': legacy}, 'synthetic-session',
            request_context={'goal_id': 'synthetic-goal', 'conversation_id': 'synthetic-session'},
            tool_schemas=self.native_schemas))
        self.assertNotIn('error', saved, saved)
        ref = self.reference(saved)
        with patch.object(self.x.case, 'image_files', return_value=self.body) as backend:
            result = self.invoke(ref)
            self.assertNotIn('error', result, result)
            backend.assert_called_once_with(self.effective)
        state = current(self.x.case)
        source = find(state, 'sources', result['evidence_id'])
        basis = self.x.call('forsic_assessment_basis', {
            'mission_id': saved['mission']['id'], 'result_id': source['id'], 'citation_refs': []})
        self.assertNotIn('error', basis, basis)
        assessed = self.x.call('forsic_assess', {
            'operation_id': 'assess-legacy-partial',
            'basis_receipt_id': basis['evidence_id'],
            'outcome': 'inconclusive', 'reasoning_summary': 'The retained read does not settle execution',
            'limitations': ['One acquired synthetic record'], 'next_check': 'Review an independent record'})
        self.assertNotIn('error', assessed, assessed)
        before = find(current(self.x.case), 'missions', saved['mission']['id'])
        self.assertEqual(before['state'], 'partial')
        self.assertEqual(len(before['execution_start_ids']), 1)
        self.assertEqual(before['result_ids'], [result['evidence_id']])
        self.rejected(ref)
        after = find(current(self.x.case), 'missions', saved['mission']['id'])
        self.assertEqual(after['execution_start_ids'], before['execution_start_ids'])
        self.assertEqual(after['result_ids'], before['result_ids'])

    def test_current_question_definition_and_work_state_remain_required(self):
        saved = self.plan(); ref = self.reference(saved)
        # Feed the real admission path valid historical projections with a changed
        # current question. This isolates execution admission from closure-writing policy.
        for changes in ({'definition_version': 'different-definition'}, {'work_state': 'scoped_closed'}):
            state = deepcopy(current(self.x.case))
            find(state, 'questions', self.x.qid).update(changes)
            with self.subTest(changes=changes), patch('forsic_plugin.report_driven.host.current', return_value=state):
                self.rejected(ref)

    def test_answer_wording_change_does_not_invalidate_saved_definition(self):
        saved = self.plan(); q = find(current(self.x.case), 'questions', self.x.qid)
        changed = self.x.call('forsic_question', {'operation_id': 'wording-only',
            'question_id': q['id'], 'expected_revision': q['revision'],
            'answer': 'The connection remains undetermined.', 'reason': 'Clarify wording without changing the question'})
        self.assertNotIn('error', changed, changed)
        with patch.object(self.x.case, 'image_files', return_value=self.body) as backend:
            out = self.x.call('forsic_image_read_mission', self.reference(saved))
            self.assertNotIn('error', out, out); backend.assert_called_once()

    def test_only_before_result_image_read_plans_can_resolve(self):
        saved = self.plan(); ref = self.reference(saved)
        mutations = [{'design_timing': 'after_result'},
                     {'reuse_result_refs': [{'kind': 'source', 'id': 'retained', 'version': 'v1'}]},
                     {'proposed_tool_name': 'forsic_read'}]
        mutations += [{'tool_arguments': {**self.effective, 'action': action}}
                      for action in ('search', 'archive_read', 'from_mission')]
        mutations += [{'tool_arguments': {**self.effective, 'mission_id': 'nested-forbidden'}}]
        mutations += [{'tool_arguments': {k: v for k, v in self.effective.items() if k != 'file_path'}}]
        for change in mutations:
            state = deepcopy(current(self.x.case))
            find(state, 'missions', saved['mission']['id']).update(change)
            with self.subTest(change=change), patch('forsic_plugin.report_driven.host.current', return_value=state):
                self.rejected(ref)

    def test_recursive_planning_is_rejected_and_schema_keeps_direct_required_fields(self):
        from tools.schema_sanitizer import sanitize_tool_schemas
        saved = self.plan(); ref = self.reference(saved)
        before = current(self.x.case)['missions']
        out = self.x.call('forsic_mission', {**self.x.plan(), 'operation_id': 'recursive',
            'tool_name': 'forsic_image_files', 'tool_arguments': ref})
        self.assertIn('error', out)
        self.assertEqual(current(self.x.case)['missions'], before)
        wire = sanitize_tool_schemas([{'type': 'function', 'function':
            deepcopy(self.x.schemas['forsic_image_files'])}])[0]['function']['parameters']
        self.assertEqual(wire['required'], ['path', 'action'])
        self.assertNotIn('from_mission', wire['properties']['action']['enum'])
        entry = sanitize_tool_schemas([{'type': 'function', 'function':
            deepcopy(self.x.schemas['forsic_image_read_mission'])}])[0]['function']['parameters']
        self.assertEqual(set(entry['required']), {'path', 'mission_id', 'mission_version'})
        self.assertEqual(set(entry['properties']), {'path', 'mission_id', 'mission_version', 'reason'})
        self.assertFalse(entry['additionalProperties'])
        for field in entry['required']:
            self.assertEqual(entry['properties'][field]['type'], 'string')
            self.assertEqual(entry['properties'][field]['minLength'], 1)
        self.assertEqual(saved['next_call'], {'tool': 'forsic_image_read_mission', 'arguments': ref})
        plan_schema = self.x.schemas['forsic_mission']['parameters']
        self.assertNotIn('forsic_image_read_mission', plan_schema['properties']['tool_name']['enum'])
        rejected = self.x.call('forsic_mission', {**self.x.plan(), 'operation_id': 'recursive-alias',
            'tool_name': 'forsic_image_read_mission', 'tool_arguments': ref})
        self.assertIn('error', rejected)
        self.assertEqual(current(self.x.case)['missions'], before)

    def test_registered_entry_missing_fields_and_extra_action_keep_rejection_receipts(self):
        saved = self.plan(); ref = self.reference(saved)
        for bad in ({'path': ref['path']}, {**ref, 'action': 'from_mission'}):
            with self.subTest(bad=bad), patch.object(self.x.case, 'image_files') as backend:
                out = self.x.call('forsic_image_read_mission', bad)
                self.assertTrue(out['execution_rejected'], out)
                self.assertFalse(out['backend_started'])
                self.assertEqual(out['error_code'], 'invalid_arguments')
                self.assertNotIn('아직 증거가 접수되지', out['error'])
                start = self.x.case.event(out['started_id'])['data']
                self.assertEqual(start['submitted_arguments'], bad)
                self.assertEqual(start['submitted_tool'], 'forsic_image_read_mission')
                self.assertTrue(start['execution_rejected'])
                backend.assert_not_called()
        self.assertEqual(find(current(self.x.case), 'missions', saved['mission']['id'])['state'], 'draft')

    def test_legacy_internal_from_mission_remains_same_exact_native_path(self):
        saved = self.plan(); legacy = {**self.reference(saved), 'action': 'from_mission'}
        with patch.object(self.x.case, 'image_files', return_value=self.body) as backend:
            result = json.loads(self.x.case.invoke('forsic_image_files', legacy, 'synthetic-session',
                request_context={'goal_id': 'synthetic-goal', 'conversation_id': 'synthetic-session'},
                tool_schemas=self.native_schemas))
            self.assertNotIn('error', result, result)
            backend.assert_called_once_with(self.effective)
        self.assertEqual(result['mission_context']['mission_id'], saved['mission']['id'])
        self.rejected(self.reference(saved))

    def test_unbound_direct_read_is_not_silently_attached_to_draft(self):
        saved = self.plan()
        with patch.object(self.x.case, 'image_files', return_value=self.body):
            out = self.x.call('forsic_image_files', self.effective)
        self.assertNotIn('error', out, out)
        self.assertNotIn('mission_context', out)
        self.assertEqual(find(current(self.x.case), 'missions', saved['mission']['id'])['state'], 'draft')

    def test_concurrent_same_reference_never_starts_second_backend(self):
        saved = self.plan(); ref = self.reference(saved)
        entered = threading.Event(); release = threading.Event()
        def slow_backend(args):
            entered.set()
            if not release.wait(5): raise AssertionError('test failed to release synthetic backend')
            return self.body
        with patch.object(self.x.case, 'image_files', side_effect=slow_backend) as backend:
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(self.invoke, ref)
                try:
                    self.assertTrue(entered.wait(5))
                    second = pool.submit(self.invoke, ref).result(5)
                    self.assertTrue(second['execution_rejected'], second)
                    self.assertFalse(second['backend_started'])
                finally:
                    release.set()
                self.assertNotIn('error', first.result(5))
            backend.assert_called_once_with(self.effective)
        self.rejected(ref)

    def crash(self, where):
        saved = self.plan(); ref = self.reference(saved)
        original_record = self.x.case.record
        def crash_after_record(*args, **kwargs):
            original_record(*args, **kwargs)
            raise AbruptExit()
        if where == 'before_backend':
            patched = patch.object(self.x.case, 'image_files', side_effect=AbruptExit)
        else:
            patched = patch.object(self.x.case, 'record',
                side_effect=AbruptExit if where == 'before_result' else crash_after_record)
        with patch.object(self.x.case, 'image_files', return_value=self.body), patched:
            with self.assertRaises(AbruptExit): self.invoke(ref)
        mission = find(current(self.x.case), 'missions', saved['mission']['id'])
        self.assertEqual(mission['state'], 'unassessed' if where == 'after_result' else 'running')
        self.assertEqual(len(mission['execution_start_ids']), 1)
        self.assertEqual(len(mission['result_ids']), 1 if where == 'after_result' else 0)
        self.rejected(ref)

    def test_crash_after_start_commit_does_not_retry_uncertain_execution(self):
        self.crash('before_backend')

    def test_crash_after_backend_before_result_commit_does_not_retry(self):
        self.crash('before_result')

    def test_crash_after_result_commit_before_delivery_uses_retained_result(self):
        self.crash('after_result')


if __name__ == '__main__':
    unittest.main()
