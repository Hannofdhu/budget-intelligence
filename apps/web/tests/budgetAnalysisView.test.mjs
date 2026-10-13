// Static rendering acceptance only. This does not simulate browser clicks,
// effect execution, download completion or a real user's visual experience.
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";
import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { normalizeHistoricalFacts } from "../src/budgetAnalysis.ts";

const require = createRequire(import.meta.url);
const { createElement } = React;
const compiled = await build({
  entryPoints: [fileURLToPath(new URL("../src/BudgetAnalysisView.tsx", import.meta.url))],
  bundle: true, write: false, platform: "node", format: "cjs", jsx: "automatic",
  external: ["react", "react-dom", "exceljs"], logLevel: "silent",
});
const module = { exports: {} };
new Function("require", "module", "exports", compiled.outputFiles[0].text)(require, module, module.exports);
const { BudgetAnalysisView } = module.exports;

const fact = (overrides = {}) => ({ id: "1", season: "暑假", track: "智慧空间", campus: "西直门", product: "常规班", grade: "七年级", subject: "数学",
  revenue: 10000, business: 9000, listRevenue: 10000, old: 7, new: 3, total: 10, ...overrides });
const fourQuarters = ["暑假", "秋季", "寒假", "春季"].map((season, index) => fact({ id: String(index), season }));

function render(facts, search = "", props = {}) {
  const names = ["location", "window", "localStorage", "sessionStorage", "history"];
  const descriptors = new Map(names.map(name => [name, Object.getOwnPropertyDescriptor(globalThis, name)]));
  const values = new Map();
  const storage = { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, String(value)), removeItem: key => values.delete(key) };
  const location = new URL(`http://127.0.0.1:5188/budget/workspace?archive=fy27&step=5${search ? `&${search.replace(/^\?/, "")}` : ""}`);
  const history = { pushState() {}, replaceState() {} };
  for (const [name, value] of Object.entries({ location, history, localStorage: storage, sessionStorage: storage,
    window: { location, history, localStorage: storage, sessionStorage: storage, addEventListener() {}, removeEventListener() {} } })) {
    Object.defineProperty(globalThis, name, { configurable: true, writable: true, value });
  }
  try { return renderToStaticMarkup(createElement(BudgetAnalysisView, { facts, contextKey: "ssr:fy27", fiscalYear: 2027, annualTarget: 40000, ...props })); }
  finally {
    for (const name of names) { const descriptor = descriptors.get(name); if (descriptor) Object.defineProperty(globalThis, name, descriptor); else delete globalThis[name]; }
  }
}

const text = html => html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
const buttonText = html => [...html.matchAll(/<button\b([^>]*)>([\s\S]*?)<\/button>/g)].map(match => ({ attrs: match[1], text: text(match[2]) }));

test("renewal-only pivot titles use cross-season stages while mixed metric headers keep revenue quarters", () => {
  const facts = fourQuarters.map(row => ({ ...row, renewalNumerator: 70, renewalDenominator: 100 }));
  const renewal = render(facts, "baView=pivot&baRows=track,grade&baMetrics=renewal");
  const renewalHeaders = renewal.match(/<table class="ba-table ba-pivot-table"[^>]*><thead>([\s\S]*?)<\/thead>/)?.[1] ?? "";
  for (const label of ["春续暑", "暑续秋", "秋续寒", "寒续春"]) assert.ok(text(renewalHeaders).includes(label));
  assert.doesNotMatch(text(renewalHeaders), /暑假|秋季|寒假|春季/);
  assert.match(renewal, /aria-label="赛道：智慧空间，年级：七年级，春续暑，预算续班率，70%"/);
  const mixed = render(facts, "baView=pivot&baRows=track,grade&baMetrics=revenue,renewal");
  const mixedHeaders = mixed.match(/<table class="ba-table ba-pivot-table"[^>]*><thead>([\s\S]*?)<\/thead>/)?.[1] ?? "";
  for (const label of ["暑假", "秋季", "寒假", "春季"]) assert.ok(text(mixedHeaders).includes(label));
  for (const label of ["春续暑续班率", "暑续秋续班率", "秋续寒续班率", "寒续春续班率"]) assert.ok(text(mixedHeaders).includes(label));
  assert.match(mixed, /aria-label="赛道：智慧空间，年级：七年级，暑假，确认收入，/);
});

test("new composition figures use the same active quarter and grade population without fake annual totals", () => {
  const facts = fourQuarters.map((row, index) => ({ ...row, old: 20 + index, new: 4 + index, total: 24 + 2 * index }));
  const html = render(facts, `bf_season=${encodeURIComponent("秋季")}&bf_grade=${encodeURIComponent("七年级")}`);
  for (const title of ["年级招生结构", "校区招生结构"]) assert.match(text(html), new RegExp(title));
  assert.match(html, /aria-label="七年级，老生21人次，新生5人次，合计26人次"/);
  assert.match(html, /aria-label="西直门，老生21人次，新生5人次，合计26人次"/);
  assert.doesNotMatch(text(html), /全年合计|年度目标/);
});

test("default analysis markup presents a business board and all four fiscal seasons in place", () => {
  const html = render(fourQuarters), readable = text(html);
  assert.match(readable, /看板/); assert.match(readable, /透视/);
  for (const season of ["暑假", "秋季", "寒假", "春季"]) assert.match(readable, new RegExp(season));
  assert.match(readable, /确认收入/); assert.match(readable, /综合折扣/); assert.match(readable, /西直门/);
  assert.match(html, /role="tab"[^>]*aria-selected="true"/);
  assert.doesNotMatch(html, /查看依据|来源与口径|参数来源|secret\.xlsx/);
});

test("the default budget board expands its detail region but mounts only the first 25 rows", () => {
  const facts = Array.from({ length: 952 }, (_, index) => fact({
    id: `detail-${index}`, subject: `明细科目${String(index).padStart(4, "0")}`,
  }));
  const before = JSON.stringify(facts), html = render(facts);
  const detailBody = html.match(/<table class="ba-table ba-detail-table"[^>]*>[\s\S]*?<tbody>([\s\S]*?)<\/tbody>/)?.[1];
  assert.ok(detailBody, "预算明细 must already be visible without clicking 查看全部明细");
  assert.match(html, /<section[^>]*class="ba-panel ba-detail"[^>]*aria-label="构成明细"/);
  assert.match(html, /<h3>预算明细<\/h3>/);
  assert.equal((detailBody.match(/<tr\b/g) ?? []).length, 25,
    "an expanded detail region must not mount all 952 records");
  assert.match(text(detailBody), /明细科目0000/);
  assert.match(text(detailBody), /明细科目0024/);
  assert.doesNotMatch(text(detailBody), /明细科目0025|明细科目0951/);
  assert.match(text(html), /1 \/ 39 · 每页 25 条/);
  const buttons = buttonText(html);
  assert.ok(buttons.some(button => button.text === "收起明细"), "the default board toggle must offer collapse");
  assert.ok(buttons.some(button => button.text === "收起"), "the expanded detail header keeps its local collapse control");
  assert.ok(buttons.some(button => button.text === "上一页" && /disabled/.test(button.attrs)));
  assert.ok(buttons.some(button => button.text === "下一页" && !/disabled/.test(button.attrs)));
  assert.equal(JSON.stringify(facts), before, "opening a detail view must not modify budget facts");
});

test("a direct pivot URL keeps detail collapsed until the user selects a value or opens raw details", () => {
  const html = render(fourQuarters, "baView=pivot&baRows=campus&baMetrics=revenue");
  assert.match(html, /<h3>预算透视<\/h3>/);
  assert.match(html, /class="ba-table ba-pivot-table"/);
  assert.doesNotMatch(html, /class="ba-panel ba-detail"/);
  assert.doesNotMatch(html, /class="ba-table ba-detail-table"/);
});

test("business board shows old and new counts plus weighted budget renewal in both chart and quarter table", () => {
  const facts = [fact({ old: 50, new: 12, total: 62, renewal: 0.5, renewalNumerator: 50, renewalDenominator: 100 }),
    fact({ id: "small", old: 7, new: 3, total: 10, renewal: 0.8, renewalNumerator: 8, renewalDenominator: 10 })];
  const html = render(facts), readable = text(html);
  assert.match(readable, /季度招生人次/); assert.match(readable, /季度续班率/);
  assert.match(readable, /季度招生预算/); assert.match(readable, /校区招生人次/); assert.match(readable, /赛道与年级续班率/);
  assert.match(readable, /老生人次/); assert.match(readable, /新生人次/);
  assert.match(readable, /57/); assert.match(readable, /15/); assert.match(readable, /72/);
  assert.match(readable, /52\.73%/); assert.doesNotMatch(readable, /65%|51\.82%/);
  assert.match(html, /role="img"[^>]*aria-label="[^"]*春续暑续班率52\.73%/);
  assert.ok(buttonText(html).some(button => button.text === "透视续班率"));
  assert.match(html, /aria-label="季度招生预算表"/);
});

test("renewal pivot supports the same weighted ratio and unknown bases remain a dash", () => {
  const facts = [fact({ renewalNumerator: 50, renewalDenominator: 100 }), fact({ id: "small", renewalNumerator: 8, renewalDenominator: 10 })];
  const html = render(facts, "baView=pivot&baMetrics=renewal"), readable = text(html);
  assert.match(readable, /预算续班率/); assert.match(readable, /52\.73%/);
  assert.match(html, /aria-label="[^"]*预算续班率[^"]*52\.73%/);
  const unknown = render([fact({ renewal: 0.9, renewalNumerator: null, renewalDenominator: null })], "baView=pivot&baMetrics=renewal");
  assert.match(text(unknown), /—/); assert.doesNotMatch(text(unknown), /90%/);
  assert.doesNotMatch(unknown, /NaN|Infinity|undefined/);
});

test("URL selects the actual pivot markup with hierarchy configuration and numeric cells", () => {
  const html = render(fourQuarters, "baView=pivot&baRows=campus,product&baMetrics=revenue,total&baUnit=yuan");
  const readable = text(html);
  assert.match(readable, /透视/); assert.match(readable, /校区/); assert.match(readable, /产品/);
  assert.match(readable, /确认收入/); assert.match(readable, /人次/);
  assert.match(readable, /10,000/); assert.match(readable, /40,000/);
  assert.match(html, /<table\b/);
  assert.ok(buttonText(html).some(button => /10,000/.test(button.text)), "numeric pivot cells must be actionable buttons, not decoration");
  for (const button of buttonText(html).filter(button => /^-?[\d,.]+$/.test(button.text))) {
    assert.match(button.attrs, /aria-label="[^"]+"/, "numeric pivot cells need a contextual accessible label");
  }
});

test("a supplied formal record renders in the same overview context using the existing record URL", () => {
  const recordContent = createElement("section", { "aria-label": "定版验收记录" }, createElement("h2", null, "R150 已定版"));
  for (const search of ["view=record", "baRecord=1"]) {
    const html = render(fourQuarters, search, { recordContent });
    assert.match(text(html), /定版记录/);
    assert.match(text(html), /R150 已定版/);
    assert.match(html, /aria-label="定版验收记录"/);
    assert.doesNotMatch(html, /href="\/budget\/publication/);
  }
});

test("a quarter filter applies to every board section and does not mislabel its total as a whole year", () => {
  const facts = fourQuarters.map((row, index) => ({ ...row, revenue: 10000 * (index + 1) }));
  const html = render(facts, `baUnit=yuan&bf_season=${encodeURIComponent("秋季")}`), readable = text(html);
  assert.match(readable, /秋季确认收入/); assert.match(readable, /20,000/);
  assert.doesNotMatch(readable, /10,000|30,000|40,000/);
  assert.doesNotMatch(readable, /全年合计|年度目标/);
});

test("display retains negative amounts, real zero and missing values instead of normalizing them", () => {
  const facts = [fact({ id: "negative", revenue: -1234.56, season: "暑假" }), fact({ id: "zero", revenue: 0, season: "秋季" }),
    fact({ id: "missing", revenue: null, season: "寒假", old: null, new: null })];
  const html = render(facts, "baView=pivot&baRows=campus&baMetrics=revenue,old,new&baUnit=yuan"), readable = text(html);
  assert.match(readable, /-1,234\.56/);
  assert.ok(buttonText(html).some(button => /^0(?:\.0+)?$/.test(button.text)), "a recorded zero must remain an actual zero");
  assert.match(readable, /—/);
  assert.doesNotMatch(readable, /NaN|Infinity|undefined/);
});

test("zero total revenue has no invented percentage or invalid visual value", () => {
  const html = render([fact({ revenue: 0, business: 0, listRevenue: 0, old: 0, new: 0, total: 0 })], "baUnit=yuan");
  assert.match(text(html), /0/); assert.match(text(html), /—/);
  assert.doesNotMatch(html, /NaN|Infinity|width:[^;]*NaN|scaleX\(NaN\)/);
});

test("empty facts render an honest empty state and no fabricated budget total", () => {
  const html = render([], "baView=pivot&baUnit=yuan"), readable = text(html);
  assert.match(readable, /没有|暂无|无明细|无结果|无数据/);
  assert.doesNotMatch(readable, /NaN|Infinity|undefined/);
  assert.doesNotMatch(readable, /40,000/);
});

test("visible analysis buttons have usable accessible names and table navigation is present", () => {
  const html = render(fourQuarters, "baView=pivot&baRows=campus,product&baMetrics=revenue,total");
  for (const button of buttonText(html)) assert.ok(button.text || /aria-label="[^"]+"/.test(button.attrs), "empty unnamed button in analysis view");
  assert.match(html, /aria-label="[^"]*(?:预算|透视|筛选)[^"]*"/);
  assert.match(html, /<th\b/); assert.match(html, /tabindex="0"/);
});

const sourcePath = new URL("../../../output/fy27-shadow/source-cells.json", import.meta.url);
test("the real FY27 overview renders 113004618.59 from 952 quarterly rows, not repeated annual totals", { skip: !fs.existsSync(sourcePath) }, () => {
  const source = JSON.parse(fs.readFileSync(sourcePath, "utf8"));
  const blocks = ["AI AL AM AN AR AS AX", "AZ BC BD BE BI BJ BO", "BQ BT BU BV BZ CA CF", "CH CK CL CM CQ CR CW"];
  const fields = ["renewal", "old", "new", "total", "list_revenue", "business", "revenue"];
  const facts = normalizeHistoricalFacts(source.sheets["滚动表"].rows.filter(row => row.row >= 2 && row.cells.A?.value != null)
    .flatMap(row => blocks.map((block, index) => ({ row: row.row, prior_students: row.cells.AE.value, entry: row.cells.C.value, source_grade: row.cells.E.value,
      track: row.cells.A.value, product: row.cells.B.value, grade: row.cells.D.value,
      subject: row.cells.F.value, campus: row.cells.G.value, annual: row.cells.W.value, season: ["暑假", "秋季", "寒假", "春季"][index],
      ...Object.fromEntries(block.split(" ").map((column, i) => [fields[i], row.cells[column]?.value ?? null])) }))));
  assert.equal(facts.length, 952);
  const html = render(facts, "baUnit=yuan");
  assert.match(text(html), /113,004,618\.59/);
  for (const rate of ["65.17%", "58.91%", "67.44%", "75.11%"]) assert.match(text(html), new RegExp(rate.replace(/[.%]/g, "\\$&")));
  assert.doesNotMatch(html, /来源与口径|参数来源|查看依据/);
});

test("the analysis view stays inside the existing five-step annual-budget shell", () => {
  const historical = fs.readFileSync(new URL("../src/HistoricalBudgetPage.tsx", import.meta.url), "utf8");
  const results = fs.readFileSync(new URL("../src/BudgetResultsPage.tsx", import.meta.url), "utf8");
  const shared = fs.readFileSync(new URL("../src/BudgetAnalysisView.tsx", import.meta.url), "utf8");
  assert.match(historical, /预算拆解导航/);
  assert.match(historical, /<BudgetAnalysisView/); assert.match(results, /<BudgetAnalysisView/);
  assert.doesNotMatch(shared, /location\.assign|location\.href\s*=/);
  assert.doesNotMatch(shared, /method:\s*["'](?:POST|PUT|PATCH|DELETE)["']/);
  assert.match(results, /fetchRevision\(counts\.budget_revision_id/);
  assert.match(results, /fetchOpeningStudentVersion\(openingId/);
  assert.doesNotMatch(results, /fetchOpeningWorkspace|saveOpening|calculateHeadcount/);
});

test("static render restores all mocked browser globals", () => {
  const before = Object.getOwnPropertyDescriptor(globalThis, "location");
  render(fourQuarters);
  assert.deepEqual(Object.getOwnPropertyDescriptor(globalThis, "location"), before);
});

test("board lays out old/new cards, quarterly student chart and tables, and weighted budget renewal", () => {
  const facts = [fact({ id: "small", old: 80, new: 20, total: 100, renewal: .8, renewalNumerator: 80, renewalDenominator: 100 }),
    fact({ id: "large", old: 540, new: 360, total: 900, renewal: .6, renewalNumerator: 540, renewalDenominator: 900 })];
  const html = render(facts), readable = text(html);
  for (const label of ["老生人次", "新生人次", "季度招生人次", "季度续班率", "季度招生预算", "校区招生人次", "赛道与年级续班率"]) assert.ok(readable.includes(label), label);
  assert.match(readable, /620/); assert.match(readable, /380/); assert.match(readable, /1,000/);
  assert.match(readable, /62%/); assert.doesNotMatch(readable, /70%/);
  assert.match(html, /<svg[^>]+role="img"/); assert.match(html, /aria-label="各季度老生与新生人次"/);
  assert.match(html, /aria-label="校区季度老生新生人次表"/);
  assert.match(html, /aria-label="赛道年级跨季续班率表"/);
  assert.doesNotMatch(readable, /按原价收入加权|点击看明细|只保存筛选和布局|当季业务确认收入|递延至下季收入/);
});

test("renewal is a selectable pivot metric and missing quarters never become zero line points", () => {
  const facts = [fact({ renewal: .65, renewalNumerator: 65, renewalDenominator: 100 })];
  const board = render(facts);
  assert.equal((board.match(/<circle\b/g) ?? []).length, 1);
  assert.equal((board.match(/<polyline\b/g) ?? []).length, 0);
  assert.match(text(board), /65%/);
  const pivot = render(facts, "baView=pivot&baMetrics=renewal");
  assert.match(text(pivot), /预算续班率/); assert.match(text(pivot), /65%/);
  assert.doesNotMatch(text(pivot), /预算续班率（人次）/);
  assert.match(pivot, /type="checkbox"[^>]*checked/);
});

test("quarter-filtered student totals have no whole-year label and keep other seasons unknown", () => {
  const facts = fourQuarters.map(row => ({ ...row, renewal: .8, renewalNumerator: 8, renewalDenominator: 10 }));
  const html = render(facts, `bf_season=${encodeURIComponent("秋季")}`), readable = text(html);
  assert.doesNotMatch(readable, /全年/);
  assert.match(readable, /合计/); assert.equal((html.match(/<circle\b/g) ?? []).length, 1);
  assert.match(readable, /80%/);
});

test("student and renewal charts keep recorded zero and exclude half-missing quarters from the bar scale", () => {
  const facts = [fact({ id: "full", old: 8, new: 2, total: 10, renewal: 0, renewalNumerator: 0, renewalDenominator: 100 }),
    fact({ id: "partial", season: "秋季", old: null, new: 10000, total: null, renewalNumerator: null, renewalDenominator: null })];
  const html = render(facts);
  assert.match(html, /height:80%/); assert.match(html, /height:20%/);
  assert.match(html, /春续暑续班率0%/); assert.equal((html.match(/<circle\b/g) ?? []).length, 1);
  assert.doesNotMatch(html, /NaN|Infinity/);
  const shared = fs.readFileSync(new URL("../src/BudgetAnalysisView.tsx", import.meta.url), "utf8");
  assert.doesNotMatch(shared, /key: "(?:current|carryout)"|当季业务确认收入|递延至下季收入/);
  assert.match(shared, /key: "renewal", label: "预算续班率", format: "rate"/);
});

test("FY27 student board displays all saved quarterly rates from the correct transition bases", { skip: !fs.existsSync(sourcePath) }, () => {
  const source = JSON.parse(fs.readFileSync(sourcePath, "utf8"));
  const blocks = ["AL AM AN AR AS AX AI", "BC BD BE BI BJ BO AZ", "BT BU BV BZ CA CF BQ", "CK CL CM CQ CR CW CH"];
  const fields = ["old", "new", "total", "list_revenue", "business", "revenue", "renewal"];
  const facts = normalizeHistoricalFacts(source.sheets["滚动表"].rows.filter(row => row.row >= 2 && row.cells.A?.value != null)
    .flatMap(row => blocks.map((block, index) => ({ row: row.row, track: row.cells.A.value, product: row.cells.B.value, grade: row.cells.D.value,
      subject: row.cells.F.value, campus: row.cells.G.value, prior_students: row.cells.AE?.value ?? null,
      season: ["暑假", "秋季", "寒假", "春季"][index], ...Object.fromEntries(block.split(" ").map((column, i) => [fields[i], row.cells[column]?.value ?? null])) }))));
  const readable = text(render(facts, "baUnit=yuan"));
  for (const value of ["23,610", "13,178", "36,788", "65.17%", "58.91%", "67.44%", "75.11%", "113,004,618.59"]) assert.ok(readable.includes(value), value);
});

const emptyFilters = { season: "", track: "", campus: "", product: "", grade: "", subject: "" };
test("controlled filters override stale URL filters immediately while retaining its pivot configuration", () => {
  const facts = [fact({ id: "summer", revenue: 11000 }), fact({ id: "autumn", season: "秋季", revenue: 22000 })];
  const html = render(facts, `baView=pivot&baUnit=yuan&bf_season=${encodeURIComponent("暑假")}`, {
    controlledFilters: { ...emptyFilters, season: "秋季" },
  });
  assert.match(html, /class="ba-table ba-pivot-table"/);
  assert.match(text(html), /22,000/); assert.doesNotMatch(text(html), /11,000/);
  assert.match(html, /aria-label="清除季度秋季"/);
});
test("hideFilters hides only duplicate selectors and retains current-scope chips and exports", () => {
  const props = { controlledFilters: { ...emptyFilters, season: "暑假" }, hideFilters: true };
  const html = render(fourQuarters, "", props);
  assert.doesNotMatch(html, /aria-label="共同筛选"|aria-label="筛选季度"/);
  assert.match(html, /aria-label="清除季度暑假"/);
  assert.ok(buttonText(html).some(button => button.text === "导出当前范围明细"));
  assert.match(render(fourQuarters), /aria-label="共同筛选"/);
});
test("crossed controlled filters keep an empty intersection instead of widening either dimension", () => {
  const facts = [fact({ id: "A", campus: "校区A", product: "产品A" }), fact({ id: "B", campus: "校区B", product: "产品B" })];
  const before = JSON.stringify(facts);
  const html = render(facts, "", { controlledFilters: { ...emptyFilters, campus: "校区A", product: "产品B" } });
  assert.match(text(html), /这个范围没有预算明细/);
  assert.match(html, /aria-label="清除校区校区A"/); assert.match(html, /aria-label="清除产品产品B"/);
  assert.equal(JSON.stringify(facts), before);
});
test("a controlled all-clear overrides stale URL filters and validation remains the parent's responsibility", () => {
  const old = `bf_season=${encodeURIComponent("暑假")}`;
  assert.match(text(render(fourQuarters, old, { controlledFilters: { ...emptyFilters } })), /4 条明细/);
  const missing = render(fourQuarters, old, { controlledFilters: { ...emptyFilters, campus: "不存在" } });
  assert.match(text(missing), /0 条明细|没有预算明细/); assert.match(missing, /aria-label="清除校区不存在"/);
  assert.doesNotMatch(text(missing), /全年确认收入/);
  // Existing callers remain uncontrolled: URL filters still take priority over initialFilters.
  assert.match(text(render(fourQuarters, old, { initialFilters: { ...emptyFilters, season: "秋季" } })), /季度：暑假/);
});

// Event/effect contract harness. No DOM, layout, browser download, network, or business mutation is simulated.
const elements = tree => Array.isArray(tree) ? tree.flatMap(elements) : !tree || typeof tree !== "object" ? []
  : [tree, ...elements(tree.props?.children)];
const nodeText = node => Array.isArray(node) ? node.map(nodeText).join("") : node === null || node === undefined || typeof node === "boolean" ? ""
  : typeof node === "object" ? nodeText(node.props?.children) : String(node);
const findButton = (tree, label) => elements(tree).find(node => node.type === "button" && nodeText(node) === label);
function hookBrowser(search = "", views = []) {
  const names = ["location", "history", "window", "localStorage", "document", "navigator"];
  const saved = new Map(names.map(name => [name, Object.getOwnPropertyDescriptor(globalThis, name)]));
  const values = new Map([["budget-analysis-personal-views-v1", JSON.stringify(views)]]), listeners = new Map();
  const storage = { getItem: name => values.get(name) ?? null, setItem: (name, value) => values.set(name, value) };
  const setLocation = value => { globalThis.location = new URL(value, "http://127.0.0.1:5188"); if (globalThis.window) globalThis.window.location = globalThis.location; };
  setLocation(`http://127.0.0.1:5188/budget/next-year?year=2028&nyTask=5&nyFilters=1${search ? `&${search}` : ""}`);
  const writes = [], clipboard = [];
  const history = { pushState(_state, _title, url) { writes.push(String(url)); setLocation(String(url)); }, replaceState(_state, _title, url) { writes.push(String(url)); setLocation(String(url)); } };
  const window = { location: globalThis.location, history, localStorage: storage,
    addEventListener(event, listener) { if (!listeners.has(event)) listeners.set(event, new Set()); listeners.get(event).add(listener); },
    removeEventListener(event, listener) { listeners.get(event)?.delete(listener); },
    requestAnimationFrame() { return 1; }, cancelAnimationFrame() {} };
  for (const [name, value] of Object.entries({ history, window, localStorage: storage, navigator: { clipboard: { async writeText(value) { clipboard.push(value); } } }, document: { createElement() { return { click() {} }; }, getElementById() { return null; } } })) {
    Object.defineProperty(globalThis, name, { configurable: true, writable: true, value });
  }
  return { writes, clipboard, setLocation, dispatch(event) { for (const listener of listeners.get(event) ?? []) listener(); },
    restore() { for (const name of names) { const descriptor = saved.get(name); if (descriptor) Object.defineProperty(globalThis, name, descriptor); else delete globalThis[name]; } } };
}
function analysisHarness(initialProps) {
  const states = [], initialized = new Set(), refs = [], effectSlots = [], pendingEffects = [];
  let stateIndex = 0, refIndex = 0, effectIndex = 0, props = initialProps;
  const fakeReact = { ...React,
    useState(initial) { const index = stateIndex++; if (!initialized.has(index)) { states[index] = typeof initial === "function" ? initial() : initial; initialized.add(index); }
      return [states[index], value => { states[index] = typeof value === "function" ? value(states[index]) : value; }]; },
    useRef(initial) { return refs[refIndex++] ??= { current: initial }; }, useMemo(callback) { return callback(); },
    useEffect(callback, deps) { const index = effectIndex++, previous = effectSlots[index];
      if (!previous || !deps || deps.length !== previous.deps?.length || deps.some((value, i) => !Object.is(value, previous.deps[i]))) {
        pendingEffects.push(() => { previous?.cleanup?.(); effectSlots[index] = { deps, cleanup: callback() }; });
      }
    },
  };
  const loaded = { exports: {} };
  new Function("require", "module", "exports", compiled.outputFiles[0].text)(name => name === "react" ? fakeReact : require(name), loaded, loaded.exports);
  const Component = loaded.exports.BudgetAnalysisView;
  return { render(next) { if (next) props = { ...props, ...next }; stateIndex = refIndex = effectIndex = 0; pendingEffects.length = 0;
      return Component({ contextKey: "controlled:2028", fiscalYear: 2028, ...props }); },
    effects() { const ready = pendingEffects.splice(0); ready.forEach(run => run()); },
    dispose() { effectSlots.forEach(slot => slot.cleanup?.()); } };
}

test("an incoming controlled filter rerenders board facts and export rows before effects and clears cell selection", () => {
  const browser = hookBrowser("baView=pivot&baRows=campus&baMetrics=revenue");
  try {
    const facts = [fact({ id: "summer", revenue: 11000 }), fact({ id: "autumn", season: "秋季", revenue: 22000 })];
    const harness = analysisHarness({ facts, controlledFilters: { ...emptyFilters } });
    let tree = harness.render(); harness.effects(); tree = harness.render();
    const cell = elements(tree).find(node => node.type === "button" && node.props["data-row"] === 0 && node.props["data-col"] === 0);
    cell.props.onClick({ detail: 0, shiftKey: false }); tree = harness.render();
    assert.ok(findButton(tree, "导出所选明细"));
    tree = harness.render({ controlledFilters: { ...emptyFilters, season: "秋季" } });
    assert.equal(findButton(tree, "导出所选明细"), undefined, "stale selection must vanish in the same render, not after an effect");
    const exportTable = elements(tree).find(node => node.props?.exportData);
    assert.deepEqual(exportTable.props.exportData.rows.map(row => row.id), ["autumn"]);
    const people = elements(tree).find(node => node.type?.name === "BudgetPeopleBoard");
    assert.equal(people, undefined, "pivot remains selected while parent filters change");
    tree = harness.render({ controlledFilters: { ...emptyFilters, season: "暑假" } });
    assert.equal(findButton(tree, "导出所选明细"), undefined, "old selections cannot reappear when switching back");
    harness.dispose();
  } finally { browser.restore(); }
});
test("controlled chart drilling requests the parent filter without overriding the parent's URL filter", () => {
  const browser = hookBrowser(`bf_season=${encodeURIComponent("暑假")}`), changes = [];
  try {
    const harness = analysisHarness({ facts: fourQuarters, controlledFilters: { ...emptyFilters }, onFiltersChange: value => changes.push(value) });
    const tree = harness.render();
    elements(tree).find(node => node.props?.["aria-label"] === "查看秋季收入明细").props.onClick();
    assert.deepEqual(changes, [{ ...emptyFilters, season: "秋季" }]);
    assert.equal(new URL(browser.writes.at(-1)).searchParams.get("bf_season"), "暑假", "parent persists bf_* after accepting the callback");
    assert.equal(new URL(browser.writes.at(-1)).searchParams.get("nyFilters"), "1");
    assert.equal(new URL(browser.writes.at(-1)).searchParams.get("nyTask"), "5");
    const stillParentControlled = harness.render();
    assert.equal(elements(stillParentControlled).find(node => node.props?.exportData).props.exportData.rows.length, 4);
  } finally { browser.restore(); }
});
test("controlled chips and personal views send filters back to the parent for validation", () => {
  const view = { id: "autumn", name: "秋季招生", contextKey: "controlled:2028", fiscalYear: 2028,
    state: { view: "pivot", unit: "yuan", filters: { ...emptyFilters, season: "秋季", campus: "已退出校区" }, config: { rows: ["campus"], column: "season", metrics: ["new"], sort: "name-asc" } } };
  const browser = hookBrowser("baView=pivot", [view]), changes = [];
  try {
    const harness = analysisHarness({ facts: fourQuarters, controlledFilters: { ...emptyFilters, season: "暑假" }, onFiltersChange: value => changes.push(value) });
    let tree = harness.render(); harness.effects(); tree = harness.render();
    elements(tree).find(node => node.props?.["aria-label"] === "清除季度暑假").props.onClick();
    assert.deepEqual(changes.at(-1), emptyFilters);
    tree = harness.render();
    elements(tree).find(node => node.type === "button" && nodeText(node).startsWith("秋季招生")).props.onClick();
    assert.deepEqual(changes.at(-1), { ...emptyFilters, season: "秋季", campus: "已退出校区" });
    tree = harness.render({ controlledFilters: { ...changes.at(-1), campus: "" } });
    assert.ok(elements(tree).some(node => node.type === "h3" && nodeText(node) === "预算透视"));
    assert.doesNotMatch(nodeText(tree), /清除不可用筛选/);
    harness.dispose();
  } finally { browser.restore(); }
});

test("disabled-only campuses and newly added unpreviewed products stay empty instead of showing the full budget", () => {
  for (const filters of [{ ...emptyFilters, campus: "已停用校区" }, { ...emptyFilters, product: "尚未预览的新产品" }]) {
    const html = render(fourQuarters, "baUnit=yuan", { controlledFilters: filters, hideFilters: true });
    assert.match(text(html), /这个范围没有预算明细/);
    assert.match(text(html), /0 条明细/);
    assert.doesNotMatch(text(html), /40,000|全年确认收入|季度收入/);
  }
});
test("controlled popstate restores view configuration only and never emits a stale filter callback", () => {
  const browser = hookBrowser(), changes = [];
  try {
    const harness = analysisHarness({ facts: fourQuarters, controlledFilters: { ...emptyFilters, season: "暑假" }, onFiltersChange: value => changes.push(value) });
    harness.render(); harness.effects();
    browser.setLocation(`http://127.0.0.1:5188/budget/next-year?year=2028&nyTask=5&nyFilters=1&baView=pivot&baMetrics=new&bf_season=${encodeURIComponent("寒假")}`);
    browser.dispatch("popstate");
    const tree = harness.render({ controlledFilters: { ...emptyFilters, season: "秋季" } });
    assert.equal(changes.length, 0); assert.equal(browser.writes.length, 0);
    assert.ok(elements(tree).some(node => node.type === "h3" && nodeText(node) === "预算透视"));
    assert.ok(elements(tree).some(node => node.props?.["aria-label"] === "清除季度秋季"));
    assert.ok(elements(tree).some(node => node.type === "th" && nodeText(node).includes("新生人次")));
    harness.dispose();
  } finally { browser.restore(); }
});
test("controlled share links include the latest parent scope and preserve task, year and explicit-empty marker", async () => {
  const browser = hookBrowser(`baView=pivot&bf_season=${encodeURIComponent("暑假")}`);
  try {
    const harness = analysisHarness({ facts: fourQuarters, controlledFilters: { ...emptyFilters, season: "秋季" } });
    let tree = harness.render();
    await findButton(tree, "复制视图链接").props.onClick();
    let shared = new URL(browser.clipboard.at(-1));
    assert.equal(shared.searchParams.get("bf_season"), "秋季");
    assert.equal(shared.searchParams.get("baView"), "pivot");
    for (const [name, value] of [["year", "2028"], ["nyTask", "5"], ["nyFilters", "1"]]) assert.equal(shared.searchParams.get(name), value);
    tree = harness.render({ controlledFilters: { ...emptyFilters } });
    await findButton(tree, "复制视图链接").props.onClick(); shared = new URL(browser.clipboard.at(-1));
    assert.equal(shared.searchParams.has("bf_season"), false); assert.equal(shared.searchParams.get("nyFilters"), "1");
  } finally { browser.restore(); }
});
test("uncontrolled popstate and board selection retain their existing filter ownership", () => {
  const browser = hookBrowser(), changes = [];
  try {
    const harness = analysisHarness({ facts: fourQuarters, onFiltersChange: value => changes.push(value) });
    let tree = harness.render(); harness.effects(); tree = harness.render();
    elements(tree).find(node => node.props?.["aria-label"] === "查看秋季收入明细").props.onClick();
    tree = harness.render(); assert.ok(findButton(tree, "导出所选明细")); assert.equal(changes.length, 0);
    browser.setLocation(`http://127.0.0.1:5188/budget/workspace?baView=pivot&bf_season=${encodeURIComponent("寒假")}`);
    browser.dispatch("popstate"); tree = harness.render();
    assert.deepEqual(changes, [{ ...emptyFilters, season: "寒假" }]);
    assert.ok(elements(tree).some(node => node.props?.["aria-label"] === "清除季度寒假"));
    harness.dispose();
  } finally { browser.restore(); }
});
test("exports after an incoming controlled change contain only the latest scope without fetching data", async () => {
  const browser = hookBrowser(), oldFetch = globalThis.fetch, createURL = URL.createObjectURL, blobs = []; let requests = 0;
  globalThis.fetch = () => { requests++; throw new Error("unexpected data fetch"); };
  URL.createObjectURL = blob => { blobs.push(blob); return createURL(blob); };
  try {
    const harness = analysisHarness({ facts: fourQuarters, controlledFilters: { ...emptyFilters } });
    harness.render();
    const tree = harness.render({ controlledFilters: { ...emptyFilters, season: "秋季" } });
    await findButton(tree, "导出当前范围明细").props.onClick();
    assert.equal(blobs.length, 1);
    const ExcelJS = require("exceljs"), workbook = new ExcelJS.Workbook();
    await workbook.xlsx.load(await blobs[0].arrayBuffer());
    assert.equal(workbook.worksheets[0].rowCount, 2, "one header plus the one filtered fact");
    assert.equal(workbook.worksheets[0].getRow(2).getCell(6).value, "秋季");
    assert.equal(requests, 0);
  } finally { globalThis.fetch = oldFetch; URL.createObjectURL = createURL; browser.restore(); }
});
