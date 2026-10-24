import { test } from "node:test";
import assert from "node:assert/strict";
import { navigateBudgetModule, budgetNavigationEvent } from "../src/budgetNavigation.ts";

test("approved primary module switches use the app shell without a document reload or budget write", () => {
  const original = globalThis.window, calls = [];
  globalThis.window = { location: { href: "http://localhost/budget/forecast", origin: "http://localhost" }, history: { pushState(...args) { calls.push(["push", String(args[2])]); } }, dispatchEvent(event) { calls.push(["event", event.type]); }, scrollTo() {} };
  try {
    const event = { button: 0, currentTarget: { href: "/budget/next-year" }, preventDefault() { this.defaultPrevented = true; } };
    navigateBudgetModule(event);
    assert.equal(event.defaultPrevented, true);
    assert.deepEqual(calls, [["push", "http://localhost/budget/next-year"], ["event", budgetNavigationEvent]]);
    for (const overrides of [{ defaultPrevented: true }, { ctrlKey: true }, { metaKey: true }, { button: 1 }, { currentTarget: { href: "/budget/next-year", target: "_blank" } }, { currentTarget: { href: "https://elsewhere.example/" } }, { currentTarget: { href: "/budget/ai" } }, { currentTarget: { href: "/budget/workspace" } }]) {
      navigateBudgetModule({ ...event, defaultPrevented: false, ...overrides });
      assert.equal(calls.length, 2, "cancelled and modified clicks preserve native browser/leave-guard behavior");
    }
    globalThis.window.location.href = "http://localhost/budget/ai";
    navigateBudgetModule({ ...event, defaultPrevented: false });
    assert.equal(calls.length, 2, "AI's existing native unsent-prompt guard must not be bypassed");
  } finally { if (original === undefined) delete globalThis.window; else globalThis.window = original; }
});
