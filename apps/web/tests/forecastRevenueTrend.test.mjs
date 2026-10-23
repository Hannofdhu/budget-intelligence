import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { budgetWorkbook } from "../src/budgetExport.ts";
import { tableCopyRows } from "../src/tableTransfer.ts";
import { getForecastRevenueTrend, getForecastSchedule } from "../src/forecastApi.ts";

const require = createRequire(import.meta.url);
const compiled = await build({ entryPoints: [fileURLToPath(new URL("../src/ForecastRevenueTrend.tsx", import.meta.url))],
  bundle: true, write: false, platform: "node", format: "cjs", jsx: "automatic", external: ["react", "react-dom", "exceljs"], loader: { ".css": "empty" }, logLevel: "silent" });
function load(react) {
  const module = { exports: {} };
  new Function("require", "module", "exports", compiled.outputFiles[0].text)(name => name === "react" && react ? react : require(name), module, module.exports);
  return module.exports;
}
const { ForecastRevenueTrendView, forecastTrendGeometry, forecastTrendTransfer, forecastTrendDifference, forecastTrendComparison } = load();
const periods = ["全年", "暑假", "秋季", "寒假", "春季"];
function point(date, sequence, overrides = {}) {
  return { date, sequence, version_id: `version-${sequence}`, status: "DRAFT", created_at: `${date}T01:30:00Z`, as_of: "2026-10-01", generation: "MANUAL",
    periods: Object.fromEntries(periods.map((period, index) => [period, { forecast_revenue: String(1000000 + index * 100000 + sequence), actual_revenue: String(300000 + sequence), actual_status: "AVAILABLE" }])), ...overrides };
}
const data = () => ({ baseline_year: 2027, timezone: "Asia/Shanghai", points: [point("2026-10-01", 1), point("2026-10-05", 2), point("2026-10-16", 3)] });
const defaults = () => ({ data: data(), versionId: "version-3", loading: false, error: "", days: 30, schedule: { enabled: true, time: "09:30", timezone: "Asia/Shanghai", last_success_at: "2026-10-16T01:30:00Z", last_run: null, next_run_at: "2026-10-17T01:30:00Z", catch_up: true }, onDays() {}, onRetry() {}, onVersion() {} });
const text = html => html.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim();
const render = props => renderToStaticMarkup(createElement(ForecastRevenueTrendView, { ...defaults(), ...props }));
function descendants(node, predicate, output = []) {
  if (Array.isArray(node)) { node.forEach(child => descendants(child, predicate, output)); return output; }
  if (!node || typeof node !== "object") return output;
  if (predicate(node)) output.push(node);
  descendants(node.props?.children, predicate, output); descendants(node.props?.actions, predicate, output); return output;
}
function nodeText(node) { if (Array.isArray(node)) return node.map(nodeText).join(""); if (node && typeof node === "object") return nodeText(node.props?.children); return node == null ? "" : String(node); }
function harness(props = {}) {
  const states = []; let cursor = 0;
  const react = { useState(initial) { const index = cursor++; if (!(index in states)) states[index] = typeof initial === "function" ? initial() : initial; return [states[index], value => { states[index] = typeof value === "function" ? value(states[index]) : value; }]; },
    useId() { return "trend-test"; }, useMemo(fn) { return fn(); }, useEffect() {}, useRef(value) { return { current: value }; } };
  const { ForecastRevenueTrendView: View } = load(react), values = { ...defaults(), ...props };
  return { render() { cursor = 0; return View(values); }, values };
}

test("trend reuses the current data section with explicit units, range, series definitions and daily schedule", () => {
  const html = render({});
  assert.match(html, /结转收入趋势/); assert.match(html, /FY27/); assert.match(html, /不受下方筛选影响/);
  assert.match(html, /预计为当时预测的最终结转收入/); assert.match(html, /实际为截至当时已结转的累计收入/);
  assert.match(html, /每天 09:30 自动更新（北京时间）/); assert.match(html, /最近成功 2026-10-16 09:30/);
  assert.match(html, /全年 · 单位：万元/); assert.match(html, /同日取最后成功一版/);
  assert.match(html, /<select aria-label="历史范围"/);
  for (const period of periods) assert.match(html, new RegExp(`aria-pressed="${period === "全年" ? "true" : "false"}">${period}`));
  assert.match(html, /class="forecast-trend-line is-forecast"/); assert.match(html, /class="forecast-trend-line is-actual"/);
  assert.match(html, /type="range"/); assert.match(html, /aria-valuetext="2026-10-16，第3版，数据截至2026-10-01"/);
  assert.match(html, /数据截至 2026-10-01/); assert.match(html, /保存于 2026-10-16 09:30/);
  assert.match(html, /aria-label="趋势显示方式"/); assert.match(html, /aria-pressed="true">看变化/);
  assert.match(html, /较 2026-10-01 保存值增减/); assert.match(html, /各自保存值为基准/);
});

test("change geometry uses the first saved day of the current window and each series' own baseline", () => {
  const fixture = data();
  fixture.points[0].periods["全年"] = { forecast_revenue: "107244100", actual_revenue: "71799900", actual_status: "AVAILABLE" };
  fixture.points[1].periods["全年"] = { forecast_revenue: "107142900", actual_revenue: "71672000", actual_status: "AVAILABLE" };
  fixture.points[2].periods["全年"] = { forecast_revenue: "107300000", actual_revenue: "72000000", actual_status: "AVAILABLE" };
  const source = JSON.stringify(fixture), geometry = forecastTrendGeometry(fixture.points, "全年", 900, "change");
  assert.equal(geometry.valueAt(0, "forecast_revenue"), 0); assert.equal(geometry.valueAt(0, "actual_revenue"), 0);
  assert.equal(geometry.valueAt(1, "forecast_revenue"), -10.12); assert.equal(geometry.valueAt(1, "actual_revenue"), -12.79);
  assert.ok(geometry.min < -12.79); assert.ok(geometry.max > 20.01);
  const total = forecastTrendGeometry(fixture.points, "全年", 900, "total");
  assert.equal(total.min, 0); assert.equal(total.valueAt(1, "forecast_revenue"), 10714.29);
  const narrowed = forecastTrendGeometry(fixture.points.slice(1), "全年", 900, "change");
  assert.equal(narrowed.valueAt(0, "forecast_revenue"), 0); assert.equal(narrowed.valueAt(1, "forecast_revenue"), 15.71);
  assert.equal(JSON.stringify(fixture), source);
});

test("change mode never invents a missing baseline, interpolate gaps, or substitute a later actual", () => {
  const fixture = data(); fixture.points[0].periods["全年"].actual_revenue = null;
  fixture.points[1].periods["全年"].forecast_revenue = null;
  const geometry = forecastTrendGeometry(fixture.points, "全年", 240, "change");
  assert.equal(geometry.series("actual_revenue"), ""); assert.equal(geometry.valueAt(2, "actual_revenue"), null);
  assert.equal((geometry.series("forecast_revenue").match(/M/g) ?? []).length, 2);
  assert.doesNotMatch(geometry.series("forecast_revenue"), /NaN|Infinity/); assert.ok(geometry.x(2) <= 240);
  assert.match(render({ data: fixture }), /首个保存日未提供实际结转收入/);
  fixture.points[0].periods["全年"].forecast_revenue = null;
  const app = harness({ data: fixture }); let tree = app.render();
  assert.equal(descendants(tree, node => node.type === "svg").length, 0);
  assert.match(nodeText(tree), /首个保存日缺少基准值/);
  descendants(tree, node => node.type === "button" && nodeText(node) === "看总量")[0].props.onClick(); tree = app.render();
  assert.equal(descendants(tree, node => node.type === "svg").length, 1, "available later observations remain reachable in total mode");
});

test("single and unchanged change-mode observations stay on zero with a non-degenerate axis", () => {
  const fixture = data(); fixture.points = fixture.points.map(item => ({ ...item, periods: fixture.points[0].periods }));
  for (const points of [fixture.points, fixture.points.slice(0, 1)]) {
    const geometry = forecastTrendGeometry(points, "全年", 240, "change");
    assert.equal(geometry.min, -1); assert.equal(geometry.max, 1);
    assert.equal(geometry.y(0), (geometry.top + geometry.bottom) / 2);
    assert.doesNotMatch(geometry.series("actual_revenue"), /NaN|Infinity/);
    points.forEach((_, index) => assert.equal(geometry.valueAt(index, "actual_revenue"), 0));
  }
});

test("display switching is local, preserves exact selection and never starts another read or save", () => {
  const callbacks = [], fixture = data(), before = JSON.stringify(fixture);
  const app = harness({ data: fixture, onVersion: () => callbacks.push("version"), onDays: () => callbacks.push("days"), onRetry: () => callbacks.push("retry") });
  let tree = app.render(); descendants(tree, node => node.type === "input" && node.props.type === "range")[0].props.onChange({ target: { value: "1" } });
  tree = app.render(); descendants(tree, node => node.type === "button" && nodeText(node) === "看总量")[0].props.onClick(); tree = app.render();
  assert.equal(descendants(tree, node => node.type === "button" && nodeText(node) === "看总量")[0].props["aria-pressed"], true);
  assert.match(nodeText(tree), /绝对金额，纵轴含 0/);
  assert.equal(descendants(tree, node => node.type === "input" && node.props.type === "range")[0].props.value, 1);
  descendants(tree, node => node.type === "button" && nodeText(node) === "看变化")[0].props.onClick(); tree = app.render();
  assert.match(nodeText(tree), /较 2026-10-01 保存值增减/); assert.deepEqual(callbacks, []);
  assert.equal(descendants(tree, node => node.type === "svg").length, 1); assert.equal(JSON.stringify(fixture), before);
});

test("selected amounts compare to the ledger's direct previous version, including an earlier same-day version", () => {
  const fixture = data(), selected = fixture.points[2];
  selected.sequence = 8; selected.version_id = "version-8";
  selected.periods["全年"] = { forecast_revenue: "107142900.001", actual_revenue: "71672000", actual_status: "AVAILABLE" };
  selected.previous_version = { version_id: "version-7-same-day", sequence: 7, created_at: "2026-10-16T01:00:00Z", as_of: "2026-10-14", periods: { ...selected.periods, "全年": { forecast_revenue: "107244100.004", actual_revenue: "71672000", actual_status: "AVAILABLE" } } };
  assert.equal(forecastTrendDifference("107244100.004", "107142900.001"), 10.1200003);
  assert.equal(forecastTrendComparison(selected, "全年", "forecast_revenue").delta, -10.1200003);
  assert.equal(forecastTrendComparison(selected, "全年", "forecast_revenue").text, "比上版（第7版） 减少 10.12 万元");
  assert.equal(forecastTrendComparison(selected, "全年", "actual_revenue").text, "比上版（第7版） 持平 0 万元");
  const html = render({ data: fixture, versionId: "version-8" }); assert.match(html, /比上版（第7版） 减少 10.12 万元/); assert.doesNotMatch(html, /比上版（第2版）/);
  const exported = forecastTrendTransfer(fixture, "全年");
  assert.equal(exported.rows[2].previous_version_id, "version-7-same-day"); assert.equal(exported.rows[2].forecast_change, -10.1200003);
  const tiny = { ...selected, periods: { ...selected.periods, "全年": { forecast_revenue: "107244100.005", actual_revenue: "71672000", actual_status: "AVAILABLE" } } };
  assert.match(forecastTrendComparison(tiny, "全年", "forecast_revenue").text, /增加 不足 0.01 万元/);
});

test("first, omitted, incomplete and missing previous values stay distinct without daily-point fallback", () => {
  const fixture = data(), selected = fixture.points[2]; selected.previous_version = null;
  assert.match(forecastTrendComparison(selected, "全年", "forecast_revenue").text, /上版信息未提供/);
  assert.equal(forecastTrendComparison({ ...selected, sequence: 1 }, "全年", "forecast_revenue").text, "首版，无上版");
  delete selected.previous_version;
  assert.match(forecastTrendComparison(selected, "全年", "forecast_revenue").text, /上版信息未提供/);
  selected.previous_version = { version_id: "missing-periods", sequence: 2, created_at: "2026-10-16T01:00:00Z", as_of: "2026-10-01", periods: null };
  assert.equal(forecastTrendComparison(selected, "全年", "forecast_revenue").delta, null);
  assert.match(forecastTrendComparison(selected, "全年", "forecast_revenue").text, /数据未完整/);
  selected.previous_version.periods = { ...selected.periods, "全年": { ...selected.periods["全年"], actual_revenue: null } };
  assert.equal(forecastTrendComparison(selected, "全年", "forecast_revenue").delta, 0);
  assert.equal(forecastTrendComparison(selected, "全年", "actual_revenue").delta, null);
  assert.equal(forecastTrendDifference("0", null), null); assert.equal(forecastTrendDifference("-12.50", "-13.00"), .00005);
});

test("geometry preserves real date distances and signed values rather than evenly spacing absent days", () => {
  const fixture = data(), original = JSON.stringify(fixture), geometry = forecastTrendGeometry(fixture.points, "全年");
  assert.equal(geometry.height, 230); assert.equal(geometry.bottom, 192); assert.equal(geometry.dateY, 220);
  assert.equal(geometry.x(0), geometry.left); assert.equal(geometry.x(2), geometry.right);
  assert.ok(Math.abs((geometry.x(1) - geometry.left) / (geometry.right - geometry.left) - 4 / 15) < 1e-12);
  fixture.points[0].periods["全年"].actual_revenue = "-123456.789";
  const signed = forecastTrendGeometry(fixture.points, "全年");
  assert.ok(signed.min < -12.3456789); assert.ok(signed.max > 100); assert.ok(signed.y(-12.3456789) > signed.y(0));
  assert.equal(JSON.stringify(data()), original);
});

test("missing actuals break the path and do not become zero, interpolate, or erase known negative facts", () => {
  const fixture = data(); fixture.points[1].periods["全年"] = { forecast_revenue: null, actual_revenue: null, actual_status: "UNAVAILABLE" };
  fixture.points[0].periods["全年"].actual_revenue = "-50000";
  const before = JSON.stringify(fixture), geometry = forecastTrendGeometry(fixture.points, "全年");
  assert.equal((geometry.series("actual_revenue").match(/M/g) ?? []).length, 2);
  assert.equal((geometry.series("actual_revenue").match(/L/g) ?? []).length, 0);
  assert.equal((geometry.series("forecast_revenue").match(/M/g) ?? []).length, 2);
  const transfer = forecastTrendTransfer(fixture, "全年"); assert.equal(transfer.rows[1].actual_revenue, null); assert.equal(transfer.rows[0].actual_revenue, -5);
  assert.match(transfer.rows[1].actual_status, /未提供实际/); assert.equal(JSON.stringify(fixture), before);
});

test("single day shows a true observation without manufacturing prior history or a multi-day line", () => {
  const fixture = { ...data(), points: [point("2026-10-16", 3)] }, geometry = forecastTrendGeometry(fixture.points, "全年");
  assert.equal(geometry.x(0), (geometry.left + geometry.right) / 2); assert.match(geometry.series("forecast_revenue"), /^M[^L]*$/);
  const html = render({ data: fixture }); assert.match(html, /目前只有一个保存日/); assert.match(html, /不补造历史趋势/);
  assert.match(html, /type="range"[^>]*disabled=""/);
});

test("all missing history is clearly unavailable, while numeric zero is a real point", () => {
  const fixture = data(); for (const point of fixture.points) point.periods["全年"] = { forecast_revenue: null, actual_revenue: null, actual_status: "UNAVAILABLE" };
  let html = render({ data: fixture }); assert.doesNotMatch(html, /<svg/); assert.match(html, /不补零或编造曲线/); assert.match(html, /未提供/);
  fixture.points[0].periods["全年"].actual_revenue = "0";
  html = render({ data: fixture }); assert.match(html, /<svg/); assert.match(forecastTrendGeometry(fixture.points, "全年").series("actual_revenue"), /^M/);
});

test("controls switch periods without changing values, allow native keyboard date selection and open exact versions", () => {
  const versions = [], changes = [], fixture = data(), before = JSON.stringify(fixture);
  const app = harness({ data: fixture, onVersion: id => versions.push(id), onDays: days => changes.push(days) });
  let tree = app.render();
  descendants(tree, node => node.type === "button" && nodeText(node) === "秋季")[0].props.onClick(); tree = app.render();
  assert.equal(descendants(tree, node => node.type === "button" && nodeText(node) === "秋季")[0].props["aria-pressed"], true);
  let slider = descendants(tree, node => node.type === "input" && node.props.type === "range")[0];
  slider.props.onChange({ target: { value: "0" } }); tree = app.render();
  slider = descendants(tree, node => node.type === "input" && node.props.type === "range")[0]; assert.equal(slider.props.value, 0);
  descendants(tree, node => node.type === "button" && nodeText(node) === "查看当日完整预测")[0].props.onClick(); assert.deepEqual(versions, ["version-1"]);
  const range = descendants(tree, node => node.type === "select")[0]; range.props.onChange({ target: { value: "90" } }); range.props.onChange({ target: { value: "all" } }); assert.deepEqual(changes, [90, "all"]);
  assert.equal(JSON.stringify(fixture), before);
});

test("pointer selection finds the nearest real date position and selected metadata matches that exact snapshot", () => {
  const app = harness(), geometry = forecastTrendGeometry(data().points, "全年"), tree = app.render();
  const svg = descendants(tree, node => node.type === "svg")[0];
  svg.props.onPointerDown({ clientX: geometry.x(1) + 20, currentTarget: { getBoundingClientRect: () => ({ left: 20, width: 900 }) } });
  const next = app.render();
  assert.equal(descendants(next, node => node.type === "input" && node.props.type === "range")[0].props.value, 1);
  const versionButton = descendants(next, node => node.type === "button" && nodeText(node) === "查看当日完整预测");
  assert.equal(versionButton.length, 1);
});

test("full forecast entry is hidden for the displayed version and only appears for a different saved day", () => {
  const versions = [], app = harness({ onVersion: id => versions.push(id) });
  const entry = tree => descendants(tree, node => node.type === "button" && nodeText(node) === "查看当日完整预测");
  let tree = app.render(); assert.equal(entry(tree).length, 0, "current version has no redundant navigation");
  descendants(tree, node => node.type === "input" && node.props.type === "range")[0].props.onChange({ target: { value: "0" } });
  tree = app.render(); assert.equal(entry(tree).length, 1); entry(tree)[0].props.onClick(); assert.deepEqual(versions, ["version-1"]);
  app.values.busy = true; assert.equal(entry(app.render())[0].props.disabled, true, "keep existing unsaved/busy protection");
  app.values.busy = false; app.values.versionId = "version-1";
  app.values.data = { ...data(), points: [point("2026-10-01", 1)] };
  assert.equal(entry(app.render()).length, 0, "already opened historical snapshot has no duplicate entry");
  app.values.versionId = "version-3"; app.values.data = data(); tree = app.render();
  descendants(tree, node => node.type === "input" && node.props.type === "range")[0].props.onChange({ target: { value: "2" } });
  assert.equal(entry(app.render()).length, 0, "returning slider to current version hides entry again");
  const html = render({}); assert.doesNotMatch(html, /查看 P\d+ 版本/);
  assert.match(html, />第1版<\/button>/, "the existing detailed trend table retains its version links");
});

test("empty, loading and failed reads never show invented zeros or claim data loss; retry is local", () => {
  let html = render({ data: null, loading: true }); assert.match(html, /正在读取已保存的预测趋势/); assert.doesNotMatch(html, /<svg/);
  html = render({ data: null }); assert.match(html, /还没有可用的历史快照/); assert.doesNotMatch(html, /<table/);
  html = render({ error: "连接超时", days: 90, shownDays: 30 }); assert.match(html, /仍显示近 30 天的上次结果/); assert.match(html, /其他预测内容已保留/); assert.match(html, /连接超时/); assert.match(html, /<svg/);
  let retries = 0; const app = harness({ error: "读取失败", onRetry: () => retries++ });
  descendants(app.render(), node => node.type === "button" && nodeText(node) === "重新读取趋势")[0].props.onClick(); assert.equal(retries, 1);
});

test("schedule failure uses actual backend state and error, without fabricating success or refreshing", () => {
  const config = defaults().schedule;
  const html = render({ schedule: { ...config, last_run: { state: "FAILED", finished_at: "2026-10-16T01:30:00Z", error: { code: "UPSTREAM_TIMEOUT", message: "来源读取超时", stage: "SOURCE_QUERY", diagnostic_id: "diagnostic-1", exception_type: "TimeoutError" } } } });
  assert.match(html, /自动更新失败，已保留上次预测/); assert.equal((html.match(/已保留上次预测/g) ?? []).length, 1);
  assert.match(html, /<details class="forecast-trend-failure"><summary>查看原因/); assert.match(html, /来源读取超时/);
  assert.match(html, /SOURCE_QUERY/); assert.match(html, /diagnostic-1/); assert.match(html, /TimeoutError/); assert.match(html, /错误代码/);
  assert.doesNotMatch(html, /重试自动更新|重新执行/);
  assert.match(render({ schedule: { ...config, last_run: { state: "RUNNING" } } }), /正在自动更新，当前显示上次预测/);
  assert.match(render({ schedule: null, scheduleError: true }), /自动更新时间暂未读取，已保存预测不受影响/);
  assert.match(render({ schedule: { ...config, last_success_at: null } }), /尚无自动更新成功记录/);
  assert.match(render({ schedule: { ...config, enabled: false } }), /自动更新未启用/);
});

test("collapsed trend data has copy and typed export for exactly the same period, days and full source metadata", async () => {
  const fixture = data(), original = JSON.stringify(fixture), transfer = forecastTrendTransfer(fixture, "秋季"), html = render({});
  assert.match(html, /<details class="forecast-trend-data"><summary>/); assert.match(html, /一键复制/); assert.match(html, /导出趋势数据/);
  assert.equal(transfer.rows.length, 3); assert.equal(transfer.rows[0].period, "秋季"); assert.equal(transfer.rows[0].forecast_revenue, 120.0001);
  assert.equal(transfer.rows[0].version_id, "version-1"); assert.equal(transfer.rows[0].as_of, "2026-10-01"); assert.equal(transfer.rows[0].created_at, "2026-10-01 09:30");
  const copied = tableCopyRows(transfer.columns, transfer.rows); assert.equal(copied.length, 4); assert.equal(copied[0].length, 17); assert.equal(copied[1][2], "120.0001");
  const workbook = await budgetWorkbook(transfer.columns, transfer.rows, "趋势数据");
  assert.equal(workbook.worksheets[0].rowCount, 4); assert.equal(workbook.worksheets[0].columnCount, 17);
  assert.equal(workbook.worksheets[0].getCell("C2").value, 120.0001); assert.equal(workbook.worksheets[0].getCell("D2").value, 30.0001);
  assert.equal(JSON.stringify(fixture), original);
});

test("queued retries remain visible as unfinished while first queueing and active retries have distinct states", () => {
  const config = defaults().schedule, lastError = { code: "SOURCE_TIMEOUT", message: "来源读取超时", diagnostic_id: "retry-diagnostic" };
  let html = render({ schedule: { ...config, last_run: { state: "QUEUED", error: lastError } } });
  assert.match(html, /本次自动更新未完成，等待重试，当前保留上次预测/);
  assert.match(html, /forecast-trend-warning/); assert.match(html, /<details class="forecast-trend-failure"><summary>查看原因/); assert.match(html, /retry-diagnostic/);
  assert.doesNotMatch(html, /自动更新失败，已保留/);
  html = render({ schedule: { ...config, last_run: { state: "QUEUED", error: null } } });
  assert.match(html, /等待自动更新/); assert.doesNotMatch(html, /forecast-trend-failure|forecast-trend-warning|未完成，等待重试/);
  html = render({ schedule: { ...config, last_run: { state: "RUNNING", error: lastError } } });
  assert.match(html, /正在重试，保留上次预测/); assert.doesNotMatch(html, /forecast-trend-failure|forecast-trend-warning|自动更新失败|等待重试/);
  html = render({ schedule: { ...config, last_run: { state: "RUNNING", error: null } } });
  assert.match(html, /正在自动更新，当前显示上次预测/); assert.doesNotMatch(html, /正在重试/);
});

test("trend and schedule requests are authenticated GETs, encode exact version, pass cancellation, and never call refresh", async () => {
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, "fetch"), calls = [], controller = new AbortController();
  Object.defineProperty(globalThis, "fetch", { configurable: true, value: async (url, options) => { calls.push({ url, options }); return new Response(JSON.stringify({ points: [] }), { headers: { "Content-Type": "application/json" } }); } });
  try {
    const identity = { userId: "owner", departmentId: "dept", roles: ["DEPARTMENT_BUDGET_OWNER"] };
    await getForecastRevenueTrend(identity, 30, "version/a ?", controller.signal); await getForecastSchedule(identity, controller.signal);
    assert.equal(calls.length, 2); assert.match(calls[0].url, /\/forecasts\/fy27\/trend\?/);
    const search = new URL(calls[0].url, "http://localhost").searchParams; assert.equal(search.get("days"), "30"); assert.equal(search.get("through_version"), "version/a ?");
    assert.match(calls[1].url, /\/forecasts\/fy27\/schedule$/);
    for (const call of calls) { assert.equal(call.options.method, undefined); assert.equal(call.options.signal, controller.signal); assert.equal(call.options.headers["X-User-ID"], "owner"); assert.equal(call.options.body, undefined); }
  } finally { if (descriptor) Object.defineProperty(globalThis, "fetch", descriptor); else delete globalThis.fetch; }
});

test("independent loader cancels old requests and ignores late versions; a failed range keeps only same-version saved history", async () => {
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, "fetch"), requests = [], states = [], effects = [], pending = [];
  let cursor = 0;
  Object.defineProperty(globalThis, "fetch", { configurable: true, value: (url, options) => new Promise((resolve, reject) => requests.push({ url, options, resolve: body => resolve(new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } })), reject })) });
  const react = {
    useState(initial) { const index = cursor++; if (!(index in states)) states[index] = initial; return [states[index], value => { states[index] = typeof value === "function" ? value(states[index]) : value; }]; },
    useEffect(fn, deps) { const index = cursor++, previous = effects[index]; if (!previous || deps.some((value, index) => value !== previous.deps[index])) { pending.push(() => { previous?.cleanup?.(); effects[index] = { deps, cleanup: fn() }; }); } },
  };
  const { ForecastRevenueTrend } = load(react), identity = { userId: "reader", departmentId: "dept", roles: ["DEPARTMENT_BUDGET_OWNER"] };
  const props = { identity, versionId: "version-A", onVersion() {} };
  const render = () => { cursor = 0; let tree = ForecastRevenueTrend(props); while (pending.length) pending.shift()(); cursor = 0; tree = ForecastRevenueTrend(props); return tree.props; };
  const settle = () => new Promise(resolve => setTimeout(resolve, 0));
  try {
    let view = render(); assert.equal(view.loading, true); assert.equal(view.data, null); assert.equal(view.versionId, "version-A"); assert.equal(requests.length, 2);
    props.versionId = "version-B"; view = render(); assert.equal(requests[0].options.signal.aborted, true); assert.equal(requests[1].options.signal.aborted, true);
    const old = data(), current = { ...data(), points: [point("2026-10-16", 20)] };
    requests[2].resolve(current); requests[3].resolve(defaults().schedule); await settle(); view = render(); assert.equal(view.data.points[0].sequence, 20); assert.equal(view.versionId, "version-B"); assert.equal(view.loading, false);
    requests[0].resolve(old); requests[1].resolve({ ...defaults().schedule, time: "00:00" }); await settle(); view = render(); assert.equal(view.data.points[0].sequence, 20); assert.equal(view.schedule.time, "09:30");
    view.onDays(90); view = render(); assert.equal(view.loading, true); assert.equal(view.data.points[0].sequence, 20); assert.equal(view.shownDays, 30); assert.equal(view.days, 90);
    requests[4].reject(new Error("network timeout")); await settle(); view = render(); assert.match(view.error, /network timeout/); assert.equal(view.shownDays, 30); assert.equal(view.data.points[0].sequence, 20);
    props.versionId = "version-C"; view = render(); assert.equal(view.data, null, "never retain another exact version's data");
    for (const effect of effects) effect?.cleanup?.(); assert.equal(requests.at(-1).options.signal.aborted, true);
  } finally { for (const effect of effects) effect?.cleanup?.(); if (descriptor) Object.defineProperty(globalThis, "fetch", descriptor); else delete globalThis.fetch; }
});
