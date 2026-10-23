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
  external: ["react", "react-dom", "exceljs"], loader: { ".css": "empty" }, logLevel: "silent",
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
  assert.match(mixed, /aria-label="赛道：智慧空间，年级：七年级，暑假，结转收入，/);
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
  const tabs = buttonText(html).filter(button => /role="tab"/.test(button.attrs));
  assert.deepEqual(tabs.map(button => button.text), ["看板", "透视", "明细"]);
  assert.match(tabs[0].attrs, /aria-selected="true"/);
  assert.match(tabs[1].attrs, /aria-selected="false"/);
  assert.match(tabs[2].attrs, /aria-selected="false"/);
  for (const season of ["暑假", "秋季", "寒假", "春季"]) assert.match(readable, new RegExp(season));
  assert.match(readable, /结转收入/); assert.match(readable, /综合折扣/); assert.match(readable, /西直门/);
  assert.match(html, /role="tab"[^>]*aria-selected="true"/);
  assert.doesNotMatch(html, /查看依据|来源与口径|参数来源|secret\.xlsx/);
});

test("the default board has no embedded details and the sibling detail tab mounts only its first 25 rows", () => {
  const facts = Array.from({ length: 952 }, (_, index) => fact({
    id: `detail-${index}`, subject: `明细科目${String(index).padStart(4, "0")}`,
  }));
  const before = JSON.stringify(facts), board = render(facts), html = render(facts, "baView=detail");
  assert.doesNotMatch(board, /ba-detail-table|查看全部明细|收起明细/);
  const detailBody = html.match(/<table class="ba-table ba-detail-table"[^>]*>[\s\S]*?<tbody>([\s\S]*?)<\/tbody>/)?.[1];
  assert.ok(detailBody, "the sibling 明细 tab must show the detail table immediately");
  assert.match(html, /<section[^>]*class="ba-panel ba-detail"[^>]*aria-label="构成明细"/);
  assert.match(html, /<h3>预算明细<\/h3>/);
  assert.equal((detailBody.match(/<tr\b/g) ?? []).length, 25,
    "the detail tab must not mount all 952 records");
  assert.match(text(detailBody), /明细科目0000/);
  assert.match(text(detailBody), /明细科目0024/);
  assert.doesNotMatch(text(detailBody), /明细科目0025|明细科目0951/);
  assert.match(text(html), /1 \/ 39 · 每页 25 条/);
  const buttons = buttonText(html);
  assert.ok(buttons.some(button => button.text === "一键复制"));
  assert.ok(buttons.some(button => button.text === "导出明细"));
  assert.ok(!buttons.some(button => button.text === "收起明细" || button.text === "收起"));
  assert.match(html, /role="tabpanel"[^>]*aria-labelledby="ba-tab-detail"/);
  assert.ok(buttons.some(button => button.text === "上一页" && /disabled/.test(button.attrs)));
  assert.ok(buttons.some(button => button.text === "下一页" && !/disabled/.test(button.attrs)));
  assert.equal(JSON.stringify(facts), before, "opening a detail view must not modify budget facts");
});

test("a direct pivot URL renders only its pivot and replaces the scattered presets with one view selector", () => {
  const html = render(fourQuarters, "baView=pivot&baRows=campus&baMetrics=revenue");
  assert.match(html, /<h3(?:\s[^>]*)?>预算透视<\/h3>/);
  assert.match(html, /class="ba-table ba-pivot-table"/);
  assert.doesNotMatch(html, /class="ba-panel ba-detail"/);
  assert.doesNotMatch(html, /class="ba-table ba-detail-table"/);
  assert.match(html, /aria-label="透视视图"/);
  assert.match(html, /<optgroup label="系统预设"/);
  assert.doesNotMatch(html, /ba-named-views|ba-presets|>透视表<|>原始明细</);
});

test("legacy raw-detail links open the sibling detail tab without stale pivot controls or transfers", () => {
  const html = render(fourQuarters, "baView=pivot&baTable=detail&baRows=campus&baMetrics=revenue");
  assert.match(html, /id="ba-tab-detail"[^>]*aria-selected="true"/);
  assert.match(html, /class="ba-table ba-detail-table"/);
  assert.doesNotMatch(html, /class="ba-table ba-pivot-table"|aria-label="透视视图"|>复制透视<|>导出透视</);
  const buttons = buttonText(html);
  assert.equal(buttons.filter(button => button.text === "导出明细").length, 1);
  assert.equal(buttons.filter(button => button.text === "一键复制").length, 1);
});

test("business board shows old and new counts plus weighted budget renewal in both chart and quarter table", () => {
  const facts = [fact({ old: 50, new: 12, total: 62, renewal: 0.5, renewalNumerator: 50, renewalDenominator: 100 }),
    fact({ id: "small", old: 7, new: 3, total: 10, renewal: 0.8, renewalNumerator: 8, renewalDenominator: 10 })];
  const html = render(facts), readable = text(html);
  assert.match(readable, /季度招生人次/); assert.match(readable, /季度续班率/);
  assert.match(readable, /季度招生预算/); assert.match(readable, /校区招生预算/); assert.match(readable, /赛道与年级续班率/);
  assert.match(readable, /老生人次/); assert.match(readable, /新生人次/);
  assert.match(readable, /57/); assert.match(readable, /15/); assert.match(readable, /72/);
  assert.match(readable, /52\.73%/); assert.doesNotMatch(readable, /65%|51\.82%/);
  assert.match(html, /role="img"[^>]*aria-label="[^"]*春续暑续班率52\.73%/);
  assert.ok(buttonText(html).some(button => button.text === "透视续班率"));
  assert.match(html, /aria-label="季度招生预算表"/);
});

test("track-subject board follows quarterly enrollment and shares the active scope and currency unit", () => {
  const facts = [
    fact({ id: "chosen", season: "秋季", total: 37, revenue: 12345.6789 }),
    fact({ id: "other-campus", season: "秋季", campus: "中关村", total: 81, revenue: 87654 }),
    fact({ id: "other-season", season: "暑假", total: 92, revenue: 40000 }),
    fact({ id: "other-track", season: "秋季", track: "智慧伴学", total: 73, revenue: 20000 }),
    fact({ id: "other-subject", season: "秋季", subject: "英语", total: 41, revenue: 50000 }),
  ];
  const search = new URLSearchParams({ bf_campus: "西直门", bf_track: "智慧空间", bf_subject: "数学", bf_season: "秋季", baUnit: "yuan" }).toString();
  const html = render(facts, search);
  assert.ok(html.indexOf("<h3>季度招生预算</h3>") < html.indexOf("<h3>赛道科目招生预算</h3>"));
  assert.ok(html.indexOf("<h3>赛道科目招生预算</h3>") < html.indexOf("<h3>校区招生预算</h3>"));
  const table = html.match(/<table\b[^>]*class="ba-table ba-track-subject-table"[^>]*>([\s\S]*?)<\/table>/)?.[1] ?? "";
  assert.match(table, /结转收入（元）/);
  assert.match(table, /aria-label="智慧空间，数学，秋季，招生人次，37"/);
  assert.match(table, /aria-label="智慧空间，数学，秋季，结转收入，12,345\.68"/);
  assert.match(table, /aria-label="智慧空间，数学，合计，招生人次，37"/);
  assert.doesNotMatch(table, /中关村|智慧伴学|英语|87,654|40,000|50,000/);
  assert.equal((table.match(/<tbody>[\s\S]*?<\/tbody>/)?.[0].match(/<tr\b/g) ?? []).length, 1);
  const wan = render(facts, search.replace("baUnit=yuan", "baUnit=wan"));
  assert.match(wan, /aria-label="智慧空间，数学，秋季，结转收入，1\.23"/);
});

test("a single campus enrollment budget replaces the separate enrollment and revenue tables in the same board", () => {
  const html = render(fourQuarters), readable = text(html);
  assert.equal((html.match(/<h3>校区招生预算<\/h3>/g) ?? []).length, 1);
  assert.equal((html.match(/<table\b[^>]*class="ba-table ba-campus-budget-table"[^>]*>/g) ?? []).length, 1);
  assert.ok(html.indexOf("<h3>赛道科目招生预算</h3>") < html.indexOf("<h3>校区招生预算</h3>"));
  assert.ok(html.indexOf("<h3>校区招生预算</h3>") < html.indexOf("<h3>赛道与年级续班率</h3>"));
  assert.doesNotMatch(readable, /校区招生人次|各校区预算/);
  assert.doesNotMatch(html, /校区季度老生新生人次表|校区季度收入表/);
  const table = html.match(/<table\b[^>]*class="ba-table ba-campus-budget-table"[^>]*>([\s\S]*?)<\/table>/)?.[1] ?? "";
  assert.match(table, /aria-label="西直门，暑假，总人次，10"/);
  assert.match(table, /aria-label="西直门，暑假，新生人次，3"/);
  assert.match(table, /aria-label="西直门，暑假，老生人次，7"/);
  assert.match(table, /aria-label="西直门，暑假，结转收入，1"/);
});

test("campus combined enrollment and revenue obey every shared filter and the current currency unit", () => {
  const facts = [
    fact({ id: "chosen", season: "秋季", total: 37, new: 8, old: 29, revenue: 12345.6789 }),
    fact({ id: "other-campus", season: "秋季", campus: "中关村", total: 81, revenue: 87654 }),
    fact({ id: "other-season", season: "暑假", total: 92, revenue: 40000 }),
    fact({ id: "other-track", season: "秋季", track: "智慧伴学", total: 73, revenue: 20000 }),
    fact({ id: "other-subject", season: "秋季", subject: "英语", total: 41, revenue: 50000 }),
    fact({ id: "other-product", season: "秋季", product: "白金", total: 44, revenue: 60000 }),
    fact({ id: "other-grade", season: "秋季", grade: "八年级", total: 45, revenue: 70000 }),
  ];
  const before = JSON.stringify(facts);
  const search = new URLSearchParams({ bf_campus: "西直门", bf_track: "智慧空间", bf_subject: "数学", bf_product: "常规班", bf_grade: "七年级", bf_season: "秋季", baUnit: "yuan" }).toString();
  for (const unit of ["yuan", "wan"]) {
    const html = render(facts, search.replace("baUnit=yuan", `baUnit=${unit}`));
    const table = html.match(/<table\b[^>]*class="ba-table ba-campus-budget-table"[^>]*>([\s\S]*?)<\/table>/)?.[1] ?? "";
    assert.ok(table);
    for (const [metric, value] of [["总人次", "37"], ["新生人次", "8"], ["老生人次", "29"], ["结转收入", unit === "yuan" ? "12,345.68" : "1.23"]]) {
      assert.ok(table.includes(`aria-label="西直门，秋季，${metric}，${value}"`));
      assert.ok(table.includes(`aria-label="西直门，合计，${metric}，${value}"`));
    }
    assert.match(table, new RegExp(`结转收入（${unit === "yuan" ? "元" : "万元"}）`));
    assert.equal((table.match(/<tbody>[\s\S]*?<\/tbody>/)?.[0].match(/<tr\b/g) ?? []).length, 1);
    assert.doesNotMatch(table, /中关村|智慧伴学|英语|白金|八年级|87,654|40,000|50,000|60,000|70,000/);
  }
  assert.equal(JSON.stringify(facts), before);
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
  assert.match(readable, /结转收入/); assert.match(readable, /人次/);
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
  assert.match(readable, /秋季结转收入/); assert.match(readable, /20,000/);
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
  for (const label of ["老生人次", "新生人次", "季度招生人次", "季度续班率", "季度招生预算", "校区招生预算", "赛道与年级续班率"]) assert.ok(readable.includes(label), label);
  assert.match(readable, /620/); assert.match(readable, /380/); assert.match(readable, /1,000/);
  assert.match(readable, /62%/); assert.doesNotMatch(readable, /70%/);
  assert.match(html, /<svg[^>]+role="img"/); assert.match(html, /aria-label="各季度老生与新生人次"/);
  assert.match(html, /aria-label="校区季度招生预算表，可横向滚动"/);
  assert.match(html, /aria-label="赛道年级跨季续班率表"/);
  assert.doesNotMatch(readable, /按原价收入加权|点击看明细|只保存筛选和布局|当季业务确认收入|当季业务结转收入|递延至下季收入/);
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
  assert.ok(buttonText(pivot).some(button => button.text === "透视设置" && /aria-expanded="false"/.test(button.attrs)));
  assert.doesNotMatch(pivot, /<fieldset class="ba-metrics"/);
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
  assert.doesNotMatch(shared, /key: "(?:current|carryout)"|当季业务确认收入|当季业务结转收入|递延至下季收入/);
  assert.match(shared, /key: "renewal", label: "续班率", format: "rate"/);
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
test("hideFilters hides only duplicate selectors and keeps scope chips and detail transfers", () => {
  const props = { controlledFilters: { ...emptyFilters, season: "暑假" }, hideFilters: true };
  const html = render(fourQuarters, "baView=detail", props);
  assert.doesNotMatch(html, /aria-label="共同筛选"|aria-label="筛选季度"/);
  assert.match(html, /aria-label="清除季度暑假"/);
  assert.ok(buttonText(html).some(button => button.text === "导出明细"));
  assert.match(render(fourQuarters), /class="budget-scope-bar" aria-label="当前范围"/);
});
test("an explicit parent scope hides only duplicate scope summary and retains units, links and scoped transfers", () => {
  const props = { controlledFilters: { ...emptyFilters, season: "秋季" }, hideFilters: true, hideScopeSummary: true };
  const html = render(fourQuarters, "baView=detail", props);
  assert.doesNotMatch(html, /aria-label="当前范围"|aria-label="清除季度秋季"|budget-scope-bar|筛选季度/);
  assert.match(html, /aria-label="金额单位"/); assert.match(html, /复制视图链接/);
  assert.match(html, /导出明细/); assert.match(html, /一键复制/);
  assert.match(html, /1 条/);
  assert.doesNotMatch(html.match(/<tbody>[\s\S]*?<\/tbody>/)?.[0] ?? "", /暑假|寒假|春季/);
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
  assert.doesNotMatch(text(missing), /全年结转收入/);
  // Existing callers remain uncontrolled: URL filters still take priority over initialFilters.
  assert.match(text(render(fourQuarters, old, { initialFilters: { ...emptyFilters, season: "秋季" } })), /季度：暑假/);
});

// Event/effect contract harness. No DOM, layout, browser download, network, or business mutation is simulated.
const elements = tree => Array.isArray(tree) ? tree.flatMap(elements) : !tree || typeof tree !== "object" ? []
  // Expand the stateless shared control so existing interaction assertions still
  // exercise its real select/chip callbacks rather than an opaque component.
  : [tree, ...elements(typeof tree.type === "function" && tree.type.name === "BudgetScopeBar" ? tree.type(tree.props) : tree.props?.children)];
const nodeText = node => Array.isArray(node) ? node.map(nodeText).join("") : node === null || node === undefined || typeof node === "boolean" ? ""
  : typeof node === "object" ? nodeText(typeof node.type === "function" && node.type.name === "BudgetScopeBar" ? node.type(node.props) : node.props?.children) : String(node);
const findButton = (tree, label) => elements(tree).find(node => node.type === "button" && nodeText(node) === label);
const viewActions = tree => elements(tree).find(node => node.type === "button" && node.props?.["aria-label"] === "视图更多操作");
function hookBrowser(search = "", views = []) {
  const names = ["location", "history", "window", "localStorage", "document", "navigator"];
  const saved = new Map(names.map(name => [name, Object.getOwnPropertyDescriptor(globalThis, name)]));
  const values = new Map([["budget-analysis-personal-views-v1", JSON.stringify(views)]]), listeners = new Map();
  let storageWriteFailure = false;
  const storage = { getItem: name => values.get(name) ?? null,
    setItem: (name, value) => { if (storageWriteFailure) throw new Error("storage write denied"); values.set(name, value); } };
  const setLocation = value => { globalThis.location = new URL(value, "http://127.0.0.1:5188"); if (globalThis.window) globalThis.window.location = globalThis.location; };
  setLocation(`http://127.0.0.1:5188/budget/next-year?year=2028&nyTask=5&nyFilters=1${search ? `&${search}` : ""}`);
  const writes = [], clipboard = [];
  const history = { state: null,
    pushState(state, _title, url) { this.state = structuredClone(state); writes.push(String(url)); setLocation(String(url)); },
    replaceState(state, _title, url) { this.state = structuredClone(state); writes.push(String(url)); setLocation(String(url)); } };
  const window = { location: globalThis.location, history, localStorage: storage,
    addEventListener(event, listener) { if (!listeners.has(event)) listeners.set(event, new Set()); listeners.get(event).add(listener); },
    removeEventListener(event, listener) { listeners.get(event)?.delete(listener); },
    requestAnimationFrame() { return 1; }, cancelAnimationFrame() {} };
  for (const [name, value] of Object.entries({ history, window, localStorage: storage, navigator: { clipboard: { async writeText(value) { clipboard.push(value); } } }, document: { createElement() { return { click() {} }; }, getElementById() { return null; } } })) {
    Object.defineProperty(globalThis, name, { configurable: true, writable: true, value });
  }
  return { writes, clipboard, setLocation, readViews() { return JSON.parse(storage.getItem("budget-analysis-personal-views-v1") ?? "[]"); },
    historySnapshot() { return { url: globalThis.location.href, state: structuredClone(history.state) }; },
    failStorageWrites(value = true) { storageWriteFailure = value; },
    dispatch(event, state) { if (event === "popstate" && arguments.length > 1) history.state = structuredClone(state);
      for (const listener of listeners.get(event) ?? []) listener({ state: history.state }); },
    restore() { for (const name of names) { const descriptor = saved.get(name); if (descriptor) Object.defineProperty(globalThis, name, descriptor); else delete globalThis[name]; } } };
}
function analysisHarness(initialProps) {
  const states = [], initialized = new Set(), refs = [], effectSlots = [], pendingEffects = [];
  let stateIndex = 0, refIndex = 0, effectIndex = 0, props = initialProps;
  const fakeReact = { ...React,
    useState(initial) { const index = stateIndex++; if (!initialized.has(index)) { states[index] = typeof initial === "function" ? initial() : initial; initialized.add(index); }
      return [states[index], value => { states[index] = typeof value === "function" ? value(states[index]) : value; }]; },
    useRef(initial) { return refs[refIndex++] ??= { current: initial }; }, useMemo(callback) { return callback(); }, useId() { return `analysis-harness-${refIndex}`; },
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
    // Child transfers use a table-shaped stub only; this is not a DOM/layout simulation.
    child(node) { return node.type(node.props); },
    effects() { const ready = pendingEffects.splice(0); ready.forEach(run => run()); },
    dispose() { effectSlots.forEach(slot => slot.cleanup?.()); } };
}

const activeTabText = tree => nodeText(elements(tree).find(node => node.props?.role === "tab" && node.props["aria-selected"]));
const detailDataset = tree => elements(tree).find(node => node.props?.className === "ba-table ba-detail-table")?.props.exportData;

test("detail rate headings and full-scope copy use business names without changing rate values or pivot semantics", async () => {
  const browser = hookBrowser("baView=detail&bf_season=" + encodeURIComponent("春季"));
  const facts = Array.from({ length: 30 }, (_, index) => fact({ id: `spring-${index}`, season: "春季",
    renewal: index === 0 ? 0 : index === 1 ? null : .765, detailDiscount: index === 0 ? 0 : index === 1 ? null : .914 }));
  const before = JSON.stringify(facts);
  try {
    const h = analysisHarness({ facts }), tree = h.render();
    const table = elements(tree).find(node => node.props?.className === "ba-table ba-detail-table");
    const headers = elements(table).filter(node => node.type === "thead").flatMap(node => elements(node).filter(e => e.type === "th").map(nodeText));
    assert.deepEqual(headers.slice(-2), ["续班率", "折扣率"]);
    const data = table.props.exportData;
    assert.equal(data.columns.find(c => c.key === "renewal").label, "续班率");
    assert.equal(data.columns.find(c => c.key === "detailDiscount").label, "折扣率");
    assert.ok(data.columns.filter(c => c.key === "renewal" || c.key === "detailDiscount").every(c => c.format === "rate"));
    assert.deepEqual(data.rows.map(row => [row.renewal, row.detailDiscount]), facts.map(row => [row.renewal, row.detailDiscount]));
    const child = h.child(table);
    elements(child).find(node => node.type === "table").props.ref({ rows: [], tHead: { rows: [] } });
    await findButton(child, "一键复制").props.onClick();
    const copied = browser.clipboard.at(-1).split("\n");
    assert.equal(copied.length, 31, "copy still includes all filtered rows, not just the visible 25");
    assert.ok(copied[0].includes("续班率")); assert.ok(copied[0].includes("折扣率"));
    assert.doesNotMatch(copied[0], /预算续班率|明细折扣/);
    assert.equal(JSON.stringify(facts), before);
    h.dispose();
  } finally { browser.restore(); }
});

test("a supplied saved-version export identity labels details and inherited board transfers without changing amounts or scope", () => {
  const browser = hookBrowser("baView=detail");
  try {
    const context = { fiscalYear: 2028, object: "预算编制", sequence: 12, createdAt: "2026-10-13T11:34:42Z", id: "exact-N12", asOf: "2026-10-08", preview: false, sourceId: "exact-P17", sourceLabel: "FY27全年预测 · 第17版" };
    const h = analysisHarness({ facts: fourQuarters, controlledFilters: { ...emptyFilters, season: "秋季" }, versionExportContext: context });
    const tree = h.render(), data = detailDataset(tree);
    assert.equal(data.filename, "FY28-预算编制-第12版-2026-10-13_1934-预算明细");
    assert.deepEqual(data.rows.map(row => row.id), ["1"]); assert.equal(data.rows[0].revenue, 10000);
    assert.deepEqual(JSON.parse(tree.props["data-budget-export-context"]), context);
    const preview = h.render({ versionExportContext: { ...context, preview: true } });
    assert.match(detailDataset(preview).filename, /第12版.*未保存预览/);
    assert.equal(JSON.parse(preview.props["data-budget-export-context"]).id, "exact-N12");
    assert.deepEqual(detailDataset(preview).rows, data.rows);
    h.dispose();
  } finally { browser.restore(); }
});

test("pivot copy adds the exact saved-version provenance while keeping its existing numeric yuan values", async () => {
  const browser = hookBrowser("baView=pivot&baRows=campus&baColumn=season&baMetrics=revenue");
  try {
    const h = analysisHarness({ facts: fourQuarters, versionExportContext: { fiscalYear: 2028, object: "预算编制", sequence: 12, createdAt: "2026-10-13T11:34:42Z", id: "exact-N12", asOf: "2026-10-08", sourceId: "exact-P17", sourceLabel: "FY27全年预测 · 第17版" } });
    await findButton(h.render(), "复制透视").props.onClick();
    assert.equal(browser.clipboard.length, 1);
    const copied = browser.clipboard[0];
    assert.match(copied, /来源模块\t结果版本\t精确版本标识\t保存时间（北京时间）\t数据截至/);
    assert.match(copied, /FY28预算编制\t第12版\texact-N12\t2026-10-13 19:34\t2026-10-08/);
    assert.match(copied, /数据截至\t编制基准\t精确来源标识/);
    assert.match(copied, /2026-10-08\tFY27全年预测 · 第17版\texact-P17/);
    assert.match(copied, /10000/); assert.doesNotMatch(copied, /未保存预览/);
    h.dispose();
  } finally { browser.restore(); }
});
const pivotCell = (tree, row, column, metric = "revenue") => elements(tree).find(node => node.type === "button"
  && node.props["data-row"] === row && node.props["data-col"] === column && node.props["data-metric"] === metric);

test("sibling tab keyboard navigation includes details while preserving the shared scope", () => {
  const browser = hookBrowser(`bf_season=${encodeURIComponent("秋季")}`);
  try {
    const harness = analysisHarness({ facts: fourQuarters });
    let tree = harness.render();
    assert.equal(activeTabText(tree), "看板");
    let prevented = 0;
    findButton(tree, "看板").props.onKeyDown({ key: "ArrowRight", preventDefault() { prevented++; } });
    tree = harness.render(); assert.equal(activeTabText(tree), "透视");
    findButton(tree, "透视").props.onKeyDown({ key: "ArrowRight", preventDefault() { prevented++; } });
    tree = harness.render(); assert.equal(activeTabText(tree), "明细");
    assert.deepEqual(detailDataset(tree).rows.map(row => row.season), ["秋季"]);
    findButton(tree, "明细").props.onKeyDown({ key: "ArrowRight", preventDefault() { prevented++; } });
    tree = harness.render(); assert.equal(activeTabText(tree), "看板");
    findButton(tree, "看板").props.onKeyDown({ key: "End", preventDefault() { prevented++; } });
    tree = harness.render(); assert.equal(activeTabText(tree), "明细");
    assert.equal(prevented, 4);
    assert.equal(new URL(browser.writes.at(-1)).searchParams.get("bf_season"), "秋季");
    harness.dispose();
  } finally { browser.restore(); }
});

test("pivot range selection stays in the pivot and explicit detail navigation deduplicates without changing filters", () => {
  const browser = hookBrowser("baView=pivot&baRows=campus,product&baMetrics=revenue&baUnit=yuan");
  try {
    const facts = fourQuarters.map(row => ({ ...row })), before = JSON.stringify(facts);
    const harness = analysisHarness({ facts });
    let tree = harness.render(); harness.effects(); tree = harness.render();
    pivotCell(tree, 0, 0).props.onPointerDown({ button: 0, shiftKey: false });
    tree = harness.render();
    assert.equal(activeTabText(tree), "透视"); assert.equal(detailDataset(tree), undefined);
    pivotCell(tree, 1, 0).props.onPointerEnter();
    browser.dispatch("pointerup"); tree = harness.render();
    assert.equal(activeTabText(tree), "透视");
    assert.match(nodeText(tree), /2 个有效数值/);
    assert.equal(elements(tree).filter(node => node.props?.className?.includes("ba-cell-selected")).length, 2);
    findButton(tree, "查看所选明细").props.onClick(); tree = harness.render();
    assert.equal(activeTabText(tree), "明细");
    assert.deepEqual(detailDataset(tree).rows.map(row => row.id), ["0"], "subtotal and child selections must export each source row once");
    assert.equal([...new URL(browser.writes.at(-1)).searchParams.keys()].some(key => key.startsWith("bf_")), false);
    findButton(tree, "透视").props.onClick(); tree = harness.render();
    assert.equal(activeTabText(tree), "透视");
    assert.equal(elements(tree).filter(node => node.props?.className?.includes("ba-cell-selected")).length, 2);
    findButton(tree, "明细").props.onClick(); tree = harness.render();
    assert.equal(detailDataset(tree).rows.length, 4, "the sibling 明细 tab opens the complete shared scope, not an old selection");
    findButton(tree, "透视").props.onClick(); tree = harness.render();
    assert.ok(findButton(tree, "查看所选明细"), "viewing all details must not destroy the stored pivot range");
    assert.equal(JSON.stringify(facts), before);
    harness.dispose();
  } finally { browser.restore(); }
});

test("returning from selected details retains pivot hierarchy folding and selected values", () => {
  const browser = hookBrowser("baView=pivot&baRows=campus,product&baMetrics=revenue");
  try {
    const harness = analysisHarness({ facts: [...fourQuarters, fact({ id: "platinum", product: "白金" })] });
    let tree = harness.render(); harness.effects(); tree = harness.render();
    elements(tree).find(node => node.props?.["aria-label"] === "折叠西直门").props.onClick();
    tree = harness.render();
    pivotCell(tree, 0, 0).props.onClick({ detail: 0, shiftKey: false }); tree = harness.render();
    findButton(tree, "查看所选明细").props.onClick(); tree = harness.render();
    assert.deepEqual(detailDataset(tree).rows.map(row => row.id).sort(), ["0", "platinum"]);
    findButton(tree, "透视").props.onClick(); tree = harness.render();
    const expand = elements(tree).find(node => node.props?.["aria-label"] === "展开西直门");
    assert.ok(expand); assert.equal(expand.props["aria-expanded"], false);
    assert.equal(pivotCell(tree, 0, 0).props["aria-pressed"], true);
    assert.equal(pivotCell(tree, 1, 0), undefined, "folded product rows must stay folded on return");
    harness.dispose();
  } finally { browser.restore(); }
});

test("browser back and forward distinguish selected details from all details without serializing budget or selection data", () => {
  const browser = hookBrowser("baView=pivot&baRows=campus&baMetrics=revenue");
  try {
    const harness = analysisHarness({ facts: fourQuarters });
    let tree = harness.render(); harness.effects(); tree = harness.render();
    const pivotEntry = browser.historySnapshot();
    pivotCell(tree, 0, 0).props.onClick({ detail: 0, shiftKey: false }); tree = harness.render(); harness.effects(); tree = harness.render();
    findButton(tree, "查看所选明细").props.onClick(); tree = harness.render(); harness.effects(); tree = harness.render();
    const selectedEntry = browser.historySnapshot();
    assert.equal(selectedEntry.state.baDetailSelection, true);
    assert.deepEqual(detailDataset(tree).rows.map(row => row.id), ["0"]);
    assert.deepEqual(Object.keys(selectedEntry.state), ["baDetailSelection"], "history stores only a detail-mode marker, never a fact or cell selection snapshot");
    browser.setLocation(pivotEntry.url); browser.dispatch("popstate", pivotEntry.state);
    tree = harness.render(); harness.effects(); tree = harness.render();
    assert.equal(activeTabText(tree), "透视"); assert.equal(pivotCell(tree, 0, 0).props["aria-pressed"], true);
    browser.setLocation(selectedEntry.url); browser.dispatch("popstate", selectedEntry.state);
    tree = harness.render(); harness.effects(); tree = harness.render();
    assert.equal(activeTabText(tree), "明细");
    assert.deepEqual(detailDataset(tree).rows.map(row => row.id), ["0"], "forward to selected details must not widen to every quarter");
    findButton(tree, "透视").props.onClick(); tree = harness.render(); harness.effects(); tree = harness.render();
    const returnEntry = browser.historySnapshot();
    findButton(tree, "明细").props.onClick(); tree = harness.render(); harness.effects(); tree = harness.render();
    const allEntry = browser.historySnapshot();
    assert.equal(allEntry.state.baDetailSelection, false); assert.equal(detailDataset(tree).rows.length, 4);
    browser.setLocation(returnEntry.url); browser.dispatch("popstate", returnEntry.state);
    tree = harness.render(); harness.effects(); tree = harness.render();
    browser.setLocation(allEntry.url); browser.dispatch("popstate", allEntry.state);
    tree = harness.render(); harness.effects(); tree = harness.render();
    assert.equal(detailDataset(tree).rows.length, 4, "forward to the all-details entry must not reuse a stored pivot selection");
    findButton(tree, "透视").props.onClick(); tree = harness.render(); harness.effects(); tree = harness.render();
    findButton(tree, "清除选择").props.onClick(); tree = harness.render(); harness.effects(); tree = harness.render();
    browser.setLocation(selectedEntry.url); browser.dispatch("popstate", selectedEntry.state);
    tree = harness.render(); harness.effects(); tree = harness.render();
    assert.equal(detailDataset(tree).rows.length, 4, "a marker cannot resurrect a cleared temporary selection");
    harness.dispose();
  } finally { browser.restore(); }
});

test("a selected-detail history marker is ignored when the shared filter scope or pivot config changes", () => {
  const browser = hookBrowser("baView=pivot&baRows=campus&baMetrics=revenue");
  try {
    const harness = analysisHarness({ facts: fourQuarters });
    let tree = harness.render(); harness.effects(); tree = harness.render();
    pivotCell(tree, 0, 0).props.onClick({ detail: 0, shiftKey: false }); tree = harness.render(); harness.effects(); tree = harness.render();
    findButton(tree, "查看所选明细").props.onClick(); tree = harness.render(); harness.effects(); tree = harness.render();
    const selectedEntry = browser.historySnapshot();
    const changedScope = new URL(selectedEntry.url); changedScope.searchParams.set("bf_season", "秋季");
    browser.setLocation(changedScope.href); browser.dispatch("popstate", selectedEntry.state);
    tree = harness.render(); harness.effects(); tree = harness.render();
    assert.deepEqual(detailDataset(tree).rows.map(row => row.id), ["1"]);
    assert.equal(elements(tree).some(node => node.type === "h3" && nodeText(node) === "所选明细"), false);
    const changedConfig = new URL(selectedEntry.url); changedConfig.searchParams.set("baRows", "track");
    browser.setLocation(changedConfig.href); browser.dispatch("popstate", selectedEntry.state);
    tree = harness.render(); harness.effects(); tree = harness.render();
    assert.equal(detailDataset(tree).rows.length, 4);
    assert.equal(elements(tree).some(node => node.type === "h3" && nodeText(node) === "所选明细"), false);
    harness.dispose();
  } finally { browser.restore(); }
});

test("compact pivot settings expand explicitly and metric changes preserve the active shared scope", () => {
  const browser = hookBrowser(`baView=pivot&baMetrics=renewal&bf_season=${encodeURIComponent("秋季")}`);
  try {
    const harness = analysisHarness({ facts: fourQuarters.map(row => ({ ...row, renewalNumerator: 70, renewalDenominator: 100 })) });
    let tree = harness.render();
    assert.equal(findButton(tree, "透视设置").props["aria-expanded"], false);
    assert.equal(elements(tree).some(node => node.type === "fieldset" && node.props.className === "ba-metrics"), false);
    findButton(tree, "透视设置").props.onClick(); tree = harness.render();
    assert.equal(findButton(tree, "透视设置").props["aria-expanded"], true);
    const metricFields = elements(tree).find(node => node.type === "fieldset" && node.props.className === "ba-metrics");
    assert.ok(metricFields);
    const renewalLabel = elements(metricFields).find(node => node.type === "label" && nodeText(node) === "预算续班率");
    const renewalInput = elements(renewalLabel).find(node => node.type === "input");
    assert.equal(renewalInput.props.checked, true); assert.equal(renewalInput.props.disabled, true, "the last active metric cannot be removed");
    const revenueLabel = elements(metricFields).find(node => node.type === "label" && nodeText(node) === "结转收入");
    elements(revenueLabel).find(node => node.type === "input").props.onChange({ target: { checked: true } });
    tree = harness.render();
    assert.equal(activeTabText(tree), "透视");
    assert.equal(new URL(browser.writes.at(-1)).searchParams.get("bf_season"), "秋季");
    assert.equal(new URL(browser.writes.at(-1)).searchParams.get("baMetrics"), "renewal,revenue");
    findButton(tree, "透视设置").props.onClick(); tree = harness.render();
    assert.equal(elements(tree).some(node => node.type === "fieldset"), false);
    assert.ok(elements(tree).some(node => node.props?.className === "ba-config-summary" && nodeText(node).includes("预算续班率、结转收入")));
    harness.dispose();
  } finally { browser.restore(); }
});

test("one grouped pivot selector isolates saved views by budget and retains legacy saved filters", () => {
  const state = { view: "pivot", unit: "wan", filters: { ...emptyFilters, season: "暑假" }, config: { rows: ["campus"], column: "season", metrics: ["new"], sort: "name-asc" } };
  const current = { id: "legacy", name: "我的暑假招生", contextKey: "controlled:2028", fiscalYear: 2028, state };
  const other = { id: "other", name: "FY27私有视图", contextKey: "archive:fy27", fiscalYear: 2027, state };
  const browser = hookBrowser(`baView=pivot&bf_season=${encodeURIComponent("秋季")}`, [current, other]);
  try {
    const harness = analysisHarness({ facts: fourQuarters });
    let tree = harness.render(); harness.effects(); tree = harness.render();
    const picker = elements(tree).find(node => node.props?.["aria-label"] === "透视视图");
    assert.deepEqual(elements(picker).filter(node => node.type === "optgroup").map(node => node.props.label), ["系统预设", "我的透视"]);
    assert.ok(elements(picker).some(node => node.type === "option" && node.props.value === "saved:legacy"));
    assert.equal(elements(picker).some(node => node.type === "option" && node.props.value === "saved:other"), false);
    picker.props.onChange({ target: { value: "saved:legacy" } }); tree = harness.render();
    assert.ok(elements(tree).some(node => node.props?.["aria-label"] === "清除季度暑假"), "old views with no flag must retain their saved filter semantics");
    assert.equal(elements(tree).find(node => node.props?.["aria-label"] === "透视视图").props.value, "saved:legacy");
    elements(tree).find(node => node.props?.["aria-label"] === "透视视图").props.onChange({ target: { value: "preset:renewal" } }); tree = harness.render();
    assert.equal(new URL(browser.writes.at(-1)).searchParams.get("baRows"), "track,grade");
    assert.equal(new URL(browser.writes.at(-1)).searchParams.get("baMetrics"), "renewal");
    assert.equal(new URL(browser.writes.at(-1)).searchParams.has("baSavedView"), false, "a system preset cannot retain the previous personal-view identity");
    assert.ok(elements(tree).some(node => node.props?.["aria-label"] === "清除季度暑假"), "system presets must not silently reset the current data scope");
    assert.deepEqual(browser.readViews(), [current, other], "view selection never rewrites saved view records");
    harness.dispose();
  } finally { browser.restore(); }
});

test("a system preset has one first-save action and a personal view enables saving only after a real edit", () => {
  const browser = hookBrowser("baView=pivot&baRows=campus&baMetrics=revenue");
  try {
    const facts = fourQuarters.map(row => ({ ...row })), before = JSON.stringify(facts);
    const harness = analysisHarness({ facts });
    let tree = harness.render(); harness.effects(); tree = harness.render();
    assert.ok(findButton(tree, "保存视图"));
    assert.equal(findButton(tree, "保存修改"), undefined);
    assert.equal(findButton(tree, "另存为"), undefined, "the system-preset toolbar must not present two identical create actions");
    findButton(tree, "保存视图").props.onClick(); tree = harness.render();
    elements(tree).find(node => node.props?.["aria-label"] === "视图名称").props.onChange({ target: { value: "校区测试透视" } }); tree = harness.render();
    elements(tree).find(node => node.type === "form" && node.props["aria-label"] === "保存透视").props.onSubmit({ preventDefault() {} }); tree = harness.render();
    assert.equal(browser.readViews().length, 1);
    assert.equal(findButton(tree, "保存视图"), undefined);
    assert.equal(findButton(tree, "保存修改").props.disabled, true);
    assert.equal(findButton(tree, "另存为"), undefined, "save-as must stay inside the nearby more-actions popover");
    assert.equal(new URL(globalThis.location.href).searchParams.get("baSavedView"), browser.readViews()[0].id);
    elements(tree).find(node => node.props?.["aria-label"] === "金额单位").props.onChange({ target: { value: "yuan" } }); tree = harness.render();
    assert.equal(findButton(tree, "保存修改").props.disabled, false);
    assert.ok(elements(tree).some(node => node.props?.className === "ba-unsaved"));
    elements(tree).find(node => node.props?.["aria-label"] === "金额单位").props.onChange({ target: { value: "wan" } }); tree = harness.render();
    assert.equal(findButton(tree, "保存修改").props.disabled, true, "reverting to the stored configuration is not an unsaved edit");
    assert.equal(elements(tree).some(node => node.props?.className === "ba-unsaved"), false);
    viewActions(tree).props.onClick(); tree = harness.render();
    assert.ok(findButton(tree, "另存为")); assert.ok(findButton(tree, "删除"));
    assert.equal(JSON.stringify(facts), before);
    harness.dispose();
  } finally { browser.restore(); }
});

test("a saved identity survives refresh while the actual URL configuration and current scope remain authoritative", async () => {
  const saved = { id: "refresh", name: "我的暑假收入", contextKey: "controlled:2028", fiscalYear: 2028, includesFilters: true,
    state: { view: "pivot", unit: "wan", filters: { ...emptyFilters, season: "暑假" }, config: { rows: ["campus"], column: "season", metrics: ["revenue"], sort: "revenue-desc" } } };
  const browser = hookBrowser(`baView=pivot&baRows=track&baColumn=season&baMetrics=new&baSort=name-asc&baUnit=yuan&baSavedView=refresh&bf_season=${encodeURIComponent("秋季")}`, [saved]);
  try {
    const changes = [], harness = analysisHarness({ facts: fourQuarters, onFiltersChange: value => changes.push(value) });
    let tree = harness.render(); harness.effects(); tree = harness.render();
    assert.equal(elements(tree).find(node => node.props?.["aria-label"] === "透视视图").props.value, "saved:refresh");
    assert.equal(findButton(tree, "保存修改").props.disabled, false);
    assert.ok(elements(tree).some(node => node.props?.className === "ba-unsaved"));
    assert.ok(elements(tree).some(node => node.props?.["aria-label"] === "清除季度秋季"));
    assert.ok(elements(tree).some(node => node.props?.className === "ba-config-summary" && nodeText(node).includes("赛道")));
    assert.ok(elements(tree).some(node => node.type === "th" && nodeText(node).includes("新生人次")));
    assert.equal(elements(tree).find(node => node.props?.["aria-label"] === "金额单位").props.value, "yuan");
    assert.deepEqual(changes, [], "identity restoration must not reload stored filters or call parent validation");
    assert.deepEqual(browser.readViews(), [saved]);
    await findButton(tree, "复制视图链接").props.onClick();
    const shared = new URL(browser.clipboard.at(-1));
    assert.equal(shared.searchParams.has("baSavedView"), false, "a browser-private record id cannot travel in a copied share link");
    assert.equal(shared.searchParams.get("baRows"), "track"); assert.equal(shared.searchParams.get("baMetrics"), "new");
    assert.equal(shared.searchParams.get("bf_season"), "秋季");
    findButton(tree, "保存修改").props.onClick(); tree = harness.render();
    assert.equal(findButton(tree, "保存修改").props.disabled, true);
    assert.deepEqual(browser.readViews()[0].state.config.rows, ["track"]);
    assert.equal(browser.readViews()[0].state.unit, "yuan");
    assert.equal(browser.readViews()[0].state.filters.season, "秋季");
    harness.dispose();
    const refreshed = analysisHarness({ facts: fourQuarters });
    let after = refreshed.render(); refreshed.effects(); after = refreshed.render();
    assert.equal(elements(after).find(node => node.props?.["aria-label"] === "透视视图").props.value, "saved:refresh");
    assert.equal(findButton(after, "保存修改").props.disabled, true);
    refreshed.dispose();
  } finally { browser.restore(); }
});

test("unknown and other-budget saved identity markers cannot select or overwrite a private view", () => {
  const other = { id: "other", name: "其他预算透视", contextKey: "archive:fy27", fiscalYear: 2027,
    state: { view: "pivot", unit: "wan", filters: { ...emptyFilters }, config: { rows: ["campus"], column: "season", metrics: ["revenue"], sort: "revenue-desc" } } };
  for (const id of ["missing", "other"]) {
    const browser = hookBrowser(`baView=pivot&baSavedView=${id}`, [other]);
    try {
      const harness = analysisHarness({ facts: fourQuarters });
      let tree = harness.render(); harness.effects(); tree = harness.render();
      assert.ok(findButton(tree, "保存视图")); assert.equal(findButton(tree, "保存修改"), undefined);
      assert.equal(elements(tree).find(node => node.props?.["aria-label"] === "透视视图").props.value.startsWith("saved:"), false);
      assert.deepEqual(browser.readViews(), [other]);
      harness.dispose();
    } finally { browser.restore(); }
  }
});

test("back and forward restore the saved identity without reloading stored filters over the current URL", () => {
  const saved = { id: "history", name: "历史视图", contextKey: "controlled:2028", fiscalYear: 2028,
    state: { view: "pivot", unit: "wan", filters: { ...emptyFilters, season: "暑假" }, config: { rows: ["campus"], column: "season", metrics: ["new"], sort: "name-asc" } } };
  const browser = hookBrowser("baView=pivot", [saved]);
  try {
    const harness = analysisHarness({ facts: fourQuarters });
    let tree = harness.render(); harness.effects(); tree = harness.render();
    elements(tree).find(node => node.props?.["aria-label"] === "透视视图").props.onChange({ target: { value: "saved:history" } }); tree = harness.render(); harness.effects(); tree = harness.render();
    elements(tree).find(node => node.props?.["aria-label"] === "筛选季度").props.onChange("秋季"); tree = harness.render(); harness.effects(); tree = harness.render();
    const savedEntry = browser.historySnapshot();
    assert.equal(new URL(savedEntry.url).searchParams.get("baSavedView"), "history");
    elements(tree).find(node => node.props?.["aria-label"] === "透视视图").props.onChange({ target: { value: "preset:renewal" } }); tree = harness.render(); harness.effects(); tree = harness.render();
    const presetEntry = browser.historySnapshot();
    browser.setLocation(savedEntry.url); browser.dispatch("popstate", savedEntry.state);
    tree = harness.render(); harness.effects(); tree = harness.render();
    assert.equal(elements(tree).find(node => node.props?.["aria-label"] === "透视视图").props.value, "saved:history");
    assert.ok(elements(tree).some(node => node.props?.["aria-label"] === "清除季度秋季"));
    assert.equal(findButton(tree, "保存修改").props.disabled, false, "the unsaved filter edit survives history navigation");
    browser.setLocation(presetEntry.url); browser.dispatch("popstate", presetEntry.state);
    tree = harness.render(); harness.effects(); tree = harness.render();
    assert.equal(elements(tree).find(node => node.props?.["aria-label"] === "透视视图").props.value, "preset:renewal");
    assert.ok(findButton(tree, "保存视图")); assert.equal(findButton(tree, "保存修改"), undefined);
    assert.deepEqual(browser.readViews(), [saved]);
    harness.dispose();
  } finally { browser.restore(); }
});

test("layout-only views do not become dirty merely because shared filters change", () => {
  const saved = { id: "layout", name: "仅布局", contextKey: "controlled:2028", fiscalYear: 2028, includesFilters: false,
    state: { view: "pivot", unit: "wan", filters: { ...emptyFilters, season: "暑假" }, config: { rows: ["campus"], column: "season", metrics: ["new"], sort: "name-asc" } } };
  const browser = hookBrowser(`baView=pivot&bf_season=${encodeURIComponent("秋季")}`, [saved]);
  try {
    const harness = analysisHarness({ facts: fourQuarters });
    let tree = harness.render(); harness.effects(); tree = harness.render();
    elements(tree).find(node => node.props?.["aria-label"] === "透视视图").props.onChange({ target: { value: "saved:layout" } }); tree = harness.render();
    assert.equal(findButton(tree, "保存修改").props.disabled, true);
    elements(tree).find(node => node.props?.["aria-label"] === "筛选季度").props.onChange("寒假"); tree = harness.render();
    assert.equal(findButton(tree, "保存修改").props.disabled, true);
    assert.equal(elements(tree).some(node => node.props?.className === "ba-unsaved"), false);
    assert.deepEqual(browser.readViews(), [saved]);
    harness.dispose();
  } finally { browser.restore(); }
});

test("switching budget contexts restores the destination saved identity instead of clearing it with the previous context", () => {
  const state = { view: "pivot", unit: "wan", filters: { ...emptyFilters }, config: { rows: ["campus"], column: "season", metrics: ["new"], sort: "name-asc" } };
  const first = { id: "context-a", name: "A预算透视", contextKey: "budget:a", fiscalYear: 2027, state };
  const second = { id: "context-b", name: "B预算透视", contextKey: "budget:b", fiscalYear: 2028, state };
  const browser = hookBrowser("baView=pivot&baRows=campus&baMetrics=new&baSort=name-asc&baSavedView=context-a", [first, second]);
  try {
    const harness = analysisHarness({ facts: fourQuarters, contextKey: "budget:a", fiscalYear: 2027 });
    let tree = harness.render(); harness.effects(); tree = harness.render(); harness.effects(); tree = harness.render();
    assert.equal(elements(tree).find(node => node.props?.["aria-label"] === "透视视图").props.value, "saved:context-a");
    browser.setLocation("http://127.0.0.1:5188/budget/next-year?baView=pivot&baRows=campus&baMetrics=new&baSort=name-asc&baSavedView=context-b");
    tree = harness.render({ contextKey: "budget:b", fiscalYear: 2028 }); harness.effects(); tree = harness.render(); harness.effects(); tree = harness.render();
    assert.equal(elements(tree).find(node => node.props?.["aria-label"] === "透视视图").props.value, "saved:context-b");
    assert.equal(new URL(globalThis.location.href).searchParams.get("baSavedView"), "context-b");
    assert.equal(findButton(tree, "保存修改").props.disabled, true);
    assert.deepEqual(browser.readViews(), [first, second]);
    harness.dispose();
  } finally { browser.restore(); }
});

test("nearby view deletion names its exact target, cancellation preserves it, and confirmation retains the visible layout", () => {
  const view = { id: "remove", name: "秋季招生—待删除", contextKey: "controlled:2028", fiscalYear: 2028,
    state: { view: "pivot", unit: "yuan", filters: { ...emptyFilters, season: "秋季" }, config: { rows: ["campus"], column: "season", metrics: ["new"], sort: "name-asc" } } };
  const browser = hookBrowser("baView=pivot", [view]);
  try {
    const harness = analysisHarness({ facts: fourQuarters });
    let tree = harness.render(); harness.effects(); tree = harness.render();
    elements(tree).find(node => node.props?.["aria-label"] === "透视视图").props.onChange({ target: { value: "saved:remove" } }); tree = harness.render();
    viewActions(tree).props.onClick(); tree = harness.render();
    findButton(tree, "删除").props.onClick({ currentTarget: { isConnected: false } }); tree = harness.render();
    assert.match(nodeText(tree), /秋季招生—待删除/); assert.deepEqual(browser.readViews(), [view]);
    assert.ok(findButton(tree, "确认删除"));
    findButton(tree, "取消").props.onClick(); tree = harness.render();
    assert.deepEqual(browser.readViews(), [view]); assert.equal(findButton(tree, "确认删除"), undefined);
    viewActions(tree).props.onClick(); tree = harness.render();
    findButton(tree, "删除").props.onClick({ currentTarget: { isConnected: false } }); tree = harness.render();
    findButton(tree, "确认删除").props.onClick(); tree = harness.render();
    assert.deepEqual(browser.readViews(), []);
    assert.ok(findButton(tree, "保存视图"));
    assert.equal(new URL(globalThis.location.href).searchParams.has("baSavedView"), false);
    assert.ok(elements(tree).some(node => node.props?.["aria-label"] === "清除季度秋季"));
    assert.ok(elements(tree).some(node => node.type === "th" && nodeText(node).includes("新生人次")), "deletion removes only the saved configuration, not the current analysis");
    assert.equal(elements(tree).find(node => node.props?.["aria-label"] === "金额单位").props.value, "yuan");
    harness.dispose();
  } finally { browser.restore(); }
});

test("saving updates only the selected personal view, save-as creates a separate layout, and manager deletion preserves other budgets", () => {
  const state = { view: "pivot", unit: "wan", filters: { ...emptyFilters, season: "暑假" }, config: { rows: ["campus"], column: "season", metrics: ["new"], sort: "name-asc" } };
  const current = { id: "current", name: "当前透视", contextKey: "controlled:2028", fiscalYear: 2028, state };
  const other = { id: "other", name: "其他预算透视", contextKey: "archive:fy27", fiscalYear: 2027, state };
  const browser = hookBrowser("baView=pivot", [current, other]);
  try {
    const facts = fourQuarters.map(row => ({ ...row })), before = JSON.stringify(facts);
    const harness = analysisHarness({ facts });
    let tree = harness.render(); harness.effects(); tree = harness.render();
    elements(tree).find(node => node.props?.["aria-label"] === "透视视图").props.onChange({ target: { value: "saved:current" } }); tree = harness.render();
    elements(tree).find(node => node.props?.["aria-label"] === "金额单位").props.onChange({ target: { value: "yuan" } }); tree = harness.render();
    assert.ok(elements(tree).some(node => node.props?.className === "ba-unsaved" && nodeText(node) === "未保存"));
    findButton(tree, "保存修改").props.onClick(); tree = harness.render();
    assert.equal(browser.readViews().length, 2);
    assert.equal(browser.readViews().find(view => view.id === "current").state.unit, "yuan");
    assert.deepEqual(browser.readViews().find(view => view.id === "other"), { ...other, includesFilters: true });
    assert.equal(elements(tree).some(node => node.props?.className === "ba-unsaved"), false);
    viewActions(tree).props.onClick(); tree = harness.render();
    findButton(tree, "另存为").props.onClick(); tree = harness.render();
    elements(tree).find(node => node.props?.["aria-label"] === "视图名称").props.onChange({ target: { value: "仅保存布局" } }); tree = harness.render();
    const saveForm = elements(tree).find(node => node.type === "form" && node.props["aria-label"] === "保存透视");
    elements(saveForm).find(node => node.type === "input" && node.props.type === "checkbox").props.onChange({ target: { checked: false } }); tree = harness.render();
    elements(tree).find(node => node.type === "form" && node.props["aria-label"] === "保存透视").props.onSubmit({ preventDefault() {} }); tree = harness.render();
    const created = browser.readViews().find(view => view.name === "仅保存布局");
    assert.ok(created); assert.notEqual(created.id, "current"); assert.equal(created.includesFilters, false);
    assert.equal(browser.readViews().length, 3);
    elements(tree).find(node => node.props?.["aria-label"] === "筛选季度").props.onChange("秋季"); tree = harness.render();
    elements(tree).find(node => node.props?.["aria-label"] === "透视视图").props.onChange({ target: { value: "saved:current" } }); tree = harness.render();
    assert.ok(elements(tree).some(node => node.props?.["aria-label"] === "清除季度暑假"));
    elements(tree).find(node => node.props?.["aria-label"] === "筛选季度").props.onChange("秋季"); tree = harness.render();
    elements(tree).find(node => node.props?.["aria-label"] === "透视视图").props.onChange({ target: { value: `saved:${created.id}` } }); tree = harness.render();
    assert.ok(elements(tree).some(node => node.props?.["aria-label"] === "清除季度秋季"), "a layout-only saved view must retain the current filter scope");
    viewActions(tree).props.onClick(); tree = harness.render();
    findButton(tree, "另存为").props.onClick(); tree = harness.render();
    elements(tree).find(node => node.props?.["aria-label"] === "视图名称").props.onChange({ target: { value: "当前透视" } }); tree = harness.render();
    elements(tree).find(node => node.type === "form" && node.props["aria-label"] === "保存透视").props.onSubmit({ preventDefault() {} }); tree = harness.render();
    assert.match(nodeText(tree), /这个名称已存在/); assert.equal(browser.readViews().length, 3, "save-as must not silently overwrite a same-named view");
    findButton(tree, "取消").props.onClick(); tree = harness.render();
    findButton(tree, "管理").props.onClick(); tree = harness.render();
    assert.ok(elements(tree).some(node => node.props?.["aria-label"] === "管理我的透视"));
    assert.equal(elements(tree).some(node => node.props?.["aria-label"] === "删除个人视图其他预算透视"), false);
    elements(tree).find(node => node.props?.["aria-label"] === "删除个人视图仅保存布局").props.onClick({ currentTarget: { isConnected: false } }); tree = harness.render();
    assert.equal(browser.readViews().length, 3, "delete must wait for a named confirmation");
    assert.match(nodeText(tree), /仅保存布局/);
    findButton(tree, "确认删除").props.onClick(); tree = harness.render();
    assert.equal(browser.readViews().length, 2); assert.equal(browser.readViews().some(view => view.id === created.id), false);
    assert.deepEqual(browser.readViews().find(view => view.id === "other"), { ...other, includesFilters: true });
    assert.equal(JSON.stringify(facts), before, "saving and deleting display configurations must never mutate budget facts");
    harness.dispose();
  } finally { browser.restore(); }
});

test("saved-view actions wait for storage initialization and failed writes leave saved views and budget facts unchanged", () => {
  const state = { view: "pivot", unit: "wan", filters: { ...emptyFilters }, config: { rows: ["campus"], column: "season", metrics: ["new"], sort: "name-asc" } };
  const existing = { id: "existing", name: "已有透视", contextKey: "controlled:2028", fiscalYear: 2028, state };
  const browser = hookBrowser("baView=pivot", [existing]);
  try {
    const facts = fourQuarters.map(row => ({ ...row })), before = JSON.stringify(facts);
    const harness = analysisHarness({ facts });
    let tree = harness.render();
    assert.equal(findButton(tree, "保存视图").props.disabled, true);
    assert.equal(findButton(tree, "另存为"), undefined, "save-as is not a duplicate first-save button");
    harness.effects(); tree = harness.render();
    assert.equal(findButton(tree, "保存视图").props.disabled, false);
    elements(tree).find(node => node.props?.["aria-label"] === "透视视图").props.onChange({ target: { value: "saved:existing" } }); tree = harness.render();
    elements(tree).find(node => node.props?.["aria-label"] === "金额单位").props.onChange({ target: { value: "yuan" } }); tree = harness.render();
    browser.failStorageWrites();
    findButton(tree, "保存修改").props.onClick(); tree = harness.render();
    assert.match(nodeText(tree), /无法保存个人视图/); assert.deepEqual(browser.readViews(), [existing]);
    assert.ok(elements(tree).some(node => node.props?.className === "ba-unsaved"), "failed update must remain visibly unsaved");
    viewActions(tree).props.onClick(); tree = harness.render();
    findButton(tree, "另存为").props.onClick(); tree = harness.render();
    elements(tree).find(node => node.props?.["aria-label"] === "视图名称").props.onChange({ target: { value: "未落盘的新透视" } }); tree = harness.render();
    elements(tree).find(node => node.type === "form" && node.props["aria-label"] === "保存透视").props.onSubmit({ preventDefault() {} }); tree = harness.render();
    assert.deepEqual(browser.readViews(), [existing]);
    assert.equal(elements(tree).find(node => node.props?.["aria-label"] === "视图名称").props.value, "未落盘的新透视", "failed save-as retains the name for retry");
    findButton(tree, "取消").props.onClick(); tree = harness.render();
    findButton(tree, "管理").props.onClick(); tree = harness.render();
    elements(tree).find(node => node.props?.["aria-label"] === "删除个人视图已有透视").props.onClick({ currentTarget: { isConnected: false } }); tree = harness.render();
    findButton(tree, "确认删除").props.onClick(); tree = harness.render();
    assert.deepEqual(browser.readViews(), [existing]);
    assert.ok(elements(tree).some(node => node.props?.["aria-label"] === "删除个人视图已有透视"), "failed deletion cannot hide a still-saved view");
    assert.equal(JSON.stringify(facts), before);
    harness.dispose();
  } finally { browser.restore(); }
});

test("malformed stored views sanitize filters and layout before reaching controlled callbacks", () => {
  const base = { contextKey: "controlled:2028", fiscalYear: 2028 };
  const views = [null, 17, {}, { ...base, id: "no-state", name: "无状态", state: null },
    { ...base, id: "objects", name: "损坏维度", state: { view: { invalid: true }, unit: ["yuan"],
      filters: { track: null, campus: { invalid: true }, product: ["白金"], grade: 7, subject: true, season: "秋季", extra: { injected: true } } } },
    { ...base, id: "filter-array", name: "数组筛选", state: { filters: ["秋季"] } },
    { ...base, id: "filter-number", name: "数值筛选", state: { filters: 23, config: { rows: ["campus", "campus", { invalid: true }, "unknown"],
      column: {}, metrics: ["new", 7, {}, "new"], sort: ["name-asc"] } } },
    { ...base, id: "state-array", name: "数组状态", state: [] }];
  const browser = hookBrowser("baView=pivot", views), changes = [];
  try {
    const harness = analysisHarness({ facts: fourQuarters, controlledFilters: { ...emptyFilters, season: "暑假" }, onFiltersChange: value => changes.push(value) });
    let tree = harness.render(); harness.effects(); tree = harness.render();
    const picker = () => elements(tree).find(node => node.props?.["aria-label"] === "透视视图");
    assert.equal(elements(picker()).some(node => node.type === "option" && node.props.value === "saved:no-state"), false);
    for (const [id, expected] of [["objects", { ...emptyFilters, product: "白金", season: "秋季" }], ["filter-array", emptyFilters], ["filter-number", emptyFilters], ["state-array", emptyFilters]]) {
      picker().props.onChange({ target: { value: `saved:${id}` } }); tree = harness.render();
      assert.deepEqual(changes.at(-1), expected, `${id} must contain only the six valid dimension selections, including legitimate string arrays`);
      assert.ok(Object.values(changes.at(-1)).every(value => typeof value === "string"));
      const url = new URL(browser.writes.at(-1));
      assert.equal(url.searchParams.get("baRows"), "campus");
      assert.equal(url.searchParams.get("baColumn"), "season");
      assert.equal(url.searchParams.get("baMetrics"), id === "filter-number" ? "new" : "revenue");
      assert.equal(url.searchParams.get("baSort"), "revenue-desc");
      assert.equal(url.searchParams.get("baUnit"), "wan");
      assert.doesNotMatch(nodeText(tree), /\[object Object\]|undefined|NaN/);
    }
    assert.deepEqual(browser.readViews(), views, "reading and selecting sanitized views must not silently rewrite local storage");
    harness.dispose();
  } finally { browser.restore(); }
});

test("stored string labels for disabled or unpreviewed businesses remain intact for parent validation", () => {
  const filters = { ...emptyFilters, campus: "已停用的合法校区", product: "尚未预览的合法产品" };
  const view = { id: "pending", name: "待预览业务", contextKey: "controlled:2028", fiscalYear: 2028,
    state: { view: "pivot", unit: "yuan", filters, config: { rows: ["campus"], column: "season", metrics: ["new"], sort: "name-asc" } } };
  const browser = hookBrowser("baView=pivot", [view]), changes = [];
  try {
    const harness = analysisHarness({ facts: fourQuarters, controlledFilters: { ...emptyFilters }, onFiltersChange: value => changes.push(value) });
    let tree = harness.render(); harness.effects(); tree = harness.render();
    elements(tree).find(node => node.props?.["aria-label"] === "透视视图").props.onChange({ target: { value: "saved:pending" } }); tree = harness.render();
    assert.deepEqual(changes, [filters], "sanitization removes invalid types, not legitimate unknown business labels");
    tree = harness.render({ controlledFilters: filters });
    assert.match(nodeText(tree), /这个范围没有预算明细/);
    assert.ok(elements(tree).some(node => node.props?.["aria-label"] === "清除校区已停用的合法校区"));
    assert.ok(elements(tree).some(node => node.props?.["aria-label"] === "清除产品尚未预览的合法产品"));
    assert.deepEqual(browser.readViews(), [view]);
    harness.dispose();
  } finally { browser.restore(); }
});

test("saved-view dirty comparison ignores filter-object property order", () => {
  const reversedFilters = Object.fromEntries(Object.entries({ ...emptyFilters, season: "秋季" }).reverse());
  const view = { id: "reordered", name: "排序稳定视图", contextKey: "controlled:2028", fiscalYear: 2028,
    state: { view: "pivot", unit: "wan", filters: reversedFilters, config: { rows: ["campus"], column: "season", metrics: ["new"], sort: "name-asc" } } };
  const browser = hookBrowser("baView=pivot", [view]), changes = [];
  try {
    const harness = analysisHarness({ facts: fourQuarters, controlledFilters: { ...emptyFilters }, onFiltersChange: value => changes.push(value) });
    let tree = harness.render(); harness.effects(); tree = harness.render();
    elements(tree).find(node => node.props?.["aria-label"] === "透视视图").props.onChange({ target: { value: "saved:reordered" } });
    tree = harness.render({ controlledFilters: { ...emptyFilters, season: "秋季" } });
    assert.equal(elements(tree).some(node => node.props?.className === "ba-unsaved"), false);
    assert.deepEqual(changes.at(-1), { ...emptyFilters, season: "秋季" });
    harness.dispose();
  } finally { browser.restore(); }
});

test("returning from a formal record restores the pivot scroll contract even when the selected analysis view is unchanged", () => {
  const browser = hookBrowser("baView=pivot&baRows=campus,product&baMetrics=revenue");
  try {
    const recordContent = createElement("section", { "aria-label": "定版验收记录" }, "R150 已定版");
    const harness = analysisHarness({ facts: fourQuarters, recordContent });
    let tree = harness.render(); harness.effects(); tree = harness.render();
    const oldWrap = { scrollTop: 275, scrollLeft: 900 };
    const table = elements(tree).find(node => node.type === "table" && node.props.className === "ba-table ba-pivot-table");
    table.props.ref.current = { closest: () => oldWrap };
    pivotCell(tree, 0, 0).props.onClick({ detail: 0, shiftKey: false }); tree = harness.render(); harness.effects(); tree = harness.render();
    findButton(tree, "定版记录").props.onClick(); tree = harness.render();
    assert.equal(activeTabText(tree), "定版记录");
    table.props.ref.current = null; // A native DOM would clear the ref on unmount.
    harness.effects(); tree = harness.render();
    findButton(tree, "透视").props.onClick(); tree = harness.render();
    const nextWrap = { scrollTop: 0, scrollLeft: 0 };
    elements(tree).find(node => node.type === "table" && node.props.className === "ba-table ba-pivot-table").props.ref.current = { closest: () => nextWrap };
    harness.effects(); tree = harness.render();
    assert.equal(activeTabText(tree), "透视");
    assert.deepEqual(nextWrap, oldWrap, "recordView transitions must retrigger restoration even though state.view remained pivot");
    assert.equal(pivotCell(tree, 0, 0).props["aria-pressed"], true);
    assert.ok(findButton(tree, "查看所选明细"));
    harness.dispose();
  } finally { browser.restore(); }
});

test("the combined campus board filters in place and switches to a quarterly pivot with all four metrics", () => {
  const browser = hookBrowser("baUnit=yuan"), changes = [];
  try {
    const facts = [...fourQuarters, fact({ id: "other", campus: "中关村" })];
    const harness = analysisHarness({ facts, controlledFilters: { ...emptyFilters }, onFiltersChange: value => changes.push(value) });
    const tree = harness.render();
    const people = elements(tree).find(node => node.type?.name === "BudgetPeopleBoard");
    assert.ok(people);
    const campus = elements(people.type(people.props)).find(node => node.type?.name === "BudgetCampusBoard");
    assert.ok(campus);
    assert.equal(campus.props.unit, "yuan");
    assert.deepEqual(campus.props.facts.map(row => row.id), facts.map(row => row.id));
    campus.props.onFilterCampus("西直门");
    assert.deepEqual(changes.at(-1), { ...emptyFilters, campus: "西直门" });
    let updated = harness.render({ controlledFilters: { ...emptyFilters, campus: "西直门" } });
    const filteredPeople = elements(updated).find(node => node.type?.name === "BudgetPeopleBoard");
    const filteredCampus = elements(filteredPeople.type(filteredPeople.props)).find(node => node.type?.name === "BudgetCampusBoard");
    assert.deepEqual(filteredCampus.props.facts.map(row => row.id), fourQuarters.map(row => row.id));
    filteredCampus.props.onPivot();
    updated = harness.render();
    assert.ok(elements(updated).some(node => node.type === "h3" && nodeText(node) === "预算透视"));
    const url = new URL(browser.writes.at(-1));
    assert.equal(url.searchParams.get("baRows"), "campus");
    assert.equal(url.searchParams.get("baMetrics"), "total,new,old,revenue");
    assert.equal(url.searchParams.get("baUnit"), "yuan");
    assert.ok(elements(updated).some(node => node.props?.["aria-label"] === "清除校区西直门"));
    harness.dispose();
  } finally { browser.restore(); }
});

test("an incoming controlled filter clears pivot selection before effects and details contain only the latest scope", () => {
  const browser = hookBrowser("baView=pivot&baRows=campus&baMetrics=revenue");
  try {
    const facts = [fact({ id: "summer", revenue: 11000 }), fact({ id: "autumn", season: "秋季", revenue: 22000 })];
    const harness = analysisHarness({ facts, controlledFilters: { ...emptyFilters } });
    let tree = harness.render(); harness.effects(); tree = harness.render();
    const cell = elements(tree).find(node => node.type === "button" && node.props["data-row"] === 0 && node.props["data-col"] === 0);
    cell.props.onClick({ detail: 0, shiftKey: false }); tree = harness.render();
    assert.ok(findButton(tree, "查看所选明细"));
    assert.equal(elements(tree).find(node => node.props?.exportData), undefined, "selecting a cell must not mount raw details");
    tree = harness.render({ controlledFilters: { ...emptyFilters, season: "秋季" } });
    assert.equal(findButton(tree, "查看所选明细"), undefined, "stale selection must vanish in the same render, not after an effect");
    const people = elements(tree).find(node => node.type?.name === "BudgetPeopleBoard");
    assert.equal(people, undefined, "pivot remains selected while parent filters change");
    tree = harness.render({ controlledFilters: { ...emptyFilters, season: "暑假" } });
    assert.equal(findButton(tree, "查看所选明细"), undefined, "old selections cannot reappear when switching back");
    tree = harness.render({ controlledFilters: { ...emptyFilters, season: "秋季" } });
    findButton(tree, "明细").props.onClick(); tree = harness.render();
    const exportTable = elements(tree).find(node => node.props?.exportData);
    assert.deepEqual(exportTable.props.exportData.rows.map(row => row.id), ["autumn"]);
    harness.dispose();
  } finally { browser.restore(); }
});
test("controlled chart drilling requests the parent filter without overriding the parent's URL filter", () => {
  const browser = hookBrowser(`bf_season=${encodeURIComponent("暑假")}`), changes = [];
  try {
    const harness = analysisHarness({ facts: fourQuarters, controlledFilters: { ...emptyFilters }, onFiltersChange: value => changes.push(value) });
    const tree = harness.render();
    elements(tree).find(node => node.props?.["aria-label"] === "查看秋季结转收入明细").props.onClick();
    assert.deepEqual(changes, [{ ...emptyFilters, season: "秋季" }]);
    assert.equal(new URL(browser.writes.at(-1)).searchParams.get("bf_season"), "暑假", "parent persists bf_* after accepting the callback");
    assert.equal(new URL(browser.writes.at(-1)).searchParams.get("nyFilters"), "1");
    assert.equal(new URL(browser.writes.at(-1)).searchParams.get("nyTask"), "5");
    const stillParentControlled = harness.render();
    assert.equal(elements(stillParentControlled).find(node => node.props?.exportData).props.exportData.rows.length, 4);
  } finally { browser.restore(); }
});
test("controlled chips and saved pivot selection send filters back to the parent for validation", () => {
  const view = { id: "autumn", name: "秋季招生", contextKey: "controlled:2028", fiscalYear: 2028,
    state: { view: "pivot", unit: "yuan", filters: { ...emptyFilters, season: "秋季", campus: "已退出校区" }, config: { rows: ["campus"], column: "season", metrics: ["new"], sort: "name-asc" } } };
  const browser = hookBrowser("baView=pivot", [view]), changes = [];
  try {
    const harness = analysisHarness({ facts: fourQuarters, controlledFilters: { ...emptyFilters, season: "暑假" }, onFiltersChange: value => changes.push(value) });
    let tree = harness.render(); harness.effects(); tree = harness.render();
    elements(tree).find(node => node.props?.["aria-label"] === "清除季度暑假").props.onClick();
    assert.deepEqual(changes.at(-1), emptyFilters);
    tree = harness.render();
    elements(tree).find(node => node.type === "select" && node.props["aria-label"] === "透视视图").props.onChange({ target: { value: "saved:autumn" } });
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
    assert.doesNotMatch(text(html), /40,000|全年结转收入|季度结转收入/);
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
    elements(tree).find(node => node.props?.["aria-label"] === "查看秋季结转收入明细").props.onClick();
    tree = harness.render(); assert.ok(elements(tree).some(node => node.props?.exportData)); assert.equal(changes.length, 0);
    browser.setLocation(`http://127.0.0.1:5188/budget/workspace?baView=pivot&bf_season=${encodeURIComponent("寒假")}`);
    browser.dispatch("popstate"); tree = harness.render();
    assert.deepEqual(changes, [{ ...emptyFilters, season: "寒假" }]);
    assert.ok(elements(tree).some(node => node.props?.["aria-label"] === "清除季度寒假"));
    harness.dispose();
  } finally { browser.restore(); }
});
test("detail-tab exports after an incoming controlled change contain only the latest scope without fetching data", async () => {
  const browser = hookBrowser(), oldFetch = globalThis.fetch, createURL = URL.createObjectURL, blobs = []; let requests = 0;
  globalThis.fetch = () => { requests++; throw new Error("unexpected data fetch"); };
  URL.createObjectURL = blob => { blobs.push(blob); return createURL(blob); };
  try {
    const harness = analysisHarness({ facts: fourQuarters, controlledFilters: { ...emptyFilters } });
    harness.render();
    let tree = harness.render({ controlledFilters: { ...emptyFilters, season: "秋季" } });
    findButton(tree, "明细").props.onClick(); tree = harness.render();
    const table = elements(tree).find(node => node.props?.exportData);
    assert.ok(table, "the sibling detail tab owns the export dataset");
    const child = harness.child(table);
    elements(child).find(node => node.type === "table").props.ref({ rows: [], tHead: { rows: [] } });
    const exported = new Promise((resolve, reject) => {
      const deadline = setTimeout(() => reject(new Error("detail export did not finish")), 5000);
      URL.createObjectURL = blob => { blobs.push(blob); clearTimeout(deadline); resolve(); return createURL(blob); };
    });
    findButton(child, "导出明细").props.onClick();
    await exported;
    assert.equal(blobs.length, 1);
    const ExcelJS = require("exceljs"), workbook = new ExcelJS.Workbook();
    await workbook.xlsx.load(await blobs[0].arrayBuffer());
    assert.equal(workbook.worksheets[0].rowCount, 2, "one header plus the one filtered fact");
    assert.equal(workbook.worksheets[0].getRow(2).getCell(6).value, "秋季");
    assert.equal(requests, 0);
  } finally { globalThis.fetch = oldFetch; URL.createObjectURL = createURL; browser.restore(); }
});

test("personal multi-select views save exact selections, survive navigation, and clear one scope without budget writes", () => {
  const filters = { ...emptyFilters, campus: ["A,B", "中关村"], season: ["暑假", "秋季"] };
  const state = { view: "pivot", unit: "yuan", filters, config: { rows: ["campus"], column: "season", metrics: ["revenue"], sort: "name-asc" } };
  const saved = { id: "multi", name: "两校区两季度", contextKey: "controlled:2028", fiscalYear: 2028, includesFilters: true, state };
  const facts = ["A,B", "中关村", "排除校区"].flatMap(campus => fourQuarters.map(row => ({ ...row, id: `${campus}-${row.season}`, campus })));
  const before = JSON.stringify(facts), browser = hookBrowser("baView=pivot", [saved]), oldFetch = globalThis.fetch;
  let requests = 0; globalThis.fetch = () => { requests++; throw new Error("filter state must not call budget APIs"); };
  try {
    const harness = analysisHarness({ facts });
    let tree = harness.render(); harness.effects(); tree = harness.render();
    elements(tree).find(node => node.props?.["aria-label"] === "透视视图").props.onChange({ target: { value: "saved:multi" } });
    tree = harness.render(); harness.effects(); tree = harness.render();
    assert.ok(elements(tree).some(node => node.props?.["aria-label"] === "清除校区A,B、中关村"));
    assert.ok(elements(tree).some(node => node.props?.["aria-label"] === "清除季度暑假、秋季"));
    assert.deepEqual(new URL(browser.writes.at(-1)).searchParams.getAll("bf_campus"), ["A,B", "中关村"]);
    findButton(tree, "明细").props.onClick(); tree = harness.render();
    assert.deepEqual(detailDataset(tree).rows.map(row => row.id), ["A,B-暑假", "A,B-秋季", "中关村-暑假", "中关村-秋季"]);
    findButton(tree, "透视").props.onClick(); tree = harness.render();
    elements(tree).find(node => node.props?.["aria-label"] === "筛选季度").props.onChange(["寒假", "春季"]); tree = harness.render();
    assert.equal(findButton(tree, "保存修改").props.disabled, false);
    findButton(tree, "保存修改").props.onClick(); tree = harness.render();
    assert.deepEqual(browser.readViews()[0].state.filters, { ...filters, season: ["寒假", "春季"] });
    const multiEntry = browser.historySnapshot();
    elements(tree).find(node => node.props?.["aria-label"] === "清除校区A,B、中关村").props.onClick(); tree = harness.render();
    assert.equal(new URL(browser.writes.at(-1)).searchParams.has("bf_campus"), false);
    assert.deepEqual(new URL(browser.writes.at(-1)).searchParams.getAll("bf_season"), ["寒假", "春季"]);
    browser.setLocation(multiEntry.url); browser.dispatch("popstate", multiEntry.state); tree = harness.render(); harness.effects(); tree = harness.render();
    assert.deepEqual(elements(tree).find(node => node.props?.["aria-label"] === "筛选校区").props.value, ["A,B", "中关村"]);
    assert.deepEqual(elements(tree).find(node => node.props?.["aria-label"] === "筛选季度").props.value, ["寒假", "春季"]);
    findButton(tree, "清除筛选").props.onClick(); tree = harness.render();
    assert.equal([...new URL(browser.writes.at(-1)).searchParams.keys()].some(name => name.startsWith("bf_")), false);
    assert.equal(requests, 0); assert.equal(JSON.stringify(facts), before);
    harness.dispose();
  } finally { globalThis.fetch = oldFetch; browser.restore(); }
});

test("multi-select detail copy and XLSX keep every filtered row beyond the visible page without budget requests", async () => {
  const facts = ["A,B", "中关村", "排除校区"].flatMap(campus => fourQuarters.flatMap(row =>
    Array.from({ length: 8 }, (_, index) => ({ ...row, id: `${campus}-${row.season}-${index}`, campus,
      subject: `科目${index}`, revenue: index === 0 ? -12.345678 : index === 1 ? 0 : 10000 + index }))));
  const filters = { ...emptyFilters, campus: ["A,B", "中关村"], season: ["暑假", "秋季"] };
  const before = JSON.stringify(facts), browser = hookBrowser(), originalFetch = globalThis.fetch, originalURL = URL.createObjectURL;
  let requests = 0; globalThis.fetch = () => { requests++; throw new Error("unexpected budget request"); };
  try {
    const harness = analysisHarness({ facts, controlledFilters: filters });
    let tree = harness.render(); findButton(tree, "明细").props.onClick(); tree = harness.render();
    const table = elements(tree).find(node => node.props?.className === "ba-table ba-detail-table");
    assert.equal(table.props.exportData.rows.length, 32);
    assert.equal(elements(table).filter(node => node.type === "tbody")[0].props.children.length, 25);
    const child = harness.child(table); elements(child).find(node => node.type === "table").props.ref({ rows: [], tHead: { rows: [] } });
    await findButton(child, "一键复制").props.onClick();
    const copied = browser.clipboard.at(-1).split("\n"); assert.equal(copied.length, 33, "header plus all 32 filtered rows, not only the visible 25");
    assert.match(copied.join("\n"), /A,B/); assert.match(copied.join("\n"), /中关村/); assert.doesNotMatch(copied.join("\n"), /排除校区|寒假|春季/);
    const file = new Promise((resolve, reject) => { const timer = setTimeout(() => reject(new Error("export did not complete")), 5000);
      URL.createObjectURL = blob => { clearTimeout(timer); resolve(blob); return originalURL(blob); }; });
    findButton(child, "导出明细").props.onClick();
    const blob = await file, ExcelJS = require("exceljs"), workbook = new ExcelJS.Workbook(); await workbook.xlsx.load(await blob.arrayBuffer());
    const sheet = workbook.worksheets[0]; assert.equal(sheet.rowCount, 33);
    const values = sheet.getSheetValues().flatMap(row => Array.isArray(row) ? row : []);
    assert.ok(values.includes(-12.345678)); assert.ok(values.includes(0));
    assert.ok(values.includes("A,B")); assert.ok(values.includes("中关村")); assert.ok(!values.includes("排除校区"));
    assert.equal(requests, 0); assert.equal(JSON.stringify(facts), before); harness.dispose();
  } finally { globalThis.fetch = originalFetch; URL.createObjectURL = originalURL; browser.restore(); }
});
