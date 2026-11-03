"""Native human report follow-up over retained questions, offline only."""
from copy import deepcopy
import json
import sqlite3
import unittest
from unittest.mock import patch
from hermes_cli.goals import GoalState, goal_identity
from hermes_cli.goals_runtime import RUNTIME_HANDOFF_REASON
import test_investigation_context as fixtures
from forsic_plugin.investigation_context import native_goal_binding, resolve_context, report_source_goal_id, report_followup_selection
from forsic_plugin.report_driven import host
from forsic_plugin.report_driven.narrative import selection
from forsic_plugin.report_driven.report_reference import resolve_report, ReportReferenceError, scope_from_args
from forsic_plugin import independent_review as review

class ReportFollowupTests(unittest.TestCase):
    setUp = fixtures.InvestigationContextTests.setUp
    bind = fixtures.InvestigationContextTests.bind
    session = fixtures.InvestigationContextTests.session
    save_note = fixtures.InvestigationContextTests.save_note

    def followup(self):
        previous = goal_identity(self.goal)
        previous_state = deepcopy(self.goal)
        previous_state.status, previous_state.paused_reason = 'paused', RUNTIME_HANDOFF_REASON
        ids = sorted(q['id'] for q in review._question_records(self.resolved()))
        self.case.record('report_narrative', {'schema': 'forsic-report-narrative-2',
            'goal_id': previous, 'question_ids': ids, 'draft_hash': 'prior-synthetic-report',
            'revision': 1, 'draft_revision': 0, 'basis_id': 'synthetic-basis', 'fields': {},
            'sections': {'synthesis': {'text': 'The acquired records leave the shared identity unresolved.'}}}, self.sid)
        self.goal = GoalState(goal='Rewrite the existing report only', created_at=2.0,
            runtime_followup_of=previous, runtime_followup_user_row_id=7)
        self.session(self.sid, goal=self.goal)
        receipt = {'previous_goal_id': previous, 'goal_id': goal_identity(self.goal),
            'accepted_user_row_id': 7, 'previous_status': 'paused',
            'previous_paused_reason': RUNTIME_HANDOFF_REASON,
            'previous_goal_state': json.loads(previous_state.to_json())}
        with sqlite3.connect(self.native) as db:
            db.execute('CREATE TABLE messages(id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, active INTEGER, compacted INTEGER, display_kind TEXT, display_metadata TEXT, content TEXT)')
            db.execute('INSERT INTO messages VALUES (7,?,?,1,0,NULL,?,?)',
                (self.sid, 'user', json.dumps({'runtime_handoff_followup': receipt}), self.goal.goal))
        return previous

    def resolved(self):
        return resolve_context(self.sid, intake_root=self.intake, native_db=self.native)

    def binding(self):
        return native_goal_binding(self.sid, intake_root=self.intake, native_db=self.native)

    def call(self, **args):
        return json.loads(self.case.invoke('forsic_reporting', args, self.sid,
                                          request_context=self.binding()))

    def test_native_followup_compose_render_and_reference_leave_question_unchanged(self):
        previous=self.followup(); before=host.current(self.case)['questions']; ids=[q['id'] for q in before]
        packet=self.call(action='compose',question_ids=ids)
        self.assertNotIn('error',packet,packet)
        self.assertEqual(packet['question_ids'],ids)
        rendered=self.call(action='render',question_ids=ids,allow_partial=True)
        self.assertNotIn('error',rendered,rendered)
        binding = self.binding()
        scope = scope_from_args({'_session_id': binding['conversation_id'], '_goal_id': binding['goal_id'],
            '_report_source_goal_id': binding['report_source_goal_id'],
            '_report_source_question_ids': binding['report_source_question_ids']})
        self.assertEqual(scope['source_goal_id'], previous)
        self.assertEqual(scope['source_question_ids'], sorted(ids))
        manifest,_,_=resolve_report(self.case,host.current(self.case),rendered['bundle_id'],report_scope=scope)
        self.assertEqual(manifest['report_scope'],scope)
        self.assertEqual(host.current(self.case)['questions'],before)
        with self.assertRaises(ReportReferenceError):
            resolve_report(self.case,host.current(self.case),rendered['bundle_id'],report_scope={**scope,'goal_id':'unrelated'})

    def test_default_selection_does_not_adopt_prior_questions(self):
        self.followup()
        result=self.call(action='compose')
        self.assertNotIn('error', result, result)
        self.assertEqual(result['question_ids'], ['Q-'+self.note['note_id']])
        resolved=resolve_context(self.sid,intake_root=self.intake,native_db=self.native)
        self.assertEqual(review._question_records(resolved),[])
        scoped=review.report_review_scope(resolved,['Q-'+self.note['note_id']])
        self.assertEqual([q['id'] for q in review._review_questions(scoped)],['Q-'+self.note['note_id']])
        self.assertEqual(scoped['goal_id'],goal_identity(self.goal))

    def test_legacy_receipt_without_uid_requires_original_visible_row(self):
        self.followup()
        self.assertIsNotNone(report_followup_selection(self.resolved()))
        with sqlite3.connect(self.native) as db:
            db.execute('UPDATE messages SET active=0,compacted=1 WHERE id=7')
        self.assertIsNotNone(report_followup_selection(self.resolved()))
        with sqlite3.connect(self.native) as db:
            db.execute('UPDATE messages SET compacted=0 WHERE id=7')
            db.execute('INSERT INTO messages SELECT 8,session_id,role,1,0,display_kind,display_metadata,content FROM messages WHERE id=7')
        # A visible row with identical content and receipt cannot invent the
        # missing durable occurrence identity after the original was rewound.
        self.assertIsNone(report_followup_selection(self.resolved()))

    def test_other_goal_conversation_and_forged_argument_remain_rejected(self):
        old=goal_identity(self.goal)
        self.goal=GoalState(goal='Unrelated request',created_at=3.0);self.session(self.sid,goal=self.goal)
        denied=self.call(action='compose',question_ids=['Q-'+self.note['note_id']],_report_source_goal_id=old)
        self.assertIn('Compose only',denied['error'])
        state=host.current(self.case); q=state['questions'][0]
        args={'_goal_id':'current','_session_id':self.sid,'_report_source_goal_id':old,'question_ids':[q['id']]}
        self.assertEqual(selection(state,args),[q['id']])
        for change in ({'goal_id':'unrelated'},{'session_id':'foreign-conversation'}):
            other=deepcopy(state);other['questions'][0].update(change)
            with self.assertRaises(ValueError):selection(other,args)
        self.assertEqual(report_source_goal_id({'goal':self.goal}),'')

    def test_branch_does_not_borrow_followup_provenance(self):
        self.followup(); self.session(self.sid,goal=self.goal,end='compression')
        self.session('branch',parent=self.sid,config={'_branched_from':self.sid})
        binding=native_goal_binding('branch',intake_root=self.intake,native_db=self.native)
        self.assertEqual(binding['report_source_goal_id'],'')

    def test_report_selection_does_not_grant_question_write_authority(self):
        self.followup(); before=host.current(self.case)['questions'];q=before[0]
        result=self.call(action='question',payload={'operation_id':'deny-edit','question_id':q['id'],
            'expected_revision':q['revision'],'answer':'Unauthorized replacement','reason':'editorial'})
        self.assertIn('error',result)
        self.assertEqual(host.current(self.case)['questions'],before)

if __name__=='__main__':unittest.main()
