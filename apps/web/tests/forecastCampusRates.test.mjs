import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";
import { previewForecast } from "../src/forecastApi.ts";
import { budgetReadKey, peekBudgetRead, storeBudgetRead } from "../src/budgetReadCache.ts";

const require = createRequire(import.meta.url);
const compiled = await build({ entryPoints: [fileURLToPath(new URL("../src/ForecastCampusRates.tsx", import.meta.url))], bundle: true,
  write: false, platform: "node", format: "cjs", jsx: "automatic", external: ["react", "react-dom", "exceljs"], loader: { ".css": "empty" }, logLevel: "silent" });
const campus = (name = "公主坟") => ({ season: "秋季", campus: name, budget_new: 65, current_business_new: 34,
  new_base: 38, old_base: 235, budgeted_additional: 31, unbudgeted_additional: 0, additional: 31, new_withdrawal: 1, old_withdrawal: 33 });
const baseAssumptions = () => ({ states: { 暑假: "FINALIZED", 秋季: "OPEN", 寒假: "FUTURE", 春季: "FUTURE" }, minimum_sample: 30, mappings: [], adjustments: [] });
const identity = { userId: "owner", departmentId: "rate-tests", roles: ["DEPARTMENT_BUDGET_OWNER"] };
const calculation = () => ({ annual: { forecast_revenue: 10000000, gap: 100000 }, ready: true, issues: [],
  campuses: [campus(), campus("清河")], rows: ["公主坟", "清河"].map(campus => ({ season: "秋季", campus, forecast_total: 270, forecast_revenue: 700000 })) });
const nodes = (node, predicate) => Array.isArray(node) ? node.flatMap(n => nodes(n, predicate)) : !node || typeof node !== "object" ? [] : [...(predicate(node) ? [node] : []), ...nodes(node.props?.children, predicate)];
const text = node => Array.isArray(node) ? node.map(text).join("") : node && typeof node === "object" ? text(node.props?.children) : node == null ? "" : String(node);
function harness() {
  const state = [], effects = [], refs = [], queued = []; let cursor = 0;
  const react = { useState(initial) { const index = cursor++; if (!(index in state)) state[index] = typeof initial === "function" ? initial() : initial; return [state[index], value => { state[index] = typeof value === "function" ? value(state[index]) : value; }]; },
    useRef(initial) { const index = cursor++; return refs[index] ??= { current: initial }; },
    useEffect(fn, deps) { const index = cursor++, prior = effects[index]; if (!prior || deps.some((value, i) => value !== prior.deps[i])) queued.push(() => { prior?.cleanup?.(); effects[index] = { deps, cleanup: fn() }; }); } };
  const module = { exports: {} };
  new Function("require", "module", "exports", compiled.outputFiles[0].text)(name => name === "react" ? react : require(name), module, module.exports);
  const assumptions = baseAssumptions(), saved = calculation();
  const props = { identity, version: { id: "parent", sequence: 27, as_of: "2026-10-24", assumptions, calculation: saved }, assumptions,
    active: true, disabled: false, onChange(next) { props.assumptions = next; }, onStatus(next) { props.status = next; } };
  const render = () => { cursor = 0; const tree = module.exports.ForecastCampusRates(props); while (queued.length) queued.shift()(); return tree; };
  return { props, render, cleanup() { effects.forEach(effect => effect?.cleanup?.()); } };
}
const settle = () => new Promise(resolve => setTimeout(resolve, 0));

test("preview POST is cancellable and never invalidates or replaces saved forecast reads", async () => {
  const original = globalThis.fetch, calls = [], controller = new AbortController();
  const key = budgetReadKey(identity, "/api/v1/forecasts/fy27/current"), saved = { version: { id: "saved" } };
  storeBudgetRead(key, saved);
  globalThis.fetch = async (url, options) => { calls.push({ url, options }); return new Response(JSON.stringify({ preview: true, parent_id: "parent", calculation: calculation(), system_campuses: [campus()] })); };
  try {
    await previewForecast(identity, "parent", baseAssumptions(), controller.signal);
    assert.equal(calls.length, 1); assert.match(calls[0].url, /\/preview$/);
    assert.equal(calls[0].options.method, "POST"); assert.equal(calls[0].options.headers["Idempotency-Key"], undefined);
    assert.equal(JSON.parse(calls[0].options.body).reason, undefined);
    assert.equal(peekBudgetRead(key), saved); controller.abort(); assert.equal(calls[0].options.signal.aborted, true);
  } finally { globalThis.fetch = original; }
});

test("three sliders share one complete-assumption preview; switching campus retains drafts, undo restores saved values", async () => {
  const original = globalThis.fetch, calls = [];
  globalThis.fetch = async (url, options) => { calls.push({ url, options }); return new Response(JSON.stringify({ preview: true, parent_id: "parent", calculation: calculation(), system_campuses: [campus(), campus("清河")] })); };
  const h = harness();
  try {
    h.render(); await new Promise(resolve => setTimeout(resolve, 450)); await settle(); let tree = h.render();
    assert.equal(nodes(tree, n => n.type === "input" && n.props.type === "range").length, 3);
    const exportTable = nodes(tree, n => typeof n.type === "function" && n.type.name === "ActionTable")[0];
    assert.equal(exportTable.props.exportLabel, "导出当前表格");
    assert.equal(exportTable.props.exportData.rows.length, 2);
    const number = () => nodes(h.render(), n => n.type === "input" && n.props.id === "rate-number-additional")[0];
    number().props.onChange({ target: { value: "120" } }); tree = h.render();
    assert.equal(h.props.assumptions.adjustments[0].value, 44); assert.equal(h.props.assumptions.adjustments[0].one_shot, true);
    const select = nodes(tree, n => n.type === "select" && n.props["aria-label"] === "调整校区")[0];
    select.props.onChange({ target: { value: "清河" } }); assert.equal(number().props.value, "100");
    number().props.onChange({ target: { value: "130" } }); tree = h.render();
    assert.equal(h.props.assumptions.adjustments.length, 2);
    nodes(tree, n => n.type === "select" && n.props["aria-label"] === "调整校区")[0].props.onChange({ target: { value: "公主坟" } });
    assert.equal(number().props.value, "120");
    h.props.active = false; h.render(); h.props.active = true; tree = h.render(); assert.equal(number().props.value, "120");
    nodes(tree, n => n.type === "button" && text(n) === "撤销本校区修改")[0].props.onClick(); tree = h.render();
    assert.equal(number().props.value, "100"); assert.equal(h.props.assumptions.adjustments.length, 1); assert.equal(h.props.assumptions.adjustments[0].campus, "清河");
    await new Promise(resolve => setTimeout(resolve, 450)); await settle(); tree = h.render();
    const sent = JSON.parse(calls.at(-1).options.body);
    assert.deepEqual(sent.assumptions, h.props.assumptions); assert.equal(sent.parent_id, "parent");
    assert.match(text(tree), /仅|只改本次试算/); assert.match(text(tree), /未保存/);
    assert.equal(nodes(tree, n => n.type === "button" && /保存|确认/.test(text(n))).length, 0, "the component never adds another save flow");
  } finally { h.cleanup(); globalThis.fetch = original; }
});

test("aborted or late trials cannot replace a newer preview; stale parents are visible without losing inputs", async () => {
  const original = globalThis.fetch, calls = [];
  globalThis.fetch = (url, options) => new Promise(resolve => calls.push({ url, options, resolve: (body, status = 200) => resolve(new Response(JSON.stringify(body), { status })) }));
  const h = harness();
  try {
    h.render(); await new Promise(resolve => setTimeout(resolve, 450));
    h.props.assumptions = { ...h.props.assumptions, minimum_sample: 31 }; h.render();
    assert.equal(calls[0].options.signal.aborted, true);
    await new Promise(resolve => setTimeout(resolve, 450));
    calls[1].resolve({ detail: "预测版本已变化，请重新读取后操作" }, 409); await settle(); let tree = h.render();
    assert.match(text(tree), /预测版本已变化/); assert.equal(h.props.status.previewError, true);
    calls[0].resolve({ preview: true, parent_id: "parent", calculation: calculation(), system_campuses: [campus()] }); await settle(); tree = h.render();
    assert.match(text(tree), /预测版本已变化/); assert.equal(h.props.assumptions.minimum_sample, 31);
    h.props.active = false; h.render(); assert.equal(calls[1].options.signal.aborted, true);
  } finally { h.cleanup(); globalThis.fetch = original; }
});
