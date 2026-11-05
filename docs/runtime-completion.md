# Native report completion policy

Forsic uses Hermes' existing goal, conversation loop, scheduler, leases and SQLite
receipts. Report work is a read-only projection of current case records. It cannot
grant execution authority, change evidence or increase an allowance.

## Activation and migration

New goals keep the legacy policy unless the owning profile explicitly selects:

```yaml
goals:
  runtime_policy: report-completion-v2
  ready_target_mode: soft
  completion_contract:
    required_formats: [html]
    completion_target: final_ready
    layout_required: false
```

Required independent/executive review also follows the existing profile settings.
The contract is pinned in the goal at creation. Changing the profile does not
retroactively change an existing goal. Format and layout requirements must reflect
the actual deliverable; an unverified screen/print requirement remains unfinished.
Forsic's new-install configuration selects v2 explicitly; unmanaged Hermes profiles
retain their legacy default.

An existing goal requires explicit, authorized migration while paused. Use
`hermes_cli.goals_completion.migrate_policy` with the exact original goal identity,
the intended completion contract and an authorization reference. The CAS rejects
a replaced goal, a pending manual pause, unknown accounting or unresolved recovery.
It preserves used time, turns, checkpoints, review history and the pause reason.
Resume through the native goal lifecycle with `reset_budget=False`. Migration is
not a fresh allowance or permission to resume a user's manual stop.

The preparation target can be `strict` or `soft`. Forsic's new-install configuration
selects `soft`: the target starts the finishing window, rather than aborting a
healthy finishing operation merely for crossing that target. The original hard
allowance remains unchanged. Existing pinned goals, including goals without this
field, keep their strict behavior. An authorized change uses
`amend_ready_target_mode` against a paused, drained goal. Its CAS appends an
amendment receipt while preserving the original migration, consumption and history.

## Clock, reserve and dispatch

The persisted native active clock includes WAIT and restart downtime while active;
an explicit pause freezes it. An optional absolute wall deadline remains effective
across pauses. The earlier deadline governs submission.

For an allowance B, settlement S is `ceil(max(900 seconds, B/48) / 60) * 60`.
The artifact preparation target is the native deadline minus S. Known remaining
report work W, queue Q and uncertainty U reserve `W + Q + U + S`; queue already
included in W is not counted twice. Without a valid measurement, the reserve is at
least 25% of B. Thus cold-start discovery closes by 75% of a 12/18/24-hour allowance.
Closing is sticky. An overdue checkpoint does not reopen discovery. Strict mode
settles at the preparation target; soft mode permits admitted finishing work until
the original hard deadline. Neither mode renews a request or recovery deadline.

Model, summary, tool, review and delegated dispatch are bounded by the native
deadline, the pinned target mode, original recovery episode and inherited operation
deadline. The actual SDK consumer enforces that bound even when a stream continues
to emit tokens; a late response cannot become a successful result. Cancellation
belongs to the individual request, so an old timer cannot interrupt a later request.
Retries and human approval waits do not renew these bounds. Closing
allows canonical report actions and narrowly selected verification of a current
material blocker. A self-declared report label cannot authorize a broad shell
command. Saved missions and the existing bounded selected-file scanner provide
verification authority; fresh identity, input, blocker and argument checks still
apply immediately before execution.

Closing also admits same-owner, current-revision judgment updates, answers and
dispositions for current material findings, and the corresponding document edits.
It does not admit new questions or missions merely because they are labeled report
work. Returned bounded verification can be assessed without repeating discovery.

## Recovery and checkpoint receipts

Turn outcomes are typed: complete, yielded, recoverable error, terminal error and
interrupted. Display prose cannot authorize a retry. Compression deferral persists
a bounded recovery intent with an owner, due time, original deadline and attempt
count before releasing the turn. The existing TUI scheduler restores it on startup
without a browser. Native admission and turn leases win over duplicate wakeups.
Unknown delivery is recorded rather than automatically resent.

Milestone decisions use a small canonical packet with tools removed and provider
input/output headroom checked. Exactly five fields are required. One format repair
shares the original episode; restart and repair cannot reset its deadline or count.
The source message UID is persisted before native CAS. Invalid output is never
converted into a model `finish` decision. A recoverable unavailable decision can
produce a separate native technical-closing disposition. Storage/CAS failure and
manual control remain authoritative.

## Report resources and completion axes

The projection contains current judgments, outstanding review/disposition work,
formats, artifact selection, delivery and available ordinary review slots. Unknown
work is not zero. An executive missing-item repair cannot consume the last ordinary
slot reserved for required final review. Reservation and attempt intent use the
existing review lock and history; uncertain calls are not refunded.

An existing review worker has a read-only owner/deadline projection. Native WAIT
retains its original bound; the shared recovery scheduler checks the stored request,
thread and response without starting another review or waking the model repeatedly.
A durable response permits one native continuation through CAS and normal lease
admission. An unknown/lost owner, ambiguous dispatch or expired deadline produces
an explicit incomplete disposition. A busy file lock alone is not proof of progress.

Read-only projection caching is bounded and invalidates on native identity,
canonical ledger, review intent/receipt, configuration and artifact changes. Inflight
or uncertain review state is not reused. Cached summaries never replace the actual
finalization, file hash or basis checks.

Every v2 exit records model-free settlement against the original goal. Existing
artifact paths, byte counts and hashes are verified. Artifact status (`final_ready`,
`final_ready_with_limitations`, `draft_preserved`, `partial_checkpoint`), review
status, delivery status and native goal status are separate. A selected report with
an explicitly allowed review-availability limitation retains that limitation. A
goal stopping or a file existing is not proof of report completion.

The existing startup/periodic recovery sweep also settles an expired v2 goal when
no live actor owns it. Unknown accounting is recorded explicitly. An unavailable
work projection preserves verified earlier draft paths for that same goal, with
draft status; it cannot reuse an old final or delivery endorsement. Manual control,
replacement identities and conflicting CAS writes remain authoritative.

The current-judgment UI reads the same state: phase, remaining active allowance,
reserve, preparation target, wake schedule, owner/lease diagnostics and required
blocker count. A missing owner is visible; an active status is not presented as
proof that a worker is running. The native control snapshot also exposes the
import-time module fingerprint so disk parity is not mistaken for loaded code.

## Verification and rollback

Native regressions run through `upstream/hermes/scripts/run_tests.sh`. Product
regressions use isolated synthetic case/native databases. Deterministic integration
tests connect recovery, milestone source/CAS, current independent review, rendering,
selection and actual artifact verification. Mutation controls test actual admission
and receipt boundaries.

Mock-provider and virtual-clock tests are distinct from live-provider autonomous
E2E, operating-case observation and actual 12/18/24-hour soak. Record configuration,
provider/model fingerprint, sampling, context size, concurrency, required reviews,
interventions and actual elapsed time for each live run. A short successful run is
not a 24-hour soak. Missing checks remain pending, and a single success does not
establish a zero recurrence rate.

Deploy only at verified native safe boundaries, with state and source backups and
conflict checks. Preserve evidence, case/native journals, usage and credentials.
Rollback requires paused, drained recovery and a compatible reader of the new
metadata. Do not restore an old database over newer receipts or start an old binary
against an active v2 episode. Exported source excludes operational data and secrets.
