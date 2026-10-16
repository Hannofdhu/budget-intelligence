import { readFilterParam, writeFilterParam, type FilterValue } from "./filterSelection.ts";
import { forecastGroupDimensions, type ForecastGroupDimension } from "./forecastApi.ts";

export const forecastFilterDimensions = ["season", "track", "campus", "product", "grade", "subject"] as const;
export type ForecastViewFilters = Record<typeof forecastFilterDimensions[number], FilterValue>;

/** Distinguish an old link's open-quarter default from an explicitly cleared scope. */
export function hasForecastFilterState(search: string): boolean {
  const params = new URLSearchParams(search);
  return params.has("bf_scope") || forecastFilterDimensions.some(key => params.has(`bf_${key}`));
}

/** Unknown members stay selected and yield an honest empty range, never become “all”. */
export function readForecastViewFilters(search: string, defaultSeason: string = ""): ForecastViewFilters {
  const params = new URLSearchParams(search), explicit = hasForecastFilterState(search);
  return Object.fromEntries(forecastFilterDimensions.map(key => [key,
    key === "season" && !explicit ? defaultSeason : readFilterParam(params, `bf_${key}`),
  ])) as ForecastViewFilters;
}

/** Own only bf_* scope fields; saved version, archive and other URL state survive. */
export function writeForecastViewFilters(search: string, filters: ForecastViewFilters): string {
  const params = new URLSearchParams(search);
  for (const key of forecastFilterDimensions) writeFilterParam(params, `bf_${key}`, filters[key]);
  params.set("bf_scope", "1");
  return params.toString();
}

function normalizedGroupDimensions(groups: readonly string[]): ForecastGroupDimension[] {
  const selected = [...new Set(groups.filter((group): group is ForecastGroupDimension =>
    forecastGroupDimensions.includes(group as ForecastGroupDimension)))].slice(0, 6);
  return selected.length ? selected : ["track"];
}

/** Grouping changes table presentation only, never the selected data scope. */
export function readForecastGroupDimensions(search: string): ForecastGroupDimension[] {
  return normalizedGroupDimensions(new URLSearchParams(search).getAll("fg_row"));
}

/** Own only fg_row; exact snapshot, scope, and unrelated navigation state survive. */
export function writeForecastGroupDimensions(search: string, groups: readonly ForecastGroupDimension[]): string {
  const params = new URLSearchParams(search);
  params.delete("fg_row");
  for (const group of normalizedGroupDimensions(groups)) params.append("fg_row", group);
  return params.toString();
}
