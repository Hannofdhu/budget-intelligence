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
  const changes = [], portals = [], optionElements = new Map(), focusCalls = [], mountStyles = [];
  let stateIndex = 0, refIndex = 0, effectIndex = 0, dirty = false, props;
  const onChange = value => { changes.push(value); props = { ...props, value }; dirty = true; };
  props = { value: "", options: ["A,B", "中关村", "西直门"], "aria-label": "筛选校区", onChange, ...initial };
  const document = { body: { name: "document-body" }, activeElement: null,
    addEventListener(name, fn) { if (!listeners.has(name)) listeners.set(name, new Set()); listeners.get(name).add(fn); },
    removeEventListener(name, fn) { listeners.get(name)?.delete(fn); } };
  function focus(element, options) {
    focusCalls.push({ name: element.name ?? element.key, options, geometry: { ...menuElement.style } });
    document.activeElement = element;
    // A focus contract model, not browser layout: unguarded focus can scroll
    // both the document and its containing dialog, as the CUA reproduction did.
    if (!options?.preventScroll) { window.scrollY = 4079; document.body.scrollTop = 4079; if (initial.dialog) initial.dialog.scrollTop = 4079; }
  }
  const triggerElement = { name: "trigger", focus(options) { focus(this, options); },
    getBoundingClientRect() { return initial.triggerRect ?? { left: 20, top: 40, right: 260, bottom: 80, width: 240, height: 40 }; },
    contains(node) { return node === this; }, closest() { return initial.dialog ?? null; } };
  const menuElement = { name: "menu", style: {}, scrollTop: 0, clientTop: 1,
    get clientHeight() { return Math.min(initial.menuHeight ?? 320, Number.parseFloat(this.style.maxHeight ?? "320"), optionElements.size * 36); },
    getBoundingClientRect() {
      const top = this.style.top && this.style.top !== "auto" ? Number.parseFloat(this.style.top)
        : window.innerHeight - Number.parseFloat(this.style.bottom ?? "0") - this.clientHeight;
      return { top, bottom: top + this.clientHeight, height: this.clientHeight };
    },
    contains(node) { return node === this || [...optionElements.values()].includes(node); },
    querySelectorAll() { return [...optionElements.values()]; }, querySelector() { return [...optionElements.values()].find(node => node.selected) ?? null; } };
  const window = { innerWidth: 390, innerHeight: 844, scrollY: 640,
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
        mountStyles.push({ ...menu.props.style }); Object.assign(menuElement.style, menu.props.style);
        menu.props.ref.current = menuElement;
        const visible = new Set();
        for (const button of find(menu, node => node.props?.role === "option")) {
          const key = String(button.key), element = optionElements.get(key) ?? { key, focus(options) { focus(this, options); },
            getBoundingClientRect() {
              const index = [...optionElements.keys()].indexOf(key);
              const top = menuElement.getBoundingClientRect().top + menuElement.clientTop + index * 36 - menuElement.scrollTop;
              return { top, bottom: top + 36, height: 36 };
            } };
          element.selected = button.props["aria-selected"]; optionElements.set(key, element); visible.add(key);
        }
        for (const key of optionElements.keys()) if (!visible.has(key)) optionElements.delete(key);
      } else { refs[1].current = null; }
      pending.splice(0).forEach(run => run());
      assert.ok(++iterations < 8, "hook updates must settle");
    } while (dirty);
    return tree;
  };
  return { render, changes, portals, document, window, triggerElement, menuElement, listeners, focusCalls, mountStyles,
    dispatch(name, target) { for (const listener of listeners.get(name) ?? []) listener({ target }); },
    dispatchWindow(name) { for (const listener of windowListeners.get(name) ?? []) listener(); },
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

test("first open positions a fixed popup before focus and opening/reopening/closing never requests page scrolling", () => {
  for (const dialog of [null, { name: "containing-dialog", scrollTop: 135 }]) {
    const h = harness({ dialog });
    try {
      let tree = h.render();
      for (let i = 0; i < 2; i++) {
        trigger(tree).props.onClick(); tree = h.render();
        assert.equal(h.mountStyles.at(-1).position, "fixed", "the first DOM commit must not append an in-flow menu");
        const call = h.focusCalls.at(-1);
        assert.equal(call.geometry.top, "84px", "placement must be applied before initial focus, not in a later commit");
        assert.deepEqual(call.options, { preventScroll: true });
        menu(tree).props.onKeyDown(key("Escape")); tree = h.render();
      }
      assert.equal(h.window.scrollY, 640); assert.equal(h.document.body.scrollTop, undefined);
      if (dialog) assert.equal(dialog.scrollTop, 135);
      assert.ok(h.focusCalls.every(call => call.options?.preventScroll));
      assert.deepEqual(h.changes, []);
    } finally { h.restore(); }
  }
});

test("initial last selection and Home/End scroll only the listbox; resize clears the old placement side", () => {
  const options = Array.from({ length: 30 }, (_, i) => `校区${i}`), h = harness({ options, value: "校区29", menuHeight: 144 });
  try {
    let tree = h.render(); trigger(tree).props.onClick(); tree = h.render();
    assert.equal(h.document.activeElement.key, "校区29"); assert.ok(h.menuElement.scrollTop > 0);
    assert.equal(h.window.scrollY, 640);
    menu(tree).props.onKeyDown(key("Home")); assert.equal(h.menuElement.scrollTop, 0);
    menu(tree).props.onKeyDown(key("End")); assert.ok(h.menuElement.scrollTop > 0);
    assert.equal(h.window.scrollY, 640); assert.ok(h.focusCalls.every(call => call.options?.preventScroll));
    h.window.innerHeight = 250; h.dispatchWindow("resize");
    assert.equal(h.menuElement.style.maxHeight, "158px", "a downward menu may not use the larger space above its trigger");
    h.window.innerHeight = 100; h.dispatchWindow("resize");
    assert.equal(h.menuElement.style.top, "auto"); assert.notEqual(h.menuElement.style.bottom, "auto");
    h.window.innerHeight = 844; h.dispatchWindow("resize");
    assert.equal(h.menuElement.style.top, "84px"); assert.equal(h.menuElement.style.bottom, "auto");
    assert.deepEqual(h.changes, []);
  } finally { h.restore(); }
});
