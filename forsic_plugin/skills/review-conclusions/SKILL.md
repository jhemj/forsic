---
name: review-conclusions
description: Check observation accuracy, inferential links, and competing explanations in important conclusions, correcting them within the same investigation.
---
# Review important conclusions

Before delivering key answers or closing scope, review changed claims in the existing investigation turn, starting
with the strongest statements. Include titles, summaries, and timelines. Repeated review of unchanged evidence
and judgments, separate missions, and review receipts are unnecessary.

Configured independent review returns advice at existing native boundaries, not original evidence or proof of
completion. Read forsic_reporting(action=review_inbox) for unanswered material questions and their source lookups.
Compare feedback with originals when relevant evidence returns. If it changes the current answer, save answer,
judgment and related review_responses together via forsic_question. Otherwise forsic_review_response saves the
disposition on the same question using top-level fields and its current expected_revision. Explain the actual
finding with selected C1/C2 evidence, or use scope_limited with an explicit limitation. Receiving advice is not
answering it; do not accept a reviewer's premise without checking. Choose useful followup checks yourself.
Only if forsic_review_response is absent from the session's pinned menu, use review_inbox's response_contract.legacy_input
with the same fields under forsic_reporting(action=review_response, payload=...).

1. **Observation:** Compare the statement with the selected citation field's subject, object, action, result, and time.
   Commands/configuration, execution, success/effects, authorization, and attribution to a person are different
   claims. If the content comes from another line, select that line.
2. **Scope:** Check actual acquisition, display, and search scope and remaining regions. full_field is the entire
   preserved field; byte_preview is a byte display. Do not turn records_decoded=false into structured-record
   interpretation. Zero literal matches or omissions in partial listings do not establish absence across unchecked
   periods, expressions, or activities.
3. **Links:** State assumptions needed to connect observations to explanations. Compare source/clue independence
   and explanatory power; summaries, notes, or repeated queries of the same result are not independent evidence.
   Interpretation labels do not justify incorrect observations.
4. **Competing explanations:** Compare the strongest realistic alternative and conflicting evidence. Multiple
   independent circumstances can support a favored judgment without direct proof of every link. Qualify links
   that remain indistinguishable.

Refer to correlate-time for temporal links. Correct field conversion does not establish a particular event's time
or causality. Verification covers only the proposition actually compared. Do not extend current hashes/metadata
to historical identity or source authenticity. A benign explanation of activity differs from overall system state.

For discrepancies, correct the statement itself and use answer-question to correct the same question's current
answer, evidence, counterarguments, and limits together. Do not retain an earlier categorical claim by merely
appending limitations. Distinguish retrying a failed save from actual semantic review. Preserve originals and
pre-correction records; continue other feasible investigation work.

Judge completion through the original request, actual review scope, and value of remaining checks. Disclose
important unreviewed material without requiring every file to be checked. File creation, successful save validation,
and optional review are separate from investigation success. Preserve limits of unreviewed items without steering
the conclusion toward benign activity or compromise.

For scan-sensitive-data findings check candidate→actual-data, holdings→access, communication→transfer and sample→population promotion. Reviewer input remains safe; raw local report appendix is not review material. See [scan-sensitive-data](../scan-sensitive-data/SKILL.md).
