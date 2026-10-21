import {useTenant} from '@/contexts/tenant-context';
import {useEffect, useRef, useState} from 'react';
import {Link, useSearchParams} from 'react-router';
import {Bot, FolderOpen, Globe, Bell, Box, Package, Wrench, BookOpen, ArrowLeft, ChevronRight} from 'lucide-react';
import {ForsicManagement} from '@/components/ForsicManagement';
import {ForsicHostingSettings} from '@/components/ForsicHostingSettings';
import {PluginSlot} from '@/plugins';
import {useProfileScope} from '@/contexts/useProfileScope';
import {usePageHeader} from '@/contexts/usePageHeader';
import './forsic-management-page.css';

const categories = [
  {id:'models',label:'조사·검토 모델',description:'포식이와 렌의 모델, API 키, 문맥 길이',icon:Bot,group:'기본 설정'},
  {id:'hosting',label:'자료 폴더·접속',description:'기본 자료 폴더와 포식이에 접속할 범위',icon:FolderOpen,group:'기본 설정'},
  {id:'gti',label:'외부 조회',description:'VT / GTI 조회와 외부 연결',icon:Globe,group:'기본 설정'},
  {id:'telegram',label:'텔레그램 알림',description:'질문과 주요 조사 결과를 받을 연결',icon:Bell,group:'기본 설정'},
  {id:'environments',label:'분석 환경',description:'분석 도구가 실행될 환경과 적용 범위',icon:Box,group:'확장 관리'},
  {id:'packs',label:'확장팩',description:'추가 도구·스킬 가져오기와 사용 선택',icon:Package,group:'확장 관리'},
  {id:'builder',label:'도구 제작',description:'소스와 필요한 기능으로 확장팩 준비',icon:Wrench,group:'확장 관리'},
  {id:'skills',label:'스킬 제안',description:'포식이가 제안한 반복 절차 검토',icon:BookOpen,group:'확장 관리'},
] as const;
type Category = typeof categories[number]['id'];
const connectionSections = {models:'llm',gti:'gti',telegram:'telegram'} as const;
function isConnection(value:Category):value is keyof typeof connectionSections {return value in connectionSections;}
function isManagement(value:Category):value is 'environments'|'packs'|'builder'|'skills' {return ['environments','packs','builder','skills'].includes(value);}

export default function ForsicManagementPage(){
  const tenant=useTenant(); const {profile}=useProfileScope(); const [params,setParams]=useSearchParams(); const {setTitle}=usePageHeader();
  const selected=categories.find(item=>item.id===params.get('section'))||categories[0];
  const category=selected.id;
  const [visited,setVisited]=useState({connections:isConnection(category),hosting:category==='hosting',management:isManagement(category)});
  const lastConnection=useRef<keyof typeof connectionSections>('models');
  const lastManagement=useRef<'environments'|'packs'|'builder'|'skills'>('environments');
  if(isConnection(category))lastConnection.current=category;
  if(isManagement(category))lastManagement.current=category;
  useEffect(()=>setVisited(previous=>({connections:previous.connections||isConnection(category),hosting:previous.hosting||category==='hosting',management:previous.management||isManagement(category)})),[category]);
  const caseId=params.get('case')||undefined; const resume=params.get('resume')||'';
  const back=new URLSearchParams({profile}); if(tenant)back.set('tenant_id',tenant.tenant_id); if(caseId)back.set('case',caseId); if(resume)back.set('resume',resume);else back.set('view','history');
  useEffect(()=>{setTitle('설정');return()=>setTitle(null);},[setTitle]);
  function select(id:Category){const next=new URLSearchParams(params);next.set('section',id);setParams(next,{replace:true});}
  return <main className="forsic-management-page forsic-settings-page" aria-label="설정">
    <header className="forsic-settings-header"><div><h1>설정</h1><p>연결부터 분석 환경까지 한곳에서 관리합니다.</p></div><Link to={`${resume?'/chat':'/forsic'}?${back}`}><ArrowLeft size={16} aria-hidden="true"/>{resume?'대화로 돌아가기':'사건 목록으로'}</Link></header>
    <div className="forsic-settings-layout">
      <nav className="forsic-settings-nav" aria-label="설정 항목">{['기본 설정','확장 관리'].map(group=><div key={group}><h2>{group}</h2>{categories.filter(item=>item.group===group).map(item=><button key={item.id} type="button" aria-current={category===item.id?'page':undefined} onClick={()=>select(item.id)}><item.icon size={19} aria-hidden="true"/><span>{item.label}</span><ChevronRight size={15} aria-hidden="true"/></button>)}</div>)}</nav>
      <div className="forsic-settings-content">
        <header className="forsic-settings-section-heading"><h2>{selected.label}</h2><p>{selected.description}</p></header>
        {(visited.connections||isConnection(category))&&<div hidden={!isConnection(category)}><PluginSlot name="settings-connections" componentProps={{section:connectionSections[lastConnection.current]}} fallback={<p role="status">연결 설정을 불러오고 있어요. 계속 표시되면 화면을 새로고침해 주세요.</p>}/></div>}
        {(visited.hosting||category==='hosting')&&<div hidden={category!=='hosting'}><ForsicHostingSettings/></div>}
        {(visited.management||isManagement(category))&&<div hidden={!isManagement(category)}><ForsicManagement profile={profile} caseId={caseId} section={lastManagement.current} onSectionChange={select}/></div>}
      </div>
    </div>
  </main>;
}
