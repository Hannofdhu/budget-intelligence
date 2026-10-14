// Navigation-only fixtures; these tests never call or change budget APIs.
import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";
import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";

const require = createRequire(import.meta.url);
const bundled = await build({ entryPoints: [fileURLToPath(new URL("../src/BudgetPageNavigation.tsx", import.meta.url))], bundle: true,
  write: false, platform: "node", format: "cjs", jsx: "automatic", external: ["react"], logLevel: "silent" });
const compile = react => {
  const module = { exports: {} };
  new Function("require", "module", "exports", bundled.outputFiles[0].text)(name => name === "react" ? react : require(name), module, module.exports);
  return module.exports;
};
const labels = ["目标与范围", "入口人数", "招生参数", "资源与财务", "预算概览"];
const nodes = tree => Array.isArray(tree) ? tree.flatMap(nodes) : !tree || typeof tree !== "object" ? [] : [tree, ...nodes(tree.props?.children)];

function harness() {
  const refs = [], effects = []; let cursor = 0;
  const mock = { ...React, useRef(value) { return refs[cursor++] ??= { current: value }; }, useEffect(fn) { effects.push(fn); } };
  const exported = compile(mock);
  return { ...exported, effects, render(fn) { cursor = 0; effects.length = 0; return fn(exported); } };
}

function browser() {
  const keys = ["window", "history", "location", "requestAnimationFrame", "cancelAnimationFrame", "ResizeObserver"];
  const before = Object.fromEntries(keys.map(key => [key, globalThis[key]]));
  const listeners = new Map(), frames = new Map(), scrolls = [], observed = [];
  let frameId = 0;
  globalThis.window = { scrollX: 0, scrollY: 700, addEventListener(name, fn) { (listeners.get(name) ?? listeners.set(name, new Set()).get(name)).add(fn); },
    removeEventListener(name, fn) { listeners.get(name)?.delete(fn); }, scrollTo(position) { scrolls.push(position); this.scrollX = position.left; this.scrollY = position.top; } };
  globalThis.history = { state: { unrelated: "preserved" }, scrollRestoration: "auto", replaceState(state) { this.state = state; } };
  globalThis.location = new URL("http://localhost/budget/workspace?archive=fy27&step=5");
  globalThis.requestAnimationFrame = fn => { frames.set(++frameId, fn); return frameId; };
  globalThis.cancelAnimationFrame = id => frames.delete(id);
  globalThis.ResizeObserver = class { constructor(callback) { this.callback = callback; observed.push(this); } observe(node) { this.node = node; } disconnect() { this.disconnected = true; } };
  return { listeners, frames, scrolls, observed,
    emit(name, value) { for (const fn of listeners.get(name) ?? []) fn(value); },
    nextFrame() { const queue = [...frames.values()]; frames.clear(); queue.forEach(fn => fn()); },
    flush() { while (frames.size) { const queue = [...frames.values()]; frames.clear(); queue.forEach(fn => fn()); } },
    restore() { for (const key of keys) { if (before[key] === undefined) delete globalThis[key]; else globalThis[key] = before[key]; } },
  };
}

test("page navigation renders all five complete names and one unmistakable current page", () => {
  const { BudgetPageNavigation } = compile(React);
  const html = renderToStaticMarkup(React.createElement(BudgetPageNavigation, { labels, current: 5, onChange() {}, label: "预算拆解导航" }));
  assert.equal((html.match(/<button /g) ?? []).length, 5);
  assert.equal((html.match(/aria-current="page"/g) ?? []).length, 1);
  assert.match(html, /aria-label="预算拆解导航"/);
  for (const label of labels) assert.match(html, new RegExp(`<strong>${label}</strong>`));
  assert.doesNotMatch(html, /is-done|decision-progress-short|aria-current="step"/);
});

test("history restoration runs after the newly mounted table and late native restoration, not before them", () => {
  const b = browser();
  try {
    const h = harness(), heading = { current: { focus() { assert.fail("back must not focus"); }, scrollIntoView() { assert.fail("back must not align"); } } };
    const render = () => h.render(({ useBudgetPagePosition }) => useBudgetPagePosition(5, true, heading));
    render(); const cleanup = h.effects[0]();
    b.emit("popstate", { state: { budgetPagePosition: { x: 0, y: 249.09 } } });
    render(); const cleanupRestore = h.effects[1]();
    b.nextFrame();
    assert.equal(b.scrolls.length, 0, "first frame only waits for the restored page to settle");
    // Chromium can restore/clamp against the previous short page after the popstate callback.
    window.scrollTo({ left: 0, top: 0, behavior: "instant" });
    b.nextFrame();
    assert.equal(window.scrollY, 249.09, "saved position wins after the browser's late restore");
    cleanupRestore(); cleanup();
  } finally { b.restore(); }
});

test("clicking the current page is a no-op, while a different page calls only the navigation callback", () => {
  const h = harness(), changed = [];
  const tree = h.render(({ BudgetPageNavigation }) => BudgetPageNavigation({ labels, current: 5, onChange: page => changed.push(page), label: "预算拆解导航" }));
  const buttons = nodes(tree).filter(node => node.type === "button");
  buttons[4].props.onClick(); assert.deepEqual(changed, []);
  buttons[1].props.onClick(); assert.deepEqual(changed, [2]);
});

test("measured header/nav heights update after resize and observers/listeners clean up", () => {
  const b = browser();
  try {
    const values = {}, shell = { style: { setProperty(key, value) { values[key] = value; } } };
    let height = 72;
    const node = { closest: () => shell, getBoundingClientRect: () => ({ height }) };
    const { observeBudgetChrome } = compile(React);
    const cleanup = observeBudgetChrome(node, "--budget-header-height");
    assert.equal(values["--budget-header-height"], "72px");
    height = 120; b.observed[0].callback(); assert.equal(values["--budget-header-height"], "120px");
    height = 160; b.emit("resize"); assert.equal(values["--budget-header-height"], "160px");
    cleanup(); assert.equal(b.observed[0].disconnected, true); assert.equal(b.listeners.get("resize").size, 0);
  } finally { b.restore(); }
});

test("narrow navigation reveals the complete active item on initial render and resize without scrolling the page", () => {
  const b = browser();
  try {
    const h = harness(); let activeRight = 620;
    const nav = { scrollLeft: 0, closest: () => ({ style: { setProperty() {} } }),
      querySelector: () => ({ getBoundingClientRect: () => ({ left: activeRight - 140, right: activeRight }) }),
      getBoundingClientRect: () => ({ left: 0, right: 300, height: 62 }) };
    const tree = h.render(({ BudgetPageNavigation }) => BudgetPageNavigation({ labels, current: 5, onChange() {}, label: "预算拆解导航" }));
    const cleanupMeasure = tree.props.ref(nav), cleanupReveal = h.effects[0]();
    assert.equal(nav.scrollLeft, 328);
    activeRight = 480; b.emit("resize"); assert.equal(nav.scrollLeft, 516);
    assert.deepEqual(b.scrolls, []);
    cleanupReveal(); cleanupMeasure();
  } finally { b.restore(); }
});

test("intentional page changes preserve the old position then align and focus the heading; popstate never focuses it", () => {
  const b = browser();
  try {
    const h = harness(), focus = [], align = [];
    const heading = { current: { focus: options => focus.push(options), scrollIntoView: options => align.push(options) } };
    const render = (page, ready) => h.render(({ useBudgetPagePosition }) => useBudgetPagePosition(page, ready, heading));
    const begin = render(5, true), cleanup = h.effects[0]();
    assert.equal(history.state.unrelated, "preserved"); assert.deepEqual(history.state.budgetPagePosition, { x: 0, y: 700 });
    begin(); location = new URL("http://localhost/budget/workspace?archive=fy27&step=2"); history.state = {};
    render(2, true); h.effects[1](); b.flush();
    assert.deepEqual(focus, [{ preventScroll: true }]); assert.deepEqual(align, [{ block: "start", behavior: "instant" }]);
    location = new URL("http://localhost/budget/workspace?archive=fy27&step=5");
    b.emit("popstate", { state: { budgetPagePosition: { x: 0, y: 780 } } });
    render(5, true); h.effects[1](); b.flush();
    assert.equal(b.scrolls.at(-1).top, 780); assert.equal(focus.length, 1);
    assert.equal(history.scrollRestoration, "auto"); cleanup();
  } finally { b.restore(); }
});

test("unknown history entries retain native restoration and delayed loading waits before restoring known positions", () => {
  const b = browser();
  try {
    const h = harness(), heading = { current: { focus() { assert.fail("back must not focus"); }, scrollIntoView() { assert.fail("back must not align"); } } };
    const render = ready => h.render(({ useBudgetPagePosition }) => useBudgetPagePosition(5, ready, heading));
    render(true); const cleanup = h.effects[0]();
    b.emit("popstate", { state: {} }); render(true); h.effects[1](); b.flush();
    assert.deepEqual(b.scrolls, []); assert.equal(history.scrollRestoration, "auto");
    b.emit("popstate", { state: { budgetPagePosition: { x: 0, y: 1800 } } });
    render(false); h.effects[1](); b.flush(); assert.deepEqual(b.scrolls, []);
    render(true); h.effects[1](); b.flush(); assert.equal(b.scrolls.at(-1).top, 1800);
    const count = b.scrolls.length;
    b.emit("popstate", { state: { budgetPagePosition: { x: 0, y: 999 } } });
    location = new URL("http://localhost/budget/next-year?year=2028");
    render(true); h.effects[1](); b.flush(); assert.equal(b.scrolls.length, count, "a leave guard rollback cancels destination position");
    cleanup();
  } finally { b.restore(); }
});
