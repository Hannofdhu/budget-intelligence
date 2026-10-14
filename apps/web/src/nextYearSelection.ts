import type {
  NextYearCalculation, NextYearClassRepairAction, NextYearInputs, NextYearRate,
} from "./nextYearApi.ts";

// Remove the open index signature before Omit, otherwise TypeScript loses the named fields.
type KnownActionFields = { [Key in keyof NextYearClassRepairAction as string extends Key ? never
  : number extends Key ? never : Key]: NextYearClassRepairAction[Key] };

/** Frontend-only review actions. TRACK_TARGET makes the solver's allocation visible. */
export type NextYearSelectableAction = Omit<KnownActionFields, "kind"> & {
  kind: NextYearClassRepairAction["kind"] | "TRACK_TARGET";
  [key: string]: unknown;
};

const seasons = ["暑假", "秋季", "寒假", "春季"];
const currentRateFields = new Set(["renewal_ppm", "share_ppm"]);
const boundaryFields = new Set(["renewal_lower_ppm", "renewal_upper_ppm", "share_lower_ppm", "share_upper_ppm"]);
const canonical = (value: unknown): unknown => Array.isArray(value) ? value.map(canonical)
  : value && typeof value === "object" ? Object.fromEntries(Object.entries(value)
    .sort(([left], [right]) => left.localeCompare(right)).map(([field, item]) => [field, canonical(item)])) : value;
const same = (left: unknown, right: unknown): boolean => JSON.stringify(canonical(left)) === JSON.stringify(canonical(right));
const clone = <T,>(value: T): T => structuredClone(value);
const failure = (message: string): never => { throw new Error(message); };

export function nextYearActionKey(action: NextYearSelectableAction, index: number): string {
  return `${action.kind}-${index}`;
}

function count(value: unknown): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0 || value > 10_000_000) {
    return failure("建议人次无效，请重新生成方案");
  }
  return value;
}

function effectiveEntry(row: NextYearInputs["combinations"][number]): boolean {
  return row.entry_override ?? row.entry;
}

function rowAt<T>(rows: T[], matches: (row: T) => boolean): T {
  const found = rows.filter(matches);
  if (found.length !== 1) return failure("方案对应的业务或参数已变化，请重新生成方案");
  return found[0];
}

function rateValue(rate: NextYearRate, field: string): number {
  const value = (rate as unknown as Record<string, unknown>)[field];
  if (typeof value === "number") return value;
  const kind = field.startsWith("renewal_") ? "renewal" : "share";
  const current = rate[kind === "renewal" ? "renewal_ppm" : "share_ppm"];
  return field.includes("_lower_") ? Math.max(0, current - 50_000)
    : Math.min(kind === "renewal" ? 1_000_000 : 999_900, current + 50_000);
}

function entryCounts(inputs: NextYearInputs, calculation: NextYearCalculation): Map<string, number> {
  return new Map(inputs.combinations.filter(row => row.enabled && effectiveEntry(row)).map(row => {
    const detail = calculation.entry_details?.find(item => item.combination_id === row.id);
    const summer = calculation.rows.find(item => item.combination_id === row.id && item.season === "暑假");
    return [row.id, count(detail?.students ?? summer?.new_students)];
  }));
}

function applyAction(
  merged: NextYearInputs, original: NextYearInputs, proposed: NextYearInputs,
  action: NextYearSelectableAction,
): string {
  if (action.kind === "ENTRY_REALLOCATION") {
    if (action.field !== "entry_students" || typeof action.combination_id !== "string") {
      return failure("入口建议字段无法识别，请重新生成方案");
    }
    const matches = (row: NextYearInputs["combinations"][number]) => row.id === action.combination_id;
    const before = rowAt(original.combinations, matches), after = rowAt(proposed.combinations, matches);
    if (!before.enabled || !effectiveEntry(before) || count(action.after) !== after.entry_students) {
      return failure("入口建议与SKU不一致，请重新生成方案");
    }
    count(action.before);
    if (action.before_input !== undefined && !same(action.before_input, before.entry_students ?? null)) {
      return failure("入口原值已变化，请重新生成方案");
    }
    if (before.entry_locked && !same(before, after)) return failure("建议不能修改已锁定的入口人次");
    const target = rowAt(merged.combinations, matches);
    target.entry_students = after.entry_students;
    if (Object.hasOwn(after, "entry_locked")) target.entry_locked = after.entry_locked;
    else delete target.entry_locked;
    return `entry:${before.id}`;
  }
  if (action.kind === "NEW_STUDENT_TARGET") {
    if (action.field !== "plans.students" || typeof action.combination_id !== "string" || !seasons.includes(String(action.season))) {
      return failure("新生目标字段无法识别，请重新生成方案");
    }
    const matches = (row: NextYearInputs["plans"][number]) => row.combination_id === action.combination_id && row.season === action.season;
    const before = rowAt(original.plans, matches), after = rowAt(proposed.plans, matches);
    if (count(action.after) !== after.students) return failure("新生目标与建议不一致，请重新生成方案");
    if (before.locked && !same(before, after)) return failure("建议不能修改已锁定的新生目标");
    // The lock and reason make an absolute target effective; copying students alone is wrong.
    merged.plans[merged.plans.findIndex(matches)] = clone(after);
    return `plan:${before.combination_id}:${before.season}`;
  }
  if (action.kind === "RENEWAL_ADJUSTMENT" || action.kind === "RATE_BOUNDARY") {
    const field = String(action.field), identifier = action.rate_id;
    const allowed = action.kind === "RATE_BOUNDARY" ? boundaryFields : currentRateFields;
    if (typeof identifier !== "string" || !allowed.has(field)) {
      return failure("招生参数建议字段无法识别，请重新生成方案");
    }
    const matches = (row: NextYearRate) => row.id === identifier;
    const before = rowAt(original.rates, matches), after = rowAt(proposed.rates, matches);
    const value = (after as unknown as Record<string, unknown>)[field];
    if (typeof value !== "number" || !Number.isInteger(value) || value < 0
      || value > (field.startsWith("renewal") ? 1_000_000 : 999_900) || value % 100 !== 0 || action.after !== value) {
      return failure("招生参数建议无效，请重新生成方案");
    }
    if (before.locked && !same(before, after)) return failure("建议不能修改已锁定的招生参数");
    (rowAt(merged.rates, matches) as unknown as Record<string, unknown>)[field] = value;
    return `rate:${identifier}:${field}`;
  }
  if (action.kind === "TRACK_TARGET") {
    const track = action.track;
    if (action.field !== "target_revenue_yuan" || typeof track !== "string"
      || !Object.hasOwn(original.track_targets, track) || !Object.hasOwn(proposed.track_targets, track)
      || !same(action.before, original.track_targets[track]) || !same(action.after, proposed.track_targets[track])) {
      return failure("赛道目标分配与建议不一致，请重新生成方案");
    }
    merged.track_targets[track] = proposed.track_targets[track];
    return `track:${track}`;
  }
  return failure("建议类型无法识别，请重新生成方案");
}

/** Pure selection merge. Never calculate, save, or silently accept an unlisted change. */
export function applyNextYearClassRepairSelection(
  original: NextYearInputs, proposed: NextYearInputs, actions: NextYearSelectableAction[], selectedKeys: Set<string>,
): NextYearInputs {
  const known = new Set(actions.map(nextYearActionKey));
  if ([...selectedKeys].some(key => !known.has(key))) return failure("勾选项已失效，请重新生成方案");
  const full = clone(original), destinations = new Set<string>();
  for (const action of actions) {
    const destination = applyAction(full, original, proposed, action);
    if (destinations.has(destination)) return failure("方案含重复建议，请重新生成方案");
    destinations.add(destination);
  }
  if (!same(full, proposed)) {
    return failure("方案包含未列出的目标、业务、来源或财务改动，已拒绝应用，请重新生成方案");
  }
  const chosen = actions.filter((action, index) => selectedKeys.has(nextYearActionKey(action, index)));
  const entryDelta = chosen.filter(action => action.kind === "ENTRY_REALLOCATION")
    .reduce((sum, action) => sum + count(action.after) - count(action.before), 0);
  if (entryDelta !== 0) return failure(`入口合计冲突：勾选建议会使入口总量${entryDelta > 0 ? "增加" : "减少"} ${Math.abs(entryDelta)} 人，请同时勾选对应的调入和调出项`);
  if (selectedKeys.size === 0) return clone(original);
  if (selectedKeys.size === actions.length) return clone(proposed);
  const merged = clone(original);
  for (const action of chosen) applyAction(merged, original, proposed, action);
  return merged;
}

/** Validate the authoritative /preview response; a partial preview may be shown, not applied. */
export function assertNextYearSelectionPreview(
  original: NextYearInputs, before: NextYearCalculation, merged: NextYearInputs,
  actions: NextYearSelectableAction[], selectedKeys: Set<string>, calculation: NextYearCalculation,
): void {
  if (!calculation.complete || calculation.fiscal_year !== merged.fiscal_year
    || calculation.annual.revenue_yuan === null || calculation.annual.total_students === null) {
    return failure("所选建议未完成预算计算，请修正参数后重新预览");
  }
  const active = merged.combinations.filter(row => row.enabled);
  const expected = new Set(active.flatMap(row => seasons.map(season => `${row.id}:${season}`)));
  const actual = calculation.rows.map(row => `${row.combination_id}:${row.season}`);
  if (actual.length !== expected.size || new Set(actual).size !== actual.length || actual.some(key => !expected.has(key))) {
    return failure("所选建议的预算计算未完整覆盖四季度，请重新预览");
  }
  const oldEntries = entryCounts(original, before), newEntries = entryCounts(merged, calculation);
  const selectedEntries = new Map(actions.flatMap((action, index) => action.kind === "ENTRY_REALLOCATION"
    && selectedKeys.has(nextYearActionKey(action, index)) ? [[String(action.combination_id), count(action.after)] as const] : []));
  for (const [identifier, value] of oldEntries) {
    if (newEntries.get(identifier) !== (selectedEntries.get(identifier) ?? value)) {
      return failure("入口分配冲突：自动分配影响了未勾选项，请同时勾选关联的入口建议后重试");
    }
  }
  if ([...newEntries.values()].reduce((sum, value) => sum + value, 0) !== merged.entry_total) {
    return failure("入口合计不符，请检查勾选的入口建议后重新预览");
  }
  const blocking = calculation.issues.filter(issue => issue.severity === "BLOCKING");
  if (blocking.length) return failure(`所选建议还有 ${blocking.length} 项计算问题：${blocking[0].message}`);
  const classIssues = calculation.issues.filter(issue => ["BELOW_MINIMUM_CLASS_SIZE", "ENABLED_COMBINATION_ALL_SEASONS_ZERO"].includes(issue.code));
  if (classIssues.length) return failure(`所选建议仍有 ${classIssues.length} 项成班问题，请补选关联建议后再应用`);
}

/** Explain every solver edit in the same selectable UI, including derived track targets. */
export function nextYearBalanceActions(
  original: NextYearInputs, proposed: NextYearInputs, before: NextYearCalculation, after: NextYearCalculation,
): NextYearSelectableAction[] {
  const actions: NextYearSelectableAction[] = [], counts = entryCounts(original, before);
  for (const row of original.combinations) {
    const next = rowAt(proposed.combinations, candidate => candidate.id === row.id);
    if (!same(row.entry_students, next.entry_students) || !same(row.entry_locked, next.entry_locked)) {
      actions.push({ kind: "ENTRY_REALLOCATION", field: "entry_students", combination_id: row.id, season: "暑假",
        track: row.track, product: row.product, campus: row.campus, grade: row.grade, subject: row.subject,
        before: counts.get(row.id) ?? count(row.entry_students), before_input: row.entry_students ?? null,
        after: count(next.entry_students), reason: "在入口总量不变的前提下调整分配，接近经营目标" });
    }
  }
  for (const row of original.rates) {
    const next = rowAt(proposed.rates, candidate => candidate.id === row.id);
    for (const field of [...currentRateFields, ...boundaryFields]) {
      if (same((row as unknown as Record<string, unknown>)[field], (next as unknown as Record<string, unknown>)[field])) continue;
      actions.push({ kind: boundaryFields.has(field) ? "RATE_BOUNDARY" : "RENEWAL_ADJUSTMENT", field, rate_id: row.id,
        season: row.season, track: row.track, product: row.product, campus: row.campus, grade: row.grade,
        before: rateValue(row, field), after: rateValue(next, field), reason: field === "share_ppm"
          ? "在允许范围内调整新生占比，接近经营目标" : "在允许范围内调整招生参数，接近经营目标" });
    }
  }
  for (const [track, value] of Object.entries(original.track_targets)) {
    if (!same(value, proposed.track_targets[track])) actions.push({ kind: "TRACK_TARGET", field: "target_revenue_yuan", track,
      before: value, after: proposed.track_targets[track], reason: "将已填写的全年经营目标按预算结转收入分配到赛道，全年目标不变" });
  }
  // Also verifies that no source, price, minimum, plan or other hidden edit is accepted.
  const keys = new Set(actions.map(nextYearActionKey));
  applyNextYearClassRepairSelection(original, proposed, actions, keys);
  if (after.fiscal_year !== proposed.fiscal_year) return failure("调平结果与预算财年不一致，请重新生成方案");
  return actions;
}
