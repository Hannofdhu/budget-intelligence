import type { BudgetFilters, BudgetDimension } from "./budgetAnalysisTypes.ts";

const dimensions: BudgetDimension[] = ["season", "track", "campus", "product", "grade", "subject"];
const empty: BudgetFilters = { season: "", track: "", campus: "", product: "", grade: "", subject: "" };
type FilterStorage = Pick<Storage, "getItem" | "setItem">;
const browserStorage = () => { try { return typeof sessionStorage === "undefined" ? undefined : sessionStorage; } catch { return undefined; } };
const clean = (input: unknown): BudgetFilters => Object.fromEntries(dimensions.map(key => [key, typeof input === "object" && input !== null && typeof (input as Record<string, unknown>)[key] === "string" ? (input as Record<string, string>)[key] : ""])) as BudgetFilters;

/** URL is shareable view state; storage is only a per-year fallback, never budget data. */
export function readNextYearFilters(year: number, search: string, storage: FilterStorage | undefined = browserStorage()): BudgetFilters {
  const params = new URLSearchParams(search);
  if (params.get("nyFilters") === "1" || dimensions.some(key => params.has(`bf_${key}`))) return Object.fromEntries(dimensions.map(key => [key, params.get(`bf_${key}`) ?? ""])) as BudgetFilters;
  try { return clean(JSON.parse(storage?.getItem(`ny-filters-${year}`) ?? "null")); } catch { return { ...empty }; }
}

export function persistNextYearFilters(year: number, filters: BudgetFilters, storage: FilterStorage | undefined = browserStorage()): void {
  try { storage?.setItem(`ny-filters-${year}`, JSON.stringify(clean(filters))); } catch { /* A blocked/full storage must not block budget work. */ }
}

export function writeNextYearFilters(search: string, filters: BudgetFilters): string {
  const params = new URLSearchParams(search);
  params.set("nyFilters", "1"); // An explicitly cleared view must override an old stored view.
  for (const key of dimensions) { if (filters[key]) params.set(`bf_${key}`, filters[key]); else params.delete(`bf_${key}`); }
  return params.toString();
}

export function validateNextYearFilters(filters: BudgetFilters, combinations: Omit<BudgetFilters, "season">[]): BudgetFilters {
  return Object.fromEntries(dimensions.map(key => [key, key === "season" ? ["暑假", "秋季", "寒假", "春季"].includes(filters[key]) ? filters[key] : "" : combinations.some(row => row[key] === filters[key]) ? filters[key] : ""])) as BudgetFilters;
}
