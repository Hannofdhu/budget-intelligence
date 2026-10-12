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
  getNextYearCatalog, getNextYearVersion, restoreNextYearVersion, balanceNextYearBudget,
  nextYearEditKey as key, applyNextYearEdits, allocateNextYearTrackTargets, nextYearRowTotal } from "../src/nextYearApi.ts";

const require = createRequire(import.meta.url);
const bundled = await build({ entryPoints: [fileURLToPath(new URL("../src/NextYearBudgetPage.tsx", import.meta.url))],
  bundle: true, write: false, platform: "node", format: "cjs", jsx: "automatic", external: ["react", "react-dom", "exceljs"], loader: { ".css": "empty" }, logLevel: "silent" });
const compile = loader => { const module = { exports: {} }; new Function("require", "module", "exports", bundled.outputFiles[0].text)(loader, module, module.exports); return module.exports; };
const { NextYearOverview, NextYearTaskNavigation, nextYearExportColumns, nextYearExportRows, nextYearClassStatus, nextYearRateUsage,
  effectiveNextYearEntry, parseNextYearParameterPaste, nextYearImpactRows, nextYearAnalysisFacts, nextYearTableData } = compile(require);
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
  assert.match(withoutTarget, /经营目标待填写/); assert.match(text(withoutTarget), /全年预算收入（万元） 36/);
  assert.doesNotMatch(withoutTarget, /全年经营目标|目标差额|目标（万元）|差额（万元）/);
  const supplied = renderOverview(calculation({ annual: totals({ target_revenue_yuan: "600000", gap_yuan: "240000" }), tracks: [{ track: "智慧空间", ...missing }] }));
  assert.match(text(supplied), /全年经营目标 60万元 目标差额 24万元/);
  assert.doesNotMatch(supplied, /目标（万元）|差额（万元）/);
});
test("overview uses computed goals, business-order quarters and Chinese class status without publishing language", () => {
  const html = renderOverview();
  assert.match(text(html), /全年预算收入（万元） 36 总人次 400 老生人次 320 新生人次 80/);
  assert.match(text(html), /全年经营目标 40万元 目标差额 4万元/);
  let previous = -1; for (const season of seasons) { const next = html.indexOf(`<h3>${season}</h3>`); assert.ok(next > previous); previous = next; }
  assert.doesNotMatch(html, />PASS</); assert.match(html, /达标/);
  assert.equal(nextYearClassStatus("BELOW_MINIMUM_CLASS_SIZE"), "未达班均"); assert.equal(nextYearClassStatus("NO_PLANNED_CLASS"), "未计划开班");
  assert.doesNotMatch(html, /已定版|发布成功/);
});
test("filtered overview does not compare a single-quarter result with full-year targets or zero-fill other quarters", () => {
  const html = renderOverview(calculation(), { ...filters, season: "秋季" });
  assert.match(text(html), /当前范围收入（万元） 9 总人次 100/); assert.doesNotMatch(html, /全年经营目标|目标差额|全年预算收入/);
  assert.match(html, /<h3>暑假<\/h3>[\s\S]*?<strong>—<\/strong>/); assert.match(html, /<h3>秋季<\/h3>[\s\S]*?<strong>9<\/strong>/);
  const matrix = html.match(/aria-label="赛道预算对比"[\s\S]*?<tbody>([\s\S]*?)<\/tbody>/)?.[1]; assert.match(text(matrix), /智慧空间 9 100/); assert.doesNotMatch(html, /目标（万元）|差额（万元）/);
});
test("overview distinguishes negative, zero, unknown and empty ranges in visible numeric results", () => {
  for (const [amount, expected] of [["0", "0"], ["-12500", "-1.25"], [null, "—"]]) {
    const html = renderOverview(calculation({ annual: totals({ revenue_yuan: amount }), rows: [row({ revenue_yuan: amount })] }));
    assert.match(text(html), new RegExp(`全年预算收入（万元） ${expected}`));
  }
  const empty = renderOverview(calculation(), { ...filters, campus: "不存在的校区" }); assert.match(empty, /当前范围没有预算结果/); assert.match(text(empty), /当前范围收入（万元） —/);
});
test("five tasks are same-page controls, never links into the legacy FY28 workspace", () => {
  const html = renderToStaticMarkup(React.createElement(NextYearTaskNavigation, { task: 5, onChange() {} }));
  assert.equal((html.match(/<button /g) ?? []).length, 5); assert.equal((html.match(/aria-current="step"/g) ?? []).length, 1); assert.doesNotMatch(html, /href=/);
  for (const name of ["目标与范围", "入口人数", "招生参数", "资源与财务", "预算概览"]) assert.match(html, new RegExp(name));
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
const nodes = tree => Array.isArray(tree) ? tree.flatMap(nodes) : !tree || typeof tree !== "object" ? [] : [tree, ...nodes(tree.props?.children)];
function pageHarness({ stateData = envelope(), edited = {}, changedReason = "", activeTask = 5, activeIdentity = identity, activePreview = null } = {}) {
  const state = [activeIdentity, stateData, structuredClone(stateData.draft?.inputs ?? null), edited, "", "", "", activePreview, changedReason, activeTask, { ...filters }, {}, "", false];
  const refs = [], effects = []; let cursor = 0, refCursor = 0;
  const mockReact = { ...React, useState(initial) { const i = cursor++; return [state[i] === undefined ? typeof initial === "function" ? initial() : initial : state[i], value => { state[i] = typeof value === "function" ? value(state[i]) : value; }]; }, useEffect(fn) { effects.push(fn); }, useMemo(fn) { return fn(); }, useRef(initial) { const i = refCursor++; return refs[i] ??= { current: initial }; } };
  const { NextYearBudgetPage } = compile(name => name === "react" ? mockReact : require(name));
  return { state, effects, render() { cursor = 0; refCursor = 0; effects.length = 0; return NextYearBudgetPage(); }, html() { return renderToStaticMarkup(this.render()); } };
}
const fakeBrowser = () => {
  const saved = { location: globalThis.location, history: globalThis.history, window: globalThis.window, requestAnimationFrame: globalThis.requestAnimationFrame };
  globalThis.location = { search: "", href: "http://127.0.0.1:5188/budget/next-year" };
  globalThis.history = { pushState() {} }; globalThis.window = { location: { assign() {} } }; globalThis.requestAnimationFrame = fn => fn();
  return () => { for (const [key, value] of Object.entries(saved)) { if (value === undefined) delete globalThis[key]; else globalThis[key] = value; } };
};
const button = (tree, label) => nodes(tree).find(n => n.type === "button" && text(renderToStaticMarkup(n)) === label);
test("initial page never creates a draft; no confirmed source has one clear forecast link", () => {
  const restore = fakeBrowser();
  try { const harness = pageHarness({ stateData: envelope({ source: null, draft: null }) }); const html = harness.html(); assert.match(html, /先确认 FY27 预测/); assert.match(html, /前往达成预测/); assert.doesNotMatch(html, /生成 FY28 草稿/); }
  finally { restore(); }
});
test("saved draft and unsaved preview are distinct; changing inputs shows the original results as pending recalculation", () => {
  const restore = fakeBrowser();
  try {
    const base = pageHarness().html(); assert.match(base, /FY27 预测 P16/); assert.match(base, /已保存 N1/);
    const stale = pageHarness({ edited: { [key("entry")]: "0" } }).html(); assert.match(stale, /待重算 · 原结果 N1/); assert.match(text(stale), /全年预算收入（万元） 36/);
    const preview = pageHarness({ edited: { [key("entry")]: "0" }, activePreview: calculation({ annual: totals({ revenue_yuan: "200000" }) }) }).html(); assert.match(preview, /未保存预览/); assert.match(text(preview), /全年预算收入（万元） 20/); assert.doesNotMatch(preview, /已定版/);
  } finally { restore(); }
});
test("only applied zero-old plans can be edited; source dimensions stay read-only and viewer cannot save", () => {
  const restore = fakeBrowser();
  try {
    const fixture = envelope({ draft: draft({ calculation: calculation({ rows: [row({ season: "暑假", planned_new_applied: true }), row({ season: "秋季", planned_new_applied: true, old_students: 0 }), row({ season: "春季", planned_new_applied: false })] }) }) });
    const html = pageHarness({ stateData: fixture, activeTask: 3 }).html(); assert.match(html, /计划新生 · 零老生范围/);
    assert.match(html, /秋季 西直门 \/ 智慧空间 \/ 常规班 \/ 七年级 \/ 数学计划新生人次/);
    assert.doesNotMatch(html, /暑假 西直门 \/ 智慧空间 \/ 常规班 \/ 七年级 \/ 数学计划新生人次/);
    assert.doesNotMatch(html, /春季 西直门 \/ 智慧空间 \/ 常规班 \/ 七年级 \/ 数学计划新生人次/);
    const viewer = pageHarness({ activeIdentity: { ...identity, roles: [] }, activeTask: 4 }).html(); assert.doesNotMatch(viewer, /保存并重算/); assert.match(viewer, /disabled=""/);
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
    button(harness.render(), "保存并重算").props.onClick();
    assert.equal(harness.state[9], 3); assert.match(harness.state[5], /招生参数：按0.01/); assert.equal(harness.state[3][key("rates", "rate-0", "renewal_ppm")], "70.131"); assert.equal(calls, 0);
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
    const harness = pageHarness({ edited: { [key("entry")]: "0" } }); const control = button(harness.render(), "预览");
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
    button(harness.render(), "保存并重算").props.onClick(); await new Promise(resolve => setImmediate(resolve));
    assert.equal(harness.state[3][key("entry")], "105"); assert.equal(harness.state[8], "新入口目标"); assert.equal(harness.state[1].draft.sequence, 1); assert.match(harness.state[5], /当前草稿已变化/);
    const next = envelope({ draft: draft({ id: "next-year-N2", sequence: 2, parent_id: "next-year-N1", inputs: inputs({ entry_total: 105 }) }) });
    globalThis.fetch = async () => Response.json(next);
    button(harness.render(), "保存并重算").props.onClick(); await new Promise(resolve => setImmediate(resolve));
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
    button(harness.render(), "预览").props.onClick(); await new Promise(resolve => setImmediate(resolve));
    assert.equal(submitted.inputs.target_revenue_yuan, null); assert.deepEqual(submitted.inputs.track_targets, { 智慧空间: null });
    assert.equal(harness.state[1].draft.inputs.target_revenue_yuan, "400000", "preview cannot rewrite the saved goal");
    assert.equal(harness.state[9], 5); assert.equal(harness.state[7].annual.gap_yuan, null);
  } finally { globalThis.fetch = original; restore(); }
});
test("no goal does not block changing counts, previewing or saving; target fields remain blank", async () => {
  const restore = fakeBrowser(), original = globalThis.fetch, requests = [];
  const noGoal = inputs({ target_revenue_yuan: null, track_targets: { 智慧空间: null } });
  const result = calculation({ annual: totals({ target_revenue_yuan: null, gap_yuan: null, attainment: null }) });
  globalThis.fetch = async (url, options) => { requests.push({ url, body: JSON.parse(options.body) }); return Response.json(url.endsWith("/preview") ? { source, calculation: result } : envelope({ draft: draft({ sequence: 2, inputs: inputs({ ...noGoal, entry_total: 125 }), calculation: result }) })); };
  try {
    const harness = pageHarness({ stateData: envelope({ draft: draft({ inputs: noGoal, calculation: result }) }), edited: { [key("entry")]: "125" }, changedReason: "修改入口人数" });
    button(harness.render(), "预览").props.onClick(); await new Promise(resolve => setImmediate(resolve));
    assert.equal(requests[0].body.inputs.entry_total, 125); assert.equal(requests[0].body.inputs.target_revenue_yuan, null); assert.equal(harness.state[7].annual.revenue_yuan, "360000");
    button(harness.render(), "保存并重算").props.onClick(); await new Promise(resolve => setImmediate(resolve));
    assert.equal(requests[1].body.inputs.target_revenue_yuan, null); assert.equal(harness.state[1].draft.sequence, 2);
    harness.state[9] = 1; const tree = harness.render(), html = harness.html();
    const targetLabel = nodes(tree).find(node => node.type === "label" && text(renderToStaticMarkup(node)).startsWith("全年经营目标（元）"));
    assert.equal(nodes(targetLabel).find(node => node.type === "input").props.value, ""); assert.match(html, /FY27 预测收入（万元）/);
    assert.equal(button(tree, "按预算收入分配赛道目标").props.disabled, true);
  } finally { globalThis.fetch = original; restore(); }
});
test("track allocation uses calculated budget income only after the annual target is manually specified", () => {
  const restore = fakeBrowser();
  try {
    const withoutTargets = inputs({ target_revenue_yuan: null, track_targets: { 智慧空间: null, 智慧伴学: null } });
    const fixture = envelope({ draft: draft({ inputs: withoutTargets, calculation: calculation({ tracks: [{ track: "智慧空间", ...totals({ revenue_yuan: "300000", target_revenue_yuan: null }) }, { track: "智慧伴学", ...totals({ revenue_yuan: "100000", target_revenue_yuan: null }) }] }) }) });
    const harness = pageHarness({ stateData: fixture, activeTask: 1, edited: { [key("target")]: "800000" } });
    button(harness.render(), "按预算收入分配赛道目标").props.onClick();
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
  assert.match(page, /exportData=\{\{ columns:/); assert.match(page, /copyRows=\{\[headers/);
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
    const html = harness.html(); assert.match(html, /FY27 预测 P3/); assert.match(html, /历史参数说明/); assert.match(html, /value="777"/);
    assert.doesNotMatch(html, /保存并重算|按经营目标自动调平/); assert.match(html, /以此版本恢复新草稿/);
  } finally { restore(); }
});

test("infeasible balancing preserves input edits and the saved version, while partial proposals remain unsaved previews", async () => {
  const restore = fakeBrowser(), original = globalThis.fetch;
  try {
    const harness = pageHarness({ edited: { [key("entry")]: "125" } });
    globalThis.fetch = async () => Response.json({ status: "INCOMPLETE", proposed_inputs: null, message: "尚未找到方案" });
    button(harness.render(), "按经营目标自动调平").props.onClick(); await new Promise(resolve => setImmediate(resolve));
    assert.equal(harness.state[3][key("entry")], "125"); assert.equal(harness.state[1].draft.sequence, 1); assert.match(harness.state[5], /尚未找到方案/);
    globalThis.fetch = async () => Response.json({ status: "PARTIAL", proposed_inputs: inputs({ entry_total: 125 }), calculation: calculation({ annual: totals({ revenue_yuan: "380000", gap_yuan: "20000" }) }), message: "改善建议，尚未达到经营目标" });
    button(harness.render(), "按经营目标自动调平").props.onClick(); await new Promise(resolve => setImmediate(resolve));
    assert.equal(harness.state[2].entry_total, 125); assert.equal(harness.state[7].annual.revenue_yuan, "380000"); assert.equal(harness.state[1].draft.sequence, 1);
    assert.match(harness.html(), /未保存预览|修改影响对比/); assert.match(harness.state[22], /尚未达到经营目标/);
  } finally { globalThis.fetch = original; restore(); }
});

test("overview uses one shared filter system and unset class planning is not displayed as zero", () => {
  const restore = fakeBrowser();
  try { const harness = pageHarness(), tree = harness.render(), html = harness.html();
    assert.doesNotMatch(html, /aria-label="FY28预算筛选"/); assert.match(html, /设置计划班额/);
    const analysis = nodes(tree).find(node => typeof node.type === "function" && node.type.name === "BudgetAnalysisView"); assert.ok(analysis); assert.equal(analysis.props.hideSummary, true);
    assert.equal(analysis.props.facts.length, 4); assert.equal(analysis.props.fiscalYear, 2028);
  } finally { restore(); }
});

const paged = (tree, label) => nodes(tree).find(node => typeof node.type === "function" && node.props?.label === label);
const pagedRow = (tree, label, index = 0) => { const table = paged(tree, label); assert.ok(table, label); return table.props.render(table.props.rows[index], index); };
const labeledInput = (tree, label) => { const item = nodes(tree).find(node => node.type === "label" && text(renderToStaticMarkup(node)) === label); assert.ok(item, label); return nodes(item).find(node => node.type === "input"); };
const flush = () => new Promise(resolve => setImmediate(resolve));

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
    for (const [label, value] of [["校区", "新校区"], ["赛道", "智慧空间"], ["产品", "常规班"], ["年级", "八年级"], ["科目", "英语"], ["学费（元）", "1200"], ["产品计划班额（共用）", "16"], ["新增原因", "新增校区试算"]]) labeledInput(harness.render(), label).props.onChange({ target: { value } });
    button(harness.render(), "加入预算范围").props.onClick();
    const added = harness.state[2].combinations.find(item => item.added); assert.ok(added); assert.equal(added.entry_override, false);
    assert.equal(harness.state[2].class_sizes[0].planned_class_size, 16); assert.equal(harness.state[2].class_sizes[0].minimum, 10);
    assert.equal(harness.state[1].draft.inputs.combinations.length, 1); assert.equal(requests.length, 0);
    let table = paged(harness.render(), "预算手动调节明细"), index = table.props.rows.findIndex(item => item.combination_id === added.id && item.season === "暑假"); assert.ok(index >= 0);
    nodes(table.props.render(table.props.rows[index], index)).find(node => node.type === "input" && node.props.type === "text").props.onChange({ target: { value: "30" } });
    harness.state[9] = 3; table = paged(harness.render(), "FY28 计划新生"); assert.equal(table.props.rows.filter(item => item.combination.id === added.id).length, 4);
    button(harness.render(), "预览").props.onClick(); await flush();
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
    harness.state[9] = 3; const html = harness.html(); assert.match(html, /FY27 预测 P3/); assert.match(html, /value="777"/); assert.match(html, /来自历史N0的说明/); assert.doesNotMatch(html, /保存并重算|基准预测版本/);
    assert.equal(harness.state[1].draft.source.sequence, 16);
  } finally { globalThis.fetch = original; restore(); }
});

test("selecting an older confirmed forecast sends that exact source while preserving the current parent", async () => {
  const restore = fakeBrowser(), original = globalThis.fetch; let sent;
  try {
    const old = { ...source, id: "forecast-P3", sequence: 3, fiscal_year: 2027 };
    const harness = pageHarness({ activeTask: 1 }); harness.state[14] = { source_fiscal_year: 2027, default_fiscal_year: 2028, sources: [source, old], fiscal_years: [{ fiscal_year: 2028, has_draft: true }] };
    nodes(harness.render()).find(node => node.type === "select" && node.props["aria-label"] === "基准预测版本").props.onChange({ target: { value: old.id } });
    globalThis.fetch = async (url, options) => { sent = { url, body: JSON.parse(options.body) }; return Response.json(envelope({ draft: draft({ id: "new-N2", sequence: 2, parent_id: "next-year-N1", source: old }) })); };
    button(harness.render(), "以此预测生成新草稿").props.onClick(); await flush();
    assert.match(sent.url, /prepare$/); assert.equal(sent.body.forecast_id, old.id); assert.equal(sent.body.parent_id, "next-year-N1"); assert.equal(harness.state[1].draft.sequence, 2); assert.equal(harness.state[1].draft.source.sequence, 3);
  } finally { globalThis.fetch = original; restore(); }
});

test("year switching commits the URL only on successful loading and popstate reloads the selected ledger", async () => {
  const restore = fakeBrowser(), original = globalThis.fetch; const urls = [], listeners = {};
  try {
    globalThis.history = { pushState(_a, _b, url) { urls.push(String(url)); globalThis.location = { href: String(url), search: url.search }; }, replaceState(_a, _b, url) { globalThis.location = { href: String(url), search: url.search }; } };
    globalThis.window.addEventListener = (name, fn) => { listeners[name] = fn; }; globalThis.window.removeEventListener = () => {};
    const harness = pageHarness(); harness.state[14] = { source_fiscal_year: 2027, sources: [], default_fiscal_year: 2028, fiscal_years: [{ fiscal_year: 2028 }, { fiscal_year: 2029 }] };
    globalThis.fetch = async url => Response.json(url.endsWith("session") ? identity : { detail: "读取下一年度失败" }, { status: url.endsWith("session") ? 200 : 503 });
    nodes(harness.render()).find(node => node.type === "select" && node.props["aria-label"] === "预算财年").props.onChange({ target: { value: "2029" } }); await flush();
    assert.equal(urls.length, 0); assert.equal(harness.state[1].draft.inputs.fiscal_year, 2028); assert.match(harness.state[5], /读取下一年度失败/);
    const next = envelope({ draft: draft({ inputs: inputs({ fiscal_year: 2029 }), calculation: calculation({ fiscal_year: 2029 }) }) });
    globalThis.fetch = async url => Response.json(url.endsWith("session") ? identity : url.endsWith("catalog") ? { source_fiscal_year: 2027, sources: [], fiscal_years: [{ fiscal_year: 2028 }, { fiscal_year: 2029 }] } : next);
    nodes(harness.render()).find(node => node.type === "select" && node.props["aria-label"] === "预算财年").props.onChange({ target: { value: "2029" } }); await flush();
    assert.equal(harness.state[15], 2029); assert.match(urls[0], /year=2029/);
    harness.render(); harness.effects[1](); globalThis.location = { href: "http://127.0.0.1:5188/budget/next-year?year=2028&nyTask=2", search: "?year=2028&nyTask=2" };
    globalThis.fetch = async url => Response.json(url.endsWith("session") ? identity : url.endsWith("catalog") ? { sources: [], fiscal_years: [{ fiscal_year: 2028 }] } : envelope());
    await listeners.popstate(); assert.equal(harness.state[15], 2028); assert.equal(harness.state[9], 2); assert.equal(harness.state[1].draft.inputs.fiscal_year, 2028);
    harness.state[3] = { [key("entry")]: "125" }; harness.render(); harness.effects[1](); globalThis.location = { href: "http://127.0.0.1:5188/budget/next-year?year=2029", search: "?year=2029" };
    await listeners.popstate(); assert.match(globalThis.location.search, /year=2028/); assert.equal(harness.state[3][key("entry")], "125"); assert.match(harness.state[5], /先保存或放弃修改/);
  } finally { globalThis.fetch = original; restore(); }
});

test("default adjustment bounds are the real five-point range, clearable, and use Chinese field labels", () => {
  const restore = fakeBrowser();
  try {
    const harness = pageHarness({ activeTask: 3 }), cells = pagedRow(harness.render(), "自动调平参数范围", 1), fields = nodes(cells).filter(node => node.type === "input");
    assert.deepEqual(fields.map(item => item.props.placeholder), ["默认 75", "默认 85", "默认 20", "默认 30"]); assert.deepEqual(fields.map(item => item.props.value), ["", "", "", ""]);
    assert.match(renderToStaticMarkup(cells), /续班率下限（%）/); assert.doesNotMatch(renderToStaticMarkup(cells), /renewal_lower_ppm/);
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
    assert.match(harness.html(), /先处理成班问题/); assert.ok(button(harness.render(), "查看成班明细")); assert.ok(button(harness.render(), "调整业务范围"));
    button(harness.render(), "查看成班明细").props.onClick(); assert.equal(harness.state[9], 4);
    button(harness.render(), "放弃修改").props.onClick(); assert.deepEqual(harness.state[25], []); assert.doesNotMatch(harness.html(), /先处理成班问题/);
    const changed = nextYearImpactRows(calculation({ rows: [row({ class_count: null })] }), calculation({ rows: [row({ total_students: 110, class_count: null })] })); assert.equal(changed[0].class_delta, null);
    const rounded = allocateNextYearTrackTargets("100.01", { A: "1", B: "1", C: "1" }, 2); assert.deepEqual(rounded, { A: "33.34", B: "33.34", C: "33.33" });
  } finally { globalThis.fetch = original; restore(); }
});
