# Offline structured-query helper

Use only within existing `forsic_shell` boundaries: network none, read-only originals, 8GiB, 2CPU. The trusted helper must be provided read-only at `/opt/forsic/structured_query.py`. Versions other than `duckdb==1.5.6` are rejected. `--help` works without dependencies but does not establish query success. Do not install packages/extensions at runtime.

## Usage

CSV requires UTF-8 comma-separated data with a header; an initial header UTF-8 BOM is allowed. Quoted newlines and CRLF are handled. JSONL requires one JSON object per physical line. Invalid UTF-8, duplicate JSON keys, arrays/scalars, and blank lines fail as JSONL rows. Invalid CSV quoting/encoding fails the whole input because boundaries cannot be safely recovered. Compressed inputs, automatic format/encoding inference, and CSV delimiter autodetection are unsupported.

Example assumes `/work/events.jsonl` has `user` and `timestamp`; check the actual schema first.

```bash
cat > /work/query.sql <<'SQL'
SELECT user, count(*) AS records
FROM events
GROUP BY user
ORDER BY records DESC, user
SQL
printf '{"step":"structured_query","state":"started"}\n'
if python /opt/forsic/structured_query.py \
  --source events=/work/events.jsonl \
  --timestamp events=timestamp \
  --sql-file /work/query.sql --output /work/user-counts --max-rows 20; then
  query_code=0
else
  query_code=$?
fi
printf '{"step":"structured_query","state":"returned","exit_code":%s}\n' "$query_code"
```

Add files through options such as `--source accounts=/work/accounts.csv` and join in SQL. Do not repeat table names. Names must start with a lowercase ASCII letter and contain at most 32 letters/digits/underscores. Column names must be unique ignoring case; `_forsic_` is reserved. All input/SQL/output paths must be absolute under `/work`. Parent traversal, symlinks, FIFOs, and other special files are rejected. Do not overwrite inputs/existing results. Output parents must already exist; use a new prefix.

Only one SELECT is allowed; CTEs, aggregates, joins, and explicit CAST are supported. Do not heuristically rewrite SQL or append LIMIT. The DuckDB parser checks statement type; execution uses a read-only transaction after loading. COPY, INSTALL, LOAD, ATTACH, SET, PRAGMA, DDL, DML, and multiple statements are rejected. File readers, URLs, extensions, and Python replacement scans are disabled, including external reads within SELECT. Input is read only with Python's standard library into temporary in-memory tables; no JSON extension install/load is needed.

## Results and provenance

Outputs: `<prefix>.rows.jsonl` and `<prefix>.receipt.json`. stdout contains only paths, hashes, returned/excluded counts, and truncation status. Rows are JSON arrays in column order; receipt `result_columns` supplies names/SQL types. Read only needed ranges. Failures exit 2 with small JSON stderr errors, preserving actual DuckDB error type, up to 1,000 JSON characters of original error, and truncation status. Errors may contain SQL, column names, and failed-conversion values; evidence quotations are not trusted commands. Resolve missing columns/format errors against diagnostics and receipt/actual schema. Forced termination can leave partial files: verify receipt and result SHA-256 before use.
The conditional preserves actual helper status for independent checks. Do not run aggregates dependent on failed results. Final shell exit 0 or derived-stage output alone does not establish query success: check query_code and JSON error/summary. Use returned rows_path/receipt_path unchanged. result_bytes/result_sha256 describe the actual rows file. Empty rows may be valid SELECT output; do not treat all zero-byte results as generation failure.

All input columns are VARCHAR. Preserve CSV `001`, empty strings, and raw times. JSON strings/numeric tokens remain strings without changing decimal precision or exponent notation. Booleans and nested arrays/objects remain JSON text; JSON null loads as SQL NULL. Later JSONL columns are added with NULL in preceding rows. Column/object-key ordering is derived representation, not original character/byte layout.

Reserved provenance columns:

| Column | Meaning |
| --- | --- |
| `_forsic_source_sha256` | SHA-256 of the entire loaded copy |
| `_forsic_source_path` | Current `/work` copy path |
| `_forsic_row` | 1-based physical JSONL line or 1-based logical CSV record excluding header |
| `_forsic_byte_start` / `_forsic_byte_end` | Zero-based half-open `[start,end)` source-copy byte range, including newline |
| `_forsic_raw_timestamp` | Raw value of the column specified by `--timestamp TABLE=FIELD`; NULL if unselected, JSON null, or missing |

All time columns remain in input columns whether selected separately or not. No time field is guessed; numeric timestamps receive no automatic units/timezone/role. Before source rereads, verify current full-copy hash against receipt, then read `[start,end)`. For source quotations, trace back through original export/parser provenance. Table-value positions are not original character coordinates.

Receipts retain helper/engine versions, input hashes/schemas, conversion rules, exact SQL/SQL hash, limits, accepted/rejected counts, up to 10 failure locations, and result hash. Reject results if input hashes/file state change during reading. Receipts also have SHA-256; without execution time/random IDs, identical inputs/helper/version/SQL/options and stable ordering produce identical receipts. Resolve `ORDER BY` ties and avoid random/current_timestamp or other nondeterministic expressions. A receipt is neither a cryptographic signature nor a guarantee of original-evidence authenticity.

## Limits and failure scope

At most 8 inputs, 256MiB each/512MiB total, 1,000,000 rows per file, 256 input columns, and 1MiB per physical line/CSV record. SQL is at most 64KiB. Timeout includes loading: default 60 seconds, maximum 300. Do not bypass it by increasing shell limits for a longer separate query. For large data, select meaningful independent scopes into derived files and record selection/exclusions.

DuckDB uses a 1GB memory limit, 1 thread, zero temporary spill bytes, disabled automatic extension install/load and external access, configuration lock, and SQL session TimeZone UTC. UTC session defaults do not identify raw timestamp timezones. The engine memory limit is not a whole-process cap: Python return rows/JSON serialization allocate outside it, so the existing 8GiB container boundary remains necessary. SQL timeouts use engine interrupt; retain the container's external timeout. These settings do not establish isolation against arbitrary native-engine vulnerabilities.

Returned rows default to 100; `--max-rows` allows at most 1,000; total stored result output is at most 1MiB. These are not memory caps during SQL result creation/Python row serialization. A row exceeding the output limit is omitted with `result_truncated=true`, not a total-row count. Distinguish output truncation from raw-input truncation/failure. `--skip-invalid` counts/excludes only invalid JSONL rows and CSV column-count errors. CSV encoding/quoting errors, schema conflicts, and resource limits still fail the whole operation, without a successful receipt.

## Version, license, and official sources

Verified 2026-10-19: `duckdb==1.5.6`, MIT license, Python >=3.10, with Python 3.12 manylinux x86-64/aarch64 wheels. Extra JSON/httpfs extensions are not shipped. Bind each platform wheel hash to its artifact in the build lock.

- [Official PyPI release and Python/wheel metadata](https://pypi.org/project/duckdb/1.5.6/)
- [Pinned engine release MIT license](https://github.com/duckdb/duckdb/blob/v1.5.6/LICENSE)
- [Official security boundaries, external access/extensions, and resources](https://duckdb.org/docs/current/operations_manual/securing_duckdb/overview)
- [Official connection settings](https://duckdb.org/docs/current/configuration/overview)

Current docs may change. Before changing versions, rerun synthetic CSV/JSONL, provenance, read-only, escape-rejection, timeout, and output-limit checks against the implementation's actual pin.
