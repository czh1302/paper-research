import { adminClient, corsHeaders, handleError, HttpError, preflight, sha256 } from "../_shared/http.ts";
import { isUuid } from "../_shared/experiments.ts";

Deno.serve(async (request) => {
  const early = preflight(request); if (early) return early;
  try {
    const body = await request.json();
    if (typeof body.token !== "string" || body.token.length < 32 || !isUuid(body.artifactId)) {
      throw new HttpError(400, "Invalid shared artifact request");
    }
    const admin = adminClient();
    const { data: share, error: shareError } = await admin.from("share_tokens")
      .select("report_id").eq("token_hash", await sha256(body.token)).is("revoked_at", null)
      .gt("expires_at", new Date().toISOString()).maybeSingle();
    if (shareError) throw shareError;
    if (!share) throw new HttpError(404, "Share not found or expired");
    const { data: artifact, error } = await admin.from("experiment_artifacts")
      .select("storage_path,file_name,mime_type,kind,byte_size,experiment:idea_experiments!inner(report_id,status,outcome,deletion_requested_at)")
      .eq("id", body.artifactId).eq("public_safe", true)
      .in("kind", ["plot", "metrics", "result_report"])
      .in("mime_type", ["application/json", "image/png", "image/jpeg", "image/webp"])
      .maybeSingle();
    if (error) throw error;
    const experiment = Array.isArray(artifact?.experiment) ? artifact.experiment[0] : artifact?.experiment;
    if (
      !artifact
      || experiment?.report_id !== share.report_id
      || experiment?.status !== "ready"
      || experiment?.outcome === "pending"
      || experiment?.deletion_requested_at
    ) throw new HttpError(404, "Artifact not found");
    if (!Number.isFinite(Number(artifact.byte_size)) || Number(artifact.byte_size) < 0 || Number(artifact.byte_size) > 8_388_608) {
      throw new HttpError(413, "Shared artifact is too large");
    }
    const { data: blob, error: downloadError } = await admin.storage.from("experiment-artifacts")
      .download(artifact.storage_path);
    if (downloadError || !blob) throw downloadError ?? new Error("Could not read shared artifact");
    if (blob.size > 8_388_608 || blob.size !== Number(artifact.byte_size)) {
      throw new HttpError(413, "Shared artifact size does not match its published metadata");
    }
    const disposition = body.download === true ? "attachment" : "inline";
    return new Response(blob, { status: 200, headers: {
      ...corsHeaders(request), "Content-Type": artifact.mime_type,
      "Content-Disposition": `${disposition}; filename*=UTF-8''${encodeURIComponent(artifact.file_name)}`,
      "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
    } });
  } catch (error) { return handleError(request, error); }
});
