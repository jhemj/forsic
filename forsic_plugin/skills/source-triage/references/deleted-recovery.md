# Deleted-evidence recovery and carving

For deleted files or missing executables important to the question, define expected added value and scope.
Build declarations include PhotoRec 7.2, Sleuth Kit 4.12.1, and libewf tools. Check actual container executables,
versions, and format lists through `forsic_shell(action="capabilities")`. This neither reads evidence nor attempts
recovery. Missing executables, failed checks, unverified format support, unattempted recovery, and lack of
authorization differ. Installation/help does not establish support for the selected target or recovery results.
Use relevant command help to check target/scope support.

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
