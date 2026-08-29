import { authenticate, handleError, HttpError, json, preflight } from "../_shared/http.ts";

Deno.serve(async (request) => {
  const early = preflight(request);
  if (early) return early;
  try {
    const { user, admin } = await authenticate(request);
    const { shareId } = await request.json();
    if (!shareId) throw new HttpError(400, "shareId is required");
    const { data, error } = await admin
      .from("share_tokens")
      .update({ revoked_at: new Date().toISOString() })
      .eq("id", shareId)
      .eq("user_id", user.id)
      .select("id")
      .maybeSingle();
    if (error) throw error;
    if (!data) throw new HttpError(404, "Share not found");
    return json(request, { revoked: true });
  } catch (error) {
    return handleError(request, error);
  }
});

