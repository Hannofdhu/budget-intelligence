/** Unsent text stays in this tab only; never a conversation, model call or budget write. */
type DraftStorage = Pick<Storage, "getItem" | "setItem" | "removeItem">;
const prefix = "budget.ai-chat-draft.v1:";
const maximumLength = 32_000;

function tabStorage(): DraftStorage | null {
  try { return typeof window === "undefined" ? null : window.sessionStorage; }
  catch { return null; }
}

export function aiChatDraftKey(id: string | null, source = "", handoff = ""): string {
  return id ? `chat:${id}` : `new:${JSON.stringify([source, handoff])}`;
}

export function readAiChatDraft(key: string, fallback = "", storage = tabStorage()): string {
  try {
    const value = storage?.getItem(prefix + key);
    return typeof value === "string" && value.length <= maximumLength ? value : fallback;
  } catch { return fallback; }
}

export function saveAiChatDraft(key: string, text: string, storage = tabStorage()): boolean {
  if (!storage || typeof text !== "string" || text.length > maximumLength) return false;
  try { storage.setItem(prefix + key, text); return true; }
  catch { return false; }
}

export function clearAiChatDraft(key: string, storage = tabStorage()): void {
  try { storage?.removeItem(prefix + key); }
  catch { /* A blocked cache must not prevent leaving or acknowledging an accepted message. */ }
}
