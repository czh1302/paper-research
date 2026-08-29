import type { Session } from "@supabase/supabase-js";
import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { AuthPanel } from "./components/AuthPanel";
import { Layout } from "./components/Layout";
import { checkIsAdmin } from "./lib/api";
import { isConfigured, supabase, supabaseUrl } from "./lib/supabase";

const AdminPage = lazy(() => import("./pages/AdminPage").then((module) => ({ default: module.AdminPage })));
const DashboardPage = lazy(() => import("./pages/DashboardPage").then((module) => ({ default: module.DashboardPage })));
const JobPage = lazy(() => import("./pages/JobPage").then((module) => ({ default: module.JobPage })));
const NewAnalysisPage = lazy(() => import("./pages/NewAnalysisPage").then((module) => ({ default: module.NewAnalysisPage })));
const ReportPage = lazy(() => import("./pages/ReportPage").then((module) => ({ default: module.ReportPage })));
const SharedReportPage = lazy(() => import("./pages/SharedReportPage").then((module) => ({ default: module.SharedReportPage })));

function Loading() {
  return <div className="panel p-12 text-center text-slate-400">加载页面…</div>;
}

function AdminTicketLogin() {
  const started = useRef(false);
  const [error, setError] = useState("");
  useEffect(() => {
    if (started.current || !supabase || !supabaseUrl) return;
    started.current = true;
    const token = new URLSearchParams(window.location.hash.slice(1)).get("admin_ticket");
    if (!token) { setError("管理员二维码无效"); return; }
    void supabase.functions.invoke("admin-qr-login", { body: { token } }).then(({ data, error: exchangeError }) => {
      if (exchangeError) throw exchangeError;
      const actionLink = data?.actionLink;
      if (typeof actionLink !== "string" || !actionLink.startsWith(`${supabaseUrl}/auth/v1/verify`)) {
        throw new Error("服务器返回了无效登录地址");
      }
      window.location.replace(actionLink);
    }).catch(() => setError("管理员二维码无效、已使用或已过期，请重新生成。"));
  }, []);
  return <div className="grid min-h-screen place-items-center p-5"><div className="panel max-w-lg p-8 text-center"><p className="eyebrow">Administrator sign-in</p><h1 className="mt-3 text-2xl font-semibold text-paper">{error ? "无法登录" : "正在安全兑换管理员凭据…"}</h1><p className={`mt-4 text-sm ${error ? "text-red-200" : "text-slate-400"}`}>{error || "请勿关闭页面，完成后将自动进入管理界面。"}</p></div></div>;
}

function SetupRequired() {
  return <div className="grid min-h-screen place-items-center p-5"><div className="panel max-w-xl p-8"><p className="eyebrow">Configuration required</p><h1 className="mt-3 text-3xl font-semibold text-paper">连接 Supabase 后启动网站</h1><p className="mt-4 leading-7 text-slate-400">复制 <code className="text-amber">apps/web/.env.example</code> 为本地环境文件，只填写 Supabase URL、anon key 和 Turnstile site key。秘密 provider key 不得出现在前端。</p></div></div>;
}

function PrivateApp({ session, isAdmin }: { session: Session; isAdmin: boolean }) {
  return <Layout email={session.user.email} isAdmin={isAdmin}><Suspense fallback={<Loading/>}><Routes><Route path="/" element={<DashboardPage/>}/><Route path="/new" element={<NewAnalysisPage/>}/><Route path="/jobs/:id" element={<JobPage/>}/><Route path="/reports/:id" element={<ReportPage/>}/>{isAdmin && <Route path="/admin" element={<AdminPage/>}/>} {isAdmin && <Route path="/admin/jobs/:id" element={<JobPage readOnly/>}/>} {isAdmin && <Route path="/admin/reports/:id" element={<ReportPage readOnly/>}/>}<Route path="*" element={<Navigate to={isAdmin && window.location.hash.startsWith("#/admin") ? "/admin" : "/"} replace/>}/></Routes></Suspense></Layout>;
}

export default function App() {
  const [session, setSession] = useState<Session | null | undefined>(undefined);
  const [isAdmin, setIsAdmin] = useState<boolean | undefined>(undefined);
  useEffect(() => {
    if (!supabase) { setSession(null); return; }
    void supabase.auth.getSession().then(({ data }) => setSession(data.session));
    const { data } = supabase.auth.onAuthStateChange((_event, next) => setSession(next));
    return () => data.subscription.unsubscribe();
  }, []);
  useEffect(() => {
    if (!session) { setIsAdmin(false); return; }
    setIsAdmin(undefined);
    void checkIsAdmin().then(setIsAdmin).catch(() => setIsAdmin(false));
  }, [session?.access_token]);
  useEffect(() => {
    if (session && isAdmin === true && new URLSearchParams(window.location.search).get("admin") === "1") {
      window.location.replace(`${window.location.pathname}#/admin`);
    }
  }, [isAdmin, session]);
  if (!isConfigured) return <SetupRequired/>;
  if (window.location.hash.startsWith("#admin_ticket=")) return <AdminTicketLogin/>;
  if (location.hash.startsWith("#/share/")) return <Suspense fallback={<Loading/>}><Routes><Route path="/share/:token" element={<SharedReportPage/>}/></Routes></Suspense>;
  if (session === undefined) return <div className="grid min-h-screen place-items-center text-slate-400">正在建立安全会话…</div>;
  if (!session) return <main className="relative mx-auto max-w-7xl px-5"><AuthPanel/></main>;
  if (isAdmin === undefined) return <div className="grid min-h-screen place-items-center text-slate-400">正在验证访问权限…</div>;
  return <PrivateApp session={session} isAdmin={isAdmin}/>;
}
