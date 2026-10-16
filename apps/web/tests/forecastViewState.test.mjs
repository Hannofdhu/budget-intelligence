import { test } from "node:test";
import assert from "node:assert/strict";
import { hasForecastFilterState, readForecastViewFilters, writeForecastViewFilters, readForecastGroupDimensions, writeForecastGroupDimensions } from "../src/forecastViewState.ts";

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

test("forecast grouping defaults for old links and ignores invalid values while preserving selected order", () => {
  for (const search of ["", "?archive=fy27", "?fg_row=", "?fg_row=invalid&fg_row=track,grade", "?fg_row=__proto__"])
    assert.deepEqual(readForecastGroupDimensions(search), ["track"]);
  assert.deepEqual(readForecastGroupDimensions("?fg_row=season&fg_row=grade&fg_row=season&fg_row=invalid&fg_row=campus"), ["season", "grade", "campus"]);
  const six = ["subject", "grade", "season", "track", "product", "campus"];
  assert.deepEqual(readForecastGroupDimensions(six.map(group => `fg_row=${group}`).join("&")), six);
  assert.deepEqual(readForecastGroupDimensions(writeForecastGroupDimensions("", [])), ["track"]);
  assert.deepEqual(readForecastGroupDimensions(writeForecastGroupDimensions("", ["grade", "track", "grade", "invalid"])), ["grade", "track"]);
});

test("group writes own no data scope fields, and clearing filters retains grouping and exact saved version", () => {
  const original = "?archive=fy27&forecast_version=exact-p19&bf_scope=1&bf_campus=校区,A&bf_campus=未知校区&bf_season=秋季&bf_season=寒假&bf_legacy=keep&fg_row=track&external=one&external=two";
  const before = new URLSearchParams(original), written = writeForecastGroupDimensions(original, ["campus", "product", "season"]);
  const after = new URLSearchParams(written);
  assert.deepEqual(after.getAll("fg_row"), ["campus", "product", "season"]);
  for (const key of ["archive", "forecast_version", "bf_scope", "bf_campus", "bf_season", "bf_legacy", "external"])
    assert.deepEqual(after.getAll(key), before.getAll(key), key);
  assert.deepEqual(readForecastViewFilters(written), readForecastViewFilters(original), "changing grouping never widens the data range");
  const cleared = writeForecastViewFilters(written, readForecastViewFilters("?bf_scope=1"));
  assert.deepEqual(readForecastGroupDimensions(cleared), ["campus", "product", "season"]);
  assert.equal(new URLSearchParams(cleared).get("forecast_version"), "exact-p19");
  assert.equal(hasForecastFilterState("?fg_row=grade"), false, "grouping alone does not suppress the old open-quarter default");
  assert.equal(readForecastViewFilters("?fg_row=grade", "秋季").season, "秋季");
});

test("refresh and browser history can restore independent grouping and filter states from each URL", () => {
  const start = "?archive=fy27&forecast_version=original&bf_scope=1&bf_season=秋季&fg_row=track";
  const groupChanged = writeForecastGroupDimensions(start, ["track", "grade", "subject"]);
  const filterChanged = writeForecastViewFilters(groupChanged, { ...readForecastViewFilters(groupChanged), season: ["秋季", "寒假"] });
  const otherSnapshot = new URLSearchParams(filterChanged); otherSnapshot.set("forecast_version", "older");
  for (const search of [filterChanged, groupChanged, start, groupChanged, filterChanged, otherSnapshot.toString()]) {
    const refreshed = new URLSearchParams(search).toString();
    assert.deepEqual(readForecastGroupDimensions(refreshed), readForecastGroupDimensions(search));
    assert.deepEqual(readForecastViewFilters(refreshed), readForecastViewFilters(search));
  }
  assert.deepEqual(readForecastGroupDimensions(otherSnapshot.toString()), ["track", "grade", "subject"]);
  assert.deepEqual(readForecastViewFilters(otherSnapshot.toString()).season, ["秋季", "寒假"]);
});
