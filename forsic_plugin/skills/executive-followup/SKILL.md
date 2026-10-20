---
name: executive-followup
description: Select important follow-up questions from the integrated investigation report or current answers, then reuse evidence, investigate, and update the report.
---
# Follow-up questions from the reader

When a report draft exists or the user asks what else to check, identify **questions left after reading the current answers**.
Follow forsic-report-driven's references/audiences.md for audience guidance. In ordinary conversation,
select only useful questions. Use the specified 5 questions per round for 3 rounds only when the independent
executive follow-up feature below is enabled. Do not investigate every possibility or write management
decisions, response recommendations, or a “what decision is needed” section. Keep user-facing answers and reports in Korean.

## Read and compare

Read forsic_reporting(state/gaps), the integrated report draft, and the relevant latest notes and evidence.
For long material, continue through existing pointer pages as needed. Read the report before creating
a generic checklist. Do not recreate answered questions, semantically equivalent open questions, or questions outside this scope.

For case-specific reader question examples, read
forsic_skill(name="executive-followup", file_path="references/reader-questions.md").

## Short follow-up list

For each question, record **follow-up question / current answer and evidence / remaining gap / next check**.
Explain its importance by how it could change the conclusion, confirmed impact, or investigation scope.
Replace vague requests for more investigation with the source, target, period, and observation that would
change the answer. Distinguish editing gaps answerable from current evidence from gaps requiring a new discriminating check.

## Connect to the existing investigation loop

- If the source already contains the answer, assess existing results and update the notes and report. Do not repeat collection.
- Correct an open question in forsic_question with its existing question_id and expected_revision.
  Save an important new question through the same tool with its current answer, scope, evidence, and next_checks.
  Select evidence using C2/C1 references from actual reads; a question does not establish compromise.
- Perform necessary reads or searches and update the current answer. Optionally use mission → existing tool → assess
  when advance discriminating conditions need to be preserved. Do not create retrospective missions for results already read.
  Proceed only with executable work within the user's currently approved overall investigation scope.
- For checks requiring new material or permission, record what is needed. Missing material, timeout, and unevaluated results are not evidence of absence.
- If results change an answer or important gap, render both reports from the same snapshot again. Follow
  forsic-report-driven's finalize procedure for final selection and Telegram delivery; do not attach drafts under review.

When no important executable next check remains, state remaining limitations and finish. Do not generate
endless follow-up questions from the same report. This is a reader-perspective improvement, not independent analyst approval or proof of a complete investigation.

## Enabled independent executive questions and answers

When runtime executive_followup is enabled, an executive reader in an independent context reads the current
report and provided source references at the existing native goal's final review boundary, returning 5 distinct
important questions per round. The main agent must not impersonate this reader to generate the questions.
The reader has no authority to execute tools, adopt evidence, or close the investigation; questions are not new facts or evidence.

Read all questions, source lookups, and current question_id/revision in forsic_reporting(action=review_inbox).
After investigating, call forsic_review_response with top-level review_id/finding_id, operation_id,
question_id/current expected_revision, disposition, reason, and citation_refs containing actual C1/C2 selections.
Use no action/payload wrapper for this tool; scope_limited also requires limitation. Only if forsic_review_response
is absent from the session's pinned tool menu, follow review_inbox's response_contract.legacy_input to send the same
fields as forsic_reporting(action=review_response, payload=...). Respond as investigated for actual checks,
refuted for evidence-based refutation, or scope_limited with the exact unperformed scope and reason. Responses are saved in the same notes;
do not treat the question's own source anchor as answer verification. If new investigation is needed, perform
only necessary discriminating checks with approved material and tools.

Distinguish unsupported tools, unavailable material, actual execution failure, and insufficient permission.
Do not turn an unsupported capability into a claim that separate approval is required. If permission is insufficient,
identify the actual user scope or permission restriction. If recovery could change the answer, compare the target,
original location or format, required result, and cost. Do not require carving every image to resolve a question
or assume recovery is possible.

Open the next round only after all 5 preceding responses are validly linked to current notes. Give the independent
reader all 5 prior questions and Forsic responses, the latest report, and source references to select remaining questions.
Forsic responses are analyst input, distinct from original evidence. Do not repeat equivalent questions to fill a quota.
Finish after 3 rounds and 15 responses; do not request a fourth review.

The runtime manages round and question counts and validates prior responses. On incomplete returns, cancellation,
manual stop, exhausted call budget, or unknown delivery status, do not resend or resume automatically.
Report preserved state and limitations. Completion of 15 responses completes the follow-up conversation;
it does not establish every hypothesis, independent analyst approval, or case closure. If answers change,
update existing notes and reports from the same snapshot and follow the existing finalize procedure.
