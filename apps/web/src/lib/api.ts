import { requireSupabase } from "./supabase";
import type { AdminJobRow, AdminUserRow, JobRecord, ReportRecord, ReportSectionName, ReportSectionResponse, SourcePdfResponse } from "./types";

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

export async function getSharedReport(token: string): Promise<{ report: ReportRecord; expiresAt: string }> {
  const { data, error } = await requireSupabase().functions.invoke("get-share", { body: { token } });
  if (error) throw error;
  return data;
}

export function downloadText(name: string, text: string, type: string) {
  const link = document.createElement("a");
  link.href = URL.createObjectURL(new Blob([text], { type }));
  link.download = name;
  link.click();
  URL.revokeObjectURL(link.href);
}
