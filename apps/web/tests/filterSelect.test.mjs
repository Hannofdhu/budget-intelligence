// Hook/event contracts with synthetic element refs. Browser geometry, top-layer
// rendering and platform accessibility still require the real-browser acceptance.
import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

const require = createRequire(import.meta.url);
const compiled = await build({ entryPoints: [fileURLToPath(new URL("../src/FilterSelect.tsx", import.meta.url))], bundle: true,
  write: false, platform: "node", format: "cjs", jsx: "automatic", external: ["react", "react-dom"], loader: { ".css": "empty" }, logLevel: "silent" });
const find = (node, predicate) => Array.isArray(node) ? node.flatMap(child => find(child, predicate)) : !node || typeof node !== "object" ? []
  : [...(predicate(node) ? [node] : []), ...find(node.props?.children, predicate)];
const text = node => Array.isArray(node) ? node.map(text).join("") : node && typeof node === "object" ? text(node.props?.children)
  : node == null || typeof node === "boolean" ? "" : String(node);
const load = resolve => { const loaded = { exports: {} }; new Function("require", "module", "exports", compiled.outputFiles[0].text)(resolve, loaded, loaded.exports); return loaded.exports.FilterSelect; };

test("filter face stays one compact named dropdown with selected values, not an expanded list", () => {
  const Component = load(require);
  const html = renderToStaticMarkup(createElement(Component, { "aria-label": "筛选校区", value: ["A,B", "中关村"],
    options: ["A,B", "中关村"], onChange() {} }));
  assert.equal((html.match(/role="combobox"/g) ?? []).length, 1);
  assert.match(html, /aria-expanded="false"/); assert.match(html, /aria-haspopup="listbox"/);
  assert.match(html, /title="A,B、中关村"/); assert.match(html, /已选：A,B、中关村/);
  assert.doesNotMatch(html, /role="listbox"|<select[^>]*multiple/);
});

function harness(initial = {}) {
  const names = ["window", "document"], descriptors = new Map(names.map(name => [name, Object.getOwnPropertyDescriptor(globalThis, name)]));
  const listeners = new Map(), windowListeners = new Map(), states = [], refs = [], slots = [], pending = [];
  const changes = [], portals = [], optionElements = new Map();
  let stateIndex = 0, refIndex = 0, effectIndex = 0, dirty = false, props;
  const onChange = value => { changes.push(value); props = { ...props, value }; dirty = true; };
  props = { value: "", options: ["A,B", "中关村", "西直门"], "aria-label": "筛选校区", onChange, ...initial };
  const document = { body: { name: "document-body" }, activeElement: null,
    addEventListener(name, fn) { if (!listeners.has(name)) listeners.set(name, new Set()); listeners.get(name).add(fn); },
    removeEventListener(name, fn) { listeners.get(name)?.delete(fn); } };
  const triggerElement = { name: "trigger", focus() { document.activeElement = this; },
    getBoundingClientRect() { return { left: 20, top: 40, right: 260, bottom: 80, width: 240, height: 40 }; },
    contains(node) { return node === this; }, closest() { return initial.dialog ?? null; } };
  const menuElement = { name: "menu", contains(node) { return node === this || [...optionElements.values()].includes(node); },
    querySelectorAll() { return [...optionElements.values()]; }, querySelector() { return [...optionElements.values()].find(node => node.selected) ?? null; } };
  const window = { innerWidth: 390, innerHeight: 844,
    addEventListener(name, fn) { if (!windowListeners.has(name)) windowListeners.set(name, new Set()); windowListeners.get(name).add(fn); },
    removeEventListener(name, fn) { windowListeners.get(name)?.delete(fn); } };
  for (const [name, value] of Object.entries({ document, window })) Object.defineProperty(globalThis, name, { configurable: true, writable: true, value });
  const react = { ...require("react"), useState(initialValue) {
    const index = stateIndex++; if (!(index in states)) states[index] = typeof initialValue === "function" ? initialValue() : initialValue;
    return [states[index], next => { const value = typeof next === "function" ? next(states[index]) : next;
      if (!Object.is(value, states[index])) { states[index] = value; dirty = true; } }];
  }, useRef(value) { return refs[refIndex++] ??= { current: value }; }, useId() { return "filter-harness-id"; },
    useLayoutEffect: effect, useEffect: effect };
  function effect(callback, deps) {
    const index = effectIndex++, previous = slots[index];
    if (!previous || !deps || deps.some((value, i) => !Object.is(value, previous.deps?.[i]))) {
      pending.push(() => { previous?.cleanup?.(); slots[index] = { deps, cleanup: callback() }; });
    }
  }
  const Component = load(name => name === "react" ? react : name === "react-dom" ? { createPortal(child, target) { portals.push(target); return child; } } : require(name));
  const render = next => {
    if (next) props = { ...props, ...next };
    let tree, iterations = 0;
    do {
      dirty = false; stateIndex = refIndex = effectIndex = 0; pending.length = 0;
      tree = Component(props);
      const trigger = find(tree, node => node.props?.role === "combobox")[0]; trigger.props.ref.current = triggerElement;
      const menu = find(tree, node => node.props?.role === "listbox")[0];
      if (menu) {
        menu.props.ref.current = menuElement;
        const visible = new Set();
        for (const button of find(menu, node => node.props?.role === "option")) {
          const key = String(button.key), element = optionElements.get(key) ?? { key, focus() { document.activeElement = this; } };
          element.selected = button.props["aria-selected"]; optionElements.set(key, element); visible.add(key);
        }
        for (const key of optionElements.keys()) if (!visible.has(key)) optionElements.delete(key);
      } else { refs[1].current = null; }
      pending.splice(0).forEach(run => run());
      assert.ok(++iterations < 8, "hook updates must settle");
    } while (dirty);
    return tree;
  };
  return { render, changes, portals, document, triggerElement, listeners,
    dispatch(name, target) { for (const listener of listeners.get(name) ?? []) listener({ target }); },
    restore() { slots.forEach(slot => slot.cleanup?.()); for (const name of names) { const descriptor = descriptors.get(name);
      if (descriptor) Object.defineProperty(globalThis, name, descriptor); else delete globalThis[name]; } },
  };
}

const trigger = tree => find(tree, node => node.props?.role === "combobox")[0];
const menu = tree => find(tree, node => node.props?.role === "listbox")[0];
const option = (tree, name) => find(menu(tree), node => node.props?.role === "option" && text(node).replace("✓", "") === name)[0];
function key(value, extra = {}) { let prevented = false, stopped = false;
  return { key: value, nativeEvent: { isComposing: false }, preventDefault() { prevented = true; }, stopPropagation() { stopped = true; },
    get prevented() { return prevented; }, get stopped() { return stopped; }, ...extra }; }

test("two option clicks stay expanded, use exact values, support deselection, and all clears once", () => {
  const h = harness();
  try {
    let tree = h.render(); trigger(tree).props.onClick(); tree = h.render();
    assert.equal(menu(tree).props["aria-multiselectable"], "true");
    option(tree, "A,B").props.onClick(); tree = h.render();
    assert.equal(trigger(tree).props["aria-expanded"], true); assert.equal(h.changes.at(-1), "A,B");
    option(tree, "中关村").props.onClick(); tree = h.render();
    assert.equal(trigger(tree).props["aria-expanded"], true); assert.deepEqual(h.changes.at(-1), ["A,B", "中关村"]);
    assert.equal(option(tree, "A,B").props["aria-selected"], true); assert.equal(option(tree, "中关村").props["aria-selected"], true);
    option(tree, "A,B").props.onClick(); tree = h.render(); assert.equal(h.changes.at(-1), "中关村");
    option(tree, "全部").props.onClick(); tree = h.render(); assert.equal(h.changes.at(-1), "");
    assert.equal(option(tree, "全部").props["aria-selected"], true); assert.equal(trigger(tree).props["aria-expanded"], true);
  } finally { h.restore(); }
});

test("Escape returns focus, outside pointer and focus close without discarding the selection", () => {
  const h = harness({ value: ["A,B", "中关村"] });
  try {
    let tree = h.render(); trigger(tree).props.onClick(); tree = h.render();
    const escape = key("Escape"); menu(tree).props.onKeyDown(escape); tree = h.render();
    assert.equal(menu(tree), undefined); assert.equal(h.document.activeElement, h.triggerElement); assert.equal(escape.prevented, true); assert.equal(escape.stopped, true);
    trigger(tree).props.onClick(); tree = h.render(); h.dispatch("pointerdown", h.triggerElement); tree = h.render(); assert.ok(menu(tree));
    h.dispatch("pointerdown", { name: "outside" }); tree = h.render(); assert.equal(menu(tree), undefined);
    trigger(tree).props.onClick(); tree = h.render(); h.dispatch("focusin", { name: "other-control" }); tree = h.render(); assert.equal(menu(tree), undefined);
    assert.deepEqual(h.changes, []); assert.equal(trigger(tree).props.title, "A,B、中关村");
  } finally { h.restore(); }
});

test("keyboard arrows and Home/End move option focus, while text composition and modified shortcuts are untouched", () => {
  const h = harness();
  try {
    let tree = h.render(); const down = key("ArrowDown"); trigger(tree).props.onKeyDown(down); tree = h.render(); assert.equal(down.prevented, true);
    assert.equal(h.document.activeElement.key, "");
    menu(tree).props.onKeyDown(key("ArrowDown")); assert.equal(h.document.activeElement.key, "A,B");
    menu(tree).props.onKeyDown(key("End")); assert.equal(h.document.activeElement.key, "西直门");
    menu(tree).props.onKeyDown(key("ArrowDown")); assert.equal(h.document.activeElement.key, "");
    menu(tree).props.onKeyDown(key("ArrowUp")); assert.equal(h.document.activeElement.key, "西直门");
    menu(tree).props.onKeyDown(key("Home")); assert.equal(h.document.activeElement.key, "");
    for (const event of [key("End", { nativeEvent: { isComposing: true } }), key("ArrowDown", { ctrlKey: true }), key("ArrowDown", { metaKey: true }), key("ArrowDown", { altKey: true })]) {
      menu(tree).props.onKeyDown(event); assert.equal(event.prevented, false); assert.equal(h.document.activeElement.key, "");
    }
    const tab = key("Tab"); menu(tree).props.onKeyDown(tab); tree = h.render(); assert.equal(menu(tree), undefined); assert.equal(tab.prevented, false);
    assert.deepEqual(h.changes, [], "navigation must not select or save a value");
  } finally { h.restore(); }
});

test("disabling an open filter closes its menu and unavailable selected options remain visible when reopened", () => {
  const h = harness({ value: ["停用校区", "A,B"] });
  try {
    let tree = h.render(); trigger(tree).props.onClick(); tree = h.render();
    assert.equal(option(tree, "停用校区").props["aria-selected"], true);
    tree = h.render({ disabled: true }); assert.equal(trigger(tree).props.disabled, true); assert.equal(menu(tree), undefined);
    assert.equal(trigger(tree).props.title, "停用校区、A,B"); assert.deepEqual(h.changes, []);
  } finally { h.restore(); }
});

test("filter menus mount inside their nearest modal dialog and otherwise use the document layer", () => {
  for (const dialog of [null, { name: "containing-dialog" }]) {
    const h = harness({ dialog });
    try {
      let tree = h.render(); trigger(tree).props.onClick(); tree = h.render(); assert.ok(menu(tree));
      assert.ok(h.portals.length > 0);
      assert.ok(h.portals.every(target => target === (dialog ?? h.document.body)), "a body portal cannot escape a native modal top layer");
    } finally { h.restore(); }
  }
});
