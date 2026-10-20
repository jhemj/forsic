import {useEffect, useRef, useState, type ReactNode, type CSSProperties} from 'react';
import {BookOpen, Clock3, FileText, FolderArchive, RefreshCw, TerminalSquare, Wrench, X} from 'lucide-react';
import type {useForsicInspector} from '@/hooks/useForsicInspector';
import {toolDetail} from '@/lib/forsic-tool-detail';
import {ForsicCapabilities} from './ForsicCapabilities';
import {ForsicInvestigation} from './ForsicInvestigation';
import {useForsicInvestigation} from '@/hooks/useForsicInvestigation';
import {ForsicReports} from './ForsicReports';
import {ForsicPaneSeparator} from './ForsicPaneSeparator';
import {ForsicEvidenceCollection} from './ForsicEvidenceCollection';
import {useForsicAvailableWidth, useForsicPaneWidth} from '@/hooks/useForsicPaneWidth';
import './forsic-workbench.css';

type View = 'investigation' | 'reports' | 'evidence' | 'tools' | 'skills';
interface Props {
  children: ReactNode;
  inspector: ReturnType<typeof useForsicInspector>;
  connected: boolean;
  running?: boolean;
  statusText?: string;
  expectedCaseId?: string;
}
const VIEW_LABELS = {investigation: '조사 모니터', reports: '보고서', evidence: '증거 모음', tools: '도구', skills: '스킬'};
export function ForsicWorkbench({children, inspector, connected, running: nativeRunning, statusText, expectedCaseId}: Props) {
  const [view, setView] = useState<View | null>(() => typeof window !== 'undefined' && window.innerWidth > 600 ? 'investigation' : null);
  const [reviewTab, setReviewTab] = useState(false);
  const investigation = useForsicInvestigation({sessionId: inspector.sessionId, profile: inspector.profile, caseId: expectedCaseId, active: inspector.active});
  const caseId = investigation.data?.case_id || expectedCaseId || null;
  const reviewSelected = inspector.active && view === 'investigation' && reviewTab;
  useEffect(() => {
    const detail = {profile: inspector.profile, sessionId: inspector.sessionId, caseId, selected: reviewSelected};
    const publish = () => window.dispatchEvent(new CustomEvent('forsic:review-panel', {detail}));
    publish();
    window.addEventListener('forsic:review-activity-request', publish);
    return () => {
      window.removeEventListener('forsic:review-activity-request', publish);
      window.dispatchEvent(new CustomEvent('forsic:review-panel', {detail: {...detail, selected: false}}));
    };
  }, [inspector.profile, inspector.sessionId, caseId, reviewSelected]);
  const layout = useRef<HTMLDivElement>(null);
  const availableWidth = useForsicAvailableWidth(layout);
  const monitor = useForsicPaneWidth({storageKey: 'forsic.monitor-width', defaultWidth: (availableWidth - 6) / 2, min: 280, max: Math.min(900, availableWidth - 366), storedMin: 280, storedMax: 900});
  const panel = useRef<HTMLElement>(null);
  const tabs = useRef<Partial<Record<View, HTMLButtonElement>>>({});
  useEffect(() => {if (view) panel.current?.focus();}, [view]);
  const closePanel = () => {if (view) tabs.current[view]?.focus(); setView(null);};
  const activeTool = inspector.currentTools.at(-1);
  const running = nativeRunning ?? (inspector.running === true || inspector.currentTools.length > 0);
  const status = statusText || (!connected ? '저장된 대화' : activeTool ? toolDetail(activeTool).label
    : running ? '자료를 살펴보고 있어요' : inspector.running === false ? '다음 대화를 기다리고 있어요' : '조사 기록과 연결되어 있어요');
  const counts = {reports: investigation.data?.reports?.length, evidence: undefined, investigation: investigation.data ? investigation.data.counts?.timeline ?? investigation.data.timeline.length : undefined, tools: undefined, skills: undefined};
  const icons = {investigation: Clock3, reports: FileText, evidence: FolderArchive, tools: Wrench, skills: BookOpen};
  return <div className="forsic-workbench" data-connected={connected}>
    <div className="forsic-workbench-toolbar">
      <div className="forsic-workbench-status"><span className="forsic-workbench-presence" data-running={running && connected} aria-hidden="true"/><span><strong>현재 대화</strong><small role="status">{status}</small></span></div>
      <nav className="forsic-inspector-tabs" aria-label="사건 조사 모니터와 도구">
        {(Object.keys(VIEW_LABELS) as View[]).map(key => {
          const Icon = icons[key];
          return <button type="button" key={key} data-secondary={key === 'tools' || key === 'skills'} ref={node => {if (node) tabs.current[key] = node;}} aria-expanded={view === key} aria-controls={view ? 'forsic-inspector' : undefined} onClick={() => setView(current => current === key ? null : key)}><Icon size={15} aria-hidden="true"/><span>{VIEW_LABELS[key]}</span>{counts[key] !== undefined && <b>{counts[key]}</b>}</button>;
        })}
      </nav>
    </div>
    <div ref={layout} className="forsic-workbench-layout" data-inspector-open={view !== null} style={{'--forsic-monitor-width': `${monitor.width}px`} as CSSProperties}>
      <div className="forsic-workbench-chat">{children}</div>
      {view && <ForsicPaneSeparator label="조사 모니터 너비" controls="forsic-inspector" pane="right" {...monitor} onResize={monitor.setWidth} onReset={monitor.reset} />}
      {<aside hidden={!view} ref={panel} tabIndex={-1} id="forsic-inspector" className="forsic-inspector" aria-label={view ? VIEW_LABELS[view] : '조사 모니터'} onKeyDown={event => {if (event.key === 'Escape') {event.stopPropagation(); closePanel();}}}>
        <header className="forsic-inspector-header"><span className="forsic-inspector-title">{view === 'investigation' ? <Clock3 size={16} aria-hidden="true"/> : <TerminalSquare size={16} aria-hidden="true"/>}<strong>{!view || view === 'investigation' ? '조사 모니터' : view === 'reports' ? '사건 보고서' : view === 'evidence' ? '증거 모음' : `${VIEW_LABELS[view]} 설정`}</strong></span><div>{view !== 'evidence' && view !== 'tools' && view !== 'skills' && <button type="button" aria-label="목록 새로고침" disabled={investigation.loading} data-loading={investigation.loading} onClick={() => void investigation.refresh()}><RefreshCw size={15} aria-hidden="true"/></button>}<button type="button" aria-label="조사 패널 닫기" onClick={closePanel}><X size={17} aria-hidden="true"/></button></div></header>
        <div className="forsic-inspector-scroll">
          <div hidden={view !== 'investigation'}><ForsicInvestigation key={`${inspector.profile}:${investigation.data?.case_id || expectedCaseId || ''}`} data={investigation.data} loading={investigation.loading} error={investigation.error} readSource={investigation.readSource} onReviewTabChange={setReviewTab}/></div>
          {view === 'evidence' && <ForsicEvidenceCollection key={`${inspector.profile}:${inspector.sessionId}:${expectedCaseId || investigation.data?.case_id || ''}`}
            sessionId={inspector.sessionId} profile={inspector.profile} caseId={expectedCaseId || investigation.data?.case_id} active={inspector.active}/>}
          {view === 'reports' && <ForsicReports questions={investigation.data?.questions} reports={investigation.data?.reports} loading={investigation.loading} error={investigation.error} hasCase={Boolean(investigation.data)} profile={inspector.profile}/>}
          {(view === 'tools' || view === 'skills') && <ForsicCapabilities kind={view} profile={inspector.profile} running={running && connected}/>}
        </div>
      </aside>}
    </div>
  </div>;
}
