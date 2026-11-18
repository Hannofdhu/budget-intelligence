import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import * as React from "react";
import { build } from "esbuild";
import { renderToStaticMarkup } from "react-dom/server";

const require = createRequire(import.meta.url);
const compiled = await build({ entryPoints: [fileURLToPath(new URL("../src/AiChatLayout.tsx", import.meta.url))], bundle: true,
  write: false, platform: "node", format: "cjs", jsx: "automatic", external: ["react", "react/jsx-runtime"], logLevel: "silent" });
function load(react = React) {
  const module = { exports: {} };
  new Function("require", "module", "exports", compiled.outputFiles[0].text)(id => id === "react" ? react : require(id), module, module.exports);
  return module.exports;
}
const { AiChatLayout, sidebarBounds, sidebarWidth, readSidebarWidth, saveSidebarWidth } = load();
function storage() {
  const values = new Map(); return { values, getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) };
}
test("width is bounded for the main conversation and the docked knowledge panel without discarding a wide preference", () => {
  assert.equal(sidebarWidth(null, 1440, false), 264);
  assert.equal(sidebarWidth(null, 1024, false), 236);
  assert.equal(sidebarWidth(520, 960, false), 480);
  assert.equal(sidebarWidth(520, 1280, true), 476);
  assert.equal(sidebarWidth(520, 1440, true), 520);
  assert.equal(sidebarWidth(-100, 1440, false), 220);
  assert.equal(sidebarWidth(Infinity, 1440, false), 264);
  assert.deepEqual(sidebarBounds(0, true), { min: 220, max: 220 });
});
test("only a bounded presentation preference is persisted; blocked storage remains usable", () => {
  const saved = storage();
  assert.equal(readSidebarWidth(saved), null);
  saveSidebarWidth(381.3, saved); assert.equal(readSidebarWidth(saved), 381);
  assert.deepEqual([...saved.values], [["budget.ai-chat-sidebar-width.v1", "381"]]);
  saveSidebarWidth(9999, saved); assert.equal(readSidebarWidth(saved), 520);
  saveSidebarWidth(null, saved); assert.equal(readSidebarWidth(saved), null);
  for (const value of ["not a number", "", "NaN", "Infinity"]) { saved.values.set("budget.ai-chat-sidebar-width.v1", value); assert.equal(readSidebarWidth(saved), null); }
  const blocked = { getItem() { throw Error("blocked"); }, setItem() { throw Error("blocked"); }, removeItem() { throw Error("blocked"); } };
  assert.equal(readSidebarWidth(blocked), null);
  assert.doesNotThrow(() => saveSidebarWidth(380, blocked)); assert.doesNotThrow(() => saveSidebarWidth(null, blocked));
});
test("separator is named and keyboard discoverable without replacing or remounting conversation children", () => {
  const html = renderToStaticMarkup(React.createElement(AiChatLayout, { sidebarOpen: true, knowledgeOpen: false }, React.createElement("main", null, "原对话")));
  assert.match(html, /role="separator"/); assert.match(html, /aria-label="调整对话侧栏宽度"/);
  assert.match(html, /aria-controls="ai-chat-sidebar"/); assert.match(html, /aria-valuenow="264"/);
  assert.match(html, /tabindex="0"/); assert.match(html, /原对话/);
  const closed = renderToStaticMarkup(React.createElement(AiChatLayout, { sidebarOpen: false, knowledgeOpen: false }, "原输入"));
  assert.doesNotMatch(closed, /role="separator"/); assert.match(closed, /原输入/);
});
function harness(viewport = 1440) {
  const original = { window: globalThis.window, ResizeObserver: globalThis.ResizeObserver }, saved = storage(), listeners = new Map(), slots = [], effects = [], cleanups = [];
  let cursor = 0, closed = false;
  globalThis.window = { innerWidth: viewport, localStorage: saved, addEventListener: (name, fn) => listeners.set(name, fn), removeEventListener: name => listeners.delete(name) };
  globalThis.ResizeObserver = undefined;
  const hooks = { ...React,
    useState(initial) { const i = cursor++; if (!(i in slots)) slots[i] = typeof initial === "function" ? initial() : initial; return [slots[i], next => { slots[i] = typeof next === "function" ? next(slots[i]) : next; }]; },
    useRef(initial) { const i = cursor++; return slots[i] ??= { current: initial }; },
    useEffect(fn, deps) { const i = cursor++, before = slots[i]; if (!before || deps.some((value, index) => value !== before.deps[index])) {
      slots[i] = { deps }; effects.push(() => { before?.cleanup?.(); const cleanup = fn(); slots[i].cleanup = cleanup; cleanups.push(cleanup); });
    } },
  };
  const ui = load(hooks), children = React.createElement("main", null, "未发送的问题");
  const target = { captured: new Set(), focused: false, focus() { this.focused = true; }, setPointerCapture(id) { this.captured.add(id); }, hasPointerCapture(id) { return this.captured.has(id); }, releasePointerCapture(id) { this.captured.delete(id); } };
  const render = (props = {}) => { cursor = 0; return ui.AiChatLayout({ sidebarOpen: true, knowledgeOpen: false, children, ...props }); };
  const separator = tree => tree.props.children[1];
  const event = (x, extra = {}) => ({ clientX: x, pointerId: 7, isPrimary: true, button: 0, currentTarget: target, preventDefault() {}, ...extra });
  return { render, separator, event, target, saved, listeners, children,
    effects() { effects.splice(0).forEach(fn => fn()); },
    close() { if (closed) return; closed = true; cleanups.forEach(fn => fn?.()); for (const [key, value] of Object.entries(original)) if (value === undefined) delete globalThis[key]; else globalThis[key] = value; },
  };
}
test("drag captures the pointer, changes layout only, saves on release and restores defaults on double click", () => {
  const h = harness();
  try {
    let tree = h.render(); h.effects();
    h.separator(tree).props.onPointerDown(h.event(264));
    h.separator(tree).props.onPointerMove(h.event(384)); tree = h.render();
    assert.equal(tree.props.style["--ai-chat-sidebar-width"], "384px");
    assert.equal(tree.props.children[0], h.children); assert.match(tree.props.className, /is-sidebar-resizing/);
    assert.equal(h.target.captured.has(7), true); assert.equal(readSidebarWidth(h.saved), null);
    h.separator(tree).props.onPointerUp(h.event(384)); tree = h.render();
    assert.equal(readSidebarWidth(h.saved), 384); assert.equal(h.target.captured.size, 0);
    assert.doesNotMatch(tree.props.className, /is-sidebar-resizing/);
    h.separator(tree).props.onDoubleClick(); tree = h.render();
    assert.equal(tree.props.style["--ai-chat-sidebar-width"], "264px"); assert.equal(readSidebarWidth(h.saved), null);
  } finally { h.close(); }
});
test("cancel, lost capture, Escape, blur, responsive changes and unmount release the drag without saving partial widths", () => {
  for (const cause of ["cancel", "lost", "escape", "blur", "resize", "hide", "unmount"]) {
    const h = harness();
    try {
      let tree = h.render(); h.effects(); h.separator(tree).props.onPointerDown(h.event(264)); h.separator(tree).props.onPointerMove(h.event(400)); tree = h.render();
      if (cause === "cancel") h.separator(tree).props.onPointerCancel();
      if (cause === "lost") h.separator(tree).props.onLostPointerCapture();
      if (cause === "escape") h.separator(tree).props.onKeyDown({ key: "Escape", preventDefault() {} });
      if (cause === "blur") h.listeners.get("blur")();
      if (cause === "resize") { globalThis.window.innerWidth = 800; h.listeners.get("resize")(); }
      if (cause === "hide") { tree = h.render({ sidebarOpen: false }); h.effects(); }
      if (cause === "unmount") h.close();
      else {
        tree = h.render(cause === "hide" ? { sidebarOpen: false } : {});
        assert.doesNotMatch(tree.props.className, /is-sidebar-resizing/, cause);
        if (!["resize", "hide"].includes(cause)) assert.equal(tree.props.style["--ai-chat-sidebar-width"], "264px", cause);
      }
      assert.equal(h.target.captured.size, 0, cause); assert.equal(readSidebarWidth(h.saved), null, cause);
    } finally { h.close(); }
  }
});
test("keyboard widths are bounded and narrow or hidden sidebars never expose a draggable separator", () => {
  const h = harness();
  try {
    let tree = h.render(); h.effects();
    for (const [key, shiftKey, expected] of [["ArrowRight", false, 274], ["ArrowRight", true, 324], ["Home", false, 220], ["ArrowLeft", true, 220], ["End", false, 520], ["Enter", false, 264]]) {
      h.separator(tree).props.onKeyDown({ key, shiftKey, preventDefault() {} }); tree = h.render();
      assert.equal(tree.props.style["--ai-chat-sidebar-width"], `${expected}px`);
    }
    globalThis.window.innerWidth = 1100; h.listeners.get("resize")();
    assert.equal(h.separator(h.render({ knowledgeOpen: true })), false);
    globalThis.window.innerWidth = 800; h.listeners.get("resize")();
    assert.equal(h.separator(h.render()), false);
    assert.equal(h.separator(h.render({ sidebarOpen: false })), false);
  } finally { h.close(); }
});
