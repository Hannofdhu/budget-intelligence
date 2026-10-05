import { identityHeaders, newRequestId, parseResponse, type ApiIdentity } from "./api.ts";

export const forecastSeasons = ["暑假", "秋季", "寒假", "春季"] as const;
export type ForecastSeason = typeof forecastSeasons[number];
export type ForecastState = "FINALIZED" | "OPEN" | "ENDED" | "FUTURE";
export type ForecastNumber = string | number | null;
export type ForecastDimension = "track" | "campus" | "product" | "grade" | "subject";
export type ForecastDimensions = Record<ForecastDimension, string>;
export const forecastDimensionLabels: Record<ForecastDimension, string> = { track: "赛道", campus: "校区", product: "产品", grade: "年级", subject: "科目" };
export const forecastStateLabels: Record<ForecastState, string> = { FINALIZED: "已定版", OPEN: "招生中", ENDED: "招生结束", FUTURE: "未开始" };
export const defaultForecastStates: Record<ForecastSeason, ForecastState> = { 暑假: "FINALIZED", 秋季: "OPEN", 寒假: "FUTURE", 春季: "FUTURE" };

export interface ForecastIssue {
  code: string; message: string; severity: "BLOCKING" | "WARNING"; season?: string; dims?: Partial<ForecastDimensions>;
}
export interface ForecastIssueGroup {
  key: string; code: string; season: string; severity: "BLOCKING" | "WARNING"; title: string; issues: ForecastIssue[];
}
const mappingIssueCodes = new Set(["SOURCE_UNMAPPED", "FROZEN_CARRYIN_UNMAPPED"]);
const issueTitles: Record<string, string> = {
  SOURCE_UNMAPPED: "实际业务待对应", FROZEN_CARRYIN_UNMAPPED: "结转实际待对应",
  CURRENT_SOURCE_MISSING: "缺少当前实际", UPSTREAM_HEADCOUNT_UNAVAILABLE: "前季预测完成后自动计算",
  WITHDRAWAL_HISTORY_MISSING: "退班预测待补充", REVENUE_PARAMETER_INVALID: "收入参数待确认",
};
export function groupForecastIssues(issues: ForecastIssue[]): ForecastIssueGroup[] {
  const groups = new Map<string, ForecastIssueGroup>();
  for (const issue of issues) {
    const key = JSON.stringify([issue.code, issue.season ?? ""]);
    const group = groups.get(key) ?? { key, code: issue.code, season: issue.season ?? "", severity: issue.severity, title: issueTitles[issue.code] ?? issue.message, issues: [] };
    group.issues.push(issue);
    if (issue.severity === "BLOCKING") group.severity = "BLOCKING";
    groups.set(key, group);
  }
  return [...groups.values()].sort((a, b) => Number(!mappingIssueCodes.has(a.code)) - Number(!mappingIssueCodes.has(b.code))
    || Number(a.severity !== "BLOCKING") - Number(b.severity !== "BLOCKING")
    || forecastSeasons.indexOf(a.season as ForecastSeason) - forecastSeasons.indexOf(b.season as ForecastSeason)
    || a.title.localeCompare(b.title, "zh-CN"));
}
/** Only unmatched real source records need correspondence; downstream budget rows never do. */
export function forecastMappingIssues(issues: ForecastIssue[]): ForecastIssue[] {
  const fields = Object.keys(forecastDimensionLabels) as ForecastDimension[];
  return [...new Map(issues.filter(issue => mappingIssueCodes.has(issue.code) && fields.every(key => issue.dims?.[key]))
    .map(issue => [JSON.stringify(fields.map(key => issue.dims?.[key])), issue])).values()];
}
export interface ForecastRow extends ForecastDimensions {
  season: string; budget_revenue: ForecastNumber; forecast_revenue: ForecastNumber; budget_total: ForecastNumber;
  forecast_total: ForecastNumber; forecast_new?: ForecastNumber; forecast_old?: ForecastNumber;
  method: "FROZEN_ACTUAL" | "TERMINAL_FORECAST" | "PARENT_PARAMETER_PROJECTION" | "UNMAPPED_ACTUAL" | "UNMAPPED_FROZEN_CARRYIN";
  additional?: ForecastNumber; new_withdrawal?: ForecastNumber; old_withdrawal?: ForecastNumber;
  renewal?: ForecastNumber; unmapped?: boolean; counts_not_applicable?: boolean;
}
/** Engine contribution zero is never advertised as the budget of an unmatched actual business. */
export function forecastPresentationRow(row: ForecastRow): ForecastRow {
  const unmatched = row.unmapped === true || row.method === "UNMAPPED_ACTUAL" || row.method === "UNMAPPED_FROZEN_CARRYIN";
  return { ...row, budget_revenue: unmatched ? null : row.budget_revenue,
    budget_total: unmatched || row.counts_not_applicable ? null : row.budget_total,
    forecast_total: row.counts_not_applicable ? null : row.forecast_total };
}
export interface ForecastTotals {
  budget_revenue: ForecastNumber; forecast_revenue: ForecastNumber; gap: ForecastNumber; achievement?: ForecastNumber;
  budget_total?: ForecastNumber; forecast_total?: ForecastNumber;
}
export interface ForecastQuarter extends ForecastTotals {
  season: string; state: ForecastState; status?: "BLOCKED" | "GAP" | "NO_SIGNIFICANT_GAP" | "ABOVE_BUDGET";
}
export interface ForecastCampus {
  season: string; campus: string; budget_new: ForecastNumber; current_business_new: ForecastNumber;
  additional_default: ForecastNumber; additional: ForecastNumber; new_withdrawal: ForecastNumber;
  old_withdrawal: ForecastNumber; new_base: ForecastNumber; old_base: ForecastNumber;
}
export interface ForecastMapping { source: ForecastDimensions; target: ForecastDimensions; reason: string }
/** Prepare an editable draft only; neither aliases nor saved correspondences are inferred. */
export function prepareForecastMapping(source: ForecastDimensions): ForecastMapping {
  return { source: { ...source }, target: { ...source }, reason: "" };
}
export type ForecastAdjustmentKind = "additional" | "new_withdrawal" | "old_withdrawal";
export interface ForecastAdjustment { season: string; campus: string; kind: ForecastAdjustmentKind; value: number; reason: string }
export interface ForecastAssumptions {
  states: Record<ForecastSeason, ForecastState>; minimum_sample: number; mappings: ForecastMapping[]; adjustments: ForecastAdjustment[];
}
export interface ForecastCalculation {
  rule_version: string; ready: boolean; annual: ForecastTotals; quarters: ForecastQuarter[];
  rows: ForecastRow[]; issues: ForecastIssue[]; campuses: ForecastCampus[];
}
export interface ForecastVersion {
  id: string; sequence: number; status: "DRAFT" | "CONFIRMED"; as_of: string; created_at: string;
  states: Record<ForecastSeason, ForecastState>; assumptions: ForecastAssumptions; calculation: ForecastCalculation;
}
export interface ForecastEnvelope {
  baseline_year: number; baseline_hash: string; version: ForecastVersion | null;
  history: Pick<ForecastVersion, "id" | "sequence" | "status" | "as_of" | "created_at">[];
  syncing?: boolean;
}

export function forecastNumber(value: ForecastNumber | undefined): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value !== "string" || !/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(value.trim())) return null;
  const parsed = Number(value.trim());
  return Number.isFinite(parsed) ? parsed : null;
}

/** Missing one component means the entire selected total is unknown, not zero. */
export function sumForecastRows(rows: ForecastRow[], key: "budget_revenue" | "forecast_revenue" | "budget_total" | "forecast_total"): number | null {
  if (!rows.length) return null;
  let sum = 0, correction = 0;
  for (const row of rows) {
    const value = forecastNumber(row[key]);
    if (value === null) return null;
    const next = sum + value;
    correction += Math.abs(sum) >= Math.abs(value) ? sum - next + value : value - next + sum;
    sum = next;
  }
  const total = sum + correction;
  return Number.isFinite(total) ? total : null;
}

export function groupForecastRows(rows: ForecastRow[], dimension: ForecastDimension): (ForecastTotals & { name: string; records: number; unmapped: boolean })[] {
  const groups = new Map<string, ForecastRow[]>();
  for (const row of rows) groups.set(row[dimension], [...(groups.get(row[dimension]) ?? []), row]);
  return [...groups].map(([name, group]) => {
    const isUnmapped = (row: ForecastRow) => row.unmapped === true || row.method === "UNMAPPED_ACTUAL" || row.method === "UNMAPPED_FROZEN_CARRYIN";
    const unmapped = group.some(isUnmapped), budgetRows = group.filter(row => !isUnmapped(row));
    // Unmatched actuals remain visible, but the engine's contribution zero is not a known budget for that business.
    // A mixed group can display its known baseline; correspondence is required before any comparison is meaningful.
    const budget = budgetRows.length ? sumForecastRows(group, "budget_revenue") : null;
    const forecast = sumForecastRows(group, "forecast_revenue");
    const studentRows = group.filter(row => !row.counts_not_applicable);
    return { name, records: group.length, unmapped,
      budget_revenue: budget, forecast_revenue: forecast, gap: unmapped || budget === null || forecast === null ? null : budget - forecast,
      achievement: unmapped || budget === null || budget <= 0 || forecast === null ? null : forecast / budget,
      budget_total: budgetRows.length ? sumForecastRows(studentRows, "budget_total") : null,
      forecast_total: sumForecastRows(studentRows, "forecast_total") };
  }).sort((a, b) => (forecastNumber(b.budget_revenue) ?? -Infinity) - (forecastNumber(a.budget_revenue) ?? -Infinity) || a.name.localeCompare(b.name, "zh-CN"));
}

const base = "/api/v1/forecasts/fy27";
async function forecastResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const payload = await response.clone().json().catch(() => null) as { detail?: unknown } | null;
    if (typeof payload?.detail === "string") throw new Error(payload.detail);
  }
  return parseResponse<T>(response);
}
export async function getForecast(identity: ApiIdentity, signal?: AbortSignal): Promise<ForecastEnvelope> {
  return forecastResponse(await fetch(`${base}/current`, { headers: identityHeaders(identity), signal }));
}
async function commandForecast(path: string, identity: ApiIdentity, body: unknown): Promise<ForecastEnvelope> {
  return forecastResponse(await fetch(`${base}${path}`, { method: "POST", headers: { ...identityHeaders(identity), "Content-Type": "application/json", "Idempotency-Key": newRequestId() }, body: JSON.stringify(body) }));
}
export function refreshForecast(identity: ApiIdentity, states: Record<ForecastSeason, ForecastState>, reason: string): Promise<ForecastEnvelope> {
  return commandForecast("/refresh", identity, { states, reason });
}
export function reviseForecast(identity: ApiIdentity, parentId: string, assumptions: ForecastAssumptions, reason: string): Promise<ForecastEnvelope> {
  return commandForecast("/revise", identity, { parent_id: parentId, reason, assumptions });
}
export function confirmForecast(identity: ApiIdentity, id: string, reason: string): Promise<ForecastEnvelope> {
  return commandForecast(`/${encodeURIComponent(id)}/confirm`, identity, { reason });
}
