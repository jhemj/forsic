// @vitest-environment jsdom
import {act} from 'react';
import {createRoot} from 'react-dom/client';
import {afterEach, expect, it, vi} from 'vitest';
import {ForsicMissionFocus} from './ForsicMissionFocus';
import type {InvestigationTaskFocus} from '@/lib/forsic-investigation';
vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
let dispose = () => {};
afterEach(() => {dispose(); document.body.innerHTML = '';});
function render(focus: InvestigationTaskFocus) {
  const host = document.createElement('div'); document.body.append(host);
  const root = createRoot(host);
  act(() => root.render(<ForsicMissionFocus focus={focus}/>));
  dispose = () => {act(() => root.unmount());};
  return host;
}
const focus: InvestigationTaskFocus = {status:'ready',omitted_tasks:2,tasks:[{
  id:'Q-synthetic',revision:4,definition_version:'definition-current',question:'요청이 실행되었나요?',work_state:'active',assessment:'undetermined',review_required:false,
  answer_is_model_assessment:'요청만 관측되었고 실행은 확인되지 않았어요.',scope:'합성 호스트',judgments:[{inference_strength:'possible',assumptions:[],limitations:['부분 기록'],counterevidence_refs:[],counterevidence_ref_count:1}],alternatives:[],next_checks:['실행 기록과 대조'],remaining_gap_count:1,remaining_gap_examples:[{id:'G-gap',reason:'실행 자료 미확인',disposition:'open'}],omitted_checks:1,omitted_details:2,details_call:{tool:'forsic_reporting',arguments:{action:'state',question_id:'Q-synthetic'}},check_counts:{completed:1,pending_or_recheck:2},
  checks:[{mission_id:'M-complete',mission_version:'mv1',state:'assessed',complete:true,tool:'forsic_read',why:'요청 읽기',result_ref:{kind:'source',id:'result-exact',version:'sv1'},assessment_ref:{kind:'assessment',id:'AS-exact',version:'1'},outcome:'inconclusive'},
    {mission_id:'M-pending',mission_version:'mv2',state:'returned',complete:false,tool:'forsic_read',why:'실행 자료 읽기',result_ref:null,assessment_ref:null,outcome:null},
    {mission_id:'M-failed',mission_version:'mv3',state:'failed',complete:false,tool:'forsic_read',why:'실패한 검사',result_ref:null,assessment_ref:null,outcome:null}]}]};
it('keeps qualifications and next check visible while assessed checks collapse with exact handles', () => {
  const host = render(focus);
  expect(host.textContent).toContain('부분 기록');
  expect(host.textContent).toContain('실행 기록과 대조');
  const completed = host.querySelector<HTMLDetailsElement>('.forsic-completed-checks');
  expect(completed?.open).toBe(false);
  expect(completed?.textContent).toContain('result-exact');
  expect(completed?.textContent).toContain('AS-exact');
  expect(completed?.textContent).not.toContain('실패한 검사');
  expect(host.querySelectorAll('li[data-complete="false"]')).toHaveLength(2);
  expect(host.textContent).toContain('평가 대기');
  expect(host.textContent).toContain('생략된 상세 2개, 검사 1개');
});
it('shows recheck as outstanding after a changed judgment', () => {
  const changed = structuredClone(focus); const task = changed.tasks[0];
  task.review_required = true; task.check_counts.completed = 0;
  task.checks.forEach(check => {check.complete = false; check.state = 'recheck';});
  const host = render(changed);
  expect(host.querySelector('.forsic-completed-checks')).toBeNull();
  expect(host.textContent).toContain('현재 판단 재검토 필요');
});
it('distinguishes unknown retained state from no saved questions', () => {
  const host = render({status:'unavailable',tasks:[],omitted_tasks:null});
  expect(host.textContent).toContain('확인하지 못했어요');
  expect(host.textContent).not.toContain('저장된 질문이 없어요');
});
