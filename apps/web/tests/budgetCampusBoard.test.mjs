// Read-only rendering, aggregation and transfer acceptance. Browser geometry
// and actual downloads are verified separately, not inferred from SSR.
import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";
import { createElement, Fragment } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { budgetWorkbook } from "../src/budgetExport.ts";
import { filterBudgetFacts } from "../src/budgetAnalysis.ts";
import { tableCopyRows } from "../src/tableTransfer.ts";

const require = createRequire(import.meta.url);
const compiled = await build({
  entryPoints: [fileURLToPath(new URL("../src/BudgetCampusBoard.tsx", import.meta.url))],
  bundle: true, write: false, platform: "node", format: "cjs", jsx: "automatic",
  external: ["react", "react-dom"], logLevel: "silent",
});
function load(moduleRequire = require) {
  const module = { exports: {} };
  new Function("require", "module", "exports", compiled.outputFiles[0].text)(moduleRequire, module, module.exports);
  return module.exports.BudgetCampusBoard;
}
const Board = load();
const CallbackBoard = load(name => name === "react" ? { Fragment, useMemo: compute => compute() } : require(name));
const fact = (overrides = {}) => ({
  id: "a", season: "暑假", track: "智慧空间", campus: "西直门", product: "常规班", grade: "七年级", subject: "数学",
  revenue: 10000, business: 9000, listRevenue: 10000, old: 50, new: 10, total: 60,
  renewalNumerator: 50, renewalDenominator: 100, ...overrides,
});
const props = (facts, overrides = {}) => ({ facts, fiscalYear: 2027, unit: "wan", onSelect() {}, onFilterCampus() {}, onPivot() {}, ...overrides });
const render = (facts, overrides) => renderToStaticMarkup(createElement(Board, props(facts, overrides)));
const tableHtml = html => html.match(/<table\b[^>]*class="ba-table ba-campus-budget-table"[^>]*>([\s\S]*?)<\/table>/)?.[1] ?? "";
function findElements(node, predicate) {
  if (Array.isArray(node)) return node.flatMap(child => findElements(child, predicate));
  if (!node || typeof node !== "object") return [];
  // Expand presentation wrappers while retaining the shared ActionTable node:
  // its explicit export data is part of the acceptance contract.
  const rendered = typeof node.type === "function" && !node.props?.exportData ? node.type(node.props) : node.props?.children;
  return [...(predicate(node) ? [node] : []), ...findElements(rendered, predicate)];
}
function transferData(facts, overrides) {
  const tables = findElements(CallbackBoard(props(facts, overrides)), node => node.props?.exportData && node.props?.tableLabel === "校区季度招生预算表");
  assert.equal(tables.length, 1, "the combined board must retain shared copy/export actions");
  return tables[0].props.exportData;
}
const rowValues = (data, row) => data.columns.map(column => row[column.key]);

test("campus enrollment budget uses campus rows and quarterly total/new/old/revenue columns in the requested order", () => {
  const html = render([fact()]), table = tableHtml(html);
  assert.ok(table);
  assert.match(html, /<h3>校区招生预算<\/h3>/);
  assert.match(table, /<th scope="col" row[Ss]pan="2"[^>]*>校区<\/th>/);
  for (const quarter of ["暑假", "秋季", "寒假", "春季", "合计"]) {
    assert.match(table, new RegExp(`<th[^>]*col[Ss]pan="4"[^>]*>${quarter}<`));
  }
  const secondaryRow = table.match(/<thead><tr>[\s\S]*?<\/tr><tr>([\s\S]*?)<\/tr><\/thead>/)?.[1] ?? "";
  const secondaryHeaders = [...secondaryRow.matchAll(/<th scope="col"(?:\s[^>]*)?>([^<]+)<\/th>/g)].map(match => match[1]);
  assert.deepEqual(secondaryHeaders, Array.from({ length: 5 }, () => ["总人次", "新生人次", "老生人次", "结转收入（万元）"]).flat());
  assert.match(html, /一键复制/);
  assert.match(html, /导出明细/);
  assert.match(html, /透视查看/);
  assert.doesNotMatch(html, /校区招生人次|各校区预算|NaN|Infinity|undefined/);
});

test("each campus gets one row across all tracks, grades, subjects and products with a single overall total", () => {
  const facts = [
    fact(),
    fact({ id: "b", grade: "八年级", product: "白金", total: "15", new: "4", old: "11", revenue: "2500.25" }),
    fact({ id: "c", campus: "中关村", subject: "英语", total: 20, new: 8, old: 12, revenue: 4000 }),
    fact({ id: "d", campus: "中关村", track: "智慧伴学", total: 30, new: 8, old: 22, revenue: 8000 }),
    fact({ id: "e", season: "秋季", total: 40, new: 10, old: 30, revenue: 6000 }),
  ];
  const table = tableHtml(render(facts));
  assert.equal((table.match(/<tbody>[\s\S]*?<\/tbody>/)?.[0].match(/<tr\b/g) ?? []).length, 2);
  assert.equal((table.match(/<tfoot>[\s\S]*?<\/tfoot>/)?.[0].match(/<tr\b/g) ?? []).length, 1);
  for (const label of ["西直门，暑假，总人次，75", "西直门，暑假，新生人次，14", "西直门，暑假，老生人次，61", "西直门，合计，总人次，115"]) assert.ok(table.includes(`aria-label="${label}"`), label);
  assert.doesNotMatch(table, /智慧空间|智慧伴学|八年级|白金|数学|英语/);
  const data = transferData(facts);
  assert.equal(data.rows.length, 3);
  assert.deepEqual(rowValues(data, data.rows.find(row => row.campus === "西直门")), ["西直门", 75, 14, 61, 1.250025, 40, 10, 30, .6, null, null, null, null, null, null, null, null, 115, 24, 91, 1.850025]);
  assert.deepEqual(rowValues(data, data.rows.at(-1)), ["合计", 125, 30, 95, 2.450025, 40, 10, 30, .6, null, null, null, null, null, null, null, null, 165, 40, 125, 3.050025]);
});

test("the combined table uses the current filtered facts and never mutates or reconstructs saved observations", () => {
  const all = [
    fact(), fact({ id: "other-campus", campus: "中关村", revenue: 99999, total: 999 }),
    fact({ id: "other-subject", subject: "英语", revenue: 99999, total: 999 }),
    fact({ id: "other-season", season: "秋季", revenue: 99999, total: 999 }),
    fact({ id: "other-product", product: "白金", revenue: 99999, total: 999 }),
  ];
  const before = JSON.stringify(all), filtered = filterBudgetFacts(all, { campus: "西直门", subject: "数学", season: "暑假", product: "常规班" });
  const data = transferData(filtered), html = render(filtered);
  assert.equal(data.rows.length, 2);
  assert.deepEqual(rowValues(data, data.rows[0]), ["西直门", 60, 10, 50, 1, null, null, null, null, null, null, null, null, null, null, null, null, 60, 10, 50, 1]);
  assert.doesNotMatch(tableHtml(html), /中关村|999/);
  assert.equal(JSON.stringify(all), before);
});

test("null, true zero and negatives remain distinct for each metric, independent of the other columns", () => {
  const facts = [
    fact({ total: 0, new: 0, old: 0, revenue: 0 }),
    fact({ id: "negative", season: "秋季", total: -2, new: -1, old: -1, revenue: -12500 }),
    fact({ id: "missing", season: "寒假", total: null, new: 2, old: null, revenue: null }),
  ];
  const html = render(facts);
  for (const label of ["西直门，暑假，总人次，0", "西直门，暑假，新生人次，0", "西直门，暑假，老生人次，0", "西直门，暑假，结转收入，0", "西直门，秋季，总人次，-2", "西直门，秋季，结转收入，-1.25", "西直门，寒假，总人次，—", "西直门，寒假，新生人次，2", "西直门，春季，总人次，—", "西直门，合计，总人次，—"]) assert.ok(html.includes(`aria-label="${label}"`), label);
  const data = transferData(facts);
  assert.deepEqual(rowValues(data, data.rows[0]), ["西直门", 0, 0, 0, 0, -2, -1, -1, -1.25, null, 2, null, null, null, null, null, null, null, 1, null, null]);
  const partial = transferData([fact(), fact({ id: "partial", total: null, new: 4, old: null, revenue: 5000 })]);
  assert.deepEqual(rowValues(partial, partial.rows[0]), ["西直门", null, 14, null, 1.5, null, null, null, null, null, null, null, null, null, null, null, null, null, 14, null, 1.5]);
  assert.doesNotMatch(html, /NaN|Infinity|undefined/);
});

test("currency changes only revenue while all four metrics retain raw numeric precision for export", () => {
  const facts = [fact({ total: 1.234567891, new: .234567891, old: 1, revenue: 12345.6789012345 })];
  for (const unit of ["yuan", "wan"]) {
    const data = transferData(facts, { unit, fiscalYear: 2028 }), values = rowValues(data, data.rows[0]);
    assert.equal(data.columns.length, 21);
    assert.deepEqual(values.slice(1, 5), [facts[0].total, facts[0].new, facts[0].old, facts[0].revenue / (unit === "wan" ? 10000 : 1)]);
    assert.deepEqual(values.slice(-4), values.slice(1, 5));
    assert.ok(data.columns.slice(1).every((column, index) => column.format === (index % 4 === 3 ? "money" : "count")));
    assert.ok(data.columns.slice(1).every(column => typeof data.rows[0][column.key] === "number" || data.rows[0][column.key] === null));
    assert.match(data.filename, /FY28/);
    assert.match(render(facts, { unit }), new RegExp(`结转收入（${unit === "wan" ? "万元" : "元"}）`));
  }
});

test("XLSX export and clipboard carry all quarters and metric headers, numeric cells, null and unrounded values", async () => {
  const data = transferData([fact({ total: 1.234567891, new: .234567891, old: 1, revenue: 12345.6789012345 }), fact({ id: "zero", campus: "中关村", total: 0, new: 0, old: 0, revenue: 0 })]);
  const workbook = await budgetWorkbook(data.columns, data.rows);
  const { default: ExcelJS } = await import("exceljs");
  const reopened = new ExcelJS.Workbook();
  await reopened.xlsx.load(await workbook.xlsx.writeBuffer());
  const sheet = reopened.worksheets[0];
  data.rows.forEach((row, rowIndex) => data.columns.forEach((column, columnIndex) => {
    const expected = row[column.key] ?? null, cell = sheet.getCell(rowIndex + 2, columnIndex + 1);
    assert.equal(cell.value, expected);
    if (column.format && expected !== null) assert.equal(cell.type, ExcelJS.ValueType.Number);
  }));
  const copied = tableCopyRows(data.columns, data.rows), west = data.rows.findIndex(row => row.campus === "西直门") + 1;
  assert.equal(copied[west][1], String(1.234567891));
  assert.equal(copied[west][4], String(12345.6789012345 / 10000));
  assert.equal(copied[west][5], "");
  for (const [index, metric] of [[1, "总人次"], [2, "新生人次"], [3, "老生人次"], [4, "结转收入"]]) {
    assert.ok(copied[0][index].includes("暑假")); assert.ok(copied[0][index].includes(metric));
  }
  assert.ok(copied[0][4].includes("万元"));
});

test("each quarterly and annual metric opens the exact campus scope, and overall totals avoid an invented campus", () => {
  const calls = [], tree = CallbackBoard(props([fact()], { onSelect: (path, label) => calls.push({ path, label }) }));
  for (const [metric, value] of [["总人次", "60"], ["新生人次", "10"], ["老生人次", "50"], ["结转收入", "1"]]) {
    for (const [campus, period, expected] of [["西直门", "暑假", { campus: "西直门", season: "暑假" }], ["西直门", "合计", { campus: "西直门" }], ["合计", "暑假", { season: "暑假" }], ["合计", "合计", {}]]) {
      const [button] = findElements(tree, node => node.type === "button" && node.props["aria-label"] === `${campus}，${period}，${metric}，${value}`);
      assert.ok(button, `${campus} / ${period} / ${metric}`);
      button.props.onClick();
      assert.deepEqual(calls.at(-1).path, expected);
      assert.ok(calls.at(-1).label.includes(metric));
      assert.ok(calls.at(-1).label.includes(period));
    }
  }
});

test("campus label filters in place and the pivot action retains its dedicated callback", () => {
  const filtered = [], pivots = [];
  const tree = CallbackBoard(props([fact()], { onFilterCampus: campus => filtered.push(campus), onPivot: (...args) => pivots.push(args) }));
  const [campus] = findElements(tree, node => node.type === "button" && node.props.children === "西直门");
  assert.ok(campus); campus.props.onClick(); assert.deepEqual(filtered, ["西直门"]);
  const [pivot] = findElements(tree, node => node.type === "button" && node.props.children === "透视查看");
  assert.ok(pivot); pivot.props.onClick(); assert.deepEqual(pivots, [[]]);
});

test("missing values disable drilldown while recorded zeros stay actionable, and empty scopes do not invent exports", () => {
  const tree = CallbackBoard(props([fact({ total: 0, new: 0, old: 0, revenue: 0 })]));
  const values = findElements(tree, node => node.type === "button" && node.props["aria-label"]);
  assert.ok(values.length);
  assert.ok(values.filter(button => button.props["aria-label"].endsWith("，—")).every(button => button.props.disabled === true));
  assert.ok(values.filter(button => button.props["aria-label"].endsWith("，0")).every(button => button.props.disabled === false));
  const html = render([]);
  assert.match(html, /当前范围没有招生预算/);
  assert.equal(tableHtml(html), "");
  assert.doesNotMatch(html, /一键复制|导出明细|NaN|Infinity|undefined/);
});
