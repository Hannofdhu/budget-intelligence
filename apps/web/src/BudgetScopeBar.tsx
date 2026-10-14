import type { ReactNode } from "react";
import "./budgetScope.css";

export type BudgetScopeDimension<T> = { key: keyof T & string; label: string; options: string[] };
type Props<T extends Record<string, string>> = {
  filters: T;
  dimensions: readonly BudgetScopeDimension<T>[];
  onChange: (next: T) => void;
  label?: string;
  count?: number;
  disabled?: boolean;
  children?: ReactNode;
  selectLabelPrefix?: string;
  onClear?: () => void;
};

/** A view-only scope control. Its caller owns filtering, URL state and data. */
export function BudgetScopeBar<T extends Record<string, string>>({ filters, dimensions, onChange,
  label = "当前范围", count, disabled = false, children, selectLabelPrefix = "筛选", onClear }: Props<T>) {
  const active = dimensions.filter(dimension => filters[dimension.key] !== "");
  const clear = () => {
    if (onClear) onClear();
    else onChange({ ...filters, ...Object.fromEntries(dimensions.map(({ key }) => [key, ""])) });
  };
  return <section className="budget-scope-bar" aria-label={label}>
    <div className="budget-scope-heading">
      <div className="budget-scope-summary"><strong>{label}</strong>{!active.length && <span>全部预算</span>}
        {count !== undefined && <small>{count.toLocaleString("zh-CN")} 条明细</small>}</div>
      <div className="budget-scope-actions">{children}{active.length > 0 && <button type="button" className="budget-scope-clear" disabled={disabled} onClick={clear}>清除筛选</button>}</div>
    </div>
    <div className="budget-scope-fields">{dimensions.map(({ key, label: dimensionLabel, options }) => {
      // A disabled/new business can legitimately have no facts. Keep its exact
      // selected value visible instead of silently broadening an empty scope.
      const values = [...new Set([...(filters[key] && !options.includes(filters[key]) ? [filters[key]] : []), ...options])].filter(value => value !== "");
      return <label key={key}><span>{dimensionLabel}</span><select aria-label={`${selectLabelPrefix}${dimensionLabel}`} value={filters[key]} disabled={disabled}
        onChange={event => onChange({ ...filters, [key]: event.target.value })}>
        <option value="">全部</option>{values.map(value => <option key={value} value={value}>{value}</option>)}
      </select></label>;
    })}</div>
    {active.length > 0 && <div className="budget-scope-chips" aria-label="已选筛选">{active.map(({ key, label: dimensionLabel }) => <button type="button" key={key} disabled={disabled}
      onClick={() => onChange({ ...filters, [key]: "" })} aria-label={`清除${dimensionLabel}${filters[key]}`}>
      {dimensionLabel}：{filters[key]}<span aria-hidden="true">×</span>
    </button>)}</div>}
  </section>;
}
