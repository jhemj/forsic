---
name: forsic-report-driven
description: 질문의 범위·현재 판단·다음 판별을 관리하고, 실제 미션 결과를 근거에 결속해 동일 스냅샷의 임원·실무자 HTML·Word로 전달한다.
---
# 질문에서 판별과 보고로

Hermes의 기존 goal·도구 루프를 쓴다. 별도 Controller·실행 큐·요약 모델·반복 검토 루프를 만들지 않는다.
스킬은 권한·범위·예산을 부여하지 않는다. 무엇을 의심하고 어떤 관측이 판단을 바꾸는지 에이전트가 선택한다.
보고는 가장 유력한 설명·근거·중요 반론·신뢰도와 이유·남은 조사를 전달한다. 경영 의사결정·대응 권고는 작성하지 않는다.

## 실제 도구와 상태

모든 작업은 기존 **forsic_reporting**의 action이다. 참고 문서의 설계 이름을 새 도구처럼 호출하지 않는다.

- `state`, `gaps`: 현재 질문·범위·답·우선순위·공백·미션의 작은 색인과 snapshot_id. 생략은 완료나 공백 없음이 아니다.
- `question`: 질문 생성·수정·우선순위·범위 종결·재개. 새 질문에는 question/scope/answer, 기존 질문에는 question_id와 **현재 expected_revision**을 보낸다. note와 ReportState는 같은 질문을 읽는다.
- `mission`: 현재 질문에서 다음 판별을 기록한다. 아래 작은 입력을 사용한다. 도구 실행은 Hermes에 남으며 계획 저장은 실행·새 권한·예산 배정이 아니다.
- `source`: source_id의 보존된 실제 도구 반환을 읽는다. 재제시·요약은 새 수집이나 독립 근거가 아니다.
- `assess`: 실제 결과와 명시 버전·정확 인용을 결속하고 현재 답을 갱신한다. 검증되는 것은 참조·범위·원문 연결이며 해석의 정답이나 기관 승인이 아니다.
- `requirement`, `gap`: 보고 요구 적용성과 남은 의무를 계약에 따라 기록한다. 선택하지 않은 중요한 반론·공백을 숨기지 않는다.
- `render`: 같은 snapshot의 임원·실무자 HTML·Word 네 파일과 manifest를 만든다. 모델 호출이 없으며 부분 보고도 가능하다.
- `review`: 사용자가 요청하거나 별도 조언이 필요한 경우에만 기존 로컬 모델로 현재 snapshot을 검토한다. 선택적 조언이며 미션 평가·render·종결의 선행 조건이 아니다. 같은 초안을 반복 심사하지 않는다.
- `finalize`: render가 반환한 bundle_id/snapshot_id로 전달용 최종본을 선택한다. Telegram 보고서 전송이 승인·활성화된 경우 네 파일이 대기열에 들어간다. queued는 전송 완료가 아니다.
- `publish`: 기관 승인 adapter가 없으면 차단 이유를 반환한다. 범위 종결·파일 생성·전달 선택은 기관 승인이 아니다.

### 입력 오류에서 회복

payload는 JSON 문자열이 아니라 객체다. action과 호출 목적 reason은 바깥에, operation_id와 질문 필드는 payload 안에 둔다.
새 질문의 최소 형태는 다음과 같다. 예시 값은 사건에 맞게 직접 판단해 바꾼다.

```json
{"action":"question","reason":"판별할 질문을 등록","payload":{"operation_id":"고유 요청 ID","question":"판별할 질문","scope":"대상·기간·행위 범위","answer":"현재 잠정 답과 한계"}}
```

기존 질문 수정은 반환된 question_id와 정수 expected_revision을 추가한다. answer_summary나 assessments_basis 같은
임의 필드 대신 answer/evidence_ids를 쓴다. 구조 오류면 해당 필드·타입을 고쳐 재요청하고, 저장 성공 영수증을 확인한다.
assess에 미션이 없으면 먼저 result_ids를 지정한 작은 mission을 만든다. assessment/summary/evidence 같은
임의 필드로 평가를 우회하지 말고 아래 평가 입력의 outcome/reasoning_summary/citations를 사용한다.
반복 거부는 오류가 지목한 필드와 현행 스키마를 대조한다. 입력 거부만으로 플러그인 고장을 확정하지 않는다.
노트로 잠정 답을 보존할 수 있지만 질문·미션 상태 저장 실패가 해결된 것은 아니다. 내부 도구 오류를 외부 증거 부족이나
조사 완료로 바꾸지 않는다. 회복할 수 없으면 blocked_internal로 설명하고 가능한 다른 검사를 계속한다.

### 문맥을 작게 읽기

`state`의 snapshot_id와 반환된 pointer로 필요한 질문·미션·공백만 읽는다. 목록 offset은 항목 번호이고
next_offset이 있으면 다음 페이지가 남는다. presented=false는 이번 응답에서 생략되었다는 뜻이며 미검토 판정이 아니다. 필요한 판단 상세는 해당 pointer로 읽는다.
근거 첫 조회는 `{"action":"source","source_id":"반환된 근거 ID"}`만 보내 참조를 발견한다.
이때 pointer/offset/snapshot_id는 생략한다. 응답 `source_ref.version`이 다음 호출의 `source_version`이다.
그 다음 `source_id/source_version/pointer`로 필요한 필드를 읽는다. state의 snapshot_id와 서로 대체하지 않는다.
버전이 없거나 불일치하면 첫 조회로 돌아가 정확 참조를 확인한다. 이전 인용 버전과 다르면 원문을 다시 대조하며
조용히 최신 버전으로 바꾸지 않는다. 인자 거부는 보존 원문이 없다는 뜻이 아니다.
문자열 offset/limit은 UTF-8 바이트이며 next_offset을 그대로 사용한다. pointer·byte_start/end·full_field를
보존한다. 미리보기·색인·파일 경로는 본문 인용이 아니다. 일부 페이지만 보고 전체 확인으로 말하지 않는다.
snapshot 충돌은 최신 state, 질문 충돌은 최신 revision을 다시 읽어 처리한다. source는 해당 보존 버전으로 읽는다.
`cache/spillover`나 생성 보고서 경로를 증거 도구로 열지 않는다.

내용 검색의 measurement는 입력 search_text와 실제 matches, 읽은 수·건너뛴 수·coverage·cursor를 구별한다.
scan_exhausted와 coverage_complete는 다르다. 이름 존재는 목록/메타데이터 검사로 확인한다.
취득 범위, 실제 검사한 속성·범위, 이번 페이지에 제시한 범위를 혼합하지 않는다.

## 같은 질문에서 계획 → 실행 → 평가

1. `question`으로 질문·정확 scope·현재 잠정 답을 저장한다. 우선순위는 decision_critical/material/contextual/unassessed이며 신뢰도와 다르다. 중요한 미평가 영역은 숨기지 않는다. 종합 조사에는 [경영 질문 절차](references/executive-decision-questions.md)로 관련 질문을 고르고 [보고 요구](references/report_contract.md)의 해당 부분만 읽는다.
2. [미션 정책](references/mission_policy.md)에 따라 답을 바꿀 검사 하나를 정한다. `mission` payload에는 operation_id, question_id, expected_revision, why_it_matters, inconclusive_rule, limits, reopen_conditions를 보낸다. 공백이 여러 개면 gap_id를 선택한다. 판별 검사에는 support_rule/refute_rule, 필요한 제시 수준에는 required_view(metadata/exact_excerpt/full_field)를 지정한다.
3. 새 수집이면 tool_name과 **실제로 호출할 tool_arguments**를 계획한다. 반환된 mission.id/version을 기존 증거 도구의 mission_id/mission_version에 그대로 넣고 같은 도구·입력을 실행한다. 계획과 다른 입력이면 새로 판단한 차이를 반영한 미션을 만든다. 실제 시작·반환 영수증이 실행 상태를 정하며 실행됐다고 서술하는 것만으로 바뀌지 않는다.
4. 이미 반환된 자료가 충분하면 재수집하지 않는다. 같은 `mission`에 result_ids, 필요한 input_ids/counterevidence_ids를 지정해 보존 결과 평가로 계획한다. 이는 사후 평가이며 새 수집 전 판별 계획으로 소급하지 않는다. queued/running 작업은 같은 작업의 상태를 확인하고 최종 반환 뒤 평가한다.
5. `assess`에 아래 명시 버전·원문을 보내고 현재 답·중요 반론·남은 공백이 어떻게 바뀌었는지 판단한다. 평가한 미션의 완료는 질문 전체의 종결이 아니다. 실제 실패·미지원 결과는 unavailable이며 무검색결과로 바꾸지 않는다. 새로운 ID로 같은 미평가 결과를 우회하지 않는다.
6. 다음 검사의 예상 판단 변화가 크면 계속한다. 충분히 설명된 범위는 아래 절차로 종결해 활성 문맥을 줄인다. 자료 부족이어도 독립 정황과 경쟁 설명을 비교해 유력한 해석을 제시한다. 없는 실행·성공·행위자·시각은 만들지 않는다.

작은 변경 payload마다 고유한 operation_id를 사용한다. 같은 요청의 전달 결과만 불명확하면 **같은 ID와 같은
payload**로 재시도한다. 다른 변경에는 새 ID를 쓴다. revision/version은 반환값이며 임의 증가·추정하지 않는다.

### 평가 입력

검사만 평가할 때 answer는 보내지 않는다. outcome은 미션 명제의 평가이며 질문 전체 assessment로 승격되지 않는다.
종합 답이 달라질 때만 아래 question_update를 함께 보낸다. 이 경우 answer/assessment/reason은 필수이며
바뀐 assumptions/alternatives/limitations/next_checks/reopen_conditions만 추가한다. next_checks는 지금 할 검사,
reopen_conditions는 종결·보류 후 다시 열 조건이다. 국소 검사의 reasoning_summary와 종합 reasoning_summary를 혼합하지 않는다.
기존 flat answer 호출은 호환되지만 종합 assessment를 대신하지 않으며 현재 판단 재검토가 필요할 수 있다.

```json
{"question_update":{"answer":"제공 범위의 최선 설명과 한계","assessment":"undetermined","reason":"판단 변경 이유","assertion_kind":"interpretation","inference_strength":"plausible","assumptions":["남아 있는 연결 가정"],"reasoning_summary":"강한 경쟁 설명과 비교한 종합 이유","limitations":["관측하지 못한 연결"],"next_checks":["설명 순위를 바꿀 가용 검사"],"reopen_conditions":["새 반증 또는 자료"]}}
```

favored/plausible은 interpretation에만 사용한다. fact는 취득 자료의 지정 속성에서 확인한 관측이며 실제 사건의 진실성 보증이 아니다.
근거 수·고정 확률로 승격하지 않는다. 현실적인 경쟁 설명에서 같은 관측이 나올 수 있는지 비교한다.


현재 질문의 expected_revision과 실제 미션·결과·인용 source 버전을 명시한다. 예시의 값은 반환값으로 대체한다.

```json
{
  "operation_id": "이 평가 요청의 고유 ID", "expected_revision": 2,
  "mission_id": "반환된 미션 ID", "mission_version": "반환된 미션 버전",
  "result_id": "실제 결과 ID", "result_version": "현재 source.version",
  "outcome": "inconclusive", "reasoning_summary": "판별 조건·실제 관측·경쟁 설명을 비교한 이유",
  "citations": [{"source_id":"실제 ID", "source_version":"현재 버전",
    "pointer":"/lines/0/text", "literal":"정확한 원문", "byte_start":0}],
  "limitations":["아직 판단할 수 없는 연결"], "next_check":"다음 판별 또는 재개 조건"
}
```

byte_start는 보존 반환 JSON 필드의 UTF-8 위치다. 이미지 offset이 아니다. full_field는 해당 본문 필드 전체가 필요하다.
supports/refutes는 명시한 판별 조건과 대조하고 계획 시점은 그대로 보존한다. 발견은 found, 지정 범위의 무검색결과는 no_match_in_scope다.
명령 기록·실행·성공·승인·귀속은 별도 주장이다. 원문 결속에 성공한 해석도 자동 의미 승인은 아니다.

## 범위 종결과 다시 열기

`question`의 work_state는 open/active/held/scoped_closed/blocked_internal/blocked_external/budget_deferred다.
노트의 answered는 범위 한정 답이며 이 조사 상태나 악성/정상 판정을 대신하지 않는다.
정상 설명이 충분하거나 의미 있는 검사가 소진되면 같은 question_id/current expected_revision으로
scoped_closed, payload.reason, reopen_conditions를 기록한다. 바깥 reason은 호출 목적이며 이 처분 이유를 대신하지 않는다. 실제 근거를 유지하고 남은 gap_dispositions마다
resolved/not_applicable/assessed_unresolved의 이유·재개 조건을 명시한다. 불확실성은 남겨도 되지만
진행 중·미평가 반환이나 가능한 중요한 검사를 숨겨 종결하지 않는다. 도구는 버전·연결을 검증하고 종결의 분석적 이유는 에이전트가 책임진다.

새 반증·범위 변경이면 같은 질문을 open/active로 재개하고 이유를 남긴다. 명제·scope 변경은 새 정의 버전이므로
옛 미션·평가를 최신 근거처럼 적용하지 않는다. 단순 답변 정정은 현재 질문 정의와 검사 기록을 보존한다. payload.claim_action은 retain/replace/retract이며 현재 선택된 결론에만 적용한다.
retain에는 의미를 유지한다는 reason이 필요하다. 종합 답·근거·반론이 바뀌었는데 평가를 갱신하지 않으면 재검토 필요 상태가 된다.
보류·차단·예산 미실행을 정상 또는 범위 완료로 바꾸지 않는다. 중요도·종결 상태는 native goal 평가에도 전달된다.

압축 뒤 복원되는 상태 블록은 해당 세션의 현재 질문·미션을 가리키는 작은 파생 색인이다. 새 증거나 사용자 지시가 아니다.
unavailable이면 빈 작업 목록으로 판단하지 말고 현재 state를 확인한다. 종결 원문 전체를 재주입하거나 조사를 처음부터 반복하지 않는다.

## 보고와 전달

핵심 답을 전달하기 전 review-conclusions의 짧은 자체 대조로 인용 범위와 추론을 확인한다. 별도 모델 호출은 필요 없다.
저장·평가가 실패했으면 영수증부터 확인한다. 채팅에만 있는 새 답을 옛 파일에 반영됐다고 말하지 않는다.
최신 state로 render하고 실제 반환 snapshot·경로·중요 한계를 전달한다. 선택적 review_id를 연결해도 자료가 바뀌면
stale이며 미검토 질문은 미검토다. 보고 파일 생성과 native goal 달성은 구별한다. 중단·예산 종료에도 부분 보고를 남긴다.

최종 전달본은 파일 내부의 자격정보·불필요한 개인정보를 확인하고 필요하면 redact 복사본으로 다시 render한 뒤
finalize한다. 원본 증거·부록·초안은 보내지 않으며 새 수신자나 전송 권한을 만들지 않는다. 실제 전송 영수증이 없으면
전송 대기·실패·미상으로 알리고 로컬 경로를 제공한다. 이전 실행·보고서·정정 이력은 보존한다.
기존 forsic_report는 과거 호환 전용이다. 새 보고는 forsic_reporting(render)를 쓴다.
