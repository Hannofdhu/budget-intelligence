// Static rendering acceptance only. This does not simulate browser clicks,
// effect execution, download completion or a real user's visual experience.
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { normalizeHistoricalFacts } from "../src/budgetAnalysis.ts";

const require = createRequire(import.meta.url);
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

test("default analysis markup presents a business board and all four fiscal seasons in place", () => {
  const html = render(fourQuarters), readable = text(html);
  assert.match(readable, /看板/); assert.match(readable, /透视/);
  for (const season of ["暑假", "秋季", "寒假", "春季"]) assert.match(readable, new RegExp(season));
  assert.match(readable, /确认收入/); assert.match(readable, /综合折扣/); assert.match(readable, /西直门/);
  assert.match(html, /role="tab"[^>]*aria-selected="true"/);
  assert.doesNotMatch(html, /查看依据|来源与口径|参数来源|secret\.xlsx/);
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
  const blocks = ["AL AM AN AR AS AX", "BC BD BE BI BJ BO", "BT BU BV BZ CA CF", "CK CL CM CQ CR CW"];
  const fields = ["old", "new", "total", "list_revenue", "business", "revenue"];
  const facts = normalizeHistoricalFacts(source.sheets["滚动表"].rows.filter(row => row.row >= 2 && row.cells.A?.value != null)
    .flatMap(row => blocks.map((block, index) => ({ track: row.cells.A.value, product: row.cells.B.value, grade: row.cells.D.value,
      subject: row.cells.F.value, campus: row.cells.G.value, annual: row.cells.W.value, season: ["暑假", "秋季", "寒假", "春季"][index],
      ...Object.fromEntries(block.split(" ").map((column, i) => [fields[i], row.cells[column]?.value ?? null])) }))));
  assert.equal(facts.length, 952);
  const html = render(facts, "baUnit=yuan");
  assert.match(text(html), /113,004,618\.59/);
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
});

test("static render restores all mocked browser globals", () => {
  const before = Object.getOwnPropertyDescriptor(globalThis, "location");
  render(fourQuarters);
  assert.deepEqual(Object.getOwnPropertyDescriptor(globalThis, "location"), before);
});
