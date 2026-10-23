import { identityHeaders, newRequestId, parseResponse, type ApiIdentity } from "./api.ts";
import { forecastNumber, forecastSeasons, type ForecastSeason } from "./forecastApi.ts";

export { forecastSeasons as nextYearSeasons };
export type NextYearNumber = string | number | null;
export interface ForecastBasis {
  id: string; sequence: number; as_of: string; status: "CONFIRMED";
  forecast_revenue: string; forecast_total: number;
  calculation_hash: string; snapshot_hash: string; baseline_hash: string;
  fiscal_year?: number;
}
export interface NextYearCombination {
  id: string; track: string; product: string; grade: string; subject: string; campus: string;
  entry: boolean; enabled: boolean; source_entry_weight: number;
  source_new_by_season: Record<ForecastSeason, number>; opening_carryover_yuan: string;
  entry_override?: boolean | null; entry_students?: number | null; entry_locked?: boolean; added?: boolean; reason?: string;
}
export interface NextYearRate {
  id: string; season: ForecastSeason; track: string; product: string; grade: string; campus: string;
  renewal_ppm: number; share_ppm: number;
  locked?: boolean; renewal_lower_ppm?: number; renewal_upper_ppm?: number; share_lower_ppm?: number; share_upper_ppm?: number;
}
export interface NextYearFinance { id: string; combination_id: string; season: ForecastSeason; price_yuan: string; discount_ppm: number; recognition_ppm: number }
export interface NextYearInputs {
  fiscal_year: number; source_as_of: string; target_revenue_yuan: string | null; track_targets: Record<string, string | null>;
  entry_total: number; tax_factor: string; combinations: NextYearCombination[]; rates: NextYearRate[];
  /** minimum is the shared 班均; planned_class_size is frozen legacy input, ignored by new calculations. */
  finance: NextYearFinance[]; class_sizes: { track: string; product: string; minimum: number; planned_class_size?: number | null }[];
  plans: { combination_id: string; season: ForecastSeason; students: number; locked?: boolean; reason?: string }[];
  opening_sources: { track: string; product: string; grade: string; subject: string; campus: string; students: number; target_id: string | null; exclusion: string | null }[];
  notes: Record<string, unknown>[];
}
export interface NextYearTotals {
  revenue_yuan: NextYearNumber; target_revenue_yuan: NextYearNumber; gap_yuan: NextYearNumber;
  total_students: number | null; old_students: number | null; new_students: number | null; attainment: NextYearNumber;
  class_count?: number | null;
}
export interface NextYearRow {
  combination_id: string; season: ForecastSeason; track: string; product: string; grade: string; subject: string; campus: string; entry: boolean;
  old_students: number | null; new_students: number | null; total_students: number | null; revenue_yuan: NextYearNumber;
  price_yuan: NextYearNumber; discount_ppm: number | null; renewal_ppm: number | null; share_ppm: number | null; class_size_status: string;
  planned_new_applied: boolean;
  class_count?: number | null; class_average?: number | null; planned_class_size?: number | null; manual_locked?: boolean;
}
export interface NextYearCalculation {
  fiscal_year: number; rule_versions: Record<string, string>; annual: NextYearTotals;
  quarters: (NextYearTotals & { season: ForecastSeason })[]; tracks: (NextYearTotals & { track: string })[];
  campuses: (NextYearTotals & { campus: string })[]; rows: NextYearRow[];
  entry_allocations: { campus: string; students: number; source_weight: number }[];
  entry_details?: { combination_id: string; campus: string; track: string; product: string; grade: string; subject: string; students: number; locked: boolean }[];
  opening: { spring_students: number; eligible_students: number; graduated_students: number; subject_ended_students: number; summer_old_students: number; carryover_yuan: string };
  issues: { code: string; message: string; season?: string; combination_id?: string; severity: "WARNING" | "BLOCKING"; [key: string]: unknown }[];
  complete: boolean; publication_available: false;
}
/** Frozen versions without this rule keep their original results until explicitly recalculated. */
export const nextYearUsesClassAverage = (calculation: NextYearCalculation) => calculation.rule_versions.class_count === "class-average-ceiling-v1";
export interface NextYearDraft {
  id: string; sequence: number; parent_id: string | null; status: "DRAFT"; source: ForecastBasis;
  inputs: NextYearInputs; calculation: NextYearCalculation; created_at: string;
  created_by?: string; reason?: string; operation?: string; restored_from_id?: string | null;
  /** Read-only projection from the validated frozen source; not a field to persist into source. */
  source_fiscal_year?: number | null;
}
export interface NextYearEnvelope {
  source: ForecastBasis | null; draft: NextYearDraft | null; source_changed: boolean;
  history: { id: string; sequence: number; created_at: string; source_fiscal_year?: number | null; source_sequence?: number | null; [key: string]: unknown }[];
  latest_draft_id?: string; read_only?: boolean;
}
export interface NextYearBasisPreview {
  read_only: true; fiscal_year: number; parent_id: string | null; source_id: string;
  source: ForecastBasis; inputs: NextYearInputs; calculation: NextYearCalculation;
}
export interface NextYearCatalog {
  source_fiscal_year: number; sources: ForecastBasis[];
  fiscal_years: { fiscal_year: number; has_draft: boolean; latest_sequence?: number | null; [key: string]: unknown }[];
  default_fiscal_year: number;
}
export interface NextYearBalanceResult {
  status: string; message?: string; proposed_inputs: NextYearInputs | null; calculation: NextYearCalculation;
  impact?: Record<string, unknown>; constraints?: { class_issues?: NextYearCalculation["issues"]; class_diagnostics?: { code: string; count: number; message: string; action: string; [key: string]: unknown }[]; [key: string]: unknown }; attempts?: unknown[];
}
export interface NextYearRateBoundary {
  id: string; current_ppm: number; lower_bound_ppm: number; upper_bound_ppm: number; locked: boolean;
  lock_source: string | null; bound_source: string;
}
export interface NextYearClassReadinessRow {
  code: string; combination_id: string; season: string | null; track: string; product: string; campus: string; grade: string; subject: string;
  current_students: number | null; old_students: number | null; new_students: number | null; minimum: number | null; gap: number | null;
  carryover_yuan: NextYearNumber; closure_allowed: boolean | null; plan_locked: boolean; seed_plan: boolean; entry_locked: boolean; rate_locked: boolean;
  combination_locked: boolean; lock_reasons: string[];
  rate_parameters: { season: string; rate_id: string; renewal: NextYearRateBoundary; share: NextYearRateBoundary }[];
  bound_proof?: { scope: string; entry_adjustment: boolean; lower_students: number; upper_students: number; minimum: number; unreachable: boolean; message: string } | null;
  actions: string[];
}
export interface NextYearClassReadiness {
  source: ForecastBasis; calculation_complete: boolean; blocking_issue_count: number; issue_count: number; combination_count: number; closure_allowed_count: number; carry_protected_count: number;
  rows: NextYearClassReadinessRow[];
}
export interface NextYearClassRepairAction {
  kind: "ENTRY_REALLOCATION" | "NEW_STUDENT_TARGET" | "RATE_BOUNDARY" | "RENEWAL_ADJUSTMENT";
  combination_id?: string; season?: string | null; track?: string; product?: string; campus?: string; grade?: string; subject?: string;
  field: string; before: NextYearNumber; after: NextYearNumber; reason: string;
  before_input?: NextYearNumber; before_total_students?: number; after_total_students?: number; old_students?: number; minimum?: number;
  additional_students?: number; stable_old_floor?: number; stability_reserve_students?: number; plan_is_not_actual?: boolean; manual_target_priority?: boolean; [key: string]: unknown;
}
export interface NextYearClassRepairProblem {
  code: string; message: string; combination_id?: string; season?: string | null; track?: string; product?: string; campus?: string; grade?: string; subject?: string;
  [key: string]: unknown;
}
export interface NextYearClassRepairResult {
  source: ForecastBasis; status: "READY" | "PARTIAL"; message: string; proposed_inputs: NextYearInputs | null;
  before_calculation: NextYearCalculation; calculation: NextYearCalculation; impact: Record<string, unknown>;
  actions: NextYearClassRepairAction[]; issue_count: { before: number; after: number };
  before_issues: NextYearCalculation["issues"]; after_issues: NextYearCalculation["issues"]; unresolved: NextYearClassRepairProblem[]; lock_conflicts: NextYearClassRepairProblem[];
  stability_unresolved?: NextYearClassRepairProblem[]; adjustable_range_class_safe?: boolean;
  stability_scope?: { entry_allocation_fixed: boolean; rate_bounds: string; user_and_zero_old_seed_rate_locks_preserved: boolean; integer_rounding_guard: string; not_a_revenue_target_reachability_proof: boolean };
  entry_soft_deviations: { combination_id: string; track: string; product: string; campus: string; grade: string; subject: string; before: number; after: number; soft_lower: number; soft_upper: number; deviation_students: number; reason: string }[];
  summary: { entry_reallocated_students: number; new_target_count: number; additional_new_students: number; changed_rate_bound_count: number; stability_reserve_students?: number };
  constraints: Record<string, boolean>; input_changes?: Record<string, unknown>;
}

const baseFor = (year = 2028) => `/api/v1/next-year-budgets/${year < 2100 ? `fy${String(year).slice(-2)}` : year}`;
async function response<T>(value: Response): Promise<T> {
  if (!value.ok) {
    const payload = await value.clone().json().catch(() => null) as { detail?: unknown } | null;
    if (typeof payload?.detail === "string") throw new Error(payload.detail);
  }
  return parseResponse<T>(value);
}
export async function getNextYearBudget(identity: ApiIdentity, signal?: AbortSignal, year = 2028): Promise<NextYearEnvelope> {
  return response(await fetch(`${baseFor(year)}/current`, { headers: identityHeaders(identity), signal }));
}
async function command<T>(path: string, identity: ApiIdentity, body: unknown, year = 2028): Promise<T> {
  return response(await fetch(`${baseFor(year)}${path}`, { method: "POST", headers: { ...identityHeaders(identity), "Content-Type": "application/json", "Idempotency-Key": newRequestId() }, body: JSON.stringify(body) }));
}
export function prepareNextYearBudget(identity: ApiIdentity, forecastId: string, parentId: string | null, year = 2028): Promise<NextYearEnvelope> {
  return command("/prepare", identity, { forecast_id: forecastId, parent_id: parentId, reason: `基于已确认 FY${String(year - 1).slice(-2)} 预测生成 FY${String(year).slice(-2)} 预算草稿` }, year);
}
/** Read-only new-basis calculation; creating a version remains an explicit prepare call. */
export async function previewNextYearBasis(identity: ApiIdentity, forecastId: string, parentId: string | null, year = 2028): Promise<NextYearBasisPreview> {
  return response(await fetch(`${baseFor(year)}/prepare-preview`, { method: "POST", headers: { ...identityHeaders(identity), "Content-Type": "application/json" }, body: JSON.stringify({ forecast_id: forecastId, parent_id: parentId }) }));
}
export function reviseNextYearBudget(identity: ApiIdentity, parentId: string, inputs: NextYearInputs, reason: string): Promise<NextYearEnvelope> {
  return command("/revise", identity, { parent_id: parentId, inputs, reason }, inputs.fiscal_year);
}
export function previewNextYearBudget(identity: ApiIdentity, parentId: string, inputs: NextYearInputs): Promise<{ source: ForecastBasis; calculation: NextYearCalculation }> {
  return command("/preview", identity, { parent_id: parentId, inputs }, inputs.fiscal_year);
}
export async function getNextYearCatalog(identity: ApiIdentity, signal?: AbortSignal): Promise<NextYearCatalog> {
  return response(await fetch("/api/v1/next-year-budgets/catalog", { headers: identityHeaders(identity), signal }));
}
export async function getNextYearVersion(identity: ApiIdentity, versionId: string, year = 2028): Promise<NextYearEnvelope> {
  return response(await fetch(`${baseFor(year)}/versions/${encodeURIComponent(versionId)}`, { headers: identityHeaders(identity) }));
}
export function restoreNextYearVersion(identity: ApiIdentity, parentId: string, versionId: string, reason: string, year = 2028): Promise<NextYearEnvelope> {
  return command("/restore", identity, { parent_id: parentId, version_id: versionId, reason }, year);
}
export function balanceNextYearBudget(identity: ApiIdentity, parentId: string, inputs: NextYearInputs, options: Record<string, unknown> = {}): Promise<NextYearBalanceResult> {
  return command("/balance", identity, { parent_id: parentId, inputs, options }, inputs.fiscal_year);
}
export function checkNextYearClassReadiness(identity: ApiIdentity, parentId: string, inputs: NextYearInputs): Promise<NextYearClassReadiness> {
  return command("/class-readiness", identity, { parent_id: parentId, inputs }, inputs.fiscal_year);
}
export function proposeNextYearClassRepair(identity: ApiIdentity, parentId: string, inputs: NextYearInputs): Promise<NextYearClassRepairResult> {
  return command("/class-repair", identity, { parent_id: parentId, inputs }, inputs.fiscal_year);
}

export type NextYearEdits = Record<string, string>;
export const nextYearEditKey = (...path: (string | number)[]) => JSON.stringify(path);
const moneyUnits = (value: string) => {
  const match = /^\+?(\d*)(?:\.(\d*))?(?:e([+-]?\d+))?$/i.exec(value.trim());
  if (!match || !(match[1] || match[2])) throw new Error("收入目标须为非负数");
  const integer = BigInt(`${match[1]}${match[2] ?? ""}`), shift = 6 + Number(match[3] ?? 0) - (match[2]?.length ?? 0);
  if (integer === 0n || (shift < 0 && -shift > integer.toString().length)) return 0n;
  if (!Number.isSafeInteger(shift) || shift > 32) throw new Error("收入目标超出允许范围");
  if (shift >= 0) return integer * 10n ** BigInt(shift);
  const divisor = 10n ** BigInt(-shift); return (integer + divisor / 2n) / divisor;
};
const moneyString = (value: bigint) => `${value / 1000000n}.${String(value % 1000000n).padStart(6, "0")}`;
/** Goal allocation only. Never changes student counts, income, or any saved draft. */
export function allocateNextYearTrackTargets(total: string, original: Record<string, string>, decimals: 2 | 6 = 6): Record<string, string> {
  const number = forecastNumber(total); if (number === null || number < 0 || number > 1000000000000) throw new Error("收入目标须为非负数且不超过允许范围");
  const scale = decimals === 2 ? 10000n : 1n, target = (moneyUnits(total) + scale / 2n) / scale, names = Object.keys(original).sort(), weights = names.map(name => moneyUnits(original[name]));
  if (!names.length) throw new Error("没有可分配的赛道");
  let denominator = weights.reduce((sum, value) => sum + value, 0n);
  if (denominator === 0n) { weights.fill(1n); denominator = BigInt(names.length); }
  const allocations = names.map((name, index) => ({ name, value: target * weights[index] / denominator, remainder: target * weights[index] % denominator }));
  let remaining = target - allocations.reduce((sum, item) => sum + item.value, 0n);
  for (const item of [...allocations].sort((a, b) => a.remainder === b.remainder ? a.name.localeCompare(b.name) : a.remainder > b.remainder ? -1 : 1)) {
    if (remaining === 0n) break; item.value += 1n; remaining -= 1n;
  }
  return Object.fromEntries(allocations.map(item => [item.name, decimals === 2 ? moneyString(item.value * scale).slice(0, -4) : moneyString(item.value)]));
}
export function applyNextYearEdits(original: NextYearInputs, edits: NextYearEdits): { inputs: NextYearInputs | null; errors: Record<string, string> } {
  const inputs = structuredClone(original), errors: Record<string, string> = {};
  const fail = (key: string, message: string) => { errors[key] = message; };
  for (const [key, raw] of Object.entries(edits)) {
    let path: (string | number)[];
    try { path = JSON.parse(key); } catch { fail(key, "无效编辑项"); continue; }
    if (!Array.isArray(path)) { fail(key, "无效编辑项"); continue; }
    const [section, id, field] = path, number = forecastNumber(raw), validMoney = number !== null && number >= 0 && number <= 1000000000000;
    if (section === "target") { if (!raw.trim()) inputs.target_revenue_yuan = null; else if (!validMoney) fail(key, "经营目标须为非负数"); else inputs.target_revenue_yuan = raw.trim(); }
    else if (section === "track-target") { if (!Object.hasOwn(inputs.track_targets, id)) fail(key, "赛道不存在"); else if (!raw.trim()) inputs.track_targets[id] = null; else if (!validMoney) fail(key, "赛道目标须为非负数"); else inputs.track_targets[id] = raw.trim(); }
    else if (section === "entry") { if (number === null || !Number.isSafeInteger(number) || number < 0 || number > 10000000) fail(key, "入口人数须为0至1000万的整数"); else inputs.entry_total = number; }
    else if (section === "tax") { if (number === null || number <= 0 || number > 10) fail(key, "转换系数须大于0且不超过10"); else inputs.tax_factor = raw.trim(); }
    else if (section === "rates" || section === "finance") {
      const item = section === "rates" ? inputs.rates.find(row => row.id === id) : inputs.finance.find(row => row.id === id);
      const fields = section === "rates" ? ["renewal_ppm", "share_ppm"] : ["price_yuan", "discount_ppm", "recognition_ppm"];
      if (!item || !fields.includes(String(field))) { fail(key, "参数项不存在"); continue; }
      if (field === "price_yuan") { if (!validMoney || number! > 1000000000) fail(key, "学费须为0至10亿元"); else (item as NextYearFinance).price_yuan = raw.trim(); }
      else {
        const match = /^(\d+)(?:\.(\d{0,4}))?$/.exec(raw.trim());
        const ppm = match ? Number(match[1]) * 10000 + Number((match[2] ?? "").padEnd(4, "0")) : null;
        if (ppm === null || !Number.isSafeInteger(ppm) || ppm < 0 || ppm > 1000000 || (field === "share_ppm" && ppm > 999900)) fail(key, field === "share_ppm" ? "新生占比须在0%至99.99%" : "比例须在0%至100%，最多4位小数");
        else if (section === "rates" && ppm % 100 !== 0) fail(key, "按0.01个百分点填写");
        else if (field === "discount_ppm" && ppm % 1000 !== 0) fail(key, "折扣按0.1个百分点填写");
        else Object.assign(item, { [String(field)]: ppm });
      }
    } else if (section === "entry-count") {
      const item = inputs.combinations.find(row => row.id === id);
      if (!item) fail(key, "SKU不存在");
      else if (!raw.trim()) { item.entry_students = null; item.entry_locked = false; }
      else if (number === null || !Number.isSafeInteger(number) || number < 0 || number > 10000000) fail(key, "人数须为0至1000万的整数");
      else { item.entry_students = number; item.entry_locked = true; }
    } else if (section === "entry-grade") {
      const members = inputs.combinations.filter(row => row.enabled && row.grade === id && (row.entry_override ?? row.entry)).sort((a, b) => a.id.localeCompare(b.id));
      if (!raw.trim() && members.length) { for (const item of members) { item.entry_students = null; item.entry_locked = false; } continue; }
      if (!members.length || number === null || !Number.isSafeInteger(number) || number < 0 || number > 10000000) { fail(key, "年级入口须为0至1000万的整数，并至少选择一个入口业务"); continue; }
      const originalWeight = members.reduce((sum, row) => sum + row.source_entry_weight, 0), denominator = originalWeight || members.length;
      const allocations = members.map(row => { const numerator = number * (originalWeight ? row.source_entry_weight : 1); return { row, value: Math.floor(numerator / denominator), remainder: numerator % denominator }; });
      let remainder = number - allocations.reduce((sum, item) => sum + item.value, 0);
      for (const item of [...allocations].sort((a, b) => b.remainder - a.remainder || a.row.id.localeCompare(b.row.id))) { if (!remainder) break; item.value++; remainder--; }
      for (const item of allocations) { item.row.entry_students = item.value; item.row.entry_locked = true; }
    } else if (section === "class") {
      const item = inputs.class_sizes[Number(id)];
      if (!item || number === null || !Number.isSafeInteger(number) || number < 1 || number > 10000) fail(key, "班均须为1至10000的整数");
      else item.minimum = number;
    } else if (section === "class-size") {
      fail(key, "计划班额已合并为班均，请修改班均");
    } else if (section === "plan" || section === "manual") {
      if (!inputs.combinations.some(row => row.id === id) || !forecastSeasons.includes(field as ForecastSeason)) { fail(key, "计划明细不存在"); continue; }
      if (number === null || !Number.isSafeInteger(number) || number < 0 || number > 10000000) { fail(key, "计划新生须为0至1000万的整数"); continue; }
      const plan = inputs.plans.find(row => row.combination_id === id && row.season === field);
      if (plan) { plan.students = number; if (section === "manual") { plan.locked = true; plan.reason = "预算手动调节"; } }
      else inputs.plans.push({ combination_id: String(id), season: field as ForecastSeason, students: number, ...(section === "manual" ? { locked: true, reason: "预算手动调节" } : {}) });
    } else if (section === "bounds") {
      const item = inputs.rates.find(row => row.id === id);
      const allowed = ["renewal_lower_ppm", "renewal_upper_ppm", "share_lower_ppm", "share_upper_ppm"];
      const ppm = number === null ? null : Math.round(number * 10000);
      if (item && allowed.includes(String(field)) && !raw.trim()) { delete (item as unknown as Record<string, unknown>)[String(field)]; continue; }
      if (!item || !allowed.includes(String(field)) || ppm === null || ppm % 100 !== 0 || ppm < 0 || ppm > (String(field).startsWith("share") ? 999900 : 1000000)) fail(key, "调整范围按0.01个百分点填写，且不能超过比例上限");
      else Object.assign(item, { [String(field)]: ppm });
    } else fail(key, "此字段不能修改");
  }
  if (!Object.keys(errors).length) {
    for (const rate of inputs.rates) {
      if ((rate.renewal_lower_ppm ?? Math.max(0, rate.renewal_ppm - 50000)) > (rate.renewal_upper_ppm ?? Math.min(1000000, rate.renewal_ppm + 50000))) fail(nextYearEditKey("bounds", rate.id, "renewal_lower_ppm"), "续班率下限不能高于上限");
      if ((rate.share_lower_ppm ?? Math.max(0, rate.share_ppm - 50000)) > (rate.share_upper_ppm ?? Math.min(999900, rate.share_ppm + 50000))) fail(nextYearEditKey("bounds", rate.id, "share_lower_ppm"), "新生占比下限不能高于上限");
    }
    const targets = Object.entries(inputs.track_targets), specified = targets.filter(([, value]) => value !== null);
    if (inputs.target_revenue_yuan === null) {
      if (specified.length) fail(nextYearEditKey("target"), "先填写全年经营目标");
    } else if (specified.length && specified.length !== targets.length) {
      const missing = targets.find(([, value]) => value === null)!;
      fail(nextYearEditKey("track-target", missing[0]), "填写全部赛道目标，或全部留空");
    } else if (specified.length) {
      try { const difference = specified.reduce((sum, [, value]) => sum + moneyUnits(value!), 0n) - moneyUnits(inputs.target_revenue_yuan);
        if (difference > 10000n || difference < -10000n) fail(nextYearEditKey("target"), "赛道目标合计须等于全年经营目标");
      } catch { fail(nextYearEditKey("target"), "经营目标须为有效数字"); }
    }
  }
  return { inputs: Object.keys(errors).length ? null : inputs, errors };
}

export type NextYearEditCategory = "target" | "track_target" | "entry_total" | "entry_detail" | "rates" | "finance" | "class_size" | "plan" | "bounds" | "tax" | "other";
export interface NextYearEditItem {
  key: string;
  category: NextYearEditCategory;
  task: 1 | 2 | 3 | 4 | 5;
  label: string;
  beforeDisplay: string | null;
  afterDisplay: string;
  beforeNumber: number | null;
  afterNumber: number | null;
  unit: "money" | "percent" | "count" | "factor";
  invalid?: boolean;
}
export interface NextYearEditGroup {
  category: NextYearEditCategory;
  label: string;
  items: NextYearEditItem[];
  scale?: string;
}
export interface NextYearEditSummary {
  groups: NextYearEditGroup[];
  totalCount: number;
}

const editCategoryOrder: { category: NextYearEditCategory; label: string }[] = [
  { category: "target", label: "全年经营目标" },
  { category: "track_target", label: "赛道目标" },
  { category: "entry_total", label: "年度入口人次" },
  { category: "entry_detail", label: "入口分配" },
  { category: "rates", label: "续班率与新生占比" },
  { category: "bounds", label: "调平范围" },
  { category: "plan", label: "计划新生" },
  { category: "finance", label: "学费 / 折扣 / 结转" },
  { category: "class_size", label: "班均参数" },
  { category: "tax", label: "含税转换系数" },
  { category: "other", label: "其他" },
];

const formatMoney = (n: number | null, suffix = "元") => n === null ? null : `${n.toLocaleString("zh-CN", { maximumFractionDigits: 2 })} ${suffix}`;
const formatCount = (n: number | null) => n === null ? null : `${n.toLocaleString("zh-CN")} 人`;
const formatPercent = (ppm: number | null) => ppm === null ? null : `${(ppm / 10000).toLocaleString("zh-CN", { maximumFractionDigits: 2 })} %`;
const parsePercent = (raw: string): number | null => { const trimmed = raw.replace(/%$/, "").trim(); if (!trimmed) return null; const n = forecastNumber(trimmed); return n === null ? null : n; };
const summarizeDimensions = (row: { campus?: string; track?: string; product?: string; grade?: string; subject?: string } | undefined) =>
  row ? [row.campus, row.track, row.product, row.grade, row.subject].filter(Boolean).join(" / ") : "";

const boundsLabels: Record<string, string> = {
  renewal_lower_ppm: "续班率下限", renewal_upper_ppm: "续班率上限",
  share_lower_ppm: "新生占比下限", share_upper_ppm: "新生占比上限",
};
const financeLabels: Record<string, string> = { price_yuan: "学费", discount_ppm: "折扣", recognition_ppm: "当季结转" };

/** Pure: classify every unsaved edit into a display-ready group. Does not re-run calculation. */
export function summarizeNextYearEdits(inputs: NextYearInputs, edits: NextYearEdits, errors: Record<string, string> = {}): NextYearEditSummary {
  const items: NextYearEditItem[] = [];
  const comboById = new Map(inputs.combinations.map(row => [row.id, row]));
  const rateById = new Map(inputs.rates.map(row => [row.id, row]));
  const financeById = new Map(inputs.finance.map(row => [row.id, row]));
  for (const [editKey, raw] of Object.entries(edits)) {
    let path: (string | number)[];
    try { path = JSON.parse(editKey); } catch { continue; }
    if (!Array.isArray(path)) continue;
    const [section, id, field] = path;
    const invalid = Object.hasOwn(errors, editKey);
    const trimmed = raw.trim();
    const parsedNumber = forecastNumber(raw);
    const base = { key: editKey, invalid };
    if (section === "target") {
      items.push({ ...base, category: "target", task: 1, label: "全年经营目标",
        beforeDisplay: formatMoney(forecastNumber(inputs.target_revenue_yuan)),
        afterDisplay: trimmed ? formatMoney(parsedNumber) ?? trimmed : "（清空）",
        beforeNumber: forecastNumber(inputs.target_revenue_yuan), afterNumber: parsedNumber, unit: "money" });
    } else if (section === "track-target") {
      items.push({ ...base, category: "track_target", task: 1, label: `${id} 目标`,
        beforeDisplay: formatMoney(forecastNumber(inputs.track_targets[String(id)])),
        afterDisplay: trimmed ? formatMoney(parsedNumber) ?? trimmed : "（清空）",
        beforeNumber: forecastNumber(inputs.track_targets[String(id)]), afterNumber: parsedNumber, unit: "money" });
    } else if (section === "entry") {
      items.push({ ...base, category: "entry_total", task: 2, label: "年度入口人次",
        beforeDisplay: formatCount(inputs.entry_total),
        afterDisplay: trimmed ? formatCount(parsedNumber) ?? trimmed : "（清空）",
        beforeNumber: inputs.entry_total, afterNumber: parsedNumber, unit: "count" });
    } else if (section === "entry-count") {
      const combo = comboById.get(String(id));
      items.push({ ...base, category: "entry_detail", task: 2, label: `${summarizeDimensions(combo)} 入口人次`,
        beforeDisplay: formatCount(combo?.entry_students ?? null),
        afterDisplay: trimmed ? formatCount(parsedNumber) ?? trimmed : "（自动分配）",
        beforeNumber: combo?.entry_students ?? null, afterNumber: parsedNumber, unit: "count" });
    } else if (section === "entry-grade") {
      items.push({ ...base, category: "entry_detail", task: 2, label: `${id} 整年级入口人次`,
        beforeDisplay: null,
        afterDisplay: trimmed ? formatCount(parsedNumber) ?? trimmed : "（自动分配）",
        beforeNumber: null, afterNumber: parsedNumber, unit: "count" });
    } else if (section === "rates") {
      const rate = rateById.get(String(id));
      const isRenewal = field === "renewal_ppm";
      const currentPpm = isRenewal ? rate?.renewal_ppm ?? null : rate?.share_ppm ?? null;
      const parsedPct = parsePercent(raw);
      items.push({ ...base, category: "rates", task: 3,
        label: `${summarizeDimensions(rate)} · ${rate?.season ?? ""} ${isRenewal ? "续班率" : "新生占比"}`,
        beforeDisplay: formatPercent(currentPpm),
        afterDisplay: trimmed ? `${trimmed.replace(/%$/, "")} %` : "（清空）",
        beforeNumber: currentPpm === null ? null : currentPpm / 10000,
        afterNumber: parsedPct, unit: "percent" });
    } else if (section === "bounds") {
      const rate = rateById.get(String(id));
      const currentPpm = (rate as unknown as Record<string, number | undefined> | undefined)?.[String(field)] ?? null;
      const parsedPct = parsePercent(raw);
      items.push({ ...base, category: "bounds", task: 3,
        label: `${summarizeDimensions(rate)} · ${rate?.season ?? ""} ${boundsLabels[String(field)] ?? "调平范围"}`,
        beforeDisplay: formatPercent(currentPpm),
        afterDisplay: trimmed ? `${trimmed.replace(/%$/, "")} %` : "（恢复默认）",
        beforeNumber: currentPpm === null ? null : currentPpm / 10000,
        afterNumber: parsedPct, unit: "percent" });
    } else if (section === "plan" || section === "manual") {
      const combo = comboById.get(String(id));
      const plan = inputs.plans.find(row => row.combination_id === id && row.season === field);
      items.push({ ...base, category: "plan", task: 3,
        label: `${summarizeDimensions(combo)} · ${field} ${section === "manual" ? "手动新生" : "计划新生"}`,
        beforeDisplay: formatCount(plan?.students ?? null),
        afterDisplay: trimmed ? formatCount(parsedNumber) ?? trimmed : "（清空）",
        beforeNumber: plan?.students ?? null, afterNumber: parsedNumber, unit: "count" });
    } else if (section === "finance") {
      const fin = financeById.get(String(id));
      const combo = fin ? comboById.get(fin.combination_id) : undefined;
      const isMoney = field === "price_yuan";
      const title = financeLabels[String(field)] ?? "财务参数";
      const currentValue = isMoney ? forecastNumber(fin?.price_yuan) : (fin as unknown as Record<string, number | undefined> | undefined)?.[String(field)] ?? null;
      const parsedPct = isMoney ? null : parsePercent(raw);
      items.push({ ...base, category: "finance", task: 4,
        label: `${summarizeDimensions(combo)} · ${fin?.season ?? ""} ${title}`,
        beforeDisplay: isMoney ? formatMoney(currentValue) : formatPercent(currentValue),
        afterDisplay: trimmed ? (isMoney ? formatMoney(parsedNumber) ?? trimmed : `${trimmed.replace(/%$/, "")} %`) : "（清空）",
        beforeNumber: isMoney ? currentValue : currentValue === null ? null : currentValue / 10000,
        afterNumber: isMoney ? parsedNumber : parsedPct,
        unit: isMoney ? "money" : "percent" });
    } else if (section === "class" || section === "class-size") {
      const row = inputs.class_sizes[Number(id)];
      const isPlan = section === "class-size";
      const current = isPlan ? row?.planned_class_size ?? null : row?.minimum ?? null;
      items.push({ ...base, category: "class_size", task: 4,
        label: `${row?.track ?? ""} / ${row?.product ?? ""} ${isPlan ? "历史班量基准（已停用）" : "班均"}`,
        beforeDisplay: formatCount(current),
        afterDisplay: trimmed ? formatCount(parsedNumber) ?? trimmed : "（清空）",
        beforeNumber: current, afterNumber: parsedNumber, unit: "count" });
    } else if (section === "tax") {
      items.push({ ...base, category: "tax", task: 4, label: "含税转换系数",
        beforeDisplay: inputs.tax_factor,
        afterDisplay: trimmed || "（清空）",
        beforeNumber: forecastNumber(inputs.tax_factor), afterNumber: parsedNumber, unit: "factor" });
    } else {
      items.push({ ...base, category: "other", task: 1, label: String(section ?? "未知改动"),
        beforeDisplay: null, afterDisplay: trimmed || "（清空）",
        beforeNumber: null, afterNumber: parsedNumber, unit: "count" });
    }
  }
  const byCategory = new Map<NextYearEditCategory, NextYearEditItem[]>();
  for (const item of items) {
    const bucket = byCategory.get(item.category) ?? [];
    bucket.push(item); byCategory.set(item.category, bucket);
  }
  const groups: NextYearEditGroup[] = [];
  for (const { category, label } of editCategoryOrder) {
    const bucket = byCategory.get(category);
    if (!bucket?.length) continue;
    groups.push({ category, label, items: bucket, scale: computeGroupScale(category, bucket) });
  }
  return { groups, totalCount: items.length };
}

function computeGroupScale(category: NextYearEditCategory, items: NextYearEditItem[]): string | undefined {
  if (category === "rates" || category === "bounds") {
    let max = 0; let any = false;
    for (const item of items) {
      if (item.beforeNumber === null || item.afterNumber === null) continue;
      const diff = Math.abs(item.afterNumber - item.beforeNumber);
      if (diff > max) { max = diff; any = true; }
    }
    return any ? `最大 ${max.toLocaleString("zh-CN", { maximumFractionDigits: 2 })} 个百分点` : undefined;
  }
  if (category === "entry_detail" || category === "entry_total" || category === "plan" || category === "class_size") {
    let delta = 0; let any = false;
    for (const item of items) {
      if (item.afterNumber === null) continue;
      delta += item.afterNumber - (item.beforeNumber ?? 0); any = true;
    }
    return any ? `合计 ${delta >= 0 ? "+" : ""}${delta.toLocaleString("zh-CN")} 人` : undefined;
  }
  if (category === "finance") {
    const prices = items.filter(item => item.unit === "money");
    if (!prices.length) return undefined;
    let delta = 0;
    for (const item of prices) {
      if (item.afterNumber === null) continue;
      delta += item.afterNumber - (item.beforeNumber ?? 0);
    }
    return `学费合计 ${delta >= 0 ? "+" : ""}${delta.toLocaleString("zh-CN", { maximumFractionDigits: 2 })} 元`;
  }
  if (category === "target" || category === "track_target") {
    let delta = 0; let any = false;
    for (const item of items) {
      if (item.afterNumber === null) continue;
      delta += item.afterNumber - (item.beforeNumber ?? 0); any = true;
    }
    if (!any) return undefined;
    const wan = delta / 10000;
    return `${delta >= 0 ? "+" : ""}${wan.toLocaleString("zh-CN", { maximumFractionDigits: 2 })} 万元`;
  }
  return undefined;
}

/** Read-only summary. Any unknown component keeps its selected total unknown. */
export function nextYearRowTotal(rows: NextYearRow[], key: "revenue_yuan" | "old_students" | "new_students" | "total_students"): number | null {
  if (!rows.length) return null;
  let total = 0, correction = 0;
  for (const row of rows) { const n = forecastNumber(row[key]); if (n === null) return null;
    const next = total + n; correction += Math.abs(total) >= Math.abs(n) ? total - next + n : n - next + total; total = next; }
  const value = total + correction;
  return Number.isFinite(value) ? value : null;
}
