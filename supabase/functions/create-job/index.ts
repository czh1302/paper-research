import { authenticate, handleError, HttpError, json, preflight, verifyTurnstile } from "../_shared/http.ts";

Deno.serve(async (request) => {
  const early = preflight(request);
  if (early) return early;
  try {
    const { user, admin } = await authenticate(request);
    const body = await request.json();
    const mode = body.mode as "single" | "multi";
    const uploadIds = body.uploadIds as string[];
    const maxRounds = Number(body.maxRounds);
    if (!Array.isArray(uploadIds) || !["single", "multi"].includes(mode) || !Number.isInteger(maxRounds)) {
      throw new HttpError(400, "Invalid analysis request");
    }
    const expected = mode === "single" ? uploadIds.length === 1 : uploadIds.length >= 2 && uploadIds.length <= 5;
    if (!expected || maxRounds < 1 || maxRounds > 5) throw new HttpError(400, "Invalid mode, PDF count, or rounds");
    await verifyTurnstile(body.turnstileToken, request.headers.get("CF-Connecting-IP"));

    const { data: uploads, error: uploadsError } = await admin
      .from("uploads")
      .select("id,storage_path,original_name,size_bytes,status")
      .eq("user_id", user.id)
      .in("id", uploadIds);
    if (uploadsError) throw uploadsError;
    if (!uploads || uploads.length !== uploadIds.length) throw new HttpError(400, "Upload records are missing");

    for (const upload of uploads) {
      const parts = upload.storage_path.split("/");
      const folder = parts.slice(0, -1).join("/");
      const fileName = parts.at(-1)!;
      const { data: objects, error: listError } = await admin.storage.from("papers").list(folder, { search: fileName });
      if (listError || !objects?.some((item) => item.name === fileName)) {
        throw new HttpError(400, `Upload is incomplete: ${upload.original_name}`);
      }
    }
    await admin.from("uploads").update({ status: "uploaded" }).in("id", uploadIds).eq("user_id", user.id);

    const { data, error } = await admin.rpc("reserve_job", {
      p_user_id: user.id,
      p_mode: mode,
      p_file_ids: uploadIds,
      p_max_rounds: maxRounds,
      p_languages: ["zh", "en"],
    });
    if (error) {
      const message = error.message.includes("insufficient") ? "Insufficient analysis units" : error.message;
      throw new HttpError(409, message);
    }
    return json(request, { job: data }, 201);
  } catch (error) {
    return handleError(request, error);
  }
});

