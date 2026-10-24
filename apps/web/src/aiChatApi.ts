export type ChatState = "idle" | "queued" | "preparing" | "running" | "stopping" | "interrupted" | "cancelled" | "error";
export type ChatContext = { label: string; scope: string; href: string; prompt: string };
import type { KnowledgeSnapshot } from "./aiKnowledgeApi";
export type ChatActivity = { id: string; tool: string; summary: string; state: string; output: string };
export type ChatMessage = { id: string; role: "user" | "assistant"; text: string; created_at: string; state: string;
  activities?: ChatActivity[]; error?: string; progress?: string; model?: string; knowledge?: KnowledgeSnapshot;
  knowledge_proposal_id?: string; knowledge_notice?: string;
  validation?: { state: "limited" | "needs_review"; warnings: string[]; notice: string; resources: string[]; tables_examined: number } };
export type ChatSummary = { id: string; title: string; created_at: string; updated_at: string; pinned: boolean;
  archived: boolean; state: ChatState; context: ChatContext | null; revision: number; message_count: number; needs_stop?: boolean;
  queue_position?: number | null; progress?: string; unread?: boolean; completion?: { id: string; state: ChatState; at: string } | null };
export type ChatConversation = ChatSummary & { messages: ChatMessage[] };

const prefix = "/api/v1/ai-agent/conversations";
const identifier = (id: string) => {
  if (!/^[a-f0-9]{32}$/.test(id)) throw new Error("对话标识无效，请从历史列表重新打开");
  return `${prefix}/${id}`;
};
export const chatIsBusy = (state?: string) => ["queued", "preparing", "running", "stopping"].includes(state ?? "");
export const chatRequestId = () => crypto.randomUUID().replaceAll("-", "");

export async function chatRequest(path: string, method = "GET", payload?: unknown, signal?: AbortSignal): Promise<unknown> {
  const response = await fetch(path, { method, credentials: "same-origin", cache: "no-store", signal,
    ...(payload !== undefined ? { headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) } : {}) });
  const body = await response.json().catch(() => null);
  if (!response.ok) throw new Error(typeof body?.detail === "string" ? body.detail
    : Array.isArray(body?.detail) && typeof body.detail[0]?.msg === "string" ? body.detail[0].msg.replace(/^Value error, /, "")
    : response.status === 401 ? "浏览器连接已过期，请点击重新连接；编辑中的消息仍保留"
    : `对话请求未完成（${response.status}），已保留当前内容`);
  return body;
}
const request = chatRequest;

function validKnowledge(value: unknown): boolean {
  const item = value as KnowledgeSnapshot;
  return Boolean(item && Number.isSafeInteger(item.revision) && typeof item.captured_at === "string"
    && Number.isFinite(Date.parse(item.captured_at)) && Array.isArray(item.items) && item.items.length <= 10
    && item.items.every(entry => entry && typeof entry.id === "string" && typeof entry.title === "string"
      && typeof entry.content === "string" && Number.isSafeInteger(entry.revision) && entry.source && typeof entry.source === "object"));
}

function summary(value: unknown): ChatSummary {
  const item = value as ChatSummary;
  if (!item || !/^[a-f0-9]{32}$/.test(item.id) || typeof item.title !== "string" || typeof item.archived !== "boolean"
    || typeof item.pinned !== "boolean" || !["idle", "queued", "preparing", "running", "stopping", "interrupted", "cancelled", "error"].includes(item.state)
    || !Number.isSafeInteger(item.revision) || typeof item.created_at !== "string" || typeof item.updated_at !== "string"
    || !Number.isFinite(Date.parse(item.created_at)) || !Number.isFinite(Date.parse(item.updated_at))
    || item.context !== null && (!item.context || ["label", "scope", "href", "prompt"].some(key => typeof item.context?.[key as keyof ChatContext] !== "string"))
    || item.queue_position != null && (!Number.isSafeInteger(item.queue_position) || item.queue_position < 1)
    || item.progress !== undefined && typeof item.progress !== "string"
    || item.unread !== undefined && typeof item.unread !== "boolean"
    || item.completion != null && (!/^[a-f0-9]{32}$/.test(item.completion.id) || !["idle", "error", "interrupted", "cancelled"].includes(item.completion.state) || !Number.isFinite(Date.parse(item.completion.at)))) {
    throw new Error("对话响应不完整，未替换已有记录");
  }
  return item;
}

function conversation(value: unknown): ChatConversation {
  const item = summary(value) as ChatConversation;
  if (!Array.isArray(item.messages) || item.messages.some(message => !message || typeof message.id !== "string"
    || !["user", "assistant"].includes(message.role) || typeof message.text !== "string"
    || !["complete", "queued", "preparing", "running", "stopping", "interrupted", "cancelled", "error"].includes(message.state)
    || message.knowledge !== undefined && !validKnowledge(message.knowledge)
    || message.knowledge_proposal_id !== undefined && !/^[a-f0-9]{32}$/.test(message.knowledge_proposal_id)
    || message.knowledge_notice !== undefined && typeof message.knowledge_notice !== "string"
    || message.validation !== undefined && (!message.validation || !["limited", "needs_review"].includes(message.validation.state)
      || !Array.isArray(message.validation.warnings) || message.validation.warnings.some(value => typeof value !== "string")
      || typeof message.validation.notice !== "string" || !Array.isArray(message.validation.resources)
      || message.validation.resources.some(value => typeof value !== "string"))
    || message.activities !== undefined && (!Array.isArray(message.activities) || message.activities.some(activity =>
      !activity || ["id", "tool", "summary", "state", "output"].some(key => typeof activity[key as keyof ChatActivity] !== "string"))))) {
    throw new Error("消息记录不完整，未替换已有内容");
  }
  return item;
}

export async function listChats(signal?: AbortSignal): Promise<ChatSummary[]> {
  const result = await request(prefix, "GET", undefined, signal) as { conversations: unknown[] };
  if (!Array.isArray(result?.conversations)) throw new Error("对话列表暂时无法读取");
  return result.conversations.map(summary);
}
export async function getChat(id: string, signal?: AbortSignal) { return conversation(await request(identifier(id), "GET", undefined, signal)); }
export async function createChat(requestId: string, context: ChatContext | null) {
  return conversation(await request(prefix, "POST", { request_id: requestId, context }));
}
export async function sendChat(id: string, text: string, requestId: string) {
  return conversation(await request(`${identifier(id)}/messages`, "POST", { text, request_id: requestId }));
}
export async function updateChat(id: string, values: Partial<Pick<ChatSummary, "title" | "pinned" | "archived">>) {
  return conversation(await request(identifier(id), "PATCH", values));
}
export async function stopChat(id: string) { return conversation(await request(`${identifier(id)}/stop`, "POST", {})); }
export async function readChatResult(id: string, completionId: string) {
  return conversation(await request(`${identifier(id)}/read`, "POST", { completion_id: completionId }));
}

export function chatMarkdown(record: ChatConversation): string {
  return `# ${record.title}\n\n保存于本机 · ${record.updated_at}\n${record.context ? `\n引用：${record.context.label}\n范围：${record.context.scope}\n来源：${record.context.href}\n` : ""}`
    + record.messages.map(message => `\n## ${message.role === "user" ? "我" : "AI智解"}\n\n${message.validation?.state === "needs_review" ? `> 待核实：${message.validation.warnings.join("；")}\n\n` : ""}${message.text}\n${message.state === "cancelled" ? "\n> 已取消排队，未调用模型。\n" : message.state === "queued" ? "\n> 排队中，尚未调用模型。\n" : message.state === "interrupted" ? "\n> 回答已停止，已收到的内容保留。\n" : ["preparing", "running", "stopping"].includes(message.state) ? "\n> 本轮尚未完成，此为已收到的内容。\n" : ""}${message.error ? `\n> ${message.error}\n` : ""}`
      + (message.knowledge ? `\n> 本轮已加载口径与偏好：${message.knowledge.items.map(item => `${item.title}（修订${item.revision}）`).join("；")}。此为回答时快照，不代表预算已核验。\n` : "")
      + (message.activities?.length ? `\n<details>\n<summary>执行过程</summary>\n\n${message.activities.map(activity => `- ${activity.tool} · ${activity.state}\n\n\`\`\`text\n${activity.summary}\n${activity.output}\n\`\`\`\n`).join("\n")}\n</details>\n` : "")).join("\n");
}
