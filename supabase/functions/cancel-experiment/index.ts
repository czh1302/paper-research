import { authenticate, handleError, HttpError, json, preflight } from "../_shared/http.ts";
import { experimentPermissions, getExperimentAccess, publicExperiment, requirePermission } from "../_shared/experiments.ts";
Deno.serve(async (request) => {
  const early = preflight(request); if (early) return early;
  try {
    const { user, admin } = await authenticate(request); const body = await request.json();
    const access = await getExperimentAccess(admin, user, String(body.experimentId ?? ""));
    requirePermission(access.permissions, "cancel");
    const { data, error } = await admin.rpc("request_experiment_cancellation", {
      p_experiment_id: access.experiment.id, p_user_id: access.experiment.user_id,
    });
    if (error?.message.includes("not found")) throw new HttpError(404, "Experiment not found");
    if (error) throw error;
    return json(request, {
      experiment: publicExperiment(data), permissions: experimentPermissions(data, access.adminMode),
    });
  } catch (error) { return handleError(request, error); }
});
