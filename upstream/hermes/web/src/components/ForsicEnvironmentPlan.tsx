import {useState} from 'react';

interface Candidate {key:string;kind:'apt'|'python'|'release';name:string;current:string;version:string;hashes:string[];selectable:boolean;reason:string;details:unknown}
interface Props {sources:unknown[];environmentId:string;planText:string;onPlanChange:(text:string)=>void;disabled:boolean}
function object(value:unknown):Record<string,unknown>{return value&&typeof value==='object'&&!Array.isArray(value)?value as Record<string,unknown>:{};}
function text(value:unknown,fallback='미확인'){return typeof value==='string'&&value?value:fallback;}
function readPlan(value:string){const plan=value?JSON.parse(value):{};if(!plan||typeof plan!=='object'||Array.isArray(plan))throw new Error('변경 계획 JSON을 먼저 확인해 주세요.');return plan as Record<string,unknown>;}
function latestReceipt(sources:unknown[],environmentId:string){return sources.map(object).filter(item=>item.environment_id===environmentId&&('apt' in item||'python' in item||'release_binaries' in item)).sort((left,right)=>Number(right.checked_at||0)-Number(left.checked_at||0))[0];}
function candidates(receipt:Record<string,unknown>,environmentId:string):Candidate[]{
  const result:Candidate[]=[];
  for(const [family,kind] of [['apt','apt'],['python','python'],['release_binaries','release']] as const){
    const section=object(receipt[family]);
    for(const [name,raw] of Object.entries(object(section.packages))){
      const item=object(raw);const version=text(item.candidate,'');const current=text(kind==='apt'?item.installed:item.current);
      const checked=kind==='apt'?section.state==='checked':item.state==='checked';
      const hashes=Array.isArray(item.wheels)?[...new Set(item.wheels.map(wheel=>object(wheel).sha256).filter((sha):sha is string=>typeof sha==='string'&&/^[a-f0-9]{64}$/.test(sha)))]:[];
      const reason=!checked?'후보 확인 실패·오프라인':!version||version==='(none)'?'사용 가능한 후보 미확인':version===current?'조회한 설치 버전과 동일':kind==='apt'&&typeof item.dependency_plan_exit_code==='number'&&item.dependency_plan_exit_code!==0?'의존성 변경 계획 확인 실패':kind==='release'?'지원 플랫폼·asset SHA256을 고정한 binary_lock 검토 필요':kind==='python'&&!hashes.length?'검증 가능한 wheel SHA256 없음':'';
      result.push({key:`${environmentId}:${kind}:${name}:${version}`,kind,name,current,version,hashes,selectable:!reason,reason,details:item});
    }
  }
  return result;
}
export function ForsicEnvironmentPlan({sources,environmentId,planText,onPlanChange,disabled}:Props){
  const [selected,setSelected]=useState<Record<string,boolean>>({});const [error,setError]=useState('');
  const [checkName,setCheckName]=useState('');const [executable,setExecutable]=useState('');const [argumentsText,setArgumentsText]=useState('');const [expected,setExpected]=useState('');
  const receipt=latestReceipt(sources,environmentId);const rows=receipt?candidates(receipt,environmentId):[];const picked=rows.filter(item=>selected[item.key]&&item.selectable);
  let checks:unknown[]=[];let changes:Record<string,unknown>={};
  try{changes=readPlan(planText);if(Array.isArray(changes.checks))checks=changes.checks;}catch{/* Keep the advanced draft editable. */}
  function savePlan(update:(plan:Record<string,unknown>)=>Record<string,unknown>){try{onPlanChange(JSON.stringify(update(readPlan(planText)),null,2));setError('');}catch{setError('변경 계획 JSON을 먼저 확인해 주세요. 입력은 보관했어요.');}}
  function applySelection(){savePlan(plan=>{const apt={...object(plan.apt)};const python=Array.isArray(plan.python)?[...plan.python]:[];for(const item of picked){if(item.kind==='apt')apt[item.name]=item.version;else if(item.kind==='python'){const index=python.findIndex(entry=>object(entry).name===item.name);const entry={name:item.name,version:item.version,sha256:item.hashes};if(index<0)python.push(entry);else python[index]=entry;}}return {...plan,apt,python,checks:Array.isArray(plan.checks)?plan.checks:[]};});}
  function addCheck(){if(argumentsText.split(/\r?\n/).some(value=>['--help','--version','-h','-V'].includes(value))){setError('버전·도움말 확인 대신 실제 합성 입력을 처리하는 검사를 작성해 주세요.');return;}savePlan(plan=>({...plan,checks:[...(Array.isArray(plan.checks)?plan.checks:[]),{name:checkName.trim(),argv:[executable.trim(),...argumentsText.split(/\r?\n/).filter(Boolean)],expected_stdout:expected}]}));}
  function candidateCard(item:Candidate){return <article className="forsic-update-candidate" key={item.key}>
        <label><input type="checkbox" aria-label={`${item.name} ${item.version||'미확인'} 업데이트 선택`} checked={Boolean(selected[item.key])} disabled={disabled||!item.selectable} onChange={event=>setSelected({...selected,[item.key]:event.target.checked})}/><strong>{item.name}</strong><small>{item.kind==='release'?'공개 release':item.kind}</small></label>
        <p>설치: {item.current} → 후보: {item.version||'미확인'}</p>{item.reason&&<small>{item.reason}</small>}
        {item.kind==='python'&&item.selectable&&<small>wheel 내용 해시 {item.hashes.length}개 · 플랫폼과 전이 의존성의 정확한 버전·해시는 준비 전에 별도 확인하세요.</small>}
        <details><summary>조회 근거·의존성·파일</summary><pre>{JSON.stringify(item.details,null,2)}</pre></details>
      </article>;}
  return <div className="forsic-environment-plan">
    <fieldset><legend>업데이트 후보 선택</legend>
      {!receipt&&<p className="forsic-management-caption">업데이트 확인을 누르면 선택한 환경의 실제 조회 결과가 표시됩니다.</p>}
      {receipt&&(['apt','python','release_binaries'] as const).map(family=>{const state=object(receipt[family]).state;return state&&state!=='checked'?<p key={family} role="status">{family==='release_binaries'?'공개 release':family} · {state==='partial_or_offline'?'일부 확인 실패·오프라인':state==='failed_or_offline'?'확인 실패·오프라인':'확인되지 않음'}</p>:null;})}
      {receipt&&<p>선택 가능한 후보 {rows.filter(item=>item.selectable).length}개</p>}
      {rows.filter(item=>item.selectable).map(candidateCard)}
      {rows.some(item=>!item.selectable)&&<details><summary>동일 버전·추가 검토 항목 {rows.filter(item=>!item.selectable).length}개</summary><div className="forsic-candidate-secondary">{rows.filter(item=>!item.selectable).map(candidateCard)}</div></details>}
      <button disabled={disabled||!picked.length} onClick={applySelection}>선택한 후보 {picked.length}개를 변경 계획에 반영</button>
    </fieldset>
    <details className="forsic-functional-check-editor"><summary>기능 검사 만들기 · 고급 설정 · 등록된 검사 {checks.length}개</summary>
    <fieldset><legend>작은 합성 입력의 기능 검사</legend><p className="forsic-management-caption">버전·도움말 확인만으로 기능 검사를 대신하지 않습니다. 검사 명령은 증거 없는 새 환경에서 실행됩니다.</p>
      <label>검사 이름<input aria-label="기능 검사 이름" value={checkName} disabled={disabled} onChange={event=>setCheckName(event.target.value)}/></label>
      <label>실행 파일<input aria-label="기능 검사 실행 파일" value={executable} disabled={disabled} onChange={event=>setExecutable(event.target.value)} placeholder="컨테이너 안의 실행 파일"/></label>
      <label>명령 인수 (한 줄에 하나)<textarea aria-label="기능 검사 인수" value={argumentsText} disabled={disabled} onChange={event=>setArgumentsText(event.target.value)}/></label>
      <label>기대하는 실제 출력<textarea aria-label="기능 검사 기대 출력" value={expected} disabled={disabled} onChange={event=>setExpected(event.target.value)}/></label>
      <button disabled={disabled||!checkName.trim()||!executable.trim()||!expected} onClick={addCheck}>이 기능 검사를 계획에 추가</button>
      <p>{checks.length}개 검사 등록</p>{checks.map((check,index)=><div key={index}><pre>{JSON.stringify(check,null,2)}</pre><button disabled={disabled} onClick={()=>savePlan(plan=>({...plan,checks:checks.filter((_,position)=>position!==index)}))}>검사 {index+1} 제거</button></div>)}
    </fieldset>
    </details>
    {error&&<p role="alert">{error}</p>}
    <details><summary>고급 편집 · 전체 변경 계획 JSON</summary><label>선택한 변경 계획<textarea aria-label="환경 변경 계획" value={planText} disabled={disabled} onChange={event=>onPlanChange(event.target.value)} placeholder={'{"apt":{},"python":[],"checks":[]}'}/></label></details>
    <details><summary>선택한 변경 요약</summary><pre>{JSON.stringify(changes,null,2)}</pre></details>
    {!!planText&&!checks.length&&<p role="status">환경을 준비하려면 변경한 기능을 확인하는 검사를 계획에 추가하세요.</p>}
  </div>;
}
