import { authenticate, handleError, HttpError, json, preflight } from "../_shared/http.ts";

Deno.serve(async (request) => {
  const early = preflight(request);
  if (early) return early;
  try {
    const { user, admin } = await authenticate(request);
    const { jobId } = await request.json();
    if (!jobId) throw new HttpError(400, "jobId is required");
    const { error } = await admin.rpc("request_job_cancellation", {
      p_job_id: jobId,
      p_user_id: user.id,
    });
    if (error?.message.includes("job not found")) throw new HttpError(404, "Job not found");
    if (error) throw error;
    return json(request, { cancelled: true });
  } catch (error) {
    return handleError(request, error);
  }
});
