"""Synthetic current-answer retention under crowded independent review inputs."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from forsic_plugin import independent_review as review
import test_review_dispositions as fixtures


class ReviewPacketBudgetTests(unittest.TestCase):
    setUp=fixtures.ReviewDispositionTests.setUp
    bind=fixtures.ReviewDispositionTests.bind
    session=fixtures.ReviewDispositionTests.session
    save_note=fixtures.ReviewDispositionTests.save_note
    setup_review=fixtures.ReviewDispositionTests.setup_review
    add_sources=fixtures.ReviewDispositionTests.add_sources
    response=fixtures.ReviewDispositionTests.response
    resolved=fixtures.ReviewDispositionTests.resolved
    adopted_link=fixtures.ReviewDispositionTests.adopted_link

    def crowded_answer(self):
        self.setup_review()
        self.note['next_checks']=[{'target':'current artifact','reason':'DISCRIMINATING_CHECK '+('detail '*400)}]*20
        self.save_note(answer='CURRENT_BOUNDED_ANSWER '+('assessment '*200),
            assessment='inconclusive',work_state='active',limitations=['CURRENT_LIMIT '+('scope '*400)]*20,
            review_responses=[{'review_id':'review-old','finding_id':'finding-old',
                'disposition':'scope_limited','reason':'CURRENT_DISPOSITION '+('detail '*400),
                'citations':[{'literal':'nested '*400}]*2}]*6)

    def test_nested_history_cannot_remove_current_answer_limits_or_next_check(self):
        self.crowded_answer()
        for budget in (40000,24000,16000):
            with self.subTest(budget=budget),patch.object(review,'MAX_INPUT',budget):
                packet=review._collect(self.resolved(),'final','Current candidate '+('draft '*2000))
                body=json.loads(packet['prompt']);state=body['analyst_state']
                self.assertEqual(state['omitted_questions'],0)
                question=state['questions'][0]
                self.assertTrue(question['answer'].startswith('CURRENT_BOUNDED_ANSWER'))
                self.assertEqual(question['assessment'],'inconclusive')
                self.assertEqual(question['work_state'],'active')
                self.assertIn('CURRENT_LIMIT',json.dumps(question['limitations']))
                self.assertIn('DISCRIMINATING_CHECK',json.dumps(question['next_checks']))
                self.assertIn('CURRENT_DISPOSITION',json.dumps(question['review_responses']))
                self.assertTrue(question['presentation_omissions']['truncated_fields'])
                self.assertTrue(body['sources']);self.assertTrue(packet['review_refs'])
                self.assertLessEqual(len(packet['prompt'])+len(review.SYSTEM),budget)

    def test_two_large_question_owners_share_the_allowance(self):
        self.crowded_answer()
        other=deepcopy(self.note);other['note_id']='other-owned-question'
        other['investigation']['answer']='SECOND_CURRENT_ANSWER '+('detail '*400)
        self.case.record('note',other,self.sid)
        packet=review._collect(self.resolved(),'final')
        state=json.loads(packet['prompt'])['analyst_state']
        self.assertEqual(state['omitted_questions'],0)
        self.assertEqual(len(state['questions']),2)
        self.assertEqual({q['id'] for q in state['questions']},
                         {'Q-'+self.note['note_id'],'Q-other-owned-question'})
        self.assertTrue(any(q['answer'].startswith('SECOND_CURRENT_ANSWER') for q in state['questions']))
        self.assertLessEqual(len(packet['prompt'])+len(review.SYSTEM),review.MAX_INPUT)

    def test_old_selected_observation_keeps_exact_grounding_after_question_compaction(self):
        self.crowded_answer();old=self.sources[0]['evidence_id']
        literal=self.case.event(old)['data']['lines'][0]['text']
        self.adopted_link(old,'/lines/0/text',literal)
        self.add_sources(60)
        with patch.object(review,'MAX_INPUT',24000):
            packet=review._collect(self.resolved(),'final')
        body=json.loads(packet['prompt']);observations=body['analyst_state']['grounding']['observations']
        selected=next(o for o in observations if o.get('literal')==literal)
        self.assertFalse(selected['semantic_validation'])
        self.assertEqual(selected['source_ref']['id'],old)
        self.assertIn(literal,packet['refs'][old]['/lines/0/text'])
        for source in body['sources']:
            for field in source['fields']:
                self.assertEqual(field['review_ref'],review._field_handle(source,field))
        self.assertLessEqual(len(packet['prompt'])+len(review.SYSTEM),24000)

    def test_current_evidence_selection_retains_old_read_and_correct_omission_count(self):
        self.crowded_answer()
        old=self.case.record('tool_result',{'tool':'forsic_read','path':'current-selected.log',
            'complete_file':True,'lines':[{'line':1,'text':'CURRENT_SELECTED_OBSERVATION'}]},self.sid)
        foreign=self.case.record('tool_result',{'tool':'forsic_read','path':'FOREIGN_SELECTED_SOURCE',
            'lines':[{'line':1,'text':'FOREIGN_SELECTED_CONTENT'}]},'other-session')
        self.note['evidence_ids']=[old,foreign];self.save_note()
        self.add_sources(70)
        packet=review._collect(self.resolved(),'final')
        body=json.loads(packet['prompt'])
        self.assertIn(old,packet['refs'])
        self.assertNotIn(foreign,packet['refs'])
        self.assertNotIn('FOREIGN_SELECTED_CONTENT',packet['prompt'])
        self.assertEqual(packet['refs'][old]['/lines/0/text'],self.case.event(old)['data']['lines'][0]['text'])
        self.assertEqual(body['omitted']['sources'],packet['count']-len(body['sources']))
        blind=review._collect(self.resolved(),'exploration')
        self.assertNotIn('/lines/0/text',blind['refs'].get(old,{}))

    def test_crowded_packet_keeps_full_state_fingerprint_and_excludes_foreign_session(self):
        self.crowded_answer()
        foreign=deepcopy(self.note);foreign.update(note_id='foreign',session_id='other-session')
        foreign['investigation']['answer']='FOREIGN_ANSWER'
        self.case.record('note',foreign,'other-session')
        from test_report_driven import fixture
        other_case,_,other_note=fixture(self.root/'other-case')
        other_note.update(session_id=self.sid,goal_id=self.note['goal_id'])
        other_note.setdefault('investigation',{})['answer']='FOREIGN_CASE_ANSWER'
        other_case.record('note',other_note,self.sid)
        first=review._collect(self.resolved(),'final')
        self.assertNotIn('FOREIGN_CASE_ANSWER',first['prompt'])
        self.assertNotIn('FOREIGN_ANSWER',first['prompt'])
        self.assertEqual(len(json.loads(first['prompt'])['analyst_state']['questions']),1)
        # A review-response tail remains followthrough, not a new substantive answer.
        answers=deepcopy(self.note['investigation']['review_responses'])
        answers[-1]['reason']+=' changed hidden tail'
        self.save_note(review_responses=answers)
        # Followthrough alone does not change the substantive evaluation fingerprint.
        self.assertEqual(first['evaluation_fingerprint'],review._collect(self.resolved(),'final')['evaluation_fingerprint'])
        self.save_note(limitations=self.note['investigation']['limitations']+['NEW_HIDDEN_LIMIT'])
        self.assertNotEqual(first['evaluation_fingerprint'],review._collect(self.resolved(),'final')['evaluation_fingerprint'])


if __name__=='__main__':unittest.main()
