"""Synthetic task presentation: result execution, local evaluation and answer stay separate."""
from copy import deepcopy
import json
import unittest
import tempfile
import sqlite3
from contextlib import closing
from unittest.mock import patch

from forsic_plugin.task_focus import check_records, project_tasks


class SelectedTaskReadTests(unittest.TestCase):
    def setUp(self):
        from test_report_driven import fixture, worked
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.case, _, _ = fixture(self.tmp.name)
        worked(self.case)

    def selected(self):
        from forsic_plugin.task_focus import _selected_task_state
        return _selected_task_state(self.case)

    def assert_same_cards(self):
        from forsic_plugin.report_driven.host import current
        full = current(self.case)
        narrow = self.selected()
        def semantic(value):
            if isinstance(value,dict):return {k:semantic(v) for k,v in value.items() if k != 'snapshot_id'}
            if isinstance(value,list):return [semantic(v) for v in value]
            return value
        self.assertEqual(semantic(project_tasks(narrow, narrow['questions'])), semantic(project_tasks(full, full['questions'])))
        return narrow

    def test_large_unselected_evidence_and_reporting_history_do_not_enter_task_read(self):
        from forsic_plugin.task_focus import retained_task_state
        self.case.record('tool_result', dict(tool='forsic_read', path='unselected.log',
            lines=[dict(line=1, text='UNSELECTED_BODY' * 400000)]))
        self.case.record('tool_result', dict(tool='forsic_reporting', unused='REPORT_HISTORY' * 400000))
        before = self.case.db.read_bytes()
        narrow = self.assert_same_cards()
        decoded = []
        original = json.loads
        def decode(value, *args, **kwargs):
            decoded.append(value)
            return original(value, *args, **kwargs)
        with patch('json.loads', side_effect=decode):
            bounded = retained_task_state(self.case)
        self.assertTrue(bounded['_report_selection_only'])
        self.assertLess(narrow['_task_focus_read']['bytes'], 100000)
        self.assertFalse(any(isinstance(x, str) and ('UNSELECTED_BODY' in x or 'REPORT_HISTORY' in x) for x in decoded))
        self.assertEqual(before, self.case.db.read_bytes())

    def test_unrelated_event_count_does_not_exhaust_selected_record_budget(self):
        from forsic_plugin.task_focus import retained_task_state
        with self.case.connect() as db:
            db.executemany('INSERT INTO events VALUES (?,?,?,?,?)',
                ((f'irrelevant-{i}', i, 'tool_start', '', '{"tool":"forsic_reporting"}') for i in range(20001)))
        narrow = retained_task_state(self.case)
        self.assertIsNotNone(narrow)
        self.assertLess(narrow['_report_read']['records'], 30)

    def test_required_source_budget_is_preserved_and_read_failure_is_not_no_tasks(self):
        from forsic_plugin.task_focus import retained_task_state
        # Enlarge an already referenced source; do not weaken its exact version.
        with self.case.connect() as db:
            db.execute("UPDATE events SET data=json_set(data,'$.retained_context',?) "
                       "WHERE id=(SELECT id FROM events WHERE kind='tool_result' LIMIT 1)", ('x' * (4 * 1024 * 1024),))
        self.assertIsNone(retained_task_state(self.case))

    def test_projection_releases_read_lock_before_canonical_revalidation(self):
        from forsic_plugin.report_driven.host import current
        def project(case, *, events):
            with closing(sqlite3.connect(case.db, timeout=0.05)) as writer:
                with writer:
                    writer.execute('INSERT INTO events VALUES (?,?,?,?,?)', ('during-projection', 1, 'test', '', '{}'))
            self.assertNotIn('during-projection', {e['id'] for e in events})
            return current(case, events=events)
        with patch('forsic_plugin.report_driven.host.current', side_effect=project):
            self.selected()

    def test_definition_change_and_legacy_correction_match_canonical_judgment(self):
        from forsic_plugin.notes import note
        from forsic_plugin.report_driven.host import current
        state = current(self.case); q = state['questions'][0]
        note(self.case, dict(action='save', note_id=q['id'][2:], revision=q['revision'],
            question='Different target proposition', answer='Corrected interpretation, not a completed check',
            correction_reason='The original scope was too broad'))
        self.assert_same_cards()

    def test_unselected_external_reference_does_not_change_scope_definition(self):
        from forsic_plugin.report_driven.host import current
        self.case.record('tool_result', dict(tool='forsic_intel', source_kind='external_intelligence',
            indicator='example.invalid', lookup_at='2026-01-01', limitation='Reputation only'))
        self.assertEqual(self.selected()['scope'], current(self.case)['scope'])

    def test_native_start_return_failure_and_unpaired_receipts_keep_exact_checks(self):
        from test_mission_native_tools import MissionNativeToolsTests
        from forsic_plugin.report_driven.host import current
        from forsic_plugin.task_focus import _selected_task_state
        for missing in (False, True):
            with self.subTest(missing=missing):
                fixture = MissionNativeToolsTests(); fixture.setUp()
                try:
                    path = 'absent.log' if missing else 'request.log'
                    mission = fixture.plan(arguments={'path': path})
                    def same():
                        full = current(fixture.case); narrow = _selected_task_state(fixture.case)
                        self.assertEqual(project_tasks(narrow, narrow['questions']), project_tasks(full, full['questions']))
                        return project_tasks(narrow, narrow['questions'])['tasks'][0]
                    self.assertFalse(same()['checks'][0]['complete'])
                    returned = json.loads(fixture.case.invoke('forsic_read', dict(path=path,
                        mission_id=mission['id'], mission_version=mission['version']),
                        'synthetic-session', request_context={'goal_id':'synthetic-goal'}))
                    self.assertEqual('error' in returned, missing)
                    check = same()['checks'][0]
                    self.assertFalse(check['complete'])
                    self.assertEqual(check['state'], 'failed' if missing else 'returned')
                    self.assertEqual(check['result_ref']['id'], returned['evidence_id'])
                    unpaired = fixture.case.event(returned['evidence_id'])['data']
                    unpaired['started_id'] = 'missing-start'
                    fixture.case.record('tool_result', unpaired)
                    self.assertEqual(same()['checks'][0], check)
                finally:
                    fixture.tearDown()


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
        self.state = dict(meta={'snapshot_id':'synthetic'},questions=[self.q],missions=[self.m],tests=[self.t],assessments=[self.a],claims=[],gaps=[])

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
