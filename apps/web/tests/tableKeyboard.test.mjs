import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import fs from "node:fs";
import { build } from "esbuild";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { clampTableSelection, moveTableSelection, renderedCellGrid, scrollTableViewportToEdge, selectedTableRows, tableSelectionBounds } from "../src/tableKeyboard.ts";

const at = (row, column) => ({ row, column });
const range = (row, column, endRow = row, endColumn = column) => ({ anchor: at(row, column), focus: at(endRow, endColumn) });
test("table coordinates move within bounds and preserve a Shift anchor without mutating inputs", () => {
  const source = range(1, 1), before = structuredClone(source);
  assert.deepEqual(moveTableSelection(source, "ArrowDown", 3, 4), range(2, 1));
  assert.deepEqual(moveTableSelection(source, "ArrowRight", 3, 4, true), range(1, 1, 1, 2));
  assert.deepEqual(moveTableSelection(range(0, 0), "ArrowLeft", 3, 4), range(0, 0));
  assert.deepEqual(moveTableSelection(range(2, 3), "ArrowDown", 3, 4), range(2, 3));
  assert.deepEqual(moveTableSelection(range(2, 3), "Home", 3, 4), range(2, 0));
  assert.deepEqual(moveTableSelection(range(1, 1), "End", 3, 4), range(1, 3));
  assert.deepEqual(moveTableSelection(source, "Home", 3, 4, true, true), range(1, 1, 0, 0));
  assert.deepEqual(moveTableSelection(source, "End", 3, 4, false, true), range(2, 3));
  assert.deepEqual(source, before); assert.equal(moveTableSelection(source, "End", 0, 0), null);
});
test("rectangular copy handles reversed selection, ragged rows, missing cells and raw precision", () => {
  const grid = [["校区", "学费", "折扣", "空值"], ["西直门", "1234.567890", "0", ""], ["中关村", "-0.000001", "—"], ["0012", "=SUM(A1)", "0.12345678901234567890"]];
  const before = structuredClone(grid);
  assert.deepEqual(selectedTableRows(grid, range(3, 3, 1, 1)), [["1234.567890", "0", ""], ["-0.000001", "—", ""], ["=SUM(A1)", "0.12345678901234567890", ""]]);
  assert.deepEqual(grid, before); assert.deepEqual(selectedTableRows(grid, null), []);
  assert.deepEqual(selectedTableRows([], range(1, 1)), []);
  assert.deepEqual(clampTableSelection(range(-3, NaN, 999, Infinity), 3, 4), range(0, 0, 2, 0));
  assert.deepEqual(tableSelectionBounds(range(4, 7, 2, 1)), { top: 2, bottom: 4, left: 1, right: 7 });
});
test("merged quarter headings and row groups retain their original single DOM owner", () => {
  const campus = { value: "校区", rowSpan: 2, colSpan: 1 }, summer = { value: "暑假", rowSpan: 1, colSpan: 2 };
  const old = { value: "老生", rowSpan: 1, colSpan: 1 }, fresh = { value: "新生", rowSpan: 1, colSpan: 1 };
  const grid = renderedCellGrid([[campus, summer], [old, fresh]]);
  assert.equal(grid[0][0], campus); assert.equal(grid[1][0], campus);
  assert.equal(grid[0][1], summer); assert.equal(grid[0][2], summer);
  assert.equal(grid[1][1], old); assert.equal(grid[1][2], fresh);
  assert.equal(campus.rowSpan, 2); assert.equal(summer.colSpan, 2);
});

test("table edge scroll resets only the closest explicit table viewport and never the document scroll", () => {
  const body = { scrollTop: 420, scrollLeft: 17, style: { overflowX: "auto", overflowY: "auto" } };
  const outer = { parentElement: body, scrollTop: 230, scrollLeft: 30, style: { overflowX: "auto", overflowY: "auto" }, scrollHeight: 2000, clientHeight: 600, scrollWidth: 1600, clientWidth: 900 };
  const viewport = { parentElement: outer, scrollTop: 540, scrollLeft: 290, style: { overflowX: "auto", overflowY: "auto" }, scrollHeight: 1800, clientHeight: 400, scrollWidth: 1600, clientWidth: 800 };
  const wrapper = { parentElement: viewport, style: { overflowX: "visible", overflowY: "visible" } };
  const table = { parentElement: wrapper, ownerDocument: { body, documentElement: {}, defaultView: { getComputedStyle: node => node.style } } };
  assert.equal(scrollTableViewportToEdge(table, "start"), true);
  assert.equal(viewport.scrollTop, 0); assert.equal(viewport.scrollLeft, 0);
  assert.equal(outer.scrollTop, 230); assert.equal(outer.scrollLeft, 30); assert.equal(body.scrollTop, 420);
  assert.equal(scrollTableViewportToEdge(table, "end"), true);
  assert.equal(viewport.scrollTop, 1400); assert.equal(viewport.scrollLeft, 800);
  table.parentElement = body;
  assert.equal(scrollTableViewportToEdge(table, "start"), false); assert.equal(body.scrollTop, 420); assert.equal(body.scrollLeft, 17);
});

const require = createRequire(import.meta.url);
const built = await build({ entryPoints: [fileURLToPath(new URL("../src/ActionTable.tsx", import.meta.url))], bundle: true, write: false,
  platform: "node", format: "cjs", jsx: "automatic", external: ["react", "exceljs"], loader: { ".css": "empty" }, logLevel: "silent" });
const find = (node, predicate) => Array.isArray(node) ? node.flatMap(child => find(child, predicate)) : !node || typeof node !== "object" ? []
  : [...(predicate(node) ? [node] : []), ...find(node.props?.children, predicate)];
const text = node => Array.isArray(node) ? node.map(text).join("") : node && typeof node === "object" ? text(node.props?.children) : node == null || typeof node === "boolean" ? "" : String(node);
function harness(extra = {}) {
  const states = [], refs = [], effects = [], dependencies = [];
  let stateIndex = 0, refIndex = 0, effectIndex = 0, idIndex = 0, props = { children: "stable cells", keyboardNavigation: true, ...extra };
  const mockedReact = { ...require("react"), useId() { return `table-id-${idIndex++}`; }, useState(initial) { const i = stateIndex++; if (!(i in states)) states[i] = initial;
    return [states[i], next => { states[i] = typeof next === "function" ? next(states[i]) : next; }]; },
    useRef(initial) { return refs[refIndex++] ??= { current: initial }; },
    useEffect(callback, deps) { const i = effectIndex++; if (!dependencies[i] || deps.some((value, index) => !Object.is(value, dependencies[i][index]))) {
      dependencies[i] = deps; effects.push(callback);
    } } };
  const module = { exports: {} };
  new Function("require", "module", "exports", built.outputFiles[0].text)(name => name === "react" ? mockedReact : require(name), module, module.exports);
  const h = { states, refs, textSelection: null, editable: false, focused: 0, scrolled: 0,
    render(next = {}) { props = { ...props, ...next }; stateIndex = refIndex = effectIndex = idIndex = 0; h.tree = module.exports.ActionTable(props); return h.tree; },
    flush() { effects.splice(0).forEach(run => run()); },
    node() { return find(h.render(), node => node.type === "table")[0]; },
    button(label) { return find(h.render(), node => node.type === "button" && text(node) === label)[0]; },
    key(key, options = {}) { const event = { key, target: h.table, currentTarget: h.table, defaultPrevented: false, nativeEvent: {},
      shiftKey: false, altKey: false, ctrlKey: false, metaKey: false, preventDefault() { this.defaultPrevented = true; }, ...options };
      h.node().props.onKeyDown(event); return event; },
    copy(options = {}) { const data = [];
      const event = { target: h.table, currentTarget: h.table, defaultPrevented: false, clipboardData: { setData(type, value) { data.push([type, value]); } },
        preventDefault() { this.defaultPrevented = true; }, ...options };
      h.node().props.onCopy(event); return { event, data }; },
    click(cell, options = {}) { const event = { target: cell, currentTarget: h.table, defaultPrevented: false, detail: 1, shiftKey: false,
      altKey: false, ctrlKey: false, metaKey: false, ...options }; h.node().props.onClick(event); return event; },
    focus(target) { h.table.ownerDocument.activeElement = target ?? h.table; h.node().props.onFocus({ target: target ?? h.table, currentTarget: h.table, defaultPrevented: false }); },
  };
  const cell = value => ({ innerText: value, rowSpan: 1, colSpan: 1, attrs: {}, querySelectorAll() { return []; },
    setAttribute(key, value) { this.attrs[key] = value; }, removeAttribute(key) { delete this.attrs[key]; }, scrollIntoView() { h.scrolled++; },
    closest(selector) { if (selector === "table") return h.table; if (selector === "th,td") return this; return null; } });
  h.cells = [["季度", "收入", "人次"], ["暑假", "1234.567890", "0"], ["秋季", "-0.000001", "—"]].map(row => row.map(cell));
  h.table = { rows: h.cells.map(cells => ({ cells })), tHead: { rows: [{ cells: h.cells[0] }] }, parentElement: null,
    ownerDocument: { defaultView: { getSelection: () => h.textSelection } }, closest: () => h.editable ? {} : null,
    focus() { h.focused++; h.table.ownerDocument.activeElement = h.table; } };
  h.node().props.ref(h.table); h.flush(); return h;
}
const settle = () => new Promise(resolve => setImmediate(resolve));
async function clipboard(run) {
  const previous = Object.getOwnPropertyDescriptor(globalThis, "navigator"), writes = [];
  Object.defineProperty(globalThis, "navigator", { configurable: true, value: { clipboard: { writeText: async value => { writes.push(value); } } } });
  try { await run(writes); } finally { if (previous) Object.defineProperty(globalThis, "navigator", previous); else delete globalThis.navigator; }
}

test("keyboard navigation is explicit opt-in and native table/copy/export semantics remain", () => {
  const module = { exports: {} };
  new Function("require", "module", "exports", built.outputFiles[0].text)(require, module, module.exports);
  const ordinary = renderToStaticMarkup(createElement(module.exports.ActionTable, { tableLabel: "普通表" }, createElement("tbody", null)));
  assert.doesNotMatch(ordinary, /tabindex|data-table-keyboard|快捷键/); assert.match(ordinary, /一键复制/); assert.match(ordinary, /导出当前表格/);
  const enabled = renderToStaticMarkup(createElement(module.exports.ActionTable, { keyboardNavigation: true, tableLabel: "季度招生", "aria-describedby": "scope" }, createElement("tbody", null)));
  assert.match(enabled, /tabindex="0"/); assert.match(enabled, /aria-label="季度招生"/); assert.match(enabled, /aria-describedby="scope /);
  assert.match(enabled, /快捷键/); assert.doesNotMatch(enabled, /role="grid"|<button[^>]*>复制所选<\/button>/);
  const h = harness({ keyboardNavigation: false }); h.focus(); assert.equal(h.key("ArrowRight").defaultPrevented, false);
});
test("focus, arrows, Shift ranges and Escape expose exact range without changing table text", async () => {
  await clipboard(async writes => {
    const h = harness(), before = h.cells.map(row => row.map(cell => cell.innerText));
    h.focus(); assert.equal(h.cells[0][0].attrs["data-table-active"], "true");
    h.key("ArrowDown"); h.key("ArrowRight"); h.key("ArrowDown", { shiftKey: true }); h.key("ArrowRight", { shiftKey: true });
    assert.match(text(h.render()), /第 3 行，第 3 列 · 已选 4 格/); assert.ok(h.button("复制所选"));
    assert.equal(h.key("c", { metaKey: true }).defaultPrevented, true); await settle();
    assert.deepEqual(writes, ["1234.567890\t0\n-0.000001\t—"]); assert.match(text(h.render()), /已复制所选 2 行 × 2 列/);
    assert.deepEqual(h.cells.map(row => row.map(cell => cell.innerText)), before); assert.ok(h.scrolled > 0);
    assert.equal(h.key("Escape").defaultPrevented, true); assert.equal(h.button("复制所选"), undefined);
    assert.equal(h.key("c", { ctrlKey: true }).defaultPrevented, false); assert.ok(h.cells.flat().every(cell => !Object.keys(cell.attrs).length));
  });
});
test("editable controls, buttons, composition, text selections and browser shortcuts are never hijacked", async () => {
  await clipboard(async writes => {
    const h = harness(); h.focus();
    for (const target of ["INPUT", "TEXTAREA", "SELECT", "BUTTON", "A", "SUMMARY"]) {
      assert.equal(h.key("ArrowRight", { target: { tagName: target } }).defaultPrevented, false, target);
      assert.equal(h.key("c", { ctrlKey: true, target: { tagName: target } }).defaultPrevented, false, target);
    }
    assert.equal(h.key("ArrowDown", { nativeEvent: { isComposing: true } }).defaultPrevented, false);
    for (const options of [{ ctrlKey: true, shiftKey: true }, { metaKey: true, altKey: true }, { altKey: true }]) {
      assert.equal(h.key("c", options).defaultPrevented, false);
    }
    assert.equal(h.key("a", { ctrlKey: true }).defaultPrevented, false);
    assert.equal(h.key("f", { metaKey: true }).defaultPrevented, false);
    assert.equal(h.key("ArrowRight", { ctrlKey: true }).defaultPrevented, false);
    h.textSelection = { isCollapsed: false, toString: () => "浏览器选择的文字" };
    assert.equal(h.key("c", { ctrlKey: true }).defaultPrevented, false); assert.equal(h.key("ArrowRight").defaultPrevented, false);
    h.textSelection = null; h.editable = true;
    assert.equal(h.key("ArrowRight").defaultPrevented, false); assert.equal(h.key("c", { metaKey: true }).defaultPrevented, false);
    await settle(); assert.equal(writes.length, 0);
  });
});
test("click and Shift click select cells but links, controls and native text selection stay usable", () => {
  const h = harness(); h.click(h.cells[1][1]); h.click(h.cells[2][2], { shiftKey: true });
  assert.match(text(h.render()), /已选 4 格/); assert.equal(h.focused, 2);
  const nested = { closest(selector) { return selector.includes("button") ? {} : h.cells[0][0]; } };
  h.click(nested); assert.match(text(h.render()), /已选 4 格/); assert.equal(h.focused, 2);
  h.click(h.cells[0][0], { detail: 2 }); assert.equal(h.focused, 2);
  h.textSelection = { isCollapsed: false, toString: () => "原生文字" }; h.click(h.cells[0][0]); assert.equal(h.focused, 2);
});
test("paging/filtering clears visual selection and stale content cannot be copied before effects flush", async () => {
  await clipboard(async writes => {
    const h = harness(); h.click(h.cells[1][1]); h.cells[1][1].innerText = "另一页的收入";
    h.key("c", { ctrlKey: true }); await settle(); assert.equal(writes.length, 0); assert.match(text(h.render()), /表格已变化，请重新选择/);
    h.click(h.cells[1][1]); h.render({ children: "new visible rows" }); h.flush();
    assert.equal(h.button("复制所选"), undefined); assert.ok(h.cells.flat().every(cell => !cell.attrs["data-table-selected"]));
    assert.equal(h.key("c", { ctrlKey: true }).defaultPrevented, false);
  });
});
test("full-range copy remains independent of local selection and retains original raw export values", async () => {
  await clipboard(async writes => {
    const h = harness({ exportData: { columns: [{ key: "id", label: "编号" }, { key: "price", label: "学费（元）", format: "money" }],
      rows: Array.from({ length: 53 }, (_, index) => ({ id: `00${index}`, price: "1234.567890" })), filename: "完整明细" } });
    h.click(h.cells[1][1]); h.button("一键复制").props.onClick(); await settle();
    assert.equal(writes[0].split("\n").length, 54); assert.match(writes[0], /0052\t1234\.567890/);
    assert.ok(h.button("导出明细"));
  });
});
test("clipboard rejection is surfaced and an existing handler can veto keyboard selection", async () => {
  const previous = Object.getOwnPropertyDescriptor(globalThis, "navigator");
  Object.defineProperty(globalThis, "navigator", { configurable: true, value: { clipboard: { writeText: async () => { throw new Error("剪贴板权限被拒绝"); } } } });
  try {
    const h = harness(); h.focus(); h.key("c", { ctrlKey: true }); await settle();
    assert.match(text(h.render()), /剪贴板权限被拒绝/); assert.doesNotMatch(text(h.render()), /已复制所选/);
    let calls = 0; const veto = harness({ onKeyDown(event) { calls++; event.preventDefault(); } }); veto.focus();
    const before = text(veto.render()); veto.key("ArrowDown"); assert.equal(calls, 1); assert.equal(text(veto.render()), before);
  } finally { if (previous) Object.defineProperty(globalThis, "navigator", previous); else delete globalThis.navigator; }
});

test("native copy fallback handles system/menu commands only for the focused table's valid visible selection", () => {
  const h = harness(); h.focus(); h.key("ArrowDown"); h.key("ArrowRight"); h.key("ArrowDown", { shiftKey: true }); h.key("ArrowRight", { shiftKey: true });
  const { event, data } = h.copy();
  assert.equal(event.defaultPrevented, true);
  assert.deepEqual(data, [["text/plain", "1234.567890\t0\n-0.000001\t—"]]);
  assert.match(text(h.render()), /已复制所选 2 行 × 2 列/);
  h.table.ownerDocument.activeElement = {};
  assert.equal(h.copy().event.defaultPrevented, false);
  h.focus(); h.cells[1][1].innerText = "已切换的分页数据";
  assert.equal(h.copy().data.length, 0); assert.match(text(h.render()), /表格已变化，请重新选择/);
  assert.equal(h.button("复制所选"), undefined);
});

test("native copy fallback respects input/button targets, text selection, editable tables and caller veto", () => {
  const h = harness(); h.focus();
  for (const tagName of ["INPUT", "TEXTAREA", "SELECT", "BUTTON", "A", "SUMMARY"]) {
    const result = h.copy({ target: { tagName } }); assert.equal(result.event.defaultPrevented, false); assert.deepEqual(result.data, []);
  }
  h.textSelection = { isCollapsed: false, toString: () => "用户原生选择的文字" };
  assert.equal(h.copy().event.defaultPrevented, false); assert.equal(h.copy().data.length, 0);
  h.textSelection = null; h.editable = true; assert.equal(h.copy().event.defaultPrevented, false);
  const disabled = harness({ keyboardNavigation: false }); disabled.focus(); assert.equal(disabled.copy().event.defaultPrevented, false);
  let calls = 0; const veto = harness({ onCopy(event) { calls++; event.preventDefault(); } }); veto.focus();
  assert.deepEqual(veto.copy().data, []); assert.equal(calls, 1); assert.doesNotMatch(text(veto.render()), /已复制所选/);
});

test("native copy fallback never claims success for unavailable or rejecting clipboard data", () => {
  const h = harness(); h.focus();
  const missing = h.copy({ clipboardData: null }); assert.equal(missing.event.defaultPrevented, true);
  assert.match(text(h.render()), /浏览器无法直接复制/); assert.doesNotMatch(text(h.render()), /已复制所选/);
  const denied = h.copy({ clipboardData: { setData() { throw new Error("系统剪贴板拒绝写入"); } } });
  assert.equal(denied.event.defaultPrevented, true); assert.match(text(h.render()), /系统剪贴板拒绝写入/); assert.doesNotMatch(text(h.render()), /已复制所选/);
});

test("Ctrl/Cmd Home and End move the sticky table body to its edges while retaining exact selection", () => {
  const h = harness(), body = { scrollTop: 900, scrollLeft: 0 };
  const viewport = { parentElement: body, scrollTop: 400, scrollLeft: 300, scrollHeight: 1800, clientHeight: 500, scrollWidth: 1600, clientWidth: 700,
    style: { overflowX: "auto", overflowY: "auto" } };
  h.table.parentElement = viewport; h.table.ownerDocument.body = body;
  h.table.ownerDocument.defaultView.getComputedStyle = node => node.style;
  h.focus();
  // scrollIntoView sees a sticky heading/footer in place and cannot move body scroll itself.
  assert.equal(h.key("Home", { metaKey: true }).defaultPrevented, true);
  assert.equal(viewport.scrollTop, 0); assert.equal(viewport.scrollLeft, 0);
  assert.equal(h.cells[0][0].attrs["data-table-active"], "true");
  assert.match(text(h.render()), /第 1 行，第 1 列 · 已选 1 格/);
  assert.equal(h.key("End", { ctrlKey: true, shiftKey: true }).defaultPrevented, true);
  assert.equal(viewport.scrollTop, 1300); assert.equal(viewport.scrollLeft, 900);
  assert.equal(h.cells[2][2].attrs["data-table-active"], "true");
  assert.match(text(h.render()), /第 3 行，第 3 列 · 已选 9 格/);
  assert.equal(h.copy().data[0][1].split("\n").length, 3);
  assert.equal(body.scrollTop, 900, "page scrolling is never reset by a table edge shortcut");
});

function edgeScrollProbe(h) {
  const events = [], body = { scrollTop: 900, scrollLeft: 17 };
  let top = 400, left = 300;
  const viewport = { parentElement: body, scrollHeight: 1800, clientHeight: 500, scrollWidth: 1600, clientWidth: 700,
    style: { overflowX: "auto", overflowY: "auto" },
    get scrollTop() { return top; }, set scrollTop(value) { top = value; events.push("edge-top"); },
    get scrollLeft() { return left; }, set scrollLeft(value) { left = value; events.push("edge-left"); } };
  h.table.parentElement = viewport; h.table.ownerDocument.body = body;
  h.table.ownerDocument.defaultView.getComputedStyle = node => node.style;
  for (const cell of h.cells.flat()) cell.scrollIntoView = () => { events.push("reveal"); h.scrolled++; };
  return { events, viewport, body };
}

test("owned SKU viewports settle before revealing edge cells while legacy tables retain the original order and Shift anchor", () => {
  for (const scrollClassName of [undefined, "budget-data-section__scroll"]) for (const command of ["ctrlKey", "metaKey"]) {
    const h = harness({ scrollClassName }), { events, viewport, body } = edgeScrollProbe(h);
    const expectedOrder = scrollClassName ? ["edge-top", "edge-left", "reveal"] : ["reveal", "edge-top", "edge-left"];
    for (const key of ["Home", "End"]) {
      h.click(h.cells[1][1]); events.length = 0;
      assert.equal(h.key(key, { [command]: true }).defaultPrevented, true);
      assert.deepEqual(events, expectedOrder, `${scrollClassName ?? "legacy"} ${command} ${key} without Shift`);
      assert.match(text(h.render()), /已选 1 格/);
      assert.equal(h.cells[key === "Home" ? 0 : 2][key === "Home" ? 0 : 2].attrs["data-table-active"], "true");
    }
    h.click(h.cells[1][1]); events.length = 0;
    assert.equal(h.key("End", { [command]: true, shiftKey: true }).defaultPrevented, true);
    assert.deepEqual(events, expectedOrder, `${scrollClassName ?? "legacy"} ${command} End`);
    assert.equal(viewport.scrollTop, 1300); assert.equal(viewport.scrollLeft, 900);
    assert.equal(h.cells[2][2].attrs["data-table-active"], "true");
    assert.match(text(h.render()), /第 3 行，第 3 列 · 已选 4 格/);
    assert.equal(h.copy().data[0][1], "1234.567890\t0\n-0.000001\t—", "Shift keeps the middle-cell anchor and original precision");
    events.length = 0;
    assert.equal(h.key("Home", { [command]: true, shiftKey: true }).defaultPrevented, true);
    assert.deepEqual(events, expectedOrder, `${scrollClassName ?? "legacy"} ${command} Home`);
    assert.equal(viewport.scrollTop, 0); assert.equal(viewport.scrollLeft, 0);
    assert.equal(h.cells[0][0].attrs["data-table-active"], "true");
    assert.match(text(h.render()), /第 1 行，第 1 列 · 已选 4 格/);
    assert.equal(h.copy().data[0][1], "季度\t收入\n暑假\t1234.567890", "reversing direction still extends from the same original anchor");
    assert.equal(body.scrollTop, 900); assert.equal(body.scrollLeft, 17);
  }
});

test("empty old and owned table viewports do not consume edge shortcuts or attempt scrolling", () => {
  for (const scrollClassName of [undefined, "budget-data-section__scroll"]) {
    const h = harness({ scrollClassName }), { events, viewport, body } = edgeScrollProbe(h);
    h.table.rows = []; h.table.tHead = { rows: [] }; h.focus();
    for (const command of ["ctrlKey", "metaKey"]) for (const key of ["Home", "End"]) {
      assert.equal(h.key(key, { [command]: true, shiftKey: true }).defaultPrevented, false);
    }
    assert.deepEqual(events, []);
    assert.equal(viewport.scrollTop, 400); assert.equal(viewport.scrollLeft, 300);
    assert.equal(body.scrollTop, 900); assert.equal(body.scrollLeft, 17);
    assert.equal(h.button("复制所选"), undefined);
  }
});

test("shared read-only tables use one keyboard entry while the existing pivot and forecast editor retain their own controls", () => {
  const source = file => fs.readFileSync(new URL(`../src/${file}`, import.meta.url), "utf8");
  const analysis = source("BudgetAnalysisView.tsx");
  assert.match(analysis, /<ActionTable keyboardNavigation className="ba-table ba-detail-table" scrollClassName="ba-table-wrap"/);
  assert.match(analysis, /className="ba-table-wrap" ref=\{observeBudgetTableHeader\} tabIndex=\{0\} aria-label="预算透视表，可横向滚动"/);
  assert.match(analysis, /<table className="ba-table ba-pivot-table" ref=\{tableRef\}>/);
  assert.equal((analysis.match(/<ActionTable keyboardNavigation/g) ?? []).length, 1, "the existing pivot range is not replaced by another selection system");
  const people = source("BudgetPeopleBoard.tsx");
  for (const label of ["季度招生预算表", "赛道年级跨季续班率表"]) {
    assert.ok(people.includes(`<ActionTable keyboardNavigation scrollClassName="ba-table-wrap" scrollLabel="${label}，可横向滚动" aria-label="${label}"`));
    assert.ok(!people.includes(`tabIndex={0} aria-label="${label}"`));
  }
  const quarter = source("BudgetQuarterTable.tsx");
  assert.match(quarter, /scrollClassName="ba-table-wrap" scrollRef=\{observeBudgetTableHeader\} scrollLabel=\{`\$\{tableLabel\}，可横向滚动`\}/);
  assert.match(quarter, /<ActionTable keyboardNavigation className=\{`ba-table \$\{tableClassName\}`\} tableLabel=\{tableLabel\}/);
  const forecast = source("ForecastPage.tsx");
  for (const label of ["季度达成明细表", "维度达成表", "预测明细表", "校区预测人数"]) {
    assert.ok(forecast.includes(`className="forecast-table-region" aria-label="${label}"><ActionTable keyboardNavigation`));
    const region = forecast.slice(forecast.indexOf(`className="forecast-table-region" aria-label="${label}"`));
    assert.match(region.slice(0, region.indexOf("<thead>")), /scrollClassName="forecast-table-wrap"/);
    assert.match(region.slice(0, region.indexOf("<thead>")), new RegExp(`aria-label="${label}"`));
    assert.ok(!forecast.includes(`tabIndex={0} aria-label="${label}"`));
  }
  assert.match(forecast, /<ActionTable exportData=\{parameterTransfer\} keyboardNavigation tableLabel=\{legacyTrial \? "试用假设明细" : "独立业务参数"\}/);
  assert.match(forecast, /aria-label=\{legacyTrial \? "试用假设明细表" : "独立业务参数表"\} className="forecast-sku-parameter-table" scrollClassName="forecast-table-wrap"/);
  assert.match(forecast, /<input[^>]*onChange|<select[^>]*onChange/, "the forecast parameter table retains its existing editable controls");
});
