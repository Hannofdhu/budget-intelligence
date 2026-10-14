// New forecast basis is previewed with mocked fetch only; no live budget writes.
import { test } from "node:test";
import assert from "node:assert/strict";
import { previewNextYearBasis } from "../src/nextYearApi.ts";

const identity = { userId: "owner", departmentId: "department", roles: ["DEPARTMENT_BUDGET_OWNER"] };
const result = { read_only: true, fiscal_year: 2028, parent_id: "N12", source_id: "P18",
  source: { id: "P18", status: "CONFIRMED", calculation_hash: "calculation", snapshot_hash: "snapshot", baseline_hash: "baseline" },
  inputs: { fiscal_year: 2028, target_revenue_yuan: null, entry_total: 10 },
  calculation: { annual: { revenue_yuan: "123000", target_revenue_yuan: null, gap_yuan: null } } };

test("new-basis preview sends only saved forecast and expected parent identities to the read-only endpoint", async () => {
  const original = globalThis.fetch, calls = [];
  globalThis.fetch = async (url, options) => { calls.push({ url, ...options }); return Response.json(result); };
  try {
    assert.deepEqual(await previewNextYearBasis(identity, "P18", "N12"), result);
    assert.equal(calls.length, 1);
    assert.equal(calls[0].url, "/api/v1/next-year-budgets/fy28/prepare-preview");
    assert.equal(calls[0].method, "POST");
    assert.deepEqual(JSON.parse(calls[0].body), { forecast_id: "P18", parent_id: "N12" });
    const headers = new Headers(calls[0].headers);
    assert.equal(headers.get("X-User-ID"), identity.userId);
    assert.equal(headers.get("X-Department-ID"), identity.departmentId);
    assert.equal(headers.get("Content-Type"), "application/json");
    assert.equal(headers.get("Idempotency-Key"), null);
    assert.doesNotMatch(calls[0].body, /reason|inputs|target|source_snapshot/);
    assert.doesNotMatch(calls[0].url, /\/prepare$|revise|restore|publish|confirm|refresh/);
  } finally { globalThis.fetch = original; }
});

test("new-basis preview supports initial planning without inventing a parent or a business target", async () => {
  const original = globalThis.fetch; let body;
  globalThis.fetch = async (_url, options) => {
    body = JSON.parse(options.body);
    return Response.json({ ...result, parent_id: null });
  };
  try {
    const preview = await previewNextYearBasis(identity, "P18", null);
    assert.deepEqual(body, { forecast_id: "P18", parent_id: null });
    assert.equal(preview.parent_id, null);
    assert.equal(preview.inputs.target_revenue_yuan, null);
    assert.equal(preview.calculation.annual.gap_yuan, null);
  } finally { globalThis.fetch = original; }
});

test("new-basis preview binds to the selected planning fiscal year", async () => {
  const original = globalThis.fetch, urls = [];
  globalThis.fetch = async url => { urls.push(url); return Response.json(result); };
  try {
    await previewNextYearBasis(identity, "P29", "N29", 2029);
    await previewNextYearBasis(identity, "P100", null, 2100);
    assert.deepEqual(urls, ["/api/v1/next-year-budgets/fy29/prepare-preview", "/api/v1/next-year-budgets/2100/prepare-preview"]);
  } finally { globalThis.fetch = original; }
});

test("refused new-basis preview surfaces the reason without fallback, automatic creation or retry", async () => {
  const original = globalThis.fetch, urls = [];
  globalThis.fetch = async url => { urls.push(url); return Response.json({ detail: "预算准备草稿已变化，请重新读取后操作" }, { status: 409 }); };
  try {
    await assert.rejects(previewNextYearBasis(identity, "P18", "N12"), /草稿已变化/);
    assert.deepEqual(urls, ["/api/v1/next-year-budgets/fy28/prepare-preview"]);
  } finally { globalThis.fetch = original; }
});
