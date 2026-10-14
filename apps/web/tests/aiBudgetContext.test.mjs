// Pure navigation/prompt context only; no Pi session, model call, credentials or budget writes.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { aiBudgetHref, safeAiBudgetSource, readAiBudgetContext, aiBudgetContextPrompt } from "../src/aiBudgetContext.ts";

test("only known relative budget pages can become AI context or a return link", () => {
  for (const value of [null, "", "https://external.example/budget/next-year", "//external.example/budget/workspace", "javascript:alert(1)", "/api/v1/next-year-budgets/fy28/current", "/budget/ai", "/budget/workspace\\external", "/budget/workspace\n", "/budget/%77orkspace", "/budget/workspace" + "?x=" + "x".repeat(32769)]) {
    assert.equal(safeAiBudgetSource(value), null, String(value).slice(0, 80));
    assert.equal(aiBudgetHref(value), "/budget/ai");
  }
  assert.equal(readAiBudgetContext("?source=https%3A%2F%2Fexternal.example"), null);
});

test("complete repeated filters and view configuration survive navigation without unrelated or credential query data", () => {
  const source = "/budget/workspace?archive=fy27&step=5&bf_campus=A%2CB&bf_campus=A%26B&bf_subject=数学&bf_subject=英语&baView=pivot&baRows=campus&baUnit=wan&baSavedView=my-view&token=secret&authorization=private";
  const href = aiBudgetHref(source), link = new URL(href, "https://budget.local");
  assert.equal(link.pathname, "/budget/ai");
  const context = readAiBudgetContext(link.search);
  assert.equal(context.moduleLabel, "查看预算"); assert.equal(context.fiscalYear, 2027);
  assert.deepEqual(context.filters.find(item => item.key === "campus").value, ["A,B", "A&B"]);
  assert.deepEqual(context.filters.find(item => item.key === "subject").value, ["数学", "英语"]);
  assert.equal(new URL(context.sourceHref, "https://budget.local").searchParams.get("baSavedView"), "my-view");
  assert.match(context.scopeLabel, /校区：A,B、A&B/); assert.match(context.scopeLabel, /科目：数学、英语/);
  assert.equal(context.versionLabel, "FY27 已定版（只读）");
  assert.doesNotMatch(href, /secret|private|token|authorization/);
});

test("loaded forecast and next-year versions are attached explicitly instead of assuming latest", () => {
  for (const [source, version, module, year] of [
    ["/budget/forecast?archive=fy27&bf_scope=1&bf_season=秋季&bf_season=寒假", { id: "forecast-uuid-17", label: "P17" }, "达成预测", 2027],
    ["/budget/next-year?year=2029&nyTask=4&nyFilters=1&bf_campus=西直门&bf_campus=中关村", { id: "budget-uuid-12", label: "N12" }, "下一年预算", 2029],
  ]) {
    const href = aiBudgetHref(source, version), context = readAiBudgetContext(new URL(href, "https://budget.local").search);
    assert.deepEqual(context.version, version); assert.equal(context.fiscalYear, year); assert.equal(context.moduleLabel, module);
    assert.equal(new URL(context.sourceHref, "https://budget.local").searchParams.get(module === "下一年预算" ? "nyVersion" : "forecast_version"), version.id, "the return URL itself must restore the exact saved snapshot");
    assert.match(context.versionLabel, new RegExp(version.id)); assert.match(context.versionLabel, new RegExp(version.label));
    const prompt = aiBudgetContextPrompt(context);
    assert.match(prompt, new RegExp(version.id)); assert.match(prompt, /精确版本/); assert.match(prompt, /授权只读接口/);
    assert.match(prompt, /读取不到时明确说明，不用其他版本替代/);
    assert.match(prompt, /未保存的改动不在上下文中/);
  }
});

test("unknown and conflicting version declarations never masquerade as the original precise version", () => {
  for (const source of ["/budget/forecast?archive=fy27", "/budget/next-year?nyTask=3"]) {
    const context = readAiBudgetContext(new URL(aiBudgetHref(source), "https://budget.local").search);
    assert.equal(context.version, null); assert.match(context.versionLabel, /版本未指定/);
    assert.match(aiBudgetContextPrompt(context), /不能把最新版本冒充用户原页面的版本或范围/);
  }
  const explicit = readAiBudgetContext(new URL(aiBudgetHref("/budget/forecast?forecast_version=exact-old", { id: "other", label: "P99" }), "https://budget.local").search);
  assert.equal(explicit.version.id, "exact-old"); assert.doesNotMatch(explicit.versionLabel, /P99|other/);
  assert.match(explicit.versionLabel, /序号待核对/);
  const invalid = readAiBudgetContext(new URL(aiBudgetHref("/budget/next-year", { id: "../../credentials", label: "N1" }), "https://budget.local").search);
  assert.equal(invalid.version, null);
  const invalidSource = readAiBudgetContext(new URL(aiBudgetHref("/budget/next-year?nyVersion=../../not-a-version", { id: "other", label: "N1" }), "https://budget.local").search);
  assert.equal(invalidSource.version, null);
  const blankSource = readAiBudgetContext(new URL(aiBudgetHref("/budget/next-year?nyVersion=", { id: "exact-current", label: "N12" }), "https://budget.local").search);
  assert.equal(new URL(blankSource.sourceHref, "https://budget.local").searchParams.get("nyVersion"), "exact-current");
  assert.match(aiBudgetContextPrompt(null), /未绑定来源页面/);
});

test("context preparation has no transport, saved state or AI lifecycle side effects", () => {
  const original = globalThis.fetch; let requests = 0;
  globalThis.fetch = () => { requests++; throw new Error("context is read-only view data"); };
  try {
    const href = aiBudgetHref("/budget/next-year?bf_campus=西直门", { id: "N12-id", label: "N12" });
    const context = readAiBudgetContext(new URL(href, "https://budget.local").search);
    aiBudgetContextPrompt(context); assert.equal(requests, 0);
    const source = fs.readFileSync(new URL("../src/aiBudgetContext.ts", import.meta.url), "utf8");
    assert.doesNotMatch(source, /\bfetch\s*\(|localStorage|sessionStorage|createAiAgentSession|\.input\s*\(|\.send\s*\(/);
  } finally { globalThis.fetch = original; }
});
