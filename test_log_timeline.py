"""Synthetic source -> note -> shared HTML/Word timeline; no model or network."""
from copy import deepcopy
import json
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile
from test_report_driven import fixture
from forsic_plugin.notes import note, timeline
from forsic_plugin.report_driven.host import current, checked
from forsic_plugin.report_driven.views import bundle


class LogTimelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.case, _, _ = fixture(self.temp.name)
        self.texts = ['접속 2026-10-01T23:59:00Z accepted',
                      '2026-10-02T09:01:00+09:00 transferred',
                      'Oct  2 09:02:00 attempt', '2026-10-02 09:03:00 local']
        (self.case.root / 'times.log').write_text('\n'.join(self.texts))
        self.eid = json.loads(self.case.invoke('forsic_read', {'path':'times.log'}))['evidence_id']

    def tearDown(self):
        self.temp.cleanup()

    def row(self, i, literal):
        return dict(description='로그 관측 '+str(i), evidence_ids=[self.eid],
                    time_source=dict(field='log', evidence_id=self.eid,
                                     pointer=f'/lines/{i}/text', literal=literal,
                                     byte_start=self.texts[i].encode().index(literal.encode())))

    def save(self, rows, **kwargs):
        return note(self.case, dict(action='save', question='로그에 언제 기록됐나?',
                    answer='원문 시각을 확인했습니다.', evidence_ids=[self.eid], timeline=rows, **kwargs))['note']

    def test_both_reports_share_originals_order_and_unknown_time(self):
        rows=[self.row(1,'2026-10-02T09:01:00+09:00'), self.row(0,'2026-10-01T23:59:00Z'),
              self.row(2,'Oct  2 09:02:00'), self.row(3,'2026-10-02 09:03:00')]
        saved=self.save(rows)
        self.assertEqual(saved['timeline'][1]['display_time'], '2026-10-02 08:59:00 KST')
        state=current(self.case)
        checked(state)
        self.assertEqual(len(state['timeline']),4)
        self.assertEqual([t['comparable'] for t in state['timeline']], [True,True,False,False])
        self.assertEqual(state['timeline'][0]['raw_values'],['2026-10-01T23:59:00Z'])
        self.assertEqual(state['timeline'][-1]['normalized_values'],[])
        result=bundle(self.case,state)
        for name in result['files']:
            path=Path(name)
            if path.suffix=='.html': text=path.read_text()
            else:
                with ZipFile(path) as doc: text=doc.read('word/document.xml').decode()
            self.assertIn('2026-10-02T08:59:00+09:00',text)
            self.assertIn('Oct  2 09:02:00',text)

    def test_false_literal_or_character_offset_does_not_save(self):
        row=self.row(0,'2026-10-01T23:59:00Z')
        for change in ({'literal':'2026-10-02T23:59:00Z'}, {'byte_start':3}, {'pointer':'/path'}, {'byte_start':True}):
            bad=deepcopy(row);bad['time_source'].update(change)
            with self.assertRaises(ValueError): self.save([bad])

    def test_duplicate_and_correction(self):
        row=self.row(0,'2026-10-01T23:59:00Z')
        self.assertEqual(len(timeline([row,row],self.case)),1)
        saved=self.save([row]); first=current(self.case)['meta']['snapshot_id']
        self.save([self.row(1,'2026-10-02T09:01:00+09:00')],note_id=saved['note_id'],revision=saved['revision'],correction_reason='로그 선택 정정')
        state=current(self.case)
        self.assertNotEqual(first,state['meta']['snapshot_id'])
        self.assertEqual(len(state['timeline']),1)
        self.assertEqual(state['timeline'][0]['raw_values'],['2026-10-02T09:01:00+09:00'])

    def test_failed_source_and_unbound_legacy_not_promoted(self):
        bad=self.case.record('tool_result',dict(tool='forsic_read',error='unavailable',lines=[{'text':self.texts[0]}]))
        row=self.row(0,'2026-10-01T23:59:00Z');row['time_source']['evidence_id']=bad;row['evidence_ids']=[bad]
        with self.assertRaises(ValueError): self.save([row])
        self.save([dict(time='2026-10-01T23:59:00Z',description='legacy',evidence_ids=[self.eid])])
        self.assertEqual(current(self.case)['timeline'],[])

    def test_same_timestamp_distinct_source_positions_survive(self):
        eid=self.case.record('tool_result',dict(tool='forsic_read',lines=[{'text':'2026-10-02T09:00:00Z first'},{'text':'2026-10-02T09:00:00Z second'}]))
        rows=[dict(description='동일 시각 기록',evidence_ids=[eid],time_source=dict(evidence_id=eid,field='log',pointer=f'/lines/{i}/text',literal='2026-10-02T09:00:00Z',byte_start=0)) for i in (0,1)]
        self.assertEqual(len(timeline(rows+rows,self.case)),2)

    def test_out_of_range_kst_preserves_raw(self):
        eid=self.case.record('tool_result',dict(tool='forsic_read',text='9999-12-31T23:59:59-12:00'))
        row=dict(description='경계 시각',evidence_ids=[eid],time_source=dict(evidence_id=eid,field='log',pointer='/text',literal='9999-12-31T23:59:59-12:00',byte_start=0))
        value=timeline([row],self.case)[0]
        self.assertEqual(value['time'],'9999-12-31T23:59:59-12:00')
        self.assertFalse(value['comparable'])


if __name__=='__main__': unittest.main()
