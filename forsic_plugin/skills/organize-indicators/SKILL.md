---
name: organize-indicators
description: 현재 사건에서 관측된 주소·해시·경로·계정을 출처와 함께 모아 중복을 정리하고, 사건상 의미와 외부 평판을 구별한다.
---
# 사건 지표 정리

분석에 중요한 값이 발견됐거나 IOC 목록을 요청받으면 `forsic_indicators`를 사용한다.
목록을 채우려고 무관한 주소·개인정보를 전부 추출하지 않는다. 관측된 값은 악성 판정이 아니다.

`upsert`에 type(ip/domain/url/hash/path/account), 원래 value와 source_refs를 넣는다.
source_refs는 보존된 반환의 source_id, pointer(예: /lines/0/text), literal(해당 값),
byte_start(그 문자열 필드 안 UTF-8 위치)를 사용한다. 디스크 위치와 혼동하지 않는다.
`forsic_reporting(source)`에서 원문을 확인할 수 있다. 임의 출처·위치를 만들지 않는다.

- 같은 정규화 값은 한 항목으로 합쳐지며 발견 출처들은 남는다. 기존 항목을 먼저 list/get하고,
  판단을 고칠 때 현재 revision과 summary에 정정 이유를 쓴다. 철회는 withdrawn으로 남긴다.
- summary에는 이 사건에서 왜 관련 있는지, limitations에는 정상 운영 가능성과 미확인 범위를 적는다.
  observed/suspicious/benign/undetermined는 현재 해석이다. 평판 응답을 자동으로 사건 판정으로 바꾸지 않는다.
- 해시는 전체 파일에서 계산한 값인지 로그에 기록된 문자열인지 구별한다. E01 내부 파일은
  `forsic_image_files(action=hash, file_path=...)`로 계산한다. sha256_slice나 E01 컨테이너 해시는
  내부 파일 전체 해시가 아니다.
- 내부 IP·경로·계정도 로컬 정리가 가능하다. 저장은 외부 조회가 아니다. 평판 보강이 실제 필요하면
  threat-intelligence를 읽고 기존 승인·설정 범위에서만 조회한다. URL에 접속하지 않는다.
- 처리 시각 updated_at을 최초 발견·사건 발생 시각으로 쓰지 않는다. 사건 시각은 correlate-time으로
  원문과 연결한다. 계정명만으로 사람, 공용 IP만으로 같은 행위자를 확정하지 않는다.

현재 답·반론이 바뀌면 기존 note/mission/assess를 갱신한다. 보고서에는 같은 스냅샷의 지표가
연결된다. 목록 제출은 export(format=json 또는 csv)를 사용한다. 자동 차단목록이나 새 Telegram
첨부 전송은 아니다. 원시값과 출처가 포함되므로 외부 공유는 사용자가 지정한 범위만 따로 검토한다.
