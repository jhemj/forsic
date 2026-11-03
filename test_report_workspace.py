"""Synthetic end-to-end report tab contracts; no model/server/original evidence."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from forsic_plugin.report_workspace import selected_basis, clean_basis, authoring, workspace
from forsic_plugin.report_driven.host import current
from forsic_plugin.report_driven.views import selected_report_identity
from forsic_plugin.report_driven.narrative import progress_view
from forsic_plugin import investigation_view as view


class ReportWorkspaceTests(unittest.TestCase):
    def setUp(self):
        from test_report_narrative import ReportNarrativeTests
        self.fixture = ReportNarrativeTests(); self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.case = self.fixture.case
        self.fixture.write('major_findings')

    def assert_equivalent(self):
        full = current(self.case); selected = selected_basis(self.case)
        clean = clean_basis(selected)
        for ids in [[q['id'] for q in full['questions']]] + [n['question_ids'] for n in full.get('report_narratives', [])]:
            self.assertEqual(selected_report_identity(full, ids), selected_report_identity(clean, ids))
        self.assertEqual(progress_view(full), progress_view(clean))
        return selected

    def save_bundle(self, state, *, created='2026-01-01T00:00:00Z', final=False, ident=None):
        raw=json.dumps(state).encode(); sid=state['meta']['snapshot_id']
        (self.case.output/('report-state-'+sid+'.json')).write_bytes(raw)
        ident = ident or hashlib.sha256(raw+created.encode()).hexdigest()
        path=self.case.output/('report-bundle-'+ident);path.mkdir()
        (path/'integrated.html').write_text('<h1>Synthetic saved report</h1>')
        ids=state['report_narratives'][-1]['question_ids']
        manifest=dict(case_id=self.case.config['case_id'],bundle_id=ident,snapshot_id=sid,
            state_sha256=hashlib.sha256(raw).hexdigest(),files=[dict(name='integrated.html')],
            question_ids=ids,selection_mode='selected_questions',
            selected_content_revision=selected_report_identity(state,ids),created_at=created,
            layout_review={'status':'not_rendered'},status=state['status'],editorial_status='technical_partial')
        (path/'manifest.json').write_text(json.dumps(manifest))
        if final:
            self.case.record('report_finalized',dict(case_id=self.case.config['case_id'],bundle_id=ident,
                snapshot_id=sid,selection='final_delivery_copy',independent_review={'final_review_current':True}))
        return ident,manifest,path

    def catalog(self):
        basis=selected_basis(self.case); progress,draft=authoring(self.case,basis)
        result={'reports':[]}; counts={}
        view._reports(self.case,self.case.config['case_id'],result,counts,current_basis=basis,draft=draft)
        return result['reports'],progress,workspace(progress,result['reports'])

    def test_large_history_is_not_decoded_and_same_current_judgment_and_prose_survive(self):
        # Replaced prose and historic derived forensic payload are irrelevant;
        # their latest canonical record is still retained exactly.
        state=current(self.case); latest=state['report_narratives'][0]
        old=deepcopy(latest);old['fields']['old_obsolete_body']={'unused':'NEVER_DECODE_OLD_PROSE'*300000}
        self.case.record('report_narrative',old);self.case.record('report_narrative',latest)
        self.case.record('test',{})  # unrelated record must not affect state
        n=deepcopy(self.fixture.note);n['forensic']={'old_large_view':'NEVER_DECODE_OLD_NOTE'*300000}
        with self.case.connect() as db:
            last=json.loads(db.execute("SELECT data FROM events WHERE kind='note' ORDER BY rowid DESC LIMIT 1").fetchone()[0])
        self.case.record('note',n);self.case.record('note',last)
        self.case.record('tool_result',{'tool':'forsic_reporting','body':'NEVER_DECODE_TOOL_OUTPUT'*300000})
        self.case.record('tool_result',{'tool':'forsic_read','path':'unselected.log','body':'NEVER_DECODE_UNSELECTED'*300000})
        expected=current(self.case)
        decoded=[]; original=json.loads
        def loads(raw,*a,**kw):
            decoded.append(raw);return original(raw,*a,**kw)
        before=self.case.db.read_bytes()
        with patch('json.loads',side_effect=loads): selected=selected_basis(self.case)
        self.assertEqual(progress_view(expected),progress_view(clean_basis(selected)))
        self.assertEqual(selected_report_identity(expected,latest['question_ids']), selected_report_identity(clean_basis(selected),latest['question_ids']))
        self.assertLess(selected['_report_read']['bytes'],400000)
        self.assertFalse(any(isinstance(raw,str) and 'NEVER_DECODE_' in raw for raw in decoded))
        self.assertEqual(before,self.case.db.read_bytes())

    def test_selected_identity_survives_answer_edit_and_changed_definition(self):
        from forsic_plugin.notes import note
        self.assert_equivalent()
        q=current(self.case)['questions'][0]
        note(self.case,dict(action='save',note_id=q['id'][2:],revision=q['revision'],question='A different proposition?',
             answer='A bounded changed interpretation',correction_reason='Original target was too broad'))
        self.assert_equivalent()

    def test_draft_stays_readable_when_exact_source_exceeds_existing_budget(self):
        with self.case.connect() as db:
            db.execute("UPDATE events SET data=json_set(data,'$.oversized',?) WHERE id=(SELECT id FROM events WHERE kind='tool_result' LIMIT 1)", ('x'*(4*1024*1024),))
        with self.assertRaises(ValueError): selected_basis(self.case)
        progress,draft=authoring(self.case,None)
        self.assertTrue(draft)
        self.assertEqual(progress['status'],'unavailable')
        self.assertIsNone(progress['current'])
        self.assertTrue(next(g for g in progress['groups'] if g['id']=='major_findings')['preview'])
        self.assertIn('저장된 초안',progress['read_issue'])

    def test_large_selected_results_keep_exact_identity_without_aggregate_body_limit(self):
        from forsic_plugin.report_workspace import MAX_INPUT_BYTES
        # Two legitimate selected results are individually bounded but together
        # exceed the UI working set. No source or citation may be dropped.
        with self.case.connect() as db:
            db.execute("UPDATE events SET data=json_set(data,'$.retained_findings',?) WHERE kind='tool_result'", ('x'*(2300*1024),))
        before = self.case.db.read_bytes()
        selected = self.assert_equivalent()
        self.assertGreater(selected['_report_read']['raw_bytes'], MAX_INPUT_BYTES)
        self.assertLess(selected['_report_read']['bytes'], MAX_INPUT_BYTES)
        full = current(self.case)
        self.assertEqual(full['sources'], selected['sources'])
        self.assertEqual(full['observations'], selected['observations'])
        self.assertEqual(before, self.case.db.read_bytes())
        ids = full['report_narratives'][0]['question_ids']
        identity = selected_report_identity(clean_basis(selected), ids)
        source_id = selected['sources'][0]['id']
        with self.case.connect() as db:
            db.execute("UPDATE events SET data=json_set(data,'$.retained_findings',?) WHERE id=?", ('y'*(2300*1024), source_id))
        changed = self.assert_equivalent()
        self.assertNotEqual(identity, selected_report_identity(clean_basis(changed), ids))
        self.assertNotEqual(selected['meta']['content_revision'], changed['meta']['content_revision'])

    def test_projected_source_keeps_legacy_file_time_and_log_citations(self):
        source_id = self.fixture.reads[0]['evidence_id']
        stamp = '2026-01-02T03:04:05+00:00'
        with self.case.connect() as db:
            db.execute("UPDATE events SET data=json_set(data,'$.times',json(?),'$.lines[0].text',?,'$.large',?) WHERE id=?",
                       (json.dumps({'mtime':1767323045}), stamp+' observed synthetic request', 'x'*600000, source_id))
            row = json.loads(db.execute("SELECT data FROM events WHERE kind='note' ORDER BY rowid DESC LIMIT 1").fetchone()[0])
        row['timeline'] = [
            dict(description='File metadata, not installation time', evidence_ids=[source_id],
                 time_source=dict(evidence_id=source_id, field='mtime')),
            dict(description='Log record time', evidence_ids=[source_id],
                 time_source=dict(evidence_id=source_id, field='log', pointer='/lines/0/text', literal=stamp, byte_start=0))]
        self.case.record('note',row)
        selected = self.assert_equivalent(); full = current(self.case)
        self.assertEqual(len(full['timeline']),2)
        self.assertEqual(full['timeline'],selected['timeline'])
        self.assertEqual(full['observations'],selected['observations'])

    def test_large_receipt_preserves_actual_mission_execution_states(self):
        from test_investigation_state import InvestigationStateTests
        fixture = InvestigationStateTests(); fixture.setUp()
        self.addCleanup(fixture.tearDown)
        saved = fixture.mission()
        rid = fixture.linked_result(saved, data={'status':'queued','job_id':'synthetic-job','bulk':'x'*600000})
        for status,expected in [('queued','running'),('failed','failed'),('completed','returned')]:
            with self.subTest(status=status):
                with fixture.case.connect() as db:
                    db.execute("UPDATE events SET data=json_set(data,'$.status',?) WHERE id=?",(status,rid))
                full=current(fixture.case); selected=selected_basis(fixture.case)
                for field in ('sources','missions','tests','observations'):
                    self.assertEqual(full[field], selected[field])
                self.assertEqual(full['tests'][-1]['execution_state'],expected)

    def test_new_draft_does_not_relabel_old_file_or_old_final_current(self):
        old=current(self.case);ident,_,_=self.save_bundle(old,final=True)
        self.fixture.write('report_identity')
        reports,progress,flow=self.catalog();report=reports[0]
        self.assertEqual(report['id'],ident)
        self.assertEqual(report['draft_relation'],'older')
        self.assertFalse(report['current'])
        self.assertEqual(report['lifecycle'],'final')
        self.assertEqual(report['finalization']['layout_status'],'not_rendered')
        self.assertGreater(progress['draft_revision'],report['draft_revision'])
        self.assertNotEqual(flow['phase'],'final_selected')
        self.assertEqual(flow['final_report_id'],ident)

    def test_selected_final_same_version_and_unknown_layout_are_separate(self):
        self.save_bundle(current(self.case),final=True)
        reports,progress,flow=self.catalog()
        self.assertEqual(reports[0]['draft_relation'],'same')
        self.assertTrue(reports[0]['current'])
        self.assertEqual(flow['phase'],'final_selected')
        self.assertEqual(reports[0]['verification']['layout'],'not_rendered')
        self.assertFalse(progress['ready'])  # selection is not a blanket editorial certificate

    def test_foreign_and_wrong_snapshot_receipts_never_make_a_final(self):
        ident,manifest,_=self.save_bundle(current(self.case))
        for case_id,snapshot in [('other-case',manifest['snapshot_id']),(self.case.config['case_id'],'f'*64)]:
            self.case.record('report_finalized',dict(case_id=case_id,bundle_id=ident,snapshot_id=snapshot,selection='final_delivery_copy'))
        reports,_,flow=self.catalog()
        self.assertEqual(reports[0]['lifecycle'],'saved')
        self.assertIsNone(flow['final_report_id'])

    def test_corrupted_snapshot_not_mislabeled_final_or_current(self):
        ident,manifest,_=self.save_bundle(current(self.case),final=True)
        (self.case.output/('report-state-'+manifest['snapshot_id']+'.json')).write_text('{}')
        reports,_,flow=self.catalog()
        self.assertEqual(reports[0]['lifecycle'],'saved')
        self.assertEqual(reports[0]['comparison_status'],'unavailable')
        self.assertIsNone(flow['final_report_id'])

    def test_final_selection_remains_in_bounded_history(self):
        state=current(self.case);ident,_,_=self.save_bundle(state,final=True)
        self.save_bundle(state,created='2026-01-02T00:00:00Z')
        self.save_bundle(state,created='2026-01-03T00:00:00Z')
        with patch.object(view,'MAX_REPORTS',2): reports,_,flow=self.catalog()
        self.assertEqual(len(reports),2)
        self.assertEqual(flow['final_report_id'],ident)
        self.assertNotEqual(flow['saved_report_id'],ident)

    def test_immutable_summary_reused_and_file_change_invalidates_cache(self):
        from forsic_plugin.report_workspace import saved_summary, _snapshot_summary
        _,manifest,_=self.save_bundle(current(self.case))
        _snapshot_summary.cache_clear()
        first=saved_summary(self.case,manifest)
        second=saved_summary(self.case,manifest)
        self.assertIs(first,second)
        self.assertEqual(_snapshot_summary.cache_info().hits,1)
        path=self.case.output/('report-state-'+manifest['snapshot_id']+'.json')
        path.write_text('{}')
        with self.assertRaises(ValueError): saved_summary(self.case,manifest)

    def test_unrelated_history_count_does_not_exhaust_input_budget(self):
        with self.case.connect() as db:
            db.executemany('INSERT INTO events VALUES (?,?,?,?,?)',((f'noise-{i}',i,'tool_start','','{"tool":"forsic_reporting"}') for i in range(20001)))
        self.assertLess(self.assert_equivalent()['_report_read']['records'],50)
