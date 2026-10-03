import { aggregateBudgetFacts } from "./budgetAnalysis.ts";
import type { BudgetDimension, BudgetFact, BudgetMetric } from "./budgetAnalysisTypes.ts";

export type BudgetSelectionCell = {
  row: { path: Partial<Record<BudgetDimension, string>> };
  column: { value: string | null };
  value: number | null;
};

function contains(fact: BudgetFact, cell: BudgetSelectionCell, column: BudgetDimension | "none") {
  return Object.entries(cell.row.path).every(([key, value]) => fact[key as BudgetDimension] === value)
    && (column === "none" || cell.column.value === null || fact[column] === cell.column.value);
}

// Filter the original facts once: selecting overlapping subtotal cells must
// neither multiply records nor discard the unselected children of a subtotal.
export function budgetFactsForCells(facts: BudgetFact[], cells: BudgetSelectionCell[], column: BudgetDimension | "none") {
  return facts.filter(fact => cells.some(cell => contains(fact, cell, column)));
}

export function budgetSelectionStatistics(facts: BudgetFact[], cells: BudgetSelectionCell[], metric: BudgetMetric, column: BudgetDimension | "none") {
  const numbers = cells.map(cell => cell.value).filter((value): value is number => value !== null && Number.isFinite(value));
  if (numbers.length === 0) return null;
  const members = budgetFactsForCells(facts, cells, column);
  const hasOverlap = members.some(fact => cells.filter(cell => contains(fact, cell, column)).length > 1);
  const sum = metric === "discount" ? null : aggregateBudgetFacts(members)[metric];
  return {
    count: numbers.length,
    sum,
    average: !hasOverlap && sum !== null ? sum / numbers.length : null,
    min: Math.min(...numbers),
    max: Math.max(...numbers),
    hasOverlap,
  };
}
