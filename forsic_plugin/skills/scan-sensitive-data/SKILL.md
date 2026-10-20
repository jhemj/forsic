---
name: scan-sensitive-data
description: Find credential and Korean personal-data candidates in selected retained evidence through the offline structured scan, preserving scope and separating detection from incident conclusions.
---
# Inspect selected sensitive-data candidates

Use when a current question needs credential traces or personal-data content/scale. Inspect existing results first.
Select question-relevant files or retained text scalars from actual returned source handles; never invent a source,
version, pointer, path, or output directory. A report gap alone does not justify a whole-disk scan.

Call forsic_shell with action=sensitive_scan, targets=[{source_id,source_version,pointer,kind?,encoding?,format?}],
profiles=["secrets","pii_kr"], and mode="triage" or "scope_count". Use only profiles needed for the question.
Targets must identify retained text or host-approved evidence-file references returned by existing tools.
The default kind=retained_text examines only the selected retained body scalar (for example /lines/0/text),
not the full original. kind=evidence_file selects an exact retained original-path metadata pointer from an
existing list/read/hash/search result. kind=workspace_export uses /stdout of one complete current-session
image_access export JSON receipt; its hash-bound /work export remains derived output. Do not manually write
paths or treat a directory/container as a file. source_handle is an alternative to source_id/source_version.
Check returned input_role and coverage before interpreting scope. Optional format is text/csv/jsonl;
encoding is auto/utf-8/utf-16/cp949 where supported.
Use resume_ref only to replay the exact retained scan with the same targets/profiles/mode; it is not a new scan
or automatic continuation over unread files.
The host validates identity, scope, decoding, capabilities and bounds. Read actual status/limitations, not only exit codes.

- triage locates candidates and small bounded examples; scope_count requests occurrence/value-group counts in the
  supported selected scope. Neither mode establishes people, valid credentials, complete holdings or exfiltration.
- Read returned safe candidates, source coordinates, coverage and continuation handles. Generic shell stdout,
  native scanner reports, private artifact paths and base64 are not alternative ways to recover raw matches.
- Do not execute evidence commands or test credentials online. Detecting a key does not prove issuance, validity,
  attacker use, external exposure, or transfer. Compare context and independent access/movement records.
- Unsupported format/encoding, encrypted content, truncated text, timeout, skip and unavailable engines are distinct
  from completed scans without candidates. A negative result applies only to supported profiles and inspected ranges.
- Save adopted observations, contextual judgment, counterarguments and unread scope through the existing
  forsic_question writer using returned safe source/citation handles. Retain candidate IDs and value-group units;
  repeated hits/copies are not independent people or evidence. Do not put raw secrets or personal values in notes,
  chat, review requests, main report prose or public fixtures.

Use existing personal_data_presence and, with separate evidence, external_disclosure report slots. Credential
findings may inform entry_route/attacker_actions/decisive_evidence without creating a new required dimension.
The local integrated HTML/Word technical appendix automatically includes bounded original excerpts only for
selected/adopted report sources, with exact byte locations and omission counts. It is local-only and excluded
from automatic Telegram document delivery. Manual local downloads remain available. Do not copy that appendix
into model/review material or request raw originals through generic source lookup.

Load only the needed reference: [profiles](references/profiles.md),
[privacy and evidence](references/privacy-and-evidence.md), [Korean data](references/korean-data.md).
