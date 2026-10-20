"""Synthetic read-boundary measurements; no services, models or original evidence."""
from contextlib import contextmanager
from copy import deepcopy
import json
import hashlib
from pathlib import Path
import sqlite3
import tempfile
import time
import tracemalloc
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from forsic_plugin.notes import model_read, model_view, note
from forsic_plugin.report_driven import host
from forsic_plugin import independent_review as review
from forsic_plugin.investigation_context import resolve_context
from test_report_driven import fixture
import test_investigation_context as context_fixtures


class ReadEfficiencyTests(unittest.TestCase):
    def measure(self, operation):
        decode = json.loads
        sizes = []
        def counted(value, *args, **kwargs):
            sizes.append(len(value.encode() if isinstance(value,str) else value))
            return decode(value,*args,**kwargs)
        tracemalloc.start()
        started = time.perf_counter()
        try:
            with patch('json.loads', side_effect=counted):
                result = operation()
            elapsed = time.perf_counter()-started
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        return result, dict(decoded_rows=len(sizes),decoded_bytes=sum(sizes),peak_python_bytes=peak,seconds=elapsed)

    def test_ten_thousand_note_revisions_decode_only_current_and_explicit_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'ledger.sqlite3'
            @contextmanager
            def connect():
                db=sqlite3.connect(path);db.row_factory=sqlite3.Row
                try:yield db
                finally:db.close()
            case=SimpleNamespace(connect=connect)
            with connect() as db:
                db.execute('CREATE TABLE events(kind TEXT,data TEXT)')
                db.executemany('INSERT INTO events VALUES (?,?)',(('note',json.dumps(dict(
                    note_id='synthetic',revision=index,answer='retained answer '+str(index)+' x'*512,
                    question='Synthetic scope',gaps=['Unresolved scope']))) for index in range(10000)))
                db.commit()
            full, baseline=self.measure(lambda:note(case,{'action':'get','note_id':'synthetic'}))
            current, bounded=self.measure(lambda:model_read(case,{'action':'get','note_id':'synthetic'}))
            self.assertEqual(current['note'],full['note'])
            self.assertEqual(current['history_total'],10000)
            self.assertNotIn('history',current)
            self.assertEqual(baseline['decoded_rows'],10000)
            self.assertEqual(bounded['decoded_rows'],1)
            self.assertLess(bounded['peak_python_bytes'],baseline['peak_python_bytes']/5)
            for offset in (0,1,9998,10001):
                args=dict(action='get',note_id='synthetic',include_history=True,offset=offset,limit=3)
                page,measurement=self.measure(lambda:model_read(case,args))
                self.assertEqual(model_view(page,args),model_view(full,args))
                self.assertLessEqual(measurement['decoded_rows'],4)
            with self.assertRaisesRegex(ValueError,'Unknown note_id'):
                model_read(case,{'action':'get','note_id':'absent'})
            print('Synthetic 10000-note reads:',json.dumps(dict(full=baseline,bounded=bounded),sort_keys=True))

    def test_ignored_control_body_decoding_is_constant_without_changing_types_or_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'ledger.sqlite3'
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE events(id TEXT,time REAL,kind TEXT,session TEXT,data TEXT)')
                rows=[('control',1,'pre_api_request','synthetic',json.dumps({'unused':'x'*2000000})),
                      ('future',2,'unknown_event','synthetic',json.dumps({'keep':'unrecognized body'}))]
                rows.extend((str(index),3,'pre_api_request','synthetic',json.dumps(value)) for index,value in enumerate([[],None,7,'text',True]))
                db.executemany('INSERT INTO events VALUES (?,?,?,?,?)',rows);db.commit()
            case=SimpleNamespace(db=path)
            before=path.read_bytes()
            full,baseline=self.measure(lambda:host.ledger(case))
            projected,bounded=self.measure(lambda:host.ledger(case,metadata_only_kinds={'pre_api_request'}))
            expected=deepcopy(full);expected[0]['data']={}
            self.assertEqual(projected,expected)
            self.assertLess(bounded['decoded_bytes'],1000)
            self.assertGreater(baseline['decoded_bytes'],2000000)
            self.assertLess(bounded['peak_python_bytes'],baseline['peak_python_bytes']/5)
            self.assertEqual(path.read_bytes(),before)
            with sqlite3.connect(path) as db:
                db.execute('INSERT INTO events VALUES (?,?,?,?,?)',('invalid',4,'pre_api_request','','{broken'));db.commit()
            with self.assertRaises(json.JSONDecodeError):
                host.ledger(case,metadata_only_kinds={'pre_api_request'})
            print('Synthetic control-body reads:',json.dumps(dict(full=baseline,bounded=bounded),sort_keys=True))

    def test_review_material_keeps_frozen_cutoff_and_exact_sources_without_full_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            case,sources,saved=fixture(tmp)
            with case.connect() as db:
                db.executemany('INSERT INTO events VALUES (?,?,?,?,?)',((f'note-history-{index}',1,'note','',json.dumps(
                    {**saved,'revision':index+2,'answer':'Historical selected answer '+str(index)+' x'*512})) for index in range(1000)))
            state=host.current(case)
            selected=state['questions'][0]['id']
            baseline=host.review_material(case,state,[selected])
            later={**saved,'revision':saved['revision']+1,'answer':'Later corrected answer'}
            case.record('note',later)
            case.record('post_api_request',{'irrelevant':'x'*2000000})
            with patch.object(host,'ledger',side_effect=AssertionError('Review must not reload complete ledger')):
                material,measurement=self.measure(lambda:host.review_material(case,state,[selected]))
            self.assertEqual(material,baseline)
            self.assertEqual(material['question_definition_versions'][selected],1001)
            self.assertLess(measurement['decoded_bytes'],100000)
            self.assertEqual(material['original_results'],baseline['original_results'])
            print('Synthetic selected-review reads:',json.dumps(measurement,sort_keys=True))


class FreshnessEfficiencyTests(unittest.TestCase):
    setUp=context_fixtures.InvestigationContextTests.setUp
    session=context_fixtures.InvestigationContextTests.session
    bind=context_fixtures.InvestigationContextTests.bind
    save_note=context_fixtures.InvestigationContextTests.save_note

    def resolved(self):
        return resolve_context(self.sid,intake_root=self.intake,native_db=self.native)

    def same_fingerprints(self):
        resolved=self.resolved()
        # Compute the contract independently of the fingerprint helper: exact
        # source bytes and substantive question state, excluding write bookkeeping.
        digest=hashlib.sha256();selected_digest=hashlib.sha256()
        lineage=resolved['session_lineage']
        with self.case.connect() as db:
            query="SELECT id,data FROM events WHERE kind='tool_result' AND session IN ("+','.join('?' for _ in lineage)+") AND json_extract(data,'$.tool') IN ("+','.join('?' for _ in review.EVIDENCE_TOOLS)+") AND json_extract(data,'$.error') IS NULL AND coalesce(json_extract(data,'$.exit_code'),0)=0 AND json_extract(data,'$.reused_from') IS NULL ORDER BY rowid"
            for row in db.execute(query,(*lineage,*sorted(review.EVIDENCE_TOOLS))):
                digest.update(row['id'].encode());digest.update(row['data'].encode())
        questions=review._question_records(resolved)
        selected=sorted({sid for question in questions for sid in [*question.get('evidence_ids',[]),
            *question.get('temporal_summary',{}).get('selected_source_ids',[])]})
        with self.case.connect() as db:
            for sid in selected[:32]:
                selected_digest.update(sid.encode())
                row=db.execute("SELECT data FROM events WHERE id=? AND kind='tool_result'",(sid,)).fetchone()
                if row:selected_digest.update(row[0].encode())
        from forsic_plugin.observation_scope import observed_scope
        expected={'fingerprint':review._hash({'sources_sha256':digest.hexdigest(),
            'question':self.case.config.get('question',''),'scope':self.case.config.get('scope',''),
            'goal':resolved['goal'].goal,'observation_scope':observed_scope(resolved,max_targets=40,max_chars=3600),
            'selected_sources_sha256':selected_digest.hexdigest(),'selected_sources_omitted':max(0,len(selected)-32)}),
            'evaluation_fingerprint':review._hash([{key:value for key,value in question.items()
                if key not in ('revision','review_responses')} for question in questions])}
        for stage in ('checkpoint','final'):
            snapshot=review._collect(resolved,stage=stage)
            fresh=review.freshness(resolved)
            self.assertEqual(fresh,{key:snapshot[key] for key in ('fingerprint','evaluation_fingerprint')})
            self.assertEqual(fresh,expected)
        return fresh

    def test_fingerprint_only_matches_payload_across_edits_sources_and_goal_boundaries(self):
        before=self.same_fingerprints()
        self.save_note(review_responses=[{'review_id':'synthetic-review','finding_id':'synthetic-finding',
            'disposition':'scope_limited','reason':'The acquired scope lacks execution records.',
            'limitation':'Execution is not established.','citation_refs':[],'citations':[]}])
        response_only=self.same_fingerprints()
        self.assertEqual(before,response_only)
        self.save_note()  # A new revision with no substantive change stays current.
        self.assertEqual(response_only,self.same_fingerprints())
        self.save_note(answer='Corrected synthetic answer',priority='decision_critical')
        changed=self.same_fingerprints()
        self.assertNotEqual(before['evaluation_fingerprint'],changed['evaluation_fingerprint'])
        source=self.case.record('tool_result',{'tool':'forsic_read','path':'extra.log',
            'lines':[{'line':1,'text':'request only'}],'complete_file':False},self.sid)
        appended=self.same_fingerprints()
        self.assertNotEqual(changed['fingerprint'],appended['fingerprint'])
        with self.case.connect() as db:
            row=db.execute('SELECT data FROM events WHERE id=?',(source,)).fetchone()
            data=json.loads(row[0]);data['lines'][0]['text']='changed retained bytes'
            db.execute('UPDATE events SET data=? WHERE id=?',(json.dumps(data),source))
        modified=self.same_fingerprints()
        self.assertNotEqual(appended['fingerprint'],modified['fingerprint'])
        self.case.record('tool_result',{'tool':'forsic_read','path':'foreign','lines':[{'line':1,'text':'foreign'}]},'different-session')
        self.assertEqual(modified,self.same_fingerprints())
        with patch.object(review,'_collect',side_effect=AssertionError('Freshness must not assemble payload')), \
             patch.object(review,'_field_handle',side_effect=AssertionError('Freshness must not assemble source excerpts')):
            self.assertEqual(review.freshness(self.resolved()),modified)

    def test_owned_question_read_decodes_only_latest_revision(self):
        resolved=self.resolved()
        before=review._question_records(resolved)
        with self.case.connect() as db:
            raw=db.execute("SELECT data FROM events WHERE kind='note' ORDER BY rowid DESC LIMIT 1").fetchone()[0]
            value=json.loads(raw)
            db.executemany('INSERT INTO events VALUES (?,?,?,?,?)',((f'large-note-{i}',1,'note',self.sid,
                json.dumps({**value,'revision':i+100,'answer':'synthetic history '+str(i)})) for i in range(1000)))
        decoder=json.loads
        with patch('forsic_plugin.independent_review.json.loads',wraps=decoder) as decode:
            questions=review._question_records(resolved)
        self.assertEqual(decode.call_count,len(questions))
        self.assertEqual(questions[0]['answer'],'synthetic history 999')
        self.assertEqual(questions[0]['id'],before[0]['id'])
        expected=deepcopy(before);expected[0].update(answer='synthetic history 999',revision=1099)
        self.assertEqual(questions,expected)

    def test_freshness_measurement_keeps_exact_identity_without_retaining_a_payload(self):
        for index in range(48):
            self.case.record('tool_result',dict(tool='forsic_read',path=f'fixture-{index}.log',
                complete_file=False,lines=[dict(line=1,text='retained synthetic observation '*1000)]),self.sid)
        resolved=self.resolved();before=self.case.db.read_bytes()
        measure=ReadEfficiencyTests().measure
        payload,baseline=measure(lambda:review._collect(resolved))
        identity,bounded=measure(lambda:review.freshness(resolved))
        self.assertEqual(identity,{key:payload[key] for key in ('fingerprint','evaluation_fingerprint')})
        self.assertNotIn('prompt',identity)
        self.assertEqual(self.case.db.read_bytes(),before)
        print('Synthetic review freshness:',json.dumps(dict(payload=baseline,fingerprint_only=bounded),sort_keys=True))


if __name__=='__main__':unittest.main()
