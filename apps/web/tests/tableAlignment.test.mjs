// Rendering contracts; real geometry is checked in the browser, not simulated here.
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

const require = createRequire(import.meta.url);
async function component(name) {
  const compiled = await build({ entryPoints: [fileURLToPath(new URL(`../src/${name}.tsx`, import.meta.url))],
    bundle: true, write: false, platform: "node", format: "cjs", jsx: "automatic", external: ["react", "react-dom", "exceljs"],
    loader: { ".css": "empty" }, logLevel: "silent" });
  const module = { exports: {} };
  new Function("require", "module", "exports", compiled.outputFiles[0].text)(require, module, module.exports);
  return module.exports[name];
}
const ActionTable = await component("ActionTable"), RateTableEditor = await component("RateTableEditor");
const BudgetQuarterTable = await component("BudgetQuarterTable");
const css = fs.readFileSync(new URL("../src/budgetDesign.css", import.meta.url), "utf8");

test("the shared alignment contract retains table identity, scroll ownership, keyboard entry and native controls", () => {
  const html = renderToStaticMarkup(createElement(ActionTable, { className: "existing-table", tableLabel: "参数", keyboardNavigation: true,
    scrollClassName: "existing-scroll" }, createElement("tbody", null, createElement("tr", null,
    createElement("td", { className: "budget-cell-number" }, createElement("input", { inputMode: "decimal", value: "-0.125", readOnly: true }))))));
  assert.match(html, /class="existing-scroll"/);
  assert.match(html, /<table class="existing-table" data-budget-table="true" tabindex="0"/);
  assert.match(html, /data-table-keyboard="true"/);
  assert.match(html, /value="-0.125"/);
  assert.match(html, /一键复制/); assert.match(html, /导出当前表格/);
  assert.doesNotMatch(html, /role="grid"|overflow:hidden/);
});

test("numeric editors and wrappers share the column end anchor without touching text forms or hiding validation", () => {
  assert.match(css, /table\[data-budget-table\] :is\(th, td\)\.budget-cell-text \{ text-align: left;/);
  assert.match(css, /table\[data-budget-table\] :is\(th, td\)\.budget-cell-number \{ text-align: right;/);
  assert.match(css, /table\[data-budget-table\] :is\(th, td\)\.budget-cell-center \{ text-align: center;/);
  assert.match(css, /\.budget-cell-number > \.ny-field \{ margin-inline-start: auto; margin-inline-end: 0;/);
  assert.match(css, /\.budget-cell-number :is\(input\[inputmode="decimal"\], input\[inputmode="numeric"\], input\[type="number"\]\) \{ text-align: right; font-variant-numeric: tabular-nums;/);
  assert.match(css, /\.budget-cell-number :is\(\.rate-percent, \.rate-range\) \{ width: max-content; margin-inline-start: auto;/);
  assert.match(css, /table\[data-budget-table\]\.ba-table thead th\.budget-cell-number \{ left: auto; z-index: 4;/,
    "numeric leaves in a row-spanned header must scroll with their data instead of freezing as the first business column");
  const contract = css.slice(css.indexOf("/* The column owns alignment"), css.indexOf(":is(.decision-shell, .budget-shell) :is(button"));
  assert.doesNotMatch(contract, /nth-child|overflow|display: none|!important|\.ny-field input \{/);
});

test("rate editor keeps selection centered and both single and range percentage editors numeric", () => {
  const value = { items: [{ rate_reference_id: "reference-a", current_value_ppm: 584600, lower_bound_ppm: 500000, upper_bound_ppm: 650000 }] };
  const before = JSON.stringify(value);
  const html = renderToStaticMarkup(createElement(RateTableEditor, { value, referenceRows: [{ rate_reference_id: "reference-a", campus: "校区", grade: "七年级", quarter_name: "秋季" }],
    dirty: false, onChange() { assert.fail("render must not edit rates"); }, onAction() { assert.fail("render must not lock rates"); } }));
  assert.match(html, /<th scope="col" class="budget-cell-center"><input type="checkbox"/);
  assert.match(html, /<td class="budget-cell-center"><input type="checkbox"/);
  for (const heading of ["当前值", "允许范围"]) assert.match(html, new RegExp(`<th scope="col" class="budget-cell-number">${heading}</th>`));
  assert.match(html, /<td class="budget-cell-number"><div class="rate-percent">/);
  assert.match(html, /<td class="budget-cell-number"><div class="rate-range">/);
  for (const amount of ["58.46", "50", "65"]) assert.ok(html.includes(`value="${amount}"`));
  assert.equal(JSON.stringify(value), before);
});

test("multi-level quarterly tables declare centered groups and numeric leaves even at the first cell of the second header row", () => {
  const facts = [{ id: "sku-a", campus: "一个很长的校区名称", track: "智慧伴学", product: "普通", grade: "七年级", subject: "数学", season: "暑假",
    old: 0, new: 1, total: 1, revenue: -0.01, business: 0, listRevenue: 0, renewal: null, discount: null }];
  const before = JSON.stringify(facts);
  const html = renderToStaticMarkup(createElement(BudgetQuarterTable, { facts, unit: "wan", fiscalYear: 2028, rowDimensions: ["campus"],
    metrics: [{ key: "total", label: "总人次" }, { key: "revenue", label: "结转收入" }], title: "季度汇总", tableLabel: "季度汇总", tableClassName: "sample-quarter-table", onSelect() { assert.fail("render must not navigate"); } }));
  assert.match(html, /scope="colgroup" class="budget-cell-center" colSpan="2"/i);
  assert.match(html, /<tr><th scope="col" class="budget-cell-number">总人次/);
  assert.equal((html.match(/scope="col" class="budget-cell-number"/g) ?? []).length, 10);
  assert.match(html, /<td class="budget-cell-number"><button/);
  assert.equal(JSON.stringify(facts), before);
});
