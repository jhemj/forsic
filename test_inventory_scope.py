"""Offline inventory scope and compaction; synthetic files, no model or network."""
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from forsic_plugin.context_results import summarize
from forsic_plugin.image_reader import inspect
from forsic_plugin.investigation_context import recent_observed_scope
from forsic_plugin.report_driven.host import source_record
from forsic_plugin.report_driven.measurement import acquisition_context, measurement_view
from forsic_plugin.report_driven.source_locator import retained_coverage
import test_efficiency_contracts as efficiency


class InventoryScopeTests(unittest.TestCase):
    def setUp(self):
        for target in ('socket.socket.connect', 'socket.create_connection'):
            blocker=patch(target,side_effect=AssertionError('offline inventory test forbids network'))
            blocker.start();self.addCleanup(blocker.stop)

    def body(self, count=3, **extra):
        return {'tool':'forsic_image_files','path':'synthetic.e01','file_path':'/settings/jobs',
                'representation':'directory_inventory','listing_complete':True,'content_not_measured':True,
                'complete':True,'next_offset':None,
                'entries':[{'path':'/settings/jobs/account-'+str(i),'bytes':i*9,'mode':'-rw-------'} for i in range(count)],
                **extra}

    def test_real_listing_pages_measure_names_without_opening_contents(self):
        with tempfile.TemporaryDirectory() as tmp:
            image=Path(tmp)/'synthetic.e01'
            image.write_bytes(b'EVF\x09\x0d\x0a\xff\x00'+b'\x00\x01\x00\x00\x00')
            entries=[]
            for i in range(101):
                stat=SimpleNamespace(st_size=i,st_ino=i,st_mode=0o100600,st_mtime=1)
                entries.append(SimpleNamespace(path='/settings/jobs/'+str(i),get=lambda value=stat:SimpleNamespace(lstat=lambda:value)))
            node=SimpleNamespace(scandir=lambda:iter(entries),open=MagicMock(side_effect=AssertionError('contents not measured')))
            target=SimpleNamespace(disks=SimpleNamespace(add=lambda _:None,apply=lambda:None),volumes=[SimpleNamespace(offset=0)])
            modules={'dissect.target':SimpleNamespace(Target=lambda _:target),
                     'dissect.target.containers.ewf':SimpleNamespace(EwfContainer=lambda _:SimpleNamespace(close=lambda:None)),
                     'dissect.target.filesystem':SimpleNamespace(open=lambda _:SimpleNamespace(get=lambda _:node))}
            with patch.dict(sys.modules,modules):
                args={'action':'list','path':image.name,'file_path':'/settings/jobs'}
                first=inspect(image,args);last=inspect(image,{**args,'offset':100})
                self.assertEqual(len(first['entries']),100);self.assertEqual(first['next_offset'],100)
                self.assertFalse(first['complete']);self.assertFalse(first['listing_complete'])
                self.assertTrue(last['complete']);self.assertFalse(last['listing_complete'])
                node.scandir=lambda:iter(entries[:3])
                small=inspect(image,args)
            self.assertTrue(small['listing_complete']);self.assertTrue(small['content_not_measured'])
            self.assertEqual(small['representation'],'directory_inventory')
            self.assertNotIn('complete_file',small);node.open.assert_not_called()

    def test_source_lookup_pruning_and_projection_keep_the_same_inventory_scope(self):
        x=efficiency.EfficiencyContracts();x.setUp();self.addCleanup(x.doCleanups)
        args={'path':'synthetic.e01','action':'list','file_path':'/settings/jobs'}
        with patch.object(x.case,'image_files',return_value=self.body()):
            result=x.call('forsic_image_files',args)
        self.assertNotIn('error',result,result)
        crumb=json.loads(summarize('forsic_image_files',args,json.dumps(result)))
        source=source_record(x.case.event(result['evidence_id']))
        coverage=json.loads(source['coverage'])
        self.assertEqual(coverage['representation'],'directory_inventory')
        self.assertTrue(coverage['listing_complete']);self.assertTrue(coverage['content_not_measured'])
        self.assertEqual(coverage['returned_entries_count'],3)
        self.assertEqual(crumb['inventory']['omitted_entries'],0)
        self.assertEqual(crumb['inventory']['source_ref'],result['source_ref'])
        discovered=x.call(crumb['lookup']['tool'],crumb['lookup']['arguments'])
        self.assertNotIn('error',discovered,discovered)
        for i,item in enumerate(crumb['inventory']['entries']):
            self.assertEqual(item['pointer'],'/entries/'+str(i))
            self.assertEqual(item['path'],result['entries'][i]['path'])
            self.assertEqual(item['bytes'],result['entries'][i]['bytes'])
            self.assertEqual(item['mode'],result['entries'][i]['mode'])
            lookup={**crumb['lookup']['arguments'],'source_version':discovered['source_ref']['version'],
                    'pointer':item['pointer']+'/path'}
            page=x.call('forsic_reporting',lookup)
            self.assertNotIn('error',page,page)
            self.assertEqual(page['text'],item['path'])
            self.assertTrue(page['acquisition_context']['returned_scope']['content_not_measured'])
            self.assertEqual(page['acquisition_context']['field_role'],'metadata')
        resolved={'case':x.case,'conversation_id':'synthetic-session'}
        observed=recent_observed_scope(resolved,{'sources':[source]})
        self.assertEqual(observed[0]['source_id'],result['evidence_id'])
        self.assertTrue(observed[0]['observed']['content_not_measured'])
        self.assertEqual(observed[0]['observed']['returned_entries_count'],3)
        self.assertEqual(measurement_view(self.body())['representation'],'directory_inventory')
        self.assertNotIn('complete_file',acquisition_context(self.body(),'/entries/0/path')['returned_scope'])

    def test_bounded_entries_and_long_paths_have_exact_references_and_omissions(self):
        for count,extra in [(0,{}),(101,{}),(3,{'file_path':'x'*2500})]:
            body=self.body(count,evidence_id='retained',source_ref={'id':'retained','version':'v1'},**extra)
            if count==3:body['entries'][0]['path']='a'*3000
            crumb=summarize('forsic_image_files',{'action':'list'},json.dumps(body))
            self.assertIsNotNone(crumb);self.assertLessEqual(len(crumb),2000)
            view=json.loads(crumb);inv=view['inventory']
            self.assertEqual(inv['source_ref'],{'id':'retained','version':'v1'})
            self.assertEqual(len(inv['entries'])+inv['omitted_entries'],count)
            self.assertLessEqual(len(inv['entries']),8)
            for item in inv['entries']:
                original=body['entries'][int(item['pointer'].rsplit('/',1)[1])]
                self.assertEqual(item['path'],original['path'])
            self.assertTrue(view['returned_scope']['content_not_measured'])
        source={'coverage':json.dumps({**self.body(),'scope':'x'*3000})}
        kept=json.loads(retained_coverage(source))
        self.assertTrue(kept['listing_complete']);self.assertTrue(kept['content_not_measured'])

    def test_closure_recovery_choices_remain_indivisible_and_budgeted(self):
        for size in (1,30):
            recovery={'committed_gaps':[{'gap_id':'G-'+str(i),'gap_text':'unreviewed settings '+str(i)*size} for i in range(size)],
                      'proposed_gaps':[{'gap_text':'new uncommitted gap '+str(i)*size} for i in range(size)],
                      'draft_fingerprint':'f'*64}
            body={'error':'No question saved','error_code':'invalid_question_fields',
                  'invalid_fields':[{'path':'gap_dispositions','message':'use committed IDs'}],
                  'recovery':recovery}
            crumb=summarize('forsic_question',{},json.dumps(body))
            self.assertIsNotNone(crumb);self.assertLessEqual(len(crumb),2000)
            view=json.loads(crumb)['recovery']
            self.assertEqual(view['draft_fingerprint'],recovery['draft_fingerprint'])
            for key in ('committed_gaps','proposed_gaps'):
                if key in view:self.assertEqual(view[key],recovery[key])
                else:self.assertEqual(view['omitted_'+key],size)

    def test_compaction_accumulates_initial_rejection_omissions(self):
        recovery={'committed_gaps':[{'gap_id':'G-'+str(i),'gap_text':'선택 '+str(i)+'가'*200} for i in range(16)],
                  'omitted_committed_gaps':184,'proposed_gaps':[{'gap_text':'검토 '+str(i)+'나'*200} for i in range(16)],
                  'omitted_proposed_gaps':184,'draft_fingerprint':'f'*64}
        problems=[{'path':'gap_dispositions['+str(i)+']','error':'선택을 확인하세요.'*80} for i in range(6)]
        body={'error':'No question saved','error_code':'invalid_question_fields',
              'invalid_fields':problems,'invalid_field_count':200,'omitted_invalid_fields':194,'recovery':recovery}
        value=json.loads(summarize('forsic_question',{},json.dumps(body)))
        self.assertEqual(value['invalid_field_count'],200)
        self.assertEqual(value['omitted_invalid_fields']+len(value['invalid_fields']),200)
        for key in ('committed_gaps','proposed_gaps'):
            self.assertEqual(value['recovery']['omitted_'+key]+len(value['recovery'].get(key,[])),200)


if __name__=='__main__':unittest.main()
