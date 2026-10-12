"""Read-only investigation relationship/source UI contracts with synthetic cases."""
import json
import tempfile
import unittest
from unittest.mock import Mock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_report_driven import fixture, worked
from forsic_plugin.dashboard import plugin_api as api
from forsic_plugin.report_driven.current_judgment import current_judgment

class RelationshipAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.case,self.results,_=fixture(self.tmp.name);worked(self.case)
        catalog=Mock();catalog.get.side_effect=lambda case_id='',session_id='': {'manifest':self.case.manifest} if case_id=='synthetic' else None
        self.patch=patch.object(api,'directory',return_value=catalog);self.patch.start();self.addCleanup(self.patch.stop)
        app=FastAPI();app.include_router(api.router);self.client=TestClient(app)
    def test_common_judgment_and_exact_source_are_read_only(self):
        before=self.case.db.read_bytes()
        view=self.client.get('/report-driven',params={'case_id':'synthetic'}).json()
        self.assertEqual(view['judgments'],{q['id']:current_judgment(view['state'],q) for q in view['state']['questions']})
        obs=view['state']['observations'][0]
        result=self.client.get('/source-page',params={'case_id':'synthetic','source_id':obs['source_ref']['id'],
            'source_version':obs['source_ref']['version'],'pointer':obs['field_pointer'],'offset':obs['byte_start'],'limit':obs['byte_end']-obs['byte_start']})
        self.assertEqual(result.status_code,200);self.assertEqual(result.json()['text'],obs['literal'])
        self.assertEqual(self.case.db.read_bytes(),before)
    def test_foreign_and_stale_source_are_rejected(self):
        view=self.client.get('/report-driven',params={'case_id':'synthetic'}).json();src=view['state']['sources'][0]
        params={'case_id':'synthetic','source_id':src['id'],'source_version':src['version']}
        for changed in ({'source_version':'old'},{'source_id':'foreign'}):
            self.assertEqual(self.client.get('/source-page',params={**params,**changed}).status_code,409)
        self.assertEqual(self.client.get('/source-page',params={**params,'case_id':'other'}).status_code,404)
    def test_explicit_case_and_bounded_offset(self):
        for params,code in [({},422),({'case_id':'','source_id':'x','source_version':'v'},400),
                            ({'case_id':'synthetic','source_id':'x','source_version':'v','offset':-1},422),
                            ({'case_id':'synthetic','source_id':'x','source_version':'v','limit':99999},422)]:
            self.assertEqual(self.client.get('/source-page',params=params).status_code,code)
