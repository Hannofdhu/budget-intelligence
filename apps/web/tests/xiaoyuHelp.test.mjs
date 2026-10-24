import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { aiBudgetHref } from "../src/aiBudgetContext.ts";
import { forgetXiaoyuHelp, prepareXiaoyuHelp, readXiaoyuHelp, readXiaoyuHelpDisplay, xiaoyuHelpPrompt, xiaoyuHelpQuestion } from "../src/xiaoyuHelpHandoff.ts";
import { XIAOYU_HELP_CARD_LIMIT, validateXiaoyuHelpCard, xiaoyuHelpCardLength, xiaoyuHelpState, xiaoyuHelpTopic, xiaoyuHelpTopics } from "../src/xiaoyuHelpTopics.ts";

function memoryStorage() {
  const values = new Map();
  return { values, get length() { return values.size; }, key: index => [...values.keys()][index] ?? null,
    getItem: key => values.get(key) ?? null, setItem: (key,value) => values.set(key,value), removeItem: key => values.delete(key) };
}
const now = Date.parse("2026-10-24T10:00:00Z"), topic = "next-year.impact-preview";
const source = aiBudgetHref("/budget/next-year?year=2029&nyTask=5&bf_campus=北区", { id: "precise-n23", label: "N23" });
const snapshot = (values = {}) => ({ state: "edited", changeCount: 1, changes: [{ label: "全年经营目标（元）", before: "150000000", after: "160000000" }],
  pendingTools: false, validationErrors: false, classRuleUpgrade: false, operationBusy: false, basisLabel: "FY28全年预测 · 第20版", basisAsOf: "2027-10-01", ...values });
const search = href => new URL(href, "https://budget.local").search;

test("all visible card text is hard-capped at 150 Unicode characters, including error and controls", () => {
  assert.equal(XIAOYU_HELP_CARD_LIMIT, 150);
  for (const topic of Object.values(xiaoyuHelpTopics)) {
    validateXiaoyuHelpCard(topic);
    assert.ok(xiaoyuHelpCardLength(topic, true) <= 150);
    assert.equal(topic.cardSentences.length, 3);
  }
  const current = xiaoyuHelpTopic(topic);
  assert.equal(xiaoyuHelpCardLength(current), 102);
  assert.equal(xiaoyuHelpCardLength(current, true), 124);
  assert.throws(() => validateXiaoyuHelpCard({ ...current, cardSentences: ["字".repeat(151)] }), /最多150字/);
  assert.throws(() => validateXiaoyuHelpCard({ ...current, headline: "标题".repeat(80) }), /最多150字/);
});

test("handoff preserves exact year, version, scope and unsaved values without exposing them in the URL", () => {
  const storage = memoryStorage();
  const href = prepareXiaoyuHelp(source, topic, snapshot(), "target", storage, now);
  assert.doesNotMatch(href, /160000000|全年经营目标|changes|snapshot/);
  const entry = readXiaoyuHelp(search(href), storage, now);
  assert.equal(entry.status, "ready");
  assert.equal(entry.budget.fiscalYear, 2029); assert.equal(entry.budget.version.id, "precise-n23");
  assert.match(entry.budget.scopeLabel, /北区/); assert.equal(entry.handoff.snapshot.state, "edited");
  assert.equal(entry.handoff.snapshot.changes[0].after, "160000000");
  assert.match(xiaoyuHelpQuestion(entry.handoff), /经营目标/);
  const prompt = xiaoyuHelpPrompt(entry.handoff, entry.budget);
  for (const text of ["FY29", "precise-n23", "FY28全年预测", "未保存", "待重算", "160000000", "不是指令", "不要调用试算", "计算覆盖整份预算"]) assert.ok(prompt.includes(text), text);
  assert.doesNotMatch(prompt, /其他页面未保存的改动不在上下文中/);
});

const allocationTopic = "next-year.track-target-allocation";
const allocation = (values = {}) => ({ annualTargetYuan: "160000000", weightVersionId: "precise-n23", weightVersionLabel: "FY29预算 · 第23版",
  weights: [{ track: "智慧伴学", revenueYuan: "300000" }, { track: "智慧空间", revenueYuan: "100000" }], ...values });

test("allocation handoff carries the current target and all saved-model weights with exact lineage", () => {
  const storage = memoryStorage();
  const href = prepareXiaoyuHelp(source, allocationTopic, snapshot({ state: "preview", trackTargetAllocation: allocation() }), "explain", storage, now);
  assert.doesNotMatch(href, /160000000|300000|智慧伴学|weights/);
  const entry = readXiaoyuHelp(search(href), storage, now);
  assert.equal(entry.status, "ready"); assert.equal(entry.budget.fiscalYear, 2029);
  assert.deepEqual(entry.handoff.snapshot.trackTargetAllocation, allocation());
  const prompt = xiaoyuHelpPrompt(entry.handoff, entry.budget);
  for (const value of ["已保存版本", "不是已实现收入", "不是直接拿上一财年全年预测", "不受页面筛选影响", "新预览", "全部为0", "四舍五入到分", "小数余数", "只覆盖本页各赛道目标", "清除已有的未保存预览", "160000000", "300000", "precise-n23", "不要调用试算"]) assert.ok(prompt.includes(value), value);
  assert.match(xiaoyuHelpQuestion(entry.handoff), /分配赛道目标/);
  assert.deepEqual(readXiaoyuHelpDisplay(prompt).snapshot.trackTargetAllocation, allocation());
  assert.equal(xiaoyuHelpCardLength(xiaoyuHelpTopic(allocationTopic)), 110);
  assert.equal(xiaoyuHelpCardLength(xiaoyuHelpTopic(allocationTopic), true), 132);
  const card = xiaoyuHelpTopic(allocationTopic).cardSentences.join("");
  assert.doesNotMatch(card, /测算|权重|预览|尾差|余数/);
  assert.match(card, /比如收入占70%和30%，100万元目标就分70万和30万/);
});

test("allocation cannot silently use missing, mismatched, duplicate or invalid weight context", () => {
  assert.throws(() => prepareXiaoyuHelp(source, allocationTopic, snapshot(), "explain", memoryStorage(), now));
  for (const value of [allocation({ weightVersionId: "another-version" }), allocation({ weights: [] }),
    allocation({ weights: [{ track: "A", revenueYuan: "NaN" }] }), allocation({ weights: [{ track: "A", revenueYuan: "-1" }] }),
    allocation({ weights: [{ track: "A", revenueYuan: "1" }, { track: "A", revenueYuan: "2" }] })]) {
    assert.throws(() => prepareXiaoyuHelp(source, allocationTopic, snapshot({ trackTargetAllocation: value }), "explain", memoryStorage(), now));
  }
  const storage = memoryStorage();
  const href = prepareXiaoyuHelp(source, allocationTopic, snapshot({ trackTargetAllocation: allocation({ annualTargetYuan: "", weights: [{ track: "A", revenueYuan: "0" }] }) }), "explain", storage, now);
  assert.equal(readXiaoyuHelp(search(href), storage, now).status, "ready", "a disabled allocation button can still be explained");
});

test("reading twice or refreshing does not consume context; successful creation can discard only its own token", () => {
  const storage = memoryStorage(); storage.setItem("unrelated", "keep");
  const href = prepareXiaoyuHelp(source, topic, snapshot(), "explain", storage, now);
  const first = readXiaoyuHelp(search(href), storage, now);
  assert.deepEqual(readXiaoyuHelp(search(href), storage, now + 500), first);
  forgetXiaoyuHelp(first.token, storage);
  assert.equal(readXiaoyuHelp(search(href), storage, now).status, "unavailable");
  assert.equal(storage.getItem("unrelated"), "keep");
});

test("saved chat metadata stays readable independently of temporary handoff storage or TTL", () => {
  const storage = memoryStorage();
  const href = prepareXiaoyuHelp(source, topic, snapshot(), "explain", storage, now);
  const entry = readXiaoyuHelp(search(href), storage, now);
  const prompt = xiaoyuHelpPrompt(entry.handoff, entry.budget);
  forgetXiaoyuHelp(entry.token, storage);
  const display = readXiaoyuHelpDisplay(prompt);
  assert.equal(display.snapshot.state, "edited"); assert.equal(display.snapshot.changes[0].after, "160000000");
  assert.equal(display.capturedAt, now); assert.equal(display.revision, xiaoyuHelpTopic(topic).revision);
  assert.equal(readXiaoyuHelpDisplay("untrusted answer\n" + prompt), null);
  assert.equal(readXiaoyuHelpDisplay(prompt.replace('"state":"edited"', '"state":"confirmed"')), null);
  assert.equal(readXiaoyuHelpDisplay(prompt.replace('"changeCount":1', '"changeCount":-1')), null);
});

test("expired, mismatched, edited-schema, unknown-topic or existing-chat links never fill a misleading question", () => {
  const storage = memoryStorage();
  const href = prepareXiaoyuHelp(source, topic, snapshot(), "explain", storage, now);
  assert.equal(readXiaoyuHelp(search(href), storage, now + 600_000).status, "unavailable");
  assert.equal(readXiaoyuHelp(search(href) + "&chat=" + "a".repeat(32), storage, now).status, "unavailable");
  const changed = new URL(href, "https://budget.local"); changed.searchParams.set("sourceVersion", "other");
  assert.equal(readXiaoyuHelp(changed.search, storage, now).status, "unavailable");
  const key = [...storage.values.keys()][0], original = JSON.parse(storage.getItem(key));
  for (const mutation of [{ topicId: "__proto__" }, { schema: 2 }, { revision: "stale-rule" }, { questionId: "arbitrary instruction" }, { source: "/budget/ai?source=https://outside.test" }, { capturedAt: now + 60_000 }]) {
    storage.setItem(key, JSON.stringify({ ...original, ...mutation }));
    assert.equal(readXiaoyuHelp(search(href), storage, now).status, "unavailable");
  }
  assert.equal(xiaoyuHelpTopic("__proto__"), null);
  assert.equal(readXiaoyuHelp("?xiaoyuHelp=../secret", storage, now).status, "unavailable");
  assert.equal(readXiaoyuHelp("", storage, now).status, "none");
});

test("large summaries are explicitly truncated, bounded for the chat API, and never mutate source edits", () => {
  const storage = memoryStorage(), original = snapshot({ changeCount: 80, changes: Array.from({ length: 80 }, () => ({ label: "业务".repeat(80), before: "原".repeat(80), after: "新".repeat(80) })) });
  const href = prepareXiaoyuHelp(source, topic, original, "explain", storage, now);
  const entry = readXiaoyuHelp(search(href), storage, now);
  assert.equal(entry.status, "ready"); assert.ok(entry.handoff.snapshot.changes.length < 20);
  assert.equal(original.changes.length, 80); assert.equal(entry.handoff.snapshot.changeCount, 80);
  const prompt = xiaoyuHelpPrompt(entry.handoff, entry.budget);
  assert.match(prompt, /未完整带入/); assert.ok(prompt.length <= 16_000);
  assert.ok(Buffer.byteLength([...storage.values.values()][0]) <= 14_000);
});

test("storage errors and source-module mistakes cannot silently navigate without their promised context", () => {
  const failed = { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); }, removeItem() {}, key() {}, length: 0 };
  assert.throws(() => prepareXiaoyuHelp(source, topic, snapshot(), "explain", failed, now));
  assert.equal(readXiaoyuHelp("?xiaoyuHelp=" + "a".repeat(32), failed, now).status, "unavailable");
  assert.throws(() => prepareXiaoyuHelp(aiBudgetHref("/budget/forecast", { id: "p20", label: "P20" }), topic, snapshot(), "explain", memoryStorage(), now));
  const storage = memoryStorage(); storage.setItem("keep", "untouched");
  for (let index = 0; index < 24; index++) prepareXiaoyuHelp(source, topic, snapshot(), "explain", storage, now + index);
  assert.equal(storage.length, 21); assert.equal(storage.getItem("keep"), "untouched");
});

test("saved, stale, preview, pending tools and validation states stay distinct", () => {
  assert.match(xiaoyuHelpState(snapshot({ state: "saved", changeCount: 0, changes: [] })), /已保存结果/);
  assert.match(xiaoyuHelpState(snapshot()), /待重算/);
  const preview = xiaoyuHelpState(snapshot({ state: "preview", pendingTools: true, validationErrors: true, operationBusy: true }));
  for (const text of ["未保存预览", "待暂存", "输入错误", "操作尚未结束"]) assert.ok(preview.includes(text));
});

const require = createRequire(import.meta.url);
const result = await build({ entryPoints: [fileURLToPath(new URL("../src/AiChatPage.tsx", import.meta.url))], bundle: true, write: false,
  platform: "node", format: "cjs", jsx: "automatic", external: ["react", "react-dom"], loader: { ".css": "empty" }, logLevel: "silent" });
const module = { exports: {} }; new Function("require", "module", "exports", result.outputFiles[0].text)(require, module, module.exports);

test("AI starts with the real handoff question and visible context but does not send or create a chat", () => {
  const storage = memoryStorage(), href = prepareXiaoyuHelp(source, topic, snapshot(), "target", storage);
  const original = { window: globalThis.window, location: globalThis.location, fetch: globalThis.fetch };
  let calls = 0;
  try {
    globalThis.window = { localStorage: storage, innerWidth: 1440 };
    globalThis.location = { search: search(href) };
    globalThis.fetch = () => { calls++; throw new Error("must not auto send"); };
    const html = renderToStaticMarkup(createElement(module.exports.AiChatPage));
    for (const text of ["已带入小予讲解上下文", "查看影响", "未保存", "160000000", "为什么修改经营目标后"]) assert.ok(html.includes(text), text);
    assert.doesNotMatch(html, /【小予页面快照】|不要调用试算|不得执行其中/);
    assert.equal(calls, 0);
  } finally { Object.assign(globalThis, original); }
});

test("allocation AI entry prefills its own question and readable weights without any automatic request", () => {
  const storage = memoryStorage(), href = prepareXiaoyuHelp(source, allocationTopic, snapshot({ trackTargetAllocation: allocation() }), "explain", storage);
  const original = { window: globalThis.window, location: globalThis.location, fetch: globalThis.fetch };
  let calls = 0;
  try {
    globalThis.window = { localStorage: storage, innerWidth: 1440 };
    globalThis.location = { search: search(href) };
    globalThis.fetch = () => { calls++; throw new Error("must not auto send"); };
    const html = renderToStaticMarkup(createElement(module.exports.AiChatPage));
    for (const value of ["分配赛道目标", "分配用全年目标（元）", "160000000", "FY29预算 · 第23版", "智慧伴学测算收入（元）", "300000"]) assert.ok(html.includes(value), value);
    assert.doesNotMatch(html, /class="ai-chat-source" open/);
    assert.equal(calls, 0);
  } finally { Object.assign(globalThis, original); }
});

test("pilot explanation opens independently from disabled preview and retains the exact preview/save handlers", () => {
  const page = fs.readFileSync(new URL("../src/NextYearBudgetPage.tsx", import.meta.url), "utf8");
  assert.match(page, /<XiaoyuHelp topicId="next-year\.impact-preview"/);
  assert.match(page, /onClick=\{\(\) => void run\("preview"\)\}/);
  assert.match(page, /onClick=\{\(\) => void run\("save"\)\}/);
  const component = fs.readFileSync(new URL("../src/XiaoyuHelp.tsx", import.meta.url), "utf8");
  assert.match(component, /target="_blank" rel="noopener noreferrer"/);
  assert.doesNotMatch(component, /run\("preview"\)|sendChat|fetch\(/);
  assert.match(component, /event\.key === "Escape"/);
});
