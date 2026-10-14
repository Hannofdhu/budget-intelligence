// Real page tree contracts; no network/effects, data writes or browser simulation.
import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";
import { historicalExportColumns } from "../src/historicalBudgetExport.ts";
import { tableCopyRows } from "../src/tableTransfer.ts";

const require = createRequire(import.meta.url);
const compiled = await build({ entryPoints: [fileURLToPath(new URL("../src/HistoricalBudgetPage.tsx", import.meta.url))],
  bundle: true, write: false, platform: "node", format: "cjs", jsx: "automatic", external: ["react", "react-dom", "exceljs"],
  loader: { ".css": "empty" }, logLevel: "silent" });
const find = (node, predicate) => Array.isArray(node) ? node.flatMap(child => find(child, predicate)) : !node || typeof node !== "object" ? []
  : [...(predicate(node) ? [node] : []), ...find(typeof node.type === "function" && node.type.name === "BudgetDataSection"
    ? node.type(node.props) : node.props?.children, predicate)];
const elementText = node => Array.isArray(node) ? node.map(elementText).join("") : node && typeof node === "object" ? elementText(node.props?.children)
  : node == null || typeof node === "boolean" ? "" : String(node);
const line = (index, more = {}) => ({ row: index, season: "暑假", track: "智慧空间", campus: "西直门", product: "普通",
  grade: "七年级", source_grade: "七年级", subject: "数学", entry: "是", annual: 12000.12, prior_students: 10, opening: 10,
  minimum: 10, renewal: .65, share: .1, old: 10, new: 3, total: 13, price: 1000, discount: .9, tuition: 900,
  list_revenue: 13000, business: 11700, recognition: .9, current: 10530, revenue: 10028.57, carryout: 1170,
  sources: {}, parameters: {}, excel_topup: false, ...more });

function pageTree(rows, step, filters, page = 0, quarterLayout = "wide") {
  const states = [{ rows }, "", step, filters, page, quarterLayout];
  let cursor = 0;
  const module = { exports: {} };
  const customRequire = name => name === "react" ? { ...require(name),
    useState() { assert.ok(cursor < states.length, "model any additional page state explicitly"); return [states[cursor++], () => {}]; },
    useEffect() {}, useRef: current => ({ current }), useMemo: compute => compute(),
  } : require(name);
  new Function("require", "module", "exports", compiled.outputFiles[0].text)(customRequire, module, module.exports);
  const tree = module.exports.HistoricalBudgetPage();
  assert.equal(cursor, states.length);
  return tree;
}

test("FY27 uses the same five task names as next-year budget in navigation and current-page headings", () => {
  const labels = ["定目标和SKU", "入口新生定多少", "续班率、新生占比", "学费、折扣、班额", "看结果 / 对目标"];
  const filters = { season: "", track: "", campus: "", product: "", grade: "", subject: "" };
  for (const step of [1, 2, 3, 4, 5]) {
    const tree = pageTree([line(1)], step, filters);
    const [navigation] = find(tree, node => typeof node.type === "function" && node.type.name === "BudgetPageNavigation");
    assert.deepEqual(navigation.props.labels, labels);
    assert.equal(navigation.props.current, step);
    const [heading] = find(tree, node => node.type === "h1");
    assert.equal(elementText(heading), labels[step - 1]);
  }
});

test("FY27 shares SKU identity and section chrome while retaining finalized read-only content and other task layouts", () => {
  const rows = [line(1), line(2, { campus: "中关村" }), line(3, { season: "秋季" })];
  const before = JSON.stringify(rows), filters = { season: "", track: "", campus: "西直门", product: "", grade: "", subject: "" };
  const tree = pageTree(rows, 1, filters);
  const sections = find(tree, node => typeof node.type === "function" && node.type.name === "BudgetDataSection");
  assert.equal(sections.length, 1);
  assert.equal(sections[0].props.title, "SKU");
  assert.equal(sections[0].props.count, 1);
  assert.equal(sections[0].props.id, "history-details", "existing detail deep links retain their target");
  const [section] = find(tree, node => node.type === "section" && node.props["aria-label"] === "预算明细");
  assert.ok(section);
  assert.equal(section.props["aria-labelledby"], "history-details-title");
  assert.equal(find(section, node => node.type === "h2" && elementText(node) === "SKU").length, 1);
  assert.equal(find(section, node => ["input", "select", "textarea"].includes(node.type)).length, 0,
    "a shared editing-module shell never makes finalized facts editable");
  assert.equal(find(section, node => node.type === "button" && /新增|保存|查看影响/.test(elementText(node))).length, 0);
  for (const step of [2, 3, 4, 5]) {
    assert.equal(find(pageTree(rows, step, filters), node => typeof node.type === "function" && node.type.name === "BudgetDataSection").length, 0,
      "M1 does not silently migrate unrelated task sections");
  }
  assert.equal(JSON.stringify(rows), before);
});

test("FY27 SKU identity uses shared ordered five-dimension cells without changing entry, annual revenue or transfer data", () => {
  const dimensions = { campus: "百学汇学习机教学区与中关村联合长名称校区", track: "智慧伴学跨学段课程赛道",
    product: "云帆白金高端小班联合产品", grade: "七年级与八年级衔接年级", subject: "数学与综合实践长名称科目" };
  const rows = Array.from({ length: 61 }, (_, index) => line(index, { ...dimensions, entry: index % 2 ? "否" : "是", annual: index === 25 ? -12.123456 : index }));
  const before = JSON.stringify(rows), filters = { season: "", track: "", campus: "", product: "", grade: "", subject: "" };
  const [table] = find(pageTree(rows, 1, filters, 1), node => typeof node.type === "function" && node.type.name === "ActionTable" && node.props.exportData);
  const [head] = find(table, node => node.type === "thead"), [body] = find(table, node => node.type === "tbody");
  assert.deepEqual(find(head, node => node.type === "th").map(elementText),
    ["校区 / 赛道 / 产品", "年级 / 科目", "入口", "全年结转收入（元）"]);
  const [first] = find(body, node => node.type === "tr"), cells = find(first, node => node.type === "th" || node.type === "td");
  assert.equal(cells.length, 4);
  assert.deepEqual(cells.map(cell => cell.type), ["th", "td", "td", "td"]);
  assert.equal(cells[0].props.scope, "row");
  assert.equal(elementText(find(cells[0], node => node.type === "strong")[0]), dimensions.campus);
  assert.deepEqual(find(cells[0], node => node.type === "small").map(elementText), [`${dimensions.track} / ${dimensions.product}`]);
  assert.equal(elementText(cells[1]), `${dimensions.grade}${dimensions.subject}`);
  assert.deepEqual(find(cells[1], node => node.type === "small").map(elementText), [dimensions.subject]);
  assert.equal(elementText(cells[2]), "否");
  assert.equal(elementText(cells[3]), (-12.123456).toLocaleString("zh-CN", { maximumFractionDigits: 2 }));
  assert.equal(find(first, node => node.type === "input").length, 0, "FY27 remains read-only rather than acquiring participation checkboxes");
  assert.deepEqual(table.props.exportData.rows, rows);
  assert.deepEqual(table.props.exportData.columns, historicalExportColumns(1));
  assert.equal(table.props.exportData.rows[25].annual, -12.123456, "the visual cell does not reduce exported precision");
  const copied = tableCopyRows(table.props.exportData.columns, table.props.exportData.rows);
  assert.equal(copied.length, 62, "the transfer owner copies the complete filtered range rather than its 25 visible rows");
  assert.equal(copied[26][table.props.exportData.columns.findIndex(column => column.key === "annual")], "-12.123456");
  assert.equal(find(body, node => node.type === "tr").length, 25);
  assert.equal(JSON.stringify(rows), before);
});

test("FY27 read-only detail pages have one transfer owner for every step and keep the full filtered range", () => {
  const rows = [
    ...Array.from({ length: 61 }, (_, i) => line(i)),
    ...Array.from({ length: 11 }, (_, i) => line(100 + i, { campus: "西直门南" })),
    line(200, { season: "秋季" }), line(201, { entry: "否" }),
  ];
  const before = JSON.stringify(rows), filters = { season: "暑假", track: "", campus: "西直门", product: "", grade: "", subject: "" };
  for (const step of [1, 2, 3, 4]) {
    const tree = pageTree(rows, step, filters, 1, "detail");
    const [section] = find(tree, node => node.type === "section" && node.props["aria-label"] === "预算明细");
    assert.ok(section);
    assert.equal(find(section, node => node.type === "button" && elementText(node) === "导出明细").length, 0,
      "the page heading must not repeat the ActionTable export");
    const transfers = find(section, node => typeof node.type === "function" && node.type.name === "ActionTable" && node.props.exportData);
    assert.equal(transfers.length, 1);
    const transfer = transfers[0].props.exportData;
    const expected = rows.filter(row => row.campus === "西直门" && row.season === "暑假" && (step !== 2 || row.entry === "是"));
    assert.deepEqual(transfer.rows, expected);
    assert.equal(transfer.rows.length, step === 2 ? 61 : 62);
    assert.deepEqual(transfer.columns, historicalExportColumns(step));
    const [body] = find(transfers[0].props.children, node => node.type === "tbody");
    assert.equal(find(body, node => node.type === "tr").length, 25, "only display is paged; copy/export receives every filtered row");
    assert.equal(transfer.rows.some(row => row.campus === "西直门南"), false, "similar campus names do not widen scope");
  }
  assert.equal(JSON.stringify(rows), before, "presentation and transfer must not change finalized facts");
});

test("FY27 detail transfers preserve the existing hidden quarter scope behavior and an empty filtered range", () => {
  const rows = [line(1), line(2, { season: "秋季" })];
  const filters = { season: "秋季", track: "", campus: "", product: "", grade: "", subject: "" };
  for (const step of [1, 2, 3, 4]) {
    const [table] = find(pageTree(rows, step, filters, 0, "detail"), node => typeof node.type === "function" && node.type.name === "ActionTable" && node.props.exportData);
    assert.deepEqual(table.props.exportData.rows.map(row => row.season), [step < 3 ? "暑假" : "秋季"]);
    const [empty] = find(pageTree(rows, step, { ...filters, campus: "不存在校区" }, 0, "detail"), node => typeof node.type === "function" && node.type.name === "ActionTable" && node.props.exportData);
    assert.deepEqual(empty.props.exportData.rows, []);
  }
});

test("FY27 parameter and finance pages default to an exact-business, read-only cross-quarter table", () => {
  const rows = [line(7), line(7, { season: "秋季", renewal: "-", share: null, new: -3 }), line(8),
    line(8, { season: "秋季", renewal: .77 }), line(9, { campus: "中关村" })];
  const before = JSON.stringify(rows), filters = { season: "", track: "", campus: "西直门", product: "", grade: "", subject: "" };
  for (const step of [3, 4]) {
    const tree = pageTree(rows, step, filters);
    const [quarter] = find(tree, node => typeof node.type === "function" && node.type.name === "QuarterlyBusinessTable");
    assert.ok(quarter);
    assert.deepEqual(quarter.props.rows, rows.slice(0, 4), "the full current range, not the paged source details, is arranged");
    assert.equal(quarter.props.rowKey(rows[0]), quarter.props.rowKey(rows[1]));
    assert.notEqual(quarter.props.rowKey(rows[0]), quarter.props.rowKey(rows[2]), "same dimension labels never merge distinct source businesses");
    assert.equal(quarter.props.onCellAction, undefined, "finalized rows have no editing action");
    const controls = find(tree, node => node.type === "button" && ["跨季度对照", "逐季明细"].includes(elementText(node)));
    assert.deepEqual(controls.map(button => button.props["aria-pressed"]), [true, false]);
    assert.equal(find(tree, node => typeof node.type === "function" && node.type.name === "ActionTable" && node.props.exportData).length, 0,
      "only the displayed cross-quarter table owns copy and export");
    if (step === 3) {
      const renewal = quarter.props.metrics.find(column => column.key === "renewal");
      assert.equal(renewal.value(rows[1]), "-");
      assert.equal(renewal.render(rows[1]), "-");
    }
  }
  assert.equal(JSON.stringify(rows), before);
});

test("FY27 quarterly filters are retained in horizontal reading and never synthesize absent rows", () => {
  const rows = [line(7), line(7, { season: "秋季", revenue: -12.123456 })];
  const filters = { season: "秋季", track: "", campus: "", product: "", grade: "", subject: "" };
  const [quarter] = find(pageTree(rows, 4, filters), node => typeof node.type === "function" && node.type.name === "QuarterlyBusinessTable");
  assert.deepEqual(quarter.props.rows, [rows[1]]);
  assert.equal(quarter.props.metrics.find(column => column.key === "revenue").value(rows[1]), -12.123456,
    "formatting never replaces the original numeric precision");
});

test("FY27 annual targets precede the explicitly local scope and hidden quarter is not an active control", () => {
  const rows = [line(1), line(2, { campus: "中关村", season: "秋季", revenue: -20.5 })];
  const before = JSON.stringify(rows), filters = { season: "秋季", track: "", campus: "西直门", product: "", grade: "", subject: "" };
  const tree = pageTree(rows, 1, filters);
  const order = find(tree, node => node.type === "h2" || typeof node.type === "function" && node.type.name === "BudgetScopeBar");
  assert.match(elementText(order[0]), /全年目标与定版结转收入.*全部预算/);
  const [scope] = find(tree, node => typeof node.type === "function" && node.type.name === "BudgetScopeBar");
  assert.equal(order[1], scope, "whole-year figures must not sit beneath controls that do not affect them");
  assert.equal(scope.props.count, 1);
  assert.deepEqual(scope.props.dimensions.map(item => item.key), ["track", "campus", "product", "grade", "subject"]);
  assert.equal(scope.props.filters.season, "秋季", "hidden quarter state is retained for later quarterly pages");
  const scopeTree = scope.type(scope.props);
  assert.equal(find(scopeTree, node => node.type === "button" && node.props["aria-label"]?.startsWith("清除季度")).length, 0);
  const [quarterly] = find(pageTree(rows, 3, filters), node => typeof node.type === "function" && node.type.name === "BudgetScopeBar");
  assert.equal(quarterly.props.dimensions[0].key, "season");
  assert.deepEqual(quarterly.props.dimensions[0].options, ["暑假", "秋季", "寒假", "春季"]);
  assert.equal(quarterly.props.count, 0, "an empty intersection must not widen to another campus or quarter");
  assert.equal(JSON.stringify(rows), before);
});
