# Linux: authentication and session traces

## authlog / securelog

`target-query TARGET -f authlog -j --no-cache` targets `/var/log/auth.log*` and `/var/log/secure*`, not all journal/audit/authentication backends. Verify successful reading of rotated files such as gzip and the actual inspected-file list. `ts/source/service/pid/message` and service-specific fields form a dynamic schema; do not invent missing fields. Relocate source lines through message/source; distinguish repeated messages with surrounding lines and line numbers/byte ranges.

Preserve raw ISO offsets. This version's ISO parser expects fractional seconds, as in `2024-01-01T00:00:00.000000+00:00`. Synthetic checks skipped ISO lines without fractions: inspect source format/warnings and read originals directly if needed. Yearless syslog may receive year-rollover inference from file mtime and target timezone; do not describe the entire parser ts as source-recorded time. Output is not guaranteed chronological. If mtime became collection time or OS timezone is missing, review year/timezone inference separately and retain source/correlate-time uncertainty bounds. Read Accepted, Failed, session opened, and sudo command according to their distinct meanings. Successful authentication differs from actual work; remote addresses differ from human identity.

## utmp / wtmp / btmp

Read selected-file types, times, and source coordinates with the verified helper `/opt/forsic/login_records.py`, provided read-only in `forsic_shell`. Do not invent temporary struct sizes/type arrays. glibc `bits/utmp.h`/`bits/utmpx.h` define **7=USER_PROCESS, 8=DEAD_PROCESS, 5=INIT_PROCESS**. Neither a btmp filename nor type=7 alone establishes authentication success/failure. Compare file role, user, host, terminal, PID, and original auth logs.

Aggregate file JSONL line by line as below. The here-doc uses stdin for code, so pass data through a file. Perform complex selected-record aggregation in the same Python loop.

```sh
parser_code=0
python /opt/forsic/login_records.py '/work/selected-wtmp' \
  --format linux-glibc-x86-le-384 \
  --origin-locator '선택 원본 파일 또는 이미지·볼륨 내부 경로' \
  > '/work/selected-wtmp.jsonl' 2> '/work/selected-wtmp.stderr' || parser_code=$?
python3 - '/work/selected-wtmp.jsonl' "$parser_code" <<'PY'
import json, sys
records, summary = 0, None
for line in open(sys.argv[1]):
    row = json.loads(line)
    if row['kind'] == 'record': records += 1
    elif row['kind'] == 'summary': summary = row
print(json.dumps({'exit_code': int(sys.argv[2]), 'summary': summary,
                  'records': records if summary else None}))
PY
```

Replace paths and origin-locator with actual selected sources. `origin_locator_asserted_by_caller` is the caller-supplied original location, not helper verification of an original-image relationship. Separately link copy-input SHA-256 to original/volume/extraction records and hashes. First decompress rotated compressed files into selected /work copies; record original compressed-file location/hash and decompression command/errors. The helper does not support compressed/archive input. Decompressed byte_start/byte_end are not original compressed-byte coordinates.

The sole supported format is **Linux/glibc x86-compatible, little endian, 384 bytes**. Type is signed int16; PID/session/tv_sec/tv_usec are signed int32. Key offsets:
ut_type=0, ut_pid=4, ut_line=8, ut_id=40, ut_user=44, ut_host=76, ut_exit=332,
ut_session=336, tv_sec=340, tv_usec=344, ut_addr_v6=348.
This is a 32/64-bit x86 glibc-compatible layout, not all Linux ABIs, big endian, BSD/macOS, or Solaris. Files have no ABI-identifying header. `--format` is an explicit caller choice after verifying source OS/ABI, not automatic detection. If compatibility cannot be established or layout differs, record unsupported/unverified; do not guess a parser to fit size. A size divisible by 384 alone does not prove compatibility.

JSONL kind=source records parser/version, input SHA-256, format assumptions, and size. Only kind=record is a record: preserve zero-based record_index, input path, byte_start/byte_end (exclusive end), ut_type_raw/ut_type_name, ut_tv_raw tv_sec/tv_usec, timestamp_utc, and complete raw_record_hex. Strings display through NUL with UTF-8 replacement; recover exact bytes from raw_record_hex. Preserve addresses as ut_addr_v6_bytes_hex without guessing address families. timestamp_utc derives from signed POSIX seconds/microseconds displayed as UTC; it does not establish host timezone configuration or clock accuracy. Invalid tv_usec retains raw values without UTC conversion. Undefined types retain raw values and return UNKNOWN warnings.

Check final kind=summary together with exit code: 0 means selected-file parsing complete or empty; 1 error; 2 truncated tail/record warning; 3 output-record limit. Default `--limit 10000` limits output/parsing and returns unparsed_complete_records/limited. truncated_tail preserves offset, length, and raw hex. If limit and tail coexist, inspect both. EOF alone does not establish completeness. Input is one regular file of at most 256 MiB. Status complete covers only the selected file/specified structure, not complete login history, all rotations, or authentication outcome. stdout remaining after errors is partial.
Exit 2 also covers invalid arguments: distinguish stderr kind=error from the last stdout kind=summary; do not relabel failure without summary as partial parsing. The example preserves parser status so independent follow-up checks can run. With no summary, inspect retained stderr instead of aggregating. Read summary/exclusions before adopting results. Final shell exit 0 does not establish parser success.

Dissect 3.25.1 `utmp` aliases `wtmp`, reading `/var/log/wtmp*` and `/var/run/utmp*` together; `btmp` reads `/var/log/btmp*`. Consider this scope only when broader discovery is needed. Its times use only tv_sec and omit original paths, offsets, microseconds, and partial tails: use the helper above for exact selected-file coordinates, precision, and completeness. Do not count USER_PROCESS, DEAD_PROCESS, and BOOT_TIME as equivalent logins. Review rotated duplicates, missing session logs, clock changes, and retention gaps; absence alone establishes neither concealment nor absence of logins.
