# Verified execution surface and scope

Verification baseline: dissect.target 3.25.1, Python 3.12. Initial synthetic checks used the existing reader image forsic-dissect:3.25.1 (image ID a95fe9da96f8 at verification). Shell-analysis and reader images have different roles; image names alone do not establish installed support. Recheck versions/functions in the actual forsic_shell runtime.

```sh
python -c 'import importlib.metadata as m; print(m.version("dissect.target"))'
target-query --help
target-reg --help
target-query -f prefetch --help
target-query -f amcache.application_files --help
target-query -f authlog --help
target-query -f utmp --help
python /opt/forsic/login_records.py --help
```

target-query selects functions with `-f` and emits JSON records with `-j`. Distinguish JSONL `_type: recorddescriptor` schema rows from actual `_type: record` rows; do not count schema rows as events. Use `--no-cache`; preserve stdout/stderr and producer status separately in /work. If needed, `--limit N` limits output, without guaranteeing complete inspection or limiting all parsing. `-l` lists plugins, not successful evidence inspection; `--dry-run` is not execution. Do not default to all functions, whole disks, or recursive child targets.

```sh
parser_code=0
target-query '/work/selected-root' -f authlog -j --no-cache \
  > '/work/authlog.jsonl' 2> '/work/authlog.stderr' || parser_code=$?
python - '/work/authlog.jsonl' "$parser_code" <<'PY'
import json, sys
counts = {}
with open(sys.argv[1]) as source:
    for line in source:
        row = json.loads(line)
        kind = row.get('_type', 'unknown')
        counts[kind] = counts.get(kind, 0) + 1
print(json.dumps({'producer_exit_code': int(sys.argv[2]), 'rows_by_type': counts}))
PY
```

Adapt paths to the actual target. This example counts returned rows, not inspected artifacts or successful events. A here-doc supplies Python code through stdin, so pass data in a file argument. Use `json.load(file)` for one JSON document; use per-line `json.loads` for JSONL. Never pipe data into the same stdin consumed by a here-doc. Reuse valid retained outputs when repairing the consumer; rerun a producer only when its own execution failed or its scope must change.

Directory loaders require OS layout and metadata. Placing one file in a directory does not preserve OS detection, timezone, users, or Windows environment-variable resolution. Do not invent missing metadata as case facts. For standalone files, use explicit file parsers/APIs in the references below. Do not assume a whole-image automatic loader selects the same volume as existing tools; verify selected volumes through existing image_access.

Check exit codes and stderr alongside actual returns. Some plugins warn on file errors and continue. Do not report partial results as successful full inspection. Distinguish failure, unsupported, no data, and unexamined. Do not hide failure traces with `--quiet`.

## Sources

Pinned source tag 3.25.1 resolves to commit `a5d10e4355ff64dda79e90bb7643550c688948a5`.
[CLI source](https://github.com/fox-it/dissect.target/tree/a5d10e4355ff64dda79e90bb7643550c688948a5/dissect/target/tools),
[Windows plugins](https://github.com/fox-it/dissect.target/tree/a5d10e4355ff64dda79e90bb7643550c688948a5/dissect/target/plugins/os/windows),
[Unix log plugins](https://github.com/fox-it/dissect.target/tree/a5d10e4355ff64dda79e90bb7643550c688948a5/dissect/target/plugins/os/unix/log).
Upstream dissect.target is AGPL-3.0; this is an independently written procedure, not copied code. Installed 3.25.1 sources/help and synthetic data verified these interfaces. Real hive-damage recovery, diverse OS builds, real-evidence completeness, and investigative effectiveness were not verified. Do not browse or update upstream during investigation.

The selected utmp/wtmp/btmp helper is separate `forsic.login_records` v1, using only Python's standard library. Format/constants derive from glibc `bits/utmp.h` and `bits/utmpx.h`. Compiled `sizeof`/`offsetof` checks against development x86_64 glibc headers and synthetic fixtures built with independent offsets verified the 384-byte structure, type7/8, raw time/microseconds, input coordinates/hashes, tails, and unsupported inputs. These are offline contract checks for the Linux reference's stated format, not validation of case effectiveness or other ABIs.
