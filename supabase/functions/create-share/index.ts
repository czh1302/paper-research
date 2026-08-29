import { authenticate, handleError, HttpError, json, preflight, randomToken, sha256 } from "../_shared/http.ts";

Deno.serve(async (request) => {
  const early = preflight(request);
  if (early) return early;
  try {
    const { user, admin } = await authenticate(request);
    const { reportId } = await request.json();
    if (!reportId) throw new HttpError(400, "reportId is required");
    const { data: report, error } = await admin
      .from("reports")
      .select("id,job:jobs!inner(user_id)")
      .eq("id", reportId)
      .eq("job.user_id", user.id)
      .maybeSingle();
    if (error) throw error;
    if (!report) throw new HttpError(404, "Report not found");
    const token = randomToken();
    const expiresAt = new Date(Date.now() + 30 * 24 * 60 * 60 * 1000).toISOString();
    const { data: share, error: insertError } = await admin
      .from("share_tokens")
      .insert({ report_id: reportId, user_id: user.id, token_hash: await sha256(token), expires_at: expiresAt })
      .select("id")
      .single();
    if (insertError) throw insertError;
    return json(request, { shareId: share.id, token, expiresAt }, 201);
  } catch (error) {
    return handleError(request, error);
  }
});

