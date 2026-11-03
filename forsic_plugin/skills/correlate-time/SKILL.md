---
name: correlate-time
description: Record raw times and temporal relationships separately from inferred event windows and their assumptions.
---
# Event timeline
Distinguish log occurrence, collection, and file mtime/ctime/atime. Verify ctime semantics for the filesystem and
tool; do not automatically interpret it as creation, execution, or introduction of a particular line. Temporal
proximity alone does not establish causality. Listings or file mtime alone do not establish content retention periods.

Record only selected events in the current question's `forsic_question.timeline`. Updates use question_id and
expected_revision; include retained entries when replacing arrays. Preserve existing event_ref values.
For stat times, link {evidence_id, field: mtime/ctime/atime} in time_source for code conversion. For logs, link
{evidence_id, field: log, pointer, literal, byte_start} to exact UTF-8 coordinates in the preserved original.
pointer is the position in the returned array. Include description and evidence_ids. Do not add years or timezones
absent from the original to raw timestamps or substitute investigation work times for event times.

Without exact timestamps, `temporal` can express a favored event window under assumptions. Set observed_or_inferred,
timestamp_role, and confidence_basis; use inferred for estimates. As needed, link earliest/latest (null if unknown),
candidates, assumptions, year_basis, timezone_basis, and clock_domain. Apply year/timezone assumptions only to the
inferred interval. Assess plausibility by comparing independence and competing explanations for metadata such as
ctime, logs, and boot/rotation records. Without an exact time_source, use basis_refs to link actual values or string
coordinates of selected evidence. If an absolute interval is difficult to establish, record only relations through
before/after/overlap/same_boot. State shared-clock or boot-scope assumptions when needed. Preserve contradictory
evidence as candidates and limitations. Keep observed numbers unchanged; explicitly correct mistaken activity
interpretations in the same question.

Prefer the narrowest defensible conclusion, not a repeated list of unknowns. A directly recorded initial-entry
timestamp is not required for a qualified bound. If an independently supported unauthorized action required prior
access, that action may bound entry as **no later than** its time, under the stated host/session linkage and clock
assumptions. The same timestamp alone does not prove strictly earlier entry. Use a strict before/after relationship
only when the supporting order excludes the boundary; leave the other endpoint null. Do not fill a missing day,
year, timezone or seconds with guessed precision. A normal login, collection date or metadata update alone does
not establish that intrusion occurred. In that situation give the supported account-activity window and retain
intrusion linkage as the specific unresolved issue.

Keep the source's literal timestamp unchanged. Save the inferred bound, subject, assumptions and contrary account
in the current question's existing temporal/claim fields; the report presents that judgment rather than creating
a second one. Report a short nominal lead such as `[supported date] 이전 (추정)` or `늦어도 [supported date] (추정)`,
then the decisive premise and limitation. These are display patterns, not default dates or required conclusions.

For image acquisition time, first read available acquisition logs and image-header fields and compare
completion/chain-of-custody records. Return a supported interval or qualified completion-date statement
when exact acquisition time is absent, preserving the distinct label and inference. A file birth/mtime
or E01 conversion date alone cannot replace acquisition. Record both the useful narrowed answer and
the remaining uncertainty. In report time_range.reasoning retain the selected source keys, time role,
premise, strongest alternative and year/timezone basis; code validates those fields, not their entailment.
