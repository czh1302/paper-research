import { authenticate, handleError, HttpError, json, preflight } from "../_shared/http.ts";
import { getExperimentAccess, requirePermission } from "../_shared/experiments.ts";
Deno.serve(async (request) => {
  const early = preflight(request); if (early) return early;
  try {
    const { user, admin } = await authenticate(request); const body = await request.json();
    const access = await getExperimentAccess(admin, user, String(body.experimentId ?? ""));
    requirePermission(access.permissions, "download");
    const { data: artifact, error } = await admin.from("experiment_artifacts")
      .select("id,storage_path,file_name,mime_type,byte_size,sha256,revision_id,created_at")
      .eq("experiment_id", access.experiment.id).eq("kind", "repository_zip")
      .order("created_at", { ascending: false }).limit(1).maybeSingle();
    if (error) throw error;
    if (!artifact) throw new HttpError(404, "Repository archive is not ready");
    const { data: signed, error: signedError } = await admin.storage.from("experiment-artifacts")
      .createSignedUrl(artifact.storage_path, 300, { download: artifact.file_name });
    if (signedError || !signed?.signedUrl) throw signedError ?? new Error("Could not sign repository archive");
    const { storage_path: _path, ...metadata } = artifact;
    return json(request, { ...metadata, signedUrl: signed.signedUrl,
      expiresAt: new Date(Date.now() + 300_000).toISOString() });
  } catch (error) { return handleError(request, error); }
});
