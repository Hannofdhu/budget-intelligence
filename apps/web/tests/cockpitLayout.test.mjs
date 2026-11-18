import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";

const css = fs.readFileSync(new URL("../src/cockpit.css", import.meta.url), "utf8");
const page = fs.readFileSync(new URL("../src/BudgetCockpitPage.tsx", import.meta.url), "utf8");

test("cockpit toolbar normalizes link and button geometry without changing the global home CTA", () => {
  const controls = css.match(/\.cockpit-shell \.cockpit-actions > :is\(button, a\) \{([^}]+)\}/)?.[1];
  assert.ok(controls);
  assert.match(controls, /min-height: 2\.5rem/);
  assert.match(controls, /min-width: 0/);
  assert.match(controls, /margin: 0/);
  assert.match(controls, /padding: \.5rem \.875rem/);
  assert.match(controls, /align-items: center; justify-content: center/);
  assert.match(controls, /font-size: var\(--budget-body-size\)/);
  assert.match(controls, /white-space: nowrap/);
  assert.doesNotMatch(css, /^\.decision-primary\s*\{/m, "do not alter home or other modules");
  assert.match(page, /className="cockpit-actions" role="group" aria-label="驾驶舱操作"/);
});

test("cockpit actions wrap as one group, then form ordered narrow-screen rows", () => {
  const actions = css.match(/^\.cockpit-actions \{([^}]+)\}/m)?.[1];
  assert.match(actions, /flex: 0 0 auto/);
  assert.match(actions, /flex-wrap: nowrap/);
  const compact = css.match(/@media \(max-width: 600px\) \{([\s\S]*?)\n\}/)?.[1];
  assert.match(compact, /\.cockpit-actions \{[^}]*flex-basis: 100%/);
  assert.match(css, /@media \(max-width: 420px\) \{\s*\.cockpit-actions \{[^}]*grid-template-columns: repeat\(2, minmax\(0, 1fr\)\)/);
  assert.match(css, /\.cockpit-actions > \.decision-primary \{ grid-column: 1 \/ -1/);
});

test("touch sizing covers the AI link as well as the two buttons", () => {
  assert.match(css, /@media \(pointer: coarse\) \{\s*\.cockpit-shell \.cockpit-actions > :is\(button, a\) \{ min-height: 2\.75rem; min-width: 2\.75rem/);
});
