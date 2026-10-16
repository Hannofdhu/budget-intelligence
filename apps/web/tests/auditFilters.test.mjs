// Read-only audit requests: repeated roles remain a backend OR across all pages.
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";
import { fetchAuditEvents } from "../src/api.ts";

const identity = { userId: "audit-reviewer", departmentId: "example-department", roles: ["DEPARTMENT_BUDGET_OWNER"] };

test("audit role selections serialize as repeated parameters, preserving legacy single-value and pagination", async () => {
  const original = globalThis.fetch, requests = [];
  globalThis.fetch = async (url, options) => { requests.push({ url, options }); return Response.json({ items: [], next_cursor: null }); };
  try {
    const base = { revisionId: "revision-a", eventType: "PUBLISHED", objectType: "BUDGET", actorId: "owner",
      reasonContains: "原因", occurredFrom: "2026-10-01", occurredTo: "2026-10-14", cursor: "cursor-a", limit: 50 };
    for (const actingRole of ["DATA_MAINTAINER", ["DATA_MAINTAINER", "PERMISSION_ADMIN", "DATA_MAINTAINER"], "", []]) {
      await fetchAuditEvents("budget-a", { ...base, actingRole }, identity);
    }
    assert.deepEqual(requests.map(({ url }) => new URL(url, "https://test").searchParams.getAll("acting_role")), [
      ["DATA_MAINTAINER"], ["DATA_MAINTAINER", "PERMISSION_ADMIN"], [], [],
    ]);
    for (const request of requests) {
      const params = new URL(request.url, "https://test").searchParams;
      assert.equal(params.get("annual_budget_id"), "budget-a"); assert.equal(params.get("revision_id"), "revision-a");
      assert.equal(params.get("cursor"), "cursor-a"); assert.equal(params.get("limit"), "50");
      assert.equal(params.get("reason_contains"), "原因"); assert.equal(params.get("occurred_from"), "2026-10-01");
      assert.equal(request.options.method, undefined); assert.equal(request.options.body, undefined);
      assert.equal(new Headers(request.options.headers).get("X-Department-ID"), "example-department");
      assert.equal(new Headers(request.options.headers).get("X-Roles"), "DEPARTMENT_BUDGET_OWNER", "filter roles never become authorization roles");
    }
  } finally { globalThis.fetch = original; }
});

test("audit uses the shared multi-select and selected roles continue through version changes and load-more", () => {
  const source = fs.readFileSync(new URL("../src/VersionAuditPage.tsx", import.meta.url), "utf8");
  assert.match(source, /actingRole: FilterValue/);
  assert.match(source, /<FilterSelect aria-label="审计角色"/);
  assert.match(source, /actingRole: filterValues\(state\.actingRole\)\.length \? state\.actingRole : undefined/);
  assert.match(source, /loadAudits\(budget\.annual_budget_id, nextFilters, false, undefined, revisionId, compareId\)/);
  assert.match(source, /loadAudits\(budget\.annual_budget_id, appliedFilters, true, auditCursor\)/);
  assert.doesNotMatch(source, /auditEvents\.filter\(/, "filtering only a loaded page would be incomplete");
  assert.match(source, /条已加载/);
  assert.match(source, /className="budget-page-title"/);
  assert.match(source, /className="budget-section-title"/);
});

const require = createRequire(import.meta.url);
const compiled = await build({ entryPoints: [fileURLToPath(new URL("../src/VersionAuditPage.tsx", import.meta.url))],
  bundle: true, write: false, platform: "node", format: "cjs", jsx: "automatic", external: ["react", "react-dom"],
  loader: { ".css": "empty" }, logLevel: "silent" });
const text = node => Array.isArray(node) ? node.map(text).join("") : node && typeof node === "object" ? text(node.props?.children)
  : node == null || typeof node === "boolean" ? "" : String(node);
const find = (node, predicate) => Array.isArray(node) ? node.flatMap(child => find(child, predicate)) : !node || typeof node !== "object" ? []
  : [...(predicate(node) ? [node] : []), ...find(node.props?.children, predicate)];

function auditHarness() {
  const originalWindow = globalThis.window;
  globalThis.window = { location: { search: "" }, localStorage: { getItem() { return null; } } };
  const revision = (id, no) => ({ budget_revision_id: id, annual_budget_id: "budget-a", revision_no: no,
    parent_revision_id: no === 2 ? "revision-a" : null, command_type: "REPLACE_TARGET_SET", component_manifest: {},
    created_at: "2026-10-14T00:00:00Z", revision_content_hash: "a".repeat(64) });
  const initial = new Map([
    [2, { annual_budget_id: "budget-a", fiscal_year: 2027, current_draft_revision_id: "revision-b", current_published_revision_id: null }],
    [3, [revision("revision-b", 2), revision("revision-a", 1)]], [4, "revision-b"], [5, "revision-a"],
  ]);
  const states = [], writes = [], refs = [];
  let index = 0, refIndex = 0;
  const react = { ...require("react"), useState(value) {
    const key = index++;
    if (!(key in states)) states[key] = initial.has(key) ? initial.get(key) : typeof value === "function" ? value() : value;
    return [states[key], next => { states[key] = typeof next === "function" ? next(states[key]) : next; writes.push(key); }];
  }, useRef(value) { const index = refIndex++; return refs[index] ??= { current: value }; }, useMemo: callback => callback(), useCallback: callback => callback, useEffect() {} };
  const loaded = { exports: {} };
  new Function("require", "module", "exports", compiled.outputFiles[0].text)(name => name === "react" ? react : require(name), loaded, loaded.exports);
  return {
    render() { index = 0; refIndex = 0; return loaded.exports.VersionAuditPage(); }, states, writes,
    restore() { globalThis.window = originalWindow; },
  };
}

test("audit multi-role control changes only filter view state and sends both roles to the read-only query", async () => {
  const harness = auditHarness(), original = globalThis.fetch, requests = [];
  globalThis.fetch = async (url, options) => { requests.push({ url, options }); return Response.json({ items: [], next_cursor: null }); };
  try {
    const originalTree = harness.render();
    const role = find(originalTree, node => node.props?.["aria-label"] === "审计角色")[0];
    assert.equal(role.type.name, "FilterSelect"); assert.equal(role.props.allLabel, "全部角色");
    assert.deepEqual(role.props.options.map(item => item.value), ["DATA_MAINTAINER", "DEPARTMENT_BUDGET_OWNER", "PERMISSION_ADMIN"]);
    const beforeBudget = JSON.stringify(harness.states.slice(2, 6));
    role.props.onChange(["DATA_MAINTAINER", "PERMISSION_ADMIN"]);
    assert.equal(harness.writes.length, 1, "selecting roles updates exactly one filter state");
    assert.deepEqual(harness.states[harness.writes[0]].actingRole, ["DATA_MAINTAINER", "PERMISSION_ADMIN"]);
    assert.equal(JSON.stringify(harness.states.slice(2, 6)), beforeBudget, "filtering never edits budget or revisions");
    assert.equal(requests.length, 0, "selecting a role never starts an archive or writes to a budget");
    const tree = harness.render();
    const apply = find(tree, node => node.type === "button" && text(node) === "应用筛选")[0];
    apply.props.onClick();
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(requests.length, 1);
    const params = new URL(requests[0].url, "https://test").searchParams;
    assert.deepEqual(params.getAll("acting_role"), ["DATA_MAINTAINER", "PERMISSION_ADMIN"]);
    assert.equal(params.get("revision_id"), "revision-b"); assert.equal(params.get("limit"), "50");
    assert.equal(requests[0].options.method, undefined); assert.equal(requests[0].options.body, undefined);
    assert.ok(find(tree, node => node.type === "h1" && node.props.className === "budget-page-title").length);
    assert.ok(find(tree, node => node.type === "h2" && node.props.className === "budget-section-title").length >= 3);
  } finally { globalThis.fetch = original; harness.restore(); }
});
