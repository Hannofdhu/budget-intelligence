import { test } from "node:test";
import assert from "node:assert/strict";
import { expandTableCells, tableClipboardText, tableExportData, copyTableText, tableCopyRows } from "../src/tableTransfer.ts";
import { budgetWorkbook } from "../src/budgetExport.ts";

test("table transfer expands row groups and merged headings into a rectangular grid", () => {
  assert.deepEqual(expandTableCells([
    [{ value: "校区", rowSpan: 2 }, { value: "人次", colSpan: 2 }],
    [{ value: "老生" }, { value: "新生" }],
    [{ value: "西直门", rowSpan: 2 }, { value: "1,234" }, { value: "0" }],
    [{ value: "-3" }, { value: "—" }],
  ]), [["校区", "人次", "人次"], ["校区", "老生", "新生"], ["西直门", "1,234", "0"], ["西直门", "-3", "—"]]);
});
test("clipboard preserves negatives, escapes tabs and prevents text formulas", () => {
  assert.equal(tableClipboardText([["=-1+2", "-3", "a\tb", 'a"b', "@SUM(A1)"]]), "'=-1+2\t-3\t\"a\tb\"\t\"a\"\"b\"\t'@SUM(A1)");
});
test("rendered-table export keeps numeric types, percentages, missing values and textual identifiers", async () => {
  const { columns, rows } = tableExportData(["校区", "收入（万元）", "续班率", "人次", "版本编号"], [["西直门", "1,234.56", "67.12%", "0", "0012"], ["中关村", "-3.25", "0%", "—", "0013"]]);
  assert.equal(rows[0]["1"], 1234.56); assert.equal(rows[0]["2"], .6712); assert.equal(rows[0]["3"], 0);
  assert.equal(rows[1]["1"], -3.25); assert.equal(rows[1]["3"], null); assert.equal(rows[0]["4"], "0012");
  const workbook = await budgetWorkbook(columns, rows);
  assert.equal(workbook.worksheets[0].getCell("B2").type, 2);
  assert.equal(workbook.worksheets[0].getCell("C2").numFmt, "0.00%");
  assert.equal(workbook.worksheets[0].getCell("E2").type, 3);
});
test("clipboard success requires the actual browser clipboard operation to succeed", async () => {
  const previous = Object.getOwnPropertyDescriptor(globalThis, "navigator");
  let copied;
  Object.defineProperty(globalThis, "navigator", { configurable: true, value: { clipboard: { writeText: async value => { copied = value; } } } });
  try { await copyTableText([["校区", "人次"], ["西直门", "0"]]); assert.equal(copied, "校区\t人次\n西直门\t0"); }
  finally { if (previous) Object.defineProperty(globalThis, "navigator", previous); else delete globalThis.navigator; }
});

test("paged transfers include all filtered rows and business revenue remains numeric", () => {
  const columns = [{ key: "campus", label: "校区" }, { key: "revenue", label: "业务收入（元）", format: "money" }, { key: "rate", label: "续班率", format: "rate" }];
  const rows = Array.from({ length: 53 }, (_, index) => ({ campus: `校区${index}`, revenue: index, rate: .5 }));
  const copied = tableCopyRows(columns, rows);
  assert.equal(copied.length, 54); assert.equal(copied[53][0], "校区52"); assert.equal(copied[1][2], "50%");
  assert.equal(tableExportData(["业务收入（元）", "版本编号"], [["123.45", "0012"]]).rows[0]["0"], 123.45);
});
