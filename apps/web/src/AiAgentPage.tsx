import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import type { Terminal } from "@xterm/xterm";
import { FY27Header } from "./FY27Header";
import { aiBudgetContextPrompt, readAiBudgetContext, type AiBudgetContext } from "./aiBudgetContext";
import {
  AiAgentTerminalConnection, aiAgentSessionActive, createAiAgentSession, getAiAgentStatus, stopAiAgentSession,
  type AiAgentSession, type AiAgentStatus,
} from "./aiAgentApi";
import "@xterm/xterm/css/xterm.css";
import "./aiAgent.css";

const terminalStates: Record<string, string> = {
  connecting: "正在连接", connected: "已连接", running: "已连接", ready: "已连接", starting: "正在准备",
  disconnected: "已断开", stopped: "已停止", exited: "已结束", failed: "运行失败", idle: "等待输入",
};
export function aiAgentStateLabel(state: string): string { return terminalStates[state.toLowerCase()] ?? "状态更新中"; }

export function aiAgentModelLabel(status: AiAgentStatus | null): string {
  if (!status?.default_model) return "模型待连接";
  const model = status.models.find(item => `${item.provider}/${item.id}` === status.default_model || item.id === status.default_model);
  return model?.name || status.default_model;
}

function sessionLabel(session: AiAgentSession): string {
  const created = session.created_at ? new Date(session.created_at) : null;
  return created && Number.isFinite(created.getTime())
    ? created.toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" })
    : `工作会话 ${session.id.slice(-6)}`;
}

export const aiAgentBudgetTasks = [
  { id: "budget", category: "查看预算", question: "结转收入和招生人次主要分布在哪里？", prompt: "请先读取已保存的预算，列明财年、版本、金额单位和数据时间。按季度、赛道和校区分析结转收入与招生人次，指出主要分布和差异。不要修改预算。" },
  { id: "forecast", category: "达成预测", question: "预计缺口主要来自哪些业务？", prompt: "请先读取最新已保存的达成预测及其预算基准，列明财年、版本、金额单位和数据时间。按季度和赛道解释预计达成率与缺口，区分实际和预测。不要刷新或确认预测。" },
  { id: "next-year", category: "下一年预算", question: "离经营目标还差多少，先调整什么？", prompt: "请先读取最新已保存的下一年预算及其预测基准，列明财年、版本、金额单位和数据时间。核对经营目标是否已手工设置；未设置时不要自行假定目标。若已设置，分析缺口及招生人次、续班率、学费的调整方向，只给建议，不保存或调平。具体调整仍在下一年预算原有流程完成。" },
] as const;

/** A task prepares text only. The sole send path remains the user's Pi input. */
export function aiAgentPreparedPrompt(taskId: string, context?: AiBudgetContext | null): string {
  const prompt = aiAgentBudgetTasks.find(task => task.id === taskId)?.prompt ?? "";
  if (!prompt || context === undefined) return prompt;
  return `${prompt.replaceAll("最新已保存", "已保存")}\n\n${aiBudgetContextPrompt(context)}`;
}

export function AiAgentWorkspace({ status, loading, busy, error, notice, sessionId, sessionEnded = false, selectedSession, onSelectSession, onStart, onContinue, onStop, onRetry, preparedTaskId = "", onPrepareTask, onCopyPrepared, copyNotice = "", terminal, sourceContext = null }: {
  status: AiAgentStatus | null;
  loading: boolean;
  busy: boolean;
  error: string;
  notice: string;
  sessionId: string | null;
  sessionEnded?: boolean;
  selectedSession: string;
  onSelectSession(id: string): void;
  onStart(): void;
  onContinue(): void;
  onStop(): void;
  onRetry(): void;
  preparedTaskId?: string;
  onPrepareTask?(id: string): void;
  onCopyPrepared?(): void;
  copyNotice?: string;
  terminal?: ReactNode;
  sourceContext?: AiBudgetContext | null;
}) {
  const sessions = (status?.sessions ?? []).filter(aiAgentSessionActive);
  const preparedPrompt = aiAgentPreparedPrompt(preparedTaskId, sourceContext);
  return <main className="decision-workspace ai-agent-workspace" aria-busy={loading || busy}>
    <header className="ai-agent-heading">
      <h1 className="budget-page-title">AI智解</h1>
      <div className="ai-agent-actions">
        {sessionId && !sessionEnded ? <button onClick={onStop} disabled={busy}>停止会话</button>
          : <button className="ai-agent-primary" onClick={onStart} disabled={!status?.available || busy || loading}>{busy ? "正在准备…" : sessionEnded ? "开始新对话" : "开始对话"}</button>}
      </div>
    </header>
    {(error || (!loading && status && !status.available)) && <div className="ai-agent-notice ai-agent-notice--error" role="alert">
      <div><strong>AI 暂时不可用</strong><p>{error || status?.reason || "运行环境尚未准备好"}</p></div>
      <button onClick={onRetry} disabled={busy || loading}>重新读取</button>
    </div>}
    {notice && <p className="ai-agent-inline-notice" role="status">{notice}</p>}
    <section className="ai-agent-paper budget-surface" aria-label="AI智解工作区">
      <div className="ai-agent-paper-meta">
        <span><i aria-hidden="true" />{aiAgentModelLabel(status)}</span>
        <div><span>预算只读</span><span>独立工作区</span></div>
      </div>
      <section className="ai-agent-source-context" aria-labelledby="ai-agent-source-title">
        <div><h2 className="budget-subsection-title" id="ai-agent-source-title">当前阅读上下文</h2>{sourceContext && <a href={sourceContext.sourceHref}>返回来源页面</a>}</div>
        {sourceContext ? <dl><div><dt>来源</dt><dd>{sourceContext.moduleLabel} · FY{String(sourceContext.fiscalYear).slice(-2)}</dd></div><div><dt>版本</dt><dd>{sourceContext.versionLabel}</dd></div><div><dt>范围</dt><dd>{sourceContext.scopeLabel}</dd></div></dl>
          : <p>未绑定来源页面。分析前先读取并核对财年、精确版本和范围。</p>}
        <p>此处仅准备问题；复制并在 Pi 中发送后才会分析。未保存改动不会传入，链接中的版本和范围仍须通过授权只读接口核对，返回来源页面后也请核对版本。</p>
      </section>
      <details className="ai-agent-task-entry" open={!sessionId}>
        <summary>{sessionId ? "预算问题示例" : loading ? "正在读取 AI 状态…" : "有什么预算问题？"}</summary>
        <div className="ai-agent-task-grid">
          {aiAgentBudgetTasks.map(task => <button key={task.id} className={preparedTaskId === task.id ? "is-selected" : ""}
            aria-pressed={preparedTaskId === task.id} onClick={() => onPrepareTask?.(task.id)}>
            <span>{task.category}</span><strong>{task.question}</strong>
          </button>)}
        </div>
        {preparedPrompt && <div className="ai-agent-prepared" role="region" aria-label="准备的问题">
          <p>{preparedPrompt}</p>
          <div><span>仅准备问题，发送在 Pi 中完成</span><button onClick={onCopyPrepared}>复制问题</button></div>
          {copyNotice && <p className="ai-agent-copy-notice" role="status">{copyNotice}</p>}
        </div>}
        <p className="ai-agent-context-boundary">先读取已保存版本；其他页面未保存的改动不在上下文中。</p>
      </details>
      {sessionId ? terminal : <p className="ai-agent-start-hint">点击上方“开始对话”，进入 Pi 后提问。</p>}
    </section>
    <section className="ai-agent-recall" aria-label="会话回顾">
      <h2 className="budget-section-title">会话回顾</h2>
      {!sessionId && sessions.length > 0 && <div className="ai-agent-recall-actions">
        <label htmlFor="ai-agent-session">工作会话</label><select id="ai-agent-session" aria-label="选择工作会话" value={selectedSession} onChange={event => onSelectSession(event.target.value)} disabled={busy || loading}>
          {sessions.map(session => <option key={session.id} value={session.id}>{sessionLabel(session)}</option>)}
        </select>
        <button onClick={onContinue} disabled={!status?.available || !selectedSession || busy || loading}>继续对话</button>
      </div>}
      <p>{sessionId && !sessionEnded ? "当前对话保留在 Pi 中。输入 " : "开始或继续对话后，输入 "}<code>/resume</code> 回顾历史对话。</p>
    </section>
    <details className="ai-agent-help">
      <summary>使用帮助</summary>
      <dl>
        <div><dt>模型与设置</dt><dd><code>/model</code> 切换模型；<code>/settings</code> 调整设置。</dd></div>
        <div><dt>会话与记录</dt><dd><code>/new</code> 新对话；<code>/resume</code> 历史会话；<code>/tree</code> 查看分支；<code>/fork</code> 创建分支。</dd></div>
        <div><dt>工具与扩展</dt><dd><code>/compact</code> 压缩上下文；<code>/hotkeys</code> 查看快捷键。工具、技能、扩展在独立工作区运行，额外联网被隔离。</dd></div>
        <div><dt>输入与文件</dt><dd>直接输入并按 Enter 发送；Shift+Enter 换行。外部编辑器、内联图片和部分系统快捷键受浏览器限制。</dd></div>
        <div><dt>切换模块</dt><dd>离开页面不会停止会话。停止后，仍可在新会话里用 <code>/resume</code> 回顾记录。</dd></div>
      </dl>
    </details>
  </main>;
}

function AiAgentTerminal({ sessionId, onState }: { sessionId: string; onState(state: string): void }) {
  const mount = useRef<HTMLDivElement>(null);
  const terminal = useRef<Terminal | null>(null);
  const connection = useRef<AiAgentTerminalConnection | null>(null);
  const [state, setState] = useState("connecting");
  const [reason, setReason] = useState("");
  const [selected, setSelected] = useState(false);
  const [clipboardNotice, setClipboardNotice] = useState("");

  useEffect(() => {
    let disposed = false;
    let clean = () => {};
    void Promise.all([import("@xterm/xterm"), import("@xterm/addon-fit")]).then(([{ Terminal }, { FitAddon }]) => {
      if (disposed || !mount.current) return;
      const term = new Terminal({
        fontFamily: '"SF Mono", "Menlo", "Consolas", "PingFang SC", monospace', fontSize: 14, lineHeight: 1.3,
        // Keep xterm's native insertText path for Chinese, emoji and mobile input.
        cursorBlink: true, cursorStyle: "bar", scrollback: 10000, screenReaderMode: false, disableStdin: true,
        linkHandler: { activate() {} },
        theme: {
          background: "#ffffff", foreground: "#173f37", cursor: "#173f37", cursorAccent: "#ffffff",
          selectionBackground: "#dbe9df", selectionForeground: "#173f37",
          black: "#18231f", red: "#b55248", green: "#427b64", yellow: "#96713c", blue: "#456e87", magenta: "#805a82", cyan: "#477e78", white: "#e5eae3",
          brightBlack: "#6a7c70", brightRed: "#b55248", brightGreen: "#427b64", brightYellow: "#96713c", brightBlue: "#456e87", brightMagenta: "#805a82", brightCyan: "#477e78", brightWhite: "#ffffff",
        },
      });
      terminal.current = term;
      const fit = new FitAddon();
      term.loadAddon(fit);
      // Terminal escape sequences cannot write to the user's clipboard or open arbitrary URLs.
      const clipboardGuard = term.parser.registerOscHandler(52, () => true);
      term.open(mount.current);
      const client = new AiAgentTerminalConnection(sessionId, {
        output(data) { term.write(data); },
        state(value, message) {
          if (disposed) return;
          setState(value);
          onState(value);
          setReason(message ?? "");
          term.options.disableStdin = ["connecting", "disconnected", "stopped", "exited", "failed"].includes(value.toLowerCase());
        },
        connected() { resize(); term.focus(); },
      });
      connection.current = client;
      const resize = () => {
        if (!mount.current || disposed || mount.current.clientWidth < 1 || mount.current.clientHeight < 1) return;
        fit.fit();
        client.resize(term.cols, term.rows);
      };
      term.attachCustomKeyEventHandler(event => {
        if (event.type !== "keydown" || event.isComposing || event.key !== "Enter" || event.ctrlKey || event.metaKey) return true;
        if (event.shiftKey || event.altKey) {
          event.preventDefault();
          client.input(event.altKey ? "\x1b[13;3u" : "\x1b[13;2u");
          return false;
        }
        return true;
      });
      const input = term.onData(data => { client.input(data); });
      const selection = term.onSelectionChange(() => setSelected(term.hasSelection()));
      const observer = new ResizeObserver(resize);
      observer.observe(mount.current);
      window.addEventListener("resize", resize);
      resize();
      void client.connect();
      clean = () => {
        observer.disconnect(); window.removeEventListener("resize", resize);
        input.dispose(); selection.dispose(); clipboardGuard.dispose(); client.dispose(); term.dispose();
        terminal.current = null; connection.current = null;
      };
    }).catch(error => {
      if (!disposed) { setState("disconnected"); setReason(error instanceof Error ? error.message : "终端未能打开，请重新读取页面"); }
    });
    return () => { disposed = true; clean(); };
  }, [sessionId, onState]);

  const copy = async () => {
    const text = terminal.current?.getSelection();
    if (!text) return;
    try { if (!navigator.clipboard?.writeText) throw new Error("unavailable"); await navigator.clipboard.writeText(text); setClipboardNotice("已复制选中内容"); }
    catch { setClipboardNotice("复制失败，请使用系统复制快捷键"); }
  };
  const paste = async () => {
    try {
      if (!navigator.clipboard?.readText) throw new Error("unavailable");
      const text = await navigator.clipboard.readText();
      if (state === "disconnected" || !connection.current || !terminal.current) return;
      terminal.current.paste(text); terminal.current.focus(); setClipboardNotice("");
    } catch { setClipboardNotice("无法读取剪贴板，请使用系统粘贴快捷键"); }
  };
  const disconnected = ["disconnected", "failed", "exited", "stopped"].includes(state.toLowerCase());
  return <div className="ai-agent-terminal-frame">
    <div className="ai-agent-terminal-toolbar">
      <span className={disconnected ? "ai-agent-connection is-disconnected" : "ai-agent-connection"} role="status">{aiAgentStateLabel(state)}</span>
      <div>
        {disconnected && <button onClick={() => { setReason(""); void connection.current?.connect(); }}>重新连接</button>}
        <button onClick={() => { void copy(); }} disabled={!selected}>复制选中</button>
        <button onClick={() => { void paste(); }} disabled={disconnected || state === "connecting"}>粘贴</button>
      </div>
    </div>
    {reason && <p className="ai-agent-terminal-message" role="alert">{reason}</p>}
    {clipboardNotice && <p className="ai-agent-terminal-message" role="status">{clipboardNotice}</p>}
    <div className="ai-agent-terminal" ref={mount} aria-label="Pi 交互终端" />
  </div>;
}

export function AiAgentPage() {
  const sourceContext = readAiBudgetContext(typeof location === "undefined" ? "" : location.search);
  const [status, setStatus] = useState<AiAgentStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [sessionState, setSessionState] = useState("connecting");
  const [selectedSession, setSelectedSession] = useState("");
  const [preparedTaskId, setPreparedTaskId] = useState("");
  const [copyNotice, setCopyNotice] = useState("");
  const controller = useRef<AbortController | null>(null);
  const mounted = useRef(true);
  const operation = useRef(false);

  const load = useCallback(async () => {
    controller.current?.abort();
    const request = new AbortController(); controller.current = request;
    setLoading(true); setError("");
    try {
      const result = await getAiAgentStatus(request.signal);
      if (request.signal.aborted || !mounted.current) return;
      setStatus(result);
      setSelectedSession(previous => result.sessions.some(session => session.id === previous && aiAgentSessionActive(session))
        ? previous : result.sessions.find(aiAgentSessionActive)?.id ?? "");
    } catch (failure) {
      if (!request.signal.aborted && mounted.current) setError(failure instanceof Error ? failure.message : "AI 状态未能读取");
    } finally { if (!request.signal.aborted && mounted.current) setLoading(false); }
  }, []);

  useEffect(() => {
    mounted.current = true; void load();
    return () => { mounted.current = false; controller.current?.abort(); };
  }, [load]);

  const start = async () => {
    if (operation.current || !status?.available) return;
    operation.current = true; setBusy(true); setError(""); setNotice("");
    try {
      const session = await createAiAgentSession();
      if (!mounted.current) return;
      setSessionState("connecting"); setSessionId(session.id); setSelectedSession(session.id);
      setStatus(previous => previous ? { ...previous, sessions: [session, ...previous.sessions.filter(item => item.id !== session.id)] } : previous);
    } catch (failure) { if (mounted.current) setError(failure instanceof Error ? failure.message : "会话未能开始"); }
    finally { operation.current = false; if (mounted.current) setBusy(false); }
  };
  const stop = async () => {
    if (operation.current || !sessionId) return;
    operation.current = true; setBusy(true); setError("");
    try {
      const stopped = sessionId; await stopAiAgentSession(stopped);
      if (!mounted.current) return;
      setSessionId(null); setSelectedSession(status?.sessions.find(item => item.id !== stopped && aiAgentSessionActive(item))?.id ?? ""); setNotice("会话已停止，记录保留");
      setStatus(previous => previous ? { ...previous, sessions: previous.sessions.map(item => item.id === stopped ? { ...item, state: "stopped" } : item) } : previous);
    } catch (failure) { if (mounted.current) setError(failure instanceof Error ? failure.message : "会话未能停止"); }
    finally { operation.current = false; if (mounted.current) setBusy(false); }
  };
  const copyPrepared = async () => {
    const prompt = aiAgentPreparedPrompt(preparedTaskId, sourceContext);
    if (!prompt) return;
    try {
      if (!navigator.clipboard?.writeText) throw new Error("unavailable");
      await navigator.clipboard.writeText(prompt);
      if (mounted.current) setCopyNotice("已复制，在 Pi 中粘贴后发送");
    } catch { if (mounted.current) setCopyNotice("复制失败，请选中问题后使用系统复制快捷键"); }
  };
  return <div className="decision-shell ai-agent-shell">
    <FY27Header active="ai"><span className="ai-agent-header-model" title={aiAgentModelLabel(status)}>{aiAgentModelLabel(status)}</span></FY27Header>
    <AiAgentWorkspace status={status} loading={loading} busy={busy} error={error} notice={notice} sessionId={sessionId}
      sessionEnded={["stopped", "exited", "terminated"].includes(sessionState.toLowerCase())}
      selectedSession={selectedSession} onSelectSession={setSelectedSession} onStart={() => { void start(); }}
      onContinue={() => { if (selectedSession && status?.available) { setError(""); setNotice(""); setSessionState("connecting"); setSessionId(selectedSession); } }}
      onStop={() => { void stop(); }} onRetry={() => { void load(); }}
      preparedTaskId={preparedTaskId} onPrepareTask={id => { setPreparedTaskId(id); setCopyNotice(""); }}
      onCopyPrepared={() => { void copyPrepared(); }} copyNotice={copyNotice} sourceContext={sourceContext}
      terminal={sessionId ? <AiAgentTerminal key={sessionId} sessionId={sessionId} onState={setSessionState} /> : null} />
  </div>;
}
