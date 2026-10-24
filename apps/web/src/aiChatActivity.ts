import type { ChatSummary } from "./aiChatApi";

export function chatTaskLabel(item: Pick<ChatSummary, "state" | "queue_position" | "progress" | "message_count" | "needs_stop">): string {
  if (item.needs_stop) return "退出待确认";
  if (item.state === "queued") return item.queue_position ? `排队第${item.queue_position}位` : "排队中";
  if (item.state === "running") return item.progress || "正在回答";
  return ({ preparing: "正在准备", stopping: "正在停止", cancelled: "已取消排队", interrupted: "回答已停止", error: "本轮未完成", idle: item.message_count ? "已完成" : "未开始" } as Record<string,string>)[item.state] || "状态待核对";
}

export function mergeChatHistory(previous: ChatSummary[], incoming: ChatSummary[]): ChatSummary[] {
  const byId = new Map(previous.map(item => [item.id, item]));
  for (const item of incoming) {
    const existing = byId.get(item.id);
    if (!existing || item.revision >= existing.revision) byId.set(item.id, item);
  }
  return [...byId.values()].sort((a,b) => Number(b.pinned) - Number(a.pinned) || b.updated_at.localeCompare(a.updated_at));
}

export function newChatResults(previous: ChatSummary[], incoming: ChatSummary[], visibleId: string | null): ChatSummary[] {
  const byId = new Map(previous.map(item => [item.id,item]));
  return incoming.filter(item => item.unread && item.completion && item.id !== visibleId
    && ["idle", "error", "interrupted"].includes(item.completion.state)
    && byId.get(item.id)?.completion?.id !== item.completion.id
    && (!byId.has(item.id) || item.revision >= byId.get(item.id)!.revision));
}
