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
