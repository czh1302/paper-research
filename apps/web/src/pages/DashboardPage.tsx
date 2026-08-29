import { ArrowRight, FileStack, FlaskConical, Plus, RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { listJobs } from "../lib/api";
import { useLanguage } from "../lib/language";
import type { JobRecord } from "../lib/types";
import { StatusBadge } from "../components/StatusBadge";

export function DashboardPage() {
  const { text, formatDate, formatNumber } = useLanguage();
  const [jobs, setJobs] = useState<JobRecord[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  async function load() {
    setLoading(true); setError("");
    try { setJobs(await listJobs()); }
    catch (cause) { setError(cause instanceof Error ? cause.message : text("加载失败", "Could not load jobs")); }
    finally { setLoading(false); }
  }
  useEffect(() => { void load(); }, []);
  return (
    <>
      <div className="flex flex-col justify-between gap-6 md:flex-row md:items-end">
        <div><p className="eyebrow">{text("研究工作台", "Research workspace")}</p><h1 className="mt-3 text-4xl font-semibold tracking-tight text-content">{text("你的分析任务", "Your analysis jobs")}</h1><p className="mt-3 text-muted">{text("每个结论都可回溯到 PDF 页码或检索来源。", "Every conclusion traces back to a PDF page or retrieval source.")}</p></div>
        <div className="flex gap-3"><button className="button button-secondary" onClick={load}><RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />{text("刷新", "Refresh")}</button><Link className="button button-primary" to="/new"><Plus className="h-4 w-4" />{text("新建分析", "New analysis")}</Link></div>
      </div>
      <div className="mt-8 grid gap-4 sm:grid-cols-2">
        <div className="panel flex items-center gap-4 p-5"><span className="grid h-11 w-11 place-items-center rounded-xl bg-warning/10"><FileStack className="h-5 w-5 text-warning" /></span><div><div className="text-3xl font-semibold tracking-tight text-content">{formatNumber(jobs.length)}</div><div className="mt-1 text-sm text-muted">{text("历史任务", "Analysis history")}</div></div></div>
        <div className="panel flex items-center gap-4 p-5"><span className="grid h-11 w-11 place-items-center rounded-xl bg-accent/10"><FlaskConical className="h-5 w-5 text-accent-strong" /></span><div><div className="text-sm font-semibold tracking-wide text-content">{text("内测访问", "BETA ACCESS")}</div><div className="mt-1 text-sm text-muted">{text("内测期间不限任务配额", "No job quota during beta")}</div></div></div>
      </div>
      {error && <div className="mt-6 rounded-xl border border-danger/25 bg-danger/[.07] p-4 text-danger">{error}</div>}
      <section className="panel mt-8 overflow-hidden">
        <div className="flex items-center justify-between border-b border-line px-6 py-5"><h2 className="font-semibold text-content">{text("最近分析", "Recent analyses")}</h2><span className="text-xs text-faint">{formatNumber(jobs.length)} {text("个任务", "jobs")}</span></div>
        {!loading && jobs.length === 0 ? <div className="grid place-items-center px-6 py-20 text-center"><span className="grid h-14 w-14 place-items-center rounded-2xl bg-subtle"><FileStack className="h-7 w-7 text-faint" /></span><h3 className="mt-5 text-lg font-semibold text-content">{text("还没有分析任务", "No analyses yet")}</h3><p className="mt-2 text-sm text-muted">{text("上传第一篇论文，建立你的研究图谱。", "Upload your first paper to build a research map.")}</p><Link className="button button-primary mt-6" to="/new">{text("开始分析", "Start analysis")}</Link></div> :
          <div className="divide-y divide-line">{jobs.map((job) => <Link key={job.id} to={`/jobs/${job.id}`} className="group flex items-center gap-4 px-6 py-5 transition hover:bg-subtle/60"><div className="min-w-0 flex-1"><div className="flex flex-wrap items-center gap-3"><span className="font-mono text-xs text-faint">{job.id.slice(0, 8)}</span><StatusBadge status={job.status} /><span className="text-xs text-muted">{job.mode === "single" ? text("单论文", "Single paper") : text("多论文", "Multi-paper")} · {text(`${job.max_rounds}轮`, `${job.max_rounds} round(s)`)}</span></div><div className="mt-3 h-1.5 overflow-hidden rounded-full bg-subtle"><div className="h-full rounded-full bg-accent transition-all" style={{ width: `${job.progress}%` }} /></div><p className="mt-2 text-xs text-faint">{formatDate(job.created_at)}</p></div><ArrowRight className="h-5 w-5 text-faint transition group-hover:translate-x-0.5 group-hover:text-accent-strong" /></Link>)}</div>}
      </section>
    </>
  );
}
