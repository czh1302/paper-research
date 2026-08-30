import { adminClient, handleError, HttpError, json, preflight, sha256 } from "../_shared/http.ts";

Deno.serve(async (request) => {
  const early = preflight(request);
  if (early) return early;
  try {
    const { token } = await request.json();
    if (typeof token !== "string" || token.length < 32) throw new HttpError(400, "Invalid share token");
    const admin = adminClient();
    const { data, error } = await admin
      .from("share_tokens")
      .select("id,expires_at,report:reports(id,job_id,summary,content,created_at)")
      .eq("token_hash", await sha256(token))
      .is("revoked_at", null)
      .gt("expires_at", new Date().toISOString())
      .maybeSingle();
    if (error) throw error;
    if (!data) throw new HttpError(404, "Share not found or expired");
    const report = Array.isArray(data.report) ? data.report[0] : data.report;
    if (!report) throw new HttpError(404, "Report not found");
    return json(request, {
      report: {
        id: report.id,
        job_id: report.job_id,
        content: report.summary || report.content,
        created_at: report.created_at,
      },
      expiresAt: data.expires_at,
    });
  } catch (error) {
    return handleError(request, error);
  }
});
