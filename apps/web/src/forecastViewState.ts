import { readFilterParam, writeFilterParam, type FilterValue } from "./filterSelection.ts";

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
