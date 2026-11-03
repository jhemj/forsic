"""Synthetic executive input composition; no case data or model calls."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from forsic_plugin import independent_review as review


class ExecutivePacketCompositionTests(unittest.TestCase):
    def packet(self):
        sources=[]
        for i in range(3):
            source={'source_id':f'source-{i}','source_version':f'event-sha256:{i}',
                    'tool':'forsic_read','fields':[],'omitted_fields':0}
            for j in range(3):
                field={'pointer':f'/lines/{j}/text','value':f'Synthetic observation {i}/{j}: '+('configuration only; '*50),
                       'byte_start':0,'byte_end':1000}
                field['review_ref']=review._field_handle(source,field)
                source['fields'].append(field)
            sources.append(source)
        chosen=sources[2]['fields'][2]
        state={'role':'analyst_judgment_not_source_evidence',
               'questions':[{'id':'current','question':'Did configuration result in execution?',
                             'answer':'Current answer: configuration is observed. '+('Bounded explanation. '*80),
                             'limitations':['Current limit: execution is unobserved. '+('Acquisition scope. '*80)],
                             'claim_refs':[{'id':'claim-current','version':1}]}],
               'grounding':{'claims':[{'id':'claim-current','statement':'Configuration does not establish execution.',
                                     'limitations':['No independent execution trace.'],'role':'analyst_claim_not_source_evidence'}],
                            'observations':[{'source_ref':{'id':'source-2'},'field_pointer':chosen['pointer'],
                                'literal':chosen['value'],'byte_start':0,'byte_end':1000,
                                'coordinate_validation':'retained_source_version_and_exact_utf8_span_only',
                                'semantic_validation':False}]},
               'report_draft':{'role':'authored_report_not_source_evidence','content':'Report prose. '*500},
               'task_focus':{'role':'navigation','tasks':['Navigation projection. '*160]}}
        body={'stage':'final','sources':sources,'omitted':{'sources':4},'analyst_state':state,
              'candidate_response':{'role':'analyst_draft_not_source_evidence','text':'Candidate prose. '*330},
              'observed_scope':{'role':'observation_coverage_not_completion','inventory':'Coverage projection. '*140}}
        snapshot={'prompt':review._json(body),'refs':{},'review_refs':{
            field['review_ref']:review._review_coordinate(source,field)
            for source in sources for field in source['fields']}}
        notes=[];findings=[];dispositions=[]
        for i in range(5):
            notes.append({'id':f'q-{i}','revision':1,'question':f'Previous question {i}?',
                          'answer':f'Complete prior answer {i}: '+('The configured artifact does not prove successful execution. '*22),
                          'limitations':[f'Prior limitation {i}: acquisition excludes volatile memory.'],
                          'assessment':'inconclusive'})
            findings.append({'kind':'executive_question','executive_round':1,'review_id':'prior',
                             'finding_id':f'f-{i}','question':notes[-1]['question']})
            dispositions.append({'review_id':'prior','finding_id':f'f-{i}','question_id':f'q-{i}',
                'disposition':'scope_limited','reason':f'Full reason {i}: '+('The retained scope is bounded. '*18),
                'limitation':f'Full explicit limitation {i}.','citation_refs':[f'C{i}-native-handle'],
                'citations':[{'literal':'Expanded coordinate copy. '*30}]})
        return snapshot,notes,{'findings':findings,'dispositions':dispositions}

    def compose(self,snapshot,notes,projection):
        with patch.object(review,'_question_records',return_value=notes):
            return review._executive_snapshot({},snapshot,2,projection)

    def test_large_current_packet_and_all_five_answers_fit_without_semantic_clipping(self):
        snapshot,notes,projection=self.packet()
        original=json.loads(snapshot['prompt'])
        # Reproduce the old failure even after it discarded every source but one
        # and all expanded history citation copies.
        old=deepcopy(original);old['sources']=old['sources'][:1]
        old['executive_followup']={'previous_round':{'questions':[
            {'question':f['question'],'main_response':{k:v for k,v in d.items() if k!='citations'}}
            for f,d in zip(projection['findings'],projection['dispositions'])], 'main_answers':notes}}
        self.assertGreater(len(review._json(old))+len(review.EXECUTIVE_SYSTEM),review.MAX_INPUT)
        result=self.compose(snapshot,notes,projection);body=json.loads(result['prompt'])
        self.assertLessEqual(len(result['prompt'])+len(review.EXECUTIVE_SYSTEM),review.MAX_INPUT)
        previous=body['executive_followup']['previous_round']
        self.assertEqual(previous['main_answers'],notes)
        self.assertEqual(len(previous['questions']),5)
        for item,expected in zip(previous['questions'],projection['dispositions']):
            for key in ('disposition','reason','limitation','citation_refs'):
                self.assertEqual(item['main_response'][key],expected[key])
        for key in ('questions','grounding','report_draft'):
            self.assertEqual(body['analyst_state'][key],original['analyst_state'][key])
        self.assertTrue(body['executive_presentation']['omitted_optional_views'])
        self.assertIn(original['sources'][2]['fields'][2]['review_ref'],result['review_refs'])
        handles={f['review_ref'] for s in body['sources'] for f in s['fields']}
        self.assertEqual(set(result['review_refs']),handles)
        for source in body['sources']:
            for field in source['fields']:
                self.assertEqual(field['review_ref'],review._field_handle(source,field))
                self.assertIn(field['value'],result['refs'][source['source_id']][field['pointer']])
        self.assertEqual(body['omitted']['sources'],4+3-len(body['sources']))

    def test_source_trimming_removes_handles_and_preserves_selected_grounding(self):
        snapshot,notes,projection=self.packet()
        original=json.loads(snapshot['prompt'])
        # The authored report stays mandatory even when previews must shrink.
        with patch.object(review,'MAX_INPUT',33000):
            result=self.compose(snapshot,notes,projection)
        body=json.loads(result['prompt'])
        handles={f['review_ref'] for s in body['sources'] for f in s['fields']}
        self.assertLess(len(handles),len(snapshot['review_refs']))
        self.assertEqual(set(result['review_refs']),handles)
        self.assertIn(original['sources'][2]['fields'][2]['review_ref'],handles)
        self.assertEqual(body['analyst_state']['grounding'],original['analyst_state']['grounding'])
        actual_refs={}
        for source in body['sources']:
            for field in source['fields']:
                review._remember_field(actual_refs.setdefault(source['source_id'],{}),field['pointer'],field['value'])
        self.assertEqual(result['refs'],actual_refs)
        self.assertEqual(body['omitted']['sources'],4+3-len(body['sources']))
        self.assertEqual(body['analyst_state']['report_draft'],original['analyst_state']['report_draft'])
        self.assertLessEqual(len(result['prompt'])+len(review.EXECUTIVE_SYSTEM),33000)

    def test_report_view_cannot_be_omitted_to_make_irreducible_packet_pass(self):
        snapshot,notes,projection=self.packet()
        body=json.loads(snapshot['prompt'])
        body['analyst_state']['report_draft']['content']='Required authored report. '*1800
        snapshot['prompt']=review._json(body)
        snapshot['report_draft_fingerprint']='synthetic-bound-report-fingerprint'
        with self.assertRaisesRegex(ValueError,'Complete executive question/answer history exceeds'):
            self.compose(snapshot,notes,projection)

    def test_irreducible_history_still_fails_explicitly(self):
        snapshot,notes,projection=self.packet()
        notes[0]['answer']='Unabridged required answer. '*1800
        with self.assertRaisesRegex(ValueError,'Complete executive question/answer history exceeds'):
            self.compose(snapshot,notes,projection)

    def test_small_packet_keeps_existing_report_and_citation_views(self):
        snapshot,notes,projection=self.packet()
        body=json.loads(snapshot['prompt'])
        for key in ('report_draft','task_focus'):body['analyst_state'][key]={'content':'Small view'}
        body['candidate_response']={'text':'Small report'};body['observed_scope']={'inventory':'Small coverage'}
        snapshot['prompt']=review._json(body)
        result=self.compose(snapshot,notes,projection);actual=json.loads(result['prompt'])
        for key in body:self.assertEqual(actual[key],body[key])
        self.assertNotIn('executive_presentation',actual)
        for item in actual['executive_followup']['previous_round']['questions']:
            self.assertTrue(item['main_response']['citations'])


if __name__=='__main__':unittest.main()
