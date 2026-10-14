// M1 presentation contracts only: fixtures stay in memory and never call budget APIs.
import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

const require = createRequire(import.meta.url);
const compiled = await build({ entryPoints: [fileURLToPath(new URL("../src/BudgetDataSection.tsx", import.meta.url))], bundle: true,
  write: false, platform: "node", format: "cjs", jsx: "automatic", external: ["react"], loader: { ".css": "empty" }, logLevel: "silent" });
const loaded = { exports: {} };
new Function("require", "module", "exports", compiled.outputFiles[0].text)(require, loaded, loaded.exports);
const { BudgetDataSection } = loaded.exports;
const find = (node, predicate) => Array.isArray(node) ? node.flatMap(child => find(child, predicate)) : !node || typeof node !== "object" ? []
  : [...(predicate(node) ? [node] : []), ...find(node.props?.children, predicate)];
const text = node => Array.isArray(node) ? node.map(text).join("") : node && typeof node === "object" ? text(node.props?.children)
  : node == null || typeof node === "boolean" ? "" : String(node);

test("SKU sections share one named semantic section and a peer-level heading with the precise count unit", () => {
  const children = createElement("p", null, "保留原有表格内容");
  for (const count of [0, 1, 233, 1008]) {
    const tree = BudgetDataSection({ id: "sku-sample", title: "SKU", count, children });
    assert.equal(tree.type, "section");
    assert.equal(tree.props.id, "sku-sample");
    assert.equal(tree.props["aria-labelledby"], "sku-sample-title");
    const headings = find(tree, node => typeof node.type === "string" && /^h[1-6]$/.test(node.type));
    assert.equal(headings.length, 1);
    assert.equal(headings[0].type, "h2");
    assert.equal(headings[0].props.id, "sku-sample-title");
    assert.equal(text(headings[0]), "SKU");
    assert.equal(find(tree, node => node.type === "span" && text(node) === `${count.toLocaleString("zh-CN")} 条 SKU`).length, 1);
    assert.equal(find(tree, node => node === children).length, 1, "the owner supplies content without a second rendering pipeline");
  }
});

test("section chrome preserves its owner content and actions without creating business or transfer operations", () => {
  let clicks = 0;
  const action = createElement("button", { type: "button", onClick() { clicks++; } }, "新增校区 / 产品");
  const children = createElement("table", { "aria-label": "原有 SKU 表格" }, createElement("tbody", null,
    createElement("tr", null, createElement("td", null, "-12.123456"), createElement("td", null, "0"))));
  const props = Object.freeze({ id: "ny-sku", title: "SKU", count: 2, actions: action, children });
  const tree = BudgetDataSection(props);
  const buttons = find(tree, node => node.type === "button");
  assert.deepEqual(buttons, [action], "the shell adds no second save, preview, copy or export button");
  assert.equal(clicks, 0, "rendering never invokes an owner action");
  buttons[0].props.onClick();
  assert.equal(clicks, 1);
  assert.equal(find(tree, node => node === children).length, 1);
  assert.equal(find(tree, node => node.type === "table").length, 1);
  assert.match(text(tree), /-12\.1234560/, "chrome never normalizes or recalculates owner values");
  assert.equal(props.children, children);
  assert.equal(props.actions, action);
});

test("read-only and empty SKU sections omit empty action containers and keep original section attributes", () => {
  const tree = BudgetDataSection({ id: "history-details", title: "SKU", count: 0, "aria-label": "预算明细",
    children: createElement("p", null, "当前筛选下没有明细") });
  assert.equal(tree.props["aria-label"], "预算明细");
  assert.equal(find(tree, node => node.type === "button" || node.type === "input" || node.type === "select").length, 0,
    "read-only chrome must not infer an editor from count or title");
  assert.equal(find(tree, node => ["div", "span"].includes(node.type) && text(node) === "").length, 0,
    "no blank action wrapper remains when no actions were supplied");
  const html = renderToStaticMarkup(createElement(BudgetDataSection, { id: "history-details", title: "SKU", count: 0,
    "aria-label": "预算明细" }, createElement("p", null, "当前筛选下没有明细")));
  assert.match(html, /aria-labelledby="history-details-title"/);
  assert.match(html, /<h2[^>]*id="history-details-title"/);
  assert.match(html, /0 条 SKU/);
  assert.match(html, /当前筛选下没有明细/);
  assert.doesNotMatch(html, /<button|<input|<select|条业务明细|条季度明细/);
});

const tableBuild = await build({ entryPoints: [fileURLToPath(new URL("../src/ActionTable.tsx", import.meta.url))], bundle: true,
  write: false, platform: "node", format: "cjs", jsx: "automatic", external: ["react", "exceljs"], loader: { ".css": "empty" }, logLevel: "silent" });
function actionTable(props) {
  let id = 0;
  const loaded = { exports: {} };
  const customRequire = name => name === "react" ? { ...require(name), useRef: current => ({ current }),
    useId: () => `table-${id++}`, useState: initial => [initial, () => {}], useEffect() {} } : require(name);
  new Function("require", "module", "exports", tableBuild.outputFiles[0].text)(customRequire, loaded, loaded.exports);
  return loaded.exports.ActionTable(props);
}

test("table toolbar slots remain outside the scroll viewport without forwarding presentation props to table", () => {
  const search = createElement("input", { "aria-label": "筛选 SKU", value: "西直门", readOnly: true });
  const children = createElement("tbody", null, createElement("tr", null, createElement("td", null, "1234.567890")));
  const exportData = { filename: "SKU", columns: [{ key: "revenue", label: "结转收入（元）" }], rows: [{ revenue: "1234.567890" }] };
  const before = JSON.stringify(exportData);
  const tree = actionTable({ toolbarStart: search, scrollClassName: "budget-data-scroll", children, exportData,
    keyboardNavigation: true, tableLabel: "SKU", "aria-describedby": "sku-scope" });
  const [toolbar] = find(tree, node => node.type === "div" && node.props.className?.split(" ").includes("table-transfer"));
  const [viewport] = find(tree, node => node.type === "div" && node.props.className?.split(" ").includes("budget-data-scroll"));
  const [table] = find(tree, node => node.type === "table");
  assert.ok(toolbar); assert.ok(viewport); assert.ok(table);
  assert.equal(find(toolbar, node => node === search).length, 1);
  assert.equal(find(viewport, node => node === search).length, 0);
  assert.equal(find(viewport, node => node === table).length, 1);
  assert.equal(find(viewport, node => node.type === "button").length, 0, "search and transfer controls never scroll with rows");
  assert.deepEqual(find(toolbar, node => node.type === "button").map(text), ["一键复制", "导出明细"]);
  assert.equal(table.props.toolbarStart, undefined);
  assert.equal(table.props.scrollClassName, undefined);
  assert.equal(table.props.children, children);
  assert.equal(table.props["aria-label"], "SKU");
  assert.match(table.props["aria-describedby"], /^sku-scope /);
  assert.equal(table.props.tabIndex, 0);
  assert.equal(typeof table.props.onKeyDown, "function");
  assert.equal(typeof table.props.onCopy, "function");
  assert.equal(JSON.stringify(exportData), before, "layout slots must not alter transfer data");
});

test("ordinary ActionTable calls retain the unwrapped table and native non-keyboard behavior", () => {
  const children = createElement("tbody", null);
  const tree = actionTable({ children, tableLabel: "原有表格" });
  const [table] = find(tree, node => node.type === "table");
  assert.ok(table);
  assert.equal(table.props.children, children);
  assert.equal(table.props.tabIndex, undefined);
  assert.equal(table.props["data-table-keyboard"], undefined);
  assert.equal(find(tree, node => node.type === "div" && find(node, child => child === table).length).length, 0,
    "an omitted scroll slot does not change existing table container ownership");
  assert.deepEqual(find(tree, node => node.type === "button").map(text), ["一键复制", "导出当前表格"]);
});
