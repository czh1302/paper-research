import { FitAddon } from "@xterm/addon-fit";
import { Terminal } from "@xterm/xterm";
import "@xterm/xterm/css/xterm.css";
import { useEffect, useRef, useState } from "react";
import { createTerminalTicket } from "../lib/api";
import { useLanguage } from "../lib/language";
import { useTheme } from "../lib/theme";

function terminalPayload(value: unknown): string {
  if (typeof value !== "string") return "";
  try {
    const parsed = JSON.parse(value) as { type?: string; data?: unknown; message?: unknown };
    if ((parsed.type === "output" || parsed.type === "terminal.output") && typeof parsed.data === "string") return parsed.data;
    if (parsed.type === "error" && typeof parsed.message === "string") return `\r\n${parsed.message}\r\n`;
  } catch { /* Raw PTY output. */ }
  return value;
}

function terminalTheme(theme: "light" | "dark") {
  return theme === "dark"
    ? { background: "#152131", foreground: "#ecf3f7", cursor: "#49c5b3", selectionBackground: "#2b5362" }
    : { background: "#f6f8fb", foreground: "#142235", cursor: "#0f8f80", selectionBackground: "#c8eae5" };
}

export function ExperimentTerminal({ experimentId, canRead, canWrite, active, ready }: { experimentId: string; canRead: boolean; canWrite: boolean; active: boolean; ready: boolean }) {
  const { text } = useLanguage();
  const { theme } = useTheme();
  const hostRef = useRef<HTMLDivElement | null>(null);
  const terminalRef = useRef<Terminal | null>(null);
  const themeRef = useRef(theme);
  const [connectionState, setConnectionState] = useState<"connecting" | "connected" | "reconnecting" | "unavailable">("connecting");
  themeRef.current = theme;

  useEffect(() => {
    if (terminalRef.current) terminalRef.current.options.theme = terminalTheme(theme);
  }, [theme]);

  useEffect(() => {
    if (!active || !canRead || !ready || !hostRef.current) return;
    const host = hostRef.current;
    const terminal = new Terminal({
      allowProposedApi: false,
      convertEol: true,
      cursorBlink: canWrite,
      cursorStyle: "bar",
      disableStdin: !canWrite,
      fontFamily: '"SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace',
      fontSize: 13,
      lineHeight: 1.35,
      scrollback: 5000,
      theme: terminalTheme(themeRef.current),
    });
    terminalRef.current = terminal;
    const fitAddon = new FitAddon();
    terminal.loadAddon(fitAddon);
    terminal.open(host);
    fitAddon.fit();
    terminal.writeln(text("正在连接 E2B 沙箱终端…", "Connecting to the E2B sandbox terminal…"));
    let disposed = false;
    let socket: WebSocket | null = null;
    let reconnectTimer: number | undefined;
    let reconnectAttempt = 0;

    const send = (payload: Record<string, unknown>) => {
      if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify(payload));
    };
    const connect = async () => {
      setConnectionState(reconnectAttempt ? "reconnecting" : "connecting");
      try {
        const ticket = await createTerminalTicket(experimentId, terminal.cols, terminal.rows, canWrite);
        if (disposed) return;
        socket = new WebSocket(ticket.websocketUrl);
        socket.binaryType = "arraybuffer";
        socket.onopen = () => {
          reconnectAttempt = 0;
          setConnectionState("connected");
          send({ type: "resize", cols: terminal.cols, rows: terminal.rows });
        };
        socket.onmessage = (event) => {
          if (typeof event.data === "string") terminal.write(terminalPayload(event.data));
          else if (event.data instanceof ArrayBuffer) terminal.write(new Uint8Array(event.data));
          else if (event.data instanceof Blob) void event.data.arrayBuffer().then((buffer) => !disposed && terminal.write(new Uint8Array(buffer)));
        };
        socket.onclose = () => {
          if (disposed) return;
          reconnectAttempt += 1;
          setConnectionState("reconnecting");
          reconnectTimer = window.setTimeout(connect, Math.min(1000 * 2 ** Math.min(reconnectAttempt, 4), 10_000));
        };
        socket.onerror = () => socket?.close();
      } catch {
        if (disposed) return;
        reconnectAttempt += 1;
        setConnectionState(reconnectAttempt > 4 ? "unavailable" : "reconnecting");
        reconnectTimer = window.setTimeout(connect, Math.min(1000 * 2 ** Math.min(reconnectAttempt, 4), 10_000));
      }
    };
    const inputDisposable = canWrite ? terminal.onData((data) => send({ type: "input", data })) : undefined;
    const resizeDisposable = terminal.onResize(({ cols, rows }) => send({ type: "resize", cols, rows }));
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(() => {
      try { fitAddon.fit(); } catch { /* The panel may be temporarily hidden. */ }
    });
    observer?.observe(host);
    void connect();
    return () => {
      disposed = true;
      if (reconnectTimer) window.clearTimeout(reconnectTimer);
      observer?.disconnect();
      inputDisposable?.dispose();
      resizeDisposable.dispose();
      socket?.close();
      terminal.dispose();
      if (terminalRef.current === terminal) terminalRef.current = null;
    };
  }, [active, canRead, canWrite, experimentId, ready, text]);

  if (!canRead) return <div className="experiment-empty">{text("当前账户没有终端查看权限。", "This account cannot view the terminal.")}</div>;
  if (!ready) return <div className="experiment-empty"><strong>{text("沙箱尚未就绪", "Sandbox is not ready")}</strong><span>{text("代码会先生成并显示；终端将在 E2B 沙箱创建或恢复后自动开放。", "Code is generated and shown first. The terminal opens after the E2B sandbox is created or restored.")}</span></div>;
  return <div className="experiment-terminal-shell">
    <div className="experiment-terminal-state" data-state={connectionState} aria-live="polite">
      {connectionState === "connected" ? text("终端已连接", "Terminal connected") : connectionState === "unavailable" ? text("终端正在自动恢复", "Terminal is recovering") : text("正在连接终端…", "Connecting terminal…")}
      {!canWrite && <span>{text(" · 只读", " · read only")}</span>}
    </div>
    <div className="experiment-terminal" ref={hostRef}/>
  </div>;
}
