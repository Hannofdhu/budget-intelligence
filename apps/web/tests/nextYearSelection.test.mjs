// Synthetic, pure review helpers only: no network, source workbook, or business writes.
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  applyNextYearClassRepairSelection as apply, nextYearActionKey as key,
  assertNextYearSelectionPreview as verify, nextYearBalanceActions as balanceActions,
} from "../src/nextYearSelection.ts";

const seasons = ["暑假", "秋季", "寒假", "春季"];
const clone = value => structuredClone(value);
const selected = actions => new Set(actions.map(key));
const inputs = () => {
  const combinations = ["A", "B"].map((track, index) => ({ id: `combo-${index}`, track, product: "普通班", campus: "示例校区",
    grade: index ? "八年级" : "七年级", subject: "数学", entry: true, enabled: true, source_entry_weight: index ? 5 : 95,
    source_new_by_season: Object.fromEntries(seasons.map(season => [season, 1])), opening_carryover_yuan: "0" }));
  return { fiscal_year: 2028, source_as_of: "2026-10-01", target_revenue_yuan: "1000", track_targets: { A: null, B: null },
    entry_total: 100, tax_factor: "1.06", combinations,
    rates: combinations.flatMap(combo => seasons.map((season, i) => ({ id: `${combo.id}-rate-${i}`, season, track: combo.track,
      product: combo.product, campus: combo.campus, grade: combo.grade, renewal_ppm: 800000, share_ppm: 250000 }))),
    finance: combinations.flatMap(combo => seasons.map((season, i) => ({ id: `${combo.id}-finance-${i}`, combination_id: combo.id,
      season, price_yuan: "100", discount_ppm: 900000, recognition_ppm: 1000000 }))),
    plans: combinations.flatMap(combo => seasons.map(season => ({ combination_id: combo.id, season, students: 1 }))),
    class_sizes: [{ track: "A", product: "普通班", minimum: 10 }, { track: "B", product: "普通班", minimum: 10 }],
    opening_sources: [], notes: [{ code: "FROZEN_NOTE" }] };
};
const calculation = (data, allocation = [95, 5]) => {
  const totals = { revenue_yuan: "900", target_revenue_yuan: "1000", gap_yuan: "100", total_students: 100,
    old_students: 0, new_students: 100, attainment: ".9" };
  return { fiscal_year: data.fiscal_year, rule_versions: {}, annual: totals,
    quarters: seasons.map(season => ({ season, ...totals })), tracks: [], campuses: [],
    rows: data.combinations.flatMap(combo => seasons.map(season => ({ combination_id: combo.id, season,
      track: combo.track, product: combo.product, campus: combo.campus, grade: combo.grade, subject: combo.subject, entry: true,
      old_students: 0, new_students: 20, total_students: 20, revenue_yuan: "100", price_yuan: "100", discount_ppm: 900000,
      renewal_ppm: 800000, share_ppm: 250000, class_size_status: "PASS", planned_new_applied: false }))),
    entry_details: data.combinations.map((combo, i) => ({ combination_id: combo.id, campus: combo.campus, track: combo.track,
      product: combo.product, grade: combo.grade, subject: combo.subject, students: allocation[i], locked: false })),
    entry_allocations: [], opening: { spring_students: 0, eligible_students: 0, graduated_students: 0, subject_ended_students: 0,
      summer_old_students: 0, carryover_yuan: "0" }, issues: [], complete: true, publication_available: false };
};
function proposal() {
  const original = inputs(), proposed = clone(original);
  proposed.combinations[0].entry_students = 80;
  proposed.combinations[1].entry_students = 20;
  proposed.plans[1] = { ...proposed.plans[1], students: 19, locked: true, reason: "建议未来招收 19 人" };
  proposed.rates[0].renewal_ppm = 850000;
  proposed.rates[0].renewal_upper_ppm = 900000;
  const actions = [
    { kind: "ENTRY_REALLOCATION", combination_id: "combo-0", season: "暑假", field: "entry_students", before: 95, before_input: null, after: 80, reason: "调出" },
    { kind: "ENTRY_REALLOCATION", combination_id: "combo-1", season: "暑假", field: "entry_students", before: 5, before_input: null, after: 20, reason: "调入" },
    { kind: "NEW_STUDENT_TARGET", combination_id: "combo-0", season: "秋季", field: "plans.students", before: 0, before_input: 1, after: 19, reason: "成班需求" },
    { kind: "RENEWAL_ADJUSTMENT", rate_id: "combo-0-rate-0", field: "renewal_ppm", before: 800000, after: 850000, reason: "续班建议" },
    { kind: "RATE_BOUNDARY", rate_id: "combo-0-rate-0", field: "renewal_upper_ppm", before: 850000, after: 900000, reason: "扩大边界" },
  ];
  return { original, proposed, actions };
}

test("action keys retain the agreed kind-index contract", () => {
  assert.equal(key({ kind: "NEW_STUDENT_TARGET" }, 2), "NEW_STUDENT_TARGET-2");
});
test("empty selection is an independent clone and neither source is mutated", () => {
  const { original, proposed, actions } = proposal(), snapshot = JSON.stringify([original, proposed]);
  const merged = apply(original, proposed, actions, new Set());
  assert.deepEqual(merged, original); assert.notEqual(merged, original);
  merged.notes.push({ changed: true });
  assert.equal(JSON.stringify([original, proposed]), snapshot);
});
test("all selected is byte-for-byte the proposed inputs, with effective manual plan metadata", () => {
  const { original, proposed, actions } = proposal();
  const merged = apply(original, proposed, actions, selected(actions));
  assert.equal(JSON.stringify(merged), JSON.stringify(proposed));
  assert.notEqual(merged, proposed); assert.equal(merged.plans[1].locked, true); assert.match(merged.plans[1].reason, /19/);
});
test("half of an entry transfer is rejected rather than silently auto-filled", () => {
  const { original, proposed, actions } = proposal();
  for (const index of [0, 1]) assert.throws(() => apply(original, proposed, actions, new Set([key(actions[index], index)])), /入口合计冲突/);
});
test("selecting a student target copies its complete plan, not students alone", () => {
  const { original, proposed, actions } = proposal();
  const merged = apply(original, proposed, actions, new Set([key(actions[2], 2)]));
  assert.deepEqual(merged.plans[1], proposed.plans[1]);
  assert.deepEqual(merged.combinations, original.combinations); assert.deepEqual(merged.rates, original.rates);
});
test("unselected plans preserve old students, lock and reason exactly", () => {
  const { original, proposed, actions } = proposal();
  const merged = apply(original, proposed, actions, new Set([key(actions[3], 3)]));
  assert.deepEqual(merged.plans, original.plans);
  assert.equal(merged.rates[0].renewal_ppm, 850000); assert.equal(merged.rates[0].renewal_upper_ppm, undefined);
});
test("rate edits use rate_id, even without a combination_id, and change only the chosen field", () => {
  const { original, proposed, actions } = proposal();
  const merged = apply(original, proposed, actions, new Set([key(actions[4], 4)]));
  assert.equal(merged.rates[0].renewal_upper_ppm, 900000); assert.equal(merged.rates[0].renewal_ppm, 800000);
  assert.deepEqual(merged.rates.slice(1), original.rates.slice(1));
});
test("explicit pinning is preserved when displayed before and after boundary values are identical", () => {
  const original = inputs(), proposed = clone(original); proposed.rates[0].renewal_upper_ppm = 850000;
  const actions = [{ kind: "RATE_BOUNDARY", rate_id: original.rates[0].id, field: "renewal_upper_ppm", before: 850000, after: 850000, reason: "显式保留上限" }];
  assert.deepEqual(apply(original, proposed, actions, selected(actions)), proposed);
});
test("unknown selections and fields fail closed without changing input", () => {
  const { original, proposed, actions } = proposal(), snapshot = JSON.stringify(original);
  assert.throws(() => apply(original, proposed, actions, new Set(["removed-action"])), /勾选项已失效/);
  actions[0].field = "opening_carryover_yuan";
  assert.throws(() => apply(original, proposed, actions, selected(actions)), /字段无法识别/);
  assert.equal(JSON.stringify(original), snapshot);
});
test("hidden protected source, operating target, finance, minimum or business edits are refused", () => {
  const mutations = [value => { value.target_revenue_yuan = "2000"; }, value => { value.source_as_of = "2026-10-02"; },
    value => { value.notes = []; }, value => { value.tax_factor = "1.05"; }, value => { value.finance[0].price_yuan = "110"; },
    value => { value.class_sizes[0].minimum = 11; }, value => { value.combinations[0].enabled = false; },
    value => { value.combinations[0].source_entry_weight = 20; }, value => { value.combinations[0].opening_carryover_yuan = "1"; }];
  for (const mutate of mutations) { const { original, proposed, actions } = proposal(); mutate(proposed);
    assert.throws(() => apply(original, proposed, actions, selected(actions)), /未列出|业务范围/); }
});
test("unlisted editable changes are not smuggled into a full selection", () => {
  const { original, proposed, actions } = proposal(); proposed.rates[1].share_ppm = 260000;
  assert.throws(() => apply(original, proposed, actions, selected(actions)), /未列出的/);
});
test("preexisting user locks are never changed by entry, plan or rate suggestions", () => {
  for (const kind of ["entry", "plan", "rate"]) {
    const { original, proposed, actions } = proposal();
    if (kind === "entry") { original.combinations[0].entry_locked = true; proposed.combinations[0].entry_locked = true; }
    if (kind === "plan") { original.plans[1].locked = true; original.plans[1].reason = "原锁定"; }
    if (kind === "rate") { original.rates[0].locked = true; proposed.rates[0].locked = true; }
    assert.throws(() => apply(original, proposed, actions, selected(actions)), /已锁定/);
  }
});
test("duplicate recommendations and invalid percentage values are refused", () => {
  const { original, proposed, actions } = proposal();
  assert.throws(() => apply(original, proposed, [...actions, actions[3]], new Set()), /重复建议/);
  actions[3].after = proposed.rates[0].renewal_ppm = 850001;
  assert.throws(() => apply(original, proposed, actions, selected(actions)), /参数建议无效/);
});
test("authoritative full preview and a valid non-entry subset pass verification", () => {
  const { original, proposed, actions } = proposal(), before = calculation(original);
  assert.doesNotThrow(() => verify(original, before, proposed, actions, selected(actions), calculation(proposed, [80, 20])));
  const keys = new Set([key(actions[2], 2)]), merged = apply(original, proposed, actions, keys);
  assert.doesNotThrow(() => verify(original, before, merged, actions, keys, calculation(merged)));
});
test("preview rejects an implicit redistribution of an unselected entry even when the total still equals 100", () => {
  const { original, proposed, actions } = proposal(), keys = new Set([key(actions[2], 2)]);
  const merged = apply(original, proposed, actions, keys), changed = calculation(merged, [80, 20]);
  assert.equal(changed.entry_details.reduce((sum, row) => sum + row.students, 0), original.entry_total);
  assert.throws(() => verify(original, calculation(original), merged, actions, keys, changed), /未勾选项/);
});
test("incomplete, missing-season and wrong-year preview responses cannot be applied", () => {
  const { original, proposed, actions } = proposal(), before = calculation(original);
  const mutations = [value => { value.complete = false; }, value => { value.annual.revenue_yuan = null; },
    value => { value.rows.pop(); }, value => { value.rows[1] = clone(value.rows[0]); }, value => { value.fiscal_year = 2029; }];
  for (const mutate of mutations) { const value = calculation(proposed, [80, 20]); mutate(value);
    assert.throws(() => verify(original, before, proposed, actions, selected(actions), value), /未完成|未完整/); }
});
test("blocking and remaining class issues keep an otherwise complete preview non-applicable", () => {
  const { original, proposed, actions } = proposal(), before = calculation(original);
  for (const issue of [{ severity: "BLOCKING", code: "MISSING_PARAMETER", message: "参数未齐" },
    { severity: "WARNING", code: "BELOW_MINIMUM_CLASS_SIZE", message: "人数不足" },
    { severity: "WARNING", code: "ENABLED_COMBINATION_ALL_SEASONS_ZERO", message: "四季度为零" }]) {
    const after = calculation(proposed, [80, 20]); after.issues = [issue];
    assert.throws(() => verify(original, before, proposed, actions, selected(actions), after), /计算问题|成班问题/);
  }
});
test("balance recommendations expose entry, renewal, share and derived track targets with full parity", () => {
  const original = inputs(), proposed = clone(original);
  proposed.combinations[0].entry_students = 80; proposed.combinations[1].entry_students = 20;
  proposed.rates[0].renewal_ppm = 820000; proposed.rates[1].share_ppm = 270000;
  proposed.track_targets = { A: "600", B: "400" };
  const actions = balanceActions(original, proposed, calculation(original), calculation(proposed, [80, 20]));
  assert.equal(actions.filter(action => action.kind === "ENTRY_REALLOCATION").length, 2);
  assert.equal(actions.filter(action => action.kind === "TRACK_TARGET").length, 2);
  assert.ok(actions.some(action => action.field === "share_ppm"));
  assert.equal(JSON.stringify(apply(original, proposed, actions, selected(actions))), JSON.stringify(proposed));
});
test("partial derived target selection copies only its named track and preserves the manual annual target", () => {
  const original = inputs(), proposed = clone(original); proposed.track_targets = { A: "600", B: "400" };
  const actions = balanceActions(original, proposed, calculation(original), calculation(proposed));
  const merged = apply(original, proposed, actions, new Set([key(actions[0], 0)]));
  assert.deepEqual(merged.track_targets, { A: "600", B: null });
  assert.equal(merged.target_revenue_yuan, original.target_revenue_yuan);
});
test("sorted solver track keys are harmless and all-selected JSON still matches the exact response", () => {
  const original = inputs(), proposed = clone(original);
  original.track_targets = { B: null, A: null }; proposed.track_targets = { A: "600", B: "400" };
  const actions = balanceActions(original, proposed, calculation(original), calculation(proposed));
  assert.equal(JSON.stringify(apply(original, proposed, actions, selected(actions))), JSON.stringify(proposed));
});
test("balance helpers reject a hidden plan, discount or annual target change instead of accepting it", () => {
  for (const mutate of [value => { value.plans[1].students = 20; }, value => { value.finance[0].discount_ppm = 800000; },
    value => { value.target_revenue_yuan = "2000"; }]) {
    const original = inputs(), proposed = clone(original); mutate(proposed);
    assert.throws(() => balanceActions(original, proposed, calculation(original), calculation(proposed)), /未列出/);
  }
});
test("balance pinning of unchanged calculated entry counts is represented and stays zero-sum", () => {
  const original = inputs(), proposed = clone(original);
  proposed.combinations[0].entry_students = 95; proposed.combinations[1].entry_students = 5;
  const actions = balanceActions(original, proposed, calculation(original), calculation(proposed));
  assert.equal(actions.length, 2); assert.ok(actions.every(action => action.before === action.after));
  assert.deepEqual(apply(original, proposed, actions, selected(actions)), proposed);
});
