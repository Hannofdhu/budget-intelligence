import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";

const require = createRequire(import.meta.url);
const compiled = await build({ entryPoints: [fileURLToPath(new URL("../src/QuarterlyBusinessTable.tsx", import.meta.url))],
  bundle: true, write: false, platform: "node", format: "cjs", jsx: "automatic", external: ["react", "react-dom", "exceljs"],
  loader: { ".css": "empty" }, logLevel: "silent" });
const find = (node, predicate) => Array.isArray(node) ? node.flatMap(child => find(child, predicate)) : !node || typeof node !== "object" ? []
  : [...(predicate(node) ? [node] : []), ...find(node.props?.children, predicate)];
const text = node => Array.isArray(node) ? node.map(text).join("") : node && typeof node === "object" ? text(node.props?.children)
  : node == null || typeof node === "boolean" ? "" : String(node);
function render(props, page = 0) {
  const module = { exports: {} };
  const customRequire = name => name === "react" ? { ...require(name), useState: () => [page, () => {}], useMemo: compute => compute() } : require(name);
  new Function("require", "module", "exports", compiled.outputFiles[0].text)(customRequire, module, module.exports);
  return module.exports.QuarterlyBusinessTable(props);
}
const base = { rowKey: row => row.id, season: row => row.season, filename: "招生参数跨季度", tableLabel: "招生参数",
  businessColumns: [{ key: "campus", label: "校区", value: row => row.campus }],
  metrics: [{ key: "rate", label: "续班率", format: "rate", value: row => row.rate }, { key: "count", label: "人次", format: "count", value: row => row.count }] };

test("cross-quarter table is a compact metric-by-season reader with one full-range copy/export owner", () => {
  const rows = Array.from({ length: 31 }, (_, index) => ({ id: String(index), campus: "西直门", season: "秋季", rate: .65, count: index + .123456 }));
  const before = JSON.stringify(rows), tree = render({ ...base, rows }, 1);
  const [table] = find(tree, node => typeof node.type === "function" && node.type.name === "ActionTable");
  assert.ok(table);
  assert.equal(table.props.keyboardNavigation, true);
  assert.equal(table.props.scrollClassName, "qbt-scroll");
  assert.equal(typeof table.props.scrollRef, "function");
  assert.equal(table.props.scrollLabel, "招生参数，四季横排，可横向滚动");
  assert.equal(table.props.exportData.rows.length, 31, "not just the 11 visible businesses");
  assert.equal(table.props.copyRows.length, 63, "all 31 businesses times two metrics plus header");
  assert.deepEqual(table.props.copyRows[0], ["业务", "指标", "暑假", "秋季", "寒假", "春季"]);
  const [head] = find(table, node => node.type === "thead");
  assert.equal(find(head, node => node.type === "th").length, 6, "four seasons do not explode into 20 metric columns");
  const [body] = find(table, node => node.type === "tbody");
  assert.equal(find(body, node => node.type === "tr").length, 22);
  assert.equal(find(body, node => node.type === "input" || node.type === "select").length, 0, "no second editor");
  assert.equal(table.props.exportData.rows[0]["quarter-1-1"], .123456, "exports keep unrounded source precision");
  assert.equal(JSON.stringify(rows), before);
});

test("cell actions locate exact original row and metric; missing quarters have no action", () => {
  const rows = [{ id: "one", campus: "西直门", season: "秋季", rate: "-", count: -2.123456 }], actions = [];
  const tree = render({ ...base, rows, onCellAction: (...args) => actions.push(args) });
  const [table] = find(tree, node => typeof node.type === "function" && node.type.name === "ActionTable");
  const buttons = find(table, node => node.type === "button");
  assert.equal(buttons.length, 2);
  assert.equal(buttons[0].props["aria-label"], "西直门，秋季续班率，定位编辑");
  assert.equal(text(buttons[0]), "-");
  buttons[0].props.onClick();
  assert.equal(actions[0][0], rows[0]);
  assert.equal(actions[0][1], "rate");
  assert.equal(find(table, node => node.props?.["aria-label"] === "无该季度明细").length, 6);
});

test("non-applicable cells stay read-only even when the same business has other editable metrics", () => {
  const source = { id: "one", campus: "西直门", season: "暑假", rate: "-", count: 12 }, actions = [], checked = [];
  const tree = render({ ...base, rows: [source], onCellAction: (...args) => actions.push(args),
    canCellAction: (row, metricKey) => { checked.push([row, metricKey]); return metricKey === "count"; } });
  const [table] = find(tree, node => typeof node.type === "function" && node.type.name === "ActionTable");
  const buttons = find(table, node => node.type === "button");
  assert.equal(buttons.length, 1, "a saved rate without an original input is not a fake navigation action");
  assert.match(buttons[0].props["aria-label"], /暑假人次/);
  assert.equal(text(table).includes("-"), true, "the original non-applicable value is still readable");
  assert.deepEqual(checked, [[source, "rate"], [source, "count"]], "missing quarters never invoke the applicability predicate");
  buttons[0].props.onClick();
  assert.deepEqual(actions, [[source, "count"]]);
});

test("cell navigation accessible names identify the exact business as well as quarter and metric", () => {
  const rows = [{ id: "west", campus: "西直门", season: "秋季", count: 12 },
    { id: "south", campus: "中关村", season: "秋季", count: 20 }];
  const tree = render({ ...base, rows, onCellAction: () => {} });
  const [table] = find(tree, node => typeof node.type === "function" && node.type.name === "ActionTable");
  const names = find(table, node => node.type === "button").map(button => button.props["aria-label"]);
  assert.equal(new Set(names).size, 4);
  assert.ok(names.includes("西直门，秋季人次，定位编辑"));
  assert.ok(names.includes("中关村，秋季人次，定位编辑"));
});

test("ambiguous duplicate identities are visibly disclosed and empty scope stays empty", () => {
  const rows = [{ id: "one", campus: "西直门", season: "秋季", rate: .6 }, { id: "one", campus: "西直门", season: "秋季", rate: .7 }];
  const tree = render({ ...base, rows });
  assert.match(text(find(tree, node => node.props?.role === "status")[0]), /同季有多条明细.*未跨季配对/);
  const empty = render({ ...base, rows: [] });
  assert.match(text(empty), /当前范围没有明细/);
  const [table] = find(empty, node => typeof node.type === "function" && node.type.name === "ActionTable");
  assert.deepEqual(table.props.exportData.rows, []);
  assert.equal(find(empty, node => node.type === "button").every(button => button.props.disabled), true);
});
