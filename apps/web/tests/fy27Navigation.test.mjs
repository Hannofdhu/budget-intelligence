// Shared-navigation contracts. Pixel positions and real browser history are checked separately.
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

const require = createRequire(import.meta.url);
const source = name => fs.readFileSync(new URL(`../src/${name}`, import.meta.url), "utf8");
const compiled = await build({
  entryPoints: [fileURLToPath(new URL("../src/FY27Header.tsx", import.meta.url))],
  bundle: true, write: false, platform: "node", format: "cjs", jsx: "automatic",
  external: ["react", "react-dom"], loader: { ".css": "empty" }, logLevel: "silent",
});
const module = { exports: {} };
new Function("require", "module", "exports", compiled.outputFiles[0].text)(require, module, module.exports);
const { FY27Header } = module.exports;
const urls = {
  budget: "/budget/workspace?archive=fy27&step=5",
  forecast: "/budget/forecast?archive=fy27",
  "next-year": "/budget/next-year",
};
const labels = { budget: "查看预算", forecast: "达成预测", "next-year": "下一年预算" };
const nodes = tree => {
  if (Array.isArray(tree)) return tree.flatMap(nodes);
  if (!tree || typeof tree !== "object") return [];
  return [tree, ...nodes(tree.props?.children)];
};
const links = tree => nodes(tree).filter(node => node.type === "a" && Object.values(labels).includes(node.props.children));

test("FY27 modules render the same single switch with exact routes and one current page", () => {
  for (const active of Object.keys(labels)) {
    const html = renderToStaticMarkup(createElement(FY27Header, {
      active, children: createElement("span", { "data-test": "context" }, active === "budget" ? "已定版" : "试用试算 P13"),
    }));
    assert.match(html, /<header[^>]*class="[^"]*decision-topbar[^"]*fy27-topbar/);
    assert.equal((html.match(/class="fy27-module-switch"/g) ?? []).length, 1);
    assert.match(html, /aria-label="预算模块"/);
    assert.equal((html.match(/aria-current="page"/g) ?? []).length, 1);
    const tree = FY27Header({ active, children: "状态" });
    const actual = links(tree);
    assert.equal(actual.length, 3);
    for (const key of Object.keys(labels)) {
      const link = actual.find(node => node.props.children === labels[key]);
      assert.equal(link.props.href, urls[key]);
      assert.equal(link.props["aria-current"], active === key ? "page" : undefined);
    }
    assert.match(html, /class="fy27-header-context"/);
    assert.match(html, /data-test="context"/);
    assert.match(html, active === "budget" ? /已定版/ : /试用试算 P13/);
  }
});

test("clicking the current module never reloads or invokes the leave guard", () => {
  for (const active of Object.keys(labels)) {
    let prevented = 0, left = 0;
    const tree = FY27Header({ active, children: null, onBeforeLeave() { left++; } });
    const current = links(tree).find(node => node.props["aria-current"] === "page");
    current.props.onClick({ preventDefault() { prevented++; } });
    assert.equal(prevented, 1);
    assert.equal(left, 0);
  }
});

test("the other module delegates leave permission without saving or refreshing data", () => {
  for (const active of Object.keys(labels)) {
    let prevented = 0, received;
    const event = { preventDefault() { prevented++; } };
    const tree = FY27Header({ active, children: null, onBeforeLeave(value) { received = value; value.preventDefault(); } });
    links(tree).find(node => !node.props["aria-current"]).props.onClick(event);
    assert.equal(received, event, "the page can cancel navigation and preserve its draft");
    assert.equal(prevented, 1);
  }
  assert.doesNotMatch(source("FY27Header.tsx"), /\bfetch\s*\(|refreshForecast|reviseForecast|confirmForecast/);
});

test("budget, forecast and next-year budget share the header without separate module navigation", () => {
  for (const name of ["HistoricalBudgetPage.tsx", "ForecastPage.tsx", "NextYearBudgetPage.tsx"]) {
    const page = source(name);
    assert.match(page, /<FY27Header\b/, name);
    assert.doesNotMatch(page, /className="(?:history-product-nav|forecast-navigation)"/, name);
  }
  assert.match(source("HistoricalBudgetPage.tsx"), /className="decision-progress"/,
    "budget detail navigation stays available below the shared module switch");
});

test("the common header remains sticky and centered with one shared narrow-screen layout", () => {
  const shared = source("fy27Navigation.css"), global = source("styles.css");
  assert.match(global, /\.decision-topbar\s*\{[^}]*position:\s*sticky;[^}]*top:\s*0;/);
  assert.match(shared, /grid-template-columns:\s*minmax\(0,\s*1fr\)\s+auto\s+minmax\(0,\s*1fr\)/);
  assert.match(shared, /@media\s*\(max-width:\s*1000px\)/);
  assert.match(shared, /grid-template-rows:\s*35px\s+48px/,
    "different budget selectors and forecast labels cannot change the switch's vertical position");
  assert.match(shared, /\.fy27-module-switch\s*\{[^}]*grid-row:\s*2/);
  assert.match(shared, /\.fy27-module-switch\s*\{[^}]*grid-column:\s*1\s*\/\s*-1/);
  assert.match(shared, /\.fy27-module-switch\s*\{[^}]*justify-self:\s*center/);
  assert.doesNotMatch(source("forecast.css"), /\.forecast-shell\s+\.decision-topbar\s*\{/,
    "the forecast cannot move the common header independently");
});

test("forecast switching protects both uncommitted scope input and unsaved assumptions with an in-product dialog", () => {
  const page = source("ForecastPage.tsx");
  assert.match(page, /settingChanged \|\| trialDraftChanged/);
  assert.match(page, /onDraftChange=\{setTrialDraftChanged\}/);
  assert.match(page, /onBeforeLeave=\{beforeLeave\}/);
  assert.match(page, /setPendingNavigation\(event\.currentTarget\.href\)/);
  assert.match(page, /dialog\?\.showModal\(\)/);
  assert.match(page, /<dialog[^>]*aria-labelledby="forecast-leave-title"/);
  assert.match(page, /beforeunload/);
  assert.match(page, /addEventListener\("pageshow", restore\)/);
  assert.match(page, /leaving\.current = false; setPendingNavigation\(""\)/);
  assert.match(page, /showConfirm && confirmationReason\.trim\(\)/);
  const unsaved = page.match(/const unsaved =([\s\S]*?);/)?.[1] ?? "";
  assert.doesNotMatch(unsaved, /adjustment\.campus/,
    "retained campus selection after saving is not unsaved business input");
  assert.doesNotMatch(page, /window\.confirm\(/);
});
