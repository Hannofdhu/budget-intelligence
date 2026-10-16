// Presentation and read-only identity tests. No budget writes or live model calls.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { revisionCommandLabel, revisionRelations } from "../src/budgetRevisionDisplay.ts";
import { aiBudgetHref, readAiBudgetContext, aiBudgetContextPrompt } from "../src/aiBudgetContext.ts";

const revision = (id, formal = false) => ({ budget_revision_id: id, component_manifest: formal ? { formal_confirmation_id: "confirmation-exact" } : {} });

test("R relations retain independent draft, publication and formal confirmation semantics", () => {
  const budget = { current_draft_revision_id: "draft-12", current_published_revision_id: "published-10" };
  const published = new Set(["published-10", "historic-7"]);
  assert.deepEqual(revisionRelations(revision("draft-12"), budget, published).map(item => item.label), ["当前草稿"]);
  assert.deepEqual(revisionRelations(revision("published-10", true), budget, published).map(item => item.label), ["当前定版", "已正式确认"]);
  assert.deepEqual(revisionRelations(revision("historic-7", true), budget, published).map(item => item.label), ["曾定版", "已正式确认"]);
  assert.deepEqual(revisionRelations(revision("historical-draft"), budget, published).map(item => item.label), ["历史草稿"]);
  assert.deepEqual(revisionRelations(revision("draft-12", true), budget, published).map(item => item.label), ["当前草稿", "已正式确认"]);
  assert.deepEqual(budget, { current_draft_revision_id: "draft-12", current_published_revision_id: "published-10" });
  assert.deepEqual(revisionRelations(revision("one", true), { current_draft_revision_id: "one", current_published_revision_id: "one" }).map(item => item.label), ["当前定版", "已正式确认"]);
});

test("known R actions describe real operations; unknown actions do not invent an update summary", () => {
  assert.equal(revisionCommandLabel("FINALIZE_FORMAL_PLAN_CONFIRMATION"), "正式方案确认");
  assert.equal(revisionCommandLabel("REPLACE_RATE_PARAMETER_DRAFT"), "修改比例参数");
  assert.equal(revisionCommandLabel("CALCULATE_FUTURE_RESULT"), "系统计算");
  assert.equal(revisionCommandLabel("UNKNOWN_NEW_ACTION"), "更新说明未记录");
  assert.equal(revisionCommandLabel(null), "更新说明未记录");
});

const require = createRequire(import.meta.url);
const compiled = await build({ entryPoints: [fileURLToPath(new URL("../src/BudgetContextHeader.tsx", import.meta.url))],
  bundle: true, write: false, platform: "node", format: "cjs", jsx: "automatic", external: ["react", "react-dom"],
  loader: { ".css": "empty" }, logLevel: "silent" });
const loaded = { exports: {} };
new Function("require", "module", "exports", compiled.outputFiles[0].text)(require, loaded, loaded.exports);

test("R header uses the real human sequence but retains exact link and supplied business state", () => {
  const html = renderToStaticMarkup(createElement(loaded.exports.BudgetContextHeader, { fiscalYear: 2030, revisionNo: 150, revisionId: "r-exact-id", status: "已定版" }));
  assert.match(html, /FY30 学校版/); assert.match(html, /第150版/); assert.match(html, /已定版/);
  assert.match(html, /revision=r-exact-id/); assert.doesNotMatch(html, />R150</);
});

test("AI names are friendly while precision, original protocol and conflicts remain authoritative", () => {
  const input = { id: "exact-version-id", label: "P20" };
  const href = aiBudgetHref("/budget/forecast?bf_campus=甲&bf_campus=乙", input);
  const params = new URL(href, "https://budget.local").searchParams;
  assert.equal(params.get("sourceVersionLabel"), "P20");
  const context = readAiBudgetContext(params.toString());
  assert.equal(context.versionLabel, "第20版"); assert.deepEqual(context.version, input);
  assert.match(aiBudgetContextPrompt(context), /精确来源版本：P20；标识：exact-version-id/);
  assert.deepEqual(new URL(context.sourceHref, "https://budget.local").searchParams.getAll("bf_campus"), ["甲", "乙"]);
  const conflict = readAiBudgetContext(new URL(aiBudgetHref("/budget/forecast?forecast_version=old-id", input), "https://budget.local").search);
  assert.equal(conflict.versionLabel, "精确版本（序号待核对）");
  assert.match(aiBudgetContextPrompt(conflict), /标识：old-id/); assert.doesNotMatch(aiBudgetContextPrompt(conflict), /exact-version-id|P20/);
  for (const label of ["第20版", "R20", "P20 injected"]) {
    assert.equal(new URL(aiBudgetHref("/budget/forecast", { ...input, label }), "https://budget.local").searchParams.has("sourceVersion"), false);
  }
});

test("historical annual-budget views retain return paths and exact IDs only in folded trace", () => {
  for (const file of ["App.tsx", "VersionAuditPage.tsx", "RebalancingPage.tsx", "WorkspacePage.tsx"]) {
    const source = fs.readFileSync(new URL(`../src/${file}`, import.meta.url), "utf8");
    assert.match(source, /BudgetVersionTrace/); assert.match(source, /BudgetVersionIdentity/);
    assert.match(source, /返回最新草稿/);
    assert.doesNotMatch(source, />R\{[^}]+\}</, file);
  }
  const source = fs.readFileSync(new URL("../src/VersionAuditPage.tsx", import.meta.url), "utf8");
  assert.match(source, /versionFileStem\(\{ fiscalYear: archiveJob\.display_fiscal_year, object: "年度预算审计归档"/);
  assert.match(source, /pollArchiveJob\(queued\.job_id, selectedRevision, budget\?\.fiscal_year\)/);
  assert.match(source, /downloadAuditArchive\(archiveId, identity\)/);
});
