// SSR and callback acceptance for the people board. Real browser layout is
// checked separately; these tests do not claim to measure computed alignment.
import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";
import { createElement, Fragment } from "react";
import { renderToStaticMarkup } from "react-dom/server";

const require = createRequire(import.meta.url);
const compiled = await build({
  entryPoints: [fileURLToPath(new URL("../src/BudgetPeopleBoard.tsx", import.meta.url))],
  bundle: true, write: false, platform: "node", format: "cjs", jsx: "automatic",
  external: ["react", "react-dom"], logLevel: "silent",
});
function load(moduleRequire = require) {
  const module = { exports: {} };
  new Function("require", "module", "exports", compiled.outputFiles[0].text)(moduleRequire, module, module.exports);
  return module.exports.BudgetPeopleBoard;
}
const BudgetPeopleBoard = load();
const fact = (overrides = {}) => ({
  id: "a", season: "暑假", track: "智慧空间", campus: "西直门", product: "常规班", grade: "七年级", subject: "数学",
  revenue: 10000, business: 9000, listRevenue: 10000, old: 50, new: 10, total: 60,
  renewalNumerator: 50, renewalDenominator: 100, ...overrides,
});
const props = facts => ({ facts, onSelect() {}, onFilterCampus() {}, onPivot() {} });
const render = facts => renderToStaticMarkup(createElement(BudgetPeopleBoard, props(facts)));
const text = html => html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
const renewalTable = html => html.match(/<table\b[^>]*class="ba-table ba-renewal-table"[^>]*>([\s\S]*?)<\/table>/)?.[1] ?? "";
const findElements = (node, predicate) => {
  if (Array.isArray(node)) return node.flatMap(child => findElements(child, predicate));
  if (!node || typeof node !== "object") return [];
  return [...(predicate(node) ? [node] : []), ...findElements(node.props?.children, predicate)];
};

test("renewal visuals use transition labels while enrollment quarters keep their names", () => {
  const html = render(["暑假", "秋季", "寒假", "春季"].map((season, index) => fact({ id: String(index), season })));
  for (const stage of ["春续暑", "暑续秋", "秋续寒", "寒续春"]) {
    assert.match(text(html), new RegExp(stage));
    assert.match(renewalTable(html), new RegExp(`<th scope="col">${stage}</th>`));
    assert.match(html, new RegExp(`aria-label="查看${stage}预算续班率50%`));
  }
  assert.match(text(html), /续班阶段/);
  assert.match(html, /aria-label="各季度老生与新生人次"/);
  for (const season of ["暑假", "秋季", "寒假", "春季"]) assert.match(html, new RegExp(`<th scope="row">${season}</th>`));
  assert.doesNotMatch(html, /aria-label="查看(?:暑假|秋季|寒假|春季)预算续班率/);
});

test("track and grade renewal rows aggregate numerator and denominator across campuses without averaging", () => {
  const html = render([
    fact(), fact({ id: "b", campus: "中关村南", renewalNumerator: 8, renewalDenominator: 10 }),
    fact({ id: "c", grade: "八年级", renewalNumerator: 9, renewalDenominator: 10 }),
    fact({ id: "d", track: "智慧伴学", renewalNumerator: 6, renewalDenominator: 20 }),
  ]);
  const table = renewalTable(html);
  assert.match(text(html), /赛道与年级续班率/);
  assert.match(table, /<th scope="col">赛道<\/th><th scope="col">年级<\/th>/);
  assert.match(table, /aria-label="智慧空间，七年级，春续暑，预算续班率，52\.73%"/);
  assert.match(table, /aria-label="智慧空间，八年级，春续暑，预算续班率，90%"/);
  assert.match(table, /aria-label="智慧伴学，七年级，春续暑，预算续班率，30%"/);
  assert.equal((table.match(/<tbody>[\s\S]*?<\/tbody>/)?.[0].match(/<tr\b/g) ?? []).length, 3, "only complete track-grade leaf groups belong in the table");
  assert.match(table, /<tfoot>[\s\S]*?52\.14%/);
  assert.doesNotMatch(table, /65%|西直门|中关村南/);
});

test("a renewal cell opens the exact track-grade-season details and keeps canonical season keys", () => {
  // Exercise the pure rendered callbacks with useMemo evaluated synchronously;
  // this does not emulate effects or browser events.
  const Board = load(name => name === "react" ? { Fragment, useMemo: compute => compute() } : require(name));
  const calls = [];
  const tree = Board({ ...props([fact()]), onSelect: (path, label) => calls.push({ path, label }) });
  const [button] = findElements(tree, node => node.type === "button" && node.props["aria-label"] === "智慧空间，七年级，春续暑，预算续班率，50%");
  assert.ok(button);
  button.props.onClick();
  assert.deepEqual(calls, [{ path: { track: "智慧空间", grade: "七年级", season: "暑假" }, label: "智慧空间 / 七年级 / 春续暑 · 预算续班率" }]);
});

test("unknown renewal stays unpainted and zero is displayed and plotted as real zero", () => {
  const html = render([
    fact({ renewalNumerator: 0, renewalDenominator: 100 }),
    fact({ id: "missing", grade: "八年级", renewalNumerator: null, renewalDenominator: null }),
  ]);
  const table = renewalTable(html);
  assert.match(table, /class="ba-renewal-heatcell" style="background-color:rgba\(54, 106, 85, 0\.04\)"[^>]*>[\s\S]*?春续暑，预算续班率，0%/);
  assert.match(table, /<td class="ba-renewal-heatcell"><button[^>]*智慧空间，八年级，春续暑，预算续班率，—/);
  const zeroOnly = render([fact({ renewalNumerator: 0, renewalDenominator: 100 })]);
  assert.match(zeroOnly, /class="ba-renewal-point"[^>]*cy="150"/);
  assert.doesNotMatch(html, /NaN|Infinity|undefined/);
});

test("the merged campus budget gives old/new/total equal column treatment instead of row-spanned metric labels", () => {
  const html = render([fact()]);
  const campus = html.match(/<table\b[^>]*class="ba-table ba-campus-budget-table"[^>]*>([\s\S]*?)<\/table>/)?.[1] ?? "";
  assert.ok(campus);
  assert.match(campus, /<th scope="col">总人次<\/th><th scope="col">新生人次<\/th><th scope="col">老生人次<\/th>/);
  assert.equal((campus.match(/<tbody>[\s\S]*?<\/tbody>/)?.[0].match(/<tr\b/g) ?? []).length, 1);
  assert.equal((campus.match(/<tfoot>[\s\S]*?<\/tfoot>/)?.[0].match(/<tr\b/g) ?? []).length, 1);
  assert.doesNotMatch(campus, /scope="rowgroup"|row[Ss]pan="3"|ba-people-metric/);
  assert.doesNotMatch(html, /校区招生人次|校区季度老生新生人次表/);
});
