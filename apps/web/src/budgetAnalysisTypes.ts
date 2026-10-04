// Read-only display facts. Values and calculation versions remain owned by their source.
export type BudgetDimension = "season" | "track" | "campus" | "product" | "grade" | "subject";
export type BudgetMetric = "revenue" | "business" | "old" | "new" | "total" | "discount" | "renewal";
export type BudgetValue = number | string | null;
export type BudgetFact = Record<BudgetDimension, string> & {
  id: string;
  revenue: BudgetValue; business: BudgetValue; listRevenue: BudgetValue;
  old: BudgetValue; new: BudgetValue; total: BudgetValue;
  current?: BudgetValue; carryout?: BudgetValue;
  price?: BudgetValue; renewal?: BudgetValue; share?: BudgetValue;
  detailDiscount?: BudgetValue; recognition?: BudgetValue;
  // Saved budget input rate × eligible preceding enrollment; not rounded old counts.
  renewalNumerator?: BudgetValue; renewalDenominator?: BudgetValue;
};
export type BudgetFilters = Record<BudgetDimension, string>;
export type BudgetPivotConfig = {
  rows: BudgetDimension[];
  column: BudgetDimension | "none";
  metrics: BudgetMetric[];
  sort: "revenue-desc" | "name-asc";
};
export type BudgetMetricValues = Record<BudgetMetric, number | null>;
export type BudgetPivotRow = {
  key: string; parentKey: string | null; depth: number; label: string;
  path: Partial<Record<BudgetDimension, string>>; leaf: boolean;
  cells: Record<string, BudgetMetricValues>; total: BudgetMetricValues;
};
export type BudgetPivot = {
  columns: { key: string; label: string; value: string | null }[];
  rows: BudgetPivotRow[];
  totals: BudgetMetricValues;
  columnTotals: Record<string, BudgetMetricValues>;
};
export type BudgetAnalysisState = {
  view: "board" | "pivot";
  filters: BudgetFilters;
  config: BudgetPivotConfig;
  unit: "yuan" | "wan";
};
