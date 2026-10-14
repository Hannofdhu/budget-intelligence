import { test } from "node:test";
import assert from "node:assert/strict";
import { renewalStageLabel, budgetPeriodLabel, compareBudgetGrades } from "../src/budgetDisplay.ts";
import { buildBudgetPivot, pivotExport, filterBudgetFacts, emptyBudgetFilters } from "../src/budgetAnalysis.ts";

const seasons = ["暑假", "秋季", "寒假", "春季"];
const stages = ["春续暑", "暑续秋", "秋续寒", "寒续春"];
const facts = seasons.map((season, index) => ({
  id: String(index), season, track: "初中", grade: "七年级", product: "常规班", subject: "数学", campus: "西直门",
  revenue: 100, business: 90, listRevenue: 100, old: 7, new: 3, total: 10,
  renewalNumerator: 70, renewalDenominator: 100,
}));

test("renewal stage labels never rename revenue periods, filters or original fact keys", () => {
  const before = structuredClone(facts);
  seasons.forEach((season, index) => {
    assert.equal(renewalStageLabel(season), stages[index]);
    assert.equal(budgetPeriodLabel(season, "renewal"), stages[index]);
    for (const metric of ["revenue", "business", "old", "new", "total", "discount"]) assert.equal(budgetPeriodLabel(season, metric), season);
  });
  assert.equal(renewalStageLabel("合计"), "合计");
  assert.equal(renewalStageLabel("未知"), "未知");
  assert.equal(filterBudgetFacts(facts, { ...emptyBudgetFilters, season: "暑假" }).length, 1);
  assert.equal(filterBudgetFacts(facts, { ...emptyBudgetFilters, season: "春续暑" }).length, 0);
  assert.deepEqual(facts, before);
});

test("mixed pivot export uses renewal transition labels and original revenue quarter names with numeric values unchanged", () => {
  const config = { rows: ["track", "grade"], column: "season", metrics: ["revenue", "renewal"], sort: "name-asc" };
  const pivot = buildBudgetPivot(facts, config), before = structuredClone(pivot);
  const exported = pivotExport(pivot, config);
  for (let index = 0; index < seasons.length; index++) {
    const revenue = exported.columns.find(column => column.label === `${seasons[index]} · 结转收入（元）`);
    const renewal = exported.columns.find(column => column.label === `${stages[index]} · 预算续班率`);
    assert.ok(revenue); assert.ok(renewal);
    assert.equal(exported.rows[0][revenue.key], 100);
    assert.equal(exported.rows[0][renewal.key], 0.7);
    assert.equal(renewal.format, "rate");
  }
  assert.equal(exported.rows.length, 2, "one leaf plus grand total, never duplicate track subtotal");
  assert.deepEqual(pivot, before); assert.deepEqual(facts.map(fact => fact.season), seasons);
});

test("non-season pivot labels stay unchanged even when a member has a season-like name", () => {
  const config = { rows: ["grade"], column: "campus", metrics: ["renewal"], sort: "name-asc" };
  const pivot = buildBudgetPivot([{ ...facts[0], campus: "暑假" }], config);
  assert.equal(pivotExport(pivot, config).columns[1].label, "暑假 · 预算续班率");
});

test("grade display order is educational order in both boards and nested renewal pivots", () => {
  const grades = ["九年级", "高三", "八年级", "高一年级", "七年级"];
  assert.deepEqual([...grades].sort(compareBudgetGrades), ["七年级", "八年级", "九年级", "高一年级", "高三"]);
  const config = { rows: ["track", "grade"], column: "season", metrics: ["renewal"], sort: "name-asc" };
  const groupedFacts = grades.map((grade, index) => ({ ...facts[0], id: String(index), grade }));
  const before = structuredClone(groupedFacts);
  const pivot = buildBudgetPivot(groupedFacts, config);
  assert.deepEqual(pivot.rows.filter(row => row.leaf).map(row => row.path.grade), ["七年级", "八年级", "九年级", "高一年级", "高三"]);
  assert.equal(pivot.totals.renewal, 0.7); assert.deepEqual(groupedFacts, before);
});
