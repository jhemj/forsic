# Forsic 보고 도구 adapter 제안

이 문서의 이름은 Hermes 기본 도구나 현재 설치된 API가 아니다. 실제 native extension으로 연결해야 한다. Skill만 복사해서 실행 권한이 생기지 않는다.

| 역할 | 입력 | 반환 | 권한 |
|---|---|---|---|
| report_state_read | case/run, known snapshot, changed refs | 현재 질문·채택 주장·공백·허용 참조·delta | 읽기 전용 |
| report_gap_audit | snapshot ref, requirement profile version | 요구사항 상태·차단·미션 후보 근거 | 결정적 상태 계산; 의미 정답 판정 아님 |
| report_mission_record | mission draft, expected snapshot | 미션 ID·draft/blocked·오류 | 호스트가 명시적 버전·권한 검사 |
| report_assessment_propose | exact test/result refs, claims, counters | 채택/거부/부분 결과와 사유 | 모델이 스스로 승인하지 못함 |
| report_render | frozen snapshot, reader profiles | 같은 manifest의 HTML/Word | 추가 모델 호출·새 사실 생성 없음 |
| report_publish | validated artifacts, approval ref | publish manifest | 기관 배포 권한 필요 |

원문·검사 도구는 별도 읽기 전용 도구 계약을 사용한다. 임의 shell, 증거 명령 실행, 발견 주소 접속, 자동 외부 조회를 도구 기능으로 추가하지 않는다.

## 구현 순서

1. 현재 원장→ReportState의 adapter와 출처·버전 참조를 연결한다.
2. 상태 audit를 읽기 전용 도구로 노출한다.
3. Hermes가 미션을 선택하고 기존 도구 루프에서 실행하도록 skill을 등록한다.
4. 독립 채택 결과가 ReportState delta로 들어오게 한다.
5. reader view와 공통 renderer를 연결한다.
6. 승인·manifest·변경 영향·과거 파일 불변의 출판 경로를 연결한다.

동봉 `gap_audit.py`는 오프라인 참고 구현이다. 실제 영수증·원문·권한을 증명하지 않으며 테스트 통과를 의미 품질 검증으로 사용하지 않는다.
