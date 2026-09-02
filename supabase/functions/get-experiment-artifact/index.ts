import { authenticate, handleError, HttpError, json, preflight } from "../_shared/http.ts";
import { getExperimentAccess, isUuid, requirePermission } from "../_shared/experiments.ts";
Deno.serve(async (request) => {
  const early = preflight(request); if (early) return early;
  try {
    const { user, admin } = await authenticate(request); const body = await request.json();
    if (!isUuid(body.artifactId)) throw new HttpError(400, "Invalid artifact id");
    const { data: artifact, error } = await admin.from("experiment_artifacts")
      .select("id,experiment_id,kind,storage_path,file_name,mime_type,byte_size,sha256,metadata,created_at")
      .eq("id", body.artifactId).maybeSingle();
    if (error) throw error;
    if (!artifact) throw new HttpError(404, "Artifact not found");
    if (body.experimentId !== undefined && body.experimentId !== artifact.experiment_id) {
      throw new HttpError(404, "Artifact not found");
    }
    const access = await getExperimentAccess(admin, user, artifact.experiment_id);
    requirePermission(access.permissions, "readCode");
    const { data: signed, error: signedError } = await admin.storage.from("experiment-artifacts")
      .createSignedUrl(artifact.storage_path, 300, { download: body.download === true ? artifact.file_name : undefined });
    if (signedError || !signed?.signedUrl) throw signedError ?? new Error("Could not sign experiment artifact");
    const { storage_path: _path, experiment_id: _experimentId, ...metadata } = artifact;
    return json(request, { artifact: { id: metadata.id, name: metadata.file_name,
      kind: metadata.kind === "repository_zip" || metadata.kind === "git_bundle" ? "archive"
        : metadata.kind === "source_file" ? "source" : metadata.kind === "result_report" ? "report" : metadata.kind,
      mimeType: metadata.mime_type, byteSize: metadata.byte_size, createdAt: metadata.created_at,
      metadata: metadata.metadata }, signedUrl: signed.signedUrl,
      expiresAt: new Date(Date.now() + 300_000).toISOString() });
  } catch (error) { return handleError(request, error); }
});
