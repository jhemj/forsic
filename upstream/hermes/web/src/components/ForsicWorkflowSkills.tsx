import {useState} from 'react';
import type {WorkflowSkillProposal} from '@/lib/forsic-management';
import './forsic-workflow-skills.css';

interface Props {
  items: WorkflowSkillProposal[]; editable: boolean; caseId?: string;
  onReview: (review: {title: string; action: string; fields: Record<string, unknown>}) => void;
}
const states: Record<string, string> = {review_pending: '검토 대기', published: '스킬 저장됨', rejected: '제안 반려', withdrawn: '제안 철회', not_requested: '사용 선택 안 됨', applied: '사용 가능', pending: '안전한 적용 대기', selected: '사용 선택됨', registered: '등록됨', not_saved: '미등록', not_observed: '실제 효과 미확인'};
const label = (value: string) => states[value] || value;

function Proposal({item, editable, caseId, onReview}: Omit<Props, 'items'> & {item: WorkflowSkillProposal}) {
  const [content, setContent] = useState(item.draft.skill_md);
  const [scope, setScope] = useState<'case' | 'default'>(caseId ? 'case' : 'default');
  const changed = content !== item.draft.skill_md;
  const pending = item.status === 'review_pending';
  const fields = {proposal_id: item.id, proposal_revision: item.revision};
  return <article className="forsic-workflow-skill" aria-label={`스킬 제안 ${item.draft.name}`}>
    <header><div><small>{item.target ? '기존 절차 개선' : '새 절차'} · v{item.draft.version}</small><h3>{item.draft.name}</h3></div><span>{label(item.status)}</span></header>
    <p>{item.draft.description}</p>
    <dl><dt>제안 이유</dt><dd>{item.reason}</dd><dt>사용 조건</dt><dd>{item.applicability}</dd><dt>한계</dt><dd>{item.limitations}</dd></dl>
    <p className="forsic-management-caption">성공한 실행 기록 {item.origin.receipts.length}건 · {label(item.validation.functional)} · {label(item.activation.status)}</p>
    <details><summary>출처와 검증</summary><p>출처 사건: {item.origin.case_id}</p><ul>{item.origin.receipts.map(ref => <li key={ref.evidence_id}>{ref.tool} · <code>{ref.evidence_id}</code></li>)}</ul><p>구조 검사: {item.validation.structure}</p>{item.validation.lint?.map((issue, index) => <p key={index}>{issue.severity}: {issue.message}</p>)}<small>절차의 적용 범위와 사건 정보 제거 여부를 검토하세요. 실행 성공만으로 분석의 정확성을 보장하지 않습니다.</small></details>
    <details><summary>절차 내용 검토·수정</summary><label>SKILL.md<textarea aria-label={`${item.draft.name} 절차 내용`} spellCheck={false} value={content} disabled={!editable || !pending} onChange={event => setContent(event.target.value)}/></label>{changed && <button disabled={!editable || !pending || !content.trim()} onClick={() => onReview({title: '스킬 초안 수정', action: 'skill.draft', fields: {...fields, draft: {...item.draft, skill_md: content}}})}>수정한 초안 저장 검토</button>}</details>
    {item.target && <details><summary>기존 절차와 변경 비교</summary><pre>{item.diff}</pre></details>}
    {item.target?.builtin && <p className="forsic-management-caption">기본 제공 스킬의 개선 제안입니다. 변경 내용은 검토용으로 보존되며 배포된 스킬을 자동 교체하지 않습니다.</p>}
    {pending && <div className="forsic-management-actions"><button disabled={!editable || changed || Boolean(item.target?.builtin)} onClick={() => onReview({title: '검토한 절차를 스킬로 저장', action: 'skill.publish', fields: {...fields, reviewed_sha256: item.draft_sha256}})}>검토한 스킬 저장</button><button disabled={!editable} onClick={() => onReview({title: '스킬 제안 반려', action: 'skill.reject', fields})}>제안 반려</button></div>}
    {changed && <small>수정한 초안을 먼저 저장한 뒤 다시 검토해 주세요.</small>}
    {item.status === 'published' && <div className="forsic-management-actions"><label>사용 범위<select aria-label={`${item.draft.name} 사용 범위`} value={scope} onChange={event => setScope(event.target.value as 'case' | 'default')} disabled={!editable}><option value="case" disabled={!caseId}>현재 사건</option><option value="default">같은 테넌트의 새 사건</option></select></label><button disabled={!editable} onClick={() => onReview({title: '검토한 스킬 사용 선택', action: 'skill.activate', fields: {...fields, scope}})}>스킬 사용 선택</button></div>}
  </article>;
}
export function ForsicWorkflowSkills({items, ...props}: Props) {
  return <div className="forsic-workflow-skills"><p className="forsic-management-caption">포식이가 반복해서 쓸 만한 절차를 제안합니다. 내용을 검토해 저장하고 사용 범위를 선택하면 기존 스킬과 함께 불러올 수 있어요.</p>{items.map(item => <Proposal key={`${item.id}:${item.revision}`} item={item} {...props}/>)}{!items.length && <p>아직 저장된 스킬 제안이 없어요.</p>}</div>;
}
