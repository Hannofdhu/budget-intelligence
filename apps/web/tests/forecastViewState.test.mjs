import { test } from "node:test";
import assert from "node:assert/strict";
import { hasForecastFilterState, readForecastViewFilters, writeForecastViewFilters } from "../src/forecastViewState.ts";

test("old forecast links retain open-quarter default while explicit empty scope and single-value links restore exactly", () => {
  assert.equal(readForecastViewFilters("?archive=fy27&forecast_version=saved-p16", "秋季").season, "秋季");
  assert.equal(hasForecastFilterState("?archive=fy27"), false);
  assert.equal(readForecastViewFilters("?bf_scope=1", "秋季").season, "");
  assert.equal(readForecastViewFilters("?bf_season=", "秋季").season, "");
  assert.deepEqual(readForecastViewFilters("?bf_season=暑假&bf_campus=西直门", "秋季"),
    { season: "暑假", track: "", campus: "西直门", product: "", grade: "", subject: "" });
});

test("forecast scope uses repeated exact members and never splits commas, drops unknown values or widens an empty intersection", () => {
  const filters = readForecastViewFilters("?bf_season=暑假&bf_season=秋季&bf_season=暑假&bf_campus=校区,A&bf_campus=未知校区&bf_subject=数学&bf_subject=英语", "秋季");
  assert.deepEqual(filters.season, ["暑假", "秋季"]);
  assert.deepEqual(filters.campus, ["校区,A", "未知校区"]);
  assert.deepEqual(filters.subject, ["数学", "英语"]);
  assert.deepEqual(readForecastViewFilters(writeForecastViewFilters("", filters), "秋季"), filters);
});

test("forecast filter writes preserve exact version, archive, view state and foreign parameters, and clear only owned scope", () => {
  const old = "?archive=fy27&forecast_version=p16&baView=details&external=one&external=two&bf_legacy=keep&bf_campus=西直门";
  const filters = readForecastViewFilters("?bf_scope=1&bf_campus=西直门&bf_campus=中关村&bf_season=暑假&bf_season=秋季");
  const written = new URLSearchParams(writeForecastViewFilters(old, filters));
  assert.equal(written.get("forecast_version"), "p16"); assert.equal(written.get("archive"), "fy27"); assert.equal(written.get("baView"), "details");
  assert.deepEqual(written.getAll("external"), ["one", "two"]); assert.equal(written.get("bf_legacy"), "keep");
  assert.deepEqual(written.getAll("bf_campus"), ["西直门", "中关村"]); assert.deepEqual(written.getAll("bf_season"), ["暑假", "秋季"]);
  const clear = writeForecastViewFilters(written.toString(), readForecastViewFilters("?bf_scope=1"));
  const cleared = new URLSearchParams(clear);
  assert.equal(cleared.get("forecast_version"), "p16"); assert.equal(cleared.get("bf_scope"), "1");
  assert.equal(cleared.has("bf_campus"), false); assert.equal(cleared.has("bf_season"), false);
  assert.deepEqual(readForecastViewFilters(clear, "秋季"), { season: "", track: "", campus: "", product: "", grade: "", subject: "" });
});
