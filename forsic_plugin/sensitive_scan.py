"""Isolated, bounded local detector. Only safe.json may leave private storage.

This module runs inside the analysis container; it never loads an evidence path
or detector configuration supplied by evidence. Original excerpts are private.
"""
from __future__ import annotations

import bisect
import contextlib
import csv
import hashlib
import hmac
import importlib.util
import io
import json
import math
import logging
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time

SCHEMA = "forsic.sensitive.scan.v1"
SCAN_ROOT = Path("/scan-input")
PRIVATE_ROOT = Path("/private")
TOOLS = Path("/opt/forensic-tools")
MAX_FILES = 64
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_TOTAL_BYTES = 32 * 1024 * 1024
MAX_FINDINGS = 2000
MAX_OUTPUT_BYTES = 16 * 1024 * 1024
MAX_PRIVATE_BYTES = 128 * 1024 * 1024
EXCERPT_CHARS = 160
PII_TYPES = {"KR_RRN", "KR_FRN", "KR_PASSPORT", "KR_DRIVER_LICENSE", "KR_BRN", "CREDIT_CARD", "EMAIL_ADDRESS", "PHONE_NUMBER"}
CONTEXT_WINDOW = 48
# Trusted, versioned literal aliases. These signals describe nearby text only;
# they do not bind a field, raise a score or establish an actual person's data.
KOREAN_LABEL_ALIASES_V1 = {
    "KR_RRN": ("korean_rrn_label_nearby", ("주민등록번호", "주민번호")),
    "KR_FRN": ("korean_frn_label_nearby", ("외국인등록번호",)),
    "PHONE_NUMBER": ("korean_phone_label_nearby", ("연락처", "전화번호")),
    "EMAIL_ADDRESS": ("korean_email_label_nearby", ("이메일", "전자우편")),
    "KR_PASSPORT": ("korean_passport_label_nearby", ("여권",)),
    "KR_DRIVER_LICENSE": ("korean_driver_license_label_nearby", ("운전면허",)),
    "CREDIT_CARD": ("korean_card_label_nearby", ("카드",)),
    "KR_BRN": ("korean_business_identifier_label_nearby", ("사업자등록번호",)),
}


def _context_signals(text, start, end, entity):
    policy = KOREAN_LABEL_ALIASES_V1.get(entity)
    if policy is None:
        return []
    line_start = max(text.rfind("\n", 0, start), text.rfind("\r", 0, start)) + 1
    following_breaks = [index for index in (text.find("\n", end), text.find("\r", end)) if index >= 0]
    line_end = min(following_breaks) if following_breaks else len(text)
    left = text[max(line_start, start - CONTEXT_WINDOW):start]
    right = text[end:min(line_end, end + CONTEXT_WINDOW)]
    signal, aliases = policy
    return [signal] if any(alias in left or alias in right for alias in aliases) else []


class ScanError(Exception):
    """The code, never an exception string, may enter the safe result."""


def _read_regular(path, limit):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ScanError("not_regular_file")
        return stream.read(limit + 1)


def _write_private(path, value):
    payload = json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode()
    if len(payload) > MAX_OUTPUT_BYTES:
        raise ScanError("output_limit")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(payload)


def _decode(data, encoding, partial=False):
    """Strict decode with an exact codepoint-boundary to original-byte map."""
    bom = 0
    if encoding == "auto":
        encoding = "utf-16" if data.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8"
    if encoding == "utf-16":
        if data.startswith(b"\xff\xfe"):
            codec, bom = "utf-16-le", 2
        elif data.startswith(b"\xfe\xff"):
            codec, bom = "utf-16-be", 2
        else:
            raise ScanError("utf16_bom_required")
    elif encoding == "utf-8":
        codec = "utf-8"
        bom = 3 if data.startswith(b"\xef\xbb\xbf") else 0
    elif encoding == "cp949":
        codec = "cp949"
    else:
        raise ScanError("unsupported_encoding")
    # A sampled prefix may end inside one multibyte character. Trim only that
    # incomplete tail, never replace a malformed byte in the examined range.
    for trim in range(4 if partial else 1):
        try:
            body = data[bom:len(data) - trim if trim else len(data)]
            text = body.decode(codec, errors="strict")
            break
        except UnicodeDecodeError as exc:
            if not partial or exc.end != len(body):
                raise ScanError("decode_error") from None
    else:
        raise ScanError("decode_error")
    offsets = [bom]
    for char in text:
        offsets.append(offsets[-1] + len(char.encode(codec)))
    if "\x00" in text:
        raise ScanError("unsupported_binary_text")
    return text, offsets, codec


def _record_ranges(text, kind):
    """Only verified record numbers; no fabricated cell or JSON pointers."""
    if kind == "text":
        return [], None
    lines = text.splitlines(keepends=True)
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line))
    records = []
    try:
        if kind == "csv":
            reader = csv.reader(io.StringIO(text, newline=""), strict=True)
            previous = 0
            for number, _ in enumerate(reader, 1):
                records.append((starts[previous], starts[reader.line_num], number))
                previous = reader.line_num
        elif kind == "jsonl":
            for index, line in enumerate(lines):
                if line.strip():
                    json.loads(line)
                    records.append((starts[index], starts[index + 1], index + 1))
        else:
            return [], "unsupported_structured_format"
    except (ValueError, csv.Error):
        return [], "structured_parse_error"
    return records, None


def _presidio_load():
    path = TOOLS / "sensitive/presidio_local.py"
    spec = importlib.util.spec_from_file_location("forsic_presidio_local", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.load_recognizers(), module


class _RegexTimeoutHandler(logging.Handler):
    def emit(self, record):
        if "timed out" in record.getMessage():
            raise ScanError("recognizer_timeout")


def _version(binary):
    try:
        result = subprocess.run([str(binary), "version"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                timeout=3, env={"PATH": "/usr/bin:/bin"})
        value = result.stdout.decode("ascii").strip()
        return value if result.returncode == 0 and re.fullmatch(r"v?\d+\.\d+\.\d+", value) else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def _presidio_version():
    for metadata in (TOOLS / "sensitive/python").glob("presidio_analyzer-*.dist-info/METADATA"):
        for line in metadata.read_text().splitlines():
            if re.fullmatch(r"Version: [0-9.]+", line):
                return line.split(": ", 1)[1]
    return None


def _child_limits(output_limit=MAX_OUTPUT_BYTES):
    import resource
    resource.setrlimit(resource.RLIMIT_FSIZE, (output_limit, output_limit))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def _gitleaks(text, private, deadline):
    binary = TOOLS / "bin/gitleaks"
    config = TOOLS / "sensitive/gitleaks.toml"
    if not binary.is_file() or not config.is_file():
        raise ScanError("engine_not_installed")
    # Reserve both final JSON files. Native output has a per-child and an
    # aggregate private-storage bound, including pipes redirected to files.
    used = sum(p.lstat().st_size for p in PRIVATE_ROOT.rglob("*") if p.is_file())
    output_limit = min(MAX_OUTPUT_BYTES, (MAX_PRIVATE_BYTES - used - 2 * MAX_OUTPUT_BYTES) // 3)
    if output_limit < 4096:
        raise ScanError("private_output_limit")
    # stdin avoids source-root .gitleaksignore auto-discovery in dir mode.
    report = private / "gitleaks.json"
    command = [str(binary), "stdin", "--config", str(config),
               "--gitleaks-ignore-path", str(TOOLS / "sensitive/empty.gitleaksignore"),
               "--ignore-gitleaks-allow", "--max-archive-depth", "0", "--max-decode-depth", "0",
               "--exit-code", "10", "--no-banner", "--no-color", "--log-level", "error",
               "--report-format", "json", "--report-path", str(report),
               "--timeout", str(max(1, math.ceil(deadline - time.monotonic())))]
    env = {"PATH": "/usr/bin:/bin", "HOME": str(private), "LANG": "C.UTF-8"}
    with open(private / "stdout", "xb") as out, open(private / "stderr", "xb") as err:
        try:
            result = subprocess.run(command, input=text.encode("utf-8"), stdout=out, stderr=err,
                                    cwd=private, env=env, timeout=max(.01, deadline - time.monotonic()),
                                    preexec_fn=lambda: _child_limits(output_limit))
        except subprocess.TimeoutExpired:
            raise ScanError("engine_timeout") from None
    if result.returncode not in (0, 10):
        raise ScanError("engine_failed")
    try:
        hits = json.loads(_read_regular(report, MAX_OUTPUT_BYTES))
    except (OSError, ValueError):
        raise ScanError("engine_report_invalid") from None
    if not isinstance(hits, list) or len(hits) > MAX_FINDINGS * 10:
        raise ScanError("engine_report_limit")
    # stderr at error log level means completeness cannot be inferred from exit.
    if (private / "stderr").stat().st_size:
        raise ScanError("engine_diagnostic_error")
    return hits


def _secret_span(text, hit):
    """Gitleaks v8: byte columns identify Match, not its Secret subgroup."""
    matched, secret = hit.get("Match"), hit.get("Secret")
    if not isinstance(matched, str) or not isinstance(secret, str) or not secret:
        return None
    raw = text.encode("utf-8")
    newlines = [m.start() for m in re.finditer(b"\n", raw)]
    try:
        line = int(hit["StartLine"])
        previous_newline = 0 if line == 1 else newlines[line - 2]
        start = previous_newline + int(hit["StartColumn"]) - 1
        if raw[start:start + len(matched.encode())] != matched.encode():
            return None
        base = len(raw[:start].decode("utf-8"))
    except (KeyError, ValueError, IndexError, UnicodeDecodeError):
        return None
    places = [m.start() for m in re.finditer(re.escape(secret), matched)]
    if len(places) != 1:
        return None
    return base + places[0], base + places[0] + len(secret)


def _validate(request):
    if request.get("schema") != SCHEMA or request.get("mode") not in ("triage", "scope_count"):
        raise ScanError("invalid_request")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", request.get("scan_id", "")):
        raise ScanError("invalid_request")
    profiles = request.get("profiles")
    if not isinstance(profiles, list) or not profiles or len(profiles) != len(set(profiles)) or not set(profiles) <= {"secrets", "pii_kr"}:
        raise ScanError("invalid_request")
    inputs = request.get("inputs")
    if not isinstance(inputs, list) or not 1 <= len(inputs) <= MAX_FILES:
        raise ScanError("invalid_request")
    seen = set()
    for item in inputs:
        name = item.get("input_id", "")
        if not re.fullmatch(r"F[0-9]{4}", name) or name in seen or item.get("path") != str(SCAN_ROOT / name):
            raise ScanError("invalid_input")
        seen.add(name)
    if request.get("output_dir") != str(PRIVATE_ROOT) or request.get("key_path") != str(PRIVATE_ROOT / "hmac.key"):
        raise ScanError("invalid_private_path")
    seconds = request.get("deadline_seconds", 300)
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not 0 < seconds <= 300:
        raise ScanError("invalid_deadline")


def run_scan(request):
    _validate(request)
    deadline = time.monotonic() + request.get("deadline_seconds", 300)
    key = _read_regular(request["key_path"], 64)
    if len(key) != 32:
        raise ScanError("invalid_key")
    private = {"schema": SCHEMA, "scan_id": request["scan_id"], "inputs": [], "findings": []}
    safe = {"schema": SCHEMA, "scan_id": request["scan_id"], "representation": "derived_masked_detection_result",
            "original_evidence": False, "execution": {"state": "returned"}, "engines": [], "findings": [],
            "findings_omitted": 0, "raw_access": "restricted_local_artifact",
            "context_policy": {"method": "bounded_same_line_literal_alias", "alias_version": "korean_labels_v1",
                               "window_codepoints_each_side": CONTEXT_WINDOW, "nlp": "not_loaded",
                               "interpretation": "lexical_proximity_only_no_field_binding_or_person_verification"},
            "limitations": ["no_cell_or_json_pointer_mapping", "no_json_escape_decoding", "no_nlp_context_or_free_text_person_recognition"],
            "privacy": {"masking_policy": "whole_value_opaque_v1", "raw_hashes_private": True,
                        "normalization_version": "exact_values_v1"},
            "summary": {"secret_occurrences_observed": 0, "pii_occurrences_observed": 0,
                        "organization_identifier_occurrences_observed": 0, "distinct_values_observed": 0,
                        "count_method": "retained_unique_source_span_and_value", "counts_are_lower_bounds": False,
                        "files_with_findings": 0, "records_with_findings": None,
                        "distinct_people": None, "credential_live_validity": "not_tested", "external_disclosure": "not_assessed"}}
    recognizers, recognizer_module = {}, None
    if "pii_kr" in request["profiles"]:
        try:
            # Even optional dependency diagnostics belong only in private pipes.
            with open(PRIVATE_ROOT / "presidio-diagnostics", "x") as diagnostics:
                with contextlib.redirect_stdout(diagnostics), contextlib.redirect_stderr(diagnostics):
                    recognizers, recognizer_module = _presidio_load()
            if set(recognizers) != PII_TYPES:
                raise ScanError("recognizer_inventory_mismatch")
            engine_state = "available"
        except Exception:
            recognizers = {}
            engine_state = "not_supported"
        safe["engines"].append({"name": "presidio_local", "state": engine_state,
                                "version": _presidio_version(),
                                "adapter_sha256": hashlib.sha256(_read_regular(TOOLS / "sensitive/presidio_local.py", 1024 * 1024)).hexdigest() if recognizer_module else None,
                                "recognizer_manifest_sha256": hashlib.sha256(_read_regular(TOOLS / "sensitive/presidio-source-sha256.json", 1024 * 1024)).hexdigest() if recognizer_module else None,
                                "recognizers": sorted(recognizers), "nlp": "not_loaded"})
    if "secrets" in request["profiles"]:
        binary, config = TOOLS / "bin/gitleaks", TOOLS / "sensitive/gitleaks.toml"
        safe["engines"].append({"name": "gitleaks", "state": "available" if binary.is_file() and config.is_file() else "not_supported",
                               "binary_sha256": hashlib.sha256(_read_regular(binary.resolve(strict=True), 64 * 1024 * 1024)).hexdigest() if binary.is_file() else None,
                               "config_sha256": hashlib.sha256(_read_regular(config, MAX_OUTPUT_BYTES)).hexdigest() if config.is_file() else None,
                               "version": _version(binary) if binary.is_file() else None,
                               "transport": "stdin", "archive_decode_depth": 0})
    coverage = {"state": "complete", "selected_files": len(request["inputs"]), "fully_processed_files": 0,
                "partial_files": 0, "failed_files": 0, "excluded_files": 0,
                "count_scope": "observed_ranges_only", "inputs": []}
    safe["coverage"] = coverage
    groups, dedup = {}, {}
    total_read = 0
    for item in request["inputs"]:
        name = item["input_id"]
        entry = {"input_id": name, "state": "complete", "bytes_total": None, "bytes_read": 0,
                 "ranges": [], "reason_codes": [], "profile_states": {}, "record_mapping_state": "not_attempted"}
        coverage["inputs"].append(entry)
        private_entry = {"input_id": name, "source_role": item.get("source_role"), "coverage_metadata": item.get("coverage"), "matches": []}
        private["inputs"].append(private_entry)
        def reason(code, state="partial"):
            if code not in entry["reason_codes"]:
                entry["reason_codes"].append(code)
            entry["state"] = state
        if time.monotonic() >= deadline or total_read >= MAX_TOTAL_BYTES or len(safe["findings"]) >= MAX_FINDINGS:
            reason("job_limit", "excluded")
            continue
        try:
            source_stat = os.stat(item["path"], follow_symlinks=False)
            size = source_stat.st_size
            entry["bytes_total"] = size
            limit = min(MAX_FILE_BYTES, MAX_TOTAL_BYTES - total_read)
            data = _read_regular(item["path"], limit)
            entry["bytes_read"] = len(data)
            after_read = os.stat(item["path"], follow_symlinks=False)
            if (source_stat.st_dev, source_stat.st_ino, source_stat.st_size, source_stat.st_mtime_ns) != (after_read.st_dev, after_read.st_ino, after_read.st_size, after_read.st_mtime_ns):
                raise ScanError("input_changed")
            partial = len(data) > limit
            data = data[:limit]
            total_read += entry["bytes_read"]
            text, offsets, codec = _decode(data, item.get("encoding", "auto"), partial)
            private_entry["encoding"] = codec
            private_entry["source_sha256"] = hashlib.sha256(data).hexdigest()
            private_entry["hash_scope"] = "read_prefix" if partial else "whole_file"
            entry["ranges"] = [{"start": 0, "end": offsets[-1]}]
            if partial:
                reason("input_byte_limit")
            records, record_error = _record_ranges(text, item.get("format", "text"))
            entry["record_mapping_state"] = "failed" if record_error else "not_requested" if item.get("format", "text") == "text" else "complete"
            if record_error:
                entry["reason_codes"].append(record_error)
            newlines = [m.start() for m in re.finditer("\n", text)]
            hits = []
            if "pii_kr" in request["profiles"]:
                entry["profile_states"]["pii_kr"] = "complete" if recognizers else "not_supported"
                if not recognizers:
                    reason("pii_engine_not_supported", "failed")
                for entity, recognizer in recognizers.items():
                    if time.monotonic() >= deadline:
                        reason("deadline_reached")
                        entry["profile_states"]["pii_kr"] = "partial"
                        break
                    try:
                        pattern_module = sys.modules.get("presidio_analyzer.pattern_recognizer")
                        if pattern_module is not None:
                            pattern_module.REGEX_TIMEOUT_SECONDS = max(.01, min(5, deadline - time.monotonic()))
                        timeout_handler = _RegexTimeoutHandler()
                        recognizer_logger = logging.getLogger("presidio-analyzer")
                        recognizer_logger.addHandler(timeout_handler)
                        try:
                            results = recognizer.analyze(text=text, entities=[entity], nlp_artifacts=None)
                        finally:
                            recognizer_logger.removeHandler(timeout_handler)
                        if len(results) > MAX_FINDINGS:
                            reason("finding_limit")
                            safe["summary"]["counts_are_lower_bounds"] = True
                        for hit in results[:MAX_FINDINGS]:
                            start, end = hit.start, hit.end
                            if hit.entity_type != entity or not 0 <= start < end <= len(text):
                                raise ScanError("recognizer_result_invalid")
                            validation = recognizer.validate_result(text[start:end])
                            if validation not in (True, False, None):
                                validation = None
                            if len(hits) < MAX_FINDINGS:
                                hits.append((start, end, text[start:end], "presidio_local", entity, entity, validation,
                                             getattr(recognizer, "forsic_provenance", "presidio_upstream")))
                            else:
                                reason("finding_limit")
                                safe["summary"]["counts_are_lower_bounds"] = True
                                break
                    except Exception:
                        reason("recognizer_failed")
                        entry["profile_states"]["pii_kr"] = "partial"
            if "secrets" in request["profiles"]:
                scratch = PRIVATE_ROOT / ("native-" + name)
                scratch.mkdir(mode=0o700)
                try:
                    secret_hits = _gitleaks(text, scratch, deadline)
                    entry["profile_states"]["secrets"] = "complete"
                    for hit in secret_hits:
                        span = _secret_span(text, hit)
                        if span is None:
                            reason("secret_coordinate_unresolved")
                            private_entry["matches"].append({"unlocated_engine_observation": hit})
                            continue
                        # Rule IDs are trusted-config identifiers, but require a
                        # fixed identifier alphabet before returning to a model.
                        rule = hit.get("RuleID", "")
                        rule = rule if isinstance(rule, str) and re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", rule) else "opaque_rule"
                        hits.append((*span, text[span[0]:span[1]], "gitleaks", rule, "SECRET", None, "trusted_rules"))
                except ScanError as exc:
                    code = exc.args[0]
                    entry["profile_states"]["secrets"] = "not_supported" if code == "engine_not_installed" else "failed"
                    reason(code, "partial" if hits else "failed")
            for start, end, value, engine, rule, entity, validation, origin in hits:
                digest = hmac.new(key, (entity + "\0" + value).encode(), hashlib.sha256).hexdigest()
                identity = (source_stat.st_dev, source_stat.st_ino, start, end, value)
                context_signals = _context_signals(text, start, end, entity)
                observation = {"engine": engine, "rule_id": rule, "entity_type": entity, "format_validation": validation, "provenance": origin}
                if identity in dedup:
                    index = dedup[identity]
                    for signal in context_signals:
                        if signal not in safe["findings"][index]["context_signals"]:
                            safe["findings"][index]["context_signals"].append(signal)
                            private["findings"][index]["context_signals"].append(signal)
                    if observation not in safe["findings"][index]["observations"]:
                        safe["findings"][index]["observations"].append(observation)
                        private["findings"][index]["observations"].append(observation)
                    continue
                if len(safe["findings"]) >= MAX_FINDINGS:
                    safe["findings_omitted"] += 1
                    safe["summary"]["counts_are_lower_bounds"] = True
                    reason("finding_limit")
                    continue
                category = "secret_candidate" if entity == "SECRET" else "organization_identifier" if entity == "KR_BRN" else "pii_candidate"
                prefix = "S" if category == "secret_candidate" else "P"
                if digest not in groups:
                    groups[digest] = prefix + f"{len(groups) + 1:04d}"
                group = groups[digest]
                finding_id = "D" + f"{len(safe['findings']) + 1:05d}"
                line = bisect.bisect_left(newlines, start)
                end_line = bisect.bisect_left(newlines, end - 1)
                locator = {"byte_start": offsets[start], "byte_end": offsets[end], "line_start": line + 1,
                           "line_end": end_line + 1, "column_start": start - (newlines[line - 1] + 1 if line else 0) + 1,
                           "column_end": end - (newlines[end_line - 1] + 1 if end_line else 0) + 1,
                           "coordinate_space": "original_bytes_and_unicode_codepoints", "end_exclusive": True}
                for record_start, record_end, number in records:
                    if record_start <= start < end <= record_end:
                        locator["structured_record"] = {"format": item["format"], "record_number": number}
                        break
                safe_finding = {"finding_id": finding_id, "group_id": group, "input_id": name, "category": category,
                                "entity_type": entity, "masked_excerpt": "<" + ("SECRET" if prefix == "S" else "PII") + ":" + group + ">",
                                "safe_excerpt_coordinate_space": "derived_masked_utf8", "source_locator": locator,
                                "observations": [observation], "context_signals": context_signals.copy(), "real_person_data": "not_assessed"}
                left, right = max(0, start - EXCERPT_CHARS), min(len(text), end + EXCERPT_CHARS)
                # Long matches do not make appendix excerpts unbounded.
                right = min(right, left + 2 * EXCERPT_CHARS + 256)
                private_finding = {"finding_id": finding_id, "input_id": name, "group_id": group,
                                   "match_byte_start": offsets[start], "match_byte_end": offsets[end],
                                   "value_hmac": digest, "encoding": codec, "excerpt": text[left:right],
                                   "excerpt_byte_start": offsets[left], "excerpt_byte_end": offsets[right],
                                   "omission_ranges": ([{"start": 0, "end": offsets[left]}] if offsets[left] else []) +
                                                      ([{"start": offsets[right], "end": size}] if offsets[right] < size else []),
                                   "source_locator": locator, "observations": [observation], "context_signals": context_signals.copy()}
                dedup[identity] = len(safe["findings"])
                safe["findings"].append(safe_finding)
                private["findings"].append(private_finding)
                summary_key = "secret_occurrences_observed" if prefix == "S" else "organization_identifier_occurrences_observed" if entity == "KR_BRN" else "pii_occurrences_observed"
                safe["summary"][summary_key] += 1
        except ScanError as exc:
            reason(exc.args[0], "failed")
        except Exception:
            reason("input_processing_failed", "failed")
    for entry in coverage["inputs"]:
        field = {"complete": "fully_processed_files", "partial": "partial_files", "failed": "failed_files", "excluded": "excluded_files"}[entry["state"]]
        coverage[field] += 1
    if coverage["fully_processed_files"] != coverage["selected_files"]:
        coverage["state"] = "partial" if coverage["partial_files"] or coverage["fully_processed_files"] else "failed"
        safe["summary"]["counts_are_lower_bounds"] = True
    safe["summary"]["distinct_values_observed"] = len(groups)
    safe["summary"]["files_with_findings"] = len({finding["input_id"] for finding in safe["findings"]})
    if all(entry["record_mapping_state"] == "complete" for entry in coverage["inputs"]):
        safe["summary"]["records_with_findings"] = len({(finding["input_id"], finding["source_locator"]["structured_record"]["record_number"])
                                                           for finding in safe["findings"] if "structured_record" in finding["source_locator"]})
    private["coverage"] = coverage
    private["engines"] = safe["engines"]
    private["context_policy"] = safe["context_policy"]
    private["limitations"] = safe["limitations"]
    _write_private(PRIVATE_ROOT / "result.json", private)
    _write_private(PRIVATE_ROOT / "safe.json", safe)
    return safe


def main():
    os.umask(0o077)
    try:
        if sys.argv[1:] != ["--request", str(PRIVATE_ROOT / "request.json")]:
            return 2
        request = json.loads(_read_regular(PRIVATE_ROOT / "request.json", 65536))
        # Suppress even unforeseen recognizer exceptions/import diagnostics.
        diagnostics_fd = os.open(PRIVATE_ROOT / "helper-diagnostics", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(diagnostics_fd, "w") as diagnostics, contextlib.redirect_stdout(diagnostics), contextlib.redirect_stderr(diagnostics):
            run_scan(request)
        print("FORSIC_SENSITIVE_SCAN_COMPLETE")
        return 0
    except BaseException:
        return 2


if __name__ == "__main__":
    sys.exit(main())
