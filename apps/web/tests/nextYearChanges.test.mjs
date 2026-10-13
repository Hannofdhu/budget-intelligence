// Pure in-memory scenarios only. No live budget, database, BI request or saved version.
import test from "node:test";
import assert from "node:assert/strict";
import { nextYearEditKey as key, applyNextYearEdits } from "../src/nextYearApi.ts";
import { summarizeNextYearChanges, undoNextYearChange, nextYearEditTask } from "../src/nextYearChanges.ts";

const combo = (id = "c1", fields = {}) => ({ id, campus: "西直门", track: "智慧空间", product: "常规班", grade: "七年级", subject: "数学", entry: true, enabled: true, source_entry_weight: 100, source_new_by_season: { 暑假: 100, 秋季: 10, 寒假: 10, 春季: 10 }, opening_carryover_yuan: "0", ...fields });
const inputs = () => ({ fiscal_year: 2028, source_as_of: "2026-10-05", target_revenue_yuan: "1000000", track_targets: { 智慧空间: null }, entry_total: 100, tax_factor: "1.06", combinations: [combo()],
  rates: [{ id: "r1", season: "秋季", campus: "西直门", track: "智慧空间", product: "常规班", grade: "七年级", renewal_ppm: 800000, share_ppm: 200000 }],
  finance: [{ id: "f1", combination_id: "c1", season: "秋季", price_yuan: "3000", discount_ppm: 900000, recognition_ppm: 1000000 }],
  class_sizes: [{ track: "智慧空间", product: "常规班", minimum: 10, planned_class_size: 20 }],
  plans: [{ combination_id: "c1", season: "秋季", students: 15 }], opening_sources: [], notes: [] });
const items = summary => summary.groups.flatMap(group => group.items);
const get = (summary, path) => items(summary).find(item => item.key === key(...path));
const addBusiness = (value, id, dimensions = {}) => {
  const added = combo(id, { added: true, ...dimensions });
  value.combinations.push(added);
  const rate = { ...value.rates[0], id: `${id}-rate`, ...Object.fromEntries(["campus", "track", "product", "grade"].map(field => [field, added[field]])) };
  if (!value.rates.some(row => ["campus", "track", "product", "grade"].every(field => row[field] === rate[field]))) value.rates.push(rate);
  value.finance.push({ ...value.finance[0], id: `${id}-finance`, combination_id: id });
  value.plans.push({ combination_id: id, season: "秋季", students: 0, locked: true, reason: "新业务" });
  if (!value.class_sizes.some(row => row.track === added.track && row.product === added.product)) value.class_sizes.push({ track: added.track, product: added.product, minimum: 1, planned_class_size: 12 });
  if (!Object.hasOwn(value.track_targets, added.track)) value.track_targets[added.track] = null;
  return added;
};

test("saved-baseline diff includes staged scalars, raw edits and state controls without mutating inputs", () => {
  const baseline = inputs(), staged = structuredClone(baseline);
  staged.entry_total = 120; staged.rates[0].renewal_ppm = 850000; staged.finance[0].price_yuan = "3200";
  staged.class_sizes[0].planned_class_size = 22; staged.combinations[0].enabled = false; staged.combinations[0].entry_override = false;
  staged.combinations[0].entry_locked = true; staged.rates[0].locked = true; staged.plans[0].locked = true;
  const raw = { [key("tax")]: "1.05" }, before = JSON.stringify([baseline, staged, raw]);
  const summary = summarizeNextYearChanges(baseline, staged, raw);
  assert.equal(summary.totalCount, 10);
  assert.equal(get(summary, ["entry"]).beforeNumber, 100); assert.equal(get(summary, ["entry"]).afterNumber, 120);
  assert.equal(get(summary, ["rates", "r1", "renewal_ppm"]).beforeNumber, 80); assert.equal(get(summary, ["rates", "r1", "renewal_ppm"]).afterNumber, 85);
  for (const path of [["enabled", "c1"], ["entry-scope", "c1"], ["entry-lock", "c1"], ["rate-lock", "r1"], ["plan-lock", "c1", "秋季"]]) assert.ok(get(summary, path));
  assert.equal(JSON.stringify([baseline, staged, raw]), before);
});

test("typed value returning to the saved value is not a change even after stage absorbed an earlier edit", () => {
  const baseline = inputs(), staged = structuredClone(baseline); staged.entry_total = 120;
  const summary = summarizeNextYearChanges(baseline, staged, { [key("entry")]: "100.0", [key("tax")]: "1.0600", [key("target")]: "1e6" });
  assert.equal(summary.totalCount, 0);
  assert.equal(summarizeNextYearChanges(baseline, baseline, { [key("bounds", "r1", "renewal_lower_ppm")]: "", [key("track-target", "智慧空间")]: "" }).totalCount, 0);
});

test("illegal raw input is immediately visible, remains verbatim and never contributes a numeric scale", () => {
  const baseline = inputs(), raw = { [key("entry")]: "-200", [key("rates", "r1", "renewal_ppm")]: "110", [key("finance", "f1", "discount_ppm")]: "88.11" };
  const summary = summarizeNextYearChanges(baseline, baseline, raw);
  assert.ok(items(summary).every(item => item.invalid));
  assert.match(get(summary, ["entry"]).afterDisplay, /-200/);
  assert.ok(summary.groups.every(group => group.scale === undefined));
  assert.equal(raw[key("entry")], "-200");
});

test("malformed keys and unknown fields stay visible and can be undone without changing valid inputs", () => {
  const baseline = inputs(), edits = { broken: "abc", [key("not-editable")]: "123" };
  const summary = summarizeNextYearChanges(baseline, baseline, edits);
  assert.equal(summary.totalCount, 2); assert.ok(items(summary).every(item => item.invalid));
  const undone = undoNextYearChange(baseline, baseline, edits, items(summary).find(item => item.key === "broken"));
  assert.deepEqual(undone.edits, { [key("not-editable")]: "123" }); assert.deepEqual(undone.inputs, baseline);
});

test("unknown is not zero, cleared bounds restore automatic values and valid numeric zero remains meaningful", () => {
  const baseline = inputs(); baseline.target_revenue_yuan = null; baseline.rates[0].renewal_lower_ppm = 750000;
  const summary = summarizeNextYearChanges(baseline, baseline, { [key("target")]: "0", [key("entry-count", "c1")]: "0", [key("bounds", "r1", "renewal_lower_ppm")]: "" });
  assert.equal(get(summary, ["target"]).beforeNumber, null); assert.equal(get(summary, ["target"]).afterNumber, 0);
  assert.equal(get(summary, ["entry-count", "c1"]).beforeNumber, null); assert.equal(get(summary, ["entry-count", "c1"]).afterNumber, 0);
  assert.match(get(summary, ["bounds", "r1", "renewal_lower_ppm"]).afterDisplay, /恢复默认/);
  assert.ok(summary.groups.every(group => group.scale === undefined));
});

test("grade assignment has its true saved before value and does not double count a grade plus detail", () => {
  const baseline = inputs(); baseline.combinations[0].entry_students = 70;
  baseline.combinations.push(combo("c2", { subject: "英语", entry_students: 30 }));
  const summary = summarizeNextYearChanges(baseline, baseline, { [key("entry-grade", "七年级")]: "120", [key("entry-count", "c1")]: "80" });
  assert.equal(get(summary, ["entry-grade", "七年级"]).beforeNumber, 100);
  assert.match(get(summary, ["entry-grade", "七年级"]).beforeDisplay, /100.*人次/);
  assert.equal(summary.groups.find(group => group.category === "entry_detail").scale, undefined);
});

test("manual override of an automatic plan is meaningful even when the input count equals its prior seed", () => {
  const baseline = inputs(), raw = { [key("manual", "c1", "秋季")]: "15" };
  const summary = summarizeNextYearChanges(baseline, baseline, raw), item = get(summary, ["manual", "c1", "秋季"]);
  assert.equal(item.beforeDisplay, "自动计算"); assert.equal(item.afterNumber, 15); assert.equal(item.task, 3);
  assert.equal(undoNextYearChange(baseline, baseline, raw, item).edits[key("manual", "c1", "秋季")], undefined);
});

test("route mapping covers every ordinary and state change section without legacy fallthrough", () => {
  for (const section of ["target", "track-target", "business", "enabled"]) assert.equal(nextYearEditTask(key(section, "id")), 1);
  for (const section of ["entry", "entry-count", "entry-grade", "entry-scope", "entry-lock"]) assert.equal(nextYearEditTask(key(section, "id")), 2);
  for (const section of ["rates", "bounds", "plan", "manual", "rate-lock", "plan-lock"]) assert.equal(nextYearEditTask(key(section, "id")), 3);
  for (const section of ["finance", "class", "class-size", "tax"]) assert.equal(nextYearEditTask(key(section, "id")), 4);
  assert.equal(nextYearEditTask("not json"), 1);
});

test("single scalar undo resets staged and raw forms to baseline while preserving another edit", () => {
  const baseline = inputs(), staged = structuredClone(baseline); staged.entry_total = 120; staged.tax_factor = "1.05";
  const edits = { [key("entry")]: "125", [key("rates", "r1", "renewal_ppm")]: "83" }, before = JSON.stringify([baseline, staged, edits]);
  const item = get(summarizeNextYearChanges(baseline, staged, edits), ["entry"]);
  const undone = undoNextYearChange(baseline, staged, edits, item);
  assert.equal(undone.inputs.entry_total, 100); assert.equal(undone.inputs.tax_factor, "1.05");
  assert.deepEqual(undone.edits, { [key("rates", "r1", "renewal_ppm")]: "83" });
  assert.equal(JSON.stringify([baseline, staged, edits]), before);
});

test("undo a staged manual plan restores count, original lock and reason as one override", () => {
  const baseline = inputs(), staged = structuredClone(baseline);
  staged.plans[0] = { ...staged.plans[0], students: 25, locked: true, reason: "预算手动调节" };
  const item = get(summarizeNextYearChanges(baseline, staged, {}), ["manual", "c1", "秋季"]);
  const undone = undoNextYearChange(baseline, staged, {}, item);
  assert.deepEqual(undone.inputs.plans, baseline.plans);
  assert.equal(summarizeNextYearChanges(baseline, undone.inputs, undone.edits).totalCount, 0);
});

test("undo grade allocation restores its saved distribution and locks, keeping independent raw details", () => {
  const baseline = inputs(); baseline.combinations[0].entry_students = 100; baseline.combinations[0].entry_locked = true;
  const staged = applyNextYearEdits(baseline, { [key("entry-grade", "七年级")]: "120" }).inputs;
  const edits = { [key("entry-grade", "七年级")]: "130", [key("tax")]: "1.05" };
  const item = get(summarizeNextYearChanges(baseline, staged, edits), ["entry-grade", "七年级"]);
  const undone = undoNextYearChange(baseline, staged, edits, item);
  assert.deepEqual(undone.inputs.combinations, baseline.combinations); assert.deepEqual(undone.edits, { [key("tax")]: "1.05" });
});

test("undo newly introduced nullable fields removes the property rather than writing a false zero", () => {
  const baseline = inputs(), staged = structuredClone(baseline); delete baseline.class_sizes[0].planned_class_size;
  staged.rates[0].renewal_lower_ppm = 750000;
  let summary = summarizeNextYearChanges(baseline, staged, {});
  let result = undoNextYearChange(baseline, staged, {}, get(summary, ["bounds", "r1", "renewal_lower_ppm"]));
  assert.equal(Object.hasOwn(result.inputs.rates[0], "renewal_lower_ppm"), false);
  summary = summarizeNextYearChanges(baseline, result.inputs, {});
  result = undoNextYearChange(baseline, result.inputs, {}, get(summary, ["class-size", 0]));
  assert.equal(Object.hasOwn(result.inputs.class_sizes[0], "planned_class_size"), false);
});

test("new-business initialization is represented once with all dimensions and explicit task", () => {
  const baseline = inputs(), staged = structuredClone(baseline); addBusiness(staged, "added", { track: "新赛道", product: "云帆", grade: "八年级" });
  const summary = summarizeNextYearChanges(baseline, staged, {});
  assert.equal(summary.totalCount, 1); const item = items(summary)[0];
  assert.equal(item.key, key("business", "added")); assert.equal(item.task, 1); assert.match(item.label, /新赛道.*云帆.*八年级.*数学/);
  assert.equal(item.beforeDisplay, "未加入"); assert.equal(item.afterDisplay, "加入预算");
});

test("undo new business removes only its unshared additions and preserves another new business and raw changes", () => {
  const baseline = inputs(), staged = structuredClone(baseline);
  addBusiness(staged, "a", { track: "新赛道A", product: "云帆" }); addBusiness(staged, "b", { track: "新赛道B", product: "实验班" });
  staged.entry_total = 180;
  const raw = { [key("finance", "a-finance", "price_yuan")]: "3500", [key("rates", "a-rate", "renewal_ppm")]: "60", [key("manual", "a", "秋季")]: "30", [key("finance", "b-finance", "price_yuan")]: "3600", [key("class-size", 2)]: "18", [key("tax")]: "1.05" };
  const before = JSON.stringify([baseline, staged, raw]), summary = summarizeNextYearChanges(baseline, staged, raw);
  const result = undoNextYearChange(baseline, staged, raw, get(summary, ["business", "a"]));
  assert.ok(!result.inputs.combinations.some(row => row.id === "a")); assert.ok(result.inputs.combinations.some(row => row.id === "b"));
  assert.ok(!result.inputs.finance.some(row => row.combination_id === "a")); assert.ok(!result.inputs.plans.some(row => row.combination_id === "a"));
  assert.ok(!result.inputs.rates.some(row => row.id === "a-rate")); assert.ok(result.inputs.rates.some(row => row.id === "b-rate"));
  assert.equal(Object.hasOwn(result.inputs.track_targets, "新赛道A"), false); assert.equal(Object.hasOwn(result.inputs.track_targets, "新赛道B"), true);
  assert.equal(result.inputs.entry_total, 180); assert.equal(result.inputs.class_sizes.length, 2);
  assert.deepEqual(result.edits, { [key("finance", "b-finance", "price_yuan")]: "3600", [key("class-size", 1)]: "18", [key("tax")]: "1.05" });
  assert.equal(JSON.stringify([baseline, staged, raw]), before);
});

test("undo new business retains parameters shared by another subject and baseline parameters", () => {
  const baseline = inputs(), staged = structuredClone(baseline);
  addBusiness(staged, "a", { product: "云帆" }); addBusiness(staged, "b", { product: "云帆", subject: "英语" });
  const summary = summarizeNextYearChanges(baseline, staged, {}), result = undoNextYearChange(baseline, staged, {}, get(summary, ["business", "a"]));
  assert.ok(result.inputs.rates.some(row => row.id === "a-rate")); // Same campus/track/product/grade, so b still uses this rate.
  assert.ok(result.inputs.class_sizes.some(row => row.product === "云帆"));
  assert.deepEqual(result.inputs.rates[0], baseline.rates[0]); assert.deepEqual(result.inputs.class_sizes[0], baseline.class_sizes[0]);
});

test("state-only undo restores the saved state without discarding an unrelated staged scalar", () => {
  const baseline = inputs(), staged = structuredClone(baseline);
  staged.combinations[0].enabled = false; staged.rates[0].locked = true; staged.entry_total = 130;
  let summary = summarizeNextYearChanges(baseline, staged, {});
  let result = undoNextYearChange(baseline, staged, {}, get(summary, ["enabled", "c1"]));
  assert.equal(result.inputs.combinations[0].enabled, true); assert.equal(result.inputs.entry_total, 130);
  summary = summarizeNextYearChanges(baseline, result.inputs, {});
  result = undoNextYearChange(baseline, result.inputs, {}, get(summary, ["rate-lock", "r1"]));
  assert.equal(Object.hasOwn(result.inputs.rates[0], "locked"), false); assert.equal(result.inputs.entry_total, 130);
});

test("undo staged plan lock also removes the same-row raw manual override but preserves independent work", () => {
  const baseline = inputs(), staged = structuredClone(baseline);
  staged.plans[0] = { ...staged.plans[0], students: 25, locked: true, reason: "预算手动调节" };
  const edits = { [key("manual", "c1", "秋季")]: "30", [key("manual", "c1", "春季")]: "77", [key("tax")]: "1.05" };
  const before = JSON.stringify([baseline, staged, edits]);
  const item = get(summarizeNextYearChanges(baseline, staged, edits), ["plan-lock", "c1", "秋季"]);
  const result = undoNextYearChange(baseline, staged, edits, item);
  assert.deepEqual(result.edits, { [key("manual", "c1", "春季")]: "77", [key("tax")]: "1.05" });
  assert.equal(result.inputs.plans[0].students, 25); // This is the independently staged count, not the undone raw 30.
  assert.equal(Object.hasOwn(result.inputs.plans[0], "locked"), false); assert.equal(Object.hasOwn(result.inputs.plans[0], "reason"), false);
  const projected = applyNextYearEdits(result.inputs, result.edits).inputs;
  assert.ok(projected); assert.equal(Boolean(projected.plans.find(row => row.season === "秋季").locked), false);
  assert.equal(projected.plans.find(row => row.season === "春季").students, 77); assert.equal(projected.plans.find(row => row.season === "春季").locked, true);
  assert.equal(projected.tax_factor, "1.05"); assert.equal(JSON.stringify([baseline, staged, edits]), before);
});

test("undo staged entry lock removes only its associated raw count so projection cannot re-lock it", () => {
  const baseline = inputs(); baseline.combinations.push(combo("c2", { subject: "英语" }));
  const staged = structuredClone(baseline); staged.combinations[0].entry_students = 120; staged.combinations[0].entry_locked = true;
  const edits = { [key("entry-count", "c1")]: "125", [key("entry-count", "c2")]: "30", [key("rates", "r1", "renewal_ppm")]: "85" };
  const before = JSON.stringify([baseline, staged, edits]);
  const item = get(summarizeNextYearChanges(baseline, staged, edits), ["entry-lock", "c1"]);
  const result = undoNextYearChange(baseline, staged, edits, item);
  assert.deepEqual(result.edits, { [key("entry-count", "c2")]: "30", [key("rates", "r1", "renewal_ppm")]: "85" });
  assert.equal(result.inputs.combinations[0].entry_students, 120); assert.equal(Object.hasOwn(result.inputs.combinations[0], "entry_locked"), false);
  const projected = applyNextYearEdits(result.inputs, result.edits).inputs;
  assert.ok(projected); assert.equal(Boolean(projected.combinations[0].entry_locked), false);
  assert.equal(projected.combinations[1].entry_students, 30); assert.equal(projected.combinations[1].entry_locked, true);
  assert.equal(projected.rates[0].renewal_ppm, 850000); assert.equal(JSON.stringify([baseline, staged, edits]), before);
});

test("undo lock on a newly introduced plan removes the override metadata without deleting its count", () => {
  const baseline = inputs(), staged = structuredClone(baseline);
  staged.plans.push({ combination_id: "c1", season: "春季", students: 12, locked: true, reason: "手动招生计划" });
  const edits = { [key("manual", "c1", "春季")]: "15", [key("entry")]: "130" };
  const item = get(summarizeNextYearChanges(baseline, staged, edits), ["plan-lock", "c1", "春季"]);
  const result = undoNextYearChange(baseline, staged, edits, item), plan = result.inputs.plans.find(row => row.season === "春季");
  assert.equal(plan.students, 12); assert.equal(Object.hasOwn(plan, "locked"), false); assert.equal(Object.hasOwn(plan, "reason"), false);
  assert.deepEqual(result.edits, { [key("entry")]: "130" });
  assert.equal(Boolean(applyNextYearEdits(result.inputs, result.edits).inputs.plans.find(row => row.season === "春季").locked), false);
});
