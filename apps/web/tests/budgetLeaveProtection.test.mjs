import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import fs from "node:fs";
import * as React from "react";
import { build } from "esbuild";

const require = createRequire(import.meta.url);
async function compile(file) {
  const result = await build({ entryPoints: [fileURLToPath(new URL(`../src/${file}`, import.meta.url))], bundle: true,
    write: false, platform: "node", format: "cjs", jsx: "automatic", external: ["react", "react/jsx-runtime"], loader: { ".css": "empty" }, logLevel: "silent" });
  return hooks => {
    const module = { exports: {} };
    new Function("require", "module", "exports", result.outputFiles[0].text)(id => id === "react" ? hooks : require(id), module, module.exports);
    return module.exports;
  };
}
const navigation = await compile("budgetNavigation.ts"), knowledge = await compile("AiKnowledge.tsx");
function browser() {
  const original = globalThis.window, events = new EventTarget(), writes = [];
  const window = { location: { href: "http://localhost/budget/ai?chat=original", origin: "http://localhost" },
    history: { pushState(_state, _title, value) { writes.push(String(value)); window.location.href = String(value); } },
    addEventListener: (...args) => events.addEventListener(...args), removeEventListener: (...args) => events.removeEventListener(...args),
    dispatchEvent: event => events.dispatchEvent(event), scrollTo() {} };
  globalThis.window = window;
  return { window, events, writes, close() { if (original === undefined) delete globalThis.window; else globalThis.window = original; } };
}
test("AI leave protection covers module clicks, history and native reload, and releases cleanly", () => {
  const h = browser(), cleanups = [], hooks = { ...React, useEffect(fn) { cleanups.push(fn()); } };
  const nav = navigation(hooks); let blocks = 0;
  try {
    nav.useBudgetModuleLeaveProtection(true, () => blocks++);
    assert.equal(h.events.dispatchEvent(new Event(nav.budgetModuleLeaveEvent, { cancelable: true })), false);
    assert.equal(blocks, 1); assert.equal(h.writes.length, 0);
    h.window.location.href = "http://localhost/budget/forecast";
    assert.equal(h.events.dispatchEvent(new Event(nav.budgetHistoryLeaveEvent, { cancelable: true })), false);
    assert.equal(blocks, 2); assert.equal(h.window.location.href, "http://localhost/budget/ai?chat=original");
    assert.equal(h.events.dispatchEvent(new Event("beforeunload", { cancelable: true })), false);
    cleanups.splice(0).forEach(fn => fn?.());
    assert.equal(h.events.dispatchEvent(new Event(nav.budgetModuleLeaveEvent, { cancelable: true })), true);
    assert.equal(h.events.dispatchEvent(new Event(nav.budgetHistoryLeaveEvent, { cancelable: true })), true);
    assert.equal(h.events.dispatchEvent(new Event("beforeunload", { cancelable: true })), true);
    assert.equal(blocks, 2);
  } finally { cleanups.forEach(fn => fn?.()); h.close(); }
});
test("multiple AI guards restore blocked history once, while clean editors allow leaving", () => {
  const h = browser(), cleanups = [], nav = navigation({ ...React, useEffect(fn) { cleanups.push(fn()); } });
  let blocks = 0;
  try {
    nav.useBudgetModuleLeaveProtection(false, () => blocks++);
    assert.equal(h.events.dispatchEvent(new Event(nav.budgetModuleLeaveEvent, { cancelable: true })), true);
    nav.useBudgetModuleLeaveProtection(true, () => blocks++); nav.useBudgetModuleLeaveProtection(true, () => blocks++);
    h.events.dispatchEvent(new Event(nav.budgetHistoryLeaveEvent, { cancelable: true }));
    assert.equal(h.writes.length, 1); assert.equal(blocks, 2);
  } finally { cleanups.forEach(fn => fn?.()); h.close(); }
});

const nodes = tree => Array.isArray(tree) ? tree.flatMap(nodes) : !tree || typeof tree !== "object" ? [] : [tree, ...nodes(tree.props?.children)];
function panelHarness() {
  const h = browser(), slots = [], effects = []; let cursor = 0, blocked = 0;
  const hooks = { ...React,
    useState(initial) { const i = cursor++; if (!(i in slots)) slots[i] = typeof initial === "function" ? initial() : initial; return [slots[i], next => { slots[i] = typeof next === "function" ? next(slots[i]) : next; }]; },
    useRef(initial) { const i = cursor++; return slots[i] ??= { current: initial }; },
    useEffect(fn, deps) { const i = cursor++, before = slots[i]; if (!before || !deps || deps.some((value, index) => value !== before.deps?.[index])) {
      slots[i] = { deps, cleanup: before?.cleanup }; effects.push(() => { slots[i].cleanup?.(); slots[i].cleanup = fn(); });
    } },
  };
  const { KnowledgePanel } = knowledge(hooks);
  const item = { id: "a".repeat(32), title: "原偏好", content: "原内容", kind: "preference", module: "all", fiscal_year: null, keywords: [], state: "active", revision: 1,
    created_at: "2026-10-26T00:00:00Z", updated_at: "2026-10-26T00:00:00Z", source: {}, history: [] };
  const props = { open: true, collection: { revision: 1, items: [item], builtin: [] }, error: "", loading: false, workingId: "", editingId: null,
    snapshot: null, view: "manage", onClose() {}, onRefresh() {}, onEditing(id) { props.editingId = id; },
    onNavigationBlocked() { blocked++; props.open = true; }, onAction: async () => {}, onCreate: async () => {}, onSave: async () => {}, onSource() {}, onView() {} };
  const render = () => { let tree; for (let i = 0; i < 3; i++) { cursor = 0; tree = KnowledgePanel(props); effects.splice(0).forEach(fn => fn()); } return tree; };
  const button = label => nodes(render()).find(node => node.type === "button" && node.props.children === label);
  const field = (name, type) => nodes(render()).find(node => node.type === "form" && (!name || node.props["aria-label"] === name))
    && nodes(nodes(render()).find(node => node.type === "form" && (!name || node.props["aria-label"] === name))).find(node => node.type === type);
  return { ...h, props, render, button, field, blocked: () => blocked,
    navigate() { const event = new Event("budget:module-leave", { cancelable: true }); return h.events.dispatchEvent(event); },
    close() { slots.forEach(slot => slot?.cleanup?.()); h.close(); } };
}
test("an unsaved new preference blocks leaving even with its panel hidden; cancel releases protection without saving", () => {
  const h = panelHarness();
  try {
    assert.equal(h.navigate(), true);
    h.button("新增知识或偏好").props.onClick();
    h.field("新增知识或偏好", "input").props.onChange({ target: { value: "临时验收偏好" } }); h.render();
    h.props.open = false; h.render();
    assert.equal(h.navigate(), false); assert.equal(h.blocked(), 1); assert.equal(h.props.open, true);
    assert.ok(nodes(h.render()).some(node => node.props?.role === "alert" && /未保存/.test(node.props.children)));
    assert.equal(h.field("新增知识或偏好", "input").props.value, "临时验收偏好");
    h.button("取消新增").props.onClick(); h.render(); assert.equal(h.navigate(), true);
  } finally { h.close(); }
});
test("editing existing preferences and in-flight knowledge writes cannot be lost to a soft route", () => {
  const h = panelHarness();
  try {
    h.props.editingId = "a".repeat(32); h.render();
    h.field(null, "input").props.onChange({ target: { value: "尚未保存的修改" } }); h.render();
    assert.equal(h.navigate(), false);
    h.button("取消编辑").props.onClick(); h.render(); assert.equal(h.navigate(), true);
    h.props.workingId = "new"; h.render(); assert.equal(h.navigate(), false);
    h.props.workingId = ""; h.render(); assert.equal(h.navigate(), true);
  } finally { h.close(); }
});
test("normal background answers do not block module switching or cause automatic resend/stop", () => {
  const source = fs.readFileSync(new URL("../src/AiChatPage.tsx", import.meta.url), "utf8");
  assert.match(source, /useBudgetModuleLeaveProtection\(sending \|\| stopping \|\| Boolean\(draftStorageUnavailable && draft.trim\(\)\)/);
  assert.doesNotMatch(source, /useBudgetModuleLeaveProtection\(busy/);
  assert.match(source, /if \(window.location.pathname !== "\/budget\/ai"\) return/);
  for (const file of ["ForecastPage.tsx", "NextYearBudgetPage.tsx"]) {
    assert.match(fs.readFileSync(new URL(`../src/${file}`, import.meta.url), "utf8"), /if \(!commitBudgetModuleNavigation\(/);
  }
});
