import { type CommandHandle, Sandbox } from "npm:e2b@2.46.1";
import { adminClient, json, sha256 } from "../_shared/http.ts";

type TerminalTicket = {
  experiment_id: string;
  sandbox_id: string;
  pty_session_id: string | null;
  ticket_mode: "read" | "write";
  terminal_session_epoch: number;
  checkpoint_key: string;
};

type TerminalContext = {
  sandbox: Sandbox;
  handle: CommandHandle;
  pid: number;
};

const encoder = new TextEncoder();
const workspace = "/home/user/repository";
const tmuxSession = "research-atlas";
const outboundHighWaterBytes = 1_048_576;
const inboundQueueMaxMessages = 32;
const inboundQueueMaxBytes = 1_048_576;
const inboundMessageMaxCharacters = 65_536;

function allowedOrigin(request: Request): boolean {
  const origin = request.headers.get("origin") ?? "";
  if (/^http:\/\/(localhost|127\.0\.0\.1):\d+$/.test(origin)) return true;
  const configured = Deno.env.get("PUBLIC_SITE_URL")?.trim();
  if (!configured) return false;
  try {
    return origin === new URL(configured).origin;
  } catch {
    return false;
  }
}

function positiveInteger(
  value: string | undefined,
  fallback: number,
  maximum: number,
): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0
    ? Math.min(Math.floor(parsed), maximum)
    : fallback;
}

function existingPty(value: string | null): number | null {
  if (!value || !/^\d+$/.test(value)) return null;
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : null;
}

function terminalError(
  socket: WebSocket,
  message: string,
  closeCode = 1011,
): void {
  if (socket.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify({ type: "error", message }));
    socket.close(closeCode, message.slice(0, 120));
  }
}

async function consumeTicket(request: Request): Promise<TerminalTicket | null> {
  const ticket = new URL(request.url).searchParams.get("ticket")?.trim();
  if (!ticket || ticket.length > 256) return null;
  const admin = adminClient();
  const ticketHash = await sha256(ticket);
  const { data, error } = await admin.rpc(
    "consume_experiment_terminal_ticket",
    {
      p_token_hash: ticketHash,
    },
  );
  if (error) throw error;
  const row = Array.isArray(data) ? data[0] : data;
  if (
    !row?.experiment_id || !row?.sandbox_id ||
    !Number.isSafeInteger(Number(row.terminal_session_epoch)) ||
    !["read", "write"].includes(row.ticket_mode)
  ) return null;
  return {
    experiment_id: String(row.experiment_id),
    sandbox_id: String(row.sandbox_id),
    pty_session_id: row.pty_session_id ? String(row.pty_session_id) : null,
    ticket_mode: row.ticket_mode as "read" | "write",
    terminal_session_epoch: Number(row.terminal_session_epoch),
    // The one-time ticket itself is never persisted. Its digest gives each
    // relay attachment an opaque, retry-safe checkpoint namespace.
    checkpoint_key: `terminal-${ticketHash.slice(0, 48)}`,
  };
}

async function terminalSessionAuthorized(
  ticket: TerminalTicket,
  requireWrite: boolean,
): Promise<boolean> {
  // Re-evaluate the operational kill switch for every input/resize. An
  // already-upgraded WebSocket must lose authority immediately when pilots
  // are disabled, not only when its original ticket was issued.
  if (Deno.env.get("E2B_PILOT_ENABLED")?.trim().toLowerCase() !== "true") {
    return false;
  }
  const admin = adminClient();
  const [runtimeResult, experimentResult, validationResult] = await Promise.all([
      admin.from("experiment_runtime")
        .select("state,terminal_session_epoch")
        .eq("experiment_id", ticket.experiment_id)
        .eq("sandbox_id", ticket.sandbox_id)
        .maybeSingle(),
      admin.from("idea_experiments")
        .select("status,cancellation_requested,deletion_requested_at")
        .eq("id", ticket.experiment_id)
        .maybeSingle(),
      admin.from("experiment_validation_runtime")
        .select("action_id")
        .eq("experiment_id", ticket.experiment_id)
        .in("state", ["creating", "running", "destroying"])
        .limit(1),
    ]);
  const actionResult = requireWrite
    ? await admin.from("experiment_actions")
      .select("id")
      .eq("experiment_id", ticket.experiment_id)
      .in("status", ["queued", "running", "recovering"])
      .limit(1)
    : { data: [] as Array<{ id: string }>, error: null };
  if (
    runtimeResult.error || experimentResult.error || validationResult.error ||
    actionResult.error
  ) return false;
  const runtime = runtimeResult.data;
  const experiment = experimentResult.data;
  return Boolean(
    runtime?.state === "running" &&
      Number(runtime.terminal_session_epoch) === ticket.terminal_session_epoch &&
      experiment?.status === "ready" &&
      !experiment.cancellation_requested &&
      !experiment.deletion_requested_at &&
      (validationResult.data?.length ?? 0) === 0 &&
      (!requireWrite || (actionResult.data?.length ?? 0) === 0),
  );
}

type CheckpointExperiment = {
  user_id: string;
  current_revision_id: string | null;
  status: string;
  cancellation_requested: boolean;
  deletion_requested_at: string | null;
};

/**
 * Queue a no-op command action whose only effect is the Worker's existing
 * `_archive_dirty_worktree(actor="terminal")` checkpoint. The PTY/tmux
 * process is deliberately left alone. All authority is re-resolved on the
 * server instead of being accepted from the browser or ticket payload.
 */
async function enqueueTerminalCheckpoint(
  ticket: TerminalTicket,
  sequence: number,
): Promise<boolean> {
  if (ticket.ticket_mode !== "write") return false;
  if (Deno.env.get("E2B_PILOT_ENABLED")?.trim().toLowerCase() !== "true") {
    return false;
  }
  const admin = adminClient();
  const idempotencyKey = `${ticket.checkpoint_key}-${sequence}`;

  // A current revision can change between the read and enqueue RPC when a
  // previous terminal checkpoint finishes. Retry once with the new revision;
  // the stable idempotency key also makes an ambiguous RPC retry harmless.
  for (let attempt = 0; attempt < 2; attempt += 1) {
    const [
      { data: experiment, error: experimentError },
      { data: runtime, error: runtimeError },
      { data: validationRuntimes, error: validationError },
      { data: activeActions, error: actionsError },
    ] = await Promise.all([
      admin.from("idea_experiments")
        .select(
          "user_id,current_revision_id,status,cancellation_requested,deletion_requested_at",
        )
        .eq("id", ticket.experiment_id)
        .maybeSingle(),
      admin.from("experiment_runtime")
        .select("sandbox_id,state,terminal_session_epoch")
        .eq("experiment_id", ticket.experiment_id)
        .eq("sandbox_id", ticket.sandbox_id)
        .maybeSingle(),
      admin.from("experiment_validation_runtime")
        .select("action_id")
        .eq("experiment_id", ticket.experiment_id)
        .in("state", ["creating", "running", "destroying"])
        .limit(1),
      admin.from("experiment_actions")
        .select("id")
        .eq("experiment_id", ticket.experiment_id)
        .in("status", ["queued", "running", "recovering"])
        .limit(1),
    ]);
    if (experimentError) throw experimentError;
    if (runtimeError) throw runtimeError;
    if (validationError) throw validationError;
    if (actionsError) throw actionsError;
    const current = experiment as CheckpointExperiment | null;
    if (
      !current || !current.user_id || !current.current_revision_id ||
      current.status !== "ready" || current.cancellation_requested ||
      current.deletion_requested_at ||
      !runtime || runtime.state !== "running" ||
      Number(runtime.terminal_session_epoch) !== ticket.terminal_session_epoch ||
      (validationRuntimes?.length ?? 0) > 0 ||
      (activeActions?.length ?? 0) > 0
    ) return false;

    const { error } = await admin.rpc("enqueue_experiment_action", {
      p_experiment_id: ticket.experiment_id,
      p_user_id: current.user_id,
      p_kind: "command",
      p_request: {
        command: "true",
        archiveOnly: true,
        source: "terminal",
      },
      p_base_revision_id: current.current_revision_id,
      p_idempotency_key: idempotencyKey,
    });
    if (!error) return true;
    if (
      !String(error.message ?? "").includes("revision conflict") || attempt > 0
    ) throw error;
  }
  return false;
}

async function connectTerminal(
  ticket: TerminalTicket,
  socket: WebSocket,
  signal: AbortSignal,
  onBackpressure: () => void,
): Promise<TerminalContext> {
  if (!(await terminalSessionAuthorized(ticket, ticket.ticket_mode === "write"))) {
    throw new Error("Terminal session was revoked");
  }
  const apiKey = Deno.env.get("E2B_API_KEY")?.trim();
  if (!apiKey) throw new Error("E2B terminal service is not configured");
  const sandboxTimeoutMs = positiveInteger(
    Deno.env.get("E2B_RUN_TIMEOUT_SECONDS"),
    3600,
    3600,
  ) * 1000;
  const streamTimeoutMs = positiveInteger(
    Deno.env.get("EXPERIMENT_TERMINAL_STREAM_SECONDS"),
    120,
    300,
  ) * 1000;
  const sandbox = await Sandbox.connect(ticket.sandbox_id, {
    apiKey,
    timeoutMs: sandboxTimeoutMs,
    signal,
  });
  const onData = (data: Uint8Array) => {
    if (socket.readyState !== WebSocket.OPEN) return;
    if (socket.bufferedAmount + data.byteLength > outboundHighWaterBytes) {
      onBackpressure();
      return;
    }
    try {
      socket.send(data);
    } catch {
      onBackpressure();
    }
  };

  let handle: CommandHandle | null = null;
  const previousPid = existingPty(ticket.pty_session_id);
  if (previousPid) {
    try {
      handle = await sandbox.pty.connect(previousPid, {
        onData,
        timeoutMs: streamTimeoutMs,
        requestTimeoutMs: 30_000,
        signal,
      });
    } catch {
      // The PTY can end while the tmux session remains alive. A new PTY below
      // reattaches to the same named tmux session without losing its process.
    }
  }

  let created = false;
  if (!handle) {
    handle = await sandbox.pty.create({
      cols: 120,
      rows: 36,
      cwd: workspace,
      onData,
      timeoutMs: streamTimeoutMs,
      requestTimeoutMs: 30_000,
      signal,
    });
    created = true;
  }

  const admin = adminClient();
  const now = new Date().toISOString();
  const { data: runtime, error: runtimeError } = await admin.from(
    "experiment_runtime",
  )
    .update({
      pty_session_id: String(handle.pid),
      state: "running",
      last_heartbeat_at: now,
      updated_at: now,
    })
    .eq("experiment_id", ticket.experiment_id)
    .eq("sandbox_id", ticket.sandbox_id)
    .eq("state", "running")
    .eq("terminal_session_epoch", ticket.terminal_session_epoch)
    .select("experiment_id")
    .maybeSingle();
  if (runtimeError || !runtime) {
    if (created) await handle.kill().catch(() => false);
    throw runtimeError ??
      new Error("Experiment runtime changed before terminal connection");
  }

  if (created) {
    const command =
      `exec tmux new-session -A -s ${tmuxSession} -c ${workspace}\r`;
    await sandbox.pty.sendInput(handle.pid, encoder.encode(command), {
      requestTimeoutMs: 30_000,
      signal,
    });
  }
  return { sandbox, handle, pid: handle.pid };
}

Deno.serve(async (request) => {
  if (Deno.env.get("E2B_PILOT_ENABLED")?.trim().toLowerCase() !== "true") {
    return json(request, { error: "Terminal service is disabled" }, 503);
  }
  if (request.headers.get("upgrade")?.toLowerCase() !== "websocket") {
    return json(request, { error: "WebSocket upgrade required" }, 426);
  }
  if (!allowedOrigin(request)) {
    return json(request, { error: "Origin not allowed" }, 403);
  }

  let ticket: TerminalTicket | null;
  try {
    ticket = await consumeTicket(request);
  } catch {
    return json(
      request,
      { error: "Terminal ticket could not be verified" },
      503,
    );
  }
  if (!ticket) {
    return json(
      request,
      { error: "Terminal ticket is invalid or expired" },
      401,
    );
  }

  const { socket, response } = Deno.upgradeWebSocket(request);
  const controller = new AbortController();
  let context: TerminalContext | null = null;
  let terminalReady: Promise<TerminalContext> | null = null;
  let operations = Promise.resolve();
  let reconnectTimer: ReturnType<typeof setTimeout> | undefined;
  let authorizationTimer: ReturnType<typeof setInterval> | undefined;
  let authorizationCheckRunning = false;
  let disconnectPromise: Promise<void> | null = null;
  let checkpointSequence = 0;
  let acceptingMessages = true;
  let queuedMessages = 0;
  let queuedBytes = 0;
  let resizeScheduled = false;
  let pendingResize: { cols: number; rows: number } | null = null;

  const queueCheckpoint = async () => {
    if (ticket.ticket_mode !== "write") return;
    checkpointSequence += 1;
    // Checkpoint failure must not expose database/E2B details to the browser
    // or prevent the durable tmux session from being reattached. A later
    // relay disconnect will enqueue another idempotent checkpoint.
    await enqueueTerminalCheckpoint(ticket, checkpointSequence).catch(() =>
      false
    );
  };

  const disconnect = () => {
    if (disconnectPromise) return disconnectPromise;
    disconnectPromise = (async () => {
      if (reconnectTimer !== undefined) clearTimeout(reconnectTimer);
      if (authorizationTimer !== undefined) clearInterval(authorizationTimer);
      // Queue before detaching so a normal close, error, and the scheduled
      // Edge reconnect all use the same durable revision path.
      await queueCheckpoint();
      controller.abort();
      const current = context;
      context = null;
      if (current) await current.handle.disconnect().catch(() => undefined);
    })();
    return disconnectPromise;
  };

  const closeForBackpressure = () => {
    if (!acceptingMessages) return;
    acceptingMessages = false;
    controller.abort();
    if (socket.readyState === WebSocket.OPEN) {
      socket.close(1013, "Terminal flow control limit reached");
    }
  };

  const enqueueOperation = (
    byteSize: number,
    operation: () => Promise<void>,
    onSettled?: () => void,
  ): boolean => {
    if (
      !acceptingMessages || queuedMessages >= inboundQueueMaxMessages ||
      queuedBytes + byteSize > inboundQueueMaxBytes
    ) {
      closeForBackpressure();
      return false;
    }
    queuedMessages += 1;
    queuedBytes += byteSize;
    operations = operations.then(operation).catch(() => {
      terminalError(socket, "Terminal connection was interrupted");
    }).finally(() => {
      queuedMessages = Math.max(0, queuedMessages - 1);
      queuedBytes = Math.max(0, queuedBytes - byteSize);
      onSettled?.();
    });
    return true;
  };

  const scheduleResize = () => {
    if (resizeScheduled || !pendingResize || !acceptingMessages) return;
    resizeScheduled = true;
    if (!enqueueOperation(64, async () => {
      const resize = pendingResize;
      pendingResize = null;
      if (!resize || !terminalReady) return;
      const current = context ?? await terminalReady;
      if (!(await terminalSessionAuthorized(ticket, false))) {
        terminalError(socket, "Terminal session was revoked", 1008);
        return;
      }
      await current.sandbox.pty.resize(current.pid, resize, {
        requestTimeoutMs: 15_000,
        signal: controller.signal,
      });
    }, () => {
      resizeScheduled = false;
      if (pendingResize) scheduleResize();
    })) {
      resizeScheduled = false;
    }
  };

  socket.onopen = () => {
    terminalReady = connectTerminal(
      ticket,
      socket,
      controller.signal,
      closeForBackpressure,
    );
    void terminalReady.then((value) => {
      context = value;
      // Ticket epochs can be advanced by another browser attachment, an
      // action claim, cancellation or lifecycle cleanup without this socket
      // receiving input. Poll the server-side fence so an idle/read-only
      // attachment cannot keep observing output after its authority changes.
      authorizationTimer = setInterval(() => {
        if (authorizationCheckRunning || socket.readyState !== WebSocket.OPEN) {
          return;
        }
        authorizationCheckRunning = true;
        void terminalSessionAuthorized(
          ticket,
          ticket.ticket_mode === "write",
        ).then((authorized) => {
          if (!authorized && socket.readyState === WebSocket.OPEN) {
            void disconnect().finally(() =>
              socket.close(1008, "Terminal session was revoked")
            );
          }
        }).catch(() => {
          if (socket.readyState === WebSocket.OPEN) {
            void disconnect().finally(() =>
              socket.close(1012, "Reconnect terminal")
            );
          }
        }).finally(() => {
          authorizationCheckRunning = false;
        });
      }, 2_500);
      // Supabase Edge Functions have bounded wall-clock lifetimes. Closing
      // normally prompts the SPA to obtain a fresh one-time ticket and attach
      // to this same PTY/tmux session.
      reconnectTimer = setTimeout(() => {
        if (socket.readyState === WebSocket.OPEN) {
          void disconnect().finally(() =>
            socket.close(1012, "Reconnect terminal")
          );
        }
      }, 110_000);
    }).catch(() =>
      terminalError(socket, "Terminal is temporarily unavailable")
    );
  };

  socket.onmessage = (event) => {
    if (
      !acceptingMessages || typeof event.data !== "string" ||
      event.data.length > inboundMessageMaxCharacters
    ) {
      terminalError(socket, "Invalid terminal message", 1008);
      return;
    }
    const byteSize = encoder.encode(event.data).byteLength;
    if (byteSize > inboundQueueMaxBytes) {
      closeForBackpressure();
      return;
    }
    let message: {
      type?: unknown;
      data?: unknown;
      cols?: unknown;
      rows?: unknown;
    };
    try {
      message = JSON.parse(event.data);
    } catch {
      terminalError(socket, "Invalid terminal message", 1008);
      return;
    }
    if (message.type === "resize") {
      const cols = Math.max(
        2,
        Math.min(500, Math.floor(Number(message.cols))),
      );
      const rows = Math.max(
        2,
        Math.min(300, Math.floor(Number(message.rows))),
      );
      if (!Number.isFinite(cols) || !Number.isFinite(rows)) return;
      pendingResize = { cols, rows };
      scheduleResize();
      return;
    }
    if (message.type !== "input") return;
    if (ticket.ticket_mode !== "write") {
      terminalError(socket, "Terminal is read-only", 1008);
      return;
    }
    if (
      typeof message.data !== "string" ||
      message.data.length > inboundMessageMaxCharacters
    ) {
      terminalError(socket, "Invalid terminal input", 1008);
      return;
    }
    enqueueOperation(byteSize, async () => {
      if (!terminalReady) return;
      const current = context ?? await terminalReady;
      if (!(await terminalSessionAuthorized(ticket, true))) {
        terminalError(socket, "Terminal session was revoked", 1008);
        return;
      }
      await current.sandbox.pty.sendInput(
        current.pid,
        encoder.encode(message.data as string),
        {
          requestTimeoutMs: 15_000,
          signal: controller.signal,
        },
      );
    });
  };
  socket.onclose = () => {
    void disconnect();
  };
  socket.onerror = () => {
    void disconnect();
  };
  return response;
});
