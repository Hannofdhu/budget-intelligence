// Read-only UI races with transport deliberately ignoring AbortSignal to exercise ownership guards.
import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";
import { createAuditReadGuard } from "../src/auditReadGuard.ts";

const require = createRequire(import.meta.url);
const compiled = await build({ entryPoints: [fileURLToPath(new URL("../src/VersionAuditPage.tsx", import.meta.url))], bundle: true, write: false,
  platform: "node", format: "cjs", jsx: "automatic", external: ["react", "react-dom"], loader: { ".css": "empty" }, logLevel: "silent" });
const find = (node, predicate) => Array.isArray(node) ? node.flatMap(child => find(child, predicate)) : !node || typeof node !== "object" ? []
  : [...(predicate(node) ? [node] : []), ...find(node.props?.children, predicate)];
const text = node => Array.isArray(node) ? node.map(text).join("") : node && typeof node === "object" ? text(node.props?.children)
  : node == null || typeof node === "boolean" ? "" : String(node);
const revision = (id, no, parent) => ({ budget_revision_id: id, annual_budget_id: "annual", revision_no: no, parent_revision_id: parent,
  component_manifest: {}, command_type: "REPLACE_TARGET_SET", created_at: "2026-10-16T01:30:00Z", revision_content_hash: `saved-${id}` });
const revisions = [revision("r3", 3, "r2"), revision("r2", 2, "r1"), revision("r1", 1, null)];
const difference = (selected, against, hash) => ({ revision: { revision: revisions.find(item => item.budget_revision_id === selected) },
  against: { revision: revisions.find(item => item.budget_revision_id === against) }, content_hash: hash,
  changed_count: 1, removed_count: 0, unchanged_count: 0,
  components: [{ component_key: "rate_parameter_version_id", change_type: "CHANGED", against_value: "old-rate", revision_value: "new-rate" }] });
const event = (id, revisionId) => ({ event_id: id, budget_revision_id: revisionId, event_type: "BUDGET_REVISION_DERIVED", actor_id: "service", acting_role: "DATA_MAINTAINER", object_key: {}, detail: {}, reason: null, occurred_at: "2026-10-16T01:30:00Z", request_id: id });
const flush = async () => { for (let count = 0; count < 8; count++) await new Promise(resolve => setImmediate(resolve)); };

function harness() {
  const originals = { fetch: globalThis.fetch, window: globalThis.window }, requests = [], states = [], refs = [];
  let url = new URL("https://budget.local/budget/audit?revision=r3&against=r2"), cursor = 0, refCursor = 0;
  globalThis.window = { location: { get href() { return url.href; }, get search() { return url.search; } },
    history: { replaceState(_state, _title, next) { url = new URL(next, url); } }, localStorage: { getItem() { return null; }, setItem() {} } };
  globalThis.fetch = (input, options) => new Promise(resolve => {
    assert.ok(!options?.method || options.method === "GET", "only read requests are allowed");
    requests.push({ url: new URL(input, "https://budget.local"), options, resolve });
  });
  const initial = new Map([[2, { annual_budget_id: "annual", fiscal_year: 2030, current_draft_revision_id: "r3", current_published_revision_id: null }],
    [3, revisions], [4, "r3"], [5, "r2"], [6, difference("r3", "r2", "old-comparison-hash")], [13, [event("old-audit", "r3")]]]);
  const react = { ...require("react"), useCallback: value => value, useMemo: callback => callback(), useEffect() {},
    useRef(value) { const key = refCursor++; return refs[key] ??= { current: value }; },
    useState(value) { const key = cursor++; if (!(key in states)) states[key] = initial.has(key) ? initial.get(key) : typeof value === "function" ? value() : value;
      return [states[key], next => { states[key] = typeof next === "function" ? next(states[key]) : next; }]; } };
  const loaded = { exports: {} };
  new Function("require", "module", "exports", compiled.outputFiles[0].text)(id => id === "react" ? react : require(id), loaded, loaded.exports);
  const render = () => { cursor = 0; refCursor = 0; return loaded.exports.VersionAuditPage(); };
  return { states, requests, render,
    compare(id) { find(render(), node => node.type === "select")[0].props.onChange({ target: { value: id } }); },
    select(id) { const no = revisions.find(item => item.budget_revision_id === id).revision_no;
      find(render(), node => node.type === "button" && find(node, child => typeof child.type === "function" && child.type.name === "BudgetVersionIdentity" && child.props.sequence === no).length)[0].props.onClick(); },
    restore() { Object.assign(globalThis, originals); } };
}
const pending = (h, path, against, revisionId) => h.requests.find(request => !request.done && request.url.pathname.endsWith(path)
  && (against === undefined || request.url.searchParams.get("against") === against)
  && (revisionId === undefined || request.url.searchParams.get("revision_id") === revisionId));
function answer(request, value, status = 200) { assert.ok(request, "expected pending read"); request.done = true; request.resolve(Response.json(value, { status })); }

test("changing comparison clears old difference immediately, and a failed read never presents an old hash under the new title", async () => {
  const h = harness();
  try {
    assert.match(text(h.render()), /old-comparison-hash/);
    h.compare("r1");
    assert.equal(h.states[6], null); assert.equal(h.states[8], null); assert.deepEqual(h.states[13], []);
    assert.doesNotMatch(text(h.render()), /old-comparison-hash/); assert.match(text(h.render()), /正在读取当前版本对的差异/);
    answer(pending(h, "/diff", "r1"), { detail: "测试读取失败" }, 503);
    answer(pending(h, "/audit-events"), { items: [], next_cursor: null });
    await flush();
    const tree = h.render();
    assert.match(text(find(tree, node => node.type === "h1")[0]), /第3版 相比 第1版/);
    assert.match(text(tree), /当前版本对的差异尚未读取成功/); assert.doesNotMatch(text(tree), /old-comparison-hash/);
    assert.equal(h.states[6], null);
  } finally { h.restore(); }
});

test("rapid comparison changes ignore late responses even if the transport does not honor cancellation", async () => {
  const h = harness();
  try {
    h.compare("r1"); const oldDiff = pending(h, "/diff", "r1"), oldAudit = pending(h, "/audit-events");
    h.compare("r2");
    assert.equal(oldDiff.options.signal.aborted, true); assert.equal(oldAudit.options.signal.aborted, true);
    answer(pending(h, "/diff", "r2"), difference("r3", "r2", "latest-comparison-hash"));
    answer(h.requests.filter(request => request.url.pathname.endsWith("/audit-events")).at(-1), { items: [event("latest-audit", "r3")], next_cursor: null });
    await flush();
    answer(oldDiff, difference("r3", "r1", "late-stale-hash")); answer(oldAudit, { items: [event("late-stale-audit", "r3")], next_cursor: null });
    await flush();
    assert.equal(h.states[6].content_hash, "latest-comparison-hash"); assert.equal(h.states[5], "r2");
    assert.deepEqual(h.states[13].map(item => item.event_id), ["latest-audit"]);
    assert.doesNotMatch(text(h.render()), /late-stale-hash|late-stale-audit/);
  } finally { h.restore(); }
});

test("changing selected revision invalidates outstanding detail and audit reads as well as the comparison", async () => {
  const h = harness();
  try {
    const initial = h.render();
    find(initial, node => typeof node.type === "function" && node.type.name === "ComponentDiffRow")[0].props.onInspect("rate_parameter_version_id");
    find(initial, node => node.type === "button" && text(node) === "应用筛选")[0].props.onClick();
    const oldDetail = pending(h, "/detail-diff", "r2"), oldAudit = pending(h, "/audit-events", undefined, "r3");
    h.select("r2");
    assert.equal(oldDetail.options.signal.aborted, true); assert.equal(oldAudit.options.signal.aborted, true);
    assert.equal(h.states[6], null); assert.equal(h.states[8], null);
    answer(pending(h, "/diff", "r1"), difference("r2", "r1", "r2-comparison-hash"));
    answer(pending(h, "/audit-events", undefined, "r2"), { items: [event("r2-audit", "r2")], next_cursor: null });
    await flush();
    answer(oldDetail, { revision_id: "r3", against_revision_id: "r2", component_key: "rate_parameter_version_id", content_hash: "late-detail-hash", items: [], next_cursor: null });
    answer(oldAudit, { items: [event("late-r3-audit", "r3")], next_cursor: null });
    await flush();
    assert.equal(h.states[4], "r2"); assert.equal(h.states[5], "r1"); assert.equal(h.states[6].content_hash, "r2-comparison-hash");
    assert.equal(h.states[8], null); assert.deepEqual(h.states[13].map(item => item.event_id), ["r2-audit"]);
    assert.doesNotMatch(text(h.render()), /late-detail-hash|late-r3-audit/);
  } finally { h.restore(); }
});

test("request guard checks pair generation independently from AbortSignal", () => {
  const guard = createAuditReadGuard("r3", "r2"), first = guard.start("diff"), second = guard.start("diff");
  assert.equal(first.current(), false); assert.equal(second.current(), true);
  guard.setPair("r3", "r1"); assert.equal(second.current(), false);
  const detail = guard.start("detail"); guard.cancel("detail"); assert.equal(detail.current(), false);
});
