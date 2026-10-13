---
name: forsic-report-driven
description: 자유로운 자료 탐색에서 선택한 근거를 현재 판단·반론·한계와 연결하고 기존 원장으로 보고한다. 미션은 선택적 판별 검사다.
---
# 근거에서 현재 답으로

사용자 질문에 답할 가능성이 높은 자료를 직접 읽고 검색한다. 중요한 발견이나 답의 변화가 있을 때
`forsic_question`으로 저장한다. 매 읽기 전에 질문 등록·미션·평가 준비를 할 필요는 없다.
실행 시작·실제 반환·원문·출처는 프로그램이 자동 보존한다. 무엇을 조사하고 어떤 근거를 채택할지,
어느 설명이 유력하고 어디까지 종결할지는 에이전트가 판단한다.

## 탐색과 근거 선택

- `forsic_case`로 현재 사건과 자료 범위를 확인한다. 상태 색인은 `forsic_reporting(action=state)`다.
  색인은 원문이 아니다. 필요한 pointer/next_offset과 snapshot_id로 상세를 조회한다.
- 지원되는 증거 도구를 직접 사용한다. 이미지 본문은 `forsic_image_read`의 path(E01), file_path(내부 경로),
  action(read/read_bytes)를 명시한다. TAR/TAR.GZ는 archive_list의 source_ref와 필요한 member_index를
  forsic_archive_read의 listing_ref/member_index로 선택한다. 본문 근거는 실제 읽기 결과의 C2를 쓴다.
- 본문 읽기의 `presented_spans[].citation_ref`는 실제 표시된 보존 필드만 선택한다. 파일 전체·과거 실행·
  성공·승인·귀속을 보장하지 않는다. 더 좁은 인용이나 긴 결과 재조회에는
  `forsic_reporting(action=source, source_id=...)`의 query 또는 pointer/offset을 쓴다.
  반환된 C1/C2 citation_ref를 그대로 선택한다. 버전·바이트 위치를 다시 조립하지 않는다.
- 전체 목록·부분 검색·0건·미지원·실행 실패를 구별한다. 메타데이터 인용은 본문 접근이 아니다.
  원문·제시 범위·시각의 의미를 보존하고 이전 노트·요약을 새 증거로 채택하지 않는다.

## 판단 저장과 정정

`forsic_question`은 생성과 수정을 함께 처리한다. 인자는 평평한 JSON이며 action/payload로 감싸지 않는다.
새 질문은 operation_id, question, scope, answer를 보내고 question_id/expected_revision을 생략한다.
이미 읽은 근거가 있으면 첫 저장에서도 judgment와 함께 범위를 종결할 수 있다. 사전 등록은 필요 없다.
수정은 반환된 자신의 question_id와 **현재** expected_revision을 보낸다. 버전을 직접 증가시키지 않는다.
동일 요청 재시도는 같은 operation_id, 바뀐 내용은 새 operation_id다. 충돌하면 최신 질문을 먼저 읽는다.

근거를 채택할 때는 answer와 reason, 다음 judgment를 한 번에 보낸다.

```json
{"citation_refs":["읽기에서 선택한 citation_ref"],"counter_citation_refs":[],"assertion_kind":"interpretation","inference_strength":"plausible","reasoning_summary":"관측에서 이 설명으로 이어지는 이유와 중요한 반론","assumptions":[],"limitations":["판단이 적용되는 범위와 미확인 연결"]}
```

judgment는 위 객체를 받는 필드다. citation_refs는 채택 근거, counter_citation_refs는 명시적으로 선택한 반증이다.
assertion_kind=fact는 직접 관측, interpretation은 해석이다. 해석 강도는 favored/plausible/unrated이며
fact에는 unrated를 쓴다. alternatives, gaps/critical_gaps, next_checks로 경쟁 설명·한계·다음 검사를 유지한다.
프로그램은 선택한 출처·버전·정확 문자열을 검증하고 같은 노트 트랜잭션에 현재 답을 저장한다.
이 검증은 함의나 의미 정확성의 승인이 아니다. 미션·가짜 검사·사후 평가를 자동 생성하지 않는다.

새 judgment는 이전 채택 결론을 대체하며 과거 기록은 보존한다. 단순 표현 정정은 reason과
claim_action=retain으로 의미 불변을 설명할 수 있다. 철회는 claim_action=retract다.
새 근거로 판단을 바꾸면서 이전 반론·중요 한계를 설명 없이 지우지 않는다.
다른 대화 소유 질문은 참조 자료이며 자동 수정·소유권 이전하지 않는다.

## 필요한 경우에만 판별 검사

결과를 보기 전에 지지/반박/판별 불가 조건을 보존할 가치가 있으면 `forsic_mission`을 쓴다.
정확한 도구·입력과 질문·공백을 선택하고, 반환된 next_call을 실행한다. 미션 저장은 실행이 아니다.
반환 후 `forsic_assess`에 mission_id, result_id, 현재 질문 expected_revision, citation_refs와
outcome/ reasoning_summary/ limitations/ next_check/ operation_id를 한 번에 보낸다.
준비 영수증은 필요 없다. 국소 supports/refutes와 종합 질문 supported/refuted는 서로 다른 판단이다.
이미 읽은 일반 자료는 바로 question.judgment로 선택한다. 평가를 위해 사후 미션을 만들지 않는다.
진행·불확실 실행은 중복하지 않고 같은 native 작업을 관측한다. 기존 계획을 썼다면 미평가 결과를 숨기지 않는다.

## 복구와 종결

오류는 입력·권한·미지원·실제 실행·표시 실패를 구별한다. 올바른 필드는 유지하고 문제가 된 부분만 고치거나
지원되는 다른 자료·도구를 선택한다. 성공한 원문이 보존됐으면 재조회하며 수집을 반복하지 않는다.
새 ID나 경고문만 바꿔 같은 실패를 반복하지 않는다. native 중단을 임의 해제하지 않는다.

work_state는 open/active/held/scoped_closed/blocked_internal/blocked_external/budget_deferred다.
priority는 decision_critical/material/contextual/unassessed이며 신뢰도와 별개다.
범위 종결에는 priority, reason, reopen_conditions와 남은 gap_dispositions를 적는다.
새 질문의 처분은 gaps/critical_gaps의 문장을 그대로 gap_text로 선택한다. 기존 질문은 반환된 gap_id
또는 정확한 gap_text 하나를 선택하며 ID를 만들지 않는다. 각 처분에는 disposition, reason, reopen_conditions가 필요하다.
자료 부족·미검토·예산 보류는 정상 판단이 아니다. 불확실한 결론으로도 한정된 범위는 종결할 수 있다.
원래 요청→확보 자료 색인→실제 검토 범위를 대조한다. 모든 파일 검사는 필요 없지만,
등록 질문·미션을 모두 닫았다는 것만으로 중요한 미검토 영역을 숨기지 않는다.
수집기 경위의 정상 설명과 수집된 시스템 상태를 구별한다.

현재 답·근거·중요 반론·한계를 검토한 뒤 `forsic_reporting(action=render)`로 두 독자 HTML/Word를 만든다.
반환된 snapshot과 실제 경로만 전달한다. 파일 생성은 조사 성공이 아니다. finalize는 선택한 산출물의
기존 승인 미러 전달이며 의미 승인이나 조사 종결이 아니다. 별도 모델 review는 선택적 조언이다.
상세 보고 요구는 필요한 때 [report_contract](references/report_contract.md)를 읽는다.
