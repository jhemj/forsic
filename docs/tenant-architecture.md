# 테넌트와 운영기억의 경계

Forsic의 테넌트는 조직·고객·환경 단위 작업공간이며 native Hermes 프로필 하나와 1:1로 연결된다.
표시 이름은 경로가 아니다. 서버가 registry의 고정 `tenant_id`와 `profile_name`, 저장 위치를 확인한다.
이 기능은 기존 인증을 사용하는 단일 설치의 작업공간 분리이며 다중 사용자 SaaS 권한 시스템을 뜻하지 않는다.

## 시작과 기존 자료

첫 화면의 테넌트 목록, 선택 확인, 사례·기억 목록 조회는 읽기 전용이다. 조회만으로 프로필·사건·대화·agent를
만들거나 모델을 호출하지 않는다. 새 테넌트 생성은 명시적인 로컬 관리 요청으로 native profile을 빈 공간에 만들며,
이름·정책·canonical 시작 URL을 반환한다. 기존 대화, MEMORY, USER, 사례, 증거, MCP 설정을 복제하지 않는다.
새 사건·대화는 그 뒤 사용자가 시작할 때 기존 native 경로로 생성한다.

기존 설치의 이관은 시작 시 명시적인 `TenantRegistry.bootstrap_legacy()` 단계다. 기존 intake, 사례 DB, native DB,
관리 위치를 registry에 귀속하며 사건·사례 본문을 옮기거나 다시 저장하지 않는다. archive ID와 revision/body hash,
원래 source 위치는 유지한다. 원본 사건 DB가 사라져도 보관본은 읽을 수 있고 출처 상태는 `unavailable`로 남는다.
등록된 테넌트의 조회 실패를 전역 intake/library/current pointer로 대체하지 않는다.

## 공통 scope

`tenants.resolve_request()`는 tenant/profile/session/case 선택을 함께 검증한다. 중복 query와 JSON/path 선택이
서로 다르면 거절한다. 확정 scope를 요청 문맥에 묶고 native Hermes home override도 같은 프로필로 설정한다.
각 작업은 현재 registry를 다시 확인하므로 UI의 문자열이나 오래된 객체가 다른 테넌트의 권한이 되지 않는다.

| 위치 | 소유 범위와 검증 |
|---|---|
| 사건·증거·보고서·export | 명시적인 case root, manifest tenant/profile, session 소유와 사건 연결 |
| 과거 사례 | 해당 library DB, archive 원래 ID, 본문 hash, 고정 revision, source DB 소유 경로 |
| 운영기억 | native `MEMORY.md`와 같은 profile의 provenance sidecar; 현재 사건 증거로 사용하지 않음 |
| 환경·확장팩 관리 | profile의 설정·선택과 명시적인 management root; 업로드·다운로드 token도 같은 root |
| 제작 작업 | 별도 native 관리 세션의 기존 격리 경로; 조사 대화·기억·사례·원본을 상속하지 않음 |

테넌트를 등록한 설치에서 사건·사례·관리 API는 선택 없이 전역 현재 사건을 따라가지 않는다. 테넌트 목록과 host 설정은
진입을 위해 선택 없이 조회할 수 있다. query에 다른 프로필을 넣는 것만으로 localhost 관리 권한을 얻지 못한다.
기존 인증과 loopback transport, Host, Origin, 전달 헤더 검사를 유지한 뒤 registry로 확인된 프로필만 관리한다.

## 운영기억의 관리 API

모든 경로는 `/api/plugins/forsic` 아래 있으며 선택한 `tenant_id`와 canonical `profile`을 보낸다.

| 요청 | 결과 |
|---|---|
| `GET /tenants`, `GET /tenants/resolve` | 준비된 작업공간과 서버가 만든 시작 URL |
| `POST /tenants` | `display_name`, `description`, `memory_mode`, UUID `operation_id`로 빈 native profile 생성 |
| `PUT /tenants/{id}/memory-policy` | `mode`, `expected_revision`, UUID `operation_id`로 정책 CAS 갱신 |
| `GET /tenant-memory` | 작은 native 목록의 검색·페이지·용량·동기화 상태 |
| `GET /tenant-memory/{id}` 및 `/history` | 현재 값과 이력; `revision`으로 과거 값 조회 |
| `GET /tenant-memory/snapshot`, `/changes` | 현재 주입 가능한 기억과 baseline 이후 실제 작업 영수증 |
| `POST /tenant-memory/action` | 명시적인 추가·수정·제외·복원 |

수정·제외·복원에는 `memory_id`, 현재 `expected_revision`, `expected_content_sha256`이 필요하다. 복원은
`from_revision`을 명시한다. 서버는 실제 로컬 관리 요청에서 출처를 만들며 JSON의 `source`, `confirmed`, `target`을
신뢰하지 않는다. 관리 화면의 명시적 쓰기는 `user_attested` 출처이지 독립 포렌식 입증이 아니다.
본문과 provenance가 함께 commit된 `status=committed`만 저장 완료다. pending·recovery_required·실패를 성공으로
표시하지 않는다. 목록 조회는 저장 영수증을 만들지 않으며 changes polling은 최초 baseline 이후 결과만 알린다.

`off`는 새 주입·쓰기를 막고, `manual`은 명시적 저장 요청만 허용하며, `confirmed_auto`는 실제 분석가 발화에
결속된 운영 설명을 기존 agent가 판단해 저장할 수 있다. 유효 범위·기간이 불명확하면 미상으로 남긴다.
기억 제외는 앞으로 사용할 목록에서 제외하는 동작이며 과거 대화·보고서·백업의 영구 삭제가 아니다.
USER 주입, curator, background review와 별도 기억 모델·벡터 검색 엔진은 켜지 않는다.

## native 회상과 사례의 의미

v1에서는 테넌트 프로필의 `tools.session_search.enabled=false`를 설정한다. 도구 목록의 availability와 실제 handler가
함께 검사하므로 explicit DB, 다른 profile, session detail, cursor를 직접 넘겨도 transcript recall을 실행하지 않는다.
native SessionDB도 엄격 소유 정책을 적용한 프로필에서 혼합 프로필 저장소를 암묵적으로 읽지 않는다.
기존 공유 DB의 Forsic 대화 목록은 명시적 소유 mapping을 확인하는 제한된 projection으로 처리한다.
native live 요청에 profile과 runtime session을 함께 지정하면 같은 소유자인지 먼저 확인한다. 엄격 소유 저장소에서
없는 대화를 재개할 때 default DB의 이력을 자동 복사하거나 같은 ID의 빈 대화를 만들지 않는다. native 이력 import도
다른 소유자의 payload를 쓰기 전에 거절하고 같은 소유자의 import에는 profile을 보존한다. 비관리 native 저장소의
기존 복구·이식 동작은 유지한다.

같은 테넌트의 사례 검색은 기존 `CaseLibrary`를 사용한다. 검색 결과·상세·페이지·source는 동일 보관 revision에
결속된다. 사례 유사성은 참고 정보이며 현재 사건의 실행·정상·악성에 대한 증거가 아니다. 다른 테넌트의 archive,
source, revision을 직접 지정해도 조회하지 않는다. 원래 source DB를 열기 전에 소유 경로를 검사한다.

## 오프라인 검증

`test_tenant_boundaries.py`는 실제 임시 native profile과 HTTP API로 읽기 전용 첫 진입, tenant/profile 불일치,
공유 DB 외부 소유 session, 사례 ID/revision/source, 관리 token·오래된 객체, explicit native recall 우회,
기억의 실제 commit/CAS/이력/제외/복원/정책과 원격 쓰기 거절을 확인한다. legacy archive의 본문·ID·hash 보존도
검증한다. `test_tenant_ownership.py`, `test_tenant_memory.py`는 registry와 native 저장 경계를 별도로 검증한다.
이 오프라인 검사는 실제 모델의 의미 판단이나 운영 배포 성공을 증명하지 않는다.
