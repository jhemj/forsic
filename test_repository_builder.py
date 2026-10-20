"""Management-only builder contracts; model/Docker smoke is explicitly opt-in."""
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile

from forsic_plugin import repository_builder as rb


class BuilderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.manager = rb.BuilderManager(self.home)
        self.archive = self.home / "source.zip"
        with zipfile.ZipFile(self.archive, "w") as out:
            out.writestr("project/LICENSE", "Synthetic permissive test source.\n")
            out.writestr("project/README.md", "A test CLI reads lines and returns a count.")
            out.writestr("project/package/adapter.py",
                         'import json,pathlib,sys;print(json.dumps({"count":len(pathlib.Path(sys.argv[1]).read_text().splitlines())}))')
        self.job = self.manager.create(source_archive=self.archive, request="Count two synthetic lines.", operation_id="fixture")
        for _ in range(100):
            self.job = self.manager.job(self.job["id"])
            if self.job["status"] != "preparing":
                break
            time.sleep(.01)
        self.assertEqual(self.job["status"], "ready", self.job)
        self.id = self.job["id"]

    def write(self, path, content):
        return self.manager.write(self.id, {"path": path, "content": content})

    def draft(self):
        self.manager.copy(self.id, {"path": "package", "destination": "vendor"})
        self.write("fixtures/input.txt", "one\ntwo\n")
        contract = rb._format_contract()
        contract.update(kind="tool", id="local.builder-test", display_name="Synthetic counter",
                        description="Counts lines, not forensic facts.", skills=[])
        contract["tools"][0]["argv"][1] = "/extension/vendor/adapter.py"
        self.write("manifest.json", json.dumps(contract))
        return self.manager.validate(self.id, {})

    def test_source_identity_idempotency_license_and_no_host_execution(self):
        self.assertIsNone(self.job["source"]["commit"])
        self.assertEqual(self.manager.create(source_archive=self.archive, request="Count two synthetic lines.",
                                            operation_id="fixture")["id"], self.id)
        with self.assertRaisesRegex(ValueError, "different builder"):
            self.manager.create(source_archive=self.archive, request="Different request", operation_id="fixture")
        pack = self.draft()
        self.assertEqual(pack["manifest"]["source"]["sha256"], self.job["source"]["sha256"])
        self.assertIn("source-notices/LICENSE", pack["manifest"]["files"])
        self.assertEqual(pack["functional_status"], "not_verified")
        with self.assertRaisesRegex(ValueError, "successful"):
            self.manager.import_pack(self.id)
        out = self.home / "unverified.zip"
        self.assertEqual(self.manager.export(self.id, out)["functional_status"], "not_verified")
        (self.manager.directory(self.id)/"source/README.md").write_text("edited")
        with self.assertRaisesRegex(ValueError, "Source identity"):
            self.manager.validate(self.id, {})

    def test_user_edits_require_current_hash_and_are_diffed(self):
        first = self.write("example.txt", "first\n")
        with self.assertRaisesRegex(ValueError, "Draft changed"):
            self.write("example.txt", "overwrite")
        saved = self.manager.write(self.id, {"path": "example.txt", "content": "second\n",
                                          "expected_sha256": first["sha256"]})
        self.assertIn("-first", saved["diff"])
        self.assertIn("+second", saved["diff"])
        self.assertTrue(list((self.manager.directory(self.id)/"checks").glob("edit-*.diff")))

    def test_zip_and_workspace_escape_rejected(self):
        for mode in ("escape", "link", "duplicate"):
            archive = self.home / (mode + ".zip")
            with zipfile.ZipFile(archive, "w") as out:
                name = "../escaped" if mode == "escape" else "file"
                if mode == "link":
                    info = zipfile.ZipInfo(name)
                    info.external_attr = (stat.S_IFLNK | 0o777) << 16
                    out.writestr(info, "/etc/passwd")
                else:
                    out.writestr(name, "one")
                    if mode == "duplicate":
                        out.writestr(name, "two")
            destination = self.home / mode
            destination.mkdir()
            with self.assertRaises(ValueError):
                rb._source_archive(archive, destination)
        for path in ("../outside", "/etc/passwd", "a/../../x"):
            with self.assertRaises(ValueError):
                self.write(path, "x")
        for url in ("file:///etc/passwd", "http://127.0.0.1/repo", "https://user:secret@github.com/a/b",
                    "https://github.com/a/b?url=internal", "https://example.org/repo"):
            with self.assertRaises(ValueError):
                rb._repository_url(url)

    def test_fixture_receipt_binds_frozen_pack_and_changes_invalidate_import(self):
        pack = self.draft()
        seen = []
        def execute(environment_id, pack_path, argv, fixture, expected, **kwargs):
            self.assertNotEqual(pack_path, self.manager.directory(self.id)/"draft")
            seen.append(Path(pack_path,"vendor/adapter.py").read_bytes())
            self.assertEqual(argv, ["python3", "/extension/vendor/adapter.py", "/extension/fixtures/input.txt"])
            self.assertEqual(expected, {"json_fields": {"count": 2}})
            self.assertEqual(Path(fixture,"input.txt").read_text(), "one\ntwo\n")
            return {"state":"succeeded","result":{"passed":True,"environment_id":environment_id,
                                                 "stdout":'{"count":2}',"execution":{"exit_code":0}}}
        with patch.object(rb, "run_fixture", side_effect=execute):
            result = self.manager.test(self.id, {"tool":"example","environment_id":"env-synthetic"})
        self.assertTrue(result["passed"])
        self.assertEqual(result["pack_sha256"], pack["sha256"])
        self.assertTrue(self.manager._verified(self.manager.job(self.id)))
        self.assertEqual(self.manager.job(self.id)["functional_status"], "verified")
        imported = self.manager.import_pack(self.id)
        self.assertEqual(imported["sha256"], pack["sha256"])
        self.assertEqual(self.manager.job(self.id)["functional_status"], "verified")
        old = self.manager.read(self.id, {"area":"draft","path":"vendor/adapter.py"})
        self.manager.write(self.id, {"path":"vendor/adapter.py","content":"print('changed')",
                                    "expected_sha256":old["sha256"]})
        self.assertFalse(self.manager._verified(self.manager.job(self.id)))
        self.assertEqual(self.manager.job(self.id)["functional_status"], "not_verified")
        self.assertNotEqual(seen[0], (self.manager.directory(self.id)/"draft/vendor/adapter.py").read_bytes())

    def test_help_empty_results_failure_and_budget_are_not_functional_success(self):
        self.draft()
        path = self.manager.directory(self.id)/"draft/manifest.json"
        manifest = json.loads(path.read_text())
        manifest["tools"][0]["argv"] = ["python3","/extension/vendor/adapter.py","--help"]
        path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError,"Help/version"):
            self.manager.test(self.id,{"tool":"example","environment_id":"env-synthetic"})
        manifest["tools"][0]["argv"][-1] = "/extension/fixtures/input.txt"
        manifest["tools"][0]["fixtures"][0]["expected"] = {"json_fields":{}}
        path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError,"nonempty"):
            self.manager.test(self.id,{"tool":"example","environment_id":"env-synthetic"})
        manifest["tools"][0]["fixtures"][0]["expected"] = {"json_fields":{"count":2}}
        path.write_text(json.dumps(manifest))
        with patch.object(rb,"run_fixture",return_value={"state":"failed","error":"wrong output"}):
            result = self.manager.test(self.id,{"tool":"example","environment_id":"env-synthetic"})
        self.assertFalse(result["passed"])
        self.assertFalse(self.manager._verified(self.manager.job(self.id)))
        self.manager.update(self.id, tests=[result]*rb.MAX_TESTS)
        with self.assertRaisesRegex(ValueError,"budget"):
            self.manager.test(self.id,{"tool":"example","environment_id":"env-synthetic"})

    def test_current_provider_native_submission_and_explicit_resume(self):
        calls = []
        def rpc(method, params, *_):
            calls.append((method, params))
            if method=="session.create":
                return {"session_id":"runtime", "stored_session_id":"durable"}
            if method=="session.resume":
                return {"session_id":"runtime2", "stored_session_id":"durable"}
            return {"status":"streaming"}
        with patch.object(rb,"_rpc",side_effect=rpc), patch.object(rb,"_ready_agent"), \
                patch.object(rb.EnvironmentManager,"catalog",return_value={"default_environment_id":"env-synthetic"}):
            self.manager.start(self.id)
            self.assertEqual([v[0] for v in calls],["session.create","prompt.submit"])
            self.assertFalse(any("model" in v[1] or "messages" in v[1] for v in calls))
            self.assertNotIn("case",calls[0][1])
            self.manager.start(self.id)
            self.assertEqual(len(calls),2)
            self.manager.cancel(self.id)
            self.assertEqual(calls[-1][0],"session.interrupt")
            self.manager.resume(self.id)
            self.assertEqual(calls[-2][0],"session.resume")
            self.assertEqual(calls[-2][1]["session_id"],"durable")

    def test_cancel_during_agent_setup_never_submits_a_prompt(self):
        calls = []
        def rpc(method, params, *_):
            calls.append(method)
            return {"session_id":"runtime", "stored_session_id":"durable"}
        def ready(*_):
            self.manager.cancel(self.id)
        with patch.object(rb, "_rpc", side_effect=rpc), patch.object(rb, "_ready_agent", side_effect=ready), \
                patch.object(rb.EnvironmentManager, "catalog", return_value={"default_environment_id":"env-synthetic"}):
            value = self.manager.start(self.id)
        self.assertNotIn("prompt.submit", calls)
        self.assertEqual(value["status"], "cancelled")

    def test_abandoned_source_is_interrupted_and_cancellation_waits_for_worker(self):
        self.manager.update(self.id, status="preparing", stage="source", created_at=time.time()-10)
        with patch.object(self.manager, "_source_running", return_value=False):
            self.assertEqual(self.manager.list_jobs()[0]["status"], "interrupted")
        with patch.object(self.manager, "_source_running", return_value=True):
            self.assertEqual(self.manager.cancel(self.id)["status"], "cancelling")
        with patch.object(self.manager, "_source_running", return_value=False):
            self.assertEqual(self.manager.list_jobs()[0]["status"], "cancelled")

    def test_json_output_contract_and_failed_operation_cannot_pass(self):
        self.draft()
        for operation in (
            {"state":"succeeded", "result":{"passed":True, "stdout":"OK"}},
            {"state":"failed", "result":{"passed":True, "stdout":'{"count":2}'}},
        ):
            with patch.object(rb, "run_fixture", return_value=operation):
                result = self.manager.test(self.id, {"tool":"example", "environment_id":"env-synthetic"})
            self.assertFalse(result["passed"])
            self.assertFalse(self.manager._verified(self.manager.job(self.id)))
        self.assertIn("JSON", self.manager.job(self.id)["tests"][0]["error"])

    def test_inspection_pages_source_and_keeps_bulk_inventory_out_of_model_input(self):
        catalog = {"default_environment_id":"env-synthetic", "environments":[
            {"environment_id":"env-synthetic", "images":{}, "plan":{"installed":{"python":{
                "private-bulk-catalog-"+str(n): "1.0" for n in range(1000)}}}}]}
        with patch.object(rb.EnvironmentManager, "catalog", return_value=catalog):
            result = self.manager.inspect(self.id, {})
        self.assertNotIn("private-bulk-catalog", json.dumps(result))
        self.assertNotIn("files", result["source"])
        self.assertIn("README.md", result["listing"]["files"])
        self.assertLess(len(json.dumps(result)), 6000)
        for n in range(90):
            self.write(f"notes/{n:03}.txt", "fixture")
        with patch.object(rb.EnvironmentManager, "catalog", return_value=catalog):
            first = self.manager.inspect(self.id, {"area":"draft", "path":"notes/"})
            second = self.manager.inspect(self.id, {"area":"draft", "path":"notes/", "offset":80})
        self.assertEqual(first["listing"]["next_offset"],80)
        self.assertEqual(len(first["listing"]["files"]),80)
        self.assertEqual(len(second["listing"]["files"]),10)

    def test_fixture_admission_failure_is_retained_and_can_be_cancelled(self):
        self.draft()
        with patch.object(rb, "run_fixture", side_effect=ValueError("Environment absent")):
            with self.assertRaisesRegex(ValueError, "Environment absent"):
                self.manager.test(self.id, {"tool":"example", "environment_id":"absent"})
        self.assertEqual(self.manager.job(self.id)["tests"][-1]["status"], "failed")
        self.assertEqual(self.manager.cancel(self.id)["status"], "cancelled")

    def test_import_checks_the_captured_pack_not_a_second_mutable_snapshot(self):
        self.draft()
        with patch.object(self.manager, "_verified", return_value=True):
            with self.assertRaisesRegex(ValueError, "successful"):
                self.manager.import_pack(self.id)
            exported = self.manager.export(self.id, self.home/"draft-export.zip")
        self.assertEqual(exported["functional_status"], "not_verified")

    def test_initial_provider_fallback_is_rejected_before_request(self):
        from types import SimpleNamespace
        from tui_gateway import server
        ready = SimpleNamespace(wait=lambda **_: True)
        session = {"profile_home":str(self.home), "agent_ready":ready,
                   "agent":SimpleNamespace(_pending_fallback_notice="primary unavailable")}
        with patch.object(server, "_sessions", {"builder-test":session}):
            with self.assertRaisesRegex(ValueError, "fallback is disabled"):
                rb._ready_agent("builder-test", self.home)


if __name__ == "__main__":
    unittest.main()
