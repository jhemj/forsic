# Representative acceptance preparation (CP05)

These three independent, hand-authored CC0-1.0 datasets are synthetic. They are preparation for human-reviewed behavior trials, not a benchmark score or evidence of autonomous success. No real case data, downloaded fixtures, external calls, new evaluator or agent loop are included.

Expose only `inputs/<dataset>/` to a future trial. Never mount `truth/`, this README, or `manifest.json` as investigation evidence. The question file specifies the initial read range. The manifest preserves exact input and truth hashes, byte counts, provenance and limits. Truth is independently specified from the question and records, not inferred from a keyword scorer.

- `discriminating_clue`: the same job name distracts from conflicting run/account identity; exit status proves only the recorded process exit.
- `benign_explanation`: positive schedule, request and execution identity agreement explains one selected maintenance run.
- `partial_range`: first read is two lines; line 2 is deliberately malformed JSON and line 3 is initially unexamined. A continuation may change the answer; do not expose truth as evidence.

`test_acceptance_cases.py` verifies hashes/separation and takes actual retained reads through the existing registered question handler, saves a provisional answer, persists an explicitly scripted review receipt without a model call, saves the corrected answer/review response in the same writer, then inspects current projection and HTML/Word output. Scripted answers live in evaluator-only truth files. Assertions verify persistence, exact citations and projection, never semantic validity. This is lane C only. Parser failure here is a property of synthetic JSONL, not a claim that every runtime parser was executed. Existing parser/runtime tests remain separate.

For lane B, obtain separate authorization and freeze source/native revisions, actual model fingerprint/serving/sampling, tool schemas, context/compression, permissions and budgets using HARNESS_ACCEPTANCE.json. Record all actual calls, errors, range continuations, provisional/current answers, review and correction revisions, reports and interventions. Human reviewers compare retained evidence and the independent rubric: evidence fidelity, bounded inference, strongest normal alternative, material coverage, useful next check and final correction. Do not score text overlap, citation count or detection count. Report dimensions and failures individually; actual model behavior remains `not_run`. A scripted contract pass never becomes a behavior pass.
