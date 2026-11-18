import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import { aiChatDraftKey, readAiChatDraft, saveAiChatDraft, clearAiChatDraft } from "../src/aiChatDrafts.ts";

function memoryStorage() {
  const values = new Map();
  return { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) };
}

test("tab drafts survive remounts without turning unsent text into a conversation", () => {
  const storage = memoryStorage(), key = aiChatDraftKey(null, "/budget/cockpit?forecast_version=one");
  assert.equal(readAiChatDraft(key, "默认问题", storage), "默认问题");
  assert.equal(saveAiChatDraft(key, "只看秋季\n还没有发送。", storage), true);
  assert.equal(readAiChatDraft(key, "默认问题", storage), "只看秋季\n还没有发送。");
});

test("source, fiscal year, version, handoff and existing conversations never share a draft", () => {
  const storage = memoryStorage(), source = "/budget/cockpit?forecast_version=one&year=2027";
  const original = aiChatDraftKey(null, source);
  saveAiChatDraft(original, "原范围的未发送问题", storage);
  for (const key of [aiChatDraftKey(null, source.replace("one", "two")), aiChatDraftKey(null, source.replace("2027", "2028")),
    aiChatDraftKey(null, source, "another-handoff"), aiChatDraftKey(null), aiChatDraftKey("a".repeat(32)), aiChatDraftKey("b".repeat(32))]) {
    assert.equal(readAiChatDraft(key, "", storage), "");
  }
  assert.equal(aiChatDraftKey("a".repeat(32), source), aiChatDraftKey("a".repeat(32)), "existing drafts belong to exact conversation IDs");
});

test("an intentionally cleared question stays blank instead of restoring the default prompt", () => {
  const storage = memoryStorage(), key = aiChatDraftKey(null);
  saveAiChatDraft(key, "", storage);
  assert.equal(readAiChatDraft(key, "默认问题", storage), "");
});

test("accepted drafts can be removed without changing another conversation", () => {
  const storage = memoryStorage(), first = aiChatDraftKey("a".repeat(32)), second = aiChatDraftKey("b".repeat(32));
  saveAiChatDraft(first, "已接收", storage); saveAiChatDraft(second, "还没发送", storage);
  clearAiChatDraft(first, storage);
  assert.equal(readAiChatDraft(first, "", storage), "");
  assert.equal(readAiChatDraft(second, "", storage), "还没发送");
});

test("storage restrictions or quota errors remain nonblocking and are reported to the caller", () => {
  const blocked = { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("full"); }, removeItem() { throw new Error("blocked"); } };
  assert.equal(readAiChatDraft("new", "原问题", blocked), "原问题");
  assert.equal(saveAiChatDraft("new", "仍在编辑", blocked), false);
  assert.doesNotThrow(() => clearAiChatDraft("new", blocked));
  assert.equal(saveAiChatDraft("new", "仍在编辑", null), false);
});

test("draft storage uses the same input size limit and no durable or remote persistence", () => {
  const storage = memoryStorage();
  assert.equal(saveAiChatDraft("limit", "字".repeat(32_000), storage), true);
  assert.equal(saveAiChatDraft("limit", "字".repeat(32_001), storage), false);
  assert.equal(readAiChatDraft("limit", "", storage).length, 32_000);
  const source = fs.readFileSync(new URL("../src/aiChatDrafts.ts", import.meta.url), "utf8");
  assert.match(source, /window\.sessionStorage/);
  assert.doesNotMatch(source, /localStorage|fetch\(|sendChat|createChat|beforeunload/);
});
