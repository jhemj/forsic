# Image access and derived copies

The dedicated image tools and terminal functions below use the same E01 reader. Use existing tools for ordinary
listings, searches, and original-content inspection; choose terminal functions for scripts or file copies.
No separate preparation call or mandatory sequence is required.

```sh
python /opt/forsic/image_access.py --help
ia() { python /opt/forsic/image_access.py '/evidence/disk image.e01' "$@"; }
ia volumes
ia list --volume-offset 4096 --file-path '/'
ia read --volume-offset 4096 --file-path '/var/log/example.log' --start-line 1
ia read_bytes --volume-offset 4096 --file-path '/var/log/example.log' --offset 0 --length 4096
ia export --volume-offset 4096 --file-path '/var/log/example.log' --destination '/work/example.log'
```

Compose commands with functions, quoted paths, and `"$@"` rather than string variables. Replace paths/volumes
with actual returned targets. Choose backends from actual support listings; read allocated XFS files through the
helper above. Verify segments by matching E01/E02… names and header order; exclude same-named TXT files. With
multiple volumes, select a returned offset. `stat`, `hash`, and `search` are also supported. Follow actual scope
and continuation information for partial returns. Listing discovery is not content reading.

`export` copies one regular file from the image to a new `/work` file. Relative destinations are anchored to
`/work`; use the absolute destination returned by the helper. The parent folder must exist; existing files are
not overwritten. The maximum is 256 MiB; `--max-bytes` can reduce this. Initial triage has a separate 32 MiB
combined input limit and at most eight targets; check the selected profile before exporting. Original images
stay read-only under `/evidence`, including host-mounted network shares. Export only selected files needed by
a file-based parser, not the whole image. Returned bytes/sha256
describe the bytes actually exported. Failure cleans up incomplete copies without modifying originals or other
results. This does not execute derived copies. Terminal results and copies are derived evidence; do not automatically
elevate them to direct original observations, full check coverage, or exact citations.

Batch scripts can add `/opt/forsic` to Python's search path and use `from image_reader import open_image`.
Within `with open_image(path) as image:`, use `volume, fs = image.filesystem(volume_offset)` to read the selected
filesystem. Do not use image handles outside the block. For ordinary work, the CLI returns boundaries with results.
`image_reader.py IMAGE JSON_OBJECT` is the existing backend JSON entry point. Check user options through
`image_access.py --help`. Check internal adapter JSON errors too; backend-compatible exit code 0 does not mean
successful reading.

The terminal uses Bash `-e -o pipefail` to report unhandled command/pipeline failures. Only exit 1 from a basic
single grep/rg means zero search matches; do not infer execution scope from the last command name in compound
commands/pipelines. Read command_outcome with raw exit_code/stderr. capabilities image_id and helpers SHA-256
are actual environment observations for that call, separate from format support, target success, or execution
authorization. When the environment changes, do not reuse past observations as current facts.

Use separate native tool calls for independent checks. Each call retains its own execution result:

```json
{"action":"run","command":"grep -c -F -- 'selected literal' /work/example.log"}
```

```json
{"action":"run","command":"wc -c < /work/example.log"}
```

Choose the next check from the returned result; no extra batch executor is needed. Read the actual export receipt
before assuming a file exists. Do not repeat successful calls to repair another failure. Use a single `command`
when a stateful script is needed; handle expected no-match branches explicitly, never blanket `|| true`. An arbitrary
script's internal stages remain unobserved even when it prints its own status markers. Process success does not prove
coverage or a forensic conclusion. For completion_unknown, query the native execution before retrying.

Keep export JSON complete: do not pipe it through `head` or `tail`. Copy `retained_export_target` or one exact
`retained_export_targets` entry into the selected scanner call. A labelled multi-export result supplies an exact
`export_line`. Those coordinates select retained output, not original
image offsets. The scanner rechecks ownership, source version, completion, path containment and exported-byte hash.
