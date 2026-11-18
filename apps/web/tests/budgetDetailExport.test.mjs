import { test } from "node:test";
import assert from "node:assert/strict";
import { skuDetailColumns, skuDetailValues } from "../src/budgetDetailExport.ts";
import { budgetWorkbook } from "../src/budgetExport.ts";
import { budgetVersionExportAttributes, readVersionExportContext, withVersionExportContext } from "../src/budgetVersionExport.ts";

test("analytical SKU exports preserve source fields containing separators and write all rows as independent Excel columns", async () => {
  const originals = Array.from({ length: 53 }, (_, index) => ({
    campus: `百学汇/联合校区${index}`, track: "智慧空间·跨学段", product: "普通 / 白金试点",
    grade: "八年级", subject: "数学\n综合实践", revenue: index === 0 ? 0 : index === 1 ? null : -12345.678901,
    students: index === 1 ? null : index, note: index === 1 ? "待核对" : "已知",
  }));
  const saved = JSON.stringify(originals);
  const columns = [...skuDetailColumns, { key: "revenue", label: "结转收入（元）", format: "money" },
    { key: "students", label: "人次", format: "count" }, { key: "note", label: "数据状态" }];
  const rows = originals.map(row => ({ ...skuDetailValues(row), revenue: row.revenue, students: row.students, note: row.note }));
  const book = await budgetWorkbook(columns, rows), bytes = await book.xlsx.writeBuffer();
  const { default: ExcelJS } = await import("exceljs"); const read = new ExcelJS.Workbook(); await read.xlsx.load(bytes);
  const sheet = read.worksheets[0];
  assert.equal(sheet.rowCount, 54); assert.equal(sheet.columnCount, 8);
  assert.deepEqual(sheet.getRow(1).values.slice(1), ["校区", "赛道", "产品", "年级", "科目", "结转收入（元）", "人次", "数据状态"]);
  assert.equal(sheet.getCell("A2").value, originals[0].campus); assert.equal(sheet.getCell("C2").value, originals[0].product);
  assert.equal(sheet.getCell("E2").value, "数学\n综合实践"); assert.equal(sheet.getCell("F2").value, 0);
  assert.equal(sheet.getCell("F3").value, null); assert.equal(sheet.getCell("G3").value, null);
  assert.equal(sheet.getCell("H3").value, "待核对"); assert.equal(sheet.getCell("F4").value, -12345.678901);
  assert.equal(sheet.getCell("F4").type, ExcelJS.ValueType.Number); assert.equal(sheet.getCell("G54").value, 52);
  assert.equal(JSON.stringify(originals), saved);
});

test("unknown identity dimensions remain independent null fields instead of guessed display-string splits", () => {
  assert.deepEqual(skuDetailValues({ campus: "校区/赛道/产品", subject: "数学", combined: "某年级 / 科目" }), {
    campus: "校区/赛道/产品", track: null, product: null, grade: null, subject: "数学",
  });
});

test("version exports separate fiscal year, module, preview state and typed basis fields without parsing descriptive labels", () => {
  const context = { fiscalYear: 2028, object: "预算编制", sequence: 30, preview: true, id: "budget-30",
    sourceId: "forecast-28", sourceLabel: "不可解析的自定义说明 / 保留", sourceFiscalYear: 2027,
    sourceObject: "全年预测", sourceSequence: 28, sourceDataset: "数据集/含斜线", sourcePartition: "2026-10-25" };
  const attr = budgetVersionExportAttributes(context);
  const fromContainer = readVersionExportContext({ closest: () => ({ getAttribute: () => attr["data-budget-export-context"] }) });
  const data = { columns: [{ key: "revenue", label: "结转收入（元）", format: "money" }], rows: [{ revenue: 0 }], filename: "明细" };
  const before = JSON.stringify(data), output = withVersionExportContext(data, fromContainer), row = output.rows[0];
  assert.equal(row._budget_fiscal_year, 2028); assert.equal(row._budget_source, "预算编制");
  assert.equal(row._budget_version, "第30版"); assert.equal(row._budget_result_state, "未保存预览");
  assert.equal(row._budget_basis_year, 2027); assert.equal(row._budget_basis_object, "全年预测");
  assert.equal(row._budget_basis_sequence, 28); assert.equal(row._budget_basis_dataset, "数据集/含斜线");
  assert.equal(row._budget_basis, context.sourceLabel); assert.equal(row.revenue, 0);
  assert.equal(withVersionExportContext(output, fromContainer).columns.length, output.columns.length);
  const legacy = withVersionExportContext(data, { ...context, sourceFiscalYear: null, sourceObject: null, sourceSequence: null });
  assert.equal(legacy.rows[0]._budget_basis_year, undefined); assert.equal(legacy.rows[0]._budget_basis_object, undefined);
  assert.equal(legacy.rows[0]._budget_basis_sequence, undefined); assert.equal(JSON.stringify(data), before);
});
