"""Future finalized reports only; all Telegram sends use in-memory fakes."""
import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

from test_report_driven import fixture
from forsic_plugin.report_driven.host import current, invoke
from forsic_plugin.report_driven.views import bundle, bundles
from forsic_plugin.report_driven.finalization import REPORT_NAMES
from telegram_reports import ReportDelivery


class TelegramReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.case, _, self.note = fixture(self.root)
        self.state = current(self.case)
        self.rendered = bundle(self.case, self.state)
        self.since = time.time()
        self.delivery = ReportDelivery(self.root/'delivery.json', self.case.output,
                                       self.case.config['case_id'], -123, 8, self.since)

    def tearDown(self):
        self.tmp.cleanup()

    def finalize(self):
        return invoke(self.case, {'action':'finalize', 'snapshot_id':self.state['meta']['snapshot_id'],
                                  'bundle_id':self.rendered['bundle_id']})

    def test_render_alone_is_draft_no_upload_old_selection_not_backlogged(self):
        send = AsyncMock()
        asyncio.run(self.delivery.tick(send))
        send.assert_not_awaited()
        with patch('forsic_plugin.evidence.time.time', return_value=self.since-1):
            self.finalize()
        asyncio.run(self.delivery.tick(send))
        send.assert_not_awaited()
        self.assertFalse(self.delivery.state['jobs'])

    def test_finalization_current_bundle_and_four_files_once(self):
        before = deepcopy(current(self.case))
        result = self.finalize()
        self.assertTrue(result['finalized'])
        self.assertFalse(result['published'])
        self.assertEqual(current(self.case), before)
        again = self.finalize()
        self.assertTrue(again['reused'])
        self.assertEqual(result['finalization_receipt'], again['finalization_receipt'])
        sent = []
        async def send(name, payload, caption):
            sent.append((name, payload, caption))
            return len(sent)+200
        counts = asyncio.run(self.delivery.tick(send))
        self.assertEqual(counts['sent'], 4)
        self.assertEqual([r[0] for r in sent], list(REPORT_NAMES))
        for name, payload, caption in sent:
            path = self.case.output/('report-bundle-'+self.rendered['bundle_id'])/name
            self.assertEqual(payload, path.read_bytes())
            self.assertIn('조사 보고서', caption)
        restarted = ReportDelivery(self.delivery.path,self.case.output,self.case.config['case_id'],-123,8,self.since)
        asyncio.run(restarted.tick(send))
        self.assertEqual(len(sent), 4)
        self.assertEqual(self.delivery.path.stat().st_mode & 0o777, 0o600)

    def test_unknown_and_process_loss_do_not_resend_or_block_other_files(self):
        self.finalize()
        attempts = []
        async def send(name, payload, caption):
            attempts.append(name)
            if name == 'executive.html':
                raise TimeoutError('token=secret must never be recorded')
            return len(attempts)
        counts = asyncio.run(self.delivery.tick(send))
        self.assertEqual(counts['delivery_unconfirmed'], 1)
        self.assertEqual(counts['sent'], 3)
        self.assertNotIn('token=secret',self.delivery.path.read_text())
        restarted = ReportDelivery(self.delivery.path,self.case.output,self.case.config['case_id'],-123,8,self.since)
        asyncio.run(restarted.tick(send))
        self.assertEqual(len(attempts),4)
        job = next(iter(restarted.state['jobs'].values()))
        job['status'] = 'sending'
        restarted.save()
        recovered = ReportDelivery(self.delivery.path,self.case.output,self.case.config['case_id'],-123,8,self.since)
        self.assertEqual(next(iter(recovered.state['jobs'].values()))['status'],'delivery_unconfirmed')

    def test_stale_snapshot_foreign_bundle_and_arbitrary_path_rejected(self):
        for changes in ({'snapshot_id':'old'}, {'bundle_id':'../evidence'}, {'bundle_id':'a'*64}):
            with self.subTest(changes=changes), self.assertRaises((ValueError,OSError)):
                invoke(self.case, {'action':'finalize','snapshot_id':self.state['meta']['snapshot_id'],
                    'bundle_id':self.rendered['bundle_id'],**changes})
        manifest = Path(self.rendered['manifest'])
        data = json.loads(manifest.read_text());data['case_id']='OTHER'
        manifest.write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            self.finalize()

    def test_changed_file_and_symlink_no_attachment(self):
        self.finalize()
        self.delivery.discover()
        path = self.case.output/('report-bundle-'+self.rendered['bundle_id'])/'executive.html'
        path.write_text('modified')
        send = AsyncMock()
        counts = asyncio.run(self.delivery.tick(send))
        send.assert_not_awaited()
        self.assertEqual(counts['blocked'],4)
        path.unlink();path.symlink_to(self.case.root/'request.log')
        with self.assertRaises(ValueError):
            self.finalize()

    def test_attachment_manifest_exact_names_and_destination_binding(self):
        before=self.delivery.path.read_bytes()
        for chat,topic in ((-999,8),(-123,99)):
            with self.assertRaises(ValueError):
                ReportDelivery(self.delivery.path,self.case.output,self.case.config['case_id'],chat,topic,self.since)
        self.assertEqual(before,self.delivery.path.read_bytes())
        manifest=Path(self.rendered['manifest']);data=json.loads(manifest.read_text())
        data['files'][0]['name']='../../request.log';manifest.write_text(json.dumps(data))
        with self.assertRaises(ValueError):self.finalize()

    def test_router_uses_only_bound_existing_topic_and_leaves_mention_state(self):
        from telegram_mirror import TopicRouter
        self.finalize()
        router=TopicRouter(self.root/'home',self.root/'intake',self.root/'router.json',-123,'session',self.root/'old.json')
        cid=self.case.config['case_id']
        item={'case_id':cid,'sessions':['session'],'session_id':'session','created_at':time.time(),
              'synthetic':False,'output_root':str(self.case.output),'manifest':str(self.case.manifest),'label':'test'}
        router.state.update(reports_since=self.since,routes={cid:{'session':'session','thread_id':8,'status':'ready'}})
        router.catalog.list=lambda:[item]
        mirror_state={'pending':None,'status':'ready'}
        mentions=self.root/'telegram-mentions.json';mentions.write_text('{"pending":"unchanged"}')
        before=mentions.read_bytes();sent=[]
        async def send_document(topic,name,payload,caption):sent.append((topic,name));return len(sent)
        with patch('telegram_mirror.Mirror') as mirror:
            mirror.return_value.state=mirror_state
            mirror.return_value.tick=AsyncMock()
            create=AsyncMock()
            asyncio.run(router.tick(create,AsyncMock(),send_document=send_document))
            create.assert_not_awaited()
        self.assertEqual(sent,[(8,name) for name in REPORT_NAMES])
        self.assertEqual(before,mentions.read_bytes())
        self.assertEqual(router.state['routes'][cid]['report_delivery']['sent'],4)

    def correct(self):
        self.case.record('note',{**self.note,'revision':self.note['revision']+1,
                                'answer':'앞선 설명을 정정한 현재 답입니다.'})

    def test_material_correction_holds_queued_and_new_final_supersedes(self):
        old_bundle=self.rendered['bundle_id'];self.finalize();self.delivery.discover()
        self.correct()
        send=AsyncMock()
        counts=asyncio.run(self.delivery.tick(send))
        send.assert_not_awaited();self.assertEqual(counts['stale'],4)
        self.assertTrue(bundles(self.case)[0]['stale'])
        self.state=current(self.case);self.rendered=bundle(self.case,self.state);self.finalize()
        send=AsyncMock(side_effect=[1,2,3,4])
        counts=asyncio.run(self.delivery.tick(send))
        self.assertEqual(counts['sent'],4)
        self.assertFalse(any(j['bundle_id']==old_bundle and j['status']=='sent' for j in self.delivery.state['jobs'].values()))

    def test_new_final_before_first_poll_sends_only_new_four(self):
        old_bundle=self.rendered['bundle_id'];self.finalize();self.correct()
        self.state=current(self.case);self.rendered=bundle(self.case,self.state);self.finalize()
        send=AsyncMock(side_effect=[1,2,3,4])
        counts=asyncio.run(self.delivery.tick(send))
        self.assertEqual(counts['sent'],4);self.assertEqual(counts['superseded'],4)
        self.assertFalse(any(j['bundle_id']==old_bundle and j['status']=='sent' for j in self.delivery.state['jobs'].values()))

    def test_turn_only_does_not_stale_but_correction_during_upload_stops_rest(self):
        self.finalize();old_snapshot=self.state['meta']['snapshot_id']
        self.case.record('turn_complete',{'visible':'completed'})
        self.assertNotEqual(old_snapshot,current(self.case)['meta']['snapshot_id'])
        self.assertFalse(bundles(self.case)[0]['stale'])
        sent=[]
        async def send(name,payload,caption):
            sent.append(name)
            self.correct()
            return 301
        counts=asyncio.run(self.delivery.tick(send))
        self.assertEqual(len(sent),1);self.assertEqual(counts['sent'],1);self.assertEqual(counts['stale'],3)
        restarted=ReportDelivery(self.delivery.path,self.case.output,self.case.config['case_id'],-123,8,self.since)
        no_send=AsyncMock();asyncio.run(restarted.tick(no_send));no_send.assert_not_awaited()

    def test_supersede_keeps_sent_unknown_receipts_and_broken_new_blocks_old(self):
        self.finalize();self.delivery.discover()
        jobs=list(self.delivery.state['jobs'].values())
        jobs[0].update(status='sent',message_id=111)
        jobs[1].update(status='delivery_unconfirmed',error_type='TimeoutError')
        self.delivery.save();self.correct()
        self.state=current(self.case);self.rendered=bundle(self.case,self.state);self.finalize()
        Path(self.rendered['files'][0]).write_text('broken newer selection')
        send=AsyncMock();counts=asyncio.run(self.delivery.tick(send));send.assert_not_awaited()
        self.assertEqual(counts['sent'],1);self.assertEqual(counts['delivery_unconfirmed'],1)
        self.assertEqual(counts['superseded'],2)
        self.assertEqual(jobs[0]['message_id'],111)


if __name__ == '__main__':unittest.main()
