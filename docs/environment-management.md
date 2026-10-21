# 분석 환경 관리

왼쪽 하단 **관리 설정 → 분석 환경** 화면에서 새 사건의 기본 환경과 선택한 사건의 환경을 따로 확인합니다. 환경은 분석 shell과 E01 parser의 실제 Docker image ID, 플랫폼, 외부 helper 파일 해시, 설치 측정값, 검사 결과를 보존한 로컬 기록입니다. 이미지 태그를 바꾸어도 이미 고정한 사건의 실행 대상을 바꾸지 않습니다.

## 준비와 선택

1. **설치된 환경 등록**으로 로컬 `forsic-analysis:1` 및 `forsic-dissect:3.25.1` 이미지를 검사·보존합니다. 다운로드 없이 두 역할의 image ID와 실제 설치 버전을 확인하고 작은 합성 입력의 runtime 검사를 실행합니다. 이어 실제 보존 위치의 helper를 읽기 전용으로 연결해 해시를 대조하고, 해당 조직의 관리 경로에 만든 임시 작업 폴더에서 쓰기·읽기를 확인합니다. 두 이미지에서 검사가 끝나야 환경 기록을 공개합니다. 실패·취소 시 미등록 후보만 정리하고 기존 기본 환경과 사건 고정을 유지합니다.
2. **업데이트 확인**은 증거를 마운트하지 않은 별도 관리 컨테이너에서 수행합니다. apt 저장소, PyPI, 공식 GitHub release의 후보를 조회하며, 실패·오프라인과 후보 없음은 구별합니다. apt 후보에는 설치 시뮬레이션의 의존성 계획도 포함합니다. 후보 조회는 설치 완료가 아닙니다.
3. 검토한 정확한 버전·해시와 기능 검사를 준비 계획에 넣어 **새 환경 준비**를 실행합니다. 진행 단계·실제 프로세스 정체성·로그·실패 단계는 작업 ID로 보존합니다. 새 이미지는 원래 태그를 덮어쓰지 않습니다.
4. 검사가 성공한 환경만 **새 사건 기본값** 또는 **선택한 사건에 적용** 대상으로 사용합니다. 기본값 변경은 기존 사건을 수정하지 않습니다. 사건 선택 변경은 예상 revision으로 충돌을 검사하고 이전 revision을 보존합니다.
5. 관련 native 세션의 turn·compression·실행이 모두 안전한 경계에 도달할 때까지 사건 변경은 **적용 대기**입니다. 안전성 확인은 기존 native idle admission fence에서 수행하며 브라우저의 idle 표시에 의존하지 않습니다. 환경이나 스킬 변경은 중단된 조사를 재개하지 않습니다.

기존 사건에 고정 기록이 없으면 다음 관리 실행에서 설치된 실제 환경을 처음 확정합니다. 이미 남아 있는 도구 실행 기록은 수정하지 않으며, 과거에 어떤 환경을 사용했는지 소급해 보장하지 않습니다. 여러 세션은 같은 사건 output의 `environment.json` revision을 공유합니다.

등록 검사는 당시의 helper와 임시 작업 폴더를 확인한 것입니다. 이후 모든 사건·원본 경로의 접근까지 보장하지 않습니다. Docker가 컨테이너 시작을 거절하면 실제 명령 실패와 구분하여 반환합니다. 종료 코드 125나 출력 문구만으로 판단하지 않고 컨테이너 시작 기록을 조회하며, 상태를 확인하지 못한 실행은 자동 재시도하지 않습니다. 조직 홈이나 다른 사건을 넓게 마운트하여 접근 문제를 우회하지 않습니다.

## 지원하는 준비 계획

`EnvironmentManager.prepare()`는 별도 패키지 관리기 대신 기존 Docker 빌드와 도구 installer를 사용합니다. 관리 작업에는 사건 경로·노트·대화·모델 인증정보를 전달하지 않습니다.

```json
{
  "base_environment_id": "env-<preserved manifest hash>",
  "apt": {"testdisk": "7.2-0.1build1"},
  "python": [],
  "checks": [{
    "name": "selected synthetic function",
    "argv": ["python", "-c", "import json;print(json.dumps({'count':len([1,2,3])}))"],
    "expected_stdout": "\"count\": 3"
  }]
}
```

이 예제의 공통 runtime 검사는 testdisk 복구 기능의 검증이 아닙니다. 실제 도구 업데이트 계획에는 변경한 기능을 실행하고 핵심 기대 결과를 확인하는 검사를 넣어야 합니다. 도움말·버전 인자만 있는 검사는 기능 검사로 받지 않습니다. 등록 manifest는 설치 확인, 공통 runtime 검사, 사용자가 지정한 기능 검사를 분리합니다.

| 유형 | 처리 |
| --- | --- |
| apt | 패키지별 정확한 배포판 버전으로 derivative 이미지에 설치하고 `dpkg-query` 측정값과 대조합니다. 후보 조회의 의존성 시뮬레이션 및 빌드 로그를 보존합니다. |
| Python | `name`, `version`, `sha256` 목록을 요구하며 모든 전이 의존성도 hash lock에 포함해야 합니다. wheel만 `--require-hashes`로 별도 `/opt/forsic-deps` venv에 설치합니다. 기존 Dissect/Plaso Python은 수정하지 않습니다. 확장 adapter는 필요하면 `/opt/forsic-deps/bin/python`을 명시합니다. |
| release 바이너리 | 현재 관리하는 Hayabusa/YARA-X/Gitleaks의 정확한 `binary_lock`을 지정합니다. 기존 `tools/install_forensic_tools.py`의 fetch·checksum·플랫폼·안전 추출·새 prefix 설치를 Docker 빌드 내부에서 실행합니다. 후보 release의 checksum이 없으면 검토된 lock을 마련해야 합니다. |
| 기반 이미지 | 선택적 `base_image`는 `repository@sha256:<digest>`만 받습니다. 관리 단계에서 해당 digest를 다운로드하고 실제 ID로 빌드합니다. 변경 기능 검사가 필요합니다. |
| 규칙·설정 | 이미지 내부 도구 lock 및 rule/config manifest의 실제 해시를 설치 inventory에 기록합니다. 엔진 release 태그와 규칙 snapshot을 동일시하지 않습니다. |
| 기타 도구 | 관측되는 설치 버전과 자동 업데이트 지원 여부를 구별합니다. 모든 OSS updater를 제공하지 않습니다. |

확장팩 의존성은 `{method: "apt", name, version}` 또는 `{method: "python", name, version, sha256: [...]}`로 선언합니다. `plan_dependencies()`는 같은 `prepare()` 입력으로 변환하며, 누락된 기능 검사를 추가해야 설치를 시작할 수 있습니다. 선택 환경에서 실제 버전이 맞지 않으면 확장 실행은 의존성 미충족으로 표시합니다. host 설치와 알 수 없는 의존성 형식은 지원하지 않습니다.

사용 가능한 플랫폼은 현재 기능 검사한 `linux/amd64`입니다. lock에 ARM asset이 있어도 전체 분석 환경의 ARM 지원을 뜻하지 않습니다. 후보 버전 조회만으로 Python wheel이나 바이너리의 플랫폼 호환성을 확정하지 않습니다.

## 보존과 rollback

환경 manifest와 helper 파일은 내용으로 식별한 새 디렉터리에 저장합니다. 일반 shell, E01 parser, typed triage, sensitive scan, 확장 adapter가 같은 사건 revision을 사용합니다. 이미지가 없거나 helper 해시가 다르면 원래 태그로 fallback하지 않고 실행을 거부합니다. 실행 provenance는 기존 도구 반환·사건 ledger에 붙이며 별도 조사 DB를 만들지 않습니다.

rollback은 이전 보존 환경을 다시 선택하는 사건 revision입니다. 역방향 apt downgrade나 원격 재빌드를 하지 않습니다. 이미지나 참조 중인 팩을 자동 삭제하는 정리는 제공하지 않습니다. 확장팩과 스킬만 바꾸는 선택은 같은 이미지를 재사용합니다. 현재 사건은 선택 당시 확정한 팩 버전·내용 해시·ON/OFF를 보존하며 전역 허브 기본값을 뒤따라 바꾸지 않습니다.

환경 기록은 host OS나 과거 Forsic/Hermes 앱 전체를 복원하지 않습니다. 앱 source/버전 및 Hermes pin, host EWF 유틸리티의 관측 버전과 적용 범위를 진단 정보로 남깁니다. 컨테이너 밖 도구는 기록만 하며 이 환경 관리자로 설치·downgrade하지 않습니다.

## 오프라인 내보내기와 가져오기

내보내기는 등록한 image ID를 `docker image save`로 저장하고 환경 manifest, 보존 helper, 전체 파일 checksum을 tar에 묶습니다. 사건 실행 컨테이너를 commit/export하지 않습니다. 선택한 확장이 필요하면 허브의 동일 exporter가 만든 정확한 팩 archive를 `extensions`에 함께 전달하거나 별도 팩 내보내기를 사용합니다. 프로필·제작 작업 폴더 전체·사건 자료는 묶지 않습니다.

가져오기는 경로 이탈·링크·중복 이름·파일 수·확장 크기·checksum·플랫폼·API와 필요한 helper를 검사하고 `docker image load` 이후 실제 ID와 작은 기능을 재검사합니다. 묶인 확장 archive는 보존한 후 동일 허브 validator를 통해 등록해야 하며, 가져오기만으로 기본값·사건·확장을 활성화하지 않습니다. 부족한 파일이나 호환 불가를 자동 온라인 다운로드로 보충하지 않습니다.

## 실패·취소·다시 시도

작업 기록은 `<profile_home>/forsic-management/operations`에 있습니다. 입력 해시, 단계, 종료 상태, 제한된 stdout/stderr 로그, 실제 PID·kernel start ticks·boot ID를 보존합니다. 살아 있는 worker lease가 없으면 PID 숫자만으로 실행 중이라고 표시하지 않습니다. 실패 후보는 기본값이 되지 않습니다.

취소는 해당 관리 subprocess와 고유 이름의 관리 컨테이너만 대상으로 합니다. 신호를 보낸 사실만으로 취소 완료를 표시하지 않으며, 정확한 프로세스의 부재와 컨테이너 제거가 확인되기 전에는 `completion_unknown` 상태를 유지합니다. 재사용된 PID는 종료시키지 않습니다. 프로세스 종료와 이미지 빌드 daemon의 내부 정리는 구분합니다. Docker BuildKit client 중단 후 daemon 상태가 불명확하면 관련 로그·산출물을 확인해야 하며 완료로 보고하지 않습니다. 일반 조사 컨테이너·서비스·세션을 취소하지 않습니다.

동일 operation ID와 동일 입력은 멱등입니다. 다른 입력으로 재사용하면 충돌입니다. `retry(old_id, new_operation_id=...)`는 원래 입력 identity와 archive checksum을 확인해 새 작업으로 다시 시작합니다. 이번 구현은 완료 stage cache를 추정해 재사용하지 않습니다. 변경된 소스·검사 입력은 새로운 계획·작업 ID로 제출합니다.

## 실행 검증

개발 checkout에서 준비된 Python runtime으로 실행합니다. 아래 Docker 검사는 이미 로컬에 존재하는 `forsic-dissect:3.25.1`을 사용하며 네트워크 다운로드·모델 호출·실제 증거를 사용하지 않습니다. Docker daemon과 공유되는 checkout의 `artifacts/` 아래에서 임시 관리·합성 fixture·사건을 만들고 정리합니다.

```bash
python -m unittest test_environment_management.EnvironmentTests -v
FORSIC_TEST_DOCKER_ENVIRONMENTS=1 python -m unittest test_environment_management.DockerEnvironmentTests -v
```

등록의 공통 runtime 검사는 모든 포렌식 도구의 설치를 보장하지 않습니다. `installed.binaries`의 `not_installed`와 실제 실패를 확인해야 합니다. 개발 시 존재하던 로컬 분석 이미지에서는 Plaso의 `log2timeline.py`·`psort.py`가 없음을 확인했고, 이에 대한 기존 Docker 도구 확인 검사 한 항목은 실패로 기록했습니다. 이 사실을 숨기기 위해 운영 이미지 태그를 수정하지 않았습니다.

Docker 검사는 실제 등록→derivative 준비→설치 측정→작은 기능→기존 사건 보존→명시적 전환/rollback→fixture adapter 실행→save/load→두 번째 관리 root에서 같은 fixture 실행을 검증합니다. 실제 apt/PyPI/GitHub 후보 조회·업데이트 다운로드와 전체 포렌식 기능 검사는 이 오프라인 검사에서 실행하지 않습니다. 해당 기능의 실측은 운영 승인된 관리 환경에서 별도 작업 로그와 함께 확인해야 합니다.

2026-10-20의 별도 관리 실측에서는 apt/PyPI/공식 release 후보 조회를 실제 네트워크로 수행했습니다. 권한을 모두 제거한 관리 컨테이너에서 apt의 `_apt` 사용자 전환이 실패하는 문제를 발견해, 증거 없는 조회 컨테이너 내부에서만 `APT::Sandbox::User=root`를 사용하도록 고쳤습니다. 컨테이너 capability를 추가하거나 host apt 설정을 바꾸지 않았습니다.

이어 실제 설치된 `diffutils`를 `1:3.12-1`에서 저장소 후보 `1:3.12-1ubuntu0.1`로 새 이미지에 설치했습니다. 두 합성 텍스트의 unified diff 내용과 예상 종료 코드 1을 검사하고 실제 `dpkg-query` 버전을 대조했습니다. 사설 시험 기본값 변경 뒤에도 기존 합성 사건은 이전 image ID와 버전을 실행했고, 신규 합성 사건만 새 버전을 실행했습니다. 원래 `forsic-analysis:1` 및 parser 태그의 image ID가 유지됨도 재확인했습니다. 이 실측에는 실제 사건·모델 호출·host 설치·운영 배포가 없었습니다. Python 의존성 설치와 release 바이너리 교체의 실제 다운로드 검사는 별도 미검증 범위입니다.
