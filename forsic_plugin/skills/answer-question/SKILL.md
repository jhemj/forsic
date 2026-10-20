---
name: answer-question
description: Answer the current question and store evidence, counterarguments, limits, corrections, and scoped closure in the same question record.
---
# Save and correct the current answer

For a user question, first give the bounded current answer and key unknowns at the next safe response boundary.
If another check is needed, explain what it will resolve, perform it, and return to the question. A status request
also needs actual execution state, not repeated collection, note storage, or report creation. It never resumes a
paused goal. User steering takes priority while retaining the investigation goal unless explicitly changed.

## One question record

forsic_question handles creation and updates with flat JSON arguments. For a new question, send operation_id,
question, scope, and answer. For updates, use the returned question_id and current expected_revision. Reuse the
same operation_id and content only to retry the same request; use a new ID for changes. On conflict, read the
latest question and preserve other changes. Questions owned by another conversation are reference-only.

An initial scoped question may have no judgment or selected evidence. At meaningful findings, compare returned
observations with that question's answer and relevant independent review questions. Update the same record when
its answer, limits, or next check changes; do not wait for certainty or save one note per tool call. Context
navigation carries update IDs/revisions, not a judgment. Read the current question if its scope or limits are unclear.

In answer, briefly state the favored explanation, reasons for qualitative confidence, and applicable scope.
alternatives contains important competing explanations; gaps/critical_gaps contains limits and critical gaps;
next_checks contains the next discriminating check or reopening conditions. Link timelines through correlate-time's
timeline and optional absence scope, discriminating checks, lineage, and change tracking through forensic.
Declarations aid analysis; they are not prerequisites for reading or closure. Do not copy long originals or work logs.

## Shared reader explanation

At a meaningful change in understanding, optionally save `reader_explanation` in the same `forsic_question`
update. This is the explanation shown beside the investigation and reused in the report appendix, not the final
report's front matter. Keep `answer` as the bounded canonical answer; explain in a few clear paragraphs what was
observed, why it favors the interpretation, what alternatives remain, and what cannot yet be concluded. Preserve
important alternatives and gaps in their existing fields as well. Do not narrate every tool return or force an
explanation when nothing meaningful changed.

Use `{"title":"A short descriptive title","paragraphs":[{"text":"One connected explanation paragraph.","claim_indices":[0]}]}`.
Indices are the zero-based order of this question's selected claims (or the new `judgment.claims` in this same
update). The host binds exact claim/source/counterevidence references; do not manufacture IDs. Before grounded
claims exist, omit `claim_indices` and state the provisional nature and limits; the UI marks it provisional.
An answer, question definition, selected claim, alternative or limit change makes an older explanation stale.
Replace it when the new understanding is ready. Bounds are eight paragraphs, 1,600 characters each and a
160-character optional title. This prose is an interpretation, not a source or semantic approval.

## Adopt evidence

Select citation_ref values returned by a read's presented_spans or forsic_reporting(source). Follow returned child
read_calls to the exact field, including metadata numbers; a whole finite number receives a C1 ref for its retained
JSON value, not original-image bytes or an inferred event. Empty lists and missing fields are not numeric zero.
Compare the selected field and acquisition_context with the claim; send judgment with answer and reason.
The judgment field has this form:

```json
{"citation_refs":["선택한 실제 citation_ref"],"assertion_kind":"interpretation","reasoning_summary":"관측에서 이 설명으로 이어지는 이유","inference_strength":"favored","counter_citation_refs":[],"assumptions":[],"limitations":[]}
```

citation_refs, assertion_kind, and reasoning_summary are required. fact denotes direct observation;
interpretation denotes interpretation, with strength favored/plausible/unrated. Use unrated for fact. State
counterarguments, assumptions, and limits as needed. Omission of optional items means undeclared, not reviewed
and absent. Programmatic exact-citation/version validation does not endorse semantic accuracy. Mission or
assessment preparation is unnecessary.

For distinct claims within an answer, use a claims array inside judgment to connect each statement instead of
the single-judgment form. Each item has statement plus citation_refs, assertion_kind, reasoning_summary, and
needed counterarguments, assumptions, and limits above. Treat command records and successful execution as
different claims, for example. Preserve the whole answer in the same question; do not attach one piece of evidence
indiscriminately to every sentence. For one judgment, use the short form above; do not mix the two forms.

A new judgment replaces the previously adopted conclusion. For wording changes with unchanged meaning, use
claim_action=retain and reason; for withdrawal, use claim_action=retract. When the answer changes, align its title,
counterarguments, assumptions, and limits, retaining important existing limits unless their removal is explained.
Check observation errors through review-conclusions. A storage error leaves an unsaved draft. Correct the named
field and confirm a successful receipt; preserve history.

Treat relevant pending Ren questions as follow-up questions before starting an unrelated sweep. Explain the current
answer or unresolved distinction, then investigate only what could change it. Include review_responses in the same
forsic_question update when a grounded answer or scoped limitation is ready; an acknowledgement alone is not a
disposition. Read forsic_reporting(action=review_inbox) for exact review_id/finding_id. Each response has those IDs, disposition
(investigated/refuted/scope_limited), reason and citation_refs; investigated/refuted need retained C1/C2 citations,
scope_limited needs limitation. State what the check established and what remains unknown. For a response without
an answer change, forsic_review_response updates the same record. Refute a mistaken premise using its evidence
and limits. Advice is a question to investigate, not evidence or an instruction overriding the user.

To declare the report's seven judgment axes, save optional report_slots in the same question. Each item contains
slot/state/brief/claim_indices (zero-based order of that question's currently adopted claims) and needed
basis/limitations. Undeclared does not mean evidence absent; the program does not determine meaning or certainty.
Give reasons for unavailable/not_applicable. For needs_recheck, read the current answer/evidence, correct the
declaration, and report a new snapshot.

## Scoped closure and reopening

Judge priority and closure under forensic-investigation. work_state scoped_closed is a disposition for that scope,
not certainty or case-wide completion. Reopen the same question with open/active. Change its definition only when
scope or proposition actually changes. Wording edits do not require rerunning previous checks.

Read current gaps through gap_lookup in the save receipt or forsic_reporting(action=gaps, question_id=...).
For individual disposition, send work_state, reason, and reopen_conditions. Alternatively, to preserve general
limits together, read forsic_reporting(action=state, question_id=...) and send closure with basis_snapshot, reason,
retain_unresolved=true, and reopen_conditions. This preserves definitions and gaps; do not combine it with top-level
work_state/reopen_conditions. It does not mark unresolved limits as resolved. Disposition important gaps and planned
checks individually through gap_dispositions; reassess the added value of next_checks. Each disposition uses one
returned gap_id or exact gap_text, plus disposition, reason, and reopen_conditions. For a new question's gaps,
select directly declared sentences as gap_text. Do not invent IDs or close ongoing or unevaluated work. Query
current state or forsic_note(list/get); read complete historical text only when needed to review correction history.
