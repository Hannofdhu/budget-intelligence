import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import * as React from "react";
import { build } from "esbuild";

const require = createRequire(import.meta.url), conversationId = "a".repeat(32);
const file = { id: "b".repeat(64), name: "预算分析.xlsx", size: 3, modified_at: "2026-10-26T07:00:00Z",
  type_label: "Excel", media_type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", previewable: false, downloadable: true, notice: "" };
const png = { ...file, id: "c".repeat(64), name: "中文图.png", type_label: "PNG 图片", media_type: "image/png", previewable: true };
const collection = { files: [file, png], truncated: false, writing: false };
const bundled = await build({ entryPoints: [fileURLToPath(new URL("../src/aiChatFilesApi.ts", import.meta.url))], bundle: true, write: false, platform: "node", format: "cjs" });
const loaded = { exports: {} };
new Function("require", "module", "exports", bundled.outputFiles[0].text)(require, loaded, loaded.exports);
const api = loaded.exports;
const json = body => new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } });
const settle = () => new Promise(resolve => setImmediate(resolve));

test("file listing validates server metadata and uses only same-origin GET without budget actions", async () => {
  const original = globalThis.fetch, calls = [];
  globalThis.fetch = async (...args) => { calls.push(args); return json(collection); };
  try {
    assert.deepEqual(await api.listChatFiles(conversationId), collection);
    assert.equal(calls[0][0], `/api/v1/ai-agent/conversations/${conversationId}/files`);
    assert.equal(calls[0][1].method, "GET");
    assert.equal(calls[0][1].credentials, "same-origin");
    assert.equal(calls[0][1].cache, "no-store");
    await assert.rejects(api.listChatFiles("../private"), /标识无效/);
    assert.equal(calls.length, 1);
    for (const invalid of [
      { ...collection, files: [file, file] }, { ...collection, files: [{ ...file, name: "../private.xlsx" }] },
      { ...collection, files: [{ ...png, media_type: "image/svg+xml" }] },
      { ...collection, files: [{ ...file, size: 33 * 1024 * 1024 }] },
      { ...collection, files: [{ ...png, size: 9 * 1024 * 1024 }] },
    ]) {
      globalThis.fetch = async () => json(invalid);
      await assert.rejects(api.listChatFiles(conversationId), /文件列表不完整/);
    }
  } finally { globalThis.fetch = original; }
});

test("downloads preserve bytes and reject changed types, changed sizes, revoked access and unsafe previews", async () => {
  const original = globalThis.fetch, calls = [];
  globalThis.fetch = async (...args) => { calls.push(args); return new Response("abc", { headers: { "Content-Type": file.media_type } }); };
  try {
    assert.equal(await (await api.readChatFile(conversationId, file, false)).text(), "abc");
    assert.equal(calls[0][0], `/api/v1/ai-agent/conversations/${conversationId}/files/${file.id}/download`);
    assert.equal(calls[0][1].credentials, "same-origin");
    await assert.rejects(api.readChatFile(conversationId, file, true), /暂不可读取/);
    assert.equal(calls.length, 1);
    globalThis.fetch = async () => new Response("abc", { headers: { "Content-Type": "text/html" } });
    await assert.rejects(api.readChatFile(conversationId, file, false), /文件类型不一致/);
    globalThis.fetch = async () => new Response("changed", { headers: { "Content-Type": file.media_type } });
    await assert.rejects(api.readChatFile(conversationId, file, false), /文件内容已变化/);
    globalThis.fetch = async () => new Response(JSON.stringify({ detail: "文件不存在或已更新" }), { status: 404 });
    await assert.rejects(api.readChatFile(conversationId, file, false), /不存在或已更新/);
    const controller = new AbortController(); controller.abort();
    globalThis.fetch = async () => new Response("abc", { headers: { "Content-Type": file.media_type } });
    await assert.rejects(api.readChatFile(conversationId, file, false, controller.signal), { name: "AbortError" });
    assert.equal(api.chatFileSize(1536), "1.5 KB");
  } finally { globalThis.fetch = original; }
});

const uiBundle = await build({ entryPoints: [fileURLToPath(new URL("../src/AiChatFiles.tsx", import.meta.url))], bundle: true,
  write: false, platform: "node", format: "cjs", jsx: "automatic", external: ["react", "react/jsx-runtime"], loader: { ".css": "empty" }, logLevel: "silent",
  plugins: [{ name: "file-fixture", setup(builder) { builder.onResolve({ filter: /\/aiChatFilesApi$/ }, () => ({ path: "file-fixture", external: true })); } }] });
const hookBundle = await build({ entryPoints: [fileURLToPath(new URL("../src/useAiChatFiles.ts", import.meta.url))], bundle: true,
  write: false, platform: "node", format: "cjs", external: ["react"],
  plugins: [{ name: "file-fixture", setup(builder) { builder.onResolve({ filter: /\/aiChatFilesApi$/ }, () => ({ path: "file-fixture", external: true })); } }] });
const nodes = tree => Array.isArray(tree) ? tree.flatMap(nodes) : !tree || typeof tree !== "object" ? [] : [tree, ...nodes(tree.props?.children)];
const text = tree => Array.isArray(tree) ? tree.map(text).join("") : tree && typeof tree === "object" ? text(tree.props?.children) : tree == null || typeof tree === "boolean" ? "" : String(tree);
function harness(fixture, hook = false) {
  const slots = [], effects = [], lifetimes = [];
  let cursor = 0;
  const hooks = { ...React,
    useState(initial) { const i = cursor++; if (!(i in slots)) slots[i] = typeof initial === "function" ? initial() : initial; return [slots[i], next => { slots[i] = typeof next === "function" ? next(slots[i]) : next; }]; },
    useRef(value) { const i = cursor++; return slots[i] ??= { current: value }; },
    useEffect(fn, deps) { const i = cursor++; const old = slots[i]; if (!old || deps.some((dep, index) => dep !== old.deps[index])) {
      slots[i] = { deps }; effects.push(() => { old?.cleanup?.(); const cleanup = fn(); slots[i].cleanup = cleanup; lifetimes.push(cleanup); });
    } },
  };
  const ui = { exports: {} };
  new Function("require", "module", "exports", (hook ? hookBundle : uiBundle).outputFiles[0].text)(id => id === "react" ? hooks : id === "file-fixture" ? { ...api, ...fixture } : require(id), ui, ui.exports);
  return {
    render(props = {}) { cursor = 0; return hook ? ui.exports.useAiChatFiles(props.conversationId ?? conversationId, props.revision ?? 1, props.busy ?? false)
      : ui.exports.AiChatFiles({ conversationId, collection, busy: false, loading: false, listError: "", onRefresh() {}, ...props }); },
    effects() { effects.splice(0).forEach(fn => fn()); },
    unmount() { lifetimes.forEach(fn => fn?.()); },
  };
}
const button = (tree, label) => nodes(tree).find(node => node.type === "button" && node.props["aria-label"] === label);

test("cards offer explicit download and inline image preview, revoke object URLs on close and unmount", async () => {
  const originalCreate = URL.createObjectURL, originalRevoke = URL.revokeObjectURL, revoked = [], reads = [];
  URL.createObjectURL = () => "blob:test-image"; URL.revokeObjectURL = url => revoked.push(url);
  const ui = harness({ listChatFiles: async () => collection, readChatFile: async (...args) => { reads.push(args); return new Blob(["abc"], { type: "image/png" }); } });
  try {
    ui.render(); ui.effects(); await settle(); let tree = ui.render();
    assert.match(text(tree), /本条回答生成的文件2个/);
    assert.ok(button(tree, `下载 ${file.name}`));
    assert.equal(button(tree, `预览 ${file.name}`), undefined);
    button(tree, `预览 ${png.name}`).props.onClick(); await settle(); tree = ui.render();
    const image = nodes(tree).find(node => node.type === "img");
    assert.equal(image.props.src, "blob:test-image");
    assert.equal(image.props.alt, png.name);
    assert.equal(button(tree, `预览 ${png.name}`).props["aria-expanded"], true);
    assert.equal(reads[0][0], conversationId);
    button(tree, `预览 ${png.name}`).props.onClick(); tree = ui.render();
    assert.equal(nodes(tree).some(node => node.type === "img"), false);
    assert.deepEqual(revoked, ["blob:test-image"]);
    tree = ui.render({ busy: true });
    assert.equal(button(tree, `下载 ${file.name}`).props.disabled, true);
    assert.match(text(tree), /正在生成文件/);
    ui.unmount();
  } finally { URL.createObjectURL = originalCreate; URL.revokeObjectURL = originalRevoke; ui.unmount(); }
});

test("switching away aborts pending file reads and cannot open or download a previous conversation result", async () => {
  let resolveRead, readSignal, created = 0;
  const original = URL.createObjectURL;
  URL.createObjectURL = () => { created++; return "blob:should-not-open"; };
  const ui = harness({ listChatFiles: async () => collection, readChatFile: (...args) => { readSignal = args[3]; return new Promise(resolve => { resolveRead = resolve; }); } });
  try {
    ui.render(); ui.effects(); await settle(); const tree = ui.render();
    button(tree, `预览 ${png.name}`).props.onClick(); ui.unmount();
    assert.equal(readSignal.aborted, true);
    resolveRead(new Blob(["abc"])); await settle();
    assert.equal(created, 0);
  } finally { URL.createObjectURL = original; ui.unmount(); }
});

test("read failures remain retryable and refreshing delegates to the shared conversation list", async () => {
  let refreshes = 0;
  const ui = harness({ readChatFile: async () => { throw new Error("文件已更新，请刷新文件列表"); } });
  try {
    ui.render(); ui.effects(); let tree = ui.render();
    assert.ok(button(tree, `下载 ${file.name}`));
    button(tree, `下载 ${file.name}`).props.onClick(); await settle(); tree = ui.render({ onRefresh: () => refreshes++ });
    assert.match(text(tree), /文件已更新，请刷新文件列表/);
    assert.equal(button(tree, `下载 ${file.name}`).props.disabled, false);
    assert.equal(nodes(tree).find(node => node.props?.role === "alert").props.children, "文件已更新，请刷新文件列表");
    nodes(tree).find(node => node.type === "button" && text(node) === "刷新").props.onClick();
    assert.equal(refreshes, 1);
  } finally { ui.unmount(); }
});

test("files stay with the saved generating turn after follow-ups, refresh and cancellation", () => {
  const message = (id, role, time, state = "complete") => ({ id, role, created_at: `2026-10-26T${time}Z`, state, text: "" });
  const first = [message("u1", "user", "06:00:00"), message("a1", "assistant", "06:00:01")];
  const second = [...first, message("u2", "user", "08:00:00"), message("a2", "assistant", "08:00:01")];
  const third = [...second, message("u3", "user", "10:00:00"), message("a3", "assistant", "10:00:01", "running")];
  const later = { ...png, modified_at: "2026-10-26T09:00:00Z" };
  assert.deepEqual(api.groupChatFiles([file], first).get("a1"), [file]);
  assert.deepEqual([...api.groupChatFiles([file, later], third)], [["a1", [file]], ["a2", [later]]]);
  assert.deepEqual([...api.groupChatFiles(JSON.parse(JSON.stringify([file, later])), JSON.parse(JSON.stringify(third)))], [["a1", [file]], ["a2", [later]]]);
  // A file written in the small interval between queuing the question and answer belongs to that turn.
  assert.deepEqual(api.groupChatFiles([{ ...later, modified_at: "2026-10-26T08:00:00Z" }], second).get("a2")?.length, 1);
  const cancelled = [...second, message("u3", "user", "10:00:00"), message("a3", "assistant", "10:00:01", "cancelled")];
  assert.equal(api.groupChatFiles([file, later], cancelled).has("a3"), false);
  assert.equal(api.groupChatFiles([{ ...file, modified_at: "2026-10-25T00:00:00Z" }], second).get("a1")?.length, 1);
  assert.equal(api.groupChatFiles([file], []).size, 0);
});

test("the shared list rejects late revisions and hides old files immediately on conversation switches", async () => {
  let resolveOld, oldSignal, calls = 0;
  const ui = harness({ listChatFiles: (_, signal) => ++calls === 1 ? new Promise(resolve => { resolveOld = resolve; oldSignal = signal; }) : Promise.resolve(collection) }, true);
  try {
    ui.render(); ui.effects();
    ui.render({ revision: 2 }); ui.effects(); await settle();
    assert.equal(oldSignal.aborted, true);
    resolveOld({ ...collection, files: [] }); await settle();
    assert.deepEqual(ui.render({ revision: 2 }).collection, collection);
    ui.render({ revision: 2 }); ui.effects();
    assert.equal(calls, 2);
    assert.deepEqual(ui.render({ conversationId: "d".repeat(32), busy: true }).collection.files, []);
    ui.effects();
    assert.equal(calls, 2);
    ui.render({ conversationId: "d".repeat(32) }); ui.effects(); await settle();
    assert.equal(calls, 3);
  } finally { ui.unmount(); }
});

test("a shared refresh recovers list failures without clearing existing answer attachments", async () => {
  let calls = 0;
  const ui = harness({ listChatFiles: async () => { if (++calls === 2) throw new Error("暂时无法读取"); return collection; } }, true);
  try {
    ui.render(); ui.effects(); await settle();
    ui.render().onRefresh(); ui.render(); ui.effects(); await settle();
    let state = ui.render();
    assert.equal(state.error, "暂时无法读取");
    assert.deepEqual(state.collection.files, collection.files);
    state.onRefresh(); ui.render(); ui.effects(); await settle(); state = ui.render();
    assert.equal(state.error, "");
    assert.deepEqual(state.collection.files, collection.files);
    assert.equal(calls, 3);
  } finally { ui.unmount(); }
});
