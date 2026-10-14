import { test } from "node:test";
import assert from "node:assert/strict";
import { arrangeQuarterlyBusiness, quarterlyBusinessCopyValue, quarterlyBusinessExport } from "../src/quarterlyBusiness.ts";

const business = [{ key: "campus", label: "校区", value: row => row.campus }];
const metrics = [{ key: "renewal", label: "续班率", format: "rate", value: row => row.renewal },
  { key: "students", label: "人次", format: "count", value: row => row.students }];
const key = row => row.id, season = row => row.season;

test("quarterly reading preserves exact businesses, source objects and quarter ordering without calculating", () => {
  const rows = [
    { id: "first", campus: "同名校区", season: "春季", renewal: 0, students: -1.234567 },
    { id: "first", campus: "同名校区", season: "秋季", renewal: "-", students: null },
    { id: "second", campus: "同名校区", season: "秋季", renewal: "0.712345678", students: "12.345678" },
  ];
  const before = JSON.stringify(rows), arranged = arrangeQuarterlyBusiness(rows, key, season);
  assert.deepEqual(arranged.seasons, ["暑假", "秋季", "寒假", "春季"]);
  assert.equal(arranged.groups.length, 2);
  assert.equal(arranged.duplicateCount, 0);
  assert.equal(arranged.groups[0].bySeason.get("春季"), rows[0]);
  assert.equal(arranged.groups[0].bySeason.get("秋季"), rows[1]);
  assert.equal(arranged.groups[0].bySeason.has("暑假"), false);
  const transfer = quarterlyBusinessExport(arranged.groups, arranged.seasons, business, metrics);
  assert.equal(transfer.rows[0]["quarter-0-0"], null, "absent source quarters remain blank, not zero");
  assert.equal(transfer.rows[0]["quarter-3-0"], 0, "a real zero remains zero");
  assert.equal(transfer.rows[0]["quarter-1-0"], "-", "source dash remains a source dash");
  assert.equal(transfer.rows[0]["quarter-3-1"], -1.234567);
  assert.equal(transfer.rows[1]["quarter-1-0"], "0.712345678", "numeric API strings are untouched until the existing XLSX exporter handles them");
  assert.equal(transfer.columns.find(column => column.key === "quarter-1-0").format, "rate");
  assert.equal(transfer.columns.find(column => column.key === "quarter-1-1").format, "count");
  assert.equal(JSON.stringify(rows), before);
});

test("duplicate same-quarter exact keys preserve every line separately and never guess cross-quarter pairing", () => {
  const rows = [{ id: "ambiguous", campus: "西直门", season: "暑假", students: 10 },
    { id: "ambiguous", campus: "西直门", season: "暑假", students: 20 },
    { id: "ambiguous", campus: "西直门", season: "秋季", students: 30 },
    { id: "safe", campus: "西直门", season: "暑假", students: 5 },
    { id: "safe", campus: "西直门", season: "秋季", students: 6 }];
  const arranged = arrangeQuarterlyBusiness(rows, key, season);
  assert.equal(arranged.duplicateCount, 1);
  assert.equal(arranged.groups.length, 4);
  assert.deepEqual(arranged.groups.slice(0, 3).map(group => [...group.bySeason.values()]), rows.slice(0, 3).map(row => [row]));
  assert.equal(arranged.groups.slice(0, 3).every(group => group.duplicate), true);
  assert.equal(arranged.groups[3].duplicate, false);
  assert.equal(arranged.groups[3].bySeason.size, 2);
  assert.equal(new Set(arranged.groups.map(group => group.key)).size, 4);
  assert.deepEqual(quarterlyBusinessExport(arranged.groups, arranged.seasons, business, metrics).rows.map(row => row["quarter-0-1"]), [10, 20, null, 5]);
});

test("quarter arrangement preserves nonstandard seasons and empty exact scopes", () => {
  const arranged = arrangeQuarterlyBusiness([{ id: "one", campus: "原校区", season: "特别季", students: 0 }], key, season);
  assert.equal(arranged.seasons.at(-1), "特别季");
  assert.equal(arranged.groups[0].bySeason.get("特别季").students, 0);
  const empty = arrangeQuarterlyBusiness([], key, season);
  assert.deepEqual(empty.groups, []);
  assert.deepEqual(quarterlyBusinessExport(empty.groups, empty.seasons, business, metrics).rows, []);
});

test("copy distinguishes missing, source dash, real zero, negative and exact raw counts", () => {
  assert.equal(quarterlyBusinessCopyValue(undefined, metrics[1]), "");
  assert.equal(quarterlyBusinessCopyValue({ students: null }, metrics[1]), "");
  assert.equal(quarterlyBusinessCopyValue({ students: "-" }, metrics[1]), "-");
  assert.equal(quarterlyBusinessCopyValue({ students: 0 }, metrics[1]), "0");
  assert.equal(quarterlyBusinessCopyValue({ students: -12.345678 }, metrics[1]), "-12.345678");
  assert.equal(quarterlyBusinessCopyValue({ renewal: .65 }, metrics[0]), "65%");
});
