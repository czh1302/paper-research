import { AlertTriangle, ArrowLeft, ArrowRight, Check, ChevronRight, Circle, Clock3, LoaderCircle, Trash2, XCircle } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { StatusBadge } from "../components/StatusBadge";
import { adminDeleteJob, cancelJob, deleteJob, getJob, getReportByJob } from "../lib/api";
import { type Language, useLanguage } from "../lib/language";
import { requireSupabase } from "../lib/supabase";
import type { JobAttempt, JobEvent, JobRecord, ReportRecord } from "../lib/types";

const publicEventKinds = new Set([
  "queued", "resumed", "stage", "paper_parsed", "retrieval_batch",
  "retrieval_converged", "external_profile", "idea_attempt", "round_complete",
  "idea_generation_part", "evidence_previews", "completed", "auto_recovery", "waiting_resources",
]);

const stages = [
  ["等待处理", "Waiting"], ["解析 PDF", "Parsing PDF"], ["提取并核验问题定义", "Grounding the problem"],
  ["多平台检索相关论文", "Multi-source retrieval"], ["筛选并深读论文全文", "Screening full texts"],
  ["生成和审查论文级 Idea", "Generating and reviewing Ideas"], ["生成报告和导出文件", "Rendering report"],
] as const;

export function JobPage({ adminMode = false }: { adminMode?: boolean }) {
  const { language, text, formatDate } = useLanguage();
  const { id = "" } = useParams(); const navigate = useNavigate();
  const [job, setJob] = useState<JobRecord | null>(null); const [events, setEvents] = useState<JobEvent[]>([]); const [attempts, setAttempts] = useState<JobAttempt[]>([]); const [report, setReport] = useState<ReportRecord | null>(null); const [error, setError] = useState("");
  useEffect(() => {
    const client = requireSupabase();
    async function load() { try { const next = await getJob(id, adminMode); setJob(next); const result = adminMode ? await client.from("job_events").select("*").eq("job_id", id).order("created_at") : await client.from("job_events").select("id,kind,data,created_at").eq("job_id", id).in("kind", [...publicEventKinds]).order("created_at"); setEvents(((result.data ?? []) as unknown as Partial<JobEvent>[]).map(safeEvent)); if (adminMode) { const attemptResult = await client.from("job_attempts").select("id,attempt_number,failure_category,checkpoint_stage,safe_error,created_at").eq("job_id", id).order("created_at", { ascending: false }); setAttempts((attemptResult.data ?? []) as unknown as JobAttempt[]); } if (next.status === "completed") setReport(await getReportByJob(id)); } catch (cause) { setError(cause instanceof Error ? cause.message : text("任务加载失败", "Could not load the job")); } }
    void load();
    const channel = client.channel(`job:${id}`).on("postgres_changes", { event: "UPDATE", schema: "public", table: "jobs", filter: `id=eq.${id}` }, (payload) => { const next = payload.new as JobRecord; setJob((current) => ({ ...next, error: adminMode ? next.error : null, file_names: current?.file_names })); if (next.status === "completed") void getReportByJob(id).then(setReport); }).on("postgres_changes", { event: "INSERT", schema: "public", table: "job_events", filter: `job_id=eq.${id}` }, (payload) => { const next = payload.new as JobEvent; if (adminMode || publicEventKinds.has(next.kind)) setEvents((current) => [...current, safeEvent(next)]); }).subscribe();
    return () => { void client.removeChannel(channel); };
  }, [adminMode, id, text]);
  const aggregated = useMemo(() => aggregateEvents(events), [events]);
  if (error) return <div className="panel p-6 text-danger">{error}</div>;
  if (!job) return <div className="panel animate-pulse p-12 text-center text-muted">{text("加载任务…", "Loading job…")}</div>;
  const active = !["completed", "cancelled", "needs_input"].includes(job.status);
  const currentStep = stepIndex(job);
  const title = job.file_names?.length ? `${job.file_names[0]}${job.file_names.length > 1 ? ` +${job.file_names.length - 1}` : ""}` : text("论文调研任务", "Literature research job");
  const latest = events.at(-1);
  return <div className="mx-auto max-w-4xl">
    <Link className="button button-secondary mb-6 inline-flex" to={adminMode ? "/admin" : "/"}><ArrowLeft className="h-4 w-4"/>{text(adminMode ? "返回管理界面" : "返回任务列表", adminMode ? "Back to admin" : "Back to jobs")}</Link>
    <div className="flex flex-col justify-between gap-5 sm:flex-row sm:items-start"><div className="min-w-0"><p className="eyebrow">{text("分析任务", "Analysis job")} · {job.id.slice(0, 8)}</p><div className="mt-3 flex flex-wrap items-center gap-3"><h1 className="max-w-2xl truncate text-3xl font-semibold tracking-tight text-content" title={title}>{title}</h1><StatusBadge status={job.status}/></div><p className="mt-3 text-sm text-muted">{job.mode === "single" ? text("单论文", "Single paper") : text("多论文联合", "Multi-paper analysis")} · {text(`${job.max_rounds} 轮`, `${job.max_rounds} round(s)`)}</p>{adminMode && <p className="mt-2 text-xs font-medium text-warning">{text("管理员审计视图 · 报告不可编辑", "Administrator audit view · Reports cannot be edited")}</p>}</div>{adminMode ? <button className="button button-danger" onClick={() => { if (window.confirm(text("确定永久删除该任务吗？运行中的任务会先安全取消。", "Permanently delete this job? Active processing will be cancelled first."))) void adminDeleteJob(id).then(() => navigate("/admin")).catch((cause) => setError(cause.message)); }}><Trash2 className="h-4 w-4"/>{text("删除任务", "Delete job")}</button> : active ? <button className="button button-danger" onClick={() => cancelJob(id).catch((cause) => setError(cause.message))}><XCircle className="h-4 w-4"/>{text("取消任务", "Cancel job")}</button> : <button className="button button-danger" onClick={() => { if (window.confirm(text("确定永久删除该任务及其 PDF、报告和证据吗？", "Permanently delete this job, PDFs, report, and evidence?"))) void deleteJob(id).then(() => navigate("/")).catch((cause) => setError(cause.message)); }}><Trash2 className="h-4 w-4"/>{text("删除任务", "Delete job")}</button>}</div>

    <section className="panel mt-8 p-5 sm:p-6"><div className="flex flex-wrap items-end justify-between gap-4"><div><span className="text-sm text-muted">{text("当前步骤", "Current step")}</span><div className="mt-1 text-xl font-semibold text-content">{stages[Math.min(currentStep, 6)][language === "zh" ? 0 : 1]}</div><p className="mt-2 text-sm text-muted">{subprogress(job, events, language)}</p></div><div className="text-3xl font-semibold tabular-nums text-content">{job.progress}<span className="ml-0.5 text-base text-muted">%</span></div></div><div className="mt-5 h-2 overflow-hidden rounded-full bg-subtle"><div className="h-full rounded-full bg-gradient-to-r from-primary to-accent transition-all duration-700" style={{ width: `${job.progress}%` }}/></div>{active && <p className="mt-4 flex items-center gap-2 text-xs text-muted"><Clock3 className="h-4 w-4 text-accent-strong"/>{text("可以关闭页面；服务器会继续处理。", "You may close this page; the server will continue processing.")}</p>}
      <ol className="mt-6 grid gap-2 sm:grid-cols-2"><>{stages.map((labels, index) => { const complete = job.status === "completed" || index < currentStep; const current = index === currentStep && job.status !== "completed"; return <li className={`job-step ${complete ? "complete" : current ? "current" : "pending"}`} key={labels[1]}>{complete ? <Check className="h-4 w-4"/> : current ? <LoaderCircle className="h-4 w-4 animate-spin"/> : <Circle className="h-4 w-4"/>}<span><small>{text(`步骤 ${index + 1}`, `Step ${index + 1}`)}</small><strong>{labels[language === "zh" ? 0 : 1]}</strong></span></li>; })}</></ol>{job.status === "needs_input" && <Link className="button button-primary mt-5 inline-flex" to="/new">{text("重新上传 PDF", "Upload a replacement PDF")}</Link>}
    </section>
    {adminMode && job.error && <div className="mt-5 flex gap-3 rounded-xl border border-warning/25 bg-warning/[.07] p-4 text-warning"><AlertTriangle className="mt-0.5 h-5 w-5 shrink-0"/><span>{job.error}</span></div>}
    {report && <Link className="panel group mt-6 flex items-center justify-between border-accent/30 p-6 transition hover:bg-accent/[.05]" to={adminMode ? `/admin/reports/${report.id}` : `/reports/${report.id}`}><div><p className="eyebrow">{text("报告已生成", "Report ready")}</p><h2 className="mt-2 text-xl font-semibold text-content">{text("查看研究报告与论文证据", "View report and paper evidence")}</h2></div><ArrowRight className="h-6 w-6 text-accent-strong transition group-hover:translate-x-1"/></Link>}
    <section className="panel mt-6 p-5 sm:p-6"><h2 className="font-semibold text-content">{text("最新进展", "Latest update")}</h2><p className="mt-3 text-sm leading-6 text-content">{latest ? eventLabel(latest, language) : text("等待服务器领取任务…", "Waiting for the worker to claim the job…")}</p>{latest && <p className="mt-1 text-xs text-faint">{formatDate(latest.created_at)}</p>}{adminMode && <details className="report-detail mt-5"><summary><span>{text(`查看管理员日志（${events.length} 条）`, `Administrator log (${events.length})`)}</span><ChevronRight className="h-4 w-4"/></summary><div className="divide-y divide-line px-4 pb-3">{attempts.map((attempt) => <div className="py-3" key={`attempt-${attempt.id}`}><div className="flex flex-wrap items-center justify-between gap-2"><strong className="text-sm text-content">{text(`恢复尝试 ${attempt.attempt_number}`, `Recovery attempt ${attempt.attempt_number}`)}</strong><span className="text-xs text-muted">{attempt.failure_category} · {attempt.checkpoint_stage}</span></div>{attempt.safe_error && <p className="mt-1 break-words text-xs leading-5 text-warning">{attempt.safe_error}</p>}<p className="mt-1 text-xs text-faint">{formatDate(attempt.created_at)}</p></div>)}{aggregated.map((row) => <div className="py-3" key={row.kind}><div className="flex items-start justify-between gap-3"><p className="text-sm text-content">{row.latest.message || eventLabel(row.latest, language)}</p>{row.count > 1 && <span className="rounded-full bg-subtle px-2 py-0.5 text-xs text-muted">×{row.count}</span>}</div><p className="mt-1 text-xs text-faint">{formatDate(row.latest.created_at)} · {row.kind}</p></div>)}</div></details>}</section>
  </div>;
}

function stepIndex(job: JobRecord) {
  if (job.status === "completed") return 7;
  const map: Record<string, number> = { queued: 0, parsing: 1, problem_ready: 2, v4_literature_landscape: 3, searching: 3, v4_full_text: 4, v4_landscape: 4, v4_ideas: 5, analyzing: 5, rendering: 6 };
  const inferred = job.progress >= 92 ? 6 : job.progress >= 72 ? 5 : job.progress >= 52 ? 4 : job.progress >= 30 ? 3 : job.progress >= 15 ? 2 : job.progress > 0 ? 1 : 0;
  return map[job.stage] ?? map[job.status] ?? inferred;
}

function subprogress(job: JobRecord, events: JobEvent[], language: Language) {
  if (job.status === "needs_input") return language === "zh" ? "PDF 损坏、加密或无法解析，请更换文件后重新提交。" : "The PDF is damaged, encrypted, or unreadable. Replace it and submit again.";
  if (["recovering", "failed"].includes(job.status)) {
    const when = job.next_retry_at ? new Date(job.next_retry_at).toLocaleTimeString(language === "zh" ? "zh-CN" : "en-US", { hour: "2-digit", minute: "2-digit" }) : null;
    return language === "zh" ? `将从第 ${Math.min(stepIndex(job) + 1, 7)} 步继续${when ? `，预计 ${when} 自动重试` : ""}` : `Will resume at step ${Math.min(stepIndex(job) + 1, 7)}${when ? ` around ${when}` : ""}`;
  }
  if (["waiting_resources", "budget_blocked"].includes(job.status)) return language === "zh" ? "等待限流、并发或预算窗口恢复，无需操作。" : "Waiting for rate limits, capacity, or the budget window. No action is needed.";
  const profiles = events.filter((item) => item.kind === "external_profile").length;
  const attempt = [...events].reverse().find((item) => item.kind === "idea_attempt")?.data;
  const ideaPart = [...events].reverse().find((item) => item.kind === "idea_generation_part")?.data;
  if (job.stage === "v4_full_text") return language === "zh" ? `已建立 ${profiles} 篇全文证据档案` : `${profiles} full-text evidence profiles built`;
  if (job.stage === "v4_ideas" && attempt && ideaPart && ideaPart.attempt === attempt.attempt) {
    return language === "zh"
      ? `第 ${attempt.attempt}/${attempt.max_attempts} 轮 · 正在生成候选 Idea ${ideaPart.part}/${ideaPart.parts}`
      : `Round ${attempt.attempt}/${attempt.max_attempts} · Generating candidate Idea ${ideaPart.part}/${ideaPart.parts}`;
  }
  if (job.stage === "v4_ideas" && attempt) return language === "zh" ? `正在进行第 ${attempt.attempt}/${attempt.max_attempts} 轮 Idea 审查` : `Idea review round ${attempt.attempt}/${attempt.max_attempts}`;
  const latest = events.at(-1); return latest ? eventLabel(latest, language) : (language === "zh" ? "准备开始" : "Preparing");
}

function aggregateEvents(events: JobEvent[]) {
  const rows = new Map<string, { kind: string; count: number; latest: JobEvent }>();
  for (const event of events) { const row = rows.get(event.kind); rows.set(event.kind, row ? { ...row, count: row.count + 1, latest: event } : { kind: event.kind, count: 1, latest: event }); }
  return [...rows.values()].sort((left, right) => Date.parse(right.latest.created_at) - Date.parse(left.latest.created_at));
}

function eventLabel(event: JobEvent, language: Language) {
  if (event.kind === "idea_generation_part") {
    return language === "zh"
      ? `正在生成第 ${event.data.part}/${event.data.parts} 个候选 Idea`
      : `Generating candidate Idea ${event.data.part}/${event.data.parts}`;
  }
  const labels: Record<string, [string, string]> = { queued: ["任务已进入队列", "Job queued"], resumed: ["已从检查点恢复任务", "Resumed from the latest checkpoint"], stage: ["处理阶段已更新", "Processing stage updated"], paper_parsed: ["论文解析完成", "PDF parsing completed"], retrieval_batch: ["完成一批多平台检索", "Completed a retrieval batch"], retrieval_converged: ["检索覆盖已收敛", "Literature retrieval converged"], external_profile: ["已建立一篇全文证据档案", "Built a full-text evidence profile"], idea_attempt: ["正在生成并审查论文级 Idea", "Generating and reviewing paper-level Ideas"], round_complete: ["一轮检索与分析已完成", "Research loop completed"], evidence_previews: ["引用页面快照已准备", "Evidence page previews are ready"], completed: ["报告已生成", "Report generated"], auto_recovery: ["系统正在从最近检查点自动恢复", "Automatically recovering from the latest checkpoint"], waiting_resources: ["正在等待资源恢复", "Waiting for resources to recover"] };
  return (labels[event.kind] ?? ["系统正在继续处理", "Processing continues"])[language === "zh" ? 0 : 1];
}

function safeEvent(event: Partial<JobEvent>): JobEvent {
  return { id: event.id ?? 0, kind: event.kind ?? "progress", message: event.message || "", data: event.data ?? {}, created_at: event.created_at ?? new Date().toISOString() };
}
