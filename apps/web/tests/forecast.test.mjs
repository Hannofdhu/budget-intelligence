// Contract + static-render tests. Browser interaction and live BI extraction are separate acceptance checks.
import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import fs from "node:fs";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { budgetWorkbook } from "../src/budgetExport.ts";
import {
  forecastNumber, sumForecastRows, groupForecastRows, defaultForecastStates, forecastStateLabels,
  getForecast, refreshForecast, reviseForecast, confirmForecast, groupForecastIssues, forecastMappingIssues, prepareForecastMapping, forecastPresentationRow,
} from "../src/forecastApi.ts";

const require = createRequire(import.meta.url);
const compiled = await build({ entryPoints: [fileURLToPath(new URL("../src/ForecastPage.tsx", import.meta.url))],
  bundle: true, write: false, platform: "node", format: "cjs", jsx: "automatic", external: ["react", "react-dom", "exceljs"],
  loader: { ".css": "empty" }, logLevel: "silent" });
const module = { exports: {} };
new Function("require", "module", "exports", compiled.outputFiles[0].text)(require, module, module.exports);
const { ForecastDashboard, forecastGapLabel } = module.exports;
const text = html => html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
const row = (overrides = {}) => ({ season: "暑假", track: "智慧空间", campus: "西直门", product: "常规班", grade: "七年级", subject: "数学",
  budget_revenue: "100000.000000", forecast_revenue: "98000.000000", budget_total: 100, forecast_total: 97, method: "FROZEN_ACTUAL", ...overrides });
const calculation = (overrides = {}) => ({ rule_version: "test-only", ready: true,
  annual: { budget_revenue: "400000", forecast_revenue: "376000", gap: "24000", achievement: "0.94" },
  quarters: ["暑假", "秋季", "寒假", "春季"].map((season, index) => ({ season, state: defaultForecastStates[season],
    budget_revenue: "100000", forecast_revenue: index ? "92666.67" : "98000", gap: index ? "7333.33" : "2000", budget_total: 100, forecast_total: 97 })),
  rows: [row()], issues: [], campuses: [], ...overrides });
const render = data => renderToStaticMarkup(createElement(ForecastDashboard, { calculation: data, onAdjust() {} }));

test("explicit business-owned quarter states are stable and include ENDED, not calendar assumptions", () => {
  assert.deepEqual(defaultForecastStates, { 暑假: "FINALIZED", 秋季: "OPEN", 寒假: "FUTURE", 春季: "FUTURE" });
  assert.equal(forecastStateLabels.ENDED, "招生结束");
});
test("forecast display preserves recorded zero, negatives, decimals and missing values", () => {
  for (const value of [null, undefined, "", " ", "-", "NaN", "Infinity", "0x10", false]) assert.equal(forecastNumber(value), null);
  for (const [value, expected] of [[0, 0], ["0", 0], ["-1.25", -1.25], [".5", .5], [" 3.2 ", 3.2]]) assert.equal(forecastNumber(value), expected);
  assert.equal(sumForecastRows([row({ forecast_revenue: "0" }), row({ forecast_revenue: "-0.03" })], "forecast_revenue"), -.03);
  assert.equal(sumForecastRows([row(), row({ forecast_revenue: null })], "forecast_revenue"), null);
  assert.equal(sumForecastRows([], "budget_revenue"), null);
  assert.equal(sumForecastRows([row({ forecast_revenue: 1e16 }), row({ forecast_revenue: 1 }), row({ forecast_revenue: -1e16 })], "forecast_revenue"), 1);
});
test("selected totals use exact dimension groups and cannot disguise an unknown child", () => {
  const result = groupForecastRows([row(), row({ campus: "西直门南", budget_revenue: "300000", forecast_revenue: "240000" })], "track")[0];
  assert.equal(result.budget_revenue, 400000); assert.equal(result.forecast_revenue, 338000);
  assert.equal(result.gap, 62000); assert.equal(result.achievement, .845);
  assert.equal(groupForecastRows([row(), row({ forecast_revenue: null })], "track")[0].achievement, null);
  assert.equal(groupForecastRows([row({ budget_revenue: 0 })], "track")[0].achievement, null);
  assert.equal(groupForecastRows([row(), row({ method: "UNMAPPED_FROZEN_CARRYIN", unmapped: true, budget_revenue: null })], "campus")[0].unmapped, true);
  assert.equal(groupForecastRows([row({ campus: "西直门" }), row({ campus: "西直门南" })], "campus").length, 2);
});
test("gap tolerance labels are distinct from blockers and keep direction", () => {
  for (const value of ["0", "10000", "-10000"]) assert.equal(forecastGapLabel(value), "无显著缺口");
  assert.equal(forecastGapLabel("10000.01"), "预计缺口");
  assert.equal(forecastGapLabel("-10000.01"), "预计超额");
  assert.equal(forecastGapLabel(null), "待预测");
  assert.equal(forecastGapLabel("10000.01", true), "实际缺口");
  assert.equal(forecastGapLabel("-10000.01", true), "实际超额");
});
test("unmatched actuals retain money and counts but can never claim an excess against contribution zero", () => {
  const actual = row({ campus: "学习机校区", budget_revenue: "0", forecast_revenue: "13536900", budget_total: 0, forecast_total: 4321, method: "UNMAPPED_ACTUAL", unmapped: true });
  const frozen = JSON.stringify(actual);
  const result = groupForecastRows([actual], "campus")[0];
  assert.equal(result.forecast_revenue, 13536900); assert.equal(result.forecast_total, 4321);
  assert.equal(result.budget_revenue, null); assert.equal(result.budget_total, null);
  assert.equal(result.gap, null); assert.equal(result.achievement, null);
  assert.equal(JSON.stringify(actual), frozen, "engine zero contribution and source actuals stay unchanged");
  const html = render(calculation({ rows: [actual] }));
  const table = html.match(/aria-label="维度达成表"[\s\S]*?<tbody>([\s\S]*?)<\/tbody>/)?.[1] ?? "";
  assert.match(text(table), /— 1,353\.69 — — — 4,321/);
  assert.doesNotMatch(text(table), /超额|100%/);
});
test("mixed mapped and unmatched groups keep known amounts but postpone gap and achievement even with zero budgets", () => {
  for (const budget of [0, "0", "100000"]) {
    const mapped = row({ budget_revenue: budget, forecast_revenue: "98000", budget_total: 100, forecast_total: 97 });
    const unmatched = row({ budget_revenue: "0", forecast_revenue: "5000", budget_total: 0, forecast_total: 5, method: "UNMAPPED_ACTUAL" });
    const result = groupForecastRows([mapped, unmatched], "track")[0];
    assert.equal(result.budget_revenue, Number(budget)); assert.equal(result.forecast_revenue, 103000);
    assert.equal(result.budget_total, 100); assert.equal(result.forecast_total, 102);
    assert.equal(result.gap, null); assert.equal(result.achievement, null);
  }
  const knownZero = groupForecastRows([row({ budget_revenue: 0, forecast_revenue: 5000 })], "track")[0];
  assert.equal(knownZero.budget_revenue, 0); assert.equal(knownZero.gap, -5000); assert.equal(knownZero.achievement, null);
  const incomeOnly = groupForecastRows([row({ method: "UNMAPPED_FROZEN_CARRYIN", counts_not_applicable: true, budget_revenue: 0, forecast_revenue: 5000, budget_total: 0, forecast_total: 0 })], "track")[0];
  assert.equal(incomeOnly.forecast_revenue, 5000); assert.equal(incomeOnly.forecast_total, null);
  assert.equal(incomeOnly.budget_revenue, null); assert.equal(incomeOnly.gap, null);
});
test("detail and export presentation blanks unmatched budget contributions without changing actuals or the engine row", () => {
  for (const marker of [{ method: "UNMAPPED_ACTUAL" }, { method: "UNMAPPED_FROZEN_CARRYIN" }, { method: "FROZEN_ACTUAL", unmapped: true }]) {
    const original = row({ budget_revenue: "0.000000", budget_total: 0, forecast_revenue: "13536900.123456", forecast_total: 4321, ...marker });
    const before = JSON.stringify(original), presented = forecastPresentationRow(original);
    assert.equal(presented.budget_revenue, null); assert.equal(presented.budget_total, null);
    assert.equal(presented.forecast_revenue, "13536900.123456"); assert.equal(presented.forecast_total, 4321);
    assert.notEqual(presented, original); assert.equal(JSON.stringify(original), before);
  }
  for (const recordedZero of [0, "0", "0.000000"]) {
    const presented = forecastPresentationRow(row({ budget_revenue: recordedZero, budget_total: recordedZero }));
    assert.equal(presented.budget_revenue, recordedZero); assert.equal(presented.budget_total, recordedZero);
  }
  const html = render(calculation({ rows: [row({ method: "UNMAPPED_ACTUAL", budget_revenue: "0", budget_total: 0, forecast_revenue: "13536900", forecast_total: 4321 })] }));
  const body = html.match(/aria-label="预测明细表"[\s\S]*?<tbody>([\s\S]*?)<\/tbody>/)?.[1] ?? "";
  assert.match(text(body), /— 13,536,900 — 4,321 未对应实际/);
  const incomeOnly = forecastPresentationRow(row({ method: "UNMAPPED_FROZEN_CARRYIN", counts_not_applicable: true, budget_revenue: 0, budget_total: 0, forecast_total: 0, forecast_revenue: "5000" }));
  assert.equal(incomeOnly.forecast_revenue, "5000"); assert.equal(incomeOnly.forecast_total, null);
});
test("forecast XLSX roundtrip keeps unmatched budgets blank, known budget zero numeric, and actual money/counts numeric without mutating rows", async () => {
  const columns = [
    { key: "campus", label: "校区" },
    { key: "budget_revenue", label: "预算收入（元）", format: "money" },
    { key: "budget_total", label: "预算人次", format: "count" },
    { key: "forecast_revenue", label: "实际 / 预计收入（元）", format: "money" },
    { key: "forecast_total", label: "实际 / 预计人次", format: "count" },
  ];
  const originals = [
    row({ campus: "未对应实际", method: "UNMAPPED_ACTUAL", budget_revenue: "0.000000", budget_total: "0", forecast_revenue: "-325.987654", forecast_total: "17" }),
    row({ campus: "已对应零预算", budget_revenue: "0.000000", budget_total: 0, forecast_revenue: "-0.01", forecast_total: "3" }),
    row({ campus: "未对应结转", method: "UNMAPPED_FROZEN_CARRYIN", counts_not_applicable: true, budget_revenue: 0, budget_total: 0, forecast_revenue: "-42.50", forecast_total: 0 }),
    row({ campus: "显式未对应标记", unmapped: true, budget_revenue: 0, budget_total: 0, forecast_revenue: "-1.25", forecast_total: 0 }),
  ];
  const before = JSON.stringify(originals), presented = originals.map(forecastPresentationRow);
  const workbook = await budgetWorkbook(columns, presented, "FY27达成预测");
  const bytes = await workbook.xlsx.writeBuffer();
  const { default: ExcelJS } = await import("exceljs");
  const reopened = new ExcelJS.Workbook();
  await reopened.xlsx.load(bytes);
  const sheet = reopened.worksheets[0];
  for (const address of ["B2", "C2", "B4", "C4", "E4", "B5", "C5"]) assert.equal(sheet.getCell(address).value, null, `${address} must stay blank`);
  for (const address of ["B3", "C3", "D2", "E2", "D3", "E3", "D4", "D5", "E5"]) assert.equal(sheet.getCell(address).type, ExcelJS.ValueType.Number, `${address} must be a true numeric cell`);
  assert.equal(sheet.getCell("B3").value, 0); assert.equal(sheet.getCell("C3").value, 0);
  assert.equal(sheet.getCell("D2").value, -325.987654); assert.equal(sheet.getCell("E2").value, 17);
  assert.equal(sheet.getCell("D3").value, -0.01); assert.equal(sheet.getCell("E3").value, 3);
  assert.equal(sheet.getCell("D4").value, -42.5); assert.equal(sheet.getCell("D5").value, -1.25); assert.equal(sheet.getCell("E5").value, 0);
  assert.equal(sheet.getCell("D2").numFmt, "#,##0.00");
  assert.equal(JSON.stringify(originals), before);
});
test("a finalized quarter labels its frozen-actual variance as actual rather than expected", () => {
  const data = calculation({ quarters: [{ season: "暑假", state: "FINALIZED", budget_revenue: "100000", forecast_revenue: "75000", gap: "25000", budget_total: 100, forecast_total: 75 }] });
  const html = render(data);
  const quarter = html.match(/class="forecast-quarter-gap"[^>]*>([\s\S]*?)<\/div>/)?.[1] ?? "";
  assert.match(text(quarter), /实际缺口 2\.5 万/); assert.doesNotMatch(text(quarter), /预计/);
  const excess = render(calculation({ quarters: [{ ...data.quarters[0], forecast_revenue: "125000", gap: "-25000" }] }));
  assert.match(text(excess.match(/class="forecast-quarter-gap"[^>]*>([\s\S]*?)<\/div>/)?.[1] ?? ""), /实际超额 2\.5 万/);
});
test("board distinguishes final actuals, open terminal forecasts and future states in one context", () => {
  const html = render(calculation()), readable = text(html);
  for (const label of ["定版预算", "预计全年收入", "预计缺口", "预计达成率", "季度达成", "维度达成", "已定版", "招生中", "未开始", "冻结实际", "94%", "2.4 万"]) assert.ok(readable.includes(label), label);
  assert.match(html, /aria-label="季度达成明细表"/);
  assert.match(html, /aria-label="暑假，已定版，预算10 万，冻结实际9\.8 万/);
  assert.doesNotMatch(readable, /Excel|来源与口径|查看依据|参数来源|调平|上次正式/);
});
test("unready annual forecast is a dash, never copied from baseline or partially summed", () => {
  const data = calculation({ ready: false, annual: { budget_revenue: "113004618.59", forecast_revenue: null, gap: null, achievement: null },
    rows: [row({ forecast_revenue: null, forecast_total: null })], issues: [{ code: "SOURCE_UNMAPPED", severity: "BLOCKING", season: "秋季", dims: row(), message: "实际明细尚未对应预算维度" }] });
  const html = render(data), readable = text(html);
  assert.match(readable, /1 类预测条件待处理/); assert.match(readable, /处理预测条件/);
  assert.match(readable, /待预测 — 预计达成率 —/);
  assert.doesNotMatch(readable, /预计达成率 100%/);
  assert.match(readable, /实际业务待对应 秋季 1 条明细/);
  assert.doesNotMatch(readable, /智慧空间 \/ 西直门 \/ 常规班 \/ 七年级 \/ 数学/);
  assert.doesNotMatch(html, /NaN|Infinity|undefined/);
});
test("negative actual incomes are not rendered as zero-length positive chart bars", () => {
  const data = calculation({ quarters: [{ season: "暑假", state: "FINALIZED", budget_revenue: "100000", forecast_revenue: "-25000", gap: "125000", budget_total: 100, forecast_total: 0 }] });
  const html = render(data);
  assert.match(text(html), /-2\.5 万/); assert.match(html, /class="is-negative" style="transform:scaleX\(0\.25\)"/);
});
test("non-person carry-in rows do not advertise a fabricated student zero", () => {
  const html = render(calculation({ rows: [row({ method: "UNMAPPED_FROZEN_CARRYIN", counts_not_applicable: true, budget_total: 0, forecast_total: 0 })] }));
  const body = html.match(/aria-label="预测明细表"[\s\S]*?<tbody>([\s\S]*?)<\/tbody>/)?.[1] ?? "";
  assert.match(text(body), /— — 未对应结转实际/);
});
test("missing actuals cannot create NaN, Infinity or invented numerical percentages", () => {
  const html = render(calculation({ annual: { budget_revenue: 0, forecast_revenue: 0, gap: 0, achievement: null }, quarters: [], rows: [] }));
  assert.match(text(html), /无显著缺口 0 万/); assert.match(text(html), /当前范围暂无预测明细/);
  assert.doesNotMatch(html, /NaN|Infinity|undefined/);
});
test("large readiness issue sets show grouped counts and mount no detail rows before expansion", () => {
  const issues = Array.from({ length: 80 }, (_, index) => ({ code: "SOURCE_UNMAPPED", season: "秋季", severity: "BLOCKING", dims: row({ campus: `校区${index}` }), message: `测试条件 ${index}` }));
  const html = render(calculation({ ready: false, issues })), readable = text(html);
  assert.match(readable, /1 类预测条件待处理/); assert.match(readable, /阻断明细 80/); assert.match(readable, /实际业务待对应 秋季 80 条明细/);
  assert.equal((html.match(/class="forecast-issue-group-toggle"/g) ?? []).length, 1);
  assert.doesNotMatch(readable, /测试条件|校区0/); assert.match(html, /aria-expanded="false"/);
});
test("readiness headline counts condition groups, while detail keeps every server blocker and automatic dependencies are not manual tasks", () => {
  const issues = [
    ...Array.from({ length: 80 }, () => ({ code: "SOURCE_UNMAPPED", season: "秋季", severity: "BLOCKING", message: "实际业务待对应" })),
    ...Array.from({ length: 50 }, () => ({ code: "UPSTREAM_HEADCOUNT_UNAVAILABLE", season: "寒假", severity: "BLOCKING", message: "等待前季人数" })),
    ...Array.from({ length: 50 }, () => ({ code: "UPSTREAM_HEADCOUNT_UNAVAILABLE", season: "春季", severity: "BLOCKING", message: "等待前季人数" })),
  ];
  const html = render(calculation({ ready: false, issues })), readable = text(html);
  assert.match(readable, /3 类预测条件待处理/); assert.match(readable, /阻断明细 180/);
  assert.match(readable, /前季预测完成后自动计算 寒假 无需单独处理 50 条明细/);
  assert.match(readable, /前季预测完成后自动计算 春季 无需单独处理 50 条明细/);
  assert.doesNotMatch(readable, /180 项待处理/);
  assert.equal(groupForecastIssues(issues).reduce((sum, group) => sum + group.issues.length, 0), 180);
});
test("forecast header keeps the forecast version visible despite the global small-screen version hiding rule", () => {
  const css = fs.readFileSync(new URL("../src/forecast.css", import.meta.url), "utf8");
  assert.match(css, /\.forecast-shell \.decision-version\s*\{[^}]*display:\s*flex/);
  assert.match(css, /@media \(max-width: 900px\)[^\n]*\.forecast-shell \.decision-version\s*\{[^}]*grid-column:\s*2/);
  assert.doesNotMatch(css, /\.decision-version\s*\{[^}]*display:\s*none/);
});
test("issue grouping prioritizes mapping and only unmatched source records enter manual correspondence", () => {
  const issue = (code, season = "秋季", dims = row()) => ({ code, season, dims, severity: "BLOCKING", message: code });
  const issues = [issue("UPSTREAM_HEADCOUNT_UNAVAILABLE", "春季"), issue("CURRENT_SOURCE_MISSING"), issue("SOURCE_UNMAPPED"), issue("SOURCE_UNMAPPED", "暑假"), issue("FROZEN_CARRYIN_UNMAPPED")];
  const groups = groupForecastIssues(issues);
  assert.deepEqual(groups.slice(0, 3).map(group => group.code), ["SOURCE_UNMAPPED", "FROZEN_CARRYIN_UNMAPPED", "SOURCE_UNMAPPED"]);
  assert.equal(forecastMappingIssues(issues).length, 1, "same full source key is entered once across seasons and codes");
  assert.equal(forecastMappingIssues([issue("UPSTREAM_HEADCOUNT_UNAVAILABLE"), issue("CURRENT_SOURCE_MISSING")]).length, 0);
  assert.equal(forecastMappingIssues([issue("SOURCE_UNMAPPED", "秋季", { track: "智慧空间" })]).length, 0);
});
test("mapping preparation copies exact source dimensions into an editable unsaved target, never aliases or mutates", () => {
  const source = { track: "智慧空间", product: "人工审核产品", grade: "七年级", subject: "数学", campus: "人工审核校区" };
  const draft = prepareForecastMapping(source);
  assert.deepEqual(draft, { source, target: source, reason: "" });
  assert.notEqual(draft.source, source); assert.notEqual(draft.target, source); assert.notEqual(draft.source, draft.target);
  draft.target.campus = "审核后目标校区";
  assert.equal(draft.source.campus, "人工审核校区"); assert.equal(source.campus, "人工审核校区");
  assert.equal(draft.reason, "", "human reason and explicit insertion are still required");
});

test("forecast API commands use local identity, fresh idempotency keys, explicit parent and no annual command routes", async () => {
  const previous = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, options) => { calls.push({ url, options }); return new Response(JSON.stringify({ baseline_year: 2027, baseline_hash: "test", version: null, history: [] }), { status: 200 }); };
  const actor = { userId: "local-budget-owner", departmentId: "test-department", roles: ["DATA_MAINTAINER", "DEPARTMENT_BUDGET_OWNER"] };
  const assumptions = { states: defaultForecastStates, minimum_sample: 30, mappings: [], adjustments: [] };
  try {
    await getForecast(actor);
    await refreshForecast(actor, defaultForecastStates, "测试更新");
    await reviseForecast(actor, "parent-1", assumptions, "测试人数修订");
    await confirmForecast(actor, "p-1", "测试确认");
    assert.deepEqual(calls.map(call => call.url), ["/api/v1/forecasts/fy27/current", "/api/v1/forecasts/fy27/refresh", "/api/v1/forecasts/fy27/revise", "/api/v1/forecasts/fy27/p-1/confirm"]);
    for (const call of calls) assert.equal(call.options.headers["X-User-ID"], actor.userId);
    const keys = calls.slice(1).map(call => call.options.headers["Idempotency-Key"]);
    assert.equal(new Set(keys).size, 3); assert.ok(keys.every(key => key.length >= 8));
    assert.deepEqual(JSON.parse(calls[2].options.body), { parent_id: "parent-1", reason: "测试人数修订", assumptions });
    assert.ok(calls.every(call => !/annual-budgets|\/revisions\/|publication/.test(call.url)));
  } finally { globalThis.fetch = previous; }
});
test("conflicts expose a usable reload instruction and are never blindly retried", async () => {
  const previous = globalThis.fetch; let calls = 0;
  globalThis.fetch = async () => { calls++; return new Response(JSON.stringify({ detail: "预测版本已变化，请重新读取后操作" }), { status: 409 }); };
  try { await assert.rejects(getForecast({ userId: "reader", departmentId: "department", roles: [] }), /预测版本已变化，请重新读取后操作/); assert.equal(calls, 1); }
  finally { globalThis.fetch = previous; }
});
