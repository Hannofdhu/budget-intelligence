import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { actOnKnowledge, createKnowledge, editKnowledge, listKnowledge, proposeKnowledge } from "../src/aiKnowledgeApi.ts";
import { chatMarkdown, getChat } from "../src/aiChatApi.ts";

const require = createRequire(import.meta.url);
async function ui(file) {
  const result = await build({ entryPoints: [fileURLToPath(new URL(`../src/${file}`, import.meta.url))], bundle: true,
    write: false, platform: "node", format: "cjs", jsx: "automatic", external: ["react", "react-dom"], loader: { ".css": "empty" }, logLevel: "silent" });
  const module = { exports: {} };
  new Function("require", "module", "exports", result.outputFiles[0].text)(require, module, module.exports);
  return module.exports;
}
const { KnowledgeCard, KnowledgePanel } = await ui("AiKnowledge.tsx");
const { AiChatMessage } = await ui("AiChatMessage.tsx");
const id = "a".repeat(32), chatId = "b".repeat(32), answerId = "c".repeat(32);
const item = (values = {}) => ({ id, title: "校区与赛道分开呈现", content: "校区是组织维度，赛道是业务维度。",
  kind: "business", module: "all", fiscal_year: null, keywords: ["校区", "赛道"], state: "pending", revision: 1,
  created_at: "2026-10-24T03:00:00Z", updated_at: "2026-10-24T03:00:00Z", source: { conversation_id: chatId, message_id: answerId, excerpt: "不要把校区与赛道混列。" }, history: [], ...values });
const collection = { revision: 1, items: [item()], builtin: [], storage: "local-browser-owned" };

test("knowledge writes remain explicit, owner-cookie-scoped and revision checked", async () => {
  const original = globalThis.fetch, calls = [];
  globalThis.fetch = async (url, options) => { calls.push({ url, ...options }); return Response.json(options.method === "GET" ? collection : item()); };
  try {
    await listKnowledge(); await proposeKnowledge(chatId, answerId);
    await editKnowledge(item(), { title: "新标题", content: "先给结论。", kind: "preference", module: "all", fiscal_year: null, keywords: [] });
    await actOnKnowledge(item({ conflicts: [{ id: "d".repeat(32), revision: 2, title: "同名" }] }), "approve", true);
    await createKnowledge({ title: "业务偏好", content: "先给结论。", kind: "preference", module: "all", fiscal_year: null, keywords: [] }, "e".repeat(32));
    assert.deepEqual(calls.map(call => call.method), ["GET", "POST", "PATCH", "POST", "POST"]);
    assert.ok(calls.every(call => call.credentials === "same-origin" && call.cache === "no-store"));
    assert.deepEqual(JSON.parse(calls[1].body), { message_id: answerId });
    assert.equal(JSON.parse(calls[2].body).revision, 1);
    assert.deepEqual(JSON.parse(calls[3].body).replacements, { ["d".repeat(32)]: 2 });
    assert.equal(calls[4].url, "/api/v1/ai-agent/knowledge");
    assert.equal(JSON.parse(calls[4].body).request_id, "e".repeat(32));
    assert.doesNotMatch(JSON.stringify(calls), /Authorization|X-User-ID|apiKey/);
    await assert.rejects(proposeKnowledge("../host", answerId));
    await actOnKnowledge(item({ id: "core-dimensions", preset: true }), "disable");
    assert.equal(calls[5].url, "/api/v1/ai-agent/knowledge/core-dimensions/actions");
    await assert.rejects(actOnKnowledge(item({ id: "core-unknown" }), "delete"));
    await assert.rejects(createKnowledge({}, "../host"));
    assert.equal(calls.length, 6);
  } finally { globalThis.fetch = original; }
});

test("malformed knowledge or answer snapshots cannot masquerade as confirmed memory", async () => {
  const original = globalThis.fetch;
  try {
    for (const bad of [{ ...collection, items: null }, { ...collection, items: [item({ revision: "1" })] },
      { ...collection, items: [item({ state: "approved-by-model" })] }]) {
      globalThis.fetch = async () => Response.json(bad);
      await assert.rejects(listKnowledge(), /无法读取|不完整/);
    }
    globalThis.fetch = async () => Response.json({ id, title: "原对话", pinned: false, archived: false, state: "idle", context: null,
      revision: 1, created_at: "2026-10-24T00:00:00Z", updated_at: "2026-10-24T00:00:00Z", messages: [
        { id: answerId, role: "assistant", text: "原回答", state: "complete", knowledge: { items: "malformed" } }] });
    await assert.rejects(getChat(id), /不完整/);
    globalThis.fetch = async () => Response.json({ detail: [{ msg: "Value error, 不保存临时人次" }] }, { status: 422 });
    await assert.rejects(editKnowledge(item(), {}), /不保存临时人次/);
  } finally { globalThis.fetch = original; }
});

test("the inline card distinguishes candidate, saved, disabled and conversation-only states", () => {
  const props = { working: false, onAction: async () => {}, onEdit: () => {} };
  const pending = renderToStaticMarkup(createElement(KnowledgeCard, { ...props, item: item() }));
  for (const text of ["待确认", "确认记住", "修改内容", "仅用于本对话", "不会作为长期知识应用", "当前浏览器"]) assert.ok(pending.includes(text), text);
  const active = renderToStaticMarkup(createElement(KnowledgeCard, { ...props, item: item({ state: "active" }) }));
  assert.match(active, /保存成功/); assert.doesNotMatch(active, /确认记住/);
  const disabled = renderToStaticMarkup(createElement(KnowledgeCard, { ...props, item: item({ state: "disabled" }) }));
  assert.match(disabled, /不再加载/);
  const dismissed = renderToStaticMarkup(createElement(KnowledgeCard, { ...props, item: item({ state: "dismissed" }) }));
  assert.match(dismissed, /不作为跨对话知识加载/); assert.doesNotMatch(dismissed, /确认记住/);
});

test("management renders real source and snapshot truth without executing stored markup", () => {
  const props = { open: true, collection, error: "", loading: false, workingId: "", editingId: null, snapshot: null,
    view: "manage", onClose() {}, onRefresh() {}, onEditing() {}, onAction: async () => {}, onSave: async () => {}, onSource() {}, onView() {} };
  const html = renderToStaticMarkup(createElement(KnowledgePanel, props));
  for (const text of ["业务知识与偏好", "搜索知识与偏好", "新增知识或偏好", "删除：校区与赛道分开呈现", "待确认", "当前浏览器", "关闭"]) assert.ok(html.includes(text), text);
  const preset = renderToStaticMarkup(createElement(KnowledgePanel, { ...props, collection: { ...collection, items: [item({ id: "core-dimensions", preset: true, state: "active" })], builtin: [] } }));
  assert.match(preset, /aria-label="删除：校区与赛道分开呈现/);
  assert.match(preset, /初始预置 · 由你管理/);
  assert.doesNotMatch(preset, /不可删除|不由聊天修改/);
  const source = { ...item({ content: "<script>bad()</script>" }), builtin: true };
  const snap = { revision: 1, captured_at: "2026-10-24T03:00:00Z", items: [source] };
  const truth = renderToStaticMarkup(createElement(KnowledgePanel, { ...props, snapshot: snap, view: "snapshot" }));
  assert.match(truth, /回答时的知识快照/); assert.match(truth, /不代表答案已经核验/);
  assert.doesNotMatch(truth, /<script/); assert.match(truth, /&lt;script/);
});

test("proven dimension swaps keep warnings in exports and do not expose clean table exports", () => {
  const validation = { state: "needs_review", warnings: ["校区列混入赛道，待核实"], notice: "有限检查", resources: ["/budget/fy28-budget"], tables_examined: 1 };
  const message = { id: answerId, role: "assistant", text: "| 校区 |\n|---|\n| 智慧伴学 |", state: "complete", validation };
  const html = renderToStaticMarkup(createElement(AiChatMessage, { message }));
  assert.match(html, /待核实/); assert.doesNotMatch(html, /导出表格|一键复制/);
  assert.match(chatMarkdown({ title: "口径测试", updated_at: "2026-10-24", messages: [message], context: null }), /待核实/);
});

test("docked and narrow layouts remain in document flow instead of overlaying the chat", () => {
  const css = fs.readFileSync(new URL("../src/aiKnowledge.css", import.meta.url), "utf8");
  const source = fs.readFileSync(new URL("../src/AiChatPage.tsx", import.meta.url), "utf8");
  assert.match(css, /grid-template-columns: 264px minmax\(420px,1fr\)/);
  assert.match(css, /max-width: 959px/); assert.match(css, /grid-row: 1/);
  assert.doesNotMatch(css, /position:\s*(?:fixed|absolute)|z-index/);
  assert.match(source, /setKnowledgeOpen\(false\)/); assert.match(source, /查看本条回答的口径依据/);
  const hook = fs.readFileSync(new URL("../src/useAiKnowledge.ts", import.meta.url), "utf8");
  assert.match(hook, /noticeValue\?\.context === contextKey \? noticeValue.text : ""/);
  assert.match(source, /useAiKnowledge\([^\n]+selectedId \|\| "new"\)/);
  const ui = fs.readFileSync(new URL("../src/AiKnowledge.tsx", import.meta.url), "utf8");
  assert.match(ui, /确认删除/); assert.match(ui, /then\(success => \{ if \(success\)/);
  assert.match(ui, /creating\.requestId/); assert.match(ui, /Keep input and retry identity on errors/);
  assert.match(css, /\.ai-knowledge-toggle, \.ai-knowledge-sidebar-link \{[^}]+border: 1px solid var\(--budget-control-border\)/);
});
