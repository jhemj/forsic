const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const components = {};
const motionModule = {exports:{}};
const data = {case: {label: '합성 시험'}, events: [], reports: [], notes: [{note_id: 'n1', question: '질문', answer: '답', gaps: ['첫 공백', '둘째 공백'], evidence_ids: ['e1'], timeline: [{display_time: '10:00 KST', description: '시도'}, {display_time: '10:01 KST', description: '결과'}]}]};
let stateIndex = 0;
const React = {
  createElement(type, props, ...children) { return {type, props: props || {}, children}; },
  useState(initial) { return [stateIndex++ === 0 ? data : typeof initial === 'function' ? initial() : initial, () => {}]; },
  useEffect() {},
};
vm.runInNewContext(fs.readFileSync('forsic_plugin/dashboard/dist/index.js', 'utf8'), {
  window: {__HERMES_PLUGIN_SDK__: {React, fetchJSON: async () => data},
    __HERMES_PLUGINS__: {register(name, view) { components[name] = view; }, registerSlot() {}}},
  document: {},
  module: motionModule,
  URLSearchParams,
  crypto: require('node:crypto').webcrypto,
});
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
console.log('Connection settings: blank key preservation, inactive JEV and explicit network opt-in passed');
const {workspaceRoute,workspaceURL,Records,History,CaseDirectory,CaseLibrary}=motionModule.exports;
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
const usageTree=SessionUsage({usage,detail:true});
assert(JSON.stringify(usageTree).includes('1.95M'));
assert(JSON.stringify(usageTree).includes('입력 중 캐시 1.2M'));
assert(JSON.stringify(usageTree).includes('새 입력 640K'));
assert(JSON.stringify(usageTree).includes('현재 문맥 크기'));
assert(JSON.stringify(SessionUsage({usage:null})).includes('기록 없음'));
console.log('Session token usage: K/M boundaries, cache subset and unknown accounting passed');
