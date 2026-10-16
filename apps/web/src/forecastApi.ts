import { identityHeaders, newRequestId, parseResponse, type ApiIdentity } from "./api.ts";
import { compareBudgetGrades } from "./budgetDisplay.ts";

export const forecastSeasons = ["暑假", "秋季", "寒假", "春季"] as const;
export type ForecastSeason = typeof forecastSeasons[number];
export type ForecastState = "FINALIZED" | "OPEN" | "ENDED" | "FUTURE";
export type ForecastNumber = string | number | null;
export type ForecastDimension = "track" | "campus" | "product" | "grade" | "subject";
export type ForecastDimensions = Record<ForecastDimension, string>;
export const forecastDimensionLabels: Record<ForecastDimension, string> = { track: "赛道", campus: "校区", product: "产品", grade: "年级", subject: "科目" };
export type ForecastGroupDimension = ForecastDimension | "season";
export const forecastGroupDimensions = ["track", "product", "subject", "grade", "campus", "season"] as const;
export const forecastGroupDimensionLabels: Record<ForecastGroupDimension, string> = { ...forecastDimensionLabels, season: "季度" };
export const forecastStateLabels: Record<ForecastState, string> = { FINALIZED: "已定版", OPEN: "招生中", ENDED: "招生结束", FUTURE: "未开始" };
export const defaultForecastStates: Record<ForecastSeason, ForecastState> = { 暑假: "FINALIZED", 秋季: "OPEN", 寒假: "FUTURE", 春季: "FUTURE" };
export const fixedForecastRulesVersion = "fy27-fixed-rules-v1";

export interface ForecastIssue {
  code: string; message: string; severity: "BLOCKING" | "WARNING" | "INFO"; season?: string; dims?: Partial<ForecastDimensions>;
  detail?: Record<string, unknown>;
}
export interface ForecastIssueGroup {
  key: string; code: string; season: string; severity: ForecastIssue["severity"]; title: string; issues: ForecastIssue[];
}
const mappingIssueCodes = new Set(["SOURCE_UNMAPPED", "FROZEN_CARRYIN_UNMAPPED"]);
const issueTitles: Record<string, string> = {
  SOURCE_UNMAPPED: "实际业务待对应", FROZEN_CARRYIN_UNMAPPED: "结转实际待对应",
  CURRENT_SOURCE_MISSING: "缺少当前实际", UPSTREAM_HEADCOUNT_UNAVAILABLE: "前季预测完成后自动计算",
  WITHDRAWAL_HISTORY_MISSING: "退班预测待补充", REVENUE_PARAMETER_INVALID: "结转收入参数待补齐",
};
export function groupForecastIssues(issues: ForecastIssue[]): ForecastIssueGroup[] {
  const groups = new Map<string, ForecastIssueGroup>();
  for (const issue of issues) {
    const key = JSON.stringify([issue.code, issue.season ?? ""]);
    const group = groups.get(key) ?? { key, code: issue.code, season: issue.season ?? "", severity: issue.severity, title: issueTitles[issue.code] ?? issue.message, issues: [] };
    group.issues.push(issue);
    if (issue.severity === "BLOCKING" || (issue.severity === "WARNING" && group.severity === "INFO")) group.severity = issue.severity;
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
  forecast_total: ForecastNumber; budget_new?: ForecastNumber; budget_old?: ForecastNumber;
  forecast_new?: ForecastNumber; forecast_old?: ForecastNumber;
  method: "FROZEN_ACTUAL" | "ACTUAL_UNAVAILABLE" | "TERMINAL_FORECAST" | "PARENT_PARAMETER_PROJECTION" | "UNMAPPED_ACTUAL" | "UNMAPPED_FROZEN_CARRYIN";
  additional?: ForecastNumber; new_withdrawal?: ForecastNumber; old_withdrawal?: ForecastNumber;
  renewal?: ForecastNumber; unmapped?: boolean; counts_not_applicable?: boolean; budget_covered?: boolean;
  data_status?: "ACTUAL" | "ACTUAL_UNAVAILABLE" | "FORECAST" | "BASELINE_ESTIMATE" | "TRIAL_SIZE_HELD";
  future_method?: string;
}
/** Missing frozen facts stay unknown in source records; these defaults apply only to browsing. */
function isUnavailableForecastActual(row: ForecastRow): boolean {
  return (row.data_status === "ACTUAL_UNAVAILABLE" || row.method === "ACTUAL_UNAVAILABLE")
    && row.unmapped !== true
    && row.method !== "UNMAPPED_ACTUAL" && row.method !== "UNMAPPED_FROZEN_CARRYIN";
}
export function forecastActualRevenueDefaultsToZero(row: ForecastRow): boolean {
  return row.forecast_revenue === null && isUnavailableForecastActual(row);
}
export function forecastActualHeadcountDefaultsToZero(row: ForecastRow): boolean {
  return row.forecast_total === null && !row.counts_not_applicable && isUnavailableForecastActual(row);
}
/** Engine contribution zero is never advertised as the budget of an unmatched actual business. */
export function forecastPresentationRow(row: ForecastRow): ForecastRow {
  const unmatched = row.unmapped === true || row.method === "UNMAPPED_ACTUAL" || row.method === "UNMAPPED_FROZEN_CARRYIN";
  return { ...row, budget_revenue: unmatched || row.budget_covered === false ? null : row.budget_revenue,
    budget_total: unmatched || row.budget_covered === false || row.counts_not_applicable ? null : row.budget_total,
    forecast_revenue: forecastActualRevenueDefaultsToZero(row) ? 0 : row.forecast_revenue,
    forecast_total: row.counts_not_applicable ? null : forecastActualHeadcountDefaultsToZero(row) ? 0 : row.forecast_total };
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
  trial_mode?: boolean; forecast_scopes?: ForecastScope[]; fixed_rules_version?: string;
}
export interface ForecastCalculation {
  rule_version: string; ready: boolean; annual: ForecastTotals; quarters: ForecastQuarter[];
  rows: ForecastRow[]; issues: ForecastIssue[]; campuses: ForecastCampus[];
  trial_ready?: boolean; trial_assumptions?: TrialAssumption[];
  fixed_rules_version?: string; forecast_complete?: boolean; rule_notes?: TrialAssumption[];
  confirmation_policy_version?: string; confirmation_checks?: ForecastConfirmationCheck[];
}
/** Saved server checks only. The browser never qualifies missing actuals itself. */
export interface ForecastConfirmationCheck {
  season: string; source_complete: boolean; budget_components_zero: boolean; no_frozen_carryins: boolean;
  revenue_matched: boolean; enrollment_matched: boolean; excluded_empty_rows: number;
  known_revenue: ForecastNumber; actual_revenue: ForecastNumber;
  known_enrollment: ForecastNumber; actual_enrollment: ForecastNumber;
}
export interface ForecastVersion {
  id: string; sequence: number; status: "DRAFT" | "CONFIRMED"; as_of: string; created_at: string;
  states: Record<ForecastSeason, ForecastState>; assumptions: ForecastAssumptions; calculation: ForecastCalculation;
}
export interface ForecastEnvelope {
  baseline_year: number; baseline_hash: string; version: ForecastVersion | null;
  history: Pick<ForecastVersion, "id" | "sequence" | "status" | "as_of" | "created_at">[];
  syncing?: boolean;
  latest_version_id?: string | null;
}
export type ForecastVersionSummary = Pick<ForecastVersion, "id" | "sequence" | "status" | "as_of" | "created_at">;
export type ForecastTrendPeriod = "全年" | ForecastSeason;
export type ForecastTrendDays = 30 | 90 | "all";
export interface ForecastRevenueTrendValue {
  forecast_revenue: string | null; actual_revenue: string | null;
  actual_status: "AVAILABLE" | "UNAVAILABLE" | "ZERO_DEFAULTED";
}
export interface ForecastRevenueTrendPoint {
  date: string; version_id: string; sequence: number; status: "DRAFT" | "CONFIRMED";
  created_at: string; as_of: string; generation: "AUTOMATIC" | "MANUAL";
  periods: Record<ForecastTrendPeriod, ForecastRevenueTrendValue>;
}
export interface ForecastRevenueTrendEnvelope {
  baseline_year: number; timezone: string; points: ForecastRevenueTrendPoint[];
}
export interface ForecastSchedule {
  enabled: boolean; time: string; timezone: string; next_run_at: string | null;
  last_success_at: string | null; catch_up: boolean;
  last_run: { state: string; finished_at?: string | null; error?: { message?: string; code?: string } | string | null; scheduled_for?: string } | null;
}
export interface ForecastSnapshotArchive extends Record<string, unknown> {
  version: ForecastVersionSummary;
  snapshot: Record<string, unknown>;
}
export interface ForecastFailureDetail {
  code?: string; retryable?: boolean; diagnostic_id?: string; stage?: string;
  retained_version?: ForecastVersionSummary | null;
}
export class ForecastRequestError extends Error {
  readonly status: number;
  readonly detail: ForecastFailureDetail;
  constructor(message: string, status = 0, detail: ForecastFailureDetail = {}) {
    super(message); this.name = "ForecastRequestError"; this.status = status; this.detail = detail;
  }
}
export function forecastFailure(error: unknown): ForecastRequestError {
  if (error instanceof ForecastRequestError) return error;
  const apiError = error as { status?: unknown; code?: unknown } | null;
  if (error instanceof Error && typeof apiError?.status === "number") return new ForecastRequestError(error.message, apiError.status, typeof apiError.code === "string" ? { code: apiError.code } : {});
  return new ForecastRequestError(error instanceof TypeError ? "网络连接未完成，请检查连接后重试。" : error instanceof Error ? error.message : "操作未完成，请重试。", 0, { code: error instanceof TypeError ? "NETWORK_CONNECTION_FAILED" : "CLIENT_OPERATION_FAILED", retryable: true });
}
export function forecastFailureCategory(error: ForecastRequestError): string {
  const code = error.detail.code ?? "";
  if (/AUTH|LOGIN|PERMISSION|FORBIDDEN/.test(code) || [401, 403].includes(error.status)) return "数据来源登录或权限需检查";
  if (/TIMEOUT/.test(code)) return "读取数据超时";
  if (/NETWORK|CONNECTION/.test(code) || (error.status === 0 && !code)) return "连接未完成";
  if (/INVALID|MISMATCH|MISSING|PARTITION|LIMIT/.test(code)) return "来源数据待核对";
  return "本次操作未完成";
}

/** A saved calculation must identify the same rules as its saved inputs. */
export function usesFixedForecastRules(version: Pick<ForecastVersion, "assumptions" | "calculation">): boolean {
  return version.assumptions.trial_mode !== true
    && version.assumptions.fixed_rules_version === fixedForecastRulesVersion
    && version.calculation.fixed_rules_version === fixedForecastRulesVersion;
}

/** Complete department results do not waive the separate formal data checks. */
export function forecastCalculationComplete(calculation: ForecastCalculation, trialMode = false): boolean {
  return calculation.ready || calculation.forecast_complete === true || (trialMode && calculation.trial_ready === true);
}

export function forecastNumber(value: ForecastNumber | undefined): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value !== "string" || !/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(value.trim())) return null;
  const parsed = Number(value.trim());
  return Number.isFinite(parsed) ? parsed : null;
}

/** Missing one component means the entire selected total is unknown, not zero. */
export function sumForecastRows(rows: ForecastRow[], key: "budget_revenue" | "forecast_revenue" | "budget_total" | "forecast_total" | "budget_new" | "budget_old" | "forecast_new" | "forecast_old"): number | null {
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

function isUnmappedForecastRow(row: ForecastRow): boolean {
  return row.unmapped === true || row.method === "UNMAPPED_ACTUAL" || row.method === "UNMAPPED_FROZEN_CARRYIN";
}

export interface ForecastHeadcountSplits {
  budget_new: number | null; budget_old: number | null; forecast_new: number | null; forecast_old: number | null;
}

/** Saved split fields are independent facts: do not infer missing old snapshots from totals. */
function summarizeForecastStudentSplits(rows: ForecastRow[]): ForecastHeadcountSplits {
  const students = rows.filter(row => !row.counts_not_applicable);
  const budgeted = students.filter(row => row.budget_covered !== false && !isUnmappedForecastRow(row));
  const presented = students.map(row => ({ ...row,
    forecast_new: row.forecast_new == null && isUnavailableForecastActual(row) ? 0 : row.forecast_new,
    forecast_old: row.forecast_old == null && isUnavailableForecastActual(row) ? 0 : row.forecast_old,
  }));
  return { budget_new: sumForecastRows(budgeted, "budget_new"), budget_old: sumForecastRows(budgeted, "budget_old"),
    forecast_new: sumForecastRows(presented, "forecast_new"), forecast_old: sumForecastRows(presented, "forecast_old") };
}

/** Read saved student splits only; the quarter's authoritative department totals stay unchanged. */
export function forecastQuarterHeadcounts(rows: ForecastRow[], season: string): ForecastHeadcountSplits {
  return summarizeForecastStudentSplits(rows.filter(row => row.season === season));
}

export interface ForecastSummary extends ForecastTotals, ForecastHeadcountSplits {
  records: number; unmapped: boolean; hasUnbudgeted: boolean;
  budget_revenue: number | null; forecast_revenue: number | null; gap: number | null; achievement: number | null;
  budget_total: number | null; forecast_total: number | null;
}
export interface ForecastGroup extends ForecastSummary {
  key: string; name: string; dimensions: Partial<Record<ForecastGroupDimension, string>>;
}

/** Aggregate raw scoped rows, never rounded group values or the department's saved quarter totals. */
export function summarizeForecastRows(rows: ForecastRow[]): ForecastSummary {
  const unmapped = rows.some(isUnmappedForecastRow), budgetRows = rows.filter(row => !isUnmappedForecastRow(row) && row.budget_covered !== false);
  // Unmatched actuals retain their values, but the engine's contribution zero is not a known budget.
  // A mixed group retains its known baseline, and postpones comparisons until correspondence exists.
  const budget = sumForecastRows(budgetRows, "budget_revenue");
  const presented = rows.map(forecastPresentationRow), forecast = sumForecastRows(presented, "forecast_revenue");
  return { records: rows.length, unmapped, hasUnbudgeted: rows.some(row => row.budget_covered === false),
    budget_revenue: budget, forecast_revenue: forecast, gap: unmapped || budget === null || forecast === null ? null : budget - forecast,
    achievement: unmapped || budget === null || budget <= 0 || forecast === null ? null : forecast / budget,
    budget_total: sumForecastRows(budgetRows.filter(row => !row.counts_not_applicable), "budget_total"),
    forecast_total: sumForecastRows(presented.filter(row => !row.counts_not_applicable), "forecast_total"),
    ...summarizeForecastStudentSplits(rows) };
}

const forecastGroupCollator = new Intl.Collator("zh-CN", { numeric: true });
function compareForecastGroupValue(dimension: ForecastGroupDimension, left: string, right: string): number {
  if (dimension === "grade") return compareBudgetGrades(left, right);
  if (dimension === "season") {
    const leftRank = forecastSeasons.indexOf(left as ForecastSeason), rightRank = forecastSeasons.indexOf(right as ForecastSeason);
    const order = (leftRank < 0 ? forecastSeasons.length : leftRank) - (rightRank < 0 ? forecastSeasons.length : rightRank);
    if (order) return order;
  }
  return forecastGroupCollator.compare(left, right) || (left < right ? -1 : left > right ? 1 : 0);
}

/** Each exact selected tuple appears once; the display name is never used as the group identity. */
export function groupForecastRows(rows: ForecastRow[], dimension: ForecastGroupDimension | readonly ForecastGroupDimension[]): ForecastGroup[] {
  const requested = typeof dimension === "string" ? [dimension] : dimension;
  const selected = [...new Set(requested.filter(key => forecastGroupDimensions.includes(key)))];
  const dimensions: ForecastGroupDimension[] = selected.length ? selected : ["track"];
  const groups = new Map<string, { dimensions: ForecastGroup["dimensions"]; values: string[]; rows: ForecastRow[] }>();
  for (const row of rows) {
    const values = dimensions.map(key => row[key]), key = JSON.stringify(values);
    let group = groups.get(key);
    if (!group) {
      group = { dimensions: Object.fromEntries(dimensions.map((key, index) => [key, values[index]])), values, rows: [] };
      groups.set(key, group);
    }
    group.rows.push(row);
  }
  return [...groups].map(([key, group]) => ({ key, dimensions: group.dimensions, name: group.values.join(" / "), ...summarizeForecastRows(group.rows) }))
    .sort((left, right) => {
      // Preserve the existing default track overview's revenue-first reading order.
      if (dimensions.length === 1 && dimensions[0] === "track") {
        const order = (right.budget_revenue ?? -Infinity) - (left.budget_revenue ?? -Infinity);
        if (order) return order;
      }
      for (const key of dimensions) {
        const order = compareForecastGroupValue(key, left.dimensions[key] ?? "", right.dimensions[key] ?? "");
        if (order) return order;
      }
      return 0;
    });
}

const base = "/api/v1/forecasts/fy27";
// Leave 20 seconds for calculation, persistence and response after the 180-second source budget.
const forecastCommandTimeoutMs = 200000;
async function forecastResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const payload = await response.clone().json().catch(() => null) as { detail?: unknown } | null;
    if (typeof payload?.detail === "string") throw new ForecastRequestError(payload.detail, response.status);
    if (payload?.detail && typeof payload.detail === "object" && !Array.isArray(payload.detail)) {
      const detail = payload.detail as Record<string, unknown>;
      const retained = detail.retained_version;
      const metadata: ForecastFailureDetail = {
        ...(typeof detail.code === "string" ? { code: detail.code } : {}),
        ...(typeof detail.retryable === "boolean" ? { retryable: detail.retryable } : {}),
        ...(typeof detail.diagnostic_id === "string" ? { diagnostic_id: detail.diagnostic_id } : {}),
        ...(typeof detail.stage === "string" ? { stage: detail.stage } : {}),
        ...(retained === null ? { retained_version: null } : retained && typeof retained === "object" && !Array.isArray(retained)
          && typeof (retained as ForecastVersionSummary).id === "string" ? { retained_version: retained as ForecastVersionSummary } : {}),
      };
      throw new ForecastRequestError(typeof detail.message === "string" ? detail.message : `操作未完成（${response.status}）`, response.status, metadata);
    }
    try { return await parseResponse<T>(response); }
    catch (error) { throw new ForecastRequestError(error instanceof Error ? error.message : `操作未完成（${response.status}）`, response.status, { retryable: response.status >= 500 }); }
  }
  return parseResponse<T>(response);
}
export async function getForecast(identity: ApiIdentity, signal?: AbortSignal): Promise<ForecastEnvelope> {
  return forecastResponse(await fetch(`${base}/current`, { headers: identityHeaders(identity), signal }));
}
export async function getForecastVersion(identity: ApiIdentity, id: string, signal?: AbortSignal): Promise<ForecastEnvelope> {
  return forecastResponse(await fetch(`${base}/versions/${encodeURIComponent(id)}`, { headers: identityHeaders(identity), signal }));
}
/** Read existing immutable snapshots only; opening the trend never refreshes or confirms a forecast. */
export async function getForecastRevenueTrend(identity: ApiIdentity, days: ForecastTrendDays, throughVersion: string, signal?: AbortSignal): Promise<ForecastRevenueTrendEnvelope> {
  const query = new URLSearchParams({ days: String(days), through_version: throughVersion });
  return forecastResponse(await fetch(`${base}/trend?${query}`, { headers: identityHeaders(identity), signal }));
}
export async function getForecastSchedule(identity: ApiIdentity, signal?: AbortSignal): Promise<ForecastSchedule> {
  return forecastResponse(await fetch(`${base}/schedule`, { headers: identityHeaders(identity), signal }));
}
export async function getForecastSnapshot(identity: ApiIdentity, id: string): Promise<ForecastSnapshotArchive> {
  const archive = await forecastResponse<ForecastSnapshotArchive>(await fetch(`${base}/versions/${encodeURIComponent(id)}/snapshot`, { headers: identityHeaders(identity) }));
  if (archive.version?.id !== id || !archive.snapshot) throw new ForecastRequestError("本次快照与所选预测不一致，请重新读取。", 409, { code: "SNAPSHOT_VERSION_MISMATCH", retryable: false });
  return archive;
}
export async function downloadForecastSnapshot(identity: ApiIdentity, version: ForecastVersionSummary): Promise<void> {
  const archive = await getForecastSnapshot(identity, version.id);
  const blob = new Blob([`${JSON.stringify(archive, null, 2)}\n`], { type: "application/json;charset=utf-8" });
  const url = URL.createObjectURL(blob), anchor = document.createElement("a");
  const date = version.as_of.replace(/[^0-9]/g, "").slice(0, 8) || "未标日期";
  try {
    anchor.href = url; anchor.download = `FY27-预测P${version.sequence}-快照-${date}.json`;
    document.body.append(anchor); anchor.click();
  } finally { anchor.remove(); URL.revokeObjectURL(url); }
}
async function commandForecast(path: string, identity: ApiIdentity, body: unknown, requestId = newRequestId()): Promise<ForecastEnvelope> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), forecastCommandTimeoutMs);
  try {
    return await forecastResponse<ForecastEnvelope>(await fetch(`${base}${path}`, { method: "POST", headers: { ...identityHeaders(identity), "Content-Type": "application/json", "Idempotency-Key": requestId }, body: JSON.stringify(body), signal: controller.signal }));
  } catch (error) {
    if (controller.signal.aborted) throw new ForecastRequestError("等待操作结果超时，本次是否完成尚未确认。请重试核对。", 0, { code: "REQUEST_TIMEOUT", retryable: true });
    throw error;
  } finally { clearTimeout(timeout); }
}
export function refreshForecast(identity: ApiIdentity, states: Record<ForecastSeason, ForecastState>, reason: string, requestId?: string): Promise<ForecastEnvelope> {
  return commandForecast("/refresh", identity, { states, reason }, requestId);
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
    && version.assumptions.trial_mode !== true && version.calculation.ready
    && !version.calculation.issues.some(issue => issue.severity === "BLOCKING") && !pendingChanges);
}

export interface TrialFinancialDraft { price: string; discount: string; recognition: string; renewal: string; share: string; additional: string; reason: string }
export function trialFinancialDraft(scope: ForecastScope, season: ForecastSeason, legacyTrial = true): TrialFinancialDraft {
  const value = scope.financial[season];
  const decimal = (input: ForecastNumber | undefined) => { const parsed = forecastNumber(input); return parsed === null ? "" : String(parsed); };
  const percent = (input: ForecastNumber | undefined) => { const parsed = forecastNumber(input); return parsed === null ? "" : String(Number((parsed * 100).toPrecision(12))); };
  return { price: decimal(value?.price), discount: percent(value?.discount), recognition: percent(value?.recognition),
    renewal: percent(value?.renewal), share: percent(value?.share), additional: scope.additional[season] === undefined ? "" : String(scope.additional[season]), reason: legacyTrial ? "手动调整 FY27 试用假设" : "调整 FY27 独立业务参数" };
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
  if (((!original || changed.has("discount")) && (discount === null || discount < 0 || discount > 100)) || ((!original || changed.has("recognition")) && (recognition === null || recognition < 0 || recognition > 100))) return { scope: null, error: "折扣和当季结转比例须在 0% 至 100% 之间" };
  if ((!original || changed.has("renewal")) && draft.renewal.trim() && (renewal === null || renewal < 0 || renewal > 100)) return { scope: null, error: "续班率须在 0% 至 100% 之间，或留空" };
  if ((!original || changed.has("share")) && draft.share.trim() && (share === null || share < 0 || share >= 100)) return { scope: null, error: "新生占比须小于 100%，或留空" };
  if (!draft.reason.trim()) return { scope: null, error: "请填写调整原因" };
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
