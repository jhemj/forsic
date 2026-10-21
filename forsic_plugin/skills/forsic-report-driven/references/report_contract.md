# One integrated report contract

Present freshly authored reader prose, current judgments, and adopted evidence in 통합보고서.html.
Generate Word only when the user clicks Word download. It uses that report's retained reader view,
literal masking, sensitive appendix, template and style, without another model call or current-case
projection. Reuse the verified cached Word document on subsequent downloads. Do not create executive and practitioner editions with different
content, conclusions, or versions. Practitioner details belong in the same report's appendix. Without
question_ids, use questions owned by the current conversation and goal.

Use the native `forsic_reporting` compose workflow before normal rendering. Read its bounded writing packet,
write the summary, findings, and limits sections in connected natural language, and save each section using
the returned basis, revision, operation ID, and basis_keys. The program supplies exact evidence references.
Compose across the current judgments and shared question explanations rather than pasting question answers
or tool logs. Represent every selected question and important material gap. These coverage and reference
checks do not certify the prose's truth or semantic quality. Correct changed judgments through
`forsic_question`, then refresh the writing packet and revise stale prose.

Normal render returns an authoring handoff until the saved sections match the selected current basis.
Rendering itself never calls a model. An explicitly requested `allow_partial:true` creates a clearly marked
technical snapshot for evidence, review, and integrity work without authored reader prose. The manifest and
returned listing distinguish `authored_current` from `technical_partial`, retain the writing basis/revision,
and keep semantic_approval false. Neither state grants delivery approval or waives existing scope and review checks.

Optional report_slots retain seven assessment dimensions: compromise_time, entry_route, attacker_actions,
personal_data_presence, external_disclosure, decisive_evidence, and incident_timeline. They remain bound
to their owning question's selected claims, references, and scope, but do not replace natural-language
authoring. Filling all seven is not mandatory. Omission is editorial absence, not absence of evidence or
compromise; unavailable/not_confirmed and scope closure remain valid judgments.

Compare important gaps against current question gaps and preserved results. Return only worthwhile authorized
checks to native investigation. The renderer and audit neither execute tools nor save or reopen questions.
If answers change, correct them through forsic_question before rendering a new snapshot, preserving old files.

Aim for about 3 body pages describing the current most likely explanation, confirmed impact, important
counterarguments, and sequence. If complex causality requires 4 pages, retain core uncertainty. Keep
uncertainty that prevents misunderstanding beside the relevant assessment. Use the limits section for
material uncertainty and useful reopening conditions without repeating the findings. Put shared readable
question explanations and, when different, the exact canonical answers,
competing hypotheses, limitations, source coordinates, full timeline, execution arguments, receipts, hashes,
permissions, and corrections in the appendix. Use program-returned numbers, times, references, and receipts;
templates and examples are not case evidence.

Use reader references [1], [2], and so on. The shared HTML and on-demand DOCX projection links them to the final
evidence-notes appendix, preserving exact native identifiers (including C1/C2 when those are real retained
identifiers), versions, source ancestry, coordinates, quotations, coverage, and execution provenance.
Numbers are local to that report. Never invent numbers or replace arbitrary bracketed prose; select the
program's exact references through basis_keys. An older evidence version remains distinct and is never
silently substituted with the current record. Canonical technical tables appear once with linked references
from repeated appendix sections; their important limits and quotations remain available.

## IOC list and GTI cross-check appendix

The final integrated report must include selected IOCs and their GTI cross-check results in its own appendix.
Use the existing organize-indicators and threat-intelligence procedures; no new IOC ledger, report action or
GTI submission is needed. Complete one existing-report lookup or reuse a retained exact-indicator result for
every supported, authorized selected IOC before final rendering, within native budgets and any manual stop.
Preserve incomplete rows with their reasons and remaining verification when checks cannot complete. Do not
block all reporting on GTI availability or label an incomplete cross-check complete.

For each IOC preserve:
- Exact original observed value, type and canonical value when different; deduplicated identity must retain
  all relevant local discovery sources, original path/coordinates and actual evidence links.
- Case relevance/current interpretation separately from external reputation, with uncertainty and withdrawn
  status preserved. Reputation is neither an automatic blocklist nor proof of execution, communication or attribution.
- Exact GTI query value/type, returned lookup_at (with explicit timezone when formatted), retrieval/reuse status,
  outcome and delivery/http_status when available, plus the retained GTI evidence reference. Reused lookup_at
  remains the original query time; last_analysis_date is provider analysis time, not retrieval or case-event time.
- Only returned detection counts (malicious/suspicious/harmless/undetected) and relevant classification or
  gti_assessment; missing fields stay unknown. State stale results, mismatches and other important limits.
- An explicit non-query or failure reason for unsupported, not-permitted, disabled/not-configured, quota,
  authentication/permission, connection/timeout, malformed or otherwise unavailable checks. A verified 404
  not_found means queried with no report; it is distinct from no query or failed/unknown delivery. None of
  these states, nor zero detections, establishes benignness. Do not invent a lookup timestamp when absent.

The current appendix reads `forsic_indicators` summary, limitations and source_refs. Save each GTI result or
non-query reason there through upsert using the current revision; keep source_refs tied to actual retained
scalar values and byte spans. A returned GTI /indicator scalar can add an external_reference source for that
same IOC, but an error or disabled result without that scalar cannot: preserve its actual returned error/status, evidence_id or execution receipt in summary/limitations as
lookup-coverage text, retaining the IOC's existing local observation provenance. Failed/error tool results
may be refused by source/citation validation, so do not adopt them as successful case evidence or invent C1/C2
citations. Preserve their verification gap in the owning question's limits without citing a rejected result;
an ID written in text does not promise an automatic clickable link.
These are ordinary text fields, not new schema keys or automatic enrichment columns. Preserve original
observed spellings in summary when the renderer's canonical value alone would omit them.

Report selection includes only indicators with a source_ref selected by the reported questions. Select the
relevant local observation in the owning question through actual returned citations; select retained GTI results
for any reputation claims. After those saves, refresh compose and render, then verify each selected row's
summary, limitations and source/evidence links in the same snapshot's IOC appendix. No raw_response or
GTI-only field is automatically appended merely because a tool was called. If a row/reference is absent,
correct selection/saved content and regenerate; do not claim inclusion based on export(json/csv) alone.
When no IOCs were identified, state the selected examined scope and limits in the report's authored limits
section; an empty local register does not prove absence across unexamined evidence.

Only existing MD5/SHA1/SHA256 file reports and separately permitted public IP/domain reports are supported.
The reporting requirement leaves public-network opt-in unchanged. Keep private/sensitive indicators, paths,
accounts, credentials, URLs, original logs and binaries local; do not upload files, scan/rescan URLs, request
reanalysis or change connection settings. Publish the remaining verification reason in the report instead.

Word generation is an explicit local derived export. Its inputs are private, hash-bound render data in the
immutable HTML bundle, not downloadable evidence or copied executable code. A separate verified cache
records the input identity and renderer contract. Creating or reusing Word does not modify the bundle ID,
original files, current judgment, or delivery approval. A missing/unsupported contract, changed input or
cache file must fail rather than silently rebuilding from today's case. Existing bundles that already contain
Word remain valid through their original file hashes. Sensitive originals retain the same local-only policy.

For an unredacted bundle, selected-evidence.zip contains selected citations, excerpts, execution identifiers,
and hash-verification material. It is not an export of the whole case or original images. Do not claim to
verify full hashes of omitted originals or complete investigation coverage. Do not create a ZIP for redacted
reports; return that status.

Partial results may be generated and delivered. Structural/reference validation, semantic judgment, scope
closure, file creation, and delivery are distinct states. Do not treat skipped or failed optional review as
passed. Open generated files in the report view and use the actual bundle_id for finalize. This contract does
not authorize recipients, models, or original-source uploads outside existing approved mirrors and output
scope. The selected-evidence ZIP is not automatically delivered.
