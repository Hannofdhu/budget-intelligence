import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

const require = createRequire(import.meta.url);
const bundle = await build({ entryPoints: [fileURLToPath(new URL("../src/AiChatMessage.tsx", import.meta.url))],
  bundle: true, write: false, platform: "node", format: "cjs", jsx: "automatic",
  external: ["react", "react-dom"], loader: { ".css": "empty" }, logLevel: "silent" });
const module = { exports: {} };
new Function("require", "module", "exports", bundle.outputFiles[0].text)(require, module, module.exports);
const { AiChatMessage } = module.exports;
const render = (values = {}) => renderToStaticMarkup(createElement(AiChatMessage, { message: {
  id: "a".repeat(32), role: "assistant", state: "running", text: "", created_at: "", ...values,
} }));

test("waiting illustration follows real queue, analysis and stop states while keeping status text", () => {
  for (const [state, pose, text] of [["queued", "focus", "有空位后会自动开始"], ["preparing", "think", "正在处理这条问题"],
    ["running", "think", "正在处理这条问题"], ["stopping", "notice", "正在停止回答"]]) {
    const html = render({ state });
    assert.match(html, new RegExp(`/mascot/xiaoyu-${pose}\\.svg`));
    assert.ok(html.includes(text));
    assert.match(html, /alt="" aria-hidden="true"/);
    assert.doesNotMatch(html, /xiaoyu-done/);
  }
});

test("mascot withdraws when content arrives and never marks unverified, failed or cancelled answers successful", () => {
  for (const state of ["complete", "error", "interrupted", "cancelled"]) {
    assert.doesNotMatch(render({ state }), /<img/);
  }
  assert.doesNotMatch(render({ text: "已收到部分分析", state: "running" }), /<img/);
  const warning = render({ state: "complete", text: "仍需核对", validation: {
    state: "needs_review", warnings: ["缺少来源"], notice: "有限检查", resources: [], tables_examined: 0,
  } });
  assert.match(warning, /待核实/);
  assert.doesNotMatch(warning, /<img/);
  assert.doesNotMatch(render({ role: "user", state: "complete", text: "我的问题" }), /<img/);
  const subdued = renderToStaticMarkup(createElement(AiChatMessage, { showCompanion: false,
    message: { id: "a".repeat(32), role: "assistant", state: "running", text: "", created_at: "" } }));
  assert.doesNotMatch(subdued, /<img/);
  assert.match(subdued, /正在处理这条问题/);
});

test("approved mascot SVGs are small self-contained local resources", () => {
  for (const pose of ["hello", "think", "focus", "done", "notice", "rest"]) {
    const svg = fs.readFileSync(new URL(`../public/mascot/xiaoyu-${pose}.svg`, import.meta.url), "utf8");
    assert.match(svg, /viewBox="0 0 360 340"/);
    assert.ok(Buffer.byteLength(svg) < 4000);
    assert.doesNotMatch(svg, /<script|<foreignObject|<image|(?:href|url\()\s*["']?https?:/i);
  }
});
