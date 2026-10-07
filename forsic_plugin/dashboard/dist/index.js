(() => {
  const { React: R, fetchJSON: nativeFetch } = window.__HERMES_PLUGIN_SDK__;
  const h = R.createElement;
  const base = '/api/plugins/forsic';
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
    return session ? '/chat?profile=default&resume='+encodeURIComponent(session)+'&case='+encodeURIComponent(caseId) : '';
  }
  function scopedURL(url, caseId=selectedCase()) {
    return caseId && url.startsWith(base) && !url.includes('/investigations')
      ? url + (url.includes('?') ? '&' : '?') + 'case_id=' + encodeURIComponent(caseId) : url;
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
  names.forsic_reporting = '질문과 근거를 보고서로 정리하';
  names.forsic_skill = '이번 조사에 필요한 절차를 살펴보';
  const moods = {idle:'함께 살펴볼 준비', searching:'탐색 중', reading:'원문 읽는 중', verifying:'무결성 확인 중', thinking:'기록 연결 중', writing:'정리 중', found:'관련 기록 발견', uncertain:'다른 방법을 생각 중', complete:'이번 조사 정리', offline:'상태 연결 확인 중'};
  function activityState(data, error, now = Date.now()/1000) {
    if (error) return {mode:'offline', active:false, heading:'상태 연결을 확인하고 있어요'};
    const all = [...(data?.events || [])].sort((a,b) => b.time-a.time);
    const latest = all.find(e => ['turn_start','turn_complete','tool_start','tool_result','pre_api_request','post_api_request','api_request_error','pre_auxiliary_call','post_auxiliary_call'].includes(e.kind));
    const events = all.filter(e => e.session === latest?.session);
    const finished = events.find(e => e.kind === 'turn_complete');
    const afterFinish = e => !finished || e.time > finished.time;
    const start = events.find(e => e.kind === 'tool_start' && afterFinish(e) && !events.some(r => r.kind === 'tool_result' && r.data.started_id === e.id));
    const request = events.find(e => e.kind === 'pre_api_request' && afterFinish(e) && !events.some(r => ['post_api_request','api_request_error'].includes(r.kind) && r.data.api_request_id === e.data.api_request_id));
    const auxiliary = events.find(e => e.kind === 'pre_auxiliary_call' && (!finished || e.time >= finished.time) && !events.some(r => r.kind === 'post_auxiliary_call' && r.time >= e.time && r.data.aux_task === e.data.aux_task && (!e.data.api_request_id || r.data.api_request_id === e.data.api_request_id)));
    const result = events.find(e => e.kind === 'tool_result');
    let mode = 'idle', heading = finished ? '조사 내용을 대화에 정리했어요' : '함께 조사해 볼까요?';
    if (start) {
      const tool = start.data.tool, action = start.data.arguments?.action;
      mode = ['forsic_hash','forsic_verify'].includes(tool) ? 'verifying'
        : ['forsic_note','forsic_report','forsic_reporting'].includes(tool) || tool === 'forsic_cases' && action === 'save' ? 'writing'
        : ['forsic_read','forsic_read_bytes'].includes(tool) || tool === 'forsic_image_files' && ['read','read_bytes','stat'].includes(action) ? 'reading' : 'searching';
      heading = `${names[tool] || '자료를 확인하'}고 있어요`;
    } else if (request) {
      mode = 'thinking'; heading = '확인한 기록을 연결해 조사 내용을 정리하고 있어요';
    } else if (auxiliary) {
      mode = 'thinking'; heading = auxiliary.data.aux_task === 'compression' ? '다음 조사를 위해 이전 기록을 정리하고 있어요' : '조사 내용을 점검하고 있어요';
    } else if (finished && now-finished.time >= 0 && now-finished.time < 6) {
      mode = 'complete';
    }
    // A fresh result is a brief reaction, never a lasting verdict or an intrusion score.
    if (!start && result && afterFinish(result) && now-result.time >= 0 && now-result.time < 6) {
      const r = result.data;
      const emptySearch = Array.isArray(r.matches) && r.matches.length === 0;
      if (r.error || r.exit_code != null && r.exit_code !== 0 || emptySearch) {
        mode = 'uncertain';
        if (!request) heading = emptySearch ? '이 범위에서는 찾지 못했어요. 다음 확인을 살펴볼게요' : '이번 확인은 끝내지 못했어요. 다음 방법을 살펴볼게요';
      } else if (!r.reused && Array.isArray(r.matches) && r.matches.length > 0) {
        mode = 'found';
        if (!request) heading = '관련 기록을 찾았어요. 내용을 살펴볼게요';
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
  function Identity() {
    const {data,error}=useCase();
    const {mode,active,heading}=activityState(data,error);
    return h('div',{className:'forsic-identity',title:heading},
      h('div',{className:'forsic-identity-character'},h(Lens,{mode,active})),
      h('div',{className:'forsic-identity-copy'},h('strong',null,'포식이'),h('small',null,moods[mode])));
  }
  const conversationURL = (caseId,sessionId) => '/chat?'+new URLSearchParams({profile:'default',case:caseId,resume:sessionId});
  function useDirectory() {
    const [items,setItems]=R.useState([]), [error,setError]=R.useState('');
    R.useEffect(()=>{let alive=true;
      const refresh=async()=>{try {const v=await nativeFetch(base+'/investigations'); if(alive){setItems(v.cases);setError('');}}
        catch {if(alive) setError('사건 목록을 불러오지 못했어요.');}};
      refresh();const timer=setInterval(refresh,5000);return()=>{alive=false;clearInterval(timer);};
    },[]);
    return {items,error};
  }
  function analysisTitle(item,session) {
    const order=[...(item.conversations || [])].sort((a,b)=>(item.session_ids || []).indexOf(a.id)-(item.session_ids || []).indexOf(b.id));
    const index=order.findIndex(c=>c.session_ids.includes(session));
    return index<0 ? '분석' : `${['첫','두','세','네','다섯','여섯','일곱','여덟','아홉','열'][index] || index+1} 번째 분석`;
  }
  function CaseConversations({item,currentSession}) {
    const [busy,setBusy]=R.useState(false), [error,setError]=R.useState('');
    const [requestId]=R.useState(()=>crypto.randomUUID());
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
          h('span',null,analysisTitle(item,c.resume_session_id)),h('small',null,new Date(c.last_activity_at*1000).toLocaleDateString('ko-KR',{month:'short',day:'numeric'}))))),
      !item.conversations?.length && h('p',{className:'forsic-empty'},'아직 이 사건의 대화가 없어요.'),
      error&&h('p',{role:'status'},error));
  }
  function Sidebar() {
    const {data,error}=useCase(), {items,error:directoryError}=useDirectory(), route=useRoute();
    const caseId=data?.case?.case_id || selectedCase(), item=items.find(c=>c.case_id===caseId);
    const params=new URLSearchParams(window.location?.search || ''), currentSession=route.panel==='chat'?params.get('resume'):'';
    const [settings,setSettings]=R.useState(false);
    const scope=data?.case?.scope || item?.scope || '';
    return h('aside',{className:'forsic-sidebar'},
      h('nav',{className:'forsic-global-nav','aria-label':'사건 탐색'},
        h('a',{className:'forsic-nav-item',href:workspaceURL('history'),'aria-current':route.section==='history'&&route.panel!=='library'?'page':undefined},'사건 목록'),
        h('a',{className:'forsic-action-secondary',href:'/forsic?view=new','aria-current':route.section==='new'?'page':undefined},'+ 새 사건')),
      scope && h('section',{className:'forsic-context-card','aria-label':'증거 경로'},
        h('small',null,'증거 경로'),h('strong',null,scope.split('/').filter(Boolean).at(-1) || scope),
        h('details',null,h('summary',null,'전체 경로 보기'),h('code',null,scope)),
        data?.case?.selected_files?.length>0 && h('small',null,'선택한 파일 ',data.case.selected_files.length,'개')),
      caseId && scope ? h('div',{className:'forsic-current-case'},
        h('header',{className:'forsic-case-heading'},h('small',null,'현재 사건'),h('h2',null,(data?.case?.question && data.case.question!=='질문 선택 대기' ? data.case.question : item?.question) || data?.case?.label || item?.label),h('code',null,caseId)),
        item && h(CaseConversations,{key:caseId,item,currentSession}),
        h('nav',{className:'forsic-case-views','aria-label':'이 사건의 결과'},
          h('a',{className:'forsic-nav-item',href:workspaceURL('current',caseId),'aria-current':route.panel==='board'?'page':undefined},'조사 보드'),
          h('a',{className:'forsic-nav-item',href:workspaceURL('current',caseId,'reports'),'aria-current':route.panel==='reports'?'page':undefined},'보고서')))
        : h('p',{className:'forsic-empty'},route.section==='new'?'원본 경로를 등록하면 사건과 첫 대화가 만들어집니다.':'사건을 선택하면 증거 경로와 대화가 여기에 표시됩니다.'),
      directoryError&&h('p',{role:'status'},directoryError),
      h('footer',{className:'forsic-sidebar-footer'},
        h('a',{className:'forsic-nav-item',href:workspaceURL('history','','library'),'aria-current':route.panel==='library'?'page':undefined},'저장한 사례'),
        h('div',{id:'forsic-model-target'}),
        h('button',{type:'button',className:'forsic-nav-item',onClick:()=>setSettings(true)},'설정')),
      settings&&h(ConnectionSettings,{onClose:()=>setSettings(false)}));
  }
  function NewCase() {
    const [path,setPath]=R.useState(''),[busy,setBusy]=R.useState(false),[error,setError]=R.useState('');
    const [request,setRequest]=R.useState(null);
    async function submit(event) {
      event.preventDefault(); if(busy || !path.trim())return;
      const attempt=request?.path===path.trim()?request:{path:path.trim(),request_id:crypto.randomUUID()};
      setRequest(attempt);setBusy(true);setError('');
      try {const result=await nativeFetch(base+'/investigations',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(attempt)});
        window.location.href=conversationURL(result.case_id,result.session_id);
      } catch {setError('경로를 등록하지 못했어요. 접근 가능한 파일·폴더의 전체 경로인지 확인해 주세요.');setBusy(false);}
    }
    return h('section',{className:'forsic-records forsic-new-case'},
      h('header',{className:'forsic-workspace-heading'},h('div',null,h('small',null,'새 사건'),h('h1',null,'어떤 증거를 조사할까요?'),h('p',null,'파일이나 폴더의 경로를 기준으로 사건을 만들고, 그 안에서 포식이와 대화합니다.'))),
      h('form',{onSubmit:submit},h('label',{htmlFor:'forsic-evidence-path'},'증거 경로'),
        h('input',{id:'forsic-evidence-path',value:path,onChange:e=>setPath(e.target.value),placeholder:'/home/사용자/증거/사건폴더',required:true,disabled:busy,autoComplete:'off',spellCheck:false}),
        h('p',null,'원본은 읽기 전용으로 확인합니다. 조사 목적은 다음 대화에서 정할 수 있어요.'),
        error&&h('p',{role:'alert'},error),
        h('button',{type:'submit',className:'forsic-action-primary',disabled:busy||!path.trim()},busy?'경로 확인 중…':'사건 만들고 대화 시작')),
      h('div',{className:'forsic-intake-explanation'},h('h2',null,'사건과 대화'),h('p',null,'같은 증거에 대한 추가 질문은 사건 안에서 새 대화로 이어가세요. 다른 증거를 조사할 때는 새 사건을 만듭니다.')));
  }
  function settingsBody(value, secret, clearKey, telegram={}) {
    return {llm_base_url:value.llm.base_url, llm_model:value.llm.model,
      jev_base_url:value.jev.base_url, gti_enabled:value.gti.enabled,
      gti_public_network:value.gti.allow_public_network_indicators,
      gti_api_key:secret, clear_gti_key:clearKey,
      telegram_assistant_key:telegram.assistant || '', telegram_user_key:telegram.user || '',
      telegram_group:value.telegram?.group ?? null,
      clear_telegram_assistant_key:!!telegram.clearAssistant, clear_telegram_user_key:!!telegram.clearUser};
  }
  function ConnectionSettings({onClose}) {
    const dialog = R.useRef(null);
    const [tab, setTab] = R.useState('llm');
    const [telegram, setTelegram] = R.useState({assistant:'',user:'',clearAssistant:false,clearUser:false});
    const [value, setValue] = R.useState(null), [secret, setSecret] = R.useState('');
    const [clearKey, setClearKey] = R.useState(false), [busy, setBusy] = R.useState(false);
    const [message, setMessage] = R.useState(''), [error, setError] = R.useState('');
    R.useEffect(() => {
      dialog.current?.showModal();
      let live = true;
      nativeFetch(base + '/connections').then(v => {if(live) setValue(v);})
        .catch(() => {if(live) setError('연결 설정을 불러오지 못했어요. 새 설정 기능의 서버 적용 여부를 확인해주세요.');});
      return () => {live=false;};
    }, []);
    function update(section, name, next) {setValue(v => ({...v, [section]:{...v[section], [name]:next}})); setMessage('');}
    async function save(event) {
      event.preventDefault(); setBusy(true); setError(''); setMessage('');
      try {
        const saved = await nativeFetch(base + '/connections', {method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify(settingsBody(value,secret,clearKey,telegram))});
        const telegramChanged = tab === 'telegram' || telegram.assistant || telegram.user || telegram.clearAssistant || telegram.clearUser;
        setValue(saved); setSecret(''); setClearKey(false); setTelegram({assistant:'',user:'',clearAssistant:false,clearUser:false});
        setMessage(telegramChanged ? '텔레그램 설정을 저장했어요. 기존 그룹·토픽은 바꾸지 않았어요. 저장한 그룹은 새 미러를 명시적으로 연결할 때 사용해요.' : saved.llm_pending ? '저장했어요. LLM 변경은 웹 서버를 다음에 시작할 때 적용돼요. 진행 중인 조사는 그대로예요.' : '저장했어요. JEV는 아직 호출하지 않아요.');
      } catch {setError('저장하지 못했어요. API 주소·키·봇 토큰 형식을 확인해주세요. 텔레그램은 비공개 초대 링크 대신 그룹 ID 또는 공개 @아이디를 입력해주세요.');}
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
    return h('dialog', {ref:dialog, className:'forsic-connections', 'aria-label':'연결 설정', onCancel:onClose},
      h('header', null, h('div', null, h('small', null, 'FORSIC'), h('h2', null, '연결 설정')), h('button', {type:'button', onClick:onClose, 'aria-label':'연결 설정 닫기'}, '닫기')),
      h('p', {className:'forsic-connection-intro'}, '조사 모델과 외부 조회 연결을 한곳에서 관리해요.'),
      h('nav', {className:'forsic-connection-tabs', 'aria-label':'연결 종류'}, ...[['llm','LLM'],['jev','JEV · 비활성'],['gti','VT / GTI'],['telegram','텔레그램']].map(([id,label]) =>
        h('button', {type:'button', key:id, 'aria-pressed':tab===id, onClick:() => {setTab(id); setMessage(''); setError('');}},label))),
      value ? h('form', {onSubmit:save, noValidate:true},
        h('section', {hidden:tab!=='llm'}, h('h3', null, 'LLM ', h('span', {className:'forsic-connection-badge'}, '조사 모델')),
          field('API 주소 · OpenAI 호환', 'llm', 'base_url', {type:'url', required:true, placeholder:'http://서버:11434/v1'}),
          field('모델 이름', 'llm', 'model', {required:true, placeholder:'qwen3.8:latest'}),
          h('small', null, '현재 설정: ', value.llm_current.model, ' · ', value.llm_current.base_url),
          h('p', {className:'forsic-connection-hint'}, value.llm_pending ? '변경 대기 · 다음 웹 서버 시작부터 적용돼요.' : '주소·모델 변경은 다음 웹 서버 시작부터 적용돼요. 지금 조사는 바꾸지 않아요.')),
        h('section', {hidden:tab!=='jev'}, h('h3', null, 'JEV ', h('span', {className:'forsic-connection-badge muted'}, '비활성')),
          field('API 주소', 'jev', 'base_url', {type:'url', placeholder:'나중에 사용할 주소 (선택)'}),
          h('p', {className:'forsic-connection-hint'}, '주소만 보관해요. 모델 적재·분류·API 호출은 하지 않아요.')),
        h('section', {hidden:tab!=='gti'}, h('h3', null, 'VT / GTI ', h('span', {className:'forsic-connection-badge'}, value.gti.key_configured ? '키 등록됨' : '키 미등록')),
          h('label', {className:'forsic-connection-check'}, h('input', {type:'checkbox', checked:value.gti.enabled, disabled:busy, onChange:e => update('gti','enabled',e.target.checked)}), '외부 위협 평판 조회 사용'),
          h('p', {className:'forsic-connection-address'}, '공식 API · ', h('code', null, value.gti.base_url)),
          h('label', {className:'forsic-connection-field'}, h('span', null, 'API 키'), h('input', {type:'password', value:secret, autoComplete:'new-password', spellCheck:false, disabled:busy || clearKey, placeholder:value.gti.key_configured ? '등록되어 있어요 · 바꿀 때만 입력' : 'VT/GTI API 키 입력', onChange:e => setSecret(e.target.value)})),
          value.gti.key_configured && h('label', {className:'forsic-connection-check'}, h('input', {type:'checkbox', checked:clearKey, disabled:busy, onChange:e => {setClearKey(e.target.checked); setSecret('');}}), '저장된 키 삭제'),
          h('label', {className:'forsic-connection-check'}, h('input', {type:'checkbox', checked:value.gti.allow_public_network_indicators, disabled:busy, onChange:e => update('gti','allow_public_network_indicators',e.target.checked)}), '공개 IP·도메인 조회도 허용'),
          h('p', {className:'forsic-connection-hint'}, '기본은 파일 해시 조회예요. IP·도메인은 조회만으로 GTI 공유 데이터셋에 포함될 수 있어요. 파일 업로드·URL 재검사는 하지 않아요.'),
          h('button', {type:'button', disabled:busy || !value.gti.key_configured || !!secret || clearKey, onClick:check}, 'GTI 연결 확인'),
          h('small', null, '저장된 키로 공개 시험 해시 1개만 조회해요. 사건 자료·모델 호출은 없어요.')),
        h('section', {hidden:tab!=='telegram'}, h('h3',null,'텔레그램'),
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
        h('footer', null, h('button', {type:'submit', className:'forsic-connection-save', disabled:busy}, busy ? '처리 중…' : '설정 저장')))
        : !error && h('p', null, '설정을 불러오고 있어요…'),
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
      h('div', {className:'forsic-library-heading'}, h('div', null, h('h2', null, '사례 창고'), h('p', null, '이전 사건에서 무엇을 확인했고, 어떤 방법이 도움이 됐는지 찾아보세요.')),
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
      page?.total === 0 && h('p', null, '일치하는 보관 사례가 없어요. 다른 키워드를 쓰거나 조사 노트를 먼저 보관해 주세요.'),
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
    const [query, setQuery] = R.useState(''), [error, setError] = R.useState('');
    const refresh = async () => {try {setItems((await nativeFetch(base + '/investigations')).cases); setError('');}
      catch {setError('사건 목록은 서버에 변경 사항을 반영한 뒤 사용할 수 있어요. 진행 중인 조사는 계속됩니다.');}};
    R.useEffect(() => {refresh(); const timer=setInterval(refresh,5000); return () => clearInterval(timer);}, []);
    const archive = async item => {try {
      await nativeFetch('/api/sessions/' + encodeURIComponent(item.session_id), {method:'PATCH',
        headers:{'Content-Type':'application/json'}, body:JSON.stringify({profile:'default', archived:!item.archived})});
      await refresh();
    } catch {setError('보관 상태를 바꾸지 못했어요.');}};
    const visible = items.filter(item => (filter === 'archived' ? item.archived : !item.archived)
      && (item.label + ' ' + item.title + ' ' + item.case_id).toLowerCase().includes(query.toLowerCase()));
    return h('section', {id:'investigations', className:'forsic-directory'},
      h('div', {className:'forsic-directory-heading'}, h('div', null, h('h2', null, '사건 이력'),
        h('p', null, '사건을 선택하면 마지막 대화로 이어집니다.')),
        h('a', {href:'/chat?fresh=1'}, '+ 새 사건')),
      h('div', {className:'forsic-directory-filters'},
        h('button', {'aria-pressed':filter==='active', onClick:() => setFilter('active')}, '사건 목록'),
        h('button', {'aria-pressed':filter==='archived', onClick:() => setFilter('archived')}, '보관함'),
        h('input', {value:query, onChange:e => setQuery(e.target.value), placeholder:'사건 이름 검색', 'aria-label':'사건 검색'})),
      error && h('p', {role:'status'}, error),
      !error && !visible.length && h('p', null, filter==='archived' ? '보관한 사건이 없어요.' : '등록된 사건이 없어요. 새 사건에서 증거 경로를 등록하세요.'),
      ...visible.map(item => h('article', {key:item.case_id, className:'forsic-investigation'},
        h('a', {className:'forsic-investigation-link',href:conversationURL(item.case_id,item.resume_session_id), 'aria-label':item.label+' 대화 열기'},
          h('div', null, h('strong', null, item.label), h('small', null, item.case_id, item.synthetic ? ' · 합성 시험' : ''),
            h('p', null, item.question),h('code',{className:'forsic-directory-path'},item.scope)),
          h('div', {className:'forsic-investigation-status'},
            h('span', {'data-status':item.status}, {investigating:'조사 중',waiting:'대기',ended:'실행 종료',completed:'실행 종료'}[item.status]),
            h('small', null, '최근 활동 ', new Date(item.last_activity_at*1000).toLocaleString('ko-KR',{timeZone:'Asia/Seoul',month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'})))),
        h('button', {className:'forsic-archive-action',onClick:() => archive(item), 'aria-label':item.archived ? item.label+' 목록으로 복원' : item.label+' 보관', title:item.archived ? '목록으로 복원' : '사건 보관'},
          h('svg',{viewBox:'0 0 24 24',width:18,height:18,fill:'none',stroke:'currentColor',strokeWidth:1.5,'aria-hidden':true},
            h('path',{d:'M4 8H20V20H4Z M3 4H21V8H3Z'}),
            h('path',{d:item.archived?'M12 17V11 M9 14L12 11L15 14':'M9 12H15'}))))));
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
        !value.bundles.length&&h('p',{className:'forsic-empty'},'아직 생성된 보고서가 없어요. 조사 중 정리된 판단과 남은 확인은 아래에서 볼 수 있습니다.'),
        ...value.bundles.map(b=>h('article',{key:b.bundle_id,className:'forsic-report-bundle'},
          h('h3',null,b.stale?'이전 판단의 보존 보고서':'현재 판단의 부분 보고서'),
          h('p',null,b.stale?'판단이 바뀌어 다시 작성할 필요가 있습니다.':'파일 생성과 조사 완료는 다릅니다. 남은 확인과 함께 읽어 주세요.'),
          h('div',{className:'forsic-report-downloads'},...b.files.map(f=>h('a',{key:f.name,className:'forsic-action-secondary',download:f.name,href:scopedURL(base+'/report-bundles/'+b.bundle_id+'/'+f.name,caseId)},
            (f.name.startsWith('executive')?'임원용 ':'실무자용 ')+(f.name.endsWith('.docx')?'Word':'HTML')+' ↓'))),
          h('a',{className:'forsic-report-manifest',download:'manifest.json',href:scopedURL(base+'/report-bundles/'+b.bundle_id+'/manifest.json',caseId)},'파일 검증 목록'))),
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
        const key=JSON.stringify([item.time,item.description,ids,item.time_source?.evidence_id,item.time_source?.field,item.time_source?.entry_path,item.time_source?.pointer,item.time_source?.byte_start]);
        if (!timeline.has(key)) {
          // A timezone-free date can be ordered as written, never as an absolute instant.
          const stamp=item.comparable!==false && /T.*(?:Z|[+-]\d{2}:?\d{2})$/.test(item.time || '') ? Date.parse(item.time) : NaN;
          const local=(item.time || '').match(/^(\d{4}-\d{2}-\d{2})(?:[T ](\d{2}:\d{2})(?::(\d{2}))?)?/);
          timeline.set(key,{...item,key,evidence_ids:ids,stamp:Number.isFinite(stamp)?stamp:null,
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
      {key:'local',label:'시간대 미확인 · 원문 날짜순',items:view.timeline.filter(t=>t.stamp === null && t.writtenDate)},
      {key:'unknown',label:'날짜 미확인',items:view.timeline.filter(t=>t.stamp === null && !t.writtenDate)}
    ];
    return h('section',{className:'forsic-board','aria-label':'조사 보드'},
      h('header',{className:'forsic-board-heading'},h('div',null,h('h2',null,'조사 보드'),h('p',null,'저장된 답변과 연결된 근거를 한눈에 살펴보세요.')),
        h('div',{className:'forsic-board-counts'},h('span',null,'질문 ',view.notes.length),h('span',null,'연결 근거 ',view.sources.length),h('span',null,'시간 기록 ',view.timeline.length))),
      h('div',{className:'forsic-board-columns'},
        h('section',{'aria-label':'현재 조사 결과'},h('h3',null,'현재 조사 결과'),
          !view.notes.length && h('p',{className:'forsic-empty'},'아직 저장된 조사 노트가 없어요. 답과 근거가 정리되면 여기에 나타납니다.'),
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
          !view.timeline.length && h('p',{className:'forsic-empty'},'아직 근거가 연결된 시각 기록이 없어요.'),
          ...timeGroups.filter(group=>group.items.length).map(group=>h('section',{key:group.key,className:'forsic-time-group','aria-label':group.label},
            h('h4',null,group.label),h('ol',null,...group.items.map(t=>h('li',{key:t.key},
            h('time',null,t.display_time || t.time || '시각 미확인'),
            t.time_source?.field && h('small',null,t.time_source.field==='log'?'로그 기록':'파일 '+t.time_source.field),
            h('p',null,t.description),h('div',{className:'forsic-source-links'},...refs(t.evidence_ids))))))))),
      h('section',{'aria-label':'연결된 단서'},h('h3',null,'연결된 단서'),
        !view.sources.length && h('p',{className:'forsic-empty'},'노트에 인용한 근거가 여기에 모입니다.'),
        h('div',{className:'forsic-source-grid'},...view.sources.map(s=>h(EvidenceCard,{key:s.id,source:s,caseId:data?.case?.case_id || '',onSource})))));
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
      h('header',{className:'forsic-board-heading'},h('div',null,h('h2',null,'IOC와 관측 지표'),h('p',null,'어디에서 발견했고 왜 관련 있는지, 원문과 함께 확인하세요.')),
        caseId&&h('div',{className:'forsic-source-links'},...['csv','json'].map(format=>h('a',{key:format,download:'forsic-indicators.'+format,href:scopedURL(base+'/indicator-export/'+format,caseId)},format.toUpperCase()+' 내려받기')))),
      h('div',{className:'forsic-indicator-filters'},
        h('input',{'aria-label':'지표 검색',placeholder:'값·설명·출처 검색',value:query,onChange:e=>{setQuery(e.target.value);setOffset(0);}}),
        h('select',{'aria-label':'지표 판단',value:status,onChange:e=>{setStatus(e.target.value);setOffset(0);}},h('option',{value:''},'모든 판단'),...Object.entries(indicatorLabels).map(([value,label])=>h('option',{key:value,value},label)))),
      error&&h('p',{role:'status'},error),
      !caseId&&h('p',null,'먼저 사건을 선택해주세요.'),
      page&&h('p',{className:'forsic-empty'},'검색된 지표 ',page.total,'개',page.next_offset!=null||offset>0?' · '+(offset+1)+'–'+(offset+(page.indicators?.length||0))+'번째 표시':'', ' · 내려받기는 사건 전체 목록입니다.'),
      page&&!page.indicators?.length&&h('p',{className:'forsic-empty'},'아직 정리된 지표가 없어요. 관측값이 등록되면 여기에 나타납니다.'),
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
    const [boardTab,setBoardTab]=R.useState('findings');
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
      !reportsOnly && h('nav',{'aria-label':'조사 보드 보기',className:'forsic-board-tabs'},...['findings','indicators'].map(tab=>h('button',{key:tab,'aria-pressed':boardTab===tab,onClick:()=>setBoardTab(tab)},tab==='findings'?'조사 결과·시간축':'IOC·관측 지표'))),
      !reportsOnly && (boardTab==='indicators'?h(IndicatorBoard,{key:data?.case?.case_id||'none',caseId:data?.case?.case_id||'',onSource:async id=>setSelected(await fetchJSON(base+'/evidence/'+id))}):h(InvestigationBoard,{data,onSource:async id=>setSelected(await fetchJSON(base+'/evidence/'+id))})),
      data && h(ReportDriven, {key:'report-driven-' + data.case.case_id, caseId:data.case.case_id, autoLoad:reportsOnly, onSource:async id => setSelected(await fetchJSON(base + '/evidence/' + id))}),
      error && h('p', {role: 'status'}, error), !reportsOnly && h('h2', null, '조사 과정'),
      !reportsOnly && h('p', null, '최근 작업을 펼치면 확인 결과와 원문을 볼 수 있어요. 전체 대화는 이전 대화에서 확인하세요.'),
      ...(reportsOnly?[]:(data?.events || [])).filter(e => ['tool_result', 'turn_complete', 'api_request_error'].includes(e.kind)).map(e => h('details', {key: e.id},
        h('summary', null, new Date(e.time * 1000).toLocaleString('ko-KR', {timeZone: 'Asia/Seoul'}), ' · ', e.data.path || (e.data.tool === 'forsic_report' ? '보고서 저장' : e.data.tool === 'forsic_list' ? '자료 목록' : e.data.tool === 'forsic_case' ? '조사 범위' : e.kind === 'turn_complete' ? '조사 결과' : '확인 결과'), e.data.error ? ' · 확인 실패' : ''),
        h('pre', null, e.data.summary || JSON.stringify(e.data, null, 2)),
        e.kind === 'tool_result' && h('button', {onClick: () => setSelected(e)}, '원문 결과 열기'))),
      !!data?.reports?.length && h('h2', null, '이전 형식의 보고서'), ...(data?.reports || []).map((name, i) => h('button', {key: name, onClick: async () => { try { setReport(await fetchJSON(base + '/report-text/' + name)); } catch { setReport({name, markdown: '보고서를 열지 못했어요. 잠시 뒤 다시 열어주세요.'}); } }}, name.includes('-executive') ? '임원용 요약' : name.includes('-analyst') ? '분석가용 상세' : i === 0 ? '최근 보고서 열기' : '이전 보고서 ' + i)),
      report && h('div', {className: 'forsic-modal', role: 'dialog', 'aria-modal': true, 'aria-label': '조사 보고서'}, h('button', {onClick: () => setReport(null)}, '닫기'), h('h2', null, '조사 보고서'),
        report.docx && h('a', {href: scopedURL(base + '/download/' + report.docx, data?.case.case_id || ''), download: report.docx}, 'Word 내려받기'),
        report.source_appendix && h('a', {href: scopedURL(base + '/download/' + report.source_appendix, data?.case.case_id || ''), download: report.source_appendix}, '근거 부록 내려받기'),
        report.html ? h('iframe', {title: '조사 보고서 본문', srcDoc: report.html, sandbox: '', style: {width: '100%', height: '68vh', border: 0, background: '#fff'}}) : h('pre', null, ...reportBody),
        ...(report.evidence_ids || []).map((id, i) => h('button', {key: id, onClick: async () => setSelected(await fetchJSON(base + '/evidence/' + id))}, '근거 ' + (i + 1)))),
      selected && h('div', {className: 'forsic-modal', role: 'dialog', 'aria-modal': true, 'aria-label': '원문 결과'},
        h('button', {onClick: () => setSelected(null)}, '닫기'), h('h2', null, selected.data.path || selected.data.tool), h('code', null, 'E:' + selected.id), h('pre', null, JSON.stringify(selected.data, null, 2))));
  }
  function History({data,route,error}) {
    const id=selectedCase() || data?.case?.case_id || '';
    return h('section',{className:'forsic-records forsic-history'},
      h('header',{className:'forsic-workspace-heading'},h('div',null,h('small',null,'FORSIC'),h('h1',null,'사건 목록'),h('p',null,'이전 사건을 이어 보거나, 참고할 조사 사례를 찾아보세요.'))),
      h('nav',{className:'forsic-view-tabs','aria-label':'이력 종류'},
        ...[['cases','사건 목록'],['library','저장한 사례']].map(([panel,label])=>h('a',{key:panel,href:workspaceURL('history',id,panel),'aria-current':route.panel===panel?'page':undefined},label)),
        h('a',{href:'/forsic?view=new',className:'forsic-action-secondary'},'+ 새 사건')),
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
  window.__HERMES_PLUGINS__.registerSlot('forsic', 'chat:top', () => {
    const {data}=useCase();
    return data?.case?.question==='질문 선택 대기' ? h('div',{className:'forsic-intake-greeting'},'증거가 등록됐어요. 종합 침해 분석은 아래 대화에 1을 입력하거나, 확인하고 싶은 내용을 자유롭게 질문하세요.') : null;
  });
  window.__HERMES_PLUGINS__.registerSlot('forsic', 'header-left', Identity);
  document.documentElement?.classList.add('forsic-shell');
  document.title = 'Forsic · 포식이와 조사하기';
  // Offline contract/visual tests reuse the real component without a model or case writes.
  if (typeof module !== 'undefined') module.exports = {activityState, Lens, Identity, moods, CaseCard, scopedURL, caseTarget, CaseDirectory, CaseLibrary, Records, History, Sidebar, workspaceRoute, workspaceURL, ConnectionSettings, CaseConversations, NewCase, conversationURL, analysisTitle, settingsBody, investigationView, InvestigationBoard, EvidenceCard, IndicatorBoard, indicatorLabels};
})();
