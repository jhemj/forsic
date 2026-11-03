# Shared investigation explanations and authored reports

The question note remains the owner of the answer, evidence adoption, alternatives and limits.
An optional `reader_explanation` is written in the same revision-checked transaction. Its program-built
binding identifies the current answer and selected claims. The sidebar and report appendix consume the
same read-only projection. Changed answers, claims or material context make an old explanation stale;
the original note answer stays available. Merely viewing a pane never calls a model.

The report body follows the retained v2 template with the `forsic-integrated-v2-reader4` writing contract. `forsic_reporting(action=compose)` returns
one bounded writing group, current saved entries, short evidence keys, exact observation excerpts
and source lookup calls. The writing groups are `report_identity`, `overall_judgment`, `major_findings`, `event_overview`,
`information_impact`, `key_evidence`, `key_indicators`, `response_actions`, `synthesis`, `timeline`, `undated_events` and `scope`.
`template_contract.py` fixes their order, node IDs, cell counts and purposes for both HTML and Word.
The default writing order starts with detail and writes the overall judgment and event overview last.
A group can contain several meaningful rows; it does not require a model call for each cell.

The current native agent writes fresh concise Korean prose into each group's `draft.entries`.
Each entry supplies `node_id`, `cells`, `basis_keys` and `status` (`saved` or `unavailable`).
Optional `cell_details` contains the same number of plain strings as `cells`; empty detail strings
are allowed. Values and short qualified conclusions belong in `cells`; supporting explanations belong
in `cell_details`. The additive field keeps the existing writing contract and historical drafts usable.
The host validates shape, hashes both roles for CAS/review freshness, and preserves them through
projection, masking, citations and retained Word inputs. It never splits prose or selects conclusions.
Authors do not choose fonts, colors, layout or evidence numbers: the shared style profile supplies
14 pt overall conclusions, 11 pt cell leads, and 10 pt gray (#5F6368) details. Confidence and
conclusion-changing uncertainty remain in the lead. Header values remain concise; provenance
and exact identity citations appear beside their supported values. No additional style model or review loop is added.
Five major-finding rows cover timing, entry, activity, personal data and external effect; each has
a compact time-or-finding summary and a qualified judgment cell. Timing permits supported
dates, bounds and ranges with the agent's 추정/유력/확정 qualifier; other axes do not require a date.
The header is authored from retained support as hostname, representative IP and image acquisition time.
Report generation timestamps, folder names and file metadata never supply automatic acquisition or
host-identity fallbacks. `field_contract.cell_labels` makes this input order explicit.
Three impact rows distinguish presence, exposure and actual transfer.
Key-evidence rows distinguish observed material, directly proved facts and unproved scope;
timeline rows distinguish time, recorded event and interpretation. The entry's selected evidence
is resolved by the host and numbered as `[1]` in the rendered report. No new source citation,
source version or observation coordinate is inferred from prose. Existing exact source validation
remains authoritative. Shared `reader_explanation` text belongs in the appendix; the body is a new
synthesis over current question-owned judgments.

A new explicit human report follow-up may reuse the exact question selection of its previous
saved report through the native accepted-message receipt. That selection is read-only authority;
it never grants permission to rewrite questions owned by an earlier goal. The first compose call
can reuse the authenticated selection without requiring the model to rebuild its IDs. A missing
current draft must lead back to composition, not restart the existing executive question rounds.
Historical answers remain visible, while final freshness still requires review of the new body.

The bounded authoring catalog exposes individual retained sources (`S`) and observations (`O`)
with exact read calls, not just aggregate answers. Each row independently selects its relevant
records; the host never attaches all question evidence to a sentence. Question (`Q`) handles
preserve context and freshness but issue no factual citation. Claim (`C`) support applies to that
claim only. Counterevidence, source roles, exact versions and read-scope limitations remain visible.
This fixes reference selection, not semantic entailment: the author and configured Ren review still
compare each material assertion with the selected record and its limits.

The same note-owned judgment projection supplies live sidebar cards and frozen report cards.
Stale reader explanations cannot replace the current answer. HTML presents authored chronology
and major findings as ordered cards; no date is parsed out of prose. Only explicitly comparable,
observed structured instants enter a numeric time axis; estimates, unknown time and stale temporal
context retain their separate labels and assumptions. A report links to one canonical answer rather
than repeating it. Retained template regions use the versioned shared conclusion/detail profile for HTML and Word;
the template package and unchanged parts are preserved. Exact original appendix material remains
expandable on screen and available in the saved document.

The writing packet and report skill request concise qualified leads and connected Korean explanations. A supported
leading estimate must remain visible when final certainty is unavailable; bare 미확인 is reserved for
the parts without a defensible estimate. The existing semantic review covers these distinctions,
duplication, reading order and time-bound support. The host does not infer certainty from keywords,
enforce noun endings with a regex, or run a separate style model. Exact original quotations remain intact.
An older writing contract is stale for new compose/render requests, so old cell meanings cannot silently
be relabeled. This is read-only invalidation of editorial readiness; ledger events, evidence, questions
and immutable bundles are preserved, with no automatic rewrite or investigation restart.

Use `status:unavailable` with `unavailable_reason` to explain an unknown field honestly. The host
checks nonblank presence, allowed cells and evidence identities, not whether a sentence asserts a
preferred conclusion. Unknown entry route, unexamined personal data or unconfirmed transfer may
be fully written with the actual examined scope and constraints after checking answerability.
An omitted field or an empty placeholder is unfinished. Set `draft.complete:false`
to checkpoint valid partial entries of a group; the saved nodes and current unfinished group return
after interruption or native compaction. Complete the remaining nodes and save the group again.

For each save, copy `section`, `question_ids` and the short `edit_ref` from `save_contract`, add a
fresh `operation_id` and the draft. The host authenticates the edit reference against this case,
conversation/goal, exact selected material, template group and editorial CAS revision. A private
32-byte local `.report-authoring-key` authenticates these references; it stores no prose, judgments
or workflow state. Long basis hashes, source versions and revisions stay server-managed.
Identical operation retries return the existing receipt. A new operation with identical canonical
content in the same basis/conversation preserves the draft revision and current review, recording
only the operation receipt. Changed-payload reuse, forged/cross-case
references and stale concurrent edits fail without changing the draft. Explicit old basis/revision
fields cannot override the reference. New writes reject the obsolete summary/findings/limits path.
Existing immutable report bundles remain readable and downloadable.

Drafts, unfinished fields and whole-document review are persisted as existing `report_narrative`
case-ledger events. Editorial writes have a separate draft revision and never update the answer,
source records, selected claims or investigation scope. Dependency hashes permit reuse only when
an entry's exact selected material still matches; changed material invalidates affected fields.
Any changed basis or prose invalidates the whole-document review. Earlier review findings remain
available as current correction feedback, with their original basis and draft binding.

After all fields, selected questions and material gaps are represented, `compose(section=review)`
returns the assembled saved document and current evidence packet. The native agent compares
coverage, evidence support, scope/uncertainty, alternatives, consistency, duplication and reading
order. It records reasons and concern dispositions (`corrected`, reasoned `retained`, or
`unconfirmed`), including whether each issue blocks publication. Review binds to the exact basis,
draft revision and draft hash; missing node review, unresolved checks or blocking concerns prevent
ready status. Configured Ren advice uses the existing native independent-review path and existing
followthrough dispositions; no additional runner, model client, scheduler or judgment database is
introduced. A structural ready state is still not an independent certificate of sentence entailment:
`semantic_approval` remains false and the agent owns analytical judgment.

`render` requests the current authoring/review packet until this contract is ready. A caller may
explicitly request `allow_partial:true` for a technical progress snapshot. Correct invalidated
question judgments through `forsic_question` before editorial writing; prose cannot reassess a
question silently. Native model choice, stop/resume, cancellation, call/time budgets and token
accounting apply throughout. Reaching a budget limit preserves a draft and its feedback rather than
launching another writer or repeating successful collection. The workbench shows the same bounded
field progress, save times, unavailable explanations and current or stale draft previews.

HTML is the default saved report. It retains the final reader view, template bytes and style profile
as private, hash-bound inputs. **Word download** explicitly converts that selected version; opening
the reports pane or finalizing delivery never creates Word. The conversion does not read current
judgments, repeat evidence collection or call a model. Technical tables have one canonical location;
other sections link to it. Source strings and coordinates remain exact. Evidence exports, masking,
local-only sensitive appendices and delivery authorization retain their existing contracts.

Word is cached separately from the immutable HTML bundle. Concurrent downloads generate it once,
failed conversion publishes no partial file, and subsequent downloads verify and reuse the cached
bytes. A supported renderer contract permits later compatible code updates while retaining the
original content/template/style and recording both renderer hashes. Existing Word reports remain
downloadable. Native mirroring delivers only the documents that were originally saved; requesting
Word does not implicitly transmit it.

Report bundles and their verified snapshots remain immutable. The reports pane shows saved versions
by time, their current/stale/unknown relationship to the investigation, and changes to question
answers, evidence or limits. The current shared investigation summary remains inspectable without
regenerating a report. This version list is derived from existing bundles and notes, not a new history store.

Validation separates deterministic contracts, configured-model writing, visual rendering, and
operational deployment. A synthetic model example demonstrates only that example, not general
forensic correctness or a deployment to an active case.

Independent-review packets keep question revisions separate from retained source versions, including
adopted evidence outside the recent-result window. Long shell fields expose separate exact head and
tail spans with their original UTF-8 offsets and explicit omissions. These spans must not be joined
into a continuous quotation. Structured triage fields expose selected-input coverage and candidate
counts; a completed profile is not system-wide coverage or a benign verdict.

The existing observation projection separately labels `derived_observations`: bound triage receipts
and retained export declarations provide navigation without changing original body-read states.
Partial inputs and omitted entries remain visible. This is a read-only view of the existing ledger,
not a second coverage checklist or a completion gate. The agent chooses which unread leads matter,
updates its question, and narrows or reopens its judgment as needed.

For long captured terminal output, returned `readable_output_calls` use
`forsic_reporting(action="source", representation="shell_utf8")`. Decoded stream byte offsets are
separate from retained base64-scalar and original-evidence offsets. UTF-8 pages retain exact source
identity and continuation; invalid or binary streams return explicit status and raw lookup. This
navigation view does not issue evidence citations, alter the captured bytes, or rerun a command.


### Report-only human follow-up
A native human follow-up after a runtime handoff may explicitly select saved questions
from its immediate previous goal in the same conversation for compose, review and render.
The native follow-up receipt supplies this provenance; tool arguments cannot grant it.
Default selection, investigation completion and question mutations stay on the current goal.
The report manifest retains both the authoring goal and source goal; prior reports and
question ownership are unchanged. A new case, branch or unrelated goal is not adopted.


## Concurrent UI reads and report recovery

The investigation view reads a bounded SQL snapshot, then releases its read
transaction before independent-review citation revalidation or report-state
projection opens another connection. This prevents a waiting writer and a
nested reader from holding each other up in SQLite rollback-journal mode.
The retained row set, source-version checks and atomic write validation stay
unchanged; no retry executor or alternate judgment store is introduced.

Compose saves return exact reads for the saved group and the new draft index.
A stale snapshot/edit reference or invalid draft pointer remains an error. The
error carries a read-only compose recovery call scoped to the selected questions;
it neither rewrites a stale request nor adopts prose, references or conclusions.
The native agent decides whether to reread, revise or stop.

Offline regressions exercise concurrent commits during UI revalidation and
projection, native-tool recovery responses, preserved prose, and rejected stale
snapshots. Production recovery must preserve the existing investigation and
resume only a goal paused for this deployment.
# Bounded current-question cards

The task card reader reuses the canonical note/judgment/execution projector.
When the full report snapshot exceeds its existing 4 MiB / 20,000-record read
budget, navigation selects analytical note/change records, their referenced
source returns, and the matching native mission start/result receipts in one
read-only SQL snapshot. Historical note definitions remain available for stale
reference checks. Unselected tool output, prompt telemetry and report prose do
not enter this task projection. The combined selected input keeps the same
budget; it does not allocate that budget again for each query.

The reader releases its SQL transaction before canonical citation revalidation.
It neither writes judgments nor calls a model. Returned, failed, unassessed and
assessed checks keep their existing meanings. Whole-card output limits, omitted
counts and exact detail lookups are unchanged.

A selected task projection is marked `_task_focus_only`. It must not establish
report freshness, report authoring readiness or a write snapshot. Those remain
unknown unless the complete bounded report state is available. If the selected
dependencies themselves exceed the budget or cannot be read, cards remain
unavailable rather than implying that no questions or checks exist.


## Scope-preserving mandatory review and retained navigation

Native advisory continuations retain the current goal as the scope authority.
The current case's broad scope and a reviewer's proposed test cannot authorize
new collection during a report-only task. The author assesses retained results,
saves an explicit investigated/refuted response with exact citations, or records
a specific scope limitation and reopening condition. No response is fabricated
or automatically selected by the harness.

At the native final boundary, an existing current-goal authored report selection
is restored through the same human follow-up and question-ownership checks as
compose/render. Earlier report questions remain read-only; review responses are
written through the current goal's atomic question writer. Mandatory executive
review still requires three rounds of five explicit responses. That dialogue
history is distinct from whether the latest review covered the selected current
report; an earlier unscoped review is not retroactively promoted.

Review event hashes and canonical source payload versions are separate. Inbox
navigation translates the verified retained event into a case-bound source
handle and exact field read. It returns mechanical save identity fields only;
operation identity, disposition, reasoning, evidence and limitations remain
author-supplied. Large retained results use bounded discovery rather than
loading their full body into the inbox.

State/source pages return exact field and continuation calls. An invalid field,
offset, or stale snapshot remains a rejected request with a read-only recovery
call. The existing page and compacted-context budgets remain unchanged; whole
navigation calls survive native pruning. No guessed field, latest-version
substitution, evidence adoption or automatic write retry is performed.

### Resume context

A saved report selection is restored as read-only report navigation on native
resume and compaction, including its authored draft and review state. Historical
source questions do not become writable questions of the current follow-up.
Completed intake reports the evidence binding, not the original investigation
objective. Independent review questions remain response obligations; proposed
checks remain unadopted advice, available through the exact inbox lookup. The
main agent chooses their relevance and next action within the current goal.

## Current judgment reconciliation and reading scopes

Authoring material uses the same current selected claims and active gap dispositions as the
question view. Resolved and not-applicable obligations remain in the ledger but are not current
limitations. Later tool results alone never decide supersession: the analyst explicitly updates
answer, individual claims and affected gap dispositions through the existing atomic question
writer. Acquisition success, search coverage and bounded non-findings remain different claims.
The report skill's `references/current_judgment_review.md` defines the semantic comparison.

Each `cell_basis_keys` list can independently select exact support for its parallel cell from
that entry's `basis_keys`. Entry-only keys preserve context; they do not become citations for
every cell. Direct source/observation statements also bind to their owning current judgment,
so an unchanged original cannot keep a stale conclusion current. Exact identity headers and
unrelated groups can be reused; mere review receipts do not rewrite unchanged prose.

Native follow-up receipts now retain message occurrence UIDs. Compaction recovery resolves an
exact same-UID generation within verified lineage and validates its original receipt; rewind
or foreign conversation authority is rejected. When a valid follow-up lacks an owned response
question, the inbox returns a neutral explicit existing-writer call. Reading never creates it,
and historical questions remain read-only. Existing executive rounds survive the handoff.

HTML offers body-only, body plus technical evidence, and full audit views of the same immutable
snapshot. The first two citations remain visible; additional exact references expand without
changing evidence order. Endnotes link back to their occurrences. Long cells become lossless
labelled prose records; exact excerpts use monospace and only explicitly supplied line numbers.
Screen text uses 16px and 1.5 line height; supplementary explanations remain gray (#5F6368).
Default printing respects collapsed originals; full audit printing expands all records. Word
continues to use its retained template and style. Generation-time draft/review status is explicit
and distinct from E01 evidence hashing. Later final selection never mutates a saved HTML bundle.

Reader-visible limits and supplied next checks follow existing masking, scope and provenance
contracts. The host does not choose forensic conclusions, rank actions or promote a test's
local success to system-wide certainty. Synthetic regressions and private-case/rendered checks
are separate acceptance evidence; neither certifies unexamined forensic meaning.

## Source-scoped authoring and answerability

`compose(evidence_keys=[...], snapshot_id=...)` reads at most eight selected current
non-question source cards. The `/source_cards` read view retains exact quotations,
source identities, coordinates, inference scopes and scope-record roles. It does not
select evidence or infer a fact. Question-wide answers are contextual navigation.
Named values must select their own support; a context-only Q is not factual support.

New time windows require subject/qualifier and a reasoning record with selected
basis keys, timestamp role, premise, alternative, year and timezone basis. Legacy
bundles remain immutable and readable. Entailment is reviewed, not decided by a
keyword rule or a successful timestamp parser.

Whole-document `answerability_checks` cover acquisition, incident/entry timing and
personal-data presence/exposure/transfer. An agent-declared useful outstanding
investigation prevents ready and returns the existing report-research read path.
A limited result requires its real constraint and reopening check; missing exact
certainty does not force endless investigation. These are derived review records,
not new case facts or a second investigation ledger. Existing current-owner writes
remain necessary for new facts.

`report_work` separates authoring, targeted investigation, current-version review,
rendering and final delivery. Finalization reports actual integrity and independent/
layout review status, including unavailable layout. It requests delivery of the
selected bundle, not new writing, while native investigation closure and manual
stops remain separate. Minor retained wording does not restart completed rounds.

## Audience projection

`forsic-integrated-audience-1` renders the same source-bound authoring ledger as an executive body,
a practitioner appendix and an audit view. Cells supply self-contained executive explanations (finding → plain-language meaning/impact → decision); full cell_details,
assessment rationales and unavailable reasons appear in the practitioner appendix. Brief confidence
qualifiers remain authored in the body. Raw gap correction history is never appended to page one.
Major IOC and priority-action groups use the existing CAS authoring/review transaction; no second
judgment ledger or automatic malicious-address classifier is introduced. Acquisition/analysis events
remain in the detailed chronology. All quotes, source versions and reciprocal links remain available.
The adaptive Word exporter reuses the pinned template components, theme, geometry and package parts
while following this audience order; immutable old v2 bundles retain their original retained regions.

### Audience typography

New audience reports use the shared `audience_typography` role map. Title and overall judgment carry emphasis; body text is regular weight, with smaller muted citations and supporting details. Word table normalization must not override gauge/citation roles. Literal quotations retain their complete text, and historical retained profiles keep their original rendering behavior. Empty template spacers are not full-size body lines.

### Executive comprehension

The overall cell starts with a one-line qualified headline and then explains discovery, impact and needed decisions in plain Korean. Only the explicit first line receives conclusion typography. Main-body explanations must be understandable without technical background or appendix lookup. Explain terms at first use, keep complete nominal sentences, and avoid both jargon-only fragments and repeated defensive qualifications. Metadata remains concise values. Review the actual body alone for comprehension as well as the full report for source support; a materially unintelligible body requires a targeted edit, while minor wording does not reopen completed review rounds. No business function, loss, unauthorized activity or attack sequence may be invented for narrative completeness.

### Case-specific executive context

The runtime procedure lives in `forsic_plugin/skills/forsic-report-driven/references/audiences.md`.
The existing `executive_context` group adds one to five named question/answer rows to the same
CAS transaction and saved render; it is neither another review round nor another model runner.

### Complete final-review packet and scope dispositions

`independent_review` reuses one shared reviewer policy across ordinary, final-report and optional
executive modes. Final authored reports have a finite 56,000-character input bound; ordinary review
retains 40,000. These are serialized-input limits, not changes to native runtime or call budgets.
Lossless columnar rows and direct nested JSON remove repeated field names and double escaping when
needed. Complete authored cells/details, referenced current claims and consequential gap dispositions
are mandatory. Original evidence still uses exact source/version/field handles and explicit coverage.
Insufficient capacity returns preflight diagnostics without a partial final-review dispatch.

Ren receives why a material check was deferred, its declared next action and actual prerequisites.
The label `assessed_unresolved` or `none` is not a proven external constraint. Unexamined accessible
personal-data scope remains investigator work where a useful authorized bounded check exists. A later
user request for that work updates an earlier report-only scope; it does not authorize unrelated collection.
The process and stop rules live in `references/completion_and_answerability.md` and
`references/final_body_evidence_review.md`; reader wording lives in `references/style_and_wording.md`.
