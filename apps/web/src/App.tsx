import type { Session } from "@supabase/supabase-js";
import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { AuthPanel } from "./components/AuthPanel";
import { Layout } from "./components/Layout";
import { PasswordResetPanel } from "./components/PasswordResetPanel";
import { checkIsAdmin } from "./lib/api";
import { authLinkIssue, clearAuthLink, isPasswordRecoveryLink } from "./lib/auth";
import { useLanguage } from "./lib/language";
import { isConfigured, supabase, supabaseUrl } from "./lib/supabase";

const AdminPage = lazy(() => import("./pages/AdminPage").then((module) => ({ default: module.AdminPage })));
const DashboardPage = lazy(() => import("./pages/DashboardPage").then((module) => ({ default: module.DashboardPage })));
const JobPage = lazy(() => import("./pages/JobPage").then((module) => ({ default: module.JobPage })));
const NewAnalysisPage = lazy(() => import("./pages/NewAnalysisPage").then((module) => ({ default: module.NewAnalysisPage })));
const ReportPage = lazy(() => import("./pages/ReportPage").then((module) => ({ default: module.ReportPage })));
const ExperimentWorkspacePage = lazy(() => import("./pages/ExperimentWorkspacePage").then((module) => ({ default: module.ExperimentWorkspacePage })));
const SharedReportPage = lazy(() => import("./pages/SharedReportPage").then((module) => ({ default: module.SharedReportPage })));

function Loading() {
  const { text } = useLanguage();
  return <div className="panel p-12 text-center text-muted">{text("加载页面…", "Loading page…")}</div>;
}

function AdminTicketLogin() {
  const { text } = useLanguage();
  const started = useRef(false);
  const [error, setError] = useState("");
  useEffect(() => {
    const client = supabase;
    if (started.current || !client || !supabaseUrl) return;
    started.current = true;
    const token = new URLSearchParams(window.location.hash.slice(1)).get("admin_ticket");
    if (!token) { setError(text("管理员二维码无效", "Invalid administrator QR code")); return; }
    void client.functions.invoke("admin-qr-login", { body: { token } }).then(async ({ data, error: exchangeError }) => {
      if (exchangeError) throw exchangeError;
      const accessToken = data?.accessToken;
      const refreshToken = data?.refreshToken;
      if (typeof accessToken !== "string" || typeof refreshToken !== "string") {
        throw new Error(text("服务器没有返回有效会话", "The server did not return a valid session"));
      }
      const { data: sessionData, error: sessionError } = await client.auth.refreshSession({
        refresh_token: refreshToken,
      });
      if (sessionError || !sessionData.session) {
        throw new Error(text(`管理员会话保存失败：${sessionError?.message ?? "未返回会话"}`, `Could not save administrator session: ${sessionError?.message ?? "no session returned"}`));
      }
      window.location.replace(`${window.location.pathname}#/new`);
    }).catch((cause) => {
      const message = cause instanceof Error ? cause.message : text("未知错误", "Unknown error");
      setError(text(`扫码登录失败：${message}`, `QR sign-in failed: ${message}`));
    });
  }, []);
  return <div className="grid min-h-screen place-items-center bg-canvas p-5"><div className="panel max-w-lg p-8 text-center"><p className="eyebrow">{text("管理员登录", "Administrator sign-in")}</p><h1 className="mt-3 text-2xl font-semibold text-content">{error ? text("无法登录", "Unable to sign in") : text("正在安全兑换管理员凭据…", "Securely exchanging administrator credentials…")}</h1><p className={`mt-4 text-sm ${error ? "text-danger" : "text-muted"}`}>{error || text("请勿关闭页面，完成后将自动进入新建分析界面。", "Keep this page open. You will enter New analysis automatically.")}</p></div></div>;
}

function SetupRequired() {
  const { text } = useLanguage();
  return <div className="grid min-h-screen place-items-center bg-canvas p-5"><div className="panel max-w-xl p-8"><p className="eyebrow">{text("需要配置", "Configuration required")}</p><h1 className="mt-3 text-3xl font-semibold text-content">{text("连接 Supabase 后启动网站", "Connect Supabase to start the site")}</h1><p className="mt-4 leading-7 text-muted">{text("复制 apps/web/.env.example 为本地环境文件，只填写 Supabase URL、anon key 和 Turnstile site key。秘密 provider key 不得出现在前端。", "Copy apps/web/.env.example to a local environment file. Only add the Supabase URL, anon key, and Turnstile site key. Provider secrets must never be exposed to the browser.")}</p></div></div>;
}

function PrivateApp({ session, isAdmin }: { session: Session; isAdmin: boolean }) {
  return <Layout email={session.user.email} isAdmin={isAdmin}><Suspense fallback={<Loading/>}><Routes><Route path="/" element={<DashboardPage/>}/><Route path="/new" element={<NewAnalysisPage/>}/><Route path="/jobs/:id" element={<JobPage/>}/><Route path="/reports/:id" element={<ReportPage/>}/><Route path="/experiments/:id" element={<ExperimentWorkspacePage/>}/>{isAdmin && <Route path="/admin" element={<AdminPage/>}/>} {isAdmin && <Route path="/admin/jobs/:id" element={<JobPage adminMode/>}/>} {isAdmin && <Route path="/admin/reports/:id" element={<ReportPage readOnly/>}/>} {isAdmin && <Route path="/admin/experiments/:id" element={<ExperimentWorkspacePage adminMode/>}/>}<Route path="*" element={<Navigate to={isAdmin && window.location.hash.startsWith("#/admin") ? "/admin" : "/"} replace/>}/></Routes></Suspense></Layout>;
}

export default function App() {
  const { text } = useLanguage();
  const [linkIssue] = useState(authLinkIssue);
  const [requestedAuthMode] = useState(() => new URLSearchParams(window.location.search).get("auth"));
  const [passwordRecovery, setPasswordRecovery] = useState(isPasswordRecoveryLink);
  const [authMessage, setAuthMessage] = useState("");
  const [session, setSession] = useState<Session | null | undefined>(undefined);
  const [isAdmin, setIsAdmin] = useState<boolean | undefined>(undefined);
  useEffect(() => {
    if (!supabase) { setSession(null); return; }
    void supabase.auth.getSession().then(({ data }) => setSession(data.session));
    const { data } = supabase.auth.onAuthStateChange((event, next) => {
      setSession(next);
      if (event === "PASSWORD_RECOVERY") setPasswordRecovery(true);
    });
    return () => data.subscription.unsubscribe();
  }, []);
  useEffect(() => {
    if (linkIssue) clearAuthLink();
    else if (session && !passwordRecovery && new URLSearchParams(window.location.search).get("auth") === "confirm") clearAuthLink("#/new");
  }, [linkIssue, passwordRecovery, session]);
  useEffect(() => {
    if (!session) { setIsAdmin(false); return; }
    setIsAdmin(undefined);
    void checkIsAdmin().then(setIsAdmin).catch(() => setIsAdmin(false));
  }, [session?.access_token]);
  useEffect(() => {
    if (session && isAdmin === true && new URLSearchParams(window.location.search).get("admin") === "1") {
      window.location.replace(`${window.location.pathname}#/new`);
    }
  }, [isAdmin, session]);
  if (!isConfigured) return <SetupRequired/>;
  if (window.location.hash.startsWith("#admin_ticket=")) return <AdminTicketLogin/>;
  if (location.hash.startsWith("#/share/")) return <Suspense fallback={<Loading/>}><Routes><Route path="/share/:token" element={<SharedReportPage/>}/></Routes></Suspense>;
  if (session === undefined) return <div className="grid min-h-screen place-items-center bg-canvas text-muted">{text("正在建立安全会话…", "Establishing a secure session…")}</div>;
  if (passwordRecovery && session) return <PasswordResetPanel onComplete={(message) => { setPasswordRecovery(false); setAuthMessage(message); setSession(null); }}/>;
  if (!session) {
    const issueMessage = linkIssue ? text(linkIssue === "expired" ? "邮件链接已失效，请重新申请。" : "邮件链接无效，请重新申请。", linkIssue === "expired" ? "This email link has expired. Request a new one." : "This email link is invalid. Request a new one.") : authMessage;
    return <main className="relative min-h-screen bg-canvas px-5"><AuthPanel initialMode={linkIssue ? requestedAuthMode === "recovery" ? "forgot" : "signup" : "signin"} initialMessage={issueMessage}/></main>;
  }
  if (isAdmin === undefined) return <div className="grid min-h-screen place-items-center bg-canvas text-muted">{text("正在验证访问权限…", "Checking access permissions…")}</div>;
  return <PrivateApp session={session} isAdmin={isAdmin}/>;
}
