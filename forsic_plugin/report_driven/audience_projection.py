"""Audience layout over authored fields; no summarization or case inference.

Authored cells explain events, meaning and the leading account in the body.
Technical derivation and detailed qualifications remain in the appendix. Raw ledger history remains
in the audit view. Moving text never changes its author-selected references.
"""
from copy import deepcopy

from .template_contract import cell_labels


CONTRACT = 'forsic-integrated-audience-1'


def page(key, title, blocks, scope='body'):
    return dict(id=key, title=title, blocks=blocks, presentation_view=scope,
                section='body' if scope == 'body' else 'appendix',
                kicker='FORSIC / 조사 결과' if scope == 'body' else 'FORSIC / 실무자 별첨')


def heading(text, key=None):
    return dict(kind='heading', level=2, text=text, **({'id': key} if key else {}))


def entry_refs(entry, index):
    return deepcopy(entry.get('cell_citation_refs', [entry.get('citation_refs', [])] * len(entry['cells']))[index])


def authored_table(group, field, *, detailed=False):
    entries = field['entries']
    return dict(kind='table', region=group, headers=cell_labels(group),
                rows=[deepcopy(e['cells']) for e in entries],
                cell_details=[deepcopy(e.get('cell_details', [''] * len(e['cells'])))
                              if detailed else [''] * len(e['cells']) for e in entries],
                cell_citation_refs=[[entry_refs(e, i) for i in range(len(e['cells']))] for e in entries],
                authored_hierarchy=True,
                **({'widths': [25, 75]} if group == 'executive_context' else {}))


def project(pages, fields, state):
    """Use the existing authored layout as input; retain every moved field."""
    blocks = {b['region']: deepcopy(b) for p in pages for b in p['blocks'] if b.get('region')}

    def short(region):
        block = deepcopy(blocks[region])
        block.pop('detail', None)
        if 'cell_details' in block:
            block['cell_details'] = [[''] * len(row) for row in block['rows']]
        if region == 'metadata':
            # T01 below retains these exact per-value references and details.
            # Header citations otherwise recreate detail text in cite_pages().
            block['cell_citation_refs'] = [[[] for _ in row] for row in block['rows']]
        if 'assessment' in block:
            block['assessment'].pop('rationale', None)
        if region == 'overall_judgment':
            block['executive_summary'] = True
        for assessment in block.get('row_assessments', []):
            if assessment: assessment.pop('rationale', None)
        return block

    first = [short('metadata'), heading('Ⅰ. 조사 결과 종합'), short('overall_judgment')]
    first.append(heading('1. 사건 종합 해석'))
    for entry in fields['synthesis']['entries']:
        # Keep each explanation's own support; the merged technical callout is
        # not a sentence-level source binding for the executive account.
        first.append(dict(kind='paragraph', region='synthesis', text=entry['cells'][0],
                          authored_hierarchy=True, citation_refs=entry_refs(entry, 0)))
    first += [heading('2. 주요 조사결과'), short('major_findings')]
    second = [heading('Ⅱ. 개인정보 영향'), short('information_impact')]
    if 'key_indicators' in fields:
        second += [heading('Ⅲ. 주요 IOC · 침해 흔적 점검 목록'), authored_table('key_indicators', fields['key_indicators'])]
    # Main findings already explain the decisive facts and their meaning.
    # The complete source-by-source analysis remains in T01/T02 below.
    timeline = short('timeline')
    incident_rows = [i for i, kind in enumerate(timeline.get('event_kinds', []))
                     if kind not in ('acquisition', 'analysis')]
    if timeline.get('event_kinds'):
        for name in ('rows', 'cell_details', 'cell_citation_refs', 'event_kinds'):
            timeline[name] = [timeline[name][i] for i in incident_rows]
    third = [heading('Ⅳ. 주요 사건 경과')]
    if 'timeline_summary' in fields:
        for entry in fields['timeline_summary']['entries']:
            third.append(dict(kind='paragraph', region='timeline_summary', text=entry['cells'][0],
                              authored_hierarchy=True, citation_refs=entry_refs(entry, 0)))
    third.append(timeline)
    result = [page('I01', '정보시스템 침해사고 조사 결과', first),
              page('I02', '개인정보 영향·주요 IOC', second),
              page('I03', '주요 사건 경과', third)]

    explanations = []
    titles = {'report_identity': '조사 대상·채증 시점', 'overall_judgment': '종합 판단의 근거',
              'executive_context': '핵심 확인사항의 근거와 적용 범위',
              'major_findings': '주요 조사결과별 해설', 'event_overview': '사건 전개 해설',
              'timeline_summary': '사건 경과의 연결 근거',
              'information_impact': '개인정보·외부 영향의 검토 범위', 'key_indicators': 'IOC 확인·적용 범위',
              'response_actions': '우선 대응 조치 · 우선순위와 기대 효과'}
    for group, title in titles.items():
        if group not in fields: continue
        explanations.append(heading(title))
        for entry in fields[group]['entries']:
            rows = []
            refs = []
            details = entry.get('cell_details', [''] * len(entry['cells']))
            for i, (label, value, detail) in enumerate(zip(cell_labels(group), entry['cells'], details)):
                rows.append([label, value + ('\n' + detail if detail else '')])
                refs.append([[], entry_refs(entry, i)])
            assessment = entry.get('assessment')
            if assessment:
                rows.append(['평가 근거', assessment['rationale']]); refs.append([[], deepcopy(entry.get('citation_refs', []))])
            if entry.get('unavailable_reason'):
                rows.append(['미확인 사유', entry['unavailable_reason']]); refs.append([[], []])
            explanations.append(dict(kind='table', headers=['항목', '판단·근거·확인 범위'], rows=rows,
                                     cell_citation_refs=refs, widths=[23, 77]))
    result.append(page('T01', '별첨 1. 판단별 해설과 대응', explanations, 'technical'))

    evidence_details = []
    observations = {(o['id'], o['version']): o for o in state.get('observations', [])}
    for number, entry in enumerate(fields['key_evidence']['entries'], 1):
        evidence_details.append(heading('근거 ' + str(number) + ' · ' + entry['cells'][0]))
        evidence_details.append(authored_table('key_evidence', {'entries': [entry]}, detailed=True))
        selected = []
        origins = set()
        for ref in entry.get('citation_refs', []):
            observation = observations.get((ref['id'], ref.get('version')))
            if not observation or ref['kind'] != 'observation': continue
            origin = (observation.get('source_ref', {}).get('id'), observation.get('field_pointer'),
                      observation.get('byte_start'), observation.get('byte_end'))
            literal = observation.get('literal', '')
            # A representative quote is an entire retained observation, never a
            # clipped or model-rewritten original. Long originals stay in EX02.
            if origin in origins or not literal or len(literal) > 600: continue
            origins.add(origin); selected.append((literal, ref))
            if len(selected) == 2: break
        for literal, ref in selected:
            evidence_details.append(dict(kind='table', headers=['구분', '보존 원문'],
                rows=[['대표 인용', literal]], literal=True, widths=[20, 80]))
            evidence_details.append(dict(kind='note', text='대표 인용의 출처·전체 원문', citation_refs=[ref]))
    result.append(page('T02', '별첨 2. 핵심 근거 해설', evidence_details, 'technical'))
    methodology = [heading('시각 해석'), deepcopy(blocks['timeline']),
                   heading('시각 미확정 사항'), deepcopy(blocks['undated_events']),
                   heading('종합 해석과 대안'), deepcopy(blocks['synthesis']),
                   heading('조사·판단 적용 범위'), deepcopy(blocks['scope']), deepcopy(blocks['certainty_note'])]
    result.append(page('T03', '별첨 3. 시간 해석·대안·조사 범위', methodology, 'technical'))
    return result
