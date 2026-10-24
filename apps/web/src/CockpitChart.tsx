import { useEffect, useRef } from "react";
import { init, use, type EChartsCoreOption } from "echarts/core";
import { BarChart, LineChart } from "echarts/charts";
import { AriaComponent, DataZoomComponent, GridComponent, LegendComponent, TooltipComponent } from "echarts/components";
import { SVGRenderer } from "echarts/renderers";
import type { CockpitGroup, CockpitMetric } from "./cockpit";

use([BarChart, LineChart, AriaComponent, DataZoomComponent, GridComponent, LegendComponent, TooltipComponent, SVGRenderer]);
const colors: Record<CockpitMetric, string> = { budget: "#7b7971", actual: "#3f6d95", forecast: "#a65338", next_budget: "#788c5d" };
export function cockpitChartOption(groups: CockpitGroup[], metrics: CockpitMetric[], labels: Record<CockpitMetric, string>, unit: "wan" | "yuan", quarters = false, narrow = false): EChartsCoreOption {
  const divisor = unit === "wan" ? 10000 : 1, values = groups.map(row => row.label);
  const long = !quarters && groups.length > 8;
  const category = { type: "category", data: values, inverse: !quarters, axisTick: { show: false }, axisLine: { show: false },
    axisLabel: { color: "#65645f", fontSize: 12, formatter: (value: string) => value.length > (quarters || narrow ? 8 : 23) ? `${value.slice(0, quarters || narrow ? 7 : 22)}…` : value } };
  const numeric = { type: "value", axisLabel: { color: "#65645f", fontSize: 12 }, splitLine: { lineStyle: { color: "#e8e6dc" } } };
  return { animation: false, textStyle: { fontFamily: '"Poppins", "PingFang SC", "Microsoft YaHei", sans-serif' },
    aria: { enabled: true, label: { description: `${groups.length}个分组，${metrics.map(key => labels[key]).join("、")}，单位${unit === "wan" ? "万元" : "元"}。数据表提供键盘下钻与精确数值。` } },
    grid: { left: quarters ? 62 : narrow ? 95 : 190, right: long ? 40 : 18, top: 28, bottom: long ? 35 : 32, containLabel: false },
    tooltip: { trigger: "axis", renderMode: "richText", confine: true, valueFormatter: (value: unknown) => typeof value === "number" ? `${value.toLocaleString("zh-CN", { maximumFractionDigits: 2 })}${unit === "wan" ? "万元" : "元"}` : "未覆盖／待核对" },
    xAxis: quarters ? category : numeric, yAxis: quarters ? numeric : category,
    ...(long ? { dataZoom: [{ type: "inside", yAxisIndex: 0, startValue: 0, endValue: 7, zoomOnMouseWheel: false, moveOnMouseWheel: true }, { type: "slider", yAxisIndex: 0, startValue: 0, endValue: 7, width: 14, right: 4, showDetail: false }] } : {}),
    series: metrics.map(key => ({ name: labels[key], type: quarters && key === "forecast" ? "line" : "bar", symbol: "circle", symbolSize: 7,
      barMaxWidth: quarters ? 22 : 13, itemStyle: { color: colors[key] }, lineStyle: { color: colors[key], width: 2, type: key === "forecast" ? "dashed" : "solid" },
      emphasis: { focus: "series" }, data: groups.map(row => row[key] === null ? null : row[key]! / divisor) })) };
}

export function CockpitChart({ groups, metrics, labels, unit, onDrill, quarters = false }: {
  groups: CockpitGroup[]; metrics: CockpitMetric[]; labels: Record<CockpitMetric, string>; unit: "wan" | "yuan";
  onDrill: (group: CockpitGroup) => void; quarters?: boolean;
}) {
  const element = useRef<HTMLDivElement>(null), callback = useRef(onDrill); callback.current = onDrill;
  useEffect(() => {
    if (!element.current || !groups.length) return;
    const chart = init(element.current, undefined, { renderer: "svg" });
    const option = () => cockpitChartOption(groups, metrics, labels, unit, quarters, element.current!.clientWidth < 600);
    chart.setOption(option());
    chart.on("click", (event: { dataIndex?: number }) => { if (typeof event.dataIndex === "number" && groups[event.dataIndex]) callback.current(groups[event.dataIndex]); });
    const resize = () => { chart.resize(); chart.setOption(option()); };
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(resize);
    observer?.observe(element.current); window.addEventListener("resize", resize);
    return () => { observer?.disconnect(); window.removeEventListener("resize", resize); chart.dispose(); };
  }, [groups, metrics.join(), JSON.stringify(labels), unit, quarters]);
  return <div ref={element} className={`cockpit-chart${quarters ? " cockpit-chart--quarters" : ""}`} style={{ height: quarters ? 290 : Math.max(260, Math.min(groups.length, 8) * 66 + 64) }}
    role="img" aria-label={`${quarters ? "季度分布" : "维度对比"}，${groups.length}个分组，单位${unit === "wan" ? "万元" : "元"}；可切换数据表使用键盘下钻`}/>;
}
