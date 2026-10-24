import { getBudgetSession } from "./budgetReadCache";
import { getForecast, getForecastRevenueTrend, getForecastSchedule } from "./forecastApi";
import { getNextYearBudget, getNextYearCatalog } from "./nextYearApi";
import { getCockpit } from "./cockpit";

/** Read saved results only. Never refresh BI, calculate, create, save, or confirm. */
let pending: Promise<void> | null = null;
export function preloadBudgetModules() {
  if (pending) return;
  pending = getBudgetSession().then(async actor => {
    await Promise.allSettled([
      getForecast(actor).then(async saved => {
        if (saved.version) await Promise.allSettled([
          getForecastRevenueTrend(actor, 30, saved.version.id), getForecastSchedule(actor),
        ]);
      }),
      getNextYearBudget(actor).then(() => getNextYearCatalog(actor)),
      getCockpit(actor),
    ]);
  }).catch(() => {}).finally(() => { pending = null; });
}
