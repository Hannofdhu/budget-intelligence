import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";
const require = createRequire(import.meta.url);
const compiled = await build({ entryPoints: [fileURLToPath(new URL("../src/BudgetUtilityPanel.tsx", import.meta.url))], bundle: true,
  write: false, platform: "node", format: "cjs", jsx: "automatic", external: ["react"], loader: { ".css": "empty" }, logLevel: "silent" });

function harness() {
  const refs = [], effects = [], previous = [];
  let cursor = 0, effectIndex = 0;
  const loaded = { exports: {} };
  const fakeReact = { ...require("react"), useRef(value) { return refs[cursor++] ??= { current: value }; }, useId() { return "utility-title"; },
    useEffect(callback, deps) { const index = effectIndex++; if (!previous[index] || deps.some((value, i) => !Object.is(value, previous[index].deps[i]))) {
      effects.push(() => { previous[index]?.cleanup?.(); previous[index] = { deps, cleanup: callback() }; });
    } } };
  new Function("require", "module", "exports", compiled.outputFiles[0].text)(name => name === "react" ? fakeReact : require(name), loaded, loaded.exports);
  return { refs, render(props) { cursor = effectIndex = 0; return loaded.exports.BudgetUtilityPanel(props); }, flush() { effects.splice(0).forEach(run => run()); },
    dispose() { previous.forEach(slot => slot.cleanup?.()); } };
}

test("shared utility uses a native labelled dialog, blocks busy dismissal and keeps its child tree", () => {
  const h = harness(), child = { type: "input", props: { value: "未提交草稿" } };
  let closes = 0, prevented = 0;
  let tree = h.render({ open: false, title: "版本记录", onClose() { closes++; }, children: child, busy: true });
  assert.equal(tree.type, "dialog"); assert.equal(tree.props["aria-labelledby"], "utility-title");
  assert.equal(tree.props.children[1].props.children, child, "closed utility retains the subtree and drafts owned by its parent");
  tree.props.onCancel({ preventDefault() { prevented++; } }); assert.equal(prevented, 1); assert.equal(closes, 0);
  assert.equal(tree.props.children[0].props.children[1].props.disabled, true);
  tree = h.render({ open: true, title: "版本记录", onClose() { closes++; }, children: child });
  tree.props.onCancel({ preventDefault() {} }); assert.equal(closes, 1);
  assert.equal(tree.props.children[0].props.children[1].props["aria-label"], "关闭版本记录");
});

test("non-modal lifecycle focuses once, retains inputs and restores the exact launcher without scrolling", () => {
  const oldDocument = Object.getOwnPropertyDescriptor(globalThis, "document"), oldElement = Object.getOwnPropertyDescriptor(globalThis, "HTMLElement"), oldWindow = Object.getOwnPropertyDescriptor(globalThis, "window");
  class Element { isConnected = true; focusCount = 0; focus() { this.focusCount++; } }
  const launcher = new Element(), heading = new Element(), initial = new Element();
  const dialog = { open: false, shows: 0, closes: 0, showModal() { this.open = true; this.shows++; }, close() { this.open = false; this.closes++; } };
  Object.defineProperty(globalThis, "HTMLElement", { value: Element, configurable: true });
  Object.defineProperty(globalThis, "document", { value: { activeElement: launcher }, configurable: true });
  Object.defineProperty(globalThis, "window", { value: { matchMedia: () => ({ matches: false }) }, configurable: true });
  try {
    const h = harness(), props = { open: true, title: "待处理", onClose() {}, children: "details" };
    h.render(props); h.refs[0].current = dialog; h.refs[1].current = heading; h.flush();
    assert.equal(dialog.shows, 0); assert.equal(heading.focusCount, 1);
    h.render(props); h.flush(); assert.equal(heading.focusCount, 1);
    h.render({ ...props, initialFocusRef: { current: initial } }); h.flush();
    assert.equal(initial.focusCount, 0, "a recreated optional ref must not steal focus while the user is working in the open panel");
    h.render({ ...props, open: false }); h.flush(); assert.equal(dialog.closes, 0); assert.equal(launcher.focusCount, 1);
    h.render({ ...props, initialFocusRef: { current: initial } }); h.flush(); assert.equal(initial.focusCount, 1);
    h.dispose(); assert.equal(dialog.closes, 0); assert.equal(launcher.focusCount, 2);
  } finally {
    if (oldDocument) Object.defineProperty(globalThis, "document", oldDocument); else delete globalThis.document;
    if (oldElement) Object.defineProperty(globalThis, "HTMLElement", oldElement); else delete globalThis.HTMLElement;
    if (oldWindow) Object.defineProperty(globalThis, "window", oldWindow); else delete globalThis.window;
  }
});

test("all tool families reserve a rail on wide screens and join document flow on narrow screens", () => {
  const css = fs.readFileSync(new URL("../src/budgetUtility.css", import.meta.url), "utf8");
  for (const family of ["ny-context-panel", "budget-utility-panel[open]", "forecast-group-panel:not([hidden])"]) assert.ok(css.includes(family));
  assert.match(css, /min-width: 1440px/); assert.match(css, /width < 1440px/);
  assert.match(css, /width: calc\(100% - var\(--budget-tool-gutter\) \* 2 - var\(--budget-tool-width\) - var\(--budget-tool-gap\)\)/);
  assert.match(css, /position: static/); assert.doesNotMatch(css, /::backdrop/);
  assert.match(css, /max-width:600px/); assert.match(css, /prefers-reduced-motion/);
});

test("Escape closes only the current non-modal tool and cannot dismiss a busy tool", () => {
  const h = harness(); let closes = 0, stopped = 0;
  const props = { open: true, title: "版本记录", children: "内容", onClose() { closes++; } };
  let tree = h.render(props);
  assert.equal(tree.props.open, true);
  tree.props.onKeyDown({ key: "Escape", preventDefault() {}, stopPropagation() { stopped++; } });
  assert.equal(closes, 1); assert.equal(stopped, 1);
  tree = h.render({ ...props, busy: true });
  tree.props.onKeyDown({ key: "Escape", preventDefault() {}, stopPropagation() {} }); assert.equal(closes, 1);
  tree.props.onKeyDown({ key: "Escape", defaultPrevented: true }); assert.equal(closes, 1);
  tree = h.render({ ...props, open: false }); assert.equal(tree.props.open, false);
});
