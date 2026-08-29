import { AlertTriangle, ArrowRight, Clock3, Trash2, XCircle } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { StatusBadge } from "../components/StatusBadge";
import { cancelJob, deleteJob, getJob, getReportByJob } from "../lib/api";
import { requireSupabase } from "../lib/supabase";
import type { JobEvent, JobRecord, ReportRecord } from "../lib/types";

export function JobPage({ readOnly = false }: { readOnly?: boolean }) {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const [job, setJob] = useState<JobRecord | null>(null); const [events, setEvents] = useState<JobEvent[]>([]); const [report, setReport] = useState<ReportRecord | null>(null); const [error, setError] = useState("");
  useEffect(() => {
    const client = requireSupabase();
    async function load() { try { const next = await getJob(id); setJob(next); const { data } = await client.from("job_events").select("*").eq("job_id", id).order("created_at"); setEvents((data ?? []) as JobEvent[]); if (next.status === "completed") setReport(await getReportByJob(id)); } catch (cause) { setError(cause instanceof Error ? cause.message : "任务加载失败"); } }
    void load();
    const channel = client.channel(`job:${id}`).on("postgres_changes", { event: "UPDATE", schema: "public", table: "jobs", filter: `id=eq.${id}` }, (payload) => { const next = payload.new as JobRecord; setJob(next); if (next.status === "completed") void getReportByJob(id).then(setReport); }).on("postgres_changes", { event: "INSERT", schema: "public", table: "job_events", filter: `job_id=eq.${id}` }, (payload) => setEvents((current) => [...current, payload.new as JobEvent])).subscribe();
    return () => { void client.removeChannel(channel); };
  }, [id]);
  if (error) return <div className="panel p-6 text-danger">{error}</div>;
  if (!job) return <div className="panel animate-pulse p-12 text-center text-muted">加载任务…</div>;
  const active = !["completed", "cancelled", "failed", "budget_blocked"].includes(job.status);
  return (
    <div className="mx-auto max-w-4xl">
      <div className="flex flex-col justify-between gap-5 sm:flex-row sm:items-start"><div><p className="eyebrow">Analysis job · {job.id.slice(0, 8)}</p><div className="mt-3 flex flex-wrap items-center gap-3"><h1 className="text-3xl font-semibold tracking-tight text-content">论文调研任务</h1><StatusBadge status={job.status} /></div><p className="mt-3 text-sm text-muted">{job.mode === "single" ? "单论文" : "多论文联合"} · 最大 {job.max_rounds} 轮 · 当前第 {job.current_round} 轮</p>{readOnly && <p className="mt-2 text-xs font-medium text-warning">管理员只读视图</p>}</div>{!readOnly && (active ? <button className="button button-danger" onClick={() => cancelJob(id).catch((e) => setError(e.message))}><XCircle className="h-4 w-4" />取消任务</button> : <button className="button button-danger" onClick={() => deleteJob(id).then(() => navigate("/")).catch((e) => setError(e.message))}><Trash2 className="h-4 w-4" />删除任务</button>)}</div>
      <section className="panel mt-8 p-6"><div className="flex items-end justify-between gap-4"><div><span className="text-sm text-muted">当前阶段</span><div className="mt-1 text-xl font-semibold text-content">{job.stage}</div></div><div className="font-mono text-2xl font-medium text-accent-strong">{job.progress}%</div></div><div className="mt-5 h-2 overflow-hidden rounded-full bg-subtle"><div className="h-full rounded-full bg-gradient-to-r from-primary to-accent transition-all duration-700" style={{ width: `${job.progress}%` }} /></div>{active && <p className="mt-4 flex items-center gap-2 text-xs text-muted"><Clock3 className="h-4 w-4 text-accent-strong" />可以关闭页面；服务器会继续处理，重新打开后自动恢复进度。</p>}</section>
      {job.error && <div className="mt-5 flex gap-3 rounded-xl border border-danger/25 bg-danger/[.07] p-4 text-danger"><AlertTriangle className="mt-0.5 h-5 w-5 shrink-0" /><span>{job.error}</span></div>}
      {report && <Link className="panel group mt-6 flex items-center justify-between border-accent/30 p-6 transition hover:bg-accent/[.05]" to={readOnly ? `/admin/reports/${report.id}` : `/reports/${report.id}`}><div><p className="eyebrow">Report ready</p><h2 className="mt-2 text-xl font-semibold text-content">查看研究图谱与完整报告</h2></div><ArrowRight className="h-6 w-6 text-accent-strong transition group-hover:translate-x-1" /></Link>}
      <section className="panel mt-6 p-6"><h2 className="font-semibold text-content">运行日志</h2><div className="mt-5 space-y-0">{events.length === 0 && <p className="text-sm text-muted">等待 worker 领取任务…</p>}{events.map((event, index) => <div key={event.id} className="relative flex gap-4 pb-6 last:pb-0"><div className="relative z-10 mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full bg-accent ring-4 ring-accent/10" />{index < events.length - 1 && <div className="absolute left-[4px] top-4 h-full w-px bg-line" />}<div><p className="text-sm text-content">{event.message}</p><p className="mt-1 font-mono text-[10px] text-faint">{new Date(event.created_at).toLocaleString()} · {event.kind}</p></div></div>)}</div></section>
    </div>
  );
}
