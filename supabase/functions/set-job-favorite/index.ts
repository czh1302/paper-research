import { authenticate, handleError, HttpError, json, preflight } from "../_shared/http.ts";

Deno.serve(async (request) => {
  const early = preflight(request);
  if (early) return early;
  try {
    const { user, admin } = await authenticate(request);
    const { jobId, isFavorite } = await request.json();
    if (typeof jobId !== "string" || typeof isFavorite !== "boolean") {
      throw new HttpError(400, "jobId and isFavorite are required");
    }
    const { data, error } = await admin
      .from("jobs")
      .update({ is_favorite: isFavorite })
      .eq("id", jobId)
      .eq("user_id", user.id)
      .select("id,is_favorite")
      .maybeSingle();
    if (error) throw error;
    if (!data) throw new HttpError(404, "Job not found");
    return json(request, { jobId: data.id, isFavorite: data.is_favorite });
  } catch (error) {
    return handleError(request, error);
  }
});
