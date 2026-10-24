import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { chatIsBusy, chatMarkdown, createChat, getChat, listChats, sendChat, stopChat, updateChat } from "../src/aiChatApi.ts";

const require = createRequire(import.meta.url);
async function ui(file) {
  const result = await build({ entryPoints: [fileURLToPath(new URL(`../src/${file}`, import.meta.url))], bundle: true, write: false,
    platform: "node", format: "cjs", jsx: "automatic", external: ["react", "react-dom"], loader: { ".css": "empty" }, logLevel: "silent" });
  const module = { exports: {} };
  new Function("require", "module", "exports", result.outputFiles[0].text)(require, module, module.exports);
  return module.exports;
}
const { AiChatMessage, chatSafeUrl } = await ui("AiChatMessage.tsx");
const { AiChatPage, chatHistoryGroups } = await ui("AiChatPage.tsx");
const source = fs.readFileSync(new URL("../src/AiChatPage.tsx", import.meta.url), "utf8");
const id = "a".repeat(32), requestId = "b".repeat(32);
const record = (values = {}) => ({ id, title: "预算结构", pinned: false, archived: false, state: "idle", created_at: "2026-10-20T01:00:00Z", updated_at: "2026-10-23T01:00:00Z", revision: 1, message_count: 0, context: null, messages: [], ...values });

test("source references start collapsed and switching conversations resets the disclosure", () => {
  const sourceTag = source.match(/<details[^>]*className="ai-chat-source"[^>]*>/)?.[0];
  assert.ok(sourceTag);
  assert.doesNotMatch(sourceTag, /\bopen[=\s>]/);
  assert.match(sourceTag, /key=\{selectedId \|\| "new-source"\}/);
});

test("chat API uses exact owner-scoped paths, same-origin cookies and explicit JSON writes", async () => {
  const original = globalThis.fetch, calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url, ...options });
    return Response.json(url.endsWith("conversations") && options.method === "GET" ? { conversations: [record()] } : record());
  };
  try {
    await listChats(); await createChat(requestId, null); await getChat(id);
    await sendChat(id, "你好", requestId); await updateChat(id, { pinned: true }); await stopChat(id);
    assert.deepEqual(calls.map(call => call.method), ["GET", "POST", "GET", "POST", "PATCH", "POST"]);
    assert.ok(calls.every(call => call.credentials === "same-origin" && call.cache === "no-store"));
    assert.deepEqual(JSON.parse(calls[3].body), { text: "你好", request_id: requestId });
    assert.doesNotMatch(JSON.stringify(calls), /apiKey|X-User-ID|Authorization|attach-ticket/);
    await assert.rejects(getChat("../secret")); assert.equal(calls.length, 6);
  } finally { globalThis.fetch = original; }
});

test("failed or malformed chat responses never become fake success or auto retries", async () => {
  const original = globalThis.fetch; let calls = 0;
  try {
    globalThis.fetch = async () => { calls++; return Response.json({ detail: "请重新连接" }, { status: 401 }); };
    await assert.rejects(sendChat(id, "你好", requestId), /请重新连接/); assert.equal(calls, 1);
    globalThis.fetch = async () => Response.json({ ...record(), messages: [{ role: "tool", text: "not a user message" }] });
    await assert.rejects(getChat(id), /不完整/);
    globalThis.fetch = async () => Response.json({ conversations: null });
    await assert.rejects(listChats(), /无法读取/);
  } finally { globalThis.fetch = original; }
});

test("history is pinned and grouped by real local dates without synthetic sessions", () => {
  const now = new Date("2026-10-23T13:00:00+08:00");
  const groups = chatHistoryGroups([record(), record({ id: "b".repeat(32), pinned: true }), record({ id: "c".repeat(32), updated_at: "2026-10-01T00:00:00Z" })], now);
  assert.deepEqual(groups.map(group => group[0]), ["置顶", "今天", "更早"]);
  assert.equal(groups.flatMap(group => group[1]).length, 3);
  assert.equal(chatIsBusy("running"), true); assert.equal(chatIsBusy("interrupted"), false);
});

test("primary workspace is a conversation UI, not a terminal or an auto-starting agent", () => {
  const html = renderToStaticMarkup(createElement(AiChatPage));
  for (const label of ["新对话", "搜索对话标题", "已归档", "向AI智解提问", "发送消息", "本机保存", "预算只读"]) assert.ok(html.includes(label), label);
  assert.doesNotMatch(html, /xterm|终端模式|\/resume|fixture|P25|N23/);
  assert.match(source, /nativeEvent\.isComposing/); assert.match(source, /composing\.current/);
  assert.match(source, /keyCode !== 229/); assert.match(source, /!event\.shiftKey/);
  assert.doesNotMatch(source, /localStorage|sessionStorage|dangerouslySetInnerHTML/);
  const startup = source.slice(source.indexOf("useEffect(() => { alive.current"), source.indexOf("useEffect(() => {\n    if (!selectedId"));
  assert.doesNotMatch(startup, /sendChat|createChat|stopChat/);
});

test("Markdown is readable but cannot inject HTML, scripts or automatically load remote images", () => {
  const html = renderToStaticMarkup(createElement(AiChatMessage, { message: { id, role: "assistant", text: "**结论**\n\n<script>alert(1)</script>\n\n[危险](javascript:alert(1))\n\n![图](https://outside.test/private.png)", state: "complete", created_at: "" } }));
  assert.ok(html.includes("<strong>结论</strong>"));
  assert.doesNotMatch(html, /<script|<img|href="javascript|src="https:/);
  assert.equal(chatSafeUrl("javascript:alert(1)"), ""); assert.equal(chatSafeUrl("//outside.test"), "");
  assert.equal(chatSafeUrl("/budget/forecast?archive=fy27"), "/budget/forecast?archive=fy27");
});

test("every AI table keeps shared copy and export, and user text stays plain", () => {
  const html = renderToStaticMarkup(createElement(AiChatMessage, { message: { id, role: "assistant", text: "| 季度 | 人次 |\n|---|---:|\n| 秋季 | 18 |", state: "complete", created_at: "" } }));
  assert.match(html, /一键复制/); assert.match(html, /导出表格/); assert.match(html, /data-budget-table/);
  const user = renderToStaticMarkup(createElement(AiChatMessage, { message: { id, role: "user", text: "<img src=x> **我的原话**", state: "complete", created_at: "" } }));
  assert.match(user, /&lt;img/); assert.ok(user.includes("**我的原话**"));
});

test("exports include the referenced version and complete visible conversation without changing budgets", () => {
  const data = chatMarkdown(record({ context: { label: "FY28预算编制 · 第23版", scope: "春季", href: "/budget/next-year?nyVersion=exact23", prompt: "internal context" }, messages: [{ id, role: "user", text: "解释一下", created_at: "", state: "complete" }, { id: requestId, role: "assistant", text: "只读分析", created_at: "", state: "interrupted" }] }));
  assert.match(data, /第23版/); assert.match(data, /exact23/); assert.match(data, /解释一下/); assert.match(data, /只读分析/);
  assert.match(data, /回答已停止/);
  assert.doesNotMatch(data, /internal context|workspace_id/);
});

test("switch, read-back and scroll contracts protect drafts and prevent duplicate submissions", () => {
  assert.match(source, /selected\.current !== selectedId/);
  assert.match(source, /setRecord\(null\)/);
  assert.match(source, /pending\.current\?\.chatId === id/);
  assert.match(source, /messages\.some\(message => message\.id === pending\.current\?\.id\)/);
  assert.match(source, /drafts\.current\.get/); assert.match(source, /beforeunload/);
  assert.match(source, /if \(!id\) \{ setArchived\(false\); setSearch\(""\); \}/);
  assert.match(source, /record\.archived \? "已归档"/);
  assert.match(source, /if \(atBottom\.current && messages\.current\)/);
  assert.match(source, /回到最新消息/); assert.match(source, /chatIsBusy\(record\?\.state\)/);
});
