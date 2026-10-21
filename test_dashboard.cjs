const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const components = {};
const entrySlots = {};
let entryFetches = 0;
const motionModule = {exports:{}};
const data = {case: {label: '합성 시험'}, events: [], reports: [], notes: [{note_id: 'n1', question: '질문', answer: '답', gaps: ['첫 공백', '둘째 공백'], evidence_ids: ['e1'], timeline: [{display_time: '10:00 KST', description: '시도'}, {display_time: '10:01 KST', description: '결과'}]}]};
let stateIndex = 0;
const React = {
  createElement(type, props, ...children) { return {type, props: props || {}, children}; },
  useState(initial) { return [stateIndex++ === 0 ? data : typeof initial === 'function' ? initial() : initial, () => {}]; },
  useEffect() {},
  useRef(initial) {return {current:initial};},
};
vm.runInNewContext(fs.readFileSync('forsic_plugin/dashboard/dist/index.js', 'utf8'), {
  window: {__HERMES_PLUGIN_SDK__: {React, fetchJSON: async () => {entryFetches++;return data;}},
    __HERMES_PLUGINS__: {register(name, view) { components[name] = view; }, registerSlot(plugin,slot,component) {entrySlots[slot]=component;}}},
  document: {},
  module: motionModule,
  URLSearchParams,
  crypto: {getRandomValues: bytes => require('node:crypto').webcrypto.getRandomValues(bytes)},
});
const connectionSlot=entrySlots['settings-connections']({section:'telegram'});
assert.equal(connectionSlot.type,motionModule.exports.ConnectionSettings);
assert.equal(connectionSlot.props.embedded,true);
assert.equal(connectionSlot.props.section,'telegram');
const hookCountBeforeEntry=stateIndex;
const entry=entrySlots['tenant-entry']();
assert.equal(entry.type,motionModule.exports.Lens);
const entryLens=entry.type(entry.props);
assert.equal(entryLens.props['data-mood'],'idle');
assert.equal(entryLens.props['data-working'],false);
assert.equal(entryLens.children[0].props['aria-label'],'포식이 · 대기');
assert.equal(stateIndex,hookCountBeforeEntry);
assert.equal(entryFetches,0);
console.log('Tenant entry: existing idle Lens, accessible identity and no workspace fetch passed');
const tree = components.forsic();
function uniqueSiblingKeys(node) {
  if (!node || typeof node !== 'object') return;
  const keys = new Set();
  for (const child of node.children || []) {
    const key = child?.props?.key;
    if (key !== undefined) {
      assert(!keys.has(String(key)), `Repeated sibling key: ${key}`);
      keys.add(String(key));
    }
    uniqueSiblingKeys(child);
  }
}
uniqueSiblingKeys(tree);
console.log('Dashboard note/coverage/timeline sibling keys: passed');
assert.equal(motionModule.exports.scopedURL('/api/plugins/forsic/report-text/r?x=1','CASE-one'), '/api/plugins/forsic/report-text/r?x=1&case_id=CASE-one');
assert.equal(motionModule.exports.scopedURL('/api/sessions/one','CASE-one'), '/api/sessions/one');
assert.equal(motionModule.exports.scopedURL('/api/plugins/forsic/investigations','CASE-one'), '/api/plugins/forsic/investigations');
const {activityState, Lens, moods} = motionModule.exports;
const {CaseCard} = motionModule.exports;
const card = CaseCard({item:{title:'사례 <script>', summary:'권한 오류', synthetic:true, analysis_status:'partial', matched_terms:['권한'], source_state:'changed'}, onOpen:() => {}});
uniqueSiblingKeys(card);
assert(JSON.stringify(card).includes('이 보관본은 이전 기록'));
assert(JSON.stringify(card).includes('사례 <script>'));
assert(!JSON.stringify(card).includes('dangerouslySetInnerHTML'));
const event = (kind, time, data={}, id=kind, session='current') => ({kind, time, data, id, session});
const state = events => activityState({events}, '', 100);
const start = (tool, action) => event('tool_start', 90, {tool, arguments:{action}}, 't1');
for (const [tool, action, expected] of [
  ['forsic_search',null,'searching'], ['forsic_read',null,'reading'],
  ['forsic_image_files','read','reading'], ['forsic_image_files','search','searching'],
  ['forsic_hash',null,'verifying'], ['forsic_verify',null,'verifying'],
  ['forsic_report',null,'writing'], ['forsic_note',null,'writing'],
]) assert.equal(state([start(tool,action)]).mode, expected);
const request = event('pre_api_request',96,{api_request_id:'a1'});
const auxiliary = event('pre_auxiliary_call',97,{aux_task:'compression',api_request_id:'aux1'});
assert.equal(state([auxiliary]).active,true);
assert.match(state([auxiliary]).heading,/이전 기록/);
assert.equal(state([auxiliary,event('post_auxiliary_call',98,{aux_task:'compression',api_request_id:'aux1'})]).active,false);
assert.equal(state([auxiliary,event('post_auxiliary_call',98,{aux_task:'title',api_request_id:'aux2'})]).active,true);
assert.equal(state([auxiliary,event('turn_complete',99)]).active,false);
assert.equal(state([start('forsic_reporting','render')]).mode,'writing');
const hit = event('tool_result',95,{tool:'forsic_search',started_id:'t1',matches:[{text:'match'}]},'r1');
assert.equal(state([hit,start('forsic_search')]).mode,'found');
assert.equal(state([hit]).reactionId, state([hit]).reactionId);
assert.notEqual(state([hit]).reactionId, state([{...hit,id:'another-result'}]).reactionId);
assert.equal(state([request,hit,start('forsic_search')]).mode,'found');
assert.equal(activityState({events:[request,hit,start('forsic_search')]},'',102).mode,'thinking');
assert.equal(state([{...hit,time:10},start('forsic_search')]).mode,'idle');
assert.equal(state([{...hit,data:{...hit.data,reused:true}}]).mode,'idle');
assert.equal(state([event('tool_result',99,{lines:[{text:'a'}]})]).mode,'idle');
const failed = event('tool_result',99,{started_id:'t1',error:'not found'});
assert.equal(state([failed,start('forsic_read')]).mode,'uncertain');
assert.equal(state([event('tool_start',100,{tool:'forsic_search'},'t2'),failed,start('forsic_read')]).mode,'searching');
assert.equal(state([event('turn_complete',100),failed]).mode,'complete');
assert.equal(state([event('tool_result',99,{matches:[]})]).mode,'uncertain');
assert.equal(state([event('tool_result',99,{exit_code:1})]).mode,'uncertain');
assert.equal(state([request,event('post_api_request',97,{api_request_id:'a1'})]).mode,'idle');
assert.equal(state([event('turn_complete',99,{},'done','new'),event('tool_start',98,{tool:'forsic_search'},'old','old')]).mode,'complete');
assert.equal(activityState({events:[request]},'connection lost',100).mode,'offline');
for (const mode of Object.keys(moods)) {
  const lens = Lens({mode});
  assert.equal(lens.props['data-mood'],mode);
  uniqueSiblingKeys(lens);
  assert.equal(lens.props['data-working'],false);
}
// The ring follows observed work, not a cheerful/uncertain expression or active goal.
for (const events of [[start('forsic_read')],[request],[auxiliary],[hit],[event('turn_complete',99)],[]]) {
  const activity=state(events);
  assert.equal(Lens(activity).props['data-working'],activity.active);
}
assert.equal(Lens(activityState({events:[request]},'offline',100)).props['data-working'],false);
const css = fs.readFileSync('forsic_plugin/dashboard/dist/style.css','utf8');
assert(css.includes('@media(prefers-reduced-motion:reduce)'));
assert(!css.includes('forsic-peek'));
console.log('Lens: 10 expression renderings, 22 activity transitions and stable reaction IDs passed');
const {investigationView,InvestigationBoard}=motionModule.exports;
const timelineItem={time:'2026-01-01T00:00:00Z',display_time:'2026-01-01 09:00:00 KST',description:'설정 기록',evidence_ids:['e1']};
const boardData={notes:[{note_id:'one',question:'질문 <script>',answer:'확인 범위',evidence_ids:['e1'],timeline:[timelineItem]},
  {note_id:'two',question:'다른 질문',answer:'아직 모름',evidence_ids:['e2'],gaps:['미검토'],timeline:[{...timelineItem},{...timelineItem,evidence_ids:['e2']},{time:'Jan 1 03:00',description:'시각 불명',evidence_ids:['e2']}]}],events:[]};
const before=JSON.stringify(boardData),view=investigationView(boardData);
assert.equal(view.timeline.length,3); // Only exact event+evidence duplicates collapse.
assert.equal(view.timeline.at(-1).stamp,null);
assert.equal(view.sources.length,2);
assert.equal(JSON.stringify(boardData),before);
const clicked=[];
const board=InvestigationBoard({data:boardData,onSource:id=>clicked.push(id)});
uniqueSiblingKeys(board);
function visit(node){if(!node || typeof node!=='object')return;if(node.type===motionModule.exports.EvidenceCard)node.props.onSource(node.props.source.id);for(const child of node.children || [])visit(child);}
visit(board);
assert.deepEqual(clicked,['e1','e2']);
assert(JSON.stringify(board).includes('질문 <script>'));
assert(!JSON.stringify(board).includes('dangerouslySetInnerHTML'));
assert.equal(investigationView({notes:[]}).sources.length,0);
const dates=investigationView({notes:[{timeline:[
  {time:'2026-03-19 11:55',description:'local later'},
  {time:'2018-11-18 18:22:55',description:'local earlier'},
  {time:'2026-03-19 10:19~10:25',description:'local range'},
  {time:'2026-01-01T00:00:00Z',description:'UTC'},
  {time:'2026-01-01T08:00:00+09:00',description:'KST earlier'},
  {time:'unknown',description:'undated'}]}]}).timeline;
assert.deepEqual(Array.from(dates,t=>t.description),['KST earlier','UTC','local earlier','local range','local later','undated']);
assert.equal(dates[2].stamp,null);
console.log('Investigation board: exact timeline dedupe, unknown times, evidence drilldown and no data mutation passed');
const boundTimes={notes:[{timeline:[0,1].map(i=>({...timelineItem,time_source:{field:'log',evidence_id:'e1',pointer:`/lines/${i}/text`,byte_start:0}}))}]};
assert.equal(investigationView(boundTimes).timeline.length,2);
assert.equal(investigationView({notes:[{timeline:[{time:'9999-12-31T23:59:59-12:00',comparable:false}]}]}).timeline[0].stamp,null);
const inferredEvent={...timelineItem,event_ref:'TE-local',time_source:{field:'ctime'},temporal:{
  observed_or_inferred:'inferred',earliest:'2026-01-01T00:00:00Z',latest:'2026-01-01T01:00:00Z',
  confidence_basis:'관련 로그와 상태 변경 기록을 비교함',clock_domain:'host-a',timezone_basis:'로그의 UTC',year_basis:'수집 문맥',assumptions:['복사 시각과 실제 실행을 구별함']}};
const inferredData={notes:[{note_id:'a',timeline:[inferredEvent]},{note_id:'b',timeline:[inferredEvent]}]};
const inferredView=investigationView(inferredData);
assert.equal(inferredView.timeline.length,2);
assert(inferredView.timeline.every(t=>t.inferred && t.stamp===null));
const inferredTree=JSON.stringify(InvestigationBoard({data:inferredData,onSource:()=>{}}));
assert(inferredTree.includes('가정에 따른 추정 · 시간 관계'));
assert(inferredTree.includes('복사 시각과 실제 실행을 구별함'));
assert(inferredTree.includes('2026-01-01T01:00:00Z'));
assert.equal(motionModule.exports.indicatorLabels.observed,'관측됨');
assert.equal(motionModule.exports.indicatorLabels.benign,'정상 설명');
assert.equal(motionModule.exports.indicatorLabels.withdrawn,'철회됨');
const savedUseState=React.useState;
let indicatorState=0;
React.useState=initial=>[indicatorState++===0?{total:1,next_offset:null,indicators:[{indicator_id:'IOC-test',type:'url',canonical_value:'https://example.test/<script>',raw_values:['https://example.test/<script>'],status:'observed',revision:1,summary:'관측됨',limitations:[],source_refs:[{source_id:'e1',pointer:'/text',byte_start:0,source_kind:'local_observation',source_path:'log',literal:'https://example.test/<script>'}]}]}:initial,()=>{}];
const indicatorClicks=[];
const indicatorTree=motionModule.exports.IndicatorBoard({caseId:'CASE-synthetic',onSource:id=>indicatorClicks.push(id)});
uniqueSiblingKeys(indicatorTree);
const rendered=JSON.stringify(indicatorTree);
assert(rendered.includes('IOC와 관측 지표'));
assert(rendered.includes('case_id=CASE-synthetic'));
assert(!rendered.includes('dangerouslySetInnerHTML'));
function clickSource(node){if(!node||typeof node!=='object')return;if(node.type==='button'&&node.props.title?.includes('/text'))node.props.onClick();for(const child of node.children||[])clickSource(child);}
clickSource(indicatorTree);
assert.deepEqual(indicatorClicks,['e1']);
React.useState=savedUseState;
console.log('Indicator board: passive case-bound downloads, literal values and source drilldown passed');
const body=motionModule.exports.settingsBody({llm:{base_url:'http://local/v1',model:'qwen'},jev:{base_url:'http://future/v1',enabled:false},gti:{enabled:true,key_configured:true,allow_public_network_indicators:false}},'',false);
assert.equal(body.gti_api_key,'');
assert.equal(body.llm_base_url,'http://local/v1');
assert.equal(body.jev_base_url,'http://future/v1');
assert.equal(body.gti_public_network,false);
assert(!Object.hasOwn(body,'key_configured'));
assert(!Object.hasOwn(body,'jev_enabled'));
assert.equal(body.telegram_assistant_key,'');
assert.equal(body.telegram_user_key,'');
assert.equal(body.clear_telegram_assistant_key,false);
assert.equal(body.telegram_group,null);
const telegramBody=motionModule.exports.settingsBody({llm:{base_url:'http://local/v1',model:'qwen'},jev:{base_url:''},gti:{enabled:true,allow_public_network_indicators:false}},'',false,{assistant:'fake-assistant',user:'fake-user',clearUser:true});
assert.equal(telegramBody.telegram_assistant_key,'fake-assistant');
assert.equal(telegramBody.telegram_user_key,'fake-user');
assert.equal(telegramBody.clear_telegram_user_key,true);
const groupedBody=motionModule.exports.settingsBody({llm:{base_url:'http://local/v1',model:'qwen'},jev:{base_url:''},gti:{enabled:false},telegram:{group:'https://t.me/Case_Group'}},'',false);
assert.equal(groupedBody.telegram_group,'https://t.me/Case_Group');
// A cache capacity must never become the default or an implicit saved override.
const settingsBase={llm:{base_url:'http://local/v1',model:'qwen',context_length:null},jev:{base_url:''},gti:{enabled:false}};
assert(!Object.hasOwn(motionModule.exports.settingsBody(settingsBase,'',false),'llm_context_length'));
assert.equal(motionModule.exports.settingsBody({...settingsBase,llm:{...settingsBase.llm,context_changed:true,context_length:64000}},'',false).llm_context_length,64000);
assert.equal(motionModule.exports.settingsBody({...settingsBase,llm:{...settingsBase.llm,context_changed:true,context_length:null}},'',false).llm_context_length,null);
function findNode(node, predicate) {
  if (!node || typeof node !== 'object') return;
  if (predicate(node)) return node;
  for (const child of (node.children || []).flat(Infinity)) {
    const found=findNode(child,predicate);if(found)return found;
  }
}
const contextChanges=[];
const metadata={model:'qwen',base_url:'http://local/v1',minimum_context_length:64000,default_context_length:null,reported_context_length:1048576};
let contextTree=motionModule.exports.ContextLengthControl({llm:settingsBase.llm,metadata,busy:false,onChange:n=>contextChanges.push(n)});
const slider=findNode(contextTree,n=>n.type==='input'&&n.props.type==='range');
const reset=findNode(contextTree,n=>n.type==='button');
assert.equal(slider.props.min,64000);
assert.equal(slider.props.max,1048576);
assert.equal(reset.props['aria-pressed'],true);
assert.equal(findNode(contextTree,n=>n.type==='output').children[0],'모델 기본값');
assert(JSON.stringify(contextTree).includes('실제 기본 문맥 길이는 확인되지'));
assert.deepEqual(contextChanges,[]);
slider.props.onChange({target:{value:'64000'}});
reset.props.onClick();
assert.deepEqual(contextChanges,[64000,null]);
contextTree=motionModule.exports.ContextLengthControl({llm:{...settingsBase.llm,context_length:131072},metadata,busy:false,onChange:()=>{}});
assert.equal(findNode(contextTree,n=>n.type==='input').props.value,131072);
assert.equal(findNode(contextTree,n=>n.type==='button').props['aria-pressed'],false);
contextTree=motionModule.exports.ContextLengthControl({llm:{...settingsBase.llm,model:'other'},metadata,busy:false,onChange:()=>{}});
assert.equal(findNode(contextTree,n=>n.type==='input').props.max,262144); // Old model metadata is not reused.
contextTree=motionModule.exports.ContextLengthControl({llm:settingsBase.llm,metadata:{...metadata,reported_context_length:32000},busy:false,onChange:()=>{}});
assert.equal(findNode(contextTree,n=>n.type==='input').props.min,64000);
assert.equal(findNode(contextTree,n=>n.type==='input').props.max,64000); // Never offer an unsupported native subminimum override.
console.log('Context settings: default/override payloads, unknown defaults, preserved131072, model scope and slider minimum passed');
console.log('Connection settings: blank key preservation, inactive JEV and explicit network opt-in passed');
const {workspaceRoute,workspaceURL,Records,History,CaseDirectory,CaseLibrary}=motionModule.exports;
const {NewInvestigationAction,InvestigationAdmissionNotice}=motionModule.exports;
const available={allowed:true,reason:null,active_case:null};
const occupied={allowed:false,reason:'진행 중인 조사가 있어 새 조사를 만들 수 없습니다.',active_case:{case_id:'CASE-한 글',resume_session_id:'native-one',label:'합성 조사',status:'investigating'}};
const newAction=NewInvestigationAction({availability:available});
assert.equal(newAction.type,'a');assert.equal(newAction.props.href,'/forsic?view=new');
for(const count of [1,2]) {
  const action=NewInvestigationAction({availability:{...available,allowed:count<2,active_count:count,limit:2,available_slots:2-count}});
  assert.equal(action.type,count<2?'a':'button');
  assert(JSON.stringify(action).includes(`동시 조사 ${count} / 2`));
}
assert(!JSON.stringify(NewInvestigationAction({availability:{...occupied,active_count:null,limit:2}})).includes('동시 조사 0'));
for(const props of [{availability:occupied},{availability:null},{availability:available,error:'조회 실패'}]) {
  const action=NewInvestigationAction(props);
  assert.equal(action.type,'button');assert.equal(action.props.disabled,true);assert.equal(action.props.href,undefined);
  assert(action.props.title.length>0);
}
assert.equal(InvestigationAdmissionNotice({availability:available}),null);
const notice=InvestigationAdmissionNotice({availability:occupied});
assert(JSON.stringify(notice).includes(occupied.reason));
const openExisting=findNode(notice,n=>n.type==='a');
const existingParams=new URLSearchParams(openExisting.props.href.split('?')[1]);
assert.equal(existingParams.get('case'),'CASE-한 글');assert.equal(existingParams.get('resume'),'native-one');
console.log('New investigation admission: disabled while occupied/unknown/error, explicit reason and existing conversation navigation passed');
for (const [path,search,hash,section,panel] of [
  ['/chat','','','new','chat'], ['/chat','?resume=one&case=CASE-one','','current','chat'],
  ['/forsic','?case=CASE-one','','current','board'],
  ['/forsic','?view=history&case=CASE-one','','history','cases'],
  ['/forsic','?view=history&case=CASE-one&panel=library','','history','library'],
  ['/forsic','','#case-library','history','library'],
  ['/forsic','','#investigations','history','cases'], ['/sessions','','','history','conversations'],
]) {
  const route=workspaceRoute({pathname:path,search,hash});
  assert.equal(route.section,section); assert.equal(route.panel,panel);
}
assert.equal(new URLSearchParams(workspaceURL('history','CASE-한 글','library').split('?')[1]).get('case'),'CASE-한 글');
function types(node,result=[]) {if(node && typeof node==='object'){result.push(node.type);for(const child of node.children || [])types(child,result);}return result;}
const records=Records({data,error:''});
assert(types(records).includes(motionModule.exports.InvestigationRelations));
assert(!types(records).includes(CaseLibrary));
assert(!types(records).includes(CaseDirectory));
const history=History({data,route:{panel:'cases'},error:''});
assert(types(history).includes(CaseDirectory));assert(!types(history).includes(InvestigationBoard));assert(!types(history).includes(CaseLibrary));
const library=History({data,route:{panel:'library'},error:''});
assert(types(library).includes(CaseLibrary));assert(!types(library).includes(InvestigationBoard));assert(!types(library).includes(CaseDirectory));
console.log('Navigation: current/history/library isolation, legacy bookmarks and case context preservation passed');
assert.equal(motionModule.exports.caseTarget({pathname:'/chat',search:''}),null);
assert.equal(motionModule.exports.caseTarget({pathname:'/chat',search:'?case=old&resume=old&fresh=1'}),null);
assert.equal(motionModule.exports.caseTarget({pathname:'/chat',search:'?resume=one'}).session,'one');
assert.equal(motionModule.exports.caseTarget({pathname:'/forsic',search:'?view=history&case=one'}).caseId,'one');
stateIndex=0; data.case.case_id='CASE-one'; data.case.scope='/evidence/case-one';
const sidebar=motionModule.exports.Sidebar();
assert(sidebar.children.some(n=>n?.props?.['aria-label']==='증거 경로'));
assert(sidebar.children.some(n=>n?.props?.['aria-label']==='사건 탐색'));
assert(!JSON.stringify(sidebar).includes('작업 공간'));
uniqueSiblingKeys(sidebar);
stateIndex=1;
const conversations=motionModule.exports.CaseConversations({item:{case_id:'CASE-one',conversations:[{id:'one',resume_session_id:'tip',title:'검토 대화',session_ids:['one','tip'],last_activity_at:1}]},currentSession:'tip'});
const links=[];function collect(n){if(!n||typeof n!=='object')return;if(n.type==='a')links.push(n);for(const c of n.children||[])collect(c);}collect(conversations);
assert.equal(links.length,1);
const target=new URLSearchParams(links[0].props.href.split('?')[1]);
assert.equal(target.get('case'),'CASE-one');assert.equal(target.get('resume'),'tip');assert.equal(links[0].props['aria-current'],'page');
assert.equal(workspaceRoute({pathname:'/forsic',search:'?view=new'}).panel,'intake');
assert.equal(workspaceRoute({pathname:'/forsic',search:'?view=current&case=one&panel=reports'}).panel,'reports');
assert.equal(motionModule.exports.caseTarget({pathname:'/chat',search:'?case=one&resume=tip'}).session,'tip');
console.log('Case navigation: literal evidence scope, case-bound conversations and separate intake passed');

const {questionRelations,RelationDetail}=motionModule.exports;
const q={id:'q',question:'질문 <script>',work_state:'scoped_closed',closure_rationale:'추가 검사 가치 낮음'};
const j={answer:'현재 정정된 판단',assessment:'undetermined',revision:3,scope:'한정 범위',review_required:true,judgments:[],alternatives:['경쟁 설명'],next_checks:[],reopen_conditions:['새 자료']};
const relationData={judgments:{q:j},state:{questions:[q],sources:[],claims:[{id:'old',status:'retracted',adoption_receipt:'receipt',statement:'철회된 이전 답'},{id:'foreign',status:'superseded',adoption_receipt:'other',statement:'다른 질문'}],missions:[{id:'m',question_ref:{id:'q'},state:'completed'}],tests:[{id:'t',question_ref:{id:'q'},execution_state:'returned',assessment_state:'assessed'}],assessments:[{id:'a',test_ref:{id:'t'},adoption_receipt:'receipt',outcome:'inconclusive',observation_refs:[]}],observations:[],gaps:[]}};
const untouched=JSON.stringify(relationData), relations=questionRelations(relationData,'q');
assert.equal(relations.history.length,1);assert.equal(relations.history[0].id,'old');assert.equal(questionRelations(relationData,'missing'),null);
const detail=RelationDetail({value:relationData,questionId:'q',onSource:()=>{}});uniqueSiblingKeys(detail);
const serialized=JSON.stringify(detail);assert(serialized.includes('현재 정정된 판단'));assert(serialized.includes('철회된 이전 답'));assert(serialized.includes('범위 종결'));assert(serialized.includes('반환됨'));assert(serialized.includes('재검토 필요'));
assert(!serialized.includes('dangerouslySetInnerHTML'));assert(!serialized.includes('다른 질문'));assert.equal(JSON.stringify(relationData),untouched);
console.log('Question relationships: shared judgment, bounded ownership, retired history and separate execution/closure passed');
const sourceRef={kind:'source',id:'src',version:'s1'}, observationRef={kind:'observation',id:'obs',version:'o1'};
relationData.state.sources=[{id:'src',version:'s1',label:'보존 원문'}];
relationData.state.observations=[{id:'obs',version:'o1',source_ref:sourceRef,field_pointer:'/text',byte_start:0,byte_end:4,literal:'test'}];
relationData.state.assessments[0].observation_refs=[observationRef];
relationData.state.assessments.push({...relationData.state.assessments[0],id:'a2'});
j.judgments=[{id:'current',assertion_kind:'interpretation',source_refs:[observationRef],counterevidence_refs:[]}];
const deduped=questionRelations(relationData,'q');
assert.equal(deduped.observations.length,1);assert.equal(deduped.observations[0].assessmentIds.length,2);
const sourceClicks=[];
const linked=RelationDetail({value:relationData,questionId:'q',onSource:(ref,obs)=>sourceClicks.push([ref,obs])});
function followCitations(node){if(!node||typeof node!=='object')return;if(node.type==='button'&&node.props.title==='src'){assert.equal(node.props.disabled,false);node.props.onClick();}for(const child of node.children||[])followCitations(child);}
followCitations(linked);
assert.equal(sourceClicks.length,2);assert(sourceClicks.every(([ref,obs])=>ref.id==='src'&&ref.version==='s1'&&obs.id==='obs'));
console.log('Relationship citations: observation refs resolve exact source spans; repeated observations are merged passed');
// Cumulative usage is distinct from the active context window and counts cache only once.
const {tokenCount,SessionUsage}=motionModule.exports;
for(const [value,label] of [[0,'0'],[999,'999'],[1000,'1K'],[1250,'1.3K'],[999949,'999.9K'],[999950,'1M'],[7970123,'7.97M'],[10000000,'10M'],[null,'—']])assert.equal(tokenCount(value),label);
const usage={total_tokens:1950000,prompt_tokens:1840000,output_tokens:110000,input_tokens:630000,cache_write_tokens:10000,cache_read_tokens:1200000,auxiliary_tokens:550000};
const originalUsage=JSON.stringify(usage), usageTree=SessionUsage({usage,detail:true});
assert.equal(usageTree.children[0].type,'summary');
assert.equal(usageTree.children[0].children[0],'세션 누적 1.95M 토큰');
assert.equal(usageTree.children[1].children[0],'입력 1.84M 토큰 · 출력 110K 토큰 · 캐시 1.2M 토큰');
assert(!JSON.stringify(usageTree).includes('새 입력'));
assert(!JSON.stringify(usageTree).includes('보조 호출'));
assert(!JSON.stringify(usageTree).includes('현재 문맥 크기'));
assert.equal(JSON.stringify(usage),originalUsage);
assert(JSON.stringify(SessionUsage({usage:null})).includes('기록 없음'));
console.log('Session token usage: K/M boundaries, cache subset and unknown accounting passed');

// HTTP LAN supplies getRandomValues but omits secure-context-only randomUUID.
const {requestUUID}=motionModule.exports;
assert.equal(requestUUID({getRandomValues:bytes=>{bytes.fill(255);return bytes;}}),'ffffffff-ffff-4fff-bfff-ffffffffffff');
assert.equal(requestUUID({getRandomValues:bytes=>{bytes.fill(0);return bytes;}}),'00000000-0000-4000-8000-000000000000');
assert.equal(requestUUID({randomUUID:()=> 'native-uuid'}),'native-uuid');
assert.throws(()=>requestUUID({}),/Secure random generation/);
assert.match(requestUUID(),/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
console.log('HTTP LAN cryptographic UUID fallback: component mounts and RFC version/variant passed');

// Removed inactive JEV editing UI preserves existing legacy saved values.
assert(!fs.readFileSync('forsic_plugin/dashboard/dist/index.js', 'utf8').includes("['jev','JEV · 비활성']"));
assert(!fs.readFileSync('forsic_plugin/dashboard/dist/index.js', 'utf8').includes("hidden:tab!=='jev'"));

// Execute the shipped component with synthetic state and a stubbed transport only.
async function testInvestigationClosure() {
  const hooks=[], calls=[], dispatched=[], exports={exports:{}}, slots={};
  let cursor=0, confirmed=false, reply, reject, closes=0;
  const hookReact={...React,
    useState(initial) {const index=cursor++;if(!(index in hooks))hooks[index]=typeof initial==='function'?initial():initial;return [hooks[index],value=>{hooks[index]=value;}];},
    useRef(initial) {const index=cursor++;if(!(index in hooks))hooks[index]={current:initial};return hooks[index];},
  };
  const browser={location:{pathname:'/chat',search:'?profile=selected-investigator&case=synthetic-case&resume=synthetic-session'},
    __HERMES_INITIAL_PROFILE__:'served-investigator',
    confirm:message=>{assert(message.includes('사용자 종료'));assert(message.includes('보존'));return confirmed;},
    dispatchEvent:event=>dispatched.push(event.type),
    __HERMES_PLUGIN_SDK__:{React:hookReact,fetchJSON:async (url,options)=>{calls.push({url,options});return await new Promise((resolve,fail)=>{reply=resolve;reject=fail;});}},
    __HERMES_PLUGINS__:{register(name,view){slots[name]=view;},registerSlot(){}},
  };
  vm.runInNewContext(fs.readFileSync('forsic_plugin/dashboard/dist/index.js','utf8'),{window:browser,document:{},module:exports,URLSearchParams,Event:class {constructor(type){this.type=type;}},crypto:require('node:crypto').webcrypto});
  const {CloseInvestigationAction,InvestigationAdmissionNotice}=exports.exports;
  const active={case_id:'synthetic-case',resume_session_id:'synthetic-session',goal_id:'synthetic-goal',closable:true,label:'합성 조사'};
  const availability={allowed:false,reason:'차단 이유',active_case:active};
  const render=()=>{cursor=0;return CloseInvestigationAction({active,onClosed:()=>closes++});};
  let action=render();
  await findNode(action,n=>n.type==='button').props.onClick();
  assert.equal(calls.length,0); // Cancel never reaches the server.
  confirmed=true;
  const pending=findNode(action,n=>n.type==='button').props.onClick();
  const duplicate=findNode(action,n=>n.type==='button').props.onClick();
  await duplicate;
  assert.equal(calls.length,1);
  assert.equal(findNode(render(),n=>n.type==='button').props.disabled,true);
  assert.equal(closes,0);assert.equal(dispatched.length,0);
  const payload=JSON.parse(calls[0].options.body);
  assert.equal(calls[0].url,'/api/plugins/forsic/investigations/synthetic-case/close');
  assert.equal(calls[0].options.method,'POST');assert.equal(payload.confirmed,true);
  assert.equal(payload.profile,'selected-investigator');
  assert.equal(payload.goal_id,active.goal_id);assert.equal(payload.session_id,active.resume_session_id);
  assert.match(payload.request_id,/^[0-9a-f-]{36}$/);
  reject(new Error('busy'));await pending;
  action=render();
  assert.equal(findNode(action,n=>n.type==='button').props.disabled,false);
  assert(findNode(action,n=>n.props?.role==='alert'));
  const retry=findNode(action,n=>n.type==='button').props.onClick();
  assert.equal(JSON.parse(calls[1].options.body).request_id,payload.request_id);
  reply({status:'closed'});await retry;
  assert.equal(closes,1);assert.deepEqual(dispatched,['forsic:investigation-closed']);
  browser.location.pathname='/';
  const entry=InvestigationAdmissionNotice({availability});
  assert(JSON.stringify(entry).includes('기존 조사 열기'));
  // SPA navigation retains the sidebar instance without a popstate event.
  browser.location.pathname='/chat';
  const compact=InvestigationAdmissionNotice({availability});
  assert.equal(compact.type,CloseInvestigationAction);
  assert.equal(InvestigationAdmissionNotice({availability:{...availability,allowed:true,active_count:1,limit:2}}).type,CloseInvestigationAction);
  assert(!JSON.stringify(compact).includes('기존 조사 열기'));
  assert(!JSON.stringify(compact).includes('차단 이유'));
  browser.location.search='?case=other-case&resume=other-session';
  assert.equal(InvestigationAdmissionNotice({availability}),null);
  assert.equal(InvestigationAdmissionNotice({availability:null,error:'offline'}),null);
  browser.location.pathname='/forsic';
  assert(JSON.stringify(InvestigationAdmissionNotice({availability})).includes('기존 조사 열기'));
  cursor=0;assert.equal(CloseInvestigationAction({active:{...active,closable:false}}),null);
  console.log('Investigation closure: cancellation, confirmation, scoped POST, pending guard, stable retry, acknowledged refresh and compact chat action passed');
}
testInvestigationClosure().catch(error=>{console.error(error);process.exitCode=1;});
