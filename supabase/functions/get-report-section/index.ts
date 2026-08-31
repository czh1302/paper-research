import { adminClient, authenticate, handleError, HttpError, json, preflight, sha256 } from "../_shared/http.ts";

const sections = new Set(["overview", "problem", "landscape", "ideas"]);

Deno.serve(async (request) => {
  const early = preflight(request);
  if (early) return early;
  try {
    const body = await request.json();
    const reportId = String(body.reportId ?? "");
    const section = String(body.section ?? "");
    const shareToken = typeof body.shareToken === "string" ? body.shareToken : "";
    if (!reportId || !sections.has(section)) throw new HttpError(400, "Invalid report section request");
    const admin = adminClient();

    if (shareToken) {
      const { data: share, error: shareError } = await admin
        .from("share_tokens")
        .select("report_id")
        .eq("report_id", reportId)
        .eq("token_hash", await sha256(shareToken))
        .is("revoked_at", null)
        .gt("expires_at", new Date().toISOString())
        .maybeSingle();
      if (shareError) throw shareError;
      if (!share) throw new HttpError(403, "Share access denied");
    } else {
      const { user } = await authenticate(request);
      const [{ data: report, error: reportError }, { data: adminRow, error: adminError }] = await Promise.all([
        admin.from("reports").select("id,job:jobs!inner(user_id)").eq("id", reportId).maybeSingle(),
        admin.from("admin_users").select("user_id").eq("user_id", user.id).maybeSingle(),
      ]);
      if (reportError) throw reportError;
      if (adminError) throw adminError;
      const job = Array.isArray(report?.job) ? report.job[0] : report?.job;
      if (!report) throw new HttpError(404, "Report not found");
      if (job?.user_id !== user.id && !adminRow) throw new HttpError(403, "Report access denied");
    }

    const { data, error } = await admin
      .from("report_sections")
      .select("section,content,updated_at")
      .eq("report_id", reportId)
      .eq("section", section)
      .maybeSingle();
    if (error) throw error;
    if (!data) return json(request, { section, content: null });
    return json(request, data);
  } catch (error) {
    return handleError(request, error);
  }
});
