"""Deterministic reader views. No new conclusions, model calls or network."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import tempfile
import os

from .host import encoded, digest, checked, current, review_material
from .render_views import render_html, render_docx


def paragraph(text):return {'kind':'paragraph','text':str(text)}
def table(headers, rows, widths=None):
    return {'kind':'table','headers':headers,'rows':rows or [['기록 없음']+['']*(len(headers)-1)],'widths':widths or [100/len(headers)]*len(headers)}
def refs(values):return ', '.join(r['id'] for r in values) or '없음'
def joined(values):return '\n'.join(values) or '기록 없음'
def page(key,title,blocks):return dict(id=key,title=title,subtitle='동일 판단 스냅샷 · 내부 검토용',blocks=blocks)


def compile_views(s,review=None):
    from .current_judgment import current_claims, current_judgment
    judgments={q['id']:current_judgment(s,q) for q in s['questions']}
    selected_claims=current_claims(s)
    scope_status='기록된 질문의 범위를 종결했습니다. 잔여 불확실성과 재개 조건을 참조하세요.' if s['questions'] and all(q['work_state']=='scoped_closed' and not q.get('judgment_review_required') for q in s['questions']) else '질문별 진행·종결·보류 및 남은 확인 범위는 현재 조사 상태를 참조하세요.'
    requirements={r['requirement_id']:r for r in s['requirements']}
    # The skill selects questions via the supplied catalog contract. No incident
    # topic, artifact name, IOC or forced investigation checklist lives here.
    chosen={r['id'] for r in requirements.get('REQ-CORE-ANSWER',{}).get('basis_refs',[]) if r['kind']=='question'}
    executive_questions=[q for q in s['questions'] if not chosen or q['id'] in chosen]
    chosen_claims={r['id'] for r in requirements.get('REQ-FINDINGS',{}).get('basis_refs',[]) if r['kind']=='claim'}
    explicit_selection=bool(chosen or chosen_claims)
    chosen_claims.update(r['id'] for q in executive_questions for r in q['claim_refs'])
    findings=[[c['id'],c['statement']+'\n'+('해석 후보' if c['status']=='candidate' else '근거 결속된 해석'),joined(c['limitations'])+'\n가정: '+joined(c.get('assumptions',[]))+'\n판단 종류: '+c['assertion_kind']+' / '+c.get('inference_strength','unrated')+'\n근거: '+refs(c['source_refs'])+'\n반론: '+refs(c['counterevidence_refs'])] for c in selected_claims if c['status'] not in ('retracted','superseded')]
    work_labels={'open':'검토할 질문','active':'조사 중','held':'보류','scoped_closed':'해당 범위 종결',
                 'blocked_internal':'도구·내부 제약','blocked_external':'외부 자료 대기','budget_deferred':'예산으로 보류'}
    def disposition(q):
        return work_labels.get(q['work_state'],q['work_state'])+'\n'+str(q.get('closure_rationale') or q.get('deferred_reason') or '')
    answers=table(['질문','현재 답과 범위','조사 상태와 남은 확인'],[[q['id']+' '+q['question'],judgments[q['id']]['answer']+'\n'+q['scope'],disposition(q)+'\n'+(', '.join(q['remaining_gap_ids']) or '남은 검사 기록 없음')+'\n재개: '+joined(q.get('reopen_conditions',[]))] for q in s['questions']],[25,45,30])
    gaps=table(['공백','판단에 미치는 한계','다음 확인 / 재개'],[[g['id']+'\n'+g['kind'],g['original_obligation']+'\n'+g['reason'],g['feasible_next_action']+'\n'+joined(g['reopen_conditions'])] for g in s['gaps'] if g['disposition']!='resolved'],[20,45,35])
    alternatives=table(['설명','평가 / 반론','다음 판별'],[[h['id']+' '+h['explanation'],h['status']+'\n'+refs(h['counterevidence_refs'])+'\n'+joined(h['assumptions']),h['prediction']+'\n'+h['compatibility']] for h in s['hypotheses'] if h['id'] in {r['id'] for q in s['questions'] for r in q['hypothesis_refs']}],[32,34,34])
    timeline=table(['시각과 종류','내용','한계'],[[joined(t['normalized_values'] or t['raw_values'])+'\n'+t['time_kind']+' '+str(t['file_time_type'] or ''),t['explanation'],t['limitation']] for t in s['timeline']],[28,36,36])
    # Historical recommendations stay in the ledger, but new reports do not
    # prescribe business decisions. Only evidenced performed actions appear.
    actions=table(['확인된 수행 조치','근거','실행 기록'],[[a['title'],refs(a['basis_refs']),a['execution_receipt']] for a in s['actions'] if a['action_kind']=='performed' and a['execution_receipt']],[34,40,26])
    missions=table(['미션 / 원래 의무','다음에 구별할 내용','조건 / 상태'],[[m['id']+'\n'+m['original_obligation_id'],m['target_proposition']+'\n'+m['why_it_matters'],m['inconclusive_rule']+'\n'+m['state']+' / '+m['budget']['authority']] for m in s['missions']],[25,40,35])
    status=table(['상태','현재 기록'],[[k,str(v)] for k,v in s['status'].items() if k!='explicit_limitations'],[35,65])
    limit=paragraph(joined(s['status']['explicit_limitations']))
    executive_answers=table(['경영진이 알아야 할 질문','현재까지의 답','확인 범위'],[[q['question'],q['answer'],q['scope']] for q in executive_questions],[28,48,24])
    executive_findings=table(['발견','판단 근거','중요한 제한'],[[c['id'],c['statement']+'\n'+('아직 평가하지 않은 해석 후보' if c['status']=='candidate' else '원문에 결속한 해석'),joined(c['limitations'])+'\n가정: '+joined(c.get('assumptions',[]))+'\n판단 종류: '+c['assertion_kind']+' / '+c.get('inference_strength','unrated')] for c in selected_claims if c['status'] not in ('retracted','superseded') and (not explicit_selection or c['id'] in chosen_claims)],[20,44,36])
    executive_gaps=table(['아직 모르는 것','확인하지 못한 이유','다음 확인'],[[g['original_obligation'],g['reason'],joined(g['reopen_conditions'])] for g in s['gaps'] if g['disposition']!='resolved'],[28,38,34])
    executive_missions=table(['다음 확인','왜 필요한가','어떤 결과면 구별되는가'],[[m['target_proposition'],m['why_it_matters'],joined([x for x in (m['support_rule'],m['refute_rule'],m['inconclusive_rule']) if x])] for m in s['missions'] if m['state']!='completed'],[28,30,42])
    impact=requirements.get('REQ-IMPACT',{})
    impact_block=paragraph('업무 영향은 아직 평가하지 않았습니다.') if impact.get('basis_refs')==[{'kind':'scope','id':s['scope']['id'],'version':s['scope']['version']}] else paragraph(impact.get('rationale') or '업무 영향은 아직 평가하지 않았습니다.')
    executive=[page('E01','경영진을 위한 조사 요약',[executive_answers,{'kind':'heading','text':'업무 영향과 판단 근거'},impact_block,executive_findings]),
               page('E02','사건 경과와 중요한 공백',([timeline] if s['timeline'] else [])+[alternatives,executive_gaps,limit]),
               page('E03','남은 조사와 확인할 자료',[actions,executive_missions,paragraph(scope_status),paragraph('기관의 검토자·배포 승인자는 아직 지정되지 않았습니다.')])]
    sources=table(['근거 / 생성 계보','출처와 보존 지문','실제 반환 범위'],[[x['id']+'\n'+x['source_generation_group'],x['label']+'\n'+x['locator']+'\nSHA-256 '+x['content_sha256'],x['coverage']] for x in s['sources']],[23,42,35])
    observations=table(['관측 / 원문 위치','정확히 제시된 문자열','해석 한계'],[[o['id']+'\n'+o['source_ref']['id']+o['field_pointer']+f"\nUTF-8 [{o['byte_start']},{o['byte_end']})",o['literal'],o['presented_view']+'\n'+o['interpretation_limit']] for o in s['observations']],[30,38,32])
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
        page('A12','근거 탐색과 배포 상태',[paragraph('정확한 source ID, 반환 필드와 span은 A02·A05에 있습니다. 동봉 manifest.json은 네 파일과 같은 판단 스냅샷의 지문을 제공합니다. 원시 증거 전문은 기본 배포물에 넣지 않습니다.'),status,paragraph('기관 승인·수신자 정책은 연결되지 않았습니다. 이 묶음은 내부 검토용이며 발행 승인본이 아닙니다.')])]
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
    if s.get('indicators'):
        labels={'observed':'관측됨','suspicious':'의심','benign':'정상 설명','undetermined':'판단 보류','withdrawn':'철회됨'}
        rows=[[r['type']+' · '+r['canonical_value'], labels.get(r['status'],r['status'])+'\n'+r['summary'],
               '\n'.join(x['source_id']+x['pointer']+f" UTF-8 [{x['byte_start']},{x['byte_end']})" for x in r['source_refs']), joined(r['limitations'])] for r in s['indicators']]
        practitioner.append(page('A13','IOC와 관측 지표',[paragraph('관측과 사건상 판단을 구별한 로컬 목록입니다. 외부 평판은 별도 출처이며 이 목록은 자동 차단목록이 아닙니다.'),table(['지표','현재 해석','출처와 위치','한계'],rows,[25,25,30,20])]))
        active=[r for r in s['indicators'] if r['status']!='withdrawn']
        executive[0]['blocks'].append(paragraph(f'출처에 연결한 지표 {len(active)}개를 정리했습니다. 지표 수는 침해 건수가 아닙니다. 값·판단·원문 위치는 실무자용 IOC 부록에서 확인할 수 있습니다.'))
    def view(reader,pages):return dict(schema_version='forsic-report-view-1',reader=reader,title='디지털 포렌식 조사 보고서',data_mode=s['meta']['data_mode'],report_id=s['meta']['report_id'],snapshot_revision=s['meta']['snapshot_id'],security=s['meta']['classification'],pages=pages)
    return {'executive':view('executive',executive),'practitioner':view('practitioner',practitioner)}


def redact_view(value,terms):
    if isinstance(value,str):
        for term in terms:value=value.replace(term,'[비공개]')
        return value
    if isinstance(value,list):return [redact_view(v,terms) for v in value]
    if isinstance(value,dict):return {k:redact_view(v,terms) for k,v in value.items()}
    return value


def bundle(case,s,redact=(),review_id=None):
    result=checked(s)
    if any(not isinstance(x,str) or not x.strip() for x in redact):raise ValueError('Redaction terms must be nonempty literal strings')
    # Never mask shared identifiers / snapshot metadata: only copied block text.
    from ..review import review_projection
    review=review_projection(case,s['meta']['snapshot_id'],review_id,
                             lambda questions:review_material(case,s,questions))
    views=compile_views(s,review)
    protected={s['meta']['snapshot_id'],s['meta']['report_id']}|{r['id'] for key in ('questions','claims','gaps','missions','sources','observations') for r in s[key]}
    if any(term in item for term in redact for item in protected):raise ValueError('Masking cannot corrupt shared IDs')
    for v in views.values():
        for p in v['pages']:p['blocks']=redact_view(p['blocks'],redact)
    renderer_hash=digest({name:hashlib.sha256((Path(__file__).parent/name).read_bytes()).hexdigest() for name in ('views.py','render_views.py')})
    revision=digest({'snapshot':s['meta']['snapshot_id'],'views':views,'renderer':renderer_hash,'review':review})
    target=case.output/('report-bundle-'+revision)
    if target.exists():return listing(case,target,s['meta']['snapshot_id'])
    # A crash leaves an unregistered hidden staging directory, not half a bundle.
    staging=Path(tempfile.mkdtemp(prefix='.report-stage-',dir=case.output))
    files=[]
    for reader,v in views.items():
        for ext in ('html','docx'):
            p=staging/(reader+'.'+ext)
            if ext=='html':p.write_text(render_html(v),encoding='utf-8')
            else:render_docx(v,p)
            files.append(dict(name=p.name,sha256=hashlib.sha256(p.read_bytes()).hexdigest(),bytes=p.stat().st_size))
    manifest=dict(schema='forsic-report-bundle-1',bundle_id=revision,snapshot_id=s['meta']['snapshot_id'],case_id=s['meta']['case_id'],data_mode=s['meta']['data_mode'],ledger_cutoff=s['meta']['ledger_cutoff'],status=s['status'],publication='internal_partial_snapshot',findings=[c['id'] for c in s['claims']],question_ids=[q['id'] for q in s['questions']],gaps=[g['id'] for g in s['gaps']],audit=result,files=files,redacted=bool(redact),masking_scope='literal terms in reader blocks only; not a complete credential detector',template_version='Forsic_ReportDriven_Kit_v1',renderer_version='forsic-report-view-host-1')
    manifest.update(state_sha256=hashlib.sha256(encoded(s)).hexdigest(),renderer_sha256=renderer_hash,
                    content_revision=s['meta'].get('content_revision',''),
                    review={k:v for k,v in review.items() if k!='advice'})
    (staging/'manifest.json').write_bytes(encoded(manifest))
    # Store private immutable state separately, not among downloadable defaults.
    private=case.output/('report-state-'+s['meta']['snapshot_id']+'.json')
    try:
        with private.open('xb') as f:f.write(encoded(s))
    except FileExistsError:
        if private.read_bytes()!=encoded(s):raise ValueError('Immutable snapshot conflict')
    if current(case)['meta']['snapshot_id']!=s['meta']['snapshot_id']:raise ValueError('Case changed during render; staged files not registered')
    try:os.rename(staging,target)
    except FileExistsError:
        return listing(case,target,s['meta']['snapshot_id'])
    return listing(case,target,s['meta']['snapshot_id'])


def listing(case,path,snapshot_id):
    manifest=json.loads((path/'manifest.json').read_text())
    for f in manifest['files']:
        p=path/f['name']
        if p.is_symlink() or hashlib.sha256(p.read_bytes()).hexdigest()!=f['sha256']:raise ValueError('Report artifact changed')
    return dict(bundle_id=manifest['bundle_id'],snapshot_id=snapshot_id,manifest=str(path/'manifest.json'),files=[str(path/f['name']) for f in manifest['files']],published=False,review=manifest.get('review',{'review_status':'not_reviewed'}))


def report_material(state):
    """Report content identity without native turn/projection bookkeeping."""
    value=deepcopy(state)
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
    return report_material(saved)!=report_material(state if state is not None else current(case))


def bundles(case):
    state=current(case);out=[]
    for p in sorted(case.output.glob('report-bundle-*')):
        if not p.is_dir() or p.is_symlink():continue
        try:
            m=json.loads((p/'manifest.json').read_text());listing(case,p,m['snapshot_id'])
            out.append({**m,'stale':bundle_stale(case,m,state)})
        except (ValueError,OSError,KeyError):continue
    return out


def artifact(case,bundle_id,name):
    if not re.fullmatch('[a-f0-9]{64}',bundle_id) or name not in ('executive.html','executive.docx','practitioner.html','practitioner.docx','manifest.json'):raise ValueError('Invalid artifact')
    root=case.output/('report-bundle-'+bundle_id)
    if root.is_symlink():raise ValueError('Invalid bundle path')
    listing(case,root,'')
    path=root/name
    if path.is_symlink():raise ValueError('Invalid artifact path')
    return path
