import type { BudgetFilters, BudgetDimension } from "./budgetAnalysisTypes.ts";
import { normalizeFilter, readFilterParam, writeFilterParam } from "./filterSelection.ts";

const dimensions: BudgetDimension[] = ["season", "track", "campus", "product", "grade", "subject"];
const empty: BudgetFilters = { season: "", track: "", campus: "", product: "", grade: "", subject: "" };
type FilterStorage = Pick<Storage, "getItem" | "setItem">;
const browserStorage = () => { try { return typeof sessionStorage === "undefined" ? undefined : sessionStorage; } catch { return undefined; } };
const clean = (input: unknown): BudgetFilters => Object.fromEntries(dimensions.map(key => {
  const value = typeof input === "object" && input !== null ? (input as Record<string, unknown>)[key] : undefined;
  return [key, normalizeFilter(typeof value === "string" || Array.isArray(value) ? value : "")];
})) as BudgetFilters;

/** URL is shareable view state; storage is only a per-year fallback, never budget data. */
export function readNextYearFilters(year: number, search: string, storage: FilterStorage | undefined = browserStorage()): BudgetFilters {
  const params = new URLSearchParams(search);
  if (params.get("nyFilters") === "1" || dimensions.some(key => params.has(`bf_${key}`))) return Object.fromEntries(dimensions.map(key => [key, readFilterParam(params, `bf_${key}`)])) as BudgetFilters;
  try { return clean(JSON.parse(storage?.getItem(`ny-filters-${year}`) ?? "null")); } catch { return { ...empty }; }
}

export function persistNextYearFilters(year: number, filters: BudgetFilters, storage: FilterStorage | undefined = browserStorage()): void {
  try { storage?.setItem(`ny-filters-${year}`, JSON.stringify(clean(filters))); } catch { /* A blocked/full storage must not block budget work. */ }
}

export function writeNextYearFilters(search: string, filters: BudgetFilters): string {
  const params = new URLSearchParams(search);
  params.set("nyFilters", "1"); // An explicitly cleared view must override an old stored view.
  for (const key of dimensions) writeFilterParam(params, `bf_${key}`, filters[key]);
  return params.toString();
}

export function validateNextYearFilters(filters: BudgetFilters, _combinations: Record<Exclude<BudgetDimension, "season">, string>[]): BudgetFilters {
  // An unavailable option is still an explicit reading restriction. Keep it visible
  // and empty rather than silently widening it to all SKUs after a year/version change.
  return clean(filters);
}
