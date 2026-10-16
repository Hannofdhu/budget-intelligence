// Component markup and real rendered callbacks, not a browser/focus/layout test.
// Native dialog behavior and real downloads are verified separately with computer use.
import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import fs from "node:fs";
import { build } from "esbuild";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { canConfirmForecast, defaultForecastStates, fixedForecastRulesVersion, groupForecastIssues, trialFinancialDraft } from "../src/forecastApi.ts";

const require = createRequire(import.meta.url);
async function compile(path) {
  const result = await build({ entryPoints: [fileURLToPath(new URL(path, import.meta.url))], bundle: true, write: false,
    platform: "node", format: "cjs", jsx: "automatic", external: ["react", "react-dom", "exceljs"], loader: { ".css": "empty" }, logLevel: "silent" });
  return customRequire => {
    const module = { exports: {} };
    new Function("require", "module", "exports", result.outputFiles[0].text)(customRequire ?? require, module, module.exports);
    return module.exports;
  };
}
const loadReview = await compile("../src/ForecastDataReview.tsx");
const loadPage = await compile("../src/ForecastPage.tsx");
const { ForecastDataNotice, ForecastDataReview, groupForecastMissingActuals, groupForecastWarningRecords,
  forecastReviewIssueTitle, forecastConfirmationHint } = loadReview();
const { ForecastSettingsDrawer, ForecastFixedRules, ForecastConfirmationEvidence } = loadPage();
const text = html => html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
const render = (Component, props) => renderToStaticMarkup(createElement(Component, props));
const dims = (more = {}) => ({ track: "智慧空间", campus: "西直门", product: "常规班", grade: "七年级", subject: "数学", ...more });
const missing = (more = {}) => ({ code: "FINALIZED_ACTUAL_MISSING", severity: "BLOCKING", season: "暑假", message: "技术证据：已定版实际缺失", dims: dims(), ...more });
const warning = (more = {}) => ({ code: "BASELINE_NEGATIVE_VALUE", severity: "WARNING", season: "暑假", message: "技术证据：原预算负数", dims: dims(), detail: { field: "opening", value: "-325.987654" }, ...more });
const confirmationInfo = (more = {}) => missing({ severity: "INFO", detail: { confirmation_exemption: "fy27-empty-budget-actual-v1", forecast_revenue: null, forecast_total: null }, ...more });
const confirmationCheck = (more = {}) => ({ season: "暑假", source_complete: true, budget_components_zero: true, no_frozen_carryins: true,
  revenue_matched: true, enrollment_matched: true, excluded_empty_rows: 47, known_revenue: "36123456.789012", actual_revenue: "36123456.789012",
  known_enrollment: 12642, actual_enrollment: 12642, ...more });
const version = (more = {}) => ({ id: "p14", sequence: 14, status: "DRAFT", assumptions: { trial_mode: false }, calculation: { ready: true, issues: [] }, ...more });
const owner = { userId: "owner", departmentId: "department", roles: ["DEPARTMENT_BUDGET_OWNER"] };
const find = (node, predicate) => Array.isArray(node) ? node.flatMap(child => find(child, predicate)) : !node || typeof node !== "object" ? []
  : [...(predicate(node) ? [node] : []), ...find(typeof node.type === "function" && node.type.name === "BudgetDataSection" ? node.type(node.props) : node.props?.children, predicate)];

test("the dashboard data notice counts only blockers and never exposes warning records or raw evidence", () => {
  const issues = [...Array.from({ length: 47 }, () => missing()), ...["暑假", "秋季", "寒假", "春季"].map(season => warning({ season }))];
  const before = JSON.stringify(issues), html = render(ForecastDataNotice, { issues, onOpen() {} });
  assert.match(text(html), /暑假实际待核对 · 47项 查看/);
  assert.equal((html.match(/<button\b/g) ?? []).length, 1);
  assert.match(html, /aria-label="暑假实际待核对 · 47项，查看明细"/);
  assert.doesNotMatch(html, /原预算记录|BASELINE_NEGATIVE_VALUE|技术证据|325\.987654|<details|<table/);
  assert.equal(JSON.stringify(issues), before);
  assert.equal(render(ForecastDataNotice, { issues: [warning()], onOpen() {} }), "");
  assert.equal(render(ForecastDataNotice, { issues: [] }), "");
});

test("the compact notice opens review through its actual callback and is disabled without one", () => {
  let opened = 0;
  const button = ForecastDataNotice({ issues: [missing()], onOpen: () => { opened++; } });
  assert.equal(button.type, "button"); assert.equal(button.props.disabled, false);
  button.props.onClick(); assert.equal(opened, 1);
  assert.match(render(ForecastDataNotice, { issues: [missing()] }), /<button[^>]*disabled=""/);
  const generic = render(ForecastDataNotice, { issues: [missing(), missing({ code: "SOURCE_UNMAPPED", season: "秋季" })], onOpen() {} });
  assert.match(text(generic), /数据待核对 · 2项/);
});

test("qualified empty-budget INFO records never become a main-screen task and never suppress a real blocker", () => {
  const issues = Array.from({ length: 47 }, (_, index) => confirmationInfo({ dims: dims({ campus: `零预算校区${index}` }) }));
  const before = JSON.stringify(issues);
  assert.equal(render(ForecastDataNotice, { issues, onOpen() {} }), "");
  assert.deepEqual(groupForecastMissingActuals(issues), []);
  assert.deepEqual(groupForecastWarningRecords(issues), []);
  const review = render(ForecastDataReview, { issues });
  assert.match(text(review), /没有待核对数据/);
  assert.doesNotMatch(review, /47|零预算校区|FINALIZED_ACTUAL_MISSING|confirmation_exemption|<pre/);
  const mixed = [...issues, missing({ code: "CURRENT_SOURCE_MISSING", season: "秋季" })];
  assert.match(text(render(ForecastDataNotice, { issues: mixed, onOpen() {} })), /数据待核对 · 1项/);
  assert.match(text(render(ForecastDataReview, { issues: mixed })), /其他待核对数据 1 项待核对/);
  const sameGroup = groupForecastIssues([confirmationInfo(), missing(), warning({ code: "FINALIZED_ACTUAL_MISSING" })]);
  assert.equal(sameGroup[0].severity, "BLOCKING"); assert.equal(sameGroup[0].issues.length, 3);
  assert.equal(groupForecastIssues([confirmationInfo(), warning({ code: "FINALIZED_ACTUAL_MISSING" })])[0].severity, "WARNING");
  assert.equal(JSON.stringify(issues), before);
});

test("confirmation reads server readiness: INFO cannot waive readiness, role, dirty, trial or genuine BLOCKING checks", () => {
  const issues = Array.from({ length: 47 }, () => confirmationInfo());
  const ready = version({ calculation: { ready: true, issues, confirmation_policy_version: "fy27-empty-budget-actual-v1", confirmation_checks: [confirmationCheck()] } });
  const before = JSON.stringify(ready);
  assert.equal(forecastConfirmationHint(ready, owner), ""); assert.equal(canConfirmForecast(ready, owner), true);
  const notReady = { ...ready, calculation: { ...ready.calculation, ready: false, forecast_complete: true } };
  assert.equal(canConfirmForecast(notReady, owner), false); assert.equal(forecastConfirmationHint(notReady, owner), "预测尚未通过正式检查");
  for (const code of ["FINALIZED_ACTUAL_MISSING", "SOURCE_UNMAPPED", "FINALIZED_TOTAL_MISMATCH", "BASELINE_EMPTY"]) {
    const blocked = { ...ready, calculation: { ...ready.calculation, issues: [...issues, missing({ code })] } };
    assert.equal(canConfirmForecast(blocked, owner), false, `${code} still blocks even an inconsistent ready=true response`);
    assert.notEqual(forecastConfirmationHint(blocked, owner), "");
  }
  assert.equal(canConfirmForecast(ready, owner, true), false);
  assert.equal(canConfirmForecast(ready, { ...owner, roles: [] }), false);
  assert.equal(canConfirmForecast({ ...ready, assumptions: { trial_mode: true } }, owner), false);
  assert.equal(JSON.stringify(ready), before);
});

test("saved confirmation checks and all INFO raw evidence are traceable only inside folded calculation help", () => {
  const issues = Array.from({ length: 47 }, (_, index) => confirmationInfo({ dims: dims({ campus: `零预算校区${index}` }) }));
  const saved = version({ states: { ...defaultForecastStates }, assumptions: { trial_mode: false, minimum_sample: 30, fixed_rules_version: fixedForecastRulesVersion },
    calculation: { ready: true, issues, fixed_rules_version: fixedForecastRulesVersion, confirmation_policy_version: "fy27-empty-budget-actual-v1", confirmation_checks: [confirmationCheck()] } });
  const before = JSON.stringify(saved), html = render(ForecastFixedRules, { version: saved }), readable = text(html);
  assert.match(html, /^<details class="forecast-how" aria-label="预测计算方法"><summary>预测怎么算/);
  assert.match(html, /<details class="forecast-how-details"><summary>确认核验记录/);
  assert.doesNotMatch(html, /<details[^>]*\bopen(?:=""|>)/);
  assert.match(readable, /冻结来源完整 通过/); assert.match(readable, /预算金额、人次为零 通过/);
  assert.match(readable, /无冻结结转 通过/); assert.match(readable, /结转收入勾稽 通过/); assert.match(readable, /人次勾稽 通过/);
  assert.match(readable, /不阻断的空组合 47项/);
  assert.match(readable, /已知明细结转收入（元） 36123456\.789012/); assert.match(readable, /部门实际结转收入（元） 36123456\.789012/);
  assert.match(readable, /已知明细人次 12642/); assert.match(readable, /部门实际人次 12642/);
  assert.equal((html.match(/<code>FINALIZED_ACTUAL_MISSING<\/code>/g) ?? []).length, 47);
  assert.match(html, /confirmation_exemption/); assert.match(html, /forecast_revenue&quot;: null/); assert.match(html, /forecast_total&quot;: null/);
  assert.match(html, /零预算校区46/); assert.match(html, /技术证据：已定版实际缺失/);
  assert.doesNotMatch(html, /<button|<input|<select|<form/); assert.equal(JSON.stringify(saved), before);
  assert.equal(render(ForecastConfirmationEvidence, { calculation: { issues: [] } }), "");
  const failedCheck = render(ForecastConfirmationEvidence, { calculation: { issues: [], confirmation_checks: [confirmationCheck({ revenue_matched: false, known_revenue: null, actual_revenue: 0 })] } });
  assert.match(text(failedCheck), /结转收入勾稽 未通过/); assert.match(text(failedCheck), /已知明细结转收入（元） 未提供/); assert.match(text(failedCheck), /部门实际结转收入（元） 0/);
});

test("missing frozen actuals are grouped by exact campus, retain all dimensions and source issues, and never combine similar campus names", () => {
  const issues = [missing(), missing({ season: "秋季", dims: dims({ subject: "英语" }) }), missing({ dims: dims({ campus: "西直门南" }) }),
    missing({ dims: { track: "智慧空间" } }), warning(), missing({ code: "CURRENT_SOURCE_MISSING" })];
  const before = JSON.stringify(issues), groups = groupForecastMissingActuals(issues);
  assert.equal(groups.length, 3);
  const exact = groups.find(group => group.campus === "西直门");
  assert.deepEqual(exact.seasons, ["暑假", "秋季"]); assert.equal(exact.issues.length, 2);
  assert.equal(exact.issues[0], issues[0]); assert.equal(exact.issues[1].dims.subject, "英语");
  assert.equal(groups.find(group => group.campus === "西直门南").issues.length, 1);
  assert.equal(groups.find(group => group.campus === "校区未填写").issues.length, 1);
  assert.equal(JSON.stringify(issues), before);
});

test("four identical cross-season negative warnings become one record but retain all four raw issues and source value precision", () => {
  const issues = ["春季", "暑假", "寒假", "秋季"].map(season => warning({ season }));
  const before = JSON.stringify(issues), records = groupForecastWarningRecords(issues);
  assert.equal(records.length, 1); assert.equal(records[0].issues.length, 4);
  assert.deepEqual(records[0].seasons, ["暑假", "秋季", "寒假", "春季"]);
  assert.equal(records[0].field, "opening"); assert.equal(records[0].value, "-325.987654");
  assert.equal(records[0].issues[0], issues[0]); assert.equal(JSON.stringify(issues), before);
  const html = render(ForecastDataReview, { issues });
  assert.match(html, /<details class="forecast-review-records"><summary>/);
  assert.doesNotMatch(html, /<details class="forecast-review-records"[^>]*open/);
  assert.match(text(html), /原预算记录 1项/); assert.match(text(html), /暑假、秋季、寒假、春季/);
  assert.match(text(html), /期初结转 -325\.987654 元/);
  assert.equal((html.match(/<code>BASELINE_NEGATIVE_VALUE<\/code>/g) ?? []).length, 4);
  assert.doesNotMatch(html, /<input|<select|<textarea/);
});

test("warning deduplication requires complete equal dimensions, field and raw value and never discards unknown records", () => {
  for (const change of [{ dims: dims({ campus: "西直门南" }) }, { dims: { track: "智慧空间" } },
    { detail: { field: "old", value: "-325.987654" } }, { detail: { field: "opening", value: -325.987654 } },
    { detail: { field: "opening" } }, { code: "ANOTHER_WARNING" }]) {
    assert.equal(groupForecastWarningRecords([warning(), warning(change)]).length, 2);
  }
  const unknowns = [warning({ detail: { field: "opening", value: null } }), warning({ detail: { field: "opening", value: 0 } }),
    warning({ detail: { field: "opening", value: "-" } })];
  const html = render(ForecastDataReview, { issues: unknowns });
  assert.match(text(html), /未提供/); assert.match(text(html), /0 元/); assert.match(text(html), /- 元/);
  assert.doesNotMatch(html, /NaN|Infinity|undefined/);
});

test("non-baseline warnings use a neutral folded reminder and field label without creating a dashboard blocker", () => {
  const sourceWarning = warning({ code: "SOURCE_NEGATIVE_VALUE", message: "技术证据：来源负数", detail: { field: "source_net_amount", value: "-0.012345" } });
  const baselineWarning = warning({ detail: { field: "unknown_baseline_field", value: "-1.234567" } });
  for (const issues of [[sourceWarning], [baselineWarning, sourceWarning]]) {
    const before = JSON.stringify(issues), html = render(ForecastDataReview, { issues });
    assert.match(html, /<details class="forecast-review-records"><summary><strong>其他提醒<\/strong>/);
    assert.doesNotMatch(html, /<details class="forecast-review-records"[^>]*open|<strong>原预算记录<\/strong>/);
    assert.match(text(html), /相关字段 -0\.012345/); assert.match(html, /<code>SOURCE_NEGATIVE_VALUE<\/code>/);
    assert.equal(render(ForecastDataNotice, { issues, onOpen() {} }), "", "warnings do not imply a blocker or main-screen task");
    assert.equal(JSON.stringify(issues), before);
  }
  const baseline = render(ForecastDataReview, { issues: [baselineWarning] });
  assert.match(baseline, /<strong>原预算记录<\/strong>/); assert.match(text(baseline), /原预算字段 -1\.234567/);
});

test("data review separates missing actuals, other blockers and folded records with complete evidence and no actual input controls", () => {
  const issues = [missing(), missing({ dims: dims({ grade: "八年级", subject: "英语" }) }),
    missing({ code: "SOURCE_UNMAPPED", season: "秋季" }), warning()];
  const before = JSON.stringify(issues), html = render(ForecastDataReview, { issues }), readable = text(html);
  assert.match(readable, /已定版实际 2 项待核对/); assert.match(readable, /其他待核对数据 1 项待核对/); assert.match(readable, /原预算记录 1项/);
  assert.match(readable, /此处不能补录已定版实际/);
  assert.match(html, /<details class="forecast-review-campus"><summary>/);
  assert.match(html, /aria-label="西直门待核对实际"/); assert.match(html, /tabindex="0"/);
  assert.match(readable, /校区 \/ 赛道 \/ 产品 年级 \/ 科目/); assert.match(readable, /八年级 英语/);
  assert.match(readable, /实际业务待对应/); assert.match(html, /<details class="forecast-review-evidence"><summary>技术信息/);
  assert.doesNotMatch(html, /<input|<select|<textarea/); assert.equal(JSON.stringify(issues), before);
  assert.match(text(render(ForecastDataReview, { issues: [] })), /没有待核对数据/);
});

test("data review refresh is available only through its supplied callback and respects the current busy state", () => {
  let calls = 0;
  const tree = ForecastDataReview({ issues: [missing()], onUpdate: () => { calls++; } });
  const [button] = find(tree, node => node.type === "button");
  assert.ok(button); assert.equal(button.props.disabled, false); button.props.onClick(); assert.equal(calls, 1);
  const readonly = render(ForecastDataReview, { issues: [missing()] });
  assert.doesNotMatch(readonly, /<button[^>]*>更新预测/);
  assert.match(readonly, />一键复制<|>导出当前表格</);
  assert.match(render(ForecastDataReview, { issues: [missing()], onUpdate() {}, busy: true }), /<button[^>]*disabled=""[^>]*>正在更新…/);
  assert.doesNotMatch(render(ForecastDataReview, { issues: [warning()], onUpdate() {} }), /<button/);
});

test("business confirmation explanations mirror permissions, draft safety, missing actuals, trial mode and server readiness without changing guards", () => {
  assert.equal(forecastConfirmationHint(null, owner), "先生成预测");
  assert.equal(forecastConfirmationHint(version({ status: "CONFIRMED" }), owner), "本次预测已确认");
  assert.equal(forecastConfirmationHint(version(), owner, true), "先保存本次修改");
  assert.equal(forecastConfirmationHint(version(), null), "仅部门预算负责人可确认");
  assert.equal(forecastConfirmationHint(version(), { ...owner, roles: ["DATA_MAINTAINER"] }), "仅部门预算负责人可确认");
  const blocked = version({ calculation: { ready: false, forecast_complete: true, issues: Array.from({ length: 47 }, () => missing()) } });
  const before = JSON.stringify(blocked);
  assert.equal(forecastConfirmationHint(blocked, owner), "暑假实际还有47项待核对");
  assert.equal(forecastConfirmationHint(version({ calculation: { ready: false, issues: [missing({ code: "CURRENT_SOURCE_MISSING" })] } }), owner), "还有1项数据待核对");
  assert.equal(forecastConfirmationHint(version({ assumptions: { trial_mode: true } }), owner), "试用预测不能正式确认");
  assert.equal(forecastConfirmationHint(version({ calculation: { ready: false, issues: [missing({ code: "BASELINE_EMPTY" })] } }), owner), "定版预算基准待核对");
  assert.equal(forecastConfirmationHint(version({ calculation: { ready: false, issues: [] } }), owner), "预测尚未通过正式检查");
  assert.equal(forecastConfirmationHint(version({ calculation: { ready: true, issues: [warning()] } }), owner), "");
  assert.equal(JSON.stringify(blocked), before);
  assert.equal(forecastReviewIssueTitle("SOURCE_UNMAPPED"), "实际业务待对应");
  assert.equal(forecastReviewIssueTitle("UNKNOWN_CODE"), "其他数据待核对");
});

const drawerProps = (more = {}) => ({ open: true, tab: "people", onTabChange() {}, onClose() {}, canMaintain: true,
  children: createElement("section", { id: "forecast-panel-people", role: "tabpanel" }, "人数调整内容"), footer: createElement("button", null, "保存并重算"), ...more });
test("the in-place settings drawer has native dialog semantics, three labeled tabs, content and one save footer", () => {
  const html = render(ForecastSettingsDrawer, drawerProps());
  assert.match(html, /<dialog[^>]*aria-labelledby="forecast-drawer-title"/);
  assert.match(html, /role="tablist"[^>]*aria-label="预测调整项目"/);
  assert.equal((html.match(/role="tab"/g) ?? []).length, 3);
  for (const name of ["人数调整", "业务参数", "数据维护"]) assert.match(text(html), new RegExp(name));
  assert.match(html, /id="forecast-tab-people"[^>]*aria-selected="true"/);
  assert.match(html, /aria-controls="forecast-panel-people"/);
  assert.match(text(html), /人数调整内容/); assert.equal((text(html).match(/保存并重算/g) ?? []).length, 1);
  assert.doesNotMatch(html, /href=|scrollIntoView|预测参数.*独立业务参数/);
  assert.match(text(render(ForecastSettingsDrawer, drawerProps({ tab: "data" }))), /数据核对/);
});

test("drawer tab changes, close and cancel are actual callbacks, and busy cancel cannot discard edits", () => {
  // Evaluate hooks synchronously; no simulated browser or dialog implementation.
  const { ForecastSettingsDrawer: Drawer } = loadPage(name => name === "react" ? { ...require(name), useRef: () => ({ current: null }), useEffect() {} } : require(name));
  const changes = []; let closes = 0, prevented = 0;
  const tree = Drawer(drawerProps({ onTabChange: tab => changes.push(tab), onClose: () => { closes++; } }));
  for (const button of find(tree, node => node.type === "button" && node.props.role === "tab")) button.props.onClick();
  assert.deepEqual(changes, ["people", "business", "data"]);
  const [close] = find(tree, node => node.type === "button" && node.props["aria-label"] === "关闭预测侧栏");
  close.props.onClick(); assert.equal(closes, 1);
  tree.props.onCancel({ preventDefault() { prevented++; } }); assert.equal(closes, 2); assert.equal(prevented, 1);
  const busy = Drawer(drawerProps({ busy: true, onClose: () => { closes++; } }));
  busy.props.onCancel({ preventDefault() { prevented++; } }); assert.equal(closes, 2); assert.equal(prevented, 2);
  assert.equal(find(busy, node => node.type === "button" && node.props["aria-label"] === "关闭预测侧栏")[0].props.disabled, true);
});

test("read-only users can view data maintenance but cannot select editable tabs", () => {
  const html = render(ForecastSettingsDrawer, drawerProps({ canMaintain: false, tab: "data", footer: undefined }));
  for (const tab of ["people", "business"]) assert.match(html, new RegExp(`id="forecast-tab-${tab}"[^>]*disabled=""`));
  const data = html.match(/<button[^>]*id="forecast-tab-data"[^>]*>/)?.[0] ?? "";
  assert.doesNotMatch(data, /disabled/); assert.match(data, /aria-selected="true"/);
  assert.doesNotMatch(text(html), /保存并重算/);
});

test("drawer open state keeps children mounted and does not invent new navigation or source-writing actions", () => {
  const open = render(ForecastSettingsDrawer, drawerProps()), closed = render(ForecastSettingsDrawer, drawerProps({ open: false }));
  for (const html of [open, closed]) assert.match(text(html), /人数调整内容/);
  const source = fs.readFileSync(new URL("../src/ForecastPage.tsx", import.meta.url), "utf8");
  assert.doesNotMatch(source, /function open(?:Trial)?Settings\(\)[\s\S]*?scrollIntoView/);
  const review = fs.readFileSync(new URL("../src/ForecastDataReview.tsx", import.meta.url), "utf8");
  assert.doesNotMatch(review, /method:\s*["'](?:POST|PUT|PATCH|DELETE)["']|fetch\(/);
});

// This harness calls the page's real event handlers over controlled top-level
// hook values. Effects are deliberately not run: it is not a DOM renderer,
// does not call the network, and cannot establish focus/modal/layout behavior.
const pageStateNames = ["identity", "data", "busy", "error", "notice", "drawerOpen", "drawerTab", "assumptions", "reason",
  "mapping", "adjustment", "showConfirm", "confirmationReason", "trialDraftChanged", "pendingNavigation", "selectedVersionId", "failure", "versionsOpen"];
function pageHarness(overrides = {}) {
  const assumptions = { states: { ...defaultForecastStates }, minimum_sample: 30, mappings: [], adjustments: [], forecast_scopes: [], fixed_rules_version: fixedForecastRulesVersion, trial_mode: false };
  const current = version({ states: { ...defaultForecastStates }, as_of: "2026-10-06 12:00:00", assumptions,
    calculation: { ready: true, fixed_rules_version: fixedForecastRulesVersion, forecast_complete: true,
      annual: { budget_revenue: "100000", forecast_revenue: "95000", gap: "5000", achievement: ".95" },
      quarters: [], rows: [], campuses: [], issues: [], rule_notes: [] } });
  const state = { identity: { ...owner, roles: ["DATA_MAINTAINER", "DEPARTMENT_BUDGET_OWNER"] }, data: { version: current, history: [current] },
    busy: "", error: "", notice: "", drawerOpen: false, drawerTab: "people", assumptions, reason: "",
    mapping: { source: { track: "", campus: "", product: "", grade: "", subject: "" }, target: { track: "", campus: "", product: "", grade: "", subject: "" }, reason: "" },
    adjustment: { season: "秋季", campus: "", kind: "additional", value: "", reason: "" },
    showConfirm: false, confirmationReason: "", trialDraftChanged: false, pendingNavigation: "", selectedVersionId: "", failure: null, versionsOpen: false, ...overrides };
  let cursor = 0, refCursor = 0;
  const refs = [], effects = [];
  const exports = loadPage(name => name === "react" ? { ...require(name),
    useState(initial) {
      const key = pageStateNames[cursor++];
      assert.ok(key, "a new top-level state must be explicitly modeled rather than silently bypassed");
      return [state[key], update => { state[key] = typeof update === "function" ? update(state[key]) : update; }];
    }, useRef: value => refs[refCursor++] ??= { current: value }, useEffect(effect) { effects.push(effect); }, useMemo: compute => compute(),
  } : require(name));
  const page = () => { cursor = 0; refCursor = 0; effects.length = 0; const tree = exports.ForecastPage(); assert.equal(cursor, pageStateNames.length); return tree; };
  return { state, page, current, mountVersionHistory() { const effect = effects.find(effect => effect.toString().includes("popstate")); assert.ok(effect); return effect(); } };
}
const elementText = node => Array.isArray(node) ? node.map(elementText).join("") : node && typeof node === "object" ? elementText(node.props?.children) : node == null || typeof node === "boolean" ? "" : String(node);
const buttons = (tree, label) => find(tree, node => node.type === "button" && elementText(node) === label);
const component = (tree, name) => find(tree, node => typeof node.type === "function" && node.type.name === name)[0];

test("one version panel owns the only history list and frozen snapshot entry, with header and records sharing its open state", () => {
  const h = pageHarness(), before = JSON.stringify(h.state.data);
  let tree = h.page();
  assert.equal(find(tree, node => node.type === "section" && node.props["aria-label"] === "预测记录").length, 1);
  assert.equal(find(tree, node => node.type === "select" && node.props["aria-label"] === "查看保存版本").length, 0);
  assert.equal(buttons(tree, "下载本次快照").length, 1);
  const panel = component(tree, "BudgetUtilityPanel");
  assert.equal(panel.props.open, false); assert.equal(panel.props.title, "预测版本与快照");
  buttons(tree, `版本记录 P${h.current.sequence}`)[0].props.onClick();
  tree = h.page(); assert.equal(component(tree, "BudgetUtilityPanel").props.open, true);
  component(tree, "BudgetUtilityPanel").props.onClose(); assert.equal(h.state.versionsOpen, false);
  component(h.page(), "ForecastDashboard").props.onRecords(); assert.equal(h.state.versionsOpen, true);
  assert.equal(JSON.stringify(h.state.data), before); assert.equal(h.state.busy, "");
});

test("the unique fixed confirmation bar owns the compact pending entry and opens existing data review without changing any data", () => {
  const h = pageHarness();
  h.state.data.version = { ...h.current, calculation: { ...h.current.calculation, ready: false,
    issues: [missing(), missing({ code: "UPSTREAM_HEADCOUNT_UNAVAILABLE", season: "寒假" }), warning(), confirmationInfo()] } };
  const before = JSON.stringify(h.state.data), tree = h.page();
  const bars = find(tree, node => node.type === "footer" && node.props["aria-label"] === "预测确认操作");
  assert.equal(bars.length, 1); assert.equal(buttons(tree, "待处理 2").length, 1);
  assert.equal(component(tree, "ForecastDataNotice"), undefined);
  buttons(bars[0], "待处理 2")[0].props.onClick();
  assert.equal(h.state.drawerOpen, true); assert.equal(h.state.drawerTab, "data");
  assert.equal(component(h.page(), "ForecastDataReview").props.issues, h.state.data.version.calculation.issues);
  assert.equal(buttons(h.page(), "确认本版预测结果")[0].props.disabled, true);
  assert.equal(JSON.stringify(h.state.data), before);
  const css = fs.readFileSync(new URL("../src/forecast.css", import.meta.url), "utf8");
  const source = fs.readFileSync(new URL("../src/ForecastPage.tsx", import.meta.url), "utf8");
  assert.match(css, /\.forecast-confirm\s*\{[^}]*position:\s*fixed/);
  assert.match(css, /padding-bottom:\s*calc\(var\(--forecast-action-height/);
  assert.match(source, /ResizeObserver\(measure\)/); assert.match(source, /observer\?\.disconnect\(\)/);
});

test("confirmation expands to one reason-required form and still uses the exact saved parent and original server readiness guard", async () => {
  const h = pageHarness(), original = globalThis.fetch, calls = [];
  globalThis.fetch = async (url, options) => { calls.push({ url, options }); return new Response(JSON.stringify({ ...h.state.data, version: { ...h.current, status: "CONFIRMED" } }), { status: 200 }); };
  try {
    buttons(h.page(), "确认本版预测结果")[0].props.onClick();
    let tree = h.page();
    assert.equal(buttons(tree, "确认本版预测结果").length, 1, "initial trigger is replaced by submit, not duplicated");
    assert.equal(buttons(tree, "确认本版预测结果")[0].props.disabled, true);
    const form = find(tree, node => node.type === "form")[0];
    find(form, node => node.type === "input")[0].props.onChange({ target: { value: "确认保存版本，不改预算" } });
    tree = h.page(); assert.equal(buttons(tree, "确认本版预测结果")[0].props.disabled, false);
    find(tree, node => node.type === "form")[0].props.onSubmit({ preventDefault() {} });
    await settlePage(() => !h.state.busy && h.state.data.version.status === "CONFIRMED");
    assert.equal(calls.length, 1); assert.equal(calls[0].url, `/api/v1/forecasts/fy27/${h.current.id}/confirm`);
    const payload = JSON.parse(calls[0].options.body); assert.equal(payload.reason, "确认保存版本，不改预算");
    assert.equal(h.state.showConfirm, false); assert.equal(h.state.confirmationReason, "");
  } finally { globalThis.fetch = original; }
});

test("the main page has one adjustment entry, an in-place three-panel drawer and saved help only at the footer", () => {
  const h = pageHarness(), tree = h.page();
  assert.equal(buttons(tree, "调整预测").length, 1);
  const heading = find(tree, node => node.type === "header" && node.props.className === "forecast-heading")[0];
  assert.equal(buttons(heading, "调整预测").length, 1, "adjustment is next to the unique page heading");
  assert.equal(find(tree, node => node.type === "h1" && elementText(node) === "FY27全年预测").length, 1);
  const drawer = component(tree, "ForecastSettingsDrawer");
  assert.ok(drawer); assert.equal(drawer.props.open, false);
  const panels = find(drawer.props.children, node => node.type === "section" && node.props.role === "tabpanel");
  assert.deepEqual(panels.map(node => [node.props.id, node.props.hidden]), [["forecast-panel-people", false], ["forecast-panel-business", true], ["forecast-panel-data", true]]);
  const editor = component(drawer.props.children, "ForecastTrialScopeEditor");
  assert.ok(editor); assert.equal(editor.key, h.current.id);
  assert.equal(component(tree, "ForecastDataReview"), component(drawer.props.children, "ForecastDataReview"), "maintenance is not repeated outside the drawer");
  const secondary = find(tree, node => node.type === "div" && node.props.className === "forecast-secondary")[0];
  const help = component(secondary, "ForecastFixedRules");
  assert.ok(help); assert.equal(help.props.version, h.current); assert.equal(help.props.onAdjust, undefined);
  buttons(heading, "调整预测")[0].props.onClick(); assert.equal(h.state.drawerOpen, true);
  const updated = component(h.page(), "ForecastSettingsDrawer");
  updated.props.onTabChange("business");
  assert.deepEqual(find(component(h.page(), "ForecastSettingsDrawer").props.children, node => node.type === "section" && node.props.role === "tabpanel").map(node => node.props.hidden), [true, false, true]);
  assert.equal(component(component(h.page(), "ForecastSettingsDrawer").props.children, "ForecastTrialScopeEditor").key, editor.key, "changing tabs never changes the editor's mount key");
  buttons(h.page(), "数据核对")[0].props.onClick();
  assert.equal(h.state.drawerTab, "data"); assert.equal(h.state.drawerOpen, true);
});

test("closing and reopening preserves pending drafts and tab state, and updates and confirmation remain blocked", () => {
  const h = pageHarness({ drawerOpen: true, drawerTab: "business", reason: "保留这条修改说明", trialDraftChanged: true });
  const before = JSON.stringify(h.state.assumptions), tree = h.page(), drawer = component(tree, "ForecastSettingsDrawer");
  drawer.props.onClose(); assert.equal(h.state.drawerOpen, false);
  assert.equal(h.state.drawerTab, "business"); assert.equal(h.state.reason, "保留这条修改说明"); assert.equal(h.state.trialDraftChanged, true);
  assert.equal(JSON.stringify(h.state.assumptions), before);
  const closed = h.page(), pending = find(closed, node => node.type === "div" && node.props.className === "forecast-pending")[0];
  assert.match(elementText(pending), /修改未保存/); buttons(pending, "继续编辑")[0].props.onClick();
  assert.equal(h.state.drawerOpen, true); assert.equal(h.state.drawerTab, "business");
  const reopened = h.page(); assert.equal(buttons(reopened, "更新预测")[0].props.disabled, true);
  assert.equal(buttons(reopened, "确认本版预测结果")[0].props.disabled, true);
  const hint = find(reopened, node => node.type === "p" && node.props.id === "forecast-confirm-hint")[0];
  assert.equal(elementText(hint), "先保存本次修改");
});

test("one unified save cannot skip unfinished people, mapping or independent-business input", () => {
  for (const unfinished of [
    { trialDraftChanged: true }, { adjustment: { season: "秋季", campus: "西直门", kind: "additional", value: "12", reason: "待加入" } },
    { mapping: { source: dims(), target: dims({ campus: "目标校区" }), reason: "待加入对应" } },
  ]) {
    const h = pageHarness({ drawerOpen: true, reason: "本次修改", ...unfinished });
    h.state.assumptions = { ...h.state.assumptions, minimum_sample: 31 };
    const tree = h.page(), drawer = component(tree, "ForecastSettingsDrawer");
    assert.equal(buttons(drawer.props.footer, "保存并重算").length, 1);
    assert.equal(buttons(tree, "保存并重算").length, 0, "save is supplied only by the single drawer footer");
    assert.equal(buttons(drawer.props.footer, "保存并重算")[0].props.disabled, true);
    assert.match(elementText(drawer.props.footer), /先完成当前明细调整/);
    assert.equal(buttons(tree, "更新预测")[0].props.disabled, true);
    assert.equal(buttons(tree, "确认本版预测结果")[0].props.disabled, true);
  }
  const ready = pageHarness({ drawerOpen: true, reason: "合法整套修改" });
  ready.state.assumptions = { ...ready.state.assumptions, minimum_sample: 31 };
  assert.equal(buttons(component(ready.page(), "ForecastSettingsDrawer").props.footer, "保存并重算")[0].props.disabled, false);
  for (const minimum_sample of [0, 1.1, NaN]) {
    ready.state.assumptions = { ...ready.state.assumptions, minimum_sample };
    assert.equal(buttons(component(ready.page(), "ForecastSettingsDrawer").props.footer, "保存并重算")[0].props.disabled, true);
  }
});

test("page navigation uses the real pending-edit guard, while modified clicks and clean navigation stay ordinary", () => {
  const h = pageHarness({ reason: "尚未保存的原因" });
  const header = component(h.page(), "FY27Header"), guard = header.props.onBeforeLeave;
  let prevented = 0;
  const event = (more = {}) => ({ button: 0, currentTarget: { href: "http://127.0.0.1:5188/budget/workspace?archive=fy27&step=5" }, preventDefault() { prevented++; }, ...more });
  for (const flag of ["metaKey", "ctrlKey", "shiftKey", "altKey"]) guard(event({ [flag]: true }));
  guard(event({ button: 1 })); assert.equal(prevented, 0); assert.equal(h.state.pendingNavigation, "");
  guard(event()); assert.equal(prevented, 1); assert.match(h.state.pendingNavigation, /archive=fy27/);
  const dialog = find(h.page(), node => node.type === "dialog" && node.props.className === "forecast-leave-dialog")[0];
  buttons(dialog, "继续编辑")[0].props.onClick(); assert.equal(h.state.pendingNavigation, ""); assert.equal(h.state.reason, "尚未保存的原因");
  h.state.reason = ""; component(h.page(), "FY27Header").props.onBeforeLeave(event()); assert.equal(prevented, 1);
  h.state.busy = "保存预测"; component(h.page(), "FY27Header").props.onBeforeLeave(event());
  assert.equal(prevented, 2); assert.equal(h.state.notice, "当前操作完成后再切换");
  const source = fs.readFileSync(new URL("../src/ForecastPage.tsx", import.meta.url), "utf8");
  assert.match(source, /window\.addEventListener\("beforeunload", protect\)/); assert.match(source, /window\.removeEventListener\("beforeunload", protect\)/);
});

test("continuing from the leave modal returns directly to the last editing tab without dropping pending input", () => {
  for (const drawerTab of ["people", "business", "data"]) {
    const h = pageHarness({ drawerTab, drawerOpen: false, reason: "本次尚未保存的修改", trialDraftChanged: drawerTab === "business",
      adjustment: { season: "秋季", campus: "西直门", kind: "additional", value: "17", reason: "保留的人数草稿" },
      mapping: { source: dims(), target: dims({ campus: "预算校区" }), reason: "保留的对应草稿" } });
    h.state.assumptions = { ...h.state.assumptions, minimum_sample: 31 };
    const before = JSON.stringify({ assumptions: h.state.assumptions, reason: h.state.reason,
      adjustment: h.state.adjustment, mapping: h.state.mapping, trialDraftChanged: h.state.trialDraftChanged });
    let prevented = 0;
    component(h.page(), "FY27Header").props.onBeforeLeave({ button: 0, currentTarget: { href: "http://127.0.0.1:5188/budget/workspace?archive=fy27&step=5" }, preventDefault() { prevented++; } });
    assert.equal(prevented, 1); assert.notEqual(h.state.pendingNavigation, "");
    const leave = find(h.page(), node => node.type === "dialog" && node.props.className === "forecast-leave-dialog")[0];
    buttons(leave, "继续编辑")[0].props.onClick();
    assert.equal(h.state.pendingNavigation, ""); assert.equal(h.state.drawerOpen, true); assert.equal(h.state.drawerTab, drawerTab);
    const resumed = component(h.page(), "ForecastSettingsDrawer"); assert.equal(resumed.props.open, true); assert.equal(resumed.props.tab, drawerTab);
    assert.equal(JSON.stringify({ assumptions: h.state.assumptions, reason: h.state.reason,
      adjustment: h.state.adjustment, mapping: h.state.mapping, trialDraftChanged: h.state.trialDraftChanged }), before);
  }
});

test("continuing an unsaved confirmation explanation stays at confirmation instead of opening unrelated parameter editing", () => {
  const h = pageHarness({ drawerTab: "business", drawerOpen: false, showConfirm: true, confirmationReason: "保留这条正式确认说明" });
  let prevented = 0;
  component(h.page(), "FY27Header").props.onBeforeLeave({ button: 0, currentTarget: { href: "http://127.0.0.1:5188/budget/workspace?archive=fy27&step=5" }, preventDefault() { prevented++; } });
  assert.equal(prevented, 1); assert.notEqual(h.state.pendingNavigation, "");
  const leave = find(h.page(), node => node.type === "dialog" && node.props.className === "forecast-leave-dialog")[0];
  buttons(leave, "继续编辑")[0].props.onClick();
  assert.equal(h.state.pendingNavigation, ""); assert.equal(h.state.drawerOpen, false);
  assert.equal(h.state.showConfirm, true); assert.equal(h.state.confirmationReason, "保留这条正式确认说明");
  const tree = h.page(); assert.equal(component(tree, "ForecastSettingsDrawer").props.open, false);
  const form = find(tree, node => node.type === "form")[0]; assert.ok(form);
  assert.equal(find(form, node => node.type === "input")[0].props.value, "保留这条正式确认说明");
});

test("confirmation's disabled button is paired with the actual business explanation and read-only controls stay disabled", () => {
  const h = pageHarness();
  h.state.data.version = { ...h.current, calculation: { ...h.current.calculation, ready: false, issues: Array.from({ length: 47 }, () => missing()) } };
  const tree = h.page(); assert.equal(buttons(tree, "确认本版预测结果")[0].props.disabled, true);
  const hint = find(tree, node => node.type === "p" && node.props.id === "forecast-confirm-hint")[0];
  assert.equal(elementText(hint), "暑假实际还有47项待核对");
  assert.equal(buttons(tree, "确认本版预测结果")[0].props["aria-describedby"], hint.props.id);
  const readonly = pageHarness({ identity: { ...owner, roles: [] } }), readonlyTree = readonly.page();
  assert.equal(buttons(readonlyTree, "调整预测").length, 0); assert.equal(buttons(readonlyTree, "更新预测")[0].props.disabled, true);
  const drawer = component(readonlyTree, "ForecastSettingsDrawer"); assert.equal(drawer.props.canMaintain, false); assert.equal(drawer.props.footer, false);
  assert.equal(component(readonlyTree, "ForecastDataReview").props.onUpdate, undefined);
  for (const fieldset of find(drawer.props.children, node => node.type === "fieldset")) assert.equal(fieldset.props.disabled, true);
});

test("the confirmation area names this version's result and keeps exempt evidence at the saved footer without confirming it", () => {
  const h = pageHarness();
  h.state.data.version = { ...h.current, calculation: { ...h.current.calculation,
    issues: Array.from({ length: 47 }, () => confirmationInfo()), confirmation_policy_version: "fy27-empty-budget-actual-v1", confirmation_checks: [confirmationCheck()] } };
  const before = JSON.stringify(h.state.data.version), tree = h.page();
  const confirm = find(tree, node => node.type === "footer" && node.props.className === "forecast-confirm")[0];
  assert.equal(buttons(confirm, "确认本版预测结果").length, 1); assert.equal(buttons(confirm, "确认本版预测结果")[0].props.disabled, false);
  assert.doesNotMatch(elementText(confirm), /47|待核对|逐项/); assert.equal(h.state.showConfirm, false);
  assert.equal(find(confirm, node => node.type === "p" && node.props.id === "forecast-confirm-hint").length, 0);
  const help = component(find(tree, node => node.type === "div" && node.props.className === "forecast-secondary")[0], "ForecastFixedRules");
  assert.equal(help.props.version, h.state.data.version); assert.equal(JSON.stringify(h.state.data.version), before);
  h.state.data.version = { ...h.state.data.version, calculation: { ...h.state.data.version.calculation,
    issues: [...h.state.data.version.calculation.issues, missing({ code: "FINALIZED_TOTAL_MISMATCH" })] } };
  const blocked = h.page();
  assert.equal(buttons(blocked, "确认本版预测结果")[0].props.disabled, true);
  assert.equal(elementText(find(blocked, node => node.type === "p" && node.props.id === "forecast-confirm-hint")[0]), "还有1项数据待核对");
});

test("drawer tabs use one keyboard stop and real arrow, Home and End handlers without enabling read-only edit tabs", () => {
  const { ForecastSettingsDrawer: Drawer } = loadPage(name => name === "react" ? { ...require(name), useRef: () => ({ current: null }), useEffect() {} } : require(name));
  const changes = []; let prevented = 0;
  const tree = Drawer(drawerProps({ tab: "business", onTabChange: tab => changes.push(tab) }));
  const tabs = find(tree, node => node.type === "button" && node.props.role === "tab");
  assert.deepEqual(tabs.map(tab => tab.props.tabIndex), [-1, 0, -1]);
  const key = (tab, name) => tab.props.onKeyDown({ key: name, preventDefault() { prevented++; } });
  key(tabs[1], "ArrowRight"); key(tabs[1], "ArrowLeft"); key(tabs[1], "Home"); key(tabs[1], "End");
  key(tabs[0], "ArrowLeft"); key(tabs[2], "ArrowRight"); key(tabs[1], "Escape");
  assert.deepEqual(changes, ["data", "people", "people", "data", "data", "people"]); assert.equal(prevented, 6);
  const readonlyChanges = [], readonly = Drawer(drawerProps({ tab: "data", canMaintain: false, onTabChange: tab => readonlyChanges.push(tab) }));
  const readonlyTabs = find(readonly, node => node.type === "button" && node.props.role === "tab");
  assert.deepEqual(readonlyTabs.map(tab => [tab.props.disabled, tab.props.tabIndex]), [[true, -1], [true, -1], [false, 0]]);
  for (const name of ["ArrowRight", "ArrowLeft", "Home", "End"]) key(readonlyTabs[2], name);
  assert.deepEqual(readonlyChanges, ["data", "data", "data", "data"]);
});

test("the heading defaults a clean edit to people but reopens the last tab for a pending draft", () => {
  const clean = pageHarness({ drawerTab: "data" });
  buttons(clean.page(), "调整预测")[0].props.onClick();
  assert.equal(clean.state.drawerOpen, true); assert.equal(clean.state.drawerTab, "people");
  const dirty = pageHarness({ drawerTab: "business", reason: "继续这条草稿" });
  buttons(dirty.page(), "调整预测")[0].props.onClick();
  assert.equal(dirty.state.drawerOpen, true); assert.equal(dirty.state.drawerTab, "business");
});

function scopeHarness({ dirty = true, disabled = false } = {}) {
  const scopes = [
    { dims: dims(), kind: "UNBUDGETED_BUSINESS", reason: "原业务参数", additional: { 秋季: 0 },
      financial: { 秋季: { price: "3000.123456", discount: "0.85123456789", recognition: "0.9", renewal: "0.7", share: "0.2", method: "PEER_GRADE_WEIGHTED" },
        寒假: { price: "3100", discount: "0.9", recognition: "1", renewal: "0.7", share: "0.2", method: "PEER_GRADE_WEIGHTED" } } },
    { dims: dims({ campus: "西直门南" }), kind: "UNBUDGETED_BUSINESS", reason: "第二条业务", additional: { 秋季: 0 },
      financial: { 秋季: { price: "4000", discount: "0.9", recognition: "1", renewal: "0.8", share: "0.2", method: "PEER_GRADE_WEIGHTED" } } },
  ];
  const names = ["season", "query", "page", "selected", "draft", "error", "seasonFilter"];
  const state = { season: "秋季", seasonFilter: "秋季", query: "", page: 0, selected: 0, draft: { ...trialFinancialDraft(scopes[0], "秋季", false), ...(dirty ? { additional: "17", reason: "保留的手动修改" } : {}) }, error: "" };
  const changed = [], dirtyReports = [];
  let cursor = 0;
  const exports = loadPage(name => name === "react" ? { ...require(name),
    useState() { const key = names[cursor++]; assert.ok(key); return [state[key], update => { state[key] = typeof update === "function" ? update(state[key]) : update; }]; },
    useRef: value => ({ current: value }), useEffect() {}, useMemo: compute => compute(),
  } : require(name));
  const editor = () => { cursor = 0; const tree = exports.ForecastTrialScopeEditor({ scopes, states: defaultForecastStates, legacyTrial: false, disabled,
    onChange: next => changed.push(next), onDraftChange: value => dirtyReports.push(value) }); assert.equal(cursor, names.length); return tree; };
  return { scopes, state, editor, changed, dirtyReports };
}

test("independent-business dirty drafts refuse detail and season changes, retain exact input, and require explicit discard", () => {
  const h = scopeHarness(), before = JSON.stringify(h.scopes), draft = JSON.stringify(h.state.draft);
  buttons(h.editor(), "调整")[1].props.onClick();
  assert.equal(h.state.selected, 0); assert.equal(JSON.stringify(h.state.draft), draft);
  assert.match(h.state.error, /先加入本次修改，或放弃当前编辑，再切换明细/);
  assert.match(elementText(h.editor()), /先加入本次修改，或放弃当前编辑，再切换明细/);
  const season = find(h.editor(), node => typeof node.type === "function" && node.type.name === "FilterSelect")[0];
  season.props.onChange("寒假");
  assert.equal(h.state.season, "秋季"); assert.equal(h.state.selected, 0); assert.equal(JSON.stringify(h.state.draft), draft);
  assert.match(h.state.error, /先加入本次修改，或放弃当前编辑，再切换季度/);
  const discard = buttons(h.editor(), "放弃当前编辑"); assert.equal(discard.length, 1);
  assert.equal(buttons(h.editor(), "取消").length, 0); discard[0].props.onClick();
  assert.equal(h.state.selected, null); assert.equal(h.state.draft, null); assert.equal(h.state.error, "");
  assert.equal(h.changed.length, 0); assert.equal(JSON.stringify(h.scopes), before);
});

test("adding an independent-business edit uses the real validation callback, clears only staged input and preserves untouched raw precision", () => {
  const h = scopeHarness(), before = JSON.stringify(h.scopes);
  buttons(h.editor(), "加入本次修改")[0].props.onClick();
  assert.equal(h.changed.length, 1); assert.equal(h.changed[0][0].additional.秋季, 17);
  assert.equal(h.changed[0][0].reason, "保留的手动修改");
  assert.deepEqual(h.changed[0][0].financial, h.scopes[0].financial);
  assert.equal(h.changed[0][1], h.scopes[1]); assert.equal(JSON.stringify(h.scopes), before);
  assert.equal(h.state.selected, null); assert.equal(h.state.draft, null); assert.equal(h.state.error, "");
  const invalid = scopeHarness(); invalid.state.draft.additional = "1.5";
  const preserved = JSON.stringify(invalid.state.draft); buttons(invalid.editor(), "加入本次修改")[0].props.onClick();
  assert.equal(invalid.changed.length, 0); assert.equal(JSON.stringify(invalid.state.draft), preserved); assert.match(invalid.state.error, /整数人次/);
  const readonly = scopeHarness({ disabled: true }); buttons(readonly.editor(), "加入本次修改")[0].props.onClick();
  assert.equal(readonly.changed.length, 0); assert.equal(readonly.state.selected, 0);
});

test("independent-business multi-quarter filtering exports every selected quarter but only edits the explicitly chosen row quarter", () => {
  const h = scopeHarness({ dirty: false }), before = JSON.stringify(h.scopes), previousFrame = globalThis.requestAnimationFrame;
  globalThis.requestAnimationFrame = callback => { callback(); return 1; };
  try {
    h.state.selected = null; h.state.draft = null;
    const filter = tree => find(tree, node => typeof node.type === "function" && node.type.name === "FilterSelect")[0];
    filter(h.editor()).props.onChange(["秋季", "寒假"]);
    const tree = h.editor(), [table] = find(tree, node => typeof node.type === "function" && node.type.name === "ActionTable");
    assert.equal(table.props.exportData.rows.length, 4);
    assert.deepEqual(table.props.exportData.rows.map(row => row.season), ["秋季", "寒假", "秋季", "寒假"]);
    assert.equal(table.props.exportData.rows[0].price, "3000.123456");
    assert.equal(table.props.exportData.rows[1].price, "3100");
    assert.equal(table.props.exportData.rows[3].price, undefined, "missing future values remain absent rather than fabricated zero");
    const headers = find(find(table, node => node.type === "thead")[0], node => node.type === "th").map(elementText);
    assert.deepEqual(headers.slice(0, 3), ["季度", "校区 / 赛道 / 产品", "年级 / 科目"]);
    assert.deepEqual(headers.slice(3), ["学费（元）", "折扣", "当季结转比例", "续班率", "新生占比", "后续新增", "操作"]);
    const adjust = find(tree, node => node.type === "button" && node.props["aria-label"]?.startsWith("调整寒假"))[0];
    adjust.props.onClick();
    assert.equal(h.state.season, "寒假"); assert.equal(h.state.selected, 0); assert.equal(h.state.draft.price, "3100");
    assert.match(elementText(h.editor()), /寒假 · 西直门/);
    h.state.draft = { ...h.state.draft, price: "3200.123456", reason: "只改寒假单条参数" };
    buttons(h.editor(), "加入本次修改")[0].props.onClick();
    assert.equal(h.changed.length, 1);
    assert.equal(h.changed[0][0].financial.寒假.price, "3200.123456");
    assert.deepEqual(h.changed[0][0].financial.秋季, h.scopes[0].financial.秋季);
    assert.equal(h.changed[0][1], h.scopes[1]);
    assert.equal(JSON.stringify(h.scopes), before);
    const dirty = scopeHarness();
    filter(dirty.editor()).props.onChange(["暑假", "春季"]);
    assert.equal(dirty.state.seasonFilter, "秋季"); assert.equal(dirty.state.season, "秋季");
    assert.equal(dirty.changed.length, 0); assert.match(dirty.state.error, /先加入本次修改/);
  } finally { if (previousFrame === undefined) delete globalThis.requestAnimationFrame; else globalThis.requestAnimationFrame = previousFrame; }
});

const settlePage = async predicate => {
  for (let attempt = 0; attempt < 40 && !predicate(); attempt++) await new Promise(resolve => setImmediate(resolve));
  assert.ok(predicate(), "the real page request must settle");
};
function forecastBrowser() {
  const previous = globalThis.window;
  const urls = [], listeners = new Map();
  const setUrl = value => { const url = new URL(String(value), "http://127.0.0.1:5188"); globalThis.window.location.href = url.href; globalThis.window.location.search = url.search; };
  globalThis.window = { location: { href: "http://127.0.0.1:5188/budget/forecast?archive=fy27", search: "?archive=fy27" },
    history: { state: null, replaceState(_state, _title, url) { urls.push(String(url)); setUrl(url); } },
    addEventListener(name, callback) { const set = listeners.get(name) ?? new Set(); set.add(callback); listeners.set(name, set); }, removeEventListener(name, callback) { listeners.get(name)?.delete(callback); } };
  return { urls, navigate(url) { setUrl(url); for (const callback of listeners.get("popstate") ?? []) callback(); }, restore() { if (previous === undefined) delete globalThis.window; else globalThis.window = previous; } };
}

test("forecast history navigation reads the exact URL version with GET and preserves scope, while dirty changes keep the current version", async () => {
  const h = pageHarness(), browser = forecastBrowser(), previous = globalThis.fetch, calls = [];
  let cleanup;
  globalThis.fetch = async (url, options) => { calls.push({ url, options }); return Response.json({ version: { ...h.current, id: "saved-p3", sequence: 3 }, history: [h.current] }); };
  try {
    h.page(); cleanup = h.mountVersionHistory();
    browser.navigate("/budget/forecast?archive=fy27&forecast_version=saved-p3&bf_scope=1&bf_campus=西直门&bf_campus=中关村&bf_season=暑假&bf_season=秋季");
    await settlePage(() => !h.state.busy && h.state.selectedVersionId === "saved-p3");
    assert.equal(calls.length, 1); assert.equal(calls[0].url, "/api/v1/forecasts/fy27/versions/saved-p3"); assert.equal(calls[0].options.method, undefined);
    assert.deepEqual(new URLSearchParams(window.location.search).getAll("bf_campus"), ["西直门", "中关村"]);
    const header = component(h.page(), "FY27Header"); assert.deepEqual(header.props.sourceVersion, { id: "saved-p3", label: "P3" });
    cleanup(); h.state.reason = "保留本版未保存修改"; h.page(); cleanup = h.mountVersionHistory();
    const before = JSON.stringify(h.state.data);
    browser.navigate("/budget/forecast?archive=fy27&forecast_version=saved-p4&bf_scope=1&bf_season=春季");
    assert.equal(calls.length, 1); assert.equal(h.state.reason, "保留本版未保存修改"); assert.equal(h.state.selectedVersionId, "saved-p3");
    assert.equal(new URLSearchParams(window.location.search).get("forecast_version"), "saved-p3");
    assert.equal(new URLSearchParams(window.location.search).get("bf_season"), "春季");
    assert.match(h.state.notice, /先完成或放弃当前操作/); assert.equal(JSON.stringify(h.state.data), before);
    cleanup(); h.state.reason = ""; h.page(); cleanup = h.mountVersionHistory();
    globalThis.fetch = async (url, options) => { calls.push({ url, options }); return Response.json({ detail: "保存版本暂时无法读取" }, { status: 503 }); };
    browser.navigate("/budget/forecast?archive=fy27&forecast_version=saved-p5&bf_scope=1&bf_campus=未知校区");
    await settlePage(() => !h.state.busy && Boolean(h.state.error));
    assert.equal(h.state.selectedVersionId, "saved-p3"); assert.equal(JSON.stringify(h.state.data), before);
    assert.equal(new URLSearchParams(window.location.search).get("forecast_version"), "saved-p3");
    assert.equal(new URLSearchParams(window.location.search).get("bf_campus"), "未知校区");
    assert.ok(calls.every(call => call.options.method !== "POST"));
  } finally { cleanup?.(); globalThis.fetch = previous; browser.restore(); }
});

test("failed update preserves saved P16 and retry sends actual POST with the same intent rather than a saved-result GET", async () => {
  const h = pageHarness();
  h.state.data.version = { ...h.current, id: "saved-p16", sequence: 16, as_of: "2026-10-06", created_at: "2026-10-07T00:37:00+08:00", status: "CONFIRMED" };
  const saved = JSON.stringify(h.state.data), previous = globalThis.fetch, calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    if (calls.length === 1) return new Response(JSON.stringify({ detail: { code: "SOURCE_QUERY_TIMEOUT", message: "观远只读查询超时", retryable: true, diagnostic_id: "trace-test-timeout", stage: "query", retained_version: h.state.data.version } }), { status: 409 });
    return new Response(JSON.stringify({ ...h.state.data, version: { ...h.state.data.version, id: `updated-${calls.length}`, sequence: 17 } }), { status: 200 });
  };
  try {
    buttons(h.page(), "更新预测")[0].props.onClick();
    await settlePage(() => !h.state.busy && Boolean(h.state.error));
    assert.equal(JSON.stringify(h.state.data), saved);
    assert.equal(h.state.failure.error.detail.stage, "query");
    assert.equal(h.state.failure.error.detail.diagnostic_id, "trace-test-timeout");
    const failed = elementText(h.page());
    assert.match(failed, /更新未完成.*读取数据超时.*上次保存 P16.*2026-10-06/);
    assert.doesNotMatch(failed, /已读取最新|新预测已保存/);
    buttons(h.page(), "重试更新")[0].props.onClick();
    await settlePage(() => !h.state.busy && h.state.data.version.sequence === 17);
    assert.deepEqual(calls.slice(0, 2).map(call => [call.url, call.options.method]), [["/api/v1/forecasts/fy27/refresh", "POST"], ["/api/v1/forecasts/fy27/refresh", "POST"]]);
    assert.equal(calls[0].options.headers["Idempotency-Key"], calls[1].options.headers["Idempotency-Key"]);
    assert.equal(calls[0].options.body, calls[1].options.body);
    assert.equal(h.state.notice, "新预测已保存");
    buttons(h.page(), "更新预测")[0].props.onClick();
    await settlePage(() => !h.state.busy && calls.length === 3);
    assert.notEqual(calls[2].options.headers["Idempotency-Key"], calls[1].options.headers["Idempotency-Key"]);
  } finally { globalThis.fetch = previous; }
});

test("ambiguous network failure never claims that the server saved nothing and nonretryable login errors give a usable next step", async () => {
  const h = pageHarness(), previous = globalThis.fetch;
  globalThis.fetch = async () => { throw new TypeError("fetch failed"); };
  try {
    buttons(h.page(), "更新预测")[0].props.onClick();
    await settlePage(() => !h.state.busy && Boolean(h.state.error));
    assert.match(elementText(h.page()), /本次更新是否完成尚未确认/);
    assert.doesNotMatch(elementText(h.page()), /本次未保存|本次未生成/);
    globalThis.fetch = async () => new Response(JSON.stringify({ detail: { code: "SOURCE_LOGIN_REQUIRED", message: "观远登录已失效", retryable: false, retained_version: h.current } }), { status: 409 });
    buttons(h.page(), "更新预测")[0].props.onClick();
    await settlePage(() => !h.state.busy && h.state.failure.error.detail.retryable === false);
    assert.match(elementText(h.page()), /请先检查观远登录或数据权限，再更新预测/);
    assert.equal(buttons(h.page(), "重试更新").length, 0);
  } finally { globalThis.fetch = previous; }
});

test("history selection reads the exact saved version, is readonly for all mutations, and returns through the current GET", async () => {
  const h = pageHarness(), browser = forecastBrowser(), previous = globalThis.fetch, calls = [];
  const current = { ...h.current, id: "saved-p16", sequence: 16, status: "CONFIRMED", as_of: "2026-10-06", created_at: "2026-10-07T00:37:00+08:00" };
  const old = { ...current, id: "saved-p3", sequence: 3, status: "DRAFT", as_of: "2026-10-01", created_at: "2026-10-02T09:13:00+08:00", calculation: { ...current.calculation, annual: { ...current.calculation.annual, forecast_revenue: "87654.321234" } } };
  const envelope = { ...h.state.data, version: current, latest_version_id: current.id, history: [current, old] };
  h.state.data = envelope;
  globalThis.fetch = async (url, options) => { calls.push({ url, options }); return new Response(JSON.stringify({ ...envelope, version: url.endsWith(`/versions/${old.id}`) ? old : current }), { status: 200 }); };
  try {
    browser.navigate("/budget/forecast?archive=fy27&bf_scope=1&bf_season=秋季&bf_season=寒假&bf_campus=校区甲");
    const selector = find(h.page(), node => node.type === "button" && node.props["aria-label"] === "查看预测 P3")[0];
    selector.props.onClick();
    await settlePage(() => !h.state.busy && h.state.selectedVersionId === old.id);
    assert.equal(calls[0].url, "/api/v1/forecasts/fy27/versions/saved-p3");
    assert.equal(calls[0].options.method, undefined);
    assert.deepEqual(h.state.data.version.calculation, old.calculation);
    const readonly = h.page();
    assert.match(elementText(readonly), /P3 保存结果 · 只读查看/);
    assert.equal(buttons(readonly, "调整预测").length, 0);
    assert.equal(buttons(readonly, "更新预测").length, 0);
    assert.equal(buttons(readonly, "确认本版预测结果").length, 0);
    const drawer = component(readonly, "ForecastSettingsDrawer");
    assert.equal(drawer.props.canMaintain, false); assert.equal(drawer.props.footer, false);
    assert.equal(component(drawer.props.children, "ForecastDataReview").props.onUpdate, undefined);
    assert.equal(component(drawer.props.children, "ForecastTrialScopeEditor").props.disabled, true);
    assert.match(browser.urls[0], /forecast_version=saved-p3/);
    const banner = find(readonly, node => node.type === "section" && node.props.className === "forecast-readonly")[0];
    assert.equal(component(readonly, "BudgetUtilityPanel").props.open, false);
    const inlineReturn = buttons(banner, "返回当前版本");
    assert.equal(inlineReturn.length, 1); assert.equal(inlineReturn[0].props.className, "forecast-primary");
    assert.equal(buttons(component(readonly, "BudgetUtilityPanel"), "返回当前版本").length, 1, "menu shortcut is retained");
    inlineReturn[0].props.onClick();
    await settlePage(() => !h.state.busy && h.state.selectedVersionId === "");
    assert.equal(calls[1].url, "/api/v1/forecasts/fy27/current");
    assert.equal(h.state.data.version.id, current.id);
    assert.equal(buttons(h.page(), "更新预测").length, 1);
    assert.equal(buttons(h.page(), "返回当前版本").length, 0, "return entry disappears on current version");
    assert.doesNotMatch(browser.urls[1], /forecast_version/);
    const scope = new URLSearchParams(window.location.search);
    assert.deepEqual(scope.getAll("bf_season"), ["秋季", "寒假"]); assert.equal(scope.get("bf_campus"), "校区甲"); assert.equal(scope.get("bf_scope"), "1");
    assert.ok(calls.every(call => call.options.method !== "POST"));
  } finally { globalThis.fetch = previous; browser.restore(); }
});

test("visible historical return preserves identity, busy and unsaved guards without mutating a saved forecast", async () => {
  const h = pageHarness({ selectedVersionId: "saved-p3" }), before = JSON.stringify(h.state.data), previous = globalThis.fetch;
  const inlineReturn = () => buttons(find(h.page(), node => node.type === "section" && node.props.className === "forecast-readonly")[0], "返回当前版本")[0];
  const calls = []; globalThis.fetch = async (...args) => { calls.push(args); throw new Error("must not request while blocked"); };
  try {
    assert.equal(inlineReturn().props.disabled, false);
    h.state.busy = "读取当前版本"; assert.equal(inlineReturn().props.disabled, true);
    h.state.busy = ""; h.state.reason = "未保存";
    assert.equal(inlineReturn().props.disabled, true); inlineReturn().props.onClick();
    h.state.reason = ""; h.state.identity = null;
    assert.equal(inlineReturn().props.disabled, true); inlineReturn().props.onClick();
    assert.equal(calls.length, 0); assert.equal(JSON.stringify(h.state.data), before); assert.equal(h.state.selectedVersionId, "saved-p3");
  } finally { globalThis.fetch = previous; }
});

test("failed inline return retains the historical snapshot and scoped URL, and can retry with GET only", async () => {
  const h = pageHarness({ selectedVersionId: "saved-p3" }), browser = forecastBrowser(), previous = globalThis.fetch, calls = [];
  h.state.data.version = { ...h.current, id: "saved-p3", sequence: 3 };
  const before = JSON.stringify(h.state.data);
  const inlineReturn = () => buttons(find(h.page(), node => node.type === "section" && node.props.className === "forecast-readonly")[0], "返回当前版本")[0];
  browser.navigate("/budget/forecast?archive=fy27&forecast_version=saved-p3&bf_scope=1&bf_season=秋季&bf_campus=校区甲");
  globalThis.fetch = async (url, options) => { calls.push({ url, options }); return Response.json({ detail: "暂时无法读取当前版本" }, { status: 503 }); };
  try {
    inlineReturn().props.onClick(); assert.equal(inlineReturn().props.disabled, true);
    await settlePage(() => !h.state.busy && Boolean(h.state.error));
    assert.equal(JSON.stringify(h.state.data), before); assert.equal(h.state.selectedVersionId, "saved-p3");
    let scope = new URLSearchParams(window.location.search); assert.equal(scope.get("forecast_version"), "saved-p3"); assert.equal(scope.get("bf_season"), "秋季");
    assert.equal(inlineReturn().props.disabled, false);
    globalThis.fetch = async (url, options) => { calls.push({ url, options }); return Response.json({ ...h.state.data, version: h.current }); };
    inlineReturn().props.onClick(); await settlePage(() => !h.state.busy && h.state.selectedVersionId === "");
    scope = new URLSearchParams(window.location.search); assert.equal(scope.has("forecast_version"), false); assert.equal(scope.get("bf_campus"), "校区甲");
    assert.equal(h.state.data.version.id, h.current.id); assert.equal(buttons(h.page(), "返回当前版本").length, 0);
    assert.deepEqual(calls.map(call => call.url), ["/api/v1/forecasts/fy27/current", "/api/v1/forecasts/fy27/current"]);
    assert.ok(calls.every(call => !call.options.method || call.options.method === "GET"));
  } finally { globalThis.fetch = previous; browser.restore(); }
});

test("history selection remains blocked while there are pending current-version edits", () => {
  const h = pageHarness({ reason: "尚未保存的调整" });
  const selector = find(h.page(), node => node.type === "button" && node.props["aria-label"] === `查看预测 P${h.current.sequence}`)[0];
  assert.equal(selector.props.disabled, true);
  selector.props.onClick();
  assert.equal(h.state.selectedVersionId, ""); assert.equal(h.state.reason, "尚未保存的调整");
});

test("download selected-version snapshot uses the saved snapshot GET and preserves the complete archive without dumping JSON into the page", async () => {
  const h = pageHarness({ selectedVersionId: "saved-p3" }), previous = { fetch: globalThis.fetch, document: globalThis.document, create: URL.createObjectURL, revoke: URL.revokeObjectURL };
  h.state.data.version = { ...h.current, id: "saved-p3", sequence: 3, as_of: "2026-10-01", created_at: "2026-10-02T09:13:00+08:00" };
  const archive = { version: { id: "saved-p3", sequence: 3, as_of: "2026-10-01", status: "DRAFT" }, snapshot: { rows: [{ actual_revenue: null, prior_carryout: "-0.01" }], provenance: { query_fingerprint: "frozen-source" } }, assumptions: { preserved: true }, calculation: { annual: { forecast_revenue: "87654.321234" } }, confirmation: null, baseline_hash: "immutable-budget", snapshot_hash: "immutable-snapshot", calculation_hash: "immutable-calculation" };
  const calls = []; let blob, clicked = 0, revoked = "";
  const anchor = { remove() {}, click() { clicked++; } };
  globalThis.document = { createElement() { return anchor; }, body: { append() {} } };
  URL.createObjectURL = value => { blob = value; return "blob:saved-snapshot"; };
  URL.revokeObjectURL = url => { revoked = url; };
  globalThis.fetch = async (url, options) => { calls.push({ url, options }); return new Response(JSON.stringify(archive), { status: 200 }); };
  try {
    const before = JSON.stringify(h.state.data);
    buttons(h.page(), "下载本次快照")[0].props.onClick();
    await settlePage(() => !h.state.busy && clicked === 1);
    assert.deepEqual(calls.map(call => call.url), ["/api/v1/forecasts/fy27/versions/saved-p3/snapshot"]);
    assert.equal(calls[0].options.method, undefined);
    assert.deepEqual(JSON.parse(await blob.text()), archive);
    assert.equal(anchor.download, "FY27-预测P3-快照-20261001.json");
    assert.equal(revoked, "blob:saved-snapshot");
    assert.equal(JSON.stringify(h.state.data), before);
    assert.equal(h.state.notice, "P3 快照已下载");
    assert.doesNotMatch(elementText(h.page()), /query_fingerprint|immutable-snapshot|actual_revenue/);
  } finally { globalThis.fetch = previous.fetch; if (previous.document === undefined) delete globalThis.document; else globalThis.document = previous.document; URL.createObjectURL = previous.create; URL.revokeObjectURL = previous.revoke; }
});

test("an unresolved update is aborted at the request deadline and exits busy state with a retry that retains the intent", async () => {
  const h = pageHarness(), previous = { fetch: globalThis.fetch, timeout: globalThis.setTimeout, clear: globalThis.clearTimeout };
  let expire;
  globalThis.setTimeout = (callback, delay) => { assert.equal(delay, 200000); expire = callback; return 1; };
  globalThis.clearTimeout = () => {};
  globalThis.fetch = (_url, options) => new Promise((_resolve, reject) => options.signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")), { once: true }));
  try {
    buttons(h.page(), "更新预测")[0].props.onClick();
    assert.equal(h.state.busy, "更新预测"); assert.equal(typeof expire, "function");
    expire();
    await settlePage(() => !h.state.busy && Boolean(h.state.error));
    assert.equal(h.state.failure.error.detail.code, "REQUEST_TIMEOUT");
    assert.match(elementText(h.page()), /本次更新是否完成尚未确认/);
    assert.equal(buttons(h.page(), "重试更新").length, 1);
  } finally { globalThis.fetch = previous.fetch; globalThis.setTimeout = previous.timeout; globalThis.clearTimeout = previous.clear; }
});

test("a snapshot belonging to another saved version is rejected before any download and keeps the selected result intact", async () => {
  const h = pageHarness({ selectedVersionId: "p14" }), previous = globalThis.fetch;
  const before = JSON.stringify(h.state.data);
  globalThis.fetch = async () => new Response(JSON.stringify({ version: { id: "p-other" }, snapshot: { rows: [] } }), { status: 200 });
  try {
    buttons(h.page(), "下载本次快照")[0].props.onClick();
    await settlePage(() => !h.state.busy && Boolean(h.state.error));
    assert.match(h.state.error, /本次快照与所选预测不一致/);
    assert.equal(h.state.failure.error.detail.code, "SNAPSHOT_VERSION_MISMATCH");
    assert.equal(JSON.stringify(h.state.data), before);
    assert.match(elementText(h.page()), /快照下载未完成/);
  } finally { globalThis.fetch = previous; }
});
