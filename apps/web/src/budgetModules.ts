export type BudgetModule = "budget" | "forecast" | "next-year";
export type BudgetModuleYears = Record<BudgetModule, number>;

// These are the existing data destinations, not new fiscal-year routing.
export const budgetModules = [
  { key: "cockpit", href: "/budget/cockpit" },
  { key: "budget", href: "/budget/workspace?archive=fy27&step=5" },
  { key: "forecast", href: "/budget/forecast?archive=fy27" },
  { key: "next-year", href: "/budget/next-year" },
  { key: "ai", href: "/budget/ai" },
] as const;

const archiveYear = (href: string) => {
  const archive = new URL(href, "https://budget.local").searchParams.get("archive");
  const digits = /^fy(\d{2}|\d{4})$/i.exec(archive ?? "")?.[1];
  if (!digits) throw new Error("归档预算入口缺少财年");
  return digits.length === 2 ? 2000 + Number(digits) : Number(digits);
};
const archivedYear = archiveYear(budgetModules.find(module => module.key === "budget")!.href), forecastYear = archiveYear(budgetModules.find(module => module.key === "forecast")!.href);
export const defaultBudgetModuleYears: Readonly<BudgetModuleYears> = {
  budget: archivedYear, forecast: forecastYear, "next-year": forecastYear + 1,
};
const names: Record<BudgetModule, string> = { budget: "定版预算", forecast: "全年预测", "next-year": "预算编制" };
export const fiscalYearLabel = (year: number) => `FY${String(year).slice(-2)}`;
export const budgetModuleTitle = (module: BudgetModule) => names[module];
export const budgetModuleName = (module: BudgetModule, year: number) => `${fiscalYearLabel(year)}${names[module]}`;

/** Years belong to each module independently; a planning year need not be base + 1. */
export function budgetModuleYears(overrides: Partial<BudgetModuleYears> = {}): BudgetModuleYears {
  const years = { ...defaultBudgetModuleYears };
  for (const module of Object.keys(years) as BudgetModule[]) {
    const year = overrides[module];
    if (typeof year === "number" && Number.isInteger(year) && year >= 2000 && year <= 2199) years[module] = year;
  }
  return years;
}
