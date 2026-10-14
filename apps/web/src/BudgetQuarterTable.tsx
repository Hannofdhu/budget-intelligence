import { Fragment, useMemo, type ReactNode } from "react";
import { ActionTable } from "./ActionTable";
import { buildBudgetPivot, dimensionLabels, seasons } from "./budgetAnalysis";
import type { BudgetDimension, BudgetFact, BudgetMetricValues } from "./budgetAnalysisTypes";
import type { ExportColumn } from "./budgetExport";
import { observeBudgetTableHeader } from "./budgetTableLayout";

type QuarterMetric = "total" | "new" | "old" | "revenue";
type Props = {
  facts: BudgetFact[];
  unit: "yuan" | "wan";
  fiscalYear: number;
  rowDimensions: readonly BudgetDimension[];
  metrics: readonly { key: QuarterMetric; label: string }[];
  title: string;
  tableLabel: string;
  tableClassName: string;
  headerActions?: ReactNode;
  onSelect: (path: Partial<Record<BudgetDimension, string>>, label: string) => void;
  onRowSelect?: (path: Partial<Record<BudgetDimension, string>>) => void;
};
const periods = [...seasons, "合计"];

/** Shared read-only quarterly view; all transfers retain the same filtered population. */
export function BudgetQuarterTable({ facts, unit, fiscalYear, rowDimensions, metrics, title, tableLabel, tableClassName, headerActions, onSelect, onRowSelect }: Props) {
  const pivot = useMemo(() => buildBudgetPivot(facts, {
    rows: [...rowDimensions], column: "season", metrics: metrics.map(metric => metric.key), sort: "name-asc",
  }), [facts, rowDimensions, metrics]);
  // Intermediate dimension groups are not additional observations.
  const rows = pivot.rows.filter(row => row.leaf);
  const divisor = unit === "wan" ? 10000 : 1, unitLabel = unit === "wan" ? "万元" : "元";
  const heading = (metric: typeof metrics[number]) => metric.key === "revenue" ? `${metric.label}（${unitLabel}）` : metric.label;
  const valueFor = (cells: Record<string, BudgetMetricValues>, total: BudgetMetricValues, period: string, metric: QuarterMetric) => {
    if (period === "合计") return total[metric];
    const column = pivot.columns.find(item => item.value === period);
    return column ? cells[column.key]?.[metric] ?? null : null;
  };
  const displayValue = (value: number | null, metric: QuarterMetric) => value === null ? "—"
    : new Intl.NumberFormat("zh-CN", { maximumFractionDigits: metric === "revenue" ? 2 : 8 }).format(metric === "revenue" ? value / divisor : value);
  const exportColumns: ExportColumn[] = [
    ...rowDimensions.map(key => ({ key, label: dimensionLabels[key] })),
    ...periods.flatMap((period, index) => metrics.map(metric => ({
      key: `${index}_${metric.key}`, label: `${period} · ${heading(metric)}`, format: metric.key === "revenue" ? "money" as const : "count" as const,
    }))),
  ];
  const exportValues = (cells: Record<string, BudgetMetricValues>, total: BudgetMetricValues) => Object.fromEntries(periods.flatMap((period, index) => metrics.map(metric => {
    const value = valueFor(cells, total, period, metric.key);
    return [`${index}_${metric.key}`, value === null ? null : metric.key === "revenue" ? value / divisor : value];
  })));
  const exportRows = rows.map(row => ({ ...row.path, ...exportValues(row.cells, row.total) }));
  if (rows.length) exportRows.push({ ...Object.fromEntries(rowDimensions.map((dimension, index) => [dimension, index === 0 ? "合计" : ""])), ...exportValues(pivot.columnTotals, pivot.totals) });
  const cell = (path: Partial<Record<BudgetDimension, string>>, period: string, metric: typeof metrics[number], value: number | null) => {
    const names = rowDimensions.map(dimension => path[dimension]).filter(Boolean);
    const label = [...names, period].join(" / ");
    return <button type="button" className={`ba-cell${period === "合计" ? " ba-total-cell" : ""}`}
      aria-label={`${names.join("，") || "合计"}，${period}，${metric.label}，${displayValue(value, metric.key)}`}
      disabled={value === null}
      onClick={() => onSelect(period === "合计" ? path : { ...path, season: period }, `${label} · ${metric.label}`)}>
      {displayValue(value, metric.key)}
    </button>;
  };

  return <section className="ba-panel ba-quarter-budget-panel" aria-label={title}>
    <header><h3>{title}</h3>{headerActions}</header>
    {rows.length ? <ActionTable keyboardNavigation className={`ba-table ${tableClassName}`} tableLabel={tableLabel}
        scrollClassName="ba-table-wrap" scrollRef={observeBudgetTableHeader} scrollLabel={`${tableLabel}，可横向滚动`}
        exportData={{ columns: exportColumns, rows: exportRows, filename: `FY${String(fiscalYear).slice(-2)}-${title}` }}>
        <thead>
          <tr>{rowDimensions.map(dimension => <th key={dimension} scope="col" rowSpan={2} className={`ba-${dimension}-label`}>{dimensionLabels[dimension]}</th>)}{periods.map(period => <th key={period} scope="colgroup" colSpan={metrics.length}>{period}</th>)}</tr>
          <tr>{periods.map(period => <Fragment key={period}>{metrics.map(metric => <th scope="col" key={metric.key} title={metric.key === "revenue" ? "已保存预算中的季度结转收入" : undefined}>{heading(metric)}</th>)}</Fragment>)}</tr>
        </thead>
        <tbody>{rows.map(row => <tr key={row.key}>
          {rowDimensions.map(dimension => <th key={dimension} scope="row" className={`ba-${dimension}-label`}>{onRowSelect
            ? <button type="button" className="ba-text-button" onClick={() => onRowSelect(row.path)}>{row.path[dimension]}</button> : row.path[dimension]}</th>)}
          {periods.map(period => <Fragment key={period}>{metrics.map(metric => <td key={metric.key}>{cell(row.path, period, metric, valueFor(row.cells, row.total, period, metric.key))}</td>)}</Fragment>)}
        </tr>)}</tbody>
        <tfoot><tr><th scope="row" colSpan={rowDimensions.length} className={`ba-${rowDimensions[0]}-label`}>合计</th>{periods.map(period => <Fragment key={period}>{metrics.map(metric => <td key={metric.key}>{cell({}, period, metric, valueFor(pivot.columnTotals, pivot.totals, period, metric.key))}</td>)}</Fragment>)}</tr></tfoot>
      </ActionTable> : <div className="ba-empty">当前范围没有招生预算</div>}
  </section>;
}
