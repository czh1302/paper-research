import { authenticate, handleError, HttpError, json, preflight, requireActiveAccount } from "../_shared/http.ts";
import { experimentPermissions, isUuid, publicExperiment, requireManualExperimentEnabled } from "../_shared/experiments.ts";

Deno.serve(async (request) => {
  const early = preflight(request); if (early) return early;
  try {
    requireManualExperimentEnabled();
    const { user, admin } = await authenticate(request);
    await requireActiveAccount(admin, user);
    const body = await request.json();
    if (!isUuid(body.reportId) || typeof body.ideaKey !== "string" || !body.ideaKey.trim()) {
      throw new HttpError(400, "reportId and ideaKey are required");
    }
    const configuredSpendLimit = Number(Deno.env.get("E2B_MAX_SPEND_USD") ?? "90");
    const maxSpendUsd = Number.isFinite(configuredSpendLimit) && configuredSpendLimit >= 0
      ? Math.min(configuredSpendLimit, 90)
      : 90;
    const configuredLlmReservation = Number(Deno.env.get("EXPERIMENT_LLM_MAX_CNY_PER_RUN") ?? "5");
    const llmReservationCny = Number.isFinite(configuredLlmReservation) && configuredLlmReservation >= 0
      ? Math.min(configuredLlmReservation, 5)
      : 5;
    const configuredGlobalLlm = Number(Deno.env.get("EXPERIMENT_LLM_GLOBAL_MAX_CNY") ?? "200");
    const globalLlmMaxCny = Number.isFinite(configuredGlobalLlm) && configuredGlobalLlm >= 0
      ? Math.min(configuredGlobalLlm, 10_000)
      : 200;
    const { data, error } = await admin.rpc("enqueue_idea_experiment", {
      p_report_id: body.reportId,
      p_idea_key: body.ideaKey.trim(),
      p_user_id: user.id,
      p_automatic: false,
      p_max_spend_usd: maxSpendUsd,
      p_llm_reservation_cny: llmReservationCny,
      p_global_llm_max_cny: globalLlmMaxCny,
    });
    if (error?.message.includes("report not found") || error?.message.includes("idea not found")) {
      throw new HttpError(404, "Report or Idea not found");
    }
    if (error?.message.includes("must be completed") || error?.message.includes("formally reviewed")) {
      throw new HttpError(409, error.message);
    }
    if (error?.message.includes("spend limit")) {
      throw new HttpError(503, "Experiment budget is temporarily unavailable");
    }
    if (error?.message.includes("inference budget")) {
      throw new HttpError(409, "Experiment inference budget reached");
    }
    if (error) throw error;
    return json(request, {
      experiment: publicExperiment(data), permissions: experimentPermissions(data, false),
    }, 201);
  } catch (error) { return handleError(request, error); }
});
