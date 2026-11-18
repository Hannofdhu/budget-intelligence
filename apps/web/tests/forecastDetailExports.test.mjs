// Read-only export projections: visual compound cells never become compound analytical fields.
import { test } from "node:test";
import assert from "node:assert/strict";
import { build } from "esbuild";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { budgetWorkbook } from "../src/budgetExport.ts";
import { defaultForecastStates, groupForecastRows, summarizeForecastRows } from "../src/forecastApi.ts";
import { readCockpitView } from "../src/cockpit.ts";

const require = createRequire(import.meta.url);
async function load(file) {
  const result = await build({ entryPoints: [fileURLToPath(new URL(`../src/${file}`, import.meta.url))], bundle: true, write: false,
    platform: "node", format: "cjs", jsx: "automatic", external: ["react", "react-dom", "exceljs"], loader: { ".css": "empty" }, logLevel: "silent" });
  const module = { exports: {} }; new Function("require", "module", "exports", result.outputFiles[0].text)(require, module, module.exports); return module.exports;
}
const forecast = await load("ForecastPage.tsx"), review = await load("ForecastDataReview.tsx"), campus = await load("ForecastCampusRates.tsx"), cockpit = await load("BudgetCockpitPage.tsx"), analysis = await load("BudgetAnalysisView.tsx");
const dims = { campus: "甲 / 乙、联合校区", track: "空间 / 伴学", product: "云帆 / THINK", grade: "七年级", subject: "数学 / 实践" };
const row = extra => ({ ...dims, season: "暑假", budget_revenue: "10000.123456", forecast_revenue: "-23.987654", budget_total: 10,
  forecast_total: 9, budget_new: 4, budget_old: 6, forecast_new: 3, forecast_old: 6, method: "FROZEN_ACTUAL", ...extra });
const keyColumn = (transfer, key) => transfer.columns.findIndex(column => column.key === key) + 1;
async function sheet(transfer) {
  const workbook = await budgetWorkbook(transfer.columns, transfer.rows), { default: ExcelJS } = await import("exceljs"), reopened = new ExcelJS.Workbook();
  await reopened.xlsx.load(await workbook.xlsx.writeBuffer()); return reopened.worksheets[0];
}

test("forecast detail exports every filtered SKU with atomic source dimensions and numeric metrics", async () => {
  const rows = Array.from({ length: 37 }, (_, index) => row({ campus: `${dims.campus}${index}`, forecast_total: index ? index : 0 }));
  const before = JSON.stringify(rows), transfer = forecast.forecastDetailTransfer(rows);
  assert.equal(transfer.rows.length, 37);
  assert.deepEqual(transfer.columns.slice(1, 6).map(column => column.label), ["校区", "赛道", "产品", "年级", "科目"]);
  for (const [key, value] of Object.entries(dims)) assert.equal(transfer.rows[0][key], key === "campus" ? `${value}0` : value);
  const exported = await sheet(transfer);
  assert.equal(exported.rowCount, 38); assert.equal(exported.getCell(2, keyColumn(transfer, "campus")).value, `${dims.campus}0`);
  assert.equal(exported.getCell(2, keyColumn(transfer, "forecast_revenue")).value, -23.987654);
  assert.equal(exported.getCell(2, keyColumn(transfer, "budget_revenue")).value, 10000.123456);
  assert.equal(exported.getCell(2, keyColumn(transfer, "forecast_total")).value, 0);
  assert.equal(exported.getCell(2, keyColumn(transfer, "forecast_new")).value, 3);
  for (const marker of [{ method: "UNMAPPED_ACTUAL" }, { method: "UNMAPPED_FROZEN_CARRYIN" }, { unmapped: true }]) {
    const unmatched = forecast.forecastDetailTransfer([row(marker)]).rows[0];
    assert.equal(unmatched.budget_revenue, null); assert.equal(unmatched.budget_status, "未对应预算");
  }
  assert.equal(JSON.stringify(rows), before);
});

test("quarter analytical export separates signed gap from its direction and retains all saved quarter precision", async () => {
  const rows = Object.keys(defaultForecastStates).map(season => row({ season }));
  const calculation = { rows, quarters: Object.entries(defaultForecastStates).map(([season, state], index) => ({ season, state,
    budget_revenue: "10000.123456", forecast_revenue: "12000.654321", gap: index ? null : "-2000.530865", budget_total: 10, forecast_total: 9 })) };
  const before = JSON.stringify(calculation), transfer = forecast.forecastQuarterTransfer(calculation), exported = await sheet(transfer);
  assert.equal(transfer.rows.length, 4); assert.equal(transfer.columns.length, 12);
  assert.equal(transfer.rows[0].gap_state, "超额"); assert.equal(transfer.rows[0].gap, -2000.530865 / 10000);
  assert.equal(exported.getCell(2, keyColumn(transfer, "forecast_revenue")).value, 12000.654321 / 10000);
  assert.equal(exported.getCell(2, keyColumn(transfer, "gap")).value, -2000.530865 / 10000);
  assert.equal(exported.getCell(3, keyColumn(transfer, "gap")).value, null); assert.equal(transfer.rows[1].gap_state, "待核对");
  for (const kind of ["budget", "forecast"]) for (const metric of ["total", "new", "old"]) assert.equal(typeof transfer.rows[0][`${kind}_${metric}`], "number");
  assert.equal(JSON.stringify(calculation), before);
});

test("forecast analytical detail keeps unavailable actuals empty beside explicit zero without changing browser defaults", async () => {
  const rows = [row({ method: "ACTUAL_UNAVAILABLE", data_status: "ACTUAL_UNAVAILABLE", forecast_revenue: null, forecast_total: null, forecast_new: null, forecast_old: null }),
    row({ forecast_revenue: "0.000000", forecast_total: 0 }), row({ forecast_revenue: "-0.123456", forecast_total: "3" })];
  const before = JSON.stringify(rows), transfer = forecast.forecastDetailTransfer(rows), exported = await sheet(transfer);
  assert.equal(transfer.rows[0].forecast_revenue, null); assert.equal(transfer.rows[0].forecast_total, null);
  assert.equal(transfer.rows[0].forecast_revenue_status, "实际未提供"); assert.equal(transfer.rows[0].forecast_total_status, "实际未提供");
  assert.equal(transfer.rows[0].method_label, "实际未提供");
  assert.equal(exported.getCell(2, keyColumn(transfer, "forecast_revenue")).value, null); assert.equal(exported.getCell(2, keyColumn(transfer, "forecast_total")).value, null);
  assert.equal(exported.getCell(3, keyColumn(transfer, "forecast_revenue")).value, 0); assert.equal(exported.getCell(3, keyColumn(transfer, "forecast_total")).value, 0);
  assert.equal(transfer.rows[1].forecast_revenue_status, "已提供"); assert.equal(exported.getCell(4, keyColumn(transfer, "forecast_revenue")).value, -0.123456);
  assert.equal(forecast.forecastRowLabel(rows[0]), "实际结转收入、人次按0计", "browser-only presentation remains unchanged");
  assert.equal(JSON.stringify(rows), before);
});

test("forecast analytical financial-only and unbudgeted metrics remain inapplicable or uncovered rather than contribution zero", async () => {
  const financial = row({ counts_not_applicable: true, budget_total: 0, budget_new: 0, budget_old: 0, forecast_total: 0, forecast_new: 0, forecast_old: 0 });
  const unbudgeted = row({ budget_covered: false, budget_revenue: 0, budget_total: 0, budget_new: 0, budget_old: 0 });
  const transfer = forecast.forecastDetailTransfer([financial, unbudgeted]), exported = await sheet(transfer);
  for (const key of ["budget_total", "budget_new", "budget_old", "forecast_total", "forecast_new", "forecast_old"]) assert.equal(exported.getCell(2, keyColumn(transfer, key)).value, null, key);
  assert.equal(transfer.rows[0].headcount_status, "不适用"); assert.equal(transfer.rows[0].forecast_total_status, "不适用");
  for (const key of ["budget_revenue", "budget_total", "budget_new", "budget_old"]) assert.equal(exported.getCell(3, keyColumn(transfer, key)).value, null, key);
  assert.equal(transfer.rows[1].budget_status, "未单列");
});

test("budget analysis exports 952 source facts with 119 inapplicable renewal markers outside numeric columns", async () => {
  const facts = Array.from({ length: 952 }, (_, index) => ({ ...dims, id: String(index), season: "暑假", old: 0, new: 10, total: 10,
    revenue: "1000.123456", business: 1060, listRevenue: "2000", detailDiscount: .8, renewal: index < 119 ? "-" : "0.6678" }));
  facts[119].renewal = 0; facts[120].renewal = null; facts[121].old = "待重算"; facts[122].revenue = "-12.345678";
  const before = JSON.stringify(facts), transfer = analysis.budgetAnalysisDetailTransfer(facts, "历史预算明细"), exported = await sheet(transfer);
  assert.equal(transfer.rows.length, 952); assert.equal(exported.rowCount, 953);
  assert.equal(transfer.rows.filter(row => row.renewal_status === "不适用").length, 119);
  for (let index = 0; index < 119; index++) assert.equal(exported.getCell(index + 2, keyColumn(transfer, "renewal")).value, null);
  assert.equal(transfer.rows[119].renewal, 0); assert.equal(transfer.rows[119].renewal_status, "已提供");
  assert.equal(transfer.rows[120].renewal, null); assert.equal(transfer.rows[120].renewal_status, "未提供");
  assert.equal(transfer.rows[121].old, null); assert.equal(transfer.rows[121].old_status, "待重算");
  assert.equal(exported.getCell(124, keyColumn(transfer, "revenue")).value, -12.345678);
  assert.equal(exported.getCell(2, keyColumn(transfer, "revenue")).value, 1000.123456);
  for (const column of transfer.columns.filter(column => column.format)) assert.ok(transfer.rows.every(row => row[column.key] === null || typeof row[column.key] === "number" || /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$/.test(row[column.key])), column.key);
  assert.equal(JSON.stringify(facts), before);
});

test("scope export keeps missing budget amounts empty and puts coverage and surplus in independent fields", async () => {
  const rows = [row({ budget_revenue: "0", forecast_revenue: "50.123456", budget_total: 0 }), row({ campus: "独立", budget_covered: false })];
  const transfer = forecast.forecastScopeTransfer(["campus", "subject"], groupForecastRows(rows, ["campus", "subject"]), summarizeForecastRows(rows));
  const known = transfer.rows.find(item => item.campus === dims.campus), independent = transfer.rows.find(item => item.campus === "独立");
  assert.equal(known.budget_revenue, 0); assert.equal(known.gap, -50.123456 / 10000); assert.equal(known.gap_state, "超额");
  assert.equal(independent.budget_revenue, null); assert.equal(independent.budget_total, null); assert.equal(independent.budget_status, "未单列");
  assert.equal(transfer.columns.some(column => column.key === "gap_label"), false);
});

test("missing actual detail exports five source dimensions and two missing metric states without folded technical markup", async () => {
  const issues = Array.from({ length: 41 }, (_, index) => ({ code: "FINALIZED_ACTUAL_MISSING", severity: "BLOCKING", season: "暑假", dims: { ...dims, subject: `${dims.subject}${index}` }, message: "冻结实际待核对" }));
  const before = JSON.stringify(issues), transfer = review.forecastMissingActualTransfer({ campus: dims.campus, issues }), exported = await sheet(transfer);
  assert.equal(transfer.rows.length, 41); assert.equal(exported.rowCount, 42);
  assert.deepEqual(transfer.columns.slice(1, 6).map(column => column.label), ["校区", "赛道", "产品", "年级", "科目"]);
  assert.equal(transfer.rows[0].subject, `${dims.subject}0`); assert.equal(transfer.rows[0].revenue_status, "待核对"); assert.equal(transfer.rows[0].headcount_status, "待核对");
  assert.equal(transfer.rows[0].code, "FINALIZED_ACTUAL_MISSING"); assert.equal(exported.getCell(2, keyColumn(transfer, "campus")).value, dims.campus);
  const walk = node => Array.isArray(node) ? node.flatMap(walk) : node && typeof node === "object" ? [node, ...walk(node.props?.children)] : [];
  const table = walk(review.ForecastDataReview({ issues })).find(node => typeof node.type === "function" && node.type.name === "ActionTable");
  assert.equal(table.props.exportLabel, "导出当前表格");
  assert.equal(JSON.stringify(issues), before);
});

test("campus trial export retains raw typed counts and separates unsaved state from its saved forecast basis", async () => {
  const rows = [{ season: "秋季", campus: dims.campus, budget_new: "30", current_business_new: 0, budgeted_additional: "12.123456", unbudgeted_additional: null, new_withdrawal: 1, old_withdrawal: 2 }];
  const before = JSON.stringify(rows), version = { id: "exact-P28", sequence: 28, as_of: "2026-10-25" };
  const transfer = campus.forecastCampusTrialTransfer(rows, version), exported = await sheet(transfer);
  assert.equal(exported.getCell(2, keyColumn(transfer, "budget_new")).value, 30); assert.equal(exported.getCell(2, keyColumn(transfer, "budgeted_additional")).value, 12.123456);
  assert.equal(exported.getCell(2, keyColumn(transfer, "current_business_new")).value, 0); assert.equal(exported.getCell(2, keyColumn(transfer, "unbudgeted_additional")).value, null);
  assert.equal(transfer.rows[0].result_status, "未保存试算"); assert.equal(transfer.rows[0].basis_sequence, 28); assert.equal(transfer.rows[0].basis_id, "exact-P28");
  assert.equal(JSON.stringify(rows), before);
});

test("cockpit detail separates each source identity, status, coverage and filter dimension without splitting source names", async () => {
  const source = { id: "exact-P28", fiscal_year: 2027, sequence: 28, status: "CONFIRMED", as_of: "2026-10-25", created_at: "2026-10-26T10:28:00Z" };
  const data = { fiscal_year: 2027, next_fiscal_year: 2028, sources: { budget: { ...source, id: "FY27-archive", status: "FINALIZED" }, actual: source, forecast: source,
    next_budget: { ...source, id: "exact-N30", fiscal_year: 2028, sequence: 30, status: "DRAFT", source } } };
  const view = readCockpitView(""); view.unit = "yuan"; view.filters.campus = [dims.campus, "南校区、联合部"];
  const rows = [{ ...dims, season: "暑假", id: "sku", budget: "0", forecast: "12.123456", actual: null, next_budget: "-2.987654", covered: { budget: true, actual: true, forecast: true, next_budget: false } }];
  const before = JSON.stringify([data, view, rows]), transfer = cockpit.cockpitTransfer(data, view, rows, true), item = transfer.rows[0], exported = await sheet(transfer);
  assert.equal(item.campus, dims.campus); assert.equal(item.subject, dims.subject); assert.equal(item.forecast_source_id, "exact-P28"); assert.equal(item.forecast_source_sequence, 28);
  assert.equal(item.forecast_source_status, "已确认"); assert.equal(item.next_budget_source_status, "草稿"); assert.equal(item.next_basis_id, "exact-P28");
  assert.equal(item.next_basis_sequence, 28); assert.equal(item.actual_coverage, "待核对"); assert.equal(item.next_budget_coverage, "未覆盖");
  assert.deepEqual(JSON.parse(item.scope_campus), [dims.campus, "南校区、联合部"]); assert.equal(item.scope_grade, "全部");
  assert.equal(exported.getCell(2, keyColumn(transfer, "forecast")).value, 12.123456); assert.equal(exported.getCell(2, keyColumn(transfer, "budget")).value, 0);
  for (const key of ["forecast_source", "next_source", "next_basis", "source_ids", "scope"]) assert.equal(transfer.columns.some(column => column.key === key), false, key);
  assert.equal(JSON.stringify([data, view, rows]), before);
});
