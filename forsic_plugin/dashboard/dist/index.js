(() => {
  const { React: R, fetchJSON: nativeFetch, authedFetch } = window.__HERMES_PLUGIN_SDK__;
  const h = R.createElement;
  const base = '/api/plugins/forsic';
  // The plugin ships independently; keep parity with the native UUID utility.
  function requestUUID(source=globalThis.crypto) {
    if(typeof source?.randomUUID==='function')return source.randomUUID();
    if(typeof source?.getRandomValues!=='function')throw new Error('Secure random generation is unavailable in this browser.');
    const bytes=new Uint8Array(16);source.getRandomValues(bytes);
    bytes[6]=(bytes[6]&0x0f)|0x40;bytes[8]=(bytes[8]&0x3f)|0x80;
    const hex=Array.from(bytes,value=>value.toString(16).padStart(2,'0')).join('');
    return `${hex.slice(0,8)}-${hex.slice(8,12)}-${hex.slice(12,16)}-${hex.slice(16,20)}-${hex.slice(20)}`;
  }
  const selectedCase = () => new URLSearchParams(window.location?.search || '').get('case') || '';
  function workspaceRoute(location=window.location || {}) {
    const params=new URLSearchParams(location.search || '');
    if (params.get('view') === 'new') return {section:'new',panel:'intake'};
    if (location.pathname === '/sessions') return {section:'history',panel:'conversations'};
    if (location.pathname === '/chat') return {section:params.get('resume') || params.get('case') ? 'current' : 'new',panel:'chat'};
    // Keep old bookmarks working, but never combine the library and current board.
    if (params.get('view') === 'history' || ['#case-library','#investigations'].includes(location.hash))
      return {section:'history',panel:params.get('panel') === 'library' || location.hash === '#case-library' ? 'library' : 'cases'};
    return {section:'current',panel:params.get('panel') === 'reports' ? 'reports' : 'board'};
  }
  function workspaceURL(section,caseId='',panel='') {
    const query=new URLSearchParams({view:section});
    const scope=new URLSearchParams(window.location?.search || '');
    for (const key of ['tenant_id','profile']) if(scope.get(key))query.set(key,scope.get(key));
    if (caseId) query.set('case',caseId);
    if (panel) query.set('panel',panel);
    return '/forsic?'+query.toString();
  }
  function useRoute() {
    const [route,setRoute]=R.useState(()=>workspaceRoute());
    R.useEffect(()=>{const update=()=>setRoute(workspaceRoute());
      window.addEventListener('popstate',update); window.addEventListener('hashchange',update);
      return ()=>{window.removeEventListener('popstate',update); window.removeEventListener('hashchange',update);};
    },[]);
    return route;
  }
  function useConversation(caseId) {
    const [session,setSession]=R.useState('');
    R.useEffect(()=>{let alive=true; setSession('');
      if(caseId) nativeFetch(base+'/investigations').then(value=>{
        const item=value.cases.find(item=>item.case_id === caseId);
        if(alive) setSession(item?.resume_session_id || '');
      }).catch(()=>{});
      return ()=>{alive=false;};
    },[caseId]);
    return session ? conversationURL(caseId,session) : '';
  }
  function scopedURL(url, caseId=selectedCase()) {
    if(url.startsWith('/settings?')) {
      const params=new URLSearchParams(url.split('?')[1]),scope=new URLSearchParams(window.location?.search || '');
      for(const key of ['tenant_id','profile','case','resume'])if(scope.get(key))params.set(key,scope.get(key));
      return '/settings?'+params;
    }
    if(!url.startsWith(base))return url;
    const [path,query='']=url.split('?'),params=new URLSearchParams(query);
    const scope=new URLSearchParams(window.location?.search || '');
    for(const key of ['tenant_id','profile'])if(scope.get(key))params.set(key,scope.get(key));
    if(caseId && !path.includes('/investigations'))params.set('case_id',caseId);
    return path+(params.size?'?'+params:'');
  }
  const fetchJSON = (url, options) => nativeFetch(scopedURL(url), options);
  const names = { forsic_case: '조사 범위를 확인하', forsic_list: '자료 목록을 살펴보', forsic_read: '원문을 읽', forsic_read_bytes: '원문 구간을 읽', forsic_search: '관련 기록을 찾', forsic_hash: '증거의 해시를 계산하', forsic_verify: '증거 무결성을 검증하', forsic_image_info: '이미지 정보를 살펴보', forsic_image_files: '이미지 안의 자료를 살펴보', forsic_note: '질문에 대한 답을 정리하', forsic_report: '조사 보고서를 작성하' };
  function caseTarget(location=window.location || {}) {
    const params=new URLSearchParams(location.search || '');
    const caseId=params.get('case') || '', session=params.get('resume') || '';
    if(location.pathname==='/chat' && (params.get('fresh')==='1' || !caseId && !session)) return null;
    if (location.pathname==='/forsic' && (params.get('view')==='new' || !caseId)) return null;
    return {caseId,session};
  }
  function useCase() {
    const [data, setData] = R.useState(null), [error, setError] = R.useState('');
    R.useEffect(() => {
      let alive = true;
      let lastTarget = '';
      const refresh = async () => { try {
        const target=caseTarget(), identity=JSON.stringify(target);
        if (identity !== lastTarget) {setData(null); lastTarget=identity;}
        if (!target) {if(alive){setData(null);setError('');}return;}
        const {caseId:id,session}=target;
        const query = new URLSearchParams();
        if(id) query.set('case_id',id);
        if(session) query.set('session_id',session);
        const v=await nativeFetch(base+'/status?'+query);
        if(id && v.case?.case_id!==id) throw new Error('case mismatch');
        if (alive && identity === JSON.stringify(caseTarget())) { setData(v); setError(''); }
      } catch { if (alive) setError('사건 기록에 연결하지 못했어요. 잠시 뒤 다시 확인해 주세요.'); } };
      refresh(); const timer = setInterval(refresh, 2500);
      return () => { alive = false; clearInterval(timer); };
    }, []);
    return { data, error };
  }
  names.forsic_cases = '이전 사례를 살펴보';
  names.forsic_intake = '파일의 형식과 구성을 확인하';
  names.forsic_intel = '외부 위협 평판을 대조하';
  names.forsic_review_response = '검토 답변을 기록하';
  names.forsic_reporting = '질문과 근거를 보고서로 정리하';
  names.forsic_skill = '이번 조사에 필요한 절차를 살펴보';
  const moods = {idle:'대기', searching:'탐색 중', reading:'원문 읽는 중', verifying:'무결성 확인 중', thinking:'기록 연결 중', writing:'정리 중', found:'관련 기록 발견', uncertain:'확인 결과 검토', complete:'응답 완료', offline:'상태 연결 확인 중'};
  function activityState(data, error, now = Date.now()/1000) {
    if (error) return {mode:'offline', active:false, heading:'상태 연결 확인 중'};
    const all = [...(data?.events || [])].sort((a,b) => b.time-a.time);
    const latest = all.find(e => ['turn_start','turn_complete','tool_start','tool_result','pre_api_request','post_api_request','api_request_error','pre_auxiliary_call','post_auxiliary_call'].includes(e.kind));
    const events = all.filter(e => e.session === latest?.session);
    const finished = events.find(e => e.kind === 'turn_complete');
    const afterFinish = e => !finished || e.time > finished.time;
    const start = events.find(e => e.kind === 'tool_start' && afterFinish(e) && !events.some(r => r.kind === 'tool_result' && r.data.started_id === e.id));
    const request = events.find(e => e.kind === 'pre_api_request' && afterFinish(e) && !events.some(r => ['post_api_request','api_request_error'].includes(r.kind) && r.data.api_request_id === e.data.api_request_id));
    const auxiliary = events.find(e => e.kind === 'pre_auxiliary_call' && (!finished || e.time >= finished.time) && !events.some(r => r.kind === 'post_auxiliary_call' && r.time >= e.time && r.data.aux_task === e.data.aux_task && (!e.data.api_request_id || r.data.api_request_id === e.data.api_request_id)));
    const result = events.find(e => e.kind === 'tool_result');
    let mode = 'idle', heading = finished ? '응답 완료' : '대기';
    if (start) {
      const tool = start.data.tool, action = start.data.arguments?.action;
      mode = ['forsic_hash','forsic_verify'].includes(tool) ? 'verifying'
        : ['forsic_note','forsic_report','forsic_reporting','forsic_review_response'].includes(tool) || tool === 'forsic_cases' && action === 'save' ? 'writing'
        : ['forsic_read','forsic_read_bytes'].includes(tool) || tool === 'forsic_image_files' && ['read','read_bytes','stat'].includes(action) ? 'reading' : 'searching';
      heading = `${names[tool] || '자료를 확인하'}고 있어요`;
    } else if (request) {
      mode = 'thinking'; heading = '조사 내용 정리 중';
    } else if (auxiliary) {
      mode = 'thinking'; heading = auxiliary.data.aux_task === 'compression' ? '이전 기록 정리 중' : '조사 내용 점검 중';
    } else if (finished && now-finished.time >= 0 && now-finished.time < 6) {
      mode = 'complete';
    }
    // A fresh result is a brief reaction, never a lasting verdict or an intrusion score.
    if (!start && result && afterFinish(result) && now-result.time >= 0 && now-result.time < 6) {
      const r = result.data;
      const emptySearch = Array.isArray(r.matches) && r.matches.length === 0;
      if (r.error || r.exit_code != null && r.exit_code !== 0 || emptySearch) {
        mode = 'uncertain';
        if (!request) heading = emptySearch ? '해당 범위 검색 결과 없음' : '확인 실패';
      } else if (!r.reused && Array.isArray(r.matches) && r.matches.length > 0) {
        mode = 'found';
        if (!request) heading = '관련 기록 발견';
      }
    }
    const context = start || (request && result && events.find(e => e.id === result.data.started_id));
    const reactionId = ['found','uncertain'].includes(mode) ? result?.id : mode === 'complete' ? finished?.id : '';
    return {mode, reactionId, heading, active:!!(start || request || auxiliary), since:(start || request || auxiliary)?.time, context:context?.data};
  }
  function Lens({mode = 'idle', active = false}) {
    if (!moods[mode]) mode = 'idle';
    return h('div', {className:'forsic-companion', 'data-mood':mode, 'data-working':!!active},
      h('div', {className:'forsic-stage', role:'img', 'aria-label':'포식이 · ' + moods[mode]},
        h('div', {className:'forsic-activity-ring', 'aria-hidden':true}),
        h('div', {className:'forsic-ground', 'aria-hidden':true}),
        h('div', {className:'forsic-lens', 'aria-hidden':true},
          h('div', {className:'forsic-rotor'}), h('div', {className:'forsic-inner-ring'}),
          h('div', {className:'forsic-glass'},
            h('div', {className:'forsic-reflection'}), h('div', {className:'forsic-scan'}),
            h('svg', {className:'forsic-face', viewBox:'0 0 100 100', fill:'none'},
              h('g', {className:'forsic-brows', stroke:'currentColor', strokeWidth:2.7, strokeLinecap:'round'},
                h('path', {className:'forsic-brow left', d:'M31 36 Q36 33 42 35'}), h('path', {className:'forsic-brow right', d:'M58 35 Q64 33 69 36'})),
              h('g', {className:'forsic-eyes', fill:'currentColor'},
                h('ellipse', {className:'forsic-eye left', cx:37, cy:49, rx:3.8, ry:6.5}), h('ellipse', {className:'forsic-eye right', cx:63, cy:49, rx:3.8, ry:6.5})),
              h('path', {className:'forsic-happy-eyes', d:'M32 49 Q37 40 42 49 M58 49 Q63 40 68 49', stroke:'currentColor', strokeWidth:3, strokeLinecap:'round'}),
              h('path', {className:'forsic-mouth smile', d:'M44 64 Q50 70 56 64', stroke:'currentColor', strokeWidth:2.5, strokeLinecap:'round'}),
              h('path', {className:'forsic-mouth ponder', d:'M47 66 L54 65', stroke:'currentColor', strokeWidth:2.5, strokeLinecap:'round'}),
              h('path', {className:'forsic-mouth concern', d:'M44 68 Q50 63 56 68', stroke:'currentColor', strokeWidth:2.5, strokeLinecap:'round'})),
            h('div', {className:'forsic-focus', 'aria-hidden':true}, ...['tl','tr','bl','br'].map(c => h('i', {key:c, className:c}))))),
        h('div', {className:'forsic-sparks', 'aria-hidden':true}, h('i'),h('i'),h('i')),
        h('div', {className:'forsic-orbit-dots', 'aria-hidden':true}, h('i'),h('i'),h('i')),
        h('div', {className:'forsic-workmark', 'aria-hidden':true},
          h('svg', {viewBox:'0 0 24 24', fill:'none', stroke:'currentColor', strokeWidth:1.5, strokeLinecap:'round'},
            h('path', {className:'forsic-mark-search', d:'M15 15 L20 20 M17 10 A7 7 0 1 1 3 10 A7 7 0 1 1 17 10'}),
            h('path', {className:'forsic-mark-read', d:'M6 5 H18 V20 H6 Z M9 9 H15 M9 12 H15 M9 15 H13'}),
            h('path', {className:'forsic-mark-verify', d:'M9 4 L7 20 M17 4 L15 20 M4 9 H21 M3 15 H20'}),
            h('path', {className:'forsic-mark-write', d:'M5 19 L6 14 L16 4 L20 8 L10 18 Z M13 7 L17 11'})))),
      h('span', {className:'forsic-mood-label'}, h('i', {'aria-hidden':true}), moods[mode]));
  }
  function reviewScope() {
    if(window.location?.pathname!=='/chat')return null;
    const params=new URLSearchParams(window.location.search || '');
    if(params.get('fresh')==='1' || !params.get('resume'))return null;
    return {profile:params.get('profile') || '',sessionId:params.get('resume'),caseId:params.get('case') || ''};
  }
  function useReviewActivity() {
    const scope=reviewScope(), key=JSON.stringify(scope);
    const [activity,setActivity]=R.useState(null);
    const [selected,setSelected]=R.useState(null);
    R.useEffect(()=>{
      let expiry;
      const clear=()=>{clearTimeout(expiry);setActivity(null);};
      const inScope=value=>{
        const current=reviewScope();
        return !!current && JSON.stringify(current)===key && !!value &&
          value.profile===current.profile && value.sessionId===current.sessionId &&
          (!current.caseId || value.caseId===current.caseId);
      };
      const receive=event=>{
        const value=event.detail;
        if(!inScope(value))return;
        clearTimeout(expiry);
        const age=Date.now()-value.observedAt;
        if(document.hidden || value.active!==true || !value.caseId || !value.reviewId ||
          !Number.isFinite(age) || age<0 || age>=30000){setActivity(null);return;}
        setActivity({key,reviewId:value.reviewId});
        // The existing chat poll refreshes this signal. A lost publisher must
        // never leave an old independent review looking active indefinitely.
        expiry=setTimeout(clear,30000-age);
      };
      const selection=event=>{
        if(inScope(event.detail))setSelected(event.detail.selected===true ? key : null);
      };
      const visibility=()=>{if(document.hidden)clear();else request();};
      const route=()=>{clear();setSelected(null);request();};
      const request=()=>window.dispatchEvent(new CustomEvent('forsic:review-activity-request'));
      window.addEventListener('forsic:review-activity',receive);
      window.addEventListener('forsic:review-panel',selection);
      window.addEventListener('popstate',route);
      document.addEventListener('visibilitychange',visibility);
      request();
      return ()=>{
        clearTimeout(expiry);
        window.removeEventListener('forsic:review-activity',receive);
        window.removeEventListener('forsic:review-panel',selection);
        window.removeEventListener('popstate',route);
        document.removeEventListener('visibilitychange',visibility);
      };
    },[key]);
    return {generating:activity?.key===key,selected:!!scope && selected===key};
  }
  function Identity() {
    const {data,error}=useCase();
    const {mode,active,heading}=activityState(data,error);
    const review=useReviewActivity(), generating=review.generating && !error;
    const ren=generating || review.selected, label=generating?'독립 질문 생성 중':'독립 검토';
    const actor=ren?'ren':'forsic';
    return h('div',{className:'forsic-identity','data-character':actor,title:ren?'렌 · '+label+(active?' · 포식이 '+heading:''):heading},
      h('div',{className:'forsic-identity-character'},
        h('div',{className:'forsic-character-layer forsic-character-original','aria-hidden':ren},h(Lens,{mode,active})),
        h('div',{className:'forsic-character-layer forsic-character-ren','aria-hidden':!ren},
          h('img',{src:'/assets/ren-reviewer.svg',alt:'렌 · '+label,width:104,height:104,draggable:false}))),
      h('div',{className:'forsic-identity-copy',key:actor},
        h('strong',null,ren?'렌':'포식이'),h('small',null,ren?label:moods[mode])));
  }
  const conversationURL = (caseId,sessionId) => {
    const query=new URLSearchParams({case:caseId,resume:sessionId});
    const scope=new URLSearchParams(window.location?.search || '');
    for(const key of ['tenant_id','profile'])if(scope.get(key))query.set(key,scope.get(key));
    return '/chat?'+query;
  };
  function useDirectory() {
    const [items,setItems]=R.useState([]), [error,setError]=R.useState('');
    const [availability,setAvailability]=R.useState(null);
    R.useEffect(()=>{let alive=true;
      const refresh=async()=>{try {const v=await nativeFetch(base+'/investigations'); if(alive){setItems(v.cases);setAvailability(v.new_investigation || null);setError('');}}
        catch {if(alive){setAvailability(null);setError('조사 목록을 불러오지 못했어요.');}}};
      refresh();const timer=setInterval(refresh,5000);window.addEventListener('forsic:investigation-closed',refresh);return()=>{alive=false;clearInterval(timer);window.removeEventListener('forsic:investigation-closed',refresh);};
    },[]);
    return {items,error,availability};
  }
  function NewInvestigationAction({availability,error='',current=false}) {
    const allowed=availability?.allowed===true && !error;
    const capacity=!error && Number.isInteger(availability?.active_count) && Number.isInteger(availability?.limit)
      ? `동시 조사 ${availability.active_count} / ${availability.limit}` : '';
    const reason=error ? '조사 상태를 확인하지 못해 새 조사를 만들 수 없습니다.'
      : availability?.reason || (availability ? '새 조사를 시작할 수 없습니다. 조사 상태를 다시 확인해 주세요.' : '조사 상태 확인 중');
    return allowed
      ? h('a',{className:'forsic-action-secondary',href:workspaceURL('new'),'aria-current':current?'page':undefined,title:capacity?capacity+' · 일시정지·대기 포함':undefined},'+ 새 조사',capacity&&h('small',null,capacity))
      : h('button',{type:'button',className:'forsic-action-secondary',disabled:true,title:reason,'aria-label':'+ 새 조사 · '+reason},'+ 새 조사',capacity&&h('small',null,capacity));
  }
  function CloseInvestigationAction({active,onClosed}) {
    const [busy,setBusy]=R.useState(false), [error,setError]=R.useState('');
    const request=R.useRef(null), pending=R.useRef(false);
    if(!active?.closable || !active.goal_id || !active.resume_session_id)return null;
    async function close() {
      if(pending.current)return;
      if(!window.confirm(`‘${active.label || '진행 중인 조사'}’ 조사를 종료할까요?\n\n사용자 종료로 기록되며 조사 성공을 뜻하지 않습니다. 대화·증거·노트·보고서는 보존됩니다.`))return;
      pending.current=true;setBusy(true);setError('');
      try {
        const profile=new URLSearchParams(window.location?.search || '').get('profile') ?? (window.__HERMES_INITIAL_PROFILE__ || window.__HERMES_DASHBOARD_PROFILE__ || '');
        const key=JSON.stringify([profile,active.case_id,active.resume_session_id,active.goal_id]);
        if(request.current?.key!==key)request.current={key,id:requestUUID()};
        const result=await nativeFetch(base+'/investigations/'+encodeURIComponent(active.case_id)+'/close',
          {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({profile,request_id:request.current.id,session_id:active.resume_session_id,goal_id:active.goal_id,confirmed:true})});
        if(result.status!=='closed')throw new Error('Close not acknowledged');
        onClosed?.();
        window.dispatchEvent(new Event('forsic:investigation-closed'));
      } catch {setError('종료를 확인하지 못했어요. 실행 중인 작업이 있다면 멈춘 뒤 다시 시도하거나 화면을 새로고침해 조사 상태를 확인해 주세요.');}
      finally {pending.current=false;setBusy(false);}
    }
    return h('div',{className:'forsic-close-investigation'},
      h('button',{type:'button',className:'forsic-action-secondary',disabled:busy,onClick:close},busy?'종료 중…':'종료'),
      error&&h('p',{role:'alert'},error));
  }
  function InvestigationAdmissionNotice({availability,error='',onClosed}) {
    const active=availability?.active_case;
    // Native client navigation keeps this sidebar mounted; its route hook may
    // still hold the entry route after pushState. Read the current URL here.
    if(workspaceRoute().panel==='chat') {
      const target=caseTarget();
      return !error && active && (target?.caseId===active.case_id || !target?.caseId && target?.session===active.resume_session_id)
        ? h(CloseInvestigationAction,{active,onClosed}) : null;
    }
    if(availability?.allowed===true && !error)return active?.closable?h(CloseInvestigationAction,{active,onClosed}):null;
    return h('div',{className:'forsic-admission-notice',role:'status'},
      h('p',null,error ? '조사 상태를 확인하지 못해 새 조사를 만들 수 없습니다.'
        : availability?.reason || (availability ? '새 조사를 시작할 수 없습니다. 조사 상태를 다시 확인해 주세요.' : '조사 상태 확인 중')),
      active?.case_id && active?.resume_session_id && h('a',{href:conversationURL(active.case_id,active.resume_session_id)},'기존 조사 열기'),
      !error&&h(CloseInvestigationAction,{active,onClosed}));
  }
  function analysisTitle(item,session) {
    const order=[...(item.conversations || [])].sort((a,b)=>(item.session_ids || []).indexOf(a.id)-(item.session_ids || []).indexOf(b.id));
    const index=order.findIndex(c=>c.session_ids.includes(session));
    return index<0 ? '분석' : `${['첫','두','세','네','다섯','여섯','일곱','여덟','아홉','열'][index] || index+1} 번째 분석`;
  }
  function tokenCount(value) {
    if(!Number.isFinite(value) || value<0)return '—';
    if(value>=999950)return (value/1000000).toFixed(2).replace(/\.?0+$/,'')+'M';
    if(value>=1000)return (value/1000).toFixed(1).replace(/\.0$/,'')+'K';
    return String(Math.round(value));
  }
  function SessionUsage({usage,detail=false}) {
    if(!usage)return h('small',{className:'forsic-session-usage'},'사용량 기록 없음');
    const label='세션 누적 '+tokenCount(usage.total_tokens)+' 토큰';
    const description=`입력 ${tokenCount(usage.prompt_tokens)} 토큰 · 출력 ${tokenCount(usage.output_tokens)} 토큰 · 캐시 ${tokenCount(usage.cache_read_tokens)} 토큰`;
    return h(detail?'details':'small',{className:'forsic-session-usage',title:description},
      detail?h('summary',null,label):label,
      detail&&h('p',null,description));
  }
  function CaseConversations({item,currentSession}) {
    const [busy,setBusy]=R.useState(false), [error,setError]=R.useState('');
    const [requestId]=R.useState(()=>requestUUID());
    async function create() {
      if(busy)return;setBusy(true);setError('');
      try {const v=await nativeFetch(base+'/investigations/'+encodeURIComponent(item.case_id)+'/conversations',
        {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({request_id:requestId})});
        window.location.href=conversationURL(v.case_id,v.session_id);
      } catch {setError('대화를 만들지 못했어요. 같은 요청으로 다시 시도할 수 있어요.');setBusy(false);}
    }
    return h('section',{className:'forsic-conversation-section','aria-label':'이 사건의 대화'},
      h('div',{className:'forsic-section-heading'},h('h3',null,'대화'),
        h('button',{type:'button',className:'forsic-action-primary',onClick:create,disabled:busy},busy?'만드는 중…':'+ 새 대화')),
      h('div',{className:'forsic-conversations'},...(item.conversations || []).map(c=>
        h('a',{key:c.id,className:'forsic-nav-item',href:conversationURL(item.case_id,c.resume_session_id),
          'aria-current':(c.session_ids || []).includes(currentSession)?'page':undefined},
          h('span',{className:'forsic-conversation-label'},h('span',null,analysisTitle(item,c.resume_session_id)),h(SessionUsage,{usage:c.usage})),h('small',null,new Date(c.last_activity_at*1000).toLocaleDateString('ko-KR',{month:'short',day:'numeric'}))))),
      !item.conversations?.length && h('p',{className:'forsic-empty'},'대화 없음'),
      error&&h('p',{role:'status'},error));
  }
  function NavIcon({name}) {
    const paths={cases:'M3 7h7l2 2h9v11H3z M3 7V4h7l2 3',board:'M4 4h6v7H4z M14 4h6v4h-6z M4 15h6v5H4z M14 12h6v8h-6z',reports:'M6 3h9l4 4v14H6z M14 3v5h5 M9 12h7 M9 16h7',library:'M4 4h5v16H4z M9 4h5v16H9z M15 5l4-1 4 15-4 1z',settings:'M9 3h6l1 3 3 1 2 5-2 5-3 1-1 3H9l-1-3-3-1-2-5 2-5 3-1z M15 12a3 3 0 1 1-6 0 3 3 0 1 1 6 0'};
    return h('svg',{className:'forsic-nav-icon',viewBox:'0 0 24 24',width:18,height:18,fill:'none',stroke:'currentColor',strokeWidth:1.6,strokeLinecap:'round',strokeLinejoin:'round','aria-hidden':true},h('path',{d:paths[name]}));
  }
  function Sidebar() {
    const {data,error}=useCase(), {items,error:directoryError,availability}=useDirectory(), route=useRoute();
    const caseId=data?.case?.case_id || selectedCase(), item=items.find(c=>c.case_id===caseId);
    const params=new URLSearchParams(window.location?.search || ''), currentSession=route.panel==='chat'?params.get('resume'):'';
    const scope=data?.case?.scope || item?.scope || '';
    return h('aside',{className:'forsic-sidebar'},
      h('nav',{className:'forsic-global-nav','aria-label':'사건 탐색'},
        h('a',{className:'forsic-nav-item',href:workspaceURL('history'),title:'기존 조사를 찾아 대화 열기','aria-current':route.section==='history'&&route.panel!=='library'?'page':undefined},h(NavIcon,{name:'cases'}),'조사 목록'),
        h(NewInvestigationAction,{availability,error:directoryError,current:route.section==='new'})),
      h(InvestigationAdmissionNotice,{availability,error:directoryError}),
      scope && h('section',{className:'forsic-context-card','aria-label':'증거 경로'},
        h('small',null,'증거 경로'),h('strong',null,scope.split('/').filter(Boolean).at(-1) || scope),
        h('details',null,h('summary',null,'전체 경로 보기'),h('code',null,scope)),
        data?.case?.selected_files?.length>0 && h('small',null,'선택한 파일 ',data.case.selected_files.length,'개')),
      caseId && scope ? h('div',{className:'forsic-current-case'},
        h('header',{className:'forsic-case-heading'},h('small',null,'현재 사건'),h('h2',null,(data?.case?.question && data.case.question!=='질문 선택 대기' ? data.case.question : item?.question) || data?.case?.label || item?.label),h('code',null,caseId)),
        item && h(CaseConversations,{key:caseId,item,currentSession}),
        currentSession && item && h(SessionUsage,{detail:true,usage:item.conversations.find(c=>c.session_ids.includes(currentSession))?.usage}))
        : h('p',{className:'forsic-empty'},route.section==='new'?'원본 경로를 등록하면 사건과 첫 대화가 만들어집니다.':'사건을 선택하면 증거 경로와 대화가 여기에 표시됩니다.'),
      directoryError&&h('p',{role:'status'},directoryError),
      h('footer',{className:'forsic-sidebar-footer'},
        h('a',{className:'forsic-nav-item',href:workspaceURL('history','','library'),'aria-current':route.panel==='library'?'page':undefined,title:'참고할 보관 사례 찾아보기'},h(NavIcon,{name:'library'}),'저장한 사례'),
        h('div',{id:'forsic-model-target'})));
  }
  function NewCase() {
    const [path,setPath]=R.useState(''),[busy,setBusy]=R.useState(false),[error,setError]=R.useState('');
    const [request,setRequest]=R.useState(null);
    async function submit(event) {
      event.preventDefault(); if(busy || !path.trim())return;
      const attempt=request?.path===path.trim()?request:{path:path.trim(),request_id:requestUUID()};
      setRequest(attempt);setBusy(true);setError('');
      try {const result=await nativeFetch(base+'/investigations',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(attempt)});
        window.location.href=conversationURL(result.case_id,result.session_id);
      } catch {setError('경로를 등록하지 못했어요. 접근 가능한 파일·폴더의 전체 경로인지 확인해 주세요.');setBusy(false);}
    }
    return h('section',{className:'forsic-records forsic-new-case'},
      h('header',{className:'forsic-workspace-heading'},h('div',null,h('small',null,'새 사건'),h('h1',null,'증거 등록'),h('p',null,'조사할 파일이나 폴더의 전체 경로를 입력하세요.'))),
      h('form',{onSubmit:submit},h('label',{htmlFor:'forsic-evidence-path'},'증거 경로'),
        h('input',{id:'forsic-evidence-path',value:path,onChange:e=>setPath(e.target.value),placeholder:'/home/사용자/증거/사건폴더',required:true,disabled:busy,autoComplete:'off',spellCheck:false}),
        h('p',null,'원본은 읽기 전용으로 확인합니다. 조사 목적은 다음 대화에서 정할 수 있어요.'),
        error&&h('p',{role:'alert'},error),
        h('button',{type:'submit',className:'forsic-action-primary',disabled:busy||!path.trim()},busy?'경로 확인 중…':'사건 만들고 대화 시작')),
      h('div',{className:'forsic-intake-explanation'},h('h2',null,'사건과 대화'),h('p',null,'같은 증거에 대한 추가 질문은 사건 안에서 새 대화로 이어가세요. 다른 증거를 조사할 때는 새 사건을 만듭니다.')));
  }
  function settingsBody(value, secret, clearKey, telegram={}, renKey=null, llmKey=null) {
    return {llm_base_url:value.llm.base_url, llm_model:value.llm.model,
      ...(llmKey !== null || !value.llm.key_configured ? {llm_api_key:llmKey ?? ''} : {}),
      ...(value.llm.context_changed ? {llm_context_length:value.llm.context_length} : {}),
      ren_llm_enabled:!!value.ren_llm?.enabled, ren_llm_base_url:value.ren_llm?.base_url || '', ren_llm_model:value.ren_llm?.model || '',
      ...(renKey !== null || !value.ren_llm?.key_configured ? {ren_llm_api_key:renKey ?? ''} : {}),
      jev_base_url:value.jev.base_url, gti_enabled:value.gti.enabled,
      gti_public_network:value.gti.allow_public_network_indicators,
      gti_api_key:secret, clear_gti_key:clearKey,
      telegram_assistant_key:telegram.assistant || '', telegram_user_key:telegram.user || '',
      telegram_group:value.telegram?.group ?? null,
      clear_telegram_assistant_key:!!telegram.clearAssistant, clear_telegram_user_key:!!telegram.clearUser};
  }
  function ModelKeyField({label, configured, value, onChange, busy}) {
    const editing = value !== null || !configured;
    return h('div', null,
      editing ? h('label', {className:'forsic-connection-field'}, h('span', null, label+' (선택)'),
        h('input', {type:'password',value:value ?? '',autoComplete:'new-password',spellCheck:false,disabled:busy,
          placeholder:'빈칸이면 API 키 없이 연결',onChange:e=>onChange(e.target.value)}))
        : h('div', {className:'forsic-connection-field'}, h('span', null, label),
          h('span', {className:'forsic-connection-badge'}, '등록됨'),
          h('button', {type:'button',disabled:busy,'aria-label':label+' 변경',onClick:()=>onChange('')}, '변경'),
          h('button', {type:'button',disabled:busy,'aria-label':label+' 없이 사용',onClick:()=>onChange('')}, '키 없이 사용')),
      editing && h('p', {className:'forsic-connection-hint'}, '빈칸으로 저장하면 API 키 없이 연결해요.',
        configured && h('button', {type:'button',disabled:busy,'aria-label':label+' 유지',onClick:()=>onChange(null)}, '기존 키 유지')));
  }
  function ModelConnectionTest({connection, model, apiKey, saving}) {
    const [busy,setBusy]=R.useState(false), [result,setResult]=R.useState(null);
    const pending=R.useRef(false), live=R.useRef(true);
    const draft=JSON.stringify([model.base_url,model.model,apiKey,!!model.key_configured]);
    const latest=R.useRef(draft); latest.current=draft;
    R.useEffect(()=>{live.current=true;return()=>{live.current=false;};},[]);
    R.useEffect(()=>{setResult(null);},[draft]);
    async function test() {
      if(pending.current || saving)return;
      pending.current=true;setBusy(true);setResult(null);
      const tested=draft;
      try {
        const response=await nativeFetch(base+'/connections/test',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
          connection,base_url:model.base_url.trim(),model:model.model.trim(),
          ...(apiKey!==null || !model.key_configured ? {api_key:apiKey ?? ''} : {})})});
        if(live.current && latest.current===tested)setResult(response);
      } catch {if(live.current && latest.current===tested)setResult({status:'request_error'});}
      finally {pending.current=false;if(live.current)setBusy(false);}
    }
    const messages={authentication_error:'API 키 또는 모델 접근 권한을 확인해 주세요.',model_error:'모델 이름과 해당 서버의 지원 여부를 확인해 주세요.',
      timeout:'제한 시간 안에 응답을 받지 못했어요. 서버가 처리 중일 수 있어요.',connection_error:'서버에 연결하지 못했어요. API 주소와 네트워크를 확인해 주세요.',
      provider_error:'서버가 요청을 처리하지 못했어요. 서버 상태를 확인해 주세요.',invalid_response:'응답은 받았지만 모델 답변을 확인하지 못했어요.',
      request_error:'테스트를 마치지 못했어요. 주소·모델·키 입력과 접속 권한을 확인해 주세요.'};
    const success=result?.status==='ok' && result?.model_responded===true;
    return h('div',{className:'forsic-model-test'},
      h('button',{type:'button',disabled:busy||saving||!model.base_url.trim()||!model.model.trim(),onClick:test,
        'aria-label':(connection==='ren'?'렌':'포식이')+' 모델 테스트'},busy?'응답 확인 중…':'연결 테스트'),
      h('small',null,'입력한 연결로 짧은 응답을 요청해요. 저장하거나 사건 자료를 보내지 않아요.'),
      result && h('p',{role:success?'status':'alert',className:success?'forsic-model-test-ok':'forsic-connection-error'},
        success?'모델 응답 확인':messages[result.status]||messages.request_error,
        Number.isFinite(result.latency_ms) && result.latency_ms>=0 ? ' · '+(result.latency_ms/1000).toFixed(1)+'초' : ''));
  }
  function ContextLengthControl({llm, metadata, busy, onChange}) {
    const sameModel = metadata?.model === llm.model && metadata?.base_url === llm.base_url;
    const minimum = metadata?.minimum_context_length || 64000;
    const reported = sameModel ? metadata?.reported_context_length : null;
    const actualDefault = sameModel ? metadata?.default_context_length : null;
    const explicit = llm.context_length != null;
    // The control range is a convenience, not a claim about model capacity.
    const maximum = Math.max(minimum, reported || 262144, llm.context_length || 0);
    const sliderValue = Math.max(minimum, Math.min(maximum, llm.context_length || actualDefault || minimum));
    const number = value => Number(value).toLocaleString('ko-KR');
    return h('div', {className:'forsic-context-control'},
      h('div', {className:'forsic-context-heading'},
        h('label', {htmlFor:'forsic-context-length'}, '문맥 길이'),
        h('output', {htmlFor:'forsic-context-length', 'aria-live':'polite'}, explicit ? number(llm.context_length)+' 토큰' : '모델 기본값')),
      h('button', {type:'button', className:'forsic-context-default', disabled:busy, 'aria-pressed':!explicit, onClick:()=>onChange(null)}, '모델 기본값'),
      h('input', {id:'forsic-context-length', type:'range', min:minimum, max:maximum, step:1, value:sliderValue, disabled:busy,
        'aria-label':'직접 지정할 문맥 길이', 'aria-describedby':'forsic-context-hint',
        'aria-valuetext':number(sliderValue)+' 토큰 · 움직이면 직접 지정', onChange:e=>onChange(Number(e.target.value))}),
      h('div', {className:'forsic-context-scale', 'aria-hidden':true}, h('span',null,number(minimum)), h('span',null,number(maximum))),
      h('p', {id:'forsic-context-hint', className:'forsic-connection-hint'},
        actualDefault ? '제공자가 보고한 기본 문맥: '+number(actualDefault)+' 토큰. ' : '제공자의 실제 기본 문맥 길이는 확인되지 않았어요. ',
        '기본값을 선택하면 모델 정보에 따라 자동 설정합니다. 직접 지정한 길이는 저장 후 다음 대화 차례부터 적용됩니다.'),
      h('small', null, reported ? '이전에 확인한 문맥: '+number(reported)+' 토큰 · 기본값이나 현재 최대 한도를 보장하지 않습니다.' : '슬라이더 범위는 모델의 최대 지원 길이를 뜻하지 않습니다.'));
  }
  function ConnectionSettings({onClose, embedded=false, section}) {
    const dialog = R.useRef(null);
    const [localTab, setTab] = R.useState('llm');
    const tab = section || localTab;
    const [telegram, setTelegram] = R.useState({assistant:'',user:'',clearAssistant:false,clearUser:false});
    const [value, setValue] = R.useState(null), [secret, setSecret] = R.useState('');
    const [clearKey, setClearKey] = R.useState(false), [busy, setBusy] = R.useState(false);
    const [renKey, setRenKey] = R.useState(null), [llmKey, setLlmKey] = R.useState(null);
    const withRen = v => ({...v,ren_llm:{enabled:false,base_url:'',model:'',key_configured:false,...v.ren_llm}});
    const [message, setMessage] = R.useState(''), [error, setError] = R.useState('');
    const [setup, setSetup] = R.useState(null);
    R.useEffect(() => {
      if(!embedded) dialog.current?.showModal();
      let live = true;
      nativeFetch(base + '/setup').then(status => {
        if (live) setSetup(status);
        return status.local_admin ? nativeFetch(base + '/connections') : null;
      }).then(v => {if(live && v) setValue(withRen(v));})
        .catch(() => {if(live) setError('연결 설정을 불러오지 못했어요. 새 설정 기능의 서버 적용 여부를 확인해주세요.');});
      return () => {live=false;};
    }, []);
    function update(section, name, next) {setValue(v => ({...v, [section]:{...v[section], [name]:next}})); setMessage('');}
    async function save(event) {
      event.preventDefault(); setError(''); setMessage('');
      if(value.ren_llm.enabled) {
        let validURL = false;
        try {const url = new URL(value.ren_llm.base_url.trim()); validURL = ['http:','https:'].includes(url.protocol) && !!url.hostname && !url.username && !url.password && !url.search && !url.hash;} catch {}
        if(!validURL || !value.ren_llm.model.trim()) {setError('추가 모델을 사용하려면 렌 전용 URL과 모델을 모두 입력해주세요. URL은 인증정보·쿼리·프래그먼트 없는 HTTP(S) 주소여야 해요.'); return;}
      }
      setBusy(true);
      try {
        const saved = await nativeFetch(base + '/connections', {method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify(settingsBody(value,secret,clearKey,telegram,renKey,llmKey))});
        const telegramChanged = tab === 'telegram' || telegram.assistant || telegram.user || telegram.clearAssistant || telegram.clearUser;
        window.dispatchEvent(new Event('forsic:connections-changed'));
        setValue(withRen(saved)); setRenKey(null); setLlmKey(null); setSecret(''); setClearKey(false); setTelegram({assistant:'',user:'',clearAssistant:false,clearUser:false});
        setMessage(telegramChanged ? '텔레그램 설정을 저장했어요. 기존 그룹·토픽은 바꾸지 않았어요. 저장한 그룹은 새 미러를 명시적으로 연결할 때 사용해요.' : tab === 'llm' ? '저장했어요. 새 조사는 바로, 진행 중인 조사는 현재 대화 차례가 끝난 뒤 적용돼요. 서버를 다시 시작할 필요 없어요.' : '연결 설정을 저장했어요.');
      } catch {setError('저장하지 못했어요. API 주소·문맥 길이·키·봇 토큰 형식을 확인해주세요. 텔레그램은 비공개 초대 링크 대신 그룹 ID 또는 공개 @아이디를 입력해주세요.');}
      finally {setBusy(false);}
    }
    async function check() {
      setBusy(true); setError(''); setMessage('');
      try {
        const result = await nativeFetch(base + '/connections/gti-check', {method:'POST'});
        if(result.http_status === 200) setMessage('GTI 연결을 확인했어요. 공개 시험 해시의 기존 보고서를 받았어요.');
        else if(result.http_status === 404) setMessage('GTI에 연결됐지만 시험 해시의 보고서는 없어요. 모든 조회 권한을 확인한 것은 아니에요.');
        else setError(result.error || '연결을 확인하지 못했어요.');
      } catch {setError('GTI 연결 확인을 마치지 못했어요.');}
      finally {setBusy(false);}
    }
    const field = (label, section, name, extra={}) => h('label', {className:'forsic-connection-field'},
      h('span', null, label), h('input', {value:value[section][name], onChange:e => update(section,name,e.target.value), spellCheck:false, autoComplete:'off', disabled:busy, ...extra}));
    return h(embedded ? 'div' : 'dialog', {ref:dialog, className:'forsic-connections'+(embedded ? ' forsic-connections-embedded' : ''), 'aria-label':'연결 설정', onCancel:onClose},
      !embedded && h('header', null, h('div', null, h('small', null, 'FORSIC'), h('h2', null, '연결 설정')), h('button', {type:'button', onClick:onClose, 'aria-label':'연결 설정 닫기'}, '닫기')),
      setup?.local_admin && !embedded && h('a', {href:scopedURL('/settings?section=hosting'), className:'forsic-setup-link', onClick:onClose}, '자료 폴더와 접속 범위', ' →'),
      setup?.local_admin === false && h('section', {className:'forsic-connection-readonly'},
        h('h3', null, '연결 상태'),
        h('p', null, '조사 모델 · ', setup.llm_configured ? '설정됨' : '설정 필요'),
        h('p', null, '자료 폴더 · ', setup.evidence_configured ? '설정됨' : '설정 필요'),
        h('p', null, '접속 범위 · ', {local:'이 컴퓨터에서만',private_lan:'내부 네트워크',online:'보안 온라인 접속'}[setup.hosting_current?.mode] || '확인 필요'),
        h('p', null, '연결과 공개 범위는 포식이가 설치된 컴퓨터에서 변경할 수 있습니다.')),
      setup?.local_admin && !embedded && h('nav', {className:'forsic-connection-tabs', 'aria-label':'연결 종류'}, ...[['llm','LLM'],['gti','VT / GTI'],['telegram','텔레그램']].map(([id,label]) =>
        h('button', {type:'button', key:id, 'aria-pressed':tab===id, onClick:() => {setTab(id); setMessage(''); setError('');}},label))),
      value ? h('form', {onSubmit:save, noValidate:true},
        h('section', {hidden:tab!=='llm',className:'forsic-connection-group'}, h('h2', null, '조사 모델'),
          field('API 주소 · OpenAI 호환', 'llm', 'base_url', {type:'url', required:true, placeholder:'http://서버:11434/v1'}),
          field('모델 이름', 'llm', 'model', {required:true, placeholder:'qwen3.8:latest'}),
          h(ModelKeyField, {label:'조사 모델 API 키', configured:value.llm.key_configured, value:llmKey, onChange:setLlmKey, busy}),
          h(ModelConnectionTest,{connection:'main',model:value.llm,apiKey:llmKey,saving:busy}),
          h('details', {className:'forsic-connection-advanced'}, h('summary',null,'문맥 길이 · 고급 설정'),
            h(ContextLengthControl, {llm:value.llm, metadata:value.llm_context, busy, onChange:next=>setValue(v=>({...v,llm:{...v.llm,context_length:next,context_changed:true}}))})),
          h('div', {className:'forsic-connection-runtime'},
            h('p', null, h('strong',null,'저장된 기본 모델'), ' · ', value.llm_current.model),
            h('p', null, value.llm_current.base_url),
            h('p', null, '문맥 · ', value.llm_current.context_length ? Number(value.llm_current.context_length).toLocaleString('ko-KR')+' 토큰 (직접 지정)' : '모델 기본값'),
            h('p', null, '새 조사는 바로 · 진행 중인 조사는 다음 대화 차례부터 적용돼요.')),
          h('p', {className:'forsic-connection-hint'}, '진행 중인 요청은 끝까지 유지해요. API 키를 빈칸으로 저장하면 키 없이 연결해요.')),
        h('section', {hidden:tab!=='llm',className:'forsic-connection-group'}, h('h2', null, '렌 검토 모델'),
          h('label', {className:'forsic-connection-check'}, h('input', {type:'checkbox', checked:value.ren_llm.enabled, disabled:busy, onChange:e=>update('ren_llm','enabled',e.target.checked)}), '추가 모델 사용'),
          h('p', {className:'forsic-connection-hint'}, value.ren_llm.enabled ? '렌은 전용 모델과 독립된 검토 문맥을 사용해요. 포식이의 조사 모델은 바뀌지 않아요.' : '추가 모델을 끄면 렌도 현재 포식이 모델을 사용해요. 입력한 전용 설정은 보관돼요.'),
          value.ren_llm.enabled && h('div', null,
            field('렌 전용 URL', 'ren_llm', 'base_url', {type:'url',required:true}),
            field('렌 전용 모델', 'ren_llm', 'model', {required:true}),
            h(ModelKeyField, {label:'렌 전용 API 키', configured:value.ren_llm.key_configured, value:renKey, onChange:setRenKey, busy}),
            h(ModelConnectionTest,{connection:'ren',model:value.ren_llm,apiKey:renKey,saving:busy}),
            h('p', {className:'forsic-connection-hint'}, '렌 전용 키는 조사 모델의 키와 별도로 보관해요.')),
          h('p', {className:'forsic-connection-hint'}, '변경은 다음 렌 검토 호출부터 적용돼요. 진행 중인 호출은 바뀌지 않으며, 설정을 열거나 저장해도 모델을 호출하지 않아요.')),
        h('section', {hidden:tab!=='gti',className:'forsic-connection-group'}, h('h2', null, 'VT / GTI ', h('span', {className:'forsic-connection-badge'}, value.gti.key_configured ? '키 등록됨' : '키 미등록')),
          h('label', {className:'forsic-connection-check'}, h('input', {type:'checkbox', checked:value.gti.enabled, disabled:busy, onChange:e => update('gti','enabled',e.target.checked)}), '외부 위협 평판 조회 사용'),
          h('p', {className:'forsic-connection-address'}, '공식 API · ', h('code', null, value.gti.base_url)),
          h('label', {className:'forsic-connection-field'}, h('span', null, 'API 키'), h('input', {type:'password', value:secret, autoComplete:'new-password', spellCheck:false, disabled:busy || clearKey, placeholder:value.gti.key_configured ? '등록되어 있어요 · 바꿀 때만 입력' : 'VT/GTI API 키 입력', onChange:e => setSecret(e.target.value)})),
          value.gti.key_configured && h('label', {className:'forsic-connection-check'}, h('input', {type:'checkbox', checked:clearKey, disabled:busy, onChange:e => {setClearKey(e.target.checked); setSecret('');}}), '저장된 키 삭제'),
          h('label', {className:'forsic-connection-check'}, h('input', {type:'checkbox', checked:value.gti.allow_public_network_indicators, disabled:busy, onChange:e => update('gti','allow_public_network_indicators',e.target.checked)}), '공개 IP·도메인 조회도 허용'),
          h('p', {className:'forsic-connection-hint'}, '기본은 파일 해시 조회예요. IP·도메인은 조회만으로 GTI 공유 데이터셋에 포함될 수 있어요. 파일 업로드·URL 재검사는 하지 않아요.'),
          h('button', {type:'button', disabled:busy || !value.gti.key_configured || !!secret || clearKey, onClick:check}, 'GTI 연결 확인'),
          h('small', null, '저장된 키로 공개 시험 해시 1개만 조회해요. 사건 자료·모델 호출은 없어요.'),
          h('p', {className:'forsic-connection-hint'}, '변경은 저장 후 다음 GTI 조회부터 적용돼요.')),
        h('section', {hidden:tab!=='telegram',className:'forsic-connection-group'}, h('h2',null,'텔레그램'),
          field('그룹 ID 또는 공개 그룹 주소', 'telegram', 'group', {type:'text', maxLength:256, placeholder:'-100… / @group_name / https://t.me/group_name'}),
          h('p',{className:'forsic-connection-hint'},'비공개 초대 링크(t.me/+…)가 아니라 그룹 ID를 입력해주세요. 저장은 연결 정보만 보관하며, 진행 중인 그룹·사건 토픽은 옮기지 않아요.'),
          ...[['assistant','clearAssistant','assistant_key_configured','포식이 봇 토큰'],['user','clearUser','user_key_configured','내 질문용 봇 토큰']].map(([name,clear,configured,label]) =>
            h('div',{key:name,className:'forsic-telegram-token'},
              h('label',{className:'forsic-connection-field'},h('span',null,label,' ',h('span',{className:'forsic-connection-badge'},value.telegram?.[configured]?'등록됨':'미등록')),
                h('input',{type:'password',value:telegram[name],autoComplete:'new-password',spellCheck:false,disabled:busy||telegram[clear],
                  placeholder:value.telegram?.[configured]?'바꿀 때만 새 토큰 입력':'BotFather에서 발급한 봇 토큰',
                  onChange:e=>setTelegram(v=>({...v,[name]:e.target.value}))})),
              value.telegram?.[configured]&&h('label',{className:'forsic-connection-check'},
                h('input',{type:'checkbox',checked:telegram[clear],disabled:busy,onChange:e=>setTelegram(v=>({...v,[clear]:e.target.checked,[name]:''}))}),label+' 삭제'))),
          h('p',{className:'forsic-connection-hint'},'빈칸으로 저장하면 기존 토큰을 유지해요. 변경은 다음 텔레그램 미러 시작부터 적용되며, 기존 그룹과 사건별 토픽은 그대로예요.'),
          h('p',{className:'forsic-connection-hint'},'새 봇으로 바꿀 때는 두 봇 모두 기존 그룹에 참여해 있어야 해요. 저장만으로 메시지를 보내지는 않아요.')),
        h('footer', {className:'forsic-connection-actions'}, h('span',null,'변경한 연결 설정을 함께 저장합니다.'), h('button', {type:'submit', className:'forsic-connection-save', disabled:busy}, busy ? '처리 중…' : '설정 저장')))
        : !error && !setup && h('p', null, '설정을 불러오고 있어요…'),
      message && h('p', {className:'forsic-connection-message', role:'status'}, message),
      error && h('p', {className:'forsic-connection-error', role:'alert'}, error));
  }
  const sourceLabels = {unchanged:'보관 후 노트 변경 없음', changed:'원 사건 노트가 갱신됐어요 · 이 보관본은 이전 기록입니다', unavailable:'원 사건 저장소 연결 없음 · 보관본으로 열람'};
  function CaseCard({item, onOpen}) {
    return h('article', {className:'forsic-archive-card'},
      h('div', {className:'forsic-archive-meta'}, item.synthetic ? '합성 시험' : '보관 사례', ' · ', item.analysis_status === 'partial' ? '부분 조사' : '요청 범위 답변'),
      h('h3', null, item.title), h('p', null, item.summary),
      h('small', null, item.matched_terms?.length ? '일치 키워드: ' + item.matched_terms.join(' · ') : (item.tags || []).join(' · ')),
      h('small', {className:item.source_state === 'changed' ? 'forsic-archive-changed' : ''}, sourceLabels[item.source_state]),
      h('button', {onClick:onOpen}, '결론 · 근거 · 남은 확인 보기'));
  }
  function CaseLibrary({current}) {
    const fetchJSON = (url, options) => nativeFetch(scopedURL(url, current?.case_id || ''), options);
    const [query, setQuery] = R.useState(''), [page, setPage] = R.useState(null), [error, setError] = R.useState('');
    const [detail, setDetail] = R.useState(null), [source, setSource] = R.useState(null), [busy, setBusy] = R.useState(false);
    const [saved, setSaved] = R.useState(''), [tags, setTags] = R.useState('');
    const guard = async action => {setError(''); setBusy(true); try {await action();} catch (e) {setError(e.message || '사례를 열지 못했어요. 다시 확인해 주세요.');} finally {setBusy(false);}};
    const search = (offset=0) => guard(async () => setPage(await fetchJSON(base + '/library?query=' + encodeURIComponent(query) + '&offset=' + offset)));
    R.useEffect(() => {let alive=true; fetchJSON(base + '/library').then(v => {if (alive) setPage(v);}).catch(() => {if (alive) setError('사례 창고를 열지 못했어요.');}); return () => {alive=false;};}, []);
    const open = (id, revision='', offset=0) => guard(async () => {setSource(null); setDetail(await fetchJSON(base + '/library/' + id + '?revision=' + encodeURIComponent(revision) + '&offset=' + offset));});
    const save = () => guard(async () => {
      const result = await fetchJSON(base + '/library', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({tags:tags.split(',').map(t => t.trim()).filter(Boolean)})});
      setSaved(result.reused ? '같은 내용이 이미 보관되어 있어요.' : '현재 조사 노트와 연결된 근거를 보관했어요.');
      setPage(await fetchJSON(base + '/library?query=' + encodeURIComponent(query)));
    });
    return h('section', {id:'case-library', className:'forsic-library'},
      h('div', {className:'forsic-library-heading'}, h('div', null, h('h2', null, '사례 창고')),
        h('small', null, current?.synthetic ? '합성 시험 사례만 표시' : '실제 사례만 표시')),
      h('form', {className:'forsic-library-search', onSubmit:e => {e.preventDefault(); search();}},
        h('input', {value:query, onChange:e => setQuery(e.target.value), placeholder:'행위 · 오류 · 환경 키워드', 'aria-label':'사례 검색 키워드'}),
        h('button', {type:'submit', disabled:busy}, busy ? '확인 중…' : '사례 찾기')),
      current?.case_id && h('details', {className:'forsic-library-save'}, h('summary', null, '현재 사건을 사례로 보관'),
        h('p', null, '현재 조사 노트와 연결된 원문 결과를 로컬에 보관합니다. 정정한 뒤 다시 보관하면 이전 버전도 남아요.'),
        h('input', {value:tags, onChange:e => setTags(e.target.value), placeholder:'예: 예약 작업, 권한 오류, Linux', 'aria-label':'사례 태그 (쉼표로 구분)'}),
        h('button', {type:'button', disabled:busy, onClick:save}, '현재 노트 보관')),
      saved && h('p', {role:'status'}, saved), error && h('p', {role:'alert'}, error),
      page && h('small', null, `${page.total}건 · 키워드 일치순`),
      page?.total === 0 && h('p', null, '일치하는 보관 사례 없음'),
      h('div', {className:'forsic-library-grid'}, ...(page?.cases || []).map(item => h(CaseCard, {key:item.archive_id, item, onOpen:() => open(item.archive_id)}))),
      page?.next_offset != null && h('button', {onClick:() => search(page.next_offset), disabled:busy}, '다음 사례'),
      page && h('button', {onClick:() => search(0), disabled:busy}, '처음 / 새로고침'),
      detail && h('div', {className:'forsic-modal forsic-archive-detail', role:'dialog', 'aria-modal':true, 'aria-label':'보관 사례'},
        h('button', {onClick:() => {setDetail(null); setSource(null);}}, '닫기'),
        h('h2', null, detail.title), h('small', null, `${detail.case_id} · ${new Date(detail.saved_at*1000).toLocaleString('ko-KR',{timeZone:'Asia/Seoul'})} KST`),
        h('p', null, sourceLabels[detail.source_state]),
        h('label', null, '보관 버전 ', h('select', {value:detail.revision, onChange:e => open(detail.archive_id, e.target.value), 'aria-label':'보관 버전'},
          ...detail.versions.map(v => h('option', {key:v.revision, value:v.revision}, new Date(v.saved_at*1000).toLocaleString('ko-KR',{timeZone:'Asia/Seoul'}) + ' · ' + v.revision.slice(0,8))))),
        h('div', {className:'forsic-archive-comparison'},
          h('div', null, h('strong', null, '현재 사건의 질문'), h('p', null, current?.question || '질문 미등록')),
          h('div', null, h('strong', null, '이 사례에서 조사한 질문'), h('p', null, detail.question), h('small', null, detail.scope))),
        h('p', {className:'forsic-caption'}, '비슷한 점과 다른 점을 비교해 다음 확인에 활용하세요. 이 기록은 현재 사건의 증거와 별도로 보관돼요.'),
        ...detail.notes.map(n => h('article', {key:n.note_id}, h('h3', null, n.question), h('p', null, n.answer),
          ...[['alternatives','다른 가능한 설명'],['gaps','확인하지 못한 부분'],['next_checks','다음 확인']].map(([key,label]) => (n[key] || []).length ? h('div', {key}, h('strong', null, label), ...(n[key] || []).map((v,i) => h('p', {key:i}, v))) : null),
          n.correction_reason && h('p', null, '정정: ', n.correction_reason),
          ...(n.timeline || []).map((t,i) => h('p', {key:'time-'+i}, t.display_time, ' · ', t.description)))),
        detail.next_offset != null && h('button', {onClick:() => open(detail.archive_id,detail.revision,detail.next_offset), disabled:busy}, '다음 조사 노트'),
        h('h3', null, '당시 사용한 원문'),
        ...detail.sources.map((s,i) => h('button', {key:s.id, disabled:busy, onClick:() => guard(async () => setSource(await fetchJSON(base + '/library/' + detail.archive_id + '/sources/' + s.id + '?revision=' + detail.revision)))}, '근거 ' + (i+1) + ' · ' + (s.path || s.tool))),
        source && h('section', {'aria-label':'이전 사례 원문'}, h('h3', null, '보관된 원문 결과'), h('code', null, `${source.case_id} / E:${source.source.id}`), h('pre', null, JSON.stringify(source.source, null, 2)))));
  }
  function CaseDirectory() {
    const [items, setItems] = R.useState([]), [filter, setFilter] = R.useState('active');
    const [availability,setAvailability]=R.useState(null);
    const [query, setQuery] = R.useState(''), [error, setError] = R.useState('');
    const refresh = async () => {try {const value=await nativeFetch(base + '/investigations');setItems(value.cases);setAvailability(value.new_investigation || null);setError('');}
      catch {setAvailability(null);setError('조사 목록을 불러오지 못했습니다. 잠시 후 다시 확인해주세요.');}};
    R.useEffect(() => {refresh(); const timer=setInterval(refresh,5000); window.addEventListener('forsic:investigation-closed',refresh); return () => {clearInterval(timer);window.removeEventListener('forsic:investigation-closed',refresh);};}, []);
    const archive = async item => {try {
      await nativeFetch('/api/sessions/' + encodeURIComponent(item.session_id), {method:'PATCH',
        headers:{'Content-Type':'application/json'}, body:JSON.stringify({profile:item.profile_name || new URLSearchParams(window.location?.search||'').get('profile'), archived:!item.archived})});
      await refresh();
    } catch {setError('보관 상태를 바꾸지 못했어요.');}};
    const visible = items.filter(item => (filter === 'archived' ? item.archived : !item.archived)
      && (item.label + ' ' + item.title + ' ' + item.case_id).toLowerCase().includes(query.toLowerCase()));
    return h('section', {id:'investigations', className:'forsic-directory'},
      h('div', {className:'forsic-directory-heading'}, h('div', null, h('h2', null, '기존 조사'),
        h('p', null, '조사를 선택하면 마지막 대화를 엽니다.')),
        h(NewInvestigationAction,{availability,error})),
      h(InvestigationAdmissionNotice,{availability,error}),
      h('div', {className:'forsic-directory-filters'},
        h('button', {'aria-pressed':filter==='active', onClick:() => setFilter('active')}, '사건 목록'),
        h('button', {'aria-pressed':filter==='archived', onClick:() => setFilter('archived')}, '보관함'),
        h('input', {value:query, onChange:e => setQuery(e.target.value), placeholder:'사건 이름 검색', 'aria-label':'사건 검색'})),
      error && h('p', {role:'status'}, error),
      !error && !visible.length && h('div', {className:'forsic-directory-empty',role:'status'},h(NavIcon,{name:'cases'}),h('h3',null,query ? '검색 결과 없음' : filter==='archived' ? '보관한 조사 없음' : '등록한 조사 없음'),!query && filter==='active' && h(NewInvestigationAction,{availability,error})),
      ...visible.map(item => h('article', {key:item.case_id, className:'forsic-investigation'},
        h('a', {className:'forsic-investigation-link',href:conversationURL(item.case_id,item.resume_session_id), 'aria-label':item.label+' 대화 열기'},
          h('div', null, h('strong', null, item.label), h('small', null, item.case_id, item.synthetic ? ' · 합성 시험' : ''),
            h('p', null, item.question),h('code',{className:'forsic-directory-path'},item.scope)),
          h('div', {className:'forsic-investigation-status'},
            h('span', {'data-status':item.status}, {investigating:'조사 중',waiting:'대기',closed:'사용자 종료',ended:'실행 종료',completed:'실행 종료'}[item.status]),
            h('small', null, '최근 활동 ', new Date(item.last_activity_at*1000).toLocaleString('ko-KR',{timeZone:'Asia/Seoul',month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'})))),
        h('button', {className:'forsic-archive-action',onClick:() => archive(item), 'aria-label':item.archived ? item.label+' 목록으로 복원' : item.label+' 보관', title:item.archived ? '목록으로 복원' : '사건 보관'},
          h('svg',{viewBox:'0 0 24 24',width:18,height:18,fill:'none',stroke:'currentColor',strokeWidth:1.5,'aria-hidden':true},
            h('path',{d:'M4 8H20V20H4Z M3 4H21V8H3Z'}),
            h('path',{d:item.archived?'M12 17V11 M9 14L12 11L15 14':'M9 12H15'}))))));
  }
  function ReportDownload({url,name,className,children}) {
    const [busy,setBusy]=R.useState(false),[error,setError]=R.useState('');
    const request=R.useRef(null),blobUrl=R.useRef('');
    R.useEffect(()=>()=>{request.current?.abort();if(blobUrl.current)URL.revokeObjectURL(blobUrl.current);},[url]);
    async function download() {
      const controller=new AbortController();request.current=controller;setBusy(true);setError('');
      try {
        const [path,query='']=url.split('?'),params=new URLSearchParams(query);
        const profile=new URLSearchParams(window.location?.search||'').get('profile');if(profile)params.set('profile',profile);
        const response=await authedFetch(path+(params.size?'?'+params:''),{signal:controller.signal});
        if(!response.ok){setError(response.status===401?'보고서 접근 인증이 만료됐어요. 화면을 새로고침한 뒤 다시 열어 주세요.':'보고서를 내려받지 못했어요. 잠시 뒤 다시 시도해 주세요.');return;}
        const blob=await response.blob();if(controller.signal.aborted)return;
        const disposition=response.headers.get('Content-Disposition')||'';
        let filename=disposition.match(/filename="([^"]+)"|filename=([^;]+)/i)?.slice(1).find(Boolean)?.trim()||name;
        const encoded=disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
        if(encoded){try{filename=decodeURIComponent(encoded);}catch{}}
        if(blobUrl.current)URL.revokeObjectURL(blobUrl.current);
        blobUrl.current=URL.createObjectURL(blob);
        const anchor=document.createElement('a');anchor.href=blobUrl.current;anchor.download=filename.replace(/[\\/\x00-\x1f\x7f]/g,'_');
        document.body.append(anchor);anchor.click();anchor.remove();
      } catch {if(!controller.signal.aborted)setError('보고서를 내려받지 못했어요. 연결을 확인하고 다시 시도해 주세요.');}
      finally {if(!controller.signal.aborted)setBusy(false);}
    }
    return h(R.Fragment,null,h('button',{type:'button',className,disabled:busy,onClick:download},busy?'보고서를 불러오고 있어요…':children),error&&h('p',{role:'status'},error));
  }
  function ReportDriven({caseId, onSource, autoLoad=false}) {
    const [value,setValue]=R.useState(null),[error,setError]=R.useState('');
    async function load() {try {setValue(await nativeFetch(scopedURL(base+'/report-driven',caseId)));setError('');} catch {setError('보고서 상태를 불러오지 못했어요. 다시 시도해 주세요.');}}
    R.useEffect(()=>{let alive=true;setValue(null);setError('');
      if(autoLoad)nativeFetch(scopedURL(base+'/report-driven',caseId)).then(v=>{if(alive)setValue(v);}).catch(()=>{if(alive)setError('보고서 상태를 불러오지 못했어요. 다시 시도해 주세요.');});
      return()=>{alive=false;};
    },[caseId,autoLoad]);
    return h('section',{className:'forsic-report-overview'},
      h('div',{className:'forsic-section-heading'},h('h2',null,autoLoad?'조사 보고서':'보고 상태'),h('button',{className:'forsic-action-secondary',onClick:load},value?'새로고침':'보고서 확인')),
      error&&h('p',{role:'status'},error),
      autoLoad&&!value&&!error&&h('p',{role:'status'},'보고서를 불러오고 있어요.'),
      value&&h('div',null,
        !value.bundles.length&&h('p',{className:'forsic-empty'},'생성된 보고서 없음'),
        ...value.bundles.map(b=>h('article',{key:b.bundle_id,className:'forsic-report-bundle'},
          h('h3',null,b.stale?'이전 판단의 보존 보고서':'현재 판단의 부분 보고서'),
          h('p',null,b.stale?'판단이 바뀌어 다시 작성할 필요가 있습니다.':'파일 생성과 조사 완료는 다릅니다. 남은 확인과 함께 읽어 주세요.'),
          h('div',{className:'forsic-report-downloads'},...b.files.map(f=>h(ReportDownload,{key:f.name,className:'forsic-action-secondary',name:f.name,url:scopedURL(base+'/report-bundles/'+b.bundle_id+'/'+f.name,caseId)},
            (f.name.startsWith('integrated')?'통합보고서 ':f.name.startsWith('executive')?'임원용 ':'실무자용 ')+(f.name.endsWith('.docx')?'Word':'HTML')+' ↓'))),
          h(ReportDownload,{className:'forsic-report-manifest',name:'manifest.json',url:scopedURL(base+'/report-bundles/'+b.bundle_id+'/manifest.json',caseId)},'파일 검증 목록'))),
        h('details',{className:'forsic-report-coverage'},h('summary',null,'현재 판단과 남은 확인'),
          ...value.state.questions.map(q=>h('article',{key:q.id},h('h3',null,q.question),h('p',null,q.answer))),
          ...value.state.gaps.filter(g=>g.disposition!=='resolved').map(g=>h('details',{key:g.id},h('summary',null,g.original_obligation),h('p',null,g.reason),h('p',null,g.reopen_conditions.join(' / ')))),
          ...value.state.sources.map((source,i)=>h('button',{key:source.id,onClick:()=>onSource(source.id)},'근거 '+(i+1)+' · '+source.label)))));
  }
  // A read-only view of existing notes. No new model, generated facts or parallel state store.
  function investigationView(data) {
    const notes = data?.notes || [], sources = new Map(), timeline = new Map();
    const events = new Map((data?.events || []).map(e => [e.id,e]));
    for (const note of notes) {
      for (const id of [...(note.evidence_ids || []), ...(note.timeline || []).flatMap(t => t.evidence_ids || [])]) {
        if (!sources.has(id)) sources.set(id,{id, event:events.get(id), questions:[]});
        if (!sources.get(id).questions.includes(note.question)) sources.get(id).questions.push(note.question);
      }
      for (const item of note.timeline || []) {
        const ids=[...new Set(item.evidence_ids || [])].sort();
        const key=JSON.stringify([item.temporal ? note.note_id : null,item.event_ref,item.time,item.description,ids,item.time_source?.evidence_id,item.time_source?.field,item.time_source?.entry_path,item.time_source?.pointer,item.time_source?.byte_start]);
        if (!timeline.has(key)) {
          // A timezone-free date can be ordered as written, never as an absolute instant.
          const inferred=item.temporal?.observed_or_inferred==='inferred';
          const stamp=!inferred && item.comparable!==false && /T.*(?:Z|[+-]\d{2}:?\d{2})$/.test(item.time || '') ? Date.parse(item.time) : NaN;
          const local=(item.time || '').match(/^(\d{4}-\d{2}-\d{2})(?:[T ](\d{2}:\d{2})(?::(\d{2}))?)?/);
          timeline.set(key,{...item,key,inferred,evidence_ids:ids,stamp:Number.isFinite(stamp)?stamp:null,
            writtenDate:local ? local[1]+' '+(local[2] || '00:00')+':'+(local[3] || '00') : null});
        }
      }
    }
    const ordered=[...timeline.values()].sort((a,b)=>{
      if (a.stamp !== null && b.stamp !== null) return a.stamp-b.stamp;
      if (a.stamp !== null) return -1;
      if (b.stamp !== null) return 1;
      return (a.writtenDate || '\uffff').localeCompare(b.writtenDate || '\uffff');
    });
    return {notes,sources:[...sources.values()],timeline:ordered};
  }
  function EvidenceCard({source,caseId,onSource}) {
    const [record,setRecord]=R.useState(source.event || null);
    R.useEffect(()=>{
      let alive=true;
      if (!source.event) nativeFetch(scopedURL(base+'/evidence/'+encodeURIComponent(source.id),caseId))
        .then(value=>{if(alive)setRecord(value);}).catch(()=>{});
      return ()=>{alive=false;};
    },[caseId,source.id]);
    const detail=record?.data;
    const label=detail?.path || detail?.file_path || ({forsic_verify:'증거 무결성 확인',forsic_hash:'파일 해시',forsic_search:'본문 검색',forsic_image_files:'이미지 자료 확인',forsic_read:'원문 읽기'}[detail?.tool]) || '보존된 근거';
    return h('button',{className:'forsic-source-card',onClick:()=>onSource(source.id)},
      h('strong',null,label),h('code',null,source.id.slice(0,12)),h('small',null,source.questions.join(' · ')),h('span',null,'원문 보기 →'));
  }
  function InvestigationBoard({data,onSource}) {
    const view=investigationView(data);
    const refs=ids => (ids || []).map(id => h('button',{key:id,onClick:()=>onSource(id),title:id},'근거 · '+id.slice(0,8)));
    const timeGroups=[
      {key:'known',label:'시간대 확인됨 · KST 순',items:view.timeline.filter(t=>t.stamp !== null)},
      {key:'estimated',label:'가정에 따른 추정 · 시간 관계',items:view.timeline.filter(t=>t.inferred)},
      {key:'local',label:'시간대 미확인 · 원문 날짜순',items:view.timeline.filter(t=>!t.inferred && t.stamp === null && t.writtenDate)},
      {key:'unknown',label:'날짜 미확인',items:view.timeline.filter(t=>!t.inferred && t.stamp === null && !t.writtenDate)}
    ];
    return h('section',{className:'forsic-board','aria-label':'조사 보드'},
      h('header',{className:'forsic-board-heading'},h('div',null,h('h2',null,'조사 보드')),
        h('div',{className:'forsic-board-counts'},h('span',null,'질문 ',view.notes.length),h('span',null,'연결 근거 ',view.sources.length),h('span',null,'시간 기록 ',view.timeline.length))),
      h('div',{className:'forsic-board-columns'},
        h('section',{'aria-label':'현재 조사 결과'},h('h3',null,'현재 조사 결과'),
          !view.notes.length && h('p',{className:'forsic-empty'},'저장된 조사 노트 없음'),
          ...view.notes.map(n=>h('article',{key:n.note_id,className:'forsic-finding-card'},
            h('div',{className:'forsic-finding-meta'},h('span',{'data-note-status':n.status || 'open'},{answered:'답변 정리됨',open:'조사 중',needs_input:'자료 필요'}[n.status] || '조사 중'),h('small',null,'노트 v',n.revision || 1)),
            h('h4',null,n.question),h('p',{className:'forsic-answer'},n.answer),
            n.correction_reason && h('p',{className:'forsic-correction'},'정정 · ',n.correction_reason),
            h('div',{className:'forsic-source-links'},...refs(n.evidence_ids)),
            !!n.alternatives?.length && h('details',null,h('summary',null,'다른 설명'),h('ul',null,...n.alternatives.map((x,i)=>h('li',{key:i},x)))),
            !!n.gaps?.length && h('div',{className:'forsic-open-checks'},h('strong',null,'아직 확인할 내용'),h('ul',null,...n.gaps.map((x,i)=>h('li',{key:i},x)))),
            !!n.critical_gaps?.length && h('div',{className:'forsic-open-checks'},h('strong',null,'결론에 중요한 미확인 사항'),h('ul',null,...n.critical_gaps.map((x,i)=>h('li',{key:i},x)))),
            !!n.next_checks?.length && h('details',null,h('summary',null,'다음 확인'),h('ul',null,...n.next_checks.map((x,i)=>h('li',{key:i},x))))))),
        h('section',{className:'forsic-timeline','aria-label':'사건 시간축'},h('h3',null,'사건 시간축'),
          !view.timeline.length && h('p',{className:'forsic-empty'},'근거가 연결된 시각 기록 없음'),
          ...timeGroups.filter(group=>group.items.length).map(group=>h('section',{key:group.key,className:'forsic-time-group','aria-label':group.label},
            h('h4',null,group.label),h('ol',null,...group.items.map(t=>h('li',{key:t.key},
            h('time',null,t.inferred ? (t.temporal.earliest || '하한 미상')+' ~ '+(t.temporal.latest || '상한 미상') : t.display_time || t.time || '시각 미확인'),
            t.inferred && h('small',null,'관측된 원문 시각 · ',t.display_time || t.time || '정확한 시각 없음'),
            t.time_source?.field && h('small',null,t.time_source.field==='log'?'로그 기록':'파일 '+t.time_source.field),
            t.temporal && h('details',null,h('summary',null,'시간 해석의 근거와 가정'),
              h('p',null,t.temporal.confidence_basis),
              h('p',null,'시계 · ',t.temporal.clock_domain,' / 시간대 · ',t.temporal.timezone_basis,' / 연도 · ',t.temporal.year_basis),
              ...(t.temporal.assumptions || []).map((x,i)=>h('p',{key:'assumption-'+i},x)),
              ...(t.temporal.candidates || []).map((x,i)=>h('p',{key:'candidate-'+i},'후보 · ',x.earliest || '미상',' ~ ',x.latest || '미상',' / ',x.basis)),
              ...(t.temporal_conflicts || []).map((x,i)=>h('p',{key:'conflict-'+i},'시간 관계 재검토 · ',x.reason))),
            h('p',null,t.description),h('div',{className:'forsic-source-links'},...refs(t.evidence_ids))))))))),
      h('section',{'aria-label':'연결된 단서'},h('h3',null,'연결된 단서'),
        !view.sources.length && h('p',{className:'forsic-empty'},'연결된 근거 없음'),
        h('div',{className:'forsic-source-grid'},...view.sources.map(s=>h(EvidenceCard,{key:s.id,source:s,caseId:data?.case?.case_id || '',onSource})))));
  }
  const relationLabels={open:'열림',active:'조사 중',held:'보류',scoped_closed:'범위 종결',blocked_internal:'내부 차단',blocked_external:'외부 자료 필요',budget_deferred:'예산 보류',supported:'지지됨',refuted:'반박됨',conflicting:'상충',undetermined:'판단 유보',candidate:'검사 전',ready:'준비',queued:'대기',running:'진행 기록 있음',returned:'반환됨',failed:'실패',blocked:'차단',assessed:'평가됨',unassessed:'미평가',stale:'재평가 필요',completed:'미션 완료',draft:'계획',partial:'부분 수행',superseded:'대체된 판단',retracted:'철회된 판단',supports:'지지',refutes:'반박',inconclusive:'판별 불충분',found:'관측 발견',no_match_in_scope:'해당 범위 일치 없음',unavailable:'검사 불가'};
  function questionRelations(value, id) {
    const s=value.state, q=s.questions.find(q=>q.id===id);
    if(!q)return null;
    const tests=s.tests.filter(t=>t.question_ref.id===id), ids=new Set(tests.map(t=>t.id));
    const assessments=s.assessments.filter(a=>ids.has(a.test_ref.id));
    const receipts=new Set(assessments.map(a=>a.adoption_receipt).filter(Boolean));
    const observations=new Map();
    for(const a of assessments)for(const ref of a.observation_refs){
      const key=ref.id+'@'+ref.version;
      if(!observations.has(key))observations.set(key,{key,ref,assessmentIds:[]});
      observations.get(key).assessmentIds.push(a.id);
    }
    return {q, judgment:value.judgments[id], tests, assessments, observations:[...observations.values()],
      missions:s.missions.filter(m=>m.question_ref.id===id),
      history:s.claims.filter(c=>['superseded','retracted'].includes(c.status)&&c.adoption_receipt&&receipts.has(c.adoption_receipt)),
      gaps:s.gaps.filter(g=>g.question_ref.id===id&& !['resolved','not_applicable'].includes(g.disposition))};
  }
  function RelationDetail({value,questionId,onSource}) {
    const r=questionRelations(value,questionId);
    if(!r)return h('p',{className:'forsic-empty'},'질문을 선택하면 연결된 조사 기록이 나타납니다.');
    const {q,judgment:j}=r, s=value.state;
    const list=(title,items)=>items?.length?h('div',null,h('h4',null,title),h('ul',null,...items.map((x,i)=>h('li',{key:i},x)))):null;
    const sourceButton=(ref,observation,key)=>{
      const source=s.sources.find(x=>x.id===ref?.id);
      return h('button',{key,disabled:!source,onClick:()=>onSource(ref,observation),title:ref?.id},
        observation?'인용 구간 보기':source?.label||'출처 연결 없음',source&&source.version!==ref.version?' · 이전 버전':'');
    };
    const referenceView=(ref,key)=>{
      if(ref.kind==='source')return sourceButton(ref,null,key);
      const record=(ref.kind==='observation'?s.observations:s.claims).find(x=>x.id===ref.id&&x.version===ref.version);
      return h('div',{key},record?.source_ref?sourceButton(record.source_ref,record,key+'-source'):h('p',null,record?.statement||'참조를 현재 버전에서 확인할 수 없습니다.'));
    };
    return h('div',{className:'forsic-relation-detail'},
      h('article',{className:'forsic-relation-answer'},
        h('div',{className:'forsic-relation-meta'},h('span',null,'질문 · ',relationLabels[q.work_state]||q.work_state),h('span',null,'명제 평가 · ',relationLabels[j.assessment]||j.assessment),h('small',null,'판단 v'+j.revision)),
        h('h3',null,q.question),h('p',{className:'forsic-relation-scope'},'범위 · ',j.scope),
        j.review_required&&h('p',{role:'status',className:'forsic-open-checks'},'재검토 필요 · 현재 답과 채택 근거를 다시 확인해야 합니다.'),
        h('p',{className:'forsic-answer'},j.answer),
        h('p',{className:'forsic-relation-caption'},j.judgments.map(c=>c.assertion_kind==='fact'?'관측 주장':{favored:'유력한 해석',plausible:'가능한 해석',unrated:'해석 · 유력도 미평가'}[c.inference_strength]||'해석').join(' · ')),
        h('details',null,h('summary',null,'판단 근거·가정·반론'),...j.judgments.map(c=>h('section',{key:c.id,className:'forsic-relation-judgment'},
          h('h4',null,c.assertion_kind==='fact'?'자료에서 확인한 관측':{favored:'유력한 해석',plausible:'가능한 해석',unrated:'해석 · 유력도 미평가'}[c.inference_strength]||'해석'),
          c.reasoning_summary&&h('p',null,c.reasoning_summary),list('가정',c.assumptions),list('한계',c.limitations),
          h('div',{className:'forsic-source-links'},...c.source_refs.map((ref,i)=>referenceView(ref,c.id+'-s-'+i))),
          c.counterevidence_refs.length>0&&h('div',null,h('h4',null,'반증 참조'),...c.counterevidence_refs.map((ref,i)=>referenceView(ref,c.id+'-counter-'+i))))),
        list('경쟁 설명',j.alternatives)),list('다음 확인',j.next_checks),
        q.closure_rationale&&h('p',null,'범위 종결 이유 · ',q.closure_rationale),list('다시 열 조건',j.reopen_conditions)),
      h('p',{className:'forsic-relation-caption'},'아래 기록은 이 질문에 연결됩니다. 검사 결과의 지지·반박은 모델의 평가이며, 실행 완료와 질문 종결은 별개입니다.'),
      h('div',{className:'forsic-relation-lanes'},
        h('section',{'aria-label':'질문에 연결된 미션'},h('h3',null,'01 · 판별 미션'),
          !r.missions.length&&h('p',{className:'forsic-empty'},'현재 질문 정의에 적용되는 미션이 없습니다.'),
          ...r.missions.map(m=>h('article',{key:m.id,className:'forsic-relation-node'},h('small',null,relationLabels[m.state]||m.state),h('h4',null,m.target_proposition),h('p',null,m.why_it_matters),h('code',null,m.id)))),
        h('section',{'aria-label':'실제 검사와 평가'},h('h3',null,'02 · 검사와 평가'),
          !r.tests.length&&h('p',{className:'forsic-empty'},'아직 연결된 검사 기록이 없습니다.'),
          ...r.tests.map(t=>h('article',{key:t.id,className:'forsic-relation-node'},
            h('small',null,'실행 · ',relationLabels[t.execution_state]||t.execution_state,' / ',relationLabels[t.assessment_state]||t.assessment_state),
            h('h4',null,t.target_proposition),h('p',null,t.target_scope),
            t.execution_state==='running'&&h('p',null,'시작/진행 영수증 기준이며 현재 프로세스 생존을 보증하지 않습니다.'),
            t.result_ref&&sourceButton(t.result_ref,null,t.id+'-result'),
            ...r.assessments.filter(a=>a.test_ref.id===t.id).map(a=>h('div',{key:a.id,className:'forsic-relation-assessment'},h('strong',null,'검사 평가 · ',relationLabels[a.outcome]||a.outcome),h('p',null,a.reasoning_summary),list('검사의 한계',a.limitations),a.next_check&&h('p',null,'다음 검사 · ',a.next_check))),h('code',null,t.id)))),
        h('section',{'aria-label':'평가에 인용한 관측'},h('h3',null,'03 · 원문 관측'),
          !r.observations.length&&h('p',{className:'forsic-empty'},'정확한 구간에 연결된 관측이 아직 없습니다.'),
          ...r.observations.map(({key,ref,assessmentIds})=>{
            const o=s.observations.find(o=>o.id===ref.id&&o.version===ref.version);
            return h('article',{key,className:'forsic-relation-node'},
              h('small',{title:assessmentIds.join(' / ')},'검사 평가 '+assessmentIds.length+'건에서 참조 · 같은 관측'),o?h('div',null,h('blockquote',null,o.literal),h('p',null,o.interpretation_limit),h('code',null,o.field_pointer+' · '+o.byte_start+'–'+o.byte_end+' bytes'),sourceButton(o.source_ref,o,ref.id)):h('p',null,'관측 참조를 현재 버전에서 확인할 수 없습니다.'));
          }))),
      r.gaps.length>0&&h('section',null,h('h3',null,'남은 쟁점'),...r.gaps.map(g=>h('article',{key:g.id,className:'forsic-relation-node'},h('h4',null,g.original_obligation),h('p',null,g.reason)))),
      h('details',{className:'forsic-relation-history'},h('summary',null,'이전 판단 · '+r.history.length+'건'),
        h('p',null,'현재 질문 정의에 연결된 보존 이력입니다. 아래 판단은 현재 결론이 아닙니다.'),
        ...r.history.map(c=>h('article',{key:c.id},h('strong',null,relationLabels[c.status]),h('p',null,c.statement),h('code',null,c.id))),
        !r.history.length&&h('p',null,'현재 투영에 표시할 대체·철회 기록이 없습니다. 전체 사건 이력이 없다는 뜻은 아닙니다.')));
  }
  function InvestigationRelations({caseId}) {
    const [value,setValue]=R.useState(null),[error,setError]=R.useState(''),[selected,setSelected]=R.useState('');
    const sourcePanel=R.useRef(null);
    const [refresh,setRefresh]=R.useState(0),[sourceRequest,setSourceRequest]=R.useState(null),[source,setSource]=R.useState(null),[sourceError,setSourceError]=R.useState('');
    R.useEffect(()=>{let alive=true;setError('');setValue(null);setSourceRequest(null);
      if(!caseId){setValue(null);return;}
      nativeFetch(scopedURL(base+'/report-driven',caseId)).then(v=>{if(alive){setValue(v);setSelected(old=>v.state.questions.some(q=>q.id===old)?old:v.state.questions[0]?.id||'');}}).catch(()=>{if(alive)setError('조사 관계를 불러오지 못했습니다. 새로고침해 주세요.');});
      return()=>{alive=false;};
    },[caseId,refresh]);
    R.useEffect(()=>{let alive=true;setSource(null);setSourceError('');if(!sourceRequest)return;
      const params=new URLSearchParams(sourceRequest);
      nativeFetch(scopedURL(base+'/source-page?'+params,caseId)).then(v=>{if(alive)setSource(v);}).catch(()=>{if(alive)setSourceError('원문을 열지 못했습니다. 출처·버전이 바뀌었을 수 있으니 조사 관계를 새로고침해 주세요.');});
      return()=>{alive=false;};
    },[caseId,sourceRequest]);
    R.useEffect(()=>{if(sourceRequest)sourcePanel.current?.focus();},[sourceRequest]);
    const onSource=(ref,o)=>setSourceRequest({source_id:ref.id,source_version:ref.version,pointer:o?.field_pointer||'',offset:o?.byte_start||0,limit:o?Math.min(4096,o.byte_end-o.byte_start):2048});
    return h('section',{className:'forsic-board forsic-relations','aria-label':'조사 관계'},
      h('header',{className:'forsic-board-heading'},h('div',null,h('h2',null,'조사 관계')),h('button',{onClick:()=>setRefresh(x=>x+1)},'새로고침')),
      error&&h('p',{role:'alert'},error),!value&&!error&&h('p',{role:'status'},caseId?'조사 관계를 불러오는 중…':'먼저 사건을 선택하세요.'),
      value&&h('div',null,
        value.state.meta.excluded_history_records>0&&h('p',{className:'forsic-relation-caption'},'이전 질문 정의의 기록 ',value.state.meta.excluded_history_records,'건은 보존되어 있으며 현재 관계에서 제외됩니다.'),
        h('label',{className:'forsic-relation-select'},'조사 질문',h('select',{value:selected,onChange:e=>{setSelected(e.target.value);setSourceRequest(null);}},...value.state.questions.map(q=>h('option',{key:q.id,value:q.id},q.question)))),
        !value.state.questions.length&&h('p',{className:'forsic-empty'},'저장된 질문 없음'),
        h(RelationDetail,{key:selected,value,questionId:selected,onSource})),
      sourceRequest&&h('section',{className:'forsic-relation-source',ref:sourcePanel,tabIndex:-1,'aria-label':'선택한 원문 구간','aria-live':'polite'},
        h('div',{className:'forsic-section-heading'},h('h3',null,'보존 원문'),h('button',{onClick:()=>setSourceRequest(null)},'원문 닫기')),
        h('code',null,sourceRequest.source_id+' · '+sourceRequest.pointer),sourceError&&h('p',{role:'alert'},sourceError),!source&&!sourceError&&h('p',null,'원문을 불러오는 중…'),
        source&&h('div',null,h('p',null,'출처 버전 ',source.source_ref.version,' · 보존된 도구 반환의 일부입니다.'),h('pre',null,source.text??JSON.stringify(source.value??source.items,null,2)),
          source.items&&h('div',{className:'forsic-source-links'},...source.items.map(item=>h('button',{key:item.pointer,onClick:()=>setSourceRequest({...sourceRequest,pointer:item.pointer,offset:0,limit:2048})},item.key??item.pointer))),
          source.next_offset!=null&&h('button',{onClick:()=>setSourceRequest({...sourceRequest,offset:source.next_offset,limit:2048})},'다음 구간'))));
  }

  const indicatorLabels={observed:'관측됨',suspicious:'의심',benign:'정상 설명',undetermined:'판단 보류',withdrawn:'철회됨'};
  function IndicatorBoard({caseId,onSource}) {
    const [page,setPage]=R.useState(null), [error,setError]=R.useState('');
    const [query,setQuery]=R.useState(''), [status,setStatus]=R.useState(''), [offset,setOffset]=R.useState(0);
    R.useEffect(()=>{
      let alive=true;
      setPage(null);setError('');
      if(!caseId)return ()=>{alive=false;};
      const load=async()=>{try{
        const result=await nativeFetch(scopedURL(base+'/indicators?query='+encodeURIComponent(query)+'&status='+encodeURIComponent(status)+'&offset='+offset,caseId));
        if(alive){setPage(result);setError('');}
      }catch{if(alive)setError('지표 목록을 가져오지 못했어요.');}};
      load();const timer=setInterval(load,10000);
      return ()=>{alive=false;clearInterval(timer);};
    },[caseId,query,status,offset]);
    return h('section',{className:'forsic-board','aria-label':'IOC와 관측 지표'},
      h('header',{className:'forsic-board-heading'},h('div',null,h('h2',null,'IOC와 관측 지표')),
        caseId&&h('div',{className:'forsic-source-links'},...['csv','json'].map(format=>h('a',{key:format,download:'forsic-indicators.'+format,href:scopedURL(base+'/indicator-export/'+format,caseId)},format.toUpperCase()+' 내려받기')))),
      h('div',{className:'forsic-indicator-filters'},
        h('input',{'aria-label':'지표 검색',placeholder:'값·설명·출처 검색',value:query,onChange:e=>{setQuery(e.target.value);setOffset(0);}}),
        h('select',{'aria-label':'지표 판단',value:status,onChange:e=>{setStatus(e.target.value);setOffset(0);}},h('option',{value:''},'모든 판단'),...Object.entries(indicatorLabels).map(([value,label])=>h('option',{key:value,value},label)))),
      error&&h('p',{role:'status'},error),
      !caseId&&h('p',null,'먼저 사건을 선택해주세요.'),
      page&&h('p',{className:'forsic-empty'},'검색된 지표 ',page.total,'개',page.next_offset!=null||offset>0?' · '+(offset+1)+'–'+(offset+(page.indicators?.length||0))+'번째 표시':'', ' · 내려받기는 사건 전체 목록입니다.'),
      page&&!page.indicators?.length&&h('p',{className:'forsic-empty'},'등록된 지표 없음'),
      ...(page?.indicators||[]).map(r=>h('article',{key:r.indicator_id,className:'forsic-finding-card'},
        h('div',{className:'forsic-finding-meta'},h('span',null,r.type+' · '+(indicatorLabels[r.status]||r.status)),h('small',null,'v'+r.revision)),
        h('code',{className:'forsic-indicator-value'},r.canonical_value),h('p',null,r.summary),
        !!r.limitations?.length&&h('p',{className:'forsic-open-checks'},r.limitations.join(' · ')),
        h('div',{className:'forsic-source-links'},...r.source_refs.map((s,i)=>h('button',{key:s.source_id+s.pointer+s.byte_start+'-'+i,onClick:()=>onSource(s.source_id),title:s.pointer+' · '+s.literal},(s.source_kind==='external_reference'?'외부 평판':'원문')+' · '+(s.source_path||s.source_id.slice(0,8))))),
        h('details',null,h('summary',null,'원래 값과 근거 위치'),h('pre',null,JSON.stringify({raw_values:r.raw_values,source_refs:r.source_refs},null,2))))),
      h('div',{className:'forsic-source-links'},offset>0&&h('button',{onClick:()=>setOffset(Math.max(0,offset-25))},'이전'),
        page?.next_offset!=null&&h('button',{onClick:()=>setOffset(page.next_offset)},'다음')));
  }
  function Records({data,error}) {
    const conversation=useConversation(data?.case?.case_id || '');
    const [selected, setSelected] = R.useState(null), [report, setReport] = R.useState(null);
    const [boardTab,setBoardTab]=R.useState('relations');
    const fetchJSON = (url, options) => nativeFetch(scopedURL(url, data?.case.case_id || ''), options);
    R.useEffect(() => {setSelected(null); setReport(null);}, [data?.case.case_id]);
    const reportBody = report && (report.markdown || '').split(/(\[E:[a-zA-Z0-9]+\])/g).map((part, i) => {
      const match = part.match(/^\[E:([a-zA-Z0-9]+)\]$/);
      const candidates = match ? (report.evidence_ids || []).filter(id => id.startsWith(match[1])) : [];
      return candidates.length === 1 ? h('button', {key: i, onClick: async () => setSelected(await fetchJSON(base + '/evidence/' + candidates[0]))}, '근거 보기') : part;
    });
    const reportsOnly=workspaceRoute().panel==='reports';
    return h('section', {className: 'forsic-records'},
      h('header',{className:'forsic-workspace-heading'},h('div',null,h('small',null,reportsOnly?'보고서':'조사 보드'),h('h1', {id:'case-records'}, data?.case.label || '사건 기록')),
        conversation && h('a',{href:conversation},'대화로 돌아가기 →')),
      h('p', null, data?.case.question),
      !reportsOnly && h('nav',{'aria-label':'조사 보드 보기',className:'forsic-board-tabs'},...['relations','findings','indicators'].map(tab=>h('button',{key:tab,'aria-pressed':boardTab===tab,onClick:()=>setBoardTab(tab)},tab==='relations'?'조사 관계':tab==='findings'?'조사 결과·시간축':'IOC·관측 지표'))),
      !reportsOnly && (boardTab==='relations'?h(InvestigationRelations,{key:data?.case?.case_id||'none',caseId:data?.case?.case_id||''}):boardTab==='indicators'?h(IndicatorBoard,{key:data?.case?.case_id||'none',caseId:data?.case?.case_id||'',onSource:async id=>setSelected(await fetchJSON(base+'/evidence/'+id))}):h(InvestigationBoard,{data,onSource:async id=>setSelected(await fetchJSON(base+'/evidence/'+id))})),
      data && h(ReportDriven, {key:'report-driven-' + data.case.case_id, caseId:data.case.case_id, autoLoad:reportsOnly, onSource:async id => setSelected(await fetchJSON(base + '/evidence/' + id))}),
      error && h('p', {role: 'status'}, error), !reportsOnly && h('h2', null, '조사 과정'),
      !reportsOnly && h('p', null, '최근 작업을 펼치면 확인 결과와 원문을 볼 수 있어요. 전체 대화는 이전 대화에서 확인하세요.'),
      ...(reportsOnly?[]:(data?.events || [])).filter(e => ['tool_result', 'turn_complete', 'api_request_error'].includes(e.kind)).map(e => h('details', {key: e.id},
        h('summary', null, new Date(e.time * 1000).toLocaleString('ko-KR', {timeZone: 'Asia/Seoul'}), ' · ', e.data.path || (e.data.tool === 'forsic_report' ? '보고서 저장' : e.data.tool === 'forsic_list' ? '자료 목록' : e.data.tool === 'forsic_case' ? '조사 범위' : e.kind === 'turn_complete' ? '조사 결과' : '확인 결과'), e.data.error ? ' · 확인 실패' : ''),
        h('pre', null, e.data.summary || JSON.stringify(e.data, null, 2)),
        e.kind === 'tool_result' && h('button', {onClick: () => setSelected(e)}, '원문 결과 열기'))),
      !!data?.reports?.length && h('h2', null, '이전 형식의 보고서'), ...(data?.reports || []).map((name, i) => h('button', {key: name, onClick: async () => { try { setReport(await fetchJSON(base + '/report-text/' + name)); } catch { setReport({name, markdown: '보고서를 열지 못했어요. 잠시 뒤 다시 열어주세요.'}); } }}, name.includes('-executive') ? '임원용 요약' : name.includes('-analyst') ? '분석가용 상세' : i === 0 ? '최근 보고서 열기' : '이전 보고서 ' + i)),
      report && h('div', {className: 'forsic-modal', role: 'dialog', 'aria-modal': true, 'aria-label': '조사 보고서'}, h('button', {onClick: () => setReport(null)}, '닫기'), h('h2', null, '조사 보고서'),
        report.docx && h(ReportDownload, {key:report.docx,url: scopedURL(base + '/download/' + report.docx, data?.case.case_id || ''), name: report.docx}, 'Word 내려받기'),
        report.source_appendix && h(ReportDownload, {key:report.source_appendix,url: scopedURL(base + '/download/' + report.source_appendix, data?.case.case_id || ''), name: report.source_appendix}, '근거 부록 내려받기'),
        report.html ? h('iframe', {title: '조사 보고서 본문', srcDoc: report.html, sandbox: '', style: {width: '100%', height: '68vh', border: 0, background: '#fff'}}) : h('pre', null, ...reportBody),
        ...(report.evidence_ids || []).map((id, i) => h('button', {key: id, onClick: async () => setSelected(await fetchJSON(base + '/evidence/' + id))}, '근거 ' + (i + 1)))),
      selected && h('div', {className: 'forsic-modal', role: 'dialog', 'aria-modal': true, 'aria-label': '원문 결과'},
        h('button', {onClick: () => setSelected(null)}, '닫기'), h('h2', null, selected.data.path || selected.data.tool), h('code', null, 'E:' + selected.id), h('pre', null, JSON.stringify(selected.data, null, 2))));
  }
  function History({data,route,error}) {
    const id=selectedCase() || data?.case?.case_id || '';
    return h('section',{className:'forsic-records forsic-history'},
      h('header',{className:'forsic-workspace-heading'},h('div',null,h('small',null,'내 작업 공간'),h('h1',null,route.panel==='library'?'저장한 사례':'사건 목록'))),
      h('nav',{className:'forsic-view-tabs','aria-label':'이력 종류'},
        ...[['cases','사건 목록'],['library','저장한 사례']].map(([panel,label])=>h('a',{key:panel,href:workspaceURL('history',id,panel),'aria-current':route.panel===panel?'page':undefined},label)),
        h('a',{href:workspaceURL('start'),className:'forsic-action-secondary'},'시작 화면')),
      error && h('p',{role:'status'},error),
      route.panel==='library' ? h(CaseLibrary,{key:id,current:data?.case}) : h(CaseDirectory));
  }
  function Workspace() {
    const {data,error}=useCase(), route=useRoute();
    if(route.section==='new') return h(NewCase);
    return route.section==='history' ? h(History,{key:data?.case?.case_id || '',data,error,route}) : h(Records,{key:data?.case?.case_id || '',data,error});
  }
  window.__HERMES_PLUGINS__.register('forsic', Workspace);
  window.__HERMES_PLUGINS__.registerSlot('forsic', 'sidebar', Sidebar);
  window.__HERMES_PLUGINS__.registerSlot('forsic', 'settings-connections', props => h(ConnectionSettings, {...props,embedded:true}));
  window.__HERMES_PLUGINS__.registerSlot('forsic', 'chat:top', () => {
    const {data}=useCase();
    return data?.case?.question==='질문 선택 대기' ? h('div',{className:'forsic-intake-greeting'},'증거가 등록됐어요. 종합 침해 분석은 아래 대화에 1을 입력하거나, 확인하고 싶은 내용을 자유롭게 질문하세요.') : null;
  });
  window.__HERMES_PLUGINS__.registerSlot('forsic', 'header-left', Identity);
  window.__HERMES_PLUGINS__.registerSlot('forsic', 'tenant-entry', () => h(Lens, {mode:'idle', active:false}));
  document.documentElement?.classList.add('forsic-shell');
  document.title = 'Forsic';
  // Offline contract/visual tests reuse the real component without a model or case writes.
  if (typeof module !== 'undefined') module.exports = {CloseInvestigationAction, requestUUID, NewInvestigationAction, InvestigationAdmissionNotice, tokenCount, SessionUsage, questionRelations, RelationDetail, InvestigationRelations, activityState, Lens, Identity, moods, CaseCard, scopedURL, caseTarget, CaseDirectory, CaseLibrary, Records, History, Sidebar, workspaceRoute, workspaceURL, ConnectionSettings, ContextLengthControl, CaseConversations, NewCase, conversationURL, analysisTitle, settingsBody, investigationView, InvestigationBoard, EvidenceCard, IndicatorBoard, indicatorLabels, ReportDownload};
})();
