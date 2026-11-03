"""Fixed v2 writing regions shared by native authoring and both renderers.

This contract checks shape and presence only. Evidence entailment, uncertainty,
causality and the relative strength of explanations remain agent judgments.
"""
import re
from .authoring_policy import time_basis_schema

TEMPLATE_VERSION = 'forsic-integrated-v2-reader4'
# Editorial contract only; the retained Word regions and their shape stay v2.
WRITING_GUIDANCE = (
    'Write self-contained Korean explanations in cells; optional parallel cell_details hold technical basis. '
    'Keep material uncertainty in the conclusion. Use concise nominal endings (noun forms): 확인, 관찰, 추정, 유력, 미확인. '
    'Use formal hierarchy (Ⅰ. → 1. → 가. → -), one proposition per line and two-space-indented subitems as needed. '
    'The body serves executives: result, connected event meaning, leading interpretation, impact and selected IOC. '
    'Priority response actions remain in the practitioner appendix, not a separate body section. '
    'A nontechnical executive must understand the body without opening the appendix: what was found, '
    'what it means for this system, what impact is established, and what decision/action follows. '
    'Name the subject and explain technical terms on first use. Concise does not mean noun fragments or jargon lists. '
    'Use complete nominal sentences; connect each important finding to its supported meaning. '
    'Cells may contain a headline and a few explanatory lines; only metadata cells are values alone. '
    'The overall cell begins with a one-line judgment, then plain-language discovery, impact and decision lines. '
    'Do not invent business function, outage, unauthorized use, data loss or damage to make the account sound complete. '
    'Retain a brief necessary '
    'qualifier such as 유력, 추정 or 미평가. Keep the reasons favoring the leading account and any '
    'conclusion-changing competing explanation in the body. Put work procedures, exact technical derivation and detailed scope/limitations '
    'in cell_details, which the host places in the practitioner appendix, visible in its print scope. '
    'Do not repeat a conclusion in other regions or repeat the same caveat in every cell. '
    'Templates own typography and citations; '
    'supply plain strings, no HTML/CSS, manual citation numbers or internal Q/C/G/O/S handles. '
    'Preserve every material known finding and useful hypothesis in the body or its detailed appendix; '
    'brevity removes repeated wording, not distinct information. Explain the strongest supported account, '
    'why it outranks competing explanations, and what remains unproved. '
    'Do not paste ledger answers, source handles or correction history. A brief qualification in the body '
    'must keep its meaning honest; detailed limitations belong in the appendix, not in the executive lead. '
    'Prefer a supported leading estimate over bare 미확인 when certainty is unavailable: put the estimate '
    'in text and its basis, assumptions and unconfirmed part in detail. '
    'Use 미확인 alone only without a defensible estimate. Separate observation from inference: a missing direct timestamp does not preclude a '
    'supported before/by/after bound. State the event being dated, causal prerequisite, selected evidence, '
    'year/clock assumptions and strongest competing account. Date an activity as activity, not automatically '
    'as compromise. Save changed factual inferences through the current owning question before reporting them. '
    '추정/유력/확정 describe the selected proposition, not a tool success or a confidence score. '
    'Preserve exact original quotations in the appendix without rewriting their endings.'
)
# Formatting only: no hostname/IP facts are inferred or extracted from prose.
# Dates use numeric/date-time notation; provenance belongs in the detail field.
IDENTITY_VALUE_RULES = (
    ('hostname', r'(?:\S+|해당 없음)', None, 'host.example'),
    ('representative_ip', r'(?:\S+|해당 없음)', None, '192.0.2.1'),
    ('acquisition_time', r'(?:미확인|해당 없음|[0-9](?:[0-9\s./:~–—+\-T년월일시분초]|UTC|KST|Z)*)', 80, '2000-01-01'),
)


# Body order is fixed; the writing order puts synthesis after detailed fields.
GROUPS = (
    ('report_identity', '조사 대상·이미지 채증 시점', ('report_identity',), 3,
     'Supply hostname, representative IP, and image acquisition time, in that cell order. Use supported '
     'case records, not a folder name, report generation time, analysis start or file mtime/ctime. '
     'Hostname and representative IP are one non-whitespace primary token, or 미확인 / 해당 없음; no arbitrary hostname length limit is added. '
     'Acquisition time is a numeric date/time/range with optional timezone, at most 80 characters total, or 미확인 / 해당 없음. '
     'All three values are single-line, without repeated field-label prefixes or explanatory sentences. '
     'Put identity provenance, user-provided status, uncertainty reasons and timezone/start/end explanations in the corresponding cell_details, '
     'retained with the same per-cell support in the practitioner appendix. Keep different image identities/times in the details. '
     'If only a recorded acquisition completion date is supported, present that date alone and identify it as 채증 완료일 in its detail; '
     'do not discard it merely because the exact start/instant is unknown. Never substitute conversion, analysis '
     'or unrelated file metadata for acquisition. Use 미확인 only for the still unsupported portion.'),
    ('overall_judgment', '종합 판단', ('overall_judgment',), 1,
     'After drafting details, write one headline line followed by a few plain-language lines in the same cell: '
     'discovery and why it matters, established impact, and the decision/action needed. A reader with no incident '
     'background must understand the situation from this cell. The template emphasizes only the first line. '
     'Keep decisive supporting facts in the body; put technical derivation and detailed limits in cell_details.'),
    ('executive_context', '별첨 참고사항', (), 2,
     'Optional practitioner appendix only, never a separate executive body section. Preserve useful '
     'source-bound context here when it does not fit an existing appendix field. Omit this group if '
     'unnecessary; do not add orientation filler or start investigation just to fill it. '
     'Cells are a short nominal heading for that reader question, then a self-contained answer explaining '
     'the supported fact and why it matters to a decision. Consider evidence secured/preserved and its '
     'reliability, actual examination scope, affected business/customer scope, current risk/containment '
     'and decisions needed, only where this case makes them relevant. Reuse current findings and existing '
     'executive-review answers. Do not repeat the summary, gauges or actions, or create rows of generic unknowns. '
     'Distinguish physical original custody, acquired image, verified analysis copy and a provider statement; '
     'a written hash is not a successful comparison. Historical image observations do not establish current '
     'operational state or completed containment. Select exact support for each answer; put technical '
     'provenance/assumptions in detail. A decision-critical unknown can be a brief answer with its practical '
     'consequence and next action. Do not invent ownership, service role, damage, legal duties or quantities. '
     'Place decision-critical meaning in the existing overall finding, relevant major finding or '
     'priority action; the executive must not need this appendix to understand those conclusions.'),
    ('major_findings', '주요 조사결과', tuple('major_findings.' + k for k in
     ('timing', 'entry', 'activity', 'personal_data', 'external_effect')), 2,
     'Two cells per axis: a compact time/assessment summary, then one integrated finding-and-judgment '
     'explanation. For BOTH timing and initial entry include a supported date/bound/range with 추정/유력/확정, e.g. YYYY.MM.DD 이전 (추정); '
     'the example is a format, never a default date or bound. Retain actual precision; never turn the last '
     'retained record or acquisition time alone into a compromise/deletion bound. The entry summary starts '
     'with the initial-access time estimate, followed by route; the judgment explains the supported link. '
     'When no defensible bound exists, explicitly state 진입 시점 미확인 instead of omitting time. '
     'Put its concrete reason in detail. For the remaining axes use a short '
     'finding and its qualification, without forcing a date. The second cell explains the finding and its significance '
     'to a nontechnical reader, using one or two complete nominal sentences rather than another label. '
     'Put technical derivation in parallel cell_details. If not confirmed but an estimate is supported, write that estimate and '
     'its unresolved scope rather than replacing the whole answer with 미확인.'),
    ('event_overview', '사건 전개 개요', tuple('event_overview.' + k for k in
     ('entry', 'activity', 'information_contact')), 1,
     'Explain the observed account activity, suspicious program behavior/configuration, and communication evidence '
     'in plain language, including why each matters. Distinguish reconstructed events from unknown links; '
     'do not invent a connected attack story or repeat a list of technical nouns.'),
    ('information_impact', '개인정보 영향', tuple('information_impact.' + k for k in
     ('presence', 'exposure', 'transfer')), 2,
     'Separate information presence, external exposure and actual transfer; supply finding and judgment for each.'),
    ('key_evidence', '판단의 핵심 근거', (), 3,
     'For each selected evidence row write observed material, directly established fact, and what it cannot establish.'),
    ('key_indicators', '주요 IOC', (), 3,
     'Select actionable indicators from retained evidence: cells are type/value, classification/meaning, '
     'and recommended operational use. Keep the exact value and per-cell support. Put observed location, '
     'hash-to-file mapping, matching scope and false-positive cautions in cell_details. Distinguish a '
     'static C2 candidate from an observed connection; victim addresses, public DNS, ordinary shared paths '
     'and administrator accounts are not malicious indicators merely because they occur in evidence. '
     'A size/hash difference alone does not establish a backdoor or maliciousness. A PID-file value is not '
     'an observed process. Do not prescribe blanket blocking of contextual indicators. Never invent or abbreviate a file hash. '
     'If no indicator can be selected, supply an unavailable row with the reason in detail.'),
    ('response_actions', '우선 대응 조치', (), 3,
     'Write at most five distinct recommended actions for the practitioner appendix: priority, concrete action, and expected result. '
     'Use the current findings and actual business impact; do not invent completed mitigation, owners, '
     'deadlines or harm. Put dependencies, scope and decision conditions in cell_details. '
     'Preserve evidence and verify current operational state before recommending removal or isolation. '
     'Do not invent a related IP from a domain or promise eradication from a proposed action. '
     'Separate containment/remediation recommendations from the practitioner investigation checklist.'),
    ('synthesis', '종합 해석', ('synthesis.likely', 'synthesis.alternative'), 1,
     'Executive body, after the headline: explain what the combined observations mean and why the leading '
     'account is favored, using a few complete nominal-Korean sentences with explicit subjects and transitions. '
     'Connect supported observations through the same object, actor or operation; distinguish parallel evidence '
     'from established causality. The alternative entry explains a meaningful competing account and its effect '
     'on the conclusion, not a checklist of everything unknown. Keep a short qualifier for a conclusion-changing '
     'unknown. Put query patterns, parser failures, credentials, carving status, exact technical derivation and '
     'full scope in cell_details. Do not repeat the overall headline or add its label inside the text. '
     'Nominal endings must retain full explanations, not turn the account into noun fragments.'),
    ('timeline', '사건 시간축', (), 3,
     'Author a connected chronology, rendered as a visual timeline in both HTML and Word. '
     'Each entry has time, event and interpretation: preserve supported time precision; give the event '
     'a short concrete headline with its actor/target/action; explain its meaning and supported connection '
     'to the previous or next event in complete nominal Korean. Select per-cell basis for that connection. '
     'Combine repetitive observations only when no distinct event or support is lost. Chronological '
     'adjacency is not causality: link the same account, object or operation where supported; label '
     'inferred connections 추정/유력 and leave unrelated observations separate. Do not invent stages '
     'such as initial compromise or exfiltration. Put clock assumptions, alternatives and repeated '
     'technical caveats in cell_details; retain a brief conclusion-changing qualifier in the event. '
     'Keep attempt/failure/success distinct. Use event_kind to separate actual activity, response, '
     'acquisition and analysis; mere metadata does not establish an administrative action. '
     'The interpretation cell answers what this observation demonstrates about access, authority, '
     'execution, persistence, response or impact in this case. A count of recovered log lines, a tool '
     'name or a statement of what cannot be proved does not answer that question. Explain technical '
     'terms on first use. A successful-authentication record means accepted account authentication; '
     'it does not automatically mean an unauthorized intrusion. Describe a privileged-command record '
     'as the action it actually records, not proven command success or privilege theft. '
     'The host attaches selected citations at the relevant text, not an empty evidence column.'),
    ('timeline_summary', '사건 경과의 흐름', ('timeline_summary',), 1,
     'After filling the individual timeline entries, reread the whole timeline page in order and '
     'write one short connected account in two or three complete nominal-Korean sentences. '
     'Explain the progression and combined significance of the supported account activity, system '
     'changes and program observations; do not simply repeat row headings. Then revise only rows '
     'whose subject, transition or significance is unclear. Link events through supported actors, '
     'objects or operations, not mere temporal proximity. Where a connection is unproved, describe '
     'parallel strands of evidence without inventing an attack chain. Put detailed caveats and '
     'alternatives in detail; the summary conveys meaning, with only conclusion-changing qualifiers. '
     'Use selected evidence for each factual connection. This is the existing one-pass reader-order '
     'review, not another investigation or review cycle. Older saved reports without this optional '
     'region remain readable; do not rewrite unrelated groups to add it.'),
    ('undated_events', '시각 미확정 중요 사항', ('undated_events',), 1,
     'State important events whose time is unknown, or explain why no such event is represented. Never invent a date.'),
    ('scope', '판단 적용 범위', ('scope',), 1,
     'State in one sentence the actual scope and minimum limitation that could change this judgment.'),
)
GROUP_IDS = {group[0] for group in GROUPS}
OPTIONAL_GROUPS = {'executive_context', 'timeline_summary'}
WRITING_ORDER = ('major_findings', 'information_impact', 'key_evidence', 'timeline', 'timeline_summary',
                 'key_indicators', 'response_actions', 'undated_events', 'scope',
                 'report_identity', 'synthesis', 'event_overview', 'executive_context', 'overall_judgment')
REVIEW_CHECKS = ('coverage', 'evidence_support', 'scope_and_uncertainty', 'alternatives',
                 'consistency', 'duplication', 'reader_order')

# Field purposes are stable analytical questions, not case-specific answers.
# Expose them alongside named slots so generic names such as timing cannot be
# confused with acquisition/analysis times from a neighbouring source packet.
NODE_PURPOSES = {
    'report_identity': '호스트명·설정상 대표 IP·이미지 채증 시점. 채증 완료일만 있으면 완료일로 한정 표시. 변환·분석 시각과 구분.',
    'major_findings.timing': '사고 관련 활동의 관측 시점과 침해 성립 시점의 근거 있는 추정 범위. 채증 시점을 대신 넣지 않음. 비인가 행위가 선행 접근을 필요로 한다면 그 행위 시점까지 접근이 성립했다는 상한을 전제·반론과 함께 검토. 직접 최초 시각의 부재만으로 상한 추정을 버리지 않음.',
    'major_findings.entry': '최초 진입 시점(정확 시각·추정 구간·이전/늦어도 상한 중 근거 있는 표현)을 먼저 제시하고 수법·경로·판단 근거를 설명. 경로 미확인과 시점 추정 가능성은 별도. 정상 로그인만으로 침입 상한을 설정하지 않음. 어떤 범위도 추정할 수 없으면 진입 시점 미확인으로 명시하고 사유는 별첨에 보존.',
    'major_findings.activity': '자료에서 관측된 행위와 비인가성·실행·성공의 판단. 원문의 변경 관리대장 등 비교 자료를 새로운 공격 행위로 바꾸지 않음.',
    'major_findings.personal_data': '개인정보의 종류·위치·보유·접근·전송과 조사 범위 요약. 미검토·자료 부재를 안전이나 미보유로 표시하지 않음.',
    'major_findings.external_effect': '검토 범위 내 외부 영향의 관측 또는 추정. 전송 자료가 없으면 영향 미확인, 전송 없음이나 안전으로 요약하지 않음.',
    'information_impact.presence': '개인정보 종류·실제 저장 경로·검토 모집단. 파일명만으로 보유 단정 불가. 읽을 수 있는 관련 데이터의 유용한 검사가 남으면 조사 후 답변.',
    'information_impact.exposure': '권한·서비스 노출 가능성과 실제 행위자의 열람·접근을 구분. 보유 확인은 실제 접근 입증과 별개.',
    'information_impact.transfer': '실제 전송·유출의 직접 기록 또는 추정 근거와 미검토 범위. 자료 없는 상태를 부재로 확정하지 않음.',
}

# Editorial assessments are authored with the existing versioned entry. These
# scales describe different propositions; they are never probabilities or a
# keyword-derived classification. None is distinct from the lowest risk level.
ASSESSMENT_SCALES = {
    'overall_judgment': ('침해 정황 없음', '의심 정황', '침해 가능', '침해 유력', '침해 확정'),
    'major_findings.timing': ('확인 불가', '정황 확인', '범위 추정', '유력', '확정'),
    'major_findings.entry': ('확인 불가', '정황 확인', '부분 추정', '유력', '확정'),
    'major_findings.activity': ('악성행위 정황 없음', '의심 행위', '악성행위 가능', '침해 행위 유력', '주요 침해 확정'),
    'major_findings.personal_data': ('안전(미보유)', '보유·접근 제한', '노출 가능', '노출 유력', '위험'),
    'major_findings.external_effect': ('영향 정황 없음', '의심 정황', '영향 가능', '영향 유력', '영향 확정'),
}
ASSESSMENT_GUIDANCE = (
    'Optional assessment uses level 1..5 with the returned node-specific labels, '
    'or null for unassessed. The renderer displays six cells: a separate neutral 평가 보류 '
    'for null, followed by the five ordinal levels; never use level 0 or 6. Select from the current supported judgment, never '
    'keyword counts or tool success. Level 1 for privacy requires supported '
    'non-possession in the stated scope; missing review is null, not safe. '
    'rationale briefly explains the selected proposition and scope using this '
    'entry\'s basis_keys. For timing/entry, optional time_range.start/end are '
    'reader display values: YYYY년 MM월 DD일 HH시 mm분 ss초 KST only when that '
    'precision/timezone is supported; retain coarser precision or 미확인 otherwise. '
    'Never derive an incident interval from admin response, file ctime or acquisition alone. '
    'A time_range may use relation range, before, by, after or from. For before/by use start=null; '
    'for after/from use end=null. New one-sided bounds require subject (which event) and qualifier '
    '(관측/추정/유력/확정). In particular, by means no later than, including that boundary; before excludes '
    'the boundary. Do not fill an unknown end with a zero time or an arbitrary earlier day. '
    'Every window requires subject, qualifier and reasoning: select its basis_keys, timestamp_role, '
    'causal premise (or observed-sample scope), strongest alternative, year_basis and timezone_basis. '
    'An observed account-activity window is not an intrusion bound. A metadata time alone does not date '
    'installation, concealment, response or intrusion. A supported causal bound is allowed when its premise '
    'survives the competing explanation. Keep unknown precision unknown. '
    'Explain the inference in rationale and cell_details using the selected basis_keys. Keep material limits '
    'in cells/details. Assessment changes require current-body review.'
)
EVENT_KINDS = {'activity': '사건 관련 행위', 'response': '관리자 대응',
               'acquisition': '채증·변환', 'analysis': '분석', 'unknown': '분류 미확인'}


def assessment_schema():
    return {'type': 'object', 'additionalProperties': False,
        'properties': {
            'level': {'type': ['integer', 'null'], 'minimum': 1, 'maximum': 5},
            'rationale': {'type': 'string', 'minLength': 1, 'maxLength': 300, 'pattern': r'\S'},
            'time_range': {'type': 'object', 'additionalProperties': False,
                'properties': {
                    **{key: {'type': ['string', 'null'], 'minLength': 1, 'maxLength': 100, 'pattern': r'\S'}
                       for key in ('start', 'end')},
                    'relation': {'type': 'string', 'enum': ['range', 'before', 'by', 'after', 'from']},
                    'subject': {'type': 'string', 'minLength': 1, 'maxLength': 60, 'pattern': r'\S'},
                    'qualifier': {'type': 'string', 'enum': ['관측', '추정', '유력', '확정']},
                    'reasoning': time_basis_schema(),
                }, 'required': ['start', 'end', 'subject', 'qualifier', 'reasoning']},
        }, 'required': ['level', 'rationale'],
        'description': 'Versioned assessment; use the node-specific labels and guidance in field_contract.'}


def group_contract(group):
    return next(value for value in GROUPS if value[0] == group)


def expected_nodes(group):
    return list(group_contract(group)[2])


def row_limit(group):
    nodes = group_contract(group)[2]
    return 5 if group in ('response_actions', 'executive_context') else 16 if group == 'timeline' else len(nodes) if nodes else 12


def cell_labels(group):
    return {
        'report_identity': ['Hostname', '대표 IP', '이미지 채증 시점'],
        'executive_context': ['핵심 확인사항', '확인된 내용과 의미'],
        'major_findings': ['시점·요약', '판단'],
        'information_impact': ['확인된 내용', '판단·근거'],
        'key_evidence': ['관측자료', '직접 입증하는 사실', '그 자료만으로는 미입증'],
        'key_indicators': ['유형·지표', '분류·의미', '대응 활용'],
        'response_actions': ['우선순위', '권고 조치', '목적'],
        'timeline': ['시각', '기록상 사건', '사건 해석'],
    }.get(group, [group_contract(group)[1]])


def validate_group(group, draft):
    _, _, nodes, columns, _ = group_contract(group)
    entries = draft['entries']
    ids = [entry['node_id'] for entry in entries]
    if len(set(ids)) != len(ids):
        raise ValueError('Template node IDs must be unique')
    if nodes and (not set(ids) <= set(nodes) or (draft.get('complete', True) and set(ids) != set(nodes))):
        raise ValueError('Every fixed template node must be represented: ' + ', '.join(nodes))
    if not nodes and any(not re.fullmatch(re.escape(group) + r'\.[A-Za-z0-9_-]{1,48}', key) for key in ids):
        raise ValueError('Repeated rows require stable ' + group + '.<row-id> node IDs')
    if len(entries) > row_limit(group):
        raise ValueError('Too many template rows in this writing group')
    for entry in entries:
        if 'event_kind' in entry and group != 'timeline':
            raise ValueError('event_kind is supported only on timeline nodes')
        if 'assessment' in entry:
            if entry['node_id'] not in ASSESSMENT_SCALES:
                raise ValueError('Assessment is supported only on overall_judgment and major_findings nodes')
            if 'time_range' in entry['assessment'] and entry['node_id'] not in ('major_findings.timing', 'major_findings.entry'):
                raise ValueError('time_range is supported only on timing and entry nodes')
            window = entry['assessment'].get('time_range')
            if window:
                relation = window.get('relation', 'range')
                start, end = window['start'], window['end']
                valid = ((relation == 'range' and start is not None and end is not None) or
                         (relation in ('before', 'by') and start is None and end is not None) or
                         (relation in ('after', 'from') and start is not None and end is None))
                if not valid:
                    raise ValueError('time_range endpoints must match relation: range needs both; before/by only end; after/from only start')
                if relation != 'range' and not all(window.get(k) for k in ('subject', 'qualifier')):
                    raise ValueError('One-sided time_range requires subject and qualifier; preserve its inference basis in rationale/details')
        if len(entry['cells']) != columns or any(not cell.strip() for cell in entry['cells']):
            raise ValueError(f'{group} requires {columns} nonblank cells per entry')
        if group == 'report_identity':
            for cell, (slot, pattern, limit, _) in zip(entry['cells'], IDENTITY_VALUE_RULES):
                repeated_label = any(cell.casefold().startswith(label.casefold() + separator)
                                     for label in (*cell_labels(group), *[rule[0] for rule in IDENTITY_VALUE_RULES])
                                     for separator in (':', '：'))
                if ('\n' in cell or '\r' in cell or repeated_label or
                        (limit is not None and len(cell) > limit) or not re.fullmatch(pattern, cell)):
                    raise ValueError('report_identity.values.' + slot + '.text requires a primary value only: '
                        'hostname/IP use one token or 미확인 / 해당 없음; acquisition time uses date/time/range and optional timezone only (80 characters total). '
                        'Move explanations into this slot detail; update only report_identity.')
        if 'cell_details' in entry and (len(entry['cell_details']) != columns or
                any(not isinstance(detail, str) for detail in entry['cell_details'])):
            raise ValueError(f'{group} cell_details requires exactly {columns} strings, including empty strings')
        if entry['status'] == 'unavailable' and not entry.get('unavailable_reason', '').strip():
            raise ValueError('Unavailable template entries require an explicit scope/reason explanation')
        if not entry['basis_keys'] and entry['status'] != 'unavailable':
            raise ValueError('Saved prose requires selected basis_keys; use unavailable with an explanation when no material applies')
    return {**draft, 'entries': sorted(entries, key=lambda e: list(nodes).index(e['node_id']))} if nodes else draft
