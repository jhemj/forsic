import {useTenant} from '@/contexts/tenant-context';
import {useEffect} from 'react';
import {Link, useSearchParams} from 'react-router';
import {ForsicManagement} from '@/components/ForsicManagement';
import {useProfileScope} from '@/contexts/useProfileScope';
import {usePageHeader} from '@/contexts/usePageHeader';
import './forsic-management-page.css';

export default function ForsicManagementPage(){
  const tenant=useTenant();const {profile}=useProfileScope();const [params]=useSearchParams();const {setTitle}=usePageHeader();
  const caseId=params.get('case')||undefined;const resume=params.get('resume')||'';
  const back=new URLSearchParams({profile});if(tenant)back.set('tenant_id',tenant.tenant_id);if(caseId)back.set('case',caseId);if(resume)back.set('resume',resume);else back.set('view','history');
  useEffect(()=>{setTitle('관리 설정');return()=>setTitle(null);},[setTitle]);
  return <main className="forsic-management-page" aria-label="관리 설정 작업공간">
    <header><div><h1>분석 환경과 확장팩 관리</h1><p>새 사건의 기본 구성과 현재 사건의 고정 구성을 확인하고, 준비한 변경을 검토해 적용합니다.</p></div><Link to={`${resume?'/chat':'/forsic'}?${back}`}>{resume?'대화로 돌아가기':'사건 목록으로 돌아가기'}</Link></header>
    <ForsicManagement profile={profile} caseId={caseId}/>
  </main>;
}
