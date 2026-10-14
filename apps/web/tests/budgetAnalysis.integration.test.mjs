import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import {
  aggregateBudgetFacts, buildBudgetPivot, defaultAnalysisState, filterBudgetFacts,
  normalizeHistoricalFacts, normalizePublishedFacts, pivotExport,
  readAnalysisState, validateAnalysisState, writeAnalysisState,
} from "../src/budgetAnalysis.ts";

const config = { rows: ["campus", "product", "grade"], column: "season", metrics: ["revenue", "old", "new", "total", "discount"], sort: "revenue-desc" };
const close = (left, right) => assert.ok(Math.abs(left - right) < 0.00001, `${left} != ${right}`);
const source = new URL("../../../output/fy27-shadow/source-cells.json", import.meta.url);

function historicalRows() {
  const payload = JSON.parse(fs.readFileSync(source, "utf8"));
  const blocks = ["AL AM AN AR AS AW AX AY", "BC BD BE BI BJ BN BO BP", "BT BU BV BZ CA CE CF CG", "CK CL CM CQ CR CV CW CX"];
  return payload.sheets["滚动表"].rows.filter(row => row.row >= 2 && row.cells.A?.value != null).flatMap(row => {
    const cell = key => row.cells[key]?.value ?? null;
    return blocks.map((block, i) => {
      const [old, newValue, total, listRevenue, business, current, revenue, carryout] = block.split(" ");
      return { season: ["暑假", "秋季", "寒假", "春季"][i], track: cell("A"), product: cell("B"), grade: cell("D"), subject: cell("F"), campus: cell("G"),
        old: cell(old), new: cell(newValue), total: cell(total), list_revenue: cell(listRevenue), business: cell(business), current: cell(current), revenue: cell(revenue), carryout: cell(carryout), annual: cell("W") };
    });
  });
}

test("FY27 read facts keep 952 quarterly amounts, zero/negative values, and never sum repeated annual fields", { skip: !fs.existsSync(source) }, () => {
  const rows = historicalRows(), before = JSON.stringify(rows), facts = normalizeHistoricalFacts(rows);
  assert.equal(facts.length, 952);
  assert.equal(new Set(facts.map(fact => fact.id)).size, 952);
  assert.ok(facts.every(fact => !Object.hasOwn(fact, "annual")));
  assert.ok(facts.some(fact => fact.revenue < 0));
  assert.ok(facts.some(fact => fact.revenue === 0));
  const total = aggregateBudgetFacts(facts);
  assert.equal(total.revenue.toFixed(2), "113004618.59");
  assert.equal(total.total, 36788);
  assert.equal(total.old + total.new, total.total);
  close(total.discount, rows.reduce((sum, row) => sum + row.business, 0) / rows.reduce((sum, row) => sum + row.list_revenue, 0));
  assert.equal(JSON.stringify(rows), before);
});

test("FY27 nested pivot, every cell drill-through, and typed export have matching sums without counting ancestor subtotals", { skip: !fs.existsSync(source) }, () => {
  const facts = normalizeHistoricalFacts(historicalRows()), pivot = buildBudgetPivot(facts, config);
  close(pivot.totals.revenue, aggregateBudgetFacts(facts).revenue);
  for (const row of pivot.rows) {
    const members = filterBudgetFacts(facts, row.path);
    close(row.total.revenue, aggregateBudgetFacts(members).revenue);
    for (const column of pivot.columns) {
      const selected = filterBudgetFacts(members, { season: column.value });
      assert.deepEqual(row.cells[column.key], aggregateBudgetFacts(selected));
    }
  }
  const exported = pivotExport(pivot, config), body = exported.rows.slice(0, -1), footer = exported.rows.at(-1);
  assert.equal(body.length, pivot.rows.filter(row => row.leaf).length);
  close(body.reduce((sum, row) => sum + row["total:revenue"], 0), pivot.totals.revenue);
  assert.equal(footer["total:revenue"], pivot.totals.revenue);
  assert.equal(footer["total:total"], 36788);
  assert.equal(exported.columns.find(column => column.key === "total:discount").format, "rate");
  assert.equal(exported.columns.find(column => column.key === "total:revenue").format, "money");
});

function publishedRow(id, entry, old, newValue) {
  const dimensions = { fiscal_year: 2028, fiscal_season: 20281, track: "初中", product: "智慧空间", grade: "七年级", subject: "数学", campus: "西直门", entry_attribute: entry };
  const count = { ...dimensions, season_headcount_result_id: id, integer_old_students: old, integer_new_students: newValue, integer_total_students: old + newValue, renewal_rate_ppm: 700000 };
  const revenue = { ...dimensions, season_headcount_result_id: id, quarter_name: "暑假", integer_total_students: old + newValue, quarter_recognized_revenue_yuan: "100.25", business_quarter_revenue_yuan: "106.265", list_price_revenue_yuan: "120", detail_discount_rate_ppm: 900000 };
  return { count, revenue };
}

test("FY28 exact stored headcount joins are order independent and keep entry/non-entry results separate", () => {
  const one = publishedRow("headcount-one", "ENTRY", 0, 12), two = publishedRow("headcount-two", "NON_ENTRY", 9, 3);
  const facts = normalizePublishedFacts([one.revenue, two.revenue], [two.count, one.count]);
  assert.deepEqual(facts.map(fact => [fact.old, fact.new, fact.total]), [[0, 12, 12], [9, 3, 12]]);
  const values = aggregateBudgetFacts(facts);
  assert.equal(values.old, 9); assert.equal(values.new, 15); assert.equal(values.total, 24);
  assert.equal(values.revenue, 200.5);
  const withoutId = [one.revenue, two.revenue].map(({ season_headcount_result_id, ...row }) => row);
  assert.deepEqual(normalizePublishedFacts(withoutId, [two.count, one.count]).map(fact => [fact.old, fact.new]), [[0, 12], [9, 3]]);
});

test("missing, duplicate or incompatible FY28 headcounts remain unknown, never inferred from shares or total", () => {
  const { revenue, count } = publishedRow("headcount-one", "ENTRY", 0, 12);
  for (const counts of [[], [count, count], [{ ...count, integer_total_students: 13 }], [{ ...count, grade: "八年级" }], [{ ...count, fiscal_year: 2027 }]]) {
    const facts = normalizePublishedFacts([revenue], counts);
    assert.equal(facts[0].old, null); assert.equal(facts[0].new, null); assert.equal(facts[0].total, 12);
    assert.equal(aggregateBudgetFacts(facts).old, null);
  }
});

test("URL and cross-year saved-view cleanup preserve revision context and valid selections without modifying amounts", () => {
  const state = defaultAnalysisState();
  state.view = "pivot"; state.unit = "yuan"; state.config = config;
  state.filters.campus = "旧校区"; state.filters.product = "智慧空间";
  const url = writeAnalysisState("revision=immutable-version&section=results&view=record", state);
  assert.equal(new URLSearchParams(url).get("revision"), "immutable-version");
  assert.equal(new URLSearchParams(url).get("section"), "results");
  assert.equal(new URLSearchParams(url).get("view"), "record");
  assert.deepEqual(readAnalysisState(url), state);
  const facts = normalizePublishedFacts([publishedRow("headcount-one", "ENTRY", 0, 12).revenue]);
  const before = JSON.stringify(facts), cleaned = validateAnalysisState(readAnalysisState(url), facts);
  assert.deepEqual(cleaned.removedFilters, ["校区：旧校区"]);
  assert.equal(cleaned.state.filters.campus, ""); assert.equal(cleaned.state.filters.product, "智慧空间");
  assert.equal(JSON.stringify(facts), before);
});

test("switching sibling analysis tabs retains the same scoped facts and immutable budget totals", () => {
  const { revenue, count } = publishedRow("headcount-one", "ENTRY", 0, 12);
  const facts = normalizePublishedFacts([revenue], [count]), before = JSON.stringify(facts);
  const initial = { ...defaultAnalysisState(), filters: { ...defaultAnalysisState().filters, campus: "西直门", product: "智慧空间" }, config };
  const expected = aggregateBudgetFacts(filterBudgetFacts(facts, initial.filters));
  let search = "archive=fy27&step=5&revision=immutable-version&baView=pivot&baTable=detail";
  assert.equal(readAnalysisState(search).view, "detail");
  for (const view of ["detail", "pivot", "board", "detail"]) {
    search = writeAnalysisState(search, { ...initial, view });
    const resolved = validateAnalysisState(readAnalysisState(search), facts);
    assert.equal(resolved.state.view, view);
    assert.deepEqual(resolved.state.filters, initial.filters);
    assert.deepEqual(resolved.state.config, initial.config);
    assert.deepEqual(resolved.removedFilters, []);
    assert.deepEqual(aggregateBudgetFacts(filterBudgetFacts(facts, resolved.state.filters)), expected);
    assert.equal(new URLSearchParams(search).get("revision"), "immutable-version");
    assert.equal(new URLSearchParams(search).has("baTable"), false);
  }
  assert.equal(JSON.stringify(facts), before);
});
