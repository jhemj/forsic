/** Forsic shell. ChatPage presents the native session through the shared RPC client. */
import { lazy, Suspense, useEffect, useRef, useState, type ReactNode, type CSSProperties } from "react";
import { Link, Navigate, Route, Routes, useLocation } from "react-router";
import { Menu, Settings2, X } from "lucide-react";
import { ForsicSetupGate } from "@/components/ForsicSetup";
import { ForsicEvidenceLocation } from "@/components/ForsicEvidenceLocation";
import { AuthWidget } from "@/components/AuthWidget";
import { MemoryPressureBanner } from "@/components/MemoryPressureBanner";
import { PageHeaderProvider } from "@/contexts/PageHeaderProvider";
import { ForsicTenantGate } from "@/components/ForsicTenantGate";
import { useTenant } from "@/contexts/tenant-context";
import { tenantPath } from "@/lib/forsic-tenant";
import { ForsicMemoryReceipts } from "@/components/ForsicMemoryReceipts";
import { usePageHeader } from "@/contexts/usePageHeader";
import { useProfileScope } from "@/contexts/useProfileScope";
import { useSidebarStatus } from "@/hooks/useSidebarStatus";
import { fetchJSON } from "@/lib/api";
import { latchChatActivation } from "@/lib/chat-activation";
import { PluginPage, PluginSlot, usePlugins } from "@/plugins";
import { analysisTitle } from "@/lib/analysis-title";
import { ForsicPaneSeparator } from "@/components/ForsicPaneSeparator";
import { useForsicAvailableWidth, useForsicPaneWidth } from "@/hooks/useForsicPaneWidth";
import "./forsic-shell.css";

const ChatPage = lazy(() => import("@/pages/ChatPage"));
const ForsicManagementPage = lazy(() => import("@/pages/ForsicManagementPage"));
const ForsicMemoryPage = lazy(() => import("@/pages/ForsicMemoryPage"));
const ForsicEntry = lazy(() => import("@/components/ForsicEntry"));

function Loading({ children = "화면을 준비하고 있어요." }: { children?: ReactNode }) {
  return <div className="forsic-shell-loading" role="status" aria-busy="true">{children}</div>;
}

/** Useful even if the plugin fails; no hidden upstream administration controls. */
export function NavigationFallback({ loading }: { loading: boolean }) {
  const tenant=useTenant();const scoped=(path:string)=>tenant?tenantPath(path,tenant):path;
  return (
    <nav className="forsic-shell-fallback" aria-label="조사 탐색">
      {loading ? <Loading>조사 화면을 불러오고 있어요.</Loading> : <>
        <p role="status">대화와 이력은 여기에서 열 수 있어요.</p>
        <Link to={tenant?.new_url||scoped("/forsic?view=new")}>새 사건</Link>
        <Link to={scoped("/forsic?view=history")}>사건 목록</Link>
        <button type="button" onClick={() => window.location.reload()}>화면 다시 불러오기</button>
      </>}
    </nav>
  );
}

function ProfileKeyedRoutes({ children }: { children: ReactNode }) {
  const { profile } = useProfileScope();
  return <div key={profile || "__own__"} className="contents">{children}</div>;
}

function EntryScreen({mode = "start"}: {mode?: "start" | "new"}) {
  const { setTitle } = usePageHeader();
  const { pathname } = useLocation();
  useEffect(() => {setTitle("조사 시작");return () => setTitle(null);}, [setTitle, pathname]);
  return <ForsicEntry mode={mode} />;
}

function WorkspaceShell() {
  const { pathname, search } = useLocation();
  const tenant = useTenant();
  const params = new URLSearchParams(search);
  const resume = params.get("resume") || "";
  const caseId = params.get("case") || "";
  const managementRoute = pathname.replace(/\/$/, "") === "/management";
  const managementQuery = new URLSearchParams({profile: tenant!.profile_name,tenant_id: tenant!.tenant_id});
  if (caseId) managementQuery.set("case", caseId);
  if (resume) managementQuery.set("resume", resume);
  const chatRoute = pathname.replace(/\/$/, "") === "/chat";
  const needsIntake = chatRoute && (!resume || params.get("fresh") === "1");
  const isChat = chatRoute && !needsIntake;
  const entryView = (!params.get("view") && !caseId && !params.get("panel")) || params.get("view") === "start";
  const workspaceTitle = entryView ? "조사 시작" : params.get("view") === "new" ? "새 조사"
    : params.get("view") === "history" ? (params.get("panel") === "library" ? "저장한 사례" : "사건 목록")
    : params.get("panel") === "reports" ? "보고서" : "조사 보드";
  const identity = `${tenant!.tenant_id}:${tenant!.profile_name}:${caseId}:${resume}`;
  const [scope, setScope] = useState({ identity: "", label: "", title: "분석", status: "loading" });
  useEffect(() => {
    if (!isChat) return;
    let alive = true;
    fetchJSON<{cases: {case_id:string;label:string;session_ids:string[];conversations?:{id:string;session_ids:string[]}[]}[]}>("/api/plugins/forsic/investigations")
      .then(({ cases }) => {
        const item = cases.find(item => item.session_ids?.includes(resume) && (!caseId || item.case_id === caseId));
        if (alive) setScope({ identity, label: item?.label || "", title: item ? analysisTitle(item,resume) : "분석", status: item ? "ready" : "invalid" });
      }).catch(() => { if (alive) setScope({identity,label:"",title:"분석",status:"error"}); });
    return () => {alive=false;};
  }, [isChat, identity, resume, caseId]);
  const validChat = isChat && scope.identity === identity && scope.status === "ready";
  const shell = useRef<HTMLDivElement>(null);
  const shellWidth = useForsicAvailableWidth(shell);
  const navigation = useForsicPaneWidth({storageKey: "forsic.navigation-width", defaultWidth: 432, min: 240, max: Math.min(640, (shellWidth || window.innerWidth) - 676), storedMin: 240, storedMax: 640});
  const menuButton = useRef<HTMLButtonElement>(null);
  const content = useRef<HTMLElement>(null);
  const { loading } = usePlugins();
  const status = useSidebarStatus();
  const [mobileOpen, setMobileOpen] = useState(false);
  const [chatNavigation, setChatNavigation] = useState<HTMLDivElement | null>(null);

  useEffect(() => {
    const sidebar = document.getElementById("app-sidebar");
    if (!sidebar) return;
    const sync = () => setChatNavigation(sidebar.querySelector<HTMLDivElement>("#forsic-model-target"));
    sync();
    // The plugin owns its navigation DOM and registers after the shell mounts.
    const observer = new MutationObserver(sync);
    observer.observe(sidebar, { childList: true, subtree: true });
    return () => observer.disconnect();
  }, []);
  // Do not connect a chat from the board/history. Once visited, keep the view across tabs.
  const [chatMounted, setChatMounted] = useState(validChat);
  useEffect(() => setChatMounted(previous => latchChatActivation(previous, validChat)), [validChat]);
  useEffect(() => setMobileOpen(false), [pathname, search]);
  useEffect(() => {
    if (!mobileOpen) return;
    const sidebar = document.getElementById("app-sidebar");
    content.current?.setAttribute("inert", "");
    const close = (event: KeyboardEvent) => {
      if (event.key === "Escape") {setMobileOpen(false);menuButton.current?.focus();}
      if (event.key === "Tab" && sidebar) {
        const focusable = [menuButton.current, ...sidebar.querySelectorAll<HTMLElement>('a[href],button:not(:disabled),summary,input:not(:disabled),select')].filter((el): el is HTMLElement => !!el && !!el.getClientRects().length);
        const first=focusable[0], last=focusable.at(-1);
        if(event.shiftKey && (document.activeElement === first || !focusable.includes(document.activeElement as HTMLElement))) {event.preventDefault();last?.focus();}
        else if(!event.shiftKey && (document.activeElement === last || !focusable.includes(document.activeElement as HTMLElement))) {event.preventDefault();first?.focus();}
      }
    };
    sidebar?.querySelector<HTMLElement>('a[href],button:not(:disabled)')?.focus();
    document.addEventListener("keydown", close);
    return () => {document.removeEventListener("keydown", close);content.current?.removeAttribute("inert");};
  }, [mobileOpen]);

  return (
    <div ref={shell} className="forsic-shell forsic-app" data-layout-variant="standard" style={{"--forsic-sidebar-width": `${navigation.width}px`} as CSSProperties}>
      <div className="forsic-identity-anchor">
        <PluginSlot name="header-left" fallback={<div className="forsic-identity-fallback"><b>포식이</b><small>FORSIC</small></div>} />
      </div>
      <header className="forsic-mobile-header">
        <button ref={menuButton} type="button" aria-label={mobileOpen ? "메뉴 닫기" : "메뉴 열기"} aria-controls="app-sidebar"
          aria-expanded={mobileOpen} onClick={() => setMobileOpen(open => !open)}>{mobileOpen ? <X /> : <Menu />}</button>
      </header>
      {mobileOpen && <button type="button" className="forsic-menu-backdrop"
        aria-label="메뉴 닫기" onClick={() => setMobileOpen(false)} />}
      <aside id="app-sidebar" className="forsic-app-sidebar" data-open={mobileOpen} aria-label="조사 탐색">
        <div className="forsic-brand-space" aria-hidden="true" />
        <div className="forsic-tenant-header"><span>테넌트(조직)</span><strong>{tenant?.display_name}</strong><Link to="/tenants">조직 전환</Link></div>
        <div className="forsic-navigation-scroll">
          <PluginSlot name="sidebar" fallback={<NavigationFallback loading={loading} />} />
        </div>
        <Link className="forsic-management-link" aria-current={managementRoute ? "page" : undefined} to={`/management?${managementQuery}`}><Settings2 size={17} aria-hidden="true"/><span>관리 설정</span></Link>
        <Link className="forsic-management-link" to={`/memory?${new URLSearchParams({tenant_id:tenant!.tenant_id,profile:tenant!.profile_name})}`}>운영기억</Link>
        <AuthWidget />
      </aside>
      <ForsicPaneSeparator label="탐색 메뉴 너비" controls="app-sidebar" pane="left" {...navigation} onResize={navigation.setWidth} onReset={navigation.reset} />
      <section ref={content} className="forsic-app-content">
        <MemoryPressureBanner status={status} />
        <ForsicMemoryReceipts/>
        <PageHeaderProvider pluginTabs={[{ path: "/forsic", label: workspaceTitle }, {path:"/management",label:"관리 설정"}]}>
          {!isChat && !managementRoute && pathname!=="/memory" && <ForsicEvidenceLocation />}
          <ProfileKeyedRoutes>
            <Suspense fallback={<Loading />}>
              <Routes>
                <Route path="/" element={<EntryScreen />} />
                <Route path="/chat" element={needsIntake ? <EntryScreen mode={resume ? "new" : "start"} /> : null} />
                <Route path="/management" element={<ForsicManagementPage />} />
                <Route path="/memory" element={<ForsicMemoryPage />} />
                <Route path="/sessions" element={<Navigate key={search} to="/forsic?view=history" replace />} />
                <Route path="/forsic" element={entryView ? <EntryScreen /> : params.get("view") === "new" ? <ForsicEntry mode="new" /> : loading ? <Loading /> : <PluginPage name="forsic" />} />
                <Route path="*" element={<Navigate key={search} to="/forsic?view=history" replace />} />
              </Routes>
            </Suspense>
          </ProfileKeyedRoutes>
          {chatMounted && !loading && (!isChat || validChat) && (
            <div data-chat-active={isChat ? "true" : "false"} aria-hidden={!isChat}
              className={isChat ? "forsic-chat-host" : "hidden"}>
              <Suspense fallback={<Loading>대화를 연결하고 있어요.</Loading>}>
                <ChatPage isActive={validChat} navigationTarget={chatNavigation} caseLabel={scope.label} conversationTitle={scope.title} />
              </Suspense>
            </div>
          )}
          {isChat && (loading || scope.identity !== identity) && <Loading>사건과 대화의 연결을 확인하고 있어요.</Loading>}
          {isChat && !loading && scope.identity === identity && scope.status !== "ready" && <div className="forsic-shell-loading" role="alert">
            <p>{scope.status === "invalid" ? "이 대화와 사건의 연결이 일치하지 않습니다." : "사건 정보를 확인하지 못했습니다."}</p>
            <Link to="/forsic?view=history">사건 목록에서 다시 선택</Link>
          </div>}
        </PageHeaderProvider>
      </section>
    </div>
  );
}

export default function App() {
  return <ForsicSetupGate><ForsicTenantGate><WorkspaceShell /></ForsicTenantGate></ForsicSetupGate>;
}
