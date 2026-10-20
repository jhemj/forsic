import asyncio
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

from forsic_plugin.evidence import Case
from forsic_plugin.case_library import CaseLibrary


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.library_path = self.root / 'library.sqlite3'
        self.first = self.make_case('past')
        self.current = self.make_case('current')
        self.real = self.make_case('real', synthetic=False)
        self.source = self.call(self.first, 'read', path='cron.log')['evidence_id']
        self.note = self.call(self.first, 'note', action='save', question='예약 작업 실패 원인은?',
                              answer='permission_denied로 출력 파일을 만들지 못했다.', evidence_ids=[self.source],
                              alternatives=['정상 작업의 권한 변경 가능성'], gaps=['승인 여부 미확인'], next_checks=['승인 기록 확인'])['note']

    def tearDown(self): self.tmp.cleanup()

    def make_case(self, name, synthetic=True):
        folder = self.root / name
        (folder / 'evidence').mkdir(parents=True)
        (folder / 'evidence/cron.log').write_text('Synthetic only: cron exit=1 permission_denied\n')
        path = folder / 'case.json'
        path.write_text(json.dumps({'case_id':name, 'label':name, 'question':'예약 작업 성공 여부', 'scope':'예약 작업 로그', 'synthetic':synthetic,
                                    'evidence_root':str(folder / 'evidence'), 'output_root':str(folder / 'out'), 'library_path':str(self.library_path)}))
        return Case(path)

    def call(self, case, name='cases', **args):
        return json.loads(case.invoke('forsic_' + name, args))

    def save(self): return self.call(self.first, action='save', tags=['Linux', '예약 작업'])

    def test_round_trip_sources_and_no_write_on_search(self):
        saved = self.save()
        before = self.library_path.read_bytes()
        old_db = self.first.db.read_bytes()
        library = CaseLibrary(self.current)
        result = library.search({'query':'예약 permission_denied'})
        self.assertEqual(result['total'], 1)
        self.assertTrue(result['reference_only'])
        self.assertFalse(result['current_case_evidence'])
        self.assertTrue(result['cases'][0]['reference_only'])
        self.assertFalse(result['cases'][0]['current_case_evidence'])
        self.assertEqual(result['cases'][0]['matched_terms'], ['예약', 'permission_denied'])
        detail = library.invoke(result['cases'][0]['read_call']['arguments'])
        self.assertTrue(detail['reference_only'])
        self.assertFalse(detail['current_case_evidence'])
        self.assertEqual(detail['source_state'], 'unchanged')
        self.assertEqual(detail['notes'][0]['gaps'], ['승인 여부 미확인'])
        source = library.invoke(detail['sources'][0]['source_call']['arguments'])
        self.assertEqual(source['source']['data']['lines'][0]['text'], 'Synthetic only: cron exit=1 permission_denied')
        self.assertEqual(source['source']['request']['data']['arguments']['path'], 'cron.log')
        self.assertTrue(source['reference_only'])
        self.assertFalse(source['current_case_evidence'])
        self.assertEqual(source['source_state'], 'unchanged')
        self.assertEqual(before, self.library_path.read_bytes())
        self.assertEqual(old_db, self.first.db.read_bytes())

    def test_revision_reuse_and_correction(self):
        original = self.save()
        self.assertTrue(self.save()['reused'])
        self.call(self.first, 'note', action='save', note_id=self.note['note_id'], revision=1,
                  question=self.note['question'], answer='권한 오류는 기록됐지만 실행 전후 범위는 미확인',
                  evidence_ids=[self.source], correction_reason='범위 표현 수정')
        library = CaseLibrary(self.current)
        self.assertEqual(library.get({'archive_id':original['archive_id']})['source_state'], 'changed')
        new = self.save()
        self.assertNotEqual(original['revision'], new['revision'])
        detail = library.get({'archive_id':new['archive_id']})
        self.assertEqual(len(detail['versions']), 2)
        self.assertEqual(detail['source_state'], 'unchanged')
        old = library.get({'archive_id':new['archive_id'], 'revision':original['revision']})
        self.assertFalse(old['is_latest_archive'])
        self.assertIn('출력 파일', old['notes'][0]['answer'])

    def test_synthetic_separation_and_default_excludes_current(self):
        saved = self.save()
        self.assertEqual(self.call(self.first, action='search')['total'], 0)
        self.assertEqual(self.call(self.first, action='search', exclude_current=False)['total'], 1)
        self.assertEqual(self.call(self.real, action='search')['total'], 0)
        self.assertIn('error', self.call(self.real, action='get', archive_id=saved['archive_id']))

    def test_archive_is_never_current_evidence(self):
        saved = self.save()
        reference = self.call(self.current, action='source', archive_id=saved['archive_id'], source_id=self.source)
        for eid in (self.source, reference['evidence_id']):
            self.assertIn('error', self.call(self.current, 'note', action='save', question='q', answer='a', evidence_ids=[eid]))
            self.assertIn('error', self.call(self.current, 'report', markdown='x', evidence_ids=[eid]))
            self.assertIn('error', self.call(self.current, 'report', snapshot={'summary':'x','findings':[{'title':'t','detail':'d','evidence_ids':[eid]}]}))

    def test_missing_store_is_not_created_by_read(self):
        result = CaseLibrary(self.current).search({})
        self.assertEqual(result['total'], 0)
        self.assertTrue(result['reference_only'])
        self.assertFalse(result['current_case_evidence'])
        self.assertFalse(self.library_path.exists())
        self.assertIn('error', self.call(self.current, action='save'))

    def test_missing_source_db_preserves_archived_original(self):
        saved = self.save()
        self.first.db.rename(self.first.db.with_suffix('.preserved'))
        library = CaseLibrary(self.current)
        detail = library.get({'archive_id':saved['archive_id']})
        self.assertEqual(detail['source_state'], 'unavailable')
        self.assertEqual(detail['sources'][0]['id'], self.source)
        source = library.invoke(detail['sources'][0]['source_call']['arguments'])
        self.assertEqual(source['source_state'], 'unavailable')
        self.assertEqual(source['source']['id'], self.source)

    def test_keyword_search_not_like_wildcards_or_sql(self):
        self.save()
        result = self.call(self.current, action='search', query='notfound%')
        self.assertEqual(result['total'], 0)
        self.assertTrue(result['reference_only'])
        self.assertFalse(result['current_case_evidence'])
        self.assertEqual(self.call(self.current, action='search', query="' OR unknown=unknown--")['total'], 0)
        self.assertIn('error', self.call(self.current, action='get', archive_id='../../case.json'))

    def test_navigation_pins_details_sources_and_note_pages_to_archived_revision(self):
        self.call(self.first, 'note', action='save', question='추가 확인 질문', answer='추가 관측은 미확인', evidence_ids=[self.source])
        old = self.save()
        library = CaseLibrary(self.current)
        card = library.search({'query': 'permission_denied'})['cases'][0]
        detail = library.invoke({**card['read_call']['arguments'], 'limit': 1})
        source_call = detail['sources'][0]['source_call']
        next_call = detail['next_call']
        self.assertEqual(card['read_call']['tool'], 'forsic_cases')
        for call in (card['read_call'], source_call, next_call):
            self.assertEqual(call['arguments']['archive_id'], old['archive_id'])
            self.assertEqual(call['arguments']['revision'], old['revision'])
        self.call(self.first, 'note', action='save', note_id=self.note['note_id'], revision=1,
                  question=self.note['question'], answer='정정된 참고 사례의 답', evidence_ids=[self.source], correction_reason='새 관측 반영')
        latest = self.save()
        self.assertNotEqual(old['revision'], latest['revision'])
        preserved = library.invoke(card['read_call']['arguments'])
        self.assertEqual(preserved['revision'], old['revision'])
        self.assertFalse(preserved['is_latest_archive'])
        self.assertEqual(preserved['source_state'], 'changed')
        page = library.invoke(next_call['arguments'])
        self.assertEqual(page['revision'], old['revision'])
        self.assertEqual(page['notes'], preserved['notes'][1:2])
        source = library.invoke(source_call['arguments'])
        self.assertEqual(source['revision'], old['revision'])
        self.assertFalse(source['is_latest_archive'])
        self.assertEqual(source['source_state'], 'changed')

    def test_native_question_rejects_past_citations_and_current_reference_receipt(self):
        from forsic_plugin import register
        from forsic_plugin.report_driven.grounded_judgment import read_refs
        from forsic_plugin.report_driven.host import current, source_record
        saved = self.save()
        past_result = {**self.first.event(self.source)['data'], 'evidence_id': self.source}
        past_ref = read_refs(self.first, past_result)['presented_spans'][0]['citation_ref']
        ctx = Mock()
        ctx.get_config.side_effect = lambda key: str(self.root / 'intake') if key == 'intake_root' else None
        with patch('forsic_plugin.intake.Intake.case', return_value=self.current), \
             patch('forsic_plugin.investigation_context.native_goal_binding',
                   return_value={'goal_id': 'current-goal', 'conversation_id': 'current-session'}):
            register(ctx)
            handlers = {call.kwargs['name']: call.kwargs['handler'] for call in ctx.register_tool.call_args_list}
            def native(tool, args):
                return json.loads(handlers[tool](args, session_id='current-session'))
            reference = native('forsic_cases', {'action': 'source', 'archive_id': saved['archive_id'],
                                              'revision': saved['revision'], 'source_id': self.source})
            self.assertNotIn('error', reference)
            self.assertTrue(reference['reference_only'])
            self.assertFalse(reference['current_case_evidence'])
            self.assertNotIn('presented_spans', reference)
            receipt_id = reference['evidence_id']
            receipt = source_record(self.current.event(receipt_id))
            forged_ref = f"C2:{receipt_id}:{receipt['content_sha256']}:0"
            before = current(self.current)
            for index, citation in enumerate((past_ref, forged_ref)):
                result = native('forsic_question', dict(operation_id='past-citation-' + str(index),
                    question='현재 사건의 실행 여부는?', scope='현재 사건 자료', answer='과거 사례를 현재 사실로 채택',
                    reason='잘못된 사례 인용을 차단하는 합성 시험',
                    judgment={'citation_refs': [citation], 'assertion_kind': 'fact', 'reasoning_summary': '참고 사례는 현재 증거가 아니다.'}))
                self.assertIn('actual retained evidence result', result['error'])
                after = current(self.current)
                for field in ('questions', 'claims', 'observations', 'assessments'):
                    self.assertEqual(after[field], before[field])

    def test_pagination_and_ranking(self):
        self.save()
        second = self.make_case('other')
        self.call(second, 'note', action='save', question='예약 작업', answer='다른 원인 미확인')
        self.call(second, action='save')
        page = self.call(self.current, action='search', query='예약 permission_denied', limit=1)
        self.assertEqual(page['total'], 2)
        self.assertEqual(page['cases'][0]['case_id'], 'past')
        self.assertEqual(page['next_offset'], 1)
        next_page = self.call(self.current, action='search', query='예약 permission_denied', limit=1, offset=1)
        self.assertEqual(next_page['cases'][0]['case_id'], 'other')

    def test_modified_archive_rejected(self):
        saved = self.save()
        with closing(sqlite3.connect(self.library_path)) as db, db:
            db.execute("UPDATE versions SET body=replace(body,'permission_denied','changed')")
        self.assertIn('error', self.call(self.current, action='get', archive_id=saved['archive_id']))

    def test_http_api_e2e(self):
        from fastapi import FastAPI
        from forsic_plugin.dashboard import plugin_api
        import httpx
        app = FastAPI()
        app.include_router(plugin_api.router)
        async def run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
                with patch.object(plugin_api, 'current_case', return_value=self.first):
                    response = await client.post('/library', json={'tags':['예약 작업']})
                    self.assertEqual(response.status_code, 200)
                    saved = response.json()
                with patch.object(plugin_api, 'current_case', return_value=self.current):
                    response = await client.get('/library', params={'query':'permission_denied'})
                    self.assertEqual(response.json()['total'], 1)
                    response = await client.get('/library/' + saved['archive_id'])
                    self.assertEqual(response.json()['notes'][0]['note_id'], self.note['note_id'])
                    response = await client.get('/library/' + saved['archive_id'] + '/sources/' + self.source, params={'revision':saved['revision']})
                    self.assertEqual(response.json()['source']['id'], self.source)
                    self.assertEqual((await client.get('/library/unknown')).status_code, 400)
        asyncio.run(run())


if __name__ == '__main__': unittest.main()
