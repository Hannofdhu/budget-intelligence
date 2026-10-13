import { forecastNumber } from "./forecastApi.ts";
import {
  applyNextYearEdits, nextYearEditKey, summarizeNextYearEdits,
  type NextYearCombination, type NextYearEditCategory, type NextYearEditItem, type NextYearEditSummary,
  type NextYearEdits, type NextYearInputs, type NextYearRate,
} from "./nextYearApi.ts";

type EditPath = (string | number)[];
const pathOf = (key: string): EditPath => {
  try { const path: unknown = JSON.parse(key); return Array.isArray(path) ? path : []; } catch { return []; }
};
const sameNumber = (a: unknown, b: unknown) => {
  if (a === null || a === undefined || b === null || b === undefined) return a == null && b == null;
  const left = forecastNumber(a as string | number), right = forecastNumber(b as string | number);
  return left !== null && right !== null ? left === right : a === b;
};
const effectiveEntry = (row: NextYearCombination) => row.entry_override ?? row.entry;
const dimensions = (row: Partial<NextYearCombination> | undefined) => row ? [row.campus, row.track, row.product, row.grade, row.subject].filter(Boolean).join(" / ") : "";
const rateMatches = (rate: NextYearRate, row: NextYearCombination) => rate.track === row.track && rate.product === row.product && rate.grade === row.grade && rate.campus === row.campus;
const planAt = (inputs: NextYearInputs, id: unknown, season: unknown) => inputs.plans.find(row => row.combination_id === id && row.season === season);
const classAt = (inputs: NextYearInputs, row: NextYearInputs["class_sizes"][number] | undefined) => row ? inputs.class_sizes.find(item => item.track === row.track && item.product === row.product) : undefined;

/** One routing map for validation, diff links and state-only changes. */
export function nextYearEditTask(key: string): 1 | 2 | 3 | 4 | 5 {
  const section = pathOf(key)[0];
  if (["entry", "entry-count", "entry-grade", "entry-scope", "entry-lock"].includes(String(section))) return 2;
  if (["rates", "bounds", "plan", "manual", "rate-lock", "plan-lock"].includes(String(section))) return 3;
  if (["finance", "class", "class-size", "tax"].includes(String(section))) return 4;
  return 1;
}

const stateItem = (section: string, id: string, category: NextYearEditCategory, label: string, before: string, after: string, season?: string): NextYearEditItem => ({
  key: nextYearEditKey(section, id, ...(season ? [season] : [])), category,
  task: nextYearEditTask(nextYearEditKey(section)), label, beforeDisplay: before, afterDisplay: after,
  beforeNumber: null, afterNumber: null, unit: "count",
});

/** Local change description only: no calculation, request, mutation or version creation. */
export function summarizeNextYearChanges(baseline: NextYearInputs, staged: NextYearInputs, edits: NextYearEdits, errors: Record<string, string> = {}): NextYearEditSummary {
  const inferred: NextYearEdits = {}, states: NextYearEditItem[] = [];
  const baselineCombos = new Map(baseline.combinations.map(row => [row.id, row]));
  const baselineRates = new Map(baseline.rates.map(row => [row.id, row]));
  const baselineFinance = new Map(baseline.finance.map(row => [row.id, row]));
  const add = (path: EditPath, before: unknown, after: unknown, percent = false) => {
    if (!sameNumber(before, after)) inferred[nextYearEditKey(...path)] = after == null ? "" : String(percent ? Number(after) / 10000 : after);
  };
  add(["target"], baseline.target_revenue_yuan, staged.target_revenue_yuan);
  for (const [track, amount] of Object.entries(staged.track_targets)) add(["track-target", track], baseline.track_targets[track] ?? null, amount);
  add(["entry"], baseline.entry_total, staged.entry_total);
  add(["tax"], baseline.tax_factor, staged.tax_factor);
  for (const row of staged.combinations) {
    const before = baselineCombos.get(row.id);
    if (!before) { states.push(stateItem("business", row.id, "other", `${dimensions(row)} 新业务`, "未加入", "加入预算")); continue; }
    add(["entry-count", row.id], before.entry_students, row.entry_students);
    if (before.enabled !== row.enabled) states.push(stateItem("enabled", row.id, "other", `${dimensions(row)} 参与预算`, before.enabled ? "参与" : "不参与", row.enabled ? "参与" : "不参与"));
    if (effectiveEntry(before) !== effectiveEntry(row)) states.push(stateItem("entry-scope", row.id, "entry_detail", `${dimensions(row)} 入口范围`, effectiveEntry(before) ? "入口" : "非入口", effectiveEntry(row) ? "入口" : "非入口"));
    if (Boolean(before.entry_locked) !== Boolean(row.entry_locked)) states.push(stateItem("entry-lock", row.id, "entry_detail", `${dimensions(row)} 入口锁定`, before.entry_locked ? "锁定" : "自动分配", row.entry_locked ? "锁定" : "自动分配"));
  }
  for (const row of staged.rates) {
    const before = baselineRates.get(row.id);
    if (!before) continue; // New-business initialization is represented by its business item.
    for (const field of ["renewal_ppm", "share_ppm"] as const) add(["rates", row.id, field], before[field], row[field], true);
    for (const field of ["renewal_lower_ppm", "renewal_upper_ppm", "share_lower_ppm", "share_upper_ppm"] as const) add(["bounds", row.id, field], before[field], row[field], true);
    if (Boolean(before.locked) !== Boolean(row.locked)) states.push(stateItem("rate-lock", row.id, "bounds", `${dimensions(row)} · ${row.season} 调平锁定`, before.locked ? "锁定" : "可调整", row.locked ? "锁定" : "可调整"));
  }
  for (const row of staged.finance) {
    const before = baselineFinance.get(row.id);
    if (!before) continue;
    for (const field of ["price_yuan", "discount_ppm", "recognition_ppm"] as const) add(["finance", row.id, field], before[field], row[field], field !== "price_yuan");
  }
  staged.class_sizes.forEach((row, index) => {
    const before = classAt(baseline, row);
    if (!before) return;
    add(["class", index], before.minimum, row.minimum);
    add(["class-size", index], before.planned_class_size, row.planned_class_size);
  });
  for (const row of staged.plans) {
    if (!baselineCombos.has(row.combination_id)) continue;
    const before = planAt(baseline, row.combination_id, row.season);
    add([row.locked ? "manual" : "plan", row.combination_id, row.season], before?.students, row.students);
    if (Boolean(before?.locked) !== Boolean(row.locked)) states.push(stateItem("plan-lock", row.combination_id, "plan", `${dimensions(staged.combinations.find(combo => combo.id === row.combination_id))} · ${row.season} 新生锁定`, before?.locked ? "锁定" : "自动计算", row.locked ? "锁定" : "自动计算", row.season));
  }

  // Added rows supply labels/current defaults without pretending their initialization existed in the saved budget.
  const summaryBase: NextYearInputs = {
    ...baseline,
    combinations: staged.combinations.map(row => baselineCombos.get(row.id) ?? row),
    rates: staged.rates.map(row => baselineRates.get(row.id) ?? row),
    finance: staged.finance.map(row => baselineFinance.get(row.id) ?? row),
    class_sizes: staged.class_sizes.map(row => classAt(baseline, row) ?? row),
    plans: [...baseline.plans, ...staged.plans.filter(row => !baselineCombos.has(row.combination_id))],
  };
  const validation = { ...applyNextYearEdits(staged, edits).errors, ...errors };
  const merged = { ...inferred, ...edits };
  const ordinary = summarizeNextYearEdits(summaryBase, merged, validation).groups.flatMap(group => group.items).map(original => {
    const item = ["entry_total", "entry_detail", "plan"].includes(original.category) ? {
      ...original, beforeDisplay: original.beforeDisplay?.replace(/ 人$/, " 人次") ?? null, afterDisplay: original.afterDisplay.replace(/ 人$/, " 人次"),
    } : original;
    const [section, id] = pathOf(item.key);
    if (section === "entry-grade") {
      const members = baseline.combinations.filter(row => row.enabled && effectiveEntry(row) && row.grade === id);
      const known = members.length > 0 && members.every(row => row.entry_students != null);
      const before = known ? members.reduce((sum, row) => sum + row.entry_students!, 0) : null;
      return { ...item, beforeDisplay: before === null ? "自动分配" : `${before.toLocaleString("zh-CN")} 人次`, beforeNumber: before };
    }
    if (section === "manual" && !planAt(baseline, id, pathOf(item.key)[2])?.locked) return { ...item, beforeDisplay: "自动计算", beforeNumber: null };
    return item;
  }).filter(item => {
    if (item.invalid) return true;
    const [section] = pathOf(item.key);
    if (section === "manual" && !planAt(baseline, pathOf(item.key)[1], pathOf(item.key)[2])?.locked) return true;
    if (item.beforeNumber !== null && item.afterNumber !== null) return item.beforeNumber !== item.afterNumber;
    if (item.beforeNumber === null && item.afterNumber === null) {
      const raw = merged[item.key]?.trim();
      return Boolean(raw); // Clearing an already-unset optional value is not a change.
    }
    return true;
  });
  // Malformed keys are normally rejected before the legacy summarizer. Keep them visible and undoable.
  for (const [key, raw] of Object.entries(edits)) if (!ordinary.some(item => item.key === key) && validation[key] && !pathOf(key).length) ordinary.push({ key, category: "other", task: 1, label: "无效编辑项", beforeDisplay: null, afterDisplay: raw || "（空值）", beforeNumber: null, afterNumber: null, unit: "count", invalid: true });
  const items = [...ordinary, ...states];
  const labels: [NextYearEditCategory, string][] = [["target", "全年经营目标"], ["track_target", "赛道目标"], ["entry_total", "年度入口人次"], ["entry_detail", "入口分配"], ["rates", "续班率与新生占比"], ["bounds", "调平范围"], ["plan", "计划新生"], ["finance", "学费 / 折扣 / 结转"], ["class_size", "班均参数"], ["tax", "含税转换系数"], ["other", "业务范围"]];
  return { groups: labels.flatMap(([category, label]) => {
    const members = items.filter(item => item.category === category);
    return members.length ? [{ category, label, items: members, scale: changeScale(category, members) }] : [];
  }), totalCount: items.length };
}

function changeScale(category: NextYearEditCategory, items: NextYearEditItem[]): string | undefined {
  // Unknown, clearing and invalid values cannot honestly contribute a numeric delta.
  const known = items.filter(item => !item.invalid && item.beforeNumber !== null && item.afterNumber !== null);
  if (!known.length) return undefined;
  if (category === "rates" || category === "bounds") {
    const maximum = Math.max(...known.map(item => Math.abs(item.afterNumber! - item.beforeNumber!)));
    return maximum ? `最大 ${maximum.toLocaleString("zh-CN", { maximumFractionDigits: 4 })} 个百分点` : undefined;
  }
  if (category === "class_size" || category === "finance") {
    const parameters = category === "finance" ? known.filter(item => item.unit === "money") : known;
    if (!parameters.length) return undefined;
    const maximum = Math.max(...parameters.map(item => Math.abs(item.afterNumber! - item.beforeNumber!)));
    return maximum ? `${category === "finance" ? "单项学费" : "单项班额"}最大变化 ${maximum.toLocaleString("zh-CN", { maximumFractionDigits: 2 })} ${category === "finance" ? "元" : "人"}` : undefined;
  }
  if (["target", "track_target", "entry_total", "plan", "entry_detail"].includes(category)) {
    if (known.length !== items.length || category === "entry_detail" && items.some(item => pathOf(item.key)[0] === "entry-grade")) return undefined;
    const delta = known.reduce((sum, item) => sum + item.afterNumber! - item.beforeNumber!, 0), money = category === "target" || category === "track_target";
    return `${delta >= 0 ? "+" : ""}${(money ? delta / 10000 : delta).toLocaleString("zh-CN", { maximumFractionDigits: 2 })} ${money ? "万元" : "人次"}`;
  }
  return undefined;
}

/** Undo one description against the saved baseline; unrelated local work stays intact. */
export function undoNextYearChange(baseline: NextYearInputs, staged: NextYearInputs, edits: NextYearEdits, item: NextYearEditItem): { inputs: NextYearInputs; edits: NextYearEdits } {
  const inputs = structuredClone(staged), nextEdits = { ...edits };
  delete nextEdits[item.key];
  const [section, id, field] = pathOf(item.key), combo = inputs.combinations.find(row => row.id === id), originalCombo = baseline.combinations.find(row => row.id === id);
  if (section === "business" && !originalCombo) return removeAddedBusiness(baseline, inputs, nextEdits, String(id));
  if (section === "target") inputs.target_revenue_yuan = baseline.target_revenue_yuan;
  else if (section === "track-target") { if (Object.hasOwn(baseline.track_targets, id)) inputs.track_targets[String(id)] = baseline.track_targets[String(id)]; else inputs.track_targets[String(id)] = null; }
  else if (section === "entry") inputs.entry_total = baseline.entry_total;
  else if (section === "tax") inputs.tax_factor = baseline.tax_factor;
  else if (section === "enabled" && combo && originalCombo) combo.enabled = originalCombo.enabled;
  else if (section === "entry-scope" && combo && originalCombo) {
    restoreProperty(combo, originalCombo, "entry_override");
    restoreProperty(combo, originalCombo, "entry_students"); restoreProperty(combo, originalCombo, "entry_locked");
  } else if (section === "entry-lock" && combo && originalCombo) {
    restoreProperty(combo, originalCombo, "entry_locked");
    // A raw count explicitly re-locks its row when projected. Undoing the lock must not immediately re-apply it.
    delete nextEdits[nextYearEditKey("entry-count", String(id))];
  }
  else if (section === "entry-count" && combo && originalCombo) { restoreProperty(combo, originalCombo, "entry_students"); restoreProperty(combo, originalCombo, "entry_locked"); }
  else if (section === "entry-grade") {
    for (const row of inputs.combinations.filter(row => row.grade === id)) {
      const before = baseline.combinations.find(item => item.id === row.id);
      if (before) { restoreProperty(row, before, "entry_students"); restoreProperty(row, before, "entry_locked"); }
    }
  } else if (section === "rates" || section === "bounds" || section === "rate-lock") {
    const row = inputs.rates.find(row => row.id === id), before = baseline.rates.find(row => row.id === id);
    if (row && before) restoreProperty(row, before, (section === "rate-lock" ? "locked" : field) as keyof NextYearRate);
  } else if (section === "finance") {
    const row = inputs.finance.find(row => row.id === id), before = baseline.finance.find(row => row.id === id);
    if (row && before) restoreProperty(row, before, field as keyof typeof row);
  } else if (section === "class" || section === "class-size") {
    const row = inputs.class_sizes[Number(id)], before = classAt(baseline, row);
    if (row && before) restoreProperty(row, before, section === "class" ? "minimum" : "planned_class_size");
  } else if (section === "plan" || section === "manual" || section === "plan-lock") {
    const row = planAt(inputs, id, field), before = planAt(baseline, id, field);
    if (section === "plan-lock") delete nextEdits[nextYearEditKey("manual", String(id), String(field))];
    if (row && before) {
      if (section !== "plan-lock") row.students = before.students;
      if (section !== "plan") { restoreProperty(row, before, "locked"); restoreProperty(row, before, "reason"); }
    } else if (row && section === "plan-lock") {
      delete row.locked; delete row.reason;
    } else if (row && section !== "plan-lock") inputs.plans = inputs.plans.filter(value => value !== row);
  }
  return { inputs, edits: nextEdits };
}

function restoreProperty<T extends object, K extends keyof T>(row: T, before: T, key: K) {
  if (Object.hasOwn(before, key)) row[key] = before[key]; else delete row[key];
}

function removeAddedBusiness(baseline: NextYearInputs, inputs: NextYearInputs, edits: NextYearEdits, id: string) {
  const removed = inputs.combinations.find(row => row.id === id);
  if (!removed) return { inputs, edits };
  inputs.combinations = inputs.combinations.filter(row => row.id !== id);
  const removedFinance = new Set(inputs.finance.filter(row => row.combination_id === id).map(row => row.id));
  inputs.finance = inputs.finance.filter(row => row.combination_id !== id);
  inputs.plans = inputs.plans.filter(row => row.combination_id !== id);
  const removedRates = new Set(inputs.rates.filter(rate => rateMatches(rate, removed) && !baseline.rates.some(row => row.id === rate.id) && !inputs.combinations.some(row => rateMatches(rate, row))).map(row => row.id));
  inputs.rates = inputs.rates.filter(row => !removedRates.has(row.id));
  const previousClasses = inputs.class_sizes;
  inputs.class_sizes = previousClasses.filter(row => row.track !== removed.track || row.product !== removed.product || classAt(baseline, row) || inputs.combinations.some(combo => combo.track === row.track && combo.product === row.product));
  const removedTracks = new Set(Object.keys(inputs.track_targets).filter(track => track === removed.track && !Object.hasOwn(baseline.track_targets, track) && !inputs.combinations.some(row => row.track === track)));
  for (const track of removedTracks) delete inputs.track_targets[track];
  const retained: NextYearEdits = {};
  for (const [key, raw] of Object.entries(edits)) {
    const path = pathOf(key), [section, rowId] = path;
    if (["business", "enabled", "entry-scope", "entry-lock", "entry-count", "plan", "manual", "plan-lock"].includes(String(section)) && rowId === id) continue;
    if (section === "finance" && removedFinance.has(String(rowId))) continue;
    if (["rates", "bounds", "rate-lock"].includes(String(section)) && removedRates.has(String(rowId))) continue;
    if (section === "track-target" && removedTracks.has(String(rowId))) continue;
    if (section === "entry-grade" && !inputs.combinations.some(row => row.grade === rowId && row.enabled && effectiveEntry(row))) continue;
    if (section === "class" || section === "class-size") {
      const previous = previousClasses[Number(rowId)], index = inputs.class_sizes.indexOf(previous);
      if (index < 0) continue;
      path[1] = index; retained[nextYearEditKey(...path)] = raw;
    } else retained[key] = raw;
  }
  return { inputs, edits: retained };
}
