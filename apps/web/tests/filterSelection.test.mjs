import { test } from "node:test";
import assert from "node:assert/strict";
import { filterValues, normalizeFilter, hasFilter, filterLabel, matchesFilter, toggleFilterValue,
  readFilterParam, writeFilterParam } from "../src/filterSelection.ts";

test("filter selection cleans invalid values and duplicates without splitting legitimate comma labels", () => {
  const values = ["西直门", "A,B", "西直门", "", 0, null, false, {}, ["nested"], "中关村"];
  const before = JSON.stringify(values);
  assert.deepEqual(filterValues(values), ["西直门", "A,B", "中关村"]);
  assert.deepEqual(normalizeFilter(values), ["西直门", "A,B", "中关村"]);
  assert.equal(normalizeFilter(["西直门", "西直门", null]), "西直门");
  assert.equal(normalizeFilter("A,B"), "A,B");
  for (const invalid of [undefined, null, 12, true, {}, [], [null, "", false, 1]]) {
    assert.deepEqual(filterValues(invalid), []); assert.equal(normalizeFilter(invalid), ""); assert.equal(hasFilter(invalid), false);
  }
  assert.equal(filterLabel(["西直门", "中关村"]), "西直门、中关村");
  assert.equal(JSON.stringify(values), before);
});

test("one dimension matches exact OR values while empty selections mean unrestricted", () => {
  assert.equal(matchesFilter("西直门", ["西直门", "中关村"]), true);
  assert.equal(matchesFilter("中关村", ["西直门", "中关村"]), true);
  assert.equal(matchesFilter("西直门南", ["西直门", "中关村"]), false);
  assert.equal(matchesFilter("A,B", "A,B"), true); assert.equal(matchesFilter("A", "A,B"), false);
  assert.equal(matchesFilter(undefined, ["A"]), false); assert.equal(matchesFilter(undefined, ["A"], true), true);
  for (const unrestricted of [undefined, "", []]) {
    assert.equal(matchesFilter("任意值", unrestricted), true); assert.equal(matchesFilter(undefined, unrestricted), true);
  }
});

test("toggle keeps one old string, several values and all-clear interoperable without mutating selection", () => {
  assert.equal(toggleFilterValue("", "暑假"), "暑假");
  assert.deepEqual(toggleFilterValue("暑假", "秋季"), ["暑假", "秋季"]);
  const selection = ["暑假", "秋季"], before = [...selection];
  assert.equal(toggleFilterValue(selection, "暑假"), "秋季");
  assert.equal(toggleFilterValue("暑假", "暑假"), "");
  assert.equal(toggleFilterValue(selection, ""), "");
  assert.deepEqual(selection, before);
});

test("URL filter values use repeated parameters, preserve unrelated parameters and never treat commas as separators", () => {
  const params = new URLSearchParams("revision=exact-id&year=2028&nyFilters=1&custom=a%2Cb&bf_campus=old");
  writeFilterParam(params, "bf_campus", ["A,B", "中关村", "A,B"]);
  assert.deepEqual(params.getAll("bf_campus"), ["A,B", "中关村"]);
  assert.deepEqual(readFilterParam(params, "bf_campus"), ["A,B", "中关村"]);
  assert.equal(params.get("revision"), "exact-id"); assert.equal(params.get("year"), "2028");
  assert.equal(params.get("nyFilters"), "1"); assert.equal(params.get("custom"), "a,b");
  writeFilterParam(params, "bf_campus", "A,B"); assert.equal(readFilterParam(params, "bf_campus"), "A,B");
  writeFilterParam(params, "bf_campus", []); assert.equal(params.has("bf_campus"), false);
});
