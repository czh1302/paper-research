import { handleError, HttpError, json, preflight, authenticate, requireActiveAccount } from "../_shared/http.ts";

type FileInput = { name: string; size: number; type: string };

function safeName(name: string): string {
  return name.replace(/[^A-Za-z0-9._-]+/g, "_").replace(/^\.+/, "").slice(0, 160) || "paper.pdf";
}

Deno.serve(async (request) => {
  const early = preflight(request);
  if (early) return early;
  try {
    const { user, admin } = await authenticate(request);
    await requireActiveAccount(admin, user);
    const body = await request.json();
    const files = body.files as FileInput[];
    if (!Array.isArray(files) || files.length < 1 || files.length > 5) {
      throw new HttpError(400, "Upload one to five PDF files");
    }

    const output = [];
    for (const file of files) {
      if (!file?.name?.toLowerCase().endsWith(".pdf") || file.type !== "application/pdf") {
        throw new HttpError(400, "Only application/pdf files are accepted");
      }
      if (!Number.isInteger(file.size) || file.size < 1 || file.size > 50 * 1024 * 1024) {
        throw new HttpError(400, "Each PDF must be no larger than 50 MB");
      }
      const uploadId = crypto.randomUUID();
      const path = `${user.id}/${uploadId}/${safeName(file.name)}`;
      const { error: insertError } = await admin.from("uploads").insert({
        id: uploadId,
        user_id: user.id,
        storage_path: path,
        original_name: file.name,
        size_bytes: file.size,
        mime_type: file.type,
      });
      if (insertError) throw insertError;
      const { data, error } = await admin.storage.from("papers").createSignedUploadUrl(path);
      if (error || !data) {
        await admin.from("uploads").delete().eq("id", uploadId);
        throw error ?? new Error("Could not create a signed upload URL");
      }
      output.push({ uploadId, path, token: data.token });
    }
    return json(request, { uploads: output }, 201);
  } catch (error) {
    return handleError(request, error);
  }
});
