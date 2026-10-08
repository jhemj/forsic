"""Offline review integration: no live case, service, model or network."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace as Obj
import unittest
from unittest.mock import patch
import zipfile

from test_report_driven import fixture
from forsic_plugin.notes import note
from forsic_plugin.report_driven.host import current, invoke
from copy import deepcopy
from forsic_plugin.report_driven.host import review_material, ref


class ReportReviewProjectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.case, self.results, self.note = fixture(self.tmp.name)
        self.config = {'model': {'provider': 'custom', 'default': 'qwen3.8:latest',
                                'base_url': 'http://localhost:11434/v1'},
                       'auxiliary': {'report_review': {'timeout': 900}}}

    def tearDown(self):
        self.tmp.cleanup()

    def call(self, action, **args):
        return invoke(self.case, {'action': action,
                                 'snapshot_id': current(self.case)['meta']['snapshot_id'], **args})

    def check_documents(self, result, text):
        self.assertEqual(len(result['files']), 4)
        for name in result['files']:
            path = Path(name)
            if path.suffix == '.html':
                body = path.read_text()
            else:
                with zipfile.ZipFile(path) as doc:
                    body = doc.read('word/document.xml').decode()
            self.assertIn(text, body, name)
        manifest = json.loads(Path(result['manifest']).read_text())
        self.assertEqual(manifest['status']['investigation'], 'partial')
        self.assertEqual(manifest['status']['semantic_gate'], 'not_reviewed')
        return manifest

    def test_timeout_visible_in_four_files_and_not_resent_after_patience_change(self):
        before = current(self.case)
        with patch('hermes_cli.config.load_config_readonly', return_value=self.config), \
             patch('agent.auxiliary_client.call_llm', side_effect=TimeoutError('secret response body')) as model:
            first = self.call('review')
            self.config['auxiliary']['report_review']['timeout'] = 1200
            again = self.call('review')
            self.assertTrue(again['reused'])
            self.assertEqual(model.call_count, 1)
            self.assertEqual(model.call_args.kwargs['task'], 'report_review')
            self.assertIsNone(model.call_args.kwargs['timeout'])
            self.assertTrue(model.call_args.kwargs['stream'])
            self.assertEqual(model.call_args.kwargs['provider'], 'custom')
            result = self.call('render', review_id=first['review_id'])
            self.assertEqual(model.call_count, 1)
        self.assertEqual(current(self.case), before)
        manifest = self.check_documents(result, '응답을 확인하지 못했습니다')
        self.assertEqual(manifest['review']['review_status'], 'delivery_unconfirmed')
        self.assertEqual(manifest['review']['input_snapshot_id'], before['meta']['snapshot_id'])
        self.assertNotIn('secret response body', json.dumps(first))
        self.assertNotIn('secret response body', Path(result['manifest']).read_text())

    def test_received_advice_same_snapshot_not_semantic_approval(self):
        advice = '명령 요청 기록만으로 최종 성공을 확정하지 마세요.'
        chunk = Obj(model='qwen3.8:latest', usage=None, choices=[Obj(
            delta=Obj(content=advice, reasoning_content='private reasoning'), finish_reason='stop')])
        with patch('hermes_cli.config.load_config_readonly', return_value=self.config), \
             patch('agent.auxiliary_client.call_llm', return_value=iter([chunk])) as model:
            review = self.call('review')
            sent = json.loads(model.call_args.kwargs['messages'][1]['content'])
            self.assertEqual(len(sent['original_results']), 2)
            self.assertIn('requested', json.dumps(sent['original_results']))
            self.assertTrue(all(r['request']['kind'] == 'tool_start' for r in sent['original_results']))
            result = self.call('render', review_id=review['review_id'])
            repeated = self.call('render', review_id=review['review_id'])
            self.assertEqual(result['bundle_id'], repeated['bundle_id'])
            self.assertEqual(model.call_count, 1)
        self.assertEqual(review['review_status'], 'received')
        manifest = self.check_documents(result, advice)
        self.assertEqual(result['snapshot_id'], review['input_snapshot_id'])
        self.assertEqual(manifest['review']['review_status'], 'received')
        self.assertNotIn('private reasoning', json.dumps(review))

    def test_no_review_or_missing_receipt_never_blocks_partial_or_calls_model(self):
        with patch('agent.auxiliary_client.call_llm') as model:
            absent = self.call('render')
            missing = self.call('render', review_id='f' * 64)
            invalid = self.call('render', review_id='../elsewhere')
            model.assert_not_called()
        self.check_documents(absent, '별도 결론 검토를 하지 않았습니다')
        self.check_documents(missing, '검토 기록을 확인할 수 없습니다')
        self.assertEqual(invalid['review']['review_status'], 'unavailable')

    def test_note_correction_makes_review_stale_without_mutating_old_bundle(self):
        chunk = Obj(model='qwen3.8:latest', usage=None, choices=[Obj(
            delta=Obj(content='이전 답변에 대한 검토 의견', reasoning_content=None), finish_reason='stop')])
        with patch('hermes_cli.config.load_config_readonly', return_value=self.config), \
             patch('agent.auxiliary_client.call_llm', return_value=iter([chunk])) as model:
            review = self.call('review')
            original = self.call('render', review_id=review['review_id'])
            preserved = {p: Path(p).read_bytes() for p in original['files'] + [original['manifest']]}
            note(self.case, {'action': 'save', 'note_id': self.note['note_id'],
                             'revision': self.note['revision'], 'question': self.note['question'],
                             'answer': '공통 작업인지 여전히 판단할 수 없습니다.',
                             'evidence_ids': self.note['evidence_ids'],
                             'correction_reason': '정확한 원문 범위를 반영한 문장 정정'})
            changed = self.call('render', review_id=review['review_id'])
            self.assertEqual(model.call_count, 1)
        self.assertEqual(changed['review']['review_status'], 'stale')
        self.assertNotEqual(changed['snapshot_id'], original['snapshot_id'])
        self.check_documents(changed, '이전 판단에 대한 검토입니다')
        for p, raw in preserved.items():
            self.assertEqual(Path(p).read_bytes(), raw)

    def test_oversize_review_not_sent_preserved_and_visible(self):
        source = self.case.record('tool_result', {'tool':'forsic_read','path':'large synthetic record',
                                                 'lines':[{'line':1,'text':'x' * 81000}]})
        note(self.case, {'action':'save','question':'긴 원문 검토','answer':'원문 길이로 검토 미완료',
                         'evidence_ids':[source]})
        with patch('hermes_cli.config.load_config_readonly', return_value=self.config), \
             patch('agent.auxiliary_client.call_llm') as model:
            review = self.call('review')
            result = self.call('render', review_id=review['review_id'])
            model.assert_not_called()
        self.assertEqual(review['review_status'], 'not_sent')
        self.check_documents(result, '검토 요청을 전송하지 못했습니다')

    def test_new_turn_does_not_resend_unknown_unchanged_material(self):
        with patch('hermes_cli.config.load_config_readonly', return_value=self.config), \
             patch('agent.auxiliary_client.call_llm', side_effect=TimeoutError()) as model:
            first = self.call('review')
            self.case.record('turn_start', {'status':'investigating'}, 'synthetic-native-session')
            second = self.call('review')
            result = self.call('render', review_id=first['review_id'])
        self.assertEqual(first['snapshot_id'], second['snapshot_id'])
        self.assertTrue(second['reused'])
        self.assertEqual(first['review_id'], second['review_id'])
        self.assertEqual(model.call_count, 1)
        self.assertEqual(result['review']['review_status'], 'delivery_unconfirmed')
        self.assertEqual(result['review']['applies_to_snapshot_id'], second['snapshot_id'])

    def test_question_selection_omits_unrelated_body_explicitly(self):
        other = self.case.record('tool_result', {'tool':'forsic_read','path':'unrelated synthetic log',
                                                'lines':[{'line':1,'text':'unrelated body secret marker'}]})
        added = note(self.case, {'action':'save','question':'다른 질문','answer':'범위 외 참고',
                                'evidence_ids':[other]})['note']
        selected = 'Q-' + self.note['note_id']
        chunk = Obj(model='qwen3.8:latest', usage=None, choices=[Obj(
            delta=Obj(content='선택 범위의 검토 의견', reasoning_content=None), finish_reason='stop')])
        with patch('hermes_cli.config.load_config_readonly', return_value=self.config), \
             patch('agent.auxiliary_client.call_llm', return_value=iter([chunk])) as model:
            review = self.call('review', question_ids=[selected])
            text = model.call_args.kwargs['messages'][1]['content']
            sent = json.loads(text)
            self.assertNotIn('unrelated body secret marker', text)
            self.assertIn(other, sent['unpresented_source_ids'])
            self.assertIn('Q-' + added['note_id'], sent['omitted_question_ids'])
            result = self.call('render', review_id=review['review_id'])
        self.assertEqual(result['review']['question_ids'], [selected])
        self.check_documents(result, '그 밖의 질문은 이 검토에 포함하지 않았습니다')

    def test_selected_hypothesis_keeps_cross_question_counterevidence(self):
        counter = self.case.record('tool_result', {'tool':'forsic_read','path':'counter log',
                                                  'lines':[{'line':1,'text':'distinct counterevidence body'}]})
        other = note(self.case, {'action':'save','question':'별도 질문','answer':'경쟁 설명 원문',
                                'evidence_ids':[counter]})['note']
        s = deepcopy(current(self.case))
        selected = 'Q-' + self.note['note_id']
        claim = next(c for c in s['claims'] if c['id']=='F-' + other['note_id'])
        hypothesis = next(h for h in s['hypotheses'] if h['question_ref']['id']==selected)
        hypothesis['counterevidence_refs'] = [ref('claim', claim)]
        material = review_material(self.case, s, [selected])
        self.assertIn(counter, [r['source_id'] for r in material['original_results']])
        self.assertIn('distinct counterevidence body', json.dumps(material))
        self.assertIn(claim['id'], [c['id'] for c in material['report_draft']['claims']])
        self.assertEqual(material['question_ids'], [selected])


if __name__ == '__main__':
    unittest.main()
