import type { JobEvent, JobRecord } from "./types";

export const workflowStages = [
  ["等待处理", "Waiting"],
  ["解析 PDF", "Parsing PDF"],
  ["提取并核验问题定义", "Grounding the problem"],
  ["多平台检索相关论文", "Multi-source retrieval"],
  ["筛选全文并建立研究现状", "Full-text research and landscape"],
  ["Idea 与实验规范", "Ideas and experiment specifications"],
  ["生成报告和导出文件", "Rendering report"],
] as const;

export interface WorkflowState {
  step: number;
  detail: number;
  progress: number;
  stage: string;
  substage: string;
  latestEvent?: JobEvent;
}

const stagePositions: Record<string, readonly [number, number]> = {
  queued: [0, 0],
  parsing: [1, 0],
  problem_ready: [2, 0],
  searching: [3, 0],
  v4_literature_landscape: [3, 1],
  v4_full_text: [4, 0],
  v4_landscape: [4, 1],
  analyzing: [5, 0],
  v4_ideas: [5, 1],
  v4_pilot_specification: [5, 2],
  rendering: [6, 0],
  completed: [7, 0],
};

const progressFloors = [0, 5, 15, 35, 52, 74, 92, 100] as const;

function numberValue(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function progressStep(progress: number): number {
  if (progress >= 100) return 7;
  if (progress >= 92) return 6;
  if (progress >= 74) return 5;
  if (progress >= 52) return 4;
  if (progress >= 35) return 3;
  if (progress >= 15) return 2;
  if (progress > 0) return 1;
  return 0;
}

function eventState(event: JobEvent): Omit<WorkflowState, "latestEvent"> | null {
  const declaredStage = stringValue(event.data.workflow_stage);
  const declaredPosition = stagePositions[declaredStage];
  if (declaredPosition !== undefined) {
    return {
      step: declaredPosition[0],
      detail: declaredPosition[1],
      stage: declaredStage,
      substage: stringValue(event.data.substage),
      progress: numberValue(event.data.progress) ?? progressFloors[declaredPosition[0]],
    };
  }
  if (event.kind === "completed") return { step: 7, detail: 0, stage: "completed", substage: "completed", progress: 100 };
  if (event.kind === "evidence_previews") return { step: 6, detail: 0, stage: "rendering", substage: "evidence_previews", progress: 92 };
  if (event.kind === "idea_generation_part") return { step: 5, detail: 1, stage: "v4_ideas", substage: "idea_generation", progress: 74 };
  if (event.kind === "idea_attempt") return { step: 5, detail: 1, stage: "v4_ideas", substage: "idea_review", progress: 74 };
  if (event.kind === "round_complete" && ("selected" in event.data || "candidates" in event.data)) {
    return { step: 5, detail: 1, stage: "v4_ideas", substage: "idea_review", progress: 74 };
  }
  if (event.kind === "external_profile") return { step: 4, detail: 0, stage: "v4_full_text", substage: "full_text", progress: 52 };
  if (event.kind === "retrieval_batch" || event.kind === "retrieval_converged" || event.kind === "round_complete") {
    return { step: 3, detail: 1, stage: "v4_literature_landscape", substage: "literature_retrieval", progress: 35 };
  }
  if (event.kind === "paper_parsed") return { step: 1, detail: 0, stage: "parsing", substage: "pdf_parsing", progress: 5 };
  return null;
}

export function deriveWorkflowState(
  job: Pick<JobRecord, "status" | "stage" | "progress">,
  events: JobEvent[] = [],
): WorkflowState {
  const explicitPosition = stagePositions[job.stage] ?? stagePositions[job.status];
  const explicitStep = explicitPosition?.[0];
  const initialStep = job.status === "completed"
    ? 7
    : explicitStep ?? progressStep(job.progress);
  let state: WorkflowState = {
    step: initialStep,
    detail: explicitPosition?.[1] ?? 0,
    progress: job.status === "completed" ? 100 : Math.max(job.progress, progressFloors[initialStep]),
    stage: job.status === "completed" ? "completed" : job.stage,
    substage: "",
  };

  for (const event of events) {
    const next = eventState(event);
    if (!next || next.step < state.step || (next.step === state.step && next.detail < state.detail)) continue;
    if (next.step > state.step || next.detail > state.detail || !state.latestEvent || Date.parse(event.created_at) >= Date.parse(state.latestEvent.created_at)) {
      state = {
        ...next,
        progress: Math.max(state.progress, next.progress, progressFloors[next.step]),
        latestEvent: event,
      };
    } else {
      state.progress = Math.max(state.progress, next.progress);
    }
  }
  state.progress = Math.max(0, Math.min(100, state.progress));
  return state;
}

export function mergeWorkflowState(previous: WorkflowState | null, next: WorkflowState): WorkflowState {
  if (!previous || next.step > previous.step || (next.step === previous.step && next.detail > previous.detail)) return next;
  if (next.step < previous.step || next.detail < previous.detail) return previous;
  return {
    ...next,
    progress: Math.max(previous.progress, next.progress),
    latestEvent: next.latestEvent ?? previous.latestEvent,
    substage: next.substage || previous.substage,
  };
}

export function workflowStageName(step: number, language: "zh" | "en") {
  return workflowStages[Math.min(Math.max(step, 0), 6)][language === "zh" ? 0 : 1];
}
