// Terminology is presentation-only: persisted keys, money units and distinct revenue concepts remain intact.
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { metricLabels, buildBudgetPivot, pivotExport } from "../src/budgetAnalysis.ts";
import { historicalExportColumns } from "../src/historicalBudgetExport.ts";

test("all shipped frontend sources use the single recognized-revenue term", () => {
  const directory = new URL("../src/", import.meta.url);
  for (const file of fs.readdirSync(directory).filter(file => /\.(?:ts|tsx)$/.test(file))) {
    assert.doesNotMatch(fs.readFileSync(new URL(file, directory), "utf8"), /确认收入/, file);
  }
  assert.equal(metricLabels.revenue, "结转收入");
  assert.equal(metricLabels.business, "业务收入（含税）", "tax-inclusive business revenue is a distinct metric");
});

test("historical export headings keep original units, field identities and separate finance concepts", () => {
  assert.deepEqual(historicalExportColumns(1).find(column => column.key === "annual"),
    { key: "annual", label: "全年结转收入（不含税，元）", format: "money" });
  for (const step of [4, 5]) {
    assert.deepEqual(historicalExportColumns(step).find(column => column.key === "revenue"),
      { key: "revenue", label: "预算结转收入（不含税，元）", format: "money" });
  }
  const columns = historicalExportColumns(4);
  assert.equal(columns.find(column => column.key === "business").label, "业务收入（含税，元）");
  assert.equal(columns.find(column => column.key === "recognition").label, "当季确认比例");
});

test("pivot copy/export names change without replacing metric keys, values or precision", () => {
  const facts = [{ id: "one", season: "暑假", track: "智慧空间", campus: "西直门", product: "普通", grade: "七年级", subject: "数学",
    revenue: -12.345678, business: 100, listRevenue: 120, old: 1, new: 2, total: 3 }];
  const before = structuredClone(facts);
  const config = { rows: ["campus"], column: "season", metrics: ["revenue", "business"], sort: "name-asc" };
  const exported = pivotExport(buildBudgetPivot(facts, config), config);
  const revenue = exported.columns.find(column => column.label === "暑假 · 结转收入（元）");
  const business = exported.columns.find(column => column.label === "暑假 · 业务收入（含税）（元）");
  assert.ok(revenue); assert.ok(business);
  assert.ok(revenue.key.endsWith(":revenue"));
  assert.equal(revenue.format, "money");
  assert.equal(exported.rows[0][revenue.key], -12.345678);
  assert.equal(exported.rows[0][business.key], 100);
  assert.equal(exported.rows.at(-1)[revenue.key], -12.345678);
  assert.deepEqual(facts, before);
});
