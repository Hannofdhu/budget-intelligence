import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

const require = createRequire(import.meta.url);
const compiled = await build({ entryPoints: [fileURLToPath(new URL("../src/BudgetVersionIdentity.tsx", import.meta.url))], bundle: true, write: false, platform: "node", format: "cjs", jsx: "automatic", external: ["react", "react-dom"], loader: { ".css": "empty" }, logLevel: "silent" });
const module = { exports: {} };
new Function("require", "module", "exports", compiled.outputFiles[0].text)(require, module, module.exports);
const { BudgetVersionIdentity, BudgetVersionTrace } = module.exports;
const render = (component, props) => renderToStaticMarkup(createElement(component, props));
const text = html => html.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim();

test("record identity prioritizes actual saved time and reason, with independent latest and viewing relations", () => {
  const props = { sequence: 20, status: "DRAFT", createdAt: "2026-10-16T04:07:54Z", reason: "修改预算参数", latest: true, viewing: false };
  const before = JSON.stringify(props), html = render(BudgetVersionIdentity, props), readable = text(html);
  assert.match(readable, /^保存于 2026-10-16 12:07 修改预算参数 第20版 草稿 最新保存$/);
  assert.match(html, /datetime="2026-10-16T04:07:54Z"/i); assert.doesNotMatch(readable, /正在查看|已确认|P20/);
  const history = text(render(BudgetVersionIdentity, { ...props, sequence: 17, status: "CONFIRMED", latest: false, viewing: true, readonly: true }));
  assert.match(history, /第17版 已确认 正在查看 只读$/); assert.doesNotMatch(history, /最新保存/);
  assert.equal(JSON.stringify(props), before);
});

test("unknown metadata never invents approval or a save day", () => {
  const html = render(BudgetVersionIdentity, { sequence: 3, status: "DRAFT", createdAt: "2026-10-15", readonly: true });
  assert.match(text(html), /保存于 未记录 更新说明未记录 第3版 草稿 只读/);
  assert.doesNotMatch(html, /datetime=|已确认|已定版/i);
});

test("raw identity and confirmation time are confined to folded trace and never replace original saved time", () => {
  const html = render(BudgetVersionTrace, { code: "P20", id: "exact-p20", createdAt: "2026-10-16T04:07:54Z", createdBy: "forecast-daily-scheduler", confirmedAt: "2026-10-16T05:00:00Z", confirmedBy: "owner" });
  assert.match(html, /^<details class="budget-version-trace"><summary>追溯信息<\/summary>/);
  assert.doesNotMatch(html, /<details[^>]* open/);
  const readable = text(html); assert.match(readable, /内部编号 P20 精确版本标识 exact-p20/);
  assert.match(readable, /保存时间（北京时间） 2026-10-16 12:07:54/);
  assert.match(readable, /确认时间（北京时间） 2026-10-16 13:00:00/);
});
