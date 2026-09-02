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
    const { data: experimentRows, error: experimentsError } = await admin
      .from("idea_experiments")
      .select("id,idea_key,idea_rank,outcome,public_summary")
      .eq("report_id", report.id)
      .eq("status", "ready")
      .neq("outcome", "pending")
      .is("deletion_requested_at", null)
      .order("idea_rank", { ascending: true });
    if (experimentsError) throw experimentsError;
    const experiments: Array<Record<string, unknown>> = [];
    for (const experiment of experimentRows ?? []) {
      const { data: artifactRows, error: artifactsError } = await admin
        .from("experiment_artifacts")
        .select("id,kind,file_name,mime_type,byte_size,sha256,metadata")
        .eq("experiment_id", experiment.id)
        .eq("public_safe", true)
        .in("kind", ["plot", "metrics", "result_report"])
        .in("mime_type", ["application/json", "image/png", "image/jpeg", "image/webp"])
        .limit(20);
      if (artifactsError) throw artifactsError;
      const publicArtifacts: Array<Record<string, unknown>> = [];
      for (const artifact of artifactRows ?? []) {
        publicArtifacts.push({ artifactId: artifact.id, kind: artifact.kind,
          fileName: artifact.file_name, mimeType: artifact.mime_type,
          byteSize: artifact.byte_size, sha256: artifact.sha256, metadata: artifact.metadata });
      }
      experiments.push({ ideaKey: experiment.idea_key, ideaRank: experiment.idea_rank,
        outcome: experiment.outcome, summary: experiment.public_summary, artifacts: publicArtifacts });
    }
    return json(request, {
      report: {
        id: report.id,
        job_id: report.job_id,
        content: report.summary || report.content,
        created_at: report.created_at,
      },
      experiments,
      expiresAt: data.expires_at,
    });
  } catch (error) {
    return handleError(request, error);
  }
});
