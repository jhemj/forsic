"""Synthetic task presentation: result execution, local evaluation and answer stay separate."""
from copy import deepcopy
import json
import unittest
import tempfile

from forsic_plugin.task_focus import check_records, project_tasks


class TaskFocusTests(unittest.TestCase):
    def setUp(self):
        self.q = dict(id='Q-synthetic',revision=3,definition_version='definition-1',question='Was the request executed?',
            scope='one host',answer='The request alone does not establish execution',assessment='undetermined',
            work_state='active',target_proposition='request execution',claim_refs=[],next_checks=['Compare execution records'],
            alternatives=['An unexecuted request'],judgment_review_required=False)
        self.m = dict(id='M-check',version='mission-1',question_ref=dict(id=self.q['id']),
            question_definition_version='definition-1',state='completed',proposed_tool_name='forsic_read',why_it_matters='Compare execution records')
        self.t = dict(id='T-M-check',version='test-1',question_ref=dict(id=self.q['id']),
            result_ref=dict(kind='source',id='result-1',version='source-1'),execution_state='returned',assessment_state='assessed')
        self.a = dict(id='AS-local',version='1',test_ref=dict(kind='test',id=self.t['id'],version=self.t['version']),
            result_ref=self.t['result_ref'],outcome='inconclusive',reasoning_summary='Request observed, execution absent from this excerpt',limitations=['Partial record'],next_check='Compare independent record')
        self.state = dict(questions=[self.q],missions=[self.m],tests=[self.t],assessments=[self.a],claims=[],gaps=[])

    def test_exact_assessed_result_is_local_history_without_answer_promotion_or_mutation(self):
        before = deepcopy(self.state)
        body = project_tasks(self.state,[self.q])
        task = body['tasks'][0]
        self.assertTrue(task['checks'][0]['complete'])
        self.assertEqual(task['checks'][0]['result_ref'],self.t['result_ref'])
        self.assertEqual(task['assessment'],'undetermined')
        self.assertEqual(task['next_checks'],self.q['next_checks'])
        self.assertEqual(task['revision'],3)
        self.assertEqual(self.state,before)

    def test_pending_failed_unassessed_stale_and_changed_definition_never_complete(self):
        variants = [('execution_state','running'),('execution_state','failed'),
                    ('assessment_state','unassessed'),('assessment_state','stale')]
        for field,value in variants:
            with self.subTest(field=field,value=value):
                state = deepcopy(self.state); state['tests'][0][field] = value
                self.assertFalse(check_records(state,self.q)[0]['complete'])
        for field,value in [('judgment_review_required',True),('definition_version','definition-2')]:
            question = {**self.q,field:value}
            self.assertFalse(check_records(self.state,question)[0]['complete'])
        state = deepcopy(self.state);state['missions'][0]['state'] = 'failed'
        self.assertFalse(check_records(state,self.q)[0]['complete'])
        state = deepcopy(self.state);state['assessments'][0]['result_ref'] = {**self.t['result_ref'],'version':'stale'}
        self.assertFalse(check_records(state,self.q)[0]['complete'])

    def test_answer_revision_keeps_applicable_check_and_mission_free_question_is_available(self):
        question = {**self.q,'revision':4}
        self.assertTrue(check_records(self.state,question)[0]['complete'])
        state = {**self.state,'missions':[],'tests':[],'assessments':[]}
        task = project_tasks(state,[self.q])['tasks'][0]
        self.assertEqual(task['checks'],[])
        self.assertEqual(task['answer_is_model_assessment'],self.q['answer'])

    def test_actual_retained_assessment_projects_exact_test_and_result_versions(self):
        from test_report_driven import fixture, worked
        with tempfile.TemporaryDirectory() as root:
            case, _, _ = fixture(root)
            state = worked(case)
            task = project_tasks(state,state['questions'])['tasks'][0]
            # This legacy fixture updates an answer without a new aggregate
            # judgment, so its review-required marker keeps the check visible.
            self.assertTrue(state['questions'][0]['judgment_review_required'])
            self.assertEqual(task['check_counts']['completed'],0)
            self.assertEqual(task['checks'][0]['state'],'recheck')
            state['questions'][0]['judgment_review_required'] = False
            self.assertTrue(check_records(state,state['questions'][0])[0]['complete'])
            check = task['checks'][0]
            self.assertEqual(check['result_ref'],state['tests'][0]['result_ref'])
            self.assertEqual(check['assessment_ref']['id'],state['assessments'][0]['id'])
            self.assertEqual(task['work_state'],state['questions'][0]['work_state'])

    def test_card_preserves_long_answer_and_all_qualifications_before_omitting_it(self):
        question = {**self.q,'answer':'A bounded synthetic answer. '*20}
        claim = dict(id='F-synthetic',status='candidate',limitations=['first limit','second limit','third limit'],
                     assumptions=['declared assumption'])
        state = {**self.state,'questions':[question],'claims':[claim]}
        card = project_tasks(state,[question])['tasks'][0]
        self.assertEqual(card['answer_is_model_assessment'],question['answer'])
        self.assertEqual(card['judgments'][0]['limitations'],claim['limitations'])
        self.assertEqual(card['judgments'][0]['assumptions'],claim['assumptions'])

    def test_budget_omits_whole_qualified_card_and_exposes_lookup(self):
        question = {**self.q,'question':'Large synthetic question '*300}
        body = project_tasks({**self.state,'questions':[question]},[question],max_bytes=1200)
        self.assertEqual(body['tasks'],[])
        self.assertEqual(body['omitted_tasks'],1)
        self.assertEqual(body['details_call']['arguments']['action'],'state')
        self.assertLessEqual(len(json.dumps(body).encode()),1200)
