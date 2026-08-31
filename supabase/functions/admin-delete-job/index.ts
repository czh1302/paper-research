import { authenticate, handleError, HttpError, json, preflight, requireAdministrator } from "../_shared/http.ts";

Deno.serve(async (request) => {
  const early = preflight(request);
  if (early) return early;
  try {
    const { user, admin } = await authenticate(request);
    await requireAdministrator(admin, user);
    const { jobId } = await request.json();
    if (typeof jobId !== "string" || !jobId) throw new HttpError(400, "jobId is required");
    const { data, error } = await admin.rpc("admin_request_job_deletion", { p_job_id: jobId, p_requester_id: user.id });
    if (error?.message.includes("job not found")) throw new HttpError(404, "Job not found");
    if (error) throw error;
    return json(request, { state: data === "deleted" ? "deleted" : "pending" });
  } catch (error) {
    return handleError(request, error);
  }
});
