import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { budgetWorkbook } from "../src/budgetExport.ts";
import { historicalExportColumns } from "../src/historicalBudgetExport.ts";

test("XLSX keeps actual numbers, numeric API decimals, blank, zero, negative and literal strings distinct", async () => {
  const columns = [
    { key: "id", label: "校区" }, { key: "amount", label: "金额", format: "money" },
    { key: "count", label: "人次", format: "count" }, { key: "rate", label: "比例", format: "rate" },
  ];
  const rows = [
    { id: "001", amount: 12.3456, count: 0, rate: 0.9 },
    { id: "=SUM(A1:A2)", amount: "-0.01", count: "13", rate: null },
    { id: "010", amount: null, count: "-", rate: "" },
  ];
  const before = JSON.stringify(rows);
  const workbook = await budgetWorkbook(columns, rows);
  const bytes = await workbook.xlsx.writeBuffer();
  const { default: ExcelJS } = await import("exceljs");
  const reopened = new ExcelJS.Workbook();
  await reopened.xlsx.load(bytes);
  const sheet = reopened.worksheets[0];
  assert.equal(sheet.getCell("A2").value, "001");
  assert.equal(sheet.getCell("A3").value, "=SUM(A1:A2)");
  for (const address of ["B2", "C2", "D2", "B3", "C3"]) assert.equal(sheet.getCell(address).type, ExcelJS.ValueType.Number);
  assert.equal(sheet.getCell("B2").value, 12.3456); // number format must not round underlying values
  assert.equal(sheet.getCell("B3").value, -0.01);
  assert.equal(sheet.getCell("C2").value, 0);
  assert.equal(sheet.getCell("C4").value, "-");
  assert.equal(sheet.getCell("D3").value, null);
  assert.equal(sheet.getCell("D4").value, null);
  assert.equal(sheet.getCell("B2").numFmt, "#,##0.00");
  assert.equal(sheet.getCell("D2").numFmt, "0.00%");
  assert.equal(JSON.stringify(rows), before);
});

test("all five exports exclude spreadsheet locations and store amounts/counts/rates as numbers", async () => {
  const row = { track: "智慧空间", campus: "西直门", product: "高端小班", grade: "七年级", subject: "数学", season: "暑假", entry: "是",
    annual: 113004618.59, renewal: 0.7, share: 0.25, old: 7, new: 3, total: 10, minimum: 10, price: 3000, discount: 0.9,
    recognition: 0.75, business: 27000, current: 19000, carryout: 6000, revenue: 20000 };
  for (let step = 1; step <= 5; step++) {
    const columns = historicalExportColumns(step);
    assert.ok(columns.every(column => !/Excel|来源|依据|行号|小数/.test(column.label)));
    const workbook = await budgetWorkbook(columns, [row]);
    columns.forEach((column, index) => {
      assert.equal(workbook.worksheets[0].getCell(2, index + 1).value, row[column.key]);
      if (column.format) assert.equal(typeof workbook.worksheets[0].getCell(2, index + 1).value, "number");
    });
  }
});

const sourcePath = new URL("../../../output/fy27-shadow/source-cells.json", import.meta.url);
test("952 FY27 quarterly rows retain their saved revenue and cross-year carry reconciliation in XLSX", { skip: !fs.existsSync(sourcePath) }, async () => {
  const original = JSON.parse(fs.readFileSync(sourcePath, "utf8"));
  const blocks = ["AW AX AY", "BN BO BP", "CE CF CG", "CV CW CX"];
  const rows = original.sheets["滚动表"].rows.filter(row => row.row >= 2 && row.cells.A?.value != null)
    .flatMap(row => blocks.map((block, i) => {
      const [current, revenue, carryout] = block.split(" ");
      return { campus: row.cells.G.value, season: ["暑假", "秋季", "寒假", "春季"][i], current: row.cells[current].value,
        revenue: row.cells[revenue].value, carryout: row.cells[carryout].value };
    }));
  assert.equal(rows.length, 952);
  const columns = historicalExportColumns(4).filter(column => ["campus", "season", "current", "revenue", "carryout"].includes(column.key));
  const workbook = await budgetWorkbook(columns, rows);
  const { default: ExcelJS } = await import("exceljs");
  const reopened = new ExcelJS.Workbook();
  await reopened.xlsx.load(await workbook.xlsx.writeBuffer());
  assert.equal(reopened.worksheets[0].rowCount, 953);
  const totals = {};
  for (const field of ["current", "revenue", "carryout"]) {
    const index = columns.findIndex(column => column.key === field) + 1;
    totals[field] = 0;
    rows.forEach((row, i) => {
      const cell = reopened.worksheets[0].getCell(i + 2, index);
      assert.equal(cell.type, ExcelJS.ValueType.Number);
      assert.equal(cell.value, row[field]);
      totals[field] += cell.value;
    });
  }
  assert.equal(totals.revenue.toFixed(2), "113004618.59");
  assert.equal((totals.current + totals.carryout).toFixed(2), "112480004.34");
});

test("historical budget UI exposes no workbook-specific source controls", () => {
  const component = fs.readFileSync(new URL("../src/HistoricalBudgetPage.tsx", import.meta.url), "utf8");
  assert.doesNotMatch(component, /查看依据|来源与口径|参数来源|Excel 已定版|参数表|滚动表/);
});

test("normal results and confirmation views hide technical provenance controls without changing checks", () => {
  for (const filename of ["WorkspacePage.tsx", "BudgetResultsPage.tsx", "PublishedBudgetRecord.tsx"]) {
    const component = fs.readFileSync(new URL(`../src/${filename}`, import.meta.url), "utf8");
    assert.doesNotMatch(component, /查看依据|来源与口径|参数来源|查看来源详情与口径|<summary>结果来源/);
  }
  const record = fs.readFileSync(new URL("../src/PublishedBudgetRecord.tsx", import.meta.url), "utf8");
  assert.match(record, /fetchPublicationReadiness/);
  assert.match(record, /readiness\.checks\.map/);
});
