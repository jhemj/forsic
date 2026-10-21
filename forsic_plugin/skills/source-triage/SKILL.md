---
name: source-triage
description: Find question-relevant logs, autorun settings, and file traces in Linux images and extracted evidence.
---
# Explore evidence and extract leads
Inspect actual directories before choosing evidence for the question. For access, examine authentication logs;
for scheduled execution, cron/systemd settings and execution logs; for command traces, shell history and results.
Use forsic_image_files(list) for E01 listings, forsic_image_search(path=이미지, file_path=내부 범위, text=문자열)
for search, and forsic_image_read for content. Use forsic_search/read for extracted files. For image processing or
derived copies in the terminal, use the shared reader `python /opt/forsic/image_access.py`. Read
`references/image-access.md` for detailed examples when needed. Set file_path and text, and recursive when needed,
for search. Check omitted scope and subsequent pages; read original context around search lines for important
claims. Continue long lines with read_bytes. If a file path is missing, find its actual name in the parent listing.
Copying search next_call passes the selected source_ref as cursor_ref to continue precisely. Do not rewrite cursor
internals. Preserve explicit scope such as file_path/text. A page can end within a file; scan_exhausted on the last
page does not mean all previous scope was reviewed. Keep skipped files, regions, and formats as unchecked scope;
do not present returned counts as total matches.
Distinguish settings, attempts, success, authorization, and attribution. A path alone establishes neither product
identity nor maliciousness. Save important findings with selected evidence through forsic_question; stop aimless
sweeping when the question can be answered.

## Bounded initial candidates

For a comprehensive investigation, perform a useful bounded initial scan before a manual sweep after checking
actual OS/format, capabilities and inputs. Choose applicable scope and profiles; this is not an all-file or
all-profile checklist. Reuse a matching completed scan. If not applicable, unsupported, missing input, failed or
deferred, update the existing question's answer/gaps with the actual reason and unreviewed scope, then continue
other useful work. Focused questions still choose checks by their value to the requested question.

When selected files match the question, prefer a bounded triage pass over repeated
manual parsing: `forsic_shell(action="triage", profile="windows_evtx", targets=...)`
for complete EVTX exports, or `profile="linux_login_parser"` with
`login_format="linux-glibc-x86-le-384"` for that explicitly verified login ABI.
Each target requires explicit `kind="evidence_file"` (returned selected-file path
pointer) or `kind="workspace_export"` (the exact returned pointer to a complete export
receipt), plus `source_handle` or exact `source_id`/`source_version`. Copy a returned
`retained_export_target` or one `retained_export_targets` entry, including its exact export_line when present;
it selects a receipt, and admission still
checks conversation ownership, complete-file status and file hash. For image inputs, export selected complete
files with the shared reader and preserve its returned JSON receipt streams. Copy the returned selector's exact
/stdout or /stderr pointer; do not discard both receipt streams or replace them with ls/cat output. A file in
/work alone is not a retained scan input. If its retained receipt is unavailable, re-export only the selected
bounded complete file to a NEW destination and preserve the returned receipt; retain the existing export.
Use the returned selector, not a guessed workspace path.
An admitted export remains a derived copy, not verified original lineage. `format="text"`
and `encoding="auto"` are byte-preserving defaults, including EVTX/login binary
inputs; they are not assertions that the file is text. Do not invent paths, target
kinds, profile formats or source versions. Image containers and directories are
not scan inputs. For selected Linux cron/systemd configurations use
`profile="linux_persistence"`, adding `triage_format="cron_user"`,
`"cron_system"`, or `"systemd_unit"` to each retained target after identifying its
actual format. This returns structural configured-entry candidates and exact
byte/line ranges, without command/value text. Cron system/user forms, standard
macros, comments/environment lines and systemd repeated/reset/continued directives
are covered as text structure; values remain opaque. Unsupported syntax or
encoding, limits and deadlines remain explicit unchecked scope. Configured text
does not establish effective configuration, enablement, execution or maliciousness.
`capabilities` describes OS/purpose recommendations and limits;
unknown OS has no default profile. Recommendations guide profile selection, not a scan checklist. Run one profile at
a time. Linux records are parsed observations, not anomaly detections. Discover
installed image/login helpers through the capability menu and their `--help`
before writing a parser; use `python /opt/forsic/image_access.py export` for a
selected complete file and `/opt/forsic/login_records.py` for its supported ABI.

Use returned candidates to propose hypotheses, then follow `source_call` and the
original record coordinates before adopting evidence in `forsic_question`.
Review the important competing explanation and save a useful provisional answer
while further checks remain open. Candidate count, severity and zero hits are not
compromise/absence judgments. Keep failed, partial and omitted scope explicit.
Reuse a matching completed scan with `resume_ref`; never repeat unknown execution.
For candidates beyond the first page, copy `candidate_query.next_call`, then
`selection.next_call`. `triage_query` uses the exact `scan_id`/`index_sha256`;
optional input, numeric record and rule-ID filters select retained metadata without
running the parser. Index truncation and original partial scope remain limitations.
`original_byte_call`, when supplied, rereads the selected file range. Other
`source_call` values may retrieve an acquisition receipt rather than an event body.
Do not run another scanner merely to accumulate corroborating hits from the same
input/rules. Credentials and personal-data checks use `scan-sensitive-data`.

Returned listings may include `filename_observations`: preserved names with display
controls, escaped forms and source pointers. They establish characters, not intent
or maliciousness; no observation is not a clean-image verdict.

## Separate listing classification from trust judgments

Initial classification is a hypothesis about where to investigate. Choose depth by impact and discriminating value.
After reviewing content, scope, and counterarguments, a sufficient benign explanation permits scoped closure under
forensic-investigation. Do not close on initial classification alone; reopen needed scope when counterevidence
appears. Familiar product, process, or directory names, internal addresses, and regular activity are compatible with
normal operation but establish neither trust, authorization, nor harmlessness.
Distinguish the named object from its actual identity, function, usage context, and authorization. For distinctions
important to the question, select evidence available through current tools: execution paths, command lines, file
contents, or peer roles. Do not inspect all objects indiscriminately; retain important unverified targets.

Network listings show addresses, ports, and states at observation time. Address ranges establish neither purpose
nor peer trust. Until direction, process, function, and record coverage are checked, address classification alone
cannot confirm or exclude malicious communication. Do not extend a connection's absence from a past snapshot to
absence throughout the investigation scope.

For partial listings, record returned row ranges and next_line/next_call. Questions requiring total, unique, or
absence claims require reading the necessary remainder or narrowing the claim. Do not infer organizational roles,
deployers, or authorization from filenames or account names when the source does not show them.

## Select specialized evidence only as needed

For allocated TAR/TAR.GZ reading and recovery limits, consult
forsic_skill(name="source-triage", file_path="references/archives-and-recovery.md") when needed.
If deleted-evidence recovery adds value, read `references/deleted-recovery.md` and check actual tools through
`forsic_shell(action="capabilities")`. Separately verify target-format support and check scope before choosing.
Do not extend an allocated-file reader limitation into unsupported recovery or lack of authorization in the
analysis terminal. Choose inspect-documents for document/spreadsheet structure or content and inspect-scripts
for script/configuration behavior. Finding a file does not require starting every specialized examination.

For relevant credential/personal-data content, select targets from actual retained metadata/source handles and use scan-sensitive-data instead of dumping raw bodies into model context. See [scan-sensitive-data](../scan-sensitive-data/SKILL.md).
