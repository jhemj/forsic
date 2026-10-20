---
name: query-structured-logs
description: Aggregate and join /work CSV/JSONL logs with offline SQL, preserving source byte coordinates, input hashes, failure scope, and reproducibility records.
---
# Query structured logs

Use for aggregation, joins, or time-window comparisons needed by the current question. Existing Python/search may suffice for small tables/simple strings. First export selected EVTX or other specialist forensic formats to CSV/JSONL through their own parsers. This helper does not replace specialist parsers.

Originals are read-only. Prepare only needed derived copies in `/work` inside `forsic_shell`; link original image/volume/internal path, export/parser version, and original/copy hashes. Follow forsic_skill(name="source-triage", file_path="references/image-access.md") for image exports. Helper copy hashes/row numbers alone do not establish direct observation of original-image records.

Check `python /opt/forsic/structured_query.py --help` and installed `duckdb` version. Supported pin: `1.5.6`. If absent, record unsupported; do not bypass through installation, automatic extension loading, or remote connections. Read forsic_skill(name="query-structured-logs", file_path="references/helper.md") for commands, schema, and numeric limits.

Choose needed columns, scope, join keys, and raw timestamp fields; save SQL in `/work`. Input fields default to strings, without automatic type/timezone inference. State required CASTs/timezone assumptions in SQL and the current question. Count unconvertible values separately first. Do not interpret `TRY_CAST` NULL as normality/absence or conceal rows dropped by joins. For source rereads select `_forsic_source_sha256`, `_forsic_row`, `_forsic_byte_start`, `_forsic_byte_end`, `_forsic_raw_timestamp`. Include both sources in joins. Aggregates retain an input manifest but do not automatically bind row-level evidence: separately check supporting representative rows and exclusions.

The helper saves new results and a receipt in `/work`, returning a small summary. Do not load full logs/results into model context. Query needed aggregates, at most a few rows, and receipt input/failure/omission counts. Default behavior fails on even one invalid row. Use `--skip-invalid` only for justified exclusions; assess whether failure locations/counts could change the answer. Output `result_truncated` establishes neither input-inspection completion nor total result count.

Adopt only important observations into existing `forsic_question`. Shell output remains `derived_command_output`, even with exact returned-string coordinates; it is not direct source observation. Use forsic_skill(name="correlate-time")'s existing contract for raw times, inferred timezones, and clock assumptions. Do not create a new question/timeline/evidence ledger or separate investigation loop.
