import { useEffect, useRef } from "react";
import { init, use, type EChartsCoreOption } from "echarts/core";
import { BarChart, LineChart } from "echarts/charts";
import { AriaComponent, DataZoomComponent, GridComponent, LegendComponent, TooltipComponent } from "echarts/components";
import { SVGRenderer } from "echarts/renderers";
import type { CockpitGroup, CockpitMetric } from "./cockpit";

use([BarChart, LineChart, AriaComponent, DataZoomComponent, GridComponent, LegendComponent, TooltipComponent, SVGRenderer]);
const colors: Record<CockpitMetric, string> = { budget: "#7b7971", actual: "#3f6d95", forecast: "#a65338", next_budget: "#788c5d" };
export function cockpitVisibleMetrics(metrics: CockpitMetric[], hidden: CockpitMetric[]) {
  const visible = metrics.filter(metric => !hidden.includes(metric));
  return visible.length ? visible : metrics.slice(0, 1);
}
export function toggleCockpitMetric(metrics: CockpitMetric[], hidden: CockpitMetric[], metric: CockpitMetric) {
  const visible = cockpitVisibleMetrics(metrics, hidden);
  if (!metrics.includes(metric) || visible.includes(metric) && visible.length === 1) return hidden;
  return metrics.filter(key => key === metric ? visible.includes(key) : !visible.includes(key));
}
export function CockpitLegend({ metrics, hidden, labels, label, onToggle }: {
  metrics: CockpitMetric[]; hidden: CockpitMetric[]; labels: Record<CockpitMetric, string>; label: string;
  onToggle: (metric: CockpitMetric) => void;
}) {
  const visible = cockpitVisibleMetrics(metrics, hidden);
  return <div className="cockpit-legend" role="group" aria-label={`${label}显示指标`}>
    {metrics.map(metric => { const selected = visible.includes(metric), last = selected && visible.length === 1;
      return <button key={metric} type="button" data-series={metric} aria-pressed={selected}
        disabled={last} title={last ? "至少保留一个指标显示" : selected ? "点击隐藏此指标" : "点击显示此指标"}
        onClick={() => onToggle(metric)}><i aria-hidden="true"/><span>{labels[metric]}</span></button>;
    })}<small>点击图例切换显示 · 至少保留1项</small>
  </div>;
}
export function cockpitChartOption(groups: CockpitGroup[], metrics: CockpitMetric[], labels: Record<CockpitMetric, string>, unit: "wan" | "yuan", quarters = false, narrow = false, hidden: CockpitMetric[] = []): EChartsCoreOption {
  const divisor = unit === "wan" ? 10000 : 1, values = groups.map(row => row.label);
  const visible = cockpitVisibleMetrics(metrics, hidden);
  const long = !quarters && groups.length > 8;
  const category = { type: "category", data: values, inverse: !quarters, axisTick: { show: false }, axisLine: { show: false },
    axisLabel: { color: "#65645f", fontSize: 12, formatter: (value: string) => value.length > (quarters || narrow ? 8 : 23) ? `${value.slice(0, quarters || narrow ? 7 : 22)}…` : value } };
  const numeric = { type: "value", axisLabel: { color: "#65645f", fontSize: 12 }, splitLine: { lineStyle: { color: "#e8e6dc" } } };
  return { animation: false, textStyle: { fontFamily: '"Poppins", "PingFang SC", "Microsoft YaHei", sans-serif' },
    aria: { enabled: true, label: { description: `${groups.length}个分组，当前显示${visible.map(key => labels[key]).join("、")}，单位${unit === "wan" ? "万元" : "元"}。数据表提供键盘下钻与精确数值。` } },
    legend: { show: false, selected: Object.fromEntries(metrics.map(key => [labels[key], visible.includes(key)])) },
    grid: { left: quarters ? 62 : narrow ? 95 : 190, right: long ? 40 : 18, top: 28, bottom: long ? 35 : 32, containLabel: false },
    tooltip: { trigger: "axis", renderMode: "richText", confine: true, valueFormatter: (value: unknown) => typeof value === "number" ? `${value.toLocaleString("zh-CN", { maximumFractionDigits: 2 })}${unit === "wan" ? "万元" : "元"}` : "未覆盖／待核对" },
    xAxis: quarters ? category : numeric, yAxis: quarters ? numeric : category,
    ...(long ? { dataZoom: [{ type: "inside", yAxisIndex: 0, startValue: 0, endValue: 7, zoomOnMouseWheel: false, moveOnMouseWheel: true }, { type: "slider", yAxisIndex: 0, startValue: 0, endValue: 7, width: 14, right: 4, showDetail: false }] } : {}),
    series: metrics.map(key => ({ name: labels[key], type: quarters && key === "forecast" ? "line" : "bar", symbol: "circle", symbolSize: 7,
      barMaxWidth: quarters ? 22 : 13, itemStyle: { color: colors[key] }, lineStyle: { color: colors[key], width: 2, type: key === "forecast" ? "dashed" : "solid" },
      emphasis: { focus: "series" }, data: groups.map(row => row[key] === null ? null : row[key]! / divisor) })) };
}

export function CockpitChart({ groups, metrics, labels, unit, onDrill, quarters = false, hidden = [] }: {
  groups: CockpitGroup[]; metrics: CockpitMetric[]; labels: Record<CockpitMetric, string>; unit: "wan" | "yuan";
  onDrill: (group: CockpitGroup) => void; quarters?: boolean; hidden?: CockpitMetric[];
}) {
  const element = useRef<HTMLDivElement>(null), callback = useRef(onDrill); callback.current = onDrill;
  const chartRef = useRef<ReturnType<typeof init> | null>(null), hiddenRef = useRef(hidden); hiddenRef.current = hidden;
  useEffect(() => {
    if (!element.current || !groups.length) return;
    const chart = init(element.current, undefined, { renderer: "svg" });
    chartRef.current = chart;
    const option = () => cockpitChartOption(groups, metrics, labels, unit, quarters, element.current!.clientWidth < 600, hiddenRef.current);
    chart.setOption(option());
    chart.on("click", (event: { dataIndex?: number }) => { if (typeof event.dataIndex === "number" && groups[event.dataIndex]) callback.current(groups[event.dataIndex]); });
    const resize = () => { chart.resize(); chart.setOption(option()); };
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(resize);
    observer?.observe(element.current); window.addEventListener("resize", resize);
    return () => { observer?.disconnect(); window.removeEventListener("resize", resize); chart.dispose(); chartRef.current = null; };
  }, [groups, metrics.join(), JSON.stringify(labels), unit, quarters]);
  useEffect(() => {
    // A selection-only update preserves the current chart zoom/scroll position.
    const { legend, aria } = cockpitChartOption(groups, metrics, labels, unit, quarters, false, hidden);
    chartRef.current?.setOption({ legend, aria });
  }, [hidden.join(), metrics.join(), JSON.stringify(labels)]);
  return <div ref={element} className={`cockpit-chart${quarters ? " cockpit-chart--quarters" : ""}`} style={{ height: quarters ? 290 : Math.max(260, Math.min(groups.length, 8) * 66 + 64) }}
    role="img" aria-label={`${quarters ? "季度分布" : "维度对比"}，当前显示${cockpitVisibleMetrics(metrics, hidden).map(key => labels[key]).join("、")}，${groups.length}个分组，单位${unit === "wan" ? "万元" : "元"}；可切换数据表使用键盘下钻`}/>;
}
