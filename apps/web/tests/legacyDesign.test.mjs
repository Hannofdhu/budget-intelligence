// Synthetic presentation and guard fixtures only. Never execute real commands,
// confirmation, publication, database migrations or production budget requests.
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";
import * as React from "react";

const require = createRequire(import.meta.url);
const root = fileURLToPath(new URL("../src/", import.meta.url));
async function compile(name, extra = "") {
  const source = fs.readFileSync(new URL(`../src/${name}`, import.meta.url), "utf8");
  const result = await build({ stdin: { contents: source + extra, resolveDir: root, sourcefile: name, loader: "tsx" }, bundle: true,
    write: false, platform: "node", format: "cjs", jsx: "automatic", external: ["react", "react-dom", "exceljs"],
    loader: { ".css": "empty" }, logLevel: "silent" });
  return fakeReact => { const loaded = { exports: {} };
    new Function("require", "module", "exports", result.outputFiles[0].text)(name => name === "react" ? fakeReact : require(name), loaded, loaded.exports);
    return loaded.exports; };
}
const rate = await compile("RateTableEditor.tsx", "\nexport { parsePercent };"), business = await compile("BusinessForm.tsx"),
  publication = await compile("App.tsx", "\nexport { PublicationPage, CheckRow };" );
const find = (node, predicate) => Array.isArray(node) ? node.flatMap(child => find(child, predicate)) : !node || typeof node !== "object" ? []
  : [...(predicate(node) ? [node] : []), ...find(node.props?.children, predicate)];
const text = node => Array.isArray(node) ? node.map(text).join("") : node && typeof node === "object" ? text(node.props?.children)
  : node == null || typeof node === "boolean" ? "" : String(node);
function hooks(initial = []) {
  const states = [...initial]; let cursor = 0;
  return { states, reset() { cursor = 0; }, react: { ...React, useState(value) { const index = cursor++;
    if (!(index in states)) states[index] = typeof value === "function" ? value() : value;
    return [states[index], next => { states[index] = typeof next === "function" ? next(states[index]) : next; }]; },
    useMemo: callback => callback(), useCallback: callback => callback, useEffect() {}, useRef: value => ({ current: value }) } };
}

test("legacy rate editor has one transfer/search owner outside scrolling and retains row fields, percentage parsing and gates", () => {
  const h = hooks(), exports = rate(h.react), edits = [], actions = [];
  const value = { items: [{ rate_reference_id: "ref-1", current_value_ppm: 650000, lower_bound_ppm: 600000, upper_bound_ppm: 900000, boundary_reason: "沿用范围", manual_reason: "" }] };
  const referenceRows = [{ rate_reference_id: "ref-1", rate_parameter_stable_key: "stable-1", quarter_name: "暑假", fiscal_year: 2028,
    campus: "西直门", track: "智慧空间", product: "普通", grade: "七年级", rate_type: "RENEWAL_RATE", system_value_ppm: 640000, locked: false, boundary_confirmed: true }];
  const before = JSON.stringify({ value, referenceRows });
  const render = (extra = {}) => { h.reset(); return exports.RateTableEditor({ value, referenceRows, dirty: false,
    onChange: next => edits.push(next), onAction: (...args) => actions.push(args), ...extra }); };
  let tree = render(), table = find(tree, node => node.type?.name === "ActionTable")[0];
  assert.equal(table.props.scrollClassName, "rate-table-wrap"); assert.equal(table.props.scrollLabel, "比例参数明细表，可横向滚动");
  assert.equal(table.props.keyboardNavigation, undefined, "native editing keeps its existing keyboard ownership");
  assert.ok(find(table.props.toolbarStart, node => node.type === "input").length);
  assert.equal(find(table, node => node.type === "thead")[0].props.children.props.children.length, 10);
  assert.equal(find(table, node => node.type === "tbody")[0].props.children.length, 1);
  const current = find(table, node => node.type?.name === "PercentInput" && node.props.label === "当前比例")[0];
  current.props.onCommit(812345); assert.equal(edits.at(-1).items[0].current_value_ppm, 812345);
  assert.equal(value.items[0].current_value_ppm, 650000);
  assert.equal(exports.parsePercent("81.2345"), 812345); assert.equal(exports.parsePercent("0"), 0); assert.equal(exports.parsePercent("-1"), "-1");
  find(table, node => node.type === "input" && node.props["aria-label"] === "选择暑假比例")[0].props.onChange(); tree = render();
  const lock = find(tree, node => node.type === "button" && text(node) === "锁定选中项")[0]; assert.equal(lock.props.disabled, true, "a required reason is still required");
  find(tree, node => node.type === "input" && node.props.placeholder === "填写锁定或解锁原因")[0].props.onChange({ target: { value: "核对后锁定" } }); tree = render();
  assert.equal(find(tree, node => node.type === "button" && text(node) === "锁定选中项")[0].props.disabled, false);
  for (const extra of [{ dirty: true }, { disabled: true }]) {
    tree = render(extra); assert.equal(find(tree, node => node.type === "button" && text(node) === "锁定选中项")[0].props.disabled, true);
  }
  table = find(render({ disabled: true }), node => node.type?.name === "ActionTable")[0];
  assert.ok(find(table, node => node.type?.name === "PercentInput").every(node => node.props.disabled === true));
  assert.deepEqual(actions, [], "render and selection do not invoke lock commands"); assert.equal(JSON.stringify({ value, referenceRows }), before);
});

test("legacy read result table retains full filtered transfer beyond pagination, visible units and numeric column roles", () => {
  const h = hooks(), { ReadTable } = business(h.react);
  const rows = Array.from({ length: 61 }, (_, index) => ({ campus: index % 2 ? "中关村" : "西直门", integer_total_students: index,
    quarter_recognized_revenue_yuan: index ? "0" : "-12.345678", status: "READY", internal_id: `id-${index}` }));
  const before = JSON.stringify(rows), render = () => { h.reset(); return ReadTable({ rows }); };
  let tree = render(), table = find(tree, node => node.type?.name === "ActionTable")[0];
  assert.equal(table.props.scrollClassName, "trial-table"); assert.equal(table.props.scrollLabel, "结果明细表，可横向滚动");
  assert.equal(table.props.keyboardNavigation, true); assert.equal(table.props.exportData.rows.length, 61);
  assert.equal(find(table, node => node.type === "tbody")[0].props.children.length, 25);
  const headings = find(find(table, node => node.type === "thead")[0], node => node.type === "th");
  assert.deepEqual(headings.map(text), ["校区", "总人次", "季度结转收入（元）", "状态"]);
  assert.equal(headings[0].props.className, undefined); assert.equal(headings[1].props.className, "budget-cell-number");
  assert.equal(headings[2].props.className, "budget-cell-number", "decimal strings are numeric presentation columns"); assert.match(text(table), /-12\.35 元/, "existing display precision is unchanged");
  find(table.props.toolbarStart, node => node.type === "input")[0].props.onChange({ target: { value: "西直门" } }); tree = render();
  table = find(tree, node => node.type?.name === "ActionTable")[0]; assert.equal(table.props.exportData.rows.length, 31);
  assert.ok(table.props.exportData.rows.every(row => Object.values(row).includes("西直门")));
  assert.equal(find(table, node => node.type === "tbody")[0].props.children.length, 25);
  assert.equal(JSON.stringify(rows), before);
});

function publicationHarness({ blocked = false, confirmed = false, published = false, busy = false, reason = "确认原因", error = false, loading = false } = {}) {
  const revision = { budget_revision_id: "revision-1", revision_no: 3, created_at: "2026-10-14T00:00:00Z", component_manifest: confirmed ? { formal_confirmation_id: "formal-1" } : {} };
  const readiness = { status: blocked ? "BLOCKED" : "READY", blocker_count: blocked ? 1 : 0, gate_rule_version: "unchanged-rule",
    input_fingerprint: "precise-input-fingerprint", checks: [{ code: "RATE_BOUNDARIES_CONFIRMED", rule: "比例边界已确认", group: "RATE", passed: !blocked, actual: { locked_count: 1 } }], planned_new_students: [] };
  const budget = { annual_budget_id: "budget-1", fiscal_year: 2028, current_published_revision_id: published ? "revision-1" : null };
  const h = hooks(["ok", loading ? "revision-1" : "", "revision-1", { userId: "owner", departmentId: "department", roles: ["DEPARTMENT_BUDGET_OWNER"] },
    error || loading ? null : revision, error || loading ? null : budget, error || loading ? null : readiness, null, error ? "error" : "ready",
    busy ? "confirming" : "idle", null, "all", reason, "", error ? "只读读取失败" : null]);
  const exports = publication(h.react);
  return { render() { h.reset(); return exports.PublicationPage(); }, exports, revision, readiness, budget };
}

test("real publication route uses page/section/group/check hierarchy while confirmation and publish guards remain unchanged", () => {
  for (const settings of [{}, { blocked: true }, { reason: "" }, { busy: true }, { confirmed: true }, { confirmed: true, blocked: true }, { published: true }]) {
    const h = publicationHarness(settings), before = JSON.stringify({ revision: h.revision, readiness: h.readiness, budget: h.budget }), tree = h.render();
    const title = find(tree, node => node.type === "h1"); assert.equal(title.length, 1); assert.equal(title[0].props.className, "budget-page-title");
    assert.equal(find(tree, node => node.type === "h2" && node.props.className === "budget-section-title").length, 3);
    assert.equal(find(tree, node => node.type === "h3" && node.props.className === "budget-subsection-title").length, 1);
    const check = find(tree, node => node.type?.name === "CheckRow")[0];
    const checkTree = check.type(check.props); assert.equal(find(checkTree, node => node.type === "h4")[0].props.className, "publication-check-title");
    const confirm = find(tree, node => node.type === "button" && /重新计算并正式确认|正在复算并确认/.test(text(node)))[0];
    const publish = find(tree, node => node.type === "button" && text(node) === "发布年度预算基线")[0];
    if (settings.published) { assert.equal(confirm, undefined); assert.equal(publish, undefined); }
    else if (settings.confirmed) { assert.equal(confirm, undefined); assert.equal(publish.props.disabled, Boolean(settings.blocked)); }
    else { assert.ok(confirm); assert.equal(confirm.props.disabled, Boolean(settings.blocked || settings.reason === "" || settings.busy)); assert.equal(publish, undefined); }
    assert.equal(JSON.stringify({ revision: h.revision, readiness: h.readiness, budget: h.budget }), before);
  }
  const error = publicationHarness({ error: true }).render(); assert.equal(find(error, node => node.type === "h1")[0].props.className, "budget-page-title");
  assert.ok(find(error, node => node.props?.role === "alert").length);
  const loading = publicationHarness({ loading: true }).render(); assert.match(text(loading), /正在读取发布状态/);
});
