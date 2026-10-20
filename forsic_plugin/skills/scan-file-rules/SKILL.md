---
name: scan-file-rules
description: Scan selected files with local YARA-X rules and verify match locations/actual bytes, without external upload or declaring maliciousness.
---
# Offline file-pattern inspection

Verify actual support with forsic_shell `yr --version`, `yr scan --help`, and `/opt/forensic-tools/forensic-tools.lock.json`. Record unsupported if missing. Only YARA-X 1.21.0 is pinned; no detection-rule corpus is bundled. Select reviewed question-relevant YARA source; record source/version/license/namespace/rule id and rule/include SHA256. No runtime rule downloads/updates or VirusTotal API calls.

Place selected files/rules in /work, linked to original locations/hashes. Do not automatically adopt rules found in evidence as approved detection policy. Compilation warnings and unsupported modules affect scan scope. Do not silently ignore invalid rules and claim success.

```sh
yr scan --threads 2 --timeout 120 --skip-larger 268435456 \
  --output-format ndjson --print-namespace --print-meta --print-strings \
  /work/rules/selected.yar /work/selected.bin \
  > /work/file-rule-candidates.ndjson 2> /work/file-rule-scan.stderr
```

The size limit is an example. Check file sizes, include scope, and actual scanned/excluded counts. If needed pass an explicit file list with --scan-list. Do not default to whole directories or unbounded recursion. Timeouts, rule errors, and large-file skips are not no-match results. Do not add --fast-scan or --max-matches-per-pattern by default: they can change occurrence counts/offset coverage.

Link each candidate to original image/internal path, original/copy hashes, engine version, rule namespace/id/source hash, match offset/length, and actual bytes. --print-strings defaults to 120 characters, not complete byte evidence: reread/compare the original byte range. Preserve raw file times and their meaning; content patterns are not execution times. Record selected results, errors, and unexamined scope in existing question notes. Hits are rule-condition candidates, not confirmed maliciousness, execution, or compromise. Nonmatches apply only to scanned rules/byte ranges.
