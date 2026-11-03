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

forsic_question handles creation and updates with structured JSON arguments. When invoked through tool_call,
use a calls array and an arguments object, not a JSON-encoded string containing the entire request.
For a new question, send operation_id,
question, scope, and answer. For updates, use the returned question_id and current expected_revision. Reuse the
same operation_id and content only to retry the same request; use a new ID for changes. On conflict, read the
latest question and preserve other changes. Questions owned by another conversation are reference-only.

An initial scoped question may have no judgment or selected evidence. Before announcing a meaningful finding
or moving to an unrelated inquiry, compare returned observations with that question's answer and relevant
independent review questions. Commit changed answer, limits and next_checks through the same record and
verify the receipt before the public explanation. A bounded provisional finding is ready to save; do not wait
for the final report or certainty. For incremental claims, keep unchanged current claims using exact
retain_claim_ref and author only new/corrected claims with their returned citations. The full answer remains
your coherent current explanation. The runtime judgment_checkpoint identifies public explanations newer
than saved judgments: reconcile material changes, not tool counts or progress-only messages. Do not save one
note per tool call or rewrite unchanged judgments to clear a reminder. Context
navigation carries update IDs/revisions, not a judgment. Read the current question if its scope or limits are unclear.
The returned `saved_judgment` receipt states the actual committed claim count, selected IDs, answer fingerprint
and remaining review gate. Confirm it before saying that claims were saved or retained; a reason saying
"claims retained" does not select them. Use retained result read calls after interruption instead of collecting again.
Keep execution errors, report revision/render/finalize progress and retry plans out of `answer` and analytical
`next_checks`. Those describe the incident. Existing `work_state`/`reason` and report/tool receipts describe work
progress. A work-state update does not save a new interpretation or clear pending meaningful findings.
After a meaningful success or correction, reconcile previously declared gaps and selected claim limits as part
of the same update. Keep failed attempts as history; do not leave the old failure as the current answer when a
different authorized path succeeded. Acquisition, interpretation and search coverage remain separate. Use the
current gap IDs/dispositions and existing judgment fields; no note or reconciliation ritual is needed per read.

In answer, briefly state the favored explanation, reasons for qualitative confidence, and applicable scope.
alternatives contains important competing explanations; gaps/critical_gaps contains limits and critical gaps;
next_checks contains the next discriminating check or reopening conditions. Link timelines through correlate-time's
timeline and optional absence scope, discriminating checks, lineage, and change tracking through forensic.
Declarations aid analysis; they are not prerequisites for reading or closure. Do not copy long originals or work logs.

An investigation question is the analyst's stored answer; a Ren question requests analytical review; native clarify
asks the human for unavailable context or material. Do not substitute one for another. For an important missing
user-only input, follow forensic-investigation: ask what would change the answer/next check, or preserve a concrete
reason to defer and reopening condition in the existing gaps/next_checks and relevant deferred_reason/work_state.
A pending clarify is not an answer: continue independent work and retain only the dependent unknown.
After an unanswered or timed-out clarify, retain the unknown. A later human answer updates context/assumptions and
may guide a check; it is not independent event evidence or a citation proving that an event happened.

## Shared reader explanation

At a meaningful change in understanding, save a short `reader_explanation` in the same `forsic_question`
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
Copy returned read_call/next_call with all selectors. If a handle leaves context but source_id and source_version
remain, use that exact pair; never reconstruct an opaque handle or C1/C2 citation. A page offset belongs to its
returned pointer: after selecting another field, follow that field's calls. Recover missing selections through the
current source index before reacquiring an original. A newer source version does not strengthen the same observation.
For a state snapshot error, use its exact recovery and new child calls; snapshots and revisions are not interchangeable.
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

To correct selected claims in an existing v2 question, read its current_judgment and copy exact current
claim_refs into unchanged entries: {"retain_claim_ref":{"kind":"claim","id":"...","version":"..."}}.
Send judgment.claims as the complete final ordered array: retained entries plus fully authored corrected
claims with their own exact citation_refs. Include the current question_id, expected_revision, operation_id,
answer and reason. Omitted old claims are superseded. A retained entry cannot contain authored fields;
it preserves the exact adopted claim and its declared uncertainty. Retention requires unchanged proposition
and scope, current ownership and valid exact source ancestry. Do not reconstruct untouched citations or use
observation IDs as source IDs. claim_action=retain remains a whole-judgment operation without judgment.
After correction, existing report declarations and independent review may require recheck; explicitly
update only affected report rows after checking their current basis. Retention is not semantic approval.

A new judgment replaces the previously adopted conclusion. For wording changes with unchanged meaning, use
claim_action=retain and reason; for withdrawal, use claim_action=retract. When the answer changes, align its title,
counterarguments, assumptions, and limits, retaining important existing limits unless their removal is explained.
Check observation errors through review-conclusions. A storage error leaves an unsaved draft. Correct the named
field and confirm a successful receipt; preserve history. A material contradiction in a returned original (for
example, a recovered older log invalidating a saved absence) changes the same question's answer or gap now.
Keep the rejected draft until its repair commits; further investigation does not save it. `reason` explains the
question correction; `judgment.reasoning_summary` explains the inference. `reasoning_note` is unsupported.
`evidence_ids` contains source event IDs, while C1/C2 strings belong in `citation_refs`; never shorten a citation
into an evidence ID to bypass an error. Follow exact recovery read calls and repair the original selections.

Treat relevant pending Ren questions as follow-up questions before starting an unrelated sweep. Explain the current
answer or unresolved distinction, then investigate only what could change it. If the current answer changes, save
answer, judgment and related review_responses together in forsic_question. If it remains unchanged, save each
ready response individually with forsic_review_response; do not rewrite the answer to dispose of a finding.
An acknowledgement alone is not a disposition. Read forsic_reporting(action=review_inbox) for exact review_id/finding_id. Each response has those IDs, disposition
(investigated/refuted/scope_limited), reason and citation_refs; investigated/refuted need retained C1/C2 citations,
scope_limited needs limitation. State what the check established and what remains unknown. Copy sufficient
already returned citation_refs; another evidence call is unnecessary solely to repair a save. Send operation_id,
question_id and current expected_revision with the individual response as top-level fields; use empty limitation
for investigated/refuted when none applies. After each successful save, reread review_inbox for the next current
revision and remaining finding IDs. The save receipt confirms only that response committed. Refute a mistaken premise using its evidence
and limits. Advice is a question to investigate, not evidence or an instruction overriding the user.
Save each materially ready response with the current question revision before moving to unrelated collection;
other findings can remain pending. A bounded limitation is a valid disposition when a further useful check is
unavailable, but do not label an available, unperformed check as impossible. Verify the saved receipt and current
inbox before repeating work for a finding already handled.

To declare the report's seven judgment axes, save optional report_slots in the same question. Each item contains
slot/state/brief/claim_indices (zero-based order of that question's currently adopted claims) and needed
basis/limitations. Undeclared does not mean evidence absent; the program does not determine meaning or certainty.
Give reasons for unavailable/not_applicable. For an existing v2 question, save ready independent corrections with
report_slot_updates using the same row fields; omit unchanged rows. This upserts only named slots and preserves
untouched declarations with their old references and judgment basis. Do not combine it with report_slots, which
replaces the complete declaration array ([] clears it). New questions use report_slots. For needs_recheck, read
the relevant current answer/evidence, correct that declaration, and report a new snapshot. Do not reread complete
catalogs or reconstruct all seven rows before saving a ready independent correction. If the answer or claims
change, save mutually dependent changes atomically; untouched rows remain subject to needs_recheck.

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
returned gap_id or exact gap_text, plus disposition, reason, and reopen_conditions. While open/active, save
ready independent gap_dispositions incrementally; do not wait for all gaps to be ready. For a new question's gaps,
select directly declared sentences as gap_text. Do not invent IDs or close ongoing or unevaluated work. Query
current state or forsic_note(list/get); read complete historical text only when needed to review correction history.
