// Filter-view state only. No live budget, database, BI request or saved version.
import test from "node:test";
import assert from "node:assert/strict";
import { readNextYearFilters, persistNextYearFilters, writeNextYearFilters, validateNextYearFilters } from "../src/nextYearFilters.ts";

const empty = () => ({ season: "", track: "", campus: "", product: "", grade: "", subject: "" });
const filters = (fields = {}) => ({ ...empty(), ...fields });
const storage = (initial = {}) => {
  const values = new Map(Object.entries(initial));
  const calls = [];
  return {
    values, calls,
    getItem(key) { calls.push(["get", key]); return values.get(key) ?? null; },
    setItem(key, value) { calls.push(["set", key, value]); values.set(key, value); },
  };
};
const combinations = () => [
  { track: "智慧空间", campus: "西直门", product: "常规班", grade: "七年级", subject: "数学" },
  { track: "智慧伴学", campus: "中关村南", product: "云帆", grade: "八年级", subject: "英语" },
];

test("saved filter views are isolated by fiscal year and contain no other state", () => {
  const cache = storage();
  const fy28 = filters({ track: "智慧空间", grade: "七年级" });
  const fy29 = filters({ campus: "中关村南", season: "春季" });
  persistNextYearFilters(2028, fy28, cache);
  persistNextYearFilters(2029, fy29, cache);
  assert.deepEqual([...cache.values.keys()], ["ny-filters-2028", "ny-filters-2029"]);
  assert.deepEqual(readNextYearFilters(2028, "", cache), fy28);
  assert.deepEqual(readNextYearFilters(2029, "?year=2029&nyTask=3", cache), fy29);
  assert.deepEqual(readNextYearFilters(2030, "", cache), empty());
});

test("URL filter values take priority over all cached values without reading storage", () => {
  const cache = storage({ "ny-filters-2028": JSON.stringify(filters({ campus: "旧校区", track: "旧赛道" })) });
  const result = readNextYearFilters(2028, "?bf_track=智慧空间&bf_grade=七年级&nyTask=3", cache);
  assert.deepEqual(result, filters({ track: "智慧空间", grade: "七年级" }));
  assert.deepEqual(cache.calls, []);
});

test("a partial URL view is authoritative rather than silently merging cached dimensions", () => {
  const cache = storage({ "ny-filters-2028": JSON.stringify(filters({ season: "暑假", campus: "西直门", product: "常规班" })) });
  assert.deepEqual(readNextYearFilters(2028, "?bf_subject=数学", cache), filters({ subject: "数学" }));
  assert.deepEqual(readNextYearFilters(2028, "?bf_track=", cache), empty());
  assert.deepEqual(cache.calls, []);
});

test("nyFilters=1 explicitly clears an old cached view even with no bf parameters", () => {
  const cache = storage({ "ny-filters-2028": JSON.stringify(filters({ season: "秋季", track: "智慧空间" })) });
  assert.deepEqual(readNextYearFilters(2028, "?nyFilters=1&year=2028&nyTask=5", cache), empty());
  assert.deepEqual(cache.calls, []);
  assert.deepEqual(readNextYearFilters(2028, "?nyFilters=0", cache), filters({ season: "秋季", track: "智慧空间" }));
});

test("URL writing preserves year, task, analysis options and unrelated duplicate parameters", () => {
  const source = "?year=2029&nyTask=3&baView=pivot&baRows=track%2Cgrade&ba_unit=yuan&archive=fy27&tag=one&tag=two&bf_track=旧赛道&bf_subject=旧科目";
  const next = writeNextYearFilters(source, filters({ season: "寒假", track: "智慧空间", product: "云帆" }));
  const params = new URLSearchParams(next);
  assert.equal(params.get("year"), "2029");
  assert.equal(params.get("nyTask"), "3");
  assert.equal(params.get("baView"), "pivot");
  assert.equal(params.get("baRows"), "track,grade");
  assert.equal(params.get("ba_unit"), "yuan");
  assert.equal(params.get("archive"), "fy27");
  assert.deepEqual(params.getAll("tag"), ["one", "two"]);
  assert.equal(params.get("nyFilters"), "1");
  assert.equal(params.get("bf_season"), "寒假");
  assert.equal(params.get("bf_track"), "智慧空间");
  assert.equal(params.get("bf_product"), "云帆");
  assert.equal(params.has("bf_subject"), false);
  assert.equal(source.includes("bf_subject=旧科目"), true);
});

test("clearing filters removes all old dimension parameters and survives a URL round trip", () => {
  const cached = storage({ "ny-filters-2028": JSON.stringify(filters({ season: "秋季", campus: "西直门" })) });
  const next = writeNextYearFilters("?year=2028&nyTask=4&bf_season=秋季&bf_track=智慧空间&bf_campus=西直门&bf_product=云帆&bf_grade=八年级&bf_subject=英语", empty());
  const params = new URLSearchParams(next);
  for (const dimension of Object.keys(empty())) assert.equal(params.has(`bf_${dimension}`), false);
  assert.equal(params.get("nyFilters"), "1");
  assert.equal(params.get("nyTask"), "4");
  assert.deepEqual(readNextYearFilters(2028, next, cached), empty());
  assert.deepEqual(cached.calls, []);
});

test("all six dimensions round trip through a shareable URL, including reserved characters", () => {
  const original = filters({ season: "暑假", track: "智慧空间", campus: "A&B 校区", product: "云帆+实验班", grade: "七/八年级", subject: "数学 = 英语" });
  const next = writeNextYearFilters("?year=2028&nyTask=2", original);
  assert.deepEqual(readNextYearFilters(2028, next, storage()), original);
});

test("missing or corrupt cached JSON falls back to a fresh empty view", () => {
  for (const value of [null, "", "{broken", "undefined", "null", "false", "42", '"text"', "[]"]) {
    const cache = storage(value === null ? {} : { "ny-filters-2028": value });
    assert.deepEqual(readNextYearFilters(2028, "", cache), empty(), `cached value ${value}`);
  }
});

test("cached dimension arrays are accepted while non-string values are discarded without losing valid strings", () => {
  const cache = storage({ "ny-filters-2028": JSON.stringify({ season: null, track: "智慧空间", campus: 0, product: false, grade: ["七年级"], subject: { value: "数学" }, extra: "not a filter" }) });
  assert.deepEqual(readNextYearFilters(2028, "", cache), filters({ track: "智慧空间", grade: "七年级" }));
});

test("persisted values are sanitized without mutating the supplied filter object", () => {
  const original = Object.freeze({ season: "暑假", track: "智慧空间", campus: null, product: 10, grade: false, subject: { name: "数学" }, extra: "ignored" });
  const before = JSON.stringify(original), cache = storage();
  persistNextYearFilters(2028, original, cache);
  assert.deepEqual(JSON.parse(cache.values.get("ny-filters-2028")), filters({ season: "暑假", track: "智慧空间" }));
  assert.equal(JSON.stringify(original), before);
});

test("denied and full storage never block reading or persisting a filter view", () => {
  const denied = { getItem() { throw new Error("SecurityError"); }, setItem() { throw new Error("QuotaExceededError"); } };
  assert.deepEqual(readNextYearFilters(2028, "", denied), empty());
  assert.doesNotThrow(() => persistNextYearFilters(2028, filters({ track: "智慧空间" }), denied));
  assert.deepEqual(readNextYearFilters(2028, "?bf_season=春季", denied), filters({ season: "春季" }));
});

test("an inaccessible browser sessionStorage getter degrades without changing the page", () => {
  const previous = Object.getOwnPropertyDescriptor(globalThis, "sessionStorage");
  Object.defineProperty(globalThis, "sessionStorage", { configurable: true, get() { throw new Error("SecurityError"); } });
  try {
    assert.deepEqual(readNextYearFilters(2028, ""), empty());
    assert.doesNotThrow(() => persistNextYearFilters(2028, filters({ campus: "西直门" })));
    assert.deepEqual(readNextYearFilters(2028, "?nyFilters=1"), empty());
  } finally {
    if (previous) Object.defineProperty(globalThis, "sessionStorage", previous);
    else delete globalThis.sessionStorage;
  }
});

test("validation retains unavailable selections instead of silently broadening the current year scope", () => {
  const original = filters({ season: "秋季", track: "智慧空间", campus: "已关闭校区", product: "云帆", grade: "不存在年级", subject: "数学" });
  assert.deepEqual(validateNextYearFilters(original, combinations()), original);
});

test("valid individual dimensions with an empty intersection are preserved, not auto-cleared", () => {
  const original = filters({ season: "寒假", track: "智慧空间", campus: "中关村南", product: "常规班", grade: "八年级", subject: "数学" });
  assert.equal(combinations().some(row => Object.entries(original).every(([key, value]) => key === "season" || row[key] === value)), false);
  assert.deepEqual(validateNextYearFilters(original, combinations()), original);
});

test("unsupported or temporarily unavailable seasons remain explicit empty reading restrictions", () => {
  for (const season of ["暑假", "秋季", "寒假", "春季"]) assert.deepEqual(validateNextYearFilters(filters({ season }), []), filters({ season }));
  for (const season of ["Q1", "秋续寒", "暑假 ", "2028", ""]) assert.deepEqual(validateNextYearFilters(filters({ season }), []), filters({ season }));
  assert.deepEqual(validateNextYearFilters(filters({ campus: "西直门", track: "智慧空间" }), []), filters({ campus: "西直门", track: "智慧空间" }));
});

test("string zero and punctuation in dimension names are retained as real choices", () => {
  const rows = [{ track: "0", campus: "A&B", product: "", grade: "七/八年级", subject: "数学+英语" }];
  const original = filters({ track: "0", campus: "A&B", grade: "七/八年级", subject: "数学+英语" });
  assert.deepEqual(validateNextYearFilters(original, rows), original);
});

test("reading and validating return independent objects and never mutate caller data", () => {
  const original = Object.freeze(filters({ season: "秋季", track: "智慧空间", campus: "西直门" }));
  const rows = Object.freeze(combinations().map(row => Object.freeze(row)));
  const before = JSON.stringify([original, rows]), cache = storage();
  persistNextYearFilters(2028, original, cache);
  const first = readNextYearFilters(2028, "", cache), second = readNextYearFilters(2028, "", cache);
  first.track = "changed locally";
  assert.equal(second.track, "智慧空间");
  const valid = validateNextYearFilters(original, rows);
  assert.notEqual(valid, original);
  assert.equal(JSON.stringify([original, rows]), before);
  assert.doesNotThrow(() => writeNextYearFilters("?nyTask=1", original));
});

test("view-state helpers neither request data nor trigger a calculation", () => {
  const previousFetch = globalThis.fetch;
  let networkCalls = 0;
  globalThis.fetch = () => { networkCalls++; throw new Error("Filter state must not request data"); };
  try {
    const cache = storage(), original = filters({ season: "春季", track: "智慧伴学" });
    persistNextYearFilters(2028, original, cache);
    const read = readNextYearFilters(2028, "", cache);
    const validated = validateNextYearFilters(read, combinations());
    const search = writeNextYearFilters("?year=2028&nyTask=5", validated);
    assert.deepEqual(readNextYearFilters(2028, search, cache), original);
    assert.equal(networkCalls, 0);
  } finally { globalThis.fetch = previousFetch; }
});

test("multi selections use repeated URL parameters and round trip exact names, never comma-splitting", () => {
  const original = filters({ season: ["秋季", "寒假"], campus: ["A,B / 校区", "A&B"], subject: ["数学", "英语"] });
  const search = writeNextYearFilters("?year=2028&nyTask=4&bf_campus=旧校区&bf_campus=过期", original);
  const params = new URLSearchParams(search);
  assert.deepEqual(params.getAll("bf_campus"), original.campus);
  assert.deepEqual(params.getAll("bf_season"), original.season);
  assert.deepEqual(readNextYearFilters(2028, search), original);
  assert.equal(params.get("nyTask"), "4");
  assert.deepEqual(readNextYearFilters(2028, "?bf_campus=A%2CB&bf_campus=A%2CB&bf_campus=C"), filters({ campus: ["A,B", "C"] }));
});

test("legacy storage and mixed multi arrays normalize without mutating saved or supplied view state", () => {
  const cache = storage({ "ny-filters-2028": JSON.stringify({ season: ["秋季", "秋季", "", null, "寒假"], campus: "西直门", subject: ["数学", 5, false, "英语"] }) });
  const before = cache.values.get("ny-filters-2028");
  const restored = readNextYearFilters(2028, "", cache);
  assert.deepEqual(restored, filters({ season: ["秋季", "寒假"], campus: "西直门", subject: ["数学", "英语"] }));
  assert.equal(cache.values.get("ny-filters-2028"), before);
  persistNextYearFilters(2028, restored, cache);
  restored.season.push("春季");
  assert.deepEqual(readNextYearFilters(2028, "", cache).season, ["秋季", "寒假"]);
});

test("multi validation retains valid alternatives and empty intersections instead of silently broadening", () => {
  const original = filters({ season: ["秋季", "寒假", "过期"], campus: ["西直门", "过期"], subject: ["英语", "数学"], grade: [] });
  const before = JSON.stringify(original);
  assert.deepEqual(validateNextYearFilters(original, combinations()), filters({ season: ["秋季", "寒假", "过期"], campus: ["西直门", "过期"], subject: ["英语", "数学"] }));
  assert.equal(JSON.stringify(original), before);
  const noIntersection = filters({ campus: "西直门", product: "云帆", season: ["秋季", "寒假"] });
  assert.deepEqual(validateNextYearFilters(noIntersection, combinations()), noIntersection);
});
