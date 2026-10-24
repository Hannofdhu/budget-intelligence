import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { chatTaskLabel, mergeChatHistory, newChatResults } from "../src/aiChatActivity.ts";
import { chatIsBusy, chatMarkdown, getChat, readChatResult } from "../src/aiChatApi.ts";

const id = "a".repeat(32), completionId = "b".repeat(32);
const item = (values={}) => ({ id, title:"示例对话", state:"running", pinned:false, archived:false, context:null,
  revision:1, message_count:2, created_at:"2026-10-24T01:00:00Z", updated_at:"2026-10-24T01:00:00Z", ...values });
const completed = (values={}) => item({ state:"idle", revision:2, unread:true,
  completion:{ id:completionId, state:"idle", at:"2026-10-24T01:01:00Z" }, ...values });

test("sidebar labels show real queue positions, tool progress and honest terminal states", () => {
  assert.equal(chatTaskLabel(item({state:"queued", queue_position:2})), "排队第2位");
  assert.equal(chatTaskLabel(item({progress:"正在执行分析工具"})), "正在执行分析工具");
  assert.equal(chatTaskLabel(completed()), "已完成");
  assert.equal(chatTaskLabel(item({state:"error"})), "本轮未完成");
  assert.equal(chatTaskLabel(item({state:"cancelled"})), "已取消排队");
  assert.equal(chatTaskLabel(item({state:"idle", needs_stop:true})), "退出待确认");
  assert.ok(chatIsBusy("queued")); assert.ok(!chatIsBusy("cancelled"));
});

test("live history cannot roll back newer local metadata or drop another active conversation", () => {
  const newer = completed({ revision:8, pinned:true });
  const another = item({ id:"c".repeat(32), title:"另一段对话" });
  const merged = mergeChatHistory([newer, another], [item()]);
  assert.equal(merged.length,2); assert.equal(merged[0].revision,8); assert.ok(merged[0].unread);
  const changedPosition = mergeChatHistory([item({state:"queued", queue_position:2})], [item({state:"queued", queue_position:1})]);
  assert.equal(changedPosition[0].queue_position,1);
});

test("background result alerts are deduplicated by completion id, not poll count", () => {
  assert.equal(newChatResults([item()], [completed()], "other").length,1);
  assert.equal(newChatResults([completed()], [completed()], "other").length,0);
  assert.equal(newChatResults([item()], [completed()], id).length,0);
  assert.equal(newChatResults([completed({revision:8})], [completed({revision:2})], "other").length,0);
  assert.equal(newChatResults([], [completed({unread:false})], null).length,0);
  assert.equal(newChatResults([item()], [completed({completion:{id:completionId,state:"error",at:"2026-10-24T01:01:00Z"}})], null).length,1);
  assert.equal(newChatResults([], [completed({state:"cancelled",completion:{id:completionId,state:"cancelled",at:"2026-10-24T01:01:00Z"}})], null).length,0);
});

test("mark read is an exact completion acknowledgement with origin-scoped cookies", async () => {
  const original=globalThis.fetch, calls=[];
  globalThis.fetch=async (url,options)=>{calls.push({url,...options});return Response.json({...completed(),messages:[]});};
  try {
    await readChatResult(id,completionId);
    assert.equal(calls[0].url,`/api/v1/ai-agent/conversations/${id}/read`);
    assert.equal(calls[0].method,"POST"); assert.equal(calls[0].credentials,"same-origin");
    assert.deepEqual(JSON.parse(calls[0].body),{completion_id:completionId});
    globalThis.fetch=async()=>Response.json({...completed({completion:{id:"../bad",state:"idle",at:"wrong"}}),messages:[]});
    await assert.rejects(getChat(id),/不完整/);
  } finally {globalThis.fetch=original;}
});

test("exports distinguish waiting and cancelled questions from model answers",()=>{
  for(const state of ["queued","cancelled"]){
    const text=chatMarkdown({...item(),messages:[{id,role:"assistant",text:"",created_at:"",state}]});
    assert.match(text,state==="queued"?/排队中，尚未调用模型/:/已取消排队，未调用模型/);
  }
});

test("live updates and in-flow notifications preserve user control without system permissions",()=>{
  const source=fs.readFileSync(new URL("../src/AiChatPage.tsx",import.meta.url),"utf8");
  const css=fs.readFileSync(new URL("../src/aiChat.css",import.meta.url),"utf8");
  assert.match(source,/delay = 1_000/); assert.match(source,/visibilitychange/);
  assert.match(source,/后台回答已完成/); assert.match(source,/查看结果/); assert.match(source,/取消排队/);
  assert.match(source,/!nearBottom \|\| document\.visibilityState !== "visible"/);
  assert.match(source,/保留未读/); assert.match(source,/setNotificationsOpen/);
  assert.match(source,/if \(!notificationsOpen && window\.innerWidth < 960\) \{ setSidebarOpen\(false\)/);
  assert.doesNotMatch(source,/new Notification|requestPermission|window\.alert|Notification\.permission/);
  assert.doesNotMatch(css.match(/\.ai-chat-arrival \{[^}]+\}/)?.[0]||"",/position:\s*(fixed|absolute)/);
});
