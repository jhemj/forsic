---
name: data-movement
description: Distinguish attempted transfer, success, and content through file-copy, archive, and network records; investigate legitimate business transfers and verification limits.
---
# Did data actually leave?

For external communication or exfiltration questions, choose propositions distinguishable in current evidence.
Target files/data → copying/archiving → transfer attempt → connection/transfer success → actual content need not
all be preserved. Do not fill missing stages with narrative.

Read relevant files, command records, application job logs, authentication/transfer sessions, and preserved
proxy/firewall/flow records within the needed scope. Link the same sessions, targets, and periods; check missing,
rotated, and sampled scope.

- Settings or shell commands do not establish successful execution. DNS lookup is not a successful connection;
  an allowed connection establishes neither completed file transfer nor personal-data leakage. Check response
  status, job completion, and actual sent volume separately.
- Consider direction, retransmissions, compression, and protocol overhead. Do not convert network byte counts
  into leaked file sizes or numbers of affected people. Transfer content remains unconfirmed without a content link.
- Legitimate backup, deployment, and synchronization can use the same tools and destinations. Distinguish through
  authorized job records, usual execution patterns, and differences in target files and accounts. Program names
  do not fix benign/malicious status.
- Empty searches apply to the tested files and regions. Continue large logs through next_cursor or disclose
  remaining scope. Do not pretend to interpret unsupported PCAP, database, or archive formats.

Link addresses/hashes to local indicators and event times to original timelines. Do not visit discovered addresses
or upload files. Save '확인한 전송 단계 / 대상·방향 / 실제 내용 연결 / 정상 설명 / 남은 확인' in notes;
connect only next checks that could change the answer to existing report-driven missions. Leave response decisions
to people.

Use scan-sensitive-data when selected files need content identification. Link returned candidates to copy/archive/session/transfer evidence; discovery alone does not establish exfiltration. See [scan-sensitive-data](../scan-sensitive-data/SKILL.md).
