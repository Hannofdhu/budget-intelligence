import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

const require = createRequire(import.meta.url);
const compiled = await build({ entryPoints: [fileURLToPath(new URL("../src/BudgetScopeBar.tsx", import.meta.url))], bundle: true,
  write: false, platform: "node", format: "cjs", jsx: "automatic", external: ["react"], loader: { ".css": "empty" }, logLevel: "silent" });
const loaded = { exports: {} };
new Function("require", "module", "exports", compiled.outputFiles[0].text)(require, loaded, loaded.exports);
const { BudgetScopeBar } = loaded.exports;
const dimensions = [{ key: "campus", label: "校区", options: ["西直门", "中关村"] }, { key: "season", label: "季度", options: ["暑假", "秋季"] }];
const find = (node, predicate) => Array.isArray(node) ? node.flatMap(child => find(child, predicate)) : !node || typeof node !== "object" ? []
  : [...(predicate(node) ? [node] : []), ...find(node.props?.children, predicate)];
const label = node => Array.isArray(node) ? node.map(label).join("") : node && typeof node === "object" ? label(node.props?.children)
  : node == null || typeof node === "boolean" ? "" : String(node);

test("shared scope distinguishes all budget, active scope and zero results without changing input", () => {
  const filters = { campus: "", season: "" }, before = JSON.stringify(filters);
  const all = renderToStaticMarkup(createElement(BudgetScopeBar, { filters, dimensions, count: 952, onChange() {} }));
  assert.match(all, /当前范围/); assert.match(all, /全部预算/); assert.match(all, /952 条明细/);
  assert.doesNotMatch(all, /清除筛选|budget-scope-chips/);
  const empty = renderToStaticMarkup(createElement(BudgetScopeBar, { filters: { campus: "不存在校区", season: "秋季" }, dimensions, count: 0, onChange() {} }));
  assert.match(empty, /0 条明细/); assert.doesNotMatch(empty, />全部预算</);
  assert.match(empty, /role="combobox"[^>]*aria-label="筛选校区"/); assert.match(empty, /title="不存在校区"/); assert.match(empty, /清除校区不存在校区/);
  assert.equal(JSON.stringify(filters), before);
});

test("select and chip changes preserve other and hidden dimensions and clear uses the explicit owner", () => {
  const filters = { campus: "西直门", season: "秋季", hidden: "沿用" }, before = JSON.stringify(filters), changes = [];
  const tree = BudgetScopeBar({ filters, dimensions, onChange: next => changes.push(next) });
  find(tree, node => node.type?.name === "FilterSelect" && node.props["aria-label"] === "筛选校区")[0].props.onChange("中关村");
  assert.deepEqual(changes.at(-1), { campus: "中关村", season: "秋季", hidden: "沿用" });
  find(tree, node => node.type === "button" && node.props["aria-label"] === "清除季度秋季")[0].props.onClick();
  assert.deepEqual(changes.at(-1), { campus: "西直门", season: "", hidden: "沿用" });
  find(tree, node => node.type === "button" && label(node) === "清除筛选")[0].props.onClick();
  assert.deepEqual(changes.at(-1), { campus: "", season: "", hidden: "沿用" });
  let clears = 0;
  const owner = BudgetScopeBar({ filters, dimensions, onChange() { assert.fail("owner clear must not emit another change"); }, onClear() { clears++; } });
  find(owner, node => node.type === "button" && label(node) === "清除筛选")[0].props.onClick(); assert.equal(clears, 1);
  assert.equal(JSON.stringify(filters), before);
});

test("hidden quarter selection is neither presented nor cleared by an unrelated scope control", () => {
  const changes = [], filters = { campus: "西直门", season: "秋季" };
  const tree = BudgetScopeBar({ filters, dimensions: dimensions.slice(0, 1), onChange: next => changes.push(next), disabled: true, selectLabelPrefix: "全局筛选" });
  assert.equal(find(tree, node => node.type?.name === "FilterSelect" && node.props["aria-label"] === "全局筛选校区").length, 1);
  assert.equal(find(tree, node => node.type?.name === "FilterSelect" && node.props["aria-label"].includes("季度")).length, 0);
  assert.equal(find(tree, node => node.type === "button" && node.props["aria-label"] === "清除季度秋季").length, 0);
  for (const control of find(tree, node => node.type === "button" || node.type?.name === "FilterSelect")) assert.equal(control.props.disabled, true);
  assert.equal(changes.length, 0);
});

test("multi-select scope labels and chips retain exact values and only clear the selected dimension", () => {
  const changes = [], filters = { campus: ["西直门", "中关村"], season: ["暑假", "秋季"], hidden: ["A,B", "C"] };
  const before = JSON.stringify(filters), tree = BudgetScopeBar({ filters, dimensions, onChange: next => changes.push(next) });
  const campus = find(tree, node => node.type?.name === "FilterSelect" && node.props["aria-label"] === "筛选校区")[0];
  assert.deepEqual(campus.props.value, ["西直门", "中关村"]);
  campus.props.onChange(["中关村", "西直门", "A,B"]);
  assert.deepEqual(changes.at(-1), { ...filters, campus: ["中关村", "西直门", "A,B"] });
  const chip = find(tree, node => node.type === "button" && node.props["aria-label"] === "清除校区西直门、中关村")[0];
  assert.ok(chip); chip.props.onClick();
  assert.deepEqual(changes.at(-1), { ...filters, campus: "" });
  find(tree, node => node.type === "button" && label(node) === "清除筛选")[0].props.onClick();
  assert.deepEqual(changes.at(-1), { campus: "", season: "", hidden: ["A,B", "C"] });
  assert.equal(JSON.stringify(filters), before);
});
