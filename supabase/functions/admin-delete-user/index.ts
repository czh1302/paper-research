import { authenticate, handleError, HttpError, json, preflight, requireAdministrator } from "../_shared/http.ts";

Deno.serve(async (request) => {
  const early = preflight(request);
  if (early) return early;
  try {
    const { user, admin } = await authenticate(request);
    await requireAdministrator(admin, user);
    const { userId, confirmationEmail } = await request.json();
    if (typeof userId !== "string" || !userId) throw new HttpError(400, "userId is required");
    if (typeof confirmationEmail !== "string" || !confirmationEmail.trim()) throw new HttpError(400, "confirmationEmail is required");
    const { data, error } = await admin.rpc("admin_request_user_deletion", {
      p_user_id: userId,
      p_confirmation_email: confirmationEmail.trim(),
      p_requester_id: user.id,
    });
    if (error?.message.includes("user not found")) throw new HttpError(404, "User not found");
    if (error?.message.includes("own account")) throw new HttpError(409, "You cannot delete your own administrator account");
    if (error?.message.includes("protected")) throw new HttpError(409, "Administrator accounts are protected");
    if (error?.message.includes("confirmation email")) throw new HttpError(400, "Confirmation email does not match");
    if (error) throw error;
    return json(request, { state: data === "deleted" ? "deleted" : "pending" });
  } catch (error) {
    return handleError(request, error);
  }
});
