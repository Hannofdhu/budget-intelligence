import { test } from "node:test";
import assert from "node:assert/strict";
import { readCockpitView, cockpitRows, cockpitGroups, cockpitTotals, drillCockpit, backCockpit, cockpitHref, cockpitScopeFilters, cockpitSourceHref, cockpitApiPath, getCockpit } from "../src/cockpit.ts";
import { aiBudgetHref, readAiBudgetContext, aiBudgetContextPrompt } from "../src/aiBudgetContext.ts";
import { invalidateBudgetReads } from "../src/budgetReadCache.ts";

const facts = (id, track, campus, season, budget, actual, forecast, next_budget) => ({ id, track, campus, season, product: "普通", grade: "七年级", subject: "数学", budget, actual, forecast, next_budget, covered: { budget: true, actual: true, forecast: true, next_budget: true } });
const data = { fiscal_year: 2027, next_fiscal_year: 2028, read_only: true, unit: "yuan", sources: { budget: { id: "archive", fiscal_year: 2027 }, actual: { id: "p26", as_of: "2026-10-23" }, forecast: { id: "p26", sequence: 26, status: "DRAFT" }, next_budget: { id: "n25", sequence: 25, status: "DRAFT", fiscal_year: 2028, source: { id: "p20", sequence: 20 } } }, rows: [facts("a", "空间", "甲校区", "暑假", "100", "40", "90", "120"), facts("b", "空间", "乙校区", "秋季", "200", "-2", "240", "300"), facts("c", "伴学", "甲校区", "暑假", "300", "90", "300", "400")] };

test("six-dimensional repeated filters are OR within, AND across; rates are recomputed from totals", () => {
  const view = readCockpitView("?bf_campus=甲校区&bf_campus=乙校区&bf_track=空间");
  const rows = cockpitRows(data, view);
  assert.equal(rows.length, 2);
  assert.deepEqual(cockpitTotals(rows), { budget: 300, actual: 38, forecast: 330, next_budget: 420, gap: -30, achievement: 1.1, actualAchievement: 38 / 300, nextGrowth: 420 / 330 - 1 });
  assert.deepEqual(cockpitGroups(rows, ["campus", "season"], "name").map(row => Object.keys(row.path)), [["campus", "season"], ["campus", "season"]]);
});
test("drill retains global filters and complete compound path, back restores grouping; no repeated no-op drills", () => {
  const view = readCockpitView("?cc_group=track&cc_group=product&bf_campus=甲校区");
  const next = drillCockpit(view, { track: "空间", product: "普通" }, "grade");
  assert.deepEqual(next.groups, ["grade"]); assert.equal(cockpitRows(data, next).length, 1);
  assert.deepEqual(backCockpit(next, 0), view);
  assert.equal(drillCockpit(next, { track: "空间" }), next);
  assert.equal(cockpitScopeFilters(next, { campus: "乙校区" }), null, "never broaden a conflicting scope");
  const restored = readCockpitView(cockpitHref(next, data).split("?")[1]);
  assert.deepEqual(restored, next);
});
test("unknown amounts poison only their covered metric; uncovered is not zero; signed values survive", () => {
  const rows = structuredClone(data.rows);
  rows[1].actual = null;
  assert.equal(cockpitTotals(rows).actual, null);
  assert.equal(cockpitTotals(rows).budget, 600);
  rows[1].covered.actual = false;
  assert.equal(cockpitTotals(rows).actual, 130);
  assert.equal(cockpitTotals([]).budget, null);
  assert.equal(cockpitTotals([facts("zero", "x", "a", "暑假", "0", "0", "-1", "0")]).achievement, null);
});
test("exact source links preserve source fiscal year, filter intersections and source identity", () => {
  const view = drillCockpit(readCockpitView("?bf_season=秋季&bf_campus=甲校区&bf_campus=乙校区"), { track: "空间" }, "grade");
  const forecast = new URL(cockpitSourceHref("forecast", data, view), "http://budget.test");
  assert.equal(forecast.searchParams.get("forecast_version"), "p26");
  assert.equal(forecast.searchParams.get("bf_track"), "空间");
  assert.deepEqual(forecast.searchParams.getAll("bf_campus"), ["甲校区", "乙校区"]);
  const next = new URL(cockpitSourceHref("next_budget", data, view), "http://budget.test");
  assert.equal(next.searchParams.get("nyVersion"), "n25"); assert.equal(next.searchParams.get("year"), "2028");
  assert.equal(cockpitSourceHref("budget", data, view, { season: "暑假" }), null);
});
test("AI handoff carries both pinned versions plus filters, drill and group order, never sends a model request", () => {
  const view = drillCockpit(readCockpitView("?cc_group=product&cc_group=subject&bf_season=暑假&bf_season=秋季"), { track: "空间" }, "campus");
  const context = readAiBudgetContext(aiBudgetHref(cockpitHref(view, data)).split("?")[1]);
  assert.deepEqual(context.cockpitSources, { forecast: "p26", next: "n25" });
  assert.match(context.scopeLabel, /空间/); assert.deepEqual(context.groupDimensions, ["campus"]);
  const prompt = aiBudgetContextPrompt(context);
  assert.match(prompt, /get cockpit --forecast-version p26 --budget-version n25/);
  assert.match(prompt, /不同维度和每层下钻路径AND/); assert.match(prompt, /不修改、刷新、确认、保存/);
  assert.equal(readAiBudgetContext(new URLSearchParams({ source: "/budget/cockpit?forecast_version=bad%3Bcommand&nyVersion=n25" }).toString()), null);
});
test("invalid explicit IDs fail closed, none remains none, fetch is GET-only and exact response pins are enforced", async () => {
  assert.throws(() => cockpitApiPath("?forecast_version="), /未替换/);
  assert.throws(() => cockpitApiPath("?nyVersion=..%2Fwrong"), /未替换/);
  assert.match(cockpitApiPath("?forecast_version=none&nyVersion=none"), /forecast_version=none&next_year_version=none/);
  const saved = globalThis.fetch; const calls = [];
  invalidateBudgetReads();
  globalThis.fetch = async (url, init) => { calls.push({ url, init }); return new Response(JSON.stringify(data), { status: 200, headers: { "content-type": "application/json" } }); };
  try {
    const actor = { userId: "u", departmentId: "d", roles: [] };
    await getCockpit(actor, "?forecast_version=p26&nyVersion=n25");
    assert.equal(calls[0].init.method, undefined); assert.match(calls[0].url, /forecast_version=p26/);
    await assert.rejects(getCockpit(actor, "?forecast_version=old&nyVersion=n25"), /未替换/);
    globalThis.fetch = async () => new Response(JSON.stringify({ detail: "指定的预测快照不存在，未替换为最新版本" }), { status:404 });
    await assert.rejects(getCockpit(actor, "?forecast_version=missing"), error => error.status === 404 && /快照不存在/.test(error.message));
  } finally { globalThis.fetch = saved; invalidateBudgetReads(); }
});
