// Static rendering validates chart semantics, not browser clicks or visual layout.
import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

const require = createRequire(import.meta.url);
const compiled = await build({
  entryPoints: [fileURLToPath(new URL("../src/BudgetCompositionCharts.tsx", import.meta.url))],
  bundle: true, write: false, platform: "node", format: "cjs", jsx: "automatic",
  external: ["react", "react-dom"], logLevel: "silent",
});
const module = { exports: {} };
new Function("require", "module", "exports", compiled.outputFiles[0].text)(require, module, module.exports);
const { BudgetCompositionCharts } = module.exports;
const fact = (overrides = {}) => ({ id: "a", season: "暑假", track: "初中", campus: "甲校区", product: "常规班", grade: "七年级", subject: "数学",
  revenue: 10000, business: 9000, listRevenue: 10000, old: 10, new: 20, total: 30, ...overrides });
const render = facts => renderToStaticMarkup(createElement(BudgetCompositionCharts, { facts, onSelect() {} }));
const text = html => html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();

test("composition charts aggregate each grade and campus with direct counts and a shared zero-based scale", () => {
  const html = render([fact(), fact({ id: "b", campus: "乙校区", old: 30, new: 10, total: 40 }), fact({ id: "c", grade: "八年级", old: 5, new: 5, total: 10 })]);
  assert.match(text(html), /年级招生结构/); assert.match(text(html), /校区招生结构/);
  assert.match(html, /aria-label="七年级，老生40人次，新生30人次，合计70人次"/);
  assert.match(html, /aria-label="甲校区，老生15人次，新生25人次，合计40人次"/);
  assert.match(html, /aria-label="乙校区，老生30人次，新生10人次，合计40人次"/);
  assert.match(html, /data-scale-max="70"/); assert.match(html, /data-scale-max="40"/);
  assert.match(html, /class="ba-composition-old" style="width:75%"/);
  assert.match(html, /class="ba-composition-new" style="width:25%"/);
  assert.match(html, /aria-label="年级招生结构合计，老生45人次，新生35人次，合计80人次"/);
});

test("grades follow school progression, with unknown labels following the known grades", () => {
  const html = render([fact({ id: "9", grade: "九年级" }), fact({ id: "h1", grade: "高一年级" }), fact({ id: "8", grade: "八年级" }), fact({ id: "7", grade: "七年级" }), fact({ id: "x", grade: "未分类" })]);
  const gradeButtons = [...html.matchAll(/aria-label="(七年级|八年级|九年级|高一年级|未分类)，/g)].map(match => match[1]);
  assert.deepEqual(gradeButtons, ["七年级", "八年级", "九年级", "高一年级", "未分类"]);
});

test("partially missing counts are not treated as zero or drawn as a complete stack", () => {
  const html = render([fact({ old: "-", new: 8, total: 10 }), fact({ id: "b", old: 2, new: 2, total: 5 })]);
  assert.match(html, /aria-label="七年级，老生—人次，新生10人次，合计15人次"/);
  assert.match(html, /aria-label="甲校区，老生—人次，新生10人次，合计15人次"/);
  assert.doesNotMatch(html, /class="ba-composition-(?:old|new)"/);
  assert.doesNotMatch(html, /NaN|Infinity/);
  const missingTotal = render([fact({ total: null })]);
  assert.match(missingTotal, /合计—人次/); assert.doesNotMatch(missingTotal, /class="ba-composition-(?:old|new)"/);
});

test("negative counts retain their recorded values without drawing negative or normalized bars", () => {
  const html = render([fact({ old: -2, new: 7, total: 5 })]);
  assert.match(html, /aria-label="七年级，老生-2人次，新生7人次，合计5人次"/);
  assert.doesNotMatch(html, /class="ba-composition-(?:old|new)"/);
  assert.doesNotMatch(html, /width:-|NaN|Infinity/);
});

test("true zero remains zero with a zero extent and no invented plotting scale", () => {
  const html = render([fact({ old: 0, new: 0, total: 0 })]);
  assert.match(html, /老生0人次，新生0人次，合计0人次/);
  assert.equal((html.match(/data-scale-max="0"/g) ?? []).length, 2);
  assert.equal((html.match(/style="width:0%"/g) ?? []).length, 4);
  assert.doesNotMatch(html, /NaN|Infinity|—人次/);
});

test("all campuses remain in the scrollable chart instead of silently truncating to a ranking", () => {
  const facts = Array.from({ length: 19 }, (_, index) => fact({ id: String(index), campus: `校区${index + 1}` }));
  const html = render(facts);
  for (let index = 1; index <= 19; index++) assert.match(html, new RegExp(`aria-label="校区${index}，`));
  assert.match(html, /class="ba-composition-scroll" role="region" aria-label="校区招生结构明细" tabindex="0"/);
});

test("already-filtered facts alone define totals, and rendering never mutates source rows or order", () => {
  const source = Object.freeze([Object.freeze(fact({ id: "b", campus: "乙校区", grade: "八年级", old: 30, new: 10, total: 40 })), Object.freeze(fact())]);
  const before = JSON.stringify(source), filtered = source.filter(row => row.campus === "甲校区");
  const html = render(filtered);
  assert.match(html, /aria-label="校区招生结构合计，老生10人次，新生20人次，合计30人次"/);
  assert.doesNotMatch(html, /乙校区|八年级|全年|年度/);
  assert.equal(JSON.stringify(source), before);
});

test("empty data has an explicit empty state and each populated control has a contextual accessible name", () => {
  const empty = render([]);
  assert.equal((text(empty).match(/暂无招生明细/g) ?? []).length, 2);
  assert.doesNotMatch(empty, /NaN|Infinity|data-scale-max/);
  const html = render([fact()]);
  for (const button of html.matchAll(/<button\b([^>]*)>/g)) assert.match(button[1], /aria-label="[^"]+"/);
  assert.doesNotMatch(html, /查看依据|来源与口径|参数来源|<p/);
});
