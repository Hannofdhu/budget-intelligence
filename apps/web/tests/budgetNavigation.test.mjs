import { test } from "node:test";
import assert from "node:assert/strict";
import { navigateBudgetModule, commitBudgetModuleNavigation, budgetNavigationEvent, budgetModuleLeaveEvent } from "../src/budgetNavigation.ts";

function browser(source = "/budget/forecast") {
  const original = globalThis.window, calls = [], events = new EventTarget();
  const window = { location: { href: `http://localhost${source}`, origin: "http://localhost" },
    history: { pushState(_state, _title, url) { calls.push(["push", String(url)]); window.location.href = String(url); } },
    dispatchEvent(event) { calls.push(["event", event.type]); return events.dispatchEvent(event); }, scrollTo() {},
    addEventListener: (...args) => events.addEventListener(...args), removeEventListener: (...args) => events.removeEventListener(...args) };
  globalThis.window = window;
  return { window, events, calls, close() { if (original === undefined) delete globalThis.window; else globalThis.window = original; } };
}
const click = (href, extra = {}) => ({ button: 0, currentTarget: { href }, preventDefault() { this.defaultPrevented = true; }, ...extra });

test("all five primary modules switch in both directions in the same document, including AI source/version parameters", () => {
  const routes = ["/budget/cockpit", "/budget/forecast", "/budget/next-year?year=2028&nyVersion=exact30", "/budget/workspace?archive=fy27&step=5", "/budget/ai?sourceVersion=exact30&chat=abcdef"];
  for (const from of routes) for (const to of routes.filter(route => route !== from)) {
    const h = browser(from);
    try {
      const event = click(to); navigateBudgetModule(event);
      assert.equal(event.defaultPrevented, true, `${from} → ${to}`);
      assert.equal(h.window.location.href, `http://localhost${to}`);
      assert.deepEqual(h.calls, [["event", budgetModuleLeaveEvent], ["push", `http://localhost${to}`], ["event", budgetNavigationEvent]]);
    } finally { h.close(); }
  }
});
test("modified, external, cancelled and unapproved legacy navigation keeps native browser behavior", () => {
  const h = browser();
  try {
    for (const overrides of [{ defaultPrevented: true }, { ctrlKey: true }, { metaKey: true }, { shiftKey: true }, { altKey: true }, { button: 1 },
      { currentTarget: { href: "/budget/ai", target: "_blank" } }, { currentTarget: { href: "https://elsewhere.example/budget/ai" } },
      { currentTarget: { href: "/budget/workspace" } }, { currentTarget: { href: "/budget/revisions" } }]) {
      navigateBudgetModule(click("/budget/ai", overrides)); assert.equal(h.calls.length, 0);
    }
    h.window.location.href = "http://localhost/budget/workspace";
    navigateBudgetModule(click("/budget/ai")); assert.equal(h.calls.length, 0);
  } finally { h.close(); }
});
test("a knowledge editor can veto leaving AI before any history, route or document navigation occurs", () => {
  const h = browser("/budget/ai?chat=original");
  try {
    let blocked = 0;
    const guard = event => { event.preventDefault(); blocked++; };
    h.events.addEventListener(budgetModuleLeaveEvent, guard);
    const event = click("/budget/next-year"); navigateBudgetModule(event);
    assert.equal(event.defaultPrevented, true); assert.equal(blocked, 1);
    assert.equal(h.window.location.href, "http://localhost/budget/ai?chat=original");
    assert.deepEqual(h.calls, [["event", budgetModuleLeaveEvent]]);
    h.events.removeEventListener(budgetModuleLeaveEvent, guard);
    navigateBudgetModule(click("/budget/next-year")); assert.equal(h.window.location.href, "http://localhost/budget/next-year");
  } finally { h.close(); }
});
test("explicit discard commits the protected destination without a hard reload or a second guard loop", () => {
  const h = browser("/budget/next-year");
  try {
    h.events.addEventListener(budgetModuleLeaveEvent, event => event.preventDefault());
    assert.equal(commitBudgetModuleNavigation("/budget/ai?sourceVersion=exact30"), true);
    assert.deepEqual(h.calls, [["push", "http://localhost/budget/ai?sourceVersion=exact30"], ["event", budgetNavigationEvent]]);
    for (const href of ["/budget/workspace", "/budget/publication", "https://elsewhere.example/budget/ai"]) assert.equal(commitBudgetModuleNavigation(href), false);
    assert.equal(h.calls.length, 2);
  } finally { h.close(); }
});
