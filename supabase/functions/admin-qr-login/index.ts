import { adminClient, handleError, HttpError, json, preflight, sha256 } from "../_shared/http.ts";

function adminRedirectUrl(): string {
  const configured = Deno.env.get("ADMIN_REDIRECT_URL");
  if (!configured) throw new Error("ADMIN_REDIRECT_URL is not configured");
  const url = new URL(configured);
  if (url.protocol !== "https:") throw new Error("ADMIN_REDIRECT_URL must use HTTPS");
  return url.toString();
}

Deno.serve(async (request) => {
  const early = preflight(request);
  if (early) return early;
  try {
    if (request.method !== "POST") throw new HttpError(405, "Method not allowed");
    const { token } = await request.json();
    if (typeof token !== "string" || !/^[A-Za-z0-9_-]{43}$/.test(token)) {
      throw new HttpError(401, "Invalid or expired administrator ticket");
    }

    const admin = adminClient();
    const { data: claimed, error: claimError } = await admin.rpc("redeem_admin_login_ticket", {
      p_token_hash: await sha256(token),
    });
    if (claimError) throw claimError;
    const email = claimed?.[0]?.email;
    if (!email) throw new HttpError(401, "Invalid or expired administrator ticket");

    const supabaseUrl = Deno.env.get("SUPABASE_URL");
    const serviceRoleKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
    if (!supabaseUrl || !serviceRoleKey) throw new Error("Supabase service configuration is missing");
    const linkResponse = await fetch(`${supabaseUrl}/auth/v1/admin/generate_link`, {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${serviceRoleKey}`,
        "apikey": serviceRoleKey,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        type: "magiclink",
        email,
        redirect_to: adminRedirectUrl(),
      }),
    });
    if (!linkResponse.ok) throw new Error(`Supabase Auth link generation failed (${linkResponse.status})`);
    const linkPayload = await linkResponse.json();
    const hashedToken = linkPayload.hashed_token ?? linkPayload.properties?.hashed_token;
    const verificationType = linkPayload.verification_type
      ?? linkPayload.properties?.verification_type
      ?? "magiclink";
    if (typeof hashedToken !== "string" || hashedToken.length < 32) {
      throw new Error("Supabase Auth returned an invalid verification token");
    }

    const verifyResponse = await fetch(`${supabaseUrl}/auth/v1/verify`, {
      method: "POST",
      headers: {
        "apikey": serviceRoleKey,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ token_hash: hashedToken, type: verificationType }),
    });
    if (!verifyResponse.ok) throw new Error(`Supabase Auth verification failed (${verifyResponse.status})`);
    const session = await verifyResponse.json();
    if (typeof session.access_token !== "string" || typeof session.refresh_token !== "string") {
      throw new Error("Supabase Auth did not return a session");
    }
    return json(request, {
      accessToken: session.access_token,
      refreshToken: session.refresh_token,
    });
  } catch (error) {
    return handleError(request, error);
  }
});
