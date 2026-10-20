"""Synthetic-only detector contracts, with optional real offline engines."""
import importlib.util
import io
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import hashlib
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

_SPEC = importlib.util.spec_from_file_location("sensitive_scan_tested", Path(__file__).parent / "forsic_plugin/sensitive_scan.py")
scan = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(scan)


class FakeRecognizer:
    forsic_provenance = "synthetic_test"

    def __init__(self, entity):
        self.entity = entity

    def analyze(self, text, entities, nlp_artifacts):
        assert nlp_artifacts is None
        patterns = {"EMAIL_ADDRESS": r"[A-Za-z]+@example\.invalid", "KR_RRN": r"990101-1234567", "KR_BRN": r"123-45-67890"}
        return [SimpleNamespace(start=m.start(), end=m.end(), entity_type=self.entity)
                for m in re.finditer(patterns.get(self.entity, r"(?!)"), text)]

    def validate_result(self, value):
        return None


class SensitiveScanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.inputs = self.root / "input"
        self.private = self.root / "private"
        self.inputs.mkdir()
        self.private.mkdir(mode=0o700)
        (self.private / "hmac.key").write_bytes(b"x" * 32)
        self.patchers = [patch.object(scan, "SCAN_ROOT", self.inputs), patch.object(scan, "PRIVATE_ROOT", self.private)]
        for p in self.patchers:
            p.start()
            self.addCleanup(p.stop)

    def request(self, text, encoding="utf-8", kind="text", profiles=None):
        raw = text.encode(encoding) if isinstance(text, str) else text
        (self.inputs / "F0001").write_bytes(raw)
        return {"schema": scan.SCHEMA, "scan_id": "synthetic_001", "mode": "scope_count",
                "profiles": profiles or ["pii_kr"], "deadline_seconds": 30,
                "inputs": [{"input_id": "F0001", "path": str(self.inputs / "F0001"), "encoding": encoding,
                            "format": kind, "source_role": "original_evidence", "coverage": {"synthetic": True}}],
                "key_path": str(self.private / "hmac.key"), "output_dir": str(self.private)}

    def run_fake(self, request):
        recognizers = {entity: FakeRecognizer(entity) for entity in scan.PII_TYPES}
        with patch.object(scan, "_presidio_load", return_value=(recognizers, None)), patch.object(scan, "_presidio_version", return_value="synthetic"):
            return scan.run_scan(request)

    def test_safe_private_boundary_and_duplicate_value_grouping(self):
        text = '비밀필드💎=alpha@example.invalid\r\n"990101-1234567": "alpha@example.invalid"\n123-45-67890'
        result = self.run_fake(self.request(text))
        encoded = json.dumps(result, ensure_ascii=False)
        for forbidden in ["alpha@example.invalid", "990101-1234567", "123-45-67890", "비밀필드", "value_hmac", str(self.root)]:
            self.assertNotIn(forbidden, encoded)
        self.assertEqual(result["coverage"]["state"], "complete")
        self.assertEqual(result["summary"]["pii_occurrences_observed"], 3)
        self.assertEqual(result["summary"]["organization_identifier_occurrences_observed"], 1)
        self.assertIsNone(result["summary"]["distinct_people"])
        emails = [f for f in result["findings"] if f["entity_type"] == "EMAIL_ADDRESS"]
        self.assertEqual(emails[0]["group_id"], emails[1]["group_id"])
        raw = text.encode()
        private = json.loads((self.private / "result.json").read_text())
        for finding in private["findings"]:
            start, end = finding["excerpt_byte_start"], finding["excerpt_byte_end"]
            self.assertEqual(raw[start:end].decode(), finding["excerpt"])
            self.assertEqual(len(finding["value_hmac"]), 64)
        self.assertIsNone(result["findings"][0]["observations"][0]["format_validation"])
        self.assertEqual((self.private / "safe.json").stat().st_mode & 0o777, 0o600)

    def test_bom_utf16_and_cp949_original_offsets(self):
        for encoding in ["utf-8", "utf-16", "cp949"]:
            with self.subTest(encoding=encoding):
                original = "한글\r\nalpha@example.invalid 끝"
                raw = original.encode(encoding)
                if encoding == "utf-8":
                    raw = b"\xef\xbb\xbf" + raw
                text, offsets, codec = scan._decode(raw, encoding)
                start = text.index("alpha")
                end = start + len("alpha@example.invalid")
                self.assertEqual(raw[offsets[start]:offsets[end]].decode(codec), "alpha@example.invalid")
                self.assertEqual(offsets[-1], len(raw))

    def test_strict_decode_and_incomplete_sampling_tail(self):
        with self.assertRaisesRegex(scan.ScanError, "decode_error"):
            scan._decode(b"\xff", "utf-8")
        text, offsets, _ = scan._decode("한".encode() + b"\xed", "utf-8", partial=True)
        self.assertEqual(text, "한")
        self.assertEqual(offsets[-1], 3)
        with self.assertRaisesRegex(scan.ScanError, "decode_error"):
            scan._decode(b"\xffabc", "utf-8", partial=True)
        with self.assertRaisesRegex(scan.ScanError, "utf16_bom_required"):
            scan._decode(b"a\x00", "utf-16")

    def test_csv_quoted_newline_and_jsonl_verified_record_only(self):
        text = 'header,value\r\n"two\nlines",alpha@example.invalid\r\n'
        result = self.run_fake(self.request(text, kind="csv"))
        locator = result["findings"][0]["source_locator"]
        self.assertEqual(locator["structured_record"], {"format": "csv", "record_number": 2})
        self.assertEqual(locator["line_start"], 3)
        self.assertEqual(result["summary"]["records_with_findings"], 1)
        self.assertNotIn("column_name", json.dumps(result))
        records, error = scan._record_ranges('{"private-key":"alpha@example.invalid"}\n', "jsonl")
        self.assertIsNone(error)
        self.assertEqual(records[0][2], 1)
        self.assertEqual(scan._record_ranges('{bad}\n', "jsonl"), ([], "structured_parse_error"))

    def test_secret_coordinates_match_subgroup_multibyte_and_newline(self):
        text = "한글💎=ignored\n암호=synthetic-token\n"
        raw = text.encode()
        matched = "암호=synthetic-token"
        begin = raw.index(matched.encode())
        previous = raw.index(b"\n")
        hit = {"StartLine": 2, "StartColumn": begin - previous + 1, "Match": matched, "Secret": "synthetic-token"}
        span = scan._secret_span(text, hit)
        self.assertEqual(text[slice(*span)], "synthetic-token")
        hit["StartColumn"] += 1
        self.assertIsNone(scan._secret_span(text, hit))
        self.assertIsNone(scan._secret_span("xx", {"StartLine": 1, "StartColumn": 1, "Match": "xx", "Secret": "x"}))

    def test_missing_engines_are_failed_not_negative_detection(self):
        with patch.object(scan, "TOOLS", self.root / "missing"):
            result = scan.run_scan(self.request("harmless synthetic", profiles=["pii_kr", "secrets"]))
        self.assertEqual(result["coverage"]["state"], "failed")
        self.assertEqual(result["coverage"]["fully_processed_files"], 0)
        self.assertTrue(all(engine["state"] == "not_supported" for engine in result["engines"]))
        self.assertEqual(result["coverage"]["inputs"][0]["profile_states"]["secrets"], "not_supported")

    def test_sampling_and_limit_explicit(self):
        with patch.object(scan, "MAX_FILE_BYTES", 30):
            result = self.run_fake(self.request("alpha@example.invalid\n" + "가" * 100))
        self.assertEqual(result["coverage"]["state"], "partial")
        self.assertIn("input_byte_limit", result["coverage"]["inputs"][0]["reason_codes"])
        self.assertLess(result["coverage"]["inputs"][0]["ranges"][0]["end"], len(("alpha@example.invalid\n" + "가" * 100).encode()))

    def test_match_limit_does_not_claim_complete_counts(self):
        with patch.object(scan, "MAX_FINDINGS", 1):
            result = self.run_fake(self.request("alpha@example.invalid beta@example.invalid"))
        self.assertEqual(len(result["findings"]), 1)
        self.assertTrue(result["summary"]["counts_are_lower_bounds"])
        self.assertEqual(result["coverage"]["state"], "partial")

    def test_symlink_inputs_and_invalid_private_paths_rejected(self):
        request = self.request("synthetic")
        (self.inputs / "F0001").unlink()
        (self.inputs / "F0001").symlink_to(self.private / "hmac.key")
        result = self.run_fake(request)
        self.assertEqual(result["coverage"]["state"], "failed")
        request["key_path"] = str(self.inputs / "F0001")
        with self.assertRaisesRegex(scan.ScanError, "invalid_private_path"):
            scan.run_scan(request)

    def test_timeout_warning_becomes_explicit_partial(self):
        class TimeoutRecognizer(FakeRecognizer):
            def analyze(self, **kwargs):
                logging.getLogger("presidio-analyzer").warning("Pattern matching timed out: private synthetic value")
                return []
        request = self.request("alpha@example.invalid")
        recognizers = {entity: FakeRecognizer(entity) for entity in scan.PII_TYPES}
        recognizers["KR_RRN"] = TimeoutRecognizer("KR_RRN")
        with patch.object(scan, "_presidio_load", return_value=(recognizers, None)):
            result = scan.run_scan(request)
        self.assertEqual(result["coverage"]["state"], "partial")
        self.assertNotIn("private synthetic value", json.dumps(result))

    def test_cli_does_not_echo_invalid_arguments_or_exceptions(self):
        with patch.object(scan.sys, "argv", ["helper", "--secret=synthetic-sensitive"]), patch("sys.stdout", new_callable=io.StringIO) as out, patch("sys.stderr", new_callable=io.StringIO) as err:
            self.assertEqual(scan.main(), 2)
            self.assertEqual(out.getvalue() + err.getvalue(), "")
        request = self.request("alpha@example.invalid")
        (self.private / "request.json").write_text(json.dumps(request))
        with patch.object(scan.sys, "argv", ["helper", "--request", str(self.private / "request.json")]), patch.object(scan, "run_scan", side_effect=RuntimeError("synthetic-secret")), patch("sys.stdout", new_callable=io.StringIO) as out, patch("sys.stderr", new_callable=io.StringIO) as err:
            self.assertEqual(scan.main(), 2)
            self.assertEqual(out.getvalue() + err.getvalue(), "")

    def test_same_file_alias_deduplicates_and_exact_normalization_is_explicit(self):
        request = self.request("alpha@example.invalid")
        os.link(self.inputs / "F0001", self.inputs / "F0002")
        alias = dict(request["inputs"][0], input_id="F0002", path=str(self.inputs / "F0002"))
        request["inputs"].append(alias)
        result = self.run_fake(request)
        self.assertEqual(result["summary"]["pii_occurrences_observed"], 1)
        self.assertEqual(result["summary"]["files_with_findings"], 1)
        self.assertIsNone(result["summary"]["records_with_findings"])
        self.assertEqual(result["privacy"]["normalization_version"], "exact_values_v1")

    def test_failed_record_mapping_is_distinct_from_complete_text_scan(self):
        result = self.run_fake(self.request('{"email":"alpha@example.invalid", bad}\n', kind="jsonl"))
        self.assertEqual(result["coverage"]["state"], "complete")
        self.assertEqual(result["coverage"]["inputs"][0]["record_mapping_state"], "failed")
        self.assertIsNone(result["summary"]["records_with_findings"])
        self.assertNotIn("structured_record", result["findings"][0]["source_locator"])

    def test_private_native_resource_budget_prevents_starting_another_engine(self):
        tools = self.root / "tools"
        (tools / "bin").mkdir(parents=True)
        (tools / "sensitive").mkdir()
        (tools / "bin/gitleaks").write_text("synthetic")
        (tools / "sensitive/gitleaks.toml").write_text("synthetic")
        native = self.private / "native-test"
        native.mkdir()
        with patch.object(scan, "TOOLS", tools), patch.object(scan, "MAX_PRIVATE_BYTES", 2 * scan.MAX_OUTPUT_BYTES), patch.object(scan.subprocess, "run") as runner:
            with self.assertRaisesRegex(scan.ScanError, "private_output_limit"):
                scan._gitleaks("synthetic token", native, scan.time.monotonic() + 30)
            runner.assert_not_called()

    def test_versioned_korean_label_signals_are_entity_specific_and_bounded(self):
        examples = {"KR_RRN": ("주민등록번호", "주민번호"), "KR_FRN": ("외국인등록번호",),
                    "PHONE_NUMBER": ("연락처", "전화번호"), "EMAIL_ADDRESS": ("이메일", "전자우편"),
                    "KR_PASSPORT": ("여권",), "KR_DRIVER_LICENSE": ("운전면허",),
                    "CREDIT_CARD": ("카드",), "KR_BRN": ("사업자등록번호",)}
        for entity, aliases in examples.items():
            for alias in aliases:
                with self.subTest(entity=entity, alias=alias):
                    text = alias + "=SYNTHETIC"
                    signals = scan._context_signals(text, len(alias) + 1, len(text), entity)
                    self.assertEqual(len(signals), 1)
                    self.assertTrue(signals[0].isascii())
                    self.assertNotIn(alias, str(signals))
        text = "주민번호=alpha@example.invalid"
        self.assertEqual(scan._context_signals(text, text.index("alpha"), len(text), "EMAIL_ADDRESS"), [])
        for prefix in ("이메일=" + "x" * 49, "이메일\n", "이메일\r"):
            text = prefix + "alpha@example.invalid"
            self.assertEqual(scan._context_signals(text, len(prefix), len(text), "EMAIL_ADDRESS"), [])

    def test_label_context_keeps_candidates_unassessed_and_safe(self):
        result = self.run_fake(self.request("이메일=alpha@example.invalid\n주민번호=990101-1234567"))
        private = json.loads((self.private / "result.json").read_text())
        self.assertEqual(result["context_policy"]["alias_version"], "korean_labels_v1")
        self.assertEqual(result["context_policy"]["nlp"], "not_loaded")
        for safe_finding, raw_finding in zip(result["findings"], private["findings"]):
            self.assertEqual(len(safe_finding["context_signals"]), 1)
            self.assertEqual(safe_finding["context_signals"], raw_finding["context_signals"])
            self.assertEqual(safe_finding["real_person_data"], "not_assessed")
            self.assertEqual(safe_finding["category"], "pii_candidate")
            self.assertIsNone(safe_finding["observations"][0]["format_validation"])
        safe_text = json.dumps(result, ensure_ascii=False)
        for value in ("이메일", "주민번호", "alpha@example.invalid", "990101-1234567"):
            self.assertNotIn(value, safe_text)
        self.assertIn("no_cell_or_json_pointer_mapping", result["limitations"])
        self.assertIn("no_json_escape_decoding", result["limitations"])
        self.assertIsNone(result["summary"]["distinct_people"])
        self.assertEqual(result["summary"]["external_disclosure"], "not_assessed")

    def test_gitleaks_fixed_stdin_command_and_sanitized_environment(self):
        tools = self.root / "tools"
        (tools / "bin").mkdir(parents=True)
        (tools / "sensitive").mkdir()
        (tools / "bin/gitleaks").write_text("synthetic")
        (tools / "sensitive/gitleaks.toml").write_text("synthetic")
        native = self.private / "native-test"
        native.mkdir()
        def runner(command, **kwargs):
            self.assertEqual(command[1], "stdin")
            self.assertIn("--ignore-gitleaks-allow", command)
            self.assertNotIn("GITLEAKS_CONFIG", kwargs["env"])
            self.assertEqual(kwargs["input"], b"synthetic token")
            Path(command[command.index("--report-path") + 1]).write_text("[]")
            return SimpleNamespace(returncode=0)
        with patch.object(scan, "TOOLS", tools), patch.object(scan.subprocess, "run", side_effect=runner):
            self.assertEqual(scan._gitleaks("synthetic token", native, scan.time.monotonic() + 30), [])


class RealOfflineHelperTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("FORSIC_SENSITIVE_IMAGE"), "Set FORSIC_SENSITIVE_IMAGE to an already-built local candidate; no image pull")
    def test_three_encodings_real_engines_and_private_excerpts(self):
        repository = Path(__file__).resolve().parent
        artifacts = repository / "artifacts/sensitive-synthetic"
        artifacts.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="helper-real-", dir=artifacts) as temporary:
            root = Path(temporary)
            selected, private = root / "inputs", root / "private"
            selected.mkdir()
            private.mkdir(mode=0o700)
            (private / "hmac.key").write_bytes(b"h" * 32)
            text = ("한글💎\r\n연락처=010-1234-5678 email=alpha@example.invalid\r\n"
                    "주민등록번호=990101-1234567\r\n"
                    "github_token=ghp_7Kp9xR2vL8mQ4zB6tN3yH5wC1dF0sJ9aU2eV # gitleaks:allow\r\n")
            inputs, hashes = [], {}
            for index, encoding in enumerate(("utf-8", "utf-16", "cp949"), 1):
                name = f"F{index:04d}"
                value = text if encoding != "cp949" else text.replace("💎", "합성")
                raw = value.encode(encoding)
                (selected / name).write_bytes(raw)
                hashes[name] = hashlib.sha256(raw).hexdigest()
                inputs.append({"input_id": name, "path": "/scan-input/" + name, "encoding": encoding,
                               "format": "text", "source_role": "original_evidence"})
            request = {"schema": scan.SCHEMA, "scan_id": "synthetic-real-helper", "mode": "scope_count",
                       "profiles": ["secrets", "pii_kr"], "deadline_seconds": 60, "inputs": inputs,
                       "key_path": "/private/hmac.key", "output_dir": "/private"}
            (private / "request.json").write_text(json.dumps(request))
            command = ["docker", "run", "--rm", "--pull", "never", "--network", "none", "--read-only",
                       "--pids-limit", "64", "--memory", "1g", "--cpus", "2", "--cap-drop", "ALL",
                       "--security-opt", "no-new-privileges", "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
                       "-v", str(repository / "forsic_plugin/sensitive_scan.py") + ":/opt/forsic/sensitive_scan.py:ro",
                       "-v", str(selected) + ":/scan-input:ro", "-v", str(private) + ":/private:rw",
                       os.environ["FORSIC_SENSITIVE_IMAGE"], "python", "-B", "/opt/forsic/sensitive_scan.py",
                       "--request", "/private/request.json"]
            result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90)
            self.assertEqual(result.returncode, 0, "real helper did not complete")
            self.assertEqual(result.stdout, b"FORSIC_SENSITIVE_SCAN_COMPLETE\n")
            self.assertEqual(result.stderr, b"")
            safe = json.loads((private / "safe.json").read_text())
            raw = json.loads((private / "result.json").read_text())
            self.assertEqual(safe["coverage"]["fully_processed_files"], 3)
            self.assertEqual(safe["summary"]["secret_occurrences_observed"], 3)
            self.assertEqual(safe["summary"]["pii_occurrences_observed"], 9)
            self.assertEqual(len(raw["findings"]), 12)
            for finding in raw["findings"]:
                source = (selected / finding["input_id"]).read_bytes()
                excerpt = source[finding["excerpt_byte_start"]:finding["excerpt_byte_end"]]
                self.assertEqual(excerpt.decode(finding["encoding"]), finding["excerpt"])
            for name, digest in hashes.items():
                self.assertEqual(hashlib.sha256((selected / name).read_bytes()).hexdigest(), digest)
            safe_text = json.dumps(safe)
            for forbidden in ("ghp_", "alpha@example.invalid", "990101-1234567", "value_hmac"):
                self.assertNotIn(forbidden, safe_text)


if __name__ == "__main__":
    unittest.main()
