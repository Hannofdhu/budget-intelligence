import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import fs from "node:fs";
import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { build } from "esbuild";

const require = createRequire(import.meta.url);
const compiled = await build({ entryPoints: [fileURLToPath(new URL("../src/FullscreenButton.tsx", import.meta.url))], bundle: true,
  write: false, platform: "node", format: "cjs", jsx: "automatic", external: ["react", "react/jsx-runtime"], logLevel: "silent" });
function load(react = React) {
  const module = { exports: {} };
  new Function("require", "module", "exports", compiled.outputFiles[0].text)(id => id === "react" ? react : require(id), module, module.exports);
  return module.exports.FullscreenButton;
}
function harness({ initialFullscreen = false, supported = true, request, exit } = {}) {
  const original = globalThis.document, listeners = new Map(), slots = [], effects = [], cleanups = [];
  let cursor = 0, enters = 0, exits = 0;
  const doc = {
    fullscreenEnabled: supported, fullscreenElement: null,
    documentElement: { requestFullscreen() { enters++; return request ? request() : Promise.resolve(); } },
    exitFullscreen() { exits++; return exit ? exit() : Promise.resolve(); },
    addEventListener: (name, listener) => listeners.set(name, listener),
    removeEventListener: name => listeners.delete(name),
  };
  if (initialFullscreen) doc.fullscreenElement = doc.documentElement;
  globalThis.document = doc;
  const hooks = { ...React,
    useState(initial) { const i = cursor++; if (!(i in slots)) slots[i] = typeof initial === "function" ? initial() : initial; return [slots[i], next => { slots[i] = typeof next === "function" ? next(slots[i]) : next; }]; },
    useRef(initial) { const i = cursor++; return slots[i] ??= { current: initial }; },
    useEffect(fn) { const i = cursor++; if (!(i in slots)) { slots[i] = true; effects.push(() => cleanups.push(fn())); } },
  };
  const Button = load(hooks);
  const render = () => { cursor = 0; return Button(); };
  const button = () => render().props.children[0];
  const error = () => render().props.children[1];
  const toggle = () => button().props.onClick();
  return { doc, listeners, render, button, error, toggle,
    calls: () => ({ enters, exits }),
    mount() { render(); effects.splice(0).forEach(fn => fn()); },
    change(element) { doc.fullscreenElement = element; listeners.get("fullscreenchange")?.(); },
    unmount() { cleanups.splice(0).forEach(fn => fn?.()); },
    close() { this.unmount(); if (original === undefined) delete globalThis.document; else globalThis.document = original; },
  };
}
const settled = () => new Promise(resolve => setImmediate(resolve));

test("SSR shows an explicit named control without requiring browser globals or starting fullscreen", () => {
  const FullscreenButton = load();
  const html = renderToStaticMarkup(React.createElement(FullscreenButton));
  assert.match(html, /type="button"/);
  assert.match(html, /aria-label="全屏显示产品" aria-pressed="false"/);
  assert.match(html, /<span>全屏<\/span>/);
});
test("entering targets the whole document, reports only the actual browser state, and exits through the same control", async () => {
  const h = harness();
  try {
    h.mount(); assert.deepEqual(h.calls(), { enters: 0, exits: 0 });
    h.toggle(); assert.equal(h.button().props.disabled, true);
    assert.equal(h.button().props["aria-pressed"], false, "a pending request is not a successful fullscreen state");
    h.change(h.doc.documentElement); await settled();
    assert.equal(h.button().props["aria-pressed"], true);
    assert.equal(h.button().props["aria-label"], "退出产品全屏");
    assert.equal(h.button().props.children[1].props.children, "退出全屏");
    h.toggle(); h.change(null); await settled();
    assert.equal(h.button().props["aria-pressed"], false);
    assert.deepEqual(h.calls(), { enters: 1, exits: 1 });
  } finally { h.close(); }
});
test("Escape and browser-initiated exits synchronize without intercepting input or keyboard events", () => {
  const h = harness({ initialFullscreen: true });
  try {
    h.mount(); assert.equal(h.button().props["aria-pressed"], true);
    h.change(null); assert.equal(h.button().props["aria-pressed"], false);
    assert.deepEqual([...h.listeners.keys()], ["fullscreenchange"]);
    assert.deepEqual(h.calls(), { enters: 0, exits: 0 });
  } finally { h.close(); }
});
test("rapid activations share one pending request and do not invoke the API twice", async () => {
  let finish;
  const h = harness({ request: () => new Promise(resolve => { finish = resolve; }) });
  try {
    h.mount(); h.toggle(); h.toggle();
    assert.deepEqual(h.calls(), { enters: 1, exits: 0 });
    finish(); await settled(); assert.equal(h.button().props.disabled, false);
  } finally { h.close(); }
});
test("unsupported browsers and unavailable methods show guidance without fake fullscreen or throwing", async () => {
  for (const mode of ["disabled", "missing"]) {
    const h = harness({ supported: mode !== "disabled" });
    try {
      if (mode === "missing") delete h.doc.documentElement.requestFullscreen;
      h.mount(); h.toggle(); await settled();
      assert.deepEqual(h.calls(), { enters: 0, exits: 0 });
      assert.equal(h.error().props.role, "alert");
      assert.match(h.error().props.children, /浏览器不支持网页全屏/);
      assert.equal(h.button().props.disabled, false);
      assert.equal(h.button().props["aria-pressed"], false);
    } finally { h.close(); }
  }
});
test("rejected entry and exit remain retryable and preserve the browser's true state", async () => {
  for (const initialFullscreen of [false, true]) {
    let fail = true;
    const operation = () => { if (fail) return Promise.reject(new Error("denied")); return Promise.resolve(); };
    const h = harness({ initialFullscreen, request: operation, exit: operation });
    try {
      h.mount(); h.toggle(); await settled();
      assert.match(h.error().props.children, /未能切换全屏/);
      assert.equal(h.button().props.disabled, false);
      assert.equal(h.button().props["aria-pressed"], initialFullscreen);
      fail = false; h.toggle(); h.change(initialFullscreen ? null : h.doc.documentElement); await settled();
      assert.equal(h.error(), ""); assert.equal(h.button().props["aria-pressed"], !initialFullscreen);
    } finally { h.close(); }
  }
});
test("unmount removes only its own listener and never exits fullscreen or starts another operation", async () => {
  let finish;
  const h = harness({ request: () => new Promise(resolve => { finish = resolve; }) });
  try {
    h.mount(); h.toggle(); h.unmount(); finish(); await settled();
    assert.equal(h.listeners.size, 0);
    assert.deepEqual(h.calls(), { enters: 1, exits: 0 });
  } finally { h.close(); }
});
test("fullscreen does not reload, persist view state, fetch, remount product content, or change zoom", () => {
  const source = fs.readFileSync(new URL("../src/FullscreenButton.tsx", import.meta.url), "utf8");
  assert.doesNotMatch(source, /\bfetch\s*\(|localStorage|sessionStorage|location\.|window\.open|zoom:|style\.zoom|createAi|saveBudget|reviseForecast/);
  const css = fs.readFileSync(new URL("../src/fy27Navigation.css", import.meta.url), "utf8");
  assert.match(css, /\.budget-fullscreen-button:focus-visible/);
  assert.match(css, /@media \(pointer: coarse\)/);
  assert.match(css, /\.budget-fullscreen-error[^}]+var\(--budget-error\)/);
});
