import { ArrowRight, ChevronLeft, ChevronRight, FileStack, FileText, FlaskConical, MoreHorizontal, Plus, RefreshCw, Star, Trash2, X, XCircle } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { WorkflowStatusBadge } from "../components/WorkflowStatusBadge";
import { cancelJob, deleteJob, listJobs, setJobFavorite } from "../lib/api";
import { deriveWorkflowState } from "../lib/job-workflow";
import { useLanguage } from "../lib/language";
import type { JobRecord } from "../lib/types";

const terminalStatuses = new Set(["completed", "cancelled", "needs_input"]);

function jobTitle(job: JobRecord, fallback: string) {
  return job.file_names?.[0] || fallback;
}

export function DashboardPage() {
  const { text, formatDate, formatNumber } = useLanguage();
  const [jobs, setJobs] = useState<JobRecord[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [menuJobId, setMenuJobId] = useState("");
  const [pending, setPending] = useState<{ job: JobRecord; action: "cancel" | "delete" } | null>(null);
  const [acting, setActing] = useState(false);
  const [favoritesOnly, setFavoritesOnly] = useState(false);
  const [page, setPage] = useState(0);
  const pageSize = 20;
  const total = jobs[0]?.total_count ?? jobs.length;

  async function load() {
    setLoading(true);
    setError("");
    try { setJobs(await listJobs(pageSize, page * pageSize, favoritesOnly)); }
    catch (cause) { setError(cause instanceof Error ? cause.message : text("加载失败", "Could not load jobs")); }
    finally { setLoading(false); }
  }

  useEffect(() => { void load(); }, [favoritesOnly, page]);

  async function favorite(job: JobRecord) {
    const next = !job.is_favorite;
    setJobs((items) => items.map((item) => item.id === job.id ? { ...item, is_favorite: next } : item));
    setMenuJobId("");
    try {
      await setJobFavorite(job.id, next);
      if (favoritesOnly && !next) setJobs((items) => items.filter((item) => item.id !== job.id));
    } catch (cause) {
      setJobs((items) => items.map((item) => item.id === job.id ? { ...item, is_favorite: !next } : item));
      setError(cause instanceof Error ? cause.message : text("收藏操作失败", "Could not update favorite"));
    }
  }

  async function confirmAction() {
    if (!pending) return;
    setActing(true);
    setError("");
    try {
      if (pending.action === "cancel") await cancelJob(pending.job.id);
      else await deleteJob(pending.job.id);
      setPending(null);
      setMenuJobId("");
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : text("操作失败", "Action failed"));
    } finally { setActing(false); }
  }

  return <>
    <div className="flex flex-col justify-between gap-6 md:flex-row md:items-end">
      <div><p className="eyebrow">{text("研究工作台", "Research workspace")}</p><h1 className="mt-3 text-4xl font-semibold tracking-tight text-content">{text("你的分析任务", "Your analysis jobs")}</h1><p className="mt-3 text-muted">{text("按论文查找任务，并回到每一条可追溯的研究结论。", "Find jobs by paper and return to every traceable research conclusion.")}</p></div>
      <div className="flex gap-3"><button className="button button-secondary" onClick={() => void load()}><RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />{text("刷新", "Refresh")}</button><Link className="button button-primary" to="/new"><Plus className="h-4 w-4" />{text("新建分析", "New analysis")}</Link></div>
    </div>
    <div className="mt-8 grid gap-4 sm:grid-cols-2">
      <div className="panel flex items-center gap-4 p-5"><span className="grid h-11 w-11 place-items-center rounded-xl bg-warning/10"><FileStack className="h-5 w-5 text-warning" /></span><div><div className="text-3xl font-semibold tracking-tight text-content">{formatNumber(total)}</div><div className="mt-1 text-sm text-muted">{text("历史任务", "Analysis history")}</div></div></div>
      <div className="panel flex items-center gap-4 p-5"><span className="grid h-11 w-11 place-items-center rounded-xl bg-accent/10"><FlaskConical className="h-5 w-5 text-accent-strong" /></span><div><div className="text-sm font-semibold tracking-wide text-content">{text("内测访问", "Beta access")}</div><div className="mt-1 text-sm text-muted">{text("内测期间不限任务配额", "No job quota during beta")}</div></div></div>
    </div>
    {error && <div className="mt-6 rounded-xl border border-danger/25 bg-danger/[.07] p-4 text-danger">{error}</div>}
    <section className="panel mt-8 overflow-visible">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-5 py-5 sm:px-6"><div className="flex items-center gap-4"><h2 className="font-semibold text-content">{text("最近分析", "Recent analyses")}</h2><div className="flex rounded-lg bg-subtle p-1"><button className={`job-filter ${!favoritesOnly ? "active" : ""}`} onClick={() => { setPage(0); setFavoritesOnly(false); }}>{text("全部", "All")}</button><button className={`job-filter ${favoritesOnly ? "active" : ""}`} onClick={() => { setPage(0); setFavoritesOnly(true); }}><Star className="h-3.5 w-3.5"/>{text("已收藏", "Favorites")}</button></div></div><span className="text-xs text-faint">{formatNumber(total)} {text("个任务", "jobs")}</span></div>
      {loading && jobs.length === 0 && <div className="space-y-0 divide-y divide-line">{[0, 1].map((item) => <div className="px-5 py-6 sm:px-6" key={item}><div className="h-5 w-2/3 animate-pulse rounded bg-subtle"/><div className="mt-4 h-2 animate-pulse rounded bg-subtle"/><div className="mt-3 h-3 w-36 animate-pulse rounded bg-subtle"/></div>)}</div>}
      {!loading && jobs.length === 0 ? <div className="grid place-items-center px-6 py-20 text-center"><span className="grid h-14 w-14 place-items-center rounded-2xl bg-subtle"><FileStack className="h-7 w-7 text-faint" /></span><h3 className="mt-5 text-lg font-semibold text-content">{text("还没有分析任务", "No analyses yet")}</h3><p className="mt-2 text-sm text-muted">{text("上传第一篇论文，建立你的研究图谱。", "Upload your first paper to build a research map.")}</p><Link className="button button-primary mt-6" to="/new">{text("开始分析", "Start analysis")}</Link></div> :
        <div className="divide-y divide-line">{jobs.map((job) => {
          const active = !terminalStatuses.has(job.status);
          const workflow = deriveWorkflowState(job);
          const extraFiles = Math.max(0, (job.file_names?.length ?? 0) - 1);
          const title = jobTitle(job, text("论文调研任务", "Literature research job"));
          return <article className="job-list-row group relative" key={job.id}>
            <Link to={`/jobs/${job.id}`} className="flex min-w-0 flex-1 items-center gap-4 px-5 py-5 sm:px-6"><span className="grid h-11 w-11 shrink-0 place-items-center rounded-xl bg-subtle"><FileText className="h-5 w-5 text-muted"/></span><div className="min-w-0 flex-1"><div className="flex flex-wrap items-center gap-2"><h3 className="max-w-full truncate text-sm font-semibold text-content" title={title}>{title}</h3>{job.is_favorite && <Star className="h-4 w-4 fill-warning text-warning" aria-label={text("已收藏", "Favorite")}/>} {extraFiles > 0 && <span className="rounded-full bg-subtle px-2 py-1 text-[10px] font-semibold text-muted">+{extraFiles}</span>}<WorkflowStatusBadge status={job.status} step={workflow.step}/></div><div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted"><span>{job.mode === "single" ? text("单论文", "Single paper") : text("多论文", "Multi-paper")} · {text(`${job.max_rounds} 轮`, `${job.max_rounds} round(s)`)}</span><span>{formatDate(job.created_at)}</span><span className="font-mono text-[10px] text-faint">{job.id.slice(0, 8)}</span></div><div className="mt-3 h-1.5 overflow-hidden rounded-full bg-subtle"><div className="h-full rounded-full bg-accent transition-all" style={{ width: `${workflow.progress}%` }} /></div></div><ArrowRight className="h-5 w-5 shrink-0 text-faint transition group-hover:translate-x-0.5 group-hover:text-accent-strong" /></Link>
            <div className="relative mr-3 shrink-0"><button type="button" className="job-action-button" aria-label={text(`管理 ${title}`, `Manage ${title}`)} aria-expanded={menuJobId === job.id} onClick={() => setMenuJobId((value) => value === job.id ? "" : job.id)}><MoreHorizontal className="h-5 w-5"/></button>{menuJobId === job.id && <div className="job-action-menu"><button type="button" onClick={() => void favorite(job)}><Star className={`h-4 w-4 ${job.is_favorite ? "fill-warning text-warning" : ""}`}/>{job.is_favorite ? text("取消收藏", "Unfavorite") : text("收藏任务", "Favorite")}</button>{active ? <button type="button" onClick={() => setPending({ job, action: "cancel" })}><XCircle className="h-4 w-4"/>{text("取消任务", "Cancel job")}</button> : <button type="button" className="text-danger" onClick={() => setPending({ job, action: "delete" })}><Trash2 className="h-4 w-4"/>{text("永久删除", "Delete permanently")}</button>}</div>}</div>
          </article>;
        })}</div>}
      {total > pageSize && <div className="flex items-center justify-between border-t border-line px-5 py-4 text-sm text-muted sm:px-6"><span>{text(`第 ${page + 1} 页`, `Page ${page + 1}`)}</span><div className="flex gap-2"><button className="button button-secondary !min-h-9 !px-3 !py-1" disabled={page === 0 || loading} onClick={() => setPage((value) => Math.max(0, value - 1))}><ChevronLeft className="h-4 w-4"/>{text("上一页", "Previous")}</button><button className="button button-secondary !min-h-9 !px-3 !py-1" disabled={(page + 1) * pageSize >= total || loading} onClick={() => setPage((value) => value + 1)}>{text("下一页", "Next")}<ChevronRight className="h-4 w-4"/></button></div></div>}
    </section>
    {pending && <div className="confirm-layer" role="presentation"><button type="button" className="confirm-backdrop" aria-label={text("关闭确认框", "Close confirmation")} onClick={() => !acting && setPending(null)}/><section className="confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="job-confirm-title"><div className="flex items-start justify-between gap-4"><div><span className={`grid h-10 w-10 place-items-center rounded-xl ${pending.action === "delete" ? "bg-danger/10 text-danger" : "bg-warning/10 text-warning"}`}>{pending.action === "delete" ? <Trash2 className="h-5 w-5"/> : <XCircle className="h-5 w-5"/>}</span><h2 id="job-confirm-title" className="!mt-4 !text-xl !text-content">{pending.action === "delete" ? text("永久删除这个任务？", "Delete this job permanently?") : text("取消正在运行的任务？", "Cancel the running job?")}</h2></div><button className="citation-close !static" disabled={acting} onClick={() => setPending(null)} aria-label={text("关闭", "Close")}><X className="h-4 w-4"/></button></div><p className="mt-3 break-all text-sm font-medium text-content">{jobTitle(pending.job, pending.job.id.slice(0, 8))}</p><p className="mt-2 text-sm leading-6 text-muted">{pending.action === "delete" ? text("报告和任务记录将永久删除，无法恢复。", "The report and job record will be permanently deleted and cannot be recovered.") : text("服务器会在安全检查点停止处理。取消后可再次从任务菜单永久删除。", "The worker will stop at a safe checkpoint. After cancellation, the job can be permanently deleted from its menu.")}</p><div className="mt-6 flex justify-end gap-3"><button className="button button-secondary" disabled={acting} onClick={() => setPending(null)}>{text("返回", "Back")}</button><button className={pending.action === "delete" ? "button button-danger" : "button button-primary"} disabled={acting} onClick={() => void confirmAction()}>{acting ? text("处理中…", "Working…") : pending.action === "delete" ? text("确认删除", "Delete") : text("确认取消", "Cancel job")}</button></div></section></div>}
  </>;
}
