"""Synthetic native compaction, retained report dialogue and owned response writes."""
from copy import deepcopy
import json
import sqlite3
import unittest
from unittest.mock import patch

from forsic_plugin import independent_review as review
from forsic_plugin.investigation_context import report_followup_selection
from forsic_plugin.report_driven import host
from hermes_state import SessionDB
import test_report_followup_review as fixtures
import test_independent_review as advice


class ReportContinuationContractTests(unittest.TestCase):
    setUp = fixtures.ReportFollowupReviewTests.setUp
    bind = fixtures.ReportFollowupReviewTests.bind
    session = fixtures.ReportFollowupReviewTests.session
    save_note = fixtures.ReportFollowupReviewTests.save_note
    setup_review = fixtures.ReportFollowupReviewTests.setup_review
    add_sources = fixtures.ReportFollowupReviewTests.add_sources
    kwargs = fixtures.ReportFollowupReviewTests.kwargs
    request_paths = fixtures.ReportFollowupReviewTests.request_paths
    resolved = fixtures.ReportFollowupReviewTests.resolved
    prepare = fixtures.ReportFollowupReviewTests.prepare
    response = fixtures.ReportFollowupReviewTests.response
    call = fixtures.ReportFollowupReviewTests.call
    answer = fixtures.ReportFollowupReviewTests.answer

    def native_handoff(self, *, durable_receipt=False):
        fixtures.ReportFollowupReviewTests.handoff(self, 3)
        # Promote the small legacy fixture into the real native schema/writer.
        # Receipts intentionally predate the durable UID field in the recovery test.
        with sqlite3.connect(self.native) as conn:
            sessions = conn.execute('SELECT * FROM sessions').fetchall()
            metas = conn.execute('SELECT key,value FROM state_meta').fetchall()
        self.native = self.home / 'actual-native.db'
        self.db = SessionDB(self.native)
        self.addCleanup(self.db.close)
        for sid, parent, end, source, config in sessions:
            self.db.create_session(sid, source=source, parent_session_id=parent,
                                   model_config=json.loads(config))
            if end:
                self.db._execute_write(lambda conn: conn.execute(
                    'UPDATE sessions SET end_reason=? WHERE id=?', (end, sid)))
        for key, value in metas:
            self.db.set_meta(key, value)
        row_id = self.db.append_message(self.sid, 'user', content=self.goal.goal,
            display_metadata={'runtime_handoff_followup': self.receipt})
        self.assertEqual(row_id, self.goal.runtime_followup_user_row_id)
        self.uid = self.db.get_messages(self.sid)[0]['message_uid']
        if durable_receipt:
            self.receipt['accepted_user_message_uid'] = self.uid
            self.db._execute_write(lambda conn: conn.execute(
                'UPDATE messages SET display_metadata=? WHERE id=?',
                (json.dumps({'runtime_handoff_followup': self.receipt}), row_id)))
        return self.resolved()

    def compact(self):
        messages = self.db.get_messages_as_conversation(self.sid, include_row_ids=True)
        self.db.archive_and_compact(self.sid, deepcopy(messages),
            covered_ids=[m['_row_id'] for m in messages], carried_messages=messages)

    def args(self):
        resolved = self.resolved()
        return {'_goal_id': resolved['goal_id'], '_session_id': resolved['conversation_id'],
            '_session_lineage': resolved['session_lineage'], '_question_contract': 'v2',
            '_independent_review_options': {'session_id': self.sid,
                'intake_root': str(self.intake), 'native_db': str(self.native)}}

    def seed_handoff_restart(self):
        """The old defect lost inherited receipts before persisting a new round1."""
        receipts = review._receipts
        with patch.object(review, '_receipts', side_effect=lambda resolved: [
                (path, request) for path, request in receipts(resolved)
                if request['identity']['goal_id'] == resolved['goal_id']]):
            self.call(report_question_ids=self.selected)

    def test_compacted_legacy_receipt_retains_rounds_and_owned_response_finalizes_once(self):
        self.native_handoff()
        original_questions = deepcopy(host.current(self.case)['questions'])
        original_receipts = {p: p.read_bytes() for p in self.request_paths()}
        self.compact(); self.compact()
        with sqlite3.connect(self.native) as conn:
            origin = conn.execute('SELECT active,compacted,message_uid FROM messages WHERE id=1').fetchone()
        self.assertEqual(origin, (0, 0, self.uid))
        self.assertEqual(report_followup_selection(self.resolved())['question_ids'], self.selected)
        inherited = review.review_state(self.resolved())
        self.assertEqual(len(inherited['executive_followup']['rounds']), 3)
        self.assertEqual(len(inherited['dispositions']), 15)
        result = self.call(report_question_ids=self.selected)
        self.assertEqual(len(self.calls), 4)
        self.assertEqual(result['followup_action'], 'create_continuation_question')
        from hermes_cli.goals_advisory import advisory_followup_prompt
        prompt = advisory_followup_prompt(result['context'], goal_text=self.goal.goal,
            followup_actions=[result['followup_action']])
        self.assertIn(result['recovery']['arguments']['operation_id'], prompt)
        self.assertLessEqual(len(result['context']), 3500)
        for _ in range(2):
            self.call(report_question_ids=self.selected)
        self.assertEqual(len(self.calls), 4)
        before = self.case.db.read_bytes()
        inbox = host.invoke(self.case, {'action': 'review_inbox', **self.args()})
        self.assertEqual(self.case.db.read_bytes(), before)
        self.assertEqual(inbox['followup_action'], 'create_continuation_question')
        self.assertEqual(inbox['questions'], [])
        save = inbox['recovery']['save_call']
        request = {'action': 'question', 'payload': save['arguments'], **self.args()}
        owner = host.invoke(self.case, request)
        self.assertEqual(host.invoke(self.case, request)['question'], owner['question'])
        question_id = owner['question']['question_id']
        finding = inbox['findings'][0]
        old = original_questions[0]
        payload = {'operation_id': 'explicit-continuation-response',
            'question_id': old['id'], 'expected_revision': old['revision'],
            'review_id': finding['review_id'], 'finding_id': finding['finding_id'],
            'disposition': 'scope_limited', 'reason': 'The acquired scope cannot establish execution.',
            'limitation': 'No execution trace was acquired.', 'citation_refs': []}
        with self.assertRaises(ValueError):
            host.invoke(self.case, {'action': 'review_response', 'payload': payload, **self.args()})
        refreshed = host.invoke(self.case, {'action': 'review_inbox', **self.args()})
        self.assertEqual(refreshed['questions'][0]['id'], question_id)
        payload.update(question_id=question_id, expected_revision=refreshed['questions'][0]['revision'])
        response = host.invoke(self.case, {'action': 'review_response', 'payload': payload, **self.args()})
        self.assertEqual(host.invoke(self.case, {'action': 'review_response', 'payload': payload,
            **self.args()})['question'], response['question'])
        current = review.review_state(self.resolved())
        self.assertEqual(current['pending_findings'], [])
        self.assertEqual(len(current['dispositions']), 16)
        self.assertTrue(current['executive_followup']['complete'])
        # A response alone does not create another report body or another round.
        self.fake.side_effect = lambda **kw: advice.IndependentReviewTests.empty_response(self, **kw)
        self.call(report_question_ids=self.selected)
        calls = len(self.calls)
        for _ in range(2):
            self.call(report_question_ids=self.selected)
        self.assertEqual(len(self.calls), calls)
        self.assertTrue(review.review_state(self.resolved())['final_review_current'])
        from forsic_plugin.report_driven.finalization import _review_gate, independent_review_projection
        state = host.current(self.case)
        context = {'enabled': True, 'resolved': self.resolved()}
        manifest = {'bundle_id': 'synthetic', 'independent_review': independent_review_projection(context, state)}
        _, blocked = _review_gate(self.case, state, manifest, context)
        self.assertIsNone(blocked)
        saved = {q['id']: q for q in host.current(self.case)['questions']}
        self.assertEqual([saved[q['id']] for q in original_questions], original_questions)
        self.assertEqual({p: p.read_bytes() for p in original_receipts}, original_receipts)

    def test_generation_recovery_rejects_rewind_foreign_branch_reset_and_forged_receipts(self):
        resolved = self.native_handoff(durable_receipt=True)
        self.compact()
        self.assertIsNotNone(report_followup_selection(resolved))
        live = self.db.get_messages_as_conversation(self.sid, include_row_ids=True)[0]
        self.db._execute_write(lambda conn: conn.execute(
            'UPDATE messages SET message_uid=? WHERE id=?', ('different-occurrence', live['_row_id'])))
        self.assertIsNone(report_followup_selection(resolved))
        duplicate = {'executive_round': 1, 'identity': {'goal_id': resolved['goal_id']}}
        with self.assertRaisesRegex(ValueError, 'Invalid executive round sequence'):
            review.executive_receipt_sequence({'goal_id': resolved['goal_id']},
                [(None, duplicate), (None, deepcopy(duplicate))])
        self.db._execute_write(lambda conn: conn.execute(
            'UPDATE messages SET message_uid=? WHERE id=?', (self.uid, live['_row_id'])))
        # UID equality alone cannot excuse an altered accepted receipt/content.
        for receipt in ({**self.receipt, 'accepted_user_message_uid': 'forged'},
                        {**self.receipt, 'goal_id': 'f' * 64},
                        {**self.receipt, 'runtime_budget_seconds': 999}):
            self.db._execute_write(lambda conn: conn.execute(
                'UPDATE messages SET display_metadata=? WHERE id=?',
                (json.dumps({'runtime_handoff_followup': receipt}), live['_row_id'])))
            self.assertIsNone(report_followup_selection(resolved))
        self.db._execute_write(lambda conn: conn.execute(
            'UPDATE messages SET display_metadata=? WHERE id=?',
            (json.dumps({'runtime_handoff_followup': self.receipt}), live['_row_id'])))
        self.db._execute_write(lambda conn: conn.execute(
            'UPDATE sessions SET end_reason=? WHERE id=?', ('compression', self.sid)))
        for marker in ('_branched_from', '_reset_from', '_delegate_from'):
            sid = 'foreign-' + marker
            self.db.create_session(sid, source='tui', parent_session_id=self.sid,
                                   model_config={marker: self.sid})
            self.db.set_meta('goal:' + sid, self.goal.to_json())
            self.bind(sid, self.case)
            from forsic_plugin.investigation_context import resolve_context
            foreign = resolve_context(sid, intake_root=self.intake, native_db=self.native)
            self.assertIsNone(report_followup_selection(foreign))
        self.db._execute_write(lambda conn: conn.execute(
            'UPDATE messages SET active=0,compacted=0 WHERE id=?', (live['_row_id'],)))
        self.assertIsNone(report_followup_selection(resolved))
        # A foreign visible clone cannot restore a rewound occurrence's authority.
        self.db.create_session('foreign-conversation', source='tui')
        self.db.append_message('foreign-conversation', 'user', content=self.goal.goal,
            message_uid=self.uid, display_metadata={'runtime_handoff_followup': self.receipt})
        self.assertIsNone(report_followup_selection(resolved))

    def test_current_owned_corrected_claim_and_report_retain_dialogue_require_fresh_review(self):
        from forsic_plugin.report_driven.grounded_judgment import read_refs
        from forsic_plugin.report_driven.narrative import basis, material
        from forsic_plugin.report_driven.report_reference import resolve_report, scope_from_args
        from test_report_narrative import ReportNarrativeTests
        self.native_handoff()
        self.compact()
        old_questions = deepcopy(host.current(self.case)['questions'])
        self.seed_handoff_restart()
        old_receipts = {p: p.read_bytes() for p in self.request_paths()}
        self.assertEqual(len(old_receipts), 4)
        restarted = review.review_state(self.resolved())
        self.assertEqual(len(restarted['executive_followup']['rounds']), 3)
        self.assertEqual(len(restarted['historical_executive_requests']), 1)
        self.assertEqual(restarted['historical_executive_requests'][0]['round'], 1)
        self.assertFalse(restarted['historical_executive_requests'][0]['policy_round_counted'])
        self.assertEqual(len(restarted['dispositions']), 15)
        self.assertEqual(len(restarted['pending_findings']), 5)
        self.assertFalse(restarted['final_review_current'])
        start = self.case.record('tool_start', {'tool': 'forsic_read',
            'arguments': {'path': 'synthetic-correction.log'}}, self.sid)
        data = {'tool': 'forsic_read', 'started_id': start, 'path': 'synthetic-correction.log',
            'lines': [{'line': 1, 'text': 'configuration_present=true execution_observed=false'}]}
        source = self.case.record('tool_result', data, self.sid)
        citation = read_refs(self.case, {**data, 'evidence_id': source})['presented_spans'][0]['citation_ref']
        corrected = '설정은 확인되지만 선택한 기록에서 실행은 관측되지 않았습니다. 전체 시스템의 실행 부재로 일반화하지 않습니다.'
        created = host.invoke(self.case, {'action': 'question', **self.args(), 'payload': {
            'operation_id': 'current-owned-atomic-correction', 'question': '설정 관측이 실제 실행을 입증하는가?',
            'scope': '선택한 합성 설정·실행 관측 기록', 'answer': corrected,
            'reason': '설정의 존재와 실제 실행을 구별하고 관측 범위를 명시합니다.',
            'priority': 'contextual', 'gaps': ['선택 범위 밖 실행은 미확인'],
            'judgment': {'citation_refs': [citation], 'assertion_kind': 'interpretation',
                'reasoning_summary': '보존 원문은 설정 존재와 실행 미관측을 따로 기록합니다.'}}})
        qid = created['question']['question_id']
        selected = [qid]
        scoped = review.report_review_scope(self.resolved(), selected)
        before = review.review_state(scoped)
        self.assertEqual(len(before['executive_followup']['rounds']), 3)
        self.assertEqual(len(before['dispositions']), 15)
        self.assertFalse(before['final_review_current'])
        self.assertEqual(len(self.calls), 4)
        packet = host.invoke(self.case, {'action': 'compose', 'section': 'major_findings',
            'question_ids': selected, **self.args()})
        author = ReportNarrativeTests('test_native_render_requests_authored_prose_not_blank_template')
        author.case = self.case
        _, state = basis(host.current(self.case), selected)
        keys = [row['key'] for row in material(state) if row['kind'] in ('question', 'claim', 'gap')]
        draft = author.draft('major_findings', packet, keys=keys)
        draft['entries'][0]['cells'][0] = corrected
        saved = host.invoke(self.case, {'action': 'compose', 'section': 'major_findings',
            'operation_id': 'compose-corrected-owned-report', 'question_ids': selected,
            'edit_ref': packet['save_contract']['edit_ref'], 'draft': draft, **self.args()})
        self.assertTrue(saved['saved'])
        before = review.review_state(self.resolved())
        self.assertEqual(len(before['executive_followup']['rounds']), 3)
        self.assertFalse(before['final_review_current'])
        self.assertEqual(len(before['pending_findings']), 5)
        self.call(report_question_ids=selected)
        self.assertEqual(len(self.calls), 4)
        # Stale restarted advice is neither lost nor automatically accepted.
        # Each finding is explicitly answered on the current-owned correction.
        for index, finding in enumerate(before['pending_findings']):
            question = next(q for q in host.current(self.case)['questions'] if q['id'] == qid)
            host.invoke(self.case, {'action': 'review_response', **self.args(), 'payload': {
                'operation_id': f'stale-restarted-finding-{index}', 'question_id': qid,
                'expected_revision': question['revision'], 'review_id': finding['review_id'],
                'finding_id': finding['finding_id'], 'disposition': 'scope_limited',
                'reason': '현재 정정의 근거와 기존 제안을 대조하고 미수행 범위를 명시합니다.',
                'limitation': '획득 범위 밖 실행 여부는 새 원문 확보 때 재검토합니다.', 'citation_refs': []}})
        after_answers = review.review_state(self.resolved())
        self.assertEqual(len(after_answers['dispositions']), 20)
        self.assertEqual(after_answers['pending_findings'], [])
        self.assertFalse(after_answers['final_review_current'])
        self.call(report_question_ids=selected)
        self.assertEqual(len(self.calls), 5)
        pending = review.review_state(self.resolved())
        self.assertEqual(len(pending['pending_findings']), 1)
        self.assertTrue(pending['requires_followup'])
        from forsic_plugin.report_driven.finalization import _review_gate
        _, blocked = _review_gate(self.case, host.current(self.case), {'bundle_id': 'synthetic'},
            {'enabled': True, 'resolved': scoped})
        self.assertEqual(blocked['delivery_status'], 'independent_review_pending')
        finding = pending['pending_findings'][0]
        question = next(q for q in host.current(self.case)['questions'] if q['id'] == qid)
        host.invoke(self.case, {'action': 'review_response', **self.args(), 'payload': {
            'operation_id': 'corrected-report-review-response', 'question_id': qid,
            'expected_revision': question['revision'], 'review_id': finding['review_id'],
            'finding_id': finding['finding_id'], 'disposition': 'scope_limited',
            'reason': '획득 범위 밖 실행 기록은 조사하지 못했습니다.',
            'limitation': '선택 기록 밖 실행 여부는 새 원문 확보 때 재검토합니다.', 'citation_refs': []}})
        current = review.review_state(self.resolved())
        self.assertEqual(current['pending_findings'], [])
        self.assertEqual(len(current['dispositions']), 21)
        self.assertEqual(len(current['executive_followup']['rounds']), 3)
        self.assertTrue(current['final_review_current'])
        args = {**self.args(), '_independent_review_options': {
            **self.args()['_independent_review_options'], 'enabled': True, 'executive_followup': True}}
        rendered = host.invoke(self.case, {'action': 'render', 'question_ids': selected,
            'allow_partial': True, **args})
        self.assertIn('bundle_id', rendered)
        manifest, _, snapshot = resolve_report(self.case, host.current(self.case),
            rendered['bundle_id'], report_scope=scope_from_args(args))
        self.assertEqual(manifest['question_ids'], selected)
        from forsic_plugin.report_driven.views import selected_report_state
        rendered_state = selected_report_state(snapshot, selected)
        self.assertEqual([q['id'] for q in rendered_state['questions']], selected)
        self.assertEqual(rendered_state['questions'][0]['answer'], corrected)
        self.assertTrue(rendered_state['questions'][0]['claim_refs'])
        self.call(report_question_ids=selected)
        self.assertEqual(len(self.calls), 5)
        packet = host.invoke(self.case, {'action': 'compose', 'section': 'major_findings',
            'question_ids': selected, **self.args()})
        draft['entries'][0]['cells'][0] += ' 새 원문 확보 전에는 선택 범위 밖 상태를 판단하지 않습니다.'
        host.invoke(self.case, {'action': 'compose', 'section': 'major_findings',
            'operation_id': 'material-current-report-revision', 'question_ids': selected,
            'edit_ref': packet['save_contract']['edit_ref'], 'draft': draft, **self.args()})
        self.assertFalse(review.review_state(self.resolved())['final_review_current'])
        self.fake.side_effect = lambda **kw: advice.IndependentReviewTests.empty_response(self, **kw)
        self.call(report_question_ids=selected)
        self.assertEqual(len(self.calls), 6)
        current = review.review_state(self.resolved())
        self.assertEqual(len(current['executive_followup']['rounds']), 3)
        self.assertEqual(len(current['dispositions']), 21)
        self.assertTrue(current['final_review_current'])
        questions = {q['id']: q for q in host.current(self.case)['questions']}
        self.assertEqual([questions[q['id']] for q in old_questions], old_questions)
        self.assertEqual({p: p.read_bytes() for p in old_receipts}, old_receipts)


if __name__ == '__main__':
    unittest.main()
