import { test } from "node:test";
import assert from "node:assert/strict";
import { campusRateBase, campusRateValue, campusRateToCount, setCampusRate, campusForecastTotals, restoreCampusRates } from "../src/forecastRateInputs.ts";

const row = { season: "秋季", campus: "公主坟", budget_new: 65, current_business_new: 34, new_base: 38, old_base: 235, budgeted_additional: 31, unbudgeted_additional: 7, additional: 38, new_withdrawal: 1, old_withdrawal: 33 };
const assumptions = { states: { 暑假: "FINALIZED", 秋季: "OPEN", 寒假: "FUTURE", 春季: "FUTURE" }, minimum_sample: 30, mappings: [], adjustments: [] };
test("campus rates use distinct budget/business and current-class denominators", () => {
  assert.equal(campusRateValue(row, "additional"), 100);
  assert.equal(campusRateBase(row, "new_withdrawal"), 38);
  assert.equal(campusRateValue(row, "old_withdrawal"), 33 / 235 * 100);
  assert.equal(campusRateToCount(row, "additional", "120"), 44);
  assert.equal(campusRateToCount(row, "additional", "10"), 0, "cannot subtract enrolled students");
  assert.equal(campusRateToCount(row, "new_withdrawal", "10"), 4);
  assert.equal(campusRateToCount(row, "old_withdrawal", "100"), 235);
  assert.equal(campusRateToCount({ ...row, budget_new: 10, current_business_new: 0 }, "additional", "105"), 11, "half-person rounds up");
});
test("missing and zero denominators are unavailable; bad rates never become zero", () => {
  for (const base of [0, null, "bad"]) {
    assert.equal(campusRateValue({ ...row, budget_new: base }, "additional"), null);
    assert.equal(campusRateToCount({ ...row, budget_new: base }, "additional", "100"), null);
  }
  for (const input of ["", "-1", "NaN", "Infinity", "1.1234", "1e3"]) assert.equal(campusRateToCount(row, "new_withdrawal", input), null);
  assert.equal(campusRateToCount(row, "old_withdrawal", "100.1"), null);
  assert.equal(campusRateToCount({ ...row, budget_new: 10000000, current_business_new: 0 }, "additional", "1000"), null);
});
test("untouched or same-rounded-count inputs do not switch withdrawal allocation mode", () => {
  assert.equal(setCampusRate(assumptions, row, "new_withdrawal", "2.6", 1), assumptions);
  assert.equal(setCampusRate(assumptions, row, "additional", "100.01", 31), assumptions);
});
test("only the edited campus/type becomes a one-shot override; returning to saved values restores auto mode", () => {
  const before = JSON.stringify(assumptions);
  const next = setCampusRate(assumptions, row, "additional", "120", 31);
  assert.equal(next.adjustments[0].value, 44); assert.equal(next.adjustments[0].one_shot, true);
  assert.match(next.adjustments[0].reason, /仅用于本次/); assert.equal(JSON.stringify(assumptions), before);
  const secondCampus = setCampusRate(next, { ...row, campus: "清河" }, "old_withdrawal", "10", 33);
  assert.equal(secondCampus.adjustments.length, 2);
  const restored = setCampusRate(secondCampus, row, "additional", "100", 44, { assumptions, row });
  assert.equal(restored.adjustments.length, 1); assert.equal(restored.adjustments[0].campus, "清河");
});
test("saved manual overrides are restored exactly, without migrating historical values", () => {
  const original = { season: "秋季", campus: row.campus, kind: "new_withdrawal", value: 1, reason: "历史设定" };
  const saved = { ...assumptions, adjustments: [original] };
  const changed = setCampusRate(saved, row, "new_withdrawal", "20", 1);
  assert.deepEqual(setCampusRate(changed, row, "new_withdrawal", "2.632", 8, { assumptions: saved, row }).adjustments, [original]);
});
test("campus comparison never mixes quarters or campuses and preserves unknowns", () => {
  const rows = [{ season: "秋季", campus: row.campus, forecast_total: 10, forecast_revenue: 100 },
    { season: "春季", campus: row.campus, forecast_total: 999, forecast_revenue: 999 },
    { season: "秋季", campus: "清河", forecast_total: 999, forecast_revenue: 999 }];
  assert.deepEqual(campusForecastTotals({ rows }, "秋季", row.campus), { people: 10, revenue: 100 });
  assert.deepEqual(campusForecastTotals({ rows: [{ ...rows[0], forecast_total: null, forecast_revenue: null }] }, "秋季", row.campus), { people: null, revenue: null });
});
test("undo restores saved order and metadata exactly, while system reset removes only the chosen campus", () => {
  const first = { season: "秋季", campus: row.campus, kind: "additional", value: 31, reason: "历史人工值" };
  const second = { season: "秋季", campus: "清河", kind: "additional", value: 12, reason: "其他校区" };
  const saved = { ...assumptions, adjustments: [first, second] };
  const changed = setCampusRate(saved, row, "additional", "120", 31, { assumptions: saved, row });
  assert.deepEqual(restoreCampusRates(changed, saved, "秋季", row.campus), saved);
  assert.deepEqual(setCampusRate(changed, row, "additional", "100", 44, { assumptions: saved, row }), saved);
  assert.deepEqual(restoreCampusRates(changed, saved, "秋季", row.campus, true).adjustments, [second]);
});
