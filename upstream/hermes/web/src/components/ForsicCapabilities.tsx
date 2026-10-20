import {useEffect, useRef, useState} from 'react';
import {LockKeyhole, RefreshCw, Search} from 'lucide-react';
import {fetchJSON} from '@/lib/api';
import {forsicToolCopy} from '@/lib/forsic-tool-copy';
import {forsicSkillCopy, type ForsicSkillLocale} from '@/lib/forsic-skill-copy';
import './ForsicCapabilities.css';

type TargetOS = 'all' | 'windows' | 'macos' | 'linux';
type Kind = 'tools' | 'skills';
interface Settings {target_os: TargetOS; tools: Record<string, boolean>; skills: Record<string, boolean>}
interface Entry {name: string; description?: string; required: boolean; enabled: boolean; default_enabled: boolean; target_os: string[]; overridden: boolean}
export interface CapabilitySnapshot {
  revision: string; apply_status?: string; settings: Settings; tools: Entry[]; skills: Entry[]; local_admin: boolean;
  native_work_active?: boolean; pending_runtime_update?: boolean;
}
const OS_LABELS = {all: '전체 / 혼합', windows: 'Windows', macos: 'macOS', linux: 'Linux'};
function enabled(entry: Entry, kind: Kind, settings: Settings) {
  return entry.required || (settings[kind][entry.name] ?? (kind === 'tools' || settings.target_os === 'all' || entry.target_os.includes(settings.target_os)));
}
export function ForsicCapabilities({kind, profile, running = false}: {kind: Kind; profile?: string; running?: boolean}) {
  const generation = useRef(0);
  const [snapshot, setSnapshot] = useState<CapabilitySnapshot | null>(null);
  const [draft, setDraft] = useState<Settings | null>(null);
  const [locale, setLocale] = useState<ForsicSkillLocale | null>(null);
  const [query, setQuery] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const url = `/api/plugins/forsic/capabilities?profile=${encodeURIComponent(profile || 'default')}`;
  const dirty = Boolean(draft && snapshot && JSON.stringify(draft) !== JSON.stringify(snapshot.settings));
  useEffect(() => {
    const request = ++generation.current;
    setLoading(true); setSaving(false); setSnapshot(null); setDraft(null); setError(''); setNotice('');
    void fetchJSON<CapabilitySnapshot>(url).then(value => {if (request === generation.current) {setSnapshot(value); setDraft(value.settings);}}).catch(() => {if (request === generation.current) setError('설정을 불러오지 못했어요. 다시 불러와 주세요.');}).finally(() => {if (request === generation.current) setLoading(false);});
    void fetchJSON<ForsicSkillLocale>('/api/plugins/forsic/skills-ui-copy').then(value => {if (request === generation.current) setLocale(value);}).catch(() => {});
    return () => {generation.current++;};
  }, [url]);
  async function refresh() {
    const request = generation.current;
    setLoading(true); setError(''); setNotice('');
    try {const value = await fetchJSON<CapabilitySnapshot>(url); if (request !== generation.current) return; setSnapshot(value); setDraft(value.settings);}
    catch {if (request === generation.current) setError('설정을 불러오지 못했어요. 다시 불러와 주세요.');}
    finally {if (request === generation.current) setLoading(false);}
  }
  async function save() {
    if (!draft || !snapshot?.local_admin || saving) return;
    const request = generation.current;
    setSaving(true); setError(''); setNotice('');
    try {
      const value = await fetchJSON<CapabilitySnapshot>(url, {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({...draft, expected_revision: snapshot.revision})});
      if (request !== generation.current) return;
      setSnapshot(value); setDraft(value.settings); setNotice(value.apply_status === 'applied' ? '설정을 저장하고 조사 도구 구성에 반영했어요.' : '설정을 저장했어요. 조사 도구 구성 반영을 기다리고 있어요.');
    } catch (reason) {
      if (request !== generation.current) return;
      const status = reason && typeof reason === 'object' && 'status' in reason ? reason.status : undefined;
      setError(status === 409 ? '일시정지 후 적용해 주세요. 다른 곳에서 설정이 바뀌었다면 변경을 되돌리고 다시 불러와 주세요. 변경 내용은 보관되어 있어요.' : '저장하지 못했어요. 변경 내용은 보관되어 있어요. 다시 시도해 주세요.');
    } finally {if (request === generation.current) setSaving(false);}
  }
  const blocked = running || snapshot?.native_work_active;
  const editable = snapshot?.local_admin === true && !loading && !saving;
  const items = snapshot?.[kind] ?? [];
  const rows = items.map(item => ({item, copy: kind === 'tools' ? forsicToolCopy(item.name) : forsicSkillCopy(item, locale, 'forsic')}));
  const normalized = query.trim().toLocaleLowerCase();
  const matches = rows.filter(({item, copy}) => `${item.name} ${item.description || ''} ${copy.label} ${copy.description} ${'searchText' in copy ? copy.searchText : ''}`.toLocaleLowerCase().includes(normalized));
  return <section className="forsic-capabilities" aria-label="도구와 스킬 설정" aria-busy={loading || saving}>
    <div className="forsic-capabilities-scope"><strong>{kind === 'tools' ? '사용할 분석 도구' : '사용할 조사 절차'}</strong><span>프로필 전체 · 모든 사건과 대화에 공통 적용</span></div>
    <p className="forsic-inspector-caption">증거의 운영체제를 선택하세요. 포식이가 설치된 컴퓨터의 운영체제와는 별개예요. 사용자가 끈 기능은 모델이 켤 수 없습니다.</p>
    {error && <p className="forsic-inspector-notice" role="alert">{error}</p>}
    {notice && <p className="forsic-inspector-caption" role="status">{notice}</p>}
    {snapshot && draft && <>
      <label className="forsic-capabilities-preset">대상 증거 OS<select aria-label="대상 증거 OS" value={draft.target_os} disabled={!editable} onChange={event => {setDraft({target_os: event.target.value as TargetOS, tools: {}, skills: {}}); setNotice('OS 기본 구성을 미리 보고 있어요. 개별 변경은 초기화되며 저장해야 적용됩니다.');}}>{Object.entries(OS_LABELS).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
      <p className="forsic-inspector-caption">OS를 바꾸면 도구와 스킬의 개별 변경을 기본 구성으로 되돌립니다.</p>
      {!snapshot.local_admin && <p className="forsic-inspector-notice">읽기 전용 · 설치된 컴퓨터에서 설정을 변경할 수 있어요.</p>}
      {blocked && <p className="forsic-inspector-notice" role="status">조사 진행 중 · 일시정지 후 적용</p>}
      {snapshot.apply_status === 'unverified' ? <p className="forsic-inspector-caption">조사 중에는 현재 적용 상태를 확인할 수 없어요. 아래 목록은 저장 설정 기준입니다.</p> : snapshot.pending_runtime_update && <p className="forsic-inspector-caption">저장된 설정의 조사 도구 구성 반영을 기다리고 있어요. 아래 목록은 저장 설정 기준입니다.</p>}
      {snapshot.apply_status === 'unapplied' && !dirty && <button type="button" disabled={!editable || blocked} onClick={() => void save()}>적용 다시 시도</button>}
      <div className="forsic-capabilities-actions"><span>{dirty ? '저장하지 않은 변경' : '저장 설정'} · {items.filter(item => enabled(item, kind, draft)).length}/{items.length}개 켜짐</span><div><button type="button" disabled={!dirty || !editable} onClick={() => {setDraft(snapshot.settings); setNotice(''); setError('');}}>되돌리기</button><button type="button" className="forsic-capabilities-save" disabled={!dirty || !editable || blocked} onClick={() => void save()}>{saving ? '저장 중…' : '저장'}</button></div></div>
    </>}
    <div className="forsic-capabilities-search"><label className="forsic-catalogue-search"><Search size={15}/><input aria-label={kind === 'tools' ? '도구 검색' : '스킬 검색'} placeholder="이름이나 용도로 찾기" value={query} onChange={event => setQuery(event.target.value)}/></label><button type="button" aria-label="설정 다시 불러오기" title={dirty ? '변경을 저장하거나 되돌린 뒤 새로고침하세요' : '설정 다시 불러오기'} disabled={loading || saving || dirty} onClick={() => void refresh()}><RefreshCw size={15}/></button></div>
    {kind === 'tools' && items.some(item => !item.required) && <p className="forsic-inspector-caption">분석 터미널이 켜져 있으면 내부 명령까지 개별 차단되지는 않아요.</p>}
    {matches.map(({item, copy}) => <article className="forsic-capability" key={item.name} data-enabled={draft ? enabled(item, kind, draft) : item.enabled}>
      <div className="forsic-capability-heading"><strong>{copy.label || item.name}</strong><span className="forsic-capability-state">{item.required ? '항상 켜짐' : (draft ? enabled(item, kind, draft) : item.enabled) ? '켜짐' : '꺼짐'}</span></div>
      <div className="forsic-capability-body"><p>{copy.description || item.description || '등록된 요약 설명이 없습니다.'}</p><details className="forsic-capability-technical"><summary>기술 정보</summary><code>{item.name}</code>{item.description && item.description !== copy.description && <p>{item.description}</p>}</details></div>
      <div className="forsic-capabilities-switch"><span>{item.required ? <><LockKeyhole size={13}/>핵심 기능 · 항상 켜짐</> : draft && Object.hasOwn(draft[kind], item.name) ? '개별 설정' : 'OS 기본 설정'}</span><button type="button" role="switch" aria-checked={draft ? enabled(item, kind, draft) : item.enabled} aria-label={`${copy.label || item.name} 사용`} disabled={!editable || item.required} onClick={() => {if (draft) {setDraft({...draft, [kind]: {...draft[kind], [item.name]: !enabled(item, kind, draft)}}); setNotice('');}}}><span/></button></div>
    </article>)}
    {!matches.length && <p className="forsic-inspector-empty">{loading ? '설정을 불러오고 있어요.' : query ? '검색어에 맞는 항목이 없어요.' : '표시할 항목이 없습니다.'}</p>}
  </section>;
}
