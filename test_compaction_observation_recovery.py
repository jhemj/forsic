"""Synthetic pressure on read-only compaction; no evidence/backend/model calls."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from forsic_plugin import investigation_context as context
from forsic_plugin.report_driven.host import current
import test_investigation_context as fixtures


class CompactionObservationRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.InvestigationContextTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.case, self.sid = self.fixture.case, self.fixture.sid

    def record(self, **data):
        return self.case.record('tool_result', {'tool':'forsic_image_files',
            'path':'synthetic.E01','filesystem_view':'target', **data}, self.sid)

    def pressure(self):
        for index in range(28):
            self.record(file_path='/var/log/synthetic-'+str(index % 7),
                lines=[{'line':1,'text':'retained synthetic original'}],
                representation='line_text', line_start=1,line_end=1,
                complete_file=index % 2 == 0)
        failed = self.record(file_path='/var/log/absent',outcome='not_found',
            error='Selected allocated path absent',error_type='FileNotFoundError',
            error_code='not_found',execution={'state':'failed'})
        self.case.record('tool_result',{'tool':'forsic_image_files','path':'FOREIGN.E01',
            'file_path':'/foreign','outcome':'not_found','error':'foreign'}, 'other-session')
        state = current(self.case)
        question = state['questions'][0]
        question.update(answer='The selected records leave execution unknown. '+('long qualification '*200),
            next_checks=['Compare an independent execution receipt. '+('detail '*60)],
            alternatives=['Alternative explanation remains possible. '+('detail '*60)])
        review = {'status':'current','requires_followup':True,
            'pending_finding_ids':['finding-'+str(i) for i in range(4)],
            'pending_findings':[{'review_id':'r'*32,'finding_id':'finding-'+str(i),
                'question':'Which independent record establishes this scope? '+('synthetic detail '*30)} for i in range(4)]}
        return state, failed, review

    def project(self, state, review, **kwargs):
        resolved = context.resolve_context(**self.fixture.kwargs())
        with patch('forsic_plugin.independent_review.review_state',return_value=review):
            text, _, pending = context.project_context(resolved,state,**kwargs)
        self.assertLessEqual(len(text.encode()),kwargs.get('max_bytes',5000))
        return json.loads(text), pending

    def test_large_question_four_pending_and_history_keep_recent_absence_and_navigation(self):
        state, failed, review = self.pressure()
        before = deepcopy(state)
        ledger_before = self.case.db.read_bytes()
        body, pending = self.project(state,review)
        self.assertEqual(body['independent_review']['pending_count'],4)
        self.assertGreaterEqual(len(pending),4)
        self.assertTrue(body['active_questions'])
        question = body['active_questions'][0]
        self.assertEqual(question['id'],state['questions'][0]['id'])
        self.assertEqual(question['details_call']['arguments']['question_id'],question['id'])
        self.assertIn('answer_is_model_assessment',question)
        self.assertTrue(question['judgments'])
        self.assertTrue(question['remaining_gap_examples'])
        self.assertEqual(question['remaining_gap_examples'][0]['read_call']['arguments']['question_id'],question['id'])
        self.assertTrue(body['observed_scope'],body)
        self.assertTrue(body['observed_targets'],body)
        self.assertTrue(any(ref['source_id'] != failed for target in body['observed_targets']
            for ref in target['source_refs']))
        facts = {item['source_id']:item for item in body['observed_scope']}
        self.assertIn(failed,facts)
        fact = facts[failed]
        self.assertEqual(fact['source_version'],next(s['version'] for s in state['sources'] if s['id']==failed))
        self.assertEqual(fact['observed']['outcome'],'not_found')
        self.assertEqual(fact['observed']['filesystem_view'],'target')
        self.assertEqual(fact['observed']['error_code'],'not_found')
        self.assertTrue(fact['observed']['error_present'])
        self.assertEqual(body['retrieval']['action'],'source')
        self.assertNotIn('FOREIGN',json.dumps(body))
        self.assertEqual(len(context.recent_observed_scope(context.resolve_context(**self.fixture.kwargs()),state)),
            len(body['observed_scope'])+body['omitted']['observed_scope'])
        self.assertEqual(body['scope_inventory']['counts']['targets'],
            len(body['observed_targets'])+body['omitted']['observed_targets']+body['scope_inventory']['omitted']['targets'])
        self.assertFalse(body['completion']['truth_approval'])
        self.assertEqual(state,before)
        self.assertEqual(self.case.db.read_bytes(),ledger_before)

    def test_long_korean_scope_and_rendered_report_keep_failure_and_current_obligations(self):
        state, failed, review = self.pressure()
        from test_derived_observation_scope import triage_receipt
        triage_receipt(self.case,self.sid,state='partial')
        failed = self.record(file_path='/var/log/absent-report',outcome='not_found',
            error='Selected allocated path absent',error_type='FileNotFoundError',
            error_code='not_found',scope='선택한 파일시스템 범위의 부재만 측정한다. '*6)
        updated = current(self.case)
        updated['questions'] = state['questions']
        goal = deepcopy(self.fixture.goal)
        goal.goal = '보존된 합성 자료에서 실행 여부와 근거의 한계를 확인하고 중요한 미확인 범위를 설명한다. ' * 20
        resolved = context.resolve_context(**self.fixture.kwargs())
        from hermes_cli.goals import goal_identity
        resolved.update(goal=goal, goal_id=goal_identity(goal))
        for question in updated['questions']:
            question['goal_id'] = resolved['goal_id']
        report = {'status':'derived_artifact_verified', 'artifact_is_completion':False,
            'bundle_id':'b'*64,'snapshot_id':'a'*64,'saved_content_revision':'d'*64,
            'current_content_revision':'c'*64,'stale':True, 'current_question_count':1,
            'current_questions_in_report':1,'current_questions_omitted':0,'files_verified':2,
            'note':'File existence/integrity and snapshot freshness do not certify the requested investigation.'}
        with patch.object(context,'_report_context',return_value=report), patch(
                'forsic_plugin.independent_review.review_state',return_value=review):
            text, _, _ = context.project_context(resolved,updated)
        body = json.loads(text)
        self.assertLessEqual(len(text.encode()),5000)
        self.assertTrue(body['active_questions'])
        self.assertTrue(body['active_questions'][0]['remaining_gap_examples'])
        self.assertEqual(body['independent_review']['pending_count'],4)
        self.assertTrue(any(item['source_id']==failed for item in body['observed_scope']),body)
        failure = next(item for item in body['observed_scope'] if item['source_id']==failed)
        self.assertEqual(failure['observed']['error_code'],'not_found')
        self.assertEqual(failure['observed']['file_path'],'/var/log/absent-report')
        self.assertTrue(failure['details_omitted'])
        self.assertTrue(body['active_questions'][0]['judgments'])
        sparse = body['work_inventory']
        self.assertEqual(sparse['state_count_default'],0)
        owned, missions, tests, _ = context._owned_state(resolved,updated)
        full = context.work_inventory(owned,missions,tests)
        for key in ('question_states','important_question_states'):
            self.assertEqual({name:sparse[key].get(name,sparse['state_count_default']) for name in full[key]},full[key])
        self.assertEqual(body['reports']['bundle_id'],report['bundle_id'])
        self.assertEqual(body['reports']['snapshot_id'],report['snapshot_id'])
        self.assertTrue(body['reports']['stale'])
        self.assertIn('current_content_revision',body['reports']['omitted_fields'])
        self.assertIn('requested_scope',body['omitted']['clipped_fields'])
        self.assertFalse(body['completion']['truth_approval'])

    def test_absent_hash_and_stat_are_admitted_without_invented_body_coverage(self):
        for action in ('hash','stat'):
            eid = self.record(file_path='/absent-'+action,outcome='not_found',error='absent',
                error_code='not_found',error_type='FileNotFoundError',action=action)
            state = current(self.case)
            resolved = context.resolve_context(**self.fixture.kwargs())
            fact = next(f for f in context.recent_observed_scope(resolved,state) if f['source_id']==eid)
            self.assertEqual(fact['observed']['file_path'],'/absent-'+action)
            self.assertEqual(fact['observed']['outcome'],'not_found')
            self.assertNotIn('complete_file',fact['observed'])
            self.assertNotIn('body_read',fact['observed'])

    def test_existing_derived_input_lookup_stays_available_with_observation_pressure(self):
        from test_derived_observation_scope import triage_receipt
        state, _, review = self.pressure()
        triage_receipt(self.case,self.sid,state='partial')
        updated = current(self.case)
        updated['questions'] = state['questions']
        body, _ = self.project(updated,review)
        self.assertTrue(body['active_questions'])
        self.assertEqual(body['independent_review']['pending_count'],4)
        self.assertTrue(body['derived_observations'])
        record = body['derived_observations'][0]
        self.assertEqual(record['read_call']['arguments']['action'],'source')
        self.assertEqual(record['read_call']['arguments']['pointer'],'/coverage')
        self.assertEqual(record['read_call']['arguments']['source_version'],next(
            s['version'] for s in updated['sources'] if s['id']==record['read_call']['arguments']['source_id']))
        self.assertFalse(body['completion']['truth_approval'])

    def test_scoped_smaller_budget_keeps_counts_and_current_lookup(self):
        state, _, review = self.pressure()
        body, _ = self.project(state,review,max_bytes=3500)
        self.assertEqual(body['independent_review']['pending_count'],4)
        self.assertEqual(body['scope_inventory']['counts']['targets'],8)
        self.assertEqual(body['active_questions'][0]['details_call']['arguments']['question_id'],state['questions'][0]['id'])
        self.assertGreater(body['omitted']['observed_scope'],0)


if __name__ == '__main__':
    unittest.main()
