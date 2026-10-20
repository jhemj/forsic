"""Native-agent editorial writing over the existing, immutable case judgments.

This is a read packet and a CAS writer, not a model client or investigation loop.
The same agent writes bounded Korean sections. References and freshness are
checked here; prose quality and entailment remain analytical responsibilities.
"""
from copy import deepcopy
import json
import time
import uuid

SECTIONS = (
    ("summary", "종합 판단", "Explain the strongest supported answer, scope and impact in connected Korean prose."),
    ("findings", "주요 근거와 사건 경과", "Explain how the evidence supports the account, competing explanations and time assumptions. Do not repeat the summary."),
    ("limits", "남은 확인과 판단 한계", "Explain important unresolved questions, what they could change, and useful next checks or reopening conditions."),
)
SECTION_IDS = {key for key, _, _ in SECTIONS}


def schema():
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "title": {"type": "string", "minLength": 1, "maxLength": 160},
            "paragraphs": {
                "type": "array", "minItems": 1, "maxItems": 12,
                "items": {
                    "type": "object", "additionalProperties": False,
                    "properties": {
                        "text": {"type": "string", "minLength": 1, "maxLength": 2000},
                        "basis_keys": {"type": "array", "minItems": 1, "maxItems": 32,
                                       "uniqueItems": True, "items": {"type": "string"}},
                    },
                    "required": ["text", "basis_keys"],
                },
            },
        },
        "required": ["title", "paragraphs"],
    }


def selection(state, args):
    """Use the same conversation/goal boundary as the existing renderer."""
    from .host import selected_question_records
    from .report_reference import scope_from_args
    selected = args.get("question_ids")
    scope = scope_from_args(args)
    if selected is None and args.get("_goal_id") and args.get("_session_id"):
        selected = [q["id"] for q in state["questions"]
                    if q.get("goal_id") == args["_goal_id"] and q.get("session_id") == args["_session_id"]]
    _, ids = selected_question_records(state, selected)
    if any(scope.values()) and any(
        any(q.get(key, "") != value for key, value in scope.items())
        for q in state["questions"] if q["id"] in ids
    ):
        raise ValueError("Compose only questions from the current conversation and goal")
    return sorted(ids)


def basis(state, ids):
    from .views import selected_report_identity, selected_report_state
    clean = deepcopy(state)
    clean.pop("report_narratives", None)
    selected = selected_report_state(clean, ids)
    return selected_report_identity(clean, ids), selected


def _unique_refs(refs):
    result = []
    seen = set()
    for ref in refs:
        identity = (ref.get("kind"), ref.get("id"), ref.get("version"))
        if identity not in seen:
            seen.add(identity)
            result.append(deepcopy(ref))
    return result


def material(state):
    """Short stable handles save the model from assembling source identities."""
    from .current_judgment import current_claims
    claims = current_claims(state)
    by_claim = {c["id"]: c for c in claims}
    sources = {s["id"]: s for s in state["sources"]}
    records = []
    for index, q in enumerate(state["questions"], 1):
        own = [by_claim[r["id"]] for r in q["claim_refs"] if r["id"] in by_claim]
        refs = [r for c in own for r in c["source_refs"] + c["counterevidence_refs"]]
        if not refs:
            refs = [{"kind": "source", "id": sid, "version": sources[sid]["version"]}
                    for sid in q.get("evidence_ids", []) if sid in sources]
        records.append({
            "key": f"Q{index}", "kind": "question", "id": q["id"], "question_ids": [q["id"]],
            "question": q["question"], "answer": q["answer"], "scope": q["scope"],
            "work_state": q["work_state"], "assessment": q["assessment"],
            "judgment_review_required": q.get("judgment_review_required", False),
            "reader_explanation": q.get("reader_explanation", {}),
            "alternatives": q.get("alternatives", []), "next_checks": q.get("next_checks", []),
            "remaining_gap_ids": q["remaining_gap_ids"],
            "citation_refs": _unique_refs(refs),
        })
    owners = {c["id"]: [q["id"] for q in state["questions"]
                        if c["id"] in {r["id"] for r in q["claim_refs"]}] for c in claims}
    for index, c in enumerate(claims, 1):
        records.append({
            "key": f"C{index}", "kind": "claim", "id": c["id"], "question_ids": owners[c["id"]],
            **{key: deepcopy(c[key]) for key in (
                "statement", "scope", "status", "assertion_kind", "inference_strength",
                "reasoning_summary", "assumptions", "limitations") if key in c},
            "citation_refs": _unique_refs(c["source_refs"] + c["counterevidence_refs"]),
        })
    for field, prefix, kind in (("gaps", "G", "gap"), ("hypotheses", "H", "hypothesis"),
                                ("timeline", "T", "timeline")):
        rows = state[field]
        if field == "gaps":
            rows = [g for g in rows if g["disposition"] not in ("resolved", "not_applicable")]
        for index, row in enumerate(rows, 1):
            refs = [row["observation_ref"]] if row.get("observation_ref") else []
            refs.extend(row.get("counterevidence_refs", []))
            refs.extend(r for r in row.get("basis_refs", []) if r["kind"] in ("source", "observation"))
            records.append({
                **deepcopy(row), "key": prefix + str(index), "kind": kind,
                "question_ids": [row["question_ref"]["id"]] if row.get("question_ref") else [],
                "citation_refs": _unique_refs(refs),
            })
    return records


def _latest(state, ids):
    matches = [r for r in state.get("report_narratives", []) if r["question_ids"] == ids]
    return max(matches, key=lambda r: r["revision"], default=None)


def status(state, ids=None):
    ids = sorted(ids or [q["id"] for q in state["questions"]])
    identity, selected = basis(state, ids)
    latest = _latest(state, ids)
    sections = latest.get("sections", {}) if latest and latest["basis_id"] == identity else {}
    items = material(selected)
    used = {k for section in sections.values() for p in section["paragraphs"] for k in p["basis_keys"]}
    covered = {qid for item in items if item["key"] in used for qid in item["question_ids"]}
    missing_gaps = [item["key"] for item in items
                    if item["kind"] == "gap" and item.get("impact") in ("material", "decision_critical")
                    and item["key"] not in used]
    missing_sections = [key for key, _, _ in SECTIONS if key not in sections]
    pending = [{"question_id": q["id"], "revision": q["revision"],
                "read_call": {"tool": "forsic_reporting", "arguments": {"action": "state", "question_id": q["id"]}}}
               for q in selected["questions"] if q.get("judgment_review_required")]
    return {
        "basis_id": identity, "question_ids": ids,
        "revision": latest["revision"] if latest else 0,
        "ready": not pending and not missing_sections and not missing_gaps and set(ids) <= covered,
        "judgment_review_questions": pending,
        "stale": bool(latest and latest["basis_id"] != identity),
        "missing_sections": missing_sections, "missing_material_gap_keys": missing_gaps,
        "unrepresented_question_ids": [qid for qid in ids if qid not in covered],
        "sections": sections, "semantic_approval": False,
    }


def prepare(state, args):
    ids = selection(state, args)
    progress = status(state, ids)
    identity, selected = basis(state, ids)
    section = args.get("section") or next(iter(progress["missing_sections"]), "summary")
    if section not in SECTION_IDS:
        raise ValueError("Choose summary, findings or limits")
    offset = args.get("offset", 0)
    if type(offset) is not int or offset < 0:
        raise ValueError("compose offset must be a nonnegative item index")
    rows = material(selected)
    page = []
    used = 0
    for item in rows[offset:]:
        encoded = json.dumps(item, ensure_ascii=False)
        if page and (len(page) >= 8 or used + len(encoded) > 18000):
            break
        if len(encoded) > 9000:
            # No invisible truncation. The existing state reader supplies full
            # selected records; omitted text is never treated as unreviewed evidence.
            item = {key: item[key] for key in ("key", "kind", "id", "question_ids", "citation_refs")}
            field = {"question": "questions", "claim": "claims", "gap": "gaps",
                     "hypothesis": "hypotheses", "timeline": "timeline"}[item["kind"]]
            index = next(i for i, r in enumerate(state[field]) if r["id"] == item["id"])
            item.update(body_omitted=True, original_characters=len(encoded),
                        read_call={"tool": "forsic_reporting", "arguments": {
                            "action": "state", "snapshot_id": state["meta"]["snapshot_id"],
                            "pointer": f"/{field}/{index}", "limit": 4096}})
        page.append(item)
        used += len(json.dumps(item, ensure_ascii=False))
    next_offset = offset + len(page)
    output = {
        "status": ("judgment_review_required" if progress["judgment_review_questions"] else
                   "editorial_ready" if progress["ready"] else "authoring_required"),
        "role": "editorial_projection_not_evidence_or_new_judgment",
        **{k: progress[k] for k in ("basis_id", "question_ids", "revision", "stale",
                                   "missing_sections", "missing_material_gap_keys", "unrepresented_question_ids",
                                   "judgment_review_questions")},
        "section": section, "instruction": dict((k, instruction) for k, _, instruction in SECTIONS)[section],
        "writing": (
            "Write fresh connected Korean prose for the reader, not pasted question answers, IDs, tool logs or empty tables. "
            "Use basis_keys on every paragraph; the host adds [n] evidence notes. Preserve observed/inferred/unknown, "
            "important alternatives, limits and time assumptions in the relevant prose. The shared reader explanations "
            "belong in the appendix; synthesize across them for the body. Correct changed facts through forsic_question first. "
            "Use this native turn; no external writer or new investigation. Saving refs is not semantic approval."
        ),
        "items": page, "total_items": len(rows),
        "saved_sections": [
            {"section": key, "title": value["title"],
             "paragraphs": (value["paragraphs"] if key == section else [
                 {"text": p["text"][:300], "text_omitted": len(p["text"]) > 300,
                  "basis_keys": p["basis_keys"]} for p in value["paragraphs"]]),
             "read_call": {"tool": "forsic_reporting", "arguments": {
                 "action": "compose", "section": key, "question_ids": ids, "basis_id": identity}}}
            for key, value in progress["sections"].items()],
        "save_contract": {
            "tool": "forsic_reporting", "action": "compose", "section": section,
            "question_ids": ids, "basis_id": identity, "expected_revision": progress["revision"],
            "operation_id": "choose-a-new-operation-id-for-this-section-edit", "draft": schema(),
        },
    }
    if next_offset < len(rows):
        output["next_call"] = {"tool": "forsic_reporting", "arguments": {
            "action": "compose", "section": section, "question_ids": ids,
            "basis_id": identity, "offset": next_offset}}
    if progress["judgment_review_questions"]:
        output["next_call"] = progress["judgment_review_questions"][0]["read_call"]
        output["instruction"] = "Read the indicated current question and explicitly reassess its answer/assessment or selected judgment through forsic_question before authoring report prose. Scope closure and prose edits do not clear an invalidated judgment."
        output.pop("save_contract")
    return output


def save(case, state, args):
    from .host import current
    from .investigation_state import input_problems, mutation_replay, record_mutation
    operation = args.get("operation_id")
    if not isinstance(operation, str) or not operation.strip() or len(operation) > 200:
        raise ValueError("compose save requires a nonempty operation_id")
    if args.get("section") not in SECTION_IDS:
        raise ValueError("compose save requires section")
    problems = input_problems(args["draft"], schema(), "draft")
    if problems:
        raise ValueError("Invalid report prose: " + str(problems))
    if not args["draft"]["title"].strip() or any(not p["text"].strip() for p in args["draft"]["paragraphs"]):
        raise ValueError("Report prose must not be blank")
    ids = selection(state, args)
    with case.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        replay = mutation_replay(db, operation, args)
        if replay is not None:
            return replay
        now = current(case)
        if ids != selection(now, args):
            raise ValueError("Report scope changed; read compose again")
        progress = status(now, ids)
        if progress["judgment_review_questions"]:
            raise ValueError("Current judgment requires review; read compose and reassess the indicated question before saving report prose")
        if args.get("basis_id") != progress["basis_id"]:
            raise ValueError("Report judgments changed; read compose again and revise the affected prose")
        if type(args.get("expected_revision")) is not int or args["expected_revision"] != progress["revision"]:
            raise ValueError("Report prose revision changed; read compose before saving")
        _, selected = basis(now, ids)
        choices = {r["key"]: r for r in material(selected)}
        paragraphs = []
        for p in args["draft"]["paragraphs"]:
            if any(key not in choices for key in p["basis_keys"]):
                raise ValueError("Use current compose basis_keys; no foreign or invented reference")
            citations = _unique_refs(r for key in p["basis_keys"] for r in choices[key]["citation_refs"])
            paragraphs.append({**deepcopy(p), "citation_refs": citations})
        sections = deepcopy(progress["sections"])
        sections[args["section"]] = {"title": args["draft"]["title"].strip(), "paragraphs": paragraphs}
        texts = [" ".join(p["text"].split()) for section in sections.values() for p in section["paragraphs"]]
        if len(set(texts)) != len(texts):
            raise ValueError("The report repeats an identical paragraph; consolidate it in one section")
        value = {
            "schema": "forsic-report-narrative-1", "question_ids": ids, "basis_id": progress["basis_id"],
            "revision": progress["revision"] + 1, "sections": sections,
            "session_id": args.get("_session_id", ""), "goal_id": args.get("_goal_id", ""),
            "saved_at": time.time(), "semantic_approval": False,
        }
        event_id = uuid.uuid4().hex
        db.execute("INSERT INTO events VALUES (?,?,?,?,?)",
                   (event_id, value["saved_at"], "report_narrative", args.get("_session_id", ""),
                    json.dumps(value, ensure_ascii=False)))
        candidate = deepcopy(now)
        candidate["report_narratives"] = [*now.get("report_narratives", []), value]
        after = status(candidate, ids)
        result = {key: after[key] for key in (
            "basis_id", "question_ids", "revision", "ready", "missing_sections",
            "missing_material_gap_keys", "unrepresented_question_ids", "semantic_approval")}
        result.update(saved=True, event_id=event_id, section=args["section"],
                      next_call={"tool": "forsic_reporting", "arguments": {
                          "action": "render" if after["ready"] else "compose", "question_ids": ids}})
        record_mutation(db, operation, args, result)
    return result


def invoke(case, state, args):
    if "draft" in args:
        return save(case, state, args)
    packet = prepare(state, args)
    if args.get("basis_id") is not None and args["basis_id"] != packet["basis_id"]:
        raise ValueError("Report judgments changed; restart compose paging at the current basis")
    return packet


def project_body(state):
    """Read the validated writer output; never synthesize facts in a renderer."""
    progress = status(state)
    if not progress["ready"]:
        return None
    return [
        {"id": "I" + str(index).zfill(2), "title": progress["sections"][key]["title"],
         "section": "body", "kicker": label, "subtitle": "",
         "blocks": [{"kind": "paragraph", "text": p["text"], "citation_refs": p["citation_refs"]}
                    for p in progress["sections"][key]["paragraphs"]]}
        for index, (key, label, _) in enumerate(SECTIONS, 1)
    ]
