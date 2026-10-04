import { test } from "node:test";
import assert from "node:assert/strict";
import { budgetFactsForCells, budgetSelectionStatistics } from "../src/budgetAnalysisSelection.ts";

const fact = (id, product, season, revenue, more = {}) => ({ id, product, season, campus: "西直门", track: "初中", grade: "七年级", subject: "数学",
  revenue, business: revenue, listRevenue: 100, old: 3, new: 2, total: 5, ...more });
const cell = (path, season, value) => ({ row: { path }, column: { value: season }, value });
const facts = [fact("a", "A", "暑假", 30), fact("b", "B", "暑假", 70), fact("c", "A", "秋季", 200)];

test("parent subtotal with only part of its children retains the entire parent population once", () => {
  const cells = [cell({ campus: "西直门" }, "暑假", 100), cell({ campus: "西直门", product: "A" }, "暑假", 30)];
  assert.deepEqual(budgetFactsForCells(facts, cells, "season").map(row => row.id), ["a", "b"]);
  assert.deepEqual(budgetSelectionStatistics(facts, cells, "revenue", "season"), {
    count: 2, sum: 100, average: null, min: 30, max: 100, hasOverlap: true,
  });
});

test("total column with only one quarter retains all quarters and never doubles the selected quarter", () => {
  const cells = [cell({ campus: "西直门" }, null, 300), cell({ campus: "西直门" }, "暑假", 100)];
  assert.equal(budgetFactsForCells(facts, cells, "season").length, 3);
  const stats = budgetSelectionStatistics(facts, cells, "revenue", "season");
  assert.equal(stats.sum, 300); assert.equal(stats.average, null); assert.equal(stats.hasOverlap, true);
});

test("disjoint quarter cells have a legitimate numeric mean and one union sum", () => {
  const cells = [cell({ campus: "西直门" }, "暑假", 100), cell({ campus: "西直门" }, "秋季", 200)];
  const stats = budgetSelectionStatistics(facts, cells, "revenue", "season");
  assert.equal(stats.sum, 300); assert.equal(stats.average, 150); assert.equal(stats.hasOverlap, false);
});

test("a missing member stays unknown even if another selected cell has a numeric value", () => {
  const missing = [fact("a", "A", "暑假", 30), fact("b", "B", "暑假", null)];
  const cells = [cell({ campus: "西直门" }, "暑假", null), cell({ campus: "西直门", product: "A" }, "暑假", 30)];
  const stats = budgetSelectionStatistics(missing, cells, "revenue", "season");
  assert.equal(stats.count, 1); assert.equal(stats.sum, null); assert.equal(stats.average, null);
  assert.equal(stats.min, 30); assert.equal(stats.max, 30);
});

test("ratios can show numeric count and range but never a summed or unweighted mean rate", () => {
  const cells = [cell({ campus: "西直门" }, "暑假", 0.5), cell({ campus: "西直门" }, "秋季", 0.8)];
  const stats = budgetSelectionStatistics(facts, cells, "discount", "season");
  assert.equal(stats.sum, null); assert.equal(stats.average, null); assert.equal(stats.min, 0.5); assert.equal(stats.max, 0.8);
  const renewal = budgetSelectionStatistics(facts, cells, "renewal", "season");
  assert.equal(renewal.sum, null); assert.equal(renewal.average, null); assert.equal(renewal.min, 0.5); assert.equal(renewal.max, 0.8);
});

test("real zero, negative values, sparse selections and duplicate facts keep their source meaning", () => {
  const rows = [fact("a", "A", "暑假", 0), fact("a", "A", "暑假", -5)];
  const cells = [cell({ product: "A" }, "暑假", -5), cell({ product: "missing" }, "春季", null)];
  const before = JSON.stringify(rows);
  assert.equal(budgetFactsForCells(rows, cells, "season").length, 2);
  const stats = budgetSelectionStatistics(rows, cells, "revenue", "season");
  assert.equal(stats.sum, -5); assert.equal(stats.count, 1); assert.equal(stats.min, -5);
  assert.equal(budgetSelectionStatistics(rows, [cell({}, null, null)], "revenue", "none"), null);
  assert.equal(JSON.stringify(rows), before);
});
