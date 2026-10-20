# 재사용 절차 스킬 제안과 검토

포식이는 현재 조사에서 성공한 실행 영수증을 골라 일반화한 `SKILL.md` 초안을 제안할 수 있다.
후보 판단은 현재 주 에이전트가 맡는다. 새 모델 순회, 주기 작업, 전역 background review나 환경 설치는 없다.

제안은 현재 tenant/profile의 기존 `ManagementStore` 아래 `workflow-skills/`에 비공개 관리 기록으로 저장한다.
원래 사건, 실행 시작/반환 ID, 세션, 반환 내용의 SHA-256을 보존하며 원문 반환을 기록에 복사하지 않는다.
실제 사건 값과 알려진 비밀값은 초안에서 제외한다. 소스 문자열·사건 경로 비교와 native 자격정보 가림 검사,
일반적인 주소 검사는 우발적 복사를 줄이는 검사이며 완전한 익명화를 입증하지 않는다. 최종 일반화는 사용자가 검토한다.
`/var/log`, `/work/${input}` 같은 일반 절차·템플릿과 검토 가능한 명령 예시는 사용할 수 있다.
팩에는 단일 `SKILL.md`만 포함한다. 실행 스크립트·도구·설치 의존성과 설치 지시는 게시하지 않는다.

## 상태와 승인

1. `review_pending`: 비공개 초안, 제안 이유·적용 조건·한계·원래 대상과 diff를 검토한다.
2. 사용자가 실제 검토한 `draft_sha256` 및 `proposal_revision`으로 `skill.publish`를 요청한다.
3. 저장 직전에 원래 사건 manifest/영수증, 대상 스킬 내용과 버전·선택 revision을 다시 검사한다.
4. 기존 `extension_hub.preview_pack/import_pack`을 통해 새 불변 버전을 저장하고 구조 검증을 반환한다.
5. 별도 `skill.activate`에서 `case` 또는 `default` 범위를 선택한다. 기존 환경/확장 선택과 idle runtime 갱신을 사용한다.

저장·구조 검증·기능 확인·활성화는 서로 다른 필드다. 게시만으로 활성화하거나 모델 학습/기능 개선을 완료했다고
표시하지 않는다. 기능 검증은 `not_observed`다. 실행 중인 조사가 있거나 실제 runtime 적용을 증명하지 못하면 활성화는
`pending`으로 남는다. 기존 사건의 불변 선택 버전과 OFF 설정은 기존 허브 계약을 따른다.

기존 확장 개선은 정확한 `pack_id/version/sha256/skill_name`에 결속하고 같은 ID의 새 버전을 만든다.
현재 제안 경로는 의존성 없는 단일 `SKILL.md` 확장만 개선한다. 참조 파일/도구가 있는 팩은 기존 허브에서 관리한다.
기본 제공 스킬 개선도 `{builtin,sha256}`로 diff를 만들 수 있지만 **검토 전용**이다. 기본 제공 스킬을 안전하게 교체하는
overlay가 연결되기 전에는 이 제안을 게시하거나 중복 이름의 확장을 활성화하지 않는다.

거절/철회는 영속 상태다. 같은 사건·절차 내용·대상의 fingerprint를 재제안해도 기존 종결 기록을 돌려준다.
수정하면 새 hash/revision 검토가 필요하며 원래 버전은 덮어쓰지 않는다. 저장 실패는 성공으로 반환하지 않고
초안을 유지한다. 반환 유실 후에는 같은 내용의 불변 import를 재사용한다. 외부 공개·다른 테넌트 공유는 이 승인에 포함되지 않는다.

## 관리 API

`GET /api/plugins/forsic/management?case_id=...`의 `skill_proposals.items`에 초안·diff·hash·검증·게시·활성화가 표시된다.
쓰기 요청은 기존 localhost 관리 권한, tenant/profile/case 소유권, 전체 관리 `expected_revision`, `operation_id`를 검증한다.
`POST /api/plugins/forsic/management/action`에서 다음 작업을 사용한다.

| action | 추가 필드 |
|---|---|
| `skill.propose` | `proposal` |
| `skill.draft` | `proposal_id`, `draft`, `proposal_revision` |
| `skill.reject`, `skill.withdraw` | `proposal_id`, `proposal_revision` |
| `skill.publish` | `proposal_id`, `reviewed_sha256`, `proposal_revision` |
| `skill.activate` | `proposal_id`, `proposal_revision`, `scope: case/default` |

`proposal`은 `reason`, `applicability`, `limitations`, `receipts`, `draft`, `target`를 받는다.
`receipts`는 선택된 `tool_result` ID 목록이다. 프로그램이 원장 반환과 시작·세션 연결, 성공 여부와 정확한 hash를 확인한다.
`draft`는 `pack_id`, 새 `version`, `name`, 짧은 `description`, `skill_md`, 기존 Forsic 도구의 `requires_tools` 목록이다.
새 스킬의 `target`은 null이고, 기존 확장/기본 제공 개선은 위의 정확한 대상 identity를 사용한다.
주 에이전트에 노출되는 도구 표면은 제안만 허용한다. 게시·활성화는 사용자 관리 동작이다.

## 오프라인 검증

`test_skill_proposals.py`는 원장 출처/실패·불확실 실행 배제, 알려진 사건 값, draft hash/revision,
거절·철회, 대상 선택 충돌, 새 불변 버전, 저장 실패와 재시도를 검사한다.
`test_skill_proposal_api.py`는 실제 HTTP와 관리/허브 저장소로 제안→편집→검토→게시→명시 활성화,
사건별 pending 선택과 localhost 쓰기 거부를 검증한다. runtime 콜백은 합성 seam으로 대체하며 실제 모델이나 Docker를 호출하지 않는다.
