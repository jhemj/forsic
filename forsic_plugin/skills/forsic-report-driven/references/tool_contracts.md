# Current investigation and reporting tools

Current schemas and returned next_call define call forms. This table is a tool-selection index.

| Action | Tool | Returns and limits |
|---|---|---|
| Check current scope | forsic_reporting(action=case) | Case, question, evidence, and output scope linked to the session |
| Direct exploration | forsic_list/read/search, forsic_image_files/image_read/image_search, forsic_archive_read | Actual results, coverage, preserved ID; body reads offer selectable C2 |
| Retrieve preserved originals | forsic_reporting(action=source) | Selected fields and exact C1/C2; summaries are not originals |
| Save/correct current answer | forsic_question | Atomic receipt for the current answer and assessment; not semantic validation |
| Read state, gaps, history | forsic_reporting(action=state/gaps), forsic_note(list/get) | Index and questions in current owned scope, and explicitly selected history |
| Optional advance check | forsic_mission → returned next_call → forsic_assess | Plan, actual execution, local assessment; not a prerequisite for ordinary exploration |
| Generate report | forsic_reporting(action=render) | Integrated HTML/Word report from the same snapshot with an appendix in the same document |
| Approved delivery | forsic_reporting(action=finalize) | Existing mirror delivery status; neither investigation completion nor new transfer approval |

Read answer-question for save/correction arguments, mission_policy for optional checks, and report_contract
for outputs when needed. Do not directly modify originals or the ledger to pass validation.
