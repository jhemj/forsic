# User questions during an investigation

Forsic enables the existing Hermes clarification transport in background mode.
A question returns `pending` immediately. Independent investigation continues;
only conclusions or actions requiring that answer remain deferred. The deadline
is one hour (`clarify.ui_timeout: 3600`). Missing answers stay unknown and never
constitute consent. Partial submitted answers survive expiry.

Native requests own the deadline and reconnect replay. Settlements are persisted
as native clarification-result rows and consumed at an admitted turn boundary.
An expiry cannot reopen a completed or paused goal. Later actual user input stays
separate and receives pending clarification context without being blocked.
Questions are process-local like other native server requests: server shutdown
cancels them; an unanswered question is not silently recreated after restart.
Existing case events correlate requested, pending and resolved states, including
a response that wins the race with the tool-return observer. They contain status
and references, not a second copy of the user's answer. Telegram uses the existing
important-question delivery path; no past messages are replayed by this change.

Question selection remains agentic. At material gaps the investigator weighs
local inspection, independent review and facts/material only a user can supply.
No fixed question count or one-hour sleep is imposed. The configured investigation
budget is 24 hours of native active goal time; already-consumed time survives turns,
compaction and restarts, and an explicit manual pause freezes the clock. Changing an
existing budget preserves elapsed time and native pause controls; a resume does not
reset the total. After a successful runtime handoff, a new human chat message
starts a bounded native follow-up goal for that request. Prior questions/reports
stay preserved; the new scope can reuse their retained sources without adopting
old assessments automatically. Reconnects, background replies, manual pauses and
hard-expired goals do not start follow-up goals.

At 12 and 18 hours of cumulative native active-goal time, the main model reviews
findings, remaining gaps and the value of the next investigation, then chooses
finish or continue with a reason. Persist the decision on the existing native goal
using the actual saved assistant response row; a pending or unknown decision is
awaiting review, not permission to continue or a completed checkpoint. Finishing
earlier is allowed. Manual pause, guardrails, goal replacement and the hard cap
take precedence; a checkpoint never resets elapsed time or starts another goal.
These reviews use the existing native turn path, with no second loop, timer or DB.
A saved finish decision means closing intent (`runtime_phase=closing`, shown as
“마무리 선택”); the goal stays active for the existing question-save and
compose/review/render tools. It does not immediately pause on a tool-less summary.
Finishing at 12 hours suppresses the later 18-hour decision. Normal closing and
95% partial handoff retain their existing completion and report gates.

The final cap remains 24 active hours. Native closing at 90% saves the meaningful
current answer, evidence, counterarguments and limits through the existing
compose/render path; broad new discovery stops while useful bounded completion
checks remain allowed. At 95%, partial results hand off through the existing native
final-response path and automatic goal continuation pauses. 100% remains the
last-resort hard cap for stuck or unresponsive processing. Neither a checkpoint
nor budget exhaustion certifies completion. Disclose whether saved work is a draft
or partial snapshot versus a reviewed report. Open questions do not stop independent
closing work, and an absent answer stays unknown, never consent.

The UI shows start, 12-hour and 18-hour checkpoints, and the current actual final
cap (normally 24 hours), with native elapsed time frozen during explicit pauses.
A reached threshold without a saved decision shows awaiting review. The current-work
tooltip derives from existing events without another model call. The per-turn
model-request iteration limit is separate from this cumulative whole-goal allowance.

The analysis image includes binutils. `strings` availability is probed, and the
bounded binary-inspection example reads only the first MiB of a selected file.
Printable text is a lead, not proof of execution or whole-file coverage.
