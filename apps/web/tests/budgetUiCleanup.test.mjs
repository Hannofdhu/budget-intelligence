import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { historicalExportColumns } from "../src/historicalBudgetExport.ts";
import { budgetWorkbook } from "../src/budgetExport.ts";

const require = createRequire(import.meta.url);
const compiled = await build({ entryPoints: [fileURLToPath(new URL("../src/BusinessForm.tsx", import.meta.url))],
  bundle: true, write: false, platform: "node", format: "cjs", jsx: "automatic", external: ["react"], logLevel: "silent" });
const module = { exports: {} };
new Function("require", "module", "exports", compiled.outputFiles[0].text)(require, module, module.exports);
const { ReadTable, BusinessForm } = module.exports;
const source = name => fs.readFileSync(new URL(`../src/${name}`, import.meta.url), "utf8");

test("page and KPI explanations are removed without hiding whole text tags", () => {
  const removed = {
    "HistoricalBudgetPage.tsx": /查看年度目标与定版收入|暑假入口新生，按校区查看分配|查看各季度续班和新生人数|查看学费、折扣、班均和结转|按季度和业务维度查看预算|暑假入口范围的新生人次|全年入口中的七年级范围|已确认的例外范围|<small>\{note\}<\/small>/,
    "WorkspacePage.tsx": /copy\?\.intro|intro:|确认本次预算使用的数据范围和快照日期|依次计算人数、班均、收入和目标差额|将使用当前已确认参数生成一套新的计算结果|仅在需要回看来源、修正历史步骤或查看版本时使用/,
    "RebalancingPage.tsx": /先看可调整空间，再决定是否应用方案|并列、等百分点推进|0.1 个百分点步长|stage\.note|不可行证明只能开启当前修订|将生成新的不可变预算修订/,
    "App.tsx": /这版预算已定版，可以直接查看结果|先处理阻断项，再回来确认|全部硬约束均已通过。需要核对技术明细|发布前系统会再次检查全部硬约束并比对确认指纹/,
    "VersionAuditPage.tsx": /选择比较版本，查看本次变化|按稳定业务键对齐；证据文本变化不会冒充业务值变化/,
    "OpeningStudentsPage.tsx": /从上一财年春季招生人次续入暑假|这里展示理论人数|未勾选视为缺失，不会自动填零|保存会生成新修订/,
    "BudgetLifecycle.tsx": /新建草稿，保留当前定版预算/,
    "PublishedBudgetRecord.tsx": /以下为当前读取的检查结果，定版事实以上方记录为准/,
  };
  for (const [name, expression] of Object.entries(removed)) assert.doesNotMatch(source(name), expression, name);
});

test("errors, process states, dimensions, dates and audit reasons remain actual UI data", () => {
  assert.match(source("HistoricalBudgetPage.tsx"), /role="alert"/);
  assert.match(source("HistoricalBudgetPage.tsx"), /\{row\.track\} \/ \{row\.product\}/);
  assert.match(source("WorkspacePage.tsx"), /sectionStatus\(workspace, section, dirty, confirmed\)/);
  assert.match(source("WorkspacePage.tsx"), /inputIssues\.map/);
  assert.match(source("OpeningStudentsPage.tsx"), /invalid && <p role="alert"/);
  assert.match(source("App.tsx"), /publishedWithAuditMismatch && <p role="alert"/);
  assert.match(source("PublishedBudgetRecord.tsx"), /event\.reason && <p>\{event\.reason\}/);
  assert.match(source("VersionAuditPage.tsx"), /业务变化<\/dt><dd>\{businessChangeCount\}/);
});

test("read-only result table omits the two carry components and leaves recorded revenue and counts visible", () => {
  const row = { campus: "西直门", integer_total_students: 0, quarter_recognized_revenue_yuan: "-12.34",
    current_season_revenue_yuan: 987654321, next_season_carryover_revenue_yuan: 876543210 };
  const before = JSON.stringify(row);
  const html = renderToStaticMarkup(createElement(ReadTable, { rows: [row] }));
  assert.doesNotMatch(html, /当季收入|转入下季收入|987654321|876543210/);
  assert.match(html, /季度结转收入（元）/);
  assert.match(html, /-12\.34 元/);
  assert.match(html, /总人次/); assert.match(html, />0</);
  assert.equal(JSON.stringify(row), before);
});

test("resource-finance inputs keep opening carryover and current-season recognition parameters", () => {
  const schema = { type: "object", properties: {
    current_season_recognition_rate_ppm: { type: "integer" },
    opening_carryover_items: { type: "array", items: { type: "object", properties: { amount_yuan: { type: "number" } } } },
  } };
  const value = { current_season_recognition_rate_ppm: 750000, opening_carryover_items: [{ amount_yuan: 0 }] };
  const html = renderToStaticMarkup(createElement(BusinessForm, { schema, value, fixedArrays: true, onChange() {} }));
  assert.match(html, /当季结转比例/); assert.match(html, /value="75"/);
  assert.match(html, /期初结转/); assert.match(html, /期初不含税结转（元）/); assert.match(html, /value="0"/);
});

test("financial XLSX exposes recognized revenue, not current/carryout, and retains numeric zero/negative/blank", async () => {
  const columns = historicalExportColumns(4);
  assert.ok(columns.every(column => !["current", "carryout"].includes(column.key)));
  assert.ok(columns.every(column => !/当季业务确认收入|递延至下季收入/.test(column.label)));
  const rows = [0, -12.34, null].map(revenue => ({ campus: "西直门", revenue, business: 100, current: 987654321, carryout: 876543210 }));
  const before = JSON.stringify(rows);
  const workbook = await budgetWorkbook(columns, rows);
  const { default: ExcelJS } = await import("exceljs");
  const reopened = new ExcelJS.Workbook(); await reopened.xlsx.load(await workbook.xlsx.writeBuffer());
  const sheet = reopened.worksheets[0], index = columns.findIndex(column => column.key === "revenue") + 1;
  assert.equal(sheet.getCell(2, index).value, 0); assert.equal(sheet.getCell(2, index).type, ExcelJS.ValueType.Number);
  assert.equal(sheet.getCell(3, index).value, -12.34); assert.equal(sheet.getCell(4, index).value, null);
  assert.equal(sheet.columnCount, columns.length); assert.equal(JSON.stringify(rows), before);
});
