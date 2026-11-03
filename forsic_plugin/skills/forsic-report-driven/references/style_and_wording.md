# Wording, presentation, and design contract

- Integrated report body: aim for about 3 A4 pages. Current answer → entry and activity → information impact and IOC → connected main sequence. Response actions and detailed limitations belong in the practitioner appendix. Use brief honest qualifiers in the relevant assessment. Do not fill pages by rephrasing the same proposition.
- When requested, give prioritized next checks and proposed response actions with their basis, expected value and uncertainty. Separate recommendations from observed actions and management decisions; proposing an action does not execute it or extend permission.
- Preserve every materially informative finding, supported hypothesis and competing explanation. Concision removes duplicated wording, not distinct information. The body explains the combined observations, their meaning, the strongest supported account and why it is favored; include a competing account when it changes the reader's understanding. The appendix supplies full factual derivation, technical alternatives, work details and inspection scope. Uncertainty qualifies a useful explanation rather than erasing it.
- The default body view serves executives. The practitioner appendix includes judgment-first explanations, representative evidence, examined scope and material limits. Full execution, review dialogue, UUIDs, hashes, correction history and reproduction paths remain accessible in the report's audit view. Preserve them without expanding the whole record by default.
- Render integrated HTML; a requested Word export reuses the same saved claims and snapshot without another model call. Use short evidence numbers in the body; place exact internal references and detailed history in the same document's appendix. Templates are not case evidence.
- Use Forsic's charcoal text, warm-white surfaces, restrained red brand accents, thin rules, and Noto Sans CJK KR or an approved substitute. Brand color is not a risk judgment. Five-level gauges retain text/markers, a neutral confidence axis and separately labeled risk levels; unassessed is gray. Do not add large Forsic illustrations or staged activity to reports.
- Templates own typography. Keep a clear conclusion/detail hierarchy, readable gray secondary details (#5F6368), generous screen line spacing and top-aligned table cells. Screen body starts around 16px/1.5; print uses the pinned report style. Do not meet the page target by deleting limitations or excessively shrinking text.
- Express unconfirmed findings, conflicts, and corrections in text. Do not express conclusions through red color or probabilities alone.

## Reader hierarchy and cell roles

Use `Ⅰ. → 1. → 가. → -` as needed, without manufacturing extra levels. Within a finding, write one short qualified
judgment in `cells`, then basis and explanation in optional parallel `cell_details`. Keep confidence and
material uncertainty in the visible lead. Empty detail strings are allowed; supplied detail arrays match
the exact cell count. Do not split or duplicate the lead in details. Labels may use 확인, 추정, 유력 or 미확인;
explanations use short formal Korean nominal endings (확인·관찰·추정·유력·필요). Separate long explanations into finding, evidence,
meaning and limits rather than placing a document-length paragraph in one table cell.
Do not truncate exact evidence or conceal important counterevidence to meet a length target.

Header: Hostname / 대표 IP / 이미지 채증 시점 only. Use actual acquisition records and distinguish images,
acquisition start/end and timezone where relevant. Never substitute a report date, analysis cutoff,
last retained log or file metadata timestamp. Keep hostname/IP values concise; put their source, explanation or qualification in practitioner appendix
details with the same evidence references. Preserve unknown labels and reasons.

Major findings follow the returned named form: a compact qualified `summary`, then a friendly `judgment`
explaining the evidence's practical meaning. The host supplies the two visible columns, gauge and [n]
footnotes; `detail` supplies the practitioner's evidence and scope. Do not recreate the older four-column
구분/시점/판단/근거 layout inside a cell.

File references in authored reader text use **plain-language role(filename)**, for example
`의심되는 파일(worker.bin)`, `접속 기록(secure)` or `재실행 설정(crontab)` when those roles are supported.
For an unconfirmed suspicious file use `의심되는 파일(파일이름)`; never upgrade it to 악성 파일 merely to
sound concise. On later mentions keep the same understandable label or a clear short reference.
Put full paths, hashes, process IDs and technical names in practitioner detail or the major IOC section
when needed. Preserve exact filenames and distinguish same-name files by a short location qualifier.
This wording rule never changes exact quotations, IOC values, path fields or evidence coordinates.

If a fact is not confirmed but a defensible estimate exists, lead with that estimate. Explain why it is
favored, its assumptions and the still-unconfirmed link. For example, a synthetic supported finding may
use `cells: ["반복 구동 유력·실제 행위자 미확인"]` with
`cell_details: ["- 근거: 설정과 실행 흔적의 대상 일치\n- 한계: 최초 설치 시점 미확인"]`.
This is a format example, never evidence or a mandatory conclusion. If the supplied scope cannot support
an estimate, keep 미확인 with its concrete reason. Do not turn every unknown into a speculative answer.

For a supported time bound, show `[date] 이전 (추정)` or `늦어도 [date] (추정)` as the lead, with its subject
(initial entry, execution or observed activity) explicit. State the prerequisite that justifies the bound and
the principal alternative in concise supporting lines. A missing exact entry timestamp does not by itself
invalidate a bound derived from a supported unauthorized action requiring access. Mere login success does not
supply that premise. Preserve source year/timezone uncertainty and distinguish strict before from no-later-than.
Use a one-sided visual time range rather than `미확인 ~ 미확인`; the undetermined endpoint remains null in the
authored field. Do not create an event date while formatting it. Metadata explanations remain available on paper;
long exact originals belong in the selected technical/audit print scope.

## Exact Korean sentence patterns

Confirmed fact: “[대상]의 [기록 종류]에서 [내용] 확인. [n]”
Analytical judgment: “[설명] 유력\n- 근거: [독립 근거와 경쟁 설명 비교]\n- 한계: [확정하지 못한 범위와 이유]”
Indistinguishable explanations: “[관측]만으로 [경쟁 설명들] 간 구별 곤란\n- 추가 판별: [가능한 검사 또는 재검토 조건]”
Benign alternative: “[정상 설명] 가능성 잔존\n- 판별 자료: [선택 자료]”
Material request: “[질문]을 판별하려면 [최소 자료 범위]가 필요함. 현재 보유 자료·도구로는 [이유] 때문에 확인 불가.”
Actual action: “[실행 기록]에서 [조치] 수행을 확인. [결과·범위].”
Further investigation: “[미확인 질문] 판별을 위한 [자료·검사] 추가 대조 필요.”

## Prohibited shortcuts

Command record → execution success; account → actual person; private address → corporate/attacker ownership;
hash difference → malicious tampering; no result → no activity; file ctime → creation/execution time;
recommendation → completed response; model score → compromise probability.

During semantic review, compare the actual report clause with the observation and its limits. A
configured process-termination command supports the configured action; whether it executed or
succeeded depends on returned outcome evidence. Authentication records support the recorded login
event, while authorization requires its own context. A single process snapshot has a sampled time;
duration requires temporal support. File metadata times retain their field meaning and time assumptions;
they do not automatically date installation, execution, or deletion. Qualified estimates and combined
inferences are valid when the agent explains support, competing explanations and remaining uncertainty.
Keep that qualification beside the affected clause rather than hiding it in the final scope sentence.

## Forsic

Current work reflects actual request state; prior results reflect actually adopted results. Even when reading
past work, keep current execution and color tied to the latest valid scope. Explain purpose, evidence,
expected discrimination, and limitations instead of exposing full internal reasoning.


Keep the distinct purpose of each retained template region. Summarize a finding once, then use
other regions for its evidence, alternative or impact rather than repeating the same explanation.
Keep brief qualification beside the affected conclusion; group detailed limitations by judgment in the
practitioner appendix. Do not repeat gap history or generic safety padding in the body. The shared reader style uses the explicit conclusion/detail roles in HTML and downloaded
Word while retaining the source Word template, headings, geometry and original appendix details.
Author the chronology as an event sequence within comparable clocks. Each visual timeline item contains
its supported time, a concrete event headline, and a complete nominal sentence explaining its significance
and supported link to adjacent events. Show a connected account of the same account/object/operation when
supported, instead of repeating isolated caveats. Temporal proximity alone is not cause: label inferred
connections 추정/유력; keep unrelated records separate. Preserve per-cell evidence selections, attempt/result
and actor distinctions. Put technical clock assumptions and detailed alternatives in cell_details.
The host renders the same saved order as a numbered rail with event cards in HTML and Word/PDF, retaining
citations beside the relevant text. Acquisition/analysis events appear in the appendix. No empty evidence
column, invented attack stage, inferred exact date, proportional spacing or confidence percentage.

## Executive body and practitioner appendix

Fill `key_indicators` for the body major IOC section and `response_actions` for at most five priority
recommendations in the practitioner appendix only. IOC slots are indicator / meaning / use. Preserve exact values and sources; details
record location, applicability and false positives. A suspicious static domain is a candidate, not a
confirmed connection. Victim addresses and public DNS are context, not automatic blocking targets.
Do not invent a hash or compress it into a usable-looking prefix. Recommendations are not executed actions.

Body `text` supplies a readable fact, its significance and the supported interpretation, in complete nominal
sentences with clear transitions. In `synthesis`, explain the case across observations, not just within isolated
slots. `detail` supplies exact evidence, technical assumptions, full competing explanations and scope in the
practitioner appendix. Do not move the meaning or reasons for the main judgment out of the body, and do not
put the same long caveat in both. Use nominal Korean and one proposition per line. The appendix explains each judgment before presenting representative exact quotes;
full source receipts, internal identifiers and correction history are in the audit appendix. Web reading
always shows every section. The three print choices affect printed scope only, with the complete report
selected by default. Never depend on a collapsed-only note for the meaning of a body claim.
