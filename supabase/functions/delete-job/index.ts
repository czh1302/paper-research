import { authenticate, handleError, HttpError, json, preflight } from "../_shared/http.ts";

Deno.serve(async (request) => {
  const early = preflight(request);
  if (early) return early;
  try {
    const { user, admin } = await authenticate(request);
    const { jobId } = await request.json();
    if (!jobId) throw new HttpError(400, "jobId is required");
    const { data: job, error: jobError } = await admin
      .from("jobs")
      .select("id,status,job_files(upload:uploads(storage_path))")
      .eq("id", jobId)
      .eq("user_id", user.id)
      .maybeSingle();
    if (jobError) throw jobError;
    if (!job) throw new HttpError(404, "Job not found");
    if (!["completed", "cancelled", "failed", "budget_blocked"].includes(job.status)) {
      throw new HttpError(409, "Cancel the active job before deleting it");
    }
    const paths = (job.job_files ?? []).map((item: any) => item.upload?.storage_path).filter(Boolean);
    if (paths.length) await admin.storage.from("papers").remove(paths);
    const { error } = await admin.from("jobs").delete().eq("id", jobId).eq("user_id", user.id);
    if (error) throw error;
    return json(request, { deleted: true });
  } catch (error) {
    return handleError(request, error);
  }
});

