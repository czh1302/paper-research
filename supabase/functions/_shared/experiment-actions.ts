import type { SupabaseClient, User } from "npm:@supabase/supabase-js@2.112.4";
import { actionSummary, getExperimentAccess, isUuid, requireExperimentPilotEnabled, requirePermission } from "./experiments.ts";
import { HttpError, json } from "./http.ts";

const allowedKinds = new Set(["assistant", "chat", "command", "rollback", "validation", "restore"]);

function boundedBudget(name: string, fallback: number, maximum: number): number {
  const parsed = Number(Deno.env.get(name) ?? String(fallback));
  return Number.isFinite(parsed) && parsed >= 0 ? Math.min(parsed, maximum) : fallback;
}

export async function enqueueUserExperimentAction(
  request: Request,
  user: User,
  admin: SupabaseClient,
  forcedKind?: string,
): Promise<Response> {
  requireExperimentPilotEnabled();
  const body = await request.json();
  const access = await getExperimentAccess(admin, user, String(body.experimentId ?? ""));
  if (access.adminMode) throw new HttpError(403, "Administrators have read-only experiment access");
  let kind = forcedKind ?? String(body.kind ?? "");
  if (kind === "chat") kind = "assistant";
  if (!allowedKinds.has(kind)) throw new HttpError(400, "Invalid experiment action");
  const permission = kind === "assistant" ? "chat" : kind === "validation"
    ? "runValidation" : kind === "rollback" ? "rollback" : kind === "restore"
    ? "editCode" : "terminalWrite";
  requirePermission(access.permissions, permission);

  const payload: Record<string, unknown> = body.payload && typeof body.payload === "object" && !Array.isArray(body.payload)
    ? { ...body.payload } : {};
  if (body.prompt !== undefined) { payload.message = body.prompt; payload.prompt = body.prompt; }
  if (body.command !== undefined) payload.command = body.command;
  if (body.revisionId !== undefined) payload.revisionId = body.revisionId;
  if (kind === "assistant") {
    const message = typeof payload.message === "string" ? payload.message.trim() : "";
    if (!message || message.length > 20_000) throw new HttpError(400, "Assistant message is required");
    payload.message = message;
    payload.prompt = message;
  }
  if (kind === "command") {
    const command = typeof payload.command === "string" ? payload.command.trim() : "";
    if (!command || command.length > 8_000) throw new HttpError(400, "Command is required");
    payload.command = command;
  }
  if (kind === "rollback") {
    if (!isUuid(payload.revisionId)) throw new HttpError(400, "A valid revisionId is required");
    const { data: revision, error } = await admin.from("experiment_revisions").select("id")
      .eq("id", payload.revisionId).eq("experiment_id", access.experiment.id).maybeSingle();
    if (error) throw error;
    if (!revision) throw new HttpError(404, "Revision not found");
  }

  const baseRevisionId = typeof body.baseRevisionId === "string"
    ? body.baseRevisionId : access.experiment.current_revision_id ?? null;
  const idempotencyKey = typeof body.idempotencyKey === "string" && body.idempotencyKey.trim()
    ? body.idempotencyKey.trim() : crypto.randomUUID();
  const perActionLlm = boundedBudget("EXPERIMENT_LLM_MAX_CNY_PER_RUN", 5, 5);
  const assistantLlm = boundedBudget("EXPERIMENT_ASSISTANT_MAX_CNY", 20, 100);
  const experimentLlm = boundedBudget("EXPERIMENT_LLM_MAX_CNY", 40, 200);
  const globalLlm = boundedBudget("EXPERIMENT_LLM_GLOBAL_MAX_CNY", 200, 10_000);
  const maxSpendUsd = boundedBudget("E2B_MAX_SPEND_USD", 90, 90);
  const { data, error } = await admin.rpc("enqueue_experiment_action", {
    p_experiment_id: access.experiment.id,
    p_user_id: access.experiment.user_id,
    p_kind: kind,
    p_request: payload,
    p_base_revision_id: baseRevisionId,
    p_idempotency_key: idempotencyKey,
    p_llm_reservation_cny: perActionLlm,
    p_assistant_llm_max_cny: assistantLlm,
    p_experiment_llm_max_cny: experimentLlm,
    p_global_llm_max_cny: globalLlm,
    p_max_spend_usd: maxSpendUsd,
  });
  if (error?.message.includes("revision conflict")) throw new HttpError(409, "Experiment revision conflict");
  if (error?.message.includes("manual validation limit")) throw new HttpError(409, "Manual validation limit reached");
  if (error?.message.includes("assistant budget") || error?.message.includes("inference budget")) {
    throw new HttpError(409, "Experiment inference budget reached");
  }
  if (error?.message.includes("spend limit")) throw new HttpError(503, "Experiment budget is temporarily unavailable");
  if (error) throw error;
  return json(request, { state: data.status, action: actionSummary(data) }, 202);
}
