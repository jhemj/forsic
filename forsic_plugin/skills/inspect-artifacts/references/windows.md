# Windows: selected keys and execution traces

## Registry

For an already correctly configured whole-OS target, query exact keys/values:

```sh
target-reg '/work/selected-windows-root' \
  -k 'HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Run' -d 0 -l 4096
```

In 3.25.1, `target-reg` outputs text. Default value length is 100 characters; even with a larger `-l`, values exceeding the selected length are truncated. `-kv 'ValueName'` selects a value. Missing keys/values or unsupported registry may still return exit 0; check logs and actual output. HKCU may cover multiple user hives: identify the SID and actual NTUSER.DAT. Verify SYSTEM Select resolution for CurrentControlSet. Do not double-count RegBack/current hives as independent observations.

An exported individual hive can be read explicitly with `RegfHive`. Adapt example paths/keys.

```python
from pathlib import Path
from dissect.target.helpers.regutil import RegfHive
path = Path('/work/SOFTWARE')
with path.open('rb') as fh:
    hive = RegfHive(path, fh=fh)
    key = hive.key(r'Microsoft\Windows\CurrentVersion\Run')
    print(key.path, key.timestamp)
    for value in key.values():
        print(value.name, value.type, repr(value.value))
```

Record original hive path/hash, full key path, value name/type/raw value, and key last-write. Record exceptions as failures. Last-write is not an individual value's creation/execution time. Do not assume log replay or deleted-key recovery occurred automatically. A Run value observes configuration; verify execution/success through separate traces.

## Prefetch

For OS targets, use `target-query TARGET -f prefetch --compact -j --no-cache`. One `--compact` record combines the PF file, latest run time, previousruns, linkedfiles, and runcount. Default mode may generate linked-file × run-time rows; do not count rows as executions. The `prefetch` field may contain only a filename; preserve its full source path separately.

For an exported PF file, parse needed fields through the explicit file API:

```python
from dissect.target.plugins.os.windows.prefetch import Prefetch
with open('/work/selected.pf', 'rb') as fh:
    pf = Prefetch(fh)
    print(pf.version, pf.latest_timestamp, pf.previous_timestamps, pf.fn.run_count)
    print(pf.header.name, pf.metrics)
```

The 3.25.1 implementation handles PF version 17/23/30/31 and MAM compression. Check actual versions/errors. Interpret execution traces with system generation conditions, retention scope, and possible tampering in mind. linkedfiles lists associated files, not proof each executed. PF absence does not establish execution absence.

## Amcache

Default hive location: Windows/appcompat/programs/amcache.hve. Select one question-relevant function on a supported OS target; do not default to the whole `amcache` namespace.

```sh
target-query '/work/selected-windows-root' -f amcache.application_files -j --no-cache
target-query '/work/selected-windows-root' -f amcache.applications -j --no-cache
```

3.25.1 distinguishes old Root\File/Programs and InventoryApplication/InventoryApplicationFile families. Check this evidence's layout and actual output schema. For standalone hive copies, first read selected raw key values with RegfHive above. Automatic `amcache.*` mapping is not guaranteed without a full OS target. Disclose output that cannot be linked to hive paths/keys/values. Distinguish path, FileId-derived digest, program_id, and mtime_regf/install_date/link_date; do not assume a digest hashes the entire current executable. Amcache listings/installation information alone establish neither execution, actor, nor execution time. PE link_date especially is not execution time. Compare PF, actual process logs, or service/task records as needed.
