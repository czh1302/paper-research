import { authenticate, handleError, json, preflight } from "../_shared/http.ts";
import { checkpointSourceFiles, legacyCheckpointFileEntries } from "../_shared/experiment-checkpoint-files.ts";
import { publicAttachment } from "../_shared/experiment-attachments.ts";
import { actionFeed, experimentReadiness, getExperimentAccess, publicExperiment } from "../_shared/experiments.ts";

Deno.serve(async (request) => {
  const early = preflight(request); if (early) return early;
  try {
    const { user, admin } = await authenticate(request);
    const body = await request.json();
    const access = await getExperimentAccess(admin, user, String(body.experimentId ?? ""));
    const id = access.experiment.id;
    const actionColumns = access.adminMode
      ? "id,kind,status,base_revision_id,result_revision_id,safe_error,created_at,updated_at,completed_at"
      : "id,kind,status,request,response,base_revision_id,result_revision_id,safe_error,created_at,updated_at,completed_at";
    const [revisions, runs, actions, artifacts, runtime, attachments] = await Promise.all([
      admin.from("experiment_revisions").select("id,parent_revision_id,revision_number,actor,git_commit,tree_hash,summary,immutable,created_at")
        .eq("experiment_id", id).order("revision_number", { ascending: false }).limit(100),
      admin.from("experiment_runs").select("id,revision_id,run_number,trigger_kind,status,outcome,metrics,evaluation,safe_error,e2b_seconds,e2b_cost_usd,llm_cost_cny,started_at,completed_at,created_at")
        .eq("experiment_id", id).order("run_number", { ascending: false }).limit(20),
      admin.from("experiment_actions").select(actionColumns)
        .eq("experiment_id", id).order("created_at", { ascending: false }).limit(100),
      admin.from("experiment_artifacts").select("id,run_id,revision_id,kind,file_name,mime_type,byte_size,sha256,public_safe,metadata,created_at")
        .eq("experiment_id", id).order("created_at", { ascending: false }).limit(500),
      admin.from("experiment_runtime").select("state,paused_at,destroy_after,last_heartbeat_at")
        .eq("experiment_id", id).maybeSingle(),
      access.adminMode
        ? Promise.resolve({ data: [], error: null })
        : admin.from("experiment_chat_attachments").select("id,action_id,file_name,mime_type,declared_mime_type,byte_size,sha256,width,height,status,created_at")
          .eq("experiment_id", id).eq("status", "bound").order("created_at", { ascending: true }).limit(400),
    ]);
    for (const result of [revisions, runs, actions, artifacts, runtime, attachments]) if (result.error) throw result.error;
    const checkpoint = access.experiment.checkpoint ?? {};
    const sourceRows = (artifacts.data ?? []).filter((row) => row.kind === "source_file"
      && row.revision_id === access.experiment.current_revision_id);
    const files = sourceRows.map((row) => ({
      path: typeof row.metadata?.path === "string" ? row.metadata.path : row.file_name,
      type: "file", size: row.byte_size, sha256: row.sha256, updatedAt: row.created_at,
    }));
    const generationFiles = access.experiment.current_revision_id
      ? []
      : checkpointSourceFiles(checkpoint);
    const checkpointGeneratedFiles = generationFiles.map((file) => ({
      path: file.path,
      type: "file" as const,
      size: file.byteSize,
      updatedAt: typeof checkpoint.updated_at === "string" ? checkpoint.updated_at : undefined,
    }));
    const checkpointFiles = checkpointGeneratedFiles.length
      ? checkpointGeneratedFiles
      : legacyCheckpointFileEntries(checkpoint);
    const runtimeState = runtime.data ?? { state: "absent" };
    return json(request, {
      experiment: { ...publicExperiment(access.experiment), runCount: runs.data?.length ?? 0 },
      pilotSpecification: access.experiment.pilot_specification ?? {},
      permissions: access.permissions,
      readiness: experimentReadiness(access.experiment, runtimeState),
      accessMode: access.adminMode ? "admin" : "owner",
      files: files.length ? files : checkpointFiles,
      revisions: (revisions.data ?? []).map((row) => ({
        id: row.id, parentId: row.parent_revision_id,
        label: row.summary?.label ?? row.summary?.zh ?? row.summary?.en ?? `v${row.revision_number}`,
        actor: ["automatic", "system"].includes(row.actor) ? "worker" : row.actor,
        commitSha: row.git_commit, createdAt: row.created_at,
      })),
      runs: (runs.data ?? []).map((row) => ({
        id: row.id, kind: row.trigger_kind === "automatic" ? "automatic" : "manual",
        status: row.status === "completed" ? "ready" : row.status,
        outcome: row.outcome, metrics: row.metrics ?? {},
        summaryZh: row.evaluation?.summary_zh ?? null, summaryEn: row.evaluation?.summary_en ?? null,
        e2bSeconds: row.e2b_seconds, e2bCostUsd: Number(row.e2b_cost_usd),
        llmCostCny: Number(row.llm_cost_cny), createdAt: row.created_at, completedAt: row.completed_at,
      })),
      // Assistant prompts and responses belong to the experiment owner. Admin audit
      // mode exposes code, terminal output, results and costs, but not private chat.
      actions: access.adminMode
        ? []
        : ((actions.data ?? []) as unknown as Array<Record<string, unknown>>)
          .map((row) => ({
            ...row,
            attachments: (attachments.data ?? [])
              .filter((attachment) => attachment.action_id === row.id)
              .map((attachment) => publicAttachment(attachment)),
          }))
          .reverse()
          .flatMap((row) => actionFeed(row)),
      artifacts: (artifacts.data ?? []).filter((row) => !["source_file", "git_bundle"].includes(row.kind)).map((row) => ({
        id: row.id, name: row.file_name,
        kind: row.kind === "repository_zip" || row.kind === "git_bundle" ? "archive"
          : row.kind === "source_file" ? "source" : row.kind === "result_report" ? "report"
          : ["log", "metrics", "plot"].includes(row.kind) ? row.kind : "report",
        mimeType: row.mime_type, byteSize: row.byte_size, publicSafe: row.public_safe,
        createdAt: row.created_at, metadata: row.metadata,
      })),
      runtime: runtimeState,
    });
  } catch (error) { return handleError(request, error); }
});
