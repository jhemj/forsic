/** Forsic product shell. The native ChatPage still owns the PTY and input. */
import { lazy, Suspense, useEffect, useState, type ReactNode } from "react";
import { Link, Navigate, Route, Routes, useLocation } from "react-router";
import { Menu, X } from "lucide-react";
import { AuthWidget } from "@/components/AuthWidget";
import { MemoryPressureBanner } from "@/components/MemoryPressureBanner";
import { PageHeaderProvider } from "@/contexts/PageHeaderProvider";
import { ProfileProvider } from "@/contexts/ProfileProvider";
import { useProfileScope } from "@/contexts/useProfileScope";
import { useSidebarStatus } from "@/hooks/useSidebarStatus";
import { latchChatActivation } from "@/lib/chat-activation";
import { PluginPage, PluginSlot, usePlugins } from "@/plugins";
import "./forsic-shell.css";

const ChatPage = lazy(() => import("@/pages/ChatPage"));
const SessionsPage = lazy(() => import("@/pages/SessionsPage"));

function Loading({ children = "화면을 준비하고 있어요." }: { children?: ReactNode }) {
  return <div className="forsic-shell-loading" role="status" aria-busy="true">{children}</div>;
}

/** Useful even if the plugin fails; no hidden upstream administration controls. */
export function NavigationFallback({ loading }: { loading: boolean }) {
  return (
    <nav className="forsic-shell-fallback" aria-label="조사 탐색">
      {loading ? <Loading>조사 화면을 불러오고 있어요.</Loading> : <>
        <p role="status">조사 메뉴를 불러오지 못했어요. 대화와 이력은 계속 열 수 있어요.</p>
        <Link to="/chat">대화</Link>
        <Link to="/sessions">대화 이력</Link>
        <button type="button" onClick={() => window.location.reload()}>화면 다시 불러오기</button>
      </>}
    </nav>
  );
}

function ProfileKeyedRoutes({ children }: { children: ReactNode }) {
  const { profile } = useProfileScope();
  return <div key={profile || "__own__"} className="contents">{children}</div>;
}

function WorkspaceShell() {
  const { pathname, search } = useLocation();
  const isChat = pathname.replace(/\/$/, "") === "/chat";
  const { loading } = usePlugins();
  const status = useSidebarStatus();
  const [mobileOpen, setMobileOpen] = useState(false);
  // Do not start a PTY from the board/history. Once visited, keep it across tabs.
  const [chatMounted, setChatMounted] = useState(isChat);
  useEffect(() => setChatMounted(previous => latchChatActivation(previous, isChat)), [isChat]);
  useEffect(() => setMobileOpen(false), [pathname, search]);
  useEffect(() => {
    if (!mobileOpen) return;
    const close = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMobileOpen(false);
    };
    document.addEventListener("keydown", close);
    return () => document.removeEventListener("keydown", close);
  }, [mobileOpen]);

  return (
    <div className="forsic-shell forsic-app" data-layout-variant="standard">
      <header className="forsic-mobile-header">
        <button type="button" aria-label="메뉴 열기" aria-controls="app-sidebar"
          aria-expanded={mobileOpen} onClick={() => setMobileOpen(true)}><Menu /></button>
        <b>FORSIC</b>
      </header>
      {mobileOpen && <button type="button" className="forsic-menu-backdrop"
        aria-label="메뉴 닫기" onClick={() => setMobileOpen(false)} />}
      <aside id="app-sidebar" className="forsic-app-sidebar" data-open={mobileOpen} aria-label="조사 탐색">
        <header className="forsic-app-brand">
          <b>FORSIC</b>
          <button type="button" className="forsic-mobile-close" aria-label="메뉴 닫기"
            onClick={() => setMobileOpen(false)}><X /></button>
        </header>
        <PluginSlot name="sidebar" fallback={<NavigationFallback loading={loading} />} />
        <AuthWidget />
      </aside>
      <section className="forsic-app-content">
        <MemoryPressureBanner status={status} />
        <PageHeaderProvider pluginTabs={[{ path: "/forsic", label: "조사 기록" }]}>
          <ProfileKeyedRoutes>
            <Suspense fallback={<Loading />}>
              <Routes>
                <Route path="/" element={<Navigate to="/forsic?view=history" replace />} />
                <Route path="/chat" element={null} />
                <Route path="/sessions" element={<SessionsPage />} />
                <Route path="/forsic" element={loading ? <Loading /> : <PluginPage name="forsic" />} />
                <Route path="*" element={<Navigate to="/forsic?view=history" replace />} />
              </Routes>
            </Suspense>
          </ProfileKeyedRoutes>
          {chatMounted && !loading && (
            <div data-chat-active={isChat ? "true" : "false"} aria-hidden={!isChat}
              className={isChat ? "forsic-chat-host" : "hidden"}>
              <Suspense fallback={<Loading>대화를 연결하고 있어요.</Loading>}>
                <ChatPage isActive={isChat} />
              </Suspense>
            </div>
          )}
          {isChat && loading && <Loading>대화를 연결하고 있어요.</Loading>}
        </PageHeaderProvider>
      </section>
    </div>
  );
}

export default function App() {
  return <ProfileProvider><WorkspaceShell /></ProfileProvider>;
}
