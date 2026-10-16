import test from "node:test";
import assert from "node:assert/strict";
import { budgetModuleName, budgetModuleYears, defaultBudgetModuleYears } from "../src/budgetModules.ts";

test("module labels format the supplied data fiscal year rather than a fixed year or today's calendar year", () => {
  for (const year of [2027, 2028, 2029, 2030, 2100]) {
    const prefix = `FY${String(year).slice(-2)}`;
    assert.equal(budgetModuleName("budget", year), `${prefix}定版预算`);
    assert.equal(budgetModuleName("forecast", year), `${prefix}全年预测`);
    assert.equal(budgetModuleName("next-year", year), `${prefix}预算编制`);
  }
});

test("each fiscal-year context is independent, invalid overrides never leak into the navigation, and defaults are not mutated", () => {
  const original = { ...defaultBudgetModuleYears };
  assert.deepEqual(budgetModuleYears({ "next-year": 2029 }), { ...original, "next-year": 2029 });
  assert.deepEqual(budgetModuleYears({ budget: 2029, forecast: 2029, "next-year": 2029 }), { budget: 2029, forecast: 2029, "next-year": 2029 });
  for (const invalid of [NaN, Infinity, 0, 2029.5, "2029", undefined, null]) {
    assert.deepEqual(budgetModuleYears({ budget: invalid, forecast: invalid, "next-year": invalid }), original);
  }
  assert.deepEqual(defaultBudgetModuleYears, original);
});
