import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const root = fileURLToPath(new URL("../", import.meta.url));
const source = name => fs.readFileSync(path.join(root, "src", name), "utf8");
const tokens = source("budgetTokens.css");
const token = name => tokens.match(new RegExp(`--budget-${name}:\\s*([^;]+);`))?.[1].trim();
const luminance = color => {
  const rgb = color.slice(1).match(/../g).map(value => parseInt(value, 16) / 255)
    .map(value => value <= .04045 ? value / 12.92 : ((value + .055) / 1.055) ** 2.4);
  return rgb.reduce((sum, value, index) => sum + value * [.2126, .7152, .0722][index], 0);
};
const contrast = (first, second) => {
  const [light, dark] = [luminance(first), luminance(second)].sort((a, b) => b - a);
  return (light + .05) / (dark + .05);
};

test("v1.2 uses the official brand bases and explicit functional colors", () => {
  assert.equal(token("text"), "#141413");
  assert.equal(token("page-background"), "#faf9f5");
  assert.equal(token("accent"), "#d97757");
  assert.equal(token("brand-muted"), "#b0aea5");
  assert.equal(token("divider"), "#e8e6dc");
  assert.equal(token("brand-blue"), "#6a9bcc");
  assert.equal(token("brand-green"), "#788c5d");
  assert.equal(token("action-text"), token("text"));
  assert.equal(token("selected-surface"), token("text"));
  assert.equal(token("selected-text"), token("page-background"));
  for (const background of ["#ffffff", token("page-background"), token("table-header-surface")]) {
    assert.ok(contrast(token("text"), background) >= 4.5);
    assert.ok(contrast(token("text-secondary"), background) >= 4.5);
    assert.ok(contrast(token("control-border"), background) >= 3);
    for (const series of ["chart-forecast", "chart-actual", "chart-budget"]) {
      assert.ok(contrast(token(series), background) >= 3, `${series} on ${background}`);
    }
  }
  assert.ok(contrast(token("action-text"), token("accent")) >= 4.5);
  assert.ok(contrast("#ffffff", token("accent")) < 4.5, "white must not become orange-button small text");
});

test("brand fonts are real self-hosted WOFF2 assets with licenses and Chinese fallbacks", () => {
  const fonts = source("budgetFonts.css");
  const resources = [...fonts.matchAll(/url\("\.\/assets\/fonts\/([^"/]+)"\)/g)].map(match => match[1]);
  assert.equal(resources.length, 5);
  let bytes = 0;
  for (const resource of resources) {
    const content = fs.readFileSync(path.join(root, "src/assets/fonts", resource));
    assert.equal(content.subarray(0, 4).toString(), "wOF2");
    bytes += content.length;
  }
  assert.ok(bytes < 100_000, "avoid shipping unnecessary font weights or CJK font binaries");
  for (const family of ["Poppins", "Lora"]) {
    assert.match(fs.readFileSync(path.join(root, "src/assets/fonts", `${family}-LICENSE.txt`), "utf8"), /SIL OPEN FONT LICENSE/);
  }
  assert.match(token("font-family"), /"Poppins", Arial, "PingFang SC", "Microsoft YaHei", sans-serif/);
  assert.match(token("font-prose"), /"Lora", Georgia, "Songti SC", "SimSun", serif/);
  assert.equal((source("styles.css").match(/@import "\.\/budgetFonts\.css"/g) || []).length, 1);
  assert.doesNotMatch(fonts, /https?:\/\//);
  assert.doesNotMatch(source("styles.css"), /@import.*https?:/);
  assert.equal((fonts.match(/font-display: swap/g) || []).length, 5);
});

test("all shipped CSS shares the palette without old brand-green or old display fonts", () => {
  for (const name of fs.readdirSync(path.join(root, "src")).filter(name => name.endsWith(".css"))) {
    assert.doesNotMatch(source(name), /#(?:173f37|192521|286355)\b|Avenir Next|DIN Alternate/i, name);
  }
  assert.doesNotMatch(source("AiAgentPage.tsx"), /#173f37/);
  assert.doesNotMatch(source("BudgetPeopleBoard.tsx"), /rgba\(54, 106, 85/);
  assert.match(source("BudgetPeopleBoard.tsx"), /0\.04 \+ value \* 0\.16/);
});

test("primary, selection, focus, chart and narrative roles remain separate", () => {
  const controls = source("nextYear.css") + source("forecast.css") + source("aiAgent.css");
  assert.match(controls, /background: var\(--budget-accent\); color: var\(--budget-action-text\)/);
  assert.doesNotMatch(controls, /background: var\(--budget-accent\); color: var\(--budget-selected-text\)/);
  assert.match(source("fy27Navigation.css"), /\[aria-current="page"\].*background: var\(--budget-selected-surface\)/);
  assert.match(source("budgetDesign.css"), /outline: 2px solid var\(--budget-focus\)/);
  assert.match(source("budgetDesign.css"), /font-family: var\(--budget-font-prose\)/);
  assert.match(source("forecastRevenueTrend.css"), /--trend-forecast: var\(--budget-chart-forecast\); --trend-actual: var\(--budget-chart-actual\)/);
  assert.match(source("forecastRevenueTrend.css"), /stroke-dasharray: 6 5/);
  assert.match(source("AiAgentPage.tsx"), /green: "#427b64"/);
  assert.match(source("aiAgent.css"), /monospace/);
  assert.match(source("budgetDesign.css"), /prefers-reduced-motion/);
});

test("review fixes preserve visible focus and meaningful chart boundaries", () => {
  const css = source("styles.css");
  for (const selector of ["ba-people-old", "ba-people-new", "ba-composition-old", "ba-composition-new"]) {
    const rule = css.slice(css.indexOf(`.${selector} {`)).split("}")[0];
    assert.match(rule, /box-shadow: inset 0 0 0 1px var\(--budget-text\)/, selector);
    assert.doesNotMatch(rule, /min-(?:width|height)|border:/, "outlines must not enlarge zero values or distort ratios");
  }
  for (const fill of [token("accent"), token("brand-blue"), "#ffffff", token("page-background")]) {
    assert.ok(contrast(token("text"), fill) >= 3, `chart outline on ${fill}`);
  }
  assert.match(source("fy27Navigation.css"), /\[aria-current="page"\]:focus-visible \{ outline-color: var\(--budget-selected-text\)/);
  assert.doesNotMatch(source("budgetDesign.css"), /:is\([^)]*\.forecast-how-details/);
  assert.match(source("ForecastRevenueTrend.tsx"), /深陶土色虚线.*深蓝实线/);
  assert.doesNotMatch(source("ForecastRevenueTrend.tsx"), /深绿虚线/);
  const topbar = css.slice(css.indexOf(".topbar {")).split("}")[0];
  assert.match(topbar, /color: var\(--budget-text\)/);
  assert.match(topbar, /background: var\(--budget-page-background\)/);
  const toolbar = css.slice(css.indexOf(".detail-drawer__toolbar > button {")).split("}")[0];
  assert.match(toolbar, /background: var\(--budget-control-surface\)/);
  const working = css.slice(css.indexOf(".working-bar {")).split("}")[0];
  assert.match(working, /color: var\(--budget-text\); background: var\(--budget-surface\)/);
  const quarter = css.slice(css.indexOf(".ba-root .ba-quarter-bar {")).split("}")[0];
  assert.match(quarter, /border: 0;/);
  assert.match(quarter, /box-shadow: inset 0 0 0 1px var\(--budget-text\)/);
  assert.match(source("forecast.css"), /\.forecast-legend i \{[^}]*box-shadow: inset 0 0 0 1px var\(--budget-text\)/);
  const candidate = css.slice(css.indexOf(".candidate-card.is-selected .candidate-card__head em {")).split("}")[0];
  assert.match(candidate, /background: var\(--budget-selection-surface\)/);
  const unlock = css.slice(css.indexOf(".unlock-desk__actions p {")).split("}")[0];
  assert.match(unlock, /color: var\(--budget-text-secondary\)/);
  assert.match(unlock, /background: var\(--budget-surface\)/);
  const docket = css.slice(css.lastIndexOf("\n.docket__header {")).split("}")[0];
  assert.match(docket, /color: var\(--budget-text\)/);
  assert.match(docket, /background: var\(--budget-table-header-surface\)/);
});
