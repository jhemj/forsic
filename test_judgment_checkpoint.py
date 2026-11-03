"""Actual read/save/UI/compaction boundaries; synthetic case, no inference."""
from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import patch
import unittest

import test_grounded_judgment as helpers
from forsic_plugin.judgment_checkpoint import checkpoint, model_checkpoint
from forsic_plugin.question_bootstrap import PROVISIONAL_ANSWER
from forsic_plugin.report_driven.host import current, find
from forsic_plugin.report_driven.current_judgment import current_judgment
from forsic_plugin.investigation_view import project_view


class JudgmentCheckpointTests(unittest.TestCase):
    setUp = helpers.GroundedJudgmentTests.setUp
    call = helpers.GroundedJudgmentTests.call
    read = helpers.GroundedJudgmentTests.read
    payload = helpers.GroundedJudgmentTests.payload
    ref = helpers.GroundedJudgmentTests.ref

    def resolved(self, **changes):
        return dict(case=self.case, case_id=self.case.config['case_id'],
            goal_id='synthetic-goal', goal=SimpleNamespace(created_at=0),
            requested_session_id='synthetic-session', session_lineage=['synthetic-session'], **changes)

    def briefing(self, request='public-progress', session='synthetic-session', *, chars=64, tool=True):
        self.case.record('post_api_request', dict(api_request_id=request,
            assistant_content_chars=chars, assistant_tool_call_count=1,
            raw_text='NEVER_COPY_PUBLIC_TEXT_AS_EVIDENCE'), session)
        if tool:
            self.case.record('tool_start', dict(tool='forsic_read',
                native_call={'api_request_id': request}), session)

    def test_native_evidence_return_points_to_existing_question_without_saving_it(self):
        initial = self.call('forsic_question', dict(operation_id='initial-checkpoint',
            question='Did this synthetic request execute?', scope='Synthetic acquired records',
            answer=PROVISIONAL_ANSWER))
        self.assertNotIn('error', initial, initial)
        self.briefing()
        state = current(self.case)
        with patch('forsic_plugin.investigation_context.resolve_context', return_value=self.resolved()):
            returned = self.read()
        notice = returned['judgment_checkpoint']
        self.assertEqual(notice['status'], 'pending')
        self.assertEqual(notice['pending_briefings'], 1)
        self.assertEqual(notice['questions'][0]['question_id'], initial['question']['question_id'])
        self.assertEqual(current(self.case)['questions'], state['questions'])
        self.assertNotIn('NEVER_COPY', json.dumps(notice))
        self.assertTrue(returned['presented_spans'])
        self.assertEqual(self.case.event(returned['evidence_id'])['kind'], 'tool_result')

    def test_saved_interpretation_clears_pending_correction_and_ui_share_exact_revision(self):
        source = self.read()
        self.briefing()
        self.assertEqual(checkpoint(self.resolved())['status'], 'pending')
        payload = self.payload([self.ref(source)])
        saved = self.call('forsic_question', payload)
        self.assertNotIn('error', saved, saved)
        qid = saved['question']['question_id']
        receipt = saved['question']['saved_judgment']
        self.assertEqual(receipt['selected_claim_count'], 1)
        self.assertFalse(receipt['review_required'])
        self.assertEqual(receipt['selected_claim_ids'], [ref['id'] for ref in find(current(self.case), 'questions', qid)['claim_refs']])
        self.assertEqual(checkpoint(self.resolved())['status'], 'current')
        before = current(self.case)
        q = find(before, 'questions', qid)
        self.briefing('new-counterevidence')
        other = self.read('process.log')
        change = self.payload([self.ref(other)])
        change.update(operation_id='checkpoint-correction', question_id=qid, expected_revision=q['revision'],
            answer='Process creation is recorded; successful completion remains uncertain.',
            next_checks=['Read the completion receipt'], reason='Correct the request-only explanation')
        change['judgment']['counter_citation_refs'] = [self.ref(source)]
        changed = self.call('forsic_question', change)
        self.assertNotIn('error', changed, changed)
        state = current(self.case); q = find(state, 'questions', qid)
        body = self.case.db.read_bytes()
        view = project_view(self.resolved(), profile='default')
        visible = next(item for item in view['questions'] if item['id'] == qid)
        self.assertEqual(visible['answer'], current_judgment(state, q)['answer'])
        self.assertEqual(visible['version'], str(q['revision']))
        self.assertEqual(visible['next_checks'], change['next_checks'])
        self.assertEqual(visible['answer_status'], 'recorded')
        self.assertEqual(visible['correction_reason'], change['reason'])
        self.assertIsInstance(visible['updated_at'], (float, int))
        self.assertEqual(view['judgment_checkpoint']['status'], 'current')
        self.assertEqual(self.case.db.read_bytes(), body)
        with self.case.connect() as db:
            history = list(db.execute("SELECT data FROM events WHERE kind='note' AND json_extract(data,'$.note_id')=?", (qid[2:],)))
        self.assertEqual(len(history), 2)
        self.assertEqual(json.loads(history[0][0])['answer'], payload['answer'])
        self.assertEqual(find(state, 'claims', find(before, 'questions', qid)['claim_refs'][0]['id'])['status'], 'superseded')
        # Replay neither creates another revision nor starts another write loop.
        self.call('forsic_question', change)
        self.assertEqual(current(self.case), state)

    def test_workflow_save_does_not_hide_unsaved_interpretation_or_recollect_results(self):
        source = self.read()
        saved = self.call('forsic_question', self.payload([self.ref(source)]))
        qid = saved['question']['question_id']
        original = saved['question']['saved_judgment']
        self.briefing('interrupted-interpretation')
        recent = self.read('process.log')
        changed = self.call('forsic_question', dict(operation_id='workflow-only', question_id=qid,
            expected_revision=saved['question']['revision'], work_state='active',
            reason='Report generation resumed; no new incident interpretation'))
        self.assertNotIn('error', changed, changed)
        receipt = changed['question']['saved_judgment']
        for key in ('answer_sha256', 'updated_at', 'selected_claim_count', 'selected_claim_ids'):
            self.assertEqual(receipt[key], original[key], key)
        notice = model_checkpoint(self.resolved())
        self.assertEqual(notice['status'], 'pending')
        self.assertEqual(notice['retained_results'][0]['source_id'], recent['evidence_id'])
        before = self.case.db.read_bytes()
        from forsic_plugin.report_driven.host import invoke
        restored = invoke(self.case, notice['retained_results'][0]['read_call']['arguments'])
        self.assertEqual(restored['source_ref']['id'], recent['evidence_id'])
        self.assertEqual(self.case.db.read_bytes(), before)

    def test_saved_receipt_does_not_turn_retention_explanation_into_claim_selection(self):
        saved = self.call('forsic_question', dict(operation_id='unselected-claims',
            question='Synthetic undecided scope', scope='Synthetic', answer='A provisional interpretation',
            reason='Twelve claims retained'))
        self.assertNotIn('error', saved, saved)
        self.assertEqual(saved['question']['saved_judgment']['selected_claim_count'], 0)
        self.assertEqual(saved['question']['saved_judgment']['selected_claim_ids'], [])

    def test_pending_is_navigation_not_a_finding_and_excludes_unrelated_or_unpublished_calls(self):
        self.briefing('unpublished', tool=False)
        self.briefing('no-public-text', chars=0)
        self.briefing('different-session', session='unrelated')
        self.assertEqual(checkpoint(self.resolved())['pending_briefings'], 0)
        self.briefing('same-request'); self.briefing('same-request')
        value = model_checkpoint(self.resolved())
        self.assertEqual(value['pending_briefings'], 1)
        self.assertIn('not evidence', value['meaning'])
        self.assertNotIn('NEVER_COPY', json.dumps(value))
        before = current(self.case)
        model_checkpoint(self.resolved())
        self.assertEqual(current(self.case), before)
        wrong_goal = self.resolved(); wrong_goal['goal'] = SimpleNamespace(created_at=10**12)
        self.assertEqual(checkpoint(wrong_goal)['status'], 'current')

    def test_provisional_and_unavailable_are_not_reported_as_saved(self):
        saved = self.call('forsic_question', dict(operation_id='bootstrap-like',
            question='Synthetic pending question', scope='Synthetic', answer=PROVISIONAL_ANSWER))
        view = project_view(self.resolved())
        q = next(q for q in view['questions'] if q['id'] == saved['question']['question_id'])
        self.assertEqual(q['answer_status'], 'not_saved')
        self.briefing()
        self.assertIsNone(checkpoint(self.resolved())['latest_saved_at'])
        bad = deepcopy(self.resolved()); bad['case'] = SimpleNamespace(db=self.case.db.parent/'missing.sqlite3')
        self.assertEqual(checkpoint(bad)['status'], 'unavailable')
        self.assertFalse(bad['case'].db.exists())


if __name__ == '__main__':
    unittest.main()
