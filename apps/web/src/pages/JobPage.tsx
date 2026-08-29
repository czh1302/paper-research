import { AlertTriangle, ArrowRight, Clock3, Trash2, XCircle } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { StatusBadge } from "../components/StatusBadge";
import { cancelJob, deleteJob, getJob, getReportByJob } from "../lib/api";
import { type Language, useLanguage } from "../lib/language";
import { requireSupabase } from "../lib/supabase";
import type { JobEvent, JobRecord, ReportRecord } from "../lib/types";

export function JobPage({ readOnly = false }: { readOnly?: boolean }) {
  const { language, text, formatDate } = useLanguage();
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const [job, setJob] = useState<JobRecord | null>(null); const [events, setEvents] = useState<JobEvent[]>([]); const [report, setReport] = useState<ReportRecord | null>(null); const [error, setError] = useState("");
  useEffect(() => {
    const client = requireSupabase();
    async function load() { try { const next = await getJob(id); setJob(next); const { data } = await client.from("job_events").select("*").eq("job_id", id).order("created_at"); setEvents((data ?? []) as JobEvent[]); if (next.status === "completed") setReport(await getReportByJob(id)); } catch (cause) { setError(cause instanceof Error ? cause.message : text("任务加载失败", "Could not load the job")); } }
    void load();
    const channel = client.channel(`job:${id}`).on("postgres_changes", { event: "UPDATE", schema: "public", table: "jobs", filter: `id=eq.${id}` }, (payload) => { const next = payload.new as JobRecord; setJob(next); if (next.status === "completed") void getReportByJob(id).then(setReport); }).on("postgres_changes", { event: "INSERT", schema: "public", table: "job_events", filter: `job_id=eq.${id}` }, (payload) => setEvents((current) => [...current, payload.new as JobEvent])).subscribe();
    return () => { void client.removeChannel(channel); };
  }, [id]);
  if (error) return <div className="panel p-6 text-danger">{error}</div>;
  if (!job) return <div className="panel animate-pulse p-12 text-center text-muted">{text("加载任务…", "Loading job…")}</div>;
  const active = !["completed", "cancelled", "failed", "budget_blocked"].includes(job.status);
  return (
    <div className="mx-auto max-w-4xl">
      <div className="flex flex-col justify-between gap-5 sm:flex-row sm:items-start"><div><p className="eyebrow">{text("分析任务", "Analysis job")} · {job.id.slice(0, 8)}</p><div className="mt-3 flex flex-wrap items-center gap-3"><h1 className="text-3xl font-semibold tracking-tight text-content">{text("论文调研任务", "Literature research job")}</h1><StatusBadge status={job.status} /></div><p className="mt-3 text-sm text-muted">{job.mode === "single" ? text("单论文", "Single paper") : text("多论文联合", "Multi-paper analysis")} · {text(`最多 ${job.max_rounds} 轮 · 当前第 ${job.current_round} 轮`, `Up to ${job.max_rounds} round(s) · Currently round ${job.current_round}`)}</p>{readOnly && <p className="mt-2 text-xs font-medium text-warning">{text("管理员只读视图", "Administrator read-only view")}</p>}</div>{!readOnly && (active ? <button className="button button-danger" onClick={() => cancelJob(id).catch((e) => setError(e.message))}><XCircle className="h-4 w-4" />{text("取消任务", "Cancel job")}</button> : <button className="button button-danger" onClick={() => deleteJob(id).then(() => navigate("/")).catch((e) => setError(e.message))}><Trash2 className="h-4 w-4" />{text("删除任务", "Delete job")}</button>)}</div>
      <section className="panel mt-8 p-6"><div className="flex items-end justify-between gap-4"><div><span className="text-sm text-muted">{text("当前阶段", "Current stage")}</span><div className="mt-1 text-xl font-semibold text-content">{stageLabel(job.stage, language)}</div></div><div className="font-mono text-2xl font-medium text-accent-strong">{job.progress}%</div></div><div className="mt-5 h-2 overflow-hidden rounded-full bg-subtle"><div className="h-full rounded-full bg-gradient-to-r from-primary to-accent transition-all duration-700" style={{ width: `${job.progress}%` }} /></div>{active && <p className="mt-4 flex items-center gap-2 text-xs text-muted"><Clock3 className="h-4 w-4 text-accent-strong" />{text("可以关闭页面；服务器会继续处理，重新打开后自动恢复进度。", "You may close this page. The server will continue and restore progress when you return.")}</p>}</section>
      {job.error && <div className="mt-5 flex gap-3 rounded-xl border border-danger/25 bg-danger/[.07] p-4 text-danger"><AlertTriangle className="mt-0.5 h-5 w-5 shrink-0" /><span>{job.error}</span></div>}
      {report && <Link className="panel group mt-6 flex items-center justify-between border-accent/30 p-6 transition hover:bg-accent/[.05]" to={readOnly ? `/admin/reports/${report.id}` : `/reports/${report.id}`}><div><p className="eyebrow">{text("报告已生成", "Report ready")}</p><h2 className="mt-2 text-xl font-semibold text-content">{text("查看研究图谱与可读报告", "View the research map and readable report")}</h2></div><ArrowRight className="h-6 w-6 text-accent-strong transition group-hover:translate-x-1" /></Link>}
      <section className="panel mt-6 p-6"><h2 className="font-semibold text-content">{text("运行日志", "Run log")}</h2><div className="mt-5 space-y-0">{events.length === 0 && <p className="text-sm text-muted">{text("等待服务器领取任务…", "Waiting for the worker to claim the job…")}</p>}{events.map((event, index) => <div key={event.id} className="relative flex gap-4 pb-6 last:pb-0"><div className="relative z-10 mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full bg-accent ring-4 ring-accent/10" />{index < events.length - 1 && <div className="absolute left-[4px] top-4 h-full w-px bg-line" />}<div><p className="text-sm text-content">{eventLabel(event, language)}</p><p className="mt-1 font-mono text-[10px] text-faint">{formatDate(event.created_at)} · {event.kind}</p></div></div>)}</div></section>
    </div>
  );
}

function stageLabel(stage: string, language: Language) {
  const labels: Record<string, [string, string]> = {
    queued: ["排队等待", "Queued"], parsing: ["解析 PDF", "Parsing PDFs"], problem_ready: ["问题定义已生成", "Problem definition ready"],
    searching: ["检索相关工作", "Retrieving related work"], analyzing: ["分析差异与机会", "Analyzing differences and ideas"], rendering: ["生成可读报告", "Rendering the readable report"], completed: ["已完成", "Completed"],
  };
  return labels[stage]?.[language === "zh" ? 0 : 1] ?? stage;
}

function eventLabel(event: JobEvent, language: Language) {
  if (language === "en") return event.message;
  const labels: Record<string, string> = {
    resumed: "已从检查点恢复任务", stage: "处理阶段已更新", paper_parsed: "论文解析完成",
    round_complete: "一轮检索与分析已完成", early_stop: "检索已收敛，提前结束循环",
    presentation_fallback: "可读摘要生成失败，已使用兼容视图", completed: "报告已生成",
  };
  return labels[event.kind] ?? event.message;
}
