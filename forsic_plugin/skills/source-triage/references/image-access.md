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
ia list --filesystem-view target --file-path '/'
ia read_bytes --filesystem-view target --file-path '/etc/hostname' --offset 0 --length 4096
ia export --filesystem-view target --file-path '/var/log/example.log' --destination '/work/linux-example.log'
```

Compose commands with functions, quoted paths, and `"$@"` rather than string variables. Replace paths/volumes
with actual returned targets. Choose backends from actual support listings; read allocated XFS files through the
helper above. Verify segments by matching E01/E02… names and header order; exclude same-named TXT files. With
multiple physical volumes, select a returned offset. `filesystem_view` defaults to `physical` in the native
`forsic_image_files`, `forsic_image_read`, and `forsic_image_search` tools. Set it explicitly to `target`
(or CLI `--filesystem-view target`) for Dissect OS/LVM detection and the mounted virtual filesystem.
Do not combine target view with `volume_offset`; target paths belong to the mounted OS namespace.
Results record the view and observed mount paths/types rather than claiming one physical offset.
`archive_list`/`archive_read` currently require physical view. Keep the same view in continuations;
search cursors cannot cross filesystem views or changed mounts. `stat`, `hash`, and `search` are also supported. Follow actual scope
and continuation information for partial returns. Listing discovery is not content reading.

`export` copies one regular file from the image to a new `/work` file. Relative destinations are anchored to
`/work`; use the absolute destination returned by the helper. The parent folder must exist; existing files are
not overwritten. The maximum is 256 MiB; `--max-bytes` can reduce this. Initial triage has a separate 32 MiB
combined input limit and at most eight targets; check the selected profile before exporting. Original images
stay read-only under `/evidence`, including host-mounted network shares. Export only selected files needed by
a file-based parser, not the whole image. Returned bytes/sha256
describe the bytes actually exported (`hash_scope=exported_bytes`). Only a successful complete export
adds `sha256_full_file`; on failure the captured-byte hash does not imply a complete file or a surviving artifact. Failure cleans up incomplete copies without modifying originals or other
results. This does not execute derived copies. Terminal results and copies are derived evidence; do not automatically
elevate them to direct original observations, full check coverage, or exact citations.

For ordinary listings, searches, original-content reads and copies, use the native tools or CLI above;
Linux/LVM does not require generated Python. The shared reader uses validated read-only sibling segment
handles and a bounded EWF container without changing global Dissect state. Each image invocation closes its
container and segment handles on success and failure. Batch integrations must keep target/filesystem/file
objects inside `open_image(path, filesystem_view='target')` or `open_target(path)` contexts; they do not
create writable operating-system mounts. `image_reader.py IMAGE JSON_OBJECT` remains the backend JSON
entry point; inspect its JSON errors even when exit code is 0.

Choose tools from `forsic_shell` capabilities observations: module discovery does not prove import or target
support, and `/dev/fuse` presence does not prove mount permission. Use `ewfexport -h` for export options.
If a raw copy is needed, inspect the export's returned messages and actual output names: an output basename
can produce a `.raw` filename. Do not assume the requested basename exists. Match TSK's returned image and
filesystem support lists before choosing it; do not infer XFS or LVM support from executable presence.

For static checks of existing workspace copies, use `forsic_shell` with `action="static_scan"`,
`paths=["/work/selected-copy"]`, `mode="strings"` or `mode="literal"`, and optional literal `text`.
This returns byte offsets, capture/hash scope and separate per-file missing, read-failed, no-match,
match and partial outcomes without a Bash pipeline. It does not attest original-image provenance.
Keep `run` requests within the advertised timeout (at most 300 seconds, lower when the native
deadline requires it); split independent work and reuse completed exports instead of reopening images.

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
when a stateful script is needed; handle expected diagnostic branches explicitly (for example, `if command; then ...; else rc=$?; ...; fi`),
including grep/rg no-match status 1; preserve unexpected errors, never blanket `|| true`. An arbitrary
script's internal stages remain unobserved even when it prints its own status markers. Process success does not prove
coverage or a forensic conclusion. For completion_unknown, query the native execution before retrying.

Keep export JSON complete: do not pipe it through `head` or `tail`. Copy `retained_export_target` or one exact
`retained_export_targets` entry into the selected scanner call. A labelled multi-export result supplies an exact
`export_line`. Those coordinates select retained output, not original
image offsets. The scanner rechecks ownership, source version, completion, path containment and exported-byte hash.
