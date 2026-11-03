# Deleted-evidence recovery and carving

Consider targeted recovery alongside ordinary investigation when observations leave a material question gap:
for example, a relevant artifact is missing or deletion is supported, or allocated-only examination cannot answer
the question. State the supporting observation, question relevance, expected added value over existing reads,
artifact types, and selected image/partition/byte scope. A single `not_found`, empty search, unread item, parser
failure, or omission from a bounded review packet does not establish deletion. Explain uncertainty and compare
the likely value and cost with other discriminating checks; do not make full-disk carving or recovery a mandatory
phase. Ren may propose this check during early, checkpoint, or final review; the main investigator chooses,
adapts, or defers it. Ren remains read-only and cannot run tools or adopt evidence.

Establish feasibility from actual capabilities and scope; if unknown, propose the capability check rather than
claiming recovery is available, impossible, or already attempted. Distinguish technical limits from permission limits.
Build declarations include PhotoRec 7.2, Sleuth Kit 4.12.1, and libewf tools. Check actual container executables,
versions, and format lists through `forsic_shell(action="capabilities")`. This neither reads evidence nor attempts
recovery. Missing executables, failed checks, unverified format support, unattempted recovery, and lack of
authorization differ. Installation/help does not establish support for the selected target or recovery results.
Use relevant command help to check target/scope support.

Keep an observed unsupported-format result with its source reference in the current question's
limitations/next-test note. Reuse that result on continuation; retry only after a relevant input,
tool or method change. An unsupported filesystem reader does not establish that signature carving
is unavailable. Choose a bounded supported alternative when useful, or explain its remaining value
and why it is deferred. Neither capability lookup nor an unsuccessful command is recovered evidence.

- **Filesystem recovery:** Choose `fls`, `istat`, `icat`, or `tsk_recover` for supported filesystems.
  `tsk_recover` defaults to unallocated entries; `-a` selects allocated entries and `-e` selects both.
  For partitions, use `mmls` to check actual start positions and sector sizes. `-o` uses sectors, not bytes.
  This version does not support XFS metadata-based deleted recovery; distinguish it from allocated XFS reading.
- **Signature carving:** Use PhotoRec to select file types and regions relevant to the question. Attempts are
  possible without supported paths/deletion metadata, but fragmented or overwritten files may be incomplete or
  unrecoverable. Check CLI options with `photorec /help`. Set the output folder with `/d /work/선택한폴더`.
- **E01:** The current PhotoRec build lacks the EWF library and cannot open E01 directly. Distinguish this from
  TSK's EWF support. If needed, check `ewfexport -h` and export only needed byte ranges of the selected original
  as raw copies under `/work`. Consider the shell's 256 MiB per-file limit and remaining space. Do not
  unconditionally convert entire images. Preserve partial-copy start offsets and map recovered positions back
  to original coordinates. Files crossing region boundaries may be missed.

Originals are read-only under `/evidence`; outputs go under `/work`. Do not use TestDisk's original-modification
or partition-writing features. Record the selected original/segment set, tool versions/commands, filesystem,
partition/byte ranges, file-type filters, results/truncation/limits, derived-file hashes, and original locations.
No results means zero within that check's scope, not universal absence. Original names, paths, times, and execution
of carved files remain unknown without separate evidence. Inspect recovered executables statically without running
them. Recovery reading selected evidence is within existing isolated-analysis authorization; do not confuse
unsupported tools with lack of authorization. Separately assess needs for originals or networks outside scope or
permissions.

Official command documentation:
- https://www.sleuthkit.org/sleuthkit/man/tsk_recover.html
- https://www.cgsecurity.org/testdisk_doc/scripted_run.html
