// Hook lifecycle and exact-version reads only; all transport is mocked.
import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";
import { getForecastVersion } from "../src/forecastApi.ts";
import { budgetReadKey, storeBudgetRead, invalidateBudgetReads } from "../src/budgetReadCache.ts";

const require = createRequire(import.meta.url);
const bundled = await build({ entryPoints: [fileURLToPath(new URL("../src/useNextYearSkuBasis.ts", import.meta.url))],
  bundle: true, write: false, platform: "node", format: "cjs", external: ["react", "./forecastApi"], logLevel: "silent" });
const actor = { userId: "sku-evidence-reader", departmentId: "trial", roles: ["DEPARTMENT_BUDGET_OWNER"] };
const rows = [{ season: "暑假", forecast_revenue: "12.34567", forecast_total: 3 }];
const issues = [{ code: "FINALIZED_ACTUAL_MISSING", severity: "INFO", season: "暑假", dims: { campus: "西直门", track: "智慧空间", product: "云帆", grade: "八年级", subject: "数学" },
  detail: { classification: "EMPTY_BUDGET_SLOT_NO_SOURCE_BUSINESS" }, message: "空占位已核准" }];
const envelope = (id, data = rows, evidenceIssues = issues) => ({ version: { id, calculation: { rows: data, issues: evidenceIssues } } });
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const flush = () => new Promise(resolve => setImmediate(resolve));

function harness(api) {
  const state = [], dependencies = [], cleanups = []; let stateCursor = 0, effectCursor = 0, effects = [];
  const react = {
    useState(initial) { const index = stateCursor++; if (!(index in state)) state[index] = typeof initial === "function" ? initial() : initial;
      return [state[index], value => { state[index] = typeof value === "function" ? value(state[index]) : value; }]; },
    useEffect(effect, deps) { const index = effectCursor++;
      if (!dependencies[index] || deps.some((value, i) => !Object.is(value, dependencies[index][i]))) {
        dependencies[index] = deps; effects.push(() => { cleanups[index]?.(); cleanups[index] = effect(); });
      }
    },
  };
  const module = { exports: {} };
  new Function("require", "module", "exports", bundled.outputFiles[0].text)(name => name === "react" ? react : name === "./forecastApi" ? api : require(name), module, module.exports);
  return {
    render(identity = actor, sourceId = "frozen-20", enabled = true) { stateCursor = 0; effectCursor = 0; effects = [];
      const result = module.exports.useNextYearSkuBasis(identity, sourceId, enabled); effects.forEach(effect => effect()); return result; },
    dispose() { cleanups.forEach(cleanup => cleanup?.()); },
  };
}

test("SKU evidence requests only its saved source ID and never starts a read outside the SKU step", async () => {
  const calls = [], pending = deferred();
  const h = harness({ peekForecast: () => null, getForecastVersion: (...args) => { calls.push(args); return pending.promise; } });
  assert.equal(h.render(actor, "frozen-20", false).status, "loading"); assert.deepEqual(h.render(actor, "frozen-20", false).issues, []); assert.equal(calls.length, 0);
  assert.equal(h.render(null, "frozen-20", true).rows, null); assert.equal(calls.length, 0);
  assert.equal(h.render(actor, "", true).rows, null); assert.equal(calls.length, 0);
  assert.equal(h.render(actor, "frozen-20", true).status, "loading");
  assert.equal(calls.length, 1); assert.equal(calls[0][0], actor); assert.equal(calls[0][1], "frozen-20");
  assert.ok(calls[0][2] instanceof AbortSignal); h.render(); assert.equal(calls.length, 1, "unchanged source does not refetch on every render");
  pending.resolve(envelope("frozen-20")); await flush();
  const result = h.render(); assert.equal(result.status, "ready"); assert.equal(result.rows, rows); assert.equal(result.issues, issues);
  assert.equal(calls.length, 1, "rows and classified issues come from the same exact-version read"); h.dispose();
});

test("leaving the SKU step aborts its subscription and an aborted result cannot become evidence", async () => {
  const pending = deferred(), calls = [];
  const h = harness({ peekForecast: () => null, getForecastVersion: (...args) => { calls.push(args); return pending.promise; } });
  h.render(); h.render(actor, "frozen-20", false);
  assert.equal(calls[0][2].aborted, true);
  pending.resolve(envelope("frozen-20")); await flush();
  assert.equal(h.render(actor, "frozen-20", false).status, "loading"); assert.equal(h.render(actor, "frozen-20", false).rows, null);
  assert.deepEqual(h.render(actor, "frozen-20", false).issues, []); h.dispose();
});

test("switching saved source aborts the earlier read and ignores its late return", async () => {
  const old = deferred(), fresh = deferred(), calls = [];
  const h = harness({ peekForecast: () => null, getForecastVersion: (_identity, id, signal) => { calls.push({ id, signal }); return id === "old-source" ? old.promise : fresh.promise; } });
  h.render(actor, "old-source"); const pending = h.render(actor, "new-source");
  assert.equal(calls[0].signal.aborted, true); assert.equal(pending.status, "loading"); assert.equal(pending.rows, null); assert.deepEqual(pending.issues, []);
  const freshRows = [{ season: "春季", forecast_revenue: "900", forecast_total: 9 }];
  const freshIssues = [{ ...issues[0], season: "春季" }];
  fresh.resolve(envelope("new-source", freshRows, freshIssues)); await flush(); assert.equal(h.render(actor, "new-source").rows, freshRows);
  assert.equal(h.render(actor, "new-source").issues, freshIssues);
  old.resolve(envelope("old-source", [{ season: "暑假", forecast_revenue: "999999", forecast_total: 999999 }])); await flush();
  assert.equal(h.render(actor, "new-source").rows, freshRows); assert.equal(h.render(actor, "new-source").issues, freshIssues); assert.equal(calls.length, 2); h.dispose();
});

test("actor and department changes cannot reveal evidence returned to the previous identity", async () => {
  const old = deferred(), fresh = deferred(), calls = [];
  const nextActor = { ...actor, departmentId: "another-department" };
  const h = harness({ peekForecast: () => null, getForecastVersion: (identity, _id, signal) => { calls.push({ identity, signal }); return identity === actor ? old.promise : fresh.promise; } });
  h.render(); h.render(nextActor); assert.equal(calls[0].signal.aborted, true);
  old.resolve(envelope("frozen-20")); await flush(); assert.equal(h.render(nextActor).rows, null); assert.deepEqual(h.render(nextActor).issues, []);
  fresh.resolve(envelope("frozen-20", [], [])); await flush(); assert.deepEqual(h.render(nextActor).rows, []); assert.deepEqual(h.render(nextActor).issues, []); h.dispose();
});

test("a failed exact-source read shows error and never falls back to a cached current or another version", async () => {
  const calls = [], peeked = [], pending = deferred();
  const h = harness({ peekForecast: (_identity, id) => { peeked.push(id); return envelope("current-99", [{ forecast_revenue: "999999" }]); },
    getForecastVersion: (_identity, id) => { calls.push(id); return pending.promise; } });
  assert.equal(h.render().rows, null, "a cache result carrying the wrong ID is not trusted"); assert.deepEqual(h.render().issues, []);
  pending.reject(new Error("Selected frozen version unavailable")); await flush();
  const result = h.render(); assert.equal(result.status, "error"); assert.equal(result.rows, null); assert.deepEqual(result.issues, []);
  assert.deepEqual(calls, ["frozen-20"]); assert.ok(peeked.every(id => id === "frozen-20")); h.dispose();
});

test("an exact cached version may display immediately while its read revalidates", async () => {
  const pending = deferred(), cached = envelope("frozen-20"); let calls = 0;
  const h = harness({ peekForecast: () => cached, getForecastVersion: () => { calls++; return pending.promise; } });
  const first = h.render(); assert.equal(first.status, "ready"); assert.equal(first.rows, cached.version.calculation.rows); assert.equal(first.issues, cached.version.calculation.issues); assert.equal(calls, 1);
  pending.resolve(cached); await flush(); assert.equal(h.render().status, "ready"); assert.equal(h.render().issues, cached.version.calculation.issues); assert.equal(calls, 1); h.dispose();
});

test("a legacy frozen envelope with no issues keeps unknown evidence unapproved without issuing another read", async () => {
  const pending = deferred(); let calls = 0;
  const legacy = { version: { id: "frozen-20", calculation: { rows } } };
  const h = harness({ peekForecast: () => legacy, getForecastVersion: () => { calls++; return pending.promise; } });
  assert.deepEqual(h.render().issues, []); pending.resolve(legacy); await flush();
  assert.equal(h.render().rows, rows); assert.deepEqual(h.render().issues, []); assert.equal(calls, 1); h.dispose();
});

test("real exact-source transport performs GET only and refuses mismatched versions without a current fallback", async () => {
  invalidateBudgetReads(); const originalFetch = globalThis.fetch, calls = [];
  const exactId = "saved/source?20", path = `/api/v1/forecasts/fy27/versions/${encodeURIComponent(exactId)}`;
  storeBudgetRead(budgetReadKey(actor, "/api/v1/forecasts/fy27/current"), envelope("current-99"));
  globalThis.fetch = async (url, options) => { calls.push({ url, options }); return Response.json(envelope(exactId)); };
  try {
    const result = await getForecastVersion(actor, exactId); assert.equal(result.version.id, exactId); assert.deepEqual(result.version.calculation.issues, issues);
    assert.equal(calls.length, 1); assert.equal(calls[0].url, path);
    assert.ok(calls[0].options.method === undefined || calls[0].options.method === "GET");
    assert.equal(calls[0].options.body, undefined); assert.equal(new Headers(calls[0].options.headers).get("X-User-ID"), actor.userId);
    globalThis.fetch = async (url, options) => { calls.push({ url, options }); return Response.json(envelope("wrong-version")); };
    await assert.rejects(getForecastVersion(actor, "missing-source"), /未读取到所选预测版本/);
    assert.deepEqual(calls.map(call => call.url), [path, "/api/v1/forecasts/fy27/versions/missing-source"]);
    assert.ok(calls.every(call => !/\/current$|refresh|confirm|prepare/.test(call.url)));
  } finally { globalThis.fetch = originalFetch; invalidateBudgetReads(); }
});
