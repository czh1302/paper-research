import { CHAT_IMAGE_MAX_BYTES, CHAT_IMAGE_MAX_COUNT, CHAT_IMAGE_MAX_TOTAL_BYTES, CHAT_IMAGE_TYPES, safeChatImageName } from "../_shared/chat-image.ts";
import { getExperimentAccess, requireExperimentPilotEnabled, requirePermission } from "../_shared/experiments.ts";
import { authenticate, handleError, HttpError, json, preflight } from "../_shared/http.ts";

type FileInput = { name: string; size: number; type: string };

Deno.serve(async (request) => {
  const early = preflight(request); if (early) return early;
  try {
    requireExperimentPilotEnabled();
    const { user, admin } = await authenticate(request);
    const body = await request.json();
    const access = await getExperimentAccess(admin, user, String(body.experimentId ?? ""));
    if (access.adminMode) throw new HttpError(403, "Administrators cannot access private assistant images");
    requirePermission(access.permissions, "chat");
    const files = body.files as FileInput[];
    if (!Array.isArray(files) || files.length < 1 || files.length > CHAT_IMAGE_MAX_COUNT) {
      throw new HttpError(400, "Upload one to four images");
    }
    let total = 0;
    for (const file of files) {
      if (!file || typeof file.name !== "string" || !CHAT_IMAGE_TYPES.has(file.type)) throw new HttpError(400, "Only JPEG, PNG, WebP, and GIF images are accepted");
      if (!Number.isInteger(file.size) || file.size < 1 || file.size > CHAT_IMAGE_MAX_BYTES) throw new HttpError(400, "Each chat image must be no larger than 10 MB");
      total += file.size;
    }
    if (total > CHAT_IMAGE_MAX_TOTAL_BYTES) throw new HttpError(400, "Chat images must be no larger than 25 MB in total");

    const uploads = [];
    for (const file of files) {
      const id = crypto.randomUUID();
      const path = `${user.id}/${access.experiment.id}/${id}/${safeChatImageName(file.name)}`;
      const { error: insertError } = await admin.from("experiment_chat_attachments").insert({
        id,
        experiment_id: access.experiment.id,
        user_id: user.id,
        storage_path: path,
        file_name: file.name.slice(0, 180),
        declared_mime_type: file.type,
        byte_size: file.size,
      });
      if (insertError) throw insertError;
      const { data, error } = await admin.storage.from("experiment-chat-attachments").createSignedUploadUrl(path);
      if (error || !data?.signedUrl) {
        await admin.from("experiment_chat_attachments").delete().eq("id", id);
        throw error ?? new Error("Could not create a signed chat image upload");
      }
      uploads.push({ attachmentId: id, uploadUrl: data.signedUrl, expiresIn: 7200 });
    }
    return json(request, { uploads }, 201);
  } catch (error) { return handleError(request, error); }
});
