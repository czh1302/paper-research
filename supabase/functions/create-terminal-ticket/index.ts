import { authenticate, handleError, HttpError, json, preflight, randomToken, sha256 } from "../_shared/http.ts";
import { getExperimentAccess, requirePermission } from "../_shared/experiments.ts";

function terminalRelayUrl(): URL {
  const configured = Deno.env.get("EXPERIMENT_TERMINAL_WS_URL")?.trim();
  let url: URL;
  if (configured) {
    url = new URL(configured);
  } else {
    const supabaseUrl = Deno.env.get("SUPABASE_URL")?.trim();
    if (!supabaseUrl) throw new HttpError(503, "Terminal relay is not configured");
    url = new URL("/functions/v1/experiment-terminal-relay", supabaseUrl);
  }
  if (url.protocol === "https:") url.protocol = "wss:";
  if (url.protocol === "http:") url.protocol = "ws:";
  if (!["ws:", "wss:"].includes(url.protocol)) throw new HttpError(503, "Terminal relay URL is invalid");
  return url;
}

Deno.serve(async (request) => {
  const early = preflight(request); if (early) return early;
  try {
    if (Deno.env.get("E2B_PILOT_ENABLED")?.trim().toLowerCase() !== "true") {
      throw new HttpError(503, "Experiment runtime is temporarily unavailable");
    }
    const { user, admin } = await authenticate(request); const body = await request.json();
    const access = await getExperimentAccess(admin, user, String(body.experimentId ?? ""));
    requirePermission(access.permissions, "terminalRead");
    const requestedWrite = body.mode === "write";
    if (requestedWrite) requirePermission(access.permissions, "terminalWrite");
    const token = randomToken(); const expiresAt = new Date(Date.now() + 60_000).toISOString();
    const mode = requestedWrite ? "write" : "read";
    const maxSpend = Number(Deno.env.get("E2B_MAX_SPEND_USD") ?? "90");
    const costRate = Number(Deno.env.get("E2B_ESTIMATED_COST_PER_SECOND_USD") ?? "0.000092");
    const reserveSeconds = Number(Deno.env.get("E2B_RUN_TIMEOUT_SECONDS") ?? "3600");
    const { data: updatedRuntime, error } = await admin.rpc("issue_experiment_terminal_ticket", {
      p_experiment_id: access.experiment.id,
      p_user_id: access.experiment.user_id,
      p_token_hash: await sha256(token),
      p_ticket_mode: mode,
      p_expires_at: expiresAt,
      p_max_spend_usd: Number.isFinite(maxSpend) && maxSpend >= 0
        ? Math.min(maxSpend, 90) : 90,
      p_max_concurrency: 1,
      p_estimated_cost_per_second_usd: Number.isFinite(costRate) && costRate > 0 ? costRate : 0.000092,
      p_reserve_seconds: Number.isFinite(reserveSeconds) && reserveSeconds > 0
        ? Math.min(Math.floor(reserveSeconds), 3600) : 3600,
    });
    if (error?.message.includes("spend limit")) throw new HttpError(503, "Experiment budget is temporarily unavailable");
    if (error?.message.includes("concurrency limit")) throw new HttpError(409, "Another experiment is currently using the sandbox");
    if (error?.message.includes("not available") || error?.message.includes("not found")) {
      throw new HttpError(409, "Terminal is not available");
    }
    if (error) throw error;
    if (!updatedRuntime) throw new HttpError(409, "Terminal is not available");
    const websocketUrl = terminalRelayUrl();
    websocketUrl.searchParams.set("ticket", token);
    return json(request, {
      websocketUrl: websocketUrl.toString(),
      expiresAt,
    });
  } catch (error) { return handleError(request, error); }
});
