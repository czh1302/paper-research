import type { JobStatus } from "../lib/types";

const labels: Record<JobStatus, string> = { queued: "排队中", parsing: "解析 PDF", problem_ready: "问题已抽取", searching: "检索中", analyzing: "分析中", rendering: "生成报告", completed: "已完成", cancelled: "已取消", failed: "失败", budget_blocked: "预算暂停" };
const tones: Record<JobStatus, string> = { queued: "border-slate-500/30 bg-slate-500/10 text-slate-300", parsing: "border-cyan/30 bg-cyan/10 text-cyan", problem_ready: "border-cyan/30 bg-cyan/10 text-cyan", searching: "border-blue-400/30 bg-blue-400/10 text-blue-300", analyzing: "border-violet-400/30 bg-violet-400/10 text-violet-300", rendering: "border-amber/30 bg-amber/10 text-amber", completed: "border-emerald-400/30 bg-emerald-400/10 text-emerald-300", cancelled: "border-slate-500/30 bg-slate-500/10 text-slate-400", failed: "border-red-400/30 bg-red-400/10 text-red-300", budget_blocked: "border-amber/30 bg-amber/10 text-amber" };

export function StatusBadge({ status }: { status: JobStatus }) {
  return <span className={`inline-flex rounded-full border px-2.5 py-1 text-xs font-medium ${tones[status]}`}>{labels[status]}</span>;
}

