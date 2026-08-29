import type { Session } from "@supabase/supabase-js";
import { lazy, Suspense, useEffect, useState } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { AuthPanel } from "./components/AuthPanel";
import { Layout } from "./components/Layout";
import { checkIsAdmin } from "./lib/api";
import { isConfigured, supabase } from "./lib/supabase";

const AdminPage = lazy(() => import("./pages/AdminPage").then((module) => ({ default: module.AdminPage })));
const DashboardPage = lazy(() => import("./pages/DashboardPage").then((module) => ({ default: module.DashboardPage })));
const JobPage = lazy(() => import("./pages/JobPage").then((module) => ({ default: module.JobPage })));
const NewAnalysisPage = lazy(() => import("./pages/NewAnalysisPage").then((module) => ({ default: module.NewAnalysisPage })));
const ReportPage = lazy(() => import("./pages/ReportPage").then((module) => ({ default: module.ReportPage })));
const SharedReportPage = lazy(() => import("./pages/SharedReportPage").then((module) => ({ default: module.SharedReportPage })));

function Loading() {
  return <div className="panel p-12 text-center text-slate-400">加载页面…</div>;
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
  if (location.hash.startsWith("#/share/")) return <Suspense fallback={<Loading/>}><Routes><Route path="/share/:token" element={<SharedReportPage/>}/></Routes></Suspense>;
  if (session === undefined) return <div className="grid min-h-screen place-items-center text-slate-400">正在建立安全会话…</div>;
  if (!session) return <main className="relative mx-auto max-w-7xl px-5"><AuthPanel/></main>;
  if (isAdmin === undefined) return <div className="grid min-h-screen place-items-center text-slate-400">正在验证访问权限…</div>;
  return <PrivateApp session={session} isAdmin={isAdmin}/>;
}
