import type { SupabaseClient, User } from "npm:@supabase/supabase-js@2.112.4";
import { HttpError } from "./http.ts";

export function experimentPilotEnabled(): boolean {
  return Deno.env.get("E2B_PILOT_ENABLED")?.trim().toLowerCase() === "true";
}

export function manualExperimentEnabled(): boolean {
  return experimentPilotEnabled()
    && Deno.env.get("E2B_MANUAL_EXPERIMENT_ENABLED")?.trim().toLowerCase() === "true";
}

export function automaticExperimentEnabled(): boolean {
  return experimentPilotEnabled()
    && Deno.env.get("E2B_AUTO_EXPERIMENT_ENABLED")?.trim().toLowerCase() === "true";
}

export function requireExperimentPilotEnabled(): void {
  if (!experimentPilotEnabled()) {
    throw new HttpError(503, "Experiment service is not enabled");
  }
}

export function requireManualExperimentEnabled(): void {
  if (!manualExperimentEnabled()) {
    throw new HttpError(503, "Manual experiment creation is not enabled");
  }
}

export type ExperimentRow = {
  id: string;
  report_id: string;
  report_generation_id?: string | null;
  job_id: string;
  user_id: string;
  idea_key: string;
  idea_rank: number;
  idea_snapshot: Record<string, unknown>;
  pilot_specification?: Record<string, unknown>;
  pilot_compilation_required?: boolean;
  status: string;
  stage: string;
  progress: number;
  outcome: string;
  public_summary?: Record<string, unknown>;
  checkpoint?: Record<string, unknown>;
  baseline_revision_id?: string | null;
  current_revision_id?: string | null;
  latest_run_id?: string | null;
  user_validation_count: number;
  max_user_validations: number;
  repair_count: number;
  e2b_seconds: number;
  e2b_cost_usd: number | string;
  llm_cost_cny: number | string;
  cancellation_requested: boolean;
  deletion_requested_at?: string | null;
  created_at: string;
  updated_at: string;
  experiment_runs?: Array<{ count?: number }>;
};

export type ExperimentPermissions = {
  readCode: boolean;
  editCode: boolean;
  chat: boolean;
  terminalRead: boolean;
  terminalWrite: boolean;
  runValidation: boolean;
  rollback: boolean;
  download: boolean;
  cancel: boolean;
  delete: boolean;
};

export type ExperimentReadiness = {
  specificationReady: boolean;
  repositoryReadable: boolean;
  repositoryEditable: boolean;
  runtimeReady: boolean;
  assistantReady: boolean;
  terminalReady: boolean;
  validationReady: boolean;
};

export type ExperimentAccess = {
  experiment: ExperimentRow;
  adminMode: boolean;
  permissions: ExperimentPermissions;
};

export async function getExperimentAccess(
  admin: SupabaseClient,
  user: User,
  experimentId: string,
): Promise<ExperimentAccess> {
  if (!isUuid(experimentId)) throw new HttpError(400, "Invalid experiment id");
  const [{ data, error }, { data: adminRow, error: adminError }] = await Promise.all([
    admin.from("idea_experiments").select("*").eq("id", experimentId).maybeSingle(),
    admin.from("admin_users").select("user_id").eq("user_id", user.id).maybeSingle(),
  ]);
  if (error) throw error;
  if (adminError) throw adminError;
  if (!data) throw new HttpError(404, "Experiment not found");
  const experiment = data as ExperimentRow;
  const adminMode = Boolean(adminRow) && experiment.user_id !== user.id;
  if (experiment.user_id !== user.id && !adminMode) throw new HttpError(404, "Experiment not found");
  return { experiment, adminMode, permissions: experimentPermissions(experiment, adminMode) };
}

export function experimentPermissions(
  experiment: ExperimentRow,
  adminMode: boolean,
): ExperimentPermissions {
  const deleting = Boolean(experiment.deletion_requested_at);
  const cancelled = experiment.status === "cancelled" || experiment.cancellation_requested;
  const readable = !deleting;
  const mutable = !adminMode && !deleting && !cancelled;
  return {
    readCode: readable,
    editCode: mutable,
    chat: mutable,
    terminalRead: !deleting && !cancelled,
    terminalWrite: mutable,
    runValidation: mutable && experiment.user_validation_count < experiment.max_user_validations,
    rollback: mutable,
    download: readable,
    cancel: !deleting && !["ready", "cancelled"].includes(experiment.status),
    delete: !deleting,
  };
}

function generatedCheckpointFiles(checkpoint: Record<string, unknown> | undefined): boolean {
  const batches = checkpoint?.file_batches;
  if (!batches || typeof batches !== "object" || Array.isArray(batches)) return false;
  return Object.values(batches).some((batch) => {
    if (!batch || typeof batch !== "object" || Array.isArray(batch)) return false;
    return Array.isArray((batch as Record<string, unknown>).files)
      && ((batch as Record<string, unknown>).files as unknown[]).length > 0;
  });
}

export function experimentReadiness(
  experiment: ExperimentRow,
  runtime?: Record<string, unknown> | null,
): ExperimentReadiness {
  const specificationReady = Boolean(
    experiment.pilot_specification
      && Object.keys(experiment.pilot_specification).length
      && !experiment.pilot_compilation_required,
  );
  const repositoryReadable = Boolean(
    experiment.current_revision_id || generatedCheckpointFiles(experiment.checkpoint),
  );
  const inactive = experiment.status === "cancelled"
    || experiment.cancellation_requested
    || Boolean(experiment.deletion_requested_at);
  const runtimeReady = ["running", "paused"].includes(String(runtime?.state ?? ""));
  return {
    specificationReady,
    repositoryReadable,
    // Checkpoint file batches are immediately readable, but edits wait for the
    // first immutable Git revision so optimistic writes have a stable base.
    repositoryEditable: Boolean(experiment.current_revision_id) && !inactive,
    runtimeReady,
    assistantReady: specificationReady && !inactive,
    terminalReady: runtimeReady && !inactive,
    validationReady: specificationReady && Boolean(experiment.current_revision_id) && runtimeReady && !inactive,
  };
}

export function publicExperiment(experiment: ExperimentRow): Record<string, unknown> {
  const idea = experiment.idea_snapshot ?? {};
  const summary = experiment.public_summary ?? {};
  return {
    id: experiment.id,
    reportId: experiment.report_id,
    generationId: experiment.report_generation_id ?? null,
    jobId: experiment.job_id,
    ideaKey: experiment.idea_key,
    ideaRank: experiment.idea_rank,
    ideaTitleZh: typeof idea.title_zh === "string" ? idea.title_zh : "",
    ideaTitleEn: typeof idea.title_en === "string" ? idea.title_en : "",
    status: experiment.status,
    stage: experiment.stage,
    progress: experiment.progress,
    outcome: experiment.outcome,
    runCount: Number(experiment.experiment_runs?.[0]?.count ?? 0),
    summaryZh: typeof summary.summary_zh === "string" ? summary.summary_zh : null,
    summaryEn: typeof summary.summary_en === "string" ? summary.summary_en : null,
    baselineRevisionId: experiment.baseline_revision_id ?? null,
    currentRevisionId: experiment.current_revision_id ?? null,
    latestRunId: experiment.latest_run_id ?? null,
    userValidationCount: experiment.user_validation_count,
    maxUserValidations: experiment.max_user_validations,
    repairCount: experiment.repair_count,
    e2bSeconds: experiment.e2b_seconds,
    e2bCostUsd: Number(experiment.e2b_cost_usd),
    llmCostCny: Number(experiment.llm_cost_cny),
    cancellationRequested: experiment.cancellation_requested,
    deletionRequested: Boolean(experiment.deletion_requested_at),
    createdAt: experiment.created_at,
    updatedAt: experiment.updated_at,
  };
}

export function requirePermission(
  permissions: ExperimentPermissions,
  permission: keyof ExperimentPermissions,
): void {
  if (!permissions[permission]) throw new HttpError(403, "Experiment action is not permitted");
}

export function isUuid(value: unknown): value is string {
  return typeof value === "string" && /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value);
}

export function validateRepositoryPath(value: unknown): string {
  if (typeof value !== "string") {
    throw new HttpError(400, "Invalid repository path");
  }
  const normalized = value.replaceAll("\\", "/").trim().replace(/^\.\//, "");
  const encodedLength = new TextEncoder().encode(normalized).byteLength;
  if (
    encodedLength < 1
    || encodedLength > 240
    || [...normalized].some((character) => {
      const codePoint = character.codePointAt(0) ?? 0;
      return codePoint < 32 || codePoint === 127;
    })
  ) {
    throw new HttpError(400, "Invalid repository path");
  }
  const segments = normalized.split("/");
  if (normalized.startsWith("/") || segments.some((part) => !part || part === "." || part === "..")) {
    throw new HttpError(400, "Invalid repository path");
  }
  if (segments.some((part) =>
    part === ".git"
    || part === ".research-atlas"
    || part === ".env"
    || part.startsWith(".env.")
  )) {
    throw new HttpError(403, "Protected repository path");
  }
  return normalized;
}

function boundedActionText(value: unknown, maximum: number): string | null {
  if (typeof value !== "string") return null;
  const normalized = value.replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/g, "").trim();
  if (!normalized) return null;
  return normalized.length <= maximum ? normalized : `${normalized.slice(0, maximum - 1)}…`;
}

function boundedActionTail(value: unknown, maximum: number): string | null {
  if (typeof value !== "string") return null;
  const normalized = value.replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/g, "").trim();
  if (!normalized) return null;
  return normalized.length <= maximum ? normalized : `…${normalized.slice(-(maximum - 1))}`;
}

function actionPaths(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  const unique = new Set<string>();
  for (const raw of value.slice(0, 48)) {
    try {
      unique.add(validateRepositoryPath(raw));
    } catch {
      // Never return an unsafe or malformed path supplied through action data.
    }
  }
  return [...unique];
}

function actionCommandResults(value: unknown): Array<Record<string, unknown>> {
  if (!Array.isArray(value)) return [];
  return value.slice(0, 12).flatMap((raw) => {
    if (!raw || typeof raw !== "object" || Array.isArray(raw)) return [];
    const row = raw as Record<string, unknown>;
    const command = boundedActionText(row.command, 1_000);
    if (!command) return [];
    const rawExitCode = row.exitCode ?? row.exit_code;
    const exitCode = typeof rawExitCode === "number" && Number.isSafeInteger(rawExitCode)
      ? rawExitCode : null;
    const rawElapsed = row.elapsedSeconds ?? row.elapsed_seconds;
    const elapsedSeconds = typeof rawElapsed === "number" && Number.isFinite(rawElapsed) && rawElapsed >= 0
      ? Math.min(rawElapsed, 86_400) : null;
    const stdout = boundedActionTail(row.stdout, 1_600);
    const stderr = boundedActionTail(row.stderr, 1_600);
    // A concise, bounded tail is sufficient for the workspace audit card and
    // avoids placing full terminal transcripts into the assistant feed.
    const resultSummary = exitCode !== null && exitCode !== 0
      ? stderr ?? stdout : stdout ?? stderr;
    return [{ command, exitCode, elapsedSeconds, resultSummary }];
  });
}

export function actionSummary(row: Record<string, unknown>): Record<string, unknown> {
  const request = row.request && typeof row.request === "object" ? row.request as Record<string, unknown> : {};
  const response = row.response && typeof row.response === "object" ? row.response as Record<string, unknown> : {};
  const rawKind = String(row.kind ?? "system");
  const kind = ["assistant", "command", "validation", "rollback", "system"].includes(rawKind) ? rawKind : "system";
  const rawState = String(row.status ?? "queued");
  return {
    id: row.id,
    kind,
    state: rawState === "recovering" ? "queued" : rawState,
    role: rawKind === "assistant" ? (rawState === "completed" ? "assistant" : "user") : "system",
    prompt: request.message ?? request.prompt ?? null,
    content: response.content ?? response.message ?? response.explanationZh ?? response.explanationEn ?? response.explanation ?? null,
    command: request.command ?? response.command ?? null,
    modifiedFiles: actionPaths(response.files ?? response.modifiedFiles),
    deletedFiles: actionPaths(response.deletedFiles ?? response.deleted_files),
    commandResults: actionCommandResults(response.commands ?? response.commandResults),
    revisionIdBefore: row.base_revision_id ?? null,
    revisionIdAfter: row.result_revision_id ?? null,
    attachments: Array.isArray(row.attachments) ? row.attachments : [],
    createdAt: row.created_at,
    completedAt: row.completed_at ?? null,
    updatedAt: row.updated_at,
  };
}

export function actionFeed(row: Record<string, unknown>): Array<Record<string, unknown>> {
  const item = actionSummary(row);
  if (row.kind !== "assistant" || row.status !== "completed") return [item];
  const request = row.request && typeof row.request === "object" ? row.request as Record<string, unknown> : {};
  return [{
    ...item, id: `${String(row.id)}:request`, role: "user", content: null,
    prompt: request.message ?? request.prompt ?? null,
  }, { ...item, attachments: [] }];
}
