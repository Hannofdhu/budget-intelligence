// First-round layout contracts. All fixtures are in memory; no live API requests.
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";
import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";

const source = fs.readFileSync(new URL("../src/NextYearBudgetPage.tsx", import.meta.url), "utf8");
const require = createRequire(import.meta.url);
const bundle = await build({ entryPoints: [fileURLToPath(new URL("../src/NextYearBudgetPage.tsx", import.meta.url))], bundle: true, write: false,
  platform: "node", format: "cjs", jsx: "automatic", external: ["react", "react-dom", "exceljs"], loader: { ".css": "empty" }, logLevel: "silent" });
const module = { exports: {} };
new Function("require", "module", "exports", bundle.outputFiles[0].text)(require, module, module.exports);
const { NextYearTopMetrics } = module.exports;
const annual = { revenue_yuan: "125000", target_revenue_yuan: null, gap_yuan: null, total_students: 77 };
const render = props => renderToStaticMarkup(React.createElement(NextYearTopMetrics, props));

test("compact editing summary retains incomplete, preview and manually-unset target semantics", () => {
  const props = { calculation: { annual, complete: false }, status: "未保存预览", onSetTarget() {} };
  const compact = render({ ...props, compact: true }), full = render(props);
  assert.match(compact, /ny-summary-compact/); assert.doesNotMatch(full, /ny-summary-compact/);
  // Compacting must not delete warnings, replace an unset target, or change numbers.
  assert.equal(compact.replace(" ny-summary-compact", ""), full);
  assert.match(compact, /未保存预览/); assert.match(compact, /计算未完整/);
  assert.match(compact, /待设置/); assert.match(compact, /设置目标/);
  assert.match(compact, /12\.5/); assert.match(compact, />77</);
  assert.doesNotMatch(compact, /value="125000"/);
});

test("compact summary preserves signed comparisons and zero rather than silently dropping prior-version impact", () => {
  const props = { calculation: { annual: { ...annual, revenue_yuan: "0", target_revenue_yuan: "150000", gap_yuan: "150000" }, complete: true },
    status: "待重算 · 原结果 N12", previous: { sequence: 11, calculation: { annual: { ...annual, revenue_yuan: "125000", target_revenue_yuan: "150000", gap_yuan: "25000" } } } };
  const compact = render({ ...props, compact: true }), full = render(props);
  assert.equal(compact.replace(" ny-summary-compact", ""), full);
  assert.match(compact, /相对第11版 -12\.5 万元/);
  assert.match(compact, /缺口增加 12\.5 万元/);
  assert.match(compact, /<strong>0<\/strong>/);
});

test("only the fixed save bar submits preview and revise; geometry follows measured height with cleanup", () => {
  const footer = source.match(/<footer ref=\{actionBarRef\}[\s\S]*?<\/footer>/)?.[0];
  assert.ok(footer); assert.equal((source.match(/className="ny-action-bar"/g) ?? []).length, 1);
  assert.equal((source.match(/onClick=\{\(\) => void run\("save"\)\}/g) ?? []).length, 1);
  assert.equal((source.match(/onClick=\{\(\) => void run\("preview"\)\}/g) ?? []).length, 1);
  assert.match(footer, /disabled=\{Boolean\(busy\) \|\| !inputChanged\}/);
  assert.match(footer, /\{canEdit &&/); assert.match(footer, /历史版本/);
  assert.match(source, /Math\.ceil\(bar\.getBoundingClientRect\(\)\.height\)/);
  assert.match(source, /observer\?\.observe\(bar\)/); assert.match(source, /observer\?\.disconnect\(\)/);
  assert.match(source, /removeEventListener\("resize", measure\)/);
  assert.match(source, /removeProperty\("--ny-action-bar-height"\)/);
  assert.match(source, /prefers-reduced-motion: reduce/);
  assert.match(source, /matches \? "auto" : "smooth"/);
});

test("fixed action bar resets inherited form spacing and reserves its measured height without a permanent sidebar", () => {
  const css = fs.readFileSync(new URL("../src/nextYear.css", import.meta.url), "utf8");
  assert.match(css, /\.next-year-shell \.ny-action-bar button \{ margin: 0; \}/);
  assert.match(css, /\.next-year-shell \.ny-action-bar \.ny-field input \{ margin: 0; min-height: 36px;/);
  assert.match(css, /padding-bottom: calc\(var\(--ny-action-bar-height\) \+ 32px\)/);
  assert.match(css, /bottom: calc\(var\(--ny-action-bar-height\) \+ 8px\)/);
  assert.doesNotMatch(css, /:has\(\.ny-side-panel\)/);
});
