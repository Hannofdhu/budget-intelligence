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
  prepareTrialForecast, trialFinancialDraft, applyTrialFinancialDraft, canConfirmForecast,
} from "../src/forecastApi.ts";

const require = createRequire(import.meta.url);
const compiled = await build({ entryPoints: [fileURLToPath(new URL("../src/ForecastPage.tsx", import.meta.url))],
  bundle: true, write: false, platform: "node", format: "cjs", jsx: "automatic", external: ["react", "react-dom", "exceljs"],
  loader: { ".css": "empty" }, logLevel: "silent" });
const module = { exports: {} };
new Function("require", "module", "exports", compiled.outputFiles[0].text)(require, module, module.exports);
const { ForecastDashboard, forecastGapLabel, ForecastLogic, ForecastTrialStatus, ForecastTrialScopeEditor, TrialAssumptionNotes, forecastRowLabel } = module.exports;
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
    await prepareTrialForecast(actor, "parent-trial", "准备试用");
    assert.deepEqual(calls.map(call => call.url), ["/api/v1/forecasts/fy27/current", "/api/v1/forecasts/fy27/refresh", "/api/v1/forecasts/fy27/revise", "/api/v1/forecasts/fy27/p-1/confirm", "/api/v1/forecasts/fy27/prepare-trial"]);
    for (const call of calls) assert.equal(call.options.headers["X-User-ID"], actor.userId);
    const keys = calls.slice(1).map(call => call.options.headers["Idempotency-Key"]);
    assert.equal(new Set(keys).size, 4); assert.ok(keys.every(key => key.length >= 8));
    assert.deepEqual(JSON.parse(calls[2].options.body), { parent_id: "parent-1", reason: "测试人数修订", assumptions });
    assert.deepEqual(JSON.parse(calls[4].options.body), { parent_id: "parent-trial", reason: "准备试用" });
    assert.ok(calls.every(call => !/annual-budgets|\/revisions\/|publication/.test(call.url)));
  } finally { globalThis.fetch = previous; }
});
test("conflicts expose a usable reload instruction and are never blindly retried", async () => {
  const previous = globalThis.fetch; let calls = 0;
  globalThis.fetch = async () => { calls++; return new Response(JSON.stringify({ detail: "预测版本已变化，请重新读取后操作" }), { status: 409 }); };
  try { await assert.rejects(getForecast({ userId: "reader", departmentId: "department", roles: [] }), /预测版本已变化，请重新读取后操作/); assert.equal(calls, 1); }
  finally { globalThis.fetch = previous; }
});

const trialScope = (overrides = {}) => ({ dims: { track: "智慧空间", product: "独立产品", grade: "七年级", subject: "数学", campus: "完整校区" },
  kind: "UNBUDGETED_BUSINESS", reason: "测试明确试用假设", additional: { 秋季: 0 },
  financial: { 秋季: { price: "3000", discount: "0.85", recognition: "0.9", renewal: null, share: null, method: "TRIAL_ESTIMATE" },
    寒假: { price: "3100", discount: "0.9", recognition: "1", renewal: "0.7", share: "0.2", method: "PEER_GRADE_WEIGHTED" } }, ...overrides });

test("trial financial drafts use percentages for editing and preserve genuinely unspecified rates", () => {
  const original = trialScope(), before = JSON.stringify(original), draft = trialFinancialDraft(original, "秋季");
  assert.equal(draft.price, "3000"); assert.equal(draft.discount, "85"); assert.equal(draft.recognition, "90");
  assert.equal(draft.renewal, ""); assert.equal(draft.share, ""); assert.equal(draft.additional, "0");
  const result = applyTrialFinancialDraft(original, "秋季", "OPEN", { ...draft, price: "3250.50", discount: "86.5", recognition: "95", additional: "17", reason: " 秋季试用调整 " });
  assert.equal(result.error, ""); assert.ok(result.scope); assert.equal(result.scope.reason, "秋季试用调整");
  assert.deepEqual(result.scope.financial.秋季, { price: "3250.5", discount: "0.865", recognition: "0.95", renewal: null, share: null, method: "MANUAL" });
  assert.equal(result.scope.additional.秋季, 17); assert.deepEqual(result.scope.financial.寒假, original.financial.寒假);
  assert.notEqual(result.scope, original); assert.notEqual(result.scope.dims, original.dims); assert.equal(JSON.stringify(original), before);
});

test("trial edits validate finite numeric bounds, mandatory reason, integer additional and freeze actual scopes", () => {
  const original = trialScope(), draft = trialFinancialDraft(original, "秋季");
  for (const change of [{ price: "Infinity" }, { discount: "101" }, { recognition: "-1" }, { renewal: "NaN" }, { renewal: "101" }, { share: "100" }, { share: "-1" }, { additional: "1.5" }, { additional: "10000001" }, { reason: " " }]) {
    const result = applyTrialFinancialDraft(original, "秋季", "OPEN", { ...draft, ...change });
    assert.equal(result.scope, null); assert.ok(result.error);
  }
  assert.equal(applyTrialFinancialDraft(original, "秋季", "FINALIZED", draft).scope, null);
  assert.equal(applyTrialFinancialDraft(trialScope({ kind: "FINANCIAL_ONLY" }), "秋季", "OPEN", draft).scope, null);
  const future = applyTrialFinancialDraft(original, "寒假", "FUTURE", { ...trialFinancialDraft(original, "寒假"), additional: "99", renewal: "72.5", share: "18", reason: "未来试用修改" }).scope;
  assert.deepEqual(future.additional, { 秋季: 0 }); assert.equal(future.financial.寒假.renewal, "0.725"); assert.equal(future.financial.寒假.share, "0.18");
});

test("additional-only trial edits retain all raw financial decimals and estimation method, despite rounded percentage display", () => {
  const financial = { price: "3017.123456789123456789", discount: "0.853123456789123456789", recognition: "0.913987654321987654321", renewal: "0.713123456789123456789", share: "0.123987654321987654321", method: "PEER_GRADE_WEIGHTED" };
  const original = trialScope({ financial: { 秋季: financial } }), before = JSON.stringify(original), draft = trialFinancialDraft(original, "秋季");
  const result = applyTrialFinancialDraft(original, "秋季", "OPEN", { ...draft, additional: "1", reason: "仅调整新增" });
  assert.equal(result.error, ""); assert.equal(result.scope.additional.秋季, 1);
  assert.deepEqual(result.scope.financial.秋季, financial); assert.notEqual(result.scope.financial.秋季, financial);
  assert.equal(JSON.stringify(original), before);
  const emptyRates = trialScope({ financial: { 秋季: { price: 0, discount: "0.000000", recognition: "1.000000", method: "TRIAL_ESTIMATE" } } });
  const next = applyTrialFinancialDraft(emptyRates, "秋季", "OPEN", { ...trialFinancialDraft(emptyRates, "秋季"), additional: "2", reason: "仅调整新增" });
  assert.deepEqual(next.scope.financial.秋季, emptyRates.financial.秋季);
  assert.equal(Object.hasOwn(next.scope.financial.秋季, "renewal"), false); assert.equal(Object.hasOwn(next.scope.financial.秋季, "share"), false);
  const preciseShare = trialScope({ financial: { 秋季: { ...financial, share: "0.999999999999999999999" } } });
  const retained = applyTrialFinancialDraft(preciseShare, "秋季", "OPEN", { ...trialFinancialDraft(preciseShare, "秋季"), additional: "1", reason: "仅调整新增" });
  assert.equal(retained.error, ""); assert.equal(retained.scope.financial.秋季.share, "0.999999999999999999999", "display-rounded 100% is not a financial edit");
});

test("a single financial edit converts only that field and keeps untouched raw decimal strings", () => {
  const financial = { price: "3017.123456789123456789", discount: "0.853123456789123456789", recognition: "0.913987654321987654321", renewal: null, share: "0.123987654321987654321", method: "PEER_GRADE_WEIGHTED" };
  const original = trialScope({ financial: { 秋季: financial } }), draft = trialFinancialDraft(original, "秋季");
  const next = applyTrialFinancialDraft(original, "秋季", "OPEN", { ...draft, discount: "86.5", reason: "仅改折扣" }).scope;
  assert.deepEqual(next.financial.秋季, { ...financial, discount: "0.865", method: "MANUAL" });
  const normalized = applyTrialFinancialDraft(original, "秋季", "OPEN", { ...draft, discount: `${draft.discount}0`, additional: "1", reason: "格式未改数值" }).scope;
  assert.deepEqual(normalized.financial.秋季, financial);
  const cleared = applyTrialFinancialDraft(original, "秋季", "OPEN", { ...draft, share: "", reason: "清除新生占比" }).scope;
  assert.deepEqual(cleared.financial.秋季, { ...financial, share: null, method: "MANUAL" });
});

test("independent trial business is not a zero-budget excess, while covered group budgets can compare the full trial forecast", () => {
  const independent = row({ budget_covered: false, budget_revenue: 0, budget_total: 0, forecast_revenue: "5000", forecast_total: 5, method: "PARENT_PARAMETER_PROJECTION" });
  const before = JSON.stringify(independent), shown = forecastPresentationRow(independent), only = groupForecastRows([independent], "track")[0];
  assert.equal(shown.budget_revenue, null); assert.equal(shown.budget_total, null);
  assert.equal(only.budget_revenue, null); assert.equal(only.gap, null); assert.equal(only.achievement, null); assert.equal(only.hasUnbudgeted, true);
  const mixed = groupForecastRows([row(), independent], "track")[0];
  assert.equal(mixed.budget_revenue, 100000); assert.equal(mixed.forecast_revenue, 103000); assert.equal(mixed.gap, -3000);
  assert.equal(mixed.budget_total, 100); assert.equal(mixed.forecast_total, 102); assert.equal(mixed.achievement, 1.03);
  assert.equal(groupForecastRows([independent, row({ unmapped: true, budget_revenue: 0 })], "track")[0].gap, null);
  const html = render(calculation({ rows: [independent] })); assert.match(text(html), /含未单列业务 未单列/);
  const body = html.match(/aria-label="预测明细表"[\s\S]*?<tbody>([\s\S]*?)<\/tbody>/)?.[1] ?? "";
  assert.match(text(body), /未单列 5,000 未单列 5/); assert.equal(JSON.stringify(independent), before);
});

test("trial scope XLSX preserves blank unbudgeted columns and numeric forecast values", async () => {
  const raw = row({ budget_covered: false, budget_revenue: 0, budget_total: 0, forecast_revenue: "-7.25", forecast_total: 15 });
  const before = JSON.stringify(raw);
  const workbook = await budgetWorkbook([{ key: "budget_revenue", label: "预算", format: "money" }, { key: "budget_total", label: "预算人次", format: "count" },
    { key: "forecast_revenue", label: "预计收入", format: "money" }, { key: "forecast_total", label: "预计人次", format: "count" }], [forecastPresentationRow(raw)], "试用预测");
  const { default: ExcelJS } = await import("exceljs"), loaded = new ExcelJS.Workbook();
  await loaded.xlsx.load(await workbook.xlsx.writeBuffer()); const sheet = loaded.worksheets[0];
  assert.equal(sheet.getCell("A2").value, null); assert.equal(sheet.getCell("B2").value, null);
  assert.equal(sheet.getCell("C2").type, ExcelJS.ValueType.Number); assert.equal(sheet.getCell("C2").value, -7.25);
  assert.equal(sheet.getCell("D2").type, ExcelJS.ValueType.Number); assert.equal(sheet.getCell("D2").value, 15); assert.equal(JSON.stringify(raw), before);
});

test("trial completeness shows known department totals even if child actuals are unavailable, and retains formal blockers folded", () => {
  const data = calculation({ ready: false, trial_ready: true, annual: { budget_revenue: "113004618.59", forecast_revenue: "121000000", gap: "-7995381.41", achievement: "1.070753" },
    rows: [row({ forecast_revenue: null, forecast_total: null, data_status: "ACTUAL_UNAVAILABLE" })],
    issues: [{ code: "ACTUAL_UNAVAILABLE", severity: "BLOCKING", season: "暑假", message: "部分实际明细未对应" }] });
  const html = renderToStaticMarkup(createElement(ForecastDashboard, { calculation: data, trialMode: true })), readable = text(html);
  assert.match(readable, /预计全年收入 12,100 万/); assert.match(readable, /预计超额 799\.54 万/); assert.match(readable, /预计达成率 107\.08%/);
  assert.doesNotMatch(readable, /尚不能完成预测|类预测条件待处理/);
  assert.match(readable, /正式确认条件 1 类 阻断明细 1/);
  assert.match(html, /class="forecast-section forecast-issues"><details><summary>/);
  assert.match(html, /<option selected="">秋季<\/option>/); assert.equal(data.rows[0].forecast_revenue, null);
});

test("formal confirmation never accepts trial completeness or skips owner, dirty or server readiness gates", () => {
  const actor = { userId: "owner", departmentId: "test", roles: ["DEPARTMENT_BUDGET_OWNER"] };
  const version = { id: "p1", status: "DRAFT", assumptions: { trial_mode: false }, calculation: { ready: true, trial_ready: true } };
  assert.equal(canConfirmForecast(version, actor), true);
  assert.equal(canConfirmForecast({ ...version, assumptions: { trial_mode: true } }, actor), false);
  assert.equal(canConfirmForecast({ ...version, calculation: { ready: false, trial_ready: true } }, actor), false);
  assert.equal(canConfirmForecast(version, actor, true), false); assert.equal(canConfirmForecast(version, null), false);
  assert.equal(canConfirmForecast(version, { ...actor, roles: ["DATA_MAINTAINER"] }), false);
  assert.equal(canConfirmForecast({ ...version, status: "CONFIRMED" }, actor), false);
});

test("trial interactions keep complete dimensions visible and distinguish estimates from actuals without a new product shell", () => {
  const logic = renderToStaticMarkup(createElement(ForecastLogic)), status = renderToStaticMarkup(createElement(ForecastTrialStatus, { ready: true, onAdjust() {} }));
  assert.match(text(logic), /暑假 已定版实际 秋季 当前人次 ＋ 后续新增 − 预计退班 寒假、春季 承接上一季预测人数/);
  assert.match(text(status), /试用试算 完整预测已准备 非正式预测 调整试用假设/);
  const scopes = [trialScope(), trialScope({ dims: { track: "智慧伴学", product: "结转项目", grade: "八年级", subject: "英语", campus: "另一校区" }, kind: "FINANCIAL_ONLY" })];
  const editor = renderToStaticMarkup(createElement(ForecastTrialScopeEditor, { scopes, states: defaultForecastStates, onChange() {} })), readable = text(editor);
  assert.match(readable, /完整校区 智慧空间 \/ 独立产品 \/ 七年级 \/ 数学/); assert.match(readable, /结转实际/);
  assert.match(editor, /aria-label="调整秋季 智慧空间 \/ 完整校区 \/ 独立产品 \/ 七年级 \/ 数学试用假设"/);
  assert.equal(forecastRowLabel(row({ data_status: "TRIAL_SIZE_HELD" })), "暂按上一季人数");
  assert.equal(forecastRowLabel(row({ data_status: "BASELINE_ESTIMATE" })), "预算参数估计");
  assert.equal(forecastRowLabel(row({ data_status: "ACTUAL_UNAVAILABLE" })), "实际未对应");
  assert.doesNotMatch(readable, /Excel|来源与口径|参数来源|查看依据|定版规则/);
});

test("trial default dimension view uses open-quarter results without altering department annual and quarter totals", () => {
  const data = calculation({ ready: false, trial_ready: true, rows: [row({ forecast_revenue: null, forecast_total: null, data_status: "ACTUAL_UNAVAILABLE" }), row({ season: "秋季", forecast_revenue: "68000", forecast_total: 68, method: "TERMINAL_FORECAST" })] });
  const html = renderToStaticMarkup(createElement(ForecastDashboard, { calculation: data, trialMode: true }));
  const table = html.match(/aria-label="维度达成表"[\s\S]*?<tbody>([\s\S]*?)<\/tbody>/)?.[1] ?? "";
  assert.match(text(table), /10 6\.8 缺口 3\.2 68% 100 68/);
  assert.match(text(html), /预计全年收入 37\.6 万/); assert.equal(data.annual.forecast_revenue, "376000");
  const all = render(data); assert.match(text(all), /部分暑假明细未对应，全年分组暂不合计；可按季度查看/);
  const allTable = all.match(/aria-label="维度达成表"[\s\S]*?<tbody>([\s\S]*?)<\/tbody>/)?.[1] ?? "";
  assert.match(text(allTable), /20 — — — 200 —/);
});

test("trial notes explicitly disclose entered rates that were not applied, including numerical zero", () => {
  const html = renderToStaticMarkup(createElement(TrialAssumptionNotes, { assumptions: [{ code: "TRIAL_SIZE_HELD", message: "暂按上一季人数", season: "寒假", dims: trialScope().dims, detail: { unapplied_renewal: "0.7", unapplied_share: null } },
    { code: "TRIAL_SIZE_HELD", message: "暂按上一季人数", season: "春季", dims: trialScope().dims, detail: { unapplied_renewal: null, unapplied_share: 0 } }] }));
  assert.equal((text(html).match(/已填写比例暂未用于计算/g) ?? []).length, 2);
  assert.match(text(html), /寒假 \/ 智慧空间 \/ 完整校区 \/ 独立产品 \/ 七年级 \/ 数学/);
});
