export type AiAgentModel = { provider: string; id: string; name: string; model?: string };
export type AiAgentSession = { id: string; state: string; created_at?: string };
export type AiAgentStatus = {
  available: boolean;
  reason?: string | null;
  default_model?: string | null;
  models: AiAgentModel[];
  fallbacks?: string[];
  pi_version?: string | null;
  isolation?: string;
  permissions?: { budget: string; source_files: string };
  sessions: AiAgentSession[];
};

const prefix = "/api/v1/ai-agent";
function sessionPath(id: string) {
  if (!/^[a-zA-Z0-9._-]{1,128}$/.test(id)) throw new Error("工作会话无效，请重新读取");
  return `${prefix}/sessions/${encodeURIComponent(id)}`;
}

async function request<T>(path: string, method = "GET", signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, {
    method, credentials: "same-origin", signal,
    ...(method !== "GET" ? { headers: { "Content-Type": "application/json" }, body: "{}" } : {}),
  });
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = body?.detail;
    throw new Error(typeof detail === "string" ? detail : typeof detail?.message === "string" ? detail.message
      : typeof body?.message === "string" ? body.message : `AI 暂时不可用（${response.status}）`);
  }
  return body as T;
}

export async function getAiAgentStatus(signal?: AbortSignal): Promise<AiAgentStatus> {
  const status = await request<AiAgentStatus>(`${prefix}/status`, "GET", signal);
  if (!status || typeof status.available !== "boolean" || !Array.isArray(status.models) || !Array.isArray(status.sessions)) {
    throw new Error("AI 状态响应不完整，请重新读取");
  }
  return status;
}

export async function createAiAgentSession(signal?: AbortSignal): Promise<AiAgentSession> {
  const session = await request<AiAgentSession>(`${prefix}/sessions`, "POST", signal);
  sessionPath(session?.id ?? "");
  if (typeof session.state !== "string") throw new Error("工作会话响应不完整，请重新读取");
  return session;
}

export async function stopAiAgentSession(id: string): Promise<void> {
  await request(sessionPath(id), "DELETE");
}

export function aiAgentTerminalUrl(id: string, location: Pick<Location, "origin"> = window.location): string {
  const origin = new URL(location.origin);
  if (origin.protocol !== "http:" && origin.protocol !== "https:") throw new Error("AI 连接地址无效");
  origin.protocol = origin.protocol === "https:" ? "wss:" : "ws:";
  origin.pathname = `${sessionPath(id)}/terminal`;
  return origin.toString();
}

export function aiAgentSessionActive(session: AiAgentSession): boolean {
  return !["stopped", "exited", "failed", "terminated", "closed"].includes(session.state.toLowerCase());
}

type ConnectionListeners = {
  output(data: string): void;
  state(state: string, reason?: string): void;
  connected(): void;
};

/** Tickets remain in memory and are sent only as the first WebSocket frame. */
export class AiAgentTerminalConnection {
  private socket: WebSocket | null = null;
  private controller = new AbortController();
  private disposed = false;
  private lastSequence = 0;
  private columns = 80;
  private rows = 24;
  private closedReason = "";
  private terminalState = "";
  private sessionId: string;
  private listeners: ConnectionListeners;
  constructor(sessionId: string, listeners: ConnectionListeners) { this.sessionId = sessionId; this.listeners = listeners; }

  get sequence() { return this.lastSequence; }

  async connect(): Promise<void> {
    if (this.disposed) return;
    this.socket?.close();
    this.socket = null;
    this.controller.abort();
    const controller = new AbortController();
    this.controller = controller;
    this.closedReason = "";
    this.terminalState = "";
    this.listeners.state("connecting");
    try {
      const result = await request<{ ticket: string }>(`${sessionPath(this.sessionId)}/attach-ticket`, "POST", controller.signal);
      if (this.disposed || controller !== this.controller || controller.signal.aborted) return;
      if (!result || typeof result.ticket !== "string" || !result.ticket) throw new Error("连接凭证未取得，请重试");
      const socket = new WebSocket(aiAgentTerminalUrl(this.sessionId));
      this.socket = socket;
      const current = () => !this.disposed && this.socket === socket;
      socket.onopen = () => {
        if (!current()) return socket.close();
        socket.send(JSON.stringify({ type: "attach", ticket: result.ticket, after: this.lastSequence }));
        this.resize(this.columns, this.rows);
        this.listeners.state("connected");
        this.listeners.connected();
      };
      socket.onmessage = event => {
        if (!current() || typeof event.data !== "string") return;
        try {
          if (event.data.length > 2 * 1024 * 1024) throw new Error("终端输出过大，请重新连接");
          const frame = JSON.parse(event.data);
          if (frame.type === "output" && Number.isSafeInteger(frame.seq) && frame.seq > this.lastSequence && typeof frame.data === "string") {
            this.lastSequence = frame.seq;
            this.listeners.output(frame.data);
          } else if (frame.type === "gap" && Number.isSafeInteger(frame.seq) && frame.seq > this.lastSequence) {
            this.lastSequence = frame.seq;
            this.listeners.state("connected", "较早的终端显示已清理，完整对话可用 /resume 回顾");
          } else if (frame.type === "state" && typeof frame.state === "string") {
            if (["stopped", "exited", "failed", "terminated"].includes(frame.state.toLowerCase())) this.terminalState = frame.state;
            this.listeners.state(frame.state, typeof frame.reason === "string" ? frame.reason : undefined);
          }
        } catch {
          this.closedReason = "终端响应无法读取，请重新连接";
          this.listeners.state("disconnected", this.closedReason);
          socket.close();
        }
      };
      socket.onerror = () => { if (current()) { this.closedReason = "连接中断，请重新连接"; this.listeners.state("disconnected", this.closedReason); } };
      socket.onclose = () => { if (current()) this.listeners.state(this.terminalState || "disconnected", this.closedReason || undefined); };
    } catch (error) {
      if (!this.disposed && controller === this.controller && !controller.signal.aborted) {
        this.listeners.state("disconnected", error instanceof Error ? error.message : "连接失败，请重试");
      }
    }
  }

  input(data: string): boolean {
    if (this.disposed || this.socket?.readyState !== WebSocket.OPEN) return false;
    // Large pasted text is split without breaking Unicode; the server caps each UTF-8 frame at 64 KiB.
    let chunk = "", bytes = 0;
    for (const character of data) {
      const point = character.codePointAt(0)!;
      const size = point < 0x80 ? 1 : point < 0x800 ? 2 : point < 0x10000 ? 3 : 4;
      if (bytes + size > 32768) { this.socket.send(JSON.stringify({ type: "input", data: chunk })); chunk = ""; bytes = 0; }
      chunk += character; bytes += size;
    }
    if (chunk) this.socket.send(JSON.stringify({ type: "input", data: chunk }));
    return true;
  }

  resize(cols: number, rows: number): void {
    if (!Number.isFinite(cols) || !Number.isFinite(rows)) return;
    this.columns = Math.max(20, Math.min(400, Math.round(cols)));
    this.rows = Math.max(5, Math.min(160, Math.round(rows)));
    if (!this.disposed && this.socket?.readyState === WebSocket.OPEN) {
      this.socket.send(JSON.stringify({ type: "resize", cols: this.columns, rows: this.rows }));
    }
  }

  dispose(): void {
    this.disposed = true;
    this.controller.abort();
    this.socket?.close();
    this.socket = null;
  }
}
