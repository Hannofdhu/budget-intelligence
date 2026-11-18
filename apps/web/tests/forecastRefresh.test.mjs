import { test } from "node:test";
import assert from "node:assert/strict";
import { defaultForecastStates } from "../src/forecastApi.ts";
import { getForecastRefresh, startForecastRefresh, subscribeForecastRefresh } from "../src/forecastRefresh.ts";

const actor = userId => ({ userId, departmentId: "school", roles: ["DATA_MAINTAINER"] });
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };

test("a refresh survives unsubscribing/remounting and repeated starts share one request", async () => {
  const original = globalThis.fetch, response = deferred(), calls = [], identity = actor("background");
  globalThis.fetch = async (url, options) => { calls.push({ url, ...options }); return response.promise; };
  let events = 0;
  const unsubscribe = subscribeForecastRefresh(() => events++);
  try {
    const pending = startForecastRefresh(identity, defaultForecastStates, "用户更新");
    const running = getForecastRefresh(identity);
    assert.equal(running.state, "running");
    assert.equal(getForecastRefresh({ ...identity, roles: [...identity.roles].reverse() }), running);
    unsubscribe(); // Leaving the module releases a view, not the submitted request.
    assert.equal(startForecastRefresh(identity, defaultForecastStates, "重复点击"), pending);
    await Promise.resolve();
    assert.equal(calls.length, 1);
    assert.match(calls[0].url, /\/forecasts\/fy27\/refresh$/);
    response.resolve(Response.json({ baseline_year: 2027, version: { id: "new-saved-version" }, history: [] }));
    await pending;
    assert.equal(getForecastRefresh(identity).state, "succeeded");
    assert.equal(getForecastRefresh(identity).result.version.id, "new-saved-version");
    assert.equal(events, 1);
    let remounted = 0;
    const detach = subscribeForecastRefresh(() => remounted++);
    assert.equal(getForecastRefresh(identity).state, "succeeded");
    detach(); assert.equal(remounted, 0);
  } finally { unsubscribe(); globalThis.fetch = original; }
});

test("failed refreshes retain their original intent and idempotency key across leaving and retry", async () => {
  const original = globalThis.fetch, identity = actor("retry"), calls = [];
  globalThis.fetch = async (_url, options) => {
    calls.push(options);
    if (calls.length === 1) throw new TypeError("response lost");
    return Response.json({ version: { id: "same-saved-command" }, history: [] });
  };
  try {
    const states = { ...defaultForecastStates };
    await startForecastRefresh(identity, states, "原更新原因");
    states["秋季"] = "ENDED";
    assert.equal(getForecastRefresh(identity).state, "failed");
    assert.ok(getForecastRefresh(identity).error.message);
    await startForecastRefresh(identity, states, "重挂载后的原因", true);
    assert.equal(calls.length, 2);
    assert.equal(calls[0].headers["Idempotency-Key"], calls[1].headers["Idempotency-Key"]);
    assert.equal(calls[0].body, calls[1].body);
    assert.equal(JSON.parse(calls[1].body).states["秋季"], defaultForecastStates["秋季"]);
    assert.equal(getForecastRefresh(identity).state, "succeeded");
    await startForecastRefresh(identity, states, "新的显式更新");
    assert.notEqual(calls[2].headers["Idempotency-Key"], calls[1].headers["Idempotency-Key"]);
  } finally { globalThis.fetch = original; }
});

test("identity scopes cannot observe or reuse another user's result, and server failures stay failures", async () => {
  const original = globalThis.fetch, identity = actor("private"), other = actor("other");
  globalThis.fetch = async () => Response.json({ detail: { code: "FORECAST_REFRESH_IN_PROGRESS", message: "更新未完成", retryable: true } }, { status: 409 });
  try {
    await startForecastRefresh(identity, defaultForecastStates, "更新");
    assert.equal(getForecastRefresh(identity).state, "failed");
    assert.equal(getForecastRefresh(identity).result, null);
    assert.equal(getForecastRefresh(identity).error.detail.code, "FORECAST_REFRESH_IN_PROGRESS");
    assert.equal(getForecastRefresh(other), null);
    assert.equal(getForecastRefresh({ ...identity, departmentId: "other-school" }), null);
    assert.equal(getForecastRefresh({ ...identity, roles: [] }), null);
    assert.equal(getForecastRefresh(null), null);
  } finally { globalThis.fetch = original; }
});
