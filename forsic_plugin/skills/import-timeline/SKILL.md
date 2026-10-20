---
name: import-timeline
description: Parse selected files offline with Plaso, preserving raw times, provenance, and transformations; adopt only needed events into the existing question timeline.
---
# Import a targeted timeline

In forsic_shell, check `log2timeline.py --version`, `psort.py --version`, and `--help`. The analysis image pins Plaso 20260720. This CLI uses /usr/bin/python3, separate from the general shell's Python/Dissect. Do not use runtime installation/updates or remote analysis plugins.

Select only files/parsers needed for the current question. Preserve original image/volume/internal paths/hashes, export-copy hashes, parser chain/version, commands, filters, and errors in /work. Do not default to unlimited whole-disk supertimelines. Follow existing 8GiB/2CPU/64PID/maximum1800-second limits. Check 20260720 parser names with `log2timeline.py --parsers list`; traditional syslog is `text/syslog_traditional`, not `syslog`.

```sh
log2timeline.py --single_process --unattended --parsers text/syslog_traditional \
  --preferred_year 2026 --archives none --vss_stores none --status_view none \
  --temporary_directory /work --logfile /work/timeline-extract.log \
  --storage_file /work/selected.plaso /work/selected-syslog.log \
  > /work/timeline-extract.stdout 2> /work/timeline-extract.stderr
psort.py -a -o json_line --status_view none --temporary_directory /work \
  --logfile /work/timeline-export.log -w /work/selected-timeline.jsonl \
  /work/selected.plaso > /work/timeline-export.stdout 2> /work/timeline-export.stderr
```

Replace the example year with an evidence-verified assumption. Do not treat yearless/timezoneless syslog as confirmed UTC. Separately preserve raw timestamp strings, source line/byte ranges, applied year/timezone, precision, and clock domain; connect through correlate-time observed/inferred/bounds contracts. Plaso timestamp/date_time are derived, not replacements for original strings.

json_line pathspec/display_name/hash identify files, not exact source-line coordinates. Reread adopted events and bind source_ref plus line/byte spans or format-specific record locators. If multiple originals match, retain multiple candidates rather than guessing one line. Record parsing failures, skips, retention gaps, unsupported formats, and actual input/output scope.

`psort -a` disables output-stage deduplication; it does not undo extraction/storage merging. In pinned-version synthetic checks, identical syslog time/content on two lines produced one event despite -a. Do not treat event counts as original occurrence/independent-evidence counts; verify in originals. Preserve `.plaso` and source manifests; adopt only selected important events into existing forsic_question.timeline. Parser output is derived_command_output, not exact source quotation or a separate judgment ledger.
