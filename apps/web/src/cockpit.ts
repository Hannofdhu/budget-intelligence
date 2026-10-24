import { identityHeaders, parseResponse, type ApiIdentity } from "./api.ts";
import { budgetReadKey, readBudgetResource, invalidateBudgetReads, peekBudgetRead } from "./budgetReadCache.ts";
import { numericValue } from "./budgetAnalysis.ts";
import { matchesFilter, normalizeFilter, readFilterParam, writeFilterParam } from "./filterSelection.ts";
import type { BudgetDimension, BudgetFilters, BudgetValue } from "./budgetAnalysisTypes.ts";

export const cockpitDimensions = ["track", "campus", "product", "grade", "subject", "season"] as const;
export const cockpitLabels: Record<BudgetDimension, string> = { track: "赛道", campus: "校区", product: "产品", grade: "年级", subject: "科目", season: "季度" };
export const cockpitMetrics = ["budget", "actual", "forecast", "next_budget"] as const;
export type CockpitMetric = typeof cockpitMetrics[number];
export type CockpitPath = Partial<Record<BudgetDimension, string>>;
export type CockpitSource = { id: string; fiscal_year: number; status: string; sequence?: number; as_of?: string; created_at?: string; snapshot_hash?: string; calculation_hash?: string; source?: { id: string; sequence: number; as_of: string }; target_revenue_yuan?: string | null; total_students?: number; class_count?: number };
export type CockpitRow = Record<BudgetDimension, string> & Record<CockpitMetric, BudgetValue> & { id: string; covered: Record<CockpitMetric, boolean> };
export type CockpitData = { read_only: true; unit: "yuan"; fiscal_year: number; next_fiscal_year: number; read_at: string; sources: Record<CockpitMetric, CockpitSource | null>; rows: CockpitRow[]; totals: Record<CockpitMetric, BudgetValue>; notes: string[]; definitions: Record<CockpitMetric, string> };
export type CockpitStep = { path: CockpitPath; groups: BudgetDimension[] };
export type CockpitView = { filters: BudgetFilters; groups: BudgetDimension[]; trail: CockpitStep[]; view: "chart" | "table"; sort: "gap" | "budget" | "forecast" | "next_budget" | "name"; comparison: "current" | "next"; unit: "wan" | "yuan" };
export type CockpitTotals = Record<CockpitMetric, number | null> & { gap: number | null; achievement: number | null; actualAchievement: number | null; nextGrowth: number | null };
export type CockpitGroup = CockpitTotals & { key: string; path: CockpitPath; label: string; count: number };
export const emptyCockpitFilters: BudgetFilters = { season: "", track: "", campus: "", product: "", grade: "", subject: "" };
const isDimension = (value: string): value is BudgetDimension => (cockpitDimensions as readonly string[]).includes(value);
const validSourceId = (value: string | null) => value === null || /^[A-Za-z0-9_-]{1,128}$/.test(value);
export const cockpitSelection = (search: string) => {
  const params = new URLSearchParams(search);
  return { forecast: params.get("forecast_version"), next: params.get("nyVersion") };
};
export function readCockpitView(search: string): CockpitView {
  const p = new URLSearchParams(search), groups = [...new Set(p.getAll("cc_group").filter(isDimension))];
  let trail: CockpitStep[] = [];
  try {
    const raw: unknown = JSON.parse(p.get("cc_path") ?? "[]");
    if (Array.isArray(raw) && raw.length <= 6) trail = raw.filter(step => step && typeof step === "object" && step.path && !Array.isArray(step.path) && Array.isArray(step.groups))
      .map(step => ({ path: Object.fromEntries(Object.entries(step.path).filter(([key, value]) => isDimension(key) && typeof value === "string" && value.length > 0 && value.length <= 512)) as CockpitPath,
        groups: [...new Set((step.groups as unknown[]).filter((key): key is BudgetDimension => typeof key === "string" && isDimension(key)))] })).filter(step => Object.keys(step.path).length > 0 && step.groups.length > 0);
  } catch { /* Invalid view state is not a query or a budget command. */ }
  const sort = p.get("cc_sort");
  return { filters: Object.fromEntries(cockpitDimensions.map(key => [key, readFilterParam(p, `bf_${key}`)])) as BudgetFilters,
    groups: groups.length ? groups : ["track"], trail, view: p.get("cc_view") === "table" ? "table" : "chart", sort: ["gap", "budget", "forecast", "next_budget", "name"].includes(sort ?? "") ? sort as CockpitView["sort"] : "gap",
    comparison: p.get("cc_compare") === "next" ? "next" : "current", unit: p.get("cc_unit") === "yuan" ? "yuan" : "wan" };
}
export const cockpitPath = (view: CockpitView): CockpitPath => Object.assign({}, ...view.trail.map(step => step.path));
export function cockpitRows(data: CockpitData, view: CockpitView, selection: CockpitPath = {}): CockpitRow[] {
  const paths = [...view.trail.map(step => step.path), selection];
  return data.rows.filter(row => cockpitDimensions.every(key => matchesFilter(row[key], view.filters[key], true))
    && paths.every(path => Object.entries(path).every(([key, value]) => row[key as BudgetDimension] === value)));
}
export function cockpitTotals(rows: CockpitRow[]): CockpitTotals {
  const values = Object.fromEntries(cockpitMetrics.map(metric => {
    const members = rows.filter(row => row.covered[metric]);
    if (!members.length) return [metric, null];
    let total = 0, compensation = 0;
    for (const row of members) {
      const value = numericValue(row[metric]);
      if (value === null) return [metric, null];
      const next = total + value;
      compensation += Math.abs(total) >= Math.abs(value) ? total - next + value : value - next + total;
      total = next;
    }
    return [metric, Number.isFinite(total + compensation) ? total + compensation : null];
  })) as Record<CockpitMetric, number | null>;
  const { budget, actual, forecast, next_budget: next } = values;
  return { ...values, gap: budget === null || forecast === null ? null : budget - forecast,
    achievement: budget !== null && budget > 0 && forecast !== null ? forecast / budget : null,
    actualAchievement: budget !== null && budget > 0 && actual !== null ? actual / budget : null,
    nextGrowth: forecast !== null && forecast > 0 && next !== null ? next / forecast - 1 : null };
}
export function cockpitGroups(rows: CockpitRow[], dimensions: readonly BudgetDimension[], sort: CockpitView["sort"]): CockpitGroup[] {
  const groups = new Map<string, CockpitRow[]>();
  for (const row of rows) { const key = JSON.stringify(dimensions.map(dim => row[dim])); groups.set(key, [...(groups.get(key) ?? []), row]); }
  return [...groups].map(([key, members]) => ({ key, path: Object.fromEntries(dimensions.map(dim => [dim, members[0][dim]])), label: dimensions.map(dim => members[0][dim]).join(" / "), count: members.length, ...cockpitTotals(members) }))
    .sort((a, b) => sort === "name" ? a.label.localeCompare(b.label, "zh-CN") : (b[sort] ?? -Infinity) - (a[sort] ?? -Infinity) || a.label.localeCompare(b.label, "zh-CN"));
}
export function drillCockpit(view: CockpitView, path: CockpitPath, nextDimension?: BudgetDimension): CockpitView {
  const previous = cockpitPath(view);
  if (view.trail.length >= 6 || !Object.entries(path).some(([key, value]) => previous[key as BudgetDimension] !== value)) return view;
  const fixed = { ...previous, ...path };
  const next = nextDimension && !fixed[nextDimension] ? nextDimension : cockpitDimensions.find(key => !fixed[key]);
  return { ...view, trail: [...view.trail, { path, groups: view.groups }], groups: next ? [next] : view.groups };
}
export function backCockpit(view: CockpitView, length: number): CockpitView {
  const count = Math.max(0, Math.min(length, view.trail.length));
  return { ...view, groups: view.trail[count]?.groups ?? view.groups, trail: view.trail.slice(0, count) };
}
export function cockpitScopeFilters(view: CockpitView, selected: CockpitPath = {}): BudgetFilters | null {
  const filters = { ...view.filters };
  for (const path of [...view.trail.map(step => step.path), selected]) for (const [name, value] of Object.entries(path)) {
    const key = name as BudgetDimension;
    if (!matchesFilter(value, filters[key], true)) return null;
    filters[key] = value;
  }
  return filters;
}
export function cockpitHref(view: CockpitView, data?: CockpitData | null, search = ""): string {
  const params = new URLSearchParams();
  if (data) params.set("year", String(data.fiscal_year));
  const pinned = cockpitSelection(search);
  const forecast = data ? data.sources.forecast?.id ?? "none" : pinned.forecast;
  const next = data ? data.sources.next_budget?.id ?? "none" : pinned.next;
  if (forecast) params.set("forecast_version", forecast);
  if (next) params.set("nyVersion", next);
  for (const key of cockpitDimensions) writeFilterParam(params, `bf_${key}`, normalizeFilter(view.filters[key]));
  view.groups.forEach(key => params.append("cc_group", key));
  if (view.trail.length) params.set("cc_path", JSON.stringify(view.trail));
  params.set("cc_view", view.view); params.set("cc_sort", view.sort); params.set("cc_compare", view.comparison); params.set("cc_unit", view.unit);
  return `/budget/cockpit?${params}`;
}
export function cockpitSourceHref(metric: CockpitMetric, data: CockpitData, view: CockpitView, path: CockpitPath = {}): string | null {
  const filters = cockpitScopeFilters(view, path), source = data.sources[metric];
  if (!filters || !source) return null;
  const params = new URLSearchParams();
  for (const key of cockpitDimensions) writeFilterParam(params, `bf_${key}`, filters[key]);
  if (metric === "budget") { params.set("archive", "fy27"); params.set("step", "5"); return `/budget/workspace?${params}`; }
  if (metric === "next_budget") { params.set("year", String(source.fiscal_year)); params.set("nyTask", "5"); params.set("nyFilters", "1"); params.set("nyVersion", source.id); return `/budget/next-year?${params}`; }
  params.set("archive", "fy27"); params.set("forecast_version", source.id); params.set("bf_scope", "1");
  view.groups.forEach(key => params.append("fg_row", key));
  return `/budget/forecast?${params}`;
}
export function cockpitMetricLabels(data: Pick<CockpitData, "fiscal_year" | "next_fiscal_year">): Record<CockpitMetric, string> {
  const fy = (year: number) => `FY${String(year).slice(-2)}`;
  return { budget: `${fy(data.fiscal_year)}定版目标`, actual: "截至当日实际", forecast: `${fy(data.fiscal_year)}全年预计`, next_budget: `${fy(data.next_fiscal_year)}预算草稿` };
}
export function cockpitApiPath(search: string): string {
  const { forecast, next } = cockpitSelection(search), params = new URLSearchParams();
  if (!validSourceId(forecast) || !validSourceId(next)) throw new Error("无效的驾驶舱版本标识，未替换为最新版本");
  if (forecast) params.set("forecast_version", forecast);
  if (next) params.set("next_year_version", next);
  return `/api/v1/budget-cockpit${params.size ? `?${params}` : ""}`;
}
export async function getCockpit(identity: ApiIdentity, search = "", signal?: AbortSignal, fresh = false): Promise<CockpitData> {
  const path = cockpitApiPath(search), key = budgetReadKey(identity, path);
  if (fresh) invalidateBudgetReads(key);
  return readBudgetResource(key, async sharedSignal => {
    const response = await fetch(path, { headers: identityHeaders(identity), signal: sharedSignal });
    if (!response.ok) {
      const payload = await response.clone().json().catch(() => null) as { detail?: unknown } | null;
      if (typeof payload?.detail === "string") throw Object.assign(new Error(payload.detail), { status: response.status });
    }
    const data = await parseResponse<CockpitData>(response);
    const selection = cockpitSelection(search);
    if (data.read_only !== true || data.unit !== "yuan" || !Array.isArray(data.rows) || !data.sources
      || selection.forecast && (data.sources.forecast?.id ?? "none") !== selection.forecast
      || selection.next && (data.sources.next_budget?.id ?? "none") !== selection.next) throw new Error("驾驶舱来源与所选快照不一致，未替换为其他版本");
    return data;
  }, signal);
}
export const peekCockpit = (identity: ApiIdentity, search = "") => {
  try { return peekBudgetRead<CockpitData>(budgetReadKey(identity, cockpitApiPath(search))); } catch { return null; }
};
export const cockpitValue = (value: BudgetValue, unit: CockpitView["unit"] = "wan") => {
  const n = numericValue(value); return n === null ? "—" : (n / (unit === "wan" ? 10000 : 1)).toLocaleString("zh-CN", { maximumFractionDigits: 2 });
};
