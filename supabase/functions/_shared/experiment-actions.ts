import type { SupabaseClient, User } from "npm:@supabase/supabase-js@2.112.4";
import { attachmentIds, CHAT_IMAGE_MAX_COUNT, CHAT_IMAGE_MAX_TOTAL_BYTES, finalizeNewAttachments, publicAttachment, validateContextAttachments } from "./experiment-attachments.ts";
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
  const newAttachmentIds = kind === "assistant" ? attachmentIds(body.attachmentIds) : [];
  const requestedContextIds = kind === "assistant" ? attachmentIds(body.contextAttachmentIds) : [];
  const contextAttachmentIds = requestedContextIds.filter((id) => !newAttachmentIds.includes(id));
  if (newAttachmentIds.length + contextAttachmentIds.length > CHAT_IMAGE_MAX_COUNT) {
    throw new HttpError(400, "At most four images can be sent to the assistant");
  }
  if (kind === "assistant") {
    let message = typeof payload.message === "string" ? payload.message.trim() : "";
    if (!message && newAttachmentIds.length) {
      message = "请分析这些图片与当前仓库的关系并给出下一步建议。除非图片中有明确要求，否则不要修改文件或执行命令。";
    }
    if (!message || message.length > 20_000) throw new HttpError(400, "Assistant message or image is required");
    payload.message = message;
    payload.prompt = message;
    const [newAttachments, contextAttachments, history] = await Promise.all([
      finalizeNewAttachments(admin, access.experiment.id, user.id, newAttachmentIds),
      validateContextAttachments(admin, access.experiment.id, user.id, contextAttachmentIds),
      admin.from("experiment_actions").select("request,response,created_at")
        .eq("experiment_id", access.experiment.id).eq("kind", "assistant").eq("status", "completed")
        .order("created_at", { ascending: false }).limit(6),
    ]);
    if (history.error) throw history.error;
    if ([...newAttachments, ...contextAttachments].reduce((sum, item) => sum + Number(item.byte_size), 0) > CHAT_IMAGE_MAX_TOTAL_BYTES) {
      throw new HttpError(400, "Assistant image context exceeds the total size limit");
    }
    const bounded = (value: unknown) => typeof value === "string" ? value.trim().slice(0, 2_000) : "";
    payload.conversationContext = (history.data ?? []).reverse().flatMap((row) => {
      const requestPayload = row.request && typeof row.request === "object" ? row.request as Record<string, unknown> : {};
      const responsePayload = row.response && typeof row.response === "object" ? row.response as Record<string, unknown> : {};
      const userMessage = bounded(requestPayload.message ?? requestPayload.prompt);
      const assistantMessage = bounded(responsePayload.explanationZh ?? responsePayload.explanationEn ?? responsePayload.content ?? responsePayload.message);
      return [
        ...(userMessage ? [{ role: "user", content: userMessage }] : []),
        ...(assistantMessage ? [{ role: "assistant", content: assistantMessage }] : []),
      ];
    }).slice(-12);
    payload.attachmentIds = newAttachmentIds;
    payload.contextAttachmentIds = contextAttachmentIds;
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
  const { data, error } = await admin.rpc("enqueue_experiment_action_with_attachments", {
    p_experiment_id: access.experiment.id,
    p_user_id: access.experiment.user_id,
    p_kind: kind,
    p_request: payload,
    p_base_revision_id: baseRevisionId,
    p_idempotency_key: idempotencyKey,
    p_attachment_ids: newAttachmentIds,
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
  if (error?.message.includes("attachment")) throw new HttpError(409, "Experiment chat image is unavailable");
  if (error) throw error;
  const { data: attachedRows, error: attachedError } = newAttachmentIds.length
    ? await admin.from("experiment_chat_attachments").select("*").in("id", newAttachmentIds)
    : { data: [], error: null };
  if (attachedError) throw attachedError;
  return json(request, {
    state: data.status,
    action: actionSummary({ ...data, attachments: (attachedRows ?? []).map(publicAttachment) }),
  }, 202);
}
