import { test } from "node:test";
import assert from "node:assert/strict";
import { selectedBudgetRows, initialBudgetRevision, budgetRowsInScope, validBudgetSelection } from "../src/budgetBrowse.ts";

const rows = [
  { campus: "西直门", grade: "七年级", revenue: 120 },
  { campus: "西直门", grade: "八年级", revenue: 0 },
  { campus: "中关村南", grade: "七年级", revenue: -0.01 },
];
test("summary selection preserves context, zero values and source rows", () => {
  const before = JSON.stringify(rows);
  assert.deepEqual(selectedBudgetRows(rows, { dimension: "campus", name: "西直门" }), rows.slice(0, 2));
  assert.equal(JSON.stringify(rows), before);
  assert.equal(rows.reduce((n, row) => n + row.revenue, 0), 119.99);
  assert.equal(selectedBudgetRows(rows, null), rows);
});
test("selection respects already filtered context and has an honest empty state", () => {
  const context = rows.filter(row => row.grade === "八年级");
  assert.deepEqual(selectedBudgetRows(context, { dimension: "campus", name: "中关村南" }), []);
});
test("further filtering keeps a valid campus selection, never stale evidence", () => {
  const selection = { dimension: "campus", name: "西直门" };
  const context = budgetRowsInScope(rows, { grade: "八年级" });
  assert.deepEqual(validBudgetSelection(context, selection), selection);
  assert.equal(validBudgetSelection(context, { dimension: "campus", name: "中关村南" }), null);
  assert.deepEqual(budgetRowsInScope([{ fiscal_season: 20281 }], { fiscalSeason: "20281" }, { fiscalSeason: "fiscal_season" }), [{ fiscal_season: 20281 }]);
});
test("an approved budget is the default read destination; draft-only budgets still open", () => {
  assert.equal(initialBudgetRevision({ draft_revision_id: "draft", published_revision_id: "published" }), "published");
  assert.equal(initialBudgetRevision({ draft_revision_id: "draft", published_revision_id: null }), "draft");
  assert.equal(initialBudgetRevision({ draft_revision_id: null, published_revision_id: null }), null);
});
