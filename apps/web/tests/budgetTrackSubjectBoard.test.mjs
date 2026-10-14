// Read-only board acceptance: grouped results, drilldown callbacks and real
// numeric XLSX transfers. Browser geometry is verified separately.
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
  entryPoints: [fileURLToPath(new URL("../src/BudgetTrackSubjectBoard.tsx", import.meta.url))],
  bundle: true, write: false, platform: "node", format: "cjs", jsx: "automatic",
  external: ["react", "react-dom"], logLevel: "silent",
});
function load(moduleRequire = require) {
  const module = { exports: {} };
  new Function("require", "module", "exports", compiled.outputFiles[0].text)(moduleRequire, module, module.exports);
  return module.exports.BudgetTrackSubjectBoard;
}
const Board = load();
const CallbackBoard = load(name => name === "react" ? { Fragment, useMemo: compute => compute() } : require(name));
const fact = (overrides = {}) => ({
  id: "a", season: "暑假", track: "智慧空间", campus: "西直门", product: "常规班", grade: "七年级", subject: "数学",
  revenue: 10000, business: 9000, listRevenue: 10000, old: 50, new: 10, total: 60,
  renewalNumerator: 50, renewalDenominator: 100, ...overrides,
});
const props = (facts, overrides = {}) => ({ facts, fiscalYear: 2027, unit: "wan", onSelect() {}, ...overrides });
const render = (facts, overrides) => renderToStaticMarkup(createElement(Board, props(facts, overrides)));
function findElements(node, predicate) {
  if (Array.isArray(node)) return node.flatMap(child => findElements(child, predicate));
  if (!node || typeof node !== "object") return [];
  // Expand the extracted presentation component, retaining ActionTable so
  // explicit raw export data is tested without running its browser hooks.
  const rendered = typeof node.type === "function" && !node.props?.exportData ? node.type(node.props) : node.props?.children;
  return [...(predicate(node) ? [node] : []), ...findElements(rendered, predicate)];
}
const tableHtml = html => html.match(/<table\b[^>]*class="ba-table ba-track-subject-table"[^>]*>([\s\S]*?)<\/table>/)?.[1] ?? "";
function transferData(facts, overrides) {
  const tree = CallbackBoard(props(facts, overrides));
  const tables = findElements(tree, node => node.props?.exportData && node.props?.tableLabel === "赛道科目季度招生预算表");
  assert.equal(tables.length, 1, "the board must use the shared copy/export table");
  assert.ok(tables[0].props.exportData, "explicit raw numeric export data must be provided");
  return tables[0].props.exportData;
}
const rowValues = (data, row) => data.columns.map(column => row[column.key]);

test("track-subject enrollment board has four canonical quarters and a total, each with people and revenue", () => {
  const html = render([fact()]);
  const table = tableHtml(html);
  assert.ok(table);
  assert.match(html, /<h3>赛道科目招生预算<\/h3>/);
  assert.match(table, /<thead>[\s\S]*rowSpan="2"|<thead>[\s\S]*rowspan="2"/);
  for (const quarter of ["暑假", "秋季", "寒假", "春季", "合计"]) {
    assert.match(table, new RegExp(`<th[^>]*colSpan="2"[^>]*>${quarter}<|<th[^>]*colspan="2"[^>]*>${quarter}<`));
  }
  assert.equal((table.match(/>招生人次<\/th>/g) ?? []).length, 5);
  assert.equal((table.match(/>结转收入（万元）<\/th>/g) ?? []).length, 5);
  assert.match(html, /一键复制/);
  assert.match(html, /导出明细/);
  assert.doesNotMatch(html, /NaN|Infinity|undefined/);
});

test("leaf track-subject rows sum campuses, products and grades once without intermediate track subtotal duplication", () => {
  const facts = [
    fact(),
    fact({ id: "b", campus: "中关村", grade: "八年级", product: "白金", total: "15", revenue: "2500.25" }),
    fact({ id: "c", subject: "英语", total: 20, revenue: 4000 }),
    fact({ id: "d", track: "智慧伴学", total: 30, revenue: 8000 }),
    fact({ id: "e", season: "秋季", total: 40, revenue: 6000 }),
  ];
  const html = tableHtml(render(facts));
  assert.equal((html.match(/<tbody>[\s\S]*?<\/tbody>/)?.[0].match(/<tr\b/g) ?? []).length, 3);
  assert.equal((html.match(/<tfoot>[\s\S]*?<\/tfoot>/)?.[0].match(/<tr\b/g) ?? []).length, 1);
  assert.match(html, /aria-label="智慧空间，数学，暑假，招生人次，75"/);
  assert.match(html, /aria-label="智慧空间，数学，合计，招生人次，115"/);
  assert.doesNotMatch(html, /西直门|中关村|八年级|白金/);
  const data = transferData(facts);
  assert.equal(data.rows.length, 4, "three complete leaf groups plus one overall total");
  assert.deepEqual(rowValues(data, data.rows.at(-1)), ["合计", "", 125, 2.450025, 40, .6, null, null, null, null, 165, 3.050025]);
});

test("the board honors already filtered facts rather than reconstructing excluded budget rows", () => {
  const all = [
    fact(), fact({ id: "b", campus: "中关村", revenue: 99999, total: 999 }),
    fact({ id: "c", subject: "英语", revenue: 99999, total: 999 }),
    fact({ id: "d", season: "秋季", revenue: 99999, total: 999 }),
  ];
  const filtered = filterBudgetFacts(all, { campus: "西直门", subject: "数学", season: "暑假" });
  const before = JSON.stringify(filtered);
  const data = transferData(filtered);
  assert.equal(data.rows.length, 2);
  assert.deepEqual(rowValues(data, data.rows[0]), ["智慧空间", "数学", 60, 1, null, null, null, null, null, null, 60, 1]);
  assert.equal(JSON.stringify(filtered), before, "rendering and export must not mutate saved facts");
});

test("missing, actual zero and negative values retain distinct display and aggregate semantics", () => {
  const facts = [
    fact({ total: 0, revenue: 0 }),
    fact({ id: "negative", season: "秋季", total: -2, revenue: -12500 }),
    fact({ id: "missing", season: "寒假", total: null, revenue: null }),
  ];
  const html = render(facts);
  for (const label of [
    "智慧空间，数学，暑假，招生人次，0",
    "智慧空间，数学，暑假，结转收入，0",
    "智慧空间，数学，秋季，招生人次，-2",
    "智慧空间，数学，秋季，结转收入，-1.25",
    "智慧空间，数学，寒假，招生人次，—",
    "智慧空间，数学，寒假，结转收入，—",
    "智慧空间，数学，春季，招生人次，—",
    "智慧空间，数学，合计，招生人次，—",
  ]) assert.ok(html.includes(`aria-label="${label}"`), label);
  const data = transferData(facts);
  assert.deepEqual(rowValues(data, data.rows[0]), ["智慧空间", "数学", 0, 0, -2, -1.25, null, null, null, null, null, null]);
  assert.doesNotMatch(html, /NaN|Infinity|undefined/);
});

test("a partially missing metric stays missing without blanking an independent complete metric", () => {
  const data = transferData([fact(), fact({ id: "missing-count", total: null, revenue: 5000 })]);
  assert.deepEqual(rowValues(data, data.rows[0]), ["智慧空间", "数学", null, 1.5, null, null, null, null, null, null, null, 1.5]);
});

test("yuan and wan change only revenue units and preserve unrounded raw precision in numeric exports", () => {
  const facts = [fact({ total: 1.234567891, revenue: 12345.6789012345 })];
  for (const unit of ["yuan", "wan"]) {
    const data = transferData(facts, { unit });
    const expectedRevenue = facts[0].revenue / (unit === "wan" ? 10000 : 1);
    const values = rowValues(data, data.rows[0]);
    assert.equal(values[2], facts[0].total);
    assert.equal(values[3], expectedRevenue);
    assert.equal(values[10], facts[0].total);
    assert.equal(values[11], expectedRevenue);
    assert.match(render(facts, { unit }), new RegExp(`结转收入（${unit === "wan" ? "万元" : "元"}）`));
    assert.equal(data.columns.length, 12);
    assert.ok(data.columns.slice(2).every((column, index) => column.format === (index % 2 ? "money" : "count")));
    assert.ok(data.columns.slice(2).every(column => typeof data.rows[0][column.key] === "number" || data.rows[0][column.key] === null));
  }
});

test("XLSX roundtrip stores counts and revenues as numbers, preserves null and carries full precision", async () => {
  const data = transferData([
    fact({ total: 1.234567891, revenue: 12345.6789012345 }),
    fact({ id: "zero", subject: "英语", total: 0, revenue: 0 }),
    fact({ id: "negative", season: "秋季", total: -2, revenue: -12500 }),
  ]);
  const workbook = await budgetWorkbook(data.columns, data.rows);
  const { default: ExcelJS } = await import("exceljs");
  const reopened = new ExcelJS.Workbook();
  await reopened.xlsx.load(await workbook.xlsx.writeBuffer());
  const sheet = reopened.worksheets[0];
  data.rows.forEach((row, rowIndex) => data.columns.forEach((column, columnIndex) => {
    const expected = row[column.key] ?? null;
    const cell = sheet.getCell(rowIndex + 2, columnIndex + 1);
    assert.equal(cell.value, expected === "" ? null : expected);
    if (column.format && expected !== null) assert.equal(cell.type, ExcelJS.ValueType.Number);
  }));
});

test("clipboard transfer uses matching unit labels and the same full numeric data as export", () => {
  const data = transferData([fact({ total: 0, revenue: -12345.6789012345 })]);
  const copied = tableCopyRows(data.columns, data.rows);
  assert.equal(copied.length, 3);
  assert.equal(copied[1][2], "0");
  assert.equal(copied[1][3], String(-12345.6789012345 / 10000));
  assert.equal(copied[1][4], "");
  assert.ok(copied[0][3].includes("万元"));
});

test("quarter and annual cells drill into the exact track-subject scope without network or writes", () => {
  const calls = [];
  const tree = CallbackBoard(props([fact()], { onSelect: (path, label) => calls.push({ path, label }) }));
  for (const metric of ["招生人次", "结转收入"]) {
    const [quarter] = findElements(tree, node => node.type === "button" && node.props["aria-label"] === `智慧空间，数学，暑假，${metric}，${metric === "招生人次" ? "60" : "1"}`);
    assert.ok(quarter);
    quarter.props.onClick();
    assert.deepEqual(calls.at(-1).path, { track: "智慧空间", subject: "数学", season: "暑假" });
    for (const part of ["智慧空间", "数学", "暑假", metric]) assert.ok(calls.at(-1).label.includes(part));
    const [annual] = findElements(tree, node => node.type === "button" && node.props["aria-label"] === `智慧空间，数学，合计，${metric}，${metric === "招生人次" ? "60" : "1"}`);
    assert.ok(annual);
    annual.props.onClick();
    assert.deepEqual(calls.at(-1).path, { track: "智慧空间", subject: "数学" });
  }
});

test("overall total cells drill into their quarter or the full filtered dataset without a false track scope", () => {
  const calls = [];
  const tree = CallbackBoard(props([fact()], { onSelect: (path, label) => calls.push({ path, label }) }));
  for (const [period, expected] of [["暑假", { season: "暑假" }], ["合计", {}]]) {
    const [button] = findElements(tree, node => node.type === "button" && node.props["aria-label"] === `合计，${period}，招生人次，60`);
    assert.ok(button);
    button.props.onClick();
    assert.deepEqual(calls.at(-1).path, expected);
  }
});

test("missing cells are not actionable but real zeros remain usable detail links", () => {
  const tree = CallbackBoard(props([fact({ total: 0, revenue: 0 })]));
  const buttons = findElements(tree, node => node.type === "button" && node.props["aria-label"]);
  assert.ok(buttons.length);
  assert.ok(buttons.filter(button => button.props["aria-label"].endsWith("，—")).every(button => button.props.disabled === true));
  assert.ok(buttons.filter(button => button.props["aria-label"].endsWith("，0")).every(button => button.props.disabled === false));
});

test("empty input gives an explicit empty state without inventing zero-valued enrollment or an empty export", () => {
  const html = render([]);
  assert.match(html, /当前范围没有招生预算/);
  assert.equal(tableHtml(html), "");
  assert.doesNotMatch(html, /NaN|Infinity|undefined/);
  assert.doesNotMatch(html, /一键复制|导出明细/);
});
