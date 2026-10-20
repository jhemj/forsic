"""Local report-only hydration of bounded originals; never part of judgment state."""
import json


def append_sensitive(view, appendix):
    """Append only to the existing technical appendix after safe projection is saved."""
    if not appendix.get('total_findings') and not appendix.get('limitations'):
        return
    originals=bool(appendix.get('entries'))
    blocks=[{'kind':'heading','level':2,'id':'sensitive-originals','text':'민감정보 후보 원문 별첨'},
        {'kind':'callout','label':'로컬 전용 · 민감 원문 포함' if originals else '원문 미수록 · 범위와 한계 확인',
         'text':('이 별첨은 선택된 근거에 연결한 원문 일부를 포함합니다. 자동 원격 보고서 전송 대상에서 제외됩니다. ' if originals else '선택한 탐지 결과의 원문은 이 별첨에 수록되지 않았습니다. ')+
                '후보 수는 실제 사람 수·유효 자격증명 수·외부 반출량이 아닙니다.'},
        {'kind':'paragraph','text':f"보존·관측 후보 {appendix['total_findings']}개 / 수록 {appendix['returned_findings']}개 / 생략 {appendix['omitted_findings']}개 / 생략 원문 {appendix['omitted_bytes']}바이트. 좌표는 아래에 명시한 원문 또는 보존 scalar의 인코딩 기준이며 디스크 이미지 오프셋과 구별합니다."}]
    if appendix.get('counts_are_lower_bounds'):
        blocks.append({'kind':'paragraph','text':'후보 수는 관측한 하한입니다. 제한·실패·미검토 범위로 전체 후보 수와 생략 원문 규모는 확정하지 못했습니다.'})
    if appendix.get('unavailable_findings'):
        blocks.append({'kind':'paragraph','text':f"원문 검증 불가로 미수록한 후보 {appendix['unavailable_findings']}개. 위 생략 건수와 별도로 표시합니다."})
    if 'unretained_findings' in appendix:
        value=appendix['unretained_findings']
        blocks.append({'kind':'paragraph','text':'원문 미보존 후보: '+('미상' if value is None else str(value)+'개')+'. 보존된 후보와 별도로 확인해야 합니다.'})
    rows=[]
    for entry in appendix['entries']:
        source=entry['source_ref']
        location=json.dumps(entry.get('original_location',{}),ensure_ascii=False,sort_keys=True)
        rows.append([entry['finding_id']+'\n'+entry['input_id'],
            source['id']+'\n버전 '+source['version']+'\n'+source['pointer']+'\n'+location+'\n역할 '+entry.get('source_role',''),
            entry['excerpt'],
            str(entry['encoding'])+'\n발췌 ['+str(entry['excerpt_byte_start'])+','+str(entry['excerpt_byte_end'])+')\n일치 ['+str(entry['match_byte_start'])+','+str(entry['match_byte_end'])+')'])
    if rows:blocks.append({'kind':'table','headers':['후보 / 입력','출처와 원래 위치','원문 발췌','바이트 범위'],'rows':rows,'widths':[15,30,35,20]})
    blocks.extend({'kind':'paragraph','text':'한계 · '+str(x)} for x in appendix.get('limitations',[]))
    target=next((p for p in view['pages'] if p['id']=='appendix-D'),None)
    if target is None:
        target={'id':'appendix-D','title':'부록 D. 개인정보 및 외부 영향 상세','section':'appendix','blocks':[]}
        view['pages'].append(target)
    target['blocks'].extend(blocks)


def summary(appendix):
    """Metadata-only channel; excerpts and original locations stay in local report files."""
    return {k:appendix[k] for k in ('schema','total_findings','returned_findings','omitted_findings','omitted_bytes','contains_sensitive_originals','counts_are_lower_bounds','unretained_findings','unavailable_findings') if k in appendix}
