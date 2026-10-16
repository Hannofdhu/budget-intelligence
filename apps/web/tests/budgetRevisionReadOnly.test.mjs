// Exact-version read/metadata regressions; mocked GETs only, never live budget writes.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";
import { revisionRelations } from "../src/budgetRevisionDisplay.ts";

const require = createRequire(import.meta.url);
async function compile(path) {
  const built = await build({ entryPoints: [fileURLToPath(new URL(path, import.meta.url))], bundle: true, write: false,
    platform: "node", format: "cjs", jsx: "automatic", external: ["react", "react-dom", "exceljs"], loader: { ".css": "empty" }, logLevel: "silent" });
  return built.outputFiles[0].text;
}
const rebalance = await compile("../src/RebalancingPage.tsx");
const results = await compile("../src/BudgetResultsPage.tsx");
const find = (node, predicate) => Array.isArray(node) ? node.flatMap(child => find(child, predicate)) : !node || typeof node !== "object" ? []
  : [...(predicate(node) ? [node] : []), ...find(node.props?.children, predicate)];
const text = node => Array.isArray(node) ? node.map(text).join("") : node && typeof node === "object" ? text(node.props?.children)
  : node == null || typeof node === "boolean" ? "" : String(node);
const precise = { budget_revision_id: "old-published", annual_budget_id: "annual-30", revision_no: 150,
  parent_revision_id: "parent-exact", component_manifest: { revenue_result_version_id: "revenue-exact", formal_confirmation_id: "confirmation-exact" },
  created_at: "2026-10-16T01:30:00Z", created_by: "source-service", command_type: "CALCULATE_REVENUE_RESULT", revision_content_hash: "source-hash" };
const annual = { annual_budget_id: "annual-30", fiscal_year: 2030, current_draft_revision_id: "new-draft", current_published_revision_id: "new-published" };

async function mounted(code, name, props, publication = "published", fixture = {}) {
  const saved = { window: globalThis.window, fetch: globalThis.fetch }, requests = [], effects = [], states = [];
  globalThis.window = { location: { search: "?revision=old-published" }, history: { replaceState() {} }, localStorage: { getItem() { return null; }, setItem() {} } };
  globalThis.fetch = async (url, options) => {
    requests.push({ url, options });
    assert.ok(!options?.method || options.method === "GET", "mount and read must not mutate any budget");
    if (url === "/api/v1/local-trial/session") return Response.json({ userId: "reader", departmentId: "school", roles: ["DATA_MAINTAINER"] });
    if (url === "/api/v1/revisions/old-published") return Response.json(fixture.scenario ? { ...precise, component_manifest: { ...precise.component_manifest, target_closure_result_version_id: "closure-exact" } } : precise);
    if (url === "/api/v1/annual-budgets/annual-30") return Response.json(fixture.scenario ? { ...annual, current_draft_revision_id: "old-published" } : annual);
    if (url === "/api/v1/revenue-result-versions/revenue-exact") return Response.json({ fiscal_year: 2030, items: [] });
    if (url === "/api/v1/revisions/old-published/optimization-runs/latest") return fixture.scenario ? Response.json({ status: "OPTIMAL", scenarios: [fixture.scenario], infeasibility_proofs: [], input_fingerprint: "fingerprint-exact" }) : Response.json({}, { status: 404 });
    if (url === "/api/v1/target-closure-result-versions/closure-exact") return Response.json({ items: [{ scope_level: "DEPARTMENT", variance_yuan: "-100", tolerance_yuan: "1" }] });
    if (url.startsWith("/api/v1/revisions/old-published/discount-unlocks/current")) return Response.json({}, { status: 404 });
    if (url.startsWith("/api/v1/audit-events?")) {
      const params = new URL(url, "https://budget.local").searchParams;
      assert.equal(params.get("revision_id"), "old-published"); assert.equal(params.get("event_type"), "PUBLISHED");
      if (publication === "failed") return Response.json({ detail: "只读记录暂不可用" }, { status: 503 });
      return Response.json({ items: publication === "published" ? [{ budget_revision_id: "old-published", event_type: "PUBLISHED" }] : [], next_cursor: null });
    }
    throw new Error(`Unexpected read: ${url}`);
  };
  let cursor = 0, captureEffects = true;
  const react = { ...require("react"), useCallback: callback => callback, useMemo: callback => callback(), useRef: value => ({ current: value }),
    useState(initial) { const index = cursor++; if (!(index in states)) states[index] = typeof initial === "function" ? initial() : initial;
      return [states[index], value => { states[index] = typeof value === "function" ? value(states[index]) : value; }]; },
    useEffect(callback) { if (captureEffects) effects.push(callback); } };
  const loaded = { exports: {} };
  new Function("require", "module", "exports", code)(id => id === "react" ? react : require(id), loaded, loaded.exports);
  const render = () => { cursor = 0; return loaded.exports[name](props); };
  try {
    render(); captureEffects = false;
    const cleanups = effects.map(callback => callback());
    for (let index = 0; index < 24; index++) await new Promise(resolve => setImmediate(resolve));
    const tree = render();
    cleanups.forEach(cleanup => { if (typeof cleanup === "function") cleanup(); });
    return { tree, requests, render };
  } finally { Object.assign(globalThis, saved); }
}

test("historically published R version remains published/read-only even after both current pointers advance", async () => {
  const { tree, requests } = await mounted(rebalance, "RebalancingPage");
  const header = find(tree, node => typeof node.type === "function" && node.type.name === "BudgetContextHeader")[0];
  assert.equal(header.props.status, "已定版");
  assert.match(text(tree), /曾定版/); assert.match(text(tree), /只读查看/); assert.match(text(tree), /这版预算已定版/);
  assert.doesNotMatch(text(tree), /历史草稿/);
  assert.equal(find(tree, node => node.type === "button" && ["生成方案", "重新生成方案"].includes(text(node))).length, 0);
  assert.equal(find(tree, node => node.type === "a" && text(node) === "返回最新草稿")[0].props.href, "/budget/rebalancing?revision=new-draft");
  assert.equal(requests.filter(item => item.url.startsWith("/api/v1/audit-events?")).length, 1);
});

test("saved R results and candidate transfers have distinct saved-versus-preview exact identities", async () => {
  const scenario = { rebalancing_scenario_id: "candidate-exact", scenario_types: ["BALANCED"], changes: [{ scenario_change_id: "change-1", object_type: "RATE", object_stable_key: "rate-exact", original_value: 600000, suggested_value: 620000, adjustment: 20000 }],
    objective_values: {}, target_variances: [], headcount_signature: [], retention_contribution_yuan: "0", new_share_contribution_yuan: "0", result_hash: "candidate-hash" };
  const before = JSON.stringify(scenario);
  const h = await mounted(rebalance, "RebalancingPage", undefined, "none", { scenario });
  const saved = JSON.parse(find(h.tree, node => node.props?.id === "rebalance-main")[0].props["data-budget-export-context"]);
  assert.deepEqual(saved, { fiscalYear: 2030, object: "年度预算", sequence: 150, createdAt: precise.created_at, id: "old-published" });
  find(h.tree, node => typeof node.type === "function" && node.type.name === "CandidateCard")[0].props.onSelect();
  const tree = h.render();
  const preview = JSON.parse(find(tree, node => node.props?.className === "scenario-detail budget-surface")[0].props["data-budget-export-context"]);
  assert.equal(preview.preview, true); assert.equal(preview.id, "old-published"); assert.equal(preview.sequence, 150);
  assert.equal(preview.sourceId, "candidate-exact"); assert.match(preview.sourceLabel, /FY30年度预算第150版/);
  assert.equal(JSON.stringify(scenario), before);
});

test("unavailable publication history is explicitly unknown, not relabelled as historical draft", async () => {
  const { tree } = await mounted(rebalance, "RebalancingPage", undefined, "failed");
  const header = find(tree, node => typeof node.type === "function" && node.type.name === "BudgetContextHeader")[0];
  assert.equal(header.props.status, "定版状态待核对");
  assert.match(text(tree), /定版状态待核对/); assert.match(text(tree), /只读查看/); assert.doesNotMatch(text(tree), /历史草稿/);
  assert.deepEqual(revisionRelations(precise, annual, new Set(), false).map(item => item.label), ["已正式确认", "定版状态待核对"]);
});

test("normal published result and Workspace embedded result pass exact existing metadata to pivot transfer without extra reads", async () => {
  const { tree, requests } = await mounted(results, "BudgetResultsPage", { revisionId: "old-published", active: true });
  const analysis = find(tree, node => typeof node.type === "function" && node.type.name === "BudgetAnalysisView")[0];
  assert.ok(analysis);
  assert.deepEqual(analysis.props.versionExportContext, { fiscalYear: 2030, object: "年度预算", sequence: 150,
    createdAt: "2026-10-16T01:30:00Z", id: "old-published" });
  assert.equal(requests.filter(item => item.url === "/api/v1/revisions/old-published").length, 1);
  assert.equal(requests.filter(item => item.url === "/api/v1/annual-budgets/annual-30").length, 1);
  const workspace = fs.readFileSync(new URL("../src/WorkspacePage.tsx", import.meta.url), "utf8");
  assert.match(workspace, /<BudgetResultsPage key=\{workspace\.revision_id\} revisionId=\{workspace\.revision_id\}/);
  assert.doesNotMatch(workspace, /<BudgetAnalysisView/, "Workspace reuses this exact embedded results path rather than a second pivot implementation");
});
