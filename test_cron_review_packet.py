"""Offline packet regressions; synthetic retained records, no provider or case operation."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_investigation_context as fixtures
from forsic_plugin import independent_review as review
from forsic_plugin.investigation_context import resolve_context
from forsic_plugin.report_driven.host import current, invoke
from hermes_cli.goals import goal_identity
from test_report_driven import mission, proposal


class CronReviewPacketTests(unittest.TestCase):
    setUp=fixtures.InvestigationContextTests.setUp
    bind=fixtures.InvestigationContextTests.bind
    session=fixtures.InvestigationContextTests.session
    save_note=fixtures.InvestigationContextTests.save_note

    def collect(self,stage='checkpoint'):
        resolved=resolve_context(self.sid,intake_root=self.intake,native_db=self.native)
        before=self.case.db.read_bytes()
        with patch('socket.socket.connect',side_effect=AssertionError('offline test forbids network')):
            result=review._collect(resolved,stage,'Synthetic current report; execution unknown.')
        self.assertEqual(self.case.db.read_bytes(),before)
        self.assertLessEqual(len(result['prompt'])+len(review.SYSTEM),review.MAX_INPUT)
        return result,json.loads(result['prompt'])

    def finding(self,**coordinate):
        return json.dumps({'findings':[dict(question='사용자별 설정을 확인했는가?',why='목록의 후보는 원본 재확인이 필요합니다.',
            next_check='해당 원본을 조회하고 설정과 실행을 구별하세요.',**coordinate)]},ensure_ascii=False)

    def test_final_packet_keeps_source_versions_with_real_task_state_and_old_and_recent_grounding(self):
        sids=[]
        for name in ('old','recent'):
            text=name+' retained request; success unknown.'
            sid=self.case.record('tool_result',dict(tool='forsic_read',path=name+'.log',
                lines=[dict(line=1,text=text)],complete_file=True),self.sid)
            sids.append(sid)
            if name=='old':
                for index in range(50):
                    self.case.record('tool_result',dict(tool='forsic_read',path='filler.log',
                        lines=[dict(line=1,text='Synthetic filler '+str(index))]),self.sid)
        self.note['evidence_ids']=sids
        self.save_note()
        # Use actual report writers so retained_task_state and final_grounding both
        # read a valid current report ledger, including adopted exact observations.
        for action,payload in (('mission',mission),('assess',lambda state:proposal(state,self.case))):
            state=current(self.case)
            state['sources']=[source for source in state['sources'] if source['id'] in sids]
            invoke(self.case,dict(action=action,snapshot_id=state['meta']['snapshot_id'],payload=payload(state),
                _session_id=self.sid,_goal_id=goal_identity(self.goal)))
        observations=current(self.case)['observations']
        result,body=self.collect('final')
        self.assertEqual(body['analyst_state']['task_focus']['status'],'ready')
        grounded=body['analyst_state']['grounding']['observations']
        self.assertEqual({o['id'] for o in grounded},{o['id'] for o in observations})
        self.assertTrue(all(o['coordinate_validation']=='retained_source_version_and_exact_utf8_span_only' for o in grounded))
        for observation in observations:
            source=next(s for s in body['sources'] if s['source_id']==observation['source_ref']['id'])
            self.assertEqual(source['source_version'],review._retained_version(self.case,source['source_id']))
            selected=next(f for f in source['fields'] if f.get('byte_start')==0 and f['pointer']=='/lines/0/text')
            finding=review._parse(self.finding(review_ref=selected['review_ref']),result['refs'],review_refs=result['review_refs'])[0]
            self.assertEqual(finding['source_version'],source['source_version'])
            self.assertEqual(finding['quote'],observation['literal'])
        # Exercise the real render -> independent_gate.context -> final packet
        # path. A partial report is explicit and does not dispatch a reviewer.
        with patch.object(review,'_collect',wraps=review._collect) as collect, \
             patch('agent.auxiliary_client.call_llm',side_effect=AssertionError('render must not call a model')):
            rendered=invoke(self.case,dict(action='render',allow_partial=True,
                _session_id=self.sid,_goal_id=goal_identity(self.goal),
                _independent_review_options=dict(enabled=True,session_id=self.sid,
                    intake_root=self.intake,native_db=self.native)))
        self.assertTrue(any(call.args[1]=='final' for call in collect.call_args_list))
        self.assertTrue(Path(rendered['manifest']).is_file())
        self.assertTrue(any(path.endswith('.html') and Path(path).is_file() for path in rendered['files']))
        # Changed source bytes keep the selected observation explicitly unverified.
        event=deepcopy(self.case.event(sids[0])['data'])
        event['lines'][0]['text']='Changed retained source; prior observation is stale.'
        with self.case.connect() as db:
            db.execute('UPDATE events SET data=? WHERE id=?',(json.dumps(event),sids[0]))
        _,changed=self.collect('final')
        self.assertTrue(any(o['coordinate_validation']=='unverified' for o in changed['analyst_state']['grounding']['observations']))

    def test_shell_tail_inventory_remains_separate_exact_unicode_spans_with_safe_retrieval(self):
        stdout='한글 header\n'+'ordinary service log\n'*900+'\n/var/spool/cron/crontabs/nonroot-account\n'
        sid=self.case.record('tool_result',dict(tool='forsic_shell',representation='derived_command_output',
            original_evidence=False,coverage_not_established=True,stdout=stdout,stderr='',
            raw_output=dict(stdout_base64='PRIVATE_RAW_CAPTURE',complete=True)),self.sid)
        result,body=self.collect()
        item=next(s for s in body['sources'] if s['source_id']==sid)
        spans=[f for f in item['fields'] if f['pointer']=='/stdout']
        self.assertEqual(len(spans),2)
        self.assertIn('/var/spool/cron/crontabs/nonroot-account',spans[-1]['value'])
        for span in spans:
            self.assertEqual(stdout[span['character_start']:span['character_end']],span['value'])
            self.assertEqual(stdout.encode()[span['byte_start']:span['byte_end']],span['value'].encode())
            parsed=review._parse(self.finding(review_ref=span['review_ref']),result['refs'],review_refs=result['review_refs'])[0]
            self.assertEqual(parsed['retrieval']['pointer'],'/stdout')
            self.assertEqual(parsed['retrieval']['offset'],span['byte_start'])
        self.assertGreater(spans[-1]['byte_start'],spans[-1]['character_start'])
        self.assertEqual(item['omitted_text_characters']['/stdout'],len(stdout)-sum(len(s['value']) for s in spans))
        self.assertEqual(result['refs'][sid]['/stdout'],[s['value'] for s in spans])
        executive=review._executive_snapshot(resolve_context(self.sid,intake_root=self.intake,native_db=self.native),
            result,1,dict(findings=[],dispositions=[]))
        self.assertEqual(executive['refs'][sid]['/stdout'],[s['value'] for s in spans])
        from forsic_plugin.independent_review_state import _presented, _presented_handles
        request=dict(schema_version=3,messages=[dict(role='user',content=result['prompt'])])
        self.assertEqual(_presented(request)[sid]['/stdout'],result['refs'][sid]['/stdout'])
        self.assertEqual(_presented_handles(request),result['review_refs'])
        # Legacy exact-substring validation cannot accept a concatenation of disjoint excerpts.
        rejected=[]
        joined=spans[0]['value'][:30]+spans[1]['value'][-50:]
        self.assertEqual(review._parse(self.finding(source_id=sid,pointer='/stdout',quote=joined),result['refs'],rejected=rejected),[])
        self.assertIn('not an exact substring',rejected[0]['reason'])
        self.assertNotIn('PRIVATE_RAW_CAPTURE',result['prompt'])

    def test_triage_packet_has_profile_counts_input_states_and_exact_source_recheck_fields(self):
        source=dict(id='synthetic-original',version='sha256:synthetic-version',pointer='/file_path')
        inputs=[dict(input_id='I'+str(i),state='complete' if i==0 else 'partial',candidate_count=i+1,
            counts_complete=i==0,reason_codes=[] if i==0 else ['record_limit'],
            source_recheck=dict(source_ref=source,selection_kind='evidence_file')) for i in range(2)]
        candidate=dict(candidate_id='C0001',input_id='I1',status='candidate_not_adopted',
            entry_kind='cron_schedule',triage_format='cron_user',source_recheck=dict(source_ref=source,
            byte_start=41,byte_end=99,line_start=2,line_end=2),restricted_row_ref=dict(secret='PRIVATE_RESTRICTED_ROW'))
        sid=self.case.record('tool_result',dict(tool='forsic_shell',schema='forsic.triage.v1',action='triage',profile='linux_persistence',
            representation='derived_triage_candidates',original_evidence=False,coverage_not_established=True,
            adoption='candidates_only_explicit_source_recheck_and_note_adoption_required',
            coverage=dict(state='partial',selected_files=2,scope='selected_files_only',inputs=inputs),
            summary=dict(candidate_count=3,returned_candidates=1,omitted_candidates=2,counts_complete=False,count_scope='observed_output_candidates_only'),
            candidates=[candidate],command='PRIVATE_COMMAND',engine_identity=dict(private='PRIVATE_ENGINE')) ,self.sid)
        # Structured input inventory must survive beyond the recent-source window.
        for index in range(50):
            self.case.record('tool_result',dict(tool='forsic_read',path='filler.log',
                lines=[dict(line=1,text='Synthetic filler '+str(index))]),self.sid)
        result,body=self.collect()
        item=next(s for s in body['sources'] if s['source_id']==sid)
        fields={f['pointer']:f for f in item['fields']}
        self.assertEqual(item['source_role'],'derived_triage_candidates')
        for pointer,value in (('/profile','linux_persistence'),('/summary/candidate_count',3),
            ('/summary/counts_complete',False),('/coverage/inputs/1/state','partial'),
            ('/coverage/inputs/1/source_recheck/source_ref/id',source['id']),
            ('/candidates/0/status','candidate_not_adopted'),('/candidates/0/source_recheck/source_ref/id',source['id']),
            ('/candidates/0/source_recheck/byte_start',41)):
            self.assertEqual(fields[pointer]['value'],value)
            finding=review._parse(self.finding(review_ref=fields[pointer]['review_ref']),result['refs'],review_refs=result['review_refs'])[0]
            self.assertEqual(finding['source_id'],sid)  # Candidate handle never becomes original-source evidence.
        for private in ('PRIVATE_RESTRICTED_ROW','PRIVATE_COMMAND','PRIVATE_ENGINE'):
            self.assertNotIn(private,result['prompt'])

    def test_many_large_shell_returns_keep_recent_tail_and_input_budget(self):
        sids=[]
        for index in range(12):
            sids.append(self.case.record('tool_result',dict(tool='forsic_shell',
                representation='derived_command_output',original_evidence=False,coverage_not_established=True,
                stdout='head '+str(index)+'\n'+'ordinary output\n'*2000+'\n/settings/user-jobs/nonroot-account-'+str(index)),self.sid))
        result,body=self.collect('final')
        newest=next(source for source in body['sources'] if source['source_id']==sids[-1])
        tail=next(field for field in newest['fields'] if field['pointer']=='/stdout' and field['character_start']>0)
        self.assertIn('/settings/user-jobs/nonroot-account-11',tail['value'])
        self.assertGreater(body['omitted']['sources'],0)
        self.assertEqual(body['omitted']['derived_shell_outputs'],12-sum(source['tool']=='forsic_shell' for source in body['sources']))
        self.assertEqual(review._parse(self.finding(review_ref=tail['review_ref']),result['refs'],review_refs=result['review_refs'])[0]['quote'],tail['value'])


if __name__=='__main__':unittest.main()
