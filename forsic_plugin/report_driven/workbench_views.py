"""Human-readable views of optional, already selected forensic declarations."""
import json


def blocks(state):
    from .views import table, paragraph
    scopes=[];lineage=[];checks=[];changes=[];omissions=[]
    status={'complete_for_returned_measurement':'반환된 측정 범위 완료','partial':'부분 관측',
            'failed':'실패','unknown':'확인 불가','listing_only':'목록만 관측',
            'derived_coverage_unknown':'파생 출력 · 원본 검사 범위 미확인'}
    dispositions={'planned':'계획','investigated':'조사함','refuted':'반박함','scope_limited':'범위 한계로 남김'}
    reasons={
        'Selected source, time, scope, or explicit dependency changed.':'선택한 출처·시각·범위 또는 명시한 의존 근거가 변경됨',
        'Selected claim replaced or withdrawn':'선택한 주장이 대체되거나 철회됨',
        'Referenced record missing':'참조한 기록을 현재 확인할 수 없음',
        'Selected dependency version changed':'선택한 의존 근거의 버전이 변경됨',
        'Owning question proposition or scope changed':'현재 질문의 명제 또는 범위가 변경됨',
        "A selected dependency's retained basis changed; reassess its use in this answer.":'선택한 의존 관계의 보존 근거가 변경됨. 현재 답에 적용되는 범위를 재검토할 수 있음',
    }
    for question in state['questions']:
        value=question.get('forensic_context') or {};qid=question['id']
        for item in value.get('absence_scopes',[]):
            observed=[]
            for source in item['acquired']:
                actual=source['acquired_scope']
                observed.append(source['source_ref']['id']+' / '+status.get(source['measurement_status'],source['measurement_status'])+
                    '\n'+json.dumps(actual,ensure_ascii=False))
            scopes.append([qid+'\n'+item['declared_scope'], '\n'.join(observed),
                '선택 조건: '+item['query_or_decoder']+'\n제외: '+', '.join(item['excluded_scope'])+'\n한계: '+item['limitation']])
        for item in value.get('source_lineage',[]):
            lineage.append([qid+'\n'+item['source_ref']['id'],
                '관측된 부모: '+', '.join(x['id'] for x in item.get('observed_inputs',[]))+
                '\n분석가 선언: '+', '.join(x['id'] for x in item.get('analyst_declared_inputs',[])),
                '입력 계보: '+item['input_lineage']+'\n동일성 확인: '+item['identity_check_level']+
                '\n수집 세대: '+str(item.get('acquisition_generation',''))])
        for item in value.get('selected_checks',[]):
            checks.append([qid+'\n'+item['selected_link'],item['competing_explanation']+
                '\n판별 관측: '+item['discriminating_observation'],item['feasible_next_check']+
                '\n답을 바꿀 조건: '+item['expected_decision_change']+'\n'+dispositions.get(item['disposition'],item['disposition'])+' / '+item.get('reason','')])
        for item in value.get('change_impacts',[]):
            changes.append([qid,reasons.get(item['recheck_reason'],item['recheck_reason']),'재개 조건: '+', '.join(item.get('previously_declared_reopen_conditions',[]))])
        if value.get('partial'):
            omissions.append(qid+' 세부 생략: '+json.dumps(value.get('omitted_counts',{}),ensure_ascii=False)+
                ' / 의존 근거 추적 생략: '+str(value.get('omitted_dependency_count',0))+
                ' / 출처 메타데이터 표시 생략: '+str(value.get('omitted_source_metadata',0))+
                ' / 변경 영향 추적 생략: '+str(value.get('change_propagation',{}).get('omitted_changed_references',0)))
    result={}
    if scopes:result.setdefault('A02',[]).append(table(['부재 판단의 선언 범위','실제 관측 범위','검색·해석 조건과 한계'],scopes))
    if lineage:
        result.setdefault('A02',[]).extend([paragraph('같은 수집 원본의 파생 결과는 독립 정황 수로 자동 합산하지 않습니다. metadata 대조와 암호학적 동일성은 별개이며, 별도 수집도 의미적 독립성의 보증은 아닙니다.'),table(['질문과 출처','변환 계보','확인 수준'],lineage)])
    if checks:result.setdefault('A06',[]).append(table(['선택한 연결','경쟁 설명과 판별 관측','검사와 판단 변화'],checks))
    if changes:result.setdefault('A09',[]).append(table(['영향받은 질문','변경 이유','재검토 범위'],changes))
    if omissions:result.setdefault('A12',[]).append(paragraph('\n'.join(omissions)+'\n선언과 채택 근거는 같은 질문 노트에서 조회할 수 있습니다. 생략된 자동 의존 근거는 이 화면의 변경 추적에 포함되지 않습니다.'))
    return result
