import { requireSupabase, supabaseAnonKey, supabaseUrl } from "./supabase";
import type {
  AdminDeletionRequest,
  AdminJobRow,
  AdminUserRow,
  ExperimentAction,
  ExperimentArtifact,
  ExperimentChatAttachment,
  ExperimentFileContent,
  ExperimentFileEntry,
  ExperimentSummary,
  ExperimentWorkspace,
  JobRecord,
  ReportRecord,
  ReportExperimentListing,
  ReportSectionName,
  ReportSectionResponse,
  SharedExperimentSummary,
  SourcePdfResponse,
} from "./types";

export async function checkIsAdmin(): Promise<boolean> {
  const { data, error } = await requireSupabase().rpc("is_admin");
  if (error) throw error;
  return data === true;
}

export async function adminListUsers(limit = 100, offset = 0): Promise<AdminUserRow[]> {
  const { data, error } = await requireSupabase().rpc("admin_list_users", {
    p_limit: limit,
    p_offset: offset,
  });
  if (error) throw error;
  return (data ?? []) as AdminUserRow[];
}

export async function adminListJobs(limit = 100, offset = 0): Promise<AdminJobRow[]> {
  const { data, error } = await requireSupabase().rpc("admin_list_jobs", {
    p_limit: limit,
    p_offset: offset,
  });
  if (error) throw error;
  return (data ?? []) as AdminJobRow[];
}

export async function adminListDeletionRequests(): Promise<AdminDeletionRequest[]> {
  const { data, error } = await requireSupabase()
    .from("admin_deletion_requests")
    .select("id,target_kind,target_id,state,attempt_count,next_attempt_at,last_error,created_at")
    .neq("state", "completed")
    .order("created_at", { ascending: false })
    .limit(100);
  if (error) throw error;
  return (data ?? []) as AdminDeletionRequest[];
}

export async function adminDeleteJob(jobId: string): Promise<{ state: "pending" | "deleted" }> {
  const { data, error } = await requireSupabase().functions.invoke("admin-delete-job", { body: { jobId } });
  if (error) throw error;
  return data as { state: "pending" | "deleted" };
}

export async function adminDeleteUser(userId: string, confirmationEmail: string): Promise<{ state: "pending" | "deleted" }> {
  const { data, error } = await requireSupabase().functions.invoke("admin-delete-user", { body: { userId, confirmationEmail } });
  if (error) throw error;
  return data as { state: "pending" | "deleted" };
}

export async function listJobs(limit = 20, offset = 0, favoritesOnly = false): Promise<JobRecord[]> {
  const { data, error } = await requireSupabase().rpc("list_my_jobs", { p_limit: limit, p_offset: offset, p_favorites_only: favoritesOnly });
  if (error) throw error;
  return (data ?? []) as JobRecord[];
}

export async function createAnalysis(files: File[], mode: "single" | "multi", maxRounds: number, turnstileToken: string, researchBrief = "") {
  const client = requireSupabase();
  const { data: uploadData, error: uploadError } = await client.functions.invoke("create-upload", {
    body: { files: files.map((file) => ({ name: file.name, size: file.size, type: file.type })) },
  });
  if (uploadError) throw uploadError;
  const uploads = uploadData.uploads as { uploadId: string; path: string; token: string }[];
  for (let index = 0; index < files.length; index += 1) {
    const upload = uploads[index];
    const { error } = await client.storage.from("papers").uploadToSignedUrl(upload.path, upload.token, files[index], { contentType: "application/pdf" });
    if (error) throw error;
  }
  const { data, error } = await client.functions.invoke("create-job", {
    body: { mode, uploadIds: uploads.map((item) => item.uploadId), maxRounds, turnstileToken, researchBrief },
  });
  if (error) throw error;
  return data.job as JobRecord;
}

export async function getJob(jobId: string, includeInternal = false): Promise<JobRecord> {
  const client = requireSupabase();
  const result = includeInternal
    ? await client.from("jobs").select("id,mode,max_rounds,current_round,status,stage,progress,created_at,completed_at,retry_count,next_retry_at,last_recovery_at,error,job_files(position,upload:uploads(original_name))").eq("id", jobId).single()
    : await client.from("jobs").select("id,mode,max_rounds,current_round,status,stage,progress,created_at,completed_at,retry_count,next_retry_at,last_recovery_at,job_files(position,upload:uploads(original_name))").eq("id", jobId).single();
  const { data, error } = result;
  if (error) throw error;
  const row = data as unknown as Record<string, unknown>;
  const files = (((row.job_files as any[]) ?? [])).sort((a: any, b: any) => a.position - b.position);
  return { ...row, file_names: files.map((item: any) => item.upload?.original_name).filter(Boolean) } as unknown as JobRecord;
}

export async function getReportByJob(jobId: string): Promise<ReportRecord | null> {
  const { data, error } = await requireSupabase().from("reports").select("id,job_id,created_at").eq("job_id", jobId).maybeSingle();
  if (error) throw error;
  return data as ReportRecord | null;
}

export async function getReport(reportId: string): Promise<ReportRecord> {
  const { data, error } = await requireSupabase().from("reports").select("id,job_id,summary,created_at").eq("id", reportId).single();
  if (error) throw error;
  if (data.summary) return { id: data.id, job_id: data.job_id, content: data.summary, created_at: data.created_at } as ReportRecord;
  return getFullReport(reportId);
}

export async function getFullReport(reportId: string): Promise<ReportRecord> {
  const { data, error } = await requireSupabase().from("reports").select("id,job_id,content,markdown,created_at").eq("id", reportId).single();
  if (error) throw error;
  return data as ReportRecord;
}

export async function cancelJob(jobId: string) {
  const { error } = await requireSupabase().functions.invoke("cancel-job", { body: { jobId } });
  if (error) throw error;
}

export async function deleteJob(jobId: string) {
  const { error } = await requireSupabase().functions.invoke("delete-job", { body: { jobId } });
  if (error) throw error;
}

export async function setJobFavorite(jobId: string, isFavorite: boolean) {
  const { data, error } = await requireSupabase().functions.invoke("set-job-favorite", { body: { jobId, isFavorite } });
  if (error) throw error;
  return data as { jobId: string; isFavorite: boolean };
}

const sourcePdfCache = new Map<string, { expiresAt: number; value: Promise<SourcePdfResponse> }>();
export function getSourcePdf(reportId: string, assetId: string, evidenceId: string): Promise<SourcePdfResponse> {
  const key = `${reportId}:${assetId}:${evidenceId}`;
  const cached = sourcePdfCache.get(key);
  if (cached && cached.expiresAt > Date.now()) return cached.value;
  const value = requireSupabase().functions.invoke("get-source-pdf", { body: { reportId, assetId, evidenceId } }).then(({ data, error }) => {
    if (error) { sourcePdfCache.delete(key); throw error; }
    return data as SourcePdfResponse;
  });
  sourcePdfCache.set(key, { expiresAt: Date.now() + 240_000, value });
  return value;
}

export function prefetchSourcePdf(reportId: string, assetId: string, evidenceId: string) {
  void getSourcePdf(reportId, assetId, evidenceId).catch(() => undefined);
}

export async function getReportSection(reportId: string, section: ReportSectionName, shareToken?: string): Promise<ReportSectionResponse> {
  const { data, error } = await requireSupabase().functions.invoke("get-report-section", { body: { reportId, section, shareToken } });
  if (error) throw error;
  return data as ReportSectionResponse;
}

export async function createShare(reportId: string) {
  const { data, error } = await requireSupabase().functions.invoke("create-share", { body: { reportId } });
  if (error) throw error;
  return data as { shareId: string; token: string; expiresAt: string };
}

export async function revokeShare(shareId: string) {
  const { error } = await requireSupabase().functions.invoke("revoke-share", { body: { shareId } });
  if (error) throw error;
}

export async function getSharedReport(token: string): Promise<{ report: ReportRecord; experiments: SharedExperimentSummary[]; expiresAt: string }> {
  const { data, error } = await requireSupabase().functions.invoke("get-share", { body: { token } });
  if (error) throw error;
  const response = data as { report: ReportRecord; experiments?: SharedExperimentSummary[]; expiresAt: string };
  return { ...response, experiments: response.experiments ?? [] };
}

export async function getSharedExperimentArtifact(token: string, artifactId: string, download = false): Promise<Blob> {
  if (!supabaseUrl || !supabaseAnonKey) throw new Error("Supabase frontend environment is not configured");
  const response = await fetch(`${supabaseUrl}/functions/v1/get-shared-experiment-artifact`, {
    method: "POST",
    headers: {
      apikey: supabaseAnonKey,
      Authorization: `Bearer ${supabaseAnonKey}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ token, artifactId, download }),
  });
  if (!response.ok) throw new Error("Shared experiment artifact is unavailable");
  return response.blob();
}

export function downloadText(name: string, text: string, type: string) {
  const link = document.createElement("a");
  link.href = URL.createObjectURL(new Blob([text], { type }));
  link.download = name;
  link.click();
  URL.revokeObjectURL(link.href);
}

async function invokeExperiment<T>(name: string, body: Record<string, unknown>): Promise<T> {
  const { data, error } = await requireSupabase().functions.invoke(name, { body });
  if (error) throw error;
  return data as T;
}

export async function listReportExperiments(reportId: string): Promise<ReportExperimentListing> {
  const data = await invokeExperiment<{
    experiments?: ExperimentSummary[];
    manualEnabled?: boolean;
    automaticEnabled?: boolean;
  } | ExperimentSummary[]>("list-report-experiments", { reportId });
  if (Array.isArray(data)) {
    return { experiments: data, manualEnabled: false, automaticEnabled: false };
  }
  return {
    experiments: data.experiments ?? [],
    manualEnabled: data.manualEnabled === true,
    automaticEnabled: data.automaticEnabled === true,
  };
}

export async function startIdeaExperiment(reportId: string, ideaKey: string): Promise<ExperimentSummary> {
  const data = await invokeExperiment<{ experiment: ExperimentSummary }>("start-idea-experiment", { reportId, ideaKey });
  return data.experiment;
}

export async function getExperimentWorkspace(experimentId: string): Promise<ExperimentWorkspace> {
  return invokeExperiment<ExperimentWorkspace>("get-experiment-workspace", { experimentId });
}

export async function readExperimentFile(experimentId: string, path: string): Promise<ExperimentFileContent> {
  return invokeExperiment<ExperimentFileContent>("read-experiment-file", { experimentId, path });
}

export async function saveExperimentFile(
  experimentId: string,
  path: string,
  content: string,
  options: { expectedSha256?: string; baseRevisionId?: string | null } = {},
): Promise<{ file: ExperimentFileEntry & { sha256: string }; revision?: { id: string } }> {
  return invokeExperiment("save-experiment-file", { experimentId, path, content, ...options });
}

export async function moveExperimentFile(experimentId: string, fromPath: string, toPath: string): Promise<{ files: ExperimentFileEntry[]; revision?: { id: string } }> {
  return invokeExperiment("move-experiment-file", { experimentId, fromPath, toPath });
}

export async function deleteExperimentFile(experimentId: string, path: string): Promise<{ files: ExperimentFileEntry[]; revision?: { id: string } }> {
  return invokeExperiment("delete-experiment-file", { experimentId, path });
}

export async function submitExperimentAction(
  experimentId: string,
  input: {
    kind: "assistant" | "command" | "validation" | "rollback";
    prompt?: string;
    command?: string;
    revisionId?: string;
    attachmentIds?: string[];
    contextAttachmentIds?: string[];
  },
): Promise<ExperimentAction> {
  const data = await invokeExperiment<{ action: ExperimentAction }>("submit-experiment-action", { experimentId, ...input });
  return data.action;
}

export async function uploadExperimentChatImages(
  experimentId: string,
  files: File[],
  onProgress?: (index: number, percent: number) => void,
): Promise<ExperimentChatAttachment[]> {
  const client = requireSupabase();
  const { data, error } = await client.functions.invoke("create-experiment-chat-upload", {
    body: { experimentId, files: files.map((file) => ({ name: file.name, size: file.size, type: file.type })) },
  });
  if (error) throw error;
  const uploads = data.uploads as Array<{ attachmentId: string; uploadUrl: string }>;
  if (!Array.isArray(uploads) || uploads.length !== files.length) throw new Error("Invalid chat upload response");
  const completed: ExperimentChatAttachment[] = [];
  for (let index = 0; index < files.length; index += 1) {
    await new Promise<void>((resolve, reject) => {
      const form = new FormData();
      form.append("cacheControl", "3600");
      form.append("", files[index]);
      const request = new XMLHttpRequest();
      request.open("PUT", uploads[index].uploadUrl);
      request.setRequestHeader("x-upsert", "false");
      request.upload.addEventListener("progress", (event) => {
        if (event.lengthComputable) onProgress?.(index, Math.round(event.loaded / event.total * 100));
      });
      request.addEventListener("load", () => request.status >= 200 && request.status < 300
        ? resolve() : reject(new Error("Chat image upload failed")));
      request.addEventListener("error", () => reject(new Error("Chat image upload failed")));
      request.addEventListener("abort", () => reject(new Error("Chat image upload was cancelled")));
      request.send(form);
    });
    onProgress?.(index, 100);
    completed.push({
      id: uploads[index].attachmentId,
      name: files[index].name,
      mimeType: files[index].type as ExperimentChatAttachment["mimeType"],
      byteSize: files[index].size,
    });
  }
  return completed;
}

export async function getExperimentChatAttachment(
  experimentId: string,
  attachmentId: string,
): Promise<{ attachment: ExperimentChatAttachment; signedUrl: string; expiresIn: number }> {
  return invokeExperiment("get-experiment-chat-attachment", { experimentId, attachmentId });
}

export async function createTerminalTicket(experimentId: string, cols: number, rows: number, writable = false): Promise<{ websocketUrl: string; expiresAt: string }> {
  return invokeExperiment("create-terminal-ticket", { experimentId, cols, rows, mode: writable ? "write" : "read" });
}

export async function cancelExperiment(experimentId: string): Promise<{ experiment: ExperimentSummary }> {
  return invokeExperiment("cancel-experiment", { experimentId });
}

export async function deleteExperiment(experimentId: string): Promise<{ state: "pending" | "deleted" }> {
  return invokeExperiment("delete-experiment", { experimentId });
}

export async function downloadExperimentRepository(experimentId: string): Promise<{ signedUrl: string; expiresAt: string }> {
  return invokeExperiment("download-experiment-repository", { experimentId });
}

export async function getExperimentArtifact(experimentId: string, artifactId: string): Promise<{ signedUrl: string; expiresAt: string; artifact?: ExperimentArtifact }> {
  return invokeExperiment("get-experiment-artifact", { experimentId, artifactId });
}

export function subscribeToExperiment(experimentId: string, onChange: () => void): () => void {
  const client = requireSupabase();
  const channel = client
    .channel(`experiment:${experimentId}`)
    .on("postgres_changes", { event: "*", schema: "public", table: "idea_experiments", filter: `id=eq.${experimentId}` }, onChange)
    .on("postgres_changes", { event: "*", schema: "public", table: "experiment_actions", filter: `experiment_id=eq.${experimentId}` }, onChange)
    .on("postgres_changes", { event: "*", schema: "public", table: "experiment_runs", filter: `experiment_id=eq.${experimentId}` }, onChange)
    .subscribe();
  return () => { void client.removeChannel(channel); };
}

export function subscribeToReportExperiments(reportId: string, onChange: () => void): () => void {
  const client = requireSupabase();
  const channel = client
    .channel(`report-experiments:${reportId}`)
    .on("postgres_changes", { event: "*", schema: "public", table: "idea_experiments", filter: `report_id=eq.${reportId}` }, onChange)
    .subscribe();
  return () => { void client.removeChannel(channel); };
}
