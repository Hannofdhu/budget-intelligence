import { matchesFilter, type FilterValue } from "./filterSelection.ts";
import { forecastNumber } from "./forecastApi.ts";
import type { SkuEvidence } from "./nextYearSkuEvidence.ts";
import {
  applyNextYearEdits, nextYearEditKey, nextYearSeasons,
  type NextYearCalculation, type NextYearCombination, type NextYearEdits, type NextYearInputs, type NextYearRate, type NextYearRow,
} from "./nextYearApi.ts";

export type NextYearBulkTarget = "rates" | "finance" | "bounds" | "class" | "plans" | "sku" | "entry";
export type NextYearBulkFilters = Partial<Record<"campus" | "track" | "product" | "grade" | "subject" | "season", FilterValue>>;
export interface NextYearBulkField {
  key: string; label: string; unit?: string;
  options?: { value: string; label: string }[]; action?: boolean;
}
export interface NextYearBulkRow {
  id: string; identity: string[]; dimensions: Partial<Record<keyof NextYearBulkFilters, string>>;
  values: Record<string, string>; applicable: boolean;
  combinationId?: string; season?: typeof nextYearSeasons[number]; index?: number;
}
export interface NextYearBulkScope {
  target: NextYearBulkTarget; label: string; scopeLabel: string; scopeNote: string;
  fields: NextYearBulkField[]; rows: NextYearBulkRow[];
  candidateCount: number; applicableCount: number; skippedCount: number;
  identityHeaders: string[]; referenceHeaders: string[]; editableHeaders: string[];
  notes: string[]; template: string[][]; validationError?: string;
}
export interface NextYearBulkReferenceContext {
  skuEvidence?: ReadonlyMap<string, SkuEvidence>;
  sourceFiscalYear?: number; fiscalYear?: number;
  calculationStale?: boolean; entryAllocationStale?: boolean; classResultStale?: boolean;
}
export interface NextYearBulkChangeOptions {
  target: NextYearBulkTarget; field: string; mode: "uniform" | "paste";
  value?: string; paste?: string; inputs: NextYearInputs; edits: NextYearEdits;
  calculation?: NextYearCalculation | null; filters: NextYearBulkFilters;
  referenceContext?: NextYearBulkReferenceContext;
}
export interface NextYearBulkChange {
  /** Base inputs and pending edits stay separate, as in the existing page's edit pipeline. */
  inputs: NextYearInputs; edits: NextYearEdits; effectiveInputs: NextYearInputs;
  changedCount: number; scope: NextYearBulkScope;
}

const skuDimensions = ["campus", "track", "product", "grade", "subject"] as const;
const rateDimensions = ["campus", "track", "product", "grade", "season"] as const;
const headers = { campus: "校区", track: "赛道", product: "产品", grade: "年级", subject: "科目", season: "季度" };
const options = [{ value: "true", label: "是" }, { value: "false", label: "否" }];
const definitions: Record<NextYearBulkTarget, { label: string; note: string; fields: NextYearBulkField[] }> = {
  rates: { label: "续班率、新生占比", note: "按校区、赛道、产品、年级和季度修改；同组所有科目共用参数，科目筛选不会缩小生效范围。", fields: [
    { key: "renewal_ppm", label: "续班率", unit: "%" }, { key: "share_ppm", label: "新生占比", unit: "%" },
  ] },
  finance: { label: "学费、折扣、当季结转", note: "按完整SKU和季度修改，保留现有财务参数精度。", fields: [
    { key: "price_yuan", label: "学费", unit: "元" }, { key: "discount_ppm", label: "折扣", unit: "%" }, { key: "recognition_ppm", label: "当季结转", unit: "%" },
  ] },
  bounds: { label: "自动调平范围", note: "按校区、赛道、产品、年级和季度修改；同组所有科目共享上下限，当前率值必须在上下限内。", fields: [
    { key: "renewal_lower_ppm", label: "续班率下限", unit: "%" }, { key: "renewal_upper_ppm", label: "续班率上限", unit: "%" },
    { key: "share_lower_ppm", label: "新生占比下限", unit: "%" }, { key: "share_upper_ppm", label: "新生占比上限", unit: "%" },
  ] },
  class: { label: "班均", note: "班均仅按赛道和产品共享，修改会影响该赛道、产品下的全部校区、年级、科目和季度；这些维度的筛选不会缩小生效范围。", fields: [{ key: "minimum", label: "班均", unit: "人/班" }] },
  plans: { label: "计划及手动新生", note: "按完整SKU和季度修改；计划新生仅用于现有零老生范围，手动新生沿用现有锁定规则。暑假入口SKU不适用。", fields: [
    { key: "planned_new", label: "计划新生", unit: "人次" }, { key: "manual_new", label: "手动指定新生", unit: "人次" },
  ] },
  sku: { label: "SKU参与预算", note: "按完整SKU修改，全年四季度一同生效；有期初结转的SKU不能取消，至少保留一个参与预算的SKU。", fields: [{ key: "enabled", label: "参与预算", options: [{ value: "true", label: "参与" }, { value: "false", label: "不参与" }] }] },
  entry: { label: "SKU入口设置与分配", note: "按完整SKU修改暑假入口属性和指定人数；部门入口总量保持不变。取消入口会清除该SKU指定人数并解除锁定。", fields: [
    { key: "entry_override", label: "是否入口", options }, { key: "entry_students", label: "指定入口人次", unit: "人次" },
    { key: "automatic", label: "恢复自动分配", action: true },
  ] },
};
const effectiveEntry = (row: NextYearCombination) => row.entry_override ?? row.entry;
const matches = (row: Partial<Record<keyof NextYearBulkFilters, string>>, filters: NextYearBulkFilters) =>
  (Object.keys(filters) as (keyof NextYearBulkFilters)[]).every(key => matchesFilter(row[key], filters[key], true));
const percent = (number: number | undefined) => number === undefined ? "" : String(number / 10000);
const fieldHeader = (field: NextYearBulkField) => `${field.label}${field.unit ? `（${field.unit}）` : ""}`;
const projected = (inputs: NextYearInputs, edits: NextYearEdits) => {
  const result = applyNextYearEdits(inputs, edits);
  if (!result.inputs) throw Object.assign(new Error(Object.values(result.errors)[0] ?? "请先修正已有修改"), { errors: result.errors });
  return result.inputs;
};
function dimensionsFor(target: NextYearBulkTarget) {
  return target === "class" ? ["track", "product"] as const : target === "rates" || target === "bounds" ? rateDimensions
    : target === "finance" || target === "plans" ? [...skuDimensions, "season"] as const : skuDimensions;
}
function rateApplies(inputs: NextYearInputs, rate: NextYearRate, field: string) {
  if (rate.season !== "暑假") return true;
  const cohort = inputs.combinations.filter(row => row.enabled && ["track", "product", "grade", "campus"].every(key => row[key as keyof NextYearCombination] === rate[key as keyof NextYearRate]));
  if (field.startsWith("renewal")) {
    const ids = new Set(cohort.map(row => row.id));
    return inputs.opening_sources.some(row => row.target_id !== null && ids.has(row.target_id));
  }
  return cohort.length > 0 && !cohort.every(effectiveEntry);
}
function scopeRows(target: NextYearBulkTarget, inputs: NextYearInputs, calculation: NextYearCalculation | null | undefined, filters: NextYearBulkFilters, field: string): NextYearBulkRow[] {
  const dimensions = dimensionsFor(target);
  const make = (id: string, row: Partial<Record<keyof NextYearBulkFilters, string>>, values: Record<string, string>, applicable = true, rest: Partial<NextYearBulkRow> = {}): NextYearBulkRow =>
    ({ id, dimensions: row, identity: dimensions.map(key => row[key] ?? ""), values, applicable, ...rest });
  if (target === "rates" || target === "bounds") return inputs.rates.filter(row => matches(row, filters)).map(row => make(row.id, row, Object.fromEntries(definitions[target].fields.map(item => [item.key, percent(row[item.key as keyof NextYearRate] as number | undefined)])), rateApplies(inputs, row, field)));
  if (target === "class") return inputs.class_sizes.flatMap((row, index) => matches(row, filters) ? [make(String(index), row, { minimum: String(row.minimum) }, true, { index })] : []);
  if (target === "finance") return inputs.finance.flatMap(row => {
    const combination = inputs.combinations.find(item => item.id === row.combination_id);
    const dims = combination ? { ...combination, season: row.season } : null;
    return dims && matches(dims, filters) ? [make(row.id, dims, { price_yuan: row.price_yuan, discount_ppm: percent(row.discount_ppm), recognition_ppm: percent(row.recognition_ppm) }, true, { combinationId: combination!.id, season: row.season })] : [];
  });
  if (target === "plans") return inputs.combinations.filter(row => row.enabled).flatMap(combination => nextYearSeasons.flatMap(season => {
    const dims = { ...combination, season };
    if (!matches(dims, filters)) return [];
    const result = calculation?.rows.find(row => row.combination_id === combination.id && row.season === season);
    const eligible = !(season === "暑假" && effectiveEntry(combination));
    const plan = inputs.plans.find(row => row.combination_id === combination.id && row.season === season);
    const planned = eligible && Boolean(result?.planned_new_applied || (!result && combination.added));
    return [make(JSON.stringify([combination.id, season]), dims, { planned_new: plan === undefined ? "" : String(plan.students), manual_new: plan?.locked ? String(plan.students) : "" }, eligible && (field === "manual_new" || planned), { combinationId: combination.id, season })];
  }));
  return inputs.combinations.filter(row => matches(row, filters)).map(row => make(row.id, row, { enabled: String(row.enabled), entry_override: String(effectiveEntry(row)), entry_students: row.entry_students == null ? "" : String(row.entry_students), automatic: "" }, target !== "entry" || field === "entry_override" || effectiveEntry(row), { combinationId: row.id }));
}

type ReferenceColumn = { label: string; value: (row: NextYearBulkRow) => string };
const referenceNumber = (value: unknown) => forecastNumber(value as string | number | null | undefined) === null ? "" : String(value);
const historyLabels: Record<SkuEvidence["historyState"], string> = { value: "有同年级经营基础", missing: "无同年级经营基础", empty: "无同年级经营基础", incomplete: "数据待核对", loading: "读取中", error: "读取失败", ready: "待核对" };
const summerLabels: Record<SkuEvidence["summerState"], string> = { value: "已计算", excluded: "未参与预算", pending: "待重算", unknown: "待核对" };

/** Read-only decision evidence follows the parameter's real grain, never the screen's merged labels. */
function referenceColumns(target: NextYearBulkTarget, inputs: NextYearInputs, calculation: NextYearCalculation | null | undefined, context: NextYearBulkReferenceContext): ReferenceColumn[] {
  const fiscal = context.fiscalYear ?? inputs.fiscal_year;
  const previous = context.sourceFiscalYear ? `FY${String(context.sourceFiscalYear).slice(-2)}` : "编制基准";
  const current = `FY${String(fiscal).slice(-2)}`;
  const column = (label: string, value: ReferenceColumn["value"]): ReferenceColumn => ({ label: `参考·${label}`, value });
  const combo = (row: NextYearBulkRow) => inputs.combinations.find(item => item.id === row.combinationId);
  const rate = (row: NextYearBulkRow) => inputs.rates.find(item => item.id === row.id)!;
  const plan = (row: NextYearBulkRow) => inputs.plans.find(item => item.combination_id === row.combinationId && item.season === row.season);
  const snapshots = new Map<string, { rows: NextYearRow[]; status: string }>();
  const result = (row: NextYearBulkRow) => {
    const cached = snapshots.get(row.id); if (cached) return cached;
    const groups = inputs.combinations.filter(item => item.enabled && (row.combinationId ? item.id === row.combinationId
      : (target === "class" ? ["track", "product"] as const : ["campus", "track", "product", "grade"] as const).every(key => item[key] === row.dimensions[key])));
    const ids = new Set(groups.map(item => item.id)), seasons = row.season ?? row.dimensions.season;
    const selected = (calculation?.rows ?? []).filter(item => ids.has(item.combination_id) && (!seasons || item.season === seasons));
    const deduplicated = new Map<string, NextYearRow>(); let conflict = false;
    for (const item of selected) {
      const key = JSON.stringify([item.combination_id, item.season]), existing = deduplicated.get(key);
      if (existing && ["old_students", "new_students", "total_students", "revenue_yuan", "class_count"].some(field => existing[field as keyof NextYearRow] !== item[field as keyof NextYearRow])) conflict = true;
      else deduplicated.set(key, item);
    }
    const rows = [...deduplicated.values()], expected = groups.length * (seasons ? 1 : nextYearSeasons.length);
    const stale = context.calculationStale || context.entryAllocationStale || target === "class" && context.classResultStale;
    const status = stale ? "待重算" : !groups.length ? "未参与预算" : !calculation ? "待核对" : conflict || rows.length !== expected ? "数据待核对" : "已计算";
    const snapshot = { rows, status }; snapshots.set(row.id, snapshot); return snapshot;
  };
  const sum = (row: NextYearBulkRow, key: "old_students" | "new_students" | "total_students" | "revenue_yuan" | "class_count") => {
    const snapshot = result(row); if (snapshot.status !== "已计算") return "";
    let total = 0, correction = 0;
    for (const item of snapshot.rows) {
      const value = forecastNumber(item[key]); if (value === null) return "";
      const next = total + value; correction += Math.abs(total) >= Math.abs(value) ? total - next + value : value - next + total; total = next;
    }
    return referenceNumber(total + correction);
  };
  const resultColumns = (annual = false) => [
    column(`${annual ? "全年" : "当季"}老生人次`, row => sum(row, "old_students")),
    column(`${annual ? "全年" : "当季"}新生人次`, row => sum(row, "new_students")),
    column(`${annual ? "全年" : "当季"}总人次`, row => sum(row, "total_students")),
    column(`${annual ? "全年" : "当季"}结转收入（元）`, row => sum(row, "revenue_yuan")),
    column("计算结果状态", row => {
      const status = result(row).status;
      return status === "已计算" && ["old_students", "new_students", "total_students", "revenue_yuan"].some(key => sum(row, key as "old_students") === "") ? "数据待核对" : status;
    }),
  ];
  if (target === "sku") return [
    column("来源标签", row => context.skuEvidence?.get(row.id)?.sourceLabel ?? (combo(row)?.added ? "手工新增" : "来源待核对")),
    column(`${previous}结转收入（元）`, row => referenceNumber(context.skuEvidence?.get(row.id)?.historyRevenue)),
    column(`${previous}招生人次`, row => referenceNumber(context.skuEvidence?.get(row.id)?.historyStudents)),
    column(`${previous}经营基础状态`, row => {
      const evidence = context.skuEvidence?.get(row.id);
      return !evidence ? "待核对" : evidence.historyState === "value" && (evidence.historyRevenue === null || evidence.historyStudents === null) ? "数据待核对" : historyLabels[evidence.historyState];
    }),
    column(`${current}暑假老生人次`, row => referenceNumber(context.skuEvidence?.get(row.id)?.summerOld)),
    column("老生承接状态", row => summerLabels[context.skuEvidence?.get(row.id)?.summerState ?? "unknown"]),
    column(`${current}期初结转（元）`, row => referenceNumber(combo(row)?.opening_carryover_yuan)),
    column("当前入口", row => effectiveEntry(combo(row)!) ? "是" : "否"),
    column("当前参与预算", row => combo(row)?.enabled ? "参与" : "不参与"),
  ];
  if (target === "entry") {
    const allocated = (row: NextYearBulkRow) => {
      if (context.entryAllocationStale || context.calculationStale) return "";
      if (calculation?.entry_details) return referenceNumber(calculation.entry_details.find(item => item.combination_id === row.id)?.students);
      const matches = calculation?.rows.filter(item => item.combination_id === row.id && item.season === "暑假" && item.entry) ?? [];
      return matches.length === 1 ? referenceNumber(matches[0].new_students) : "";
    };
    return [column("当前入口", row => effectiveEntry(combo(row)!) ? "是" : "否"), column("当前参与预算", row => combo(row)?.enabled ? "参与" : "不参与"),
      column("部门入口总人次", () => String(inputs.entry_total)), column("来源入口人次", row => referenceNumber(combo(row)?.source_entry_weight)),
      column("当前指定入口人次", row => referenceNumber(combo(row)?.entry_students)), column("已分配入口人次", allocated),
      column("入口分配状态", row => context.entryAllocationStale || context.calculationStale ? "待重算" : !combo(row)?.enabled ? "未参与预算" : !effectiveEntry(combo(row)!) ? "不适用" : allocated(row) === "" ? "待核对" : "已计算"),
      column("当前入口锁定", row => combo(row)?.entry_locked ? "是" : "否"), column("期初结转（元）", row => referenceNumber(combo(row)?.opening_carryover_yuan))];
  }
  if (target === "finance") return [column("当前学费（元）", row => row.values.price_yuan), column("当前折扣（%）", row => row.values.discount_ppm), column("当前当季结转（%）", row => row.values.recognition_ppm), ...resultColumns()];
  if (target === "plans") return [...resultColumns(), column("当前计划新生人次", row => referenceNumber(plan(row)?.students)), column("当前手动锁定", row => plan(row)?.locked ? "是" : "否")];
  if (target === "class") return [column("当前班均（人/班）", row => row.values.minimum), ...resultColumns(true), column("全年班量", row => sum(row, "class_count")), column("班量结果状态", row => result(row).status === "已计算" && sum(row, "class_count") === "" ? "数据待核对" : result(row).status)];
  const rateColumns = [column("当前续班率（%）", row => percent(rate(row).renewal_ppm)), column("当前新生占比（%）", row => percent(rate(row).share_ppm)), column("当前调平锁定", row => rate(row).locked ? "是" : "否")];
  const rateBoundsColumns = (["renewal_lower_ppm", "renewal_upper_ppm", "share_lower_ppm", "share_upper_ppm"] as const).map((key, index) => column(["当前续班率下限（%）", "当前续班率上限（%）", "当前新生占比下限（%）", "当前新生占比上限（%）"][index], row => percent(rate(row)[key])));
  return [...rateColumns, ...rateBoundsColumns, column("上下限空白说明", () => "空白表示未显式设置，沿用现有默认范围"), ...resultColumns()];
}

/** Safe during rendering: malformed existing raw edits are reported without hiding the scope. */
export function getNextYearBulkScope(target: NextYearBulkTarget, inputs: NextYearInputs, calculation: NextYearCalculation | null | undefined, filters: NextYearBulkFilters, field = definitions[target].fields[0].key, edits: NextYearEdits = {}, mode: "uniform" | "paste" = "uniform", referenceContext: NextYearBulkReferenceContext = {}): NextYearBulkScope {
  const definition = definitions[target], selected = definition.fields.find(item => item.key === field) ?? definition.fields[0];
  let current = inputs, validationError: string | undefined;
  try { current = projected(inputs, edits); } catch (failure) { validationError = failure instanceof Error ? failure.message : "请先修正已有修改"; }
  const rows = scopeRows(target, current, calculation, filters, selected.key);
  if (target === "rates" && mode === "paste") for (const row of rows) row.applicable = definition.fields.some(item => rateApplies(current, current.rates.find(rate => rate.id === row.id)!, item.key));
  const applicable = rows.filter(row => row.applicable);
  const identityHeaders = dimensionsFor(target).map(key => headers[key]);
  const references = referenceColumns(target, current, calculation, referenceContext), referenceHeaders = references.map(column => column.label);
  const editableHeaders = target === "rates" ? ["续班率（%）", "新生占比（%）"] : [fieldHeader(selected)];
  const referenceValues = (row: NextYearBulkRow) => references.map(column => column.value(row));
  const template = target === "rates"
    ? [[...identityHeaders, ...referenceHeaders, ...editableHeaders], ...rows.map(row => [...row.identity, ...referenceValues(row), ...definition.fields.map(item => rateApplies(current, current.rates.find(rate => rate.id === row.id)!, item.key) ? row.values[item.key] : "")])]
    : [[...identityHeaders, ...referenceHeaders, ...editableHeaders], ...applicable.map(row => [...row.identity, ...referenceValues(row), selected.options?.find(option => option.value === row.values[selected.key])?.label ?? row.values[selected.key]])];
  const notes = ["修改覆盖当前筛选内的全部适用记录，不限当前页；空白单元格保持不变。", "修改加入当前改动清单，查看影响并保存新版本后才会保存。"];
  if (target === "entry") notes.push("指定人数必须为整数；剩余部门入口人次仍需有自动分配依据。恢复自动分配会清除指定人数和锁定。");
  if (validationError) notes.push(`已有修改待修正：${validationError}`);
  return { target, label: definition.label, scopeLabel: "当前筛选内全部记录", scopeNote: definition.note, fields: definition.fields.map(item => ({ ...item, ...(item.options ? { options: item.options.map(option => ({ ...option })) } : {}) })), rows, candidateCount: rows.length, applicableCount: applicable.length, skippedCount: rows.length - applicable.length, identityHeaders, referenceHeaders, editableHeaders, notes, template, ...(validationError ? { validationError } : {}) };
}

/** Excel-compatible TSV quoting, including quoted tabs and newlines in a dimension name. */
function parseTsv(text: string): string[][] {
  const rows: string[][] = []; let row: string[] = [], cell = "", quoted = false, closed = false;
  for (let index = 0; index < text.length; index++) {
    const char = text[index];
    if (quoted) {
      if (char === '"' && text[index + 1] === '"') { cell += '"'; index++; }
      else if (char === '"') { quoted = false; closed = true; }
      else cell += char;
    } else if (char === '"' && !cell && !closed) quoted = true;
    else if (char === "\t") { row.push(cell); cell = ""; closed = false; }
    else if (char === "\r" || char === "\n") {
      if (char === "\r" && text[index + 1] === "\n") index++;
      row.push(cell); if (row.some(value => value.trim())) rows.push(row); row = []; cell = ""; closed = false;
    } else { if (closed) throw new Error("粘贴格式不正确，带引号的单元格后含多余内容"); cell += char; }
  }
  if (quoted) throw new Error("粘贴格式不正确，单元格引号未结束");
  row.push(cell); if (row.some(value => value.trim())) rows.push(row);
  return rows;
}
function requestsFromPaste(text: string, scope: NextYearBulkScope, field: string): { row: NextYearBulkRow; field: string; value: string }[] {
  const lines = parseTsv(text); if (!lines.length) throw new Error("先粘贴修改内容");
  const normalize = (value: string) => value.trim().replace(/[（(][%％][）)]$/, "");
  const legacy = scope.target === "rates" && lines[0].length === 3;
  const oldHeaders = [...scope.identityHeaders, ...scope.editableHeaders];
  const hasHeader = legacy ? ["参数ID", "ID", "id"].includes(lines[0][0]) : lines[0][0] === scope.identityHeaders[0];
  let expected = legacy ? ["参数ID", "续班率", "新生占比"] : scope.template[0];
  let identityCount = legacy ? 1 : scope.identityHeaders.length;
  let editableStart = legacy ? 1 : scope.identityHeaders.length + scope.referenceHeaders.length;
  if (hasHeader) {
    if (new Set(lines[0].map(normalize)).size !== lines[0].length) throw new Error("表头包含重复列，请重新复制编辑模板");
    // Previously copied templates remain readable; references are never required identity data.
    if (!legacy && lines[0].length === oldHeaders.length && lines[0].every((value, index) => normalize(value) === normalize(oldHeaders[index]))) {
      expected = oldHeaders; editableStart = identityCount;
    }
    if (lines[0].length !== expected.length || !lines[0].every((value, index) => legacy && index === 0 || normalize(value) === normalize(expected[index]))) throw new Error("表头或列顺序不正确，请重新复制粘贴模板");
    lines.shift();
  } else if (scope.target === "rates" && !legacy && lines[0].length === oldHeaders.length) { expected = oldHeaders; editableStart = identityCount; }
  else if (!legacy) throw new Error("表头或列顺序不正确，请保留模板表头");
  const byIdentity = new Map<string, NextYearBulkRow[]>();
  for (const row of scope.rows) { const id = legacy ? row.id : JSON.stringify(row.identity); byIdentity.set(id, [...byIdentity.get(id) ?? [], row]); }
  const seen = new Set<string>(), requests: { row: NextYearBulkRow; field: string; value: string }[] = [];
  for (const [index, cells] of lines.entries()) {
    if (cells.length !== expected.length) throw new Error(`第 ${index + 1} 行列数不正确，请保留模板列数和顺序`);
    const identity = legacy ? cells[0] : JSON.stringify(cells.slice(0, identityCount)), candidates = byIdentity.get(identity) ?? [];
    if (!candidates.length) throw new Error(`第 ${index + 1} 行业务不在当前筛选范围，请核对全部身份列`);
    if (candidates.length !== 1) throw new Error(`第 ${index + 1} 行业务无法唯一匹配，未暂存本次粘贴`);
    const row = candidates[0];
    if (seen.has(row.id)) throw new Error(`第 ${index + 1} 行业务重复，请每组业务只保留一行`);
    seen.add(row.id);
    const fields = scope.target === "rates" ? ["renewal_ppm", "share_ppm"] : [field];
    fields.forEach((key, offset) => { const value = cells[editableStart + offset].trim(); if (value) requests.push({ row, field: key, value }); });
  }
  if (!requests.length) throw new Error("没有可修改的值；空白表示保持不变");
  return requests;
}
function validateCrossFields(inputs: NextYearInputs) {
  if (!inputs.combinations.some(row => row.enabled)) throw new Error("至少保留一个参与预算的SKU");
  for (const row of inputs.combinations) {
    if (!row.enabled && (forecastNumber(row.opening_carryover_yuan) ?? 0) > 0) throw new Error("有期初结转的SKU不能取消参与预算");
    if (!effectiveEntry(row) && row.entry_students != null) throw new Error("非入口SKU不能指定入口人次");
    if (row.entry_locked && row.entry_students == null) throw new Error("请先填写入口人次再锁定");
  }
  const entries = inputs.combinations.filter(row => row.enabled && effectiveEntry(row));
  const specified = entries.reduce((total, row) => total + (row.entry_students ?? 0), 0), remaining = inputs.entry_total - specified;
  if (remaining < 0) throw new Error("已指定入口人次超过部门入口总量");
  if (remaining > 0 && !entries.some(row => row.entry_students == null && row.source_entry_weight > 0)) throw new Error("剩余入口人次没有分配依据，请填写入口SKU人次或调整部门总量");
  for (const row of inputs.rates) for (const kind of ["renewal", "share"] as const) {
    const value = row[`${kind}_ppm`], lower = row[`${kind}_lower_ppm`] ?? 0, upper = row[`${kind}_upper_ppm`] ?? (kind === "renewal" ? 1000000 : 999900);
    if (value < lower || value > upper) throw new Error("当前招生参数必须位于设置的上下限内");
  }
}

/** Pure, all-or-nothing staging. It performs no requests, recalculation or persistence. */
export function buildNextYearBulkChange(options: NextYearBulkChangeOptions): NextYearBulkChange {
  const { target, field, mode, inputs, edits, calculation, filters } = options;
  const selected = definitions[target].fields.find(item => item.key === field);
  if (!selected) throw new Error("不支持的批量修改字段");
  const before = projected(inputs, edits), scope = getNextYearBulkScope(target, before, calculation, filters, field, {}, mode, options.referenceContext);
  if (!scope.applicableCount) throw new Error("当前筛选范围没有适用记录");
  if (selected.action && mode !== "uniform") throw new Error("恢复自动分配请使用统一设置");
  const value = options.value?.trim() ?? "";
  if (mode === "uniform" && !value && !selected.action) throw new Error("先填写批量修改值");
  const requests = mode === "paste" ? requestsFromPaste(options.paste ?? "", scope, field) : scope.rows.filter(row => row.applicable).map(row => ({ row, field, value }));
  const nextInputs = structuredClone(inputs), nextEdits = { ...edits };
  let changedCount = 0;
  for (const request of requests) {
    const metadata = definitions[target].fields.find(item => item.key === request.field)!;
    const raw = metadata.unit === "%" ? request.value.replace(/[%％]$/, "").trim() : request.value;
    let applicable = request.row.applicable;
    if (target === "rates") applicable = rateApplies(before, before.rates.find(row => row.id === request.row.id)!, request.field);
    const boolean = metadata.options?.find(option => option.value === raw || option.label === raw)?.value;
    if (metadata.options && boolean === undefined) throw new Error(`${metadata.label}请明确选择${metadata.options.map(option => `“${option.label}”`).join("或")}`);
    const normalized = boolean ?? raw;
    if (!applicable) {
      if (normalized === request.row.values[request.field]) continue;
      throw new Error(`当前范围有不适用“${metadata.label}”的记录，整批未应用`);
    }
    // Equal values do not create unnecessary pending edits. Numeric validation still uses the existing parser.
    if (!metadata.action && normalized === request.row.values[request.field]) continue;
    if (target === "sku" || target === "entry" && request.field === "entry_override") {
      const row = nextInputs.combinations.find(item => item.id === request.row.id)!;
      if (target === "sku") row.enabled = normalized === "true";
      else {
        row.entry_override = normalized === "true";
        if (!row.entry_override) clearEntry(row);
      }
    } else if (target === "entry" && request.field === "automatic") {
      const previous = before.combinations.find(row => row.id === request.row.id)!;
      if (previous.entry_students == null && !previous.entry_locked) continue;
      clearEntry(nextInputs.combinations.find(row => row.id === request.row.id)!);
    } else {
      if (target === "entry") materializeGrade(request.row.dimensions.grade!);
      if (target === "plans") {
        const previous = before.plans.find(row => row.combination_id === request.row.combinationId && row.season === request.row.season);
        if (previous) {
          const position = nextInputs.plans.findIndex(row => row.combination_id === previous.combination_id && row.season === previous.season);
          if (position < 0) nextInputs.plans.push({ ...previous }); else nextInputs.plans[position] = { ...previous };
        }
        delete nextEdits[nextYearEditKey("plan", request.row.combinationId!, request.row.season!)];
        delete nextEdits[nextYearEditKey("manual", request.row.combinationId!, request.row.season!)];
      }
      const section = target === "plans" ? request.field === "manual_new" ? "manual" : "plan" : target === "entry" ? "entry-count" : target;
      const path = target === "class" ? [section, request.row.index!] : target === "plans" ? [section, request.row.combinationId!, request.row.season!] : target === "entry" ? [section, request.row.id] : [section, request.row.id, request.field];
      const editKey = nextYearEditKey(...path);
      delete nextEdits[editKey]; nextEdits[editKey] = raw;
    }
    changedCount++;
  }
  function materializeGrade(grade: string) {
    // A grade edit is a collective allocation. Preserve its already-projected values on other SKUs
    // before dropping it, so a single-row override never loses unrelated unsaved changes.
    const gradeKey = nextYearEditKey("entry-grade", grade);
    if (Object.hasOwn(nextEdits, gradeKey)) {
      for (const member of nextInputs.combinations.filter(item => item.grade === grade)) {
        const projectedMember = before.combinations.find(item => item.id === member.id)!;
        member.entry_students = projectedMember.entry_students;
        member.entry_locked = projectedMember.entry_locked;
        // These per-SKU edits may have preceded the grade edit; keeping them would
        // replay an obsolete value over the materialized final grade allocation.
        delete nextEdits[nextYearEditKey("entry-count", member.id)];
      }
      delete nextEdits[gradeKey];
    }
  }
  function clearEntry(row: NextYearCombination) {
    materializeGrade(row.grade);
    delete nextEdits[nextYearEditKey("entry-count", row.id)];
    row.entry_students = null; row.entry_locked = false;
  }
  const effectiveInputs = projected(nextInputs, nextEdits);
  validateCrossFields(effectiveInputs);
  return { inputs: nextInputs, edits: nextEdits, effectiveInputs, changedCount, scope };
}
