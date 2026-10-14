import { test } from "node:test";
import assert from "node:assert/strict";
import { quarterlyScrollGeometry, observeQuarterlyBusinessScroll } from "../src/quarterlyBusinessLayout.ts";

test("the single quarter header clears actual product chrome after nested and page scrolling", () => {
  const initial = { wrapperTop: 350, wrapperHeight: 620, headerHeight: 36, chromeBottom: 135, footerHeight: 94, viewportHeight: 900 };
  assert.deepEqual(quarterlyScrollGeometry(initial), { maxHeight: 647, headerOffset: 0 }, "initial heading never floats across table tools");
  const moved = quarterlyScrollGeometry({ ...initial, wrapperTop: 49.5 });
  assert.equal(moved.headerOffset, 85.5);
  assert.equal(49.5 + moved.headerOffset, 135, "sticky four-quarter labels sit immediately below the five-step navigation");
  const narrow = quarterlyScrollGeometry({ ...initial, chromeBottom: 183, footerHeight: 160, viewportHeight: 700 });
  assert.equal(narrow.maxHeight, 333, "narrow table height reserves the real two-line header, nav and save area");
  const ending = quarterlyScrollGeometry({ ...initial, wrapperTop: -1000, wrapperHeight: 200 });
  assert.equal(ending.headerOffset, 163, "the header offset is capped within its real scroll container");
});

test("layout observer remeasures keyboard scrollIntoView and cleans every listener and observer on unmount", () => {
  const originalObserver = globalThis.ResizeObserver;
  const listeners = new Map(), adds = [], removes = [], observed = [], properties = new Map();
  let resizeCallback, disconnected = false;
  let wrapperTop = 350, chromeBottom = 135, footerTop = 806;
  const view = { innerHeight: 900, addEventListener(type, fn, options) { adds.push([type, options]); listeners.set(type, fn); },
    removeEventListener(type, fn, options) { removes.push([type, options]); assert.equal(fn, listeners.get(type)); listeners.delete(type); } };
  const header = { getBoundingClientRect: () => ({ top: 0, bottom: 72, height: 72 }) };
  const nav = { getBoundingClientRect: () => ({ top: 72, bottom: chromeBottom, height: chromeBottom - 72 }) };
  const footer = { getBoundingClientRect: () => ({ top: footerTop, bottom: view.innerHeight, height: view.innerHeight - footerTop }) };
  const head = { getBoundingClientRect: () => ({ height: 36 }) };
  const shell = { querySelectorAll: selector => selector.includes("fy27-topbar") ? [header, nav] : [footer] };
  const node = { ownerDocument: { defaultView: view }, closest: () => shell, querySelector: () => head,
    getBoundingClientRect: () => ({ top: wrapperTop, height: 620 }), style: {
      getPropertyValue: name => properties.get(name), setProperty: (name, value) => properties.set(name, value), removeProperty: name => properties.delete(name),
    } };
  globalThis.ResizeObserver = class { constructor(callback) { resizeCallback = callback; } observe(element) { observed.push(element); } disconnect() { disconnected = true; } };
  try {
    const cleanup = observeQuarterlyBusinessScroll(node);
    assert.equal(properties.get("--qbt-header-offset"), "0px");
    assert.equal(properties.get("--qbt-max-height"), "647px");
    assert.deepEqual(adds, [["scroll", { capture: true, passive: true }], ["resize", undefined]]);
    wrapperTop = 49.5;
    listeners.get("scroll")(); // scrollIntoView may move both the nested scroller and the outer page.
    assert.equal(properties.get("--qbt-header-offset"), "85.5px");
    view.innerHeight = 700; footerTop = 540; chromeBottom = 183;
    listeners.get("resize")();
    assert.equal(properties.get("--qbt-max-height"), "333px");
    assert.equal(properties.get("--qbt-header-offset"), "133.5px");
    resizeCallback();
    assert.equal(new Set(observed).size, observed.length, "resize callbacks do not repeatedly register the same nodes");
    assert.equal(observed.length, 5);
    cleanup();
    assert.equal(disconnected, true);
    assert.deepEqual(removes, [["scroll", true], ["resize", undefined]]);
    assert.equal(listeners.size, 0);
    assert.equal(properties.size, 0);
  } finally { globalThis.ResizeObserver = originalObserver; }
});
