import asyncio
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

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
        self.assertEqual(result['cases'][0]['matched_terms'], ['예약', 'permission_denied'])
        detail = library.get({'archive_id':saved['archive_id']})
        self.assertEqual(detail['source_state'], 'unchanged')
        self.assertEqual(detail['notes'][0]['gaps'], ['승인 여부 미확인'])
        source = library.get({'action':'source', 'archive_id':saved['archive_id'], 'source_id':self.source})
        self.assertEqual(source['source']['data']['lines'][0]['text'], 'Synthetic only: cron exit=1 permission_denied')
        self.assertEqual(source['source']['request']['data']['arguments']['path'], 'cron.log')
        self.assertTrue(source['reference_only'])
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
        self.assertEqual(CaseLibrary(self.current).search({})['total'], 0)
        self.assertFalse(self.library_path.exists())
        self.assertIn('error', self.call(self.current, action='save'))

    def test_missing_source_db_preserves_archived_original(self):
        saved = self.save()
        self.first.db.rename(self.first.db.with_suffix('.preserved'))
        detail = CaseLibrary(self.current).get({'archive_id':saved['archive_id']})
        self.assertEqual(detail['source_state'], 'unavailable')
        self.assertEqual(detail['sources'][0]['id'], self.source)

    def test_keyword_search_not_like_wildcards_or_sql(self):
        self.save()
        self.assertEqual(self.call(self.current, action='search', query='notfound%')['total'], 0)
        self.assertEqual(self.call(self.current, action='search', query="' OR unknown=unknown--")['total'], 0)
        self.assertIn('error', self.call(self.current, action='get', archive_id='../../case.json'))

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
