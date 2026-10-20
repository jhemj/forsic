---
name: organize-indicators
description: Collect and deduplicate observed case addresses, hashes, paths, and accounts with sources, separating case significance from external reputation.
---
# Organize case indicators

Use `forsic_indicators` when analytically important values are found or an IOC list is requested. Do not extract
all unrelated addresses or personal data to fill a list. An observed value is not a malicious verdict.

For `upsert`, provide type(ip/domain/url/hash/path/account), original value, and source_refs. source_refs uses the
preserved return's source_id, pointer (e.g. /lines/0/text), literal (the value), and byte_start (UTF-8 position within
that string field). Do not confuse it with disk position. Read originals through `forsic_reporting(source)`.
Do not invent sources or coordinates.

- Matching normalized values merge into one item while discovery sources remain. First list/get existing items;
  when correcting judgments, use the current revision and explain the correction in summary. Preserve withdrawals
  as withdrawn.
- summary explains relevance to this case; limitations states benign-operation possibilities and unconfirmed scope.
  observed/suspicious/benign/undetermined are current interpretations. Reputation responses do not automatically
  become case verdicts.
- Distinguish hashes calculated over entire files from strings recorded in logs. For E01 internal files, calculate
  with `forsic_image_files(action=hash, file_path=...)`. sha256_slice and E01 container hashes are not whole internal
  file hashes.
- Internal IPs, paths, and accounts can be organized locally. Storage is not external lookup. If reputation enrichment
  is actually needed, read threat-intelligence and query only within existing authorization/configuration. Do not
  visit URLs.
- Do not use processing time updated_at as first-seen or event time. Link event times to originals through
  correlate-time. Account names alone do not identify people; shared public IPs alone do not establish the same actor.

Update existing note/mission/assess when the current answer or counterarguments change. Reports link indicators
from the same snapshot. Use export(format=json 또는 csv) for list delivery. This is neither an automatic blocklist
nor a new Telegram attachment transmission. Raw values and sources are included; separately review external
sharing only within the user-specified scope.
