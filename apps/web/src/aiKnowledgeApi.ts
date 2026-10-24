import { chatRequest } from "./aiChatApi.ts";

export type KnowledgeDraft = { title: string; content: string; kind: "business" | "preference";
  module: "all" | "budget" | "forecast" | "next-year"; fiscal_year: number | null; keywords: string[] };
export type KnowledgeSource = { conversation_id?: string; message_id?: string; answer_id?: string;
  excerpt?: string; label?: string; href?: string };
export type KnowledgeEntry = KnowledgeDraft & { id: string; revision: number;
  state: "pending" | "active" | "disabled" | "dismissed" | "deleted"; builtin?: boolean; preset?: boolean;
  source: KnowledgeSource; created_at?: string; updated_at?: string; approved_at?: string | null;
  conflicts?: { id: string; title: string; revision: number }[];
  collection_revision?: number;
  history: { at: string; action: string; revision: number; title: string; content: string; state: string }[] };
export type KnowledgeSnapshot = { revision: number; captured_at: string; items: (KnowledgeDraft & {
  id: string; revision: number; source: KnowledgeSource; builtin?: boolean; preset?: boolean; approved_at?: string | null })[] };
export type KnowledgeCollection = { revision: number; items: KnowledgeEntry[]; builtin: KnowledgeEntry[]; storage: string };

const root = "/api/v1/ai-agent/knowledge";
function identifier(id: string) {
  if (!/^(?:[a-f0-9]{32}|core-dimensions|core-metrics)$/.test(id)) throw new Error("知识标识无效，请刷新列表");
  return `${root}/${id}`;
}
function entry(value: unknown): KnowledgeEntry {
  const item = value as KnowledgeEntry;
  if (!item || typeof item.id !== "string" || !/^(?:[a-f0-9]{32}|core-[a-z-]+)$/.test(item.id)
    || !Number.isSafeInteger(item.revision) || item.revision < 1 || typeof item.title !== "string"
    || typeof item.content !== "string" || !["business", "preference"].includes(item.kind)
    || !["all", "budget", "forecast", "next-year"].includes(item.module)
    || !["pending", "active", "disabled", "dismissed", "deleted"].includes(item.state)
    || item.fiscal_year !== null && (!Number.isSafeInteger(item.fiscal_year) || item.fiscal_year < 2000 || item.fiscal_year > 2200)
    || !Array.isArray(item.keywords) || item.keywords.some(word => typeof word !== "string")
    || !item.source || typeof item.source !== "object" || !Array.isArray(item.history)
    || Object.values(item.source).some(value => typeof value !== "string")
    || item.conflicts !== undefined && (!Array.isArray(item.conflicts) || item.conflicts.some(conflict =>
      !/^(?:[a-f0-9]{32}|core-dimensions|core-metrics)$/.test(conflict.id) || typeof conflict.title !== "string" || !Number.isSafeInteger(conflict.revision)))) {
    throw new Error("知识响应不完整，未替换已有记录");
  }
  return item;
}
export async function listKnowledge(signal?: AbortSignal): Promise<KnowledgeCollection> {
  const result = await chatRequest(root, "GET", undefined, signal) as KnowledgeCollection;
  if (!result || !Number.isSafeInteger(result.revision) || !Array.isArray(result.items) || !Array.isArray(result.builtin)) {
    throw new Error("知识列表暂时无法读取，原记录仍保留");
  }
  return { ...result, items: result.items.map(entry), builtin: result.builtin.map(entry) };
}
export async function proposeKnowledge(chatId: string, answerId: string) {
  if (![chatId, answerId].every(id => /^[a-f0-9]{32}$/.test(id))) throw new Error("知识来源无效");
  return entry(await chatRequest(`/api/v1/ai-agent/conversations/${chatId}/knowledge-proposals`, "POST", { message_id: answerId }));
}
export async function createKnowledge(draft: KnowledgeDraft, requestId: string) {
  if (!/^[a-f0-9]{32}$/.test(requestId)) throw new Error("记录标识无效，请重新打开新增表单");
  return entry(await chatRequest(root, "POST", { ...draft, request_id: requestId }));
}
export async function editKnowledge(item: KnowledgeEntry, draft: KnowledgeDraft) {
  return entry(await chatRequest(identifier(item.id), "PATCH", { ...draft, revision: item.revision }));
}
export async function actOnKnowledge(item: KnowledgeEntry, action: "approve" | "disable" | "dismiss" | "delete", replace = false) {
  const replacements = replace ? Object.fromEntries((item.conflicts ?? []).map(conflict => [conflict.id, conflict.revision])) : {};
  return entry(await chatRequest(`${identifier(item.id)}/actions`, "POST", { revision: item.revision, action, replacements }));
}
export const knowledgeModuleLabel = (value: KnowledgeDraft["module"]) => ({ all: "预算分析", budget: "定版预算", forecast: "全年预测", "next-year": "预算编制" })[value];
export const knowledgeDate = (value?: string | null) => value && Number.isFinite(Date.parse(value))
  ? new Date(value).toLocaleString("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }) : "时间未记录";
