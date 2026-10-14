import { matchesFilter, type FilterValue } from "./filterSelection.ts";

export type BudgetSelection = { dimension: string; name: string } | null;

export function budgetRowsInScope<T>(rows: T[], filters: Record<string, FilterValue>, fields: Record<string, string> = {}): T[] {
  return rows.filter(row => Object.entries(filters).every(([key, value]) => matchesFilter(String(row[(fields[key] ?? key) as keyof T]), value)));
}

export function validBudgetSelection<T>(rows: T[], selection: BudgetSelection): BudgetSelection {
  return selection && selectedBudgetRows(rows, selection).length ? selection : null;
}

// Selecting a summary never changes the surrounding filters or totals.
export function selectedBudgetRows<T>(rows: T[], selection: BudgetSelection): T[] {
  return selection ? rows.filter(row => String(row[selection.dimension as keyof T]) === selection.name) : rows;
}

export function initialBudgetRevision(budget: { draft_revision_id: string | null; published_revision_id: string | null }): string | null {
  return budget.published_revision_id ?? budget.draft_revision_id;
}
