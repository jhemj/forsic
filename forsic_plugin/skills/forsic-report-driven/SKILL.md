---
name: forsic-report-driven
description: 보고서 요구와 현재 질문 답·근거·공백에서 다음 판별 미션을 선택하고 결과 평가 후 같은 스냅샷의 임원·실무자 HTML·Word를 만든다.
---
# 보고 요구에서 다음 판별로

Hermes의 현재 사건·일반 도구 루프를 사용한다. 별도 Controller, 큐, 요약 모델을 만들지 않는다.
스킬은 권한·범위·예산을 부여하지 않는다. 공개 설명은 목적·근거·결과·다음 확인만 전달한다.
보고에는 경영 의사결정·대응 권고를 작성하지 않는다. 사람에게 판단을 맡기며, 확인된 사실·
근거·불확실성·남은 조사만 제시한다. 아래 참고 문서의 권고 양식보다 이 사용자 지시가 우선한다.

## 실제 제품 도구

키트의 `report_state_read` 등은 원래 설계 명칭이다. 이 제품에서는 **forsic_reporting**의 action으로 연결했다.

- `state`: 현재 답의 짧은 미리보기·섹션 개수·snapshot_id. 전체 ReportState가 아니다. 기존 forsic_note는 검증 전 해석 후보로 읽는다.
- `gaps`: 공백 첫 페이지와 요구 적용성 요약. 개수는 규명률이 아니며 omitted_counts의 생략을 공백 없음으로 읽지 않는다.
- `requirement`: Requirement 계약으로 적용 이유와 basis_refs를 기록한다. REQ-CORE-ANSWER의 question refs는 임원용 질문 선택, REQ-FINDINGS의 claim refs는 대표 발견 선택, REQ-IMPACT의 근거와 rationale은 업무 영향 설명에 쓰인다. 선택하지 않은 중요한 공백·반론은 숨기지 않는다.
- `source`, source_id: 현재 사건의 보존된 도구 결과 재제시. 새 수집·독립 근거가 아니다.
- `gap`: payload는 Gap 계약. 질문 ref를 그대로 보존하고 내부 오류·미제시·미평가·외부 자료·편집 공백을 구분한다.
- `mission`: payload는 Mission 계약. 원래 의무 ID에는 **gap의 id**, question_ref/gap_ref에는 현재 버전을 사용한다. 보고 목표·정확 질문 범위·판별 조건·반론을 유지한다. state=draft, budget.authority=unallocated, 네 budget 값=null. 실제 도구·모델 예산을 추정하지 않는다.
- `assess`: 아래 평가 제안. 검증 가능한 원문 결속은 호스트가 확인하고 해석으로 기록한다. 의미 승인·원의무 자동 해결은 부여하지 않는다.
- `review`, snapshot_id: 현재 state의 답·근거를 기존 로컬 모델의 별도 문맥에서 검토한다. 필요하면 question_ids로 현재 질문을 선택한다. 선택 밖 질문은 미검토이며 원문을 잘라 보내지 않는다. 별도 legacy snapshot을 보내지 않는다. 반환된 review_id/input_snapshot_id/review_status는 조언의 대상·상태이지 의미 승인 증명이 아니다.
- `render`: 같은 스냅샷의 executive/practitioner HTML·Word와 manifest. 모델 호출 없음. 선택 인자 review_id로 검토 기록을 연결한다. 실제 답·근거가 바뀌면 옛 검토는 stale로 표시되며 부분 보고는 가능하다. 턴 변화만 있으면 같은 검토 자료를 재사용한다. redact에는 복사본에서 가릴 정확 문자열을 넣을 수 있으나 자격정보 검출의 완전성은 보장하지 않는다.
- `finalize`, bundle_id, snapshot_id: render로 만든 현재 묶음을 **전달용 최종본**으로 선택한다. 연결된 사건 Telegram 토픽의 보고서 전송 기능이 켜져 있으면 임원·실무자 HTML·Word 네 파일이 첨부 대기열에 들어간다. 원본 증거·근거 JSON 부록·초안은 보내지 않는다. 실제 전송 전에는 파일 내부의 자격정보·불필요한 개인정보를 확인하고 필요하면 redact 복사본을 다시 render한다. 이 선택은 조사 완결이나 기관 승인이 아니며 부분 보고의 한계는 그대로 남는다.
- `publish`: 기관 승인 adapter가 없어 차단 사유를 반환한다. 내부 부분 보고 저장은 가능하다.

### 큰 상태·원문 이어 읽기

전체 상태를 반복 호출하거나 `cache/spillover` 경로를 증거 도구로 열지 않는다. 기존 `forsic_reporting` 안에서 이어 읽는다.

- `state`가 준 `snapshot_id`와 `pointer="/questions"`, `"/gaps"`, `"/requirements"`, `"/sources"` 등으로 필요한 섹션을 읽는다. 반환된 `items`의 `pointer`로 정확한 record/field를 선택한다.
- 목록의 `offset`은 항목 번호이고 `next_offset`이 있으면 다음 페이지가 남아 있다. `presented=false`는 그 항목의 참조만 제시됐다는 뜻이며, 판단 전에 해당 pointer를 읽는다. 미리보기와 색인은 원문 인용이 아니다.
- `source(source_id)`의 작은 결과는 기존처럼 원문을 반환한다. 큰 결과는 필드 색인을 준다. 같은 `source_version`과 `pointer="/lines/0/text"` 등의 보존 필드를 지정하면 긴 한 줄도 이어 읽을 수 있다.
- 문자열의 `offset/limit`은 UTF-8 바이트 기준이다. 직접 다음 위치를 추정하지 말고 `next_offset`을 사용한다. `byte_start/end`, `full_field`, 원래 source/version/pointer를 유지한다. 일부 페이지만 읽고 전체 필드·파일을 확인했다고 말하지 않는다.
- 상태가 달라져 snapshot이 거부되면 새 overview에서 ref를 다시 얻는다. 보존 source는 별도의 정확한 source_version으로 이어 읽는다. 누락된 반론·요구·공백은 종결 전에 해당 섹션에서 확인한다.

## 조사 순서

1. 사용자 질문을 `forsic_note`에 남긴다. `state`와 `gaps`로 현재 답·필수 반론·판별 공백을 읽는다. 종합 분석·임원 보고에는 [경영 질문 절차](references/executive-decision-questions.md)를 먼저 읽어 사건에서 확인해야 할 질문을 선택한다. 기술 작업 나열이 보고의 중심이 되어서는 안 된다.
2. [보고 계약](references/report_contract.md), [섹션 카탈로그](references/section_catalog.yaml)의 해당 요구만 적용한다. 관련성이 미평가이면 N/A로 만들지 않는다.
3. [미션 정책](references/mission_policy.md)에 따라 답을 바꿀 다음 확인 하나를 정한다. 반환 결과가 있으면 `source`로 재사용한다. 없으면 기존 읽기 전용 도구로 실제 지원 범위만 수집한다. draft가 자동 실행되지는 않는다.
4. 반환 결과를 얻으면 gap을 unassessed_result/evaluate_result로 분류하고 **after_result 평가 미션**을 기록한다. 수집 전 계획과 사후 계획을 구별한다. mission.reuse_result_refs에는 실제 결과판, input_refs에는 함께 읽은 자료, preserved_counterevidence_refs에는 반론을 넣는다. exact_target_scope는 question.scope와 같아야 한다. 새 수집 전 계획은 draft로 남기고 기존 도구에서 수행한다.
5. `assess` 후 `state`를 다시 읽는다. 미평가 처리가 끝나도 동일 작업 연결·성공·승인 등 원래 의무가 남으면 다음 판별을 설계한다. 새 이름·미션 ID는 해결 증거가 아니다. 질문 자체를 정정할 때는 같은 note_id/current revision과 correction_reason을 사용한다. 옛 정의에 대한 평가가 최신으로 승격되지 않는다.
6. 전달 전 review-conclusions로 핵심 문장을 원문에 대조한다. 별도 조언이 필요하면 현재 snapshot_id로 `review`한다. 지적을 원문에 확인해 현재 노트·평가에 반영하고 `state`를 다시 읽는다. 같은 초안을 반복 검토하거나 전달 여부 불명의 요청을 자동 재전송하지 않는다.
7. 중단·예산 종료에도 `render`로 부분 보고를 남긴다. review_id가 있어도 검토 대상 자료가 바뀌면 최신 검토가 아니다. 원래 input_snapshot_id와 적용되는 applies_to_snapshot_id를 구별한다. 미검토·실패·시간초과·stale와 핵심 공백을 남기며 이를 의미 승인으로 바꾸지 않는다. 네 파일을 만든 것과 목표 달성·승인·배포는 다르다. 다음 재개 조건과 실제 경로를 알린다. 기존 실행이나 이전 보고서를 소급 수정하지 않는다.
8. 사용자에게 전달할 최종본을 정했을 때만 해당 render의 `bundle_id`와 `snapshot_id`로 `finalize`한다. 중간 초안마다 호출하지 않는다. 현재 연결된 사건 토픽 이외의 수신자를 새로 지정하지 않는다. `delivery_status`가 queued이면 ‘전송 대기’이지 ‘전송 완료’가 아니다. 실제 전송 영수증이 없거나 실패·미상이면 상태와 로컬 파일 경로를 알리고, 같은 파일을 다시 만들거나 재전송해 해결된 척하지 않는다. 정정한 최종본은 새 버전으로 전달한다.

## 평가 제안 형식

`snapshot_id`는 직전 state 값이다. payload:

```json
{
  "mission_id": "현재 미션 ID", "mission_version": "현재 버전",
  "result_id": "실제 도구 결과 ID", "result_version": "현재 source.version",
  "outcome": "inconclusive", "reasoning_summary": "판별 조건과 실제 결과를 대조한 이유",
  "answer": "현재 질문에 대한 범위 한정 답",
  "citations": [{"source_id":"실제 ID", "source_version":"현재 버전",
    "pointer":"/lines/0/text", "literal":"정확한 원문", "byte_start":0}],
  "limitations":["아직 판단할 수 없는 내용"], "next_check":"다음 판별 또는 재개 조건"
}
```

byte_start는 보존된 도구 반환 JSON 필드의 UTF-8 위치다. 이미지 offset이 아니다.
full_field 미션은 그 필드 전체를 제시한다. 경로·메타데이터를 본문으로 인용하지 않는다.
discover는 found/no_match_in_scope 등이며 supports/refutes는 명시 판별 조건이 있는 경우에만 사용한다.
no_match는 지정 검색 범위의 결과이지 실행 부재가 아니다. 명령 문자열은 성공·승인·귀속이 아니다.
참조·원문 검증을 통과한 자연어 해석도 독립 분석가 검토와 다르다. 자동으로 final이라고 쓰지 않는다.

## 보고서와 한계

[표현 지침](references/style_and_wording.md)을 따른다. E01–E03과 A01–A12는 섹션 목적이며
실무자 12쪽 상한이 아니다. 두 독자는 같은 F-ID·답·근거·중요 한계·판단 snapshot을 사용한다.
기존 `forsic_report`는 과거 호환 경로로 남아 있다. **새 보고의 검토·생성은 forsic_reporting(review/render)를 사용한다.**
기관 승인, 자동 의미 검증, 원래 의무의 자동 종결은 현재 연결되지 않았다. 이를 가짜 status로 채우지 않는다.
