import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { build } from "esbuild";
import * as echarts from "echarts";

const require = createRequire(import.meta.url), compiled = await build({
  entryPoints: [fileURLToPath(new URL("../src/CockpitChart.tsx", import.meta.url))], bundle: true,
  write: false, platform: "node", format: "cjs", jsx: "automatic", external: ["react", "react-dom"], logLevel: "silent",
});
const module = { exports: {} };
new Function("require", "module", "exports", compiled.outputFiles[0].text)(require, module, module.exports);
const { CockpitLegend, cockpitChartOption, cockpitVisibleMetrics, toggleCockpitMetric } = module.exports;
const metrics = ["budget", "actual", "forecast"];
const labels = { budget: "FY27定版目标", actual: "截至当日实际", forecast: "FY27全年预计", next_budget: "FY28预算草稿" };
const group = (index = 0) => ({ key: String(index), label: `分组${index}`, path: { campus: `校区${index}` }, budget: 100000, actual: 40000, forecast: 90000, next_budget: 120000 });

test("legend selection hides, restores and never turns the last visible metric off", () => {
  let hidden = toggleCockpitMetric(metrics, [], "budget");
  assert.deepEqual(hidden, ["budget"]);
  hidden = toggleCockpitMetric(metrics, hidden, "actual");
  assert.deepEqual(cockpitVisibleMetrics(metrics, hidden), ["forecast"]);
  assert.deepEqual(toggleCockpitMetric(metrics, hidden, "forecast"), hidden);
  assert.deepEqual(toggleCockpitMetric(metrics, hidden, "next_budget"), hidden);
  hidden = toggleCockpitMetric(metrics, hidden, "budget");
  assert.deepEqual(cockpitVisibleMetrics(metrics, hidden), ["budget", "forecast"]);
  assert.deepEqual(cockpitVisibleMetrics(metrics, metrics), ["budget"], "defensive empty selection still has one visible metric");
});

test("legend controls expose selection, keyboard semantics and last-series protection", () => {
  const html = renderToStaticMarkup(createElement(CockpitLegend, { metrics, hidden: ["budget", "actual"], labels, label: "经营全景", onToggle() {} }));
  assert.match(html, /role="group" aria-label="经营全景显示指标"/);
  assert.match(html, /data-series="budget" aria-pressed="false"/);
  assert.match(html, /data-series="forecast" aria-pressed="true" disabled=""/);
  assert.match(html, /至少保留一个指标显示/); assert.match(html, /type="button"/);
  const css = fs.readFileSync(new URL("../src/cockpit.css", import.meta.url), "utf8");
  assert.match(css, /\.cockpit-legend button:focus-visible/);
  assert.match(css, /aria-pressed="false"\] span \{ text-decoration: line-through/);
  assert.match(css, /\.cockpit-shell \.cockpit-legend button:disabled \{ opacity: 1/);
});

test("actual ECharts rendering hides bars and quarter forecast lines without rewriting facts or zoom", () => {
  const groups = Array.from({ length: 12 }, (_, index) => group(index)), before = JSON.stringify(groups);
  const chart = echarts.init(null, undefined, { renderer: "svg", ssr: true, width: 900, height: 600 });
  try {
    chart.setOption(cockpitChartOption(groups, metrics, labels, "wan"));
    assert.match(chart.renderToSVGString(), /fill="#3f6d95"/);
    chart.dispatchAction({ type: "dataZoom", startValue: 3, endValue: 10 });
    const zoom = chart.getOption().dataZoom[0].startValue;
    const hidden = cockpitChartOption(groups, metrics, labels, "wan", false, false, ["actual"]);
    chart.setOption({ legend: hidden.legend, aria: hidden.aria });
    assert.doesNotMatch(chart.renderToSVGString(), /fill="#3f6d95"/);
    assert.match(chart.renderToSVGString(), /fill="#a65338"/);
    assert.equal(chart.getOption().dataZoom[0].startValue, zoom);
    assert.deepEqual(hidden.series.map(series => series.data), metrics.map(metric => groups.map(row => row[metric] / 10000)));
    chart.clear();
    chart.setOption(cockpitChartOption([group()], metrics, labels, "yuan", true));
    assert.match(chart.renderToSVGString(), /stroke="#a65338"/);
    const quarter = cockpitChartOption([group()], metrics, labels, "yuan", true, false, ["forecast"]);
    chart.setOption({ legend: quarter.legend, aria: quarter.aria });
    assert.doesNotMatch(chart.renderToSVGString(), /stroke="#a65338"/);
    assert.match(chart.renderToSVGString(), /fill="#3f6d95"/);
    assert.equal(JSON.stringify(groups), before);
  } finally { chart.dispose(); }
});
