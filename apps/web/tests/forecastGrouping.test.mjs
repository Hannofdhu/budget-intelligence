// Scoped display aggregation only. No API writes, recalculation, or saved snapshot mutation.
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  forecastGroupDimensions, forecastGroupDimensionLabels, forecastQuarterHeadcounts,
  forecastSeasons, groupForecastRows, summarizeForecastRows,
} from "../src/forecastApi.ts";

const row = (overrides = {}) => ({ season: "暑假", track: "智慧空间", campus: "西直门", product: "常规班", grade: "七年级", subject: "数学",
  budget_revenue: "100000.123456", forecast_revenue: "98000.654321", budget_total: 100, forecast_total: 97,
  budget_new: 40, budget_old: 60, forecast_new: 37, forecast_old: 60, method: "FROZEN_ACTUAL", ...overrides });
const valueFields = ["budget_revenue", "forecast_revenue", "gap", "achievement", "budget_total", "budget_new", "budget_old", "forecast_total", "forecast_new", "forecast_old"];

test("grouping registry includes all six business dimensions with one consistent label per dimension", () => {
  assert.deepEqual(forecastGroupDimensions, ["track", "product", "subject", "grade", "campus", "season"]);
  assert.deepEqual(forecastGroupDimensions.map(key => forecastGroupDimensionLabels[key]), ["赛道", "产品", "科目", "年级", "校区", "季度"]);
});

test("one through six selected dimensions produce one row per real tuple, with raw dimension values and no invented subtotals", () => {
  const first = row(), second = row({ track: "智慧伴学", product: "云帆", subject: "英语", grade: "八年级", campus: "学习机校区", season: "春季" });
  const source = [first, { ...first }, second];
  for (let count = 1; count <= 6; count++) {
    const selected = forecastGroupDimensions.slice(0, count), result = groupForecastRows(source, selected);
    assert.equal(result.length, 2, `${count} dimensions must not generate rollup rows`);
    for (const original of [first, second]) {
      const tuple = selected.map(key => original[key]), grouped = result.find(item => item.key === JSON.stringify(tuple));
      assert.ok(grouped, `${count} dimensions retain exact tuple identity`);
      assert.deepEqual(grouped.dimensions, Object.fromEntries(selected.map(key => [key, original[key]])));
      assert.equal(grouped.records, original === first ? 2 : 1);
      assert.equal(grouped.budget_new, original === first ? 80 : 40);
      assert.equal(grouped.forecast_old, original === first ? 120 : 60);
    }
  }
  assert.deepEqual(groupForecastRows(source, "track"), groupForecastRows(source, ["track"]), "legacy single-dimension callers remain compatible");
  assert.deepEqual(groupForecastRows(source, ["track", "track"]), groupForecastRows(source, "track"), "duplicate dimensions do not create duplicate columns");
  assert.deepEqual(groupForecastRows(source, []), groupForecastRows(source, "track"), "an empty selection safely retains the default grouping");
});

test("tuple keys never collide when dimension values contain display delimiters, separators, quotes, or prototype names", () => {
  const source = [row({ track: "甲 / 乙", subject: "丙" }), row({ track: "甲", subject: "乙 / 丙" }),
    row({ track: "甲\u001f乙", subject: '丙"丁' }), row({ track: "甲", subject: '乙\u001f丙"丁' }),
    row({ track: "__proto__", subject: "constructor" }), row({ track: "", subject: "全部" })];
  const result = groupForecastRows(source, ["track", "subject"]);
  assert.equal(result.length, source.length);
  assert.equal(new Set(result.map(item => item.key)).size, source.length);
  for (const item of result) assert.deepEqual(JSON.parse(item.key), [item.dimensions.track, item.dimensions.subject]);
  assert.equal(result.filter(item => item.name === "甲 / 乙 / 丙").length, 2, "a readable label is not a group identity");
});

test("multi-dimension sorting follows selected hierarchy with business-grade and fiscal-season ordering", () => {
  const source = ["高三", "九年级", "八年级", "七年级", "高一", "高二"].flatMap(grade => [...forecastSeasons].reverse().map(season => row({ grade, season })));
  const grades = ["七年级", "八年级", "九年级", "高一", "高二", "高三"];
  const byGrade = groupForecastRows(source, ["track", "grade", "season"]);
  assert.deepEqual(byGrade.map(item => [item.dimensions.grade, item.dimensions.season]), grades.flatMap(grade => forecastSeasons.map(season => [grade, season])));
  const bySeason = groupForecastRows(source, ["season", "grade"]);
  assert.deepEqual(bySeason.map(item => [item.dimensions.season, item.dimensions.grade]), forecastSeasons.flatMap(season => grades.map(grade => [season, grade])));
  assert.deepEqual(groupForecastRows(source, "season").map(item => item.name), forecastSeasons);
  assert.deepEqual(groupForecastRows(source, "grade").map(item => item.name), grades);
  const unknown = groupForecastRows([row({ season: "未命名季度" }), ...forecastSeasons.map(season => row({ season }))], "season");
  assert.deepEqual(unknown.map(item => item.name), [...forecastSeasons, "未命名季度"]);
});

test("default single-track view retains revenue order while other dimensions use stable Chinese numeric labels", () => {
  const source = [row({ track: "乙", budget_revenue: 10 }), row({ track: "甲", budget_revenue: 20 }), row({ track: "丙", budget_revenue: null })];
  assert.deepEqual(groupForecastRows(source, "track").map(item => item.name), ["甲", "乙", "丙"]);
  const campuses = [row({ campus: "校区10", budget_revenue: 100 }), row({ campus: "校区2", budget_revenue: 1 }), row({ campus: "校区1", budget_revenue: 10 })];
  assert.deepEqual(groupForecastRows(campuses, "campus").map(item => item.name), ["校区1", "校区2", "校区10"]);
  assert.deepEqual(groupForecastRows(campuses, ["campus", "track"]).map(item => item.dimensions.campus), ["校区1", "校区2", "校区10"]);
});

test("grouped new and old counts use the same saved-field presentation rules as the quarter breakdown", () => {
  const source = [row(), row({ method: "ACTUAL_UNAVAILABLE", forecast_revenue: null, forecast_total: null, forecast_new: null, forecast_old: undefined }),
    row({ budget_covered: false, budget_new: 900, budget_old: 900, forecast_new: 2, forecast_old: 3 }),
    row({ counts_not_applicable: true, budget_total: 500, forecast_total: 500, budget_new: 500, budget_old: 500, forecast_new: 500, forecast_old: 500 })];
  const result = groupForecastRows(source, ["track", "grade"])[0], split = forecastQuarterHeadcounts(source, "暑假");
  assert.deepEqual(Object.fromEntries(Object.keys(split).map(key => [key, result[key]])), split);
  assert.deepEqual(split, { budget_new: 80, budget_old: 120, forecast_new: 39, forecast_old: 63 });
  assert.equal(result.budget_total, 200);
  assert.equal(result.forecast_total, 194);
  assert.equal(result.hasUnbudgeted, true);
  const legacy = summarizeForecastRows([row({ budget_new: undefined, budget_old: undefined, forecast_new: undefined, forecast_old: undefined })]);
  assert.equal(legacy.budget_total, 100); assert.equal(legacy.forecast_total, 97);
  for (const key of ["budget_new", "budget_old", "forecast_new", "forecast_old"]) assert.equal(legacy[key], null, `${key} is not inferred from total counts`);
});

test("only unavailable actual splits default to zero; blank, invalid, future, and unmatched splits remain unknown", () => {
  const missing = row({ method: "ACTUAL_UNAVAILABLE", forecast_total: null, forecast_new: null, forecast_old: undefined });
  const unavailable = summarizeForecastRows([missing]);
  assert.equal(unavailable.forecast_total, 0); assert.equal(unavailable.forecast_new, 0); assert.equal(unavailable.forecast_old, 0);
  const statusOnly = summarizeForecastRows([row({ data_status: "ACTUAL_UNAVAILABLE", forecast_new: null, forecast_old: null })]);
  assert.equal(statusOnly.forecast_new, 0); assert.equal(statusOnly.forecast_old, 0);
  for (const override of [{ method: "FROZEN_ACTUAL" }, { method: "TERMINAL_FORECAST" }, { method: "PARENT_PARAMETER_PROJECTION" },
    { method: "UNMAPPED_ACTUAL" }, { method: "UNMAPPED_FROZEN_CARRYIN" }, { unmapped: true }]) {
    const result = summarizeForecastRows([{ ...missing, ...override }]);
    assert.equal(result.forecast_total, null); assert.equal(result.forecast_new, null); assert.equal(result.forecast_old, null);
  }
  for (const value of ["", " ", "-", "invalid", Infinity, NaN]) {
    const result = summarizeForecastRows([{ ...missing, forecast_new: value, forecast_old: value }]);
    assert.equal(result.forecast_new, null); assert.equal(result.forecast_old, null);
  }
  const recorded = summarizeForecastRows([{ ...missing, forecast_new: "-1.25", forecast_old: "0" }]);
  assert.equal(recorded.forecast_new, -1.25); assert.equal(recorded.forecast_old, 0);
});

test("financial-only and unbudgeted businesses retain revenue without fabricating baseline or student facts", () => {
  const financial = row({ counts_not_applicable: true, budget_revenue: 12, forecast_revenue: 15, budget_total: 999, forecast_total: 999 });
  const result = summarizeForecastRows([financial]);
  assert.equal(result.budget_revenue, 12); assert.equal(result.forecast_revenue, 15); assert.equal(result.gap, -3);
  for (const key of ["budget_total", "budget_new", "budget_old", "forecast_total", "forecast_new", "forecast_old"]) assert.equal(result[key], null);
  const independent = row({ budget_covered: false, budget_revenue: 0, budget_total: 0, budget_new: 0, budget_old: 0 });
  const only = summarizeForecastRows([independent]);
  assert.equal(only.hasUnbudgeted, true); assert.equal(only.budget_revenue, null); assert.equal(only.budget_total, null);
  assert.equal(only.budget_new, null); assert.equal(only.budget_old, null); assert.equal(only.gap, null); assert.equal(only.achievement, null);
  assert.equal(only.forecast_total, 97); assert.equal(only.forecast_new, 37); assert.equal(only.forecast_old, 60);
  const covered = row({ budget_revenue: 100, forecast_revenue: 90 }), mixed = summarizeForecastRows([covered, { ...independent, forecast_revenue: 20 }]);
  assert.equal(mixed.budget_revenue, 100); assert.equal(mixed.forecast_revenue, 110); assert.equal(mixed.gap, -10); assert.equal(mixed.achievement, 1.1);
});

test("unmapped or missing monetary facts suppress comparisons; known zero/negative budgets retain signed gap but never a rate", () => {
  for (const marker of [{ method: "UNMAPPED_ACTUAL" }, { method: "UNMAPPED_FROZEN_CARRYIN" }, { unmapped: true }]) {
    const result = summarizeForecastRows([row(), row({ ...marker, budget_revenue: 0, budget_new: 900, budget_old: 900 })]);
    assert.equal(result.unmapped, true); assert.equal(result.budget_revenue, 100000.123456);
    assert.equal(result.gap, null); assert.equal(result.achievement, null); assert.equal(result.budget_new, 40); assert.equal(result.budget_old, 60);
    assert.equal(result.forecast_new, 74); assert.equal(result.forecast_old, 120);
  }
  for (const marker of [{ budget_revenue: null }, { forecast_revenue: null }]) {
    const result = summarizeForecastRows([row(), row(marker)]);
    assert.equal(result.gap, null); assert.equal(result.achievement, null);
  }
  for (const budget of [0, -10]) {
    const result = summarizeForecastRows([row({ budget_revenue: budget, forecast_revenue: 5 })]);
    assert.equal(result.gap, budget - 5); assert.equal(result.achievement, null);
  }
});

test("current-scope summary sums raw full-precision rows and recomputes the rate instead of averaging or summing group displays", () => {
  const source = [row({ track: "甲", budget_revenue: "100.000001", forecast_revenue: "50.000001" }),
    row({ track: "乙", budget_revenue: "900.000002", forecast_revenue: "810.000002" })];
  const grouped = groupForecastRows(source, "track"), summary = summarizeForecastRows(source);
  assert.equal(summary.records, 2); assert.equal(summary.budget_revenue, 1000.000003); assert.equal(summary.forecast_revenue, 860.000003);
  assert.equal(summary.achievement, summary.forecast_revenue / summary.budget_revenue);
  assert.notEqual(summary.achievement, (grouped[0].achievement + grouped[1].achievement) / 2);
  assert.equal(summary.gap, summary.budget_revenue - summary.forecast_revenue);
  const cancellation = [row({ track: "甲", budget_revenue: 1e16, forecast_revenue: 1e16 }),
    row({ track: "甲", budget_revenue: 1, forecast_revenue: 1 }), row({ track: "乙", budget_revenue: -1e16, forecast_revenue: -1e16 })];
  const precise = summarizeForecastRows(cancellation);
  assert.equal(precise.budget_revenue, 1); assert.equal(precise.forecast_revenue, 1); assert.equal(precise.achievement, 1);
  assert.equal(groupForecastRows(cancellation, "track").reduce((total, group) => total + group.budget_revenue, 0), 0, "aggregating already-converted groups would lose this unit");
});

test("summaries preserve empty/unknown semantics and neither grouping nor sorting mutates frozen saved rows", () => {
  const source = Object.freeze([Object.freeze(row({ campus: "校区2" })), Object.freeze(row({ campus: "校区1", forecast_old: null }))]);
  const before = JSON.stringify(source), grouped = groupForecastRows(source, ["campus", "season"]), summary = summarizeForecastRows(source);
  assert.equal(grouped.length, 2); assert.equal(summary.forecast_old, null); assert.equal(JSON.stringify(source), before);
  assert.equal(source[0].campus, "校区2");
  const empty = summarizeForecastRows([]);
  assert.equal(empty.records, 0); assert.equal(empty.unmapped, false); assert.equal(empty.hasUnbudgeted, false);
  for (const key of valueFields) assert.equal(empty[key], null, key);
  assert.deepEqual(groupForecastRows([], forecastGroupDimensions), []);
});
