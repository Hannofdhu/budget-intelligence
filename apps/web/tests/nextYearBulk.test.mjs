// Pure staging only: no live data, requests, calculations or saved drafts.
import test from "node:test";
import assert from "node:assert/strict";
import { applyNextYearEdits, nextYearEditKey as key, nextYearSeasons } from "../src/nextYearApi.ts";
import { getNextYearBulkScope, buildNextYearBulkChange } from "../src/nextYearBulk.ts";

const combo = (id, overrides = {}) => ({ id, campus: "西/直门", track: "智慧空间", product: "云帆", grade: id === "c1" ? "七年级" : "八年级", subject: "数学", entry: id === "c1", enabled: true, source_entry_weight: 100,
  source_new_by_season: { 暑假: 100, 秋季: 10, 寒假: 10, 春季: 10 }, opening_carryover_yuan: "0", ...overrides });
function fixture() {
  const combinations = [combo("c1"), combo("c2")];
  const inputs = { fiscal_year: 2028, source_as_of: "2026-10-15", target_revenue_yuan: null, track_targets: { 智慧空间: null }, entry_total: 100, tax_factor: "1.06", combinations,
    rates: combinations.flatMap(row => nextYearSeasons.map(season => ({ id: `r-${row.id}-${season}`, campus: row.campus, track: row.track, product: row.product, grade: row.grade, season, renewal_ppm: 800000, share_ppm: 200000 }))),
    finance: combinations.flatMap(row => nextYearSeasons.map(season => ({ id: `f-${row.id}-${season}`, combination_id: row.id, season, price_yuan: "3000", discount_ppm: 900000, recognition_ppm: 1000000 }))),
    class_sizes: [{ track: "智慧空间", product: "云帆", minimum: 20, planned_class_size: 25 }], plans: [],
    opening_sources: [{ campus: "西/直门", track: "智慧空间", product: "云帆", grade: "八年级", subject: "数学", students: 50, target_id: "c2", exclusion: null }], notes: [] };
  const calculation = { rows: combinations.flatMap(row => nextYearSeasons.map(season => ({ ...row, combination_id: row.id, season, planned_new_applied: row.id === "c2" && ["暑假", "寒假"].includes(season) }))) };
  return { inputs, calculation };
}
const tsv = rows => rows.map(row => row.map(value => /[\t\n\r"]/.test(value) ? `"${value.replaceAll('"', '""')}"` : value).join("\t")).join("\n");
const build = (fixture, overrides) => buildNextYearBulkChange({ ...fixture, target: "rates", field: "renewal_ppm", mode: "uniform", value: "85", edits: {}, filters: { season: "秋季" }, ...overrides });

test("rates stage every filtered row, preserve pending edits and do not mutate sources", () => {
  const f = fixture(), edits = { [key("finance", "f-c1-秋季", "price_yuan")]: "3100" }, before = JSON.stringify([f, edits]);
  const result = build(f, { edits });
  assert.equal(result.changedCount, 2); assert.equal(result.edits[key("rates", "r-c1-秋季", "renewal_ppm")], "85");
  assert.equal(result.effectiveInputs.finance.find(row => row.id === "f-c1-秋季").price_yuan, "3100");
  assert.equal(result.inputs.rates[1].renewal_ppm, 800000); assert.equal(JSON.stringify([f, edits]), before);
});

test("true scope explains subject-shared rates and globally shared class average", () => {
  const f = fixture();
  const rates = getNextYearBulkScope("rates", f.inputs, f.calculation, { season: "暑假", subject: "语文" });
  assert.equal(rates.candidateCount, 2); assert.equal(rates.applicableCount, 1); assert.match(rates.scopeNote, /所有科目/);
  const classes = getNextYearBulkScope("class", f.inputs, f.calculation, { campus: "不存在校区", grade: "不存在年级", subject: "语文", season: "秋季" });
  assert.equal(classes.applicableCount, 1); assert.match(classes.scopeNote, /全部校区、年级、科目和季度/);
  assert.deepEqual(classes.identityHeaders, ["赛道", "产品"]); assert.deepEqual(classes.editableHeaders, ["班均（人/班）"]); assert.ok(classes.referenceHeaders.includes("参考·全年班量"));
  assert.equal(build(f, { target: "class", field: "minimum", value: "22", filters: { campus: "其他" } }).effectiveInputs.class_sizes[0].minimum, 22);
});

test("summer non-applicable rate cells stay blank and pasted old unchanged values are safe", () => {
  const f = fixture(), scope = getNextYearBulkScope("rates", f.inputs, f.calculation, { season: "暑假" });
  assert.deepEqual(scope.template[1].slice(-2), ["", ""]);
  const paste = scope.template.map(row => [...row]); paste[1][paste[1].length - 2] = "80"; paste[1][paste[1].length - 1] = "20"; paste[2][paste[2].length - 2] = "85";
  const result = build(f, { mode: "paste", paste: tsv(paste), filters: { season: "暑假" } });
  assert.equal(result.changedCount, 1);
  paste[1][paste[1].length - 2] = "90";
  assert.throws(() => build(f, { mode: "paste", paste: tsv(paste), filters: { season: "暑假" } }), /不适用/);
});

test("rates preserve old ID paste schema and dual metric edit", () => {
  const f = fixture();
  const result = build(f, { mode: "paste", paste: "参数ID\t续班率\t新生占比\nr-c1-秋季\t86%\t21.5" });
  assert.equal(result.changedCount, 2);
  assert.equal(result.effectiveInputs.rates.find(row => row.id === "r-c1-秋季").share_ppm, 215000);
});

test("finance uses independent SKU identity columns and original precision validation", () => {
  const f = fixture(), scope = getNextYearBulkScope("finance", f.inputs, f.calculation, { grade: "七年级", season: "秋季" }, "discount_ppm");
  assert.deepEqual(scope.template[0].slice(0, 6), ["校区", "赛道", "产品", "年级", "科目", "季度"]);
  const paste = scope.template.map(row => [...row]); paste[1][paste[1].length - 1] = "91.2%";
  assert.equal(build(f, { target: "finance", field: "discount_ppm", mode: "paste", paste: tsv(paste), filters: { grade: "七年级", season: "秋季" } }).effectiveInputs.finance[1].discount_ppm, 912000);
  assert.throws(() => build(f, { target: "finance", field: "discount_ppm", value: "91.23" }), /0.1个百分点/);
  assert.throws(() => build(f, { value: "85.001" }), /0.01个百分点/);
});

test("paste requires exact schema, exact identity, no duplicate or out-of-scope rows", () => {
  const f = fixture(), scope = getNextYearBulkScope("finance", f.inputs, f.calculation, { season: "秋季" }, "price_yuan");
  const rows = scope.template.map(row => [...row]); rows[1][rows[1].length - 1] = "3300";
  assert.throws(() => build(f, { target: "finance", field: "price_yuan", mode: "paste", paste: tsv([rows[0], rows[1], rows[1]]) }), /重复/);
  rows[2][0] = "西";
  assert.throws(() => build(f, { target: "finance", field: "price_yuan", mode: "paste", paste: tsv(rows) }), /不在当前筛选范围/);
  rows[0][1] = "业务";
  assert.throws(() => build(f, { target: "finance", field: "price_yuan", mode: "paste", paste: tsv(rows) }), /表头/);
});

test("quoted tabs and newlines in names match intact; blank values preserve edits", () => {
  const f = fixture(); f.inputs.combinations[0].campus = "西\t直\n门";
  const filters = { season: "秋季" }, scope = getNextYearBulkScope("finance", f.inputs, f.calculation, filters, "price_yuan");
  const rows = scope.template.map(row => [...row]); rows[1][rows[1].length - 1] = ""; rows[2][rows[2].length - 1] = "3400";
  const edits = { [key("finance", "f-c1-秋季", "price_yuan")]: "3200" };
  const result = build(f, { target: "finance", field: "price_yuan", mode: "paste", paste: tsv(rows), filters, edits });
  assert.equal(result.changedCount, 1); assert.equal(result.effectiveInputs.finance[1].price_yuan, "3200"); assert.equal(result.effectiveInputs.finance[5].price_yuan, "3400");
});

test("bounds and pending edits validate together, including explicit current-rate containment", () => {
  const f = fixture();
  assert.throws(() => build(f, { target: "bounds", field: "renewal_lower_ppm", value: "90" }), /下限不能高于上限|必须位于/);
  const edits = { [key("bounds", "r-c1-秋季", "renewal_upper_ppm")]: "82" };
  assert.throws(() => build(f, { edits, value: "85" }), /必须位于/);
  const wide = build(f, { target: "bounds", field: "renewal_upper_ppm", value: "100" });
  assert.equal(wide.effectiveInputs.rates[1].renewal_upper_ppm, 1000000);
});

test("plans follow existing zero-old applicability, manual lock behavior and entry-summer exclusion", () => {
  const f = fixture();
  assert.equal(getNextYearBulkScope("plans", f.inputs, f.calculation, {}, "planned_new").applicableCount, 2);
  assert.equal(getNextYearBulkScope("plans", f.inputs, f.calculation, {}, "manual_new").applicableCount, 7);
  const plan = build(f, { target: "plans", field: "planned_new", value: "0", filters: {} });
  assert.equal(plan.changedCount, 2); assert.equal(plan.effectiveInputs.plans[0].students, 0); assert.equal(plan.effectiveInputs.plans[0].locked, undefined);
  const manual = build(f, { target: "plans", field: "manual_new", value: "12", filters: { season: "秋季" } });
  assert.equal(manual.changedCount, 2); assert.equal(manual.effectiveInputs.plans[0].locked, true); assert.equal(manual.effectiveInputs.plans[0].reason, "预算手动调节");
  assert.throws(() => build(f, { target: "plans", field: "manual_new", value: "1.5" }), /整数/);
  assert.throws(() => build(f, { target: "plans", field: "manual_new", value: "12", filters: { grade: "七年级", season: "暑假" } }), /没有适用记录/);
});

test("SKU cannot drop carryover, all active SKUs or all available entry allocation", () => {
  const f = fixture(); f.inputs.combinations[1].opening_carryover_yuan = "12.3";
  assert.throws(() => build(f, { target: "sku", field: "enabled", value: "false", filters: { grade: "八年级" } }), /期初结转/);
  assert.throws(() => build(f, { target: "sku", field: "enabled", value: "false", filters: {} }), /至少保留/);
  assert.throws(() => build(f, { target: "sku", field: "enabled", value: "false", filters: { grade: "七年级" } }), /没有分配依据/);
  f.inputs.combinations[1].opening_carryover_yuan = "0";
  assert.equal(build(f, { target: "sku", field: "enabled", value: "不参与", filters: { grade: "八年级" } }).effectiveInputs.combinations[1].enabled, false);
  assert.throws(() => build(f, { target: "sku", field: "enabled", value: "0" }), /明确选择/);
});

test("entry setting does not modify department total and rejects partial explicit allocations without remaining basis", () => {
  const f = fixture();
  assert.throws(() => build(f, { target: "entry", field: "entry_students", value: "101", filters: { grade: "七年级" } }), /超过部门入口总量/);
  assert.throws(() => build(f, { target: "entry", field: "entry_students", value: "99", filters: { grade: "七年级" } }), /没有分配依据/);
  const result = build(f, { target: "entry", field: "entry_students", value: "100", filters: { grade: "七年级" } });
  assert.equal(result.effectiveInputs.entry_total, 100); assert.equal(result.effectiveInputs.combinations[0].entry_locked, true);
  assert.equal(getNextYearBulkScope("entry", f.inputs, f.calculation, {}, "entry_students").skippedCount, 1);
});

test("entry total pending edits participate in final validation", () => {
  const f = fixture(), edits = { [key("entry")]: "120" };
  const result = build(f, { target: "entry", field: "entry_students", value: "120", filters: { grade: "七年级" }, edits });
  assert.equal(result.inputs.entry_total, 100); assert.equal(result.effectiveInputs.entry_total, 120);
  assert.throws(() => build(f, { target: "entry", field: "entry_students", value: "121", filters: { grade: "七年级" }, edits }), /超过/);
});

test("cancel entry and restore automatic clear conflicting edits but preserve other grade allocations", () => {
  const f = fixture();
  f.inputs.combinations.push(combo("c3", { grade: "七年级", subject: "语文", entry: true }));
  f.inputs.combinations[1].entry = true;
  const edits = { [key("entry-grade", "七年级")]: "60", [key("finance", "f-c2-秋季", "price_yuan")]: "3100" };
  const result = build(f, { target: "entry", field: "entry_override", value: "false", filters: { grade: "七年级", subject: "数学" }, edits });
  assert.equal(result.effectiveInputs.combinations[0].entry_students, null); assert.equal(result.effectiveInputs.combinations[0].entry_locked, false);
  assert.equal(result.effectiveInputs.combinations[2].entry_students, 30); assert.equal(result.effectiveInputs.combinations[2].entry_locked, true);
  assert.equal(Object.hasOwn(result.edits, key("entry-grade", "七年级")), false); assert.equal(result.effectiveInputs.finance[5].price_yuan, "3100");
  const automatic = build(f, { target: "entry", field: "automatic", filters: { grade: "七年级", subject: "数学" }, edits });
  assert.equal(automatic.effectiveInputs.combinations[0].entry_students, null); assert.equal(automatic.effectiveInputs.combinations[2].entry_students, 30);
  assert.equal(automatic.effectiveInputs.entry_total, 100);
});

test("invalid pending values keep scope visible but block batch and unchanged templates create no dirty changes", () => {
  const f = fixture(), edits = { [key("finance", "f-c1-秋季", "discount_ppm")]: "错误" };
  const scope = getNextYearBulkScope("finance", f.inputs, f.calculation, {}, "price_yuan", edits);
  assert.ok(scope.validationError); assert.equal(scope.candidateCount, 8);
  assert.throws(() => build(f, { edits }), error => /比例/.test(error.message) && Boolean(error.errors[key("finance", "f-c1-秋季", "discount_ppm")]));
  const rates = getNextYearBulkScope("rates", f.inputs, f.calculation, { season: "秋季" });
  const unchanged = build(f, { mode: "paste", paste: tsv(rates.template) });
  assert.equal(unchanged.changedCount, 0); assert.deepEqual(unchanged.edits, {}); assert.deepEqual(unchanged.inputs, f.inputs);
});

test("a new entry count supersedes a later grade edit without changing other allocations", () => {
  const f = fixture();
  f.inputs.combinations.push(combo("c3", { grade: "七年级", subject: "语文", entry: true }));
  f.inputs.combinations[1].entry = true;
  const edits = { [key("entry-count", "c1")]: "40", [key("entry-grade", "七年级")]: "60" };
  const result = build(f, { target: "entry", field: "entry_students", value: "50", filters: { grade: "七年级", subject: "数学" }, edits });
  assert.equal(result.effectiveInputs.combinations[0].entry_students, 50);
  assert.equal(result.effectiveInputs.combinations[2].entry_students, 30);
  assert.equal(Object.hasOwn(result.edits, key("entry-grade", "七年级")), false);
});

test("materializing a grade drops obsolete earlier count edits for every member, for all entry actions", () => {
  const f = fixture();
  f.inputs.combinations.push(combo("c3", { grade: "七年级", subject: "语文", entry: true }));
  f.inputs.combinations[1].entry = true;
  const edits = { [key("entry-count", "c3")]: "80", [key("entry-grade", "七年级")]: "60" };
  for (const [field, value] of [["entry_override", "false"], ["automatic", ""], ["entry_students", "50"]]) {
    const result = build(f, { target: "entry", field, value, filters: { grade: "七年级", subject: "数学" }, edits });
    assert.equal(result.effectiveInputs.combinations[2].entry_students, 30, field);
    assert.equal(Object.hasOwn(result.edits, key("entry-count", "c3")), false, field);
    assert.equal(result.effectiveInputs.combinations[0].entry_students, field === "entry_students" ? 50 : null, field);
  }
});

test("new plan/manual values supersede both pending families while retaining projected lock metadata", () => {
  const f = fixture(), edits = { [key("manual", "c1", "秋季")]: "20", [key("plan", "c1", "秋季")]: "30" };
  const manual = build(f, { target: "plans", field: "manual_new", value: "40", filters: { grade: "七年级", season: "秋季" }, edits });
  assert.equal(manual.effectiveInputs.plans[0].students, 40); assert.equal(manual.effectiveInputs.plans[0].locked, true);
  assert.equal(Object.hasOwn(manual.edits, key("plan", "c1", "秋季")), false);
  f.calculation.rows.find(row => row.combination_id === "c1" && row.season === "秋季").planned_new_applied = true;
  const planned = build(f, { target: "plans", field: "planned_new", value: "45", filters: { grade: "七年级", season: "秋季" }, edits });
  assert.equal(planned.effectiveInputs.plans[0].students, 45); assert.equal(planned.effectiveInputs.plans[0].locked, true); assert.equal(planned.effectiveInputs.plans[0].reason, "预算手动调节");
});

test("rates Excel union applies share-only summer rows even when renewal is selected", () => {
  const f = fixture(); f.inputs.opening_sources = [];
  const filters = { grade: "八年级", season: "暑假" };
  assert.equal(getNextYearBulkScope("rates", f.inputs, f.calculation, filters, "renewal_ppm").applicableCount, 0);
  const scope = getNextYearBulkScope("rates", f.inputs, f.calculation, filters, "renewal_ppm", {}, "paste");
  assert.equal(scope.applicableCount, 1);
  const rows = scope.template.map(row => [...row]); rows[1][rows[1].length - 1] = "25";
  const result = build(f, { mode: "paste", paste: tsv(rows), filters });
  assert.equal(result.changedCount, 1); assert.equal(result.effectiveInputs.rates.find(row => row.id === "r-c2-暑假").share_ppm, 250000);
});

test("more than one page is updated and a bad final row rejects the entire paste", () => {
  const f = fixture();
  for (let index = 0; index < 30; index++) {
    const row = combo(`x${index}`, { campus: `校区${index}`, entry: false });
    f.inputs.combinations.push(row); f.inputs.finance.push({ id: `fx${index}`, combination_id: row.id, season: "秋季", price_yuan: "3000", discount_ppm: 900000, recognition_ppm: 1000000 });
  }
  assert.equal(build(f, { target: "finance", field: "price_yuan", value: "3200" }).changedCount, 32);
  const scope = getNextYearBulkScope("finance", f.inputs, f.calculation, { season: "秋季" }, "price_yuan"), rows = scope.template.map(row => [...row]);
  rows.slice(1).forEach(row => { row[row.length - 1] = "3200"; }); rows.at(-1)[rows.at(-1).length - 1] = "-1";
  const before = JSON.stringify(f.inputs);
  assert.throws(() => build(f, { target: "finance", field: "price_yuan", mode: "paste", paste: tsv(rows) }), /学费/);
  assert.equal(JSON.stringify(f.inputs), before);
  assert.equal(applyNextYearEdits(f.inputs, {}).inputs.finance[0].price_yuan, "3000");
});

const referenceCell = (scope, row, header) => row[scope.template[0].indexOf(header)];
const populateResults = f => {
  for (const row of f.calculation.rows) Object.assign(row, { old_students: 10, new_students: 5, total_students: 15, revenue_yuan: "1000.123456", class_count: 1 });
  return f;
};
const skuContext = () => ({ sourceFiscalYear: 2027, fiscalYear: 2028, skuEvidence: new Map([
  ["c1", { sourceLabel: "基准已有", historyRevenue: 0, historyStudents: 0, historyState: "value", summerOld: 0, summerState: "value", openingCarryover: 0 }],
  ["c2", { sourceLabel: "升年级承接", historyRevenue: null, historyStudents: null, historyState: "missing", summerOld: 8, summerState: "value", openingCarryover: 0 }],
]) });

test("SKU templates include the exact operating and carry-forward evidence with dynamic fiscal years", () => {
  const f = fixture(), context = skuContext();
  const scope = getNextYearBulkScope("sku", f.inputs, f.calculation, {}, "enabled", {}, "paste", context);
  assert.deepEqual(scope.identityHeaders, ["校区", "赛道", "产品", "年级", "科目"]);
  assert.deepEqual(scope.editableHeaders, ["参与预算"]);
  assert.deepEqual(scope.template[0], [...scope.identityHeaders, ...scope.referenceHeaders, ...scope.editableHeaders]);
  assert.equal(referenceCell(scope, scope.template[1], "参考·FY27结转收入（元）"), "0");
  assert.equal(referenceCell(scope, scope.template[1], "参考·FY27招生人次"), "0");
  assert.equal(referenceCell(scope, scope.template[2], "参考·FY27结转收入（元）"), "");
  assert.equal(referenceCell(scope, scope.template[2], "参考·FY27经营基础状态"), "无同年级经营基础");
  assert.equal(referenceCell(scope, scope.template[2], "参考·FY28暑假老生人次"), "8");
  assert.equal(referenceCell(scope, scope.template[2], "参考·FY28期初结转（元）"), "0");
  const changedYear = getNextYearBulkScope("sku", f.inputs, f.calculation, {}, "enabled", {}, "paste", { ...context, sourceFiscalYear: 2031, fiscalYear: 2032, calculationStale: true });
  assert.ok(changedYear.referenceHeaders.includes("参考·FY31结转收入（元）"));
  assert.equal(referenceCell(changedYear, changedYear.template[2], "参考·FY32暑假老生人次"), "8", "unrelated changes do not erase reviewed summer evidence");
  context.skuEvidence.get("c1").historyState = "empty";
  const empty = getNextYearBulkScope("sku", f.inputs, f.calculation, {}, "enabled", {}, "paste", context);
  assert.equal(referenceCell(empty, empty.template[1], "参考·FY27结转收入（元）"), "0", "reviewed empty-zero keeps its number independently of the missing-style label");
  assert.equal(referenceCell(empty, empty.template[1], "参考·FY27经营基础状态"), "无同年级经营基础");
});

test("changed reference values never become identity or budget writes, and row reordering remains safe", () => {
  const f = fixture(), context = skuContext(), scope = getNextYearBulkScope("sku", f.inputs, f.calculation, {}, "enabled", {}, "paste", context);
  const rows = scope.template.map(row => [...row]), original = structuredClone(f.inputs);
  for (const row of rows.slice(1)) for (let index = scope.identityHeaders.length; index < row.length - 1; index++) row[index] = `not an input\t${index}`;
  rows[2][rows[2].length - 1] = "不参与";
  const result = build(f, { target: "sku", field: "enabled", mode: "paste", paste: tsv([rows[0], rows[2], rows[1]]), filters: {}, referenceContext: context });
  original.combinations[1].enabled = false;
  assert.equal(result.changedCount, 1); assert.deepEqual(result.effectiveInputs, original); assert.deepEqual(result.edits, {});
  rows[2][rows[2].length - 1] = "参与";
  assert.equal(build(f, { target: "sku", field: "enabled", mode: "paste", paste: tsv(rows), filters: {}, referenceContext: context }).changedCount, 0);
});

test("shared-rate and annual-class references count each SKU-quarter exactly once", () => {
  const f = populateResults(fixture()), extra = combo("c3", { grade: "七年级", subject: "英语", entry: false });
  f.inputs.combinations.push(extra);
  f.calculation.rows.push(...nextYearSeasons.map(season => ({ ...extra, combination_id: extra.id, season, old_students: 10, new_students: 5, total_students: 15, revenue_yuan: "1000.123456", class_count: 1 })));
  f.calculation.rows.push({ ...f.calculation.rows.find(row => row.combination_id === "c1" && row.season === "秋季") });
  const scope = getNextYearBulkScope("rates", f.inputs, f.calculation, { grade: "七年级", season: "秋季", subject: "数学" });
  assert.equal(scope.candidateCount, 1);
  assert.equal(referenceCell(scope, scope.template[1], "参考·当季总人次"), "30", "a subject-shared parameter includes both subjects but not duplicate result rows");
  assert.equal(referenceCell(scope, scope.template[1], "参考·当季结转收入（元）"), "2000.246912");
  for (const target of ["rates", "bounds"]) {
    const shared = getNextYearBulkScope(target, f.inputs, f.calculation, { season: "秋季" });
    assert.ok(shared.referenceHeaders.includes("参考·当前续班率上限（%）"));
    assert.ok(shared.referenceHeaders.includes("参考·当季总人次"));
  }
  const classes = getNextYearBulkScope("class", f.inputs, f.calculation, { subject: "数学", season: "秋季" });
  assert.equal(referenceCell(classes, classes.template[1], "参考·全年总人次"), "180");
  assert.equal(referenceCell(classes, classes.template[1], "参考·全年班量"), "12");
  f.calculation.rows.at(-1).total_students = 16;
  const conflict = getNextYearBulkScope("rates", f.inputs, f.calculation, { grade: "七年级", season: "秋季" });
  assert.equal(referenceCell(conflict, conflict.template[1], "参考·当季总人次"), "");
  assert.equal(referenceCell(conflict, conflict.template[1], "参考·计算结果状态"), "数据待核对");
});

test("unknown and stale result references stay blank with status while actual zero remains numeric", () => {
  const f = populateResults(fixture()), filters = { grade: "七年级", season: "秋季" };
  f.calculation.rows[1].old_students = 0;
  const ready = getNextYearBulkScope("finance", f.inputs, f.calculation, filters);
  assert.equal(referenceCell(ready, ready.template[1], "参考·当季老生人次"), "0");
  const stale = getNextYearBulkScope("finance", f.inputs, f.calculation, filters, "price_yuan", {}, "paste", { calculationStale: true });
  assert.equal(referenceCell(stale, stale.template[1], "参考·当前学费（元）"), "3000");
  assert.equal(referenceCell(stale, stale.template[1], "参考·当季总人次"), "");
  assert.equal(referenceCell(stale, stale.template[1], "参考·计算结果状态"), "待重算");
  f.calculation.rows[1].total_students = null;
  const unknown = getNextYearBulkScope("finance", f.inputs, f.calculation, filters);
  assert.equal(referenceCell(unknown, unknown.template[1], "参考·当季总人次"), "");
  assert.equal(referenceCell(unknown, unknown.template[1], "参考·计算结果状态"), "数据待核对");
  f.calculation.rows[0].class_count = null;
  const classes = getNextYearBulkScope("class", f.inputs, f.calculation, {});
  assert.equal(referenceCell(classes, classes.template[1], "参考·全年班量"), "");
  assert.equal(referenceCell(classes, classes.template[1], "参考·班量结果状态"), "数据待核对");
});

test("all seven targets keep their previous compact templates readable and ignore reference edits", () => {
  const cases = [
    ["rates", "share_ppm", "21", { season: "秋季" }], ["finance", "price_yuan", "3100", { season: "秋季" }],
    ["bounds", "renewal_upper_ppm", "100", { season: "秋季" }], ["class", "minimum", "22", {}],
    ["plans", "manual_new", "12", { season: "秋季" }], ["sku", "enabled", "不参与", { grade: "八年级" }],
    ["entry", "entry_students", "100", { grade: "七年级" }],
  ];
  for (const [target, field, value, filters] of cases) {
    const f = populateResults(fixture()), scope = getNextYearBulkScope(target, f.inputs, f.calculation, filters, field, {}, "paste");
    assert.ok(scope.referenceHeaders.length > 0, target);
    const full = scope.template.map(row => [...row]);
    for (const row of full.slice(1)) for (let column = scope.identityHeaders.length; column < row.length - scope.editableHeaders.length; column++) row[column] = "参考可改但不入库";
    const editable = scope.template[0].length - scope.editableHeaders.length + (target === "rates" ? 1 : 0);
    full[1][editable] = value;
    const compact = full.map(row => [...row.slice(0, scope.identityHeaders.length), ...row.slice(-scope.editableHeaders.length)]);
    const options = { target, field, mode: "paste", filters };
    const fullResult = build(f, { ...options, paste: tsv(full) }), compactResult = build(f, { ...options, paste: tsv(compact) });
    assert.deepEqual(fullResult.effectiveInputs, compactResult.effectiveInputs, target);
    assert.ok(fullResult.changedCount > 0, target);
  }
});

test("reference schema headers remain required and cannot introduce duplicate or unknown editable fields", () => {
  const f = fixture(), scope = getNextYearBulkScope("finance", f.inputs, f.calculation, { season: "秋季" });
  const rows = scope.template.map(row => [...row]); rows[1][rows[1].length - 1] = "3200";
  const options = { target: "finance", field: "price_yuan", mode: "paste", filters: { season: "秋季" } };
  assert.throws(() => build(f, { ...options, paste: tsv(rows.slice(1)) }), /保留模板表头/);
  rows[0][scope.identityHeaders.length] = "参与预算";
  assert.throws(() => build(f, { ...options, paste: tsv(rows) }), /表头/);
  rows[0][scope.identityHeaders.length] = rows[0][scope.identityHeaders.length + 1];
  assert.throws(() => build(f, { ...options, paste: tsv(rows) }), /重复列/);
});
