import { requireSupabase } from "./supabase";
import type { AdminJobRow, AdminUserRow, JobRecord, Quota, ReportRecord } from "./types";

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

export async function listJobs(): Promise<JobRecord[]> {
  const { data, error } = await requireSupabase().from("jobs").select("*").order("created_at", { ascending: false });
  if (error) throw error;
  return data as JobRecord[];
}

export async function getQuota(): Promise<Quota> {
  const monthStart = new Date();
  monthStart.setUTCDate(1); monthStart.setUTCHours(0, 0, 0, 0);
  const { data, error } = await requireSupabase().from("user_quotas").select("allocation,used,reserved").eq("month_start", monthStart.toISOString().slice(0, 10)).maybeSingle();
  if (error) throw error;
  return data ?? { allocation: 5, used: 0, reserved: 0 };
}

export async function createAnalysis(files: File[], mode: "single" | "multi", maxRounds: number, turnstileToken: string) {
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
    body: { mode, uploadIds: uploads.map((item) => item.uploadId), maxRounds, turnstileToken },
  });
  if (error) throw error;
  return data.job as JobRecord;
}

export async function getJob(jobId: string): Promise<JobRecord> {
  const { data, error } = await requireSupabase().from("jobs").select("*").eq("id", jobId).single();
  if (error) throw error;
  return data as JobRecord;
}

export async function getReportByJob(jobId: string): Promise<ReportRecord | null> {
  const { data, error } = await requireSupabase().from("reports").select("*").eq("job_id", jobId).maybeSingle();
  if (error) throw error;
  return data as ReportRecord | null;
}

export async function getReport(reportId: string): Promise<ReportRecord> {
  const { data, error } = await requireSupabase().from("reports").select("*").eq("id", reportId).single();
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
