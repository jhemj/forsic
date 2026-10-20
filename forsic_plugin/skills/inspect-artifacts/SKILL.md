---
name: inspect-artifacts
description: Use installed Dissect and a verified selected-file helper to parse Windows Registry, Prefetch, Amcache, and Linux authentication/login traces, preserving source locations and interpretation limits.
---
# Targeted artifact inspection

For bounded initial login-record parsing use `forsic_shell(action="triage", profile="linux_login_parser", login_format="linux-glibc-x86-le-384", targets=[...])` after confirming that source ABI. Select complete retained evidence_file or workspace_export source handles/versions and pointers. This parses record structure; it is not an anomaly detector. The profile processes files sequentially, caps 8 files/32 MiB combined/300 seconds of container runtime/2000 records per file/20 returned candidates, and returns complete observed counts with explicit omissions and partial/failed/skipped scopes. Staging and input hash checks precede the container runtime budget and are limited by the 32 MiB input cap. Users, hosts, raw records and diagnostics remain private. Returned record-type labels and source/version/pointer calls plus byte offsets support source recheck; adopt important observations explicitly through existing question notes. A size divisible by 384 does not establish ABI compatibility, and zero records does not establish no logins.

Select only artifacts needed for the current question. Distinguish function availability from successful reading of this evidence. Keep originals read-only. For files inside images, follow forsic_skill(name="source-triage", file_path="references/image-access.md"). Keep derived copies/results in /work and link them to original paths, volumes, and hashes.

Read one relevant reference:
- Windows keys/execution traces: forsic_skill(name="inspect-artifacts", file_path="references/windows.md")
- Linux authentication/logins: forsic_skill(name="inspect-artifacts", file_path="references/linux.md")
- Versions/runtime limits: forsic_skill(name="inspect-artifacts", file_path="references/runtime.md")

In forsic_shell, check installed versions, help, and target compatibility; run only selected functions. For selected utmp/wtmp/btmp files, use the verified Linux-reference helper for structure, types, raw times, and byte coordinates; state the supported ABI. Do not guess sizes or type labels with ad hoc scripts.
Shell returns and parser records are derived_command_output. Exact C1/C2 citations do not make them direct source observations. Trace important records to source lines/bytes, hive keys/values, or binary structures. Recover missing coordinates separately or disclose the limit.

Record original/copy locations and hashes, parser/function/version, exact commands and scope, returned fields/locators, stderr, exit codes, failures, skips, and limits. Zero rows or exit 0 alone establish neither absence nor completeness. Distinguish raw recorded times, parser-supplied years/timezones, and analytical event times; read correlate-time as needed. Treat configuration, traces, execution, success, and actor attribution as separate claims. Connect results to the existing answer/evidence through answer-question. Installation, updates, evidence execution, and runtime web lookup are unnecessary.
