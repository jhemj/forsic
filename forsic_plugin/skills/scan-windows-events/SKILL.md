---
name: scan-windows-events
description: Find investigative candidates in selected Windows EVTX with offline Hayabusa rules, then reread original events and rules.
---
# Rule candidates in Windows events

For bounded initial triage prefer `forsic_shell(action="triage", profile="windows_evtx", targets=[...])`. Copy exact retained source handles/versions and file-location pointers with kind=evidence_file or workspace_export; never pass a directory or whole image. The profile stages exact bytes privately, checks the installed pinned rule/config hashes, and runs selected files sequentially. Limits: 8 files, 32 MiB combined input, 300 seconds of container runtime, 20 returned candidates, 8 MiB engine output per file. Input staging and hash validation are separate byte-bounded preprocessing. Larger EVTX requires an explicitly selected different supported analysis scope; do not truncate EVTX to fit.

The typed result returns observed candidate counts, omissions, safe trusted rule titles/severity where available, and exact source/version/pointer recheck calls. Copy `candidate_query.next_call` to page/filter retained metadata with `triage_query`, then `selection.next_call` for more. This does not rerun Hayabusa or expose private raw rows. Preserve `index_truncated` and filtered count scope. Its Windows coverage remains partial because successful Hayabusa exit alone cannot establish skipped-record/rule completeness. A source call may return the acquisition receipt, not a decoded event: recheck input hash, original event identity/time and installed rule before evidence adoption. Zero candidates do not establish absence. Sensitive text/PII uses the separate sensitive_scan action.

In forsic_shell, check `hayabusa dfir-timeline --help` and `/opt/forensic-tools/forensic-tools.lock.json`. If absent, record unsupported. Do not download tools/rules or run update-rules at runtime. Pinned Hayabusa 4.2.0 uses `dfir-timeline -t jsonl`.

Select only question-relevant EVTX channels/hosts/periods. Link image_access copies to original image, volume, internal path/hash, and copy hash. If the single-export 256MiB limit is exceeded, check existing supported access paths; do not truncate and pretend the result is EVTX. Preserve output, errors, and commands in /work.

```sh
hayabusa dfir-timeline -f /work/selected.evtx \
  -r /opt/forensic-tools/hayabusa/rules \
  -c /opt/forensic-tools/hayabusa/rules/config \
  -w -t jsonl -p super-verbose -O --threads 2 -K -b -F \
  -o /work/event-candidates.jsonl \
  > /work/event-scan.stdout 2> /work/event-scan.stderr
```

If needed, narrow rules to a reviewed individual rule/subfolder; record exact paths, filters, and hashes. `-O` emits UTC ISO time. Separately reread/preserve original `System/TimeCreated/@SystemTime`; detection Timestamp alone does not establish original precision/time semantics. The example's default full rule scope still follows known 2CPU/8GiB/64PID/maximum1800-second boundaries. Do not add sorting, deduplication, or carving by default. Check parse failures, skips, uncollected channels, excluded rules, and timeouts; disclose reviewed scope. Hayabusa may return exit 0 despite errors: inspect stdout errors/skips and /work/logs. An empty result file alone does not establish a successful no-hit scan.

For each candidate bind input EVTX hash/original location + Computer/Channel/Provider/RecordID + raw SystemTime to source backreferences. RecordID alone is not an identifier. Retain RuleID/RuleFile/RuleAuthor, pinned release asset SHA256, and rule hashes from rule-config-sha256.json. Bundled rules differ from the engine tag's submodule commit: do not cite that commit as actual bundle identity. Preserve DRL rule authors, individual licenses, and provenance. Read original events/rule conditions; corroborate execution/success/actor attribution independently before adopting important findings into existing question notes. Hits are candidates; misses do not prove absence. Shell results are derived_command_output, not substitutes for exact source citations.
