// Geometry is supplied by a fake ResizeObserver; these are layout lifecycle
// contracts, not evidence of real browser typography or zoom behavior.
import { test } from "node:test";
import assert from "node:assert/strict";
import { observeBudgetTableHeader } from "../src/budgetTableLayout.ts";

function fixture(height = 44) {
  let currentHeight = height;
  const values = new Map(), listeners = new Map(), writes = [];
  const row = { getBoundingClientRect() { return { height: currentHeight }; } };
  const view = { addEventListener(name, callback) { listeners.set(name, callback); },
    removeEventListener(name, callback) { if (listeners.get(name) === callback) listeners.delete(name); } };
  const container = { ownerDocument: { defaultView: view }, querySelector(selector) { assert.equal(selector, "thead > tr"); return row; },
    style: { setProperty(name, value) { values.set(name, value); writes.push([name, value]); }, removeProperty(name) { values.delete(name); } } };
  return { row, container, values, writes, listeners, setHeight(value) { currentHeight = value; } };
}

test("sticky second headers track fractional actual first-row heights and disconnect without reading values", () => {
  const original = globalThis.ResizeObserver, observers = [];
  globalThis.ResizeObserver = class {
    constructor(callback) { this.callback = callback; this.observed = []; this.disconnected = false; observers.push(this); }
    observe(element) { this.observed.push(element); } disconnect() { this.disconnected = true; }
  };
  try {
    const f = fixture(44.5), cleanup = observeBudgetTableHeader(f.container);
    assert.equal(f.values.get("--ba-header-row-height"), "44.5px");
    assert.equal(observers.length, 1); assert.deepEqual(observers[0].observed, [f.row]);
    f.setHeight(92.625); observers[0].callback();
    assert.equal(f.values.get("--ba-header-row-height"), "92.625px", "wrapped or enlarged headers determine the second-row offset");
    const resize = f.listeners.get("resize"); f.setHeight(106); resize(); assert.equal(f.values.get("--ba-header-row-height"), "106px");
    cleanup(); assert.equal(observers[0].disconnected, true); assert.equal(f.listeners.size, 0); assert.equal(f.values.size, 0);
    const count = f.writes.length; f.setHeight(200); observers[0].callback(); resize(); assert.equal(f.writes.length, count, "detached refs cannot write styles");
  } finally { if (original === undefined) delete globalThis.ResizeObserver; else globalThis.ResizeObserver = original; }
});

test("no ResizeObserver fallback measures initially and on resize; zero and invalid measurements do not become sticky offsets", () => {
  const original = globalThis.ResizeObserver; delete globalThis.ResizeObserver;
  try {
    const f = fixture(44), cleanup = observeBudgetTableHeader(f.container);
    for (const value of [0, -1, NaN, Infinity]) { f.setHeight(value); f.listeners.get("resize")(); assert.equal(f.values.get("--ba-header-row-height"), "44px"); }
    f.setHeight(75); f.listeners.get("resize")(); assert.equal(f.values.get("--ba-header-row-height"), "75px"); cleanup();
    assert.equal(observeBudgetTableHeader(null), undefined);
    assert.equal(observeBudgetTableHeader({ querySelector() { return null; } }), undefined);
  } finally { if (original !== undefined) globalThis.ResizeObserver = original; }
});

test("separate table viewports own separate header variables and independent observer lifetimes", () => {
  const original = globalThis.ResizeObserver, observers = [];
  globalThis.ResizeObserver = class { constructor(callback) { this.callback = callback; observers.push(this); } observe() {} disconnect() {} };
  try {
    const a = fixture(44), b = fixture(80), stopA = observeBudgetTableHeader(a.container), stopB = observeBudgetTableHeader(b.container);
    assert.equal(a.values.get("--ba-header-row-height"), "44px"); assert.equal(b.values.get("--ba-header-row-height"), "80px");
    stopA(); b.setHeight(96); observers[1].callback();
    assert.equal(a.values.size, 0); assert.equal(b.values.get("--ba-header-row-height"), "96px"); stopB();
  } finally { if (original === undefined) delete globalThis.ResizeObserver; else globalThis.ResizeObserver = original; }
});
