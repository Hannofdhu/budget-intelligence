// Material contracts supplement, not replace, actual browser acceptance.
import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";

const source = name => fs.readFileSync(new URL(`../src/${name}`, import.meta.url), "utf8");
const css = source("budgetGlass.css").replace(/\/\*[\s\S]*?\*\//g, "");

test("glass is shared by existing navigation and submission bars, never the content or a new workflow", () => {
  assert.match(source("main.tsx"), /import "\.\/budgetGlass\.css"/);
  for (const selector of [".decision-topbar", ".fy27-module-switch", ".ny-action-bar", ".forecast-confirm"]) assert.ok(css.includes(selector));
  assert.doesNotMatch(css, /\b(?:table|tbody|td|input|textarea)\b\s*[{,:]/);
  // Preserve fixed-position descendants, native controls, viewport measurements and clicks.
  assert.doesNotMatch(css, /(?:^|[;{\n])\s*(?:filter|transform|overflow|height|min-height|padding|font(?:-family|-size)?|opacity|animation|transition)\s*:/);
  assert.match(css, /pointer-events:\s*none/);
  assert.match(css, /a\[aria-current="page"\]/);
  assert.match(css, /var\(--budget-control-border\)/);
});

test("translucency is progressive and has opaque, accessibility, print and short-window fallbacks", () => {
  assert.match(css, /@supports[\s\S]*-webkit-backdrop-filter/);
  assert.match(css, /background: var\(--budget-page-background\);[\s\S]*color-mix/);
  for (const query of ["max-height: 600px", "prefers-reduced-transparency: reduce", "prefers-contrast: more", "forced-colors: active"]) {
    const block = css.split(/\n@media/).find(block => block.includes(`(${query})`)) ?? "";
    assert.ok(block.includes("--budget-glass-decoration: none"), query);
    assert.ok(block.includes("background:"), query);
  }
  assert.match(css, /@media print[\s\S]*--budget-glass-decoration: none/);
  assert.match(css, /display: var\(--budget-glass-decoration\)/);
  // :is() takes its most specific branch. A compound header branch would
  // prevent the equally scoped short-window footer fallback from winning.
  assert.doesNotMatch(css, /:is\(\.decision-topbar\.fy27-topbar,/);
});

test("rollout explicitly covers shared chrome and keeps every content role out of the glass allowlist", () => {
  const surface = css.match(/:root :is\(([^)]+)\)::before/)[1].split(",").map(value => value.trim());
  assert.deepEqual(surface, [".decision-topbar", ".topbar", ".budget-page-navigation", ".decision-progress",
    ".ny-action-bar", ".forecast-confirm", ".budget-utility-heading", ".budget-utility-footer",
    ".ny-context-heading", ".ai-chat-heading", ".ai-chat-composer-wrap", ".ai-chat-latest",
    ".ai-agent-terminal-toolbar", ".working-bar", ".budget-filter-select__menu"]);
  const background = css.match(/:root :is\(([^)]+)\) \{\s*background:/)[1].split(",").map(value => value.trim());
  assert.deepEqual(background, surface, "fallback and decorated surfaces cannot drift apart");
  const anchor = css.match(/:where\(([^)]+)\) \{\s*position: relative/)[1].split(",").map(value => value.trim());
  assert.deepEqual(anchor, surface, "unpositioned chrome gets an anchor without overriding fixed/sticky rules");
  assert.doesNotMatch(surface.join(" "), /table|metrics|messages|transcript|prose|content|scope|filters|chart|input/);
  assert.match(css, /button:not\(\.is-selected\):not\(:hover\) \{ background: var\(--budget-control-surface\)/);
  assert.match(source("aiChat.css"), /\.ai-chat-composer \{[^}]*background: var\(--budget-page-background\)/);
  assert.match(source("aiChat.css"), /\.ai-chat-main \{[^}]*background: var\(--budget-surface\)/);
  assert.match(source("budgetUtility.css"), /\.budget-utility-panel \{[^}]*background: var\(--budget-surface\)/);
});

test("the glass tint retains readable brand text over worst-case dark and bright backdrops", () => {
  const tokens = source("budgetTokens.css");
  const rgb = name => tokens.match(new RegExp(`--budget-${name}:\\s*#([\\da-f]{6})`))[1].match(/../g).map(n => parseInt(n, 16));
  const lum = channels => channels.map(c => c / 255).map(c => c <= .04045 ? c / 12.92 : ((c + .055) / 1.055) ** 2.4).reduce((s, c, i) => s + c * [.2126, .7152, .0722][i], 0);
  const ratio = (a, b) => (Math.max(lum(a), lum(b)) + .05) / (Math.min(lum(a), lum(b)) + .05);
  const alpha = Number(css.match(/--budget-page-background\) (\d+)%/)[1]) / 100;
  for (const backdrop of [0, 255]) {
    const surface = rgb("page-background").map(c => c * alpha + backdrop * (1 - alpha));
    for (const role of ["text", "text-secondary"]) assert.ok(ratio(rgb(role), surface) >= 4.5, `${role} against ${backdrop}`);
    assert.ok(ratio(rgb("control-border"), surface) >= 3);
  }
});
