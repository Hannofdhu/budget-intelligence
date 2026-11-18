import { forecastNumber, sumForecastRows, type ForecastIssue, type ForecastRow } from "./forecastApi";
import { nextYearSeasons, type NextYearCalculation, type NextYearCombination, type NextYearInputs } from "./nextYearApi";

export type SkuBasisStatus = "loading" | "error" | "ready";
export interface SkuEvidence {
  sourceLabel: string;
  historyRevenue: number | null;
  historyStudents: number | null;
  historyState: "value" | "missing" | "empty" | "incomplete" | SkuBasisStatus;
  summerOld: number | null;
  summerState: "value" | "excluded" | "pending" | "unknown";
  openingCarryover: number | null;
}
const skuKey = (row: Pick<NextYearCombination, "campus" | "track" | "product" | "grade" | "subject">) =>
  JSON.stringify([row.campus, row.track, row.product, row.grade, row.subject]);

/** Display-only projection of the frozen basis and existing next-year calculation. */
export function buildNextYearSkuEvidence(
  combinations: NextYearCombination[], openingSources: NextYearInputs["opening_sources"],
  calculation: NextYearCalculation | undefined, basisRows: ForecastRow[] | null,
  basisStatus: SkuBasisStatus, stale = false,
  basisIssues: ForecastIssue[] = [],
): Map<string, SkuEvidence> {
  const history = new Map<string, ForecastRow[]>();
  for (const row of basisRows ?? []) {
    const key = skuKey(row), group = history.get(key) ?? [];
    group.push(row); history.set(key, group);
  }
  // Only the frozen engine's reviewed empty slots can explain an absent actual.
  // Unknown facts in other rows must not acquire a zero through browsing defaults.
  const reviewedEmpty = new Set(basisIssues.filter(issue => issue.code === "FINALIZED_ACTUAL_MISSING"
    && issue.severity === "INFO" && issue.detail?.classification === "EMPTY_BUDGET_SLOT_NO_SOURCE_BUSINESS"
    && issue.dims && ["campus", "track", "product", "grade", "subject"].every(key => Boolean(issue.dims?.[key as keyof typeof issue.dims])))
    .map(issue => JSON.stringify([skuKey(issue.dims as ForecastRow), issue.season])));
  const summer = new Map(calculation?.rows.filter(row => row.season === "暑假").map(row => [row.combination_id, row]));
  const promoted = new Set(openingSources.filter(row => row.target_id && combinations.some(target => target.id === row.target_id && target.grade !== row.grade)).map(row => row.target_id));
  return new Map(combinations.map(row => {
    const past = history.get(skuKey(row)) ?? [];
    const complete = nextYearSeasons.every(season => past.some(item => item.season === season));
    const empty = promoted.has(row.id) && past.every(item => {
      const reviewed = reviewedEmpty.has(JSON.stringify([skuKey(item), item.season]));
      return [item.forecast_revenue, item.forecast_total].every(value => forecastNumber(value) === 0 || value === null && reviewed);
    });
    const historyState = basisStatus !== "ready" ? basisStatus : !past.length ? "missing" : !complete ? "incomplete" : empty ? "empty" : "value";
    const summerOld = forecastNumber(summer.get(row.id)?.old_students);
    return [row.id, {
      sourceLabel: row.added ? "手工新增" : promoted.has(row.id) ? "升年级承接" : basisStatus === "ready" && past.length ? "基准已有" : "来源待核对",
      historyRevenue: historyState === "value" || historyState === "empty" ? sumForecastRows(past, "forecast_revenue") : null,
      historyStudents: historyState === "value" || historyState === "empty" ? sumForecastRows(past, "forecast_total") : null,
      historyState,
      summerOld: !row.enabled || stale ? null : summerOld,
      summerState: !row.enabled ? "excluded" : stale ? "pending" : summerOld === null ? "unknown" : "value",
      openingCarryover: forecastNumber(row.opening_carryover_yuan),
    } satisfies SkuEvidence];
  }));
}
