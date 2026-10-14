/** View state only. Old single-value links and saved views remain valid. */
export type FilterValue = string | string[];

export function filterValues(value: unknown): string[] {
  const values = typeof value === "string" ? [value] : Array.isArray(value) ? value : [];
  return [...new Set(values.filter((item): item is string => typeof item === "string" && item !== ""))];
}

export function normalizeFilter(value: unknown): FilterValue {
  const values = filterValues(value);
  return values.length > 1 ? values : values[0] ?? "";
}

export const hasFilter = (value: unknown): boolean => filterValues(value).length > 0;
export const filterLabel = (value: unknown): string => filterValues(value).join("、");

/** OR inside one dimension, AND is applied by the owning scope across dimensions. */
export function matchesFilter(value: string | undefined, selection: FilterValue | undefined, ignoreMissing = false): boolean {
  const selected = filterValues(selection);
  return selected.length === 0 || ignoreMissing && value === undefined || value !== undefined && selected.includes(value);
}

export function toggleFilterValue(selection: FilterValue, value: string): FilterValue {
  if (!value) return "";
  const values = filterValues(selection);
  return normalizeFilter(values.includes(value) ? values.filter(item => item !== value) : [...values, value]);
}

export function readFilterParam(params: URLSearchParams, key: string): FilterValue {
  return normalizeFilter(params.getAll(key));
}

export function writeFilterParam(params: URLSearchParams, key: string, value: FilterValue): void {
  params.delete(key);
  for (const item of filterValues(value)) params.append(key, item);
}
