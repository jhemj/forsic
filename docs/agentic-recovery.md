# Native recovery and evidence-backed authoring

Forsic keeps one Hermes conversation loop, retry budget, compressor and goal lifecycle.
The following changes repair transport and storage contracts; they do not choose a forensic
conclusion, force a scanner, or add a second judgment store.

## Returned context exhaustion

A provider `length` response with tool calls and a provider-confirmed filled context uses
Hermes' existing bounded overflow compression. The returned prompt usage supplies request
pressure when the local estimate was too low. Retrying a larger output allowance against
the same full prompt cannot recover these calls. Output-only truncation retains its native
retry path. Partial arguments never execute. A pending safe pause settles without executing
the truncated call or starting another compression/request.

Every physical returned response contributes usage once before recovery; completed failed
attempts receive the existing post-request receipt. Compression remains separately measured.
Provider exceptions without returned usage do not invent a token charge.

## Saving current answers and independent review dispositions

`forsic_question` remains the atomic current-answer writer. If the answer is unchanged,
`forsic_review_response` saves one finding using that same writer. The inbox supplies exact
finding identity and current question revision choices. Re-read it after a write rather than
reusing a stale revision. An important changed answer and its reasoning still belong in the
same explicit question update.

Rejected drafts do not mutate notes. Error guidance distinguishes retained source event IDs
from C1/C2 citation selections and provides exact retained-source navigation when available.
It never silently rewrites a judgment, adopts evidence, drops fields, or relaxes case,
revision, citation or idempotency checks.

## Reviewing the retained report template

The fixed template and prose authoring path are unchanged. `compose(section=review)` returns
a complete node index with exact field/material read calls instead of repeating the entire
draft. Pages are bound to the selected basis and draft hash; changed prose/basis rejects stale
reads. After a write, the receipt supplies fresh CAS edit references.

Review records include bounded comparisons of assertions selected by the reviewer as material:
what the text says, what selected sources support, and the limits/assessment. Honest unknowns
may have no supporting basis. The host checks identity and structure, not entailment or truth.
Configuration/invocation/success, authentication/authorization and recorded/event times remain
distinct analytical questions. Full node coverage stays explicit; no number of comparisons
is evidence of investigative success. Existing saved delivery bundles remain immutable;
an older draft review without comparisons needs a current review before a new final delivery.

## Initial triage and recovery

A complete retained export offers a preparation template with its exact selected input.
The agent chooses a registered profile and supplies format/ABI assertions. Preparation is
not execution; input admission still verifies versions, ownership, complete bytes and hashes.
Compression retains exact selectors and a profile-contract lookup rather than all options.
Scanner candidates remain separate from adopted evidence. Recovery procedures distinguish
unsupported filesystem readers, signature carving, incomplete attempts and scoped deferral.

## Offline acceptance

Use a fresh temporary `HERMES_HOME` and the installed test interpreter, with repository and
`upstream/hermes` on `PYTHONPATH`. No models, live evidence or network are needed.

- Native `test_context_full_tool_recovery.py`: filled-context compression, output-only retries,
  safe pause, cancellation, bounded failure and per-attempt SQLite usage.
- `test_agentic_review_saves`: rejected draft isolation, exact repair navigation, five independent
  grounded saves, idempotency and foreign/invalid evidence rejection.
- `test_report_narrative`: exact bounded draft reconstruction, stale page/edit rejection,
  honest uncertainty and linked review comparisons.
- `test_export_triage_contract`: actual synthetic export to selected scanner admission and
  candidate/source reread, including incomplete/uncertain/changed export rejection.

These regressions establish contracts. Real investigation quality, semantic correction and
end-to-end latency remain observations of subsequent native work, not consequences guaranteed
by passing tests or deployment.
