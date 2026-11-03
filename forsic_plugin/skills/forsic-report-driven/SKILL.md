---
name: forsic-report-driven
description: Compose a readable Korean HTML report from shared investigation explanations and exact evidence. Export Word from the same saved version when requested.
---
# Current answers and the integrated report

Investigate through direct reads, searches, and evidence selection. Save answers and corrections through
answer-question's existing forsic_question. Question or mission registration before each read, retrospective
missions, and fabricated assessments are unnecessary. The program manages references, numbers, timestamps,
and execution receipts; the agent judges meaning, importance, discriminating value, and scope closure.
Keep user-facing answers and report text in clear Korean: short qualified headlines and complete formal
nominal sentences that explain meaning to a nontechnical reader. Exact quotations remain unchanged.
Use references/style_and_wording.md for writing and filename labels, and references/audiences.md for body/appendix roles.

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

The latest user task governs this stage; a later request to investigate a missing area updates the scope.
Do not keep treating an old report-only instruction as a permanent ban on that newly requested investigation.
For genuinely report-only work, re-read retained results and revise prose, references,
and limits; reader review does not authorize new extraction, scanning, or original-evidence searches. Preserve useful
out-of-scope checks as explicit limitations and reopening conditions through the same review-response writer.
Enabled independent executive review retains its existing three rounds of five substantive answers.
Continue their saved dialogue across an authorized handoff; a new execution allowance or wording-only edit
does not justify restarting completed rounds. Follow the returned current-owner recovery call if needed.
If recovered history contains an accidentally restarted review, keep its original receipt and answer its
pending findings through the returned inbox. Historical restart advice does not add a policy round or
certify the corrected report; the host decides the validated sequence and current review requirement.

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
and close with an unconfirmed answer if appropriate. Do not require 7/7 confirmation, another reviewer beyond enabled native review,
or a new scheduler. Report generation or lookup does not clear a manual stop.

## Major IOC section before final reporting

Fill key_indicators for a concise major IOC section in the body; retain exact provenance, lookup results and matching limitations in the practitioner/audit appendix. Use organize-indicators for exact values, types,
local sources/evidence links and deduplication, then threat-intelligence to cross-check every supported,
authorized selected IOC against GTI existing reports. Preserve query time, actual result/status, evidence link
and limitations per IOC, including not-found, failed, disabled, unsupported or not-permitted rows. No new
external-transmission permission or operator setting change follows from this requirement. If checks cannot
complete because of an actual external or budget constraint, disclose that reason and remaining verification; GTI success is not a prerequisite for an honest report.

Before compose/render, read forsic_skill(name="forsic-report-driven", file_path="references/report_contract.md")
for the existing summary/limitations/source_refs data path and selected-source inclusion rule. Verify the
rendered IOC appendix and its links. A saved local IOC or successful render alone does not prove GTI coverage.
If no IOCs were identified in the selected examined scope, state that bounded result and its limits rather than
inventing rows or claiming the whole evidence population contains none.

## Budget closing near report generation

The investigation runs on the existing 24-hour native active-goal budget; elapsed time
survives turns, compaction and restarts, and an explicit manual pause freezes it.
At 12 and 18 cumulative active hours, the main model summarizes findings, remaining
gaps and the value of the next investigation, then chooses finish or continue with
a reason. The existing native goal records the decision against the actual saved
assistant response row. A pending or unknown decision remains awaiting review; it
never authorizes continuation or counts as a completed checkpoint. Finish earlier
when the work is complete. Manual pause, guardrails, goal replacement and the hard
cap take precedence. Reviews use the existing native turn path without a new loop,
timer or DB and never reset elapsed time; the per-turn iteration limit is separate.
A saved finish decision enters `runtime_phase=closing` (receipt: “마무리 선택”): keep
the goal active to save the owning question and use the existing compose/review/render
tools. Do not immediately pause with a tool-less summary. A 12-hour finish suppresses
the later 18-hour decision; normal closing and 95% partial handoff keep their gates.

At 90% consumed, stop broad new discovery and save the meaningful current answer,
evidence, counterarguments and limits through the owning `forsic_question`, then
proceed with the existing compose/review/render path; useful bounded completion
checks remain allowed. At 95%, hand off partial results through the existing native
final-response path and let automatic goal continuation pause. Keep 100% as the
last-resort hard cap for stuck processing; never mark the goal DONE on budget alone.
Budget pressure never weakens finalize/review/citation gates: if generation fails
or is cut short, disclose the saved draft or partial snapshot as such;
`allow_partial:true` remains a labeled technical progress snapshot, not a completed
reader report. A pending question never blocks closing; unanswered stays unknown.

## Generation and delivery

During investigation, save meaningful changes with the optional `reader_explanation` of the existing
`forsic_question` writer. Its short connected prose, current alternatives and limits are shared by the
right workbench and the report appendix. This is an evolving explanation, not a second judgment.
If facts change, update the owning answer first. The host marks an outdated explanation for rewriting;
do not keep showing it as current. Do not rewrite it after every tool call.
When later results correct an earlier failure, scope or interpretation, use
`references/current_judgment_review.md` to reconcile the current answer, selected claims and gap dispositions
before writing. A successful extraction, completed search and negative finding are different outcomes.
Read the returned current judgment projection; old review prose and execution failures remain history,
not competing current answers. An unresolved material conflict keeps the affected interpretation qualified.

Before producing a reader report, read `forsic_reporting(action=compose)`. Follow its current
writing groups and returned node IDs. Read `references/completion_and_answerability.md` for source-scoped
writing, answerability review and bounded completion. Draft findings, information impact, key evidence,
timeline, undated events and scope first; write synthesis, event overview and overall judgment last.
The output keeps the template order: headline, connected synthesis and five findings; information impact and IOC;
chronology, followed by the practitioner/audit appendix. Synthesis explains the meaning and favored account in
the body; only work details and full technical derivation move to the appendix. Follow `references/audiences.md`.
Do not replace the template with three generic essays or paste whole answers
into table cells. A group is one meaningful writing task, not a separate call for every sentence.

Use `report_identity` for only hostname, representative IP and image acquisition time. Bind supported
values to returned evidence keys; keep user-provided identity labeled. Keep the exact available value
and any required masking; each header cell is a single-line value only. Place source/provenance notes,
qualification reasons and exact selected support in the corresponding `detail` / `cell_details` in the
practitioner appendix; they do not appear below the value in the header. For example, use
`hostname.text: host.example`, `representative_ip.text: 192.0.2.1`, and
`acquisition_time.text: YYYY-MM-DD` only when that completion date is supported; put the completion-record qualification in `detail`. Folder labels are not verified
host identity, and report/analysis timestamps or file mtime/ctime are not image acquisition time. Retain
image identity and time meaning/timezone when multiple images differ. Unknown values stay explicit.
Do not add case-management IDs, preparation/document status or system-analysis cutoff to this header;
technical provenance and version history remain available in the appendix and bundle.

In `major_findings`, the first cell is a short time or finding summary with its qualification. For the
timing and initial-entry rows, include the supported time: an exact timestamp, an expression such as
`YYYY.MM.DD 이전 (추정)`, or a justified range. Initial entry reads 시점 → 경로 → 판단 근거; a missing route
does not erase a supported temporal estimate. If no bound is defensible, explicitly write 진입 시점 미확인
and keep its concrete reason in detail. Never invent a day, year, bound or timezone to fit the pattern.
For the remaining rows use a concise substantive finding
with 추정/유력/확정 or the unresolved scope, not a forced date. The second cell combines the core finding
and judgment in one or two complete nominal sentences explaining what happened and why it matters to a nontechnical reader. Put technical `- 근거:` and `- 한계:` explanation in
the optional parallel `cell_details` value; keep confidence and conclusion-changing uncertainty in `cells`.
Do not duplicate full sentences between these cells or paste the full note answer into either one.

Not confirmed does not mean no useful answer. If current evidence supports a leading estimate, state
that estimate first, then its basis/assumptions and the specific unresolved part. Use bare 미확인 only
when no defensible estimate exists, with the missing discriminator explained. A useful uncertain answer
is allowed; no fixed confidence score, forced certainty or extra collection just to fill a field.

Keep the executive body focused on conclusions, impact, major findings, IOC and chronology.
Keep `response_actions` complete in the practitioner appendix, outside the main report body.
Put useful investigation-basis/context material in optional `executive_context` in the practitioner appendix;
never add filler to complete that optional group. Decision-critical explanations belong in the existing body
findings/actions. Follow `references/audiences.md`.
Keep a useful strong inference explicitly labeled 추정/유력; the acceptance rules are in
`references/final_body_evidence_review.md`. A label never permits invented source facts.

When the current contract offers `assessment`, select the node-specific five-level scale from the current judgment,
with a short `rationale`; level=null selects the separate neutral 평가 보류 cell, especially for unexamined
personal-data holdings. The six visible cells are this state plus levels 1..5, not six ordinal risk levels.
For timing/entry, `time_range` can represent a two-sided range or a one-sided bound. For `relation: "before"` or
`"by"` supply start=null and the supported end; for `"after"` or `"from"` supply end=null and the supported start.
Declare the `subject` being dated and `qualifier` (관측/추정/유력/확정). `by` includes the boundary (늦어도), whereas
`before` excludes it (이전). Use actual precision; no fabricated midnight, year, seconds or beginning date. If no
time bound is supported, omit time_range and state the narrower supported activity and unresolved link in cells.
Every time_range includes subject, qualifier and reasoning with selected basis_keys, timestamp_role,
premise, alternative, year_basis and timezone_basis. A source timestamp does not by itself date a different event.
Save materially new time inferences in the current owning question first. Bind the report's concise conclusion,
gauge, time boundary and rationale to those same selected claims and original evidence; do not score by keywords.
Use event_kind for timeline categories so response, acquisition and analysis are not shown as attack events.
After filling timeline rows, read the whole page as a continuous account and write `timeline_summary`.
Each row's `interpretation` explains what the recorded action means and why it matters in this case;
do not substitute recovered-line counts, unexplained tool names or a list of things not established.
Explain successful authentication, privileged commands and program execution in ordinary language.
Connect the same supported account/object/operation across rows; treat unconnected observations as
parallel evidence, not an invented causal sequence. The page summary joins the resulting strands in
two or three complete nominal sentences. Then fix only unclear subjects, transitions and significance
in those rows. Detailed clock assumptions, alternatives and limits remain in `detail`. Include this
whole-page reading in the existing `reader_order` check and one Ren copyedit pass; no extra loop.

Write fresh, concise Korean cells from the bounded relevant packet. Select returned `basis_keys`
for each entry's actual supporting material. Q question keys are context-only; they do not authorize
citations to the whole question's evidence. Use C for the selected claim's declared support scope,
or S/O for an exact source/observation after following its retained source read call. Read what the
chosen record establishes and its limits; never select the full catalog as blanket support for every
entry. The program supplies exact references and [1] footnotes. Fill the returned `form_template`:
`draft:{entries:[{node_id,values:{<named_slot>:{text,detail,basis_keys}}}]}`. Copy slot names from
`form_labels`, never invent them. `text` is the self-contained qualified answer and its meaning, or a concise header value; optional
`detail` is its basis, limitation and useful next action. Each slot selects its own support keys.
Entry-level `basis_keys`, if needed, carry context. The host supplies cell order/count, empty optional
details, exact citations, typography and numbering; do not write HTML/CSS or hand-numbered citations.
The renderer resolves one role scale in `report_style.json` for screen, print and Word.
Titles/headings use 28/22/18px; connected prose, including long overall judgments, uses
16px regular weight with about 1.55 line spacing. Secondary explanations use 14px,
citations 12px; reserve 600 weight for concise labels. Header values use regular body weight. Timeline titles
use 16px/600 and time labels 14px regular. Appendix prose stays readable and exact
source text keeps 14px monospace, whitespace, links and original coordinates.
Print uses the corresponding 10.5pt body and 9.5pt detail roles. The page target
never authorizes smaller fonts, missing evidence or changed meaning. Formatting-only
changes preserve authored content, evidence selection and the current review; they
require layout checks, not another factual review round.
Legacy `cells` input remains supported, but prefer the named form for new writing. For repeatable
key evidence and timeline groups, copy the row template for distinct evidence/events, with separate
node_ids and support; do not squeeze unrelated observations into one long row.

For a current saved group, use its `form_read_call` to read editable named slots. Correct only affected
existing nodes with `draft.mode=update`, each changed node complete; omit `complete`. Unlisted siblings
and completeness remain unchanged. Build a group in chunks with `draft.mode=merge`, stable `node_id`s,
and `complete:false`. The last chunk uses `complete:true` to complete the combined group; earlier rows
and their citations remain. Compare the returned `saved_node_ids` with the intended list. Use explicit
`mode=replace` only when replacing the entire group or intentionally removing rows. A materially changed judgment must
first be reconciled through the current question owner, then its affected report fields and final review.
Do not restart completed unrelated groups or the previous independent review rounds.

The save receipt records harmless normalization (empty optional details, duplicate support keys or
unambiguous node spelling). These are not reasons to rewrite accepted content. For a rejection, repair
the named field using the returned recovery contract; do not retry the same rejected payload unchanged.
Minor wording preferences alone do not block completion; substantive meaning, evidence, required scope
and readable layout do. Keep unresolved factual scope honest instead of forcing certainty or collecting
more evidence merely to fill the form. First read retained material, assess a supported inference and perform
useful authorized local checks. Unread acquisition records and unexamined accessible data locations are
not yet reasons to declare the answer unavailable. Use `status:unavailable` with `unavailable_reason` when appropriate;
never submit blank required slots. Preserve contrary evidence and conclusion-changing uncertainty beside
the finding. Related process or cron citations do not establish C2 strings or resolver observations.
Metadata values do not themselves establish installation/deletion time; log count and endpoints do
not establish uninterrupted retention. Useful qualified estimates remain allowed.

Before final report completion, Ren must compare the **current main-body claims with their linked retained evidence**, using the existing native independent final-review path. See `references/final_body_evidence_review.md`. A prior judgment review or a review of an older draft does not cover a changed body. This process uses the existing review inbox, current-owner dispositions and finalization gate; it does not create another runner or restart executive rounds.

After all groups are saved, read `compose(section=review)`. Its `saved_fields` is a complete node index,
not the document body. Follow group `read_call`s to inspect complete authored entries in bounded batches; child and
`next_call` pages return exact text and limits under the same draft/basis-bound `snapshot_id`. Use
`material_read_call` for selected judgments/limitations and their exact retained source calls. Large
omitted catalog rows retain `basis_key` and `kind`; follow `content_read_call` for their actual text
field instead of assuming that claims, gaps and hypotheses share one field name. Reuse
already read pages while that identity matches; a complete entry needs no separate read of its title,
cells, assessment or citations. Do not request the whole draft on every evidence page.
Compare the assembled document against its evidence and original scopes. Record the returned review contract, covering every node and check:
entailment, coverage, alternatives, uncertainty, consistency, repetition, and reader order. In the same
review check clear Korean, integrated findings without duplication, and unsupported
dates or estimates hidden behind a generic 미확인. Read references/style_and_wording.md when needed. Use existing
Ren advice and save its investigated/refuted/scope_limited dispositions through the current question.
Acknowledge a challenge only after actual follow-up, justified disagreement, or explicit scope limitation.
For material assertions you choose, record concise `comparisons` with actual `node_ids`, selected
`basis_keys`, the `statement`, what the source actually supports (`source_support`), its `limits`, and
your `assessment`. Related nodes may share a comparison; all-node review does not require one proof or
call per sentence. Missing/unverified scopes may use empty basis_keys and an explicit absence/limit.
Compare configuration or requested action with observed execution and outcome; authentication with
authorization; an instantaneous process observation with a duration claim; and metadata timestamps
with the claimed event time where relevant. These are scope distinctions for your analysis, not automatic
verdicts. Useful qualified inferences remain allowed. For each editorial finding, revise affected fields or record a reasoned retained/unconfirmed disposition.
Blocking unresolved issues keep the report a draft. Exact references alone are not semantic approval.
Do not invent a separate model caller or another review loop. Use existing native budgets; after bounded
focused revisions, preserve remaining issues rather than repeatedly rewriting the whole report.

Each save checkpoints the native case ledger. Resume remaining/stale groups; do not repeat successful
collection for prose edits. Factual corrections go through the owning `forsic_question`, then refresh
compose. The program retains only fields whose exact selected dependencies still match, and invalidates
the whole-document review after prose or judgment changes. Save receipts return fresh `edit_refs` for
every group and review; they replace all older CAS references. Use a known group contract with its fresh
reference, or the returned next_call. After a prose edit inspect its changed fields and refresh the index;
do not repeat source reads whose exact references/support remain applicable. Do not reconstruct basis hashes or silently
substitute current versions. An ambiguous call outcome requires checking the saved revision before retry.
After compression use `reports.continuation` when present: its exact snapshot, current review stage,
delivered-node navigation and next call resume the existing work. Delivered text is not a semantic
review approval; retain actual comparisons and pending dispositions. A completed document review
continues to render/finalize, without reopening unchanged nodes or restarting executive rounds.
The trusted `reports.followup_task` phase takes precedence over older authoring navigation.
`deliver_selected_version` means deliver that existing selection; do not restart composition.

`forsic_reporting(action=render)` saves integrated.html and its exact reader/template inputs.
Word is generated only when the user requests a download of that saved version; it reuses those
inputs without a model call or new investigation. Do not generate Word through the terminal.
Missing or stale prose returns a writing packet before creating a reader report. An explicit
`allow_partial:true` produces a labeled technical progress snapshot when a reader body is not ready;
do not describe this as completed report writing. A failed or interrupted model call leaves saved
sections recoverable without rerunning evidence collection.
By default, question scope is the current conversation and goal; question_ids can specify it explicitly.
Aim for about 3 body pages with concise conclusions, impact, timeline and major IOC. Keep a brief conclusion-changing qualifier in the relevant sentence; put response actions and detailed limitations in the practitioner appendix. Web reading always includes the complete body, practitioner appendix and audit records. The controls select print scope only; default print scope is the complete report.
Keep full history, corrections, execution arguments, receipts, internal IDs and review dialogue in the
audit/reproduction appendix of the same immutable report. Preserve every original and material limitation; the
page target is a warning, not permission to truncate evidence. Do not create incompatible executive and
practitioner conclusions. Report examples
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
existing approved mirror and recipient scope. Ren advice is not a truth certificate; the author must dispose of its findings. Follow `references/final_body_evidence_review.md` for the current-version review and stop conditions. This skill extends neither model nor transfer permissions.
`independent_review_rerender_required` with a current final review means the saved artifact must include
that receipt. Follow its returned render call, then finalize the returned bundle. It does not request a new
body review or prose rewrite. The bounded finalization response supplies the next action; use its
review-inbox lookup for history, not a shell command to access host-side spillover files.

Read detailed contracts only when needed with forsic_skill(name="forsic-report-driven", file_path=...).
- references/report_contract.md: integrated outputs, freshness, partial reports
- references/audiences.md: executive body and appendix in the same document
- references/tool_contracts.md: existing tools and lookup/save boundaries
- references/mission_policy.md: optional advance checks and reuse
- references/style_and_wording.md: wording, role-first filenames and presentation
- references/final_body_evidence_review.md: Ren body/evidence and unfinished-scope review, inference acceptance and stop conditions
- references/current_judgment_review.md: reconcile later results, current limits, temporal interpretations and direct support
- references/completion_and_answerability.md: answerable scope, scoped source cards, time premises, review and stop conditions
- references/evidence-sharing.md: evidence numbers, coordinates, receipts, hashes

For a material content/scale gap use scan-sensitive-data on selected retained targets. Safe adopted findings use existing slots; bounded original excerpts appear only in the local integrated appendix, with omissions and local-only delivery. See [scan-sensitive-data](../scan-sensitive-data/SKILL.md).
