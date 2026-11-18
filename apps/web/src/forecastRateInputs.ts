import { forecastNumber, sumForecastRows, type ForecastAdjustmentKind, type ForecastAssumptions, type ForecastCalculation, type ForecastCampus } from "./forecastApi.ts";

export const campusRateKinds = ["additional", "new_withdrawal", "old_withdrawal"] as const;
export const campusRateLabels: Record<ForecastAdjustmentKind, string> = {
  additional: "预算内新生预计达成率", new_withdrawal: "后续新生退班率", old_withdrawal: "后续老生退班率",
};
export const campusRateKey = (season: string, campus: string, kind: ForecastAdjustmentKind) => JSON.stringify([season, campus, kind]);
function savedOrder(adjustments: ForecastAssumptions["adjustments"], saved: ForecastAssumptions) {
  const order = new Map(saved.adjustments.map((item, index) => [campusRateKey(item.season, item.campus, item.kind), index]));
  return adjustments.sort((a, b) => (order.get(campusRateKey(a.season, a.campus, a.kind)) ?? order.size) - (order.get(campusRateKey(b.season, b.campus, b.kind)) ?? order.size));
}
export function restoreCampusRates(assumptions: ForecastAssumptions, saved: ForecastAssumptions, season: string, campus: string, system = false): ForecastAssumptions {
  const adjustments = [...assumptions.adjustments.filter(item => item.season !== season || item.campus !== campus),
    ...(system ? [] : saved.adjustments.filter(item => item.season === season && item.campus === campus))];
  return { ...assumptions, adjustments: savedOrder(adjustments, saved) };
}
export function campusRateBase(row: ForecastCampus, kind: ForecastAdjustmentKind): number | null {
  return forecastNumber(kind === "additional" ? row.budget_new : kind === "new_withdrawal" ? row.new_base : row.old_base);
}
export function campusRateCount(row: ForecastCampus, kind: ForecastAdjustmentKind): number | null {
  return forecastNumber(kind === "additional" ? row.budgeted_additional : row[kind]);
}
export function campusRateValue(row: ForecastCampus, kind: ForecastAdjustmentKind, count = campusRateCount(row, kind)): number | null {
  const base = campusRateBase(row, kind), actual = kind === "additional" ? forecastNumber(row.current_business_new) : 0;
  return base === null || base <= 0 || count === null || actual === null ? null : (actual + count) / base * 100;
}
/** Rates are an input adapter only. Income, allocation and future quarters remain server-owned. */
export function campusRateToCount(row: ForecastCampus, kind: ForecastAdjustmentKind, input: string): number | null {
  if (!/^\d+(?:\.\d{1,3})?$/.test(input)) return null;
  const rate = Number(input), base = campusRateBase(row, kind);
  const actual = kind === "additional" ? forecastNumber(row.current_business_new) : 0;
  if (base === null || base <= 0 || actual === null || rate < 0 || rate > (kind === "additional" ? 1000000 : 100)) return null;
  // Thousandths of a percentage point avoid binary-float half-person ties.
  const scaled = Math.round(rate * 1000), product = base * scaled;
  if (!Number.isSafeInteger(product)) return null;
  const count = Math.max(0, Math.floor((product + 50000) / 100000) - actual);
  return Number.isSafeInteger(count) && count <= 10000000 ? count : null;
}
export function setCampusRate(assumptions: ForecastAssumptions, row: ForecastCampus, kind: ForecastAdjustmentKind, input: string, currentCount: number | null, saved?: { assumptions: ForecastAssumptions; row: ForecastCampus }): ForecastAssumptions {
  const value = campusRateToCount(row, kind, input);
  if (value === null || value === currentCount) return assumptions;
  const remaining = assumptions.adjustments.filter(item => campusRateKey(item.season, item.campus, item.kind) !== campusRateKey(row.season, row.campus, kind));
  if (saved && value === campusRateCount(saved.row, kind)) {
    return { ...assumptions, adjustments: savedOrder([...remaining, ...saved.assumptions.adjustments.filter(item => campusRateKey(item.season, item.campus, item.kind) === campusRateKey(row.season, row.campus, kind))], saved.assumptions) };
  }
  const adjustments = [...remaining, { season: row.season, campus: row.campus, kind, value, one_shot: true,
    reason: `${campusRateLabels[kind]} ${input}%；参考基数 ${campusRateBase(row, kind)} 人次；换算 ${value} 人次。仅用于本次预测，后续更新恢复原算法。` }];
  return { ...assumptions, adjustments: saved ? savedOrder(adjustments, saved.assumptions) : adjustments };
}
export function campusForecastTotals(calculation: ForecastCalculation, season: string, campus: string) {
  const rows = calculation.rows.filter(row => row.season === season && row.campus === campus);
  return { people: sumForecastRows(rows, "forecast_total"), revenue: sumForecastRows(rows, "forecast_revenue") };
}
