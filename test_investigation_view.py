"""Offline UI projection contracts; synthetic retained SQL only, no model or server."""
from contextlib import closing
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from forsic_plugin.evidence import Case
from forsic_plugin import investigation_view as view
from forsic_plugin.dashboard import plugin_api
from forsic_plugin.report_driven.host import content_version, source_record
from forsic_plugin.report_driven.investigation_state import definition_version


class InvestigationViewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.intake = self.root / 'intake'; case_dir = self.intake / 'cases' / 'synthetic'
        case_dir.mkdir(parents=True); evidence = case_dir / 'evidence'; evidence.mkdir()
        manifest = case_dir / 'case.json'
        manifest.write_text(json.dumps(dict(case_id='synthetic', evidence_root=str(evidence),
            output_root=str(case_dir / 'results'), scope='synthetic scope')))
        writable = Case(manifest)
        self.source_id = writable.record('tool_result', dict(tool='forsic_read', path='synthetic.log',
            lines=[dict(line=1,text='Oct 1 03:00 synthetic activity')]), 'session')
        self.source = source_record(writable.event(self.source_id))
        self.note = dict(note_id='n', revision=1, session_id='session', goal_id='goal',
            question='Synthetic question?', answer='A retained provisional interpretation',
            evidence_ids=[self.source_id], investigation=dict(assessment='undetermined',work_state='active'), timeline=[])
        self.note['definition_version'] = definition_version(self.note,'synthetic scope')
        writable.record('note',self.note)
        self.case = Case(manifest,read_only=True)
        self.resolved = dict(case=self.case,case_id='synthetic', requested_session_id='session',
            session_lineage=['session'],goal_id='goal')

    def append(self,kind,value):
        return Case(self.case.manifest).record(kind,value)

    def project(self):
        return view.project_view(self.resolved,profile='default')

    def observation(self,ident='O-current'):
        return dict(id=ident,version='saved-version',source_ref=dict(kind='source',id=self.source_id,version=self.source['version']),
            statement='Recorded string',field_pointer='/lines/0/text',literal='Oct 1 03:00',byte_start=0,byte_end=11,
            interpretation_limit='Recorded clock; year and timezone unknown')

    def changes(self,records,revision='1'):
        return dict(question_ref=dict(kind='question',id='Q-n',version=revision),
            definition_versions={'Q-n':self.note['definition_version']},records=records)

    def review_receipt(self,*,goal='goal',conversation='session',session='session',review_id='synthetic-review',status='received'):
        from forsic_plugin import independent_review as review
        finding=dict(source_id=self.source_id,pointer='/lines/0/text',quote='synthetic activity',
            question='Was the retained activity investigated?',why='The retained line records activity.',
            next_check='Inspect the bounded activity.')
        presented={self.source_id:{'/lines/0/text':'Oct 1 03:00 synthetic activity'}}
        advice=json.dumps(dict(findings=[finding]))
        request=dict(schema_version=2,request_id=review_id,session_id=session,
            identity=dict(case_id='synthetic',case_manifest=str(self.case.manifest),
                goal_id=goal,conversation_id=conversation),messages=[dict(role='user',content=json.dumps(dict(
                    sources=[dict(source_id=self.source_id,fields=[dict(pointer='/lines/0/text',
                        value='Oct 1 03:00 synthetic activity')])])))] )
        path=self.case.output/('independent-review-'+review_id+'-request.json')
        path.write_text(json.dumps(request))
        path.with_name(path.name.replace('-request.json','-response.json')).write_text(json.dumps(dict(
            request_id=review_id,finish_reason='stop',status=status,raw_advice=advice)))
        return review._parse(advice,presented,review_id)[0]

    def saved_review_response(self,finding,disposition='investigated',session='session'):
        from forsic_plugin.report_driven.grounded_judgment import read_refs
        from forsic_plugin.report_driven.review_responses import _citations
        writable=Case(self.case.manifest)
        start=writable.record('tool_start',dict(tool='forsic_read',arguments=dict(path='followup.log')),session)
        data=dict(tool='forsic_read',started_id=start,path='followup.log',
            lines=[dict(line=1,text='Retained bounded followup observation.')])
        source=writable.record('tool_result',data,session)
        refs=[read_refs(self.case,dict(data,evidence_id=source))['presented_spans'][0]['citation_ref']]
        answer=dict(review_id=finding['review_id'],finding_id=finding['finding_id'],
            disposition=disposition,reason='Checked this exact question against the retained followup.',
            citation_refs=refs,citations=_citations(self.case,dict(sources=[source_record(self.case.event(source))]),
                refs,lineage=[session]),
            question_definition_version=self.note['definition_version'])
        if disposition=='scope_limited':
            answer.update(limitation='The retained result only covers the observed configuration.',citation_refs=[],citations=[])
        return answer

    def test_review_responses_project_only_validated_canonical_saved_answers_readonly(self):
        finding=self.review_receipt()
        for revision,disposition in enumerate(('investigated','refuted','scope_limited'),2):
            with self.subTest(disposition=disposition):
                answer=self.saved_review_response(finding,disposition)
                note=deepcopy(self.note);note['revision']=revision
                note['investigation']['review_responses']=[answer];self.append('note',note)
                before=self.case.db.read_bytes()
                with patch.object(Case,'record',side_effect=AssertionError('read-only')):
                    projected=self.project()['review_questions'][0]
                self.assertEqual(self.case.db.read_bytes(),before)
                self.assertEqual(projected['response'],dict(question_id='Q-n',revision=revision,
                    disposition=disposition,reason=answer['reason'],limitation=answer.get('limitation'),
                    citation_refs=answer['citation_refs']))
                self.assertEqual(projected['disposition_validation'],'validated_saved_response')
                self.assertFalse(projected['semantic_approval'])
                self.assertTrue(projected['current_goal'])
                self.assertEqual(projected['currentness'],'not_revalidated')

    def test_review_receipt_without_saved_response_is_unanswered(self):
        self.review_receipt()
        projected=self.project()['review_questions'][0]
        self.assertNotIn('response',projected)
        self.assertEqual(projected['disposition'],'not_recorded')
        self.assertEqual(projected['disposition_validation'],'unverified_or_not_recorded')

    def test_review_response_rejects_changed_scope_goal_conversation_identity_and_citations(self):
        finding=self.review_receipt()
        answer=self.saved_review_response(finding)
        valid=deepcopy(self.note);valid['investigation']['review_responses']=[answer]
        cases=[]
        changed=deepcopy(valid);changed['scope']='Changed scope';cases.append(changed)
        changed=deepcopy(valid);changed['goal_id']='different-goal';cases.append(changed)
        changed=deepcopy(valid);changed['session_id']='other-conversation';cases.append(changed)
        for field in ('review_id','finding_id'):
            changed=deepcopy(valid);changed['investigation']['review_responses'][0][field]='mismatch';cases.append(changed)
        changed=deepcopy(valid);changed['investigation']['review_responses'][0]['citation_refs']=['C2:invalid'];cases.append(changed)
        for legacy in ('accepted','rejected','deferred'):
            changed=deepcopy(valid);changed['investigation']['review_responses'][0]['disposition']=legacy;cases.append(changed)
        for revision,note in enumerate(cases,2):
            with self.subTest(note=note):
                note['revision']=revision;self.append('note',note)
                projected=self.project()['review_questions'][0]
                self.assertNotIn('response',projected)
                self.assertEqual(projected['disposition'],'not_recorded')
        self.append('note',valid)
        # A matching answer cannot attach to a receipt from another conversation.
        self.review_receipt(conversation='other-conversation')
        foreign=self.project()['review_questions'][0]
        self.assertNotIn('response',foreign)

    def test_historical_review_response_stays_historical_and_old_note_answer_is_not_resurrected(self):
        finding=self.review_receipt(status='stale')
        answer=self.saved_review_response(finding)
        note=deepcopy(self.note);note['investigation']['review_responses']=[answer];self.append('note',note)
        self.resolved['goal_id']='new-goal'
        projected=self.project()['review_questions'][0]
        self.assertIn('response',projected)
        self.assertFalse(projected['current_goal'])
        self.assertEqual(projected['currentness'],'not_revalidated')
        self.append('note',dict(self.note,revision=2))
        projected=self.project()['review_questions'][0]
        self.assertNotIn('response',projected)
        self.assertFalse(projected['current_goal'])

    def test_case_shared_review_answers_use_their_own_compression_lineage(self):
        _,native=self.native_profile()
        with closing(sqlite3.connect(native)) as db,db:
            db.execute("INSERT INTO sessions VALUES ('other-root',NULL,'compression','tui','{}','default')")
            db.execute("INSERT INTO sessions VALUES ('other-child','other-root',NULL,'tui','{}','default')")
            db.execute("INSERT INTO sessions VALUES ('other-branch','other-root',NULL,'tui',?, 'default')",
                (json.dumps(dict(_branched_from='other-root')),))
        self.resolved['_view_native_db']=native
        finding=self.review_receipt(goal='other-goal',conversation='other-root',session='other-root')
        answer=self.saved_review_response(finding,session='other-root')
        other=deepcopy(self.note);other.update(note_id='other-note',session_id='other-child',goal_id='other-goal')
        other['investigation']['review_responses']=[answer];self.append('note',other)
        # Matching ids and goal from a separate native conversation cannot replace it.
        for note_id,session in [('wrong-conversation','session'),('branch','other-branch')]:
            wrong=deepcopy(other);wrong.update(note_id=note_id,session_id=session)
            wrong['investigation']['review_responses'][0].update(disposition='scope_limited',
                reason='This belongs to a different conversation.',limitation='Separate conversation scope.',
                citation_refs=[],citations=[])
            self.append('note',wrong)
        before=self.case.db.read_bytes()
        with patch.object(Case,'record',side_effect=AssertionError('read-only')), \
             patch.object(Case,'__init__',side_effect=AssertionError('no other case may be opened')):
            projected=self.project()['review_questions'][0]
        self.assertEqual(self.case.db.read_bytes(),before)
        self.assertEqual(projected['response']['question_id'],'Q-other-note')
        self.assertEqual(projected['response']['reason'],answer['reason'])
        self.assertFalse(projected['current_goal'])
        self.assertEqual(projected['session_id'],'other-root')

    def test_duplicate_review_answers_select_newest_current_note_by_record_order(self):
        finding=self.review_receipt()
        answer=self.saved_review_response(finding,'scope_limited')
        older=deepcopy(self.note);older['revision']=100
        older['investigation']['review_responses']=[answer];self.append('note',older)
        newer=deepcopy(older);newer.update(note_id='newest-note',revision=2)
        newer['investigation']['review_responses'][0]['reason']='Newest retained response.'
        self.append('note',newer)
        projected=self.project()['review_questions'][0]['response']
        self.assertEqual(projected['question_id'],'Q-newest-note')
        self.assertEqual(projected['revision'],2)
        self.assertEqual(projected['reason'],'Newest retained response.')

    def test_latest_owned_notes_and_exact_selected_sources_are_readonly(self):
        before = self.case.db.read_bytes()
        unrelated = dict(self.note,note_id='sibling',session_id='other',goal_id='other-goal',answer='Same case retained judgment')
        self.append('note',unrelated)
        # Unrelated payload bodies must not be decoded by the UI projection.
        self.append('post_api_request',dict(large='unrelated body' * 300000))
        before = self.case.db.read_bytes()
        with patch.object(Case,'event',side_effect=AssertionError('no broad source access')), \
             patch.object(Case,'record',side_effect=AssertionError('read-only')), \
             patch.object(Case,'path',side_effect=AssertionError('no original access')):
            result = self.project()
        self.assertEqual(self.case.db.read_bytes(),before)
        self.assertEqual(result['counts']['questions'],2)
        self.assertEqual([q['id'] for q in result['questions']],['Q-sibling','Q-n'])
        self.assertEqual(result['scope'],'case')
        self.assertEqual(result['questions'][0]['session_id'],'other')
        self.assertEqual(result['questions'][0]['evidence_refs'][0]['version'],self.source['version'])
        self.assertEqual(result['sources'][0]['version'],self.source['version'])
        self.assertNotIn('unrelated body',json.dumps(result))
        with closing(view._connection(self.case)) as db:
            with self.assertRaises(sqlite3.OperationalError):
                db.execute("DELETE FROM events")

    def test_unrecorded_assessment_is_distinct_from_explicit_undetermined(self):
        self.assertEqual(self.project()['questions'][0]['assessment'],'undetermined')
        for revision, investigation in [(2,dict(work_state='active')),(3,None)]:
            with self.subTest(investigation=investigation):
                note=deepcopy(self.note);note['revision']=revision
                if investigation is None:
                    note.pop('investigation')
                else:
                    note['investigation']=investigation
                self.append('note',note)
                before=self.case.db.read_bytes()
                projected=self.project()['questions'][0]
                self.assertEqual(projected['assessment'],'not_recorded')
                self.assertEqual(projected['answer'],self.note['answer'])
                self.assertEqual(self.case.db.read_bytes(),before)
        self.append('note',dict(self.note,revision=4))
        self.assertEqual(self.project()['questions'][0]['assessment'],'undetermined')

    def test_latest_ownership_change_never_resurrects_earlier_owned_judgment(self):
        self.append('note',dict(self.note,revision=2,session_id='other',goal_id='other-goal',answer='Current answer'))
        current=self.project()['questions']
        self.assertEqual(len(current),1)
        self.assertEqual((current[0]['version'],current[0]['session_id'],current[0]['answer']),('2','other','Current answer'))
        self.resolved['goal_id']='other-goal'
        self.assertEqual(self.project()['questions'],current)

    def test_only_current_selected_observations_and_content_versions_are_exposed(self):
        current = self.observation(); historical=self.observation('O-historical')
        claim = dict(id='F-current',version='saved',status='adopted',
            source_refs=[dict(kind='observation',id=current['id'],version='saved-version')],counterevidence_refs=[])
        self.append('report_change',self.changes(dict(observations=[historical,current],claims=[claim])))
        note=deepcopy(self.note);note['investigation']['claim_refs']=[dict(kind='claim',id='F-current',version=content_version(claim))]
        self.append('note',note)
        result=self.project()
        self.assertEqual(result['counts']['observations'],1)
        self.assertEqual([o['id'] for o in result['observations']],['O-current'])
        self.assertEqual(result['observations'][0]['version'],content_version(current))
        self.assertEqual(result['observations'][0]['session_id'],'session')
        canonical={k:v for k,v in result['observations'][0].items()
                   if k!='session_id' and not k.startswith('display_')}
        self.assertEqual(result['observations'][0]['version'],content_version(canonical))
        self.assertEqual(result['observations'][0]['source_ref']['version'],self.source['version'])
        claim['status']='retracted';self.append('report_change',self.changes(dict(claims=[claim])))
        self.assertEqual(self.project()['observations'],[])

    def test_later_same_id_observation_cannot_replace_selected_version(self):
        original=self.observation()
        claim=dict(id='F-current',version='selected',status='adopted',
            source_refs=[dict(kind='observation',id=original['id'],version=content_version(original))])
        self.append('report_change',self.changes(dict(observations=[original],claims=[claim])))
        note=deepcopy(self.note);note['investigation']['claim_refs']=[dict(id='F-current',version=content_version(claim))]
        self.append('note',note)
        newer=deepcopy(original);newer.update(literal='a later unselected string',version='new-version')
        self.append('report_change',self.changes(dict(observations=[newer])))
        result=self.project()
        self.assertEqual(result['observations'][0]['literal'],original['literal'])
        self.assertEqual(result['observations'][0]['version'],content_version(original))
        replacement=deepcopy(claim);replacement['version']='unselected';replacement['source_refs']=[]
        self.append('report_change',self.changes(dict(claims=[replacement])))
        self.assertEqual(self.project()['observations'],[])
        self.assertTrue(self.project()['partial'])
        self.assertEqual(self.project()['omitted_counts']['unavailable_references'],1)

    def test_stale_source_version_is_omitted_and_counted(self):
        ob=self.observation();ob['source_ref']['version']='0'*64
        claim=dict(id='F-current',status='adopted',source_refs=[dict(kind='observation',id=ob['id'],version=ob['version'])])
        self.append('report_change',self.changes(dict(observations=[ob],claims=[claim])))
        note=deepcopy(self.note);note['investigation']['claim_refs']=[dict(id='F-current',version=content_version(claim))]
        self.append('note',note)
        result=self.project()
        self.assertEqual(result['observations'],[])
        self.assertEqual(result['omitted_counts']['observations'],1)
        self.assertTrue(result['partial'])

    def test_counts_precede_question_source_and_byte_bounds(self):
        for i in range(5):
            self.append('note',dict(self.note,note_id=str(i),question='long question '*80))
        with patch.object(view,'MAX_QUESTIONS',2),patch.object(view,'MAX_SOURCE_BYTES',1):
            result=self.project()
        self.assertEqual(result['counts']['questions'],6)
        self.assertEqual(result['omitted_counts']['questions'],4)
        self.assertEqual(result['counts']['sources'],1)
        self.assertEqual(result['omitted_counts']['sources'],1)
        with patch.object(view,'MAX_VIEW_BYTES',100):
            bounded=self.project()
        self.assertEqual(bounded['counts']['questions'],6)
        self.assertGreater(bounded['omitted_counts']['questions'],0)

    def time_row(self,*,inferred=False,field='log'):
        basis=dict(evidence_id=self.source_id,pointer='/lines/0/text',literal='Oct 1 03:00',byte_start=0,
            source_version=self.source['version'],source_role='retained_tool_result')
        return dict(event_ref='TE-clock',description='Synthetic clock interpretation',evidence_ids=[self.source_id],
            time_source=dict(field=field,evidence_id=self.source_id),raw_time='Oct 1 03:00',time='Oct 1 03:00',comparable=False,
            literal_timestamp_ref=basis,temporal=dict(observed_or_inferred='inferred' if inferred else 'observed',
                timestamp_role='event_occurrence' if inferred else 'source_timestamp',clock_domain='unknown original clock',
                earliest='2020-10-01T03:00:00+09:00' if inferred else None,latest=None,
                assumptions=['Year is only a candidate'] if inferred else [],confidence_basis='Exact retained literal only',
                basis_refs=[basis],candidates=[],relations=[],limitations=['Year/timezone unknown']))

    def test_unknown_and_inferred_times_preserve_qualifiers_and_filesystem_meaning(self):
        note=deepcopy(self.note);note['timeline']=[self.time_row(),dict(self.time_row(inferred=True,field='ctime'),event_ref='TE-inferred')]
        self.append('note',note)
        result=self.project()
        self.assertEqual(result['counts']['timeline'],2)
        self.assertEqual(result['counts']['observations'],1)  # shared exact basis deduplicated
        unknown,inferred=result['timeline']
        self.assertEqual(unknown['normalized_values'],[]);self.assertFalse(unknown['comparable'])
        self.assertEqual(inferred['shape'],'interval');self.assertFalse(inferred['comparable'])
        self.assertEqual(inferred['file_time_type'],'ctime')
        self.assertIn('ctime',inferred['limitation'])
        self.assertEqual(inferred['temporal_context']['earliest'],'2020-10-01T03:00:00+09:00')
        self.assertEqual(inferred['temporal']['assumptions'],['Year is only a candidate'])
        ref=unknown['observation_ref'];ob=result['observations'][0]
        self.assertEqual(ref,dict(kind='observation',id=ob['id'],version=ob['version']))
        with patch.object(view,'MAX_TIMES',0):
            hidden=self.project()
        self.assertEqual(hidden['counts']['timeline'],2)
        self.assertEqual(hidden['omitted_counts']['timeline'],2)
        self.assertEqual(hidden['counts']['observations'],1)

    def test_temporal_relation_target_change_or_missing_event_requires_recheck(self):
        target=dict(self.note,note_id='target',timeline=[dict(self.time_row(),event_ref='TE-target')])
        self.append('note',target)
        note=deepcopy(self.note);row=self.time_row();row.update(time='2020-10-01T03:00:00+09:00',comparable=True)
        row['temporal']['relations']=[dict(target_note_id='target',target_note_revision=1,
            target_event_ref='TE-target',relation_ref='TR-synthetic',relation='before',basis_refs=[],reason='Synthetic relation')]
        note['timeline']=[row];self.append('note',note)
        current=next(t for t in self.project()['timeline'] if t['question_id']=='Q-n')
        self.assertTrue(current['comparable'])
        self.append('note',dict(target,revision=2))
        stale=next(t for t in self.project()['timeline'] if t['question_id']=='Q-n')
        self.assertFalse(stale['comparable']);self.assertEqual(stale['normalized_values'],[])
        self.assertTrue(stale['temporal_context']['temporal_requires_recheck'])
        self.assertEqual(stale['temporal_context']['conflicting_constraints'][-1]['reason'],'temporal_relation_target_changed')
        # A missing target event also invalidates the relation at the same revision.
        self.append('note',dict(target,timeline=[]))
        missing=next(t for t in self.project()['timeline'] if t['question_id']=='Q-n')
        self.assertTrue(missing['temporal_context']['temporal_requires_recheck'])
        self.assertFalse(missing['comparable'])

    def test_saved_time_assertion_preserves_context_and_current_revision(self):
        time=dict(id='TM-saved',version='original',question_id='Q-n',time_kind='file_metadata',shape='point',
            raw_values=['42'],normalized_values=['2020-01-01T00:00:00+00:00'],comparable=True,
            observation_ref=None,explanation='mtime',limitation='Preserved mtime may predate copying',file_time_type='mtime',
            temporal_context=dict(timestamp_role='source_timestamp',temporal_requires_recheck=False))
        self.append('report_change',self.changes(dict(timeline=[time]),revision='canonical-question-version'))
        result=self.project()
        self.assertEqual(result['timeline'][0]['temporal_context']['timestamp_role'],'source_timestamp')
        self.assertTrue(result['timeline'][0]['temporal_context']['temporal_requires_recheck'])
        self.assertFalse(result['timeline'][0]['comparable'])
        canonical={k:v for k,v in result['timeline'][0].items() if k!='session_id'}
        self.assertEqual(result['timeline'][0]['version'],content_version(canonical))
        self.assertEqual(result['timeline'][0]['session_id'],'session')
        self.append('note',dict(self.note,revision=2))
        self.assertEqual(self.project()['timeline'],[])

    def test_selected_source_requires_exact_version_and_pages_utf8_without_mutation(self):
        before=self.case.db.read_bytes()
        result=view.selected_source(self.resolved,self.source_id,self.source['version'],pointer='/lines/0/text',limit=6)
        self.assertEqual(result['text'],'Oct 1 ');self.assertEqual(result['next_offset'],6)
        self.assertTrue(result['has_more'])
        self.assertEqual(result['source_ref'],dict(kind='source',id=self.source_id,version=self.source['version']))
        with self.assertRaises(ValueError):view.selected_source(self.resolved,self.source_id,'bad-version')
        with self.assertRaises(ValueError):view.selected_source(self.resolved,'foreign',self.source['version'])
        self.assertEqual(before,self.case.db.read_bytes())

    def test_two_sessions_share_case_view_while_another_case_stays_excluded(self):
        writable=Case(self.case.manifest)
        sibling_source=writable.record('tool_result',dict(tool='forsic_read',path='sibling.log',
            lines=[dict(line=1,text='Other conversation within this case')]),'sibling-session')
        sibling=dict(self.note,note_id='sibling',session_id='sibling-session',goal_id='sibling-goal',
                     evidence_ids=[sibling_source],timeline=[dict(time='unresolved',description='Sibling event')])
        self.append('note',sibling)
        other_dir=self.root/'other-case';other_dir.mkdir();(other_dir/'evidence').mkdir()
        manifest=other_dir/'case.json'
        manifest.write_text(json.dumps(dict(case_id='other-case',evidence_root=str(other_dir/'evidence'),output_root=str(other_dir/'results'))))
        other=Case(manifest)
        foreign_source=other.record('tool_result',dict(tool='forsic_read',path='foreign-secret.log'),'foreign-session')
        other.record('note',dict(self.note,note_id='foreign',session_id='foreign-session',evidence_ids=[foreign_source]))
        before=self.case.db.read_bytes();result=self.project()
        self.assertEqual(self.case.db.read_bytes(),before)
        self.assertEqual({q['session_id'] for q in result['questions']},{'session','sibling-session'})
        self.assertEqual({s['session_id'] for s in result['sources']},{'session','sibling-session'})
        self.assertEqual(result['timeline'][0]['session_id'],'sibling-session')
        self.assertEqual({s['id'] for s in result['sessions']},{'session','sibling-session'})
        self.assertTrue(all(s['available'] is False for s in result['sessions']))
        self.assertTrue(all(s['label'].startswith('보존된 대화 ') for s in result['sessions']))
        self.assertNotIn('foreign-secret',json.dumps(result));self.assertNotIn(foreign_source,json.dumps(result))
        with self.assertRaises(ValueError):
            view.selected_source(self.resolved,foreign_source,'unused')
        switched=dict(self.resolved,requested_session_id='sibling-session',session_lineage=['sibling-session'],goal_id='sibling-goal')
        same=view.project_view(switched,profile='default')
        for key in ('questions','sources','timeline','sessions'):
            self.assertEqual(result[key],same[key])

    def test_question_analysis_only_uses_retained_fields_and_selected_claims(self):
        claim=dict(id='F-current',version='saved',status='adopted',limitations=['Selected claim limitation'])
        self.append('report_change',self.changes(dict(claims=[claim])))
        note=deepcopy(self.note);note.update(alternatives=['Recorded competing explanation'],
            gaps=['Declared missing data'],critical_gaps=['Declared critical gap'])
        note['investigation']['claim_refs']=[dict(id=claim['id'],version=content_version(claim))]
        self.append('note',note)
        q=self.project()['questions'][0]
        self.assertEqual(q['counterarguments'],note['alternatives'])
        self.assertEqual(q['gaps'],['Declared critical gap','Declared missing data'])
        self.assertEqual(q['limitations'],claim['limitations'])
        self.append('report_change',self.changes(dict(claims=[dict(claim,status='retracted')])))
        self.assertNotIn('limitations',self.project()['questions'][0])
        self.append('note',dict(self.note,note_id='undeclared'))
        q=next(q for q in self.project()['questions'] if q['id']=='Q-undeclared')
        for key in ('counterarguments','gaps','limitations'):
            self.assertNotIn(key,q)

    def test_report_metadata_uses_only_safe_case_bound_routes_and_caps(self):
        ident='a'*64;directory=self.case.output/('report-bundle-'+ident);directory.mkdir()
        (directory/'executive.html').write_text('synthetic report')
        (directory/'manifest.json').write_text(json.dumps(dict(bundle_id=ident,case_id='synthetic',title='Retained title',
            created_at='2020-01-01T00:00:00Z',files=[dict(name='executive.html',url='https://attacker.invalid'),
            dict(name='../../private'),dict(name='https://attacker.invalid'),dict(name={'invalid':'shape'})])))
        invalid=self.case.output/('report-bundle-'+'b'*64);invalid.mkdir()
        (invalid/'manifest.json').write_text(json.dumps(dict(bundle_id='b'*64,case_id='different',files=[])))
        (self.case.output/'report-safe.md').write_text('synthetic legacy report')
        (self.case.output/'report-symlink.md').symlink_to(self.case.manifest)
        before=self.case.db.read_bytes();result=self.project()
        self.assertEqual(result['counts']['reports'],3)
        self.assertEqual(len(result['reports']),2)
        bundle=next(r for r in result['reports'] if r['id']==ident)
        self.assertEqual(bundle['title'],'Retained title')
        self.assertEqual(bundle['files'],[dict(name='executive.html',url='/api/plugins/forsic/report-bundles/'+ident+'/executive.html?case_id=synthetic')])
        self.assertNotIn(str(self.root),json.dumps(result['reports']))
        self.assertNotIn('attacker.invalid',json.dumps(result['reports']))
        with patch.object(view,'MAX_REPORTS',1),patch.object(view,'MAX_SESSIONS',0):
            bounded=self.project()
        self.assertEqual(bounded['counts']['reports'],3)
        self.assertGreaterEqual(bounded['omitted_counts']['reports'],2)
        self.assertEqual(bounded['omitted_counts']['sessions'],1)
        self.assertEqual(self.case.db.read_bytes(),before)

    def test_report_created_at_normalizes_instants_and_omits_invalid_or_naive_values(self):
        ident='a'*64;directory=self.case.output/('report-bundle-'+ident);directory.mkdir()
        (directory/'executive.html').write_text('synthetic report')
        values=[(1577836800,'2020-01-01T00:00:00Z'),
                (1577836800.5,'2020-01-01T00:00:00.500000Z'),
                ('2020-01-01T09:00:00+09:00','2020-01-01T00:00:00Z'),
                ('not a date',None),('2020-01-01T00:00:00',None),
                (True,None),(float('inf'),None),(float('nan'),None),(10**100,None)]
        for stored,expected in values:
            with self.subTest(created_at=stored):
                (directory/'manifest.json').write_text(json.dumps(dict(bundle_id=ident,case_id='synthetic',
                    created_at=stored,files=[dict(name='executive.html')])))
                report=self.project()['reports'][0]
                if expected is None:
                    self.assertNotIn('created_at',report)
                else:
                    self.assertEqual(report['created_at'],expected)

    def native_profile(self):
        home=self.root/'profile';home.mkdir();native=home/'state.db'
        with closing(sqlite3.connect(native)) as db,db:
            db.execute('CREATE TABLE sessions(id TEXT,parent_session_id TEXT,end_reason TEXT,source TEXT,model_config TEXT,profile_name TEXT)')
            db.execute('CREATE TABLE state_meta(key TEXT,value TEXT)')
            db.execute("INSERT INTO sessions VALUES ('session',NULL,NULL,'tui','{}','default')")
        (home/'config.yaml').write_text('plugins:\n  entries:\n    forsic:\n      settings:\n        intake_root: '+str(self.intake)+'\n')
        (self.intake/(hashlib.sha256(b'session').hexdigest()+'.json')).write_text(json.dumps(dict(manifest=str(self.case.manifest))))
        return home,native

    def test_native_binding_rejects_cross_profile_missing_session_and_implicit_scope(self):
        home,native=self.native_profile()
        with patch('hermes_cli.profiles.get_profile_dir',return_value=home),patch('hermes_cli.profiles.profile_exists',return_value=True):
            resolved=view.resolve_view('session','default')
            self.assertEqual(resolved['case_id'],'synthetic')
            with self.assertRaises(LookupError):view.resolve_view('foreign','default')
            with self.assertRaises(LookupError):view.resolve_view('session','other')
            with self.assertRaises(ValueError):view.resolve_view('session','current')
            with self.assertRaises(ValueError):view.resolve_view('','default')
        self.assertTrue(native.is_file())

    def test_http_requires_explicit_scope_and_rejects_source_case_mismatch(self):
        app=FastAPI();app.include_router(plugin_api.router,prefix='/api/plugins/forsic')
        client=TestClient(app)
        self.assertEqual(client.get('/api/plugins/forsic/investigation-view').status_code,422)
        with patch.object(plugin_api,'investigation_scope',return_value=self.resolved):
            response=client.get('/api/plugins/forsic/investigation-view',params=dict(session_id='session',profile='default'))
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.json()['session_id'],'session')
            response=client.get('/api/plugins/forsic/source-page',params=dict(case_id='other',session_id='session',
                profile='default',source_id=self.source_id,source_version=self.source['version']))
            self.assertEqual(response.status_code,409)


if __name__=='__main__':unittest.main()
