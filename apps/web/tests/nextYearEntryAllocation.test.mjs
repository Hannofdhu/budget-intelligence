import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";
import * as React from "react";

const source = fs.readFileSync(new URL("../src/NextYearBudgetPage.tsx", import.meta.url), "utf8");
const bundle = await build({ entryPoints: [fileURLToPath(new URL("../src/NextYearBudgetPage.tsx", import.meta.url))], bundle: true, write: false,
  platform: "node", format: "cjs", jsx: "automatic", external: ["react", "react-dom", "exceljs"], loader: { ".css": "empty" }, logLevel: "silent" });
const module = { exports: {} };
new Function("require", "module", "exports", bundle.outputFiles[0].text)(createRequire(import.meta.url), module, module.exports);
const { nextYearAllocatedEntryStudents: allocated, nextYearTableData } = module.exports;

test("entry allocations come from the displayed result, preserve zero, and never mutate it", () => {
  const saved = { entry_details: [{ combination_id: "english", students: 22 }, { combination_id: "chinese", students: 0 }],
    rows: [{ combination_id: "english", season: "暑假", entry: true, new_students: 99 }] };
  const before = JSON.stringify(saved);
  assert.equal(allocated(saved, "english"), 22);
  assert.equal(allocated(saved, "chinese"), 0);
  assert.equal(allocated(saved, "non-entry"), null);
  assert.equal(JSON.stringify(saved), before);
  const preview = { ...saved, entry_details: [{ combination_id: "english", students: 30 }] };
  assert.equal(allocated(preview, "english"), 30);
  assert.equal(allocated(saved, "english"), 22);
});

test("legacy snapshots use summer entry new-student results, not old, total or later quarters", () => {
  const legacy = { rows: [
    { combination_id: "entry", season: "秋季", entry: true, new_students: 88 },
    { combination_id: "entry", season: "暑假", entry: true, new_students: 12, old_students: 5, total_students: 17 },
    { combination_id: "non-entry", season: "暑假", entry: false, new_students: 6 },
    { combination_id: "unknown", season: "暑假", entry: true, new_students: null },
    { combination_id: "zero", season: "暑假", entry: true, new_students: 0 },
  ] };
  assert.equal(allocated(legacy, "entry"), 12);
  assert.equal(allocated(legacy, "non-entry"), null);
  assert.equal(allocated(legacy, "unknown"), null);
  assert.equal(allocated(legacy, "zero"), 0);
  assert.equal(allocated(legacy, "missing"), null);
  assert.equal(allocated({ ...legacy, entry_details: [] }, "entry"), null);
});

test("shared copy/export data retain numeric allocated counts including zero across all filtered rows", () => {
  const rows = Array.from({ length: 30 }, (_, index) => ({ id: `sku-${index}` }));
  const calculation = { entry_details: rows.map((row, index) => ({ combination_id: row.id, students: index })), rows: [] };
  const data = nextYearTableData(rows, ["指定人次", "已分配人次"], row => React.createElement(React.Fragment, null,
    React.createElement("td", null, React.createElement("input", { value: "", readOnly: true })),
    React.createElement("td", null, allocated(calculation, row.id))));
  assert.equal(data.length, 30);
  assert.deepEqual(data[0], { c0: "", c1: 0 });
  assert.deepEqual(data[29], { c0: "", c1: 29 });
});

test("step two includes the read-only allocation column in the shared table and explains stale edits", () => {
  const table = source.slice(source.indexOf("<details><summary>SKU入口设置与分配"), source.indexOf('<BudgetDataSection id="ny-entry-campuses"'));
  assert.match(table, /"指定人次", "已分配人次", "分配方式"/);
  assert.match(table, /const allocated = nextYearAllocatedEntryStudents\(calculation, row\.id\)/);
  assert.match(table, /entryAllocationText\(allocated, effectiveNextYearEntry\(row\) \|\| allocated !== null\)/);
  assert.match(table, /入口修改后显示“待重算”/);
  assert.match(source.slice(source.indexOf("const nextYearNumericHeaders"), source.indexOf("function PagedTable")), /"已分配人次"/);
});
