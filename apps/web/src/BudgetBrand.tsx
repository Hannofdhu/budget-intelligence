import "./budgetBrand.css";

/** The visible product name supplies the accessible name of the surrounding link. */
export function BudgetMark() {
  return <img className="budget-brand-mark" src="/brand/deconstruction-window.svg" width="32" height="32" alt="" aria-hidden="true" />;
}

export function BudgetBrand() {
  return <><BudgetMark /><strong>预算智解</strong></>;
}
