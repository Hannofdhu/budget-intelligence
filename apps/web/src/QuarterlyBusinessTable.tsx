import { useMemo, useState, type ReactNode } from "react";
import { ActionTable } from "./ActionTable";
import { arrangeQuarterlyBusiness, quarterlyBusinessCopyValue, quarterlyBusinessExport, type QuarterlyValueColumn } from "./quarterlyBusiness";
import { observeQuarterlyBusinessScroll } from "./quarterlyBusinessLayout";
import "./quarterlyBusiness.css";

export type QuarterlyBusinessColumn<Row> = QuarterlyValueColumn<Row> & { render?: (row: Row) => ReactNode };
export type QuarterlyBusinessTableProps<Row> = {
  rows: readonly Row[];
  /** Exact identity across quarters, not a display label or fuzzy dimension match. */
  rowKey: (row: Row) => string;
  season: (row: Row) => string;
  businessColumns: readonly QuarterlyBusinessColumn<Row>[];
  metrics: readonly QuarterlyBusinessColumn<Row>[];
  filename: string;
  tableLabel: string;
  seasons?: readonly string[];
  pageSize?: number;
  /** Return to the existing editor; this table never edits or persists values. */
  onCellAction?: (row: Row, metricKey?: string) => void;
  /** Some saved cells have no corresponding input in the existing editor. */
  canCellAction?: (row: Row, metricKey: string) => boolean;
  actionLabel?: string;
};

const displayed = <Row,>(row: Row, column: QuarterlyBusinessColumn<Row>) => {
  if (column.render) return column.render(row);
  const value = column.value(row);
  if (value === null || value === undefined || value === "") return "未填写";
  if (typeof value === "boolean") return value ? "是" : "否";
  if (typeof value !== "number") return value;
  return column.format === "rate" ? `${(value * 100).toLocaleString("zh-CN", { maximumFractionDigits: 2 })}%`
    : value.toLocaleString("zh-CN", { maximumFractionDigits: column.format === "count" ? 8 : 2 });
};

export function QuarterlyBusinessTable<Row>({ rows, rowKey, season, businessColumns, metrics, filename, tableLabel,
  seasons, pageSize = 20, onCellAction, canCellAction, actionLabel = "定位编辑" }: QuarterlyBusinessTableProps<Row>) {
  const [page, setPage] = useState(0);
  const arrangement = useMemo(() => arrangeQuarterlyBusiness(rows, rowKey, season, seasons), [rows, rowKey, season, seasons]);
  const transfer = useMemo(() => quarterlyBusinessExport(arrangement.groups, arrangement.seasons, businessColumns, metrics), [arrangement, businessColumns, metrics]);
  const copyRows = useMemo(() => [["业务", "指标", ...arrangement.seasons], ...arrangement.groups.flatMap(group => metrics.map(metric => [
    businessColumns.map(column => quarterlyBusinessCopyValue(group.sample, column)).join(" / "), metric.label,
    ...arrangement.seasons.map(value => quarterlyBusinessCopyValue(group.bySeason.get(value), metric)),
  ]))], [arrangement, businessColumns, metrics]);
  const size = Math.max(1, Math.floor(pageSize));
  const pageCount = Math.ceil(arrangement.groups.length / size);
  const currentPage = Math.min(page, Math.max(0, pageCount - 1));
  const visible = arrangement.groups.slice(currentPage * size, (currentPage + 1) * size);
  return <div className="quarterly-business">
    {arrangement.duplicateCount > 0 && <p className="qbt-duplicate" role="status">{arrangement.duplicateCount} 组业务同季有多条明细，已逐条保留，未跨季配对；可切回逐季明细查看。</p>}
      <ActionTable tableLabel={tableLabel} exportData={{ ...transfer, filename }} copyRows={copyRows} keyboardNavigation
        scrollClassName="qbt-scroll" scrollRef={observeQuarterlyBusinessScroll} scrollLabel={`${tableLabel}，四季横排，可横向滚动`}>
        <thead><tr><th scope="col" className="qbt-business-heading">业务</th><th scope="col">指标</th>{arrangement.seasons.map(value => <th key={value} scope="col">{value}</th>)}</tr></thead>
        <tbody>{visible.flatMap(group => metrics.map((metric, metricIndex) => <tr key={`${group.key}-${metric.key}`} className={metricIndex === 0 ? "qbt-group-start" : undefined}>
          {metricIndex === 0 && <th scope="rowgroup" rowSpan={metrics.length} className="qbt-business-cell">{businessColumns.map(column => <span key={column.key} title={column.label}>{column.render ? column.render(group.sample) : displayed(group.sample, column)}</span>)}{group.duplicate && <small>独立季度明细</small>}</th>}
          <th scope="row" className="qbt-metric">{metric.label}</th>
          {arrangement.seasons.map(value => { const source = group.bySeason.get(value); const businessLabel = source === undefined ? "" : businessColumns.map(column => quarterlyBusinessCopyValue(source, column)).filter(Boolean).join(" / "); return <td key={value} className={source === undefined ? "qbt-missing" : undefined}>
            {source === undefined ? <span aria-label="无该季度明细" title="无该季度明细">—</span> : onCellAction && (!canCellAction || canCellAction(source, metric.key)) ? <button type="button" className="qbt-value-action" onClick={() => onCellAction(source, metric.key)} aria-label={`${businessLabel}，${value}${metric.label}，${actionLabel}`} title={actionLabel}>{displayed(source, metric)}</button> : displayed(source, metric)}
          </td>; })}
        </tr>))}</tbody>
      </ActionTable>
      {rows.length === 0 && <p className="qbt-empty">当前范围没有明细。清除筛选可查看全部。</p>}
    <div className="qbt-pagination"><span>{arrangement.groups.length} 条业务 · {rows.length} 条季度明细</span><div>
      <button type="button" disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}>上一页</button>
      <span>{pageCount ? currentPage + 1 : 0} / {pageCount} 页</span>
      <button type="button" disabled={currentPage + 1 >= pageCount} onClick={() => setPage(currentPage + 1)}>下一页</button>
    </div></div>
  </div>;
}
