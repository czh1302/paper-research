import { authenticate, handleError, HttpError, json, preflight } from "../_shared/http.ts";
import { automaticExperimentEnabled, experimentPermissions, isUuid, manualExperimentEnabled, publicExperiment } from "../_shared/experiments.ts";

Deno.serve(async (request) => {
  const early = preflight(request); if (early) return early;
  try {
    const { user, admin } = await authenticate(request);
    const body = await request.json();
    const reportId = body.reportId;
    if (!isUuid(reportId)) throw new HttpError(400, "Invalid report id");
    const [{ data: report, error }, { data: adminRow, error: adminError }] = await Promise.all([
      admin.from("reports").select("id,job:jobs!inner(user_id)").eq("id", reportId).maybeSingle(),
      admin.from("admin_users").select("user_id").eq("user_id", user.id).maybeSingle(),
    ]);
    if (error) throw error; if (adminError) throw adminError;
    if (!report) throw new HttpError(404, "Report not found");
    const job = Array.isArray(report.job) ? report.job[0] : report.job;
    if (job?.user_id !== user.id && !adminRow) throw new HttpError(404, "Report not found");
    const { data, error: experimentsError } = await admin.from("idea_experiments")
      .select("*,experiment_runs!experiment_runs_experiment_id_fkey(count)").eq("report_id", reportId).is("deletion_requested_at", null)
      .order("idea_rank", { ascending: true });
    if (experimentsError) throw experimentsError;
    const adminMode = Boolean(adminRow) && job?.user_id !== user.id;
    return json(request, {
      manualEnabled: manualExperimentEnabled(),
      automaticEnabled: automaticExperimentEnabled(),
      experiments: (data ?? []).map((row) => ({
      ...publicExperiment(row), permissions: experimentPermissions(row, adminMode),
      accessMode: adminMode ? "admin" : "owner",
    })),
    });
  } catch (error) { return handleError(request, error); }
});
