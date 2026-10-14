// Read-only test fixtures and mocked requests; no live budget API mutation.
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";
import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { budgetWorkbook } from "../src/budgetExport.ts";
import { getNextYearBudget, prepareNextYearBudget, reviseNextYearBudget, previewNextYearBudget,
  getNextYearCatalog, getNextYearVersion, restoreNextYearVersion, balanceNextYearBudget, checkNextYearClassReadiness, proposeNextYearClassRepair,
  nextYearEditKey as key, applyNextYearEdits, allocateNextYearTrackTargets, nextYearRowTotal, summarizeNextYearEdits } from "../src/nextYearApi.ts";

const require = createRequire(import.meta.url);
const bundled = await build({ entryPoints: [fileURLToPath(new URL("../src/NextYearBudgetPage.tsx", import.meta.url))],
  bundle: true, write: false, platform: "node", format: "cjs", jsx: "automatic", external: ["react", "react-dom", "exceljs"], loader: { ".css": "empty" }, logLevel: "silent" });
const compile = loader => { const module = { exports: {} }; new Function("require", "module", "exports", bundled.outputFiles[0].text)(loader, module, module.exports); return module.exports; };
const { NextYearOverview, NextYearTopMetrics, NextYearTaskNavigation, nextYearExportColumns, nextYearExportRows, nextYearClassStatus, nextYearRateUsage,
  effectiveNextYearEntry, parseNextYearParameterPaste, nextYearImpactRows, nextYearAnalysisFacts, nextYearTableData, nextYearQuarterInputValue, NextYearClassReadinessPanel, nextYearClassIssueRows, NextYearClassRepairPanel, canApplyNextYearClassRepair } = compile(require);
const identity = { userId: "local-budget-owner", departmentId: "trial", roles: ["DEPARTMENT_BUDGET_OWNER"] };
const seasons = ["暑假", "秋季", "寒假", "春季"];
const filters = { season: "", track: "", campus: "", product: "", grade: "", subject: "" };
const source = { id: "forecast-P16", sequence: 16, as_of: "2026-10-06", status: "CONFIRMED", forecast_revenue: "400000", forecast_total: 400, calculation_hash: "forecast-hash", snapshot_hash: "source-hash", baseline_hash: "baseline-hash" };
const combination = (overrides = {}) => ({ id: "combo-1", track: "智慧空间", product: "常规班", grade: "七年级", subject: "数学", campus: "西直门", entry: true, enabled: true, source_entry_weight: 100, source_new_by_season: Object.fromEntries(seasons.map(s => [s, 25])), opening_carryover_yuan: "0.000000", ...overrides });
const inputs = (overrides = {}) => ({ fiscal_year: 2028, source_as_of: "2026-10-06", target_revenue_yuan: "400000", track_targets: { 智慧空间: "400000" }, entry_total: 100, tax_factor: "1.06", combinations: [combination()],
  rates: seasons.map((season, i) => ({ id: `rate-${i}`, season, track: "智慧空间", product: "常规班", grade: "七年级", campus: "西直门", renewal_ppm: 800000, share_ppm: 250000 })),
  finance: seasons.map((season, i) => ({ id: `finance-${i}`, combination_id: "combo-1", season, price_yuan: "1000", discount_ppm: 900000, recognition_ppm: 1000000 })),
  class_sizes: [{ track: "智慧空间", product: "常规班", minimum: 10 }], plans: seasons.map(season => ({ combination_id: "combo-1", season, students: 25 })), opening_sources: [], notes: [], ...overrides });
const row = (overrides = {}) => ({ ...combination(), combination_id: "combo-1", season: "暑假", old_students: 80, new_students: 20, total_students: 100, revenue_yuan: "90000.000000", price_yuan: "1000", discount_ppm: 900000, renewal_ppm: 800000, share_ppm: 250000, class_size_status: "PASS", planned_new_applied: false, ...overrides });
const totals = (overrides = {}) => ({ revenue_yuan: "360000", target_revenue_yuan: "400000", gap_yuan: "40000", total_students: 400, old_students: 320, new_students: 80, attainment: ".9", ...overrides });
const calculation = (overrides = {}) => ({ fiscal_year: 2028, rule_versions: {}, annual: totals(), quarters: seasons.map(season => ({ season, ...totals({ revenue_yuan: "90000", target_revenue_yuan: null, gap_yuan: null, total_students: 100, old_students: 80, new_students: 20 }) })), tracks: [{ track: "智慧空间", ...totals() }], campuses: [{ campus: "西直门", ...totals({ target_revenue_yuan: null, gap_yuan: null }) }], rows: seasons.map(season => row({ season })), entry_allocations: [{ campus: "西直门", students: 100, source_weight: 100 }], opening: { spring_students: 100, eligible_students: 80, graduated_students: 20, subject_ended_students: 0, summer_old_students: 64, carryover_yuan: "0" }, issues: [], complete: true, publication_available: false, ...overrides });
const draft = (overrides = {}) => ({ id: "next-year-N1", sequence: 1, parent_id: null, status: "DRAFT", source, inputs: inputs(), calculation: calculation(), created_at: "2026-10-07T00:00:00Z", ...overrides });
const envelope = (overrides = {}) => ({ source, draft: draft(), source_changed: false, history: [], ...overrides });
const text = html => html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
const renderOverview = (data = calculation(), scope = filters) => renderToStaticMarkup(React.createElement(NextYearOverview, { calculation: data, filters: scope }));

test("API uses an independent next-year ledger and exact saved forecast/parent, with LAN-safe request keys", async () => {
  const original = globalThis.fetch, requests = [];
  globalThis.fetch = async (url, options = {}) => { requests.push({ url, ...options }); return Response.json(url.endsWith("/preview") ? { source, calculation: calculation() } : envelope()); };
  try {
    const signal = new AbortController().signal;
    await getNextYearBudget(identity, signal); await prepareNextYearBudget(identity, source.id, null);
    await reviseNextYearBudget(identity, "next-year-N1", inputs(), "明确调整目标");
    const preview = await previewNextYearBudget(identity, "next-year-N1", inputs());
    assert.equal(preview.source.id, source.id); assert.equal(preview.calculation.annual.total_students, 400);
    assert.deepEqual(requests.map(r => r.url), ["current", "prepare", "revise", "preview"].map(p => `/api/v1/next-year-budgets/fy28/${p}`));
    assert.equal(requests[0].signal, signal); assert.equal(new Headers(requests[0].headers).get("X-User-ID"), identity.userId);
    assert.deepEqual(JSON.parse(requests[1].body), { forecast_id: source.id, parent_id: null, reason: "基于已确认 FY27 预测生成 FY28 预算草稿" });
    assert.deepEqual(JSON.parse(requests[3].body), { parent_id: "next-year-N1", inputs: inputs() });
    for (const r of requests.slice(1)) { assert.equal(r.method, "POST"); assert.match(r.headers["Idempotency-Key"], /^[a-f0-9-]{36}$/); }
    assert.doesNotMatch(requests.map(r => r.url).join(" "), /annual-budgets|publish|confirm|revisions/);
  } finally { globalThis.fetch = original; }
});
test("API surfaces short backend refusal text and never retries a failed save", async () => {
  const original = globalThis.fetch; let calls = 0;
  globalThis.fetch = async () => { calls++; return Response.json({ detail: "来源已变化，请重新读取" }, { status: 409 }); };
  try { await assert.rejects(reviseNextYearBudget(identity, "N1", inputs(), "原因"), /来源已变化/); assert.equal(calls, 1); }
  finally { globalThis.fetch = original; }
});
test("editable input projection preserves original and read-only lineage, using exact percentage steps", () => {
  const original = inputs(), before = JSON.stringify(original);
  const result = applyNextYearEdits(original, { [key("entry")]: "0", [key("tax")]: "1.05", [key("rates", "rate-0", "renewal_ppm")]: "70.13", [key("rates", "rate-0", "share_ppm")]: "99.99", [key("finance", "finance-0", "discount_ppm")]: "88.1", [key("finance", "finance-0", "recognition_ppm")]: "67.1234", [key("finance", "finance-0", "price_yuan")]: "1234.567890", [key("class", 0)]: "12", [key("plan", "combo-1", "秋季")]: "21" });
  assert.deepEqual(result.errors, {}); assert.equal(result.inputs.entry_total, 0); assert.equal(result.inputs.rates[0].renewal_ppm, 701300); assert.equal(result.inputs.rates[0].share_ppm, 999900);
  assert.equal(result.inputs.finance[0].discount_ppm, 881000); assert.equal(result.inputs.finance[0].recognition_ppm, 671234); assert.equal(result.inputs.finance[0].price_yuan, "1234.567890");
  assert.equal(result.inputs.plans[1].students, 21); assert.equal(result.inputs.class_sizes[0].minimum, 12); assert.equal(result.inputs.source_as_of, original.source_as_of);
  assert.equal(JSON.stringify(original), before);
});
test("input validation rejects absent, fractional counts, incompatible steps, nonfinite and excess limits without coercion", () => {
  const invalid = [[key("entry"), "-1"], [key("entry"), "1.2"], [key("entry"), "10000001"], [key("tax"), "0"], [key("tax"), "10.001"], [key("finance", "finance-0", "price_yuan"), "1000000001"], [key("finance", "finance-0", "discount_ppm"), "88.11"], [key("rates", "rate-0", "renewal_ppm"), "70.131"], [key("rates", "rate-0", "share_ppm"), "100"], [key("target"), "Infinity"], [key("target"), "-1"], [key("source_as_of"), "2027-01-01"], [key("finance", "finance-0", "combination_id"), "different"], ["not-json", "1"]];
  for (const [path, value] of invalid) { const result = applyNextYearEdits(inputs(), { [path]: value }); assert.equal(result.inputs, null, path + value); assert.ok(result.errors[path], path + value); }
});
test("one-click goal allocation follows original weights exactly, with deterministic remainder and no revenue/count changes", () => {
  assert.deepEqual(allocateNextYearTrackTargets("100.000001", { B: "30", A: "70" }), { A: "70.000001", B: "30.000000" });
  assert.deepEqual(allocateNextYearTrackTargets("1", { B: "0", A: "0", C: "0" }), { A: "0.333334", B: "0.333333", C: "0.333333" });
  assert.deepEqual(allocateNextYearTrackTargets("0e999999999", { A: "1" }), { A: "0.000000" });
  assert.deepEqual(allocateNextYearTrackTargets("1e-999999999", { A: "1" }), { A: "0.000000" });
  const original = inputs(), target = "450000.123456", goals = allocateNextYearTrackTargets(target, original.track_targets);
  const result = applyNextYearEdits(original, { [key("target")]: target, ...Object.fromEntries(Object.entries(goals).map(([name, value]) => [key("track-target", name), value])) });
  assert.ok(result.inputs); assert.equal(result.inputs.entry_total, 100); assert.deepEqual(result.inputs.finance, original.finance); assert.deepEqual(result.inputs.rates, original.rates);
  assert.equal(applyNextYearEdits(original, { [key("target")]: "450000" }).inputs, null, "explicit goal mismatch cannot silently pass");
});
test("selected totals retain zero, negatives, small precision and unknown children distinctly", () => {
  assert.equal(nextYearRowTotal([row({ revenue_yuan: "0" }), row({ revenue_yuan: "-.03" })], "revenue_yuan"), -.03);
  assert.equal(nextYearRowTotal([row(), row({ revenue_yuan: null })], "revenue_yuan"), null);
  assert.equal(nextYearRowTotal([], "total_students"), null);
  assert.equal(nextYearRowTotal([row({ revenue_yuan: 1e16 }), row({ revenue_yuan: 1 }), row({ revenue_yuan: -1e16 })], "revenue_yuan"), 1);
});
test("unset operating goal permits other edits and preserves a genuine numeric zero", () => {
  const original = inputs({ target_revenue_yuan: null, track_targets: { 智慧空间: null } });
  const changed = applyNextYearEdits(original, { [key("entry")]: "125", [key("target")]: " " });
  assert.deepEqual(changed.errors, {}); assert.equal(changed.inputs.entry_total, 125);
  assert.equal(changed.inputs.target_revenue_yuan, null); assert.deepEqual(changed.inputs.track_targets, { 智慧空间: null });
  const specified = applyNextYearEdits(original, { [key("target")]: "0" });
  assert.deepEqual(specified.errors, {}); assert.equal(specified.inputs.target_revenue_yuan, "0"); assert.equal(specified.inputs.track_targets.智慧空间, null);
});
test("manual annual goal can precede track targets; partial and mismatching track targets remain invalid", () => {
  const original = inputs({ target_revenue_yuan: null, track_targets: { 智慧空间: null, 智慧伴学: null } });
  const annualOnly = applyNextYearEdits(original, { [key("target")]: "600000" });
  assert.deepEqual(annualOnly.errors, {}); assert.equal(annualOnly.inputs.target_revenue_yuan, "600000");
  assert.deepEqual(annualOnly.inputs.track_targets, { 智慧空间: null, 智慧伴学: null });
  assert.equal(applyNextYearEdits(original, { [key("track-target", "智慧空间")]: "300000" }).inputs, null);
  const partial = applyNextYearEdits(original, { [key("target")]: "600000", [key("track-target", "智慧空间")]: "300000" });
  assert.match(partial.errors[key("track-target", "智慧伴学")], /填写全部赛道目标/);
  const complete = applyNextYearEdits(original, { [key("target")]: "600000", [key("track-target", "智慧空间")]: "300000", [key("track-target", "智慧伴学")]: "300000" });
  assert.deepEqual(complete.errors, {}); assert.equal(complete.inputs.entry_total, original.entry_total); assert.deepEqual(complete.inputs.finance, original.finance);
  assert.equal(applyNextYearEdits(complete.inputs, { [key("target")]: "600001" }).inputs, null);
});
test("unset goal removes comparison while manually supplied goal restores its own difference", () => {
  const missing = totals({ target_revenue_yuan: null, gap_yuan: null, attainment: null });
  const withoutTarget = renderOverview(calculation({ annual: missing, tracks: [{ track: "智慧空间", ...missing }] }));
  assert.match(withoutTarget, /经营目标待填写/); assert.match(text(withoutTarget), /全年预算结转收入（万元） 36/);
  assert.doesNotMatch(withoutTarget, /全年经营目标|目标差额|目标（万元）|差额（万元）/);
  const supplied = renderOverview(calculation({ annual: totals({ target_revenue_yuan: "600000", gap_yuan: "240000" }), tracks: [{ track: "智慧空间", ...missing }] }));
  assert.match(text(supplied), /全年经营目标 60万元 目标差额 24万元/);
  assert.doesNotMatch(supplied, /目标（万元）|差额（万元）/);
});
test("overview uses computed goals, business-order quarters and Chinese class status without publishing language", () => {
  const html = renderOverview();
  assert.match(text(html), /全年预算结转收入（万元） 36 总人次 400 老生人次 320 新生人次 80/);
  assert.match(text(html), /全年经营目标 40万元 目标差额 4万元/);
  let previous = -1; for (const season of seasons) { const next = html.indexOf(`<h3>${season}</h3>`); assert.ok(next > previous); previous = next; }
  assert.doesNotMatch(html, />PASS</); assert.match(html, /达标/);
  assert.equal(nextYearClassStatus("BELOW_MINIMUM_CLASS_SIZE"), "未达班均"); assert.equal(nextYearClassStatus("NO_PLANNED_CLASS"), "未计划开班");
  assert.doesNotMatch(html, /已定版|发布成功/);
});
test("next-year carryover revenue labels retain yuan and wan units without changing source fields", () => {
  const data = calculation(), before = JSON.stringify(data), html = renderOverview(data);
  for (const label of ["全年预算结转收入（万元）", "预算结转收入（万元）", "结转收入（万元）", "结转收入（元）"]) assert.ok(text(html).includes(label), label);
  assert.deepEqual(nextYearExportColumns.find(column => column.key === "revenue_yuan"), { key: "revenue_yuan", label: "预算结转收入（元）", format: "money" });
  assert.equal(nextYearExportRows(data.rows)[0].revenue_yuan, data.rows[0].revenue_yuan);
  assert.doesNotMatch(html, /确认收入|预算收入|当前范围收入|全年收入|结转结转收入/);
  assert.equal(JSON.stringify(data), before);
});
test("filtered overview does not compare a single-quarter result with full-year targets or zero-fill other quarters", () => {
  const html = renderOverview(calculation(), { ...filters, season: "秋季" });
  assert.match(text(html), /当前范围结转收入（万元） 9 总人次 100/); assert.doesNotMatch(html, /全年经营目标|目标差额|全年预算结转收入/);
  assert.match(html, /<h3>暑假<\/h3>[\s\S]*?<strong>—<\/strong>/); assert.match(html, /<h3>秋季<\/h3>[\s\S]*?<strong>9<\/strong>/);
  const matrix = html.match(/aria-label="赛道预算对比"[\s\S]*?<tbody>([\s\S]*?)<\/tbody>/)?.[1]; assert.match(text(matrix), /智慧空间 9 100/); assert.doesNotMatch(html, /目标（万元）|差额（万元）/);
});
test("overview distinguishes negative, zero, unknown and empty ranges in visible numeric results", () => {
  for (const [amount, expected] of [["0", "0"], ["-12500", "-1.25"], [null, "—"]]) {
    const html = renderOverview(calculation({ annual: totals({ revenue_yuan: amount }), rows: [row({ revenue_yuan: amount })] }));
    assert.match(text(html), new RegExp(`全年预算结转收入（万元） ${expected}`));
  }
  const empty = renderOverview(calculation(), { ...filters, campus: "不存在的校区" }); assert.match(empty, /当前范围没有预算结果/); assert.match(text(empty), /当前范围结转收入（万元） —/);
});
test("five tasks are same-page controls, never links into the legacy FY28 workspace", () => {
  const html = renderToStaticMarkup(React.createElement(NextYearTaskNavigation, { task: 5, onChange() {} }));
  assert.equal((html.match(/<button /g) ?? []).length, 5); assert.equal((html.match(/aria-current="page"/g) ?? []).length, 1); assert.doesNotMatch(html, /href=/);
  for (const name of ["定目标和SKU", "入口新生定多少", "续班率、新生占比", "学费、折扣、班额", "看结果 / 对目标"]) assert.match(html, new RegExp(name));
  const page = fs.readFileSync(new URL("../src/NextYearBudgetPage.tsx", import.meta.url), "utf8");
  assert.match(page, /useState<Task>\(readTask\)/); assert.match(page, /nyTask/); assert.match(page, /history\.pushState/); assert.match(page, /popstate/);
  assert.doesNotMatch(page, /R150|reviseForecast|confirmForecast|publishBudget|deriveRevision|年度预算基线已发布/);
  const app = fs.readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8"); assert.match(app, /path === "\/budget\/next-year"/);
});
test("XLSX output is full typed detail in yuan, ratios numeric, null blank and zero/negative retained", async () => {
  const rows = Array.from({ length: 31 }, (_, n) => row({ revenue_yuan: n === 0 ? "0" : n === 1 ? "-12.25" : "1234.567890", old_students: n === 2 ? null : 80, discount_ppm: n === 3 ? null : 900000 }));
  const workbook = await budgetWorkbook(nextYearExportColumns, nextYearExportRows(rows));
  const bytes = await workbook.xlsx.writeBuffer(), ExcelJS = require("exceljs"), recovered = new ExcelJS.Workbook(); await recovered.xlsx.load(bytes);
  const sheet = recovered.worksheets[0], index = name => nextYearExportColumns.findIndex(c => c.key === name) + 1;
  assert.equal(sheet.rowCount, 32); assert.equal(sheet.getCell(2, index("revenue_yuan")).value, 0); assert.equal(sheet.getCell(3, index("revenue_yuan")).value, -12.25);
  assert.equal(sheet.getCell(4, index("revenue_yuan")).value, 1234.567890); assert.equal(typeof sheet.getCell(4, index("revenue_yuan")).value, "number");
  assert.equal(sheet.getCell(4, index("old_students")).value, null); assert.equal(sheet.getCell(5, index("discount")).value, null); assert.equal(sheet.getCell(2, index("discount")).value, .9);
  assert.equal(sheet.getCell(2, index("class_size_status")).value, "达标");
});

// A pure hook harness exercises event behavior against mocked fetch. Effects do not run;
// this is not a substitute for browser focus, layout, or real back/forward acceptance.
const nodes = tree => Array.isArray(tree) ? tree.flatMap(nodes) : !tree || typeof tree !== "object" ? [] : [tree, ...nodes(tree.props?.children),
  ...nodes(typeof tree.type === "function" && tree.type.name === "PagedTable" ? tree.props?.section?.actions : undefined),
  ...nodes(tree.props?.changes), ...nodes(tree.props?.classes)];
function pageHarness({ stateData = envelope(), edited = {}, changedReason = "", activeTask = 5, activeIdentity = identity, activePreview = null, restoreFilters = false } = {}) {
  const state = [activeIdentity, stateData, structuredClone(stateData.draft?.inputs ?? null), edited, "", "", "", activePreview, changedReason, activeTask, restoreFilters ? undefined : { ...filters }, {}, "", false];
  const refs = [], effects = []; let cursor = 0, refCursor = 0;
  const mockReact = { ...React, useState(initial) { const i = cursor++; return [state[i] === undefined ? typeof initial === "function" ? initial() : initial : state[i], value => { state[i] = typeof value === "function" ? value(state[i]) : value; }]; }, useEffect(fn) { effects.push(fn); }, useMemo(fn) { return fn(); }, useRef(initial) { const i = refCursor++; return refs[i] ??= { current: initial }; } };
  const { NextYearBudgetPage } = compile(name => name === "react" ? mockReact : require(name));
  return { state, effects, render() { cursor = 0; refCursor = 0; effects.length = 0; return NextYearBudgetPage(); }, html() { return renderToStaticMarkup(this.render()); } };
}
const fakeBrowser = () => {
  const saved = { location: globalThis.location, history: globalThis.history, window: globalThis.window, requestAnimationFrame: globalThis.requestAnimationFrame };
  globalThis.location = { search: "", href: "http://127.0.0.1:5188/budget/next-year" };
  globalThis.history = { pushState() {}, replaceState(state) { this.state = state; }, state: {} }; globalThis.window = { location: { assign() {} }, scrollX: 0, scrollY: 0 }; globalThis.requestAnimationFrame = fn => fn();
  return () => { for (const [key, value] of Object.entries(saved)) { if (value === undefined) delete globalThis[key]; else globalThis[key] = value; } };
};
const button = (tree, label) => nodes(tree).find(n => n.type === "button" && text(renderToStaticMarkup(n)) === label);
const scopeControl = harness => nodes(harness.render()).find(node => typeof node.type === "function" && node.type.name === "BudgetScopeBar");
const openVersions = harness => button(harness.render(), "版本记录").props.onClick();
const openProblems = harness => nodes(harness.render()).find(node => node.type === "button" && node.props.onClick && text(renderToStaticMarkup(node)).startsWith("待处理")).props.onClick();
const checkClasses = harness => { openProblems(harness); classPanel(harness.render()).props.onCheck(); };
const versionPanel = harness => nodes(harness.render()).find(node => typeof node.type === "function" && node.type.name === "BudgetUtilityPanel" && node.props.title === "版本记录");
const comparisonPanel = harness => nodes(versionPanel(harness)?.props.children).find(node => typeof node.type === "function" && node.type.name === "ImpactComparison");
const startComparison = harness => { openVersions(harness); button(harness.render(), "对比上版").props.onClick(); };

test("phase 2 opening and closing versions or pending work is inspection-only and preserves raw edits, notes and unstaged paste", async () => {
  const restore = fakeBrowser(), original = globalThis.fetch; let requests = 0;
  globalThis.fetch = async () => { requests++; throw new Error("inspection must not request a calculation or create a version"); };
  try {
    const h = pageHarness({ activeTask: 3, edited: { [key("rates", "rate-1", "renewal_ppm")]: "81.230" }, changedReason: "保留精度与备注" });
    button(h.render(), "粘贴表格").props.onClick();
    nodes(h.render()).find(node => node.type === "textarea").props.onChange({ target: { value: "rate-2\t82\t26" } });
    const before = JSON.stringify([h.state[1], h.state[2], h.state[3], h.state[8], h.state[20]]);
    openVersions(h); assert.ok(versionPanel(h));
    assert.equal(nodes(versionPanel(h).props.children).find(node => node.type === "select" && node.props["aria-label"] === "基准预测版本").props.disabled, true);
    assert.match(h.html(), /先保存或放弃修改，再切换版本或更换基准/);
    versionPanel(h).props.onClose(); assert.equal(versionPanel(h), undefined);
    openProblems(h); assert.ok(classPanel(h.render()));
    const problem = nodes(h.render()).find(node => node.type?.name === "BudgetUtilityPanel" && node.props.title === "待处理");
    problem.props.onClose(); assert.equal(classPanel(h.render()), undefined);
    assert.equal(JSON.stringify([h.state[1], h.state[2], h.state[3], h.state[8], h.state[20]]), before);
    await flush(); assert.equal(requests, 0);
  } finally { globalThis.fetch = original; restore(); }
});

test("phase 2 pending work exposes class checks, blocking details and warning details in one panel without rewriting the saved result", () => {
  const restore = fakeBrowser();
  try {
    const issues = [{ code: "BELOW_MINIMUM_CLASS_SIZE", severity: "WARNING", combination_id: "combo-1", season: "秋季", message: "成班人数不足" },
      { code: "MISSING_PARAMETER", severity: "BLOCKING", combination_id: "combo-1", season: "寒假", message: "寒假人数参数未填写" },
      { code: "BASELINE_NEGATIVE_VALUE", severity: "WARNING", combination_id: "combo-1", season: "春季", message: "保留原负数" }];
    const h = pageHarness({ stateData: envelope({ draft: draft({ calculation: calculation({ issues }) }) }) }), before = JSON.stringify(h.state[1]);
    assert.equal(classPanel(h.render()), undefined); assert.equal(paged(h.render(), "预算提醒明细"), undefined);
    openProblems(h); assert.ok(classPanel(h.render()));
    assert.deepEqual(paged(h.render(), "预算待处理明细").props.rows, [issues[1]]);
    assert.deepEqual(paged(h.render(), "预算提醒明细").props.rows, [issues[2]]);
    button(pagedRow(h.render(), "预算提醒明细"), "查看SKU").props.onClick();
    assert.equal(h.state[9], 1); assert.equal(classPanel(h.render()), undefined);
    assert.deepEqual(h.state[10], { ...filters, season: "春季", track: "智慧空间", campus: "西直门", product: "常规班", grade: "七年级", subject: "数学" });
    assert.equal(JSON.stringify(h.state[1]), before); assert.deepEqual(h.state[3], {});
  } finally { restore(); }
});

test("legacy saved business-scope notes and issues render as SKU without modifying the saved envelope", () => {
  const restore = fakeBrowser();
  try {
    const issue = { code: "MISSING_PARAMETER", severity: "BLOCKING", combination_id: "combo-1", season: "秋季", message: "业务范围待核对" };
    const fixture = envelope({ draft: draft({ inputs: inputs({ notes: [{ message: "业务范围按已保存版本沿用" }] }), calculation: calculation({ issues: [issue] }) }) });
    const before = JSON.stringify(fixture), h = pageHarness({ stateData: fixture });
    assert.match(h.html(), /SKU按已保存版本沿用/);
    assert.doesNotMatch(h.html(), /业务范围/);
    openProblems(h);
    const row = renderToStaticMarkup(pagedRow(h.render(), "预算待处理明细"));
    assert.match(row, /SKU待核对/); assert.doesNotMatch(row, /业务范围/);
    assert.deepEqual(paged(h.render(), "预算待处理明细").props.rows, [issue]);
    assert.equal(JSON.stringify(fixture), before);
    assert.deepEqual(h.state[3], {});
  } finally { restore(); }
});

test("phase 2 basis preview is readonly, preserves the parent and manual target, and only explicit confirmation creates a new draft", async () => {
  const restore = fakeBrowser(), original = globalThis.fetch, requests = [];
  try {
    const older = { ...source, id: "forecast-P3", sequence: 3, fiscal_year: 2027, forecast_revenue: "765432.123456", snapshot_hash: "snapshot-P3" };
    const noGoal = inputs({ target_revenue_yuan: null, track_targets: { 智慧空间: null } }), after = calculation({ annual: totals({ target_revenue_yuan: null, gap_yuan: null }) });
    const h = pageHarness({ activeTask: 1 }); h.state[14] = { source_fiscal_year: 2027, sources: [source, older], fiscal_years: [{ fiscal_year: 2028 }] };
    const originalSaved = JSON.stringify([h.state[1], h.state[2], h.state[3]]);
    openVersions(h); nodes(h.render()).find(node => node.type === "select" && node.props["aria-label"] === "基准预测版本").props.onChange({ target: { value: older.id } });
    assert.equal(button(h.render(), "以此预测生成新草稿"), undefined); assert.equal(requests.length, 0);
    globalThis.fetch = async (url, options) => {
      requests.push({ url, method: options.method, headers: options.headers, body: JSON.parse(options.body) });
      return Response.json(url.endsWith("/prepare-preview") ? { read_only: true, fiscal_year: 2028, parent_id: "next-year-N1", source_id: older.id, source: older, inputs: noGoal, calculation: after }
        : envelope({ source: older, draft: draft({ id: "next-year-N2", sequence: 2, parent_id: "next-year-N1", source: older, inputs: noGoal, calculation: after }) }));
    };
    button(h.render(), "预览基准变化").props.onClick(); await flush();
    assert.deepEqual(requests.map(item => item.url), ["/api/v1/next-year-budgets/fy28/prepare-preview"]);
    assert.deepEqual(requests[0].body, { forecast_id: older.id, parent_id: "next-year-N1" });
    assert.equal(JSON.stringify([h.state[1], h.state[2], h.state[3]]), originalSaved);
    assert.match(h.html(), /基准变化预览 · 未生成草稿/); assert.deepEqual(comparisonPanel(h).props.after, after);
    assert.equal(h.state[2].target_revenue_yuan, "400000", "a readonly preview must not replace the manually supplied current target");
    button(h.render(), "以此预测生成新草稿").props.onClick(); await flush();
    assert.equal(requests.length, 2); assert.equal(requests[1].url, "/api/v1/next-year-budgets/fy28/prepare");
    assert.equal(requests[1].body.parent_id, "next-year-N1"); assert.equal(requests[1].body.forecast_id, older.id);
    assert.match(requests[1].headers["Idempotency-Key"], /^[a-f0-9-]{36}$/);
    assert.equal(h.state[1].draft.sequence, 2); assert.equal(h.state[1].draft.source.snapshot_hash, "snapshot-P3");
    assert.equal(h.state[2].target_revenue_yuan, null); assert.equal(h.state[2].entry_total, 100);
  } finally { globalThis.fetch = original; restore(); }
});

test("phase 2 basis preview rejects mismatched source, snapshot, parent or year and cannot expose a prepare action", async () => {
  const restore = fakeBrowser(), original = globalThis.fetch;
  try {
    const valid = { read_only: true, fiscal_year: 2028, parent_id: "next-year-N1", source_id: source.id, source, inputs: inputs(), calculation: calculation() };
    for (const change of [ { read_only: false }, { parent_id: "wrong-N" }, { source_id: "wrong-P" }, { source: { ...source, id: "wrong-P" } },
      { source: { ...source, snapshot_hash: "different-snapshot" } }, { fiscal_year: 2029 }, { inputs: inputs({ fiscal_year: 2029 }) }, { calculation: calculation({ fiscal_year: 2029 }) } ]) {
      const h = pageHarness(), before = JSON.stringify([h.state[1], h.state[2], h.state[3]]), requests = [];
      globalThis.fetch = async (url, options) => { requests.push({ url, method: options.method }); return Response.json({ ...valid, ...change }); };
      openVersions(h); button(h.render(), "预览基准变化").props.onClick(); await flush();
      assert.equal(requests.length, 1); assert.match(requests[0].url, /prepare-preview$/);
      assert.match(h.state[5], /基准预览与所选版本不一致/); assert.equal(button(h.render(), "以此预测生成新草稿"), undefined);
      assert.equal(JSON.stringify([h.state[1], h.state[2], h.state[3]]), before);
    }
  } finally { globalThis.fetch = original; restore(); }
});

test("phase 2 changing the selected source expires the preview and a late response cannot enable stale preparation", async () => {
  const restore = fakeBrowser(), original = globalThis.fetch; let resolvePreview; const requests = [];
  try {
    const other = { ...source, id: "forecast-P3", sequence: 3, snapshot_hash: "snapshot-P3", fiscal_year: 2027 };
    const h = pageHarness(); h.state[14] = { source_fiscal_year: 2027, sources: [source, other], fiscal_years: [{ fiscal_year: 2028 }] };
    globalThis.fetch = (url, options) => { requests.push({ url, body: JSON.parse(options.body) }); return new Promise(resolve => { resolvePreview = resolve; }); };
    const select = () => nodes(h.render()).find(node => node.type === "select" && node.props["aria-label"] === "基准预测版本");
    openVersions(h); button(h.render(), "预览基准变化").props.onClick();
    assert.equal(select().props.disabled, true, "the UI itself blocks changing basis during a preview");
    // Simulate an external catalog/source selection change despite the disabled control.
    select().props.onChange({ target: { value: other.id } });
    resolvePreview(Response.json({ read_only: true, fiscal_year: 2028, parent_id: "next-year-N1", source_id: source.id, source, inputs: inputs(), calculation: calculation() })); await flush();
    assert.equal(select().props.value, other.id); assert.equal(button(h.render(), "以此预测生成新草稿"), undefined);
    assert.equal(requests.length, 1); assert.match(requests[0].url, /prepare-preview$/);
    assert.equal(h.state[1].draft.id, "next-year-N1"); assert.equal(h.state[1].draft.source.id, source.id);
  } finally { globalThis.fetch = original; restore(); }
});
test("initial page never creates a draft; no confirmed source has one clear forecast link", () => {
  const restore = fakeBrowser();
  try { const harness = pageHarness({ stateData: envelope({ source: null, draft: null }) }); const html = harness.html(); assert.match(html, /先确认 FY27 预测/); assert.match(html, /前往达成预测/); assert.doesNotMatch(html, /生成 FY28 草稿/); }
  finally { restore(); }
});
test("saved draft and unsaved preview are distinct; changing inputs shows the original results as pending recalculation", () => {
  const restore = fakeBrowser();
  try {
    const base = pageHarness().html(); assert.match(base, /FY27 预测 P16/); assert.match(base, /已保存 N1/);
    const stale = pageHarness({ edited: { [key("entry")]: "0" } }).html(); assert.match(stale, /待重算 · 原结果 N1/); assert.match(text(stale), /全年结转收入（万元） 36/);
    const preview = pageHarness({ edited: { [key("entry")]: "0" }, activePreview: calculation({ annual: totals({ revenue_yuan: "200000" }) }) }).html(); assert.match(preview, /未保存预览/); assert.match(text(preview), /全年结转收入（万元） 20/); assert.doesNotMatch(preview, /已定版/);
  } finally { restore(); }
});
test("only applied zero-old plans can be edited; source dimensions stay read-only and viewer cannot save", () => {
  const restore = fakeBrowser();
  try {
    const fixture = envelope({ draft: draft({ calculation: calculation({ rows: [row({ season: "暑假", planned_new_applied: true }), row({ season: "秋季", planned_new_applied: true, old_students: 0 }), row({ season: "春季", planned_new_applied: false })] }) }) });
    const harness = pageHarness({ stateData: fixture, activeTask: 3 }); button(harness.render(), "计划新生").props.onClick();
    const html = harness.html(); assert.match(html, /计划新生 · 零老生范围/);
    assert.match(html, /秋季 西直门 \/ 智慧空间 \/ 常规班 \/ 七年级 \/ 数学计划新生人次/);
    assert.doesNotMatch(html, /暑假 西直门 \/ 智慧空间 \/ 常规班 \/ 七年级 \/ 数学计划新生人次/);
    assert.doesNotMatch(html, /春季 西直门 \/ 智慧空间 \/ 常规班 \/ 七年级 \/ 数学计划新生人次/);
    const viewer = pageHarness({ activeIdentity: { ...identity, roles: [] }, activeTask: 4 }).html(); assert.doesNotMatch(viewer, /保存新版本/); assert.match(viewer, /disabled=""/);
  } finally { restore(); }
});
test("summer entry-only cohorts do not expose ineffective share or absent opening-renewal edits", () => {
  const base = inputs(), summer = base.rates[0];
  assert.deepEqual(nextYearRateUsage(base, summer), { renewal: false, share: false });
  assert.deepEqual(nextYearRateUsage(base, base.rates[1]), { renewal: true, share: true });
  const opening = inputs({ opening_sources: [{ target_id: "combo-1", students: 50 }] });
  assert.deepEqual(nextYearRateUsage(opening, summer), { renewal: true, share: false });
  const mixed = inputs({ combinations: [combination(), combination({ id: "combo-2", subject: "英语", entry: false })] });
  assert.deepEqual(nextYearRateUsage(mixed, summer), { renewal: false, share: true });
  const restore = fakeBrowser(); try { const html = pageHarness({ activeTask: 3 }).html(); assert.match(html, /入口分配/); assert.doesNotMatch(html, /暑假 西直门 \/ 智慧空间 \/ 常规班 \/ 七年级续班率/); } finally { restore(); }
});
test("invalid edits are retained and routed to the owning task without submitting requests", async () => {
  const restore = fakeBrowser(), original = globalThis.fetch; let calls = 0;
  globalThis.fetch = async () => { calls++; throw new Error("should not fetch"); };
  try {
    const harness = pageHarness({ edited: { [key("rates", "rate-0", "renewal_ppm")]: "70.131" }, changedReason: "原因" });
    button(harness.render(), "保存新版本").props.onClick();
    assert.equal(harness.state[9], 3); assert.match(harness.state[5], /续班率、新生占比：按0.01/); assert.equal(harness.state[3][key("rates", "rate-0", "renewal_ppm")], "70.131"); assert.equal(calls, 0);
  } finally { globalThis.fetch = original; restore(); }
});
test("unsaved module navigation opens local leave protection without saving or navigating", () => {
  const restore = fakeBrowser();
  try {
    const harness = pageHarness({ edited: { [key("entry")]: "10" } }); const tree = harness.render();
    const header = nodes(tree).find(node => typeof node.type === "function" && node.type.name === "FY27Header"); let prevented = 0;
    header.props.onBeforeLeave({ button: 0, currentTarget: { href: "http://127.0.0.1:5188/budget/forecast?archive=fy27" }, preventDefault() { prevented++; } });
    assert.equal(prevented, 1); assert.equal(harness.state[12], "http://127.0.0.1:5188/budget/forecast?archive=fy27"); assert.equal(harness.state[3][key("entry")], "10");
    assert.match(harness.html(), /修改未保存/); assert.match(harness.html(), /继续编辑/);
  } finally { restore(); }
});
test("preview response displays unsaved calculation only and prevents concurrent command submission", async () => {
  const restore = fakeBrowser(), original = globalThis.fetch; let calls = 0, finish;
  globalThis.fetch = async () => { calls++; return new Promise(resolve => { finish = resolve; }); };
  try {
    const harness = pageHarness({ edited: { [key("entry")]: "0" } }); const control = button(harness.render(), "查看影响");
    control.props.onClick(); control.props.onClick(); assert.equal(calls, 1); assert.equal(harness.state[4], "preview");
    finish(Response.json({ source, calculation: calculation({ annual: totals({ revenue_yuan: "100000" }) }) })); await new Promise(resolve => setImmediate(resolve));
    assert.equal(harness.state[1].draft.id, "next-year-N1"); assert.equal(harness.state[7].annual.revenue_yuan, "100000"); assert.equal(harness.state[3][key("entry")], "0"); assert.match(harness.html(), /未保存预览/);
  } finally { globalThis.fetch = original; restore(); }
});
test("failed save preserves edited raw numbers and reason; successful save accepts only the returned immutable next draft", async () => {
  const restore = fakeBrowser(), original = globalThis.fetch;
  try {
    const harness = pageHarness({ edited: { [key("entry")]: "105" }, changedReason: "新入口目标" });
    globalThis.fetch = async () => Response.json({ detail: "当前草稿已变化" }, { status: 409 });
    button(harness.render(), "保存新版本").props.onClick(); await new Promise(resolve => setImmediate(resolve));
    assert.equal(harness.state[3][key("entry")], "105"); assert.equal(harness.state[8], "新入口目标"); assert.equal(harness.state[1].draft.sequence, 1); assert.match(harness.state[5], /当前草稿已变化/);
    const next = envelope({ draft: draft({ id: "next-year-N2", sequence: 2, parent_id: "next-year-N1", inputs: inputs({ entry_total: 105 }) }) });
    globalThis.fetch = async () => Response.json(next);
    button(harness.render(), "保存新版本").props.onClick(); await new Promise(resolve => setImmediate(resolve));
    assert.equal(harness.state[1].draft.sequence, 2); assert.equal(harness.state[2].entry_total, 105); assert.deepEqual(harness.state[3], {}); assert.equal(harness.state[8], ""); assert.equal(harness.state[7], null);
  } finally { globalThis.fetch = original; restore(); }
});
test("clearing the annual goal explicitly clears track goals and previews without comparisons", async () => {
  const restore = fakeBrowser(), original = globalThis.fetch; let submitted;
  globalThis.fetch = async (_url, options) => { submitted = JSON.parse(options.body); return Response.json({ source, calculation: calculation({ annual: totals({ target_revenue_yuan: null, gap_yuan: null, attainment: null }), tracks: [{ track: "智慧空间", ...totals({ target_revenue_yuan: null, gap_yuan: null, attainment: null }) }] }) }); };
  try {
    const harness = pageHarness({ activeTask: 1 });
    const label = nodes(harness.render()).find(node => node.type === "label" && text(renderToStaticMarkup(node)).startsWith("全年经营目标（元）"));
    nodes(label).find(node => node.type === "input").props.onChange({ target: { value: "" } });
    assert.equal(harness.state[3][key("target")], ""); assert.equal(harness.state[3][key("track-target", "智慧空间")], "");
    button(harness.render(), "查看影响").props.onClick(); await new Promise(resolve => setImmediate(resolve));
    assert.equal(submitted.inputs.target_revenue_yuan, null); assert.deepEqual(submitted.inputs.track_targets, { 智慧空间: null });
    assert.equal(harness.state[1].draft.inputs.target_revenue_yuan, "400000", "preview cannot rewrite the saved goal");
    assert.equal(harness.state[9], 1, "preview leaves the user in the target task"); assert.equal(harness.state[7].annual.gap_yuan, null);
  } finally { globalThis.fetch = original; restore(); }
});
test("no goal does not block changing counts, previewing or saving; target fields remain blank", async () => {
  const restore = fakeBrowser(), original = globalThis.fetch, requests = [];
  const noGoal = inputs({ target_revenue_yuan: null, track_targets: { 智慧空间: null } });
  const result = calculation({ annual: totals({ target_revenue_yuan: null, gap_yuan: null, attainment: null }) });
  globalThis.fetch = async (url, options) => { requests.push({ url, body: JSON.parse(options.body) }); return Response.json(url.endsWith("/preview") ? { source, calculation: result } : envelope({ draft: draft({ sequence: 2, inputs: inputs({ ...noGoal, entry_total: 125 }), calculation: result }) })); };
  try {
    const harness = pageHarness({ stateData: envelope({ draft: draft({ inputs: noGoal, calculation: result }) }), edited: { [key("entry")]: "125" }, changedReason: "修改入口人数" });
    button(harness.render(), "查看影响").props.onClick(); await new Promise(resolve => setImmediate(resolve));
    assert.equal(requests[0].body.inputs.entry_total, 125); assert.equal(requests[0].body.inputs.target_revenue_yuan, null); assert.equal(harness.state[7].annual.revenue_yuan, "360000");
    button(harness.render(), "保存新版本").props.onClick(); await new Promise(resolve => setImmediate(resolve));
    assert.equal(requests[1].body.inputs.target_revenue_yuan, null); assert.equal(harness.state[1].draft.sequence, 2);
    harness.state[9] = 1; const tree = harness.render(), html = harness.html();
    const targetLabel = nodes(tree).find(node => node.type === "label" && text(renderToStaticMarkup(node)).startsWith("全年经营目标（元）"));
    assert.equal(nodes(targetLabel).find(node => node.type === "input").props.value, ""); assert.match(html, /FY27 预测 P16/);
    openVersions(harness); assert.match(harness.html(), /基准预测版本/); assert.match(text(harness.html()), /P16.*40 万元/);
    assert.equal(button(tree, "按预算结转收入分配赛道目标").props.disabled, true);
  } finally { globalThis.fetch = original; restore(); }
});
test("track allocation uses calculated budget income only after the annual target is manually specified", () => {
  const restore = fakeBrowser();
  try {
    const withoutTargets = inputs({ target_revenue_yuan: null, track_targets: { 智慧空间: null, 智慧伴学: null } });
    const fixture = envelope({ draft: draft({ inputs: withoutTargets, calculation: calculation({ tracks: [{ track: "智慧空间", ...totals({ revenue_yuan: "300000", target_revenue_yuan: null }) }, { track: "智慧伴学", ...totals({ revenue_yuan: "100000", target_revenue_yuan: null }) }] }) }) });
    const harness = pageHarness({ stateData: fixture, activeTask: 1, edited: { [key("target")]: "800000" } });
    button(harness.render(), "按预算结转收入分配赛道目标").props.onClick();
    assert.equal(harness.state[3][key("track-target", "智慧空间")], "600000.00"); assert.equal(harness.state[3][key("track-target", "智慧伴学")], "200000.00");
    assert.equal(harness.state[2].target_revenue_yuan, null); assert.equal(harness.state[2].entry_total, 100); assert.deepEqual(harness.state[2].finance, withoutTargets.finance);
  } finally { restore(); }
});

test("new API commands keep the real fiscal year and restoration creates a draft rather than editing history", async () => {
  const original = globalThis.fetch, requests = [];
  globalThis.fetch = async (url, options = {}) => { requests.push({ url, ...options }); return Response.json(envelope()); };
  try {
    await getNextYearCatalog(identity); await getNextYearVersion(identity, "N 1", 2029);
    await restoreNextYearVersion(identity, "N2", "N1", "恢复试算", 2029);
    await balanceNextYearBudget(identity, "N2", inputs({ fiscal_year: 2029 }), { rate_step_limit_ppm: 10000 });
    await getNextYearBudget(identity, undefined, 2100);
    assert.equal(requests[0].url, "/api/v1/next-year-budgets/catalog");
    assert.equal(requests[1].url, "/api/v1/next-year-budgets/fy29/versions/N%201");
    assert.deepEqual(JSON.parse(requests[2].body), { parent_id: "N2", version_id: "N1", reason: "恢复试算" });
    assert.equal(requests[3].url, "/api/v1/next-year-budgets/fy29/balance");
    assert.equal(JSON.parse(requests[3].body).options.rate_step_limit_ppm, 10000);
    assert.equal(requests[4].url, "/api/v1/next-year-budgets/2100/current");
    assert.ok(requests.slice(2, 4).every(request => request.method === "POST" && request.headers["Idempotency-Key"]));
  } finally { globalThis.fetch = original; }
});

test("entry membership, exact grade allocations and manual zero are draft attributes, not altered source fields", () => {
  assert.equal(effectiveNextYearEntry(combination({ entry: true, entry_override: false })), false);
  assert.equal(effectiveNextYearEntry(combination({ entry: false, entry_override: true })), true);
  const original = inputs({ combinations: [combination({ id: "a", source_entry_weight: 2 }), combination({ id: "b", source_entry_weight: 1 }), combination({ id: "c", grade: "八年级", entry: false, entry_override: true, source_entry_weight: 0 })] });
  const edited = applyNextYearEdits(original, { [key("entry-grade", "七年级")]: "10", [key("entry-grade", "八年级")]: "5", [key("manual", "a", "秋季")]: "0", [key("class-size", 0)]: "16" });
  assert.deepEqual(edited.errors, {}); assert.deepEqual(edited.inputs.combinations.map(row => row.entry_students), [7, 3, 5]);
  assert.ok(edited.inputs.combinations.every(row => row.entry_locked));
  assert.equal(edited.inputs.plans.find(row => row.combination_id === "a" && row.season === "秋季").students, 0);
  assert.equal(edited.inputs.plans.find(row => row.combination_id === "a" && row.season === "秋季").locked, true);
  assert.equal(edited.inputs.class_sizes[0].minimum, 10); assert.equal(edited.inputs.class_sizes[0].planned_class_size, 16);
  assert.equal(original.combinations[0].entry_students, undefined); assert.equal(edited.inputs.combinations[0].source_entry_weight, 2);
  assert.equal(applyNextYearEdits(original, { [key("entry-weight", "a")]: "999" }).inputs, null);
});

test("parameter TSV paste validates the whole batch and does not accept out-of-scope or duplicated identifiers", () => {
  const rows = inputs().rates;
  assert.deepEqual(parseNextYearParameterPaste("参数ID\t续班率\t新生占比\nrate-1\t76%\t20.5\nrate-2\t\t0", rows), {
    [key("rates", "rate-1", "renewal_ppm")]: "76", [key("rates", "rate-1", "share_ppm")]: "20.5", [key("rates", "rate-2", "share_ppm")]: "0",
  });
  assert.throws(() => parseNextYearParameterPaste("unknown\t76\t20", rows), /不在当前筛选范围/);
  assert.throws(() => parseNextYearParameterPaste("rate-1\t76\t20\nrate-1\t75\t21", rows), /重复/);
  assert.throws(() => parseNextYearParameterPaste("rate-1\t76", rows), /三列/);
  assert.equal(applyNextYearEdits(inputs(), { [key("bounds", "rate-1", "renewal_lower_ppm")]: "90", [key("bounds", "rate-1", "renewal_upper_ppm")]: "80" }).inputs, null);
});

test("copy and export projection covers the full filtered table with numeric values and active edit inputs", () => {
  const rows = Array.from({ length: 31 }, (_, index) => ({ name: `业务${index}`, value: index }));
  const projected = nextYearTableData(rows, ["业务", "人次"], row => React.createElement(React.Fragment, null,
    React.createElement("th", null, row.name), React.createElement("td", null, React.createElement("label", null, React.createElement("span", null, "隐藏标签"), React.createElement("input", { value: String(row.value), readOnly: true })))));
  assert.equal(projected.length, 31); assert.deepEqual(projected[30], { c0: "业务30", c1: 30 });
  const page = fs.readFileSync(new URL("../src/NextYearBudgetPage.tsx", import.meta.url), "utf8");
  assert.match(page, /exportData=\{\{ columns[, :]/); assert.match(page, /copyRows=\{\[columns\.map/);
});

test("impact comparison preserves unknown values and detects added rows without treating decimal formatting as change", () => {
  const before = calculation({ rows: [row({ revenue_yuan: "90000" })] });
  assert.equal(nextYearImpactRows(before, calculation({ rows: [row({ revenue_yuan: "90000.000000" })] })).length, 0);
  const changed = nextYearImpactRows(before, calculation({ rows: [row({ revenue_yuan: "91000", total_students: 101 }), row({ combination_id: "new", revenue_yuan: "2000", total_students: 2 })] }));
  assert.equal(changed.length, 2); assert.equal(changed[0].revenue_delta, 1000); assert.equal(changed[0].student_delta, 1); assert.equal(changed[1].revenue_delta, 2000);
  const unknown = nextYearImpactRows(before, calculation({ rows: [row({ revenue_yuan: null })] })); assert.equal(unknown[0].revenue_delta, null);
  const facts = nextYearAnalysisFacts(calculation(), inputs()); assert.equal(facts.length, 4); assert.equal(facts[1].renewalDenominator, 100); assert.equal(facts[1].renewalNumerator, 80);
});

test("historical view uses the selected source, parameters and notes and cannot save into that version", () => {
  const restore = fakeBrowser();
  try {
    const historical = envelope({ draft: draft({ id: "history-N1", sequence: 1, source: { ...source, sequence: 3 }, inputs: inputs({ plans: [{ combination_id: "combo-1", season: "秋季", students: 777 }], notes: [{ message: "历史参数说明" }] }), calculation: calculation({ rows: [row({ season: "秋季", old_students: 0, planned_new_applied: true })] }) }), read_only: true });
    const harness = pageHarness({ activeTask: 3 }); harness.state[16] = historical;
    button(harness.render(), "计划新生").props.onClick();
    const html = harness.html(); assert.match(html, /FY27 预测 P3/); assert.match(html, /历史参数说明/); assert.match(html, /value="777"/);
    assert.doesNotMatch(html, /保存新版本|按经营目标自动调平/); openVersions(harness); assert.match(harness.html(), /以此版本恢复新草稿/);
  } finally { restore(); }
});

test("infeasible balancing preserves edits; generated candidates require explicit selection before local application", async () => {
  const restore = fakeBrowser(), original = globalThis.fetch;
  try {
    const harness = pageHarness({ edited: { [key("entry")]: "125" } });
    globalThis.fetch = async url => Response.json(url.endsWith("/preview") ? { source, calculation: calculation() } : { status: "INCOMPLETE", proposed_inputs: null, message: "尚未找到方案" });
    button(harness.render(), "按经营目标自动调平").props.onClick(); await new Promise(resolve => setImmediate(resolve));
    assert.equal(harness.state[3][key("entry")], "125"); assert.equal(harness.state[1].draft.sequence, 1); assert.match(harness.state[5], /尚未找到方案/);
    const proposed = inputs({ entry_total: 125 }); proposed.rates[1].renewal_ppm = 850000;
    const entry_details = [{ combination_id: "combo-1", ...combination(), students: 125, locked: false }];
    globalThis.fetch = async url => Response.json(url.endsWith("/preview") ? { source, calculation: calculation({ entry_details }) } : { status: "PARTIAL", proposed_inputs: proposed, calculation: calculation({ entry_details, annual: totals({ revenue_yuan: "380000", gap_yuan: "20000" }) }), message: "改善建议，尚未达到经营目标" });
    button(harness.render(), "按经营目标自动调平").props.onClick(); await new Promise(resolve => setImmediate(resolve));
    assert.equal(harness.state[2].entry_total, 100); assert.equal(harness.state[7], null);
    const selection = nodes(harness.render()).find(node => node.type?.name === "NextYearActionSelection"); assert.ok(selection);
    selection.props.onApply(new Set(selection.props.actions.map((action, index) => `${action.kind}-${index}`))); await flush();
    assert.equal(harness.state[5], "");
    assert.equal(harness.state[2].entry_total, 125); assert.equal(harness.state[7].annual.revenue_yuan, "380000"); assert.equal(harness.state[1].draft.sequence, 1);
    assert.match(harness.html(), /未保存预览|修改影响对比/); assert.match(harness.state[22], /尚未达到经营目标/);
  } finally { globalThis.fetch = original; restore(); }
});

test("overview uses one shared filter system and unset class planning is not displayed as zero", () => {
  const restore = fakeBrowser();
  try { const harness = pageHarness(), tree = harness.render(), html = harness.html();
    assert.equal((html.match(/aria-label="当前SKU"/g) ?? []).length, 1); assert.doesNotMatch(html, /aria-label="共同筛选"/); assert.match(html, /设置计划班额/);
    const analysis = nodes(tree).find(node => typeof node.type === "function" && node.type.name === "BudgetAnalysisView"); assert.ok(analysis); assert.equal(analysis.props.hideSummary, true);
    assert.equal(analysis.props.hideFilters, true); assert.deepEqual(analysis.props.controlledFilters, filters);
    assert.equal(analysis.props.facts.length, 4); assert.equal(analysis.props.fiscalYear, 2028);
  } finally { restore(); }
});

const paged = (tree, label) => nodes(tree).find(node => typeof node.type === "function" && node.props?.label === label);
const pagedRow = (tree, label, index = 0) => { const table = paged(tree, label); assert.ok(table, label); return table.props.render(table.props.rows[index], index); };

test("M1 real SKU section preserves full filtered transfers, search paging and editable versus historical controls", () => {
  const restore = fakeBrowser(), original = globalThis.fetch; let requests = 0;
  globalThis.fetch = async () => { requests++; throw new Error("SKU presentation must not request or save a budget"); };
  try {
    const combinations = Array.from({ length: 61 }, (_, index) => combination({ id: `sku-${index}`,
      campus: index < 31 ? "西直门" : "中关村", subject: `数学${index}`, entry: index % 3 === 0, enabled: index % 2 === 0 }));
    const fixture = envelope({ draft: draft({ inputs: inputs({ combinations }) }) });
    const saved = JSON.stringify(fixture), h = pageHarness({ stateData: fixture, activeTask: 1 });
    const local = JSON.stringify(h.state[2]);
    const section = harness => {
      const owner = paged(harness.render(), "FY28SKU"); assert.ok(owner);
      const shell = owner.type(owner.props);
      assert.equal(shell.type.name, "BudgetDataSection");
      assert.equal(shell.props.title, "SKU");
      return { shell, tree: shell.type(shell.props) };
    };
    const transfer = tree => nodes(tree).find(node => typeof node.type === "function" && node.type.name === "ActionTable");
    const visible = tree => nodes(transfer(tree).props.children).find(node => node.type === "tbody").props.children;
    const search = tree => nodes(transfer(tree).props.toolbarStart).find(node => node.type === "input");
    let current = section(h);
    assert.equal(current.shell.props.count, 61);
    assert.equal(current.tree.props["aria-labelledby"], "next-year-sku-title");
    assert.equal(button(current.tree, "新增校区 / 产品").props.disabled, false);
    assert.equal(transfer(current.tree).props.exportData.rows.length, 61);
    assert.equal(transfer(current.tree).props.copyRows.length, 62, "one header plus every current filtered SKU, not just page one");
    assert.deepEqual(transfer(current.tree).props.copyRows[0], ["校区 / 赛道 / 产品", "年级 / 科目", "入口", "参与预算"]);
    for (const [index, row] of transfer(current.tree).props.exportData.rows.entries()) {
      assert.deepEqual(Object.keys(row), ["c0", "c1", "c2", "c3"]);
      assert.equal(row.c1, `七年级 数学${index}`, "the second identity column does not swallow the entry column");
      assert.equal(row.c2, index % 3 === 0 ? "是" : "否");
      assert.equal(row.c3, index % 2 === 0 ? "是" : "否", "participation remains in column four even beyond the visible page");
      assert.equal(transfer(current.tree).props.copyRows[index + 1].length, 4);
      assert.equal(transfer(current.tree).props.copyRows[index + 1][3], row.c3);
    }
    assert.equal(visible(current.tree).length, 25);
    button(current.tree, "下一页").props.onClick(); current = section(h);
    assert.match(text(renderToStaticMarkup(visible(current.tree)[0])), /数学25/);
    assert.equal(transfer(current.tree).props.exportData.rows.length, 61, "paging changes display only");
    assert.equal(transfer(current.tree).props.copyRows.length, 62);
    search(current.tree).props.onChange({ target: { value: "西直门" } }); current = section(h);
    assert.equal(current.shell.props.count, 31);
    assert.equal(visible(current.tree).length, 25);
    assert.match(text(renderToStaticMarkup(visible(current.tree)[0])), /数学0/);
    assert.equal(transfer(current.tree).props.exportData.rows.length, 31);
    assert.equal(transfer(current.tree).props.copyRows.length, 32);
    search(current.tree).props.onChange({ target: { value: "不存在的SKU" } }); current = section(h);
    assert.equal(current.shell.props.count, 0);
    assert.equal(visible(current.tree).length, 0);
    assert.deepEqual(transfer(current.tree).props.exportData.rows, []);
    assert.equal(transfer(current.tree).props.copyRows.length, 1, "an empty search exports no fabricated row");
    assert.match(text(renderToStaticMarkup(current.tree)), /当前范围没有明细/);
    assert.equal(JSON.stringify(h.state[2]), local, "search and pagination are not staged budget edits");
    assert.deepEqual(h.state[3], {});

    for (const mode of ["viewer", "historical", "busy"]) {
      const reader = pageHarness({ stateData: fixture, activeTask: 1,
        activeIdentity: mode === "viewer" ? { ...identity, roles: [] } : identity });
      if (mode === "historical") reader.state[16] = envelope({ draft: draft({ id: "historical-sku", inputs: inputs({ combinations }) }), read_only: true });
      if (mode === "busy") reader.state[4] = "preview";
      const read = section(reader), add = button(read.tree, "新增校区 / 产品");
      if (mode === "busy") assert.equal(add.props.disabled, true); else assert.equal(add, undefined, mode);
      const checkboxes = nodes(read.tree).filter(node => node.type === "input" && node.props.type === "checkbox");
      assert.equal(checkboxes.length, 25);
      assert.ok(checkboxes.every(node => node.props.disabled), mode);
      assert.equal(transfer(read.tree).props.exportData.rows.length, 61, "read-only users retain the same complete reading range");
    }
    const editor = pageHarness({ stateData: fixture, activeTask: 1 });
    const checkbox = nodes(section(editor).tree).find(node => node.type === "input" && node.props.type === "checkbox");
    assert.equal(checkbox.props.disabled, false);
    checkbox.props.onChange({ target: { checked: false } });
    assert.equal(editor.state[2].combinations[0].enabled, false);
    assert.equal(editor.state[1].draft.inputs.combinations[0].enabled, true, "checkbox changes only the current local inputs");
    assert.equal(JSON.stringify(fixture), saved);
    assert.equal(requests, 0);
  } finally { globalThis.fetch = original; restore(); }
});

test("FY28 SKU uses the same long-label identity hierarchy while preserving only entry and editable participation columns", () => {
  const restore = fakeBrowser();
  try {
    const dimensions = { campus: "百学汇学习机教学区与中关村联合长名称校区", track: "智慧伴学跨学段课程赛道",
      product: "云帆白金高端小班联合产品", grade: "七年级与八年级衔接年级", subject: "数学与综合实践长名称科目" };
    const item = combination({ ...dimensions, entry: false, enabled: true, added: true });
    const fixture = envelope({ draft: draft({ inputs: inputs({ combinations: [item] }) }) });
    const saved = JSON.stringify(fixture), h = pageHarness({ stateData: fixture, activeTask: 1 });
    const owner = paged(h.render(), "FY28SKU");
    assert.deepEqual(owner.props.headers, ["校区 / 赛道 / 产品", "年级 / 科目", "入口", "参与预算"]);
    const shell = owner.type(owner.props), tree = shell.type(shell.props);
    const table = nodes(tree).find(node => typeof node.type === "function" && node.type.name === "ActionTable");
    const body = nodes(table.props.children).find(node => node.type === "tbody");
    const cells = nodes(body.props.children[0]).filter(node => node.type === "th" || node.type === "td");
    const label = node => text(renderToStaticMarkup(node));
    assert.equal(cells.length, 4, "identity alignment must not add a revenue or extra business column");
    assert.deepEqual(cells.map(cell => cell.type), ["th", "td", "td", "td"]);
    assert.equal(cells[0].props.scope, "row");
    assert.equal(label(nodes(cells[0]).find(node => node.type === "strong")), dimensions.campus);
    assert.deepEqual(nodes(cells[0]).filter(node => node.type === "small").map(label), [`${dimensions.track} / ${dimensions.product}`, "新业务"]);
    assert.equal(label(nodes(cells[1]).find(node => node.type === "small")), dimensions.subject);
    assert.ok(label(cells[1]).includes(dimensions.grade));
    assert.equal(label(cells[2]), "否");
    const checkbox = nodes(cells[3]).find(node => node.type === "input" && node.props.type === "checkbox");
    assert.equal(checkbox.props.checked, true); assert.equal(checkbox.props.disabled, false);
    assert.equal(nodes(cells).filter(node => node.type === "input").length, 1);
    const projected = table.props.exportData.rows[0];
    assert.deepEqual(Object.keys(projected), ["c0", "c1", "c2", "c3"]);
    for (const value of [dimensions.campus, dimensions.track, dimensions.product]) assert.ok(projected.c0.includes(value), value);
    assert.equal(projected.c1, `${dimensions.grade} ${dimensions.subject}`);
    assert.equal(projected.c2, "否"); assert.equal(projected.c3, "是");
    assert.deepEqual(table.props.copyRows[0], owner.props.headers);
    assert.deepEqual(table.props.copyRows[1], [projected.c0, projected.c1, "否", "是"]);
    h.state[9] = 2;
    assert.deepEqual(paged(h.render(), "入口分配明细").props.headers, ["校区 / 业务", "入口", "指定人次", "分配方式"],
      "the SKU identity sample does not migrate another editing task");
    assert.equal(JSON.stringify(fixture), saved);
  } finally { restore(); }
});
const labeledInput = (tree, label) => { const accessible = nodes(tree).find(node => node.type === "input" && node.props["aria-label"] === label); if (accessible) return accessible; const item = nodes(tree).find(node => node.type === "label" && text(renderToStaticMarkup(node)) === label); assert.ok(item, label); return nodes(item).find(node => node.type === "input"); };
const flush = () => new Promise(resolve => setImmediate(resolve));

const quarterTable = (harness, label) => nodes(harness.render()).find(node => typeof node.type === "function" && node.type.name === "QuarterlyBusinessTable" && node.props.tableLabel === label);
const quarterSwitch = (harness, label) => nodes(harness.render()).find(node => typeof node.type === "function" && node.type.name === "NextYearQuarterLayoutSwitch" && node.props.label === label);

test("phase 3 quarterly reading preserves raw partial, invalid, empty and high precision inputs without staging", () => {
  assert.equal(nextYearQuarterInputValue(undefined, 812345, true), .812345);
  assert.equal(nextYearQuarterInputValue("81.2300", 800000, true), .8123);
  assert.equal(nextYearQuarterInputValue("", 800000, true), "");
  assert.equal(nextYearQuarterInputValue("   ", 800000, true), "   ");
  assert.equal(nextYearQuarterInputValue("-", 800000, true), "-");
  assert.equal(nextYearQuarterInputValue("bad input", 800000, true), "bad input");
  assert.equal(nextYearQuarterInputValue("1650.123456", "1650"), "1650.123456");
  assert.equal(nextYearQuarterInputValue("0", "1650"), "0");
  assert.equal(nextYearQuarterInputValue(undefined, null), null);
});

test("phase 3 rate reading and existing editor are alternate views of the same scoped inputs, never another save flow", () => {
  const restore = fakeBrowser(), original = globalThis.fetch, requests = [];
  try {
    globalThis.fetch = async (...request) => { requests.push(request); throw new Error("reading a layout must not request data"); };
    const harness = pageHarness({ activeTask: 3, edited: { [key("rates", "rate-1", "renewal_ppm")]: "81.2300" }, changedReason: "保留备注" });
    const before = JSON.stringify([harness.state[1], harness.state[2], harness.state[3], harness.state[8]]);
    const wide = quarterTable(harness, "FY28招生参数季度对照"); assert.ok(wide);
    assert.equal(quarterSwitch(harness, "招生参数阅读方式").props.value, "quarters");
    const [editor] = nodes(harness.render()).filter(node => node.props?.className === "ny-quarter-editor"); assert.equal(editor.props.hidden, true);
    const renewal = wide.props.metrics.find(metric => metric.key === "renewal_ppm"), autumn = wide.props.rows.find(row => row.season === "秋季");
    assert.equal(renewal.value(autumn), .8123); assert.equal(renewal.render(autumn), "81.2300%");
    const blank = pageHarness({ activeTask: 3, edited: { [key("rates", "rate-1", "renewal_ppm")]: "   " } });
    const blankMetric = quarterTable(blank, "FY28招生参数季度对照").props.metrics.find(metric => metric.key === "renewal_ppm");
    assert.equal(blankMetric.value(autumn), "   "); assert.equal(blankMetric.render(autumn), "未填写");
    quarterSwitch(harness, "招生参数阅读方式").props.onChange("details");
    assert.equal(quarterTable(harness, "FY28招生参数季度对照"), undefined);
    assert.equal(nodes(harness.render()).find(node => node.props?.className === "ny-quarter-editor").props.hidden, false);
    quarterSwitch(harness, "招生参数阅读方式").props.onChange("quarters");
    assert.equal(JSON.stringify([harness.state[1], harness.state[2], harness.state[3], harness.state[8]]), before);
    assert.equal(requests.length, 0); assert.ok(button(harness.render(), "保存新版本"));
  } finally { globalThis.fetch = original; restore(); }
});

test("phase 3 rate cell navigation narrows only to the exact business and quarter and keeps the pending value", () => {
  const restore = fakeBrowser();
  try {
    const editKey = key("rates", "rate-1", "share_ppm"), harness = pageHarness({ activeTask: 3, edited: { [editKey]: "27.1250" } });
    const before = JSON.stringify([harness.state[1], harness.state[2], harness.state[3]]);
    const wide = quarterTable(harness, "FY28招生参数季度对照"), selected = wide.props.rows.find(row => row.id === "rate-1");
    wide.props.onCellAction(selected, "share_ppm");
    assert.equal(quarterSwitch(harness, "招生参数阅读方式").props.value, "details");
    assert.deepEqual(harness.state[10], { ...filters, season: selected.season, track: selected.track, campus: selected.campus, product: selected.product, grade: selected.grade });
    const table = paged(harness.render(), "FY28招生参数"); assert.deepEqual(table.props.rows.map(row => row.id), ["rate-1"]);
    assert.equal(nodes(table.props.render(table.props.rows[0])).find(node => node.type === "input" && node.props["data-ny-edit-key"] === editKey).props.value, "27.1250");
    assert.equal(JSON.stringify([harness.state[1], harness.state[2], harness.state[3]]), before);
  } finally { restore(); }
});

test("phase 3 finance cell navigation returns to the sole editor, preserving source IDs and money precision", () => {
  const restore = fakeBrowser();
  try {
    const harness = pageHarness({ activeTask: 4, edited: { [key("finance", "finance-2", "price_yuan")]: "1650.123456" } });
    const wide = quarterTable(harness, "FY28财务参数季度对照"), selected = wide.props.rows.find(row => row.id === "finance-2");
    const before = JSON.stringify([harness.state[1], harness.state[2], harness.state[3]]);
    assert.equal(wide.props.rowKey(selected), "combo-1");
    assert.equal(wide.props.metrics.find(metric => metric.key === "price_yuan").value(selected), "1650.123456");
    wide.props.onCellAction(selected, "price_yuan");
    assert.equal(quarterSwitch(harness, "财务参数阅读方式").props.value, "details");
    assert.equal(harness.state[10].season, "寒假"); assert.equal(harness.state[10].subject, "数学");
    const table = paged(harness.render(), "FY28 财务参数"); assert.deepEqual(table.props.rows.map(row => row.id), ["finance-2"]);
    assert.equal(nodes(table.props.render(table.props.rows[0])).find(node => node.type === "input" && node.props["data-ny-edit-key"] === key("finance", "finance-2", "price_yuan")).props.value, "1650.123456");
    assert.equal(JSON.stringify([harness.state[1], harness.state[2], harness.state[3]]), before);
  } finally { restore(); }
});

test("phase 3 quarterly reading never enables unavailable summer rate edits or historical writes", () => {
  const restore = fakeBrowser();
  try {
    const harness = pageHarness({ activeTask: 3 }), wide = quarterTable(harness, "FY28招生参数季度对照"), summer = wide.props.rows.find(row => row.season === "暑假");
    assert.equal(wide.props.canCellAction(summer, "renewal_ppm"), false);
    assert.equal(wide.props.canCellAction(summer, "share_ppm"), false);
    assert.equal(wide.props.canCellAction(summer, "locked"), true);
    assert.equal(wide.props.metrics.find(metric => metric.key === "share_ppm").value(summer), "入口分配");
    harness.state[16] = envelope({ read_only: true });
    assert.equal(quarterTable(harness, "FY28招生参数季度对照").props.onCellAction, undefined);
    assert.equal(quarterSwitch(harness, "招生参数阅读方式").props.readOnly, true);
    assert.equal(button(harness.render(), "保存新版本"), undefined);
    const viewer = pageHarness({ activeTask: 4, activeIdentity: { ...identity, roles: [] } });
    assert.equal(quarterTable(viewer, "FY28财务参数季度对照").props.onCellAction, undefined);
  } finally { restore(); }
});

test("editing entry counts then cancelling membership clears pending counts rather than putting them back", () => {
  const restore = fakeBrowser();
  try {
    const harness = pageHarness({ activeTask: 2 });
    let cells = pagedRow(harness.render(), "入口分配明细");
    nodes(cells).find(node => node.type === "input" && node.props.type === "text").props.onChange({ target: { value: "80" } });
    assert.equal(harness.state[3][key("entry-count", "combo-1")], "80");
    cells = pagedRow(harness.render(), "入口分配明细");
    nodes(cells).find(node => node.type === "input" && node.props.type === "checkbox").props.onChange({ target: { checked: false } });
    assert.equal(harness.state[2].combinations[0].entry_override, false); assert.equal(harness.state[2].combinations[0].entry_students, null);
    assert.equal(harness.state[2].combinations[0].entry_locked, false); assert.deepEqual(harness.state[3], {}); assert.equal(harness.state[5], "");
    const grade = pageHarness({ activeTask: 2 });
    nodes(pagedRow(grade.render(), "年级入口分配")).find(node => node.type === "input").props.onChange({ target: { value: "100" } });
    button(grade.render(), "七年级 ✓").props.onClick();
    assert.equal(grade.state[2].combinations[0].entry_override, false); assert.equal(grade.state[2].combinations[0].entry_students, null); assert.deepEqual(grade.state[3], {});
  } finally { restore(); }
});

test("typing a manual count then cancelling its lock clears the pending manual override", () => {
  const restore = fakeBrowser();
  try {
    const harness = pageHarness(); button(harness.render(), "手动调节").props.onClick();
    let cells = pagedRow(harness.render(), "预算手动调节明细");
    nodes(cells).find(node => node.type === "input" && node.props.type === "text").props.onChange({ target: { value: "0" } });
    cells = pagedRow(harness.render(), "预算手动调节明细");
    const lock = nodes(cells).find(node => node.type === "input" && node.props.type === "checkbox"); assert.equal(lock.props.checked, true);
    lock.props.onChange({ target: { checked: false } });
    assert.equal(harness.state[2].plans.find(item => item.season === "秋季").locked, false); assert.deepEqual(harness.state[3], {});
    assert.equal(nodes(pagedRow(harness.render(), "预算手动调节明细")).find(node => node.type === "input" && node.props.type === "checkbox").props.checked, false);
  } finally { restore(); }
});

test("new non-entry business is editable before its first preview and applies shared product class planning", async () => {
  const restore = fakeBrowser(), original = globalThis.fetch; const requests = [];
  try {
    globalThis.fetch = async (url, options) => { requests.push({ url, body: JSON.parse(options.body) }); return Response.json({ source, calculation: calculation() }); };
    const harness = pageHarness({ activeTask: 1 });
    button(harness.render(), "新增校区 / 产品").props.onClick();
    for (const [label, value] of [["校区", "新校区"], ["赛道", "智慧空间"], ["产品", "常规班"], ["年级", "八年级"], ["科目", "英语"], ["学费（元）", "1200"], ["产品计划班额（共用）", "16"], ["新增原因", "新增校区试算"]]) labeledInput(harness.render(), label).props.onChange({ target: { value } });
    button(harness.render(), "加入预算范围").props.onClick();
    assert.equal(labeledInput(harness.render(), "校区").props.value, ""); assert.equal(labeledInput(harness.render(), "产品").props.value, "");
    assert.equal(labeledInput(harness.render(), "学费（元）").props.value, ""); assert.equal(labeledInput(harness.render(), "产品计划班额（共用）").props.value, "");
    assert.equal(labeledInput(harness.render(), "年级").props.value, "七年级"); assert.equal(labeledInput(harness.render(), "新增原因").props.value, "新增业务");
    const added = harness.state[2].combinations.find(item => item.added); assert.ok(added); assert.equal(added.entry_override, false);
    assert.equal(harness.state[2].class_sizes[0].planned_class_size, 16); assert.equal(harness.state[2].class_sizes[0].minimum, 10);
    assert.equal(harness.state[1].draft.inputs.combinations.length, 1); assert.equal(requests.length, 0);
    let table = paged(harness.render(), "预算手动调节明细"), index = table.props.rows.findIndex(item => item.combination_id === added.id && item.season === "暑假"); assert.ok(index >= 0);
    nodes(table.props.render(table.props.rows[index], index)).find(node => node.type === "input" && node.props.type === "text").props.onChange({ target: { value: "30" } });
    harness.state[9] = 3; button(harness.render(), "计划新生").props.onClick(); table = paged(harness.render(), "FY28 计划新生"); assert.equal(table.props.rows.filter(item => item.combination.id === added.id).length, 4);
    button(harness.render(), "查看影响").props.onClick(); await flush();
    assert.equal(requests.length, 1); assert.match(requests[0].url, /preview$/); assert.equal(requests[0].body.inputs.plans.find(item => item.combination_id === added.id && item.season === "暑假").students, 30);
    assert.equal(harness.state[1].draft.sequence, 1);
  } finally { globalThis.fetch = original; restore(); }
});

test("choosing a historical version uses its source, plans and notes after the actual version request", async () => {
  const restore = fakeBrowser(), original = globalThis.fetch;
  try {
    const historical = envelope({ draft: draft({ id: "history-N0", sequence: 0, source: { ...source, sequence: 3 }, inputs: inputs({ plans: [{ combination_id: "combo-1", season: "秋季", students: 777 }], notes: [{ message: "来自历史N0的说明" }] }), calculation: calculation({ rows: [row({ season: "秋季", old_students: 0, planned_new_applied: true })] }) }), read_only: true });
    globalThis.fetch = async url => { assert.match(url, /versions\/history-N0$/); return Response.json(historical); };
    const harness = pageHarness({ stateData: envelope({ history: [{ id: "history-N0", sequence: 0, created_at: "2026-10-01T00:00:00Z" }] }) });
    button(harness.render(), "版本记录").props.onClick(); button(pagedRow(harness.render(), "FY28预算版本"), "查看").props.onClick(); await flush();
    harness.state[9] = 3; button(harness.render(), "计划新生").props.onClick(); const html = harness.html(); assert.match(html, /FY27 预测 P3/); assert.match(html, /value="777"/); assert.match(html, /来自历史N0的说明/); assert.doesNotMatch(html, /保存新版本|基准预测版本/);
    assert.equal(harness.state[1].draft.source.sequence, 16);
  } finally { globalThis.fetch = original; restore(); }
});

test("selecting an older confirmed forecast sends that exact source while preserving the current parent", async () => {
  const restore = fakeBrowser(), original = globalThis.fetch; const sent = [];
  try {
    const old = { ...source, id: "forecast-P3", sequence: 3, fiscal_year: 2027 };
    const harness = pageHarness({ activeTask: 1 }); harness.state[14] = { source_fiscal_year: 2027, default_fiscal_year: 2028, sources: [source, old], fiscal_years: [{ fiscal_year: 2028, has_draft: true }] };
    openVersions(harness);
    nodes(harness.render()).find(node => node.type === "select" && node.props["aria-label"] === "基准预测版本").props.onChange({ target: { value: old.id } });
    globalThis.fetch = async (url, options) => { sent.push({ url, body: JSON.parse(options.body) }); return Response.json(url.endsWith("prepare-preview") ? { read_only: true, fiscal_year: 2028, parent_id: "next-year-N1", source_id: old.id, source: old, inputs: inputs(), calculation: calculation() } : envelope({ draft: draft({ id: "new-N2", sequence: 2, parent_id: "next-year-N1", source: old }) })); };
    button(harness.render(), "预览基准变化").props.onClick(); await flush();
    assert.equal(sent.length, 1); assert.match(sent[0].url, /prepare-preview$/); assert.deepEqual(sent[0].body, { forecast_id: old.id, parent_id: "next-year-N1" }); assert.equal(harness.state[1].draft.id, "next-year-N1");
    button(harness.render(), "以此预测生成新草稿").props.onClick(); await flush();
    assert.equal(sent.length, 2); assert.match(sent[1].url, /prepare$/); assert.equal(sent[1].body.forecast_id, old.id); assert.equal(sent[1].body.parent_id, "next-year-N1"); assert.equal(harness.state[1].draft.sequence, 2); assert.equal(harness.state[1].draft.source.sequence, 3);
  } finally { globalThis.fetch = original; restore(); }
});

test("year-specific URLs reload their ledger, roll back on failure, and protect unsaved edits without a year selector", async () => {
  const restore = fakeBrowser(), original = globalThis.fetch; const urls = [], listeners = {}, requests = [];
  try {
    globalThis.history = { pushState(_a, _b, url) { urls.push(String(url)); globalThis.location = { href: String(url), search: url.search }; }, replaceState(_a, _b, url) { globalThis.location = { href: String(url), search: url.search }; } };
    globalThis.window.addEventListener = (name, fn) => { listeners[name] = fn; }; globalThis.window.removeEventListener = () => {};
    const harness = pageHarness(); harness.state[15] = 2028; harness.state[14] = { source_fiscal_year: 2027, sources: [], default_fiscal_year: 2028, fiscal_years: [{ fiscal_year: 2028 }, { fiscal_year: 2029 }] };
    assert.equal(nodes(harness.render()).some(node => node.type === "select" && node.props["aria-label"] === "预算财年"), false);
    globalThis.fetch = async (url, options) => { requests.push({ url, method: options?.method ?? "GET" }); return Response.json(url.endsWith("session") ? identity : { detail: "读取下一年度失败" }, { status: url.endsWith("session") ? 200 : 503 }); };
    filterEffect(harness, "popstate")(); globalThis.location = { href: "http://127.0.0.1:5188/budget/next-year?year=2029", search: "?year=2029" };
    await listeners.popstate();
    assert.equal(urls.length, 0); assert.match(globalThis.location.search, /year=2028/); assert.equal(harness.state[15], 2028); assert.equal(harness.state[1].draft.inputs.fiscal_year, 2028); assert.match(harness.state[5], /读取下一年度失败/);
    const next = envelope({ draft: draft({ inputs: inputs({ fiscal_year: 2029 }), calculation: calculation({ fiscal_year: 2029 }) }) });
    globalThis.fetch = async (url, options) => { requests.push({ url, method: options?.method ?? "GET" }); return Response.json(url.endsWith("session") ? identity : url.endsWith("catalog") ? { source_fiscal_year: 2027, sources: [], fiscal_years: [{ fiscal_year: 2028 }, { fiscal_year: 2029 }] } : next); };
    filterEffect(harness, "popstate")(); globalThis.location = { href: "http://127.0.0.1:5188/budget/next-year?year=2029", search: "?year=2029" };
    await listeners.popstate();
    assert.equal(harness.state[15], 2029); assert.equal(harness.state[1].draft.inputs.fiscal_year, 2029); assert.match(globalThis.location.search, /year=2029/);
    assert.equal(nodes(harness.render()).find(node => typeof node.type === "function" && node.type.name === "FY27Header").props.fiscalYear, 2029);
    filterEffect(harness, "popstate")(); globalThis.location = { href: "http://127.0.0.1:5188/budget/next-year?year=2028&nyTask=2", search: "?year=2028&nyTask=2" };
    globalThis.fetch = async (url, options) => { requests.push({ url, method: options?.method ?? "GET" }); return Response.json(url.endsWith("session") ? identity : url.endsWith("catalog") ? { sources: [], fiscal_years: [{ fiscal_year: 2028 }] } : envelope()); };
    await listeners.popstate(); assert.equal(harness.state[15], 2028); assert.equal(harness.state[9], 2); assert.equal(harness.state[1].draft.inputs.fiscal_year, 2028);
    const beforeProtectedNavigation = requests.length;
    harness.state[3] = { [key("entry")]: "125" }; filterEffect(harness, "popstate")(); globalThis.location = { href: "http://127.0.0.1:5188/budget/next-year?year=2029", search: "?year=2029" };
    await listeners.popstate(); assert.match(globalThis.location.search, /year=2028/); assert.equal(harness.state[3][key("entry")], "125"); assert.match(harness.state[5], /先保存或放弃修改/);
    assert.equal(requests.length, beforeProtectedNavigation); assert.equal(urls.length, 0); assert.ok(requests.every(request => request.method === "GET"));
  } finally { globalThis.fetch = original; restore(); }
});

test("default adjustment bounds are the real five-point range, clearable, and use Chinese field labels", () => {
  const restore = fakeBrowser();
  try {
    const harness = pageHarness({ activeTask: 3 }); button(harness.render(), "调平范围").props.onClick();
    const cells = pagedRow(harness.render(), "自动调平参数范围", 1), fields = nodes(cells).filter(node => node.type === "input");
    assert.deepEqual(fields.map(item => item.props.placeholder), ["默认 75", "默认 85", "默认 20", "默认 30"]); assert.deepEqual(fields.map(item => item.props.value), ["", "", "", ""]);
    assert.match(text(renderToStaticMarkup(cells)), /续班率下限/); assert.match(fields[0].props["aria-label"], /续班率下限（%）$/);
    assert.equal(paged(harness.render(), "自动调平参数范围").props.headers[2], "续班率下限（%）"); assert.doesNotMatch(text(renderToStaticMarkup(cells)), /renewal_lower_ppm/);
    fields[0].props.onChange({ target: { value: "70" } }); assert.equal(applyNextYearEdits(harness.state[2], harness.state[3]).inputs.rates[1].renewal_lower_ppm, 700000);
    const original = inputs(); original.rates[1].renewal_lower_ppm = 700000;
    assert.equal(applyNextYearEdits(original, { [key("bounds", "rate-1", "renewal_lower_ppm")]: "" }).inputs.rates[1].renewal_lower_ppm, undefined);
  } finally { restore(); }
});

test("allocation shows unspecified grades as automatic rather than silently displaying old numbers as the new target", () => {
  const restore = fakeBrowser();
  try {
    const harness = pageHarness({ activeTask: 2, edited: { [key("entry")]: "10000" } });
    const cells = pagedRow(harness.render(), "年级入口分配"), input = nodes(cells).find(node => node.type === "input");
    assert.equal(input.props.value, ""); assert.equal(input.props.placeholder, "自动分配"); assert.match(text(harness.html()), /年度入口 10,000 已指定 0 余下 10,000 自动分配/); assert.match(harness.html(), /已计算列为上次结果/);
    const plans = inputs({ combinations: [combination({ entry_students: 100, entry_locked: true })] });
    const cleared = applyNextYearEdits(plans, { [key("entry-grade", "七年级")]: "" }).inputs; assert.equal(cleared.combinations[0].entry_students, null); assert.equal(cleared.combinations[0].entry_locked, false);
  } finally { restore(); }
});

test("class diagnostics remain actionable without weakening rules and disappear when changes are discarded", async () => {
  const restore = fakeBrowser(), original = globalThis.fetch;
  try {
    const harness = pageHarness({ edited: { [key("entry")]: "125" } });
    globalThis.fetch = async () => Response.json({ status: "INCOMPLETE", proposed_inputs: null, message: "未满足最低成班人数", constraints: { class_diagnostics: [{ code: "BELOW_MINIMUM_CLASS_SIZE", count: 3, message: "未达到最低成班人数", action: "调整人次或关闭业务范围" }] } });
    button(harness.render(), "按经营目标自动调平").props.onClick(); await flush();
    assert.match(harness.html(), /先处理成班问题/); assert.ok(button(harness.render(), "处理成班问题"));
    assert.equal(harness.state[27], true); assert.equal(harness.state[9], 5);
    button(harness.render(), "放弃修改").props.onClick(); assert.deepEqual(harness.state[25], []); assert.doesNotMatch(harness.html(), /先处理成班问题/);
    const changed = nextYearImpactRows(calculation({ rows: [row({ class_count: null })] }), calculation({ rows: [row({ total_students: 110, class_count: null })] })); assert.equal(changed[0].class_delta, null);
    const rounded = allocateNextYearTrackTargets("100.01", { A: "1", B: "1", C: "1" }, 2); assert.deepEqual(rounded, { A: "33.34", B: "33.34", C: "33.33" });
  } finally { globalThis.fetch = original; restore(); }
});

const readinessRow = (overrides = {}) => ({ code: "BELOW_MINIMUM_CLASS_SIZE", combination_id: "combo-1", season: "秋季", track: "智慧空间", product: "常规班", campus: "西直门", grade: "七年级", subject: "数学", current_students: 4, old_students: 3, new_students: 1, minimum: 10, gap: 6, carryover_yuan: "0.00", closure_allowed: true, plan_locked: false, seed_plan: false, entry_locked: false, rate_locked: false, combination_locked: false, lock_reasons: [], rate_parameters: [], actions: ["核对全部业务", "自动调平前需填写经营目标"], ...overrides });
const readiness = (overrides = {}) => ({ source, calculation_complete: true, blocking_issue_count: 0, issue_count: 1, combination_count: 1, closure_allowed_count: 1, carry_protected_count: 0, rows: [readinessRow()], ...overrides });
const classScope = { code: "", season: "", campus: "", group: "code" };
const panelProps = (overrides = {}) => ({ rows: [readinessRow()], report: readiness(), scope: classScope, onScopeChange() {}, checked: true, stale: false, busy: false, readOnly: false, hasTarget: true, onCheck() {}, onNavigate() {}, onClose() {}, ...overrides });
const classPanel = tree => nodes(tree).find(node => typeof node.type === "function" && node.type.name === "NextYearClassReadinessPanel");

test("class readiness is a pure request with exact current edits and no operating target prerequisite", async () => {
  const restore = fakeBrowser(), original = globalThis.fetch, requests = [];
  try {
    globalThis.fetch = async (url, options) => { requests.push({ url, body: JSON.parse(options.body) }); return Response.json(readiness()); };
    const withoutTarget = inputs({ target_revenue_yuan: null, track_targets: { 智慧空间: null } });
    await checkNextYearClassReadiness(identity, "N1", withoutTarget);
    assert.match(requests[0].url, /fy28\/class-readiness$/); assert.deepEqual(requests[0].body, { parent_id: "N1", inputs: withoutTarget });
    const harness = pageHarness({ stateInputs: withoutTarget, edited: { [key("entry")]: "125" } });
    harness.state[2] = withoutTarget; // Test harness leaves current inputs explicit, including an absent target.
    checkClasses(harness); await flush();
    assert.equal(requests.length, 2); assert.equal(requests[1].body.inputs.entry_total, 125); assert.equal(requests[1].body.inputs.target_revenue_yuan, null);
    assert.equal(harness.state[2].entry_total, 100); assert.equal(harness.state[3][key("entry")], "125"); assert.equal(harness.state[1].draft.sequence, 1);
    assert.equal(harness.state[26].report.issue_count, 1); assert.equal(classPanel(harness.render()).props.checked, true);
    assert.doesNotMatch(requests.map(request => request.url).join(" "), /revise|prepare|restore|balance/);
  } finally { globalThis.fetch = original; restore(); }
});

test("saved issue fallback joins exact dimensions and known gaps without inferring closure or feasibility", () => {
  const data = calculation({ rows: [row({ season: "秋季", total_students: 4, old_students: 3, new_students: 1 })], issues: [
    { code: "BELOW_MINIMUM_CLASS_SIZE", combination_id: "combo-1", season: "秋季", severity: "WARNING", message: "人数不足", detail: { minimum_class_size: 10, class_size_gap: 6 } },
    { code: "ENABLED_COMBINATION_ALL_SEASONS_ZERO", combination_id: "combo-1", severity: "WARNING", message: "四季均零" },
  ] });
  const result = nextYearClassIssueRows(data, inputs());
  assert.equal(result.length, 2); assert.equal(result[0].current_students, 4); assert.equal(result[0].gap, 6); assert.equal(result[0].minimum, 10);
  assert.equal(result[0].campus, "西直门"); assert.equal(result[0].subject, "数学"); assert.equal(result[0].closure_allowed, null); assert.equal(result[0].bound_proof, undefined);
  assert.equal(result[1].season, null); assert.equal(result[1].current_students, 0); assert.equal(result[1].old_students, 0); assert.equal(result[1].gap, 0);
});

test("specialized class table filters and groups full issue dimensions and exports the whole filtered set as numbers", () => {
  let selected; const rows = Array.from({ length: 40 }, (_, index) => readinessRow({ combination_id: `combo-${index}`, campus: index < 30 ? "西直门" : "中关村" }));
  const props = panelProps({ rows, report: readiness({ issue_count: 40, rows }), scope: { ...classScope, campus: "西直门", group: "campus" }, onScopeChange(value) { selected = value; } });
  const tree = NextYearClassReadinessPanel(props), table = paged(tree, "成班问题明细");
  assert.equal(table.props.rows.length, 30); assert.equal(table.props.exportRow(rows[0]).gap, 6); assert.equal(table.props.exportRow(rows[0]).minimum, 10);
  assert.ok(table.props.exportColumns.some(column => column.key === "gap" && column.format === "count"));
  const html = renderToStaticMarkup(React.createElement(NextYearClassReadinessPanel, props));
  assert.match(html, /一键复制/); assert.match(html, /导出/); assert.match(html, /西直门 \/ 智慧空间 \/ 常规班 \/ 七年级 \/ 数学/);
  nodes(tree).find(node => node.type === "select" && node.props["aria-label"] === "成班季度筛选").props.onChange({ target: { value: "暑假" } }); assert.equal(selected.season, "暑假");
});

test("readiness only displays impossibility from server proof and never claims empty incomplete inputs passed", () => {
  const proof = { scope: "CURRENT_PREVIEW", entry_adjustment: false, lower_students: 4, upper_students: 8, minimum: 10, unreachable: true, message: "入口固定时，当前比例可调范围到不了最低成班人数。" };
  const tested = readinessRow({ bound_proof: proof }), props = panelProps({ rows: [tested], report: readiness({ rows: [tested] }), hasTarget: false });
  const html = renderToStaticMarkup(React.createElement(NextYearClassReadinessPanel, props)); assert.match(html, /当前范围无法成班/); assert.match(html, /入口固定时/); assert.equal((html.match(/未设经营目标/g) ?? []).length, 1);
  const uncertain = renderToStaticMarkup(React.createElement(NextYearClassReadinessPanel, panelProps({ rows: [readinessRow({ closure_allowed: false, bound_proof: { ...proof, unreachable: false } })] })));
  assert.doesNotMatch(uncertain, /当前范围无法成班/);
  const incomplete = renderToStaticMarkup(React.createElement(NextYearClassReadinessPanel, panelProps({ rows: [], report: readiness({ rows: [], issue_count: 0, calculation_complete: false, blocking_issue_count: 2 }) })));
  assert.match(incomplete, /人次或参数尚不完整/); assert.match(incomplete, /当前检查未完整，请核对人次和参数后重新检查/);
  assert.doesNotMatch(incomplete, /没有成班问题|全部通过|已保存结果未列出成班问题/);
});

test("phase 2 planned-student tool reveals its retained subject scope without clearing it or changing rate scope", () => {
  const restore = fakeBrowser(), originalFetch = globalThis.fetch;
  let requests = 0;
  globalThis.fetch = async () => { requests++; throw new Error("scope inspection cannot call a budget API"); };
  try {
    const combos = [combination({ entry: false }), combination({ id: "combo-English", subject: "英语", entry: false })];
    const resultRows = combos.flatMap(combo => seasons.map(season => row({ ...combo, combination_id: combo.id, season, planned_new_applied: true })));
    const fixture = envelope({ draft: draft({ inputs: inputs({ combinations: combos }), calculation: calculation({ rows: resultRows }) }) });
    const h = pageHarness({ stateData: fixture, activeTask: 3 });
    h.state[10] = { ...filters, subject: "英语", season: "秋季" };
    const saved = JSON.stringify([h.state[1], h.state[2]]), scopeBefore = JSON.stringify(h.state[10]);
    assert.equal(scopeControl(h).props.dimensions.some(item => item.key === "subject"), false, "rates do not acquire a subject dimension");
    button(h.render(), "计划新生").props.onClick();
    const tool = nodes(h.render()).find(node => typeof node.type === "function" && node.type.name === "BudgetScopeBar" && node.props.label === "计划新生范围");
    assert.ok(tool); assert.deepEqual(tool.props.dimensions.map(item => item.key), ["season", "track", "campus", "product", "grade", "subject"]);
    assert.equal(tool.props.selectLabelPrefix, "计划新生筛选"); assert.equal(tool.props.count, 1);
    assert.equal(JSON.stringify(h.state[10]), scopeBefore, "opening the tool must not clear its previously hidden subject");
    assert.deepEqual(paged(h.render(), "FY28 计划新生").props.rows.map(item => [item.combination.subject, item.season]), [["英语", "秋季"]]);
    const actualControl = tool.type(tool.props);
    assert.ok(nodes(actualControl).some(node => node.type === "select" && node.props["aria-label"] === "计划新生筛选科目" && node.props.value === "英语"));
    nodes(actualControl).find(node => node.type === "button" && node.props["aria-label"] === "清除科目英语").props.onClick();
    assert.equal(h.state[10].subject, ""); assert.equal(h.state[10].season, "秋季");
    assert.equal(paged(h.render(), "FY28 计划新生").props.rows.length, 2, "only the user's explicit chip removal widens this scope");
    assert.equal(JSON.stringify([h.state[1], h.state[2]]), saved); assert.equal(requests, 0);
  } finally { globalThis.fetch = originalFetch; restore(); }
});

test("phase 2 expanded manual adjustments reveal every effective filter on tasks with hidden season or subject", () => {
  const restore = fakeBrowser();
  try {
    const combos = [combination({ entry: false }), combination({ id: "combo-English", subject: "英语", entry: false })];
    const resultRows = combos.flatMap(combo => seasons.map(season => row({ ...combo, combination_id: combo.id, season })));
    const fixture = envelope({ draft: draft({ inputs: inputs({ combinations: combos }), calculation: calculation({ rows: resultRows }) }) });
    for (const task of [1, 2, 3]) {
      const h = pageHarness({ stateData: fixture, activeTask: task }); h.state[10] = { ...filters, subject: "英语", season: "秋季" };
      const before = JSON.stringify([h.state[1], h.state[2], h.state[10]]);
      const main = scopeControl(h); assert.equal(main.props.dimensions.some(item => item.key === (task === 3 ? "subject" : "season")), false);
      button(h.render(), "手动调节").props.onClick();
      const manual = nodes(h.render()).find(node => typeof node.type === "function" && node.type.name === "BudgetScopeBar" && node.props.label === "手动调节范围");
      assert.ok(manual); assert.equal(manual.props.dimensions.length, 6); assert.equal(manual.props.selectLabelPrefix, "手动调节筛选");
      assert.equal(manual.props.count, 1);
      assert.deepEqual(paged(h.render(), "预算手动调节明细").props.rows.map(item => [item.subject, item.season]), [["英语", "秋季"]]);
      const actualControl = manual.type(manual.props);
      for (const name of ["手动调节筛选季度", "手动调节筛选科目"]) assert.ok(nodes(actualControl).some(node => node.type === "select" && node.props["aria-label"] === name));
      button(h.render(), "手动调节").props.onClick();
      assert.equal(JSON.stringify([h.state[1], h.state[2], h.state[10]]), before, "opening and closing a scope never changes facts or retained filters");
      button(h.render(), "手动调节").props.onClick();
      manual.props.onChange({ ...manual.props.filters, campus: "不存在的校区" });
      assert.equal(paged(h.render(), "预算手动调节明细").props.rows.length, 0, "empty intersections never widen to all adjustments");
    }
  } finally { restore(); }
});

test("phase 2 tables state their limited grouping scope without changing campus or shared class parameter rows", () => {
  const restore = fakeBrowser();
  try {
    const h = pageHarness({ activeTask: 2 }); h.state[10] = { ...filters, subject: "英语", grade: "不存在的年级", season: "秋季" };
    assert.match(h.html(), /校区入口 · 仅按校区/);
    assert.deepEqual(paged(h.render(), "FY28 校区入口").props.rows, h.state[1].draft.calculation.entry_allocations);
    h.state[9] = 4;
    assert.match(h.html(), /班量与班额 · 按赛道、产品/);
    assert.deepEqual(paged(h.render(), "班量参数").props.rows.map(({ index, ...item }) => item), h.state[2].class_sizes);
  } finally { restore(); }
});

test("class issue navigation prefilters the exact business and never edits budget inputs", async () => {
  const restore = fakeBrowser(), original = globalThis.fetch;
  try {
    const harness = pageHarness(), before = JSON.stringify(harness.state[2]);
    globalThis.fetch = async () => Response.json(readiness());
    checkClasses(harness); await flush();
    classPanel(harness.render()).props.onNavigate(readinessRow(), "manual");
    assert.equal(harness.state[9], 3); assert.equal(harness.state[21], true); assert.deepEqual(harness.state[10], { season: "秋季", track: "智慧空间", campus: "西直门", product: "常规班", grade: "七年级", subject: "数学" });
    assert.equal(JSON.stringify(harness.state[2]), before); assert.deepEqual(harness.state[3], {});
  } finally { globalThis.fetch = original; restore(); }
});

test("current edits invalidate readiness, request failures retain saved issues and historical checks never POST", async () => {
  const restore = fakeBrowser(), original = globalThis.fetch; let calls = 0;
  try {
    globalThis.fetch = async () => { calls++; return Response.json(readiness()); };
    const issue = { code: "BELOW_MINIMUM_CLASS_SIZE", combination_id: "combo-1", season: "秋季", severity: "WARNING", message: "人数不足" };
    const harness = pageHarness({ stateData: envelope({ draft: draft({ calculation: calculation({ issues: [issue] }) }) }) });
    checkClasses(harness); await flush(); assert.equal(classPanel(harness.render()).props.checked, true);
    harness.state[3] = { [key("entry")]: "125" }; assert.equal(classPanel(harness.render()).props.checked, false); assert.equal(classPanel(harness.render()).props.stale, true);
    globalThis.fetch = async () => { calls++; return Response.json({ detail: "成班查询失败" }, { status: 503 }); };
    checkClasses(harness); await flush(); assert.match(harness.state[5], /成班查询失败/); assert.equal(classPanel(harness.render()).props.rows.length, 1);
    button(harness.render(), "放弃修改").props.onClick(); assert.equal(harness.state[26], null); assert.equal(harness.state[27], false);
    harness.state[16] = envelope({ draft: draft({ id: "historical-N0", sequence: 0, calculation: calculation({ issues: [issue] }) }), read_only: true });
    const beforeCalls = calls; openProblems(harness); await flush(); assert.equal(calls, beforeCalls);
    const panel = classPanel(harness.render()); assert.equal(panel.props.readOnly, true); assert.equal(panel.props.rows.length, 1);
    const html = renderToStaticMarkup(React.createElement(NextYearClassReadinessPanel, panel.props)); assert.match(html, /历史存档结果/); assert.doesNotMatch(html, /<button[^>]*>(?:重新检查|调整人次|调整参数|核对是否关闭)<\/button>/);
  } finally { globalThis.fetch = original; restore(); }
});

const repairConstraints = Object.fromEntries(["enabled_scopes_preserved", "source_preserved", "opening_carryover_preserved", "minimum_preserved", "entry_total_preserved", "price_and_discount_fixed", "user_locks_preserved", "operating_target_unchanged"].map(key => [key, true]));
const repairAction = (overrides = {}) => ({ ...combination(), combination_id: "combo-1", season: "秋季", kind: "NEW_STUDENT_TARGET", field: "plans.students", before: 1, after: 7, before_total_students: 4, after_total_students: 10, old_students: 3, minimum: 10, additional_students: 6, plan_is_not_actual: true, manual_target_priority: true, reason: "FY28招生目标建议，保留全部业务并达到原最低人数", ...overrides });
const repairPlans = base => base.plans.map(plan => plan.season === "秋季" ? { ...plan, students: 7, locked: true, reason: "FY28招生目标建议" } : plan);
const repairResult = (overrides = {}) => ({ source, status: "READY", message: "招生目标建议已生成，确认后可保存为新草稿", proposed_inputs: inputs({ plans: repairPlans(inputs()) }), before_calculation: calculation({ rows: [row({ season: "秋季", total_students: 4, new_students: 1, old_students: 3 })] }), calculation: calculation({ rows: [row({ season: "秋季", total_students: 10, new_students: 7, old_students: 3 })] }), impact: {}, actions: [repairAction()], issue_count: { before: 390, after: 0 }, before_issues: [], after_issues: [], unresolved: [], lock_conflicts: [], entry_soft_deviations: [], constraints: repairConstraints, summary: { entry_reallocated_students: 20, new_target_count: 1, additional_new_students: 6, changed_rate_bound_count: 2 }, ...overrides });
const repairPanel = tree => nodes(tree).find(node => typeof node.type === "function" && node.type.name === "NextYearClassRepairPanel");
const selectionTree = tree => {
  const element = nodes(tree).find(node => node.type?.name === "NextYearActionSelection");
  const react = { ...React, useState(initial) { return [typeof initial === "function" ? initial() : initial, () => {}]; }, useMemo(fn) { return fn(); }, useEffect() {} };
  return compile(name => name === "react" ? react : require(name)).NextYearActionSelection(element.props);
};

test("class repair request is a pure preview bound to exact edited inputs and never fills an absent target", async () => {
  const original = globalThis.fetch; const requests = [], prepared = inputs({ target_revenue_yuan: null, track_targets: { 智慧空间: null } });
  globalThis.fetch = async (url, options) => { requests.push({ url, body: JSON.parse(options.body) }); return Response.json(repairResult({ proposed_inputs: prepared })); };
  try {
    await proposeNextYearClassRepair(identity, "current-N8", prepared);
    assert.equal(requests.length, 1); assert.match(requests[0].url, /fy28\/class-repair$/); assert.deepEqual(requests[0].body, { parent_id: "current-N8", inputs: prepared });
    assert.equal(prepared.target_revenue_yuan, null); assert.doesNotMatch(requests[0].url, /revise|prepare|confirm/);
  } finally { globalThis.fetch = original; }
});

test("generating and cancelling a class scheme preserves all pending edits, preview and notes", async () => {
  const restore = fakeBrowser(), original = globalThis.fetch, requests = [];
  try {
    const withoutTarget = inputs({ target_revenue_yuan: null, track_targets: { 智慧空间: null } });
    const harness = pageHarness({ edited: { [key("entry")]: "125" } }); harness.state[2] = withoutTarget; harness.state[8] = "保留我的备注";
    globalThis.fetch = async (url, options) => { requests.push({ url, body: JSON.parse(options.body) }); return Response.json(url.endsWith("class-repair") ? repairResult({ proposed_inputs: { ...withoutTarget, entry_total: 125 } }) : readiness()); };
    checkClasses(harness); await flush();
    classPanel(harness.render()).props.onGenerate(); await flush();
    assert.equal(requests.length, 2); assert.match(requests[1].url, /class-repair$/); assert.equal(requests[1].body.inputs.entry_total, 125); assert.equal(requests[1].body.inputs.target_revenue_yuan, null);
    assert.equal(harness.state[2].entry_total, 100); assert.equal(harness.state[3][key("entry")], "125"); assert.equal(harness.state[8], "保留我的备注"); assert.equal(harness.state[7], null); assert.equal(harness.state[1].draft.sequence, 1);
    const panel = repairPanel(harness.render()); assert.ok(panel); assert.equal(panel.props.result.issue_count.after, 0);
    panel.props.onCancel(); assert.equal(harness.state[29], null); assert.equal(harness.state[3][key("entry")], "125"); assert.equal(harness.state[8], "保留我的备注"); assert.equal(harness.state[2].entry_total, 100);
  } finally { globalThis.fetch = original; restore(); }
});

test("class scheme uses backend current-input baseline, offers full numeric change exports and distinguishes recruitment targets from actuals", () => {
  const result = repairResult({ actions: [repairAction(), repairAction({ kind: "ENTRY_REALLOCATION", field: "entry_students", before: 100, after: 120, season: "暑假" }), repairAction({ kind: "RATE_BOUNDARY", field: "share_upper_ppm", before: 300000, after: 700000 })] });
  const tree = NextYearClassRepairPanel({ result, busy: false, onApply() {}, onCancel() {} });
  const impact = nodes(tree).find(node => typeof node.type === "function" && node.type.name === "ImpactComparison"); assert.equal(impact.props.before.rows[0].total_students, 4); assert.equal(impact.props.after.rows[0].total_students, 10);
  const bounds = paged(selectionTree(tree), "建议参数边界建议明细"), projection = bounds.props.exportRow(bounds.props.rows[0]); assert.equal(projection.before_value, 30); assert.equal(projection.after_value, 70); assert.equal(projection.unit, "%");
  const html = renderToStaticMarkup(React.createElement(NextYearClassRepairPanel, { result, busy: false, onApply() {}, onCancel() {} }));
  assert.match(text(html), /390 → 0/); assert.match(html, /建议预览 · 未应用 · 未保存/); assert.match(html, /招生目标，不是实际招生/); assert.match(html, /新生目标/); assert.match(html, /一键复制/); assert.match(html, /导出明细/); assert.ok(button(selectionTree(tree), "应用并预览"));
});

test("only an intact READY scheme with zero remaining issues can be applied; partial or protected-field changes cannot", () => {
  assert.equal(canApplyNextYearClassRepair(repairResult()), true);
  for (const result of [repairResult({ status: "PARTIAL", issue_count: { before: 390, after: 4 }, unresolved: [{ code: "PLAN_LOCKED", message: "新生目标已锁定", combination_id: "combo-1", season: "秋季" }] }), repairResult({ constraints: { ...repairConstraints, minimum_preserved: false } }), repairResult({ proposed_inputs: null }), repairResult({ calculation: calculation({ complete: false }) })]) {
    assert.equal(canApplyNextYearClassRepair(result), false);
    const html = renderToStaticMarkup(React.createElement(NextYearClassRepairPanel, { result, busy: false, onApply() {}, onCancel() {} })); assert.doesNotMatch(html, /<button[^>]*>应用并预览<\/button>/); assert.match(html, /原预算未修改/);
  }
});

test("class scheme separates stability reserve from recruitment targets and limits its stated scope", () => {
  const result = repairResult({
    actions: [repairAction({ stable_old_floor: 1, stability_reserve_students: 2 })],
    summary: { entry_reallocated_students: 63, new_target_count: 527, additional_new_students: 2495, changed_rate_bound_count: 3, stability_reserve_students: 1066 },
    adjustable_range_class_safe: true, stability_unresolved: [],
    stability_scope: { entry_allocation_fixed: true, rate_bounds: "EXPLICIT_OR_DEFAULT_PLUS_MINUS_5PP", user_and_zero_old_seed_rate_locks_preserved: true, integer_rounding_guard: "SEPARATE_OLD_NEW_FLOOR_AND_CEILING", not_a_revenue_target_reachability_proof: true },
  });
  const tree = NextYearClassRepairPanel({ result, busy: false, onApply() {}, onCancel() {} });
  const html = renderToStaticMarkup(React.createElement(NextYearClassRepairPanel, { result, busy: false, onApply() {}, onCancel() {} }));
  assert.match(text(html), /新增招生目标 2,495 人次 含调平预留 1,066 人次/);
  assert.match(html, /入口分配不变、建议比例范围内/); assert.match(html, /不保证任意经营目标可达/);
  const targets = paged(selectionTree(tree), "建议新生招生目标明细"), values = targets.props.exportRow(targets.props.rows[0]);
  assert.equal(values.stable_old_floor, 1); assert.equal(values.stability_reserve_students, 2);
  assert.equal(values.reason, result.actions[0].reason); assert.match(text(html), /调平预留人次/);
  assert.equal(targets.props.exportColumns.find(column => column.key === "stable_old_floor").format, "count");
  assert.equal(targets.props.exportColumns.find(column => column.key === "stability_reserve_students").format, "count");
  assert.ok(targets.props.headers.includes("调平预留人次"));
});

test("a currently legal class scheme remains applicable while unproven adjustable ranges are visible", () => {
  const result = repairResult({ adjustable_range_class_safe: false, stability_unresolved: [{ code: "RATE_LOCKED", message: "秋季续班率已锁定，整个可调范围的成班未获证明", combination_id: "combo-1", season: "秋季" }] });
  assert.equal(canApplyNextYearClassRepair(result), true);
  const tree = NextYearClassRepairPanel({ result, busy: false, onApply() {}, onCancel() {} });
  assert.ok(button(selectionTree(tree), "应用并预览"));
  const html = renderToStaticMarkup(React.createElement(NextYearClassRepairPanel, { result, busy: false, onApply() {}, onCancel() {} }));
  assert.match(html, /当前已成班；锁定项无法保证整个可调范围内持续成班，后续调平仍逐项校验/);
  assert.match(html, /预留范围提示/); assert.match(html, /秋季续班率已锁定/); assert.doesNotMatch(html, /预留适用：/);
});

test("apply is local preview only, save explicitly creates the next version, and discard returns the original draft", async () => {
  const restore = fakeBrowser(), original = globalThis.fetch, requests = [];
  try {
    const noTarget = inputs({ target_revenue_yuan: null, track_targets: { 智慧空间: null } }), proposed = { ...noTarget, plans: repairPlans(noTarget) };
    const result = repairResult({ proposed_inputs: proposed });
    const harness = pageHarness(); harness.state[2] = noTarget;
    globalThis.fetch = async (url, options) => { requests.push({ url, body: JSON.parse(options.body) }); return Response.json(url.endsWith("class-readiness") ? readiness() : url.endsWith("class-repair") ? result : envelope({ draft: draft({ id: "new-N2", sequence: 2, parent_id: "next-year-N1", inputs: proposed, calculation: result.calculation }) })); };
    checkClasses(harness); await flush(); classPanel(harness.render()).props.onGenerate(); await flush();
    repairPanel(harness.render()).props.onApply();
    assert.equal(harness.state[5], "");
    assert.equal(requests.length, 2); assert.equal(harness.state[2].plans.find(plan => plan.season === "秋季").students, 7); assert.equal(harness.state[2].target_revenue_yuan, null); assert.equal(harness.state[7].rows[0].total_students, 10); assert.equal(harness.state[1].draft.sequence, 1); assert.match(harness.html(), /未保存预览/);
    button(harness.render(), "保存新版本").props.onClick(); await flush();
    assert.equal(requests.length, 3); assert.match(requests[2].url, /revise$/); assert.equal(requests[2].body.parent_id, "next-year-N1"); assert.equal(requests[2].body.reason, "采用成班方案"); assert.equal(requests[2].body.inputs.target_revenue_yuan, null); assert.equal(harness.state[1].draft.sequence, 2);
    const cancel = pageHarness(); cancel.state[29] = { key: JSON.stringify({ version: "next-year-N1", inputs: cancel.state[2], edits: {} }), result: repairResult() };
    openProblems(cancel); repairPanel(cancel.render()).props.onApply(); assert.match(cancel.state[6], /成班方案已应用为未保存预览/);
    button(cancel.render(), "放弃修改").props.onClick(); assert.deepEqual(cancel.state[2], cancel.state[1].draft.inputs); assert.equal(cancel.state[7], null); assert.equal(cancel.state[29], null); assert.equal(cancel.state[6], ""); assert.doesNotMatch(cancel.html(), /成班方案已应用为未保存预览|保存后生效/);
  } finally { globalThis.fetch = original; restore(); }
});

test("editing any input expires the scheme; historical version cannot generate or apply a current scheme", async () => {
  const restore = fakeBrowser(), original = globalThis.fetch; let calls = 0;
  try {
    const harness = pageHarness({ activeTask: 2 }), result = repairResult(); harness.state[29] = { key: JSON.stringify({ version: "next-year-N1", inputs: harness.state[2], edits: {} }), result };
    openProblems(harness); assert.ok(repairPanel(harness.render())); labeledInput(harness.render(), "年度入口人次").props.onChange({ target: { value: "125" } });
    assert.equal(repairPanel(harness.render()), undefined); assert.match(harness.html(), /上一份成班方案已作废/); assert.equal(harness.state[2].entry_total, 100);
    globalThis.fetch = async () => { calls++; throw new Error("不应发送历史修复请求"); };
    harness.state[3] = {}; harness.state[16] = envelope({ draft: draft({ id: "historical-N0", sequence: 0 }), read_only: true });
    assert.equal(repairPanel(harness.render()), undefined); openProblems(harness);
    const panel = classPanel(harness.render()); panel.props.onGenerate(); await flush(); assert.equal(calls, 0);
    const html = renderToStaticMarkup(React.createElement(NextYearClassReadinessPanel, panel.props)); assert.doesNotMatch(html, /<button[^>]*>生成成班方案<\/button>/);
  } finally { globalThis.fetch = original; restore(); }
});

test("failed class scheme generation keeps the current inputs and raw edits without applying or saving", async () => {
  const restore = fakeBrowser(), original = globalThis.fetch;
  try {
    const harness = pageHarness({ edited: { [key("entry")]: "125" } }); harness.state[27] = true;
    globalThis.fetch = async () => Response.json({ detail: "方案计算失败，请重试" }, { status: 503 });
    classPanel(harness.render()).props.onGenerate(); await flush();
    assert.equal(harness.state[2].entry_total, 100); assert.equal(harness.state[3][key("entry")], "125"); assert.equal(harness.state[29], null); assert.equal(harness.state[7], null); assert.equal(harness.state[1].draft.sequence, 1); assert.match(harness.state[5], /方案计算失败/);
  } finally { globalThis.fetch = original; restore(); }
});

test("summarizeNextYearEdits returns no groups when there are no edits", () => {
  const summary = summarizeNextYearEdits(inputs(), {});
  assert.equal(summary.totalCount, 0);
  assert.deepEqual(summary.groups, []);
});

test("summarizeNextYearEdits groups edits by category and preserves display order", () => {
  const edits = {
    [key("rates", "rate-0", "renewal_ppm")]: "85",
    [key("target")]: "500000",
    [key("entry")]: "120",
    [key("rates", "rate-1", "share_ppm")]: "30",
  };
  const summary = summarizeNextYearEdits(inputs(), edits);
  assert.equal(summary.totalCount, 4);
  assert.deepEqual(summary.groups.map(g => g.category), ["target", "entry_total", "rates"]);
  const ratesGroup = summary.groups.find(g => g.category === "rates");
  assert.equal(ratesGroup.items.length, 2);
  assert.ok(ratesGroup.items.every(item => item.task === 3));
});

test("summarizeNextYearEdits exposes before/after display and preserves the edit key for undo", () => {
  const editKey = key("rates", "rate-0", "renewal_ppm");
  const summary = summarizeNextYearEdits(inputs(), { [editKey]: "85" });
  const item = summary.groups[0].items[0];
  assert.equal(item.key, editKey);
  assert.equal(item.category, "rates");
  assert.equal(item.unit, "percent");
  assert.equal(item.beforeNumber, 80);
  assert.equal(item.afterNumber, 85);
  assert.match(item.beforeDisplay, /80/);
  assert.match(item.afterDisplay, /85/);
});

test("summarizeNextYearEdits reports max percentage-point delta for rates and bounds", () => {
  const edits = {
    [key("rates", "rate-0", "renewal_ppm")]: "85",   // was 80, +5pp
    [key("rates", "rate-1", "renewal_ppm")]: "75",   // was 80, -5pp
    [key("rates", "rate-2", "share_ppm")]: "28",     // was 25, +3pp
  };
  const summary = summarizeNextYearEdits(inputs(), edits);
  const ratesGroup = summary.groups.find(g => g.category === "rates");
  assert.match(ratesGroup.scale, /5\s*个百分点/);
});

test("summarizeNextYearEdits reports student-count delta for entry/plan/class size", () => {
  const edits = {
    [key("entry")]: "150",                                              // was 100, +50
    [key("plan", "combo-1", "暑假")]: "30",                             // was 25, +5
    [key("class", "0")]: "12",                                          // was 10 (min), +2
  };
  const summary = summarizeNextYearEdits(inputs(), edits);
  const entryGroup = summary.groups.find(g => g.category === "entry_total");
  assert.match(entryGroup.scale, /\+50\s*人/);
  const planGroup = summary.groups.find(g => g.category === "plan");
  assert.match(planGroup.scale, /\+5\s*人/);
});

test("summarizeNextYearEdits reports money delta in 万元 for target and track target", () => {
  const edits = {
    [key("target")]: "500000",         // was 400,000, +100,000 = +10 万
    [key("track-target", "智慧空间")]: "500000",
  };
  const summary = summarizeNextYearEdits(inputs(), edits);
  const targetGroup = summary.groups.find(g => g.category === "target");
  assert.match(targetGroup.scale, /\+10\s*万元/);
  const trackGroup = summary.groups.find(g => g.category === "track_target");
  assert.match(trackGroup.scale, /\+10\s*万元/);
});

test("summarizeNextYearEdits labels empty string as 清空/自动分配 and preserves invalid flag", () => {
  const emptyKey = key("entry-count", "combo-1");
  const invalidKey = key("entry");
  const summary = summarizeNextYearEdits(inputs(), { [emptyKey]: "", [invalidKey]: "abc" }, { [invalidKey]: "入口人数须为0至1000万的整数" });
  const emptyItem = summary.groups.flatMap(g => g.items).find(i => i.key === emptyKey);
  assert.match(emptyItem.afterDisplay, /自动分配/);
  const invalidItem = summary.groups.flatMap(g => g.items).find(i => i.key === invalidKey);
  assert.equal(invalidItem.invalid, true);
});

test("summarizeNextYearEdits picks the right task tab for each section", () => {
  const edits = {
    [key("target")]: "500000",                                 // task 1
    [key("track-target", "智慧空间")]: "500000",               // task 1
    [key("entry")]: "150",                                      // task 2
    [key("entry-count", "combo-1")]: "50",                      // task 2
    [key("rates", "rate-0", "renewal_ppm")]: "85",              // task 3
    [key("bounds", "rate-0", "renewal_lower_ppm")]: "70",       // task 3
    [key("plan", "combo-1", "暑假")]: "30",                     // task 3
    [key("finance", "finance-0", "price_yuan")]: "1200",        // task 4
    [key("class", "0")]: "12",                                  // task 4
    [key("tax")]: "1.09",                                       // task 4
  };
  const summary = summarizeNextYearEdits(inputs(), edits);
  for (const item of summary.groups.flatMap(g => g.items)) {
    if (["target", "track_target"].includes(item.category)) assert.equal(item.task, 1);
    else if (["entry_total", "entry_detail"].includes(item.category)) assert.equal(item.task, 2);
    else if (["rates", "bounds", "plan"].includes(item.category)) assert.equal(item.task, 3);
    else if (["finance", "class_size", "tax"].includes(item.category)) assert.equal(item.task, 4);
  }
});

test("M0 includes staged changes and raw edits in one undoable saved-version diff", () => {
  const restore = fakeBrowser();
  try {
    const harness = pageHarness({ activeTask: 1, edited: { [key("rates", "rate-1", "renewal_ppm")]: "85" } });
    harness.state[2].combinations[0].enabled = false;
    button(harness.render(), "改动 2 项").props.onClick();
    const panel = nodes(harness.render()).find(node => node.props?.summary && node.props?.onUndo);
    const changed = panel.props.summary.groups.flatMap(group => group.items);
    assert.equal(changed.length, 2);
    const toggle = changed.find(item => item.key === key("enabled", "combo-1"));
    assert.ok(toggle); panel.props.onUndo(toggle);
    assert.equal(harness.state[2].combinations[0].enabled, true);
    assert.equal(harness.state[3][key("rates", "rate-1", "renewal_ppm")], "85");
    assert.equal(harness.state[1].draft.inputs.combinations[0].enabled, true);
  } finally { restore(); }
});

test("M0 gives valid inputs stable focus keys and routes bounds errors to their owning task", async () => {
  const restore = fakeBrowser();
  try {
    const fields = nodes(pageHarness({ activeTask: 1 }).render()).filter(node => node.type === "input");
    assert.ok(fields.some(node => node.props["data-ny-edit-key"] === key("target") && !node.props["aria-describedby"]));
    const harness = pageHarness({ edited: { [key("bounds", "rate-1", "renewal_lower_ppm")]: "110" } });
    await button(harness.render(), "查看影响").props.onClick();
    assert.equal(harness.state[9], 3);
    assert.ok(harness.state[11][key("bounds", "rate-1", "renewal_lower_ppm")]);
    assert.equal(harness.state[30], key("bounds", "rate-1", "renewal_lower_ppm"));
  } finally { restore(); }
});

test("M3 context panel is on demand, single purpose and closes with Escape", () => {
  let expanded = false, titleFocused = 0, stopped = 0;
  const heading = { current: { focus() { titleFocused++; } } }, effects = [];
  const { NextYearSidePanel } = compile(name => name === "react" ? { ...React, useRef() { return heading; }, useEffect(effect) { effects.push(effect); } } : require(name));
  const render = () => NextYearSidePanel({ title: "批量修改", expanded, onClose() { expanded = false; }, children: React.createElement("p", null, "批量工具内容") });
  assert.equal(render(), null);
  expanded = true;
  const tree = render(); effects.at(-1)();
  assert.equal(titleFocused, 1); assert.equal(tree.props["aria-labelledby"], "ny-context-title");
  assert.equal(nodes(tree).filter(node => node.props?.role === "tablist").length, 0);
  assert.match(text(renderToStaticMarkup(tree)), /批量修改 关闭 批量工具内容/);
  tree.props.onKeyDown({ key: "Escape", stopPropagation() { stopped++; } });
  assert.equal(expanded, false); assert.equal(stopped, 1); assert.equal(render(), null);
});

test("M3 single-purpose tool and task switches retain unapplied drafts without saving", () => {
  const restore = fakeBrowser(), original = globalThis.fetch; let calls = 0;
  try {
    globalThis.fetch = async () => { calls++; throw new Error("切换工具不应提交请求"); };
    const raw = { [key("rates", "rate-1", "renewal_ppm")]: "83" }, harness = pageHarness({ activeTask: 3, edited: raw });
    const saved = JSON.stringify(harness.state[1]), staged = JSON.stringify(harness.state[2]);
    button(harness.render(), "批量修改").props.onClick();
    const side = () => nodes(harness.render()).find(node => typeof node.type === "function" && node.type.name === "NextYearSidePanel");
    assert.equal(side().props.title, "批量修改"); assert.equal(side().props.expanded, true);
    nodes(harness.render()).find(node => node.type === "input" && node.props["aria-label"] === "批量修改比例").props.onChange({ target: { value: "85" } });
    assert.equal(nodes(harness.render()).find(node => node.type === "textarea"), undefined);
    button(harness.render(), "粘贴表格").props.onClick();
    const paste = "参数ID\t续班率\t新生占比\nrate-1\t85\t25";
    nodes(harness.render()).find(node => node.type === "textarea").props.onChange({ target: { value: paste } });
    assert.doesNotMatch(harness.html(), /aria-label="批量修改比例"/);
    side().props.onClose(); assert.equal(side(), undefined);
    nodes(harness.render()).find(node => typeof node.type === "function" && node.props?.task === 3 && node.props?.onChange).props.onChange(1);
    button(harness.render(), "新增校区 / 产品").props.onClick(); labeledInput(harness.render(), "校区").props.onChange({ target: { value: "待加入的新校区" } });
    nodes(harness.render()).find(node => typeof node.type === "function" && node.props?.task === 1 && node.props?.onChange).props.onChange(3);
    assert.equal(side(), undefined); button(harness.render(), "批量修改").props.onClick();
    assert.equal(nodes(harness.render()).find(node => node.type === "input" && node.props["aria-label"] === "批量修改比例").props.value, "85");
    button(harness.render(), "粘贴表格").props.onClick();
    assert.equal(nodes(harness.render()).find(node => node.type === "textarea").props.value, paste);
    nodes(harness.render()).find(node => typeof node.type === "function" && node.props?.task === 3 && node.props?.onChange).props.onChange(1);
    button(harness.render(), "新增校区 / 产品").props.onClick();
    assert.equal(labeledInput(harness.render(), "校区").props.value, "待加入的新校区");
    assert.deepEqual(harness.state[3], raw); assert.equal(JSON.stringify(harness.state[1]), saved); assert.equal(JSON.stringify(harness.state[2]), staged); assert.equal(calls, 0);
  } finally { globalThis.fetch = original; restore(); }
});

test("M3 bulk applies all 50 applicable rows locally and invalid paste cannot partially replace edits", () => {
  const restore = fakeBrowser(), original = globalThis.fetch; let calls = 0;
  try {
    globalThis.fetch = async () => { calls++; throw new Error("批量暂存不应保存预算"); };
    const combinations = Array.from({ length: 50 }, (_, index) => combination({ id: `combo-${index}`, campus: `校区${index}` }));
    const rates = combinations.map((combo, index) => ({ ...inputs().rates[1], id: `bulk-${index}`, campus: combo.campus }));
    const fixture = envelope({ draft: draft({ inputs: inputs({ combinations, rates }) }) });
    const harness = pageHarness({ stateData: fixture, activeTask: 3 }); button(harness.render(), "批量修改").props.onClick();
    assert.match(harness.html(), /当前筛选 · 50 条适用参数/);
    nodes(harness.render()).find(node => node.type === "input" && node.props["aria-label"] === "批量修改比例").props.onChange({ target: { value: "85" } });
    button(harness.render(), "应用到当前筛选").props.onClick();
    assert.equal(Object.keys(harness.state[3]).length, 50);
    for (const rate of rates) assert.equal(harness.state[3][key("rates", rate.id, "renewal_ppm")], "85");
    assert.deepEqual(harness.state[2], fixture.draft.inputs); assert.equal(harness.state[1].draft.sequence, 1);
    const pending = JSON.stringify(harness.state[3]);
    button(harness.render(), "粘贴表格").props.onClick();
    nodes(harness.render()).find(node => node.type === "textarea").props.onChange({ target: { value: "bulk-0\t84\t25\nunknown-id\t83\t25" } });
    button(harness.render(), "校验并暂存").props.onClick();
    assert.equal(JSON.stringify(harness.state[3]), pending); assert.match(harness.state[5], /不在当前筛选范围/); assert.equal(calls, 0);
  } finally { globalThis.fetch = original; restore(); }
});

test("M3 historical tools show original read-only plans and bounds with no bulk, add, apply or save actions", async () => {
  const restore = fakeBrowser(), original = globalThis.fetch; let calls = 0;
  try {
    globalThis.fetch = async () => { calls++; throw new Error("只读工具不应写请求"); };
    const historicalInputs = inputs({ plans: [{ combination_id: "combo-1", season: "秋季", students: 777 }] });
    historicalInputs.rates[1].renewal_lower_ppm = 700000;
    const historical = envelope({ draft: draft({ id: "historical-N0", sequence: 0, inputs: historicalInputs, calculation: calculation({ rows: [row({ season: "秋季", planned_new_applied: true, old_students: 0 })] }) }), read_only: true });
    const harness = pageHarness({ activeTask: 3 }); harness.state[16] = historical; const saved = JSON.stringify([harness.state[1], harness.state[2], historical]);
    button(harness.render(), "计划新生").props.onClick();
    const planInput = nodes(pagedRow(harness.render(), "FY28 计划新生")).find(node => node.type === "input");
    assert.equal(planInput.props.value, "777"); assert.equal(planInput.props.disabled, true);
    button(harness.render(), "调平范围").props.onClick();
    const bounds = nodes(pagedRow(harness.render(), "自动调平参数范围", 1)).filter(node => node.type === "input");
    assert.equal(bounds[0].props.value, "70"); assert.ok(bounds.every(node => node.props.disabled));
    for (const label of ["应用到当前筛选", "校验并暂存", "复制粘贴模板", "加入预算范围", "生成成班方案", "保存新版本", "应用并预览"]) assert.equal(button(harness.render(), label), undefined, label);
    openProblems(harness); await flush();
    assert.equal(calls, 0); assert.deepEqual(harness.state[3], {}); assert.equal(JSON.stringify([harness.state[1], harness.state[2], historical]), saved);
  } finally { globalThis.fetch = original; restore(); }
});

test("M3 incomplete new-business form arms leave protection and prevents saving a budget without that business", async () => {
  const restore = fakeBrowser(), original = globalThis.fetch; let calls = 0, prevented = 0;
  try {
    globalThis.fetch = async () => { calls++; throw new Error("未加入的新业务不能被静默遗漏后保存"); };
    const harness = pageHarness({ activeTask: 1, edited: { [key("entry")]: "125" } }), saved = JSON.stringify(harness.state[1]);
    button(harness.render(), "新增校区 / 产品").props.onClick();
    labeledInput(harness.render(), "校区").props.onChange({ target: { value: "尚未加入校区" } });
    const header = nodes(harness.render()).find(node => typeof node.type === "function" && node.type.name === "FY27Header");
    header.props.onBeforeLeave({ button: 0, currentTarget: { href: "http://127.0.0.1:5188/budget/forecast?archive=fy27" }, preventDefault() { prevented++; } });
    assert.equal(prevented, 1); assert.match(harness.state[12], /budget\/forecast/);
    button(harness.render(), "保存新版本").props.onClick(); await flush();
    assert.equal(calls, 0); assert.match(harness.state[5], /新业务未加入预算/); assert.equal(harness.state[9], 1);
    assert.equal(labeledInput(harness.render(), "校区").props.value, "尚未加入校区");
    assert.equal(harness.state[3][key("entry")], "125"); assert.equal(harness.state[2].combinations.length, 1); assert.equal(JSON.stringify(harness.state[1]), saved);
  } finally { globalThis.fetch = original; restore(); }
});

test("M3 unapplied paste alone arms leave protection and blocks preview until staged or cleared", async () => {
  const restore = fakeBrowser(), original = globalThis.fetch; let calls = 0, prevented = 0;
  try {
    globalThis.fetch = async () => { calls++; throw new Error("未暂存的粘贴不应被忽略"); };
    const harness = pageHarness({ activeTask: 3 }), paste = "rate-1\t85\t25", saved = JSON.stringify([harness.state[1], harness.state[2]]);
    button(harness.render(), "粘贴表格").props.onClick();
    nodes(harness.render()).find(node => node.type === "textarea").props.onChange({ target: { value: paste } });
    const header = nodes(harness.render()).find(node => typeof node.type === "function" && node.type.name === "FY27Header");
    header.props.onBeforeLeave({ button: 0, currentTarget: { href: "http://127.0.0.1:5188/budget/workspace?archive=fy27" }, preventDefault() { prevented++; } });
    assert.equal(prevented, 1); assert.match(harness.state[12], /budget\/workspace/);
    // Call the handler too: permission/dirty-state checks must not depend only on visual disabling.
    button(harness.render(), "查看影响").props.onClick(); await flush();
    assert.equal(calls, 0); assert.match(harness.state[5], /粘贴参数尚未暂存/); assert.equal(harness.state[9], 3);
    assert.equal(nodes(harness.render()).find(node => node.type === "textarea").props.value, paste);
    assert.deepEqual(harness.state[3], {}); assert.equal(JSON.stringify([harness.state[1], harness.state[2]]), saved);
  } finally { globalThis.fetch = original; restore(); }
});

test("M3 successful paste clears only its tool draft and previews exactly the staged numeric edits", async () => {
  const restore = fakeBrowser(), original = globalThis.fetch; const requests = [];
  try {
    globalThis.fetch = async (url, options) => { requests.push({ url, body: JSON.parse(options.body) }); return Response.json({ source, calculation: calculation() }); };
    const harness = pageHarness({ activeTask: 3 }); button(harness.render(), "粘贴表格").props.onClick();
    nodes(harness.render()).find(node => node.type === "textarea").props.onChange({ target: { value: "rate-1\t85\t25" } });
    button(harness.render(), "校验并暂存").props.onClick();
    assert.equal(nodes(harness.render()).find(node => node.type === "textarea").props.value, "");
    assert.deepEqual(harness.state[3], { [key("rates", "rate-1", "renewal_ppm")]: "85", [key("rates", "rate-1", "share_ppm")]: "25" });
    assert.equal(harness.state[2].rates[1].renewal_ppm, 800000); assert.equal(harness.state[1].draft.sequence, 1); assert.equal(requests.length, 0);
    button(harness.render(), "查看影响").props.onClick(); await flush();
    assert.equal(requests.length, 1); assert.match(requests[0].url, /\/preview$/); assert.equal(requests[0].body.inputs.rates[1].renewal_ppm, 850000);
    assert.equal(requests[0].body.inputs.rates[1].share_ppm, 250000); assert.equal(harness.state[1].draft.sequence, 1);
  } finally { globalThis.fetch = original; restore(); }
});

const topMetrics = tree => nodes(tree).find(node => typeof node.type === "function" && node.type.name === "NextYearTopMetrics");
const comparisonEffect = harness => {
  harness.render();
  const effect = harness.effects.find(fn => fn.toString().includes("comparisonCache"));
  assert.ok(effect, "comparison owns one cleanup-capable lazy loading effect");
  return effect;
};

test("M4 editing tasks compact their single full-year summary while the result task retains full metrics", () => {
  const restore = fakeBrowser();
  try {
    for (const task of [1, 2, 3, 4, 5]) {
      const harness = pageHarness({ activeTask: task }), before = JSON.stringify(harness.state[2]);
      harness.state[10] = { ...filters, campus: "不存在的校区" };
      const panels = nodes(harness.render()).filter(node => typeof node.type === "function" && node.type.name === "NextYearTopMetrics");
      assert.equal(panels.length, 1); assert.equal(panels[0].props.calculation.annual.revenue_yuan, "360000");
      assert.equal(panels[0].props.compact, task !== 5);
      const html = renderToStaticMarkup(panels[0]);
      assert.equal(html.includes("ny-summary-compact"), task !== 5);
      assert.match(html, /aria-label="全年预算总览"/); assert.match(text(html), /全年结转收入（万元） 36/); assert.match(text(html), /全年总人次 400/);
      assert.match(html, /已保存 N1/); assert.equal(JSON.stringify(harness.state[2]), before);
    }
  } finally { restore(); }
});

test("M4 preview remains on the edited task and the yellow result bar uses only the authoritative response", async () => {
  const restore = fakeBrowser(), original = globalThis.fetch; const requests = [];
  try {
    const returned = calculation({ annual: totals({ revenue_yuan: "1234567.89", total_students: 432, gap_yuan: "-834567.89" }) });
    globalThis.fetch = async (url, options) => { requests.push({ url, body: JSON.parse(options.body) }); return Response.json({ source, calculation: returned }); };
    const harness = pageHarness({ activeTask: 4, edited: { [key("tax")]: "1.05" } }), saved = JSON.stringify(harness.state[1]);
    button(harness.render(), "查看影响").props.onClick(); await flush();
    assert.equal(harness.state[9], 4); assert.equal(requests.length, 1); assert.match(requests[0].url, /\/preview$/); assert.equal(requests[0].body.inputs.tax_factor, "1.05");
    const panel = topMetrics(harness.render()), html = renderToStaticMarkup(panel);
    assert.deepEqual(panel.props.calculation, returned); assert.match(html, /ny-top-results is-preview/); assert.match(html, /未保存预览/);
    assert.match(text(html), /全年结转收入（万元） 123.46/); assert.match(text(html), /全年总人次 432/); assert.match(text(html), /超额（万元） 83.46/);
    assert.equal(JSON.stringify(harness.state[1]), saved); assert.equal(harness.state[3][key("tax")], "1.05");
  } finally { globalThis.fetch = original; restore(); }
});

test("M4 result bar distinguishes an absent goal, true zero, negative gap and incomplete calculation", () => {
  const render = annual => renderToStaticMarkup(React.createElement(NextYearTopMetrics, { calculation: calculation({ annual: totals(annual) }), status: "已保存 N2", onSetTarget() {} }));
  const absent = render({ target_revenue_yuan: null, gap_yuan: null });
  assert.match(text(absent), /经营目标（万元） 待设置/); assert.match(text(absent), /目标差额（万元） —/); assert.match(absent, /设置目标/); assert.doesNotMatch(absent, /达标|达成率/);
  const zero = render({ revenue_yuan: "0", target_revenue_yuan: "0", gap_yuan: "0", total_students: 0 });
  assert.match(text(zero), /全年结转收入（万元） 0/); assert.match(text(zero), /经营目标（万元） 0/); assert.match(text(zero), /差额（万元） 0/); assert.match(text(zero), /全年总人次 0/); assert.doesNotMatch(zero, /待设置|设置目标/);
  assert.match(text(render({ revenue_yuan: "425000", gap_yuan: "-25000" })), /超额（万元） 2.5/);
  const incomplete = renderToStaticMarkup(React.createElement(NextYearTopMetrics, { calculation: calculation({ complete: false, annual: totals({ revenue_yuan: null, gap_yuan: null, total_students: null }) }), status: "未保存预览" }));
  assert.match(incomplete, /计算未完整/); assert.match(text(incomplete), /全年结转收入（万元） —/); assert.match(text(incomplete), /全年总人次 —/); assert.doesNotMatch(incomplete, /已达标|已达目标|已通过|100%/);
});

test("M4 historical result bar uses that version rather than current draft or unsaved preview and cannot save it", () => {
  const restore = fakeBrowser();
  try {
    const historical = envelope({ draft: draft({ id: "historical-N0", sequence: 0, calculation: calculation({ annual: totals({ revenue_yuan: "200000", total_students: 222 }) }) }), read_only: true });
    const harness = pageHarness({ activeTask: 2, activePreview: calculation({ annual: totals({ revenue_yuan: "9990000" }) }) }); harness.state[16] = historical;
    const panel = topMetrics(harness.render()), html = renderToStaticMarkup(panel);
    assert.equal(panel.props.calculation.annual.revenue_yuan, "200000"); assert.match(text(html), /全年结转收入（万元） 20/); assert.match(text(html), /全年总人次 222/);
    assert.match(html, /历史 N0 · 只读/); assert.doesNotMatch(html, /未保存预览|已保存 N1/);
    assert.equal(button(harness.render(), "保存新版本"), undefined); assert.equal(harness.state[1].draft.calculation.annual.revenue_yuan, "360000");
  } finally { restore(); }
});

test("M4 previous-version comparison lazily fetches the exact parent not history metadata and reuses its cache", async () => {
  const restore = fakeBrowser(), original = globalThis.fetch; const requests = [];
  try {
    const previous = envelope({ draft: draft({ id: "actual-parent-N1", sequence: 1, calculation: calculation({ annual: totals({ revenue_yuan: "350000", total_students: 390 }) }) }) });
    globalThis.fetch = async (url, options) => { requests.push({ url, method: options?.method ?? "GET" }); return Response.json(previous); };
    const harness = pageHarness({ stateData: envelope({ draft: draft({ id: "current-N2", sequence: 2, parent_id: "actual-parent-N1" }), history: [{ id: "unrelated-N99", sequence: 99, created_at: "2026-10-01T00:00:00Z", revenue_yuan: "999999999" }] }), activeTask: 3 });
    comparisonEffect(harness)(); assert.equal(requests.length, 0); assert.equal(comparisonPanel(harness), undefined);
    startComparison(harness); const cleanup = comparisonEffect(harness)(); await flush();
    assert.deepEqual(requests, [{ url: "/api/v1/next-year-budgets/fy28/versions/actual-parent-N1", method: "GET" }]);
    const panel = comparisonPanel(harness), html = renderToStaticMarkup(panel);
    assert.deepEqual(panel.props.before, previous.draft.calculation); assert.deepEqual(panel.props.after, harness.state[1].draft.calculation); assert.match(text(html), /结转收入变化（万元） \+1 35 → 36/); assert.match(text(html), /总人次变化 \+10 390 → 400/); assert.doesNotMatch(html, /999999999|N99/);
    assert.equal(harness.state[1].draft.id, "current-N2"); assert.equal(harness.state[9], 3);
    cleanup(); button(harness.render(), "收起上版对比").props.onClick(); comparisonEffect(harness)();
    assert.equal(comparisonPanel(harness), undefined);
    button(harness.render(), "对比上版").props.onClick(); comparisonEffect(harness)(); await flush();
    assert.equal(requests.length, 1); assert.deepEqual(comparisonPanel(harness).props.before, previous.draft.calculation);
  } finally { globalThis.fetch = original; restore(); }
});

test("M4 a comparison failure preserves current results, pending edits and the active task", async () => {
  const restore = fakeBrowser(), original = globalThis.fetch; let calls = 0;
  try {
    globalThis.fetch = async () => { calls++; return Response.json({ detail: "上版暂时无法读取" }, { status: 503 }); };
    const harness = pageHarness({ stateData: envelope({ draft: draft({ parent_id: "parent-N0" }) }), activeTask: 4, edited: { [key("tax")]: "1.05" } });
    const before = JSON.stringify([harness.state[1], harness.state[2], harness.state[3]]);
    startComparison(harness); comparisonEffect(harness)(); await flush();
    assert.equal(calls, 1); assert.match(harness.html(), /上版暂时无法读取/); assert.match(text(renderToStaticMarkup(topMetrics(harness.render()))), /全年结转收入（万元） 36/);
    assert.equal(comparisonPanel(harness), undefined); assert.equal(harness.state[9], 4); assert.equal(JSON.stringify([harness.state[1], harness.state[2], harness.state[3]]), before);
  } finally { globalThis.fetch = original; restore(); }
});

test("M4 changing year or viewed version ignores the cancelled late comparison response", async () => {
  const restore = fakeBrowser(), original = globalThis.fetch;
  try {
    for (const switchKind of ["year", "version"]) {
      const pending = [];
      globalThis.fetch = (url, options) => new Promise(resolve => pending.push({ url, method: options?.method ?? "GET", resolve }));
      const harness = pageHarness({ stateData: envelope({ draft: draft({ id: "current-N5", sequence: 5, parent_id: "old-parent-N4" }) }), activeTask: 2 });
      startComparison(harness); const cancel = comparisonEffect(harness)(); assert.equal(pending.length, 1);
      cancel();
      if (switchKind === "year") {
        harness.state[15] = 2029;
        harness.state[1] = envelope({ draft: draft({ id: "fy29-N2", sequence: 2, parent_id: "new-parent-N1", inputs: inputs({ fiscal_year: 2029 }), calculation: calculation({ fiscal_year: 2029 }) }) });
      } else harness.state[16] = envelope({ draft: draft({ id: "historical-N2", sequence: 2, parent_id: "new-parent-N1" }), read_only: true });
      comparisonEffect(harness)(); assert.equal(pending.length, 2);
      assert.match(pending[1].url, switchKind === "year" ? /fy29\/versions\/new-parent-N1$/ : /fy28\/versions\/new-parent-N1$/);
      const year = switchKind === "year" ? 2029 : 2028;
      pending[1].resolve(Response.json(envelope({ draft: draft({ id: "new-parent-N1", sequence: 1, inputs: inputs({ fiscal_year: year }), calculation: calculation({ fiscal_year: year, annual: totals({ revenue_yuan: "300000" }) }) }) }))); await flush();
      pending[0].resolve(Response.json(envelope({ draft: draft({ id: "old-parent-N4", sequence: 4, calculation: calculation({ annual: totals({ revenue_yuan: "999000000" }) }) }) }))); await flush();
      const panel = comparisonPanel(harness); assert.equal(panel.props.before.annual.revenue_yuan, "300000"); assert.equal(panel.props.before.fiscal_year, year); assert.deepEqual(panel.props.after, switchKind === "year" ? harness.state[1].draft.calculation : harness.state[16].draft.calculation); assert.doesNotMatch(renderToStaticMarkup(panel), /99,900|N4/);
      assert.equal(harness.state[9], 2); assert.ok(pending.every(request => request.method === "GET"));
    }
  } finally { globalThis.fetch = original; restore(); }
});

test("M4 rejects a mismatched parent snapshot or fiscal year without contaminating current totals", async () => {
  const restore = fakeBrowser(), original = globalThis.fetch;
  try {
    for (const previous of [draft({ id: "different-parent", sequence: 0 }), draft({ id: "parent-N0", sequence: 0, inputs: inputs({ fiscal_year: 2029 }) })]) {
      globalThis.fetch = async () => Response.json(envelope({ draft: previous }));
      const harness = pageHarness({ stateData: envelope({ draft: draft({ parent_id: "parent-N0" }) }) });
      startComparison(harness); comparisonEffect(harness)(); await flush();
      assert.match(harness.html(), /上版快照与当前预算不匹配/); assert.equal(comparisonPanel(harness), undefined);
      assert.equal(topMetrics(harness.render()).props.calculation.annual.revenue_yuan, "360000"); assert.equal(harness.state[1].draft.id, "next-year-N1");
    }
  } finally { globalThis.fetch = original; restore(); }
});

const globalFilter = (harness, label) => {
  const control = scopeControl(harness); assert.ok(control, "one parent-owned scope control");
  const dimension = control.props.dimensions.find(item => item.label === label); assert.ok(dimension, label);
  return { props: { value: control.props.filters[dimension.key], onChange: event => control.props.onChange({ ...control.props.filters, [dimension.key]: event.target.value }) } };
};
const filterEffect = (harness, name) => { harness.render(); const fn = harness.effects.find(effect => effect.toString().includes(name)); assert.ok(fn, name); return fn; };
const memorySessionStorage = () => {
  const original = Object.getOwnPropertyDescriptor(globalThis, "sessionStorage"), values = new Map();
  Object.defineProperty(globalThis, "sessionStorage", { configurable: true, value: { getItem(key) { return values.get(key) ?? null; }, setItem(key, value) { values.set(key, value); } } });
  return { values, restore() { if (original) Object.defineProperty(globalThis, "sessionStorage", original); else delete globalThis.sessionStorage; } };
};
const viewHistory = () => {
  const change = (_state, _title, url) => { const parsed = new URL(String(url), globalThis.location.href); globalThis.location = { href: parsed.href, search: parsed.search }; };
  globalThis.history = { pushState: change, replaceState: change };
};

test("M5 parent restores URL filters, preserves them across tasks and refresh, and exposes only one shared filter bar", () => {
  const restore = fakeBrowser(), session = memorySessionStorage();
  try {
    viewHistory(); session.values.set("ny-filters-2028", JSON.stringify({ ...filters, campus: "旧存储校区" }));
    globalThis.location = { href: "http://127.0.0.1:5188/budget/next-year?year=2028&nyFilters=1&bf_campus=%E8%A5%BF%E7%9B%B4%E9%97%A8", search: "?year=2028&nyFilters=1&bf_campus=%E8%A5%BF%E7%9B%B4%E9%97%A8" };
    const harness = pageHarness({ activeTask: 3, restoreFilters: true });
    assert.equal(globalFilter(harness, "校区").props.value, "西直门"); assert.equal(globalFilter(harness, "季度").props.value, "");
    globalFilter(harness, "季度").props.onChange({ target: { value: "秋季" } });
    filterEffect(harness, "persistNextYearFilters")();
    assert.equal(new URLSearchParams(globalThis.location.search).get("bf_campus"), "西直门"); assert.equal(new URLSearchParams(globalThis.location.search).get("bf_season"), "秋季");
    nodes(harness.render()).find(node => typeof node.type === "function" && node.props?.task === 3 && node.props?.onChange).props.onChange(4);
    assert.equal(globalFilter(harness, "校区").props.value, "西直门"); assert.equal(globalFilter(harness, "季度").props.value, "秋季");
    nodes(harness.render()).find(node => typeof node.type === "function" && node.props?.task === 4 && node.props?.onChange).props.onChange(5);
    const html = harness.html(); assert.equal((html.match(/aria-label="当前SKU"/g) ?? []).length, 1); assert.doesNotMatch(html, /aria-label="共同筛选"/);
    const analysis = nodes(harness.render()).find(node => typeof node.type === "function" && node.type.name === "BudgetAnalysisView");
    assert.deepEqual(analysis.props.controlledFilters, { ...filters, campus: "西直门", season: "秋季" }); assert.equal(analysis.props.hideFilters, true);
    const refreshed = pageHarness({ restoreFilters: true }); assert.equal(globalFilter(refreshed, "校区").props.value, "西直门"); assert.equal(globalFilter(refreshed, "季度").props.value, "秋季");
    assert.equal(harness.state[1].draft.sequence, 1); assert.deepEqual(harness.state[3], {});
  } finally { session.restore(); restore(); }
});

test("M5 clearing a filter persists an explicit empty URL view instead of restoring old storage on refresh", () => {
  const restore = fakeBrowser(), session = memorySessionStorage();
  try {
    viewHistory(); const harness = pageHarness({ activeTask: 3 }); globalFilter(harness, "校区").props.onChange({ target: { value: "西直门" } });
    filterEffect(harness, "persistNextYearFilters")(); const scope = scopeControl(harness); scope.props.onChange({ ...filters }); filterEffect(harness, "persistNextYearFilters")();
    const params = new URLSearchParams(globalThis.location.search); assert.equal(params.get("nyFilters"), "1"); assert.equal(params.has("bf_campus"), false);
    session.values.set("ny-filters-2028", JSON.stringify({ ...filters, campus: "西直门" }));
    const refreshed = pageHarness({ restoreFilters: true }); assert.equal(globalFilter(refreshed, "校区").props.value, ""); assert.deepEqual(harness.state[3], {});
  } finally { session.restore(); restore(); }
});

test("M5 year switch restores that year's filter state without carrying the previous year's campus", async () => {
  const restore = fakeBrowser(), session = memorySessionStorage(), original = globalThis.fetch; const requests = [], listeners = {};
  try {
    globalThis.window.addEventListener = (name, fn) => { listeners[name] = fn; }; globalThis.window.removeEventListener = () => {};
    viewHistory(); session.values.set("ny-filters-2028", JSON.stringify({ ...filters, campus: "西直门" })); session.values.set("ny-filters-2029", JSON.stringify({ ...filters, campus: "中关村", season: "寒假" }));
    globalThis.location = { href: "http://127.0.0.1:5188/budget/next-year?year=2028", search: "?year=2028" };
    const next = envelope({ draft: draft({ id: "fy29-N1", inputs: inputs({ fiscal_year: 2029, combinations: [combination({ campus: "中关村" })] }), calculation: calculation({ fiscal_year: 2029 }) }) });
    const catalog = { source_fiscal_year: 2027, sources: [], default_fiscal_year: 2028, fiscal_years: [{ fiscal_year: 2028 }, { fiscal_year: 2029 }] };
    globalThis.fetch = async (url, options) => { requests.push({ url, method: options?.method ?? "GET" }); return Response.json(url.endsWith("session") ? identity : url.endsWith("catalog") ? catalog : url.includes("/fy29/") ? next : envelope()); };
    const harness = pageHarness({ restoreFilters: true }); harness.state[14] = catalog;
    assert.equal(globalFilter(harness, "校区").props.value, "西直门"); filterEffect(harness, "persistNextYearFilters")();
    filterEffect(harness, "popstate")(); globalThis.location = { href: "http://127.0.0.1:5188/budget/next-year?year=2029", search: "?year=2029" }; await listeners.popstate();
    assert.equal(harness.state[15], 2029); assert.equal(globalFilter(harness, "校区").props.value, "中关村"); assert.equal(globalFilter(harness, "季度").props.value, "寒假");
    filterEffect(harness, "persistNextYearFilters")(); assert.equal(JSON.parse(session.values.get("ny-filters-2028")).campus, "西直门"); assert.equal(JSON.parse(session.values.get("ny-filters-2029")).campus, "中关村");
    filterEffect(harness, "popstate")(); globalThis.location = { href: "http://127.0.0.1:5188/budget/next-year?year=2028", search: "?year=2028" }; await listeners.popstate();
    assert.equal(harness.state[15], 2028); assert.equal(globalFilter(harness, "校区").props.value, "西直门"); assert.equal(globalFilter(harness, "季度").props.value, "");
    assert.ok(requests.every(request => request.method === "GET")); assert.deepEqual(harness.state[3], {}); assert.equal(harness.state[1].draft.sequence, 1);
  } finally { globalThis.fetch = original; session.restore(); restore(); }
});

test("M5 parent clears only filters absent from the selected fiscal year without rewriting budget inputs", () => {
  const restore = fakeBrowser();
  try {
    const harness = pageHarness({ activeTask: 3 }); harness.state[10] = { ...filters, campus: "已不存在的校区", grade: "七年级", season: "秋季" };
    const before = JSON.stringify([harness.state[1], harness.state[2]]); filterEffect(harness, "validateNextYearFilters")();
    assert.deepEqual(harness.state[10], { ...filters, campus: "", grade: "七年级", season: "秋季" }); assert.match(harness.state[6], /已清除当前财年不存在的筛选/);
    assert.equal(JSON.stringify([harness.state[1], harness.state[2]]), before); assert.deepEqual(harness.state[3], {});
  } finally { restore(); }
});

test("M5 a delayed cross-year GET cannot replace the current budget after a same-year back navigation", async () => {
  const restore = fakeBrowser(), session = memorySessionStorage(), original = globalThis.fetch;
  const listeners = new Map(), requests = []; let finishNextYear;
  try {
    viewHistory();
    globalThis.window.addEventListener = (name, listener) => listeners.set(name, listener);
    globalThis.window.removeEventListener = (name, listener) => { if (listeners.get(name) === listener) listeners.delete(name); };
    globalThis.location = { href: "http://127.0.0.1:5188/budget/next-year?year=2028&bf_campus=西直门", search: "?year=2028&bf_campus=西直门" };
    const next = envelope({ draft: draft({ id: "fy29-delayed", inputs: inputs({ fiscal_year: 2029, combinations: [combination({ campus: "中关村" })] }), calculation: calculation({ fiscal_year: 2029 }) }) });
    globalThis.fetch = async (url, options) => {
      requests.push({ url, method: options?.method ?? "GET" });
      if (url.endsWith("session")) return Response.json(identity);
      if (url.endsWith("catalog")) return Response.json({ sources: [], fiscal_years: [] });
      assert.match(url, /fy29\/current$/);
      return new Promise(resolve => { finishNextYear = () => resolve(Response.json(next)); });
    };
    const harness = pageHarness({ restoreFilters: true }); harness.state[15] = 2028;
    filterEffect(harness, "popstate")();
    globalThis.location = { href: "http://127.0.0.1:5188/budget/next-year?year=2029&nyTask=3&bf_campus=中关村", search: "?year=2029&nyTask=3&bf_campus=中关村" };
    const firstNavigation = listeners.get("popstate")(); await flush();
    assert.equal(typeof finishNextYear, "function");
    globalThis.location = { href: "http://127.0.0.1:5188/budget/next-year?year=2028&nyTask=5&bf_campus=西直门", search: "?year=2028&nyTask=5&bf_campus=西直门" };
    await listeners.get("popstate")();
    finishNextYear(); await firstNavigation;
    assert.equal(harness.state[15], 2028);
    assert.equal(harness.state[1].draft.id, "next-year-N1");
    assert.equal(harness.state[10].campus, "西直门");
    assert.equal(new URLSearchParams(globalThis.location.search).get("year"), "2028");
    assert.equal(harness.state[4], "");
    filterEffect(harness, "persistNextYearFilters")();
    assert.equal(JSON.parse(session.values.get("ny-filters-2028")).campus, "西直门");
    assert.equal(session.values.has("ny-filters-2029"), false);
    assert.ok(requests.every(request => request.method === "GET"));
  } finally { globalThis.fetch = original; session.restore(); restore(); }
});

test("M5 an older failed year navigation cannot roll back a newer successful year and its filters", async () => {
  const restore = fakeBrowser(), session = memorySessionStorage(), original = globalThis.fetch;
  const listeners = new Map(), pending = new Map(), requests = [];
  try {
    viewHistory();
    globalThis.window.addEventListener = (name, listener) => listeners.set(name, listener);
    globalThis.window.removeEventListener = (name, listener) => { if (listeners.get(name) === listener) listeners.delete(name); };
    globalThis.location = { href: "http://127.0.0.1:5188/budget/next-year?year=2028", search: "?year=2028" };
    globalThis.fetch = async (url, options) => {
      requests.push({ url, method: options?.method ?? "GET" });
      if (url.endsWith("session")) return Response.json(identity);
      if (url.endsWith("catalog")) return Response.json({ sources: [], fiscal_years: [] });
      return new Promise((resolve, reject) => { pending.set(url, { resolve, reject }); });
    };
    const harness = pageHarness({ restoreFilters: true }); harness.state[15] = 2028;
    filterEffect(harness, "popstate")();
    globalThis.location = { href: "http://127.0.0.1:5188/budget/next-year?year=2029&bf_campus=中关村", search: "?year=2029&bf_campus=中关村" };
    const olderNavigation = listeners.get("popstate")(); await flush();
    globalThis.location = { href: "http://127.0.0.1:5188/budget/next-year?year=2030&nyTask=3&bf_campus=天通苑", search: "?year=2030&nyTask=3&bf_campus=天通苑" };
    const latestNavigation = listeners.get("popstate")(); await flush();
    const current = envelope({ draft: draft({ id: "fy30-latest", inputs: inputs({ fiscal_year: 2030, combinations: [combination({ campus: "天通苑" })] }), calculation: calculation({ fiscal_year: 2030 }) }) });
    pending.get("/api/v1/next-year-budgets/fy30/current").resolve(Response.json(current)); await latestNavigation;
    pending.get("/api/v1/next-year-budgets/fy29/current").reject(new Error("older request failed")); await olderNavigation;
    assert.equal(harness.state[15], 2030);
    assert.equal(harness.state[1].draft.id, "fy30-latest");
    assert.equal(harness.state[10].campus, "天通苑");
    assert.equal(new URLSearchParams(globalThis.location.search).get("year"), "2030");
    assert.equal(new URLSearchParams(globalThis.location.search).get("nyTask"), "3");
    assert.equal(harness.state[5], "");
    assert.ok(requests.every(request => request.method === "GET"));
  } finally { globalThis.fetch = original; session.restore(); restore(); }
});

test("M5 rejecting a year change with unsaved edits restores the latest current-year URL and local filters", async () => {
  const restore = fakeBrowser(), session = memorySessionStorage(), original = globalThis.fetch;
  const listeners = new Map(); let requests = 0;
  try {
    viewHistory();
    globalThis.window.addEventListener = (name, listener) => listeners.set(name, listener);
    globalThis.window.removeEventListener = (name, listener) => { if (listeners.get(name) === listener) listeners.delete(name); };
    globalThis.location = { href: "http://127.0.0.1:5188/budget/next-year?year=2028&nyTask=4&bf_campus=西直门", search: "?year=2028&nyTask=4&bf_campus=西直门" };
    globalThis.fetch = async () => { requests++; throw new Error("Rejected navigation must not request another budget"); };
    const edited = { [key("entry")]: "125" };
    const harness = pageHarness({ activeTask: 4, edited, restoreFilters: true }); harness.state[15] = 2028;
    globalFilter(harness, "季度").props.onChange({ target: { value: "秋季" } });
    globalFilter(harness, "科目").props.onChange({ target: { value: "数学" } });
    const expected = { ...filters, campus: "西直门", season: "秋季", subject: "数学" };
    filterEffect(harness, "popstate")();
    globalThis.location = { href: "http://127.0.0.1:5188/budget/next-year?year=2029&nyTask=5&bf_campus=中关村&bf_season=春季&baView=pivot", search: "?year=2029&nyTask=5&bf_campus=中关村&bf_season=春季&baView=pivot" };
    await listeners.get("popstate")();
    assert.equal(harness.state[15], 2028);
    assert.equal(harness.state[1].draft.id, "next-year-N1");
    assert.deepEqual(harness.state[10], expected);
    assert.deepEqual(harness.state[3], edited);
    const params = new URLSearchParams(globalThis.location.search);
    assert.equal(params.get("year"), "2028"); assert.equal(params.get("nyTask"), "4");
    assert.equal(params.get("bf_campus"), "西直门"); assert.equal(params.get("bf_season"), "秋季"); assert.equal(params.get("bf_subject"), "数学");
    assert.equal(params.get("nyFilters"), "1"); assert.equal(params.get("baView"), "pivot");
    assert.match(harness.state[5], /先保存或放弃修改，再切换财年/);
    assert.equal(requests, 0);
    filterEffect(harness, "persistNextYearFilters")();
    assert.deepEqual(JSON.parse(session.values.get("ny-filters-2028")), expected);
    assert.equal(session.values.has("ny-filters-2029"), false);
    const refreshed = pageHarness({ restoreFilters: true });
    assert.deepEqual(globalFilter(refreshed, "季度").props.value, "秋季"); assert.deepEqual(globalFilter(refreshed, "校区").props.value, "西直门");
  } finally { globalThis.fetch = original; session.restore(); restore(); }
});

test("M5 a same-year popstate during initial loading does not discard its pending GET or leave loading stuck", async () => {
  const restore = fakeBrowser(), session = memorySessionStorage(), original = globalThis.fetch;
  const listeners = new Map(), requests = []; let finishInitialLoad, cleanupInitialEffect;
  try {
    viewHistory();
    globalThis.window.addEventListener = (name, listener) => listeners.set(name, listener);
    globalThis.window.removeEventListener = (name, listener) => { if (listeners.get(name) === listener) listeners.delete(name); };
    globalThis.location = { href: "http://127.0.0.1:5188/budget/next-year?year=2028&nyTask=5", search: "?year=2028&nyTask=5" };
    globalThis.fetch = async (url, options) => {
      requests.push({ url, method: options?.method ?? "GET" });
      if (url.endsWith("session")) return Response.json(identity);
      if (url.endsWith("catalog")) return Response.json({ sources: [], fiscal_years: [{ fiscal_year: 2028 }] });
      assert.match(url, /fy28\/current$/);
      return new Promise(resolve => { finishInitialLoad = () => resolve(Response.json(envelope())); });
    };
    const harness = pageHarness(); harness.state[0] = null; harness.state[1] = null; harness.state[2] = null; harness.state[4] = "load"; harness.state[15] = 2028;
    cleanupInitialEffect = filterEffect(harness, "AbortController")(); await flush();
    assert.equal(typeof finishInitialLoad, "function"); assert.equal(harness.state[1], null); assert.equal(harness.state[4], "load");
    filterEffect(harness, "popstate")();
    globalThis.location = { href: "http://127.0.0.1:5188/budget/next-year?year=2028&nyTask=2&bf_season=寒假", search: "?year=2028&nyTask=2&bf_season=寒假" };
    await listeners.get("popstate")();
    assert.equal(harness.state[9], 2); assert.equal(harness.state[10].season, "寒假"); assert.equal(harness.state[4], "load");
    finishInitialLoad(); await flush();
    assert.equal(harness.state[1].draft.id, "next-year-N1"); assert.equal(harness.state[2].fiscal_year, 2028); assert.equal(harness.state[0].userId, identity.userId);
    assert.equal(harness.state[4], ""); assert.equal(harness.state[5], "");
    assert.equal(harness.state[9], 2); assert.equal(harness.state[10].season, "寒假");
    filterEffect(harness, "persistNextYearFilters")();
    assert.equal(JSON.parse(session.values.get("ny-filters-2028")).season, "寒假");
    assert.equal(new URLSearchParams(globalThis.location.search).get("nyTask"), "2");
    assert.ok(requests.every(request => request.method === "GET"));
    assert.equal(requests.filter(request => request.url.endsWith("/current")).length, 1);
  } finally { cleanupInitialEffect?.(); globalThis.fetch = original; session.restore(); restore(); }
});
