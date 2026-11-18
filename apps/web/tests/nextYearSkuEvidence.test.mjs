// Display-only frozen-source projection; no live reads or budget mutations.
import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";

const require = createRequire(import.meta.url);
const bundled = await build({ entryPoints: [fileURLToPath(new URL("../src/nextYearSkuEvidence.ts", import.meta.url))],
  bundle: true, write: false, platform: "node", format: "cjs", logLevel: "silent" });
const loaded = { exports: {} };
new Function("require", "module", "exports", bundled.outputFiles[0].text)(require, loaded, loaded.exports);
const { buildNextYearSkuEvidence } = loaded.exports;
const seasons = ["暑假", "秋季", "寒假", "春季"];
const sku = (overrides = {}) => ({ id: "sku-1", campus: "西直门", track: "智慧空间", product: "云帆", grade: "八年级", subject: "数学",
  enabled: true, entry: false, opening_carryover_yuan: "0", ...overrides });
const forecast = (overrides = {}) => ({ ...sku(), season: "暑假", method: "TERMINAL_FORECAST", budget_revenue: "999999", budget_total: 999,
  forecast_revenue: "10000", forecast_total: 10, ...overrides });
const year = (overrides = {}) => seasons.map(season => forecast({ season, ...overrides }));
const next = (rows = [{ combination_id: "sku-1", season: "暑假", old_students: 8 }]) => ({ rows });
const project = ({ combinations = [sku()], openingSources = [], calculation = next(), basisRows = year(), basisStatus = "ready", stale = false, basisIssues = [] } = {}) =>
  buildNextYearSkuEvidence(combinations, openingSources, calculation, basisRows, basisStatus, stale, basisIssues);
const promotion = (target = sku()) => ({ ...target, grade: "七年级", target_id: target.id, students: 12, exclusion: null });
const zeroYear = () => year({ method: "FROZEN_ACTUAL", forecast_revenue: "0", forecast_total: 0 });
const emptySlotRows = (season = "暑假") => zeroYear().map(row => row.season === season
  ? { ...row, method: "ACTUAL_UNAVAILABLE", data_status: "ACTUAL_UNAVAILABLE", forecast_revenue: null, forecast_total: null } : row);
const emptySlotIssue = (overrides = {}) => ({ code: "FINALIZED_ACTUAL_MISSING", severity: "INFO", message: "空预算占位，无来源业务", season: "暑假",
  dims: { campus: "西直门", track: "智慧空间", product: "云帆", grade: "八年级", subject: "数学" },
  detail: { classification: "EMPTY_BUDGET_SLOT_NO_SOURCE_BUSINESS" }, ...overrides });
const freeze = value => { if (value && typeof value === "object") { Object.values(value).forEach(freeze); Object.freeze(value); } return value; };

test("complete frozen history uses forecast values and preserves zero, signed money and annual people", () => {
  const basisRows = year().map((row, i) => ({ ...row, forecast_revenue: ["0", "-0.03", "10000", "20000"][i], forecast_total: i }));
  const result = project({ basisRows, calculation: next([{ combination_id: "sku-1", season: "暑假", old_students: 0 }]) }).get("sku-1");
  assert.equal(result.historyState, "value"); assert.equal(result.historyRevenue, 29999.97); assert.equal(result.historyStudents, 6);
  assert.equal(result.summerState, "value"); assert.equal(result.summerOld, 0); assert.equal(result.openingCarryover, 0);
  assert.equal(result.sourceLabel, "基准已有");
});

test("history joins all five dimensions exactly and never borrows neighboring SKU values", () => {
  const neighbors = ["campus", "track", "product", "grade", "subject"].flatMap(key => year({ [key]: `其他${key}`, forecast_revenue: "99999999", forecast_total: 99999 }));
  const result = project({ combinations: [sku(), sku({ id: "different-subject", subject: "物理" })], basisRows: [...year(), ...neighbors] });
  assert.equal(result.size, 2); assert.equal(result.get("sku-1").historyRevenue, 40000); assert.equal(result.get("sku-1").historyStudents, 40);
  assert.equal(result.get("different-subject").sourceLabel, "来源待核对");
  assert.equal(result.get("different-subject").historyState, "missing"); assert.equal(result.get("different-subject").historyRevenue, null);
});

test("absent and incomplete history are distinct and a three-season subtotal is never called annual", () => {
  const missing = project({ basisRows: [] }).get("sku-1");
  assert.equal(missing.historyState, "missing"); assert.equal(missing.sourceLabel, "来源待核对");
  assert.equal(missing.historyRevenue, null); assert.equal(missing.historyStudents, null);
  for (const omitted of seasons) {
    const partial = project({ basisRows: year().filter(row => row.season !== omitted) }).get("sku-1");
    assert.equal(partial.historyState, "incomplete", omitted); assert.equal(partial.sourceLabel, "基准已有");
    assert.equal(partial.historyRevenue, null); assert.equal(partial.historyStudents, null);
  }
});

test("loading and failed basis reads withhold history even if older row data is still present", () => {
  for (const basisStatus of ["loading", "error"]) {
    const result = project({ basisStatus }).get("sku-1");
    assert.equal(result.historyState, basisStatus); assert.equal(result.historyRevenue, null); assert.equal(result.historyStudents, null);
    assert.equal(result.sourceLabel, "来源待核对"); assert.equal(result.summerOld, 8);
  }
  const unavailable = project({ basisRows: null, basisStatus: "loading" }).get("sku-1");
  assert.equal(unavailable.historyRevenue, null); assert.equal(unavailable.historyState, "loading");
});

test("an unknown or invalid metric never becomes zero or a partial annual sum", () => {
  for (const invalid of [null, undefined, "", "NaN", "Infinity", Infinity, "1,000"]) {
    const badRevenue = year().map((row, i) => i === 1 ? { ...row, forecast_revenue: invalid } : row);
    const result = project({ basisRows: badRevenue }).get("sku-1");
    assert.equal(result.historyRevenue, null, String(invalid)); assert.equal(result.historyStudents, 40);
    const badPeople = year().map((row, i) => i === 2 ? { ...row, forecast_total: invalid } : row);
    const other = project({ basisRows: badPeople }).get("sku-1");
    assert.equal(other.historyRevenue, 40000); assert.equal(other.historyStudents, null, String(invalid));
  }
});

test("history retains unapproved unavailable actuals as unknown without borrowing another module's zero defaults", () => {
  const basisRows = year().map((row, i) => i === 0 ? { ...row, method: "ACTUAL_UNAVAILABLE", data_status: "ACTUAL_UNAVAILABLE", forecast_revenue: null, forecast_total: null } : row);
  const before = JSON.stringify(basisRows), result = project({ basisRows }).get("sku-1");
  assert.equal(result.historyRevenue, null); assert.equal(result.historyStudents, null); assert.notEqual(result.historyState, "empty");
  assert.equal(JSON.stringify(basisRows), before); assert.equal(basisRows[0].forecast_revenue, null);
  const unmatched = project({ basisRows: basisRows.map((row, i) => i === 0 ? { ...row, unmapped: true } : row) }).get("sku-1");
  assert.equal(unmatched.historyRevenue, null); assert.equal(unmatched.historyStudents, null);
  const financialOnly = project({ basisRows: year({ counts_not_applicable: true, forecast_total: null }) }).get("sku-1");
  assert.equal(financialOnly.historyRevenue, 40000); assert.equal(financialOnly.historyStudents, null);
});

test("promoted missing history, complete genuine zero and reviewed empty slots remain distinct source states", () => {
  const openingSources = [promotion()];
  const missing = project({ openingSources, basisRows: [], basisIssues: [emptySlotIssue()] }).get("sku-1");
  assert.equal(missing.historyState, "missing"); assert.equal(missing.sourceLabel, "升年级承接");
  const zero = project({ openingSources, basisRows: zeroYear() }).get("sku-1");
  assert.equal(zero.historyState, "empty"); assert.equal(zero.sourceLabel, "升年级承接");
  assert.equal(zero.historyRevenue, 0); assert.equal(zero.historyStudents, 0, "a genuine zero fact survives the unified empty-state presentation");
  for (const season of seasons) {
    const reviewed = project({ openingSources, basisRows: emptySlotRows(season), basisIssues: [emptySlotIssue({ season })] }).get("sku-1");
    assert.equal(reviewed.historyState, "empty", season); assert.equal(reviewed.sourceLabel, "升年级承接");
    assert.equal(reviewed.historyRevenue, null); assert.equal(reviewed.historyStudents, null, "review approval does not fabricate source numbers");
  }
  const ordinaryZero = project({ basisRows: zeroYear() }).get("sku-1");
  assert.equal(ordinaryZero.historyState, "value"); assert.equal(ordinaryZero.historyRevenue, 0); assert.equal(ordinaryZero.historyStudents, 0);
  assert.equal(ordinaryZero.sourceLabel, "基准已有");
});

test("empty history requires all four quarters and explicit matched approval for every unknown actual slot", () => {
  const openingSources = [promotion()], basisIssues = [emptySlotIssue()];
  for (const omitted of seasons) {
    const result = project({ openingSources, basisRows: emptySlotRows().filter(row => row.season !== omitted), basisIssues }).get("sku-1");
    assert.equal(result.historyState, "incomplete", omitted); assert.equal(result.historyRevenue, null); assert.equal(result.historyStudents, null);
  }
  const notReviewed = project({ openingSources, basisRows: emptySlotRows() }).get("sku-1");
  assert.notEqual(notReviewed.historyState, "empty"); assert.equal(notReviewed.historyRevenue, null); assert.equal(notReviewed.historyStudents, null);
  const twoUnknown = emptySlotRows().map(row => row.season === "秋季" ? { ...row, method: "ACTUAL_UNAVAILABLE", forecast_revenue: null, forecast_total: null } : row);
  assert.notEqual(project({ openingSources, basisRows: twoUnknown, basisIssues }).get("sku-1").historyState, "empty");
  for (const basisStatus of ["loading", "error"]) {
    assert.equal(project({ openingSources, basisRows: emptySlotRows(), basisIssues, basisStatus }).get("sku-1").historyState, basisStatus);
  }
});

test("empty-slot approval is scoped to exact classification, issue kind, severity, five dimensions and quarter", () => {
  const valid = emptySlotIssue(), invalid = [
    { ...valid, code: "CURRENT_SOURCE_MISSING" }, { ...valid, severity: "WARNING" },
    { ...valid, detail: {} }, { ...valid, detail: { classification: "OTHER" } },
    { ...valid, season: "秋季" }, { ...valid, season: undefined }, { ...valid, dims: undefined },
    ...Object.keys(valid.dims).flatMap(key => [{ ...valid, dims: { ...valid.dims, [key]: "不同" } },
      { ...valid, dims: Object.fromEntries(Object.entries(valid.dims).filter(([name]) => name !== key)) }]),
  ];
  for (const issue of invalid) {
    const result = project({ openingSources: [promotion()], basisRows: emptySlotRows(), basisIssues: [issue] }).get("sku-1");
    assert.notEqual(result.historyState, "empty", JSON.stringify(issue));
    assert.equal(result.historyRevenue, null); assert.equal(result.historyStudents, null);
  }
});

test("one positive or negative fact prevents empty classification even when annual values cancel to zero", () => {
  const openingSources = [promotion()], basisIssues = [emptySlotIssue()];
  for (const field of ["forecast_revenue", "forecast_total"]) {
    for (const amount of [-1, 1]) {
      const nonzero = zeroYear().map((row, index) => index === 1 ? { ...row, [field]: amount } : row);
      const result = project({ openingSources, basisRows: nonzero }).get("sku-1");
      assert.equal(result.historyState, "value", `${field}=${amount}`); assert.equal(result[field === "forecast_revenue" ? "historyRevenue" : "historyStudents"], amount);
      const reviewed = emptySlotRows().map((row, index) => index === 1 ? { ...row, [field]: amount } : row);
      assert.notEqual(project({ openingSources, basisRows: reviewed, basisIssues }).get("sku-1").historyState, "empty");
    }
    const cancelling = zeroYear().map((row, index) => ({ ...row, [field]: [1, -1, 0, 0][index] }));
    const result = project({ openingSources, basisRows: cancelling }).get("sku-1");
    assert.equal(result.historyState, "value"); assert.equal(result.historyRevenue, 0); assert.equal(result.historyStudents, 0);
  }
});

test("source labels prioritize manual additions, then explicit grade-promotion lineage, then exact history", () => {
  const openingSources = [{ ...sku({ grade: "七年级" }), target_id: "sku-1", students: 12, exclusion: null }];
  assert.equal(project({ openingSources }).get("sku-1").sourceLabel, "升年级承接");
  assert.equal(project({ combinations: [sku({ added: true })], openingSources }).get("sku-1").sourceLabel, "手工新增");
  assert.equal(project({ combinations: [sku({ added: true })], basisRows: null, basisStatus: "error" }).get("sku-1").sourceLabel, "手工新增");
  assert.equal(project({ openingSources, basisRows: [], basisStatus: "loading" }).get("sku-1").sourceLabel, "升年级承接");
  for (const source of [{ ...openingSources[0], target_id: "another-sku" }, { ...openingSources[0], target_id: null }, { ...openingSources[0], grade: "八年级" }]) {
    assert.equal(project({ openingSources: [source], basisRows: [] }).get("sku-1").sourceLabel, "来源待核对");
  }
});

test("summer evidence uses only the exact combination and summer quarter", () => {
  const calculation = next([{ combination_id: "another-sku", season: "暑假", old_students: 999 },
    { combination_id: "sku-1", season: "秋季", old_students: 888 }, { combination_id: "sku-1", season: "暑假", old_students: "12" }]);
  const result = project({ calculation }).get("sku-1");
  assert.equal(result.summerState, "value"); assert.equal(result.summerOld, 12);
  for (const missing of [undefined, next([]), next([{ combination_id: "sku-1", season: "暑假", old_students: null }]), next([{ combination_id: "sku-1", season: "暑假", old_students: "bad" }])]) {
    const result = buildNextYearSkuEvidence([sku()], [], missing, year(), "ready").get("sku-1");
    assert.equal(result.summerState, "unknown"); assert.equal(result.summerOld, null);
  }
});

test("excluded and stale summer counts never show a saved result as a fresh local calculation", () => {
  const stale = project({ stale: true }).get("sku-1");
  assert.equal(stale.summerState, "pending"); assert.equal(stale.summerOld, null); assert.equal(stale.historyRevenue, 40000);
  const excluded = project({ combinations: [sku({ enabled: false, opening_carryover_yuan: "123.45" })], stale: true }).get("sku-1");
  assert.equal(excluded.summerState, "excluded"); assert.equal(excluded.summerOld, null); assert.equal(excluded.openingCarryover, 123.45);
  const fresh = project().get("sku-1"); assert.equal(fresh.summerState, "value"); assert.equal(fresh.summerOld, 8);
});

test("opening carryover keeps explicit zero and signed amounts while malformed or absent values stay unknown", () => {
  for (const [value, expected] of [["0", 0], ["-0.03", -.03], ["1234.567890", 1234.56789], [null, null], [undefined, null], ["", null], ["bad", null]]) {
    assert.equal(project({ combinations: [sku({ opening_carryover_yuan: value })] }).get("sku-1").openingCarryover, expected);
  }
});

test("projection leaves every source, local input and saved calculation unchanged and performs no requests", () => {
  const combinations = freeze([sku(), sku({ id: "manual", added: true, enabled: false })]);
  const openingSources = freeze([{ ...sku({ grade: "七年级" }), target_id: "sku-1", students: 12, exclusion: null }]);
  const calculation = freeze(next()), basisRows = freeze(emptySlotRows()), basisIssues = freeze([emptySlotIssue()]);
  const before = JSON.stringify([combinations, openingSources, calculation, basisRows, basisIssues]);
  const savedFetch = globalThis.fetch; let requests = 0;
  globalThis.fetch = async () => { requests++; throw new Error("Display evidence must not request or mutate a budget"); };
  try {
    for (const basisStatus of ["ready", "error", "loading"]) buildNextYearSkuEvidence(combinations, openingSources, calculation, basisRows, basisStatus, true, basisIssues);
    assert.equal(requests, 0); assert.equal(JSON.stringify([combinations, openingSources, calculation, basisRows, basisIssues]), before);
    assert.equal(buildNextYearSkuEvidence([], [], undefined, null, "ready").size, 0);
  } finally { globalThis.fetch = savedFetch; }
});
