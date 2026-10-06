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
  renewal?: ForecastNumber; unmapped?: boolean; counts_not_applicable?: boolean; budget_covered?: boolean;
  data_status?: "ACTUAL" | "ACTUAL_UNAVAILABLE" | "FORECAST" | "BASELINE_ESTIMATE" | "TRIAL_SIZE_HELD";
  future_method?: string;
}
/** Engine contribution zero is never advertised as the budget of an unmatched actual business. */
export function forecastPresentationRow(row: ForecastRow): ForecastRow {
  const unmatched = row.unmapped === true || row.method === "UNMAPPED_ACTUAL" || row.method === "UNMAPPED_FROZEN_CARRYIN";
  return { ...row, budget_revenue: unmatched || row.budget_covered === false ? null : row.budget_revenue,
    budget_total: unmatched || row.budget_covered === false || row.counts_not_applicable ? null : row.budget_total,
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
  budgeted_additional?: ForecastNumber; unbudgeted_additional?: ForecastNumber;
}
export interface ForecastMapping { source: ForecastDimensions; target: ForecastDimensions; reason: string }
/** Prepare an editable draft only; neither aliases nor saved correspondences are inferred. */
export function prepareForecastMapping(source: ForecastDimensions): ForecastMapping {
  return { source: { ...source }, target: { ...source }, reason: "" };
}
export type ForecastAdjustmentKind = "additional" | "new_withdrawal" | "old_withdrawal";
export interface ForecastAdjustment { season: string; campus: string; kind: ForecastAdjustmentKind; value: number; reason: string }
export interface TrialFinancial {
  price: ForecastNumber; discount: ForecastNumber; recognition: ForecastNumber;
  renewal?: ForecastNumber; share?: ForecastNumber; method: string;
}
export interface ForecastScope {
  dims: ForecastDimensions; kind: "UNBUDGETED_BUSINESS" | "FINANCIAL_ONLY"; reason: string;
  financial: Partial<Record<ForecastSeason, TrialFinancial>>; additional: Partial<Record<ForecastSeason, number>>;
}
export interface TrialAssumption { code: string; message: string; season?: string; dims?: Partial<ForecastDimensions>; fields?: unknown; detail?: unknown }
export interface ForecastAssumptions {
  states: Record<ForecastSeason, ForecastState>; minimum_sample: number; mappings: ForecastMapping[]; adjustments: ForecastAdjustment[];
  trial_mode?: boolean; forecast_scopes?: ForecastScope[];
}
export interface ForecastCalculation {
  rule_version: string; ready: boolean; annual: ForecastTotals; quarters: ForecastQuarter[];
  rows: ForecastRow[]; issues: ForecastIssue[]; campuses: ForecastCampus[];
  trial_ready?: boolean; trial_assumptions?: TrialAssumption[];
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

export function groupForecastRows(rows: ForecastRow[], dimension: ForecastDimension): (ForecastTotals & { name: string; records: number; unmapped: boolean; hasUnbudgeted: boolean })[] {
  const groups = new Map<string, ForecastRow[]>();
  for (const row of rows) groups.set(row[dimension], [...(groups.get(row[dimension]) ?? []), row]);
  return [...groups].map(([name, group]) => {
    const isUnmapped = (row: ForecastRow) => row.unmapped === true || row.method === "UNMAPPED_ACTUAL" || row.method === "UNMAPPED_FROZEN_CARRYIN";
    const unmapped = group.some(isUnmapped), budgetRows = group.filter(row => !isUnmapped(row) && row.budget_covered !== false);
    // Unmatched actuals remain visible, but the engine's contribution zero is not a known budget for that business.
    // A mixed group can display its known baseline; correspondence is required before any comparison is meaningful.
    const budget = budgetRows.length ? sumForecastRows(budgetRows, "budget_revenue") : null;
    const forecast = sumForecastRows(group, "forecast_revenue");
    const studentRows = group.filter(row => !row.counts_not_applicable);
    return { name, records: group.length, unmapped, hasUnbudgeted: group.some(row => row.budget_covered === false),
      budget_revenue: budget, forecast_revenue: forecast, gap: unmapped || budget === null || forecast === null ? null : budget - forecast,
      achievement: unmapped || budget === null || budget <= 0 || forecast === null ? null : forecast / budget,
      budget_total: budgetRows.length ? sumForecastRows(budgetRows.filter(row => !row.counts_not_applicable), "budget_total") : null,
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
export function prepareTrialForecast(identity: ApiIdentity, parentId: string, reason: string): Promise<ForecastEnvelope> {
  return commandForecast("/prepare-trial", identity, { parent_id: parentId, reason });
}

/** Trial completeness cannot replace the formal confirmation gate. */
export function canConfirmForecast(version: ForecastVersion, identity: ApiIdentity | null, pendingChanges = false): boolean {
  return Boolean(identity?.roles.includes("DEPARTMENT_BUDGET_OWNER") && version.status !== "CONFIRMED"
    && version.assumptions.trial_mode !== true && version.calculation.ready && !pendingChanges);
}

export interface TrialFinancialDraft { price: string; discount: string; recognition: string; renewal: string; share: string; additional: string; reason: string }
export function trialFinancialDraft(scope: ForecastScope, season: ForecastSeason): TrialFinancialDraft {
  const value = scope.financial[season];
  const decimal = (input: ForecastNumber | undefined) => { const parsed = forecastNumber(input); return parsed === null ? "" : String(parsed); };
  const percent = (input: ForecastNumber | undefined) => { const parsed = forecastNumber(input); return parsed === null ? "" : String(Number((parsed * 100).toPrecision(12))); };
  return { price: decimal(value?.price), discount: percent(value?.discount), recognition: percent(value?.recognition),
    renewal: percent(value?.renewal), share: percent(value?.share), additional: scope.additional[season] === undefined ? "" : String(scope.additional[season]), reason: "手动调整 FY27 试用假设" };
}
/** Validate a single complete-dimension trial scope; rates are never fabricated from an empty field. */
export function applyTrialFinancialDraft(scope: ForecastScope, season: ForecastSeason, state: ForecastState, draft: TrialFinancialDraft): { scope: ForecastScope | null; error: string } {
  if (state === "FINALIZED") return { scope: null, error: "已定版季度的实际不调整" };
  if (scope.kind === "FINANCIAL_ONLY") return { scope: null, error: "结转实际不调整预测人数和参数" };
  const original = scope.financial[season], initial = trialFinancialDraft(scope, season);
  const financialKeys = ["price", "discount", "recognition", "renewal", "share"] as const;
  const changed = new Set(financialKeys.filter(key => {
    if (draft[key].trim() === initial[key].trim()) return false;
    const next = forecastNumber(draft[key]), previous = forecastNumber(initial[key]);
    return next === null || previous === null || next !== previous;
  }));
  const price = forecastNumber(draft.price), discount = forecastNumber(draft.discount), recognition = forecastNumber(draft.recognition);
  const renewal = draft.renewal.trim() ? forecastNumber(draft.renewal) : null;
  const share = draft.share.trim() ? forecastNumber(draft.share) : null;
  if ((!original || changed.has("price")) && (price === null || price < 0 || price > 1000000000)) return { scope: null, error: "学费须为 0 至 10 亿元之间的数值" };
  if (((!original || changed.has("discount")) && (discount === null || discount < 0 || discount > 100)) || ((!original || changed.has("recognition")) && (recognition === null || recognition < 0 || recognition > 100))) return { scope: null, error: "折扣和确认比例须在 0% 至 100% 之间" };
  if ((!original || changed.has("renewal")) && draft.renewal.trim() && (renewal === null || renewal < 0 || renewal > 100)) return { scope: null, error: "续班率须在 0% 至 100% 之间，或留空" };
  if ((!original || changed.has("share")) && draft.share.trim() && (share === null || share < 0 || share >= 100)) return { scope: null, error: "新生占比须小于 100%，或留空" };
  if (!draft.reason.trim()) return { scope: null, error: "请填写试用调整原因" };
  const additional = { ...scope.additional };
  if (state === "OPEN" || state === "ENDED") {
    if (!/^\d+$/.test(draft.additional) || !Number.isSafeInteger(Number(draft.additional)) || Number(draft.additional) > 10000000) return { scope: null, error: "后续新增须为 0 至 1000 万的整数人次" };
    additional[season] = Number(draft.additional);
  }
  const converted: Required<TrialFinancial> = { price: String(price), discount: String(discount! / 100), recognition: String(recognition! / 100),
    renewal: renewal === null ? null : String(renewal / 100), share: share === null ? null : String(share / 100), method: "MANUAL" };
  // A display-rounded percentage is not an edit. Retain every untouched Decimal string (and its method) exactly.
  const financial: TrialFinancial = original ? { ...original } : converted;
  for (const key of changed) financial[key] = converted[key];
  if (changed.size) financial.method = "MANUAL";
  return { scope: { ...scope, dims: { ...scope.dims }, reason: draft.reason.trim(), financial: { ...scope.financial, [season]: financial }, additional }, error: "" };
}
