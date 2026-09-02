import type { SupabaseClient } from "npm:@supabase/supabase-js@2.112.4";
import { CHAT_IMAGE_MAX_BYTES, CHAT_IMAGE_MAX_COUNT, CHAT_IMAGE_MAX_TOTAL_BYTES, inspectChatImage } from "./chat-image.ts";
import { HttpError } from "./http.ts";

export { CHAT_IMAGE_MAX_BYTES, CHAT_IMAGE_MAX_COUNT, CHAT_IMAGE_MAX_TOTAL_BYTES } from "./chat-image.ts";

export type ChatAttachmentRow = {
  id: string;
  experiment_id: string;
  user_id: string;
  action_id: string | null;
  storage_path: string;
  file_name: string;
  declared_mime_type: string;
  mime_type: string | null;
  byte_size: number;
  sha256: string | null;
  width: number | null;
  height: number | null;
  status: string;
  created_at: string;
};

function normalizedIds(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return [...new Set(value.filter((item): item is string => typeof item === "string"
    && /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(item)))];
}

export function attachmentIds(value: unknown, maximum = CHAT_IMAGE_MAX_COUNT): string[] {
  const ids = normalizedIds(value);
  if (ids.length > maximum) throw new HttpError(400, "Too many experiment chat attachments");
  if (Array.isArray(value) && ids.length !== value.length) throw new HttpError(400, "Invalid experiment chat attachment id");
  return ids;
}

async function digestHex(bytes: Uint8Array): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", Uint8Array.from(bytes).buffer);
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

export async function finalizeNewAttachments(
  admin: SupabaseClient,
  experimentId: string,
  userId: string,
  ids: string[],
): Promise<ChatAttachmentRow[]> {
  if (!ids.length) return [];
  const { data, error } = await admin.from("experiment_chat_attachments").select("*")
    .in("id", ids).eq("experiment_id", experimentId).eq("user_id", userId);
  if (error) throw error;
  if (!data || data.length !== ids.length) throw new HttpError(404, "Experiment chat attachment not found");
  const rows = data as ChatAttachmentRow[];
  if (rows.reduce((sum, row) => sum + Number(row.byte_size), 0) > CHAT_IMAGE_MAX_TOTAL_BYTES) {
    throw new HttpError(400, "Experiment chat images exceed the total size limit");
  }
  const finalized: ChatAttachmentRow[] = [];
  for (const row of rows) {
    if (row.status === "bound" || row.status === "ready") { finalized.push(row); continue; }
    if (row.status !== "pending" || row.action_id) throw new HttpError(409, "Experiment chat attachment is unavailable");
    const { data: object, error: downloadError } = await admin.storage
      .from("experiment-chat-attachments").download(row.storage_path);
    if (downloadError || !object) throw new HttpError(409, "Experiment chat image upload is incomplete");
    const bytes = new Uint8Array(await object.arrayBuffer());
    try {
      if (bytes.length !== Number(row.byte_size) || bytes.length < 1 || bytes.length > CHAT_IMAGE_MAX_BYTES) {
        throw new HttpError(400, "Experiment chat image size does not match the upload request");
      }
      let info: ReturnType<typeof inspectChatImage>;
      try { info = inspectChatImage(bytes); }
      catch { throw new HttpError(400, "The uploaded file is not a supported image"); }
      if (info.mimeType !== row.declared_mime_type || info.width < 1 || info.height < 1 || info.width > 32768 || info.height > 32768) {
        throw new HttpError(400, "Experiment chat image metadata is invalid");
      }
      const sha256 = await digestHex(bytes);
      const { data: updated, error: updateError } = await admin.from("experiment_chat_attachments")
        .update({ status: "ready", mime_type: info.mimeType, width: info.width, height: info.height, sha256, rejection_reason: null, updated_at: new Date().toISOString() })
        .eq("id", row.id).eq("status", "pending").select("*").single();
      if (updateError) throw updateError;
      finalized.push(updated as ChatAttachmentRow);
    } catch (validationError) {
      await admin.from("experiment_chat_attachments").update({
        status: "rejected",
        rejection_reason: validationError instanceof HttpError ? validationError.message : "invalid image",
        updated_at: new Date().toISOString(),
      }).eq("id", row.id);
      throw validationError;
    }
  }
  return finalized;
}

export async function validateContextAttachments(
  admin: SupabaseClient,
  experimentId: string,
  userId: string,
  ids: string[],
): Promise<ChatAttachmentRow[]> {
  if (!ids.length) return [];
  const { data, error } = await admin.from("experiment_chat_attachments").select("*")
    .in("id", ids).eq("experiment_id", experimentId).eq("user_id", userId)
    .eq("status", "bound").not("action_id", "is", null);
  if (error) throw error;
  if (!data || data.length !== ids.length) throw new HttpError(404, "Previous chat image is unavailable");
  return data as ChatAttachmentRow[];
}

export function publicAttachment(row: Pick<ChatAttachmentRow,
  "id" | "file_name" | "mime_type" | "declared_mime_type" | "byte_size" | "width" | "height" | "sha256" | "created_at"
>): Record<string, unknown> {
  return {
    id: row.id,
    name: row.file_name,
    mimeType: row.mime_type ?? row.declared_mime_type,
    byteSize: Number(row.byte_size),
    width: row.width,
    height: row.height,
    sha256: row.sha256,
    createdAt: row.created_at,
  };
}
