"""Reader writing follows native calls; no model/network in contract tests."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_report_driven import fixture, worked
from forsic_plugin.notes import note
from forsic_plugin.report_driven.host import current, invoke, checked
from forsic_plugin.report_driven.narrative import prepare, status, project_body
from forsic_plugin.report_driven.views import compile_views, bundle_stale, bundles


class ReportNarrativeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.case, self.reads, self.note = fixture(self.tmp.name)
        state = worked(self.case)
        question = state["questions"][0]
        invoke(self.case, {"action": "question", "payload": {
            "operation_id": "editorial-fixture-assessment", "question_id": question["id"],
            "expected_revision": question["revision"], "assessment": "undetermined",
            "reason": "Explicitly retain the synthetic uncertain answer before editorial writing"}})

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, section, text=None, keys=None, **extra):
        packet = invoke(self.case, {"action": "compose", "section": section})
        if keys is None:
            keys = [r["key"] for r in packet["items"] if r["kind"] in ("question", "claim", "gap")]
        args = {
            "action": "compose", "section": section, "operation_id": section + "-" + str(packet["revision"]),
            "basis_id": packet["basis_id"], "expected_revision": packet["revision"],
            "draft": {"title": section, "paragraphs": [
                {"text": text or {"summary": "두 기록의 이름은 비슷하지만 같은 작업이라고 판단할 연결 정보는 확인되지 않았습니다.",
                                 "findings": "요청 기록에는 공통 작업 식별자가 없어 프로세스 생성 기록과 연결할 수 없습니다.",
                                 "limits": "승인 여부와 최종 실행 성공은 검토 범위 밖입니다. 공통 작업 식별자가 확보되면 다시 확인할 수 있습니다."}[section],
                 "basis_keys": keys}]},
            **extra,
        }
        return invoke(self.case, args), args

    def finish(self):
        for section in ("summary", "findings", "limits"):
            result, _ = self.write(section)
        self.assertTrue(result["ready"])
        return result

    def test_native_render_requests_authored_prose_not_blank_template(self):
        before = self.case.db.read_bytes()
        with patch("socket.socket", side_effect=AssertionError("No hidden model call")):
            result = invoke(self.case, {"action": "render"})
        self.assertFalse(result["rendered"])
        self.assertEqual(result["status"], "authoring_required")
        self.assertEqual(result["next_call"]["arguments"]["action"], "compose")
        self.assertEqual(before, self.case.db.read_bytes())
        self.assertFalse(list(self.case.output.glob("report-bundle-*")))

    def test_sections_saved_idempotently_render_same_prose_and_exact_notes(self):
        first, request = self.write("summary")
        replay = invoke(self.case, request)
        self.assertEqual(first, replay)
        self.assertEqual(status(current(self.case))["revision"], 1)
        self.write("findings")
        last, _ = self.write("limits")
        self.assertTrue(last["ready"])
        state = current(self.case)
        checked(state)
        self.assertEqual(len(state["report_narratives"]), 1)
        pages = project_body(state)
        self.assertIn("같은 작업", pages[0]["blocks"][0]["text"])
        self.assertTrue(pages[0]["blocks"][0]["citation_refs"])
        result = invoke(self.case, {"action": "render"})
        self.assertTrue(result["files"])
        html = Path(next(p for p in result["files"] if p.endswith(".html"))).read_text()
        self.assertIn("두 기록의 이름은 비슷하지만", html)
        self.assertIn("근거 주석", html)
        self.assertNotIn("종합 판단 문구 미작성", html)
        body = compile_views(state)["integrated"]["pages"][0]
        self.assertIn("[", json.dumps(body, ensure_ascii=False))

    def test_stale_basis_revision_foreign_reference_and_duplicate_prose_fail(self):
        result, request = self.write("summary")
        for changes, message in (
            ({"operation_id": "stale-basis", "basis_id": "old"}, "judgments changed"),
            ({"operation_id": "stale-revision"}, "revision changed"),
            ({"operation_id": "foreign", "expected_revision": 1,
              "draft": {"title": "x", "paragraphs": [{"text": "x", "basis_keys": ["C9999"]}]}}, "basis_keys"),
        ):
            with self.assertRaisesRegex(ValueError, message):
                invoke(self.case, {**request, **changes})
        with self.assertRaisesRegex(ValueError, "identical paragraph"):
            self.write("findings", text=request["draft"]["paragraphs"][0]["text"])
        self.assertEqual(status(current(self.case))["revision"], result["revision"])

    def test_correction_stales_only_editorial_copy_and_keeps_old_bundle_bytes(self):
        self.finish()
        rendered = invoke(self.case, {"action": "render"})
        before = {p: Path(p).read_bytes() for p in rendered["files"]}
        old = note(self.case, {"action": "get", "note_id": self.note["note_id"]})["note"]
        note(self.case, {"action": "save", "note_id": old["note_id"], "revision": old["revision"],
                         "question": old["question"], "answer": "추가 기록에서도 동일 작업 여부는 아직 확인되지 않았습니다.",
                         "evidence_ids": old["evidence_ids"], "correction_reason": "새 검토"})
        state = current(self.case)
        self.assertTrue(status(state)["stale"])
        self.assertIsNone(project_body(state))
        self.assertFalse(invoke(self.case, {"action": "render"})["rendered"])
        self.assertEqual(before, {p: Path(p).read_bytes() for p in before})
        self.assertTrue(bundles(self.case)[0]["stale"])

    def test_material_gaps_and_selected_questions_cannot_silently_disappear(self):
        self.finish()
        state = current(self.case)
        view = status(state)
        self.assertFalse(view["unrepresented_question_ids"])
        self.assertFalse(view["missing_material_gap_keys"])
        # Structured coverage is not a semantic certificate.
        self.assertFalse(view["semantic_approval"])
        state["report_narratives"][0]["sections"]["limits"]["paragraphs"][0]["basis_keys"] = ["C1"]
        for section in ("summary", "findings"):
            state["report_narratives"][0]["sections"][section]["paragraphs"][0]["basis_keys"] = ["C1"]
        # Input fixture has an unassessed gap; mark it material and bind that
        # synthetic basis so this assertion isolates structural coverage.
        state["gaps"][0]["impact"] = "material"
        from forsic_plugin.report_driven.narrative import basis
        state["report_narratives"][0]["basis_id"] = basis(state, view["question_ids"])[0]
        result = status(state)
        self.assertFalse(result["ready"])
        self.assertTrue(result["missing_material_gap_keys"])

    def test_read_packet_bound_and_explicit_partial_snapshot(self):
        state = current(self.case)
        state["questions"][0]["answer"] = "가" * 15000
        packet = prepare(state, {})
        self.assertTrue(packet["items"][0]["body_omitted"])
        self.assertIn("read_call", packet["items"][0])
        self.assertNotIn("가" * 10000, json.dumps(packet))
        result = invoke(self.case, {"action": "render", "allow_partial": True})
        self.assertTrue(result["files"])

    def test_editorial_text_never_becomes_evidence_or_clears_question_limits(self):
        before = current(self.case)
        self.finish()
        after = current(self.case)
        for field in ("sources", "observations", "questions", "gaps", "claims"):
            self.assertEqual(before[field], after[field])
        self.assertNotEqual(before["meta"]["snapshot_id"], after["meta"]["snapshot_id"])
        self.assertEqual(before["meta"]["content_revision"], after["meta"]["content_revision"])

    def test_native_facade_retains_prose_without_fabricating_completion(self):
        response = json.loads(self.case.invoke("forsic_reporting", {"action": "compose"}))
        self.assertEqual(response["status"], "authoring_required")
        self.assertNotIn("error", response)
        self.assertEqual(current(self.case)["status"]["investigation"], "partial")

    def test_invalidated_judgment_precedes_editorial_rewrite_and_can_be_reassessed(self):
        self.finish()
        old = note(self.case, {"action": "get", "note_id": self.note["note_id"]})["note"]
        note(self.case, {"action": "save", "note_id": old["note_id"], "revision": old["revision"],
                         "question": old["question"], "answer": "새 관측과 기존 해석의 연결은 재검토가 필요합니다.",
                         "evidence_ids": old["evidence_ids"], "correction_reason": "관측 범위 변경"})
        state = current(self.case)
        question = state["questions"][0]
        self.assertTrue(question["judgment_review_required"])
        before = self.case.db.read_bytes()
        for action in ("compose", "render"):
            with self.subTest(action=action), patch("socket.socket", side_effect=AssertionError("offline only")):
                packet = invoke(self.case, {"action": action})
                self.assertEqual(packet["status"], "judgment_review_required")
                recovery = packet["judgment_review_questions"][0]
                self.assertEqual((recovery["question_id"], recovery["revision"]),
                                 (question["id"], question["revision"]))
                self.assertEqual(packet["next_call"], recovery["read_call"])
                read = invoke(self.case, packet["next_call"]["arguments"])
                self.assertIn(question["id"], json.dumps(read))
        self.assertEqual(before, self.case.db.read_bytes())
        with self.assertRaisesRegex(ValueError, "judgment.*review"):
            self.write("summary", text="이 문단의 수정만으로 판단 검토를 대신할 수 없습니다.")
        self.assertEqual(before, self.case.db.read_bytes())
        self.assertTrue(invoke(self.case, {"action": "render", "allow_partial": True})["files"])
        response = invoke(self.case, {"action": "question", "payload": {
            "operation_id": "reassess-current-answer", "question_id": question["id"],
            "expected_revision": question["revision"], "assessment": "undetermined",
            "reason": "현재 답의 유보 판단을 명시적으로 재평가"}})
        self.assertIn("question", response)
        self.assertEqual(invoke(self.case, {"action": "compose"})["status"], "authoring_required")
        self.finish()
        self.assertTrue(invoke(self.case, {"action": "render"})["files"])

    def test_pending_judgment_does_not_block_an_unrelated_selected_question(self):
        state = current(self.case)
        other = deepcopy(state["questions"][0])
        other.update(id="Q-other", judgment_review_required=True, work_state="scoped_closed",
                     closure_rationale="No further retained check chosen")
        state["questions"].append(other)
        packet = prepare(state, {"question_ids": [state["questions"][0]["id"]]})
        self.assertEqual(packet["status"], "authoring_required")
        self.assertEqual(packet["judgment_review_questions"], [])
        pending = prepare(state, {"question_ids": [other["id"]]})
        self.assertEqual(pending["status"], "judgment_review_required")
        item = next(row for row in pending["items"] if row["kind"] == "question")
        self.assertEqual(item["work_state"], "scoped_closed")
        self.assertTrue(item["judgment_review_required"])


if __name__ == "__main__":
    unittest.main()
