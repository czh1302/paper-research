import type { SupabaseClient, User } from "npm:@supabase/supabase-js@2.112.4";
import { checkpointSourceFiles } from "./experiment-checkpoint-files.ts";
import { actionSummary, experimentPilotEnabled, getExperimentAccess, requirePermission, validateRepositoryPath } from "./experiments.ts";
import { HttpError, json, sha256 } from "./http.ts";

type FileOperation = "read" | "save" | "move" | "delete";

async function readBaselineSource(
  admin: SupabaseClient,
  experimentId: string,
  baselineRevisionId: string | null | undefined,
  currentRevisionId: string | null | undefined,
  path: string,
): Promise<string | undefined> {
  if (!baselineRevisionId || baselineRevisionId === currentRevisionId) return undefined;
  const { data: artifact, error } = await admin.from("experiment_artifacts")
    .select("storage_path,byte_size")
    .eq("experiment_id", experimentId)
    .eq("revision_id", baselineRevisionId)
    .eq("kind", "source_file")
    .contains("metadata", { path })
    .order("created_at", { ascending: false })
    .limit(1)
    .maybeSingle();
  if (error || !artifact?.storage_path || Number(artifact.byte_size ?? 0) > 2_000_000) {
    return undefined;
  }
  const { data: blob, error: downloadError } = await admin.storage
    .from("experiment-artifacts")
    .download(artifact.storage_path);
  if (downloadError || !blob) return undefined;
  return await blob.text();
}

export async function handleExperimentFile(
  request: Request,
  user: User,
  admin: SupabaseClient,
  operation: FileOperation,
): Promise<Response> {
  const pilotEnabled = experimentPilotEnabled();
  if (operation !== "read" && !pilotEnabled) {
    throw new HttpError(503, "Experiment service is not enabled");
  }
  const body = await request.json();
  const experimentId = String(body.experimentId ?? "");
  const access = await getExperimentAccess(admin, user, experimentId);
  const path = validateRepositoryPath(body.path ?? body.fromPath);
  const baseRevisionId = typeof body.baseRevisionId === "string"
    ? body.baseRevisionId : access.experiment.current_revision_id ?? null;

  if (operation === "read") {
    requirePermission(access.permissions, "readCode");
    let artifactQuery = admin
      .from("experiment_artifacts")
      .select("id,storage_path,mime_type,byte_size,sha256,metadata")
      .eq("experiment_id", experimentId)
      .eq("kind", "source_file")
      .contains("metadata", { path });
    artifactQuery = access.experiment.current_revision_id
      ? artifactQuery.eq("revision_id", access.experiment.current_revision_id)
      : artifactQuery.is("revision_id", null);
    const { data: artifact, error: artifactError } = await artifactQuery
      .order("created_at", { ascending: false })
      .limit(1)
      .maybeSingle();
    if (artifactError) throw artifactError;
    if (artifact?.storage_path) {
      if (artifact.byte_size && Number(artifact.byte_size) > 2_000_000) {
        throw new HttpError(413, "File is too large for the editor");
      }
      const { data: blob, error: downloadError } = await admin.storage
        .from("experiment-artifacts")
        .download(artifact.storage_path);
      if (downloadError || !blob) throw downloadError ?? new Error("Could not read source file");
      const originalContent = await readBaselineSource(
        admin,
        experimentId,
        access.experiment.baseline_revision_id,
        access.experiment.current_revision_id,
        path,
      );
      return json(request, {
        state: "ready",
        path,
        content: await blob.text(),
        mimeType: artifact.mime_type,
        sha256: artifact.sha256,
        revisionId: artifact.metadata?.revision_id ?? access.experiment.current_revision_id ?? null,
        ...(originalContent === undefined ? {} : { originalContent }),
      });
    }
    // Automatic v1 generation persists each completed model batch in the
    // lease-fenced experiment checkpoint before a sandbox or Git revision is
    // created. Make only the requested, manifest-declared file readable while
    // generation is in progress; never return the checkpoint itself.
    if (!access.experiment.current_revision_id) {
      const generated = checkpointSourceFiles(access.experiment.checkpoint)
        .find((file) => file.path === path);
      if (generated) {
        return json(request, {
          state: "ready",
          path,
          content: generated.content,
          mimeType: "text/plain; charset=utf-8",
          sha256: await sha256(generated.content),
          revisionId: null,
          generationSnapshot: true,
        });
      }
    }
    if (access.experiment.status !== "ready") {
      throw new HttpError(409, "The requested file has not been archived yet");
    }
    // A read miss normally queues a sandbox action. With the kill switch off,
    // only already archived source artifacts remain readable; never resume a
    // paid runtime merely to satisfy a read-only request.
    if (!pilotEnabled) {
      throw new HttpError(503, "Archived source file is not available");
    }
  } else {
    requirePermission(access.permissions, "editCode");
  }

  const actionKind = operation === "read" ? "read_file" : operation === "save"
    ? "save_file" : operation === "move" ? "move_file" : "delete_file";
  const payload: Record<string, unknown> = { path };
  if (operation === "save") {
    if (typeof body.content !== "string") throw new HttpError(400, "File content is required");
    if (new TextEncoder().encode(body.content).byteLength > 1_000_000) {
      throw new HttpError(413, "File is too large for the editor");
    }
    payload.content = body.content;
    if (typeof body.expectedSha256 === "string" && body.expectedSha256) {
      let currentQuery = admin.from("experiment_artifacts")
        .select("sha256").eq("experiment_id", experimentId)
        .eq("kind", "source_file").contains("metadata", { path });
      currentQuery = baseRevisionId
        ? currentQuery.eq("revision_id", baseRevisionId)
        : currentQuery.is("revision_id", null);
      const { data: current, error: currentError } = await currentQuery.limit(1).maybeSingle();
      if (currentError) throw currentError;
      if (current && current.sha256 !== body.expectedSha256) throw new HttpError(409, "File changed since it was opened");
    }
  }
  if (operation === "move") payload.destination = validateRepositoryPath(body.destination ?? body.toPath);

  const idempotencyKey = typeof body.idempotencyKey === "string" && body.idempotencyKey
    ? body.idempotencyKey : `file:${operation}:${await sha256(JSON.stringify({ baseRevisionId, ...payload }))}`;

  const { data, error } = await admin.rpc("enqueue_experiment_action", {
    p_experiment_id: experimentId,
    p_user_id: access.experiment.user_id,
    p_kind: actionKind,
    p_request: payload,
    p_base_revision_id: baseRevisionId,
    p_idempotency_key: idempotencyKey,
  });
  if (error?.message.includes("revision conflict")) throw new HttpError(409, "Experiment revision conflict");
  if (error) throw error;
  let action = data as Record<string, unknown>;
  const deadline = Date.now() + 25_000;
  while (action.status === "queued" || action.status === "running") {
    if (Date.now() >= deadline) throw new HttpError(503, "File operation is still being applied; retry shortly");
    await new Promise((resolve) => setTimeout(resolve, 350));
    const { data: refreshed, error: refreshError } = await admin.from("experiment_actions")
      .select("*").eq("id", action.id).maybeSingle();
    if (refreshError) throw refreshError;
    if (!refreshed) throw new HttpError(404, "File operation no longer exists");
    action = refreshed;
  }
  if (action.status !== "completed") throw new HttpError(503, "File operation is queued for automatic recovery");
  const response = action.response && typeof action.response === "object"
    ? action.response as Record<string, unknown> : {};
  if (operation === "read") {
    const originalContent = await readBaselineSource(
      admin,
      experimentId,
      access.experiment.baseline_revision_id,
      access.experiment.current_revision_id,
      path,
    );
    return json(request, { path, content: String(response.content ?? ""),
      sha256: String(response.sha256 ?? ""), revisionId: access.experiment.current_revision_id ?? null,
      ...(originalContent === undefined ? {} : { originalContent }) });
  }
  const revisionId = typeof action.result_revision_id === "string" ? action.result_revision_id : null;
  const { data: sourceRows, error: sourcesError } = await admin.from("experiment_artifacts")
    .select("file_name,byte_size,sha256,metadata,created_at")
    .eq("experiment_id", experimentId).eq("revision_id", revisionId).eq("kind", "source_file");
  if (sourcesError) throw sourcesError;
  const files = (sourceRows ?? []).map((row) => ({
    path: typeof row.metadata?.path === "string" ? row.metadata.path : row.file_name,
    type: "file", size: row.byte_size, sha256: row.sha256, updatedAt: row.created_at,
  }));
  if (operation === "save") {
    const file = files.find((item) => item.path === path);
    if (!file) throw new HttpError(503, "Saved file archive is not ready");
    return json(request, { file, revision: revisionId ? { id: revisionId } : undefined });
  }
  return json(request, { files, revision: revisionId ? { id: revisionId } : undefined,
    action: actionSummary(action) });
}
