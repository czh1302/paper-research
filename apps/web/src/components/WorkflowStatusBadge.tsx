import { useLanguage } from "../lib/language";
import { workflowStageName } from "../lib/job-workflow";
import type { JobStatus } from "../lib/types";
import { StatusBadge } from "./StatusBadge";

const exceptionalStatuses = new Set<JobStatus>([
  "completed", "cancelled", "failed", "budget_blocked", "recovering", "waiting_resources", "needs_input",
]);

const tones = [
  "border-line bg-subtle text-muted",
  "border-accent/25 bg-accent/10 text-accent-strong",
  "border-accent/25 bg-accent/10 text-accent-strong",
  "border-info/25 bg-info/10 text-info",
  "border-info/25 bg-info/10 text-info",
  "border-violet/25 bg-violet/10 text-violet",
  "border-warning/25 bg-warning/10 text-warning",
];

export function WorkflowStatusBadge({ status, step }: { status: JobStatus; step: number }) {
  const { language } = useLanguage();
  if (exceptionalStatuses.has(status)) return <StatusBadge status={status} />;
  const label = workflowStageName(step, language);
  return <span className={`inline-flex rounded-full border px-2.5 py-1 text-xs font-medium ${tones[Math.min(Math.max(step, 0), 6)]}`}>{label}</span>;
}
