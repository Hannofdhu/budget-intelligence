// Shared SKU presentation only; no API requests, source data writes or inferred business columns.
import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";
import { renderToStaticMarkup } from "react-dom/server";

const require = createRequire(import.meta.url);
const compiled = await build({ entryPoints: [fileURLToPath(new URL("../src/SkuIdentity.tsx", import.meta.url))], bundle: true,
  write: false, platform: "node", format: "cjs", jsx: "automatic", external: ["react"], loader: { ".css": "empty" }, logLevel: "silent" });
const loaded = { exports: {} };
new Function("require", "module", "exports", compiled.outputFiles[0].text)(require, loaded, loaded.exports);
const { skuIdentityHeaders, skuIdentityCells } = loaded.exports;
const find = (node, predicate) => Array.isArray(node) ? node.flatMap(child => find(child, predicate)) : !node || typeof node !== "object" ? []
  : [...(predicate(node) ? [node] : []), ...find(node.props?.children, predicate)];
const text = node => Array.isArray(node) ? node.map(text).join("") : node && typeof node === "object" ? text(node.props?.children)
  : node == null || typeof node === "boolean" ? "" : String(node);
const identity = Object.freeze({ campus: "百学汇学习机教学区与中关村联合长名称校区", track: "智慧伴学跨学段课程赛道",
  product: "云帆白金高端小班联合产品", grade: "七年级与八年级衔接年级", subject: "数学与综合实践长名称科目" });

test("shared SKU identity has two ordered native cells with complete primary and secondary five-dimension labels", () => {
  assert.deepEqual(skuIdentityHeaders, ["校区 / 赛道 / 产品", "年级 / 科目"]);
  const cells = skuIdentityCells(identity);
  assert.ok(Array.isArray(cells));
  assert.equal(cells.length, 2);
  assert.deepEqual(cells.map(cell => cell.type), ["th", "td"]);
  assert.ok(cells.every(cell => cell.key != null));
  assert.equal(new Set(cells.map(cell => cell.key)).size, 2);
  assert.equal(cells[0].props.scope, "row");
  assert.equal(text(find(cells[0], node => node.type === "strong")[0]), identity.campus);
  assert.deepEqual(find(cells[0], node => node.type === "small").map(text), [`${identity.track} / ${identity.product}`]);
  assert.equal(text(cells[1]), `${identity.grade}${identity.subject}`);
  assert.deepEqual(find(cells[1], node => node.type === "small").map(text), [identity.subject]);
  for (const value of Object.values(identity)) assert.ok(text(cells).includes(value), value);
  assert.equal(find(cells, node => ["input", "button", "select", "table"].includes(node.type)).length, 0);
});

test("same FY27 and FY28 dimensions have identical markup independent of revenue, entry and edit permission metadata", () => {
  const historical = Object.freeze({ ...identity, fiscal_year: 2027, annual: -12.123456, entry: "是", enabled: false });
  const next = Object.freeze({ ...identity, fiscal_year: 2028, revenue_yuan: "998877.123456", entry: false, enabled: true });
  const before = JSON.stringify([historical, next]);
  const left = renderToStaticMarkup(skuIdentityCells(historical)), right = renderToStaticMarkup(skuIdentityCells(next));
  assert.equal(left, right, "one identity presentation must not acquire read/edit or fiscal-year-specific hierarchy");
  assert.doesNotMatch(left, /-12\.123456|998877|全年结转收入|参与预算|<input/,
    "identity cells must never insert or adjust business columns");
  assert.equal(JSON.stringify([historical, next]), before);
});

test("new-business annotation does not replace dimensions or add a column", () => {
  const cells = skuIdentityCells(identity, "新业务");
  assert.equal(cells.length, 2);
  assert.deepEqual(cells.map(cell => cell.type), ["th", "td"]);
  assert.equal(text(find(cells[0], node => node.type === "strong")[0]), identity.campus);
  assert.ok(text(cells).includes(`${identity.track} / ${identity.product}`));
  assert.ok(text(cells).includes("新业务"));
  assert.equal(text(cells[1]), `${identity.grade}${identity.subject}`);
  assert.equal(text(skuIdentityCells(identity)).includes("新业务"), false, "annotations are explicit, not inferred from business metadata");
});
