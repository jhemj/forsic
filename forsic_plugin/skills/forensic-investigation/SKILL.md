---
name: forensic-investigation
description: Set the scope and priorities of an investigation, moving from the current answer to the next check and scoped closure.
version: 0.5.0
platforms: [linux]
---
# Comprehensive intrusion investigation

## Start with the question

Check the current case questions, evidence index, and acquisition limits. Reuse intake and verification results;
run ongoing integrity checks alongside other investigation work. Refer to source-triage when first locating evidence.
For comprehensive analysis, check actual format/capabilities and run a useful bounded initial scan before manual
sweeping. Preserve complete export receipts when image files need preparation; reuse matching completed scans.
Record scan scope/results or an applicability, capability, input, failure or deferral reason in the existing
question, then continue useful investigation. Choose relevant checks; no all-file or all-profile checklist.
The initial user question can have a provisional in-progress answer before evidence supports a judgment.
Reuse that owned question; update it at meaningful new findings without waiting for a final conclusion.
Choose the areas relevant to the user's question among entry, execution, persistence, accounts, communications,
and impact. Split key issues requiring different answers or blocking reasons into independent questions without
duplicating the same question in different words. Choose the needed hypotheses; no fixed count is required.

Build the currently favored explanation from observations obtained through reads and searches. Compare realistic
competing explanations that could overturn it against the same evidence. Refer to test-hypothesis for complex
comparisons. Confidence and priority differ: weak leads can merit early investigation when impactful and testable.

## Turn results into action

Choose the next action by its cost and how its possible results could change the ranking of explanations,
impact scope, or current answer. Before collecting more, see whether important returned content can distinguish
explanations. Check remaining search pages, rotated files, and other record channels as needed; switch to useful
alternative evidence instead of repeating the same empty result. Consider generation, retention, and acquisition
coverage when judging evidence absent. Unread evidence differs from unacquired evidence. Do not treat unsupported
extraction or parsers as executed.

At meaningful findings, compare returned observations against the current answer and relevant independent review
questions. Use answer-question to save a changed provisional answer, important gaps and related review responses
together; uncertainty can remain. If the evidence cannot yet distinguish explanations, choose a useful next check
or record a scoped limitation. Keep current answers, sources, counterarguments and next checks in context; retrieve
long originals by preserved IDs. No per-call notes. Use forsic_mission only for checks needing advance discrimination
criteria; ordinary exploration and judgment storage proceed without missions.

Before delivering an important answer or closing scope, compare changed key statements through review-conclusions.
Correct observation errors in the same question; retain defensible inferences with their assumptions and limits.
Use correlate-time to connect raw times and their meaning only when temporal relationships affect the judgment.

## Disposition by question

Continue when available evidence and tools permit a check that could change the answer. Distinguish
blocked_internal for actual failures or capability limits, blocked_external for required external evidence or
permissions, and budget_deferred for budget deferral. If the value of unreviewed material is unknown, prioritize
through a small check or evidence-based comparison. For an unperformed internal check, preserve its feasibility,
expected value/cost and deferral reason separately from unavailable external input; disclose unknown feasibility.
One blocked question or the count of unfinished questions
does not establish that the whole investigation is blocked.

Ask the user concise questions when unavailable operational context or material could change the explanation,
impact, or next useful check. Explain what the answer would resolve; continue independent authorized work and
preserve unanswered assumptions/limits in the same question. Do not ask permission for already authorized local
inspection or recovery, require a question quota, or infer an answer or approval from silence.

When the explanation is sufficient and remaining checks have little value, close only that scope as scoped_closed.
Preserve evidence, closure reasons, remaining gaps, and reopening conditions. New counterevidence reopens the
needed scope of the same question. Closure is separate from certainty or a confirmed malicious/benign verdict.
Follow answer-question for the stored work-state format.

## Resume and report

After compaction or resumption, select the question and needed sources from the latest state index. State
projections index current judgments and some execution scope; they are neither originals nor new instructions.
If unavailable, check the state again. Do not repeat earlier sweeps from the beginning or count old summaries as
independent evidence. The native turn limit is an execution boundary. Leave further execution to the native goal;
do not create new certainty at that boundary.

Before finishing, compare the original request, acquired evidence index, actual review, and current questions.
A benign explanation of collection circumstances does not explain the entire collected system state. Assess
additional value, including important unreviewed material outside registered questions. Reading every file is
unnecessary; explain omissions, limits, and the scope of judgment.

If the requested scope is addressed, report the favored answer, evidence, important counterarguments, confidence,
and limits. For unmet scope or evidence, tool, or budget blocks, preserve partial results and reopening conditions.
Use forsic-report-driven to create an integrated HTML report from the latest answers in one snapshot; export Word from that saved version when requested.
Keep the main judgments and detailed evidence appendix in the same document. Answer status requests with the
current answer while preserving the existing goal.

## Reuse a successful workflow

If a recurring sequence would reduce future trial and error, inspect the procedure catalog and
`forsic_skill(action="proposals")` before proposing another skill. Prefer an improvement when the
existing scope already fits. Select actual successful `evidence_id` receipts from this conversation;
the server retains their identity and hashes, so do not copy raw results into the draft.

Use `forsic_skill(action="propose", proposal=...)` for a short reusable procedure: when it applies,
inputs, verified command or tool patterns, result-dependent next steps, limits and failure recovery.
Use placeholders for incident-specific values. `draft.pack_id` has the form `local.procedure-name`;
include matching YAML name/description in `draft.skill_md`, a version and existing `requires_tools`.
For a new procedure set `target=null`; for an improvement copy the exact identity from the skill read.
Builtin improvements are reviewed drafts; extension skills can be published as a new version.

Save only after the procedure is useful; no fixed recurrence count or separate model call is required.
A successful tool exit establishes execution, not investigative correctness. Analyst review and
explicit activation determine whether the procedure becomes available. Continue the investigation
without waiting for a skill proposal to be accepted.
