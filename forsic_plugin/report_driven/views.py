"""Deterministic reader views. No new conclusions, model calls or network."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import tempfile
import os
import shutil
import errno

from .host import encoded, digest, checked, current, review_material
from .render_views import render_html


def paragraph(text):return {'kind':'paragraph','text':str(text)}
def table(headers, rows, widths=None):
    return {'kind':'table','headers':headers,'rows':rows or [['기록 없음']+['']*(len(headers)-1)],'widths':widths or [100/len(headers)]*len(headers)}
def refs(values):return ', '.join(r['id'] for r in values) or '없음'
def joined(values):return '\n'.join(values) or '기록 없음'
def page(key,title,blocks):return dict(id=key,title=title,subtitle='동일 판단 스냅샷 · 내부 검토용',blocks=blocks)


def time_row(t):
    model=t.get('temporal') or {}
    context=t.get('temporal_context') or {}
    raw=joined(t['raw_values']) if t['raw_values'] else '정확한 원문 시각 없음'
    if not model:
        return [joined(t['normalized_values'] or t['raw_values'])+'\n'+t['time_kind']+' '+str(t['file_time_type'] or ''),t['explanation'],t['limitation']]
    label='분석가의 시각 추정' if model['observed_or_inferred']=='inferred' else '보존된 시각 관측'
    bounds=str(model.get('earliest') or '하한 미상')+' ~ '+str(model.get('latest') or '상한 미상')
    candidates=['후보: '+str(x.get('earliest') or '미상')+' ~ '+str(x.get('latest') or '미상')+' / '+x['basis'] for x in model.get('candidates',[])]
    details=t['explanation']+'\n근거: '+model['confidence_basis']+'\n가정: '+joined(model.get('assumptions',[]))
    details+='\n시계: '+model.get('clock_domain','unknown')+' / 시간대: '+model.get('timezone_basis','unknown')+' / 연도: '+model.get('year_basis','unknown')
    for relation in context.get('temporal_relations',[]):
        details+='\n관계: '+relation['relation']+' → '+relation.get('scoped_target_event_ref',relation['target_event_ref'])+' / '+relation['reason']
    limit=t['limitation']
    if context.get('temporal_requires_recheck'):
        limit+='\n시간 연결 재검토: '+joined([x['reason'] for x in context.get('conflicting_constraints',[])])
    return [label+'\n'+bounds+'\n'+joined(candidates)+'\n원문 '+str(t['file_time_type'] or '')+': '+raw,details,limit]


def _appendix_views(s,review=None,write_status=None,*,independent_review=None,scope_inventory=None):
    from .current_judgment import current_claims, current_judgment
    judgments={q['id']:current_judgment(s,q) for q in s['questions']}
    selected_claims=current_claims(s)
    def declaration(c):
        # Historical artifacts keep their original content; new v2 records
        # distinguish omission from an explicitly supplied empty analysis list.
        if 'declared_analysis_fields' not in c:
            return ''
        from .current_judgment import analysis_declaration
        fields=analysis_declaration(c)['undeclared_analysis_fields']
        return '\n미선언 분석 항목: '+', '.join(fields) if fields else ''
    scope_status='기록된 질문의 범위를 종결했습니다. 잔여 불확실성과 재개 조건을 참조하세요.' if s['questions'] and all(q['work_state']=='scoped_closed' and not q.get('judgment_review_required') for q in s['questions']) else '질문별 진행·종결·보류 및 남은 확인 범위는 현재 조사 상태를 참조하세요.'
    requirements={r['requirement_id']:r for r in s['requirements']}
    # The skill selects questions via the supplied catalog contract. No incident
    # topic, artifact name, IOC or forced investigation checklist lives here.
    chosen={r['id'] for r in requirements.get('REQ-CORE-ANSWER',{}).get('basis_refs',[]) if r['kind']=='question'}
    executive_questions=[q for q in s['questions'] if not chosen or q['id'] in chosen]
    chosen_claims={r['id'] for r in requirements.get('REQ-FINDINGS',{}).get('basis_refs',[]) if r['kind']=='claim'}
    explicit_selection=bool(chosen or chosen_claims)
    chosen_claims.update(r['id'] for q in executive_questions for r in q['claim_refs'])
    def finding_text(claim):
        if any(claim['statement']==q['answer'] for q in s['questions']):
            return '저장된 종합 답과 동일: 조사 질문과 현재 답(A01) 참조.'
        return claim['statement']
    findings=[[c['id'],finding_text(c)+'\n'+('해석 후보' if c['status']=='candidate' else '근거 결속된 해석'),joined(c['limitations'])+'\n가정: '+joined(c.get('assumptions',[]))+'\n판단 종류: '+c['assertion_kind']+' / '+c.get('inference_strength','unrated')+'\n근거: '+refs(c['source_refs'])+'\n반론: '+refs(c['counterevidence_refs'])+declaration(c)] for c in selected_claims if c['status'] not in ('retracted','superseded')]
    work_labels={'open':'검토할 질문','active':'조사 중','held':'보류','scoped_closed':'해당 범위 종결',
                 'blocked_internal':'도구·내부 제약','blocked_external':'외부 자료 대기','budget_deferred':'예산으로 보류'}
    def disposition(q):
        return work_labels.get(q['work_state'],q['work_state'])+'\n'+str(q.get('closure_rationale') or q.get('deferred_reason') or '')
    def current_answer(q):
        from .delivery import answer_record
        j=answer_record(s,q)
        explanation=q.get('reader_explanation') or {}
        if explanation.get('status') in ('current','provisional') and explanation.get('paragraphs') and '\n\n'.join(
            item['text'] for item in explanation['paragraphs'])==j['answer']:
            return ('[재검토 필요]\n' if j['review_required'] else '')+'현재 답은 읽기용 설명(EX01)에 한 번 수록합니다.'
        return ('[재검토 필요]\n' if j['review_required'] else '')+j['answer']
    answers=table(['질문','현재 답과 범위','조사 상태와 남은 확인'],[[q['id']+' '+q['question'],current_answer(q)+'\n'+q['scope'],disposition(q)+'\n'+(', '.join(q['remaining_gap_ids']) or '남은 검사 기록 없음')+'\n재개: '+joined(q.get('reopen_conditions',[]))] for q in s['questions']],[25,45,30])
    gaps=table(['공백','판단에 미치는 한계','다음 확인 / 재개'],[[g['id']+'\n'+g['kind'],g['original_obligation']+'\n'+g['reason'],g['feasible_next_action']+'\n'+joined(g['reopen_conditions'])] for g in s['gaps'] if g['disposition']!='resolved'],[20,45,35])
    alternatives=table(['설명','평가 / 반론','다음 판별'],[[h['id']+' '+h['explanation'],h['status']+'\n'+refs(h['counterevidence_refs'])+'\n'+joined(h['assumptions']),h['prediction']+'\n'+h['compatibility']] for h in s['hypotheses'] if h['id'] in {r['id'] for q in s['questions'] for r in q['hypothesis_refs']}],[32,34,34])
    timeline=table(['시각과 종류','내용·근거·가정','한계'],[time_row(t) for t in s['timeline']],[28,36,36])
    # Historical recommendations stay in the ledger, but new reports do not
    # prescribe business decisions. Only evidenced performed actions appear.
    actions=table(['확인된 수행 조치','근거','실행 기록'],[[a['title'],refs(a['basis_refs']),a['execution_receipt']] for a in s['actions'] if a['action_kind']=='performed' and a['execution_receipt']],[34,40,26])
    missions=table(['미션 / 원래 의무','다음에 구별할 내용','조건 / 상태'],[[m['id']+'\n'+m['original_obligation_id'],m['target_proposition']+'\n'+m['why_it_matters'],m['inconclusive_rule']+'\n'+m['state']+' / '+m['budget']['authority']] for m in s['missions']],[25,40,35])
    status=table(['상태','현재 기록'],[[k,str(v)] for k,v in s['status'].items() if k!='explicit_limitations'],[35,65])
    limit=paragraph(joined(s['status']['explicit_limitations']))
    impact=requirements.get('REQ-IMPACT',{})
    impact_block=paragraph('업무 영향은 아직 평가하지 않았습니다.') if impact.get('basis_refs')==[{'kind':'scope','id':s['scope']['id'],'version':s['scope']['version']}] else paragraph(impact.get('rationale') or '업무 영향은 아직 평가하지 않았습니다.')
    from .reader_projection import project_executive, project_body, labels as reader_labels, PROJECTION_VERSION
    executive=project_executive(s,executive_questions,
        [c for c in selected_claims if not explicit_selection or c['id'] in chosen_claims],
        timeline=timeline,alternatives=alternatives,actions=actions,
        impact=impact_block,scope_status=scope_status)
    sources=table(['근거 / 생성 계보','출처와 보존 지문','실제 반환 범위'],[[x['id']+'\n'+x['source_generation_group'],x['label']+'\n'+x['locator']+'\nSHA-256 '+x['content_sha256'],x['coverage']] for x in s['sources']],[23,42,35])
    observations=table(['관측 / 원문 위치','정확히 제시된 문자열','해석 한계'],[[o['id']+'\n'+o['source_ref']['id']+o['field_pointer']+f"\nUTF-8 [{o['byte_start']},{o['byte_end']})",o['literal'],o['presented_view']+'\n'+o['interpretation_limit']+('\n취득·읽기 범위: '+json.dumps(o['acquisition_context'],ensure_ascii=False) if o.get('acquisition_context') else '')] for o in s['observations']],[30,38,32])
    sources['row_ids']=[x['id'] for x in s['sources']]
    observations['row_ids']=[x['id'] for x in s['observations']]
    tests=table(['검사와 결과','판별 조건','실행 / 평가'],[[t['id']+'\n'+t['target_proposition']+'\n'+str(t['physical_job_ref']),str(t['support_rule'] or '')+'\n'+str(t['refute_rule'] or '')+'\n'+t['inconclusive_rule'],t['execution_state']+' / '+t['assessment_state']+'\n'+t['design_timing']] for t in s['tests']],[34,42,24])
    assessments=table(['평가','답을 바꾼 이유','원래 의무'],[[a['id']+' '+a['outcome'],a['reasoning_summary']+'\n'+refs(a['observation_refs']),'해결: '+', '.join(a['resolved_gap_ids'])+'\n남음: '+', '.join(a['remaining_gap_ids'])] for a in s['assessments']],[24,46,30])
    practitioner=[page('A01','조사 질문과 현재 답',[paragraph(s['scope']['original_question']),paragraph(s['scope']['approved_scope']),paragraph(s['scope']['excluded_scope']),answers]),
        page('A02','증거와 조사 범위',[paragraph(s['coverage_summary']),paragraph(s['untriaged_inventory_summary']),sources]),
        page('A03','분석 방법과 시간 해석',[paragraph('Forsic의 읽기 전용 도구 반환과 사건별 노트를 사용했습니다. 미션 초안은 실행 예약이나 예산 승인이 아닙니다.'),paragraph('문자열 span은 보존된 도구 반환 필드의 UTF-8 기준이며 디스크 이미지의 바이트 위치와 다릅니다. 기록·명령은 실행 성공이나 승인 사실이 아닙니다.'),paragraph('모델별 전체 비용·취득 무결성·의미 정확도: '+joined(s['missing_metrics'])),timeline]),
        page('A04','질문과 경쟁 설명',[answers,alternatives,tests]),
        page('A05','발견과 원문 대조',[table(['발견','현재 내용','중요 한계와 근거'],findings,[18,42,40]),observations,assessments]),
        page('A06','사건 연결과 끊긴 부분',[paragraph('동일 이름·시각 근접만으로 동일 작업이나 인과관계를 만들지 않습니다. 아래 질문별 평가와 미해결 연결 범위를 적용합니다.'),answers,gaps]),
        page('A07','사건 시간축',[timeline,paragraph('비교할 근거가 없는 시각은 정렬·KST 변환하지 않았습니다. 파일 mtime·ctime·atime은 행위 시각과 구별합니다.')]),
        page('A08','영향과 대상 범위',[paragraph(s['scope']['approved_scope']),table(['발견','현재 내용','중요 한계와 근거'],findings,[18,42,40]),paragraph('전체 자산·계정·유출량 집계는 제공되지 않았습니다. 영향 미확인을 무피해로 해석하지 않습니다.')]),
        page('A09','반론과 정정',[alternatives,limit,paragraph('과거 스냅샷은 보존됩니다. 최신 질문 정의가 바뀌면 이전 정의에 대한 미션·평가는 현재 답에서 제외합니다. 같은 물리 반환의 재사용은 독립 근거가 아닙니다.')]),
        page('A10','검사 결과와 남은 의무',[tests,assessments,paragraph('판별 결과의 채택 영수증은 참조·원문 결속 검증입니다. 자연어 의미 승인·원의무 해소 증명은 별도입니다.'),paragraph('미제공 비용: '+joined(s['missing_metrics']))]),
        page('A11','조치와 다음 미션',[actions,gaps,missions]),
        page('A12','근거 탐색과 배포 상태',[paragraph('정확한 source ID, 반환 필드와 span은 A02·A05에 있습니다. 동봉 manifest.json은 보고 파일과 선택 채증 묶음이 포함된 경우 그 묶음의 판단 스냅샷과 지문을 제공합니다. 원시 증거 전문은 기본 배포물에 넣지 않습니다.'),status,paragraph('기관 승인·수신자 정책은 연결되지 않았습니다. 이 묶음은 내부 검토용이며 발행 승인본이 아닙니다.')])]
    review=review or {'review_status':'not_reviewed','advice':''}
    label={'not_reviewed':'별도 결론 검토를 하지 않았습니다.',
           'not_sent':'별도 결론 검토 요청을 전송하지 못했습니다.',
           'delivery_unconfirmed':'별도 결론 검토의 응답을 확인하지 못했습니다. 실행 여부는 미확정입니다.',
           'incomplete':'별도 결론 검토의 답변이 끝까지 도착하지 않았습니다.',
           'received':'별도 결론 검토 의견을 받았습니다. 의미 정확도 승인이나 조사 완료가 아닙니다.',
           'stale':'이전 판단에 대한 검토입니다. 현재 답변·근거의 검토로 사용할 수 없습니다.',
           'unavailable':'지정한 결론 검토 기록을 확인할 수 없습니다.'}.get(review['review_status'],'별도 결론 검토 상태를 확인하지 못했습니다.')
    review_blocks=[{'kind':'callout','label':'결론 검토 상태','text':label}]
    if review['review_status']=='not_sent' and isinstance(review.get('input_characters'),int) and isinstance(review.get('limit_characters'),int) and review['input_characters']>review['limit_characters']:
        review_blocks.append(paragraph(f"원문을 자르지 않아 전송하지 않았습니다. 검토 입력 {review['input_characters']:,}자 / 한도 {review['limit_characters']:,}자. 질문 범위를 나누어 검토할 수 있습니다."))
    if review.get('question_ids'):review_blocks.append(paragraph('검토 대상 질문: '+', '.join(review['question_ids'])+' · 그 밖의 질문은 이 검토에 포함하지 않았습니다.'))
    if review.get('advice'):review_blocks.append(paragraph('검토 의견 · '+review['advice']))
    for pages in (executive,practitioner):pages[0]['blocks'].extend(deepcopy(review_blocks))
    independent_blocks=[]
    if independent_review:
        pending=independent_review.get('pending_finding_ids') or []
        label=('현재 최종 답에 대한 독립 검토 기록이 있습니다. 의견 수신은 의미 정확도 승인이나 조사 완료가 아닙니다.'
               if independent_review.get('final_review_current') else '현재 최종 답의 독립 검토가 확인되지 않았습니다.')
        independent_blocks.append({'kind':'callout','label':'독립 검토와 의견 처분','text':label})
        if pending:independent_blocks.append(paragraph('미처분 중요 검토 의견: '+', '.join(pending)+' · 이 보고서는 초안으로 유지됩니다.'))
        if independent_review.get('availability_limitation'):
            independent_blocks.append(paragraph('독립 검토 한계 · '+independent_review['availability_limitation']))
    # Current question notes own these dispositions; rendering never adopts the
    # reviewer's suggestion or hides a scoped limitation behind a completion flag.
    disposition_labels={'investigated':'조사 후 처리','refuted':'근거로 반박','scope_limited':'한계로 명시'}
    review_rows=[]
    for question in s['questions']:
        for response in question.get('review_responses',[]):
            citations=[c['source_id']+c['pointer']+'\n'+c['literal']+
                '\nUTF-8 ['+str(c['byte_start'])+','+str(c['byte_end'])+') / '+c['source_version']
                for c in response.get('citations',[])] or response.get('citation_refs',[])
            review_rows.append([question['id']+'\n'+response.get('review_id','')+' / '+response.get('finding_id',''),
                disposition_labels.get(response.get('disposition'),str(response.get('disposition','')))+'\n'+response.get('reason','')+
                ('\n명시한 한계: '+response['limitation'] if response.get('limitation') else ''),
                '\n'.join(citations) or '추가 인용 없음; 처분 이유와 범위를 참조하세요.'])
    executive[0]['blocks'].extend(deepcopy(independent_blocks))
    practitioner[0]['blocks'].extend(deepcopy(independent_blocks))
    if review_rows:
        detail=table(['질문과 검토 의견','현재 처분과 남은 한계','처분에 사용한 인용'],review_rows,[28,48,24])
        practitioner[0]['blocks'].append(deepcopy(detail))
        executive[0]['blocks'].append(table(['질문과 검토 의견','현재 처분과 남은 한계'],[[row[0],row[1]] for row in review_rows],[30,70]))
        executive.append({**page('EX04','부록 검토 의견과 처분 근거',[detail]),'section':'appendix'})
    if scope_inventory is not None:
        labels={'listed':'목록·메타데이터만 관측','partial_body':'본문 일부 관측',
                'body_read':'완료된 본문 반환 기록 있음','read_failed':'본문 읽기 실패'}
        rows=[]
        for target in scope_inventory['targets']:
            identity=target['identity']
            rows.append([json.dumps(identity,ensure_ascii=False),labels[target['state']],
                '\n'.join(r['source_id']+r['pointer'] for r in target['source_refs'])])
        blocks=[paragraph('현재 대화에서 실제 반환된 자료 범위입니다. 목록 발견·본문 반환은 분석 완료가 아니며, 목록에 없는 자료의 검토 여부는 알 수 없습니다. 터미널 파생 결과만으로 원본 본문 검토를 인정하지 않습니다.'),
            table(['관측 상태','대상 수'],[[labels[k],str(scope_inventory['counts'][k])] for k in labels]),
            table(['대상과 위치','관측 상태','보존 기록'],rows,[45,25,30]),
            paragraph('이 표에 생략된 대상 '+str(scope_inventory['omitted']['targets'])+'개. 미검토의 중요성과 추가 조사 가치는 질문별 한계·종결 이유를 따릅니다.')]
        executive[2]['blocks'].extend(deepcopy(blocks[:2]+blocks[3:]))
        executive.append({**page('EX03','부록 자료 관측 범위',deepcopy(blocks)), 'section':'appendix'})
        practitioner[1]['blocks'].extend(blocks)
    if write_status and write_status['affected_question_count']:
        blocks=[paragraph('수정 저장 상태 · 아래 안내는 판단 스냅샷의 저장된 답을 변경하지 않습니다.')]
        blocks.extend(paragraph(n['message']) for n in write_status['notices'])
        if write_status['omitted_notice_count']:
            blocks.append(paragraph(f"그 밖의 질문 {write_status['omitted_notice_count']}개도 수정 저장에 실패하여 현재 저장된 답을 사용합니다."))
        for pages in (executive,practitioner):
            pages[0]['blocks'][0:0]=deepcopy(blocks)
    if s.get('indicators'):
        labels={'observed':'관측됨','suspicious':'의심','benign':'정상 설명','undetermined':'판단 보류','withdrawn':'철회됨'}
        rows=[[r['type']+' · '+r['canonical_value'], labels.get(r['status'],r['status'])+'\n'+r['summary'],
               '\n'.join(x['source_id']+x['pointer']+f" UTF-8 [{x['byte_start']},{x['byte_end']})" for x in r['source_refs']), joined(r['limitations'])] for r in s['indicators']]
        practitioner.append(page('A13','IOC와 관측 지표',[paragraph('관측과 사건상 판단을 구별한 로컬 목록입니다. 외부 평판은 별도 출처이며 이 목록은 자동 차단목록이 아닙니다.'),table(['지표','현재 해석','출처와 위치','한계'],rows,[25,25,30,20])]))
        active=[r for r in s['indicators'] if r['status']!='withdrawn']
        executive[0]['blocks'].append(paragraph(f'출처에 연결한 지표 {len(active)}개를 정리했습니다. 지표 수는 침해 건수가 아닙니다. 값·판단·원문 위치는 실무자용 IOC 부록에서 확인할 수 있습니다.'))
    from .workbench_views import blocks as workbench_blocks
    additions=workbench_blocks(s)
    for target in practitioner:
        target['blocks'].extend(additions.get(target['id'],[]))
    mapping=reader_labels(s)
    review_id=review.get('review_id')
    if review_id and review_id not in mapping:mapping[review_id]='별도 검토'
    pending=(independent_review or {}).get('pending_finding_ids') or []
    for i,finding_id in enumerate(pending,1):mapping.setdefault(finding_id,'미처분 검토 의견 '+str(i))
    if pending:
        executive.append({**page('EX05','부록 미처분 검토 의견 추적',[table(['본문 표기','원장 식별자'],[[mapping[x],x] for x in pending],[35,65])]),'section':'appendix'})
    for item in executive:
        if item.get('section')=='body':item['blocks']=project_body(item['blocks'],mapping)
    def view(reader,pages):return dict(projection_version=PROJECTION_VERSION,schema_version='forsic-report-view-1',reader=reader,title='디지털 포렌식 조사 보고서',data_mode=s['meta']['data_mode'],report_id=s['meta']['report_id'],snapshot_revision=s['meta']['snapshot_id'],security=s['meta']['classification'],pages=pages)
    return {'executive':view('executive',executive),'practitioner':view('practitioner',practitioner)}


def _template_appendices(pages):
    """Place complete existing details under the retained template's A–G headings."""
    groups=(
        ('A','주요 판단 축별 상세',('IR01','EX01','A04','A06','A09')),
        ('B','근거·출처·실행 영수증',('EX02','A05','A13')),
        ('C','시각 해석과 불확실성',('A03','A07')),
        ('D','개인정보 및 외부 영향 상세',('A08',)),
        ('E','검사·도구·처리 범위',('A02','A10','EX03')),
        ('F','보고 중 질문·추가 조사·검토',('A01','A11','EX04','EX05')),
        ('G','전달 목록·승인·정정 이력',('A12',)),
    )
    remaining={p['id']:p for p in pages};result=[]
    canonical={}
    def append_section(blocks,item):
        # Keep the old section identity reachable and every detail unabridged.
        blocks.append({'kind':'heading','level':2,'id':item['id'],
                       'text':item['id']+' · '+item['title'].removeprefix('부록 ')})
        for original in item['blocks']:
            block=deepcopy(original)
            if block['kind']=='table':
                # Only byte-equivalent table content is shared. Differing
                # qualifications, quotes, columns or provenance stay distinct.
                identity=json.dumps(block,ensure_ascii=False,sort_keys=True)
                if identity in canonical:
                    blocks.append(paragraph('동일한 상세 표: '+canonical[identity]+' 참조.'))
                    continue
                key='상세 표 '+str(len(canonical)+1)
                canonical[identity]=key
                if block['rows'] and block['rows'][0][0]=='기록 없음' and all(
                    not str(cell).strip() for row in block['rows'] for cell in row[1:]
                ) and all(row[0]=='기록 없음' for row in block['rows']):
                    blocks.append({**paragraph(' / '.join(block['headers'])+': 별도로 작성된 기록 없음. 자료·행위의 부재를 뜻하지 않습니다.'),'id':key})
                    continue
                block['id']=key
            if block.get('kind')=='paragraph' and not block.get('text','').strip():continue
            blocks.append(block)
    for letter,title,keys in groups:
        blocks=[]
        for key in keys:
            if key in remaining:append_section(blocks,remaining.pop(key))
        if blocks:result.append({'id':'appendix-'+letter,'title':'부록 '+letter+'. '+title,
                                 'section':'appendix','blocks':blocks})
    # New detail sections remain visible even when a future projection adds one.
    if remaining and not result:
        result.append({'id':'appendix-G','title':'부록 G. 전달 목록·승인·정정 이력',
                       'section':'appendix','blocks':[]})
    for item in remaining.values():append_section(result[-1]['blocks'],item)
    return result


def compile_views(s,review=None,write_status=None,*,independent_review=None,scope_inventory=None):
    """One reader document; legacy builders supply lossless technical appendices."""
    from .reader_projection import project_integrated, report_slot_appendix, reference_appendix, cite_pages, PROJECTION_VERSION
    from .report_slots import project_report_slots
    from .narrative import project_body as narrative_body
    legacy=_appendix_views(s,review,write_status,independent_review=independent_review,
                           scope_inventory=scope_inventory)
    projection=project_report_slots(s)
    pages=narrative_body(s)
    if pages is None:
        pages=project_integrated(s,projection)
        notices=[block for block in pages[0]['blocks'] if block.get('label')=='보고서 작성 상태']
        notice={'kind':'callout','label':'기술 기록 스냅샷 · 독자용 본문 미작성',
                'text':'현재 판단과 보존 근거를 대조하기 위한 기술 기록입니다. 독자용 자연어 본문은 아직 작성되지 않았습니다. 자료 부재나 침해 부재를 뜻하지 않습니다.'}
        if notices:notices[0].update(notice)
        else:pages[0]['blocks'].insert(0,notice)
    appendices=[report_slot_appendix(projection)] if any(slot['entries'] for slot in projection['slots']) else []
    # Keep the exact answer, citation labels and complete technical details in
    # this same document. Existing immutable bundles are never rewritten.
    appendices.extend(deepcopy(p) for p in legacy['executive']['pages'] if p.get('section')=='appendix' and p['id']!='EX02')
    for original in legacy['practitioner']['pages']:
        item=deepcopy(original);item['section']='appendix'
        item['title']='부록 '+item['title']
        appendices.append(item)
    pages.extend(_template_appendices(appendices))
    pages=cite_pages(pages,s,projection)
    pages.append(reference_appendix(s,projection))
    return {'integrated':dict(projection_version=PROJECTION_VERSION,
        schema_version='forsic-report-view-2',reader='integrated',
        title='정보시스템 침해사고 조사 결과',data_mode=s['meta']['data_mode'],
        report_id=s['meta']['report_id'],snapshot_revision=s['meta']['snapshot_id'],
        security=s['meta']['classification'],pages=pages)}


def redact_view(value,terms):
    if isinstance(value,str):
        for term in terms:value=value.replace(term,'[비공개]')
        return value
    if isinstance(value,list):return [redact_view(v,terms) for v in value]
    if isinstance(value,dict):return {k:redact_view(v,terms) for k,v in value.items()}
    return value


def selected_report_state(s, question_ids):
    """Derived selection at the same cutoff; no new state owner or source copy."""
    from .host import selected_question_records, find_refs
    records,selected=selected_question_records(s,question_ids)
    result=deepcopy(s)
    result.update(deepcopy(records))
    result['actions']=[a for a in s['actions'] if any(a.get('id','').startswith('ACT-'+q+'-') for q in selected)]
    cited={r['id'] for _,r in find_refs(records) if r['kind']=='source'}
    result['indicators']=[r for r in s.get('indicators',[]) if any(x['source_id'] in cited for x in r['source_refs'])]
    cited.update(x['source_id'] for r in result['indicators'] for x in r['source_refs'])
    result['sources']=[source for source in s['sources'] if source['id'] in cited]
    # Legacy applicability belongs to the whole case and cannot select today's answers.
    result['requirements']=[]
    if 'report_narratives' in result:
        result['report_narratives']=[row for row in result['report_narratives']
                                    if row.get('question_ids')==sorted(selected)]
    result['status']['investigation']='supported_scope_closed' if all(
        q['work_state']=='scoped_closed' and not q.get('judgment_review_required') for q in result['questions']) else 'partial'
    result['status']['explicit_limitations']=s['status']['explicit_limitations'][:2]+[
        g['reason'] for g in result['gaps'] if g['impact'] in ('material','decision_critical') and g['disposition']!='resolved']
    result['coverage_summary']=f"선택 질문 {len(selected)}개 / 사건 질문 {len(s['questions'])}개. 출처 {len(cited)}개; 독립 증거 수·전체 조사 완료율이 아님."
    return result


def selected_report_identity(s, question_ids):
    value=selected_report_state(s,question_ids)
    value['meta'].pop('content_revision',None)
    value['meta'].pop('excluded_history_records',None)
    # Unrelated inventory growth is displayed as inventory, not a revised answer.
    value['coverage_summary']='Selected questions and their retained sources'
    return report_material(value)


def bundle(case,s,redact=(),review_id=None,question_ids=None,*,review_context=None,scope_inventory=None,report_scope=None):
    result=checked(s)
    from .write_status import projection as write_status_projection
    selected=selected_report_state(s,question_ids) if question_ids is not None else s
    from .narrative import status as narrative_status
    editorial=narrative_status(selected)
    editorial_status='authored_current' if editorial['ready'] else 'technical_partial'
    write_status=write_status_projection(case,selected)
    if any(not isinstance(x,str) or not x.strip() for x in redact):raise ValueError('Redaction terms must be nonempty literal strings')
    # Never mask shared identifiers / snapshot metadata: only copied block text.
    from ..review import review_projection
    review=review_projection(case,s['meta']['snapshot_id'],review_id,
                             lambda questions:review_material(case,s,questions))
    from .finalization import independent_review_projection
    independent=independent_review_projection(review_context,s)
    views=compile_views(selected,review,write_status['status'],independent_review=independent,scope_inventory=scope_inventory)
    protected={s['meta']['snapshot_id'],s['meta']['report_id']}|{r['id'] for key in ('questions','claims','gaps','missions','sources','observations') for r in s[key]}
    if any(term in item for term in redact for item in protected):raise ValueError('Masking cannot corrupt shared IDs')
    for v in views.values():
        for p in v['pages']:p['blocks']=redact_view(p['blocks'],redact)
    # Persist only the safe judgment snapshot before report-only originals are read.
    private=case.output/('report-state-'+s['meta']['snapshot_id']+'.json')
    try:
        with private.open('xb') as f:f.write(encoded(s))
    except FileExistsError:
        if private.read_bytes()!=encoded(s):raise ValueError('Immutable snapshot conflict')
    from ..sensitive_policy import report_appendix
    from .sensitive_appendix import append_sensitive, summary as sensitive_summary
    sensitive=report_appendix(case,[source['id'] for source in selected['sources']])
    local_only=bool(sensitive.get('contains_sensitive_originals'))
    append_sensitive(views['integrated'],sensitive)
    renderer_hash=digest({name:hashlib.sha256((Path(__file__).parent/name).read_bytes()).hexdigest() for name in ('views.py','workbench_views.py','render_views.py','word_export.py','write_status.py','delivery.py','reader_projection.py','reader_explanation.py','narrative.py','report_slots.py','report_research.py','gap_audit.py','report_style.json','templates/integrated_report.docx','evidence_export.py','sensitive_appendix.py')})
    # A crash leaves an unregistered hidden staging directory, not half a bundle.
    staging=Path(tempfile.mkdtemp(prefix='.report-stage-',dir=case.output))
    from .word_export import stage_inputs,FORMATS
    lazy_word=stage_inputs(staging,views['integrated'])
    files=[]
    for reader,v in views.items():
        p=staging/(reader+'.html');p.write_text(render_html(v),encoding='utf-8')
        files.append(dict(name=p.name,sha256=hashlib.sha256(p.read_bytes()).hexdigest(),bytes=p.stat().st_size))
    from .evidence_export import export_evidence
    if redact:
        evidence_export={'status':'not_exported_redacted','reason':'마스킹한 보고서에는 원문 발췌 묶음을 포함하지 않습니다.'}
    else:
        evidence_export=export_evidence(case,selected,staging/'selected-evidence.zip')
        files.append({k:evidence_export[k] for k in ('name','sha256','bytes')})
        evidence_export={**evidence_export,'status':'complete'}
    from .render_views import render_style_identity
    from .reader_projection import PROJECTION_VERSION
    manifest=dict(schema='forsic-report-bundle-1',snapshot_id=s['meta']['snapshot_id'],case_id=s['meta']['case_id'],data_mode=s['meta']['data_mode'],ledger_cutoff=s['meta']['ledger_cutoff'],status=s['status'],publication='internal_partial_snapshot',findings=[c['id'] for c in s['claims']],question_ids=[q['id'] for q in s['questions']],gaps=[g['id'] for g in s['gaps']],audit=result,files=files,redacted=bool(redact),masking_scope='literal terms in reader blocks only; not a complete credential detector',template_version=render_style_identity()['version'],style_profile=render_style_identity(),projection_version=PROJECTION_VERSION,evidence_export=evidence_export,layout_review={'status':'not_rendered','physical_page_count':None},renderer_version='forsic-report-view-host-2')
    from .report_slots import project_report_slots
    manifest.update(report_contract='integrated-v2',
                    document_formats=FORMATS,lazy_word=lazy_word,
                    editorial_status=editorial_status,
                    editorial={key:editorial[key] for key in ('basis_id','question_ids','revision','stale','missing_sections','semantic_approval')},
                    report_slots_sha256=digest(project_report_slots(selected)),
                    state_sha256=hashlib.sha256(encoded(s)).hexdigest(),renderer_sha256=renderer_hash,
                    content_revision=s['meta'].get('content_revision',''),
                    review={k:v for k,v in review.items() if k!='advice'},
                    write_status_cutoff=write_status['cutoff'], write_status=write_status['status'])
    if local_only:
        manifest.update(local_only=True,delivery_policy='local_only_sensitive_originals',
                        sensitive_appendix=sensitive_summary(sensitive),
                        masking_scope='literal terms in safe reader blocks only; local sensitive original appendix remains unmasked')
    if independent is not None:manifest['independent_review']=independent
    if scope_inventory is not None:manifest['scope_inventory']=redact_view(scope_inventory,redact)
    if question_ids is not None:
        manifest.update(question_ids=sorted(question_ids), findings=[c['id'] for c in selected['claims']],
                        gaps=[g['id'] for g in selected['gaps']],
                        selection_mode='selected_questions',
                        selected_content_revision=selected_report_identity(s,question_ids),
                        case_inventory={'questions':len(s['questions']),'investigation':s['status']['investigation']},
                        selected_scope_status=selected['status']['investigation'])
    options=encoded({'redact':list(redact),'review_id':review_id})
    (staging/'.render-options.json').write_bytes(options)
    manifest.update(manifest_identity='sha256-v1',
                    render_options_sha256=hashlib.sha256(options).hexdigest())
    if report_scope is not None:manifest['report_scope']=report_scope
    revision=digest(manifest)
    manifest['bundle_id']=revision
    target=case.output/('report-bundle-'+revision)
    (staging/'manifest.json').write_bytes(encoded(manifest))
    if current(case)['meta']['snapshot_id']!=s['meta']['snapshot_id']:raise ValueError('Case changed during render; staged files not registered')
    if write_status_projection(case,selected)['status']!=write_status['status']:
        raise ValueError('Question save status changed during render; staged files not registered')
    if independent_review_projection(review_context,s)!=independent:
        raise ValueError('Independent review changed during render; staged files not registered')
    try:os.rename(staging,target)
    except OSError as exc:
        if exc.errno not in (errno.EEXIST,errno.ENOTEMPTY):raise
        shutil.rmtree(staging)
        return listing(case,target,s['meta']['snapshot_id'])
    return listing(case,target,s['meta']['snapshot_id'])


def listing(case,path,snapshot_id,*,include_delivery=True):
    from .finalization import report_files
    from .report_reference import resolve_report
    manifest,_=report_files(case.output,path.name.removeprefix('report-bundle-'),case.config['case_id'],snapshot_id or None)
    snapshot_id=manifest['snapshot_id']
    saved=json.loads((case.output/('report-state-'+snapshot_id+'.json')).read_text())
    resolve_report(case,saved,manifest['bundle_id'])
    result=dict(bundle_id=manifest['bundle_id'],snapshot_id=snapshot_id,manifest=str(path/'manifest.json'),files=[str(path/f['name']) for f in manifest['files'] if f['name']!='selected-evidence.zip'],published=False,review=manifest.get('review',{'review_status':'not_reviewed'}))
    if manifest.get('editorial_status') in ('authored_current','technical_partial'):
        result.update(editorial_status=manifest['editorial_status'],editorial=deepcopy(manifest['editorial']))
    result['evidence_export']={**manifest.get('evidence_export',{'status':'not_available'})}
    if result['evidence_export'].get('status')=='complete':result['evidence_export']['path']=str(path/'selected-evidence.zip')
    if manifest.get('local_only'):
        result.update(local_only=True,delivery_policy=manifest['delivery_policy'],
                      sensitive_appendix=manifest['sensitive_appendix'])
    result['report_reference']={'bundle_id':manifest['bundle_id'],'snapshot_id':snapshot_id}
    result['actions']={'finalize':{'tool':'forsic_reporting',
        'arguments':{'action':'finalize','bundle_id':manifest['bundle_id']}}}
    # Redacted delivery text must follow the exact same literal masking policy
    # as the report. Do not return an unredacted side channel beside masked files.
    if include_delivery and not manifest.get('redacted'):
        from .delivery import delivery_view
        selected=[q for q in saved['questions'] if q['id'] in manifest['question_ids']]
        result['delivery']=delivery_view(saved,selected,scope_inventory=manifest.get('scope_inventory'))
    return result


def report_material(state):
    """Report content identity without native turn/projection bookkeeping."""
    value=deepcopy(state)
    # Adding the optional contract must not make an unchanged historical report
    # stale merely because the new projection supplies its empty default.
    for question in value.get('questions',[]):
        if not question.get('report_slots'):
            question.pop('report_slots',None)
        if not (question.get('reader_explanation') or {}).get('paragraphs'):
            question.pop('reader_explanation',None)
    if not value.get('report_narratives'):
        value.pop('report_narratives',None)
    immutable={s['id'] for s in value['sources']}|{value['scope']['id']}
    for name in ('snapshot_id','ledger_cutoff','generated_at'):
        value['meta'].pop(name,None)
    value['status'].pop('execution',None)
    # current() reserves its first limitation for the native execution status.
    value['status']['explicit_limitations']=value['status']['explicit_limitations'][1:]
    def material(item):
        if isinstance(item,dict):
            return {k:material(v) for k,v in item.items() if k!='version' or item.get('id') in immutable}
        if isinstance(item,list):return [material(v) for v in item]
        return item
    return digest(material(value))


def bundle_stale(case,manifest,state=None):
    sid=manifest.get('snapshot_id','')
    if not re.fullmatch('[a-f0-9]{64}',sid):raise ValueError('Invalid report snapshot')
    path=case.output/('report-state-'+sid+'.json')
    if path.is_symlink():raise ValueError('Invalid report state path')
    raw=path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=manifest.get('state_sha256'):
        raise ValueError('Report state changed')
    saved=json.loads(raw)
    if manifest.get('selection_mode') == 'selected_questions':
        if selected_report_identity(saved,manifest['question_ids']) != manifest['selected_content_revision']:
            raise ValueError('Selected report identity disagrees with its immutable snapshot')
        now=state if state is not None else current(case)
        return selected_report_identity(now,manifest['question_ids']) != manifest['selected_content_revision']
    return report_material(saved)!=report_material(state if state is not None else current(case))


def bundles(case):
    state=current(case);out=[]
    for p in sorted(case.output.glob('report-bundle-*')):
        if not p.is_dir() or p.is_symlink():continue
        try:
            m=json.loads((p/'manifest.json').read_text());listing(case,p,m['snapshot_id'],include_delivery=False)
            out.append({**m,'stale':bundle_stale(case,m,state)})
        except (ValueError,OSError,KeyError):continue
    return out


def artifact(case,bundle_id,name):
    if not re.fullmatch('[a-f0-9]{64}',bundle_id) or name not in ('integrated.html','integrated.docx','executive.html','executive.docx','practitioner.html','practitioner.docx','manifest.json','selected-evidence.zip'):raise ValueError('Invalid artifact')
    root=case.output/('report-bundle-'+bundle_id)
    if root.is_symlink():raise ValueError('Invalid bundle path')
    listing(case,root,'',include_delivery=False)
    path=root/name
    if path.is_symlink() or not path.is_file():raise ValueError('Invalid artifact path')
    if name!='manifest.json':
        manifest=json.loads((root/'manifest.json').read_text())
        if name not in {f['name'] for f in manifest['files']}:raise ValueError('Artifact is not registered')
    return path
