// Mocked transport and static UI contracts. No model calls, budget writes, or live sessions.
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import {
  getAiAgentStatus, createAiAgentSession, stopAiAgentSession, aiAgentTerminalUrl,
  aiAgentSessionActive, AiAgentTerminalConnection,
} from "../src/aiAgentApi.ts";

const require = createRequire(import.meta.url);
const compiled = await build({
  entryPoints: [fileURLToPath(new URL("../src/AiAgentPage.tsx", import.meta.url))], bundle: true, write: false,
  platform: "node", format: "cjs", jsx: "automatic", external: ["react", "react-dom", "@xterm/xterm", "@xterm/addon-fit"],
  loader: { ".css": "empty" }, logLevel: "silent",
});
const module = { exports: {} };
new Function("require", "module", "exports", compiled.outputFiles[0].text)(id => id.endsWith(".css") ? {} : require(id), module, module.exports);
const { AiAgentPage, AiAgentWorkspace, aiAgentModelLabel, aiAgentStateLabel } = module.exports;
const source = fs.readFileSync(new URL("../src/AiAgentPage.tsx", import.meta.url), "utf8");
const status = (overrides = {}) => ({ available: true, default_model: "configured-provider/business-model", pi_version: "1.2.0",
  models: [{ provider: "configured-provider", id: "business-model", name: "业务模型" }], fallbacks: [],
  isolation: "docker-network-none", permissions: { budget: "read-only", source_files: "none" }, sessions: [], ...overrides });
const workspaceProps = (overrides = {}) => ({ status: status(), loading: false, busy: false, error: "", notice: "", sessionId: null,
  selectedSession: "", onSelectSession() {}, onStart() {}, onContinue() {}, onStop() {}, onRetry() {}, ...overrides });
const render = props => renderToStaticMarkup(createElement(AiAgentWorkspace, workspaceProps(props)));
const text = html => html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
const nodes = tree => !tree || typeof tree !== "object" ? [] : Array.isArray(tree) ? tree.flatMap(nodes) : [tree, ...nodes(tree.props?.children)];

test("AI requests use only same-origin cookies, explicit JSON mutations and no local-budget identity", async () => {
  const original = globalThis.fetch, requests = [];
  globalThis.fetch = async (url, options) => {
    requests.push({ url, ...options });
    return Response.json(url.endsWith("/status") ? status() : { id: "session-1", state: "running" });
  };
  try {
    const signal = new AbortController().signal;
    await getAiAgentStatus(signal); await createAiAgentSession(); await stopAiAgentSession("session-1");
    assert.deepEqual(requests.map(item => [item.url, item.method]), [
      ["/api/v1/ai-agent/status", "GET"], ["/api/v1/ai-agent/sessions", "POST"], ["/api/v1/ai-agent/sessions/session-1", "DELETE"],
    ]);
    assert.equal(requests[0].signal, signal);
    for (const item of requests) {
      assert.equal(item.credentials, "same-origin");
      const headers = new Headers(item.headers);
      assert.equal(headers.get("X-User-ID"), null); assert.equal(headers.get("Authorization"), null);
      if (item.method !== "GET") { assert.equal(headers.get("Content-Type"), "application/json"); assert.equal(item.body, "{}"); }
    }
  } finally { globalThis.fetch = original; }
});

test("HTTP failures expose real backend reasons without retrying process creation", async () => {
  const original = globalThis.fetch; let calls = 0;
  globalThis.fetch = async () => { calls++; return Response.json({ detail: "隔离环境未准备好" }, { status: 503 }); };
  try { await assert.rejects(createAiAgentSession(), /隔离环境未准备好/); assert.equal(calls, 1); }
  finally { globalThis.fetch = original; }
});

test("incomplete status and session responses fail honestly", async () => {
  const original = globalThis.fetch;
  try {
    globalThis.fetch = async () => Response.json({ available: true });
    await assert.rejects(getAiAgentStatus(), /状态响应不完整/);
    globalThis.fetch = async () => Response.json({ id: "valid" });
    await assert.rejects(createAiAgentSession(), /会话响应不完整/);
  } finally { globalThis.fetch = original; }
});

test("WebSocket addresses are same-origin with no credentials or tickets in the URL", () => {
  assert.equal(aiAgentTerminalUrl("session-1", { origin: "http://127.0.0.1:5188" }), "ws://127.0.0.1:5188/api/v1/ai-agent/sessions/session-1/terminal");
  assert.equal(aiAgentTerminalUrl("session-1", { origin: "https://budget.example" }), "wss://budget.example/api/v1/ai-agent/sessions/session-1/terminal");
  for (const id of ["../escape", "bad?ticket=x", "", "a/b", "a".repeat(129)]) assert.throws(() => aiAgentTerminalUrl(id, { origin: "https://budget.example" }));
  assert.throws(() => aiAgentTerminalUrl("session-1", { origin: "file:///" }));
});

class FakeWebSocket {
  static OPEN = 1;
  static instances = [];
  readyState = 0;
  sent = [];
  closed = false;
  constructor(url) { this.url = url; FakeWebSocket.instances.push(this); }
  send(frame) { this.sent.push(JSON.parse(frame)); }
  close() { this.closed = true; this.readyState = 3; this.onclose?.(); }
  open() { this.readyState = 1; this.onopen?.(); }
  message(frame) { this.onmessage?.({ data: typeof frame === "string" ? frame : JSON.stringify(frame) }); }
}

async function withConnection(run, response) {
  const originals = { fetch: globalThis.fetch, WebSocket: globalThis.WebSocket, window: globalThis.window };
  const outputs = [], states = [], requests = []; let connected = 0;
  FakeWebSocket.instances = [];
  globalThis.WebSocket = FakeWebSocket;
  globalThis.window = { location: { origin: "http://127.0.0.1:5188" } };
  globalThis.fetch = async (url, options) => { requests.push({ url, ...options }); return response ? response(url, options) : Response.json({ ticket: `private-ticket-${requests.length}` }); };
  const client = new AiAgentTerminalConnection("session-1", {
    output(data) { outputs.push(data); }, state(value, reason) { states.push([value, reason]); }, connected() { connected++; },
  });
  try { await run({ client, outputs, states, requests, sockets: FakeWebSocket.instances, connected: () => connected }); }
  finally { client.dispose(); Object.assign(globalThis, originals); }
}

test("explicit attach sends its private ticket only as the first frame before resize", async () => {
  await withConnection(async ({ client, sockets, requests, connected }) => {
    client.resize(110, 32); await client.connect();
    assert.equal(sockets.length, 1); assert.equal(client.input("before-open"), false);
    assert.equal(requests[0].url, "/api/v1/ai-agent/sessions/session-1/attach-ticket");
    assert.equal(requests[0].method, "POST"); assert.equal(requests[0].credentials, "same-origin");
    assert.doesNotMatch(sockets[0].url, /ticket|private|\?/);
    sockets[0].open();
    assert.deepEqual(sockets[0].sent[0], { type: "attach", ticket: "private-ticket-1", after: 0 });
    assert.deepEqual(sockets[0].sent[1], { type: "resize", cols: 110, rows: 32 });
    assert.equal(connected(), 1);
    assert.equal(client.input("/model\r"), true);
    assert.deepEqual(sockets[0].sent[2], { type: "input", data: "/model\r" });
  });
});

test("reconnection resumes output after its sequence without replaying prompts or creating a process", async () => {
  await withConnection(async ({ client, sockets, outputs, requests }) => {
    await client.connect(); sockets[0].open();
    sockets[0].message({ type: "output", seq: 1, data: "你好" });
    sockets[0].message({ type: "output", seq: 1, data: "duplicate" });
    sockets[0].message({ type: "output", seq: 2, data: "\x1b[32m世界" });
    client.input("task\r"); sockets[0].close(); await client.connect(); sockets[1].open();
    assert.deepEqual(sockets[1].sent[0], { type: "attach", ticket: "private-ticket-2", after: 2 });
    sockets[1].message({ type: "output", seq: 2, data: "duplicate-again" });
    sockets[1].message({ type: "output", seq: 3, data: "restored" });
    assert.deepEqual(outputs, ["你好", "\x1b[32m世界", "restored"]);
    assert.equal(sockets[1].sent.some(frame => frame.type === "input"), false);
    assert.ok(requests.every(item => item.url.endsWith("/attach-ticket")));
  });
});

test("stale socket output and close events cannot corrupt a reconnected terminal", async () => {
  await withConnection(async ({ client, sockets, outputs, states }) => {
    await client.connect(); sockets[0].open(); await client.connect(); sockets[1].open();
    const count = states.length;
    sockets[0].message({ type: "output", seq: 500, data: "stale" }); sockets[0].close();
    assert.equal(states.length, count); assert.equal(client.sequence, 0); assert.deepEqual(outputs, []);
  });
});

test("large Chinese and emoji pastes respect server frame limits without losing Unicode or submitting twice", async () => {
  await withConnection(async ({ client, sockets }) => {
    await client.connect(); sockets[0].open();
    const pasted = "\x1b[200~" + "预算😀".repeat(12000) + "\x1b[201~";
    client.input(pasted);
    const frames = sockets[0].sent.filter(frame => frame.type === "input");
    assert.ok(frames.length > 1);
    assert.equal(frames.map(frame => frame.data).join(""), pasted);
    for (const frame of frames) { assert.ok(Buffer.byteLength(frame.data, "utf8") <= 32768); assert.doesNotMatch(frame.data, /[\uD800-\uDBFF]$/); }
  });
});

test("a truncated terminal replay is disclosed rather than silently shown as complete history", async () => {
  await withConnection(async ({ client, sockets, states, outputs }) => {
    await client.connect(); sockets[0].open(); sockets[0].message({ type: "gap", seq: 30 });
    assert.equal(client.sequence, 30); assert.match(states.at(-1)[1], /完整对话可用 \/resume 回顾/);
    sockets[0].message({ type: "output", seq: 31, data: "latest" });
    assert.deepEqual(outputs, ["latest"]);
  });
});

test("invalid frames close cleanly, preserve a readable error, and never become HTML", async () => {
  await withConnection(async ({ client, sockets, outputs, states }) => {
    await client.connect(); sockets[0].open();
    sockets[0].message({ type: "output", seq: -1, data: "invalid-sequence" });
    sockets[0].message("not-json");
    assert.deepEqual(outputs, []); assert.equal(sockets[0].closed, true);
    assert.deepEqual(states.at(-1), ["disconnected", "终端响应无法读取，请重新连接"]);
  });
  assert.doesNotMatch(source, /dangerouslySetInnerHTML|\.innerHTML\s*=|WebLinksAddon/);
  assert.match(source, /registerOscHandler\(52, \(\) => true\)/);
  assert.match(source, /linkHandler:\s*\{ activate\(\) \{\} \}/);
});

test("normal stop states remain stopped rather than becoming a misleading connection failure", async () => {
  await withConnection(async ({ client, sockets, states }) => {
    await client.connect(); sockets[0].open(); sockets[0].message({ type: "state", state: "stopped" }); sockets[0].close();
    assert.equal(states.at(-1)[0], "stopped");
  });
});

test("resize is bounded and disposed views disconnect without DELETE or stopping native Pi", async () => {
  await withConnection(async ({ client, sockets, requests, states }) => {
    await client.connect(); sockets[0].open(); client.resize(9999, 0); client.resize(NaN, Infinity);
    assert.deepEqual(sockets[0].sent.at(-1), { type: "resize", cols: 400, rows: 5 });
    const length = states.length; client.dispose();
    assert.equal(sockets[0].closed, true); assert.equal(client.input("lost"), false); assert.equal(states.length, length);
    assert.ok(requests.every(item => item.method === "POST" && item.url.endsWith("attach-ticket")));
  });
});

test("late tickets after view disposal cannot create an orphan WebSocket", async () => {
  let resolve;
  await withConnection(async ({ client, sockets }) => {
    const pending = client.connect(); client.dispose(); resolve(Response.json({ ticket: "private" })); await pending;
    assert.equal(sockets.length, 0);
  }, () => new Promise(done => { resolve = done; }));
});

test("attach failure never creates a fake terminal connection or retries automatically", async () => {
  await withConnection(async ({ client, sockets, requests, states }) => {
    await client.connect();
    assert.equal(sockets.length, 0); assert.equal(requests.length, 1);
    assert.deepEqual(states.at(-1), ["disconnected", "连接凭证已过期"]);
  }, () => Response.json({ detail: "连接凭证已过期" }, { status: 403 }));
});

test("the initial page reads status but requires a click before creating a Pi process", () => {
  const html = renderToStaticMarkup(createElement(AiAgentPage));
  assert.match(html, /AI智解|产品助手/); assert.doesNotMatch(html, /FY27|FY28|学校版/);
  assert.match(html, /正在读取 AI 状态/);
  const effect = source.slice(source.indexOf("mounted.current = true"), source.indexOf("const start = async"));
  assert.match(effect, /void load\(\)/); assert.doesNotMatch(effect, /createAiAgentSession|DELETE|stopAiAgentSession/);
  assert.match(source, /operation\.current \|\| !status\?\.available/);
  assert.doesNotMatch(source, /localStorage|sessionStorage|apiKey|local-trial/);
});

test("native Chinese, emoji and mobile insertText are not suppressed by forced screen-reader mode", () => {
  assert.match(source, /screenReaderMode:\s*false/);
  assert.doesNotMatch(source, /screenReaderMode:\s*true|addEventListener\(["'](?:input|compositionstart|compositionupdate|compositionend)["']/,
    "xterm owns IME composition and insertText; an extra handler could duplicate committed Chinese input");
  assert.match(source, /aria-label="Pi 交互终端"/);
  assert.match(source, /event\.isComposing/);
});

test("the workspace is honest about unavailable infrastructure and offers retry", () => {
  const html = render({ status: status({ available: false, reason: "Docker 未运行" }) });
  assert.match(text(html), /AI 暂时不可用 Docker 未运行 重新读取/);
  assert.match(html, /role="alert"/); assert.match(html, /disabled="">开始对话/);
  assert.doesNotMatch(text(html), /已连接|准备完成|glm/);
  assert.match(text(render({ status: null, loading: false, error: "网络不可达" })), /网络不可达/);
});

test("available UI uses configured model metadata and retained native commands, not hardcoded OpenClaw secrets", () => {
  const html = render({});
  assert.match(text(html), /业务模型/);
  for (const command of ["/model", "/settings", "/resume", "/tree", "/fork", "/compact", "/hotkeys"]) assert.ok(html.includes(command), command);
  assert.match(text(html), /预算只读 独立工作区/);
  assert.match(text(html), /有什么预算问题/);
  assert.match(text(html), /浏览器限制/);
  assert.equal(aiAgentModelLabel(status()), "业务模型");
  assert.equal(aiAgentModelLabel(status({ default_model: "custom-id" })), "custom-id");
  assert.equal(aiAgentModelLabel(null), "模型待连接");
  assert.equal(aiAgentStateLabel("connecting"), "正在连接"); assert.equal(aiAgentStateLabel("stopped"), "已停止");
});

test("retained live sessions can be reattached while stopped history stays in native /resume", () => {
  const sessions = [{ id: "running-session", state: "running" }, { id: "stopped-session", state: "stopped" }];
  const html = render({ status: status({ sessions }), selectedSession: "running-session" });
  assert.match(html, /选择工作会话/); assert.match(html, /继续对话/); assert.doesNotMatch(html, /value="stopped-session"/);
  assert.equal(aiAgentSessionActive(sessions[0]), true); assert.equal(aiAgentSessionActive(sessions[1]), false);
  const running = render({ sessionId: "running-session", terminal: createElement("div", { "data-test": "native-terminal" }, "native") });
  assert.match(running, /停止会话/); assert.match(running, /data-test="native-terminal"/); assert.doesNotMatch(running, /有什么预算问题/);
  const ended = render({ sessionId: "running-session", sessionEnded: true, terminal: createElement("div", null, "保留当前记录") });
  assert.match(ended, /开始新对话/); assert.match(ended, /保留当前记录/); assert.doesNotMatch(ended, /<button[^>]*>停止会话/);
});

test("start, retry and stop controls dispatch only the user's selected action", () => {
  let started = 0, stopped = 0, retried = 0;
  const props = workspaceProps({ onStart() { started++; }, onStop() { stopped++; }, onRetry() { retried++; } });
  const initial = nodes(AiAgentWorkspace(props));
  initial.find(node => node.type === "button" && node.props.children === "开始对话").props.onClick();
  assert.deepEqual([started, stopped, retried], [1, 0, 0]);
  nodes(AiAgentWorkspace({ ...props, sessionId: "s" })).find(node => node.type === "button" && node.props.children === "停止会话").props.onClick();
  nodes(AiAgentWorkspace({ ...props, error: "failed" })).find(node => node.type === "button" && node.props.children === "重新读取").props.onClick();
  assert.deepEqual([started, stopped, retried], [1, 1, 1]);
});
