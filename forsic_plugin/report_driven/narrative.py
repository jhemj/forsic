"""Native-agent editorial writing over the existing, immutable case judgments.

This is a read packet and a CAS writer, not a model client or investigation loop.
The same agent writes bounded Korean sections. References and freshness are
checked here; prose quality and entailment remain analytical responsibilities.
"""
from copy import deepcopy
import json
import time
import uuid

from .template_contract import (GROUPS, GROUP_IDS, OPTIONAL_GROUPS, WRITING_ORDER, REVIEW_CHECKS,
                                TEMPLATE_VERSION, WRITING_GUIDANCE, cell_labels, group_contract, validate_group,
                                ASSESSMENT_SCALES, ASSESSMENT_GUIDANCE, EVENT_KINDS, assessment_schema)
from . import authoring_form, authoring_policy


class ReportAuthoringError(ValueError):
    """Reject an invalid edit/read, returning a scoped read-only recovery call."""
    def __init__(self, message, progress, section=None, *, subtype='reference_invalid', problems=None):
        super().__init__(message)
        arguments = {'action': 'compose', 'question_ids': progress['question_ids']}
        if section in GROUP_IDS | {'review'}:
            arguments['section'] = section
        self.details = {'error_code': 'report_authoring_reference_invalid',
            'error_subtype': subtype, 'saved': False, 'draft_preserved': True,
            'recovery': {'tool': 'forsic_reporting', 'arguments': arguments},
            'next_step': 'Use recovery to read the current contract and returned read_call/edit_ref. '
                         'Keep the intended prose for review; do not invent pointers, refresh an old write '
                         'reference yourself, or recollect evidence. No draft was changed.'}
        if problems is not None:
            bounded_problems = []
            problem_bytes = 2  # JSON array brackets; keep each diagnostic indivisible.
            for problem in problems:
                encoded_bytes = len(json.dumps(problem, ensure_ascii=False).encode('utf-8'))
                if len(bounded_problems) < 16 and problem_bytes + encoded_bytes + 2 <= 4096:
                    bounded_problems.append(problem)
                    problem_bytes += encoded_bytes + 2
            self.details.update(error_code='report_authoring_input_invalid',
                input_problems=bounded_problems,
                omitted_problem_count=len(problems) - len(bounded_problems),
                next_step='Repair the named authoring fields using the recovery contract and save_contract. '
                          'Read that contract when a diagnostic is omitted. '
                          'Keep the intended prose and selected scope; no draft was changed. '
                          'Do not recollect evidence or invent a write reference.')


def schema():
    return {
        "type": "object", "additionalProperties": False,
        "properties": {"mode": {"type": "string", "enum": ["replace", "update", "merge"],
            "description": "merge adds or replaces named nodes while preserving siblings; use for incremental writing. update changes existing nodes only and preserves completeness. Explicit replace replaces the whole group, including removing omitted nodes. Omitted mode cannot remove saved nodes. Send complete changed nodes. Never revives historical or stale prose."},
            "complete": {"type": "boolean", "description": "For merge, false keeps work unfinished and true completes the combined group; omission preserves existing completeness (true for a new group). For replace, omission means complete. Omit with update."}, "entries": {
            "type": "array", "minItems": 1, "maxItems": 16,
            "items": {"type": "object", "additionalProperties": False,
                "properties": {
                    "node_id": {"type": "string", "minLength": 1, "maxLength": 100},
                    "cells": {"type": "array", "minItems": 1, "maxItems": 3,
                              "description": "Self-contained executive explanations: finding, plain-language meaning/impact, and needed decision. Complete nominal sentences, not jargon fragments. Overall judgment: headline first, then explanatory lines. Metadata: concise values. Technical derivation/limits belong in cell_details. The host supplies typography/citations.",
                              "items": {"type": "string", "minLength": 1, "maxLength": 2000, "pattern": r"\S"}},
                    "cell_details": {"type": "array", "minItems": 1, "maxItems": 3,
                                     "description": "Practitioner appendix: basis, exact scope, assumptions, alternatives, limitations and next checks. Exactly cell_count strings, empty strings allowed. Do not repeat the short lead.",
                                     "items": {"type": "string", "maxLength": 2000}},
                    "assessment": assessment_schema(),
                    "event_kind": {"type": "string", "enum": list(EVENT_KINDS),
                        "description": "Timeline only. Analyst-selected event category; activity means incident-related, not automatically malicious. Preserve original time precision, timezone assumptions and response/acquisition distinctions."},
                    "basis_keys": {"type": "array", "maxItems": 32, "uniqueItems": True,
                                   "items": {"type": "string"}},
                    "cell_basis_keys": {"type": "array", "minItems": 1, "maxItems": 3,
                        "description": "Exact support handles parallel to cells. Each list selects only that cell's support from basis_keys; entry-only keys provide context. Omit only for a single shared proposition.",
                        "items": {"type": "array", "maxItems": 32, "uniqueItems": True,
                                  "items": {"type": "string"}}},
                    "status": {"type": "string", "enum": ["saved", "unavailable"]},
                    "unavailable_reason": {"type": "string", "maxLength": 2000},
                }, "required": ["node_id", "cells", "basis_keys", "status"]},
        }}, "required": ["entries"],
    }


def input_schema():
    return authoring_form.input_schema(schema())


def review_schema():
    text = {"type": "string", "minLength": 1, "maxLength": 2000, "pattern": r"\S"}
    return {"type": "object", "additionalProperties": False,
        "properties": {
            "summary": text,
            "answerability_checks": authoring_policy.resolution_schema(),
            "reviewed_node_ids": {"type": "array", "uniqueItems": True, "minItems": 1,
                                  "items": {"type": "string"}},
            "checks": {"type": "array", "minItems": len(REVIEW_CHECKS), "maxItems": len(REVIEW_CHECKS),
                "items": {"type": "object", "additionalProperties": False,
                    "properties": {"check": {"type": "string", "enum": list(REVIEW_CHECKS)},
                                   "result": {"type": "string", "enum": ["satisfied", "unresolved"]},
                                   "reason": text}, "required": ["check", "result", "reason"]}},
            "findings": {"type": "array", "maxItems": 32, "items": {
                "type": "object", "additionalProperties": False,
                "properties": {"finding_id": {"type": "string", "minLength": 1, "maxLength": 160},
                    "node_ids": {"type": "array", "minItems": 1, "uniqueItems": True,
                                 "items": {"type": "string"}},
                    "disposition": {"type": "string", "enum": ["corrected", "retained", "unconfirmed"]},
                    "reason": text, "blocking": {"type": "boolean"}},
                "required": ["finding_id", "node_ids", "disposition", "reason", "blocking"]}},
            "comparisons": {"type": "array", "minItems": 1, "maxItems": 32, "items": {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "node_ids": {"type": "array", "minItems": 1, "uniqueItems": True,
                                 "items": {"type": "string"}},
                    "basis_keys": {"type": "array", "uniqueItems": True, "items": {"type": "string"}},
                    "statement": text, "source_support": text, "limits": text, "assessment": text},
                "required": ["node_ids", "basis_keys", "statement", "source_support", "limits", "assessment"]}},
        }, "required": ["summary", "reviewed_node_ids", "checks", "comparisons", "findings", "answerability_checks"]}


def selection(state, args):
    """Use the same conversation/goal boundary as the existing renderer."""
    from .host import selected_question_records
    from .report_reference import scope_from_args, question_in_scope
    selected = args.get("question_ids")
    scope = scope_from_args(args)
    if selected is None and args.get("_goal_id") and args.get("_session_id"):
        authored = [r for r in state.get('report_narratives', [])
                    if r.get('goal_id') == args['_goal_id'] and r.get('session_id') == args['_session_id']]
        if authored:
            selected = max(authored, key=lambda r:(r.get('saved_at', 0), r.get('revision', 0)))['question_ids']
        elif scope.get('source_question_ids'):
            selected = list(scope['source_question_ids'])
        else:
            selected = [q["id"] for q in state["questions"]
                        if q.get("goal_id") == args["_goal_id"] and q.get("session_id") == args["_session_id"]]
    _, ids = selected_question_records(state, selected)
    # Explicit IDs and this goal's saved selection take priority. Otherwise an
    # authenticated exact prior-report selection is an editorial default only;
    # ordinary goals keep their own questions and no writer authority is adopted.
    if any(scope.values()) and any(
        not question_in_scope(q, scope) for q in state["questions"] if q["id"] in ids
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
    from .current_judgment import current_claims, authoring_judgment
    claims = current_claims(state)
    by_claim = {c["id"]: c for c in claims}
    sources = {s["id"]: s for s in state["sources"]}
    records = []
    for index, q in enumerate(state["questions"], 1):
        judgment = authoring_judgment(state, q)
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
            "remaining_gap_ids": [g['id'] for g in judgment['remaining_gaps']],
            "current_judgment": judgment,
            "reconciliation_instruction": (
                "Use the current selected claims and gap dispositions. Compare local assessments with the aggregate answer; "
                "resolve or retain each affected gap and replace or retain affected assumptions/limits through forsic_question. "
                "A later successful acquisition does not establish search coverage, attribution or absence. "
                "Old local receipts remain historical; their limits do not automatically qualify unrelated current claims."),
            # The answer is analytical context, not a blanket citation for
            # every fact later written about this question.
            "citation_role": "context_only",
            "related_refs": _unique_refs(refs), "citation_refs": [],
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
            "citation_role": "support_for_this_claim_only",
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
    # Expose exact retained records directly. Previously a writer could only
    # select aggregate Q/C handles, even when other relevant sources existed.
    # Ownership comes from the existing selection closure, never case labels.
    from .host import selected_question_records, find_refs
    source_owners, observation_owners = {}, {}
    for question in state['questions']:
        owned, _ = selected_question_records(state, [question['id']])
        for _, reference in find_refs(owned):
            target = (source_owners if reference['kind'] == 'source' else
                      observation_owners if reference['kind'] == 'observation' else None)
            if target is not None:
                target.setdefault(reference['id'], set()).add(question['id'])
    for field, prefix, kind, owner_map, keys in (
        ('sources', 'S', 'source', source_owners,
         ('label', 'source_role', 'coverage', 'provenance', 'source_generation_group')),
        ('observations', 'O', 'observation', observation_owners,
         ('statement', 'source_ref', 'field_pointer', 'presented_view', 'interpretation_limit')),
    ):
        for index, row in enumerate(state.get(field, []), 1):
            records.append({
                'key': prefix + str(index), 'kind': kind, 'id': row['id'],
                'question_ids': sorted(owner_map.get(row['id'], set())),
                **{key: deepcopy(row[key]) for key in keys if key in row},
                'citation_role': 'retained_record_not_semantic_approval',
                'citation_refs': [{'kind': kind, 'id': row['id'], 'version': row['version']}],
            })
    return records


def _hash(value):
    import hashlib
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode()).hexdigest()


def _dependency_hash(item):
    if item['kind'] == 'question':
        # Work disposition and review receipts can change without rewriting an
        # unchanged conclusion. Retain every substantive authoring input.
        explanation = item.get('reader_explanation') or {}
        return _hash({'judgment':item['current_judgment'], 'reader_explanation':{
            key:explanation[key] for key in ('title','paragraphs','counterarguments','limitations')
            if key in explanation}})
    if item['kind'] in ('gap', 'hypothesis'):
        # The owner ref is refreshed for response-only note revisions too.
        # Its identity remains binding; substantive answers are independently
        # bound by judgment_dependencies. Keep all evidence versions intact.
        item = deepcopy(item)
        item['question_ref'].pop('version', None)
    return _hash(item)


def _latest(state, ids):
    matches = [r for r in state.get('report_narratives', []) if r['question_ids'] == ids]
    return max(matches, key=lambda r: r['revision'], default=None)


def _previous_fields(case, progress, latest):
    """Recover at most one prior value per missing group from the existing ledger.

    This compose-only view never enters status, dependencies, draft hashes,
    review coverage or rendered content. Old handles must be checked against
    current material before an explicit new save; no field is auto-revalidated.
    """
    missing = GROUP_IDS - progress['fields'].keys()
    if not missing or not latest or latest.get('template_version') != TEMPLATE_VERSION:
        return {}
    result = {}

    def collect(record):
        if (record.get('schema') != 'forsic-report-narrative-2' or
                record.get('template_version') != TEMPLATE_VERSION or
                record.get('question_ids') != progress['question_ids'] or
                record.get('revision', 0) > progress['revision']):
            return
        for group in missing - result.keys():
            value = record.get('fields', {}).get(group)
            if value is not None:
                result[group] = {
                    'source_revision': record['revision'],
                    'source_draft_revision': record.get('draft_revision'),
                    'source_basis_id': record['basis_id'],
                    'template_version': record['template_version'],
                    'saved_at': value.get('saved_at', record.get('saved_at')),
                    'entries': deepcopy(value['entries']), 'complete': value.get('complete', True),
                }

    # The caller's frozen latest revision wins even if another writer appends
    # while this read is being prepared. Older rows fill only missing groups.
    collect(latest)
    if case is not None and result.keys() != missing:
        with case.connect() as db:
            for (raw,) in db.execute("SELECT data FROM events WHERE kind='report_narrative' ORDER BY rowid DESC"):
                collect(json.loads(raw))
                if result.keys() == missing:
                    break
    return result


def _current_fields(latest, identity, choices):
    if not latest or latest.get('schema') != 'forsic-report-narrative-2':
        return {}, []
    # Old finding cells have different meanings. Keep historical ledger/bundles
    # intact; request fresh editorial writing only when compose/render is used.
    if latest.get('template_version') != TEMPLATE_VERSION:
        return {}, list(latest.get('fields', {}))
    fields, stale = {}, []
    for group, value in latest.get('fields', {}).items():
        # Reuse only exact dependency matches. In particular, short key Q1 may
        # never silently change owner after selection/order/version changes.
        dependencies = value.get('dependencies', {})
        valid = bool(dependencies) and all(key in choices and _dependency_hash(choices[key]) == digest
                                          for key, digest in dependencies.items())
        questions = {row['id']: row['current_judgment'] for row in choices.values()
                     if row['kind'] == 'question'}
        judgment_dependencies = value.get('judgment_dependencies')
        if judgment_dependencies is None:
            # Historical entries did not bind direct source selections to the
            # current answer. Preserve them only at their exact old basis.
            valid = False
        else:
            valid = valid and all(qid in questions and _hash(questions[qid]) == digest
                for qid, digest in judgment_dependencies.items())
        if latest['basis_id'] == identity or valid:
            fields[group] = deepcopy(value)
        else:
            stale.append(group)
    return fields, stale


def _draft_hash(fields):
    return _hash({key: {'entries': value['entries'], 'complete': value.get('complete', True)}
                  for key, value in fields.items()})


def status(state, ids=None):
    ids = sorted(ids if ids is not None else [q['id'] for q in state['questions']])
    identity, selected = basis(state, ids)
    latest = _latest(state, ids)
    items = material(selected)
    fields, stale_fields = _current_fields(latest, identity, {item['key']: item for item in items})
    used = {key for field in fields.values() for entry in field['entries'] for key in entry['basis_keys']}
    covered = {qid for item in items if item['key'] in used for qid in item['question_ids']}
    missing_gaps = [item['key'] for item in items
                    if item['kind'] == 'gap' and item.get('impact') in ('material', 'decision_critical')
                    and item['key'] not in used]
    missing = [key for key in WRITING_ORDER if
               (key not in fields and key not in OPTIONAL_GROUPS) or
               (key in fields and not fields[key].get('complete', True))]
    pending = [{'question_id': q['id'], 'revision': q['revision'],
                'read_call': {'tool': 'forsic_reporting', 'arguments': {'action': 'state', 'question_id': q['id']}}}
               for q in selected['questions'] if q.get('judgment_review_required')]
    draft_hash = _draft_hash(fields)
    review = latest.get('review') if latest else None
    review_current = bool(review and review.get('comparisons') and not missing and review.get('basis_id') == identity
                          and review.get('draft_hash') == draft_hash
                          and review.get('draft_revision') == latest.get('draft_revision'))
    review_complete = bool(review_current and authoring_policy.resolutions_complete(review)
                           and all(check['result'] == 'satisfied' for check in review['checks'])
                           and not any(f['blocking'] for f in review['findings']))
    content_complete = not pending and not missing and not missing_gaps and set(ids) <= covered
    return {
        'template_version': TEMPLATE_VERSION, 'basis_id': identity, 'question_ids': ids,
        'revision': latest['revision'] if latest else 0,
        'draft_revision': latest.get('draft_revision', 0) if latest else 0,
        'draft_hash': draft_hash, 'ready': bool(content_complete and review_complete),
        'content_complete': bool(content_complete), 'judgment_review_questions': pending,
        'stale': bool(latest and (latest['basis_id'] != identity or
                                 latest.get('template_version') != TEMPLATE_VERSION)), 'stale_fields': stale_fields,
        'missing_groups': missing, 'missing_sections': missing,
        'missing_material_gap_keys': missing_gaps,
        'unrepresented_question_ids': [qid for qid in ids if qid not in covered],
        'fields': fields, 'sections': {}, 'review': deepcopy(review) if review_current else None,
        'previous_review': deepcopy(review),
        'review_status': 'complete' if review_complete else 'needs_revision' if review_current else 'required',
        'semantic_approval': False,
    }


def review_projection(state, ids=None):
    """Labeled prose for the existing native/Ren review; never source evidence."""
    progress = status(state, ids)
    return {key: deepcopy(progress[key]) for key in
            ('template_version', 'basis_id', 'question_ids', 'draft_revision', 'draft_hash',
             'fields', 'missing_groups', 'review_status', 'review')}


def _key(case):
    """A local authentication key, not a second draft or judgment store."""
    import os
    path = case.output / '.report-authoring-key'
    if not path.exists():
        temporary = path.with_name(path.name + '.' + uuid.uuid4().hex)
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(os.urandom(32))
            try:
                os.link(temporary, path)
            except FileExistsError:
                pass
        finally:
            temporary.unlink(missing_ok=True)
    import stat
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, 'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError('Report edit authentication key must be a regular local file')
            key = stream.read(33)
    except OSError as exc:
        raise ValueError('Report edit authentication key must be a regular local file') from exc
    if len(key) != 32:
        raise ValueError('Report edit authentication key is unavailable')
    return key


def _edit_reference(case, progress, group, args):
    import base64
    import hashlib
    import hmac
    binding = [str(case.db.resolve()), case.config['case_id'], args.get('_session_id', ''),
               args.get('_goal_id', ''), progress['question_ids'], progress['basis_id'],
               progress['revision'], group, TEMPLATE_VERSION]
    tag = hmac.new(_key(case), json.dumps(binding, separators=(',', ':')).encode(), hashlib.sha256).digest()[:24]
    return 're2.' + str(progress['revision']) + '.' + base64.urlsafe_b64encode(tag).rstrip(b'=').decode()


def _packet_items(selected, offset, progress, section, limit=8):
    rows = material(selected)
    page, used = [], 0
    observations = {row['id']: row for row in selected.get('observations', [])}
    for material_index, original in enumerate(rows[offset:], offset):
        item = deepcopy(original)
        if item['kind'] == 'question':
            # Broad answers are navigation/context, not prose or evidence for
            # every field. Keep the complete current view available on demand.
            page_item = {k: deepcopy(item[k]) for k in ('key', 'kind', 'id', 'question_ids', 'citation_refs')}
            page_item.update(citation_role='context_only', body_omitted=True,
                read_call=_read_call(progress, '/material/' + str(material_index), section))
            page.append(page_item)
            used += len(json.dumps(page_item, ensure_ascii=False))
            if len(page) >= min(8, limit):
                break
            continue
        # Preserve exact quotations as separate fields with explicit omissions.
        item['evidence'] = []
        for ref in _unique_refs(item['citation_refs'] + item.get('related_refs', []))[:12]:
            entry = {'reference': deepcopy(ref), 'read_call': {'tool': 'forsic_reporting', 'arguments': {
                'action': 'source', 'source_id': ref['id'], 'source_version': ref['version']}}}
            if ref['kind'] == 'observation':
                observation = observations.get(ref['id'], {})
                source = observation.get('source_ref', {})
                entry['read_call']['arguments'].update(source_id=source.get('id'), source_version=source.get('version'),
                                                       pointer=observation.get('field_pointer', ''))
                literal = observation.get('literal')
                if literal is not None:
                    text = str(literal)
                    entry['exact_excerpt'] = text[:1600]
                    entry['omitted_characters'] = max(0, len(text) - 1600)
                    entry['coordinate_basis'] = observation.get('coordinate_basis')
            item['evidence'].append(entry)
        encoded = json.dumps(item, ensure_ascii=False)
        if page and (len(page) >= min(8, limit) or used + len(encoded) > 18000):
            break
        if len(encoded) > 9000:
            item = {key: item[key] for key in ('key', 'kind', 'id', 'question_ids', 'citation_refs')}
            # Page the same projected material, not the raw legacy note that
            # omits current claim scopes and resolved-gap reconciliation.
            item.update(body_omitted=True, original_characters=len(encoded),
                read_call=_read_call(progress, '/material/'+str(material_index), section))
        page.append(item)
        used += len(json.dumps(item, ensure_ascii=False))
    return page, len(rows)


def _review_identity(progress):
    # Review saves advance CAS without changing prose. Evidence changes or prose
    # edits replace this read view; a stale request must never read new text.
    return 'rd2.' + _hash([progress['basis_id'], progress['draft_hash']])


def _read_call(progress, pointer, section='review'):
    call = {'tool': 'forsic_reporting', 'arguments': {
        'action': 'compose', 'section': section, 'question_ids': progress['question_ids'],
        'snapshot_id': _review_identity(progress), 'pointer': pointer}}
    if _historical_pointer(pointer):
        call['arguments']['expected_revision'] = progress['revision']
    return call


def _historical_pointer(pointer):
    return any(pointer == root or pointer.startswith(root + '/')
               for root in ('/previous_review', '/previous_fields', '/previous_editable_fields'))


def _editable_entry(entry):
    keys = schema()['properties']['entries']['items']['properties']
    return {key: deepcopy(value) for key, value in entry.items() if key in keys}


def _material_navigation(item, path, progress, section):
    """Keep omitted catalog records selectable without guessing their schema."""
    result = {'basis_key': item['key'], 'kind': item['kind']}
    field = {'question': 'answer', 'claim': 'statement', 'gap': 'reason',
             'hypothesis': 'explanation', 'timeline': 'explanation',
             'source': 'label', 'observation': 'statement'}.get(item['kind'])
    if field and isinstance(item.get(field), str):
        result['content_read_call'] = _read_call(progress, path + '/' + field, section)
    return result


def _review_document(progress, selected):
    from .model_pages import child
    fields = {group: {'entries': deepcopy(field['entries']), 'complete': field.get('complete', True)}
              for group, field in progress['fields'].items()}
    index = [{'section': group, 'node_ids': [entry['node_id'] for entry in fields[group]['entries']],
              'complete': fields[group].get('complete', True),
              'read_call': _read_call(progress, child('/fields', group)),
              'edit_read_call': _read_call(progress, child('/editable_fields', group) + '/entries', group)}
             for group in WRITING_ORDER if group in fields]
    observations = {row['id']: row for row in selected.get('observations', [])}
    items = material(selected)
    for item in items:
        item['evidence'] = []
        for ref in _unique_refs(item['citation_refs'] + item.get('related_refs', [])):
            observation = observations.get(ref['id'], {}) if ref['kind'] == 'observation' else {}
            source = observation.get('source_ref', {}) if observation else ref
            item['evidence'].append({'reference': deepcopy(ref), 'read_call': {
                'tool': 'forsic_reporting', 'arguments': {'action': 'source',
                'source_id': source.get('id'), 'source_version': source.get('version'),
                **({'pointer': observation['field_pointer']} if observation.get('field_pointer') else {})}}})
    editable = {group: {**field, 'entries': [_editable_entry(entry) for entry in field['entries']]}
                for group, field in fields.items()}
    previous_editable = {group: {**field, 'entries': [_editable_entry(entry) for entry in field['entries']]}
                         for group, field in progress.get('previous_fields', {}).items()}
    forms = {group: {**field, 'entries': [authoring_form.to_form(group, entry) for entry in field['entries']]}
             for group, field in fields.items()}
    return {'index': index, 'fields': fields, 'editable_fields': editable, 'form_fields': forms, 'material': items,
            'source_cards': authoring_policy.source_cards(items, selected),
            'previous_fields': progress.get('previous_fields', {}),
            'previous_editable_fields': previous_editable,
            'previous_review': progress['previous_review']}


def _save_contract(case, progress, group, args, *, named=False):
    return {'tool': 'forsic_reporting', 'action': 'compose', 'section': group,
        'question_ids': progress['question_ids'], 'edit_ref': _edit_reference(case, progress, group, args),
        'operation_id': 'choose-a-new-operation-id-for-this-edit',
        'review' if group == 'review' else 'draft': (review_schema() if group == 'review' else
            authoring_form.form_schema(group, schema()) if named else authoring_form.canonical_schema(group, schema()))}


def _authored_entries_page(value, path, args, envelope, progress):
    """Page whole authored units, sizing their navigation inside the existing cap.

    Source/state paging retains its scalar coordinate contract. Report entries
    already bind prose, qualifiers and citations into one editorial unit; a
    fixed 3 KB child cutoff needlessly split units that fit the 6 KB response.
    """
    from .model_pages import MAX_BYTES, integer, size, descriptor
    offset = integer(args, 'offset', 0)
    if offset > len(value):
        raise ValueError('offset is beyond this collection')
    limit = integer(args, 'limit', 5, 25)
    result = {**envelope, 'pointer': path, 'offset': offset, 'partial_view': True,
              'total': len(value), 'items': [], 'next_offset': None, 'complete_value': False}
    for index in range(offset, min(len(value), offset + limit)):
        pointer = path + '/' + str(index)
        call = _read_call(progress, pointer, args.get('section', 'review'))
        item = {'pointer': pointer, 'value': deepcopy(value[index]), 'read_call': call}
        trial = {**result, 'items': result['items'] + [item], 'next_offset': index + 1}
        if size(trial) > MAX_BYTES:
            if result['items']:
                break
            item = {**descriptor(value[index], pointer), 'read_call': call}
            if size({**result, 'items': [item], 'next_offset': index + 1}) > MAX_BYTES:
                raise ValueError('Field identity exceeds the report page budget')
        result['items'].append(item)
    end = offset + len(result['items'])
    result['next_offset'] = end if end < len(value) else None
    result['complete_value'] = offset == 0 and end == len(value) and all(
        'value' in item for item in result['items'])
    if result['next_offset'] is None:
        result['next_call'] = None
    else:
        result['next_call']['arguments']['offset'] = end
    return result


def _review_page(progress, selected, args, case=None):
    from .model_pages import MAX_BYTES, descriptor, page, select, size
    if not args.get('snapshot_id'):
        raise ReportAuthoringError('snapshot_id is required; use the returned read_call unchanged',
                                   progress, args.get('section'), subtype='coordinate_missing')
    if args['snapshot_id'] != _review_identity(progress):
        raise ReportAuthoringError('Report draft or basis changed; read compose again',
                                   progress, args.get('section'), subtype='snapshot_mismatch')
    path = args['pointer']
    if _historical_pointer(path):
        if args.get('expected_revision') is None:
            raise ReportAuthoringError('expected_revision is required for historical report reads; use the returned read_call unchanged',
                                       progress, args.get('section'), subtype='coordinate_missing')
        if args['expected_revision'] != progress['revision']:
            subject = 'review' if path.startswith('/previous_review') else 'revision'
            raise ReportAuthoringError(f'Report {subject} changed; read compose again for its current historical reference',
                                       progress, args.get('section'), subtype='revision_mismatch')
    document = _review_document(progress, selected)
    if not any(path == '/' + key or path.startswith('/' + key + '/') for key in document):
        raise ReportAuthoringError('Use a returned report draft pointer', progress, args.get('section'), subtype='pointer_invalid')
    continuation = _read_call(progress, path, args.get('section', 'review'))
    # Reserve continuation/navigation bytes before model_pages budgets the body.
    continuation['arguments'].update(offset=9999999999999999999, limit=args.get('limit', 2048))
    try:
        value = select(document, path)
    except (ValueError, KeyError, TypeError) as exc:
        raise ReportAuthoringError(str(exc), progress, args.get('section'), subtype='pointer_invalid') from exc
    previous = any(path == root or path.startswith(root + '/')
                   for root in ('/previous_fields', '/previous_editable_fields'))
    envelope = {
        'snapshot_id': _review_identity(progress), 'basis_id': progress['basis_id'],
        'draft_hash': progress['draft_hash'],
        'view_kind': 'previous_report_draft' if previous else 'report_draft',
        'role': ('historical_editorial_prose_requires_revision_not_current_evidence' if previous else
                 'editorial_projection_not_evidence_or_new_judgment'),
        'next_call': continuation}
    parts = path.split('/')
    group = parts[2] if len(parts) > 2 and parts[1] in ('fields', 'editable_fields', 'form_fields') else None
    if group in progress['fields']:
        target = '/editable_fields/' + group + '/entries'
        if len(parts) >= 5 and parts[3] == 'entries' and parts[4].isdigit():
            target += '/' + parts[4]
        if parts[1] == 'fields':
            envelope['edit_read_call'] = _read_call(progress, target, group)
        elif case is not None and not progress['judgment_review_questions']:
            envelope['save_contract'] = _save_contract(case, progress, group, args, named=parts[1] == 'form_fields')
    # A read or editable node is one authoring unit. Returning its complete small
    # value avoids splitting node identity/status from prose and qualifications.
    # Larger nodes retain the same bounded, exact child/continuation paging.
    if (group in progress['fields'] and parts[1] in ('fields', 'editable_fields', 'form_fields')
            and len(parts) == 5 and parts[3] == 'entries' and isinstance(value, dict)
            and args.get('offset', 0) == 0):
        complete = {**envelope, 'pointer': path, 'offset': 0, 'partial_view': True,
                    'value': deepcopy(value), 'complete_value': True, 'next_offset': None, 'next_call': None}
        if size(complete) <= MAX_BYTES:
            return complete
    if (group in progress['fields'] and parts[1] in ('fields', 'editable_fields', 'form_fields')
            and len(parts) == 4 and parts[3] == 'entries' and isinstance(value, list)):
        return _authored_entries_page(value, path, args, envelope, progress)
    result = page(value, path, args, envelope)
    if result.get('next_offset') is not None:
        result['next_call']['arguments']['offset'] = result['next_offset']
    else:
        result['next_call'] = None
    for item in result.get('items', []):
        item['read_call'] = _read_call(progress, item['pointer'], args.get('section', 'review'))
        if path == '/material' and 'value' not in item:
            item.update(_material_navigation(select(document, item['pointer']), item['pointer'],
                                              progress, args.get('section', 'review')))
    if size(result) > MAX_BYTES and len(result.get('items', [])) > 1:
        return _review_page(progress, selected, {**args, 'limit': max(1, len(result['items']) // 2)}, case)
    if size(result) > MAX_BYTES:
        item = result['items'][0]
        result['items'][0] = {**descriptor(select(document, item['pointer']), item['pointer']),
                              'read_call': item['read_call']}
        if path == '/material':
            result['items'][0].update(_material_navigation(select(document, item['pointer']),
                item['pointer'], progress, args.get('section', 'review')))
        result['complete_value'] = False
    if size(result) > MAX_BYTES:
        raise ValueError('Field identity exceeds the report page budget')
    return result


def prepare(state, args, case=None):
    ids = selection(state, args)
    progress = status(state, ids)
    progress['previous_fields'] = _previous_fields(case, progress, _latest(state, ids))
    _, selected = basis(state, ids)
    if 'pointer' in args:
        if 'evidence_keys' in args:
            raise ReportAuthoringError('Use evidence_keys selection or a returned pointer, not both', progress, args.get('section'))
        return _review_page(progress, selected, args, case)
    group = args.get('section') or ('review' if progress['content_complete'] else
                                   next(iter(progress['missing_groups']), WRITING_ORDER[0]))
    if group not in GROUP_IDS | {'review'}:
        raise ValueError('Choose a fixed v2 template writing group; summary/findings/limits are obsolete')
    offset = args.get('offset', 0)
    if type(offset) is not int or offset < 0:
        raise ValueError('compose offset must be a nonnegative item index')
    if group != 'review' and 'offset' in args:
        # Material continuation is a read, not another full editing contract.
        # Older callers may omit the pointer/snapshot; bind that first legacy
        # read to the current snapshot and preserve it in every next_call.
        return _review_page(progress, selected, {**args, 'section': group, 'pointer': '/material',
            'snapshot_id': args.get('snapshot_id') or _review_identity(progress)}, case)
    from .model_pages import integer
    limit = integer(args, 'limit', 8, 25)
    page, total = ([], len(material(selected))) if group == 'review' else _packet_items(selected, offset, progress, group, limit)
    output = {key: deepcopy(progress[key]) for key in (
        'template_version', 'basis_id', 'question_ids', 'revision', 'draft_revision', 'draft_hash', 'stale',
        'stale_fields', 'missing_groups', 'missing_sections', 'missing_material_gap_keys',
        'unrepresented_question_ids', 'judgment_review_questions', 'review_status')}
    output.update(
        status=('judgment_review_required' if progress['judgment_review_questions'] else
                'editorial_ready' if progress['ready'] else 'document_review_required' if progress['content_complete']
                else 'authoring_required'),
        role='editorial_projection_not_evidence_or_new_judgment', section=group,
        instruction=('Compare all indexed nodes with current judgments and sources; record material comparisons and dispositions. '
                     'Check body-alone executive understanding: evidence, scope, impact, current risk and decisions relevant to this case. '
                     'Reuse settled answers; fix omissions without restarting rounds. Preserve event/time meaning. '
                     'Explicit 추정/유력 needs no direct proof: block concrete source conflicts, false citations or inference presented '
                     'as observed fact, not confidence preferences. Minor style and bounded unknowns do not block.'
                     if group == 'review' else group_contract(group)[4]),
        writing=('Fill form_template named values: text is the self-contained conclusion and plain-language meaning, detail its technical explanation, '
                 'and basis_keys support only that value. The host compiles arrays and references. '
                 + WRITING_GUIDANCE + ' Every factual entry selects relevant returned basis_keys. '
                 'S/O handles cite one retained source/observation; follow their read_call to verify support. '
                 'Q handles carry context only and attach no source citations. C handles support only their '
                 'stated claim, not unrelated facts in the answer. Select each entry independently; do not '
                 'attach the entire catalog or reuse one citation for unrelated assertions. '
                 'For cells stating different propositions, select cell_basis_keys independently. '
                 'Repeated reads and source handles are acquisition receipts, not independent evidence. '
                 'the server supplies exact references and [n] citations. Preserve observations, inferences, scope, time '
                 'assumptions, counterevidence and material uncertainty in the relevant entry. For missing material use '
                 'status=unavailable and an honest explanation. Correct changed judgments through forsic_question. '
                 'For incremental construction, use draft.mode=merge with stable node_ids and complete=false until the last chunk; '
                 'complete=true finishes the combined group without erasing earlier rows. '
                 'Use explicit mode=replace only to replace the entire group or remove rows. '
                 'For corrections to a current saved group, use draft.mode=update with only the changed existing nodes. '
                 'Read their editable_fields view; unlisted siblings and completeness are preserved. Do not reconstruct '
                 'unchanged groups or resend generated citation_refs. update cannot adopt previous_fields. '
                 'Continue in this native turn under its existing budget; no new runner or collection is required.'),
        items=page, total_items=total,
        snapshot_id=_review_identity(progress),
        saved_fields=[{'section': key, 'saved_at': field.get('saved_at'),
            'complete': field.get('complete', True),
            'node_ids': [entry['node_id'] for entry in field['entries']],
            **({'entries': deepcopy(field['entries'])} if key == group and
               len(json.dumps(field['entries'], ensure_ascii=False).encode()) < 3000 else {}),
            'read_call': _read_call(progress, '/fields/' + key + '/entries', group),
            'edit_read_call': _read_call(progress, '/editable_fields/' + key + '/entries', key),
            **({'form_read_call': _read_call(progress, '/form_fields/' + key + '/entries', key)} if key == group else {})}
            for key, field in progress['fields'].items()],
        previous_fields=[{'section': key, 'source_revision': field['source_revision'],
            'source_draft_revision': field['source_draft_revision'],
            'role': 'historical_editorial_prose_requires_revision_not_current_evidence',
            'read_call': _read_call(progress, '/previous_fields/' + key, group),
            'edit_read_call': _read_call(progress, '/previous_editable_fields/' + key + '/entries', group)}
            for key in WRITING_ORDER if (field := progress['previous_fields'].get(key)) is not None],
        previous_fields_instruction=('Use previous_fields only to compare and revise earlier prose after a basis change. '
            'Use its edit_read_call for complete authored fields without generated citation metadata; '
            'read_call retains the full historical record. '
            'It is not current evidence, approved text or valid current basis_keys. Read current material, '
            'keep supported corrections, revise affected statements and save explicitly with the current edit_ref.'),
        field_states=[{'section': key, 'nodes': list(nodes),
                       'status': 'review_needed' if key in progress['stale_fields'] or key in progress['previous_fields'] else
                       'unwritten' if key not in progress['fields'] else
                       'in_progress' if not progress['fields'][key].get('complete', True) else
                       'ready' if progress['ready'] else 'unavailable_explained' if all(
                           e['status'] == 'unavailable' for e in progress['fields'][key]['entries']) else 'saved'}
                      for key, _, nodes, _, _ in GROUPS])
    output['report_work'] = authoring_policy.work_state(progress)
    output['source_cards_call'] = _read_call(progress, '/source_cards', group)
    output['source_selection'] = ('Select up to eight current non-Q evidence_keys with this snapshot_id to read '
        'only those proposition/source/scope cards. Follow original read_call when omitted. '
        'C statements are analyst claims; G records explain scope, not physical evidence. '
        'Do not borrow a neighbouring statement\'s source. Verify text and detail separately against the selected original.')
    if 'evidence_keys' in args:
        keys = args['evidence_keys']
        if args.get('snapshot_id') != _review_identity(progress):
            raise ReportAuthoringError('Source selection requires the current compose snapshot_id', progress, group, subtype='snapshot_mismatch')
        cards = authoring_policy.source_cards(material(selected), selected)
        if (not isinstance(keys, list) or not 1 <= len(keys) <= 8 or
                any(not isinstance(k, str) or k not in cards for k in keys) or len(set(keys)) != len(keys)):
            raise ReportAuthoringError('Select one to eight distinct current non-Q evidence_keys', progress, group)
        output['items'] = []
        for key in keys:
            card = cards[key]
            row = {'key': key, 'kind': card['kind'], 'evidence_role': card['evidence_role'],
                   'read_call': _read_call(progress, '/source_cards/' + key, group)}
            if len(json.dumps(card, ensure_ascii=False).encode()) <= 2500:
                row['value'] = card
            else:
                row.update(body_omitted=True, direct_keys=card['direct_keys'])
            output['items'].append(row)
        output['total_items'] = len(keys)
        output['selected_evidence_keys'] = keys
    if group != 'review':
        contract = group_contract(group)
        output['form_template'] = authoring_form.template(group)
        output['form_labels'] = authoring_form.slot_labels(group)
        if not contract[2]:
            output['form_rows'] = ('Repeat the row template with distinct node_ids for different events or evidence. '
                                  'Keep one event/support scope per row; do not put the entire chronology or evidence catalog in row1.')
        from .template_contract import NODE_PURPOSES
        output['field_contract'] = {'node_ids': list(contract[2]), 'cell_count': contract[3],
                                    'required': group not in OPTIONAL_GROUPS,
                                    'node_purposes': {n: NODE_PURPOSES[n] for n in contract[2] if n in NODE_PURPOSES},
                                    'cell_labels': cell_labels(group),
                                    'cell_details': 'Optional parallel array of exactly cell_count strings; empty details allowed. cells contain concise qualified conclusions or metadata values; cell_details contain basis/explanation.',
                                    'cell_basis_keys': 'Optional exactly cell_count lists of returned basis_keys. Select support for each cell independently; entry-only keys carry context.',
                                    'repeated_node_prefix': group + '.' if not contract[2] else None}
        scales = {node: list(ASSESSMENT_SCALES[node]) for node in contract[2] if node in ASSESSMENT_SCALES}
        if scales:
            output['field_contract']['assessment'] = {'scales': scales, 'guidance': ASSESSMENT_GUIDANCE}
        if group == 'timeline':output['field_contract']['event_kind'] = EVENT_KINDS
        feedback = [(index, finding) for index, finding in enumerate((progress['previous_review'] or {}).get('findings', []))
                    if any(node == group or node.startswith(group + '.') for node in finding['node_ids'])]
        output['current_feedback'] = [{**{key: deepcopy(finding[key]) for key in
                                         ('finding_id', 'node_ids', 'disposition', 'blocking')},
                                      'read_call': _read_call(progress, '/previous_review/findings/' + str(index), group)}
                                     for index, finding in feedback[:5]]
        output['omitted_feedback_count'] = max(0, len(feedback) - 5)
        if feedback:
            output['feedback_read_call'] = _read_call(progress, '/previous_review/findings', group)
    else:
        output['reviewed_node_ids'] = [entry['node_id'] for field in progress['fields'].values() for entry in field['entries']]
        output['review_index_call'] = _read_call(progress, '/index')
        output['material_read_call'] = _read_call(progress, '/material')
        output['coverage_contract'] = ('saved_fields indexes every current node. Read each group once, following '
                                       'child/next calls only where omitted. Reuse unchanged pages with this snapshot_id; '
                                       'omitted material rows retain basis_key/kind and a content_read_call to their '
                                       'actual text field. Use those calls rather than guessing a shared field name. '
                                       'material and source reads are for relevant support and limits, not a new collection.')
        output['previous_review'] = ({'read_call': _read_call(progress, '/previous_review')}
                                     if progress['previous_review'] else None)
        output['review_checks'] = list(REVIEW_CHECKS)
        # The review schema and referenced procedure already describe writing;
        # do not repeat the long authoring instructions in this bounded index.
        output.pop('writing', None)
        output.pop('source_selection', None)
        # saved_fields and missing/stale lists already carry these node states.
        output.pop('field_states', None)
        if not output['previous_fields']:
            output.pop('previous_fields_instruction', None)
        output['answerability_contract'] = {
            'node_ids': list(authoring_policy.RESOLUTION_NODES),
            'instruction': 'Record retained reads, supported inference and useful authorized checks. '
                'needs_investigation prevents ready; limited needs an actual constraint and reopening check. '
                'Unread data is not missing data. Follow completion_and_answerability.md.',
            'research_read_call': {'tool': 'forsic_reporting', 'arguments': {'action': 'state',
                'question_ids': ids, 'snapshot_id': state['meta']['snapshot_id'], 'pointer': '/report_research'}}}
        if args.get('_independent_review_options'):
            output['independent_review_call'] = {'tool': 'forsic_reporting', 'arguments': {'action': 'review_inbox'}}
        output['review_role'] = 'Current native agent records the whole-document review; configured Ren advice uses its existing native review path and dispositions.'
        if case is not None and progress['content_complete'] and not progress['judgment_review_questions']:
            from .independent_gate import input_preflight
            measured = input_preflight(case, state, args, progress)
            if measured is not None:
                output['review_input_preflight'] = measured
    if case is not None:
        output['save_contract'] = _save_contract(case, progress, group, args, named=True)
    if group != 'review' and 'evidence_keys' not in args and offset + len(page) < total:
        output['next_call'] = _read_call(progress, '/material', group)
        output['next_call']['arguments'].update(offset=offset + len(page), limit=limit)
    if progress['judgment_review_questions']:
        output['next_call'] = progress['judgment_review_questions'][0]['read_call']
        output['instruction'] = 'Read the current question and explicitly reassess its answer/assessment through forsic_question before report writing. Prose edits cannot clear an invalidated judgment.'
        output.pop('save_contract', None)
    return output


def save(case, state, args):
    import hmac
    from .host import current
    from .investigation_state import input_problems, mutation_replay, record_mutation
    operation = args.get('operation_id')
    if not isinstance(operation, str) or not operation.strip() or len(operation) > 128:
        raise ValueError('compose save requires a nonempty operation_id')
    group = args.get('section')
    is_review = 'review' in args
    if group not in GROUP_IDS | {'review'} or (group == 'review') != is_review or ('draft' in args) == is_review:
        raise ReportAuthoringError('Compose requires one fixed v2 template group with draft, or section=review with review', status(state, selection(state, args)), group)
    value_schema = review_schema() if is_review else schema()
    payload = args['review' if is_review else 'draft']
    normalizations = []
    if not is_review:
        try:
            payload, normalizations = authoring_form.normalize(group, payload)
        except ValueError as exc:
            raise ReportAuthoringError(str(exc), status(state, selection(state, args)), group,
                subtype='authoring_fields_invalid', problems=[{'path': 'draft.entries', 'requirement': str(exc)}]) from exc
    problems = input_problems(payload, value_schema, 'review' if is_review else 'draft')
    if problems:
        raise ReportAuthoringError('Invalid template writing fields', status(state, selection(state, args)),
                                   group, subtype='authoring_fields_invalid', problems=problems)
    write_mode = payload.get('mode') if not is_review else None
    entry_update = write_mode == 'update'
    entry_merge = write_mode == 'merge'
    requested_complete = payload.get('complete') if entry_update or entry_merge else None
    if not is_review:
        try:
            payload = validate_group(group, {**deepcopy(payload), 'complete': False}
                                     if entry_update or entry_merge else deepcopy(payload))
            payload.pop('mode', None)
        except ValueError as exc:
            raise ReportAuthoringError(str(exc), status(state, selection(state, args)), group,
                subtype='authoring_fields_invalid', problems=[{'path': 'draft.entries', 'requirement': str(exc)}]) from exc
    ids = selection(state, args)
    with case.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        replay = mutation_replay(db, operation, args)
        if replay is not None:
            return replay
        now = current(case)
        if ids != selection(now, args):
            raise ValueError('Report scope changed; read compose again')
        progress = status(now, ids)
        if progress['judgment_review_questions']:
            raise ValueError('Current judgment requires review; read compose and reassess the indicated question')
        expected = _edit_reference(case, progress, group, args)
        if not args.get('edit_ref'):
            raise ReportAuthoringError('edit_ref is required; read the scoped compose save_contract',
                                       progress, group, subtype='coordinate_missing')
        if not isinstance(args.get('edit_ref'), str) or not hmac.compare_digest(args['edit_ref'], expected):
            raise ReportAuthoringError('Report edit reference is stale, forged, or belongs to another case/session/scope; read compose again', progress, group, subtype='edit_reference_mismatch')
        if ('basis_id' in args and args['basis_id'] != progress['basis_id']) or (
            'expected_revision' in args and args['expected_revision'] != progress['revision']):
            raise ReportAuthoringError('Explicit basis/revision conflicts with the current edit reference', progress, group, subtype='revision_mismatch')
        fields = deepcopy(progress['fields'])
        review = deepcopy((_latest(now, ids) or {}).get('review'))
        saved_at = time.time()
        if is_review:
            if not progress['content_complete']:
                raise ValueError('Complete every required template field, selected question and material gap before whole-document review')
            nodes = {entry['node_id'] for field in fields.values() for entry in field['entries']}
            if set(payload['reviewed_node_ids']) != nodes:
                raise ValueError('Whole-document review must cover every current template node')
            if len(payload['checks']) != len(REVIEW_CHECKS) or {check['check'] for check in payload['checks']} != set(REVIEW_CHECKS):
                raise ValueError('Whole-document review must include every review check exactly once')
            if any(not value.strip() for value in [payload['summary'],
                    *(c['reason'] for c in payload['checks']), *(f['reason'] for f in payload['findings'])]):
                raise ValueError('Whole-document review requires nonblank reasons')
            # A reviewer may introduce relevant counterevidence from the same
            # selected scope even when the draft failed to cite it.
            review_choices = {row['key']: row for row in material(basis(now, ids)[1])}
            allowed_keys = set(review_choices)
            try:
                authoring_policy.validate_resolutions(payload['answerability_checks'], review_choices)
            except ValueError as exc:
                raise ReportAuthoringError(str(exc), progress, group,
                    problems=[{'path': 'review.answerability_checks', 'requirement': str(exc)}]) from exc
            for comparison in payload['comparisons']:
                if not set(comparison['node_ids']) <= nodes:
                    raise ValueError('Review comparison selects an unknown current template node')
                if not set(comparison['basis_keys']) <= allowed_keys:
                    raise ValueError('Review comparison must select current compose basis_keys')
                if any(not comparison[key].strip() for key in ('statement', 'source_support', 'limits', 'assessment')):
                    raise ValueError('Review requires explicit nonblank statement/source support/limits/assessment comparisons')
            if len({f['finding_id'] for f in payload['findings']}) != len(payload['findings']):
                raise ValueError('Review finding IDs must be unique')
            for finding in payload['findings']:
                if not set(finding['node_ids']) <= nodes:
                    raise ValueError('Review finding selects an unknown current template node')
            review = {**deepcopy(payload), 'basis_id': progress['basis_id'],
                      'draft_revision': progress['draft_revision'], 'draft_hash': progress['draft_hash'],
                      'reviewer': 'native_agent', 'saved_at': saved_at}
            draft_revision = progress['draft_revision']
        else:
            existing = fields.get(group)
            existing_ids = [entry['node_id'] for entry in (existing or {}).get('entries', [])]
            incoming_ids = {entry['node_id'] for entry in payload['entries']}
            removed_node_ids = [] if entry_update or entry_merge else [node for node in existing_ids if node not in incoming_ids]
            if write_mode is None and removed_node_ids:
                raise ReportAuthoringError('Implicit replacement would discard saved rows; choose merge to preserve them '
                    'or explicit replace for intentional removal', progress, group,
                    subtype='authoring_fields_invalid', problems=[{'path': 'draft.mode',
                        'removed_node_ids': removed_node_ids,
                        'requirement': 'Use mode=merge for incremental chunks; mode=replace intentionally removes these rows.'}])
            if entry_merge:
                if existing is None and (group in progress['stale_fields'] or
                        group in _previous_fields(case, progress, _latest(now, ids))):
                    raise ReportAuthoringError('Merge cannot revive stale or historical prose; read current material '
                                               'and explicitly replace the group', progress, group)
                updates = {entry['node_id']: entry for entry in payload['entries']}
                combined = [updates.get(entry['node_id'], _editable_entry(entry))
                            for entry in (existing or {}).get('entries', [])]
                combined += [entry for entry in payload['entries'] if entry['node_id'] not in existing_ids]
                payload = validate_group(group, {'entries': combined, 'complete': requested_complete
                    if requested_complete is not None else (existing or {}).get('complete', True)})
            if entry_update:
                if existing is None:
                    raise ReportAuthoringError('Entry update requires a current valid saved group; '
                                               'historical or stale prose needs explicit replacement', progress, group)
                if requested_complete is not None:
                    if requested_complete != existing.get('complete', True):
                        raise ReportAuthoringError('Entry update preserves completeness; omit complete or use replacement', progress, group)
                    normalizations.append({'code': 'unchanged_completeness_omitted', 'field': 'complete'})
                updates = {entry['node_id']: entry for entry in payload['entries']}
                if not set(updates) <= {entry['node_id'] for entry in existing['entries']}:
                    raise ReportAuthoringError('Entry update accepts existing node IDs only; '
                                               'use merge to add rows or explicit replacement to remove rows', progress, group)
                payload = validate_group(group, {
                    'entries': [updates.get(entry['node_id'], _editable_entry(entry))
                                for entry in existing['entries']],
                    'complete': existing.get('complete', True)})
            _, selected = basis(now, ids)
            choices = {row['key']: row for row in material(selected)}
            dependencies = {}
            judgment_dependencies = {}
            from .current_judgment import authoring_judgment
            questions = {q['id']: q for q in selected['questions']}
            for entry in payload['entries']:
                if any(key not in choices for key in entry['basis_keys']):
                    raise ValueError('Use current compose basis_keys; no foreign or invented reference')
                entry['citation_refs'] = _unique_refs(ref for key in entry['basis_keys'] for ref in choices[key]['citation_refs'])
                if 'cell_basis_keys' in entry:
                    if len(entry['cell_basis_keys']) != len(entry['cells']):
                        raise ValueError('cell_basis_keys must contain exactly one list per cell')
                    if any(key not in entry['basis_keys'] for keys in entry['cell_basis_keys'] for key in keys):
                        raise ValueError('Every cell_basis_keys selection must belong to this entry basis_keys')
                    entry['cell_citation_refs'] = [_unique_refs(ref for key in keys
                        for ref in choices[key]['citation_refs']) for keys in entry['cell_basis_keys']]
                try:
                    authoring_policy.validate_support(entry, choices)
                except ValueError as exc:
                    raise ReportAuthoringError(str(exc), progress, group,
                        problems=[{'path': 'draft.entries.' + entry['node_id'], 'requirement': str(exc)}]) from exc
                dependencies.update({key: _dependency_hash(choices[key]) for key in entry['basis_keys']})
                # Exact header metadata retains its direct source dependency.
                # Semantic conclusions additionally depend on today's answer.
                for qid in ({qid for key in entry['basis_keys'] for qid in choices[key]['question_ids']}
                            if group != 'report_identity' else set()):
                    judgment_dependencies[qid] = _hash(authoring_judgment(selected, questions[qid]))
            fields[group] = {**payload, 'dependencies': dependencies,
                             'judgment_dependencies': judgment_dependencies, 'saved_at': saved_at}
            draft_revision = progress['draft_revision'] + 1
        value = {
            'schema': 'forsic-report-narrative-2', 'template_version': TEMPLATE_VERSION,
            'question_ids': ids, 'basis_id': progress['basis_id'], 'revision': progress['revision'] + 1,
            'draft_revision': draft_revision, 'draft_hash': _draft_hash(fields), 'fields': fields, 'review': review,
            'session_id': args.get('_session_id', ''), 'goal_id': args.get('_goal_id', ''),
            'saved_at': saved_at, 'semantic_approval': False,
        }
        if normalizations:
            value['input_normalizations'] = normalizations
        latest = _latest(now, ids) or {}
        same_context = all(latest.get(key) == value[key] for key in ('basis_id', 'session_id', 'goal_id'))
        unchanged = same_context and (all((progress.get('review') or {}).get(k) == v for k, v in payload.items())
            if is_review else _draft_hash(fields) == progress['draft_hash'])
        event_id = None
        if unchanged:
            # A fresh operation ID is not a content change. Preserve the exact
            # draft/review version instead of invalidating a successful review.
            after = progress
        else:
            event_id = uuid.uuid4().hex
            db.execute('INSERT INTO events VALUES (?,?,?,?,?)',
                       (event_id, saved_at, 'report_narrative', args.get('_session_id', ''), json.dumps(value, ensure_ascii=False)))
            candidate = deepcopy(now)
            candidate['report_narratives'] = [*now.get('report_narratives', []), value]
            after = status(candidate, ids)
        result = {key: after[key] for key in (
            'basis_id', 'question_ids', 'revision', 'draft_revision', 'draft_hash', 'ready', 'missing_groups', 'missing_sections',
            'missing_material_gap_keys', 'unrepresented_question_ids', 'review_status', 'semantic_approval')}
        result.update(saved=True, event_id=event_id, section=group,
            unchanged=unchanged, report_work=authoring_policy.work_state(after),
            next_call={'tool': 'forsic_reporting', 'arguments': {
                'action': 'render' if after['ready'] else 'compose', 'question_ids': ids}})
        if result['report_work']['investigation_required']:
            result['next_call'] = {'tool': 'forsic_reporting', 'arguments': {'action': 'state',
                'question_ids': ids}}
        if normalizations:
            result['input_normalizations'] = normalizations
        result['snapshot_id'] = _review_identity(after)
        if group != 'review':
            result['saved_node_ids'] = [entry['node_id'] for entry in fields[group]['entries']]
            result['removed_node_ids'] = removed_node_ids
            result['saved_field_read_call'] = _read_call(after, '/fields/' + group, group)
            result['saved_field_edit_read_call'] = _read_call(after, '/editable_fields/' + group + '/entries', group)
            result['saved_form_read_call'] = _read_call(after, '/form_fields/' + group + '/entries', group)
            if entry_update or entry_merge:
                result['updated_node_ids'] = list(updates)
        result['review_index_call'] = _read_call(after, '/index')
        result['edit_refs'] = {section: _edit_reference(case, after, section, args)
                               for section in (*WRITING_ORDER, 'review')}
        result['edit_guidance'] = ('These references replace all earlier write references. Use an already read group '
                                   'contract with its fresh reference, or follow next_call for the next contract. '
                                   'Read changed fields before reviewing; unchanged draft pages remain reusable '
                                   'only while snapshot_id matches. No evidence recollection is needed for prose edits.')
        record_mutation(db, operation, args, result)
    return result


def invoke(case, state, args):
    if 'draft' in args or 'review' in args:
        return save(case, state, args)
    packet = prepare(state, args, case)
    if args.get('basis_id') is not None and args['basis_id'] != packet['basis_id']:
        raise ValueError('Report judgments changed; restart compose at the current basis')
    return packet


def project_body(state):
    """Only current, completely reviewed fixed fields enter the ready path."""
    progress = status(state)
    if not progress['ready']:
        return None
    from .reader_projection import project_authored
    return project_authored(state, progress['fields'])


def progress_view(state, ids=None):
    """Bounded, read-only status for the workbench and native compaction."""
    if state is None:
        return {'template_version': TEMPLATE_VERSION, 'question_ids': [], 'revision': None,
                'draft_revision': None, 'ready': False, 'current': None, 'reviewed': False,
                'review_status': 'unavailable', 'status': 'unavailable',
                'groups': [{'id': key, 'label': label, 'state': 'unavailable', 'saved_at': None,
                            'preview': [], 'omitted_entries': 0} for key, label, _, _, _ in GROUPS]}
    if ids is None and state.get('report_narratives'):
        latest = max(state['report_narratives'], key=lambda value: value.get('saved_at', 0))
        ids = latest['question_ids']
    progress = status(state, ids)
    latest = _latest(state, progress['question_ids']) or {}
    groups = []
    for key, label, _, _, _ in GROUPS:
        field = progress['fields'].get(key)
        previous = latest.get('fields', {}).get(key, {})
        entries = (field or previous).get('entries', [])
        unavailable = bool(field and all(entry['status'] == 'unavailable' for entry in entries))
        phase = ('recheck' if key in progress['stale_fields'] else 'missing' if not field else
                 'in_progress' if not field.get('complete', True) else
                 'ready' if progress['ready'] else 'unavailable' if unavailable else 'saved')
        previews = []
        for entry in entries[:5]:
            text = ' · '.join(entry['cells'])
            previews.append({'node_id': entry['node_id'], 'text': text[:300],
                             'omitted_characters': max(0, len(text) - 300),
                             'status': entry['status'],
                             'unavailable_reason': entry.get('unavailable_reason', '')[:240],
                             'omitted_reason_characters': max(0, len(entry.get('unavailable_reason', '')) - 240)})
        groups.append({'id': key, 'label': label, 'state': phase,
                       'saved_at': (field or previous).get('saved_at'), 'preview': previews,
                       'omitted_entries': max(0, len(entries) - 5)})
    return {'template_version': TEMPLATE_VERSION, 'question_ids': progress['question_ids'],
            'revision': progress['revision'], 'draft_revision': progress['draft_revision'],
            'ready': progress['ready'], 'current': not progress['stale'],
            'reviewed': progress['review_status'] == 'complete', 'review_status': progress['review_status'],
            'status': 'ready' if progress['ready'] else 'recheck' if progress['stale'] else
                      'review' if progress['content_complete'] else 'drafting',
            'groups': groups}
