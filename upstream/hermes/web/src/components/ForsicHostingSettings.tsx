import {useEffect, useState} from 'react';
import {fetchJSON} from '@/lib/api';
import ForsicSetup, {type SetupStatus} from './ForsicSetup';

/** Existing setup contract, embedded without a duplicate model editor. */
export function ForsicHostingSettings(){
  const [status,setStatus]=useState<SetupStatus|null>(null);
  const [error,setError]=useState(false);
  const [attempt,setAttempt]=useState(0);
  const [saved,setSaved]=useState(false);
  useEffect(()=>{let live=true;setError(false);void fetchJSON<SetupStatus>('/api/plugins/forsic/setup').then(value=>{if(live)setStatus(value);}).catch(()=>{if(live)setError(true);});return()=>{live=false;};},[attempt]);
  if(error)return <div role="alert"><p>자료·접속 설정을 불러오지 못했어요.</p><button type="button" onClick={()=>setAttempt(value=>value+1)}>다시 불러오기</button></div>;
  if(!status)return <p role="status">자료 폴더와 접속 설정을 불러오고 있어요.</p>;
  return <div className="forsic-hosting-settings">{saved&&<p className="forsic-settings-notice" role="status">저장했어요. 서버를 다음에 시작할 때 적용됩니다.</p>}<ForsicSetup mode="hosting" initial={status} onSaved={value=>{setStatus(value);setSaved(true);}}/></div>;
}
