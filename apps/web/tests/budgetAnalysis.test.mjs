import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import {
  aggregateBudgetFacts, buildBudgetPivot, defaultAnalysisState, dimensionLabels, emptyBudgetFilters,
  filterBudgetFacts, metricLabels, normalizeHistoricalFacts, normalizePublishedFacts, numericValue,
  pivotExport, readAnalysisState, seasons, validateAnalysisState, writeAnalysisState,
} from "../src/budgetAnalysis.ts";
import { budgetWorkbook } from "../src/budgetExport.ts";

const fact = (overrides = {}) => ({ id: "1", season: "暑假", track: "智慧空间", campus: "西直门", product: "常规班", grade: "七年级", subject: "数学",
  revenue: 100, business: 90, listRevenue: 100, old: 7, new: 3, total: 10, ...overrides });
const config = (overrides = {}) => ({ rows: ["campus"], column: "season", metrics: ["revenue"], sort: "revenue-desc", ...overrides });

test("numeric values never turn blank or a dash into zero", () => {
  for (const value of [null, undefined, "", " ", "-", "－", "0x10", "NaN", "Infinity", NaN, Infinity, true]) assert.equal(numericValue(value), null);
  for (const [value, expected] of [[0, 0], ["0", 0], ["-0.1", -0.1], [" 3.2 ", 3.2], ["1e3", 1000], [".3", 0.3]]) assert.equal(numericValue(value), expected);
});

test("aggregation uses paired bases and strict completeness instead of averaging rates or hiding missing values", () => {
  const values = aggregateBudgetFacts([fact({ business: 90, listRevenue: 100, detailDiscount: 0.9 }), fact({ business: 100, listRevenue: 200, detailDiscount: 0.5 })]);
  assert.equal(values.discount, 190 / 300);
  assert.notEqual(values.discount, 0.7);
  assert.equal(values.revenue, 200);
  assert.equal(aggregateBudgetFacts([fact({ listRevenue: 0 })]).discount, null);
  assert.equal(aggregateBudgetFacts([fact(), fact({ listRevenue: null })]).discount, null);
  assert.equal(aggregateBudgetFacts([fact(), fact({ revenue: "-", old: null, new: 0 })]).revenue, null);
  assert.equal(aggregateBudgetFacts([fact(), fact({ old: null })]).old, null);
  assert.equal(aggregateBudgetFacts([fact({ revenue: 0, old: 0 })]).revenue, 0);
  assert.equal(aggregateBudgetFacts([fact({ revenue: -1.5 })]).revenue, -1.5);
  assert.deepEqual(aggregateBudgetFacts([]), { revenue: null, business: null, old: null, new: null, total: null, discount: null });
});

test("compensated aggregation preserves precision without rounding individual rows", () => {
  assert.equal(aggregateBudgetFacts([fact({ revenue: 1e16 }), fact({ revenue: 1 }), fact({ revenue: -1e16 })]).revenue, 1);
  assert.equal(aggregateBudgetFacts([fact({ revenue: "0.004" }), fact({ revenue: "0.004" })]).revenue, 0.008);
});

test("historical normalization preserves raw values but excludes repeated annual totals and workbook metadata", () => {
  const source = { row: 4, ...fact({ old: "-", new: null, total: 0, revenue: "-2.33", price: "3200" }), annual: 12345, list_revenue: "100", sources: { annual: "W4" }, parameters: {}, source_file: "secret.xlsx" };
  const normalized = normalizeHistoricalFacts([source])[0];
  assert.equal(normalized.old, "-"); assert.equal(normalized.new, null); assert.equal(normalized.total, 0);
  assert.equal(normalized.revenue, "-2.33"); assert.equal(normalized.price, "3200");
  assert.equal(normalized.listRevenue, "100");
  for (const key of ["annual", "row", "sources", "parameters", "source_file"]) assert.ok(!(key in normalized));
  assert.equal(aggregateBudgetFacts([normalized]).revenue, -2.33);
  assert.equal(normalizeHistoricalFacts([{ ...source, list_revenue: null, listRevenue: 500 }])[0].listRevenue, null);
});

test("published normalization does not fabricate old and new counts", () => {
  const published = { fiscal_season: 20281, track: "初中", campus: "西直门", product: "常规班", grade: "七年级", subject: "数学", entry_attribute: "ENTRY",
    quarter_recognized_revenue_yuan: "-1.25", business_quarter_revenue_yuan: "90", list_price_revenue_yuan: "100", integer_total_students: 10, detail_discount_rate_ppm: 900000 };
  const result = normalizePublishedFacts([published])[0];
  assert.equal(result.season, "暑假"); assert.equal(result.revenue, "-1.25");
  assert.equal(result.old, null); assert.equal(result.new, null); assert.equal(result.total, 10); assert.equal(result.detailDiscount, 0.9);
  assert.equal(aggregateBudgetFacts([result]).old, null);
});

test("published counts join exact IDs and unique complete dimensions, with ambiguous or incompatible data left missing", () => {
  const base = { fiscal_season: 20281, fiscal_year: 2028, track: "初中", campus: "西直门", product: "常规班", grade: "七年级", subject: "数学", entry_attribute: "ENTRY",
    quarter_recognized_revenue_yuan: "100", business_quarter_revenue_yuan: "90", list_price_revenue_yuan: "100", integer_total_students: 10 };
  const count = { ...base, season_headcount_result_id: "correct", integer_old_students: 6, integer_new_students: 4 };
  assert.equal(normalizePublishedFacts([base], [count])[0].old, 6);
  assert.equal(normalizePublishedFacts([{ ...base, season_headcount_result_id: "correct" }], [count])[0].new, 4);
  assert.equal(normalizePublishedFacts([{ ...base, season_headcount_result_id: "wrong" }], [count])[0].old, null);
  assert.equal(normalizePublishedFacts([base], [count, { ...count, season_headcount_result_id: "other" }])[0].old, null);
  assert.equal(normalizePublishedFacts([{ ...base, season_headcount_result_id: "correct" }], [count, count])[0].old, null);
  for (const incompatible of [ { ...count, entry_attribute: "NON_ENTRY" }, { ...count, campus: "中关村南" }, { ...count, integer_total_students: 12 }, { ...count, fiscal_year: 2027 } ]) {
    assert.equal(normalizePublishedFacts([{ ...base, season_headcount_result_id: "correct" }], [incompatible])[0].old, null);
  }
  assert.equal(normalizePublishedFacts([{ ...base, headcount_result_version_id: "v2" }], [{ ...count, headcount_result_version_id: "v1" }])[0].new, null);
});

test("hierarchical pivot uses independent facts, sorted business seasons and null sparse cells", () => {
  const facts = [fact({ id: "a", campus: "东校区", product: "A", revenue: 100, season: "春季" }), fact({ id: "b", campus: "东校区", product: "B", revenue: 0, season: "暑假" }),
    fact({ id: "c", campus: "西校区", product: "A", revenue: 200, season: "秋季" }), fact({ id: "d", campus: "东校区", product: "A", revenue: 10, season: "春季" })];
  const frozen = JSON.stringify(facts);
  const pivot = buildBudgetPivot(facts, config({ rows: ["campus", "product"] }));
  assert.deepEqual(pivot.columns.map(column => column.label), ["暑假", "秋季", "春季"]);
  assert.equal(pivot.rows[0].label, "西校区"); // descending income, not input order
  assert.equal(pivot.rows[0].parentKey, null); assert.equal(pivot.rows[0].depth, 0); assert.equal(pivot.rows[0].leaf, false);
  assert.equal(pivot.rows[1].parentKey, pivot.rows[0].key); assert.equal(pivot.rows[1].depth, 1); assert.equal(pivot.rows[1].leaf, true);
  assert.equal(pivot.rows.find(row => row.label === "A" && row.path.campus === "东校区").total.revenue, 110);
  const east = pivot.rows.find(row => row.label === "东校区");
  assert.equal(east.cells[JSON.stringify(["暑假"])].revenue, 0);
  assert.equal(east.cells[JSON.stringify(["秋季"])].revenue, null);
  assert.equal(pivot.totals.revenue, 310); assert.equal(pivot.columnTotals[JSON.stringify(["春季"])].revenue, 110);
  assert.equal(JSON.stringify(facts), frozen);
});

test("season row order is not accidentally reordered by revenue", () => {
  const pivot = buildBudgetPivot(seasons.toReversed().map((season, index) => fact({ season, revenue: index })), config({ rows: ["season"], column: "none" }));
  assert.deepEqual(pivot.rows.map(row => row.label), seasons);
});

test("filters use exact whole dimensions and never mutate display facts", () => {
  const facts = [fact({ campus: "西直门", grade: "七年级" }), fact({ campus: "西直门南", grade: "七年级" }), fact({ campus: "西直门", grade: "八年级" })];
  assert.equal(filterBudgetFacts(facts, { campus: "西直门" }).length, 2);
  assert.equal(filterBudgetFacts(facts, { campus: "西直门", grade: "七年级" }).length, 1);
  assert.equal(filterBudgetFacts(facts, emptyBudgetFilters).length, 3);
  assert.equal(filterBudgetFacts(facts, { product: "不存在" }).length, 0);
});

test("analysis URL roundtrips and preserves fiscal year, revision, section and unrelated parameters", () => {
  const state = { ...defaultAnalysisState(), view: "pivot", unit: "yuan", filters: { ...emptyBudgetFilters, campus: "西直门", season: "秋季" },
    config: config({ rows: ["campus", "product"], metrics: ["revenue", "new", "discount"], sort: "name-asc" }) };
  const search = writeAnalysisState("?archive=fy27&step=5&revision=r150&custom=hello%20world", state);
  assert.deepEqual(readAnalysisState(search), state);
  const params = new URLSearchParams(search);
  assert.equal(params.get("archive"), "fy27"); assert.equal(params.get("step"), "5"); assert.equal(params.get("revision"), "r150"); assert.equal(params.get("custom"), "hello world");
  assert.ok(!params.has("bf_grade"));
  const cleared = writeAnalysisState(search, defaultAnalysisState());
  assert.ok(!new URLSearchParams(cleared).has("bf_campus"));
});

test("invalid URL config and duplicate dimensions are sanitized without evaluating input", () => {
  const state = readAnalysisState("baRows=campus,campus,unknown,season&baColumn=season&baMetrics=revenue,revenue,evil&baSort=evil&baView=evil&baUnit=evil");
  assert.deepEqual(state.config, config({ rows: ["campus", "season"], column: "none" }));
  assert.equal(state.view, "board"); assert.equal(state.unit, "wan");
  assert.deepEqual(readAnalysisState("baRows=&baMetrics=&baColumn=bad").config, defaultAnalysisState().config);
  const written = readAnalysisState(writeAnalysisState("", { ...defaultAnalysisState(), config: config({ rows: ["campus", "campus"], column: "campus", metrics: ["old", "old"] }) }));
  assert.deepEqual(written.config, config({ rows: ["campus"], column: "none", metrics: ["old"] }));
});

test("a year switch clears unavailable filters but preserves valid zero-result combinations", () => {
  const state = { ...defaultAnalysisState(), filters: { ...emptyBudgetFilters, campus: "已关闭校区", grade: "八年级" } };
  const checked = validateAnalysisState(state, [fact({ grade: "八年级" })]);
  assert.equal(checked.state.filters.campus, ""); assert.equal(checked.state.filters.grade, "八年级");
  assert.deepEqual(checked.removedFilters, ["校区：已关闭校区"]);
  assert.equal(state.filters.campus, "已关闭校区");
  const available = [fact({ campus: "A", grade: "七年级" }), fact({ campus: "B", grade: "八年级" })];
  const noCombination = validateAnalysisState({ ...defaultAnalysisState(), filters: { ...emptyBudgetFilters, campus: "A", grade: "八年级" } }, available);
  assert.deepEqual(noCombination.removedFilters, []);
  assert.equal(filterBudgetFacts(available, noCombination.state.filters).length, 0);
});

test("pivot export contains leaves and one grand total, not repeated subtotal records, and types remain numeric", async () => {
  const facts = [fact({ campus: "A", product: "P", season: "暑假", revenue: 10 }), fact({ campus: "A", product: "Q", season: "秋季", revenue: 20 }), fact({ campus: "B", product: "P", season: "秋季", revenue: 30 })];
  const conf = config({ rows: ["campus", "product"], metrics: ["revenue", "discount"] });
  const pivot = buildBudgetPivot(facts, conf), exported = pivotExport(pivot, conf);
  assert.equal(exported.rows.length, 4);
  assert.equal(exported.rows.filter(row => row.campus === "合计").length, 1);
  assert.equal(exported.rows.at(-1)["total:revenue"], 60);
  assert.equal(exported.rows.slice(0, -1).reduce((sum, row) => sum + row["total:revenue"], 0), 60);
  const workbook = await budgetWorkbook(exported.columns, exported.rows);
  const revenueIndex = exported.columns.findIndex(column => column.key === "total:revenue") + 1;
  assert.equal(workbook.worksheets[0].getCell(2, revenueIndex).value, exported.rows[0]["total:revenue"]);
  assert.equal(workbook.worksheets[0].getCell(5, revenueIndex).value, 60);
  assert.equal(workbook.worksheets[0].getCell(5, revenueIndex).numFmt, "#,##0.00");
  assert.equal(exported.columns.some(column => /来源|口径|原表|Excel/.test(column.label)), false);
  const none = pivotExport(buildBudgetPivot(facts, config({ column: "none" })), config({ column: "none" }));
  assert.equal(none.columns.filter(column => column.format).length, 1);
  assert.equal(none.rows.at(-1)["all:revenue"], 60);
});

const sourcePath = new URL("../../../output/fy27-shadow/source-cells.json", import.meta.url);
test("the real FY27 952 quarterly facts reconcile 113004618.59 without counting 238 annual W cells four times", { skip: !fs.existsSync(sourcePath) }, () => {
  const source = JSON.parse(fs.readFileSync(sourcePath, "utf8"));
  const blocks = ["AI AJ AL AM AN AO AP AQ AR AS AV AW AX AY", "AZ BA BC BD BE BF BG BH BI BJ BM BN BO BP", "BQ BR BT BU BV BW BX BY BZ CA CD CE CF CG", "CH CI CK CL CM CN CO CP CQ CR CU CV CW CX"];
  const fields = "renewal share old new total price discount tuition list_revenue business recognition current revenue carryout".split(" ");
  const sourceRows = source.sheets["滚动表"].rows.filter(row => row.row >= 2 && row.cells.A?.value != null);
  const records = sourceRows.flatMap(row => blocks.map((block, index) => ({
    track: row.cells.A.value, product: row.cells.B.value, grade: row.cells.D.value, subject: row.cells.F.value, campus: row.cells.G.value,
    annual: row.cells.W.value, season: seasons[index], ...Object.fromEntries(block.split(" ").map((column, i) => [fields[i], row.cells[column]?.value ?? null])),
  })));
  const facts = normalizeHistoricalFacts(records), overall = aggregateBudgetFacts(facts);
  assert.equal(sourceRows.length, 238); assert.equal(facts.length, 952);
  assert.equal(overall.revenue.toFixed(2), "113004618.59");
  const annual = sourceRows.reduce((sum, row) => sum + row.cells.W.value, 0);
  assert.equal(annual.toFixed(2), overall.revenue.toFixed(2));
  assert.notEqual(overall.revenue.toFixed(2), (annual * 4).toFixed(2));
  const pivot = buildBudgetPivot(facts, config({ rows: ["campus", "product", "grade"], metrics: ["revenue", "total"] }));
  assert.equal(pivot.totals.revenue.toFixed(2), "113004618.59");
  assert.equal(pivot.rows.filter(row => row.leaf).reduce((sum, row) => sum + row.total.revenue, 0).toFixed(2), "113004618.59");
  assert.equal(Object.values(pivot.columnTotals).reduce((sum, values) => sum + values.revenue, 0).toFixed(2), "113004618.59");
  assert.ok(facts.every(row => !Object.hasOwn(row, "annual")));
});

test("public metric registry exposes only supported additive metrics and true weighted discount", () => {
  assert.deepEqual(Object.keys(dimensionLabels), ["season", "track", "campus", "product", "grade", "subject"]);
  assert.deepEqual(Object.keys(metricLabels), ["revenue", "business", "old", "new", "total", "discount"]);
  assert.ok(!Object.hasOwn(metricLabels, "renewal")); // missing full bases: no average of detail retention rates
  assert.deepEqual(defaultAnalysisState().filters, emptyBudgetFilters);
});
