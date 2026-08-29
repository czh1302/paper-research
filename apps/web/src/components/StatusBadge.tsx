import type { JobStatus } from "../lib/types";
import { useLanguage } from "../lib/language";

const labels: Record<JobStatus, [string, string]> = { queued: ["排队中", "Queued"], parsing: ["解析 PDF", "Parsing PDF"], problem_ready: ["问题已抽取", "Problem extracted"], searching: ["检索中", "Searching"], analyzing: ["分析中", "Analyzing"], rendering: ["生成报告", "Rendering report"], completed: ["已完成", "Completed"], cancelled: ["已取消", "Cancelled"], failed: ["失败", "Failed"], budget_blocked: ["预算暂停", "Budget paused"] };
const tones: Record<JobStatus, string> = { queued: "border-line bg-subtle text-muted", parsing: "border-accent/25 bg-accent/10 text-accent-strong", problem_ready: "border-accent/25 bg-accent/10 text-accent-strong", searching: "border-info/25 bg-info/10 text-info", analyzing: "border-violet/25 bg-violet/10 text-violet", rendering: "border-warning/25 bg-warning/10 text-warning", completed: "border-success/25 bg-success/10 text-success", cancelled: "border-line bg-subtle text-muted", failed: "border-danger/25 bg-danger/10 text-danger", budget_blocked: "border-warning/25 bg-warning/10 text-warning" };

export function StatusBadge({ status }: { status: JobStatus }) {
  const { language } = useLanguage();
  return <span className={`inline-flex rounded-full border px-2.5 py-1 text-xs font-medium ${tones[status]}`}>{labels[status][language === "zh" ? 0 : 1]}</span>;
}
