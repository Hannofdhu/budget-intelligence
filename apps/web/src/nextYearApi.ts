import { identityHeaders, newRequestId, parseResponse, type ApiIdentity } from "./api.ts";
import { forecastNumber, forecastSeasons, type ForecastSeason } from "./forecastApi.ts";

export { forecastSeasons as nextYearSeasons };
export type NextYearNumber = string | number | null;
export interface ForecastBasis {
  id: string; sequence: number; as_of: string; status: "CONFIRMED";
  forecast_revenue: string; forecast_total: number;
  calculation_hash: string; snapshot_hash: string; baseline_hash: string;
}
export interface NextYearCombination {
  id: string; track: string; product: string; grade: string; subject: string; campus: string;
  entry: boolean; enabled: boolean; source_entry_weight: number;
  source_new_by_season: Record<ForecastSeason, number>; opening_carryover_yuan: string;
}
export interface NextYearRate {
  id: string; season: ForecastSeason; track: string; product: string; grade: string; campus: string;
  renewal_ppm: number; share_ppm: number;
}
export interface NextYearFinance { id: string; combination_id: string; season: ForecastSeason; price_yuan: string; discount_ppm: number; recognition_ppm: number }
export interface NextYearInputs {
  fiscal_year: 2028; source_as_of: string; target_revenue_yuan: string | null; track_targets: Record<string, string | null>;
  entry_total: number; tax_factor: string; combinations: NextYearCombination[]; rates: NextYearRate[];
  finance: NextYearFinance[]; class_sizes: { track: string; product: string; minimum: number }[];
  plans: { combination_id: string; season: ForecastSeason; students: number }[];
  opening_sources: { track: string; product: string; grade: string; subject: string; campus: string; students: number; target_id: string | null; exclusion: string | null }[];
  notes: Record<string, unknown>[];
}
export interface NextYearTotals {
  revenue_yuan: NextYearNumber; target_revenue_yuan: NextYearNumber; gap_yuan: NextYearNumber;
  total_students: number | null; old_students: number | null; new_students: number | null; attainment: NextYearNumber;
}
export interface NextYearRow {
  combination_id: string; season: ForecastSeason; track: string; product: string; grade: string; subject: string; campus: string; entry: boolean;
  old_students: number | null; new_students: number | null; total_students: number | null; revenue_yuan: NextYearNumber;
  price_yuan: NextYearNumber; discount_ppm: number | null; renewal_ppm: number | null; share_ppm: number | null; class_size_status: string;
  planned_new_applied: boolean;
}
export interface NextYearCalculation {
  fiscal_year: 2028; rule_versions: Record<string, string>; annual: NextYearTotals;
  quarters: (NextYearTotals & { season: ForecastSeason })[]; tracks: (NextYearTotals & { track: string })[];
  campuses: (NextYearTotals & { campus: string })[]; rows: NextYearRow[];
  entry_allocations: { campus: string; students: number; source_weight: number }[];
  opening: { spring_students: number; eligible_students: number; graduated_students: number; subject_ended_students: number; summer_old_students: number; carryover_yuan: string };
  issues: { code: string; message: string; season?: string; combination_id?: string; severity: "WARNING" | "BLOCKING"; [key: string]: unknown }[];
  complete: boolean; publication_available: false;
}
export interface NextYearDraft {
  id: string; sequence: number; parent_id: string | null; status: "DRAFT"; source: ForecastBasis;
  inputs: NextYearInputs; calculation: NextYearCalculation; created_at: string;
}
export interface NextYearEnvelope {
  source: ForecastBasis | null; draft: NextYearDraft | null; source_changed: boolean;
  history: { id: string; sequence: number; created_at: string; [key: string]: unknown }[];
}

const base = "/api/v1/next-year-budgets/fy28";
async function response<T>(value: Response): Promise<T> {
  if (!value.ok) {
    const payload = await value.clone().json().catch(() => null) as { detail?: unknown } | null;
    if (typeof payload?.detail === "string") throw new Error(payload.detail);
  }
  return parseResponse<T>(value);
}
export async function getNextYearBudget(identity: ApiIdentity, signal?: AbortSignal): Promise<NextYearEnvelope> {
  return response(await fetch(`${base}/current`, { headers: identityHeaders(identity), signal }));
}
async function command<T>(path: string, identity: ApiIdentity, body: unknown): Promise<T> {
  return response(await fetch(`${base}${path}`, { method: "POST", headers: { ...identityHeaders(identity), "Content-Type": "application/json", "Idempotency-Key": newRequestId() }, body: JSON.stringify(body) }));
}
export function prepareNextYearBudget(identity: ApiIdentity, forecastId: string, parentId: string | null): Promise<NextYearEnvelope> {
  return command("/prepare", identity, { forecast_id: forecastId, parent_id: parentId, reason: "基于已确认 FY27 预测生成 FY28 预算草稿" });
}
export function reviseNextYearBudget(identity: ApiIdentity, parentId: string, inputs: NextYearInputs, reason: string): Promise<NextYearEnvelope> {
  return command("/revise", identity, { parent_id: parentId, inputs, reason });
}
export function previewNextYearBudget(identity: ApiIdentity, parentId: string, inputs: NextYearInputs): Promise<{ source: ForecastBasis; calculation: NextYearCalculation }> {
  return command("/preview", identity, { parent_id: parentId, inputs });
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
export function allocateNextYearTrackTargets(total: string, original: Record<string, string>): Record<string, string> {
  const number = forecastNumber(total); if (number === null || number < 0 || number > 1000000000000) throw new Error("收入目标须为非负数且不超过允许范围");
  const target = moneyUnits(total), names = Object.keys(original).sort(), weights = names.map(name => moneyUnits(original[name]));
  if (!names.length) throw new Error("没有可分配的赛道");
  let denominator = weights.reduce((sum, value) => sum + value, 0n);
  if (denominator === 0n) { weights.fill(1n); denominator = BigInt(names.length); }
  const allocations = names.map((name, index) => ({ name, value: target * weights[index] / denominator, remainder: target * weights[index] % denominator }));
  let remaining = target - allocations.reduce((sum, item) => sum + item.value, 0n);
  for (const item of [...allocations].sort((a, b) => a.remainder === b.remainder ? a.name.localeCompare(b.name) : a.remainder > b.remainder ? -1 : 1)) {
    if (remaining === 0n) break; item.value += 1n; remaining -= 1n;
  }
  return Object.fromEntries(allocations.map(item => [item.name, moneyString(item.value)]));
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
    } else if (section === "class") {
      const item = inputs.class_sizes[Number(id)];
      if (!item || number === null || !Number.isSafeInteger(number) || number < 1 || number > 10000) fail(key, "班均须为1至10000的整数"); else item.minimum = number;
    } else if (section === "plan") {
      if (!inputs.combinations.some(row => row.id === id) || !forecastSeasons.includes(field as ForecastSeason)) { fail(key, "计划明细不存在"); continue; }
      if (number === null || !Number.isSafeInteger(number) || number < 0 || number > 10000000) { fail(key, "计划新生须为0至1000万的整数"); continue; }
      const plan = inputs.plans.find(row => row.combination_id === id && row.season === field);
      if (plan) plan.students = number; else inputs.plans.push({ combination_id: String(id), season: field as ForecastSeason, students: number });
    } else fail(key, "此字段不能修改");
  }
  if (!Object.keys(errors).length) {
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

/** Read-only summary. Any unknown component keeps its selected total unknown. */
export function nextYearRowTotal(rows: NextYearRow[], key: "revenue_yuan" | "old_students" | "new_students" | "total_students"): number | null {
  if (!rows.length) return null;
  let total = 0, correction = 0;
  for (const row of rows) { const n = forecastNumber(row[key]); if (n === null) return null;
    const next = total + n; correction += Math.abs(total) >= Math.abs(n) ? total - next + n : n - next + total; total = next; }
  const value = total + correction;
  return Number.isFinite(value) ? value : null;
}
