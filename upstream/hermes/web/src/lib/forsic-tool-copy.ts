/** Product labels describe purpose; availability comes from the session/config catalogue. */
const TOOL_COPY: Record<string, [string, string]> = {
  forsic_archive_read: ['압축파일 원문 읽기', '확인한 압축파일 목록과 멤버 번호로 정확한 대상을 선택해 내용을 읽습니다.'],
  forsic_archive_relist: ['압축파일 목록 새로 확인', '선택한 과거 목록의 정확한 대상을 다시 확인하고, 새 목록과 출처를 남깁니다.'],
  forsic_assess: ['검사 결과 평가', '계획한 검사의 결과를 근거·한계와 연결해 평가합니다. 전체 사건의 결론과는 구분합니다.'],
  forsic_case: ['사건 범위 확인', '현재 사건의 증거 범위와 사용 가능한 분석 환경을 확인합니다.'],
  forsic_cases: ['저장한 사례 조회', '보존된 과거 사례를 검색하고 조회합니다. 현재 사건의 근거와 구분해서 사용합니다.'],
  forsic_hash: ['파일 해시 계산', '원본 파일의 해시를 계산하고 계산한 대상과 결과를 기록합니다.'],
  forsic_image_files: ['이미지 자료 탐색', '디스크 이미지의 볼륨·디렉터리·파일 정보와 압축파일 목록을 살펴봅니다.'],
  forsic_image_info: ['이미지 정보 확인', '증거 이미지의 형식과 메타데이터를 확인합니다. 무결성 검증은 별도입니다.'],
  forsic_image_read: ['이미지 원문 읽기', '이미지 안에서 선택한 파일과 구간을 읽고 실제 읽은 범위를 남깁니다.'],
  forsic_image_read_mission: ['계획한 자료 읽기', '저장된 검사 계획의 정확한 입력으로 이미지 자료를 읽습니다.'],
  forsic_image_search: ['이미지 기록 검색', '명시한 이미지·파일 범위에서 문자열을 검색하고 검색 범위와 이어읽기 위치를 반환합니다.'],
  forsic_indicators: ['조사 지표 정리', '해시·주소 등 조사 지표를 출처와 연결해 정리하고 중복을 병합합니다.'],
  forsic_intake: ['증거 접수', '사용자가 지정한 경로의 접근 가능 여부와 형식을 확인하고 조사 범위를 연결합니다.'],
  forsic_intel: ['위협 정보 대조', '허용된 설정과 전송 범위 안에서 조사 지표의 위협 평판을 조회합니다.'],
  forsic_list: ['증거 목록 탐색', '사건 범위 안의 파일과 디렉터리를 확인합니다.'],
  forsic_mission: ['검사 계획', '가설을 구별하는 검사가 필요할 때 지지·반박 조건과 실행 계획을 저장합니다.'],
  forsic_note: ['이전 판단 조회', '저장한 질문 노트의 현재 내용과 변경 이력을 읽습니다.'],
  forsic_question: ['질문과 판단 정리', '현재 질문의 답·근거·경쟁 설명·가정·남은 한계를 함께 기록하고 갱신합니다.'],
  forsic_read: ['원문 읽기', '선택한 증거 파일의 내용을 읽고 출처와 읽은 구간을 보존합니다.'],
  forsic_read_bytes: ['바이트 구간 읽기', '원본의 지정한 바이트 구간을 읽습니다. 바이너리나 정확한 위치 확인에 사용합니다.'],
  forsic_review_response: ['검토 답변 기록', '검토 의견의 반영 여부와 판단 이유를 해당 질문에 기록합니다.'],
  forsic_reporting: ['근거 조회와 보고', '질문·남은 검사·보존 출처·실행 기록을 조회하고 선택한 답을 보고서로 정리합니다.'],
  forsic_search: ['기록 검색', '허용된 증거 범위에서 문자열을 찾고 검색 결과와 범위를 기록합니다.'],
  forsic_shell: ['분석 터미널', '격리된 작업공간에서 명령과 스크립트로 분석합니다. 원본은 읽기 전용이며 출력은 파생 자료로 기록합니다.'],
  forsic_skill: ['조사 스킬 읽기', '필요한 조사 절차와 참고 자료를 선택해 읽습니다. 스킬 목록 조회 자체는 분석을 실행하지 않습니다.'],
  forsic_verify: ['증거 무결성 검증', '증거 이미지의 무결성 검증 작업을 시작하거나 진행 상태와 결과를 확인합니다.'],
};

const REPORTING_COPY: Record<string, [string, string]> = {
  source: ['보존 근거 조회', '선택한 출처의 정확한 원문 구간을 다시 읽습니다.'],
  state: ['조사 상태 조회', '저장된 질문·답·근거와 현재 조사 상태를 읽습니다.'],
  gaps: ['남은 확인 조회', '저장된 공백과 남은 확인 사항을 읽습니다.'],
  execution: ['실행 기록 조회', '기존 작업의 실행 상태와 반환 기록을 확인합니다.'],
  review_inbox: ['검토 의견 조회', '보존된 독립 검토 의견과 처분 상태를 읽습니다.'],
  render: ['보고서 작성', '저장된 답과 선택한 근거를 보고서 초안으로 정리합니다.'],
  finalize: ['보고서 최종본 확인', '현재 근거·검토 처분과 보고서 파일을 확인하고 최종본을 선택합니다.'],
  review: ['보고서 검토', '현재 보고서 자료에 대한 검토를 요청합니다.'],
};

export function forsicToolCopy(name: string, action?: string) {
  const copy = name === 'forsic_reporting' && action ? REPORTING_COPY[action] ?? TOOL_COPY[name] : TOOL_COPY[name];
  // These entry points do not receive the registry's public `reason` argument.
  // Question revisions and review responses carry judgment reasons, not execution purposes.
  const publicReason = Boolean(copy) && !['forsic_question', 'forsic_review_response', 'forsic_mission', 'forsic_assess', 'forsic_archive_read', 'forsic_archive_relist'].includes(name);
  return {label: copy?.[0] ?? name, description: copy?.[1] ?? '', publicReason};
}
