import { createClient, SupabaseClient, User } from "npm:@supabase/supabase-js@2.112.4";

const configuredSiteUrl = Deno.env.get("PUBLIC_SITE_URL") ?? "";
let configuredOrigin = configuredSiteUrl;
try {
  configuredOrigin = new URL(configuredSiteUrl).origin;
} catch {
  // An empty or malformed value will fail closed for non-local origins.
}

export function corsHeaders(request: Request): Record<string, string> {
  const origin = request.headers.get("origin") ?? "";
  const allowed = origin === configuredOrigin || /^http:\/\/(localhost|127\.0\.0\.1):\d+$/.test(origin);
  return {
    "Access-Control-Allow-Origin": allowed ? origin : configuredOrigin,
    "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Vary": "Origin",
  };
}

export function json(request: Request, body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...corsHeaders(request), "Content-Type": "application/json; charset=utf-8" },
  });
}

export function preflight(request: Request): Response | null {
  return request.method === "OPTIONS" ? new Response("ok", { headers: corsHeaders(request) }) : null;
}

export function adminClient(): SupabaseClient {
  const url = Deno.env.get("SUPABASE_URL");
  const key = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
  if (!url || !key) throw new Error("Supabase service configuration is missing");
  return createClient(url, key, { auth: { persistSession: false, autoRefreshToken: false } });
}

export async function authenticate(request: Request): Promise<{ user: User; admin: SupabaseClient }> {
  const authorization = request.headers.get("Authorization");
  if (!authorization?.startsWith("Bearer ")) throw new HttpError(401, "Authentication required");
  const admin = adminClient();
  const { data, error } = await admin.auth.getUser(authorization.slice(7));
  if (error || !data.user) throw new HttpError(401, "Invalid session");
  return { user: data.user, admin };
}

export class HttpError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export function handleError(request: Request, error: unknown): Response {
  if (error instanceof HttpError) return json(request, { error: error.message }, error.status);
  console.error(error instanceof Error ? error.message : "Unknown function error");
  return json(request, { error: "Internal service error" }, 500);
}

export async function verifyTurnstile(token: string | undefined, remoteIp?: string | null): Promise<void> {
  const secret = Deno.env.get("TURNSTILE_SECRET_KEY");
  if (!secret) {
    if (Deno.env.get("ALLOW_INSECURE_LOCAL_DEV") === "true") return;
    throw new HttpError(503, "Turnstile is not configured");
  }
  if (!token) throw new HttpError(400, "Turnstile verification is required");
  const body = new URLSearchParams({ secret, response: token });
  if (remoteIp) body.set("remoteip", remoteIp);
  const response = await fetch("https://challenges.cloudflare.com/turnstile/v0/siteverify", {
    method: "POST",
    body,
  });
  const result = await response.json();
  if (!result.success) throw new HttpError(403, "Turnstile verification failed");
}

export async function sha256(value: string): Promise<string> {
  const bytes = new TextEncoder().encode(value);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

export function randomToken(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(32));
  return btoa(String.fromCharCode(...bytes)).replaceAll("+", "-").replaceAll("/", "_").replaceAll("=", "");
}
