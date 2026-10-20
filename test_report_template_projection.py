"""Offline content placement and uncertainty preservation in the report template."""
from copy import deepcopy
import unittest

from forsic_plugin.report_driven.reader_projection import labels, project_integrated
from forsic_plugin.report_driven.report_slots import SLOTS


def fixture():
    state={'scope':{'id':'scope','approved_scope':'선택한 서버의 보존 자료',
                    'excluded_scope':'다른 자산 제외','evidence_labels':['선택 서버']},
           'meta':{'case_id':'synthetic-case','report_id':'synthetic-report',
                   'generated_at':'2026-01-02T03:04:05+00:00','prepared_by':'원장 투영'},
           'status':{'report':'draft','explicit_limitations':['수집 이전 구간 미검토']},
           'sources':[{'id':'SRC-one','label':'보존 반환','coverage':'선택 파일만'}],
           'observations':[{'id':'OBS-one','literal':'request accepted','interpretation_limit':'실행 성공은 미입증'},
                           {'id':'OBS-counter','literal':'request denied','interpretation_limit':'다른 요청만 해당'}],
           'questions':[],'claims':[],'timeline':[]}
    entry={'state':'inferred','brief':'정보 접촉·전송은 함께 평가한 추정이다.',
           'scope':'이 요청에 한함','basis':'요청과 성공은 구별함',
           'limitations':['본문 끝의 중요한 유보'], 'assumptions':['동일 수집 구간 가정'],
           'counterevidence_refs':[{'id':'OBS-counter'}],
           'evidence_refs':[{'id':'OBS-one'},{'id':'SRC-one'}],
           'claim_details':[{'statement':'성공으로 연결될 가능성','assertion_kind':'interpretation'}],
           'needs_recheck':True,'recheck_reasons':['선택 근거가 바뀜']}
    projection={'slots':[{'slot':key,'label':label,'missing':key not in ('attacker_actions','external_disclosure','decisive_evidence'),
                        'entries':[deepcopy(entry)] if key in ('attacker_actions','external_disclosure','decisive_evidence') else []}
                       for key,label in SLOTS]}
    return state,projection


def tables(page,variant):
    return [b for b in page['blocks'] if b.get('variant')==variant]


class ReportTemplateProjectionTests(unittest.TestCase):
    def test_layout_reorders_cells_without_changing_judgment_or_creating_disclosure_findings(self):
        state,projection=fixture();before=deepcopy((state,projection))
        pages=project_integrated(state,projection);mapping=labels(state)
        self.assertEqual([p['id'] for p in pages],['I01','I02','I03'])
        summary=tables(pages[0],'summary')[0]
        self.assertEqual(summary['headers'],['구분','핵심 확인내용','판단','근거'])
        row=next(row for row in summary['rows'] if row[0]=='주요 행위')
        authored=projection['slots'][2]['entries'][0]
        self.assertIn(authored['brief'],row[1])
        for field in ('scope','basis'):
            self.assertIn(authored[field],row[1])
        for field in ('limitations','assumptions','recheck_reasons'):
            for value in authored[field]:self.assertIn(value,row[1])
        self.assertIn(mapping['OBS-counter'],row[1])
        self.assertEqual(row[2],'재검토 필요 (이전 선언: 추정)')
        self.assertIn(mapping['OBS-one'],row[3]);self.assertIn(mapping['SRC-one'],row[3])
        impact=tables(pages[1],'impact')[0]
        disclosure=[row for row in impact['rows'] if '외부' in row[0]]
        self.assertEqual(len(disclosure),1)
        self.assertEqual(disclosure[0][0],'외부 노출·반출')
        self.assertIn(authored['brief'],disclosure[0][1])
        evidence=tables(pages[1],'evidence')[0]['rows'][0]
        self.assertIn('직접 입증 범위 별도 선언 없음',evidence[2])
        self.assertIn('추정',evidence[2]);self.assertIn('재검토 필요',evidence[2])
        self.assertIn('실행 성공은 미입증',evidence[3])
        for page in pages:
            for block in page['blocks']:
                if block['kind']=='table':
                    self.assertEqual(len(block['widths']),len(block['headers']))
                    self.assertEqual(len(set(block['widths'])),1)
                    self.assertTrue(all(len(row)==len(block['headers']) for row in block['rows']))
        self.assertEqual((state,projection),before)

    def test_missing_briefs_are_editorial_and_metadata_never_invents_approval_or_timezone(self):
        state,projection=fixture()
        for slot in projection['slots']:slot.update(missing=True,entries=[])
        pages=project_integrated(state,projection)
        missing_row=tables(pages[0],'summary')[0]['rows'][0]
        self.assertEqual(missing_row[1],'보고용 판단 선언 미작성')
        self.assertEqual(missing_row[2],'선언 미작성')
        notices=[b for page in pages for b in page['blocks'] if b['kind']=='callout']
        self.assertEqual(len(notices),1)
        self.assertIn('자료 부재나 침해 부재를 뜻하지 않습니다',notices[0]['text'])
        metadata=tables(pages[0],'metadata')[0]
        self.assertIn('판단 기준시점: '+state['meta']['generated_at'],metadata['rows'][0][1])
        self.assertNotIn('KST',str(pages));self.assertNotIn('승인 완료',str(pages))
        self.assertIn('수집 이전 구간 미검토',str(pages[2]))

    def test_all_timeline_rows_keep_raw_observed_estimated_and_uncomparable_time_separate(self):
        state,projection=fixture()
        for i in range(11):
            state['timeline'].append({'id':'TM-'+str(i),'comparable':True,
                'normalized_values':['2026-01-02T03:04:'+str(i).zfill(2)+'+00:00'],
                'raw_values':['raw-'+str(i)],'time_kind':'record','explanation':'보존 기록 '+str(i),
                'limitation':'성공·귀속은 미입증','observation_ref':{'id':'OBS-one'},
                'temporal':{'observed_or_inferred':'observed','confidence_basis':'offset 명시',
                            'timezone_basis':'원문 offset','assumptions':['기록 시계 가정']}})
        state['timeline'].append({'id':'TM-unknown','comparable':False,'normalized_values':[],
            'raw_values':['Oct 1'],'time_kind':'record','explanation':'연도 미상 기록','limitation':'연도 미확정',
            'observation_ref':{'id':'OBS-counter'},'temporal':{'observed_or_inferred':'inferred',
                'earliest':'하한 기록','latest':'상한 기록','assumptions':['시계 오차 가정']},
            'temporal_context':{'temporal_requires_recheck':True,'conflicting_constraints':[{'reason':'시계 충돌'}]}})
        before=deepcopy(state);pages=project_integrated(state,projection)
        known,unknown=tables(pages[2],'timeline')
        self.assertEqual(len(known['rows']),11);self.assertEqual(len(unknown['rows']),1)
        for i,row in enumerate(known['rows']):
            self.assertIn('raw-'+str(i),row[0]);self.assertIn('관측 시각',row[0])
            self.assertIn('보존 기록 '+str(i),row[1]);self.assertIn('기록 시계 가정',row[2])
            self.assertIn(labels(state)['OBS-one'],row[3])
        row=unknown['rows'][0]
        for text in ('시각 추정','하한 기록','상한 기록','Oct 1'):self.assertIn(text,row[0])
        for text in ('연도 미확정','시계 오차 가정','시간 연결 재검토 필요','시계 충돌'):self.assertIn(text,row[2])
        self.assertNotIn('KST',str(pages[2]));self.assertEqual(state,before)


if __name__=='__main__':unittest.main()
