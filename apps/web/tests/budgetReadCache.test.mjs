import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { budgetReadKey, readBudgetResource, peekBudgetRead, storeBudgetRead, invalidateBudgetReads, getBudgetSession } from "../src/budgetReadCache.ts";

beforeEach(() => invalidateBudgetReads());
const actor = { userId: "owner", departmentId: "a", roles: ["DATA_MAINTAINER"] };
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; };

test("background and foreground reads share one request, and one leaving page cannot cancel other readers", async () => {
  const pending = deferred(), controller = new AbortController(); let requests = 0;
  const loader = () => { requests++; return pending.promise; };
  const a = readBudgetResource("same", loader, controller.signal), b = readBudgetResource("same", loader);
  controller.abort(); await assert.rejects(a, { name: "AbortError" });
  pending.resolve({ version: 26 }); assert.deepEqual(await b, { version: 26 });
  assert.equal(requests, 1); assert.deepEqual(peekBudgetRead("same"), { version: 26 });
  assert.deepEqual(await readBudgetResource("same", loader), { version: 26 }); assert.equal(requests, 1);
});
test("keys isolate identities, roles, departments, fiscal years, and exact historical versions", () => {
  const keys = [budgetReadKey(actor, "fy28/current"), budgetReadKey({ ...actor, userId: "other" }, "fy28/current"), budgetReadKey({ ...actor, departmentId: "b" }, "fy28/current"), budgetReadKey({ ...actor, roles: [] }, "fy28/current"), budgetReadKey(actor, "fy29/current"), budgetReadKey(actor, "fy28/versions/exact-12")];
  assert.equal(new Set(keys).size, keys.length);
  for (const [i, key] of keys.entries()) storeBudgetRead(key, i);
  assert.deepEqual(keys.map(key => peekBudgetRead(key)), [0, 1, 2, 3, 4, 5]);
  assert.equal(budgetReadKey({ ...actor, roles: ["b", "a"] }, "x"), budgetReadKey({ ...actor, roles: ["a", "b"] }, "x"));
});
test("a late read cannot overwrite a newer command response or repopulate an invalidated entry", async () => {
  const pending = deferred(); const read = readBudgetResource("/forecasts/current", () => pending.promise);
  invalidateBudgetReads("/forecasts/"); storeBudgetRead("/forecasts/current", { sequence: 27 });
  pending.resolve({ sequence: 26 }); await read;
  assert.equal(peekBudgetRead("/forecasts/current").sequence, 27);
});
test("expired results remain only a bounded display snapshot and freshness requires a new request", async () => {
  const now = Date.now; let time = 10_000, requests = 0; Date.now = () => time;
  try {
    storeBudgetRead("current", { sequence: 23 }); time += 31_000;
    assert.equal(peekBudgetRead("current").sequence, 23);
    await readBudgetResource("current", async () => { requests++; return { sequence: 24 }; });
    assert.equal(requests, 1); assert.equal(peekBudgetRead("current").sequence, 24);
    time += 301_000; assert.equal(peekBudgetRead("current"), null);
  } finally { Date.now = now; }
});
test("permission errors clear cached results and a failed request is retryable without being recorded as success", async () => {
  storeBudgetRead("private", { sequence: 23 });
  await assert.rejects(readBudgetResource("fail", async () => { throw Object.assign(new Error("forbidden"), { status: 403 }); }));
  assert.equal(peekBudgetRead("private"), null);
  assert.equal(await readBudgetResource("fail", async () => "recovered"), "recovered");
});
test("session lookup is deduplicated and performs GET only, without budget writes", async () => {
  const original = fetch, requests = []; globalThis.fetch = async (url, options) => { requests.push({ url, options }); return Response.json(actor); };
  try { await Promise.all([getBudgetSession(), getBudgetSession()]); assert.equal(requests.length, 1); assert.equal(requests[0].url, "/api/v1/local-trial/session"); assert.equal(requests[0].options?.method, undefined); }
  finally { globalThis.fetch = original; }
});

test("a stuck shared request has a deadline and can retry without a stale pending promise", async () => {
  const set = globalThis.setTimeout, clear = globalThis.clearTimeout; let expire;
  globalThis.setTimeout = (fn, delay) => { assert.equal(delay, 60_000); expire = fn; return 1; }; globalThis.clearTimeout = () => {};
  try {
    const pending = readBudgetResource("hung", signal => new Promise((_resolve, reject) => signal.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")))));
    expire(); await assert.rejects(pending, /读取已保存结果超时/);
    assert.equal(peekBudgetRead("hung"), null);
    assert.equal(await readBudgetResource("hung", async () => "recovered"), "recovered");
  } finally { globalThis.setTimeout = set; globalThis.clearTimeout = clear; }
});

test("a transient revalidation failure retains only the time-bounded saved display snapshot", async () => {
  const now = Date.now; let time = 1_000; Date.now = () => time;
  try {
    storeBudgetRead("saved", { sequence: 23 }); time += 31_000;
    await assert.rejects(readBudgetResource("saved", async () => { throw new Error("network unavailable"); }));
    assert.equal(peekBudgetRead("saved").sequence, 23);
    time += 301_000; assert.equal(peekBudgetRead("saved"), null);
  } finally { Date.now = now; }
});
