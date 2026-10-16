// Real component callbacks with synthetic hook/element refs. Native top-layer
// placement, light-dismiss, autofocus and page scrolling require browser testing.
import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

const require = createRequire(import.meta.url);
const compiled = await build({ entryPoints: [fileURLToPath(new URL("../src/ForecastGroupSelector.tsx", import.meta.url))],
  bundle: true, write: false, platform: "node", format: "cjs", jsx: "automatic", external: ["react", "react-dom"], logLevel: "silent" });
const load = resolve => {
  const loaded = { exports: {} };
  new Function("require", "module", "exports", compiled.outputFiles[0].text)(resolve, loaded, loaded.exports);
  return loaded.exports.ForecastGroupSelector;
};
const find = (node, predicate) => Array.isArray(node) ? node.flatMap(child => find(child, predicate))
  : !node || typeof node !== "object" ? [] : [...(predicate(node) ? [node] : []), ...find(node.props?.children, predicate)];
const text = node => Array.isArray(node) ? node.map(text).join("") : node && typeof node === "object" ? text(node.props?.children)
  : node == null || typeof node === "boolean" ? "" : String(node);
const trigger = tree => find(tree, node => node.type === "button" && node.props.popoverTarget)[0];
const panel = tree => find(tree, node => node.props.popover === "auto")[0];
const checkbox = (tree, name) => find(tree, node => node.type === "label" && text(node) === name)
  .flatMap(node => find(node, child => child.type === "input" && child.props.type === "checkbox"))[0];
const button = (tree, label) => find(tree, node => node.type === "button" && node.props["aria-label"] === label)[0];
const groupOrder = tree => find(tree, node => node.type === "li").map(node => text(find(node, child => child.type === "span")[0]));
const dimensionLabels = { track: "赛道", product: "产品", subject: "科目", grade: "年级", campus: "校区", season: "季度" };

function harness(initial = {}) {
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, "window");
  const states = [], refs = [], effects = [], pending = [], changes = [], focusCalls = [], mountStyles = [], removed = [];
  const listeners = new Map(), handles = new Map(), pointerEvents = [], keyEvents = [], frames = new Map(), canceledFrames = [];
  let frameId = 0;
  let stateIndex = 0, refIndex = 0, effectIndex = 0, dirty = false, tree, hides = 0;
  let props = { value: ["track"], disabled: false, ...initial };
  props.onChange = value => { changes.push(value); props = { ...props, value }; dirty = true; };
  const window = { innerWidth: 1280, innerHeight: 720,
    requestAnimationFrame(callback) { const id = ++frameId; frames.set(id, callback); return id; },
    cancelAnimationFrame(id) { frames.delete(id); canceledFrames.push(id); },
    addEventListener(name, handler, capture) {
      if (!listeners.has(name)) listeners.set(name, new Set());
      listeners.get(name).add({ handler, capture });
    },
    removeEventListener(name, handler, capture) {
      removed.push({ name, handler, capture });
      for (const entry of listeners.get(name) ?? []) if (entry.handler === handler && entry.capture === capture) listeners.get(name).delete(entry);
    },
  };
  Object.defineProperty(globalThis, "window", { configurable: true, writable: true, value: window });
  const triggerElement = { rect: { left: 860, right: 1240, top: 100, bottom: 148 },
    getBoundingClientRect() { return this.rect; }, focus(options) { focusCalls.push(options); } };
  const panelElement = { style: {}, rect: { left: 0, right: 300, top: 150, bottom: 500 }, clientHeight: 350, scrollHeight: 350,
    _scrollTop: 0, get scrollTop() { return this._scrollTop; },
    set scrollTop(value) { this._scrollTop = Math.max(0, Math.min(value, this.scrollHeight - this.clientHeight)); },
    getBoundingClientRect() { return this.rect; }, hidePopover() {
    hides++;
    // Model only the notification a browser sends after hidePopover, not the
    // native state machine, top layer, autofocus or page-scroll behavior.
    panel(tree)?.props.onToggle({ nativeEvent: { newState: "closed" } });
  } };
  // Deliberately synthetic geometry/capture only; actual mouse/touch hit testing,
  // capture delivery and native popup dismissal are checked in the browser.
  const listElement = { children: [], getBoundingClientRect() {
    return { left: 0, right: 300, top: 200 - panelElement.scrollTop, bottom: 200 + this.children.length * 44 - panelElement.scrollTop };
  } };
  const handle = name => {
    if (!handles.has(name)) handles.set(name, { captured: new Set(), captures: [], releases: [], focusCalls: [],
      focus(options) { this.focusCalls.push(options); },
      setPointerCapture(id) { this.captured.add(id); this.captures.push(id); },
      hasPointerCapture(id) { return this.captured.has(id); },
      releasePointerCapture(id) { this.captured.delete(id); this.releases.push(id); },
    });
    return handles.get(name);
  };
  const react = { ...require("react"), useId() { return "forecast-groups-harness-id"; },
    useRef(value) { return refs[refIndex++] ??= { current: value }; },
    useState(initialValue) {
      const index = stateIndex++;
      if (!(index in states)) states[index] = typeof initialValue === "function" ? initialValue() : initialValue;
      return [states[index], next => {
        const value = typeof next === "function" ? next(states[index]) : next;
        if (!Object.is(value, states[index])) { states[index] = value; dirty = true; }
      }];
    },
    useEffect(callback, deps) {
      const index = effectIndex++, previous = effects[index];
      if (!previous || !deps || deps.some((value, i) => !Object.is(value, previous.deps?.[i])))
        pending.push(() => { previous?.cleanup?.(); effects[index] = { deps, cleanup: callback() }; });
    },
  };
  const Component = load(name => name === "react" ? react : require(name));
  const render = next => {
    if (next) props = { ...props, ...next };
    let attempts = 0;
    do {
      dirty = false; stateIndex = refIndex = effectIndex = 0; pending.length = 0;
      tree = Component(props);
      trigger(tree).props.ref.current = triggerElement;
      const menu = panel(tree); menu.props.ref.current = panelElement;
      const list = find(tree, node => node.type === "ol")[0];
      if (list?.props.ref) {
        list.props.ref.current = listElement;
        listElement.children = find(list, node => node.type === "li").map((_, index) => ({
          getBoundingClientRect() { return { left: 0, right: 300, top: 200 + index * 44 - panelElement.scrollTop, bottom: 244 + index * 44 - panelElement.scrollTop }; },
        }));
      }
      mountStyles.push({ ...menu.props.style }); Object.assign(panelElement.style, menu.props.style);
      pending.splice(0).forEach(run => run());
      assert.ok(++attempts < 8, "controlled updates must settle");
    } while (dirty);
    return tree;
  };
  const unmount = () => effects.forEach(effect => { effect.cleanup?.(); effect.cleanup = undefined; });
  return { render, changes, focusCalls, mountStyles, triggerElement, panelElement, listElement, window, listeners, removed,
    handle, pointerEvents, keyEvents, unmount, frames, canceledFrames,
    get hides() { return hides; },
    open() {
      panel(tree).props.onBeforeToggle({ nativeEvent: { newState: "open" } });
      panel(tree).props.onToggle({ nativeEvent: { newState: "open" } });
      return render();
    },
    choose(name, checked) {
      const input = checkbox(tree, name), fieldset = find(tree, node => node.type === "fieldset")[0];
      if (!input.props.disabled && !fieldset.props.disabled) input.props.onChange({ target: { checked } });
      return render();
    },
    pointer(name, callback, options = {}) {
      const index = props.value.findIndex(key => dimensionLabels[key] === name);
      const event = { pointerId: 1, clientX: 100, clientY: 222 + index * 44,
        button: 0, isPrimary: true, pointerType: "mouse", currentTarget: handle(name), prevented: false,
        preventDefault() { this.prevented = true; }, ...options };
      pointerEvents.push(event);
      button(tree, `拖动排序${name}`).props[callback](event);
      return render();
    },
    key(name, key) {
      const event = { key, prevented: false, stopped: false,
        preventDefault() { this.prevented = true; }, stopPropagation() { this.stopped = true; } };
      keyEvents.push(event); button(tree, `拖动排序${name}`).props.onKeyDown(event); return render();
    },
    frame() {
      const next = frames.entries().next().value;
      if (next) { const [id, callback] = next; frames.delete(id); callback(0); }
      return render();
    },
    dispatch(name) { for (const { handler } of listeners.get(name) ?? []) handler(); },
    restore() {
      unmount();
      if (descriptor) Object.defineProperty(globalThis, "window", descriptor); else delete globalThis.window;
    },
  };
}

test("one settings button owns one native popover with all six dimensions and no extra editing workflow", () => {
  const Component = load(require);
  const html = renderToStaticMarkup(createElement(Component, { value: ["track", "grade"], onChange() {} }));
  assert.equal((html.match(/popovertarget=/gi) ?? []).length, 1);
  assert.equal((html.match(/popover="auto"/g) ?? []).length, 1);
  assert.equal((html.match(/type="checkbox"/g) ?? []).length, 6);
  assert.match(html, /aria-label="分组维度：赛道、年级，设置"/);
  assert.match(html, /选择后即时更新，仅改变展示/);
  assert.match(html, /拖动调整列顺序，置顶后显示在第一列/);
  assert.match(html, /aria-label="拖动排序赛道"/);
  assert.match(html, /aria-label="置顶年级"/);
  assert.doesNotMatch(html, /向左移动|向右移动|←|→/);
  assert.doesNotMatch(html, /<form|保存预算|确认预测|工作台|保存新版本/);
  const h = harness();
  try {
    const tree = h.render(), target = trigger(tree), popup = panel(tree);
    assert.equal(target.props.popoverTarget, popup.props.id);
    assert.equal(target.props["aria-controls"], popup.props.id);
    assert.equal(target.props["aria-expanded"], false);
    assert.equal(popup.props.style.position, "fixed", "the first commit cannot append an in-flow panel");
    assert.deepEqual(find(popup, node => node.type === "label").map(text), ["赛道", "产品", "科目", "年级", "校区", "季度"]);
    assert.equal(find(tree, node => node.type === "button" && text(node) === "完成").length, 1);
  } finally { h.restore(); }
});

test("selecting and removing dimensions invokes controlled callbacks in order while the final dimension stays disabled", () => {
  const original = Object.freeze(["track"]), h = harness({ value: original });
  try {
    let tree = h.render();
    assert.equal(checkbox(tree, "赛道").props.checked, true);
    assert.equal(checkbox(tree, "赛道").props.disabled, true);
    tree = h.choose("赛道", false); assert.deepEqual(h.changes, []);
    tree = h.choose("年级", true);
    assert.deepEqual(h.changes.at(-1), ["track", "grade"]);
    assert.equal(checkbox(tree, "赛道").props.disabled, false);
    tree = h.choose("季度", true); assert.deepEqual(h.changes.at(-1), ["track", "grade", "season"]);
    assert.equal(checkbox(tree, "季度").props.checked, true);
    tree = h.choose("赛道", false); assert.deepEqual(h.changes.at(-1), ["grade", "season"]);
    tree = h.choose("季度", false); assert.deepEqual(h.changes.at(-1), ["grade"]);
    assert.equal(checkbox(tree, "年级").props.disabled, true);
    h.choose("年级", false); assert.deepEqual(h.changes.at(-1), ["grade"]);
    assert.deepEqual(original, ["track"], "selection must not mutate caller-owned values");
  } finally { h.restore(); }
});

test("pinning moves a dimension to the first column while preserving every other relative position", () => {
  const original = Object.freeze(["track", "grade", "subject", "campus"]), h = harness({ value: original });
  try {
    let tree = h.render();
    assert.equal(button(tree, "置顶赛道").props.disabled, true);
    assert.equal(text(button(tree, "置顶赛道")), "已在首位");
    button(tree, "置顶赛道").props.onClick(); assert.deepEqual(h.changes, []);
    button(tree, "置顶科目").props.onClick(); tree = h.render();
    assert.deepEqual(h.changes, [["subject", "track", "grade", "campus"]]);
    assert.equal(trigger(tree).props["aria-label"], "分组维度：科目、赛道、年级、校区，设置");
    assert.deepEqual(groupOrder(tree), ["1. 科目", "2. 赛道", "3. 年级", "4. 校区"]);
    assert.match(text(find(tree, node => node.props.role === "status")[0]), /科目已置顶，显示在第一列/);
    button(tree, "置顶科目").props.onClick(); h.render(); assert.equal(h.changes.length, 1);
    assert.deepEqual(original, ["track", "grade", "subject", "campus"]);
  } finally { h.restore(); }
});

for (const pointerType of ["mouse", "touch"]) test(`${pointerType} drag inserts across multiple rows and commits only once on drop`, () => {
  const original = Object.freeze(["track", "grade", "subject", "campus"]), h = harness({ value: original });
  try {
    h.render(); h.open();
    let tree = h.pointer("赛道", "onPointerDown", { pointerType });
    assert.deepEqual(h.handle("赛道").captures, [1]);
    assert.deepEqual(h.handle("赛道").focusCalls, [{ preventScroll: true }]);
    assert.equal(h.pointerEvents.at(-1).prevented, true);
    assert.equal(button(tree, "拖动排序赛道").props["aria-pressed"], true);
    tree = h.pointer("赛道", "onPointerMove", { pointerType, clientY: 310 });
    assert.equal(find(tree, node => node.type === "li")[2].props["data-drop-position"], "after");
    assert.deepEqual(h.changes, [], "pointer previews cannot write URL/history or controlled state");
    assert.deepEqual(groupOrder(tree), ["1. 赛道", "2. 年级", "3. 科目", "4. 校区"]);
    tree = h.pointer("赛道", "onPointerUp", { pointerType, clientY: 354 });
    assert.deepEqual(h.changes, [["grade", "subject", "campus", "track"]]);
    assert.deepEqual(groupOrder(tree), ["1. 年级", "2. 科目", "3. 校区", "4. 赛道"]);
    assert.equal(button(tree, "拖动排序赛道").props["aria-pressed"], false);
    assert.deepEqual(h.handle("赛道").releases, [1]);
    h.pointer("赛道", "onPointerUp", { pointerType, clientY: 222 });
    assert.equal(h.changes.length, 1, "duplicate up cannot commit the released session");
    h.pointer("校区", "onPointerDown", { pointerType });
    tree = h.pointer("校区", "onPointerMove", { pointerType, clientY: 222 });
    assert.equal(find(tree, node => node.type === "li")[0].props["data-drop-position"], "before");
    h.pointer("校区", "onPointerUp", { pointerType, clientY: 222 });
    assert.deepEqual(h.changes.at(-1), ["campus", "grade", "subject", "track"]);
    assert.deepEqual(original, ["track", "grade", "subject", "campus"]);
  } finally { h.restore(); }
});

test("busy state disables the trigger, native checkbox fieldset, drag and pin, and dismisses an open panel", () => {
  const h = harness({ value: ["track", "grade"] });
  try {
    let tree = h.render(); tree = h.open();
    assert.equal(trigger(tree).props["aria-expanded"], true);
    const hidesBefore = h.hides; tree = h.render({ disabled: true });
    assert.equal(h.hides, hidesBefore + 1);
    assert.equal(trigger(tree).props.disabled, true);
    assert.equal(trigger(tree).props["aria-expanded"], false);
    assert.equal(find(tree, node => node.type === "fieldset")[0].props.disabled, true);
    const operations = find(tree, node => node.type === "button" && /拖动排序|置顶/.test(node.props["aria-label"] ?? ""));
    assert.equal(operations.length, 4); assert.ok(operations.every(node => node.props.disabled));
    h.choose("季度", true); button(tree, "置顶年级").props.onClick();
    h.pointer("赛道", "onPointerDown"); h.key("赛道", " ");
    assert.deepEqual(h.changes, []);
    assert.deepEqual(h.handle("赛道").captures, []);
    tree = h.render({ disabled: false }); assert.equal(trigger(tree).props.disabled, false);
    assert.equal(find(tree, node => node.type === "fieldset")[0].props.disabled, false);
  } finally { h.restore(); }
});

test("drag ignores non-primary buttons and foreign pointers, and does not reorder a click or same-position drop", () => {
  const h = harness({ value: ["track", "grade", "subject"] });
  try {
    h.render(); h.open();
    h.pointer("赛道", "onPointerDown", { button: 2 });
    h.pointer("赛道", "onPointerDown", { isPrimary: false });
    assert.deepEqual(h.handle("赛道").captures, []);
    h.pointer("赛道", "onPointerDown");
    h.pointer("赛道", "onPointerMove", { pointerId: 2, clientY: 310 });
    h.pointer("赛道", "onPointerUp", { pointerId: 2, clientY: 310 });
    h.pointer("赛道", "onPointerCancel", { pointerId: 2 });
    h.pointer("赛道", "onLostPointerCapture", { pointerId: 2 });
    assert.equal(h.handle("赛道").hasPointerCapture(1), true);
    h.pointer("赛道", "onPointerUp", { clientY: 224 });
    assert.deepEqual(h.changes, [], "movement below four pixels is a click");
    h.pointer("年级", "onPointerDown");
    h.pointer("年级", "onPointerMove", { clientY: 273 });
    h.pointer("年级", "onPointerUp", { clientY: 273 });
    assert.deepEqual(h.changes, [], "movement inside the same destination is a no-op");
  } finally { h.restore(); }
});

test("edge dragging scrolls only the popup so a sixth dimension remains reachable and release cancels the animation", () => {
  const h = harness({ value: ["track", "grade", "subject", "campus", "product", "season"] });
  try {
    h.render(); h.open();
    h.panelElement.rect = { left: 0, right: 300, top: 150, bottom: 350 };
    h.panelElement.clientHeight = 200; h.panelElement.scrollHeight = 600;
    h.pointer("赛道", "onPointerDown");
    h.pointer("赛道", "onPointerMove", { clientY: 340 });
    assert.equal(h.panelElement.scrollTop, 8); assert.equal(h.frames.size, 1);
    for (let i = 0; i < 10; i++) h.frame();
    assert.equal(h.panelElement.scrollTop, 88); assert.deepEqual(h.changes, []);
    h.pointer("赛道", "onPointerUp", { clientY: 340 });
    assert.deepEqual(h.changes, [["grade", "subject", "campus", "product", "season", "track"]]);
    assert.equal(h.frames.size, 0); assert.ok(h.canceledFrames.length > 0);
    h.pointer("季度", "onPointerDown", { clientY: 310 });
    h.pointer("季度", "onPointerMove", { clientY: 160 });
    assert.equal(h.panelElement.scrollTop, 80);
    for (let i = 0; i < 10; i++) h.frame();
    assert.equal(h.panelElement.scrollTop, 0, "upward edge scrolling clamps at the popup boundary");
    h.pointer("季度", "onPointerUp", { clientY: 222 });
    assert.deepEqual(h.changes.at(-1), ["season", "grade", "subject", "campus", "product", "track"]);
    assert.equal(h.frames.size, 0);
    assert.deepEqual(h.focusCalls, [], "edge scrolling must not request trigger/page focus");
  } finally { h.restore(); }
});

for (const reason of ["outside", "cancel", "disabled", "unmount"])
  test(`edge animation stops after ${reason} without committing preview order`, () => {
    const h = harness({ value: ["track", "grade", "subject"] });
    try {
      h.render(); h.open();
      h.panelElement.rect = { left: 0, right: 300, top: 150, bottom: 350 };
      h.panelElement.clientHeight = 200; h.panelElement.scrollHeight = 600;
      h.pointer("赛道", "onPointerDown"); h.pointer("赛道", "onPointerMove", { clientY: 340 });
      assert.equal(h.frames.size, 1);
      if (reason === "outside") { h.pointer("赛道", "onPointerMove", { clientX: 301, clientY: 340 }); h.frame(); }
      if (reason === "cancel") h.pointer("赛道", "onPointerCancel");
      if (reason === "disabled") h.render({ disabled: true });
      if (reason === "unmount") h.unmount();
      const scrollTop = h.panelElement.scrollTop;
      assert.equal(h.frames.size, 0); h.frame(); assert.equal(h.panelElement.scrollTop, scrollTop);
      assert.deepEqual(h.changes, []);
    } finally { h.restore(); }
  });

test("a pointer over clipped rows outside the visible popup cannot select or commit them", () => {
  const h = harness({ value: ["track", "grade", "subject", "campus"] });
  try {
    h.render(); h.open(); h.panelElement.rect = { left: 0, right: 300, top: 150, bottom: 300 };
    h.pointer("赛道", "onPointerDown");
    const tree = h.pointer("赛道", "onPointerMove", { clientY: 354 });
    assert.ok(find(tree, node => node.type === "li").every(node => !node.props["data-drop-position"]));
    h.pointer("赛道", "onPointerUp", { clientY: 354 });
    assert.deepEqual(h.changes, []); assert.equal(h.frames.size, 0);
  } finally { h.restore(); }
});

for (const reason of ["pointer-cancel", "lost-capture", "Escape", "outside", "light-dismiss", "disabled", "external-order", "unmount"])
  test(`an in-progress drag is safely abandoned after ${reason}`, () => {
    const h = harness({ value: ["track", "grade", "subject"] });
    try {
      h.render(); h.open(); h.pointer("赛道", "onPointerDown");
      let tree = h.pointer("赛道", "onPointerMove", { clientY: 310 });
      assert.equal(button(tree, "拖动排序赛道").props["aria-pressed"], true);
      if (reason === "pointer-cancel") tree = h.pointer("赛道", "onPointerCancel");
      if (reason === "lost-capture") tree = h.pointer("赛道", "onLostPointerCapture");
      if (reason === "Escape") tree = h.key("赛道", "Escape");
      if (reason === "outside") tree = h.pointer("赛道", "onPointerUp", { clientX: 301, clientY: 310 });
      if (reason === "light-dismiss") {
        panel(tree).props.onToggle({ nativeEvent: { newState: "closed" } }); tree = h.render();
      }
      if (reason === "disabled") tree = h.render({ disabled: true });
      if (reason === "external-order") tree = h.render({ value: ["subject", "grade", "track"] });
      if (reason === "unmount") h.unmount();
      assert.deepEqual(h.changes, []);
      assert.equal(h.handle("赛道").hasPointerCapture(1), false);
      assert.deepEqual(h.handle("赛道").releases, [1]);
      if (reason !== "unmount") {
        assert.equal(button(tree, "拖动排序赛道").props["aria-pressed"], false);
        h.pointer("赛道", "onPointerUp", { clientY: 310 });
        assert.deepEqual(h.changes, [], "late pointer-up cannot revive a canceled or stale drag");
      }
    } finally { h.restore(); }
  });

test("selection and pin actions cancel drag preview before applying their single controlled change", () => {
  const h = harness({ value: ["track", "grade", "subject"] });
  try {
    h.render(); h.open(); h.pointer("赛道", "onPointerDown"); h.pointer("赛道", "onPointerMove", { clientY: 310 });
    h.choose("季度", true);
    assert.deepEqual(h.changes, [["track", "grade", "subject", "season"]]);
    assert.equal(h.handle("赛道").hasPointerCapture(1), false);
    h.pointer("年级", "onPointerDown");
    let tree = h.pointer("年级", "onPointerMove", { clientY: 354 });
    button(tree, "置顶科目").props.onClick(); tree = h.render();
    assert.deepEqual(h.changes.at(-1), ["subject", "track", "grade", "season"]);
    assert.equal(h.changes.length, 2);
    assert.equal(h.handle("年级").hasPointerCapture(1), false);
    assert.equal(button(tree, "拖动排序年级").props["aria-pressed"], false);
  } finally { h.restore(); }
});

test("keyboard lift, Up/Down and Home/End preview boundaries then Space or Enter commit a stable insertion", () => {
  const original = Object.freeze(["track", "grade", "subject", "campus"]), h = harness({ value: original });
  try {
    h.render(); h.open();
    let tree = h.key("年级", " ");
    assert.equal(button(tree, "拖动排序年级").props["aria-pressed"], true);
    assert.ok(h.keyEvents.at(-1).prevented && h.keyEvents.at(-1).stopped);
    h.key("年级", "ArrowUp"); tree = h.key("年级", "ArrowUp");
    assert.equal(find(tree, node => node.type === "li")[0].props["data-drop-position"], "before");
    h.key("年级", "ArrowDown"); h.key("年级", "End"); tree = h.key("年级", "ArrowDown");
    assert.equal(find(tree, node => node.type === "li")[3].props["data-drop-position"], "after");
    assert.deepEqual(h.changes, [], "keyboard previews cannot mutate controlled state");
    tree = h.key("年级", " ");
    assert.deepEqual(h.changes, [["track", "subject", "campus", "grade"]]);
    assert.equal(button(tree, "拖动排序年级").props["aria-pressed"], false);
    h.key("年级", "Enter"); h.key("年级", "Home"); tree = h.key("年级", "Enter");
    assert.deepEqual(h.changes.at(-1), ["grade", "track", "subject", "campus"]);
    assert.equal(h.changes.length, 2);
    assert.match(text(find(tree, node => node.props.role === "status")[0]), /年级已移到第1位/);
    assert.deepEqual(original, ["track", "grade", "subject", "campus"]);
  } finally { h.restore(); }
});

test("keyboard Escape cancels only sorting without dismissing the panel, blur cancels, and one dimension cannot lift", () => {
  const h = harness({ value: ["track", "grade"] });
  try {
    h.render(); h.open(); h.key("年级", " "); h.key("年级", "Home");
    let tree = h.key("年级", "Escape");
    assert.deepEqual(h.changes, []); assert.equal(h.hides, 0);
    assert.ok(h.keyEvents.at(-1).prevented && h.keyEvents.at(-1).stopped);
    assert.equal(trigger(tree).props["aria-expanded"], true);
    assert.equal(button(tree, "拖动排序年级").props["aria-pressed"], false);
    h.key("年级", " "); tree = h.key("年级", "Home");
    button(tree, "拖动排序年级").props.onBlur(); tree = h.render();
    assert.equal(button(tree, "拖动排序年级").props["aria-pressed"], false);
    assert.deepEqual(h.changes, []);
    tree = h.render({ value: ["track"] });
    assert.equal(button(tree, "拖动排序赛道").props.disabled, true);
    assert.equal(button(tree, "置顶赛道").props.disabled, true);
    h.key("赛道", " "); h.pointer("赛道", "onPointerDown");
    assert.deepEqual(h.changes, []); assert.deepEqual(h.handle("赛道").captures, []);
  } finally { h.restore(); }
});

test("done and Escape request native hide and return focus with preventScroll, without changing dimensions", () => {
  const h = harness({ value: ["track", "season"] });
  try {
    let tree = h.render(); tree = h.open();
    find(tree, node => node.type === "button" && text(node) === "完成")[0].props.onClick(); tree = h.render();
    assert.equal(h.hides, 1); assert.deepEqual(h.focusCalls, [{ preventScroll: true }]);
    assert.equal(trigger(tree).props["aria-expanded"], false);
    tree = h.open();
    let prevented = false, stopped = false;
    panel(tree).props.onKeyDown({ key: "Escape", preventDefault() { prevented = true; }, stopPropagation() { stopped = true; } });
    tree = h.render();
    assert.equal(prevented, true); assert.equal(stopped, true); assert.equal(h.hides, 2);
    assert.deepEqual(h.focusCalls, [{ preventScroll: true }, { preventScroll: true }]);
    assert.equal(trigger(tree).props["aria-expanded"], false);
    panel(tree).props.onKeyDown({ key: "Enter", preventDefault() { throw new Error("not Escape"); }, stopPropagation() { throw new Error("not Escape"); } });
    assert.equal(h.hides, 2); assert.deepEqual(h.changes, []);
  } finally { h.restore(); }
});

test("beforetoggle synchronously positions the first fixed panel, respecting viewport sides and available height", () => {
  const h = harness();
  try {
    const tree = h.render();
    assert.equal(h.mountStyles[0].position, "fixed");
    panel(tree).props.onBeforeToggle({ nativeEvent: { newState: "open" } });
    assert.deepEqual(h.panelElement.style, { position: "fixed", width: "360px", maxHeight: "520px", left: "880px", top: "152px", bottom: "auto" });
    assert.equal(trigger(tree).props["aria-expanded"], false, "geometry must be applied before the later toggle notification");
    assert.deepEqual(h.focusCalls, [], "the component leaves native open autofocus to the browser");
    h.window.innerWidth = 320; h.window.innerHeight = 300;
    h.triggerElement.rect = { left: 10, right: 310, top: 230, bottom: 270 };
    panel(tree).props.onBeforeToggle({ nativeEvent: { newState: "open" } });
    assert.deepEqual(h.panelElement.style, { position: "fixed", width: "288px", maxHeight: "218px", left: "16px", top: "auto", bottom: "74px" });
    h.triggerElement.rect = { left: 20, right: 180, top: 20, bottom: 60 };
    panel(tree).props.onBeforeToggle({ nativeEvent: { newState: "open" } });
    assert.equal(h.panelElement.style.maxHeight, "228px");
    assert.equal(h.panelElement.style.left, "16px");
    assert.equal(h.panelElement.style.top, "64px"); assert.equal(h.panelElement.style.bottom, "auto");
    const geometry = { ...h.panelElement.style };
    h.triggerElement.rect.top = 200;
    panel(tree).props.onBeforeToggle({ nativeEvent: { newState: "closed" } });
    assert.deepEqual(h.panelElement.style, geometry, "closing does not relocate the trigger or panel");
  } finally { h.restore(); }
});

test("resize and capturing scroll listeners exist only while open and clean up on close or unmount", () => {
  const h = harness();
  try {
    let tree = h.render();
    assert.equal(h.listeners.size, 0);
    tree = h.open();
    assert.equal(h.listeners.get("resize").size, 1); assert.equal(h.listeners.get("scroll").size, 1);
    assert.equal([...h.listeners.get("scroll")][0].capture, true);
    h.window.innerHeight = 200; h.triggerElement.rect = { left: 20, right: 300, top: 150, bottom: 190 };
    h.dispatch("resize"); assert.equal(h.panelElement.style.top, "auto"); assert.equal(h.panelElement.style.bottom, "54px");
    h.window.innerHeight = 720; h.triggerElement.rect = { left: 20, right: 300, top: 20, bottom: 60 };
    h.dispatch("scroll"); assert.equal(h.panelElement.style.top, "64px"); assert.equal(h.panelElement.style.bottom, "auto");
    panel(tree).props.onToggle({ nativeEvent: { newState: "closed" } }); tree = h.render();
    assert.equal(h.listeners.get("resize").size, 0); assert.equal(h.listeners.get("scroll").size, 0);
    assert.ok(h.removed.some(entry => entry.name === "scroll" && entry.capture === true));
    tree = h.open(); assert.equal(h.listeners.get("resize").size, 1); assert.equal(h.listeners.get("scroll").size, 1);
  } finally {
    h.restore();
    assert.equal(h.listeners.get("resize").size, 0); assert.equal(h.listeners.get("scroll").size, 0);
  }
});
