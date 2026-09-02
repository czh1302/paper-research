import { publicAttachment } from "../_shared/experiment-attachments.ts";
import { getExperimentAccess, isUuid, requireExperimentPilotEnabled } from "../_shared/experiments.ts";
import { authenticate, handleError, HttpError, json, preflight } from "../_shared/http.ts";

Deno.serve(async (request) => {
  const early = preflight(request); if (early) return early;
  try {
    requireExperimentPilotEnabled();
    const { user, admin } = await authenticate(request);
    const body = await request.json();
    const access = await getExperimentAccess(admin, user, String(body.experimentId ?? ""));
    if (access.adminMode) throw new HttpError(404, "Chat attachment not found");
    const attachmentId = String(body.attachmentId ?? "");
    if (!isUuid(attachmentId)) throw new HttpError(400, "Invalid chat attachment id");
    const { data, error } = await admin.from("experiment_chat_attachments").select("*")
      .eq("id", attachmentId).eq("experiment_id", access.experiment.id).eq("user_id", user.id)
      .in("status", ["ready", "bound"]).maybeSingle();
    if (error) throw error;
    if (!data) throw new HttpError(404, "Chat attachment not found");
    const { data: signed, error: signedError } = await admin.storage.from("experiment-chat-attachments")
      .createSignedUrl(data.storage_path, 300);
    if (signedError || !signed?.signedUrl) throw signedError ?? new Error("Could not sign chat attachment");
    return json(request, { attachment: publicAttachment(data), signedUrl: signed.signedUrl, expiresIn: 300 });
  } catch (error) { return handleError(request, error); }
});
