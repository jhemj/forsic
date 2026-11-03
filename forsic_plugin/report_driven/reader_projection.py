"""Lossless presentation of note-owned judgments, without semantic rewriting.

Body prose comes from selected atomic claims or the unchanged legacy answer.
Original answers remain available in an explicitly linked appendix. The renderer
never infers that a claim set captures every free-form answer qualification.
"""
from copy import deepcopy
import re

PROJECTION_VERSION = 'forsic-integrated-projection-6'


def evidence_references(state, projection=None):
    """Number exact retained identities; old versions never alias current records."""
    records={(kind,r['id'],r.get('version','')):r for field,kind in
             (('observations','observation'),('sources','source')) for r in state.get(field,[])}
    keys=list(sorted(records, key=lambda k:(k[0]!='observation',k[1],k[2])))
    def collect(value):
        if isinstance(value,dict):
            if value.get('kind') in ('source','observation') and value.get('id'):
                key=(value['kind'],value['id'],value.get('version',''))
                if key not in keys:keys.append(key)
            for child in value.values():collect(child)
        elif isinstance(value,list):
            for child in value:collect(child)
    # Historical writing is not a citation authority. Current narrative blocks
    # can cite the exact retained records above; frozen report-slot references
    # are separately preserved for audit without reviving old editorial refs.
    collect({key:value for key,value in state.items() if key!='report_narratives'})
    if projection is not None:collect(projection)
    return [dict(kind=kind,id=key,version=version,label='['+str(i)+']',
                 record=records.get((kind,key,version)))
            for i,(kind,key,version) in enumerate(keys,1)]


def reference_appendix(state, projection=None):
    """Reader endnotes replace the older E/S index, retaining native provenance."""
    references=evidence_references(state,projection)
    index={(r['kind'],r['id'],r['version']):r['label'] for r in references}
    blocks=[{'kind':'paragraph','text':
        '본문의 [번호]는 아래의 보존된 근거와 출처에 연결됩니다. 번호는 이 보고서 안에서만 사용합니다. '
        '원래 식별자와 버전은 그대로 보존합니다. UTF-8 좌표는 보존 도구 반환 필드 안의 위치이며 원본 디스크 오프셋이 아닙니다.'}]
    for ref in references:
        record=ref['record'];rows=[['원래 식별자',ref['kind']+' '+ref['id']],['보존 버전',ref['version'] or '버전 미기재']]
        if record is None:
            rows.append(['현재 대조 상태','이 버전의 원문은 현재 판단 스냅샷에 없습니다. 같은 식별자의 현재 버전으로 대체하지 않습니다.'])
        elif ref['kind']=='source':
            for key,label in (('label','자료'),('locator','보존 위치'),('content_sha256','SHA-256'),
                              ('source_generation_group','생성 계보'),('provenance','취득·실행 계보'),
                              ('source_role','출처 역할'),('coverage','실제 반환 범위')):
                if record.get(key):rows.append([label,str(record[key])])
        else:
            source=record.get('source_ref',{})
            source_label=index.get((source.get('kind','source'),source.get('id'),source.get('version','')),'')
            rows.append(['연결 출처',(source_label+'\n' if source_label else '')+source.get('id','')+'\n버전 '+source.get('version','')])
            for key,label in (('field_pointer','반환 필드'),('literal','보존 인용'),
                              ('canonical_sha256','인용 SHA-256'),('presented_view','제시 범위'),
                              ('coordinate_basis','좌표 기준'),('interpretation_limit','해석 한계')):
                if record.get(key):rows.append([label,str(record[key])])
            rows.append(['보존 필드 내 위치','UTF-8 ['+str(record.get('byte_start','?'))+','+str(record.get('byte_end','?'))+')'])
            if record.get('acquisition_context'):
                import json
                rows.append(['취득·읽기 범위',json.dumps(record['acquisition_context'],ensure_ascii=False,sort_keys=True)])
        blocks.extend([{'kind':'heading','level':2,'id':ref['label'],'text':ref['label']+' '+('관측 근거' if ref['kind']=='observation' else '출처')},
                       {'kind':'table','headers':['항목','보존된 근거와 추적 정보'],'rows':rows,'widths':[24,76],
                        'literal':True,'keep_together':True}])
    return {'id':'EX02','title':'근거 주석과 원문 추적','section':'appendix','blocks':blocks}


def cite_pages(pages, state, projection=None):
    """Attach structured author-selected citations without rewriting prose."""
    index={(r['kind'],r['id'],r['version']):r['label'] for r in evidence_references(state,projection)}
    result=deepcopy(pages)
    for page in result:
        for block in page['blocks']:
            cell_refs=block.get('cell_citation_refs')
            if cell_refs is not None:
                if block['kind']!='table' or len(cell_refs)!=len(block['rows']):
                    raise ValueError('Table citation row mismatch')
                for ri, (row, refs_row) in enumerate(zip(block['rows'],cell_refs)):
                    if len(refs_row)!=len(row):raise ValueError('Table citation column mismatch')
                    for ci, refs in enumerate(refs_row):
                        selected=[]
                        for ref in refs:
                            key=(ref.get('kind'),ref.get('id'),ref.get('version'))
                            if key not in index:raise ValueError('Unknown exact report citation')
                            if index[key] not in selected:selected.append(index[key])
                        if selected:
                            suffix=' '+', '.join(selected)
                            if block.get('cell_details') and (block['cell_details'][ri][ci] or
                                    block.get('region')=='metadata'):
                                block['cell_details'][ri][ci]+=suffix
                            else:row[ci]+=suffix
            if not block.get('citation_refs'):continue
            if block['kind'] not in ('paragraph','note','callout'):
                raise ValueError('Citations require a text block')
            selected=[]
            for ref in block['citation_refs']:
                key=(ref.get('kind'),ref.get('id'),ref.get('version'))
                if key not in index:raise ValueError('Unknown exact report citation')
                if index[key] not in selected:selected.append(index[key])
            if block.get('detail'):block['detail']+=' '+', '.join(selected)
            else:block['text']+=' '+', '.join(selected)
    return result


def labels(state):
    result = {ref['id']:ref['label'] for ref in evidence_references(state)
              if ref['record'] is not None}
    for collection, prefix in (('questions','질문'), ('claims','판단'), ('gaps','공백'),
                               ('hypotheses','대안'), ('timeline','시각'), ('missions','검사'),
                               ('tests','검사 기록'), ('assessments','평가'), ('actions','조치')):
        result.update({r['id']:f'{prefix} {i}' for i,r in enumerate(state.get(collection,[]),1) if r.get('id')})
    result[state['scope']['id']]='조사 범위'
    for question in state.get('questions',[]):
        for response in question.get('review_responses',[]):
            for field,prefix in (('review_id','검토'),('finding_id','검토 의견')):
                value=response.get(field)
                if value and value not in result:result[value]=prefix+' '+str(sum(v.startswith(prefix+' ') for v in result.values())+1)
    return result


def reader_text(value, mapping):
    """Replace only known record identities; preserve all surrounding prose."""
    if not mapping:return str(value)
    # Identities must be complete tokens: a short scope ID must not rewrite
    # ordinary words such as "microscope" or the status "scope_limited".
    pattern = r'(?<![A-Za-z0-9_-])(?:'+'|'.join(re.escape(k) for k in sorted(mapping,key=len,reverse=True))+r')(?![A-Za-z0-9_-])'
    return re.sub(pattern, lambda m:mapping[m.group()], str(value))


def project_body(value, mapping):
    if isinstance(value,str):return reader_text(value,mapping)
    if isinstance(value,list):return [project_body(v,mapping) for v in value]
    if isinstance(value,dict):
        if value.get('kind')=='table' and value.get('literal_columns'):
            result=deepcopy(value)
            result['rows']=[[cell if ci in value['literal_columns'] else project_body(cell,mapping)
                             for ci,cell in enumerate(row)] for row in value['rows']]
            return result
        # IDs and provenance are machine fields; visible block content is mapped.
        return {k:(v if k in ('id','row_ids','backrefs') else project_body(v,mapping)) for k,v in value.items()}
    return value


def project_executive(state, questions, claims, *, timeline, alternatives, actions, impact, scope_status):
    from .current_judgment import analysis_declaration, question_claims
    def p(text):return {'kind':'paragraph','text':str(text)}
    def h(text):return {'kind':'heading','text':str(text)}
    def t(headers,rows,widths):return {'kind':'table','headers':headers,'rows':rows,'widths':widths}
    def pg(key,title,blocks,section='body'):
        return {'id':key,'title':title,'subtitle':'동일 판단 스냅샷 · 내부 검토용','section':section,'blocks':blocks}
    mapping=labels(state)
    selected={c['id']:c for c in claims}
    lead=[p(state['scope']['approved_scope'])]
    answer_detail=[p('보고서 식별자 '+state['meta']['report_id']),p('판단 스냅샷 '+state['meta']['snapshot_id']),p('사건의 원래 질문 '+state['scope']['original_question'])]
    limitations=[]
    strength={'fact':'사실','interpretation':'해석','favored':'가장 유력','plausible':'가능한 설명','unrated':'강도 미평가'}
    def kind(c):
        return '판단 종류 '+strength.get(c['assertion_kind'],c['assertion_kind'])+' / '+strength.get(c.get('inference_strength','unrated'),c.get('inference_strength','unrated'))

    for index,q in enumerate(questions,1):
        own=[c for c in question_claims(state,q) if c['id'] in selected]
        lead.append(h(q['question']))
        if q.get('judgment_review_required'):lead.append(p('현재 답과 근거의 재검토 필요.'))
        # Equal prose is an aggregate answer, regardless of claim count. Keep
        # that exact answer visible rather than manufacturing a short summary.
        atomic=[c for c in own if c['status']!='candidate' and c['statement']!=q['answer']]
        if atomic:
            for c in atomic:
                lead.append(p(c['statement']))
                lead.append(p(kind(c)))
                lead.append(p('판단 적용 범위 '+c['scope']))
                lead.extend(p('한계 '+x) for x in c.get('limitations',[]))
                lead.extend(p('가정 '+x) for x in c.get('assumptions',[]))
                basis=[mapping.get(r['id'],r['id']) for r in c['source_refs']]
                if basis:lead.append(p('근거 '+', '.join(basis)))
        else:
            lead.append(p(q['answer']))
            basis=list(dict.fromkeys(mapping.get(r['id'],r['id']) for c in own for r in c['source_refs']))
            if basis:lead.append(p('근거 '+', '.join(basis)))
        lead.append(p('판단 범위 '+q['scope']))
        work_labels={'open':'검토할 질문','active':'조사 중','held':'보류','scoped_closed':'해당 범위 종결','blocked_internal':'도구·내부 제약','blocked_external':'외부 자료 대기','budget_deferred':'예산으로 보류'}
        lead.append(p('조사 상태 '+work_labels.get(q['work_state'],q['work_state'])))
        explanation=q.get('reader_explanation') or {}
        readable=explanation.get('status') in ('current','provisional') and explanation.get('paragraphs')
        answer_detail.extend([h(explanation.get('title') if readable and explanation.get('title') else '질문 '+str(index)+' '+q['question']),
                              p('기록 식별자 '+q['id'])])
        if readable:
            if explanation['status']=='provisional':answer_detail.append(p('선택된 근거 판단에 결속되지 않은 잠정 설명입니다.'))
            for item in explanation['paragraphs']:
                answer_detail.append({**p(item['text']),'citation_refs':deepcopy([*item.get('source_refs',[]),*item.get('counterevidence_refs',[])])})
            answer_detail.extend(p('경쟁 설명: '+value) for value in explanation.get('counterarguments',[]))
            answer_detail.extend(p('한계: '+value) for value in explanation.get('limitations',[]))
            if '\n\n'.join(item['text'] for item in explanation['paragraphs'])!=q['answer']:
                answer_detail.append(p('저장된 원래 답과 조사 상태는 조사 질문과 현재 답(A01)에서 대조합니다.'))
        else:
            answer_detail.append(p('저장된 현재 답과 조사 상태는 조사 질문과 현재 답(A01)에서 대조합니다.'))
        if q.get('closure_rationale'):limitations.append(p('종결 범위와 이유 '+q['closure_rationale']))
        if q.get('deferred_reason'):limitations.append(p('보류 이유 '+q['deferred_reason']))
        if q.get('alternatives'):limitations.extend(p(x) for x in q['alternatives'])
        for c in own:
            limitations.append(h('판단의 근거와 제한 '+mapping[c['id']]))
            if c.get('reasoning_summary'):limitations.append(p(c['reasoning_summary']))
            if c not in atomic:
                limitations.append(p(kind(c)))
                limitations.append(p('판단 적용 범위 '+c['scope']))
                limitations.extend(p('한계 '+x) for x in c.get('limitations',[]))
                limitations.extend(p('가정 '+x) for x in c.get('assumptions',[]))
            undeclared=analysis_declaration(c)['undeclared_analysis_fields']
            if undeclared:limitations.append(p('반론·가정·한계·판단 강도 중 명시적으로 검토하지 않은 항목이 있습니다. 부록의 분석 선언을 참조하세요.'))
            for ref in c.get('counterevidence_refs',[]):
                observation=next((o for o in state['observations'] if o['id']==ref['id']),None)
                source=next((s for s in state['sources'] if s['id']==ref['id']),None)
                detail=('인용 전문은 근거 부록 참조. 해석 한계 '+observation['interpretation_limit']) if observation else (source['label']+' · 반환 범위와 인용은 근거 부록 참조.') if source else mapping.get(ref['id'],ref['id'])
                limitations.append(p('반론 근거 '+mapping.get(ref['id'],ref['id'])+' '+detail))
        answer_detail.extend([p('범위 '+q['scope']),p('분석 선언 '+str({c['id']:analysis_declaration(c) for c in own}))])
        for c in own:
            if c['status']=='candidate' and c['statement']!=q['answer']:
                answer_detail.extend([h('미채택 해석 후보 '+c['id']),p(c['statement'])])
    assigned={r['id'] for q in questions for r in q['claim_refs']}|{'F-'+q['id'][2:] for q in questions if not q['claim_refs']}
    for c in claims:
        if c['id'] in assigned:continue
        lead.extend([h(c['title']),p(c['statement']),p(kind(c)),p('판단 적용 범위 '+c['scope'])])
        lead.extend(p('한계 '+x) for x in c.get('limitations',[]))
        lead.extend(p('가정 '+x) for x in c.get('assumptions',[]))
        basis=[mapping.get(r['id'],r['id']) for r in c['source_refs']]
        if basis:lead.append(p('근거 '+', '.join(basis)))
        if c.get('reasoning_summary'):limitations.append(p(c['reasoning_summary']))
        for ref in c.get('counterevidence_refs',[]):
            observation=next((o for o in state['observations'] if o['id']==ref['id']),None)
            limitations.append(p('반론 근거 '+mapping.get(ref['id'],ref['id'])+(' 해석 한계 '+observation['interpretation_limit'] if observation else ' · 인용 전문은 근거 부록 참조.')))
        answer_detail.extend([h(c['id']+' '+c['title']),p(c['statement']),p('분석 선언 '+str(analysis_declaration(c)))])
    if any(c['statement']!=q['answer'] for q in questions for c in claims if c['id'] in {r['id'] for r in q['claim_refs']}):
        lead.append(p('개별 판단과 그 한계를 본문에 제시. 저장된 종합 답의 전문과 추가 설명은 부록 현재 답과 추적 정보 참조.'))
    lead.extend([h('업무 영향'),deepcopy(impact)])
    boundaries=[h('검토 범위와 중요한 한계')]
    if state['scope']['excluded_scope']:boundaries.append(p('제외 범위 '+state['scope']['excluded_scope']))
    boundaries.extend(p(x) for x in state['status']['explicit_limitations'])
    boundaries.extend(limitations)
    if state['timeline']:boundaries.extend([h('시각과 해석 범위'),deepcopy(timeline)])
    hypotheses=[hyp for hyp in state['hypotheses'] if hyp['id'] in {r['id'] for q in questions for r in q['hypothesis_refs']}]
    if hypotheses:
        hypothesis_labels={'candidate':'아직 평가하지 않은 설명 후보','under_review':'검토 중','supported_for_scope':'해당 범위에서 지지','refuted_for_scope':'해당 범위에서 반박','undetermined':'판단 유보'}
        rows=[[hyp['explanation'],hypothesis_labels[hyp['status']]+'\n'+'\n'.join(hyp['assumptions'])+'\n반론 근거 '+(', '.join(mapping.get(r['id'],r['id']) for r in hyp['counterevidence_refs']) or '명시 없음'),hyp['prediction']+'\n'+hyp['compatibility']] for hyp in hypotheses]
        boundaries.extend([h('경쟁 설명'),t(['설명','평가와 반론','다음 판별'],rows,[32,34,34])])
    remaining=[]
    next_action_labels={'evaluate_result':'보존된 결과 대조','read_source':'원문 확인','design_test':'판별 검사 설계','repair_internal':'내부 도구 제약 해소','request_external_input':'외부 자료 확인','editorial':'보고서 내용 대조'}
    for gap in state['gaps']:
        if gap['disposition'] in ('resolved','not_applicable'):continue
        # Original obligations remain in the audit view; they may describe
        # failures that a later current reason has already narrowed or resolved.
        remaining.extend([h('남은 확인 사항'),p(gap['reason']),p('다음 확인 '+next_action_labels.get(gap['feasible_next_action'],gap['feasible_next_action']))])
        remaining.extend(p('재개 조건 '+x) for x in gap['reopen_conditions'])
    for q in questions:
        remaining.extend(p('다음 검사 '+x) for x in q.get('next_checks',[]))
        remaining.extend(p('재개 조건 '+x) for x in q.get('reopen_conditions',[]))
    missions=[m for m in state['missions'] if m['state']!='completed']
    for m in missions:
        remaining.extend([h(m['target_proposition']),p(m['why_it_matters'])])
        remaining.extend(p(x) for x in (m['support_rule'],m['refute_rule'],m['inconclusive_rule']) if x)
    performed=[a for a in state['actions'] if a['action_kind']=='performed' and a['execution_receipt']]
    if performed:remaining.extend([h('확인된 수행 조치'),deepcopy(actions)])
    if not remaining:remaining.append(p('추가 검사 계획이 명시되지 않았습니다. 확인 범위와 현재 답의 한계를 함께 적용합니다.'))
    remaining.append(p(scope_status))
    reference_page=reference_appendix(state)
    id_rows=[[value,key] for key,value in mapping.items() if not value.startswith('[')]
    if id_rows:reference_page['blocks'].append(t(['본문 표기','원장 식별자'],id_rows,[30,70]))
    pages=[pg('E01','현재 판단과 확인 범위',lead),pg('E02','판단의 근거와 중요한 한계',boundaries),pg('E03','남은 조사와 재개 조건',remaining),
           pg('EX01','부록 현재 답과 추적 정보',answer_detail,'appendix'),reference_page]
    return pages


def project_integrated(state, projection):
    """Place authored judgments in the report template without inventing synthesis."""
    mapping=labels(state)
    reference_index={(r['kind'],r['id'],r['version']):r['label'] for r in evidence_references(state,projection)}
    slots={slot['slot']:slot for slot in projection['slots']}
    status={'confirmed':'확인','inferred':'추정','not_confirmed':'직접 확인되지 않음',
            'unavailable':'판단 자료 부족','not_applicable':'해당 없음'}
    missing='보고용 판단 선언 미작성'
    missing_notice=missing+'. 자료 부재나 침해 부재를 뜻하지 않습니다. 기존 답과 근거는 부록 참조.'
    def p(value):return {'kind':'paragraph','text':str(value)}
    def h(value,level=1):return {'kind':'heading','text':str(value),'level':level}
    def table(headers,rows,variant):
        return {'kind':'table','headers':headers,'rows':rows,
                'widths':[100/len(headers)]*len(headers),'variant':variant}
    def page(key,title,blocks,kicker):
        return dict(id=key,title=title,kicker=kicker,subtitle='',section='body',blocks=blocks)
    def state_label(entry):
        declared=status[entry['state']]
        return '재검토 필요 (이전 선언: '+declared+')' if entry.get('needs_recheck') else declared
    entry_labels={id(entry):'보고 항목 '+str(i) for i,entry in enumerate(
        (entry for slot in projection['slots'] for entry in slot['entries']),1)}
    def readable_ref(reference):
        key=reference['id'] if isinstance(reference,dict) else reference
        if isinstance(reference,dict) and reference.get('kind') in ('source','observation') and 'version' in reference:
            return reference_index.get((reference['kind'],key,reference['version']),'이전 참조 (부록 대조)')
        return mapping.get(key,'이전 참조 (부록 대조)')
    def references(entry):
        refs=entry.get('evidence_refs',[])
        selected=', '.join(dict.fromkeys(readable_ref(x) for x in refs))
        return entry_labels[id(entry)]+ ('\n'+selected if selected else '')
    def qualifications(entry):
        lines=[]
        if entry.get('scope'):lines.append('적용 범위: '+entry['scope'])
        if entry.get('basis'):lines.append('판단 근거: '+entry['basis'])
        for field,label in (('limitations','한계'),('assumptions','가정')):
            lines.extend(label+': '+str(value) for value in entry.get(field,[]))
        if entry.get('counterevidence_refs'):
            lines.append('반론 근거: '+', '.join(dict.fromkeys(readable_ref(x) for x in entry['counterevidence_refs'])))
        if entry.get('needs_recheck'):
            lines.append('현재 판단 재검토 필요.')
            lines.extend('재검토 이유: '+str(value) for value in entry.get('recheck_reasons',[]))
        return '\n'.join(lines)
    def entry_text(entry):
        return '\n'.join(x for x in (entry['brief'],qualifications(entry)) if x)
    def slot_text(key):
        slot=slots[key]
        return missing if slot['missing'] else '\n\n'.join(
            state_label(e)+' · '+entry_text(e)+'\n근거: '+references(e) for e in slot['entries'])
    axes=(('compromise_time','사고 시점'),('entry_route','최초 진입'),
          ('attacker_actions','주요 행위'),('personal_data_presence','개인정보'),
          ('external_disclosure','외부 영향'))
    summary=[]
    for key,label in axes:
        slot=slots[key]
        if slot['missing']:summary.append([label,missing,'선언 미작성','—'])
        else:summary.extend([label,entry_text(e),state_label(e),references(e)] for e in slot['entries'])
    meta=state.get('meta',{});scope=state['scope']
    identity='\n'.join(str(meta[k]) for k in ('case_id','report_id') if meta.get(k)) or '사건 식별정보 미기재'
    system=[str(x) for x in scope.get('evidence_labels',[])]
    if meta.get('generated_at'):system.append('판단 기준시점: '+str(meta['generated_at']))
    if scope.get('display_timezone'):system.append('표시 시간대: '+str(scope['display_timezone']))
    authored=[str(meta[k]) for k in ('prepared_by','classification') if meta.get(k)]
    if state.get('status',{}).get('report'):authored.append('문서 상태: '+str(state['status']['report']))
    first=[table(['사건관리번호 / 대상','시스템·조사기준','작성 및 문서상태'],
                 [[identity,'\n'.join(system) or '조사기준 미기재','\n'.join(authored) or '작성정보 미기재']],'metadata'),
           h('Ⅰ. 조사 결과 종합'),
           h('1. 주요 조사결과',2),
           table(['구분','핵심 확인내용','판단','근거'],summary,'summary'),
           h('2. 사건 전개 개요',2),
           table(['유입 징후','시스템 내 행위','정보 접촉·전송'],
                 [[slot_text('entry_route'),slot_text('attacker_actions'),slot_text('personal_data_presence')+'\n\n'+slot_text('external_disclosure')]],'flow')]
    if any(slot['missing'] for slot in projection['slots']):
        first.insert(1,{'kind':'callout','label':'보고서 작성 상태','text':missing_notice})
    impact=[]
    # The existing disclosure declaration combines exposure and export. Keep it
    # combined; neither keywords nor layout can create two independent findings.
    for key,label in (('personal_data_presence','개인정보 보유'),('external_disclosure','외부 노출·반출')):
        slot=slots[key]
        if slot['missing']:impact.append([label,missing,'선언 미작성'])
        else:impact.extend([label,entry_text(e),state_label(e)+'\n'+references(e)] for e in slot['entries'])
    evidence=[]
    observations={o['id']:o for o in state['observations']}
    sources={s['id']:s for s in state['sources']}
    for entry in slots['decisive_evidence']['entries']:
        material=[];limits=[]
        for reference in entry.get('evidence_refs',[]):
            key=reference['id'];label=readable_ref(reference)
            if key in observations:
                observation=observations[key]
                if reference.get('version',observation.get('version'))!=observation.get('version'):
                    material.append(label+' · 선택한 이전 버전은 현재 스냅샷에 없음. 근거 주석 참조.')
                    continue
                material.append(label+' · '+observation['literal'])
                limits.append(label+' · '+observation['interpretation_limit'])
            elif key in sources:
                if reference.get('version',sources[key].get('version'))!=sources[key].get('version'):
                    material.append(label+' · 선택한 이전 버전은 현재 스냅샷에 없음. 근거 주석 참조.')
                    continue
                material.append(label+' · '+sources[key]['label']+'\n보존 범위: '+sources[key]['coverage'])
        facts=[c['statement'] for c in entry.get('claim_details',[]) if c.get('assertion_kind')=='fact']
        direct=('작성된 사실 판단 ('+state_label(entry)+'):\n'+'\n'.join(facts)) if facts else '직접 입증 범위 별도 선언 없음.'
        # A chosen brief may be an interpretation; retain its declared strength
        # even under the template's direct-fact column.
        direct+='\n보고용 판단 ('+state_label(entry)+'): '+entry_text(entry)
        evidence.append([references(entry),'\n'.join(material) or '선택 관측자료 없음',direct,
                         '\n'.join(limits) or '자료별 미입증 범위 별도 선언 없음.'])
    if not evidence:evidence.append(['—',missing,'직접 입증 범위 선언 미작성','미입증 범위 선언 미작성'])
    second=[h('Ⅲ. 개인정보 및 외부 영향'),
            table(['구분','확인된 내용','판단·근거'],impact,'impact'),
            h('Ⅳ. 판단의 핵심 근거'),
            table(['근거','관측자료','직접 입증하는 사실','그 자료만으로는 미입증'],evidence,'evidence'),
            p('원문 증거의 위치와 해석 범위는 부록에 연결됩니다. 인용의 존재만으로 인과관계·비인가성·외부 유출이 확정되지는 않습니다.')]
    next(block for block in second if block.get('variant')=='evidence')['literal_columns']=[1]
    third=[h('Ⅴ. 주요 사건 경과')]
    # The authored chronology remains distinct from observations on the clock.
    third.append(p(slot_text('incident_timeline')))
    known=[];unknown=[]
    for event in state['timeline']:
        when,explanation,limit=time_display_row(event)
        if (event.get('temporal') or {}).get('observed_or_inferred')=='observed':when='관측 시각 · '+when
        raw='\n'.join(event.get('raw_values',[]))
        if raw:when+='\n원문: '+raw
        model=event.get('temporal') or {}
        interpretation=[limit]
        for key,label in (('confidence_basis','시각 판단 근거'),('clock_domain','시계'),
                          ('timezone_basis','시간대 근거'),('year_basis','연도 근거'),
                          ('timestamp_role','시각 역할')):
            value=model.get(key,event.get(key))
            if value:interpretation.append(label+': '+str(value))
        for candidate in model.get('candidates',[]):
            interpretation.append('시각 후보: '+str(candidate.get('earliest') or '하한 미상')+' ~ '+str(candidate.get('latest') or '상한 미상')+' / '+candidate.get('basis',''))
        context=event.get('temporal_context') or {}
        for relation in context.get('temporal_relations',[]):
            interpretation.append('시간 관계: '+relation['relation']+' → '+relation.get('scoped_target_event_ref',relation.get('target_event_ref',''))+' / '+relation.get('reason',''))
        interpretation.extend('시간 충돌: '+item['reason'] for item in context.get('conflicting_constraints',[]))
        refs=[event['observation_ref']] if event.get('observation_ref') else []
        refs.extend(context.get('observation_refs',[]))
        refs.extend(event.get('claim_refs',[]))
        basis=', '.join(dict.fromkeys(readable_ref(ref) for ref in refs)) or '참조 미기재'
        row=[when,explanation,'\n'.join(x for x in interpretation if x),basis]
        (known if event.get('comparable') and event.get('normalized_values') else unknown).append(row)
    if known:third.append(table(['시각','기록상 사건','사건 해석','근거'],known,'timeline'))
    else:third.append(p('비교 가능한 시각 기록 미작성. 사건 부재를 뜻하지 않습니다.'))
    third.append(h('시각 미확정 사항',2))
    if unknown:third.append(table(['원시 시각 / 미확정','기록상 사건','사건 해석','근거'],unknown,'timeline'))
    else:third.append(p('별도로 기록된 시각 미확정 사항 없음.'))
    third.extend([h('판단 적용 범위',2),p(scope['approved_scope'])])
    if scope.get('excluded_scope'):third.append(p('제외 범위: '+scope['excluded_scope']))
    third.extend(p('한계: '+str(value)) for value in state.get('status',{}).get('explicit_limitations',[]))
    pages=[page('I01','정보시스템 침해사고 조사 결과',first,'보안 조사결과 / 통합보고서'),
           page('I02','정보 영향 및 핵심 근거',second,'통합보고서 / 정보 영향 및 핵심 근거'),
           page('I03','주요 사건 경과',third,'통합보고서 / 주요 사건 경과')]
    for item in pages:item['blocks']=project_body(item['blocks'],mapping)
    return pages


def report_slot_appendix(projection):
    """Trace each authored brief to its frozen claims without making them current."""
    blocks=[{'kind':'paragraph','text':
        '각 보고 항목은 작성 당시 질문·판단·근거 버전에 연결됩니다. 재검토가 필요한 선언의 이전 참조는 현재 유효한 근거로 승격하지 않습니다. 현재 원문·판단은 뒤의 근거 및 조사 상세 부록에서 대조합니다.'}]
    number=0
    def ref_lines(refs):
        return '\n'.join(r['kind']+' '+r['id']+'\n버전 '+r['version'] for r in refs) or '선택 없음'
    for slot in projection['slots']:
        for entry in slot['entries']:
            number+=1;label='보고 항목 '+str(number)
            state=('재검토 필요 · 이전 선언 ' if entry.get('needs_recheck') else '작성 당시 선언 ')+entry['state']
            rows=[['질문 / 판단 상태',entry['question_id']+'\n'+state],
                  ['독자용 문구',entry['brief']],['적용 범위',entry.get('scope','')],
                  ['판단 근거',entry.get('basis','') or '별도 문구 없음'],
                  ['가정 / 한계','\n'.join([*entry.get('assumptions',[]),*entry.get('limitations',[])]) or '별도 선언 없음']]
            for i,reference in enumerate(entry.get('claim_refs',[])):
                detail=(entry.get('claim_details') or [])
                claim=detail[i] if i<len(detail) else {}
                rows.append(['선택 판단 '+str(i+1),ref_lines([reference])+'\n'+claim.get('statement','')+
                             '\n판단 종류 '+claim.get('assertion_kind','미상')+' / '+claim.get('inference_strength','미상')+
                             '\n'+claim.get('reasoning_summary','')])
            rows.extend([['보존된 근거 참조',ref_lines(entry.get('evidence_refs',[]))],
                         ['보존된 반론 참조',ref_lines(entry.get('counterevidence_refs',[]))]])
            if entry.get('needs_recheck'):
                rows.append(['재검토 이유','\n'.join(entry.get('recheck_reasons',[]))])
            blocks.extend([{'kind':'heading','text':slot['label']},
                {'kind':'table','headers':['본문 연결','저장된 보고 선언'],
                 'rows':[[label,entry['brief']]],'widths':[24,76],'row_ids':[label]},
                {'kind':'table','headers':['항목','기록'],'rows':rows,'widths':[24,76]}])
    if not number:
        blocks.append({'kind':'paragraph','text':'보고용 판단 선언 미작성. 기존 질문의 답과 근거는 다음 부록에 보존되어 있습니다.'})
    return {'id':'IR01','title':'부록 보고 항목과 판단의 연결','subtitle':'작성 당시 선택과 현재 유효성을 구별',
            'section':'appendix','blocks':blocks}


def time_display_row(event):
    """Compact temporal display with explicit model uncertainty, no chronology guess."""
    model=event.get('temporal') or {}
    values=event.get('normalized_values') if event.get('comparable') else event.get('raw_values')
    when='\n'.join(values or []) or '시각 미확정'
    if model.get('observed_or_inferred')=='inferred':
        when='시각 추정 · '+str(model.get('earliest') or '하한 미상')+' ~ '+str(model.get('latest') or '상한 미상')
    when+='\n'+str(event.get('file_time_type') or event.get('time_kind') or '')
    detail=event['explanation']
    limits=event.get('limitation','')
    for assumption in model.get('assumptions',[]):limits+='\n가정: '+assumption
    if (event.get('temporal_context') or {}).get('temporal_requires_recheck'):limits+='\n시간 연결 재검토 필요'
    return [when,detail,limits]


def project_authored(state, fields):
    """Fixed v2 reader layout from validated editorial units, without inference.

    The same region IDs fill the retained Word controls. The model owns each
    statement and its uncertainty; the host supplies metadata and citations.
    """
    from .template_contract import ASSESSMENT_SCALES
    def assessment(e):
        return ({**deepcopy(e['assessment']), 'labels': list(ASSESSMENT_SCALES[e['node_id']]),
                 'tone': 'confidence' if e['node_id'] in ('major_findings.timing','major_findings.entry') else 'risk'}
                if 'assessment' in e else None)
    def entries(group):return fields[group]['entries']
    def entry(group, key=None):
        return next(e for e in entries(group) if e['node_id']==(key or group))
    def cells(e, reason_cell=0):
        return list(e['cells'])
    def details(e):
        values=list(e.get('cell_details', ['']*len(e['cells'])))
        if e.get('status')=='unavailable' and e.get('unavailable_reason'):
            reason=e['unavailable_reason']
            if not any(reason in value for value in [*values,*e['cells']]):
                values[-1]+='\n- 확인 범위: '+reason
        return values
    def refs(e, cell=None):
        if cell is not None and 'cell_citation_refs' in e:
            return deepcopy(e['cell_citation_refs'][cell])
        return deepcopy(e.get('citation_refs',[]))
    def para(region,value,kind='paragraph',citations=(),detail=''):
        return dict(kind=kind,region=region,text=value,citation_refs=list(citations),detail=detail,authored_hierarchy=kind=='paragraph')
    def heading(value,level=1):return dict(kind='heading',text=value,level=level)
    def table(region,headers,rows,refs_rows,detail_rows=None):
        return dict(kind='table',region=region,variant={'major_findings':'summary','event_overview':'flow',
            'information_impact':'impact','key_evidence':'evidence','timeline':'timeline'}.get(region,region),
            headers=headers,rows=rows,widths=[100/len(headers)]*len(headers),cell_citation_refs=refs_rows,cell_details=detail_rows or [['']*len(headers) for _ in rows],authored_hierarchy=True)
    def page(key,title,blocks,kicker):
        return dict(id=key,title=title,section='body',kicker=kicker,blocks=blocks)
    identity=entry('report_identity')
    metadata=table('metadata',['Hostname','대표 IP','이미지 채증 시점'],
                   [cells(identity,2)],[[refs(identity,i) for i in range(3)]
                       if 'cell_citation_refs' in identity else [refs(identity),[],[]]],[details(identity)])
    overall=entry('overall_judgment')
    summary=dict(kind='callout',region='overall_judgment',label='종합 판단',text=cells(overall)[0],detail=details(overall)[0],authored_hierarchy=True,citation_refs=refs(overall,0))
    if assessment(overall) is not None:summary['assessment']=assessment(overall)
    axes=(('timing','사고 시점'),('entry','최초 진입'),('activity','주요 행위'),
          ('personal_data','개인정보'),('external_effect','외부 영향'))
    findings=[];citations=[];finding_details=[];finding_assessments=[]
    for key,label in axes:
        e=entry('major_findings','major_findings.'+key);content,judgment=cells(e,1)
        findings.append([label,content,judgment,'']);finding_details.append(['',*details(e),''])
        finding_assessments.append(assessment(e))
        citations.append([[],refs(e,0),refs(e,1),[]] if 'cell_citation_refs' in e else [[],[],[],refs(e)])
    stages=[entry('event_overview','event_overview.'+key) for key in ('entry','activity','information_contact')]
    findings_table=table('major_findings',['구분','시점·요약','판단','근거'],findings,citations,finding_details)
    if any(a is not None for a in finding_assessments):findings_table['row_assessments']=finding_assessments
    first=[para('intro','관측·해석·미확인 범위를 구분한 조사 결과.','note'),metadata,
           heading('Ⅰ. 조사 결과 종합'),summary,heading('1. 주요 조사결과',2),
           findings_table,
           heading('2. 사건 전개 개요',2),
           table('event_overview',['유입 징후','시스템 내 행위','정보 접촉·전송'],
                 [[cells(e)[0] for e in stages]],[[refs(e,0) for e in stages]],[[details(e)[0] for e in stages]]),
           para('certainty_note','※ 확정: 해당 명제를 뒷받침하는 근거 확보 · 유력: 경쟁 설명보다 설명력 우세 · 추정: 근거와 전제에 따른 해석 · 미확인: 판단 근거 부족. 추정·유력에도 미확정 범위 병기.','note')]
    impact=[];citations=[];impact_details=[]
    for key,label in (('presence','개인정보 보유'),('exposure','외부 노출'),('transfer','외부 반출')):
        e=entry('information_impact','information_impact.'+key);content,judgment=cells(e)
        impact.append([label,content,judgment]);impact_details.append(['',*details(e)])
        citations.append([[],refs(e,0),refs(e,1)] if 'cell_citation_refs' in e else [[],[],refs(e)])
    evidence=[];evidence_refs=[];evidence_details=[]
    for i,e in enumerate(entries('key_evidence'),1):
        evidence.append(['근거 '+str(i),*cells(e)]);evidence_details.append(['',*details(e)])
        evidence_refs.append([[],*[refs(e,i) for i in range(3)]] if 'cell_citation_refs' in e else [refs(e),[],[],[]])
    interpretations=[entry('synthesis','synthesis.'+key) for key in ('likely','alternative')]
    synthesis=dict(kind='callout',region='synthesis',label='종합 해석',
                   text='가장 유력한 설명: '+cells(interpretations[0])[0]+'\n중요한 대안: '+cells(interpretations[1])[0],
                   detail='\n'.join(value for e in interpretations for value in details(e) if value),authored_hierarchy=True,
                   citation_refs=refs(interpretations[0],0)+refs(interpretations[1],0))
    second=[heading('Ⅲ. 개인정보 및 외부 영향'),
            table('information_impact',['구분','확인된 내용','판단·근거'],impact,citations,impact_details),
            heading('Ⅳ. 판단의 핵심 근거'),
            table('key_evidence',['근거','관측자료','직접 입증하는 사실','그 자료만으로는 미입증'],evidence,evidence_refs,evidence_details),
            heading('침해 양상에 관한 종합 판단',2),synthesis,
            para('evidence_note','근거별 보존 원문·좌표·해석 한계는 부록 참조.','note')]
    timeline=[];timeline_refs=[];timeline_details=[]
    for e in entries('timeline'):
        timeline.append([*cells(e),'']);timeline_details.append([*details(e),''])
        timeline_refs.append([*[refs(e,i) for i in range(3)],[]] if 'cell_citation_refs' in e else [[],[],[],refs(e)])
    undated=entry('undated_events');scope_entry=entry('scope')
    timeline_table=table('timeline',['시각','기록상 사건','사건 해석','근거'],timeline,timeline_refs,timeline_details)
    timeline_table['event_kinds']=[e.get('event_kind','unknown') for e in entries('timeline')]
    third=[heading('Ⅴ. 주요 사건 경과'),timeline_table,
           heading('시각 미확정 사항',2),dict(kind='callout',region='undated_events',
              label='타임라인에 임의 배치하지 않는 내용',text=cells(undated)[0],detail=details(undated)[0],authored_hierarchy=True,citation_refs=refs(undated,0)),
           heading('판단 적용 범위',2),para('scope',cells(scope_entry)[0],citations=refs(scope_entry,0),detail=details(scope_entry)[0]),
           para('time_note','사건·기록·파일 메타데이터 시각의 구분과 정규화 근거는 부록 참조.','note')]
    return [page('I01','정보시스템 침해사고 조사 결과',first,'보안 조사결과 / 통합보고서'),
            page('I02','정보 영향 및 핵심 근거',second,'통합보고서 / 본문 2쪽'),
            page('I03','주요 사건 경과',third,'통합보고서 / 본문 3쪽')]
