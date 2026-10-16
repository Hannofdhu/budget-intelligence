import { test } from "node:test";
import assert from "node:assert/strict";
import { versionLabel, versionCodeLabel, versionTimestamp, versionStatusLabel, versionUpdateLabel, versionFileStem } from "../src/budgetVersionDisplay.ts";
import { budgetVersionExportAttributes, readVersionExportContext, withVersionCopyContext, withVersionExportContext } from "../src/budgetVersionExport.ts";

test("business labels keep the original ordinal without inventing a version for archives or malformed codes", () => {
  assert.equal(versionLabel(20), "第20版"); assert.equal(versionCodeLabel("N12"), "第12版"); assert.equal(versionCodeLabel("R3"), "第3版");
  for (const value of [null, undefined, 0, -1, 1.5, NaN, Infinity, "12"]) assert.equal(versionLabel(value), "版本序号未记录");
  assert.equal(versionCodeLabel("FY27 已定版"), "FY27 已定版"); assert.equal(versionCodeLabel("bad-P20"), "bad-P20");
});

test("save time is explicit Beijing time and never inferred from a cutoff day or the computer timezone", () => {
  assert.equal(versionTimestamp("2026-10-16T04:07:54Z"), "2026-10-16 12:07");
  assert.equal(versionTimestamp("2026-10-16T04:07:54+00:00", true), "2026-10-16 12:07:54");
  assert.equal(versionTimestamp("2026-10-16T20:00:00Z"), "2026-10-17 04:00");
  for (const value of [null, undefined, "", "bad", "2026-10-15", "2026-10-15T12:00:00", "2026-02-30T12:00:00Z"]) assert.equal(versionTimestamp(value), "未记录");
});

test("business status and generation are separate and missing records stay unknown", () => {
  assert.equal(versionStatusLabel("DRAFT"), "草稿"); assert.equal(versionStatusLabel("CONFIRMED"), "已确认");
  assert.equal(versionStatusLabel("PUBLISHED"), "已定版"); assert.equal(versionStatusLabel("READONLY"), "状态未记录");
  assert.equal(versionUpdateLabel(null, "AUTOMATIC"), "自动更新"); assert.equal(versionUpdateLabel(" 调整入口 ", "MANUAL"), "调整入口");
  assert.equal(versionUpdateLabel(null, null), "更新说明未记录");
});

const context = () => ({ fiscalYear: 2029, object: "预算编制", sequence: 12, createdAt: "2026-10-16T04:07:54Z", id: "exact-n12", asOf: "2026-10-15", preview: false });
test("filenames derive the module year and saved timestamp, not the data cutoff, and mark previews", () => {
  assert.equal(versionFileStem(context()), "FY29-预算编制-第12版-2026-10-16_1207");
  assert.match(versionFileStem({ ...context(), preview: true }), /-未保存预览$/);
  assert.doesNotMatch(versionFileStem({ ...context(), sequence: undefined, createdAt: null }), /第1版|2026-10-15/);
});

test("optional nearest DOM context is read-only, validates shape, and does not widen or mutate source rows", () => {
  const attrs = budgetVersionExportAttributes(context());
  const element = { closest(selector) { assert.equal(selector, "[data-budget-export-context]"); return { getAttribute(name) { return attrs[name]; } }; } };
  const value = readVersionExportContext(element); assert.deepEqual(value, context());
  assert.equal(readVersionExportContext({ closest() { return null; } }), null);
  assert.equal(readVersionExportContext({ closest() { return { getAttribute() { return "{broken"; } }; } }), null);
  const original = { columns: [{ key: "revenue", label: "结转收入", format: "money" }], rows: [{ revenue: -123.456789 }], filename: "当前表格" };
  const before = JSON.stringify(original), exported = withVersionExportContext(original, value);
  assert.equal(JSON.stringify(original), before); assert.equal(exported.rows.length, 1); assert.equal(exported.rows[0].revenue, -123.456789);
  assert.equal(exported.rows[0]._budget_version_id, "exact-n12"); assert.equal(exported.rows[0]._budget_as_of, "2026-10-15");
  assert.equal(exported.rows[0]._budget_saved_at, "2026-10-16 12:07"); assert.equal(exported.rows[0]._budget_version, "第12版");
  assert.equal(withVersionExportContext(exported, value).columns.length, exported.columns.length);
  assert.equal(withVersionExportContext(exported, value).filename, exported.filename);
  assert.equal(withVersionExportContext(original, null), original);
});

test("unsaved input transfers identify their saved basis without claiming a new saved budget", () => {
  const result = withVersionExportContext({ columns: [{ key: "total", label: "总人次" }], rows: [{ total: 10000 }], filename: "入口" }, { ...context(), preview: true });
  assert.equal(result.rows[0]._budget_version, "未保存预览（基于第12版）"); assert.match(result.filename, /未保存预览/);
});

test("explicit paged and quarterly copy grids preserve every original cell and add the same exact source metadata", () => {
  const rows = [["校区", "暑假", "秋季"], ["校区甲", "-123.456", "0"], ["校区乙", "未记录", "10"]], before = JSON.stringify(rows);
  const source = { ...context(), sourceId: "forecast-17", sourceLabel: "FY27全年预测 · 第17版" };
  const copied = withVersionCopyContext(rows, source);
  assert.equal(JSON.stringify(rows), before); assert.equal(copied.length, rows.length);
  copied.forEach((row, index) => assert.deepEqual(row.slice(0, 3), rows[index]));
  assert.equal(copied[0].at(-1), "精确来源标识"); assert.equal(copied[1].at(-1), "forecast-17");
  assert.ok(copied[1].includes("exact-n12")); assert.ok(copied[1].includes("FY27全年预测 · 第17版"));
  assert.equal(withVersionCopyContext(rows, null), rows); assert.deepEqual(withVersionCopyContext([], source), []);
});
