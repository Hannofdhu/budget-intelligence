import { chatRequest, type ChatMessage } from "./aiChatApi";

export type ChatFile = { id: string; name: string; size: number; modified_at: string; type_label: string;
  media_type: string; previewable: boolean; downloadable: boolean; notice: string };
export type ChatFiles = { files: ChatFile[]; truncated: boolean; writing: boolean };

// Use the saved turn times, not the last visible message. This also works for
// historical workspaces without changing their messages or generating files again.
export function groupChatFiles(files: ChatFile[], messages: ChatMessage[]): Map<string, ChatFile[]> {
  const turns = messages.flatMap((message, index) => message.role === "assistant" && message.state !== "cancelled"
    ? [{ id: message.id, start: Date.parse(messages[index - 1]?.role === "user" ? messages[index - 1].created_at : message.created_at) }]
    : []);
  const groups = new Map<string, ChatFile[]>();
  for (const file of files) {
    const time = Date.parse(file.modified_at);
    const turn = [...turns].reverse().find(item => item.start <= time) ?? turns[0];
    if (turn) groups.set(turn.id, [...(groups.get(turn.id) ?? []), file]);
  }
  return groups;
}
const mediaTypes = new Set(["application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "text/csv",
  "image/png", "image/jpeg", "image/webp", "application/pdf", "application/octet-stream", "application/json", "text/plain", "text/markdown"]);
const imageTypes = new Set(["image/png", "image/jpeg", "image/webp"]);
const maxBytes = 32 * 1024 * 1024;

function root(conversationId: string) {
  if (!/^[a-f0-9]{32}$/.test(conversationId)) throw new Error("对话标识无效，请重新打开这段对话");
  return `/api/v1/ai-agent/conversations/${conversationId}/files`;
}
export async function listChatFiles(conversationId: string, signal?: AbortSignal): Promise<ChatFiles> {
  const result = await chatRequest(root(conversationId), "GET", undefined, signal) as ChatFiles;
  if (!result || !Array.isArray(result.files) || result.files.length > 200 || typeof result.truncated !== "boolean" || typeof result.writing !== "boolean"
    || result.files.some(file => !file || !/^[a-f0-9]{64}$/.test(file.id) || typeof file.name !== "string" || !file.name || /[\/\\\u0000-\u001f\u007f]/.test(file.name)
      || !Number.isSafeInteger(file.size) || file.size < 0 || typeof file.modified_at !== "string" || !Number.isFinite(Date.parse(file.modified_at))
      || typeof file.type_label !== "string" || !mediaTypes.has(file.media_type) || typeof file.notice !== "string"
      || typeof file.previewable !== "boolean" || typeof file.downloadable !== "boolean"
      || file.previewable && (!imageTypes.has(file.media_type) || !file.downloadable || file.size > 8 * 1024 * 1024)
      || file.downloadable && (file.size === 0 || file.size > maxBytes))
    || new Set(result.files.map(file => file.id)).size !== result.files.length) {
    throw new Error("文件列表不完整，请刷新重试；对话内容仍保留");
  }
  return result;
}
export async function readChatFile(conversationId: string, file: ChatFile, preview: boolean, signal?: AbortSignal): Promise<Blob> {
  const base = root(conversationId);
  if (!/^[a-f0-9]{64}$/.test(file.id) || !file.downloadable || preview && !file.previewable) throw new Error("文件暂不可读取，请刷新文件列表");
  const response = await fetch(`${base}/${file.id}/${preview ? "preview" : "download"}`, { credentials: "same-origin", cache: "no-store", signal });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(typeof body?.detail === "string" ? body.detail : `文件读取未完成（${response.status}），请刷新重试`);
  }
  const type = response.headers.get("Content-Type")?.split(";")[0];
  if (type !== file.media_type || preview && !imageTypes.has(type || "")) throw new Error("文件类型不一致，未打开文件，请刷新核对");
  const length = response.headers.get("Content-Length");
  if (length && Number(length) > maxBytes) throw new Error("文件超过32MB，暂不提供下载");
  const blob = await response.blob();
  if (signal?.aborted) throw new DOMException("Aborted", "AbortError");
  if (blob.size !== file.size || blob.size > maxBytes) throw new Error("文件内容已变化，请刷新文件列表后重试");
  return blob;
}
export function chatFileSize(size: number): string {
  return size < 1024 ? `${size} B` : size < 1024 * 1024 ? `${(size / 1024).toFixed(1)} KB` : `${(size / 1024 / 1024).toFixed(1)} MB`;
}
