"""Actual native question/CAS writes and shared read-only reader projections."""
from copy import deepcopy
import json
from unittest.mock import patch
import unittest

import test_grounded_judgment as helpers
from forsic_plugin.report_driven.host import current, find
from forsic_plugin.report_driven.reader_explanation import project
from forsic_plugin.investigation_view import project_view


class ReaderExplanationTests(unittest.TestCase):
    setUp = helpers.GroundedJudgmentTests.setUp
    call = helpers.GroundedJudgmentTests.call
    read = helpers.GroundedJudgmentTests.read
    payload = helpers.GroundedJudgmentTests.payload
    ref = helpers.GroundedJudgmentTests.ref

    def saved_note(self, qid):
        with self.case.connect() as db:
            return json.loads(db.execute("SELECT data FROM events WHERE kind='note' AND json_extract(data,'$.note_id')=? ORDER BY rowid DESC LIMIT 1", (qid.removeprefix('Q-'),)).fetchone()[0])

    def explanation(self, indexed=True):
        return {'title': '요청과 실행을 구별한 이유', 'paragraphs': [
            {'text': '요청 문자열은 확인했지만 완료된 실행을 입증하지는 않습니다.', **({'claim_indices': [0]} if indexed else {})}]}

    def create(self, **changes):
        read = self.read()
        payload = self.payload([self.ref(read)])
        payload.update(reader_explanation=self.explanation(), **changes)
        result = self.call('forsic_question', payload)
        self.assertNotIn('error', result, result)
        return result['question']['question_id'], payload

    def test_save_projection_answer_change_stale_replacement_and_sidebar_parity(self):
        qid, payload = self.create()
        state = current(self.case)
        note = self.saved_note(qid)
        explanation = project(note, state['claims'])
        self.assertEqual(explanation['status'], 'current')
        self.assertEqual(explanation['canonical_answer'], payload['answer'])
        self.assertIn(payload['alternatives'][0], explanation['counterarguments'])
        self.assertIn(payload['gaps'][0], explanation['limitations'])
        self.assertIn(payload['judgment']['limitations'][0], explanation['limitations'])
        claim = find(state, 'claims', note['investigation']['claim_refs'][0]['id'])
        paragraph = explanation['paragraphs'][0]
        self.assertEqual(paragraph['source_refs'], claim['source_refs'])
        self.assertEqual(paragraph['counterevidence_refs'], claim['counterevidence_refs'])
        before = self.case.db.read_bytes()
        resolved = dict(case=self.case, case_id=self.case.config['case_id'], requested_session_id='synthetic-session', session_lineage=['synthetic-session'], goal_id='synthetic-goal')
        sidebar = project_view(resolved, profile='default')
        question = next(q for q in sidebar['questions'] if q['id'] == qid)
        self.assertEqual(question['reader_explanation'], explanation)
        self.assertEqual(self.case.db.read_bytes(), before)
        # The report appendix receives precisely this same host projection.
        self.assertEqual(find(state, 'questions', qid)['reader_explanation'], explanation)
        from forsic_plugin.report_driven.views import compile_views
        from forsic_plugin.report_driven.render_views import render_html
        appendix = render_html(compile_views(state)['integrated'])
        self.assertEqual(appendix.count(explanation['paragraphs'][0]['text']), 1)
        revision = find(state, 'questions', qid)['revision']
        change = self.call('forsic_question', dict(operation_id='changed-answer', question_id=qid,
            expected_revision=revision, answer='판단을 정정하여 실행 여부를 다시 검토합니다.', reason='New bounded interpretation'))
        self.assertNotIn('error', change, change)
        state = current(self.case)
        stale = project(self.saved_note(qid), state['claims'])
        self.assertEqual(stale['status'], 'stale')
        self.assertFalse(stale['paragraphs'])
        self.assertNotIn('title', stale)
        self.assertEqual(stale['canonical_answer'], '판단을 정정하여 실행 여부를 다시 검토합니다.')
        self.assertNotIn(explanation['paragraphs'][0]['text'], render_html(compile_views(state)['integrated']))
        revision = find(state, 'questions', qid)['revision']
        replacement = self.explanation(False)
        replacement['paragraphs'][0]['text'] = '새 답의 근거 연결을 다시 확인하는 중입니다.'
        result = self.call('forsic_question', dict(operation_id='new-explanation', question_id=qid,
            expected_revision=revision, reader_explanation=replacement))
        self.assertNotIn('error', result, result)
        latest = project(self.saved_note(qid), current(self.case)['claims'])
        self.assertEqual(latest['status'], 'provisional')
        self.assertEqual(latest['paragraphs'][0]['text'], replacement['paragraphs'][0]['text'])

    def test_claim_or_context_changes_stale_without_semantic_reapproval(self):
        qid, _ = self.create()
        note = self.saved_note(qid); state = current(self.case)
        for key in ('statement', 'limitations', 'counterevidence_refs', 'status'):
            changed = deepcopy(state['claims'])
            c = next(c for c in changed if c['id'] == note['investigation']['claim_refs'][0]['id'])
            c[key] = 'superseded' if key == 'status' else ['Changed'] if key != 'statement' else 'Changed claim'
            self.assertEqual(project(note, changed)['status'], 'stale', key)
        changed = deepcopy(note); changed['alternatives'] = ['Important new alternative']
        self.assertEqual(project(changed, state['claims'])['status'], 'stale')
        changed = deepcopy(note); changed['scope'] = 'Changed scope'
        self.assertEqual(project(changed, state['claims'])['status'], 'stale')
        self.assertFalse(project(note, state['claims'])['semantic_approval'])

    def test_provisional_without_claims_optional_and_no_per_tool_obligation(self):
        result = self.call('forsic_question', dict(operation_id='provisional', question='무엇을 확인해야 하나?',
            scope='Synthetic acquired records', answer='현재는 판별할 수 없습니다.',
            alternatives=['수집 범위 밖의 기록이 있을 수 있습니다.'],gaps=['실행 결과 미확인'],
            reader_explanation=self.explanation(False)))
        self.assertNotIn('error', result, result)
        qid = result['question']['question_id']
        projected = project(self.saved_note(qid), [])
        self.assertEqual(projected['status'], 'provisional')
        self.assertFalse(projected['paragraphs'][0]['claim_refs'])
        no = self.call('forsic_question', dict(operation_id='optional', question='별도 질문',scope='Synthetic',answer='보류'))
        self.assertNotIn('error', no, no)
        self.assertEqual(project(self.saved_note(no['question']['question_id']), [])['status'], 'missing')

    def test_bad_indices_author_supplied_refs_and_bounds_roll_back_atomic_write(self):
        read = self.read(); base = self.payload([self.ref(read)])
        before = current(self.case)['meta']['content_revision']
        invalid = [dict(paragraphs=[dict(text='Unknown',claim_indices=[9])]),
            dict(paragraphs=[dict(text='Injected',source_refs=[dict(kind='source',id='made-up',version='1')])]),
            dict(paragraphs=[dict(text='x'*1601)]), dict(paragraphs=[]),
            dict(paragraphs=[dict(text='Valid',claim_indices=[True])])]
        for index, explanation in enumerate(invalid):
            result=self.call('forsic_question', dict(base,operation_id='invalid-'+str(index),reader_explanation=explanation))
            self.assertIn('error', result, result)
            self.assertEqual(current(self.case)['meta']['content_revision'], before)
        self.assertNotIn('reader_explanation', base)

    def test_retry_revision_and_ownership_fences_still_apply(self):
        qid, payload = self.create()
        before = current(self.case)['meta']['content_revision']
        self.assertNotIn('error', self.call('forsic_question', payload))
        self.assertEqual(current(self.case)['meta']['content_revision'], before)
        result = self.call('forsic_question',dict(operation_id='stale-explanation',question_id=qid,
            expected_revision=999,reader_explanation=self.explanation()))
        self.assertIn('error', result)
        self.assertEqual(current(self.case)['meta']['content_revision'], before)
