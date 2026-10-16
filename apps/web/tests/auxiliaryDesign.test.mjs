// Presentation migration contracts. No live budgets, sessions or data writes.
import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

const require = createRequire(import.meta.url);
async function compiled(path) {
  const result = await build({ entryPoints: [fileURLToPath(new URL(path, import.meta.url))], bundle: true, write: false,
    platform: "node", format: "cjs", jsx: "automatic", external: ["react", "react-dom", "exceljs"], loader: { ".css": "empty" }, logLevel: "silent" });
  return customRequire => {
    const module = { exports: {} };
    new Function("require", "module", "exports", result.outputFiles[0].text)(customRequire ?? require, module, module.exports);
    return module.exports;
  };
}
const opening = await compiled("../src/OpeningStudentsPage.tsx"), rebalance = await compiled("../src/RebalancingPage.tsx"), workspace = await compiled("../src/WorkspacePage.tsx");
const find = (node, predicate) => Array.isArray(node) ? node.flatMap(child => find(child, predicate)) : !node || typeof node !== "object" ? []
  : [...(predicate(node) ? [node] : []), ...find(typeof node.type === "function" && node.type.name === "BudgetDataSection" ? node.type(node.props) : node.props?.children, predicate)];
const label = node => Array.isArray(node) ? node.map(label).join("") : node && typeof node === "object" ? label(node.props?.children) : node == null || typeof node === "boolean" ? "" : String(node);
const dims = (more = {}) => ({ campus: "西直门", track: "智慧空间", product: "普通", grade: "七年级", subject: "数学", ...more });

function openingHarness({ current = true, valid = true } = {}) {
  const data = { opening_status: "DRAFT", opening_student_version_id: "opening-1", fiscal_year: 2028, source_fiscal_season: 202704, is_current_draft: current,
    sources: [{ source_component_id: "frozen-1", dataset_name: "春季冻结来源", snapshot_date: "2027-03-01", source_batch: "batch", partition: "frozen", content_hash: "source-hash",
      rows: [dims({ enrollment: valid ? 12 : null, rate_parameter_id: "rate-1", renewal_rate_ppm: 650000 }), dims({ campus: "西直门南", enrollment: 8, rate_parameter_id: "rate-2", renewal_rate_ppm: 700000 })] }],
    combinations: [dims({ combination_id: "combo-1", campus: "未覆盖新校区" })] };
  const state = { revision: "revision-1", identity: { userId: "owner", departmentId: "department", roles: ["DATA_MAINTAINER"] }, data,
    sourceId: "frozen-1", zeros: [], reason: "核对冻结来源", query: "", busy: false, error: "", receipt: "" };
  const names = Object.keys(state); let cursor = 0;
  const exports = opening(name => name === "react" ? { ...require(name), useEffect() {}, useRef: value => ({ current: value }),
    useState() { const key = names[cursor++]; assert.ok(key); return [state[key], value => { state[key] = typeof value === "function" ? value(state[key]) : value; }]; } } : require(name));
  return { state, data, render() {
    const oldLocation = globalThis.location, oldStorage = globalThis.localStorage;
    globalThis.location = { search: "" }; globalThis.localStorage = { getItem() { return null; } };
    try { cursor = 0; const tree = exports.OpeningStudentsPage(); assert.equal(cursor, names.length); return tree; }
    finally { if (oldLocation === undefined) delete globalThis.location; else globalThis.location = oldLocation; if (oldStorage === undefined) delete globalThis.localStorage; else globalThis.localStorage = oldStorage; }
  } };
}

test("opening source and uncovered details reuse section and transfer roles while source remains a single configuration", () => {
  const h = openingHarness(), before = JSON.stringify(h.data), tree = h.render();
  assert.deepEqual(find(tree, node => typeof node.type === "function" && node.type.name === "BudgetDataSection").map(node => node.props.id), ["opening-frozen-source", "opening-uncovered"]);
  assert.equal(find(tree, node => node.type === "h1")[0].props.className, "budget-page-title");
  const source = find(tree, node => node.type === "select")[0];
  assert.equal(source.props.multiple, undefined); assert.equal(source.props.value, "frozen-1");
  const tables = find(tree, node => typeof node.type === "function" && node.type.name === "ActionTable");
  assert.equal(tables.length, 2); assert.ok(tables.every(table => table.props.scrollClassName.includes("budget-data-section__scroll")));
  assert.equal(tables[0].props.tableLabel, "冻结来源明细"); assert.equal(tables[1].props.tableLabel, "无来源覆盖的明细");
  const sourceValues = find(find(tables[0], node => node.type === "tbody")[0], node => node.type === "td");
  for (const index of [5, 6, 7, 13, 14, 15]) assert.equal(sourceValues[index].props.className, "budget-cell-number");
  assert.deepEqual(find(find(tables[1], node => node.type === "thead")[0], node => node.type === "th").map(label), ["校区 / 赛道 / 产品", "年级 / 科目", "确认期初为 0"]);
  assert.equal(JSON.stringify(h.data), before);
});

test("opening source transfers retain exact saved facts while unsaved zero confirmations are previews without invented sequence/time", () => {
  const h = openingHarness();
  const context = (tree, id) => JSON.parse(find(tree, node => node.props?.id === id)[0].props["data-budget-export-context"]);
  let tree = h.render();
  const source = context(tree, "opening-frozen-source");
  assert.equal(source.id, "revision-1"); assert.equal(source.sourceId, "frozen-1");
  assert.equal(source.object, "期初来源（已保存事实）"); assert.equal(source.asOf, "2027-03-01");
  assert.equal(source.sequence, undefined); assert.equal(source.createdAt, undefined); assert.equal(source.preview, undefined);
  assert.equal(context(tree, "opening-uncovered").preview, false);
  find(tree, node => node.type === "input" && node.props.type === "checkbox")[0].props.onChange({ target: { checked: true } });
  tree = h.render();
  assert.equal(context(tree, "opening-uncovered").preview, true);
  assert.equal(context(tree, "opening-uncovered").id, "revision-1");
  assert.deepEqual(context(tree, "opening-frozen-source"), source, "unsaved maintenance never changes the frozen-source fact label");
});

test("opening query changes only the visible table and zero confirmation still uses original draft/read-only gates", () => {
  const h = openingHarness(), before = JSON.stringify(h.data);
  const [source] = find(h.render(), node => typeof node.type === "function" && node.type.name === "ActionTable");
  find(source.props.toolbarStart, node => node.type === "input")[0].props.onChange({ target: { value: "西直门南" } });
  const [filtered] = find(h.render(), node => typeof node.type === "function" && node.type.name === "ActionTable");
  const rows = find(find(filtered, node => node.type === "tbody")[0], node => node.type === "tr");
  assert.equal(rows.length, 1); assert.match(label(rows[0]), /西直门南/); assert.equal(h.state.zeros.length, 0);
  const checkbox = find(h.render(), node => node.type === "input" && node.props.type === "checkbox")[0];
  checkbox.props.onChange({ target: { checked: true } }); assert.deepEqual(h.state.zeros, ["combo-1"]);
  const save = find(h.render(), node => node.type === "button" && label(node) === "保存期初版本")[0];
  assert.equal(save.props.disabled, false);
  for (const settings of [{ current: false }, { valid: false }]) {
    const readonly = openingHarness(settings), tree = readonly.render();
    const save = find(tree, node => node.type === "button" && label(node) === "保存期初版本")[0];
    assert.equal(save.props.disabled, true);
    if (settings.current === false) assert.equal(find(tree, node => node.type === "input" && node.props.type === "checkbox")[0].props.disabled, true);
  }
  assert.equal(JSON.stringify(h.data), before);
});

test("scenario change details are one native transferable table with unchanged five columns and full stable keys", () => {
  const { ChangeTable } = rebalance();
  const changes = Array.from({ length: 61 }, (_, index) => ({ scenario_change_id: `change-${index}`, object_type: index % 2 ? "RATE" : "ENTRY",
    object_stable_key: `very-long-exact-key-${index}-source-retained-000000`, original_value: 100000, suggested_value: 200000, adjustment: 100000 }));
  const before = JSON.stringify(changes), table = ChangeTable({ changes });
  assert.equal(table.type.name, "ActionTable"); assert.equal(table.props.keyboardNavigation, true);
  assert.deepEqual(find(find(table, node => node.type === "thead")[0], node => node.type === "th").map(label), ["杠杆", "对象键", "原值", "建议值", "变化"]);
  assert.equal(find(find(table, node => node.type === "tbody")[0], node => node.type === "tr").length, 61);
  assert.equal(label(find(table, node => node.type === "code")[60]), changes[60].object_stable_key);
  const html = renderToStaticMarkup(createElement(ChangeTable, { changes }));
  assert.match(html, /<table[^>]*aria-label="候选方案参数变更"/); assert.doesNotMatch(html, /role="table"|role="row"/);
  assert.equal((html.match(/>一键复制<\/button>/g) ?? []).length, 1); assert.equal((html.match(/>导出当前表格<\/button>/g) ?? []).length, 1);
  assert.equal(JSON.stringify(changes), before);
});

test("legacy workspace uses one semantic page title and preserves finalized fieldset and confirmation guards", () => {
  const data = { revision_id: "revision-1", revision_no: 1, fiscal_year: 2028, is_current_draft: false, is_published: true, manifest: {}, source: {}, sections: [
    { key: "targets", label: "经营目标", schema: { type: "object", properties: {} }, value: {}, data: {}, path: "/targets", confirm: { path: "/confirm", body: {} } },
  ] };
  const states = [null, [], data, "targets", {}, false, false, "", "", "", null, "", false]; let cursor = 0;
  const { WorkspacePage } = workspace(name => name === "react" ? { ...require(name), useEffect() {}, useMemo: compute => compute(), useRef: value => ({ current: value }), useState() { return [states[cursor++], () => {}]; } } : require(name));
  const before = JSON.stringify(data), tree = WorkspacePage(); assert.equal(cursor, states.length);
  const title = find(tree, node => node.type === "h1"); assert.equal(title.length, 1); assert.equal(title[0].props.className, "budget-page-title");
  const fieldset = find(tree, node => node.type === "fieldset" && node.props.className === "business-fieldset")[0]; assert.equal(fieldset.props.disabled, true);
  assert.equal(find(tree, node => node.props?.className === "content-kicker").length, 0);
  assert.equal(JSON.stringify(data), before);
});

function readonlyRateWorkspace({ published = false, historical = false, busy = false, selected = "rates" } = {}) {
  const item = { rate_reference_id: "ref-1", current_value_ppm: 650000, lower_bound_ppm: 600000, upper_bound_ppm: 900000, boundary_reason: "沿用", manual_reason: "" };
  const draft = selected === "rates" ? { items: [item] } : { target_revenue_yuan: "130000000" };
  const data = { revision_id: "readonly-revision", revision_no: 3, fiscal_year: 2028,
    is_current_draft: !historical, is_published: published, manifest: { rate_parameter_version_id: "rate-version" }, source: {}, sections: [
      { key: selected, label: selected === "rates" ? "比例参数" : "经营目标", value: draft, schema: { type: "object", properties: { target_revenue_yuan: { type: "string" } } },
        data: { record_count: 1, confirmed_boundary_count: 0, items: [{ ...item, rate_parameter_stable_key: "stable-1", quarter_name: "暑假", campus: "西直门" }] },
        path: "/unchanged-write-path", confirm: { path: "/unchanged-confirm-path", body: {} } },
    ] };
  const rootStates = [null, [], data, selected, draft, false, busy, "", "", "", null, "", false];
  const states = new Map([["WorkspacePage", rootStates]]); let active = "WorkspacePage", cursor = 0, id = 0;
  const fakeReact = { ...require("react"), useEffect() {}, useMemo: compute => compute(), useRef: value => ({ current: value }), useId: () => `fixture-${id++}`,
    useState(initial) { const values = states.get(active) ?? []; states.set(active, values); const index = cursor++;
      if (!(index in values)) values[index] = typeof initial === "function" ? initial() : initial;
      return [values[index], next => { values[index] = typeof next === "function" ? next(values[index]) : next; }]; } };
  const { WorkspacePage } = workspace(name => name === "react" ? fakeReact : require(name));
  const expand = node => {
    if (Array.isArray(node)) return node.map(expand);
    if (!node || typeof node !== "object") return node;
    if (typeof node.type === "function" && ["RateTableEditor", "PercentInput", "ActionTable", "BusinessForm", "BudgetLifecycle"].includes(node.type.name)) {
      const previous = [active, cursor]; active = node.type.name; cursor = 0;
      const rendered = node.type(node.props); [active, cursor] = previous; return expand(rendered);
    }
    return { ...node, props: { ...node.props, children: expand(node.props?.children) } };
  };
  return { data, draft, rootStates, render() { active = "WorkspacePage"; cursor = 0; return expand(WorkspacePage()); } };
}

function descendantsWithAncestors(node, predicate, ancestors = []) {
  if (Array.isArray(node)) return node.flatMap(child => descendantsWithAncestors(child, predicate, ancestors));
  if (!node || typeof node !== "object") return [];
  return [...(predicate(node) ? [{ node, ancestors }] : []), ...descendantsWithAncestors(node.props?.children, predicate, [...ancestors, node])];
}
const effectivelyDisabled = ({ node, ancestors }) => Boolean(node.props.disabled || ancestors.some(parent => parent.type === "fieldset" && parent.props.disabled));

test("readonly workspace keeps rate copy/export outside disabled ancestors but disables every editing and commit control", () => {
  for (const settings of [{ published: true }, { historical: true }, { busy: true }]) {
    const h = readonlyRateWorkspace(settings), before = JSON.stringify({ data: h.data, draft: h.draft }); let tree = h.render();
    const tools = descendantsWithAncestors(tree, node => node.type === "button" && ["一键复制", "导出当前表格"].includes(label(node)));
    assert.equal(tools.length, 2); assert.ok(tools.every(control => !effectivelyDisabled(control)), "read-only budgets can still transfer the original table");
    assert.ok(tools.every(({ ancestors }) => !ancestors.some(parent => parent.props?.className === "rate-table-wrap")), "transfer tools never scroll with the rows");
    const search = descendantsWithAncestors(tree, node => node.type === "input" && node.props.placeholder === "筛选季节、校区、产品或年级")[0];
    assert.equal(effectivelyDisabled(search), false);
    const percentage = descendantsWithAncestors(tree, node => node.type === "input" && ["当前比例", "比例下限", "比例上限"].includes(node.props["aria-label"]));
    assert.equal(percentage.length, 3); assert.ok(percentage.every(effectivelyDisabled));
    const reasons = descendantsWithAncestors(tree, node => node.type === "input" && ["范围依据", "人工调整原因"].includes(node.props["aria-label"]));
    assert.equal(reasons.length, 2); assert.ok(reasons.every(effectivelyDisabled));
    const commits = descendantsWithAncestors(tree, node => node.type === "button" && ["已保存", "保存比例", "正在处理…", "确认并继续"].includes(label(node)));
    assert.equal(commits.length, 2); assert.ok(commits.every(effectivelyDisabled), "removing the read table fieldset must not enable saving or confirmation");
    descendantsWithAncestors(tree, node => node.type === "input" && node.props["aria-label"] === "选择暑假比例")[0].node.props.onChange();
    tree = h.render();
    const batch = descendantsWithAncestors(tree, node => node.type === "input" && ["例如 80", "例如 70", "例如 90"].includes(node.props.placeholder));
    assert.equal(batch.length, 3); assert.ok(batch.every(effectivelyDisabled));
    const actions = descendantsWithAncestors(tree, node => node.type === "button" && ["应用到选中项", "锁定选中项", "解锁选中项"].includes(label(node)));
    assert.equal(actions.length, 3); assert.ok(actions.every(effectivelyDisabled));
    assert.equal(effectivelyDisabled(descendantsWithAncestors(tree, node => node.type === "input" && node.props.placeholder === "填写锁定或解锁原因")[0]), true);
    assert.equal(h.rootStates[5], false); assert.equal(JSON.stringify({ data: h.data, draft: h.draft }), before, "selecting read-only rows never mutates the budget");
    const business = readonlyRateWorkspace({ ...settings, selected: "targets" }).render();
    const ordinaryInput = descendantsWithAncestors(business, node => node.type === "input" && node.props.value === "130000000")[0];
    assert.ok(ordinaryInput); assert.equal(effectivelyDisabled(ordinaryInput), true, "ordinary business fields retain their disabled fieldset");
  }
});
