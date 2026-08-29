import { ArrowRight, FileStack, Gauge, Plus, RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { getQuota, listJobs } from "../lib/api";
import type { JobRecord, Quota } from "../lib/types";
import { StatusBadge } from "../components/StatusBadge";

export function DashboardPage() {
  const [jobs, setJobs] = useState<JobRecord[]>([]);
  const [quota, setQuota] = useState<Quota | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  async function load() {
    setLoading(true); setError("");
    try { const [jobRows, quotaRow] = await Promise.all([listJobs(), getQuota()]); setJobs(jobRows); setQuota(quotaRow); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "加载失败"); }
    finally { setLoading(false); }
  }
  useEffect(() => { void load(); }, []);
  const remaining = quota ? quota.allocation - quota.used - quota.reserved : 0;
  return (
    <>
      <div className="flex flex-col justify-between gap-6 md:flex-row md:items-end">
        <div><p className="eyebrow">Research workspace</p><h1 className="mt-3 text-4xl font-semibold text-paper">你的分析任务</h1><p className="mt-3 text-slate-400">每个结论都可回溯到 PDF 页码或检索来源。</p></div>
        <div className="flex gap-3"><button className="button button-secondary" onClick={load}><RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />刷新</button><Link className="button button-primary" to="/new"><Plus className="h-4 w-4" />新建分析</Link></div>
      </div>
      <div className="mt-8 grid gap-4 sm:grid-cols-3">
        <div className="panel p-5"><Gauge className="h-5 w-5 text-cyan" /><div className="mt-4 text-3xl font-semibold text-paper">{remaining}</div><div className="mt-1 text-sm text-slate-400">本月剩余分析单元</div></div>
        <div className="panel p-5"><FileStack className="h-5 w-5 text-amber" /><div className="mt-4 text-3xl font-semibold text-paper">{jobs.length}</div><div className="mt-1 text-sm text-slate-400">历史任务</div></div>
        <div className="panel p-5"><div className="font-mono text-xs text-cyan">1 UNIT</div><div className="mt-4 text-lg font-semibold text-paper">1 PDF × 1 ROUND</div><div className="mt-1 text-sm text-slate-400">提前停止会退还未运行轮次</div></div>
      </div>
      {error && <div className="mt-6 rounded-xl border border-red-400/30 bg-red-400/10 p-4 text-red-200">{error}</div>}
      <section className="panel mt-8 overflow-hidden">
        <div className="flex items-center justify-between border-b border-white/10 px-6 py-5"><h2 className="font-semibold text-paper">Recent analyses</h2><span className="font-mono text-xs text-slate-500">{jobs.length} JOBS</span></div>
        {!loading && jobs.length === 0 ? <div className="grid place-items-center px-6 py-20 text-center"><FileStack className="h-10 w-10 text-slate-600" /><h3 className="mt-5 text-lg font-semibold text-paper">还没有分析任务</h3><p className="mt-2 text-sm text-slate-400">上传第一篇论文，建立你的研究图谱。</p><Link className="button button-primary mt-6" to="/new">开始分析</Link></div> :
          <div className="divide-y divide-white/10">{jobs.map((job) => <Link key={job.id} to={`/jobs/${job.id}`} className="flex items-center gap-4 px-6 py-5 transition hover:bg-white/[.025]"><div className="min-w-0 flex-1"><div className="flex flex-wrap items-center gap-3"><span className="font-mono text-xs text-slate-500">{job.id.slice(0, 8)}</span><StatusBadge status={job.status} /><span className="text-xs text-slate-500">{job.mode === "single" ? "单论文" : "多论文"} · {job.max_rounds}轮</span></div><div className="mt-3 h-1.5 overflow-hidden rounded-full bg-white/5"><div className="h-full rounded-full bg-cyan transition-all" style={{ width: `${job.progress}%` }} /></div><p className="mt-2 text-xs text-slate-500">{new Date(job.created_at).toLocaleString()}</p></div><ArrowRight className="h-5 w-5 text-slate-500" /></Link>)}</div>}
      </section>
    </>
  );
}

