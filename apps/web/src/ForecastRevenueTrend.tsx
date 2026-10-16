import { useEffect, useId, useMemo, useRef, useState } from "react";
import type { ApiIdentity } from "./api";
import { ActionTable } from "./ActionTable";
import { BudgetDataSection } from "./BudgetDataSection";
import { BudgetVersionTrace } from "./BudgetVersionIdentity";
import { versionLabel, versionTimestamp, versionStatusLabel, versionUpdateLabel } from "./budgetVersionDisplay";
import type { ExportColumn } from "./budgetExport";
import { forecastNumber, forecastSeasons, getForecastRevenueTrend, getForecastSchedule,
  type ForecastRevenueTrendEnvelope, type ForecastRevenueTrendPoint, type ForecastSchedule,
  type ForecastTrendDays, type ForecastTrendPeriod } from "./forecastApi";
import "./forecastRevenueTrend.css";

const periods: readonly ForecastTrendPeriod[] = ["全年", ...forecastSeasons];
const rangeLabel = (days: ForecastTrendDays) => days === "all" ? "全部历史" : `近 ${days} 天`;
const amount = (value: string | null | undefined) => {
  const numeric = forecastNumber(value);
  return numeric === null ? "未提供" : `${(numeric / 10000).toLocaleString("zh-CN", { maximumFractionDigits: 2 })} 万元`;
};
const savedTime = versionTimestamp;
const datePosition = (value: string) => Date.parse(`${value}T00:00:00+08:00`);
const trendPlotHeight = 230;

/** Only transforms saved values for presentation. Missing historical actuals remain missing. */
export function forecastTrendTransfer(data: ForecastRevenueTrendEnvelope, period: ForecastTrendPeriod) {
  const columns: ExportColumn[] = [
    { key: "date", label: "快照日期（北京时间）" }, { key: "period", label: "期间" },
    { key: "forecast_revenue", label: "预计结转收入（万元）", format: "money" },
    { key: "actual_revenue", label: "截至当日实际结转收入（万元）", format: "money" },
    { key: "actual_status", label: "实际数据说明" }, { key: "version", label: "预测版本" },
    { key: "version_id", label: "精确版本标识" }, { key: "status", label: "确认状态" },
    { key: "as_of", label: "数据截至日期" }, { key: "created_at", label: "保存时间（北京时间）" },
    { key: "generation", label: "生成方式" },
  ];
  const rows = data.points.map(point => {
    const value = point.periods[period], forecast = forecastNumber(value?.forecast_revenue), actual = forecastNumber(value?.actual_revenue);
    return { date: point.date, period, forecast_revenue: forecast === null ? null : forecast / 10000,
      actual_revenue: actual === null ? null : actual / 10000,
      actual_status: value?.actual_status === "ZERO_DEFAULTED" ? "含未知实际按0计" : value?.actual_status === "AVAILABLE" ? "已保存实际" : "历史快照未提供实际",
      version: versionLabel(point.sequence), version_id: point.version_id, status: versionStatusLabel(point.status),
      as_of: point.as_of, created_at: savedTime(point.created_at), generation: versionUpdateLabel(null, point.generation) };
  });
  return { columns, rows, filename: `FY${String(data.baseline_year).slice(-2)}-${period}-结转收入趋势-多版本-${data.points[0]?.date ?? "无保存日"}-${data.points.at(-1)?.date ?? "无保存日"}` };
}

/** Geometry uses chronological spacing and breaks at nulls; it never invents earlier observations. */
export function forecastTrendGeometry(points: ForecastRevenueTrendPoint[], period: ForecastTrendPeriod, width = 900) {
  const left = 72, right = Math.max(left + 1, width - 24), top = 14, bottom = trendPlotHeight - 38;
  const values = points.flatMap(point => [forecastNumber(point.periods[period]?.forecast_revenue), forecastNumber(point.periods[period]?.actual_revenue)])
    .filter((value): value is number => value !== null).map(value => value / 10000);
  const low = Math.min(0, ...values), high = Math.max(0, ...values), span = high - low || 1;
  const min = low < 0 ? low - span * .06 : 0, max = high > 0 ? high + span * .06 : low < 0 ? 0 : 1;
  const first = datePosition(points[0]?.date ?? ""), last = datePosition(points.at(-1)?.date ?? "");
  const x = (index: number) => points.length === 1 ? (left + right) / 2 : left + (datePosition(points[index].date) - first) / (last - first || 1) * (right - left);
  const y = (value: number) => bottom - (value - min) / (max - min) * (bottom - top);
  const series = (key: "forecast_revenue" | "actual_revenue") => {
    let connected = false;
    return points.map((point, index) => {
      const value = forecastNumber(point.periods[period]?.[key]);
      if (value === null) { connected = false; return ""; }
      const part = `${connected ? "L" : "M"}${x(index)},${y(value / 10000)}`; connected = true; return part;
    }).filter(Boolean).join(" ");
  };
  return { left, right, top, bottom, height: trendPlotHeight, dateY: trendPlotHeight - 10, min, max, x, y, series,
    ticks: Array.from({ length: 5 }, (_, index) => min + (max - min) * index / 4) };
}

interface ViewProps {
  data: ForecastRevenueTrendEnvelope | null; versionId: string; loading: boolean; error: string; days: ForecastTrendDays;
  shownDays?: ForecastTrendDays; schedule: ForecastSchedule | null; scheduleError?: boolean;
  onDays: (days: ForecastTrendDays) => void; onRetry: () => void; onVersion: (id: string) => void; busy?: boolean;
}

export function ForecastRevenueTrendView({ data, versionId, loading, error, days, shownDays = days, schedule, scheduleError = false, onDays, onRetry, onVersion, busy = false }: ViewProps) {
  const [period, setPeriod] = useState<ForecastTrendPeriod>("全年"), [selectedId, setSelectedId] = useState("");
  const plot = useRef<HTMLDivElement>(null), [chartWidth, setChartWidth] = useState(900);
  const chartId = useId(), points = data?.points ?? [];
  const selectedIndex = Math.max(0, points.findIndex(point => point.version_id === selectedId) < 0 ? points.length - 1 : points.findIndex(point => point.version_id === selectedId));
  const selected = points[selectedIndex], value = selected?.periods[period];
  const geometry = useMemo(() => forecastTrendGeometry(points, period, chartWidth), [points, period, chartWidth]);
  const transfer = useMemo(() => data ? forecastTrendTransfer(data, period) : null, [data, period]);
  const visibleColumns = transfer?.columns.filter(column => column.key !== "version_id") ?? [];
  const selectIndex = (index: number) => { if (points[index]) setSelectedId(points[index].version_id); };
  const hasNumbers = points.some(point => forecastNumber(point.periods[period]?.forecast_revenue) !== null || forecastNumber(point.periods[period]?.actual_revenue) !== null);
  useEffect(() => {
    const element = plot.current;
    if (!element) return;
    const measure = () => setChartWidth(Math.max(240, element.getBoundingClientRect().width));
    measure();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    observer?.observe(element);
    return () => observer?.disconnect();
  }, [hasNumbers]);
  const dayIndices = [...new Set(chartWidth < 500 ? [0, points.length - 1] : [0, Math.floor((points.length - 1) / 3), Math.floor((points.length - 1) * 2 / 3), points.length - 1])].filter(index => index >= 0);
  const scheduleFailure = schedule?.last_run?.error;
  const scheduleFailureMessage = typeof scheduleFailure === "string" ? scheduleFailure : scheduleFailure?.message;
  return <BudgetDataSection id="forecast-revenue-trend" title="结转收入趋势" className="forecast-revenue-trend"
    description={`预计为当时预测的最终结转收入；实际为截至当时已结转的累计收入。${data ? `FY${String(data.baseline_year).slice(-2)} · ` : ""}全部业务，不受下方筛选影响。`}
    actions={<label className="forecast-trend-range">历史范围<select aria-label="历史范围" value={String(days)} onChange={event => onDays(event.target.value === "all" ? "all" : Number(event.target.value) as 30 | 90)}>
      <option value="30">近 30 天</option><option value="90">近 90 天</option><option value="all">全部历史</option>
    </select></label>}>
    <div className="forecast-trend-context">
      <div className="forecast-trend-periods" role="group" aria-label="趋势期间">{periods.map(item => <button key={item} type="button" aria-pressed={period === item} onClick={() => setPeriod(item)}>{item}</button>)}</div>
      <div className="forecast-trend-legend"><span className="is-forecast">预计结转收入</span><span className="is-actual">截至当日实际结转收入</span></div>
    </div>
    <div className="forecast-trend-refresh" role="status">
      {schedule ? <span>{schedule.enabled ? `每天 ${schedule.time} 自动更新（北京时间）` : "自动更新未启用"}{schedule.last_success_at ? ` · 最近成功 ${savedTime(schedule.last_success_at)}` : " · 尚无自动更新成功记录"}</span>
        : scheduleError ? <span>自动更新时间暂未读取，已保存预测不受影响</span> : <span>正在读取自动更新状态…</span>}
      {schedule?.last_run?.state === "FAILED" && <span className="forecast-trend-warning">最近自动更新未完成，已保留上次预测。{scheduleFailureMessage}</span>}
    </div>
    {loading && <p className="forecast-trend-message" role="status">{data ? "正在读取趋势，保留上次结果…" : "正在读取已保存的预测趋势…"}</p>}
    {error && <div className="forecast-trend-error" role="status"><span>趋势读取未完成。{data ? `仍显示${rangeLabel(shownDays)}的上次结果，其他预测内容已保留。` : "其他预测内容已保留。"} {error}</span><button type="button" disabled={loading} onClick={onRetry}>重新读取趋势</button></div>}
    {!loading && !error && !points.length && <p className="forecast-trend-message">当前范围还没有可用的历史快照；后续成功更新后将在这里积累趋势。</p>}
    {points.length > 0 && <>
      <div className="forecast-trend-chart-heading"><span>{period} · 单位：万元</span><span>{rangeLabel(shownDays)} · {points.length} 个保存日 · 同日取最后成功一版</span></div>
      {hasNumbers ? <div className="forecast-trend-plot" ref={plot}>
        <svg viewBox={`0 0 ${chartWidth} ${geometry.height}`} role="img" aria-labelledby={`${chartId}-title ${chartId}-description`} onPointerDown={event => {
          const bounds = event.currentTarget.getBoundingClientRect(), relative = (event.clientX - bounds.left) / bounds.width * chartWidth;
          const index = points.reduce((nearest, _, candidate) => Math.abs(geometry.x(candidate) - relative) < Math.abs(geometry.x(nearest) - relative) ? candidate : nearest, 0);
          selectIndex(index);
        }}>
          <title id={`${chartId}-title`}>{`${period}结转收入趋势（万元）`}</title>
          <desc id={`${chartId}-description`}>预计结转收入为深绿虚线，截至当日实际结转收入为蓝灰实线。横轴为北京时间快照保存日期，缺失数据不连线。可在下方使用日期选择滑块或展开趋势数据读取每个值。</desc>
          {geometry.ticks.map((tick, index) => <g key={index} className="forecast-trend-grid"><line x1={geometry.left} x2={geometry.right} y1={geometry.y(tick)} y2={geometry.y(tick)}/><text x={geometry.left - 10} y={geometry.y(tick) + 4} textAnchor="end">{tick.toLocaleString("zh-CN", { maximumFractionDigits: 0 })}</text></g>)}
          <path className="forecast-trend-line is-forecast" d={geometry.series("forecast_revenue")}/>
          <path className="forecast-trend-line is-actual" d={geometry.series("actual_revenue")}/>
          {points.map((point, index) => <g key={point.version_id}>{(["forecast_revenue", "actual_revenue"] as const).map(key => {
            const numeric = forecastNumber(point.periods[period]?.[key]);
            return numeric === null ? null : <circle key={key} className={`forecast-trend-dot ${key === "forecast_revenue" ? "is-forecast" : "is-actual"}`} cx={geometry.x(index)} cy={geometry.y(numeric / 10000)} r={index === selectedIndex ? 4.5 : points.length <= 31 ? 2.5 : 1.5}/>;
          })}</g>)}
          {selected && <line className="forecast-trend-cursor" x1={geometry.x(selectedIndex)} x2={geometry.x(selectedIndex)} y1={geometry.top} y2={geometry.bottom}/>}
          {dayIndices.map(index => <text className="forecast-trend-date" key={index} x={geometry.x(index)} y={geometry.dateY} textAnchor={points.length === 1 ? "middle" : index === 0 ? "start" : index === points.length - 1 ? "end" : "middle"}>{points[index].date.slice(5)}</text>)}
        </svg>
      </div> : <p className="forecast-trend-message">这些历史快照未提供{period}结转收入，不补零或编造曲线。</p>}
      {points.length === 1 && <p className="forecast-trend-hint">目前只有一个保存日，先展示真实数据点；不补造历史趋势。</p>}
      <label className="forecast-trend-date-control" htmlFor={`${chartId}-date`}><span>选择保存日</span><input id={`${chartId}-date`} type="range" min={0} max={Math.max(0, points.length - 1)} step={1} value={selectedIndex} disabled={points.length < 2}
        aria-valuetext={selected ? `${selected.date}，${versionLabel(selected.sequence)}，数据截至${selected.as_of}` : "无数据"} onChange={event => selectIndex(Number(event.target.value))}/><time>{selected?.date}</time></label>
      {selected && <div className="forecast-trend-point" aria-live="polite" aria-atomic="true">
        <div className="forecast-trend-point-values"><span>预计结转收入<strong>{amount(value?.forecast_revenue)}</strong></span><span>截至当日实际结转收入<strong>{amount(value?.actual_revenue)}</strong></span></div>
        <div className="forecast-trend-point-source"><span>{versionLabel(selected.sequence)} · {versionStatusLabel(selected.status)} · {versionUpdateLabel(null, selected.generation)}</span><span>数据截至 {selected.as_of} · 保存于 {savedTime(selected.created_at)}</span>{selected.version_id !== versionId && <button type="button" disabled={busy} onClick={() => onVersion(selected.version_id)}>查看当日完整预测</button>}</div>
        <BudgetVersionTrace code={`P${selected.sequence}`} id={selected.version_id} createdAt={selected.created_at}/>
        {value?.actual_status === "ZERO_DEFAULTED" && <p className="forecast-trend-hint">本版含未知实际按 0 计，原始缺失可在版本快照中追溯。</p>}
        {value?.actual_status === "UNAVAILABLE" && <p className="forecast-trend-hint">本版历史快照未提供实际结转收入，不按 0 补齐。</p>}
      </div>}
      {transfer && <details className="forecast-trend-data"><summary>查看趋势数据 · {period} · {points.length} 个保存日</summary>
        <ActionTable keyboardNavigation aria-label={`${period}结转收入趋势数据`} tableLabel={`${period}结转收入趋势`} exportData={transfer} exportLabel="导出趋势数据" scrollClassName="forecast-trend-table-wrap">
          <thead><tr>{visibleColumns.map(column => <th key={column.key} scope="col">{column.label}</th>)}</tr></thead>
          <tbody>{transfer.rows.map(row => <tr key={row.version_id}>{visibleColumns.map(column => <td key={column.key}>{column.key === "version" ? <button type="button" disabled={busy} onClick={() => onVersion(row.version_id)}>{row.version}</button> : row[column.key as keyof typeof row] === null ? "未提供" : typeof row[column.key as keyof typeof row] === "number" ? (row[column.key as keyof typeof row] as number).toLocaleString("zh-CN", { maximumFractionDigits: 2 }) : row[column.key as keyof typeof row]}</td>)}</tr>)}</tbody>
        </ActionTable>
      </details>}
    </>}
  </BudgetDataSection>;
}

/** Independent read-only loading: a trend failure cannot clear the page's saved calculation. */
export function ForecastRevenueTrend({ identity, versionId, onVersion, busy = false }: { identity: ApiIdentity; versionId: string; onVersion: (id: string) => void; busy?: boolean }) {
  const [days, setDays] = useState<ForecastTrendDays>(30), [retry, setRetry] = useState(0);
  const [saved, setSaved] = useState<{ version: string; days: ForecastTrendDays; data: ForecastRevenueTrendEnvelope } | null>(null);
  const [loading, setLoading] = useState(true), [error, setError] = useState("");
  const [schedule, setSchedule] = useState<ForecastSchedule | null>(null), [scheduleError, setScheduleError] = useState(false);
  useEffect(() => {
    const controller = new AbortController(); let current = true;
    setLoading(true); setError("");
    void getForecastRevenueTrend(identity, days, versionId, controller.signal).then(data => { if (current) setSaved({ version: versionId, days, data }); })
      .catch(failure => { if (current && !controller.signal.aborted) setError(failure instanceof Error ? failure.message : "请稍后重试。"); })
      .finally(() => { if (current) setLoading(false); });
    return () => { current = false; controller.abort(); };
  }, [identity, versionId, days, retry]);
  useEffect(() => {
    const controller = new AbortController(); let current = true;
    setSchedule(null); setScheduleError(false);
    void getForecastSchedule(identity, controller.signal).then(value => { if (current) setSchedule(value); })
      .catch(() => { if (current && !controller.signal.aborted) setScheduleError(true); });
    return () => { current = false; controller.abort(); };
  }, [identity, versionId, retry]);
  const visible = saved?.version === versionId ? saved : null;
  return <ForecastRevenueTrendView data={visible?.data ?? null} versionId={versionId} shownDays={visible?.days} loading={loading} error={error} days={days} schedule={schedule} scheduleError={scheduleError}
    onDays={setDays} onRetry={() => setRetry(value => value + 1)} onVersion={onVersion} busy={busy}/>;
}
