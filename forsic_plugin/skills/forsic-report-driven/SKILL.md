---
name: forsic-report-driven
description: Compose a readable Korean HTML report from shared investigation explanations and exact evidence. Export Word from the same saved version when requested.
---
# Current answers and the integrated report

Investigate through direct reads, searches, and evidence selection. Save answers and corrections through
answer-question's existing forsic_question. Question or mission registration before each read, retrospective
missions, and fabricated assessments are unnecessary. The program manages references, numbers, timestamps,
and execution receipts; the agent judges meaning, importance, discriminating value, and scope closure.
Keep user-facing answers and report text in Korean.

The initial user question may remain provisional while investigation proceeds. Save meaningful findings
and changed limits in that same owned question before reporting; neither final certainty nor a fixed
hypothesis count is required. Reads and report views do not create analytical conclusions.

## Lookup and seven assessment dimensions

Read current owned questions and gap_lookup in forsic_reporting(action=state/gaps). Select details using the
returned snapshot_id and pointer/next_call. Questions owned by another conversation are read-only; do not
adopt past answers as evidence for a new case. Read preserved originals through action=source query/pointer/offset
and use actual returned C1/C2 citation_ref or the read's presented_spans. body_omitted means the summary
omits the body. Retrieve existing results before repeating successful collection.

Consider compromise time (compromise_time), entry route (entry_route), attacker actions (attacker_actions),
personal-data possession (personal_data_presence), external exposure or transfer (external_disclosure), decisive
evidence (decisive_evidence), and incident timeline (incident_timeline) through natural prose, assessment tables,
and relevant chronology. Select the dimensions that matter to this question; do not fill empty tables. Select
confirmed/inferred/not_confirmed/unavailable/not_applicable from actual scope and evidence. An undeclared
state is not evidence of absence. In optional question report_slots, save slot/state/brief/claim_indices and any
needed basis/limitations. claim_indices are zero-based positions of that question's adopted assessments;
the program binds references. If changed answers, definitions, or selected evidence cause needs_recheck,
read the current question and original sources and correct the declaration.

## Important gaps during reporting

Select state/gaps' report_research_call(pointer=/report_research) for the read-only review list. Read actual
questions, gaps, sources, and missions using needed items/sub-pointers and next_call. Compare important
contradictions and unconfirmed links in current answers against actual gaps, rather than collecting merely
to fill missing dimensions. For a worthwhile next discriminating check, correct the same owned question
with current expected_revision or create a necessary new question through forsic_question. Choose why it
matters, its scope, and what result would change the conclusion. Investigate with existing authorized native
tools, then correct the same answer, counterarguments, and limitations. Use forsic_mission only to preserve
conditions for an optional advance check; ordinary exploration and saving answers do not require it.

Reuse preserved results for completed work with the same question definition, scope, exact inputs, selected
material versions, and discriminating conditions. An unrelated revision, new operation_id, or new mission ID
does not justify repetition. Query existing work for running or completion-unknown operations. Failure,
partial coverage, and no match establish neither successful examination of all material nor absence.
Follow existing native error and retry contracts; do not retry automatically. If relevant new evidence or
conditions change, select only changed checks. Report review does not extend native call or time budgets.
Once authorized checks with discriminating value are exhausted, preserve the reason and reopening conditions
and close with an unconfirmed answer if appropriate. Do not require 7/7 confirmation, a separate reviewer,
or a new scheduler. Report generation or lookup does not clear a manual stop.

## Generation and delivery

During investigation, save meaningful changes with the optional `reader_explanation` of the existing
`forsic_question` writer. Its short connected prose, current alternatives and limits are shared by the
right workbench and the report appendix. This is an evolving explanation, not a second judgment.
If facts change, update the owning answer first. The host marks an outdated explanation for rewriting;
do not keep showing it as current. Do not rewrite it after every tool call.

Before producing a reader report, use `forsic_reporting(action=compose)`. The returned packet supplies
current shared explanations, judgments, gaps and short `basis_keys`. Read `next_call` or omitted details
when material. Write fresh Korean paragraphs across the selected questions for `summary`, `findings`
and `limits`: strongest supported answer; evidence and event sequence; important uncertainty and
worthwhile remaining checks. Choose natural titles. Explain how facts connect rather than copying
whole answers or slot labels. Preserve contrary evidence, inferred times and significant uncertainty
in the relevant sentences. Avoid repeating the same finding across sections. Do not invent an event,
number, actor or successful execution to make the prose flow.

Save one bounded section using `compose` with `section`, `basis_id`, `expected_revision`, `operation_id`
and `draft:{title,paragraphs:[{text,basis_keys}]}`. The host supplies exact citations and `[1]` footnotes.
These are ordinary native turns on the selected model, not a separate writer loop. Additional calls
are allowed within the current native budget. Read the saved sections and critique consistency with
their evidence before rendering; exact references alone are not semantic approval. Factual corrections
go through `forsic_question`; prose edits change only the derived section. When the basis changes,
refresh compose and rewrite the affected report. Reuse successful evidence reads.

`forsic_reporting(action=render)` saves integrated.html and its exact reader/template inputs.
Word is generated only when the user requests a download of that saved version; it reuses those
inputs without a model call or new investigation. Do not generate Word through the terminal.
Missing or stale prose returns a writing packet before creating a reader report. An explicit
`allow_partial:true` produces a labeled technical progress snapshot when a reader body is not ready;
do not describe this as completed report writing. A failed or interrupted model call leaves saved
sections recoverable without rerunning evidence collection.
By default, question scope is the current conversation and goal; question_ids can specify it explicitly.
Aim for about 3 body pages, keeping conclusion-changing uncertainty in the relevant sentence. Put detailed
limitations, full history, corrections, originals, execution arguments, receipts, hashes, missions, and permissions
in the same report's appendix. Do not create separate executive and practitioner editions. Report examples
and templates are format references, not case evidence. Preserve the user's existing optional executive
question mode of 3 rounds × 5 questions; report review must not automatically generate a fourth round.

Compare core evidence for current answers through review-conclusions and check the returned snapshot,
actual paths, and errors. Correct ledger answers before regenerating a new bundle for new conclusions.
Preserve old files. Partial reports with unconfirmed findings are allowed; file creation does not prove the
investigation is complete. evidence_export is a selected-evidence bundle, not a full raw-image copy.
Saved report bundles are immutable versions. Shared explanations update the live workbench; reopening
a saved version shows that version's cutoff and differences from current judgments, not rewritten text.
Do not create it for redacted reports; it is not automatically delivered by finalize.
Pass render's bundle_id to finalize. If content changes, follow returned recovery and deliver only within
existing approved mirror and recipient scope. Optional model review is advisory; this skill extends neither
model nor transfer permissions.

Read detailed contracts only when needed with forsic_skill(name="forsic-report-driven", file_path=...).
- references/report_contract.md: integrated outputs, freshness, partial reports
- references/audiences.md: executive body and appendix in the same document
- references/tool_contracts.md: existing tools and lookup/save boundaries
- references/mission_policy.md: optional advance checks and reuse
- references/style_and_wording.md: wording and presentation
- references/evidence-sharing.md: evidence numbers, coordinates, receipts, hashes

For a material content/scale gap use scan-sensitive-data on selected retained targets. Safe adopted findings use existing slots; bounded original excerpts appear only in the local integrated appendix, with omissions and local-only delivery. See [scan-sensitive-data](../scan-sensitive-data/SKILL.md).
