import { matchesBoundedSchema, utf8Size } from "../_shared/bounded-json-schema.ts";
import { adminClient, handleError, HttpError, json, sha256 } from "../_shared/http.ts";

const MAX_TRANSPORT_BYTES = 65_536;

async function readBoundedBody(request: Request): Promise<string> {
  if (!request.body) return "";
  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      total += value.byteLength;
      if (total > MAX_TRANSPORT_BYTES) {
        await reader.cancel("body limit exceeded");
        throw new HttpError(413, "Inference request is too large");
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }
  const bytes = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
}

function tokenHeader(request: Request, name: string): string {
  const value = request.headers.get(name)?.trim() ?? "";
  if (!/^[A-Za-z0-9_-]{32,160}$/.test(value)) {
    throw new HttpError(401, "Invalid or expired inference credential");
  }
  return value;
}

function requestIdHeader(request: Request): string {
  const value = request.headers.get("x-research-atlas-request-id")?.trim() ?? "";
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value)) {
    throw new HttpError(401, "Invalid or expired inference credential");
  }
  return value;
}

function boundedInteger(value: unknown, fallback: number, maximum: number): number {
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed > 0 ? Math.min(parsed, maximum) : fallback;
}

async function submit(request: Request): Promise<Response> {
  const declared = Number(request.headers.get("content-length") ?? "0");
  if (declared > MAX_TRANSPORT_BYTES) throw new HttpError(413, "Inference request is too large");
  let raw: string;
  try {
    raw = await readBoundedBody(request);
  } catch (error) {
    if (error instanceof HttpError) throw error;
    throw new HttpError(400, "Inference request must use valid UTF-8 JSON");
  }
  if (utf8Size(raw) > MAX_TRANSPORT_BYTES) throw new HttpError(413, "Inference request is too large");
  let body: Record<string, unknown>;
  try {
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error();
    body = parsed as Record<string, unknown>;
  } catch {
    throw new HttpError(400, "Inference request must be a JSON object");
  }
  if (!Object.hasOwn(body, "input") || Object.keys(body).some((key) => key !== "input")) {
    throw new HttpError(400, "Inference request violates the frozen contract");
  }

  const oneShotToken = tokenHeader(request, "x-research-atlas-inference-token");
  const requestId = requestIdHeader(request);
  const pollToken = tokenHeader(request, "x-research-atlas-poll-token");
  const oneShotHash = await sha256(oneShotToken);
  const admin = adminClient();
  const { data: inspection, error: inspectionError } = await admin.rpc(
    "inspect_sandbox_inference_token",
    { p_token_hash: oneShotHash },
  );
  if (inspectionError || !inspection || typeof inspection !== "object") {
    throw new HttpError(401, "Invalid or expired inference credential");
  }
  const contract = (inspection as Record<string, unknown>).contract;
  if (!contract || typeof contract !== "object" || Array.isArray(contract)) {
    throw new HttpError(401, "Invalid or expired inference credential");
  }
  const contractObject = contract as Record<string, unknown>;
  const maxRequestBytes = boundedInteger(contractObject.max_request_bytes, 16_384, 32_768);
  const serializedInput = JSON.stringify(body.input);
  if (utf8Size(serializedInput) > maxRequestBytes) {
    throw new HttpError(413, "Inference request is too large");
  }
  const schema = contractObject.request_json_schema;
  if (!schema || typeof schema !== "object" || Array.isArray(schema)
    || !matchesBoundedSchema(body.input, schema as Record<string, unknown>)) {
    throw new HttpError(400, "Inference request violates the frozen contract");
  }

  const { data, error } = await admin.rpc("consume_sandbox_inference_token", {
    p_token_hash: oneShotHash,
    p_request_id: requestId,
    p_request: body.input,
    p_request_sha256: await sha256(serializedInput),
    p_poll_token_hash: await sha256(pollToken),
  });
  if (error || !data || typeof data !== "object") {
    throw new HttpError(401, "Invalid or expired inference credential");
  }
  const result = data as Record<string, unknown>;
  return json(request, {
    requestId: result.request_id,
    state: "queued",
    pollAfterMs: 500,
    expiresAt: result.expires_at,
  }, 202);
}

async function poll(request: Request): Promise<Response> {
  const requestId = new URL(request.url).searchParams.get("requestId") ?? "";
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(requestId)) {
    throw new HttpError(401, "Invalid or expired inference credential");
  }
  const pollToken = tokenHeader(request, "x-research-atlas-poll-token");
  const admin = adminClient();
  const { data, error } = await admin.rpc("poll_sandbox_inference_request", {
    p_request_id: requestId,
    p_poll_token_hash: await sha256(pollToken),
  });
  if (error || !data || typeof data !== "object") {
    throw new HttpError(401, "Invalid or expired inference credential");
  }
  const result = data as Record<string, unknown>;
  return json(request, {
    requestId: result.request_id,
    state: result.state,
    result: result.result ?? undefined,
    error: result.error ?? undefined,
    pollAfterMs: ["queued", "running", "recovering"].includes(String(result.state)) ? 500 : undefined,
    expiresAt: result.expires_at,
  });
}

Deno.serve(async (request) => {
  try {
    if (Deno.env.get("E2B_PILOT_ENABLED")?.trim().toLowerCase() !== "true") {
      throw new HttpError(503, "Experiment inference is temporarily unavailable");
    }
    if (request.method === "POST") return await submit(request);
    if (request.method === "GET") return await poll(request);
    return new Response(null, { status: 405, headers: { Allow: "GET, POST" } });
  } catch (error) {
    return handleError(request, error);
  }
});
