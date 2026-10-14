import type { ReactNode } from "react";
import { FilterSelect } from "./FilterSelect";
import { filterLabel, hasFilter, type FilterValue } from "./filterSelection";
import "./budgetScope.css";

export type BudgetScopeDimension<T> = { key: keyof T & string; label: string; options: string[] };
type Props<T extends Record<string, FilterValue>> = {
  filters: T;
  dimensions: readonly BudgetScopeDimension<T>[];
  onChange: (next: T) => void;
  label?: string;
  count?: number;
  countLabel?: string;
  disabled?: boolean;
  children?: ReactNode;
  selectLabelPrefix?: string;
  onClear?: () => void;
};

/** A view-only scope control. Its caller owns filtering, URL state and data. */
export function BudgetScopeBar<T extends Record<string, FilterValue>>({ filters, dimensions, onChange,
  label = "当前范围", count, countLabel = "条明细", disabled = false, children, selectLabelPrefix = "筛选", onClear }: Props<T>) {
  const active = dimensions.filter(dimension => hasFilter(filters[dimension.key]));
  const clear = () => {
    if (onClear) onClear();
    else onChange({ ...filters, ...Object.fromEntries(dimensions.map(({ key }) => [key, ""])) });
  };
  return <section className="budget-scope-bar" aria-label={label}>
    <div className="budget-scope-heading">
      <div className="budget-scope-summary"><strong>{label}</strong>{!active.length && <span>全部预算</span>}
        {count !== undefined && <small>{count.toLocaleString("zh-CN")} {countLabel}</small>}</div>
      <div className="budget-scope-actions">{children}{active.length > 0 && <button type="button" className="budget-scope-clear" disabled={disabled} onClick={clear}>清除筛选</button>}</div>
    </div>
    <div className="budget-scope-fields">{dimensions.map(({ key, label: dimensionLabel, options }) => {
      // A disabled/new business can legitimately have no facts. Keep its exact
      // selected value visible instead of silently broadening an empty scope.
      return <label key={key}><span>{dimensionLabel}</span><FilterSelect aria-label={`${selectLabelPrefix}${dimensionLabel}`} value={filters[key]} disabled={disabled}
        options={options} onChange={value => onChange({ ...filters, [key]: value })}/></label>;
    })}</div>
    {active.length > 0 && <div className="budget-scope-chips" aria-label="已选筛选">{active.map(({ key, label: dimensionLabel }) => <button type="button" key={key} disabled={disabled}
      onClick={() => onChange({ ...filters, [key]: "" })} aria-label={`清除${dimensionLabel}${filterLabel(filters[key])}`}>
      {dimensionLabel}：{filterLabel(filters[key])}<span aria-hidden="true">×</span>
    </button>)}</div>}
  </section>;
}
