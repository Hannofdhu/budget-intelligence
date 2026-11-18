import { useEffect, useState } from "react";
import { listChatFiles, type ChatFiles } from "./aiChatFilesApi";

const empty: ChatFiles = { files: [], truncated: false, writing: false };

export function useAiChatFiles(conversationId: string | null, revision: number, busy: boolean) {
  const [result, setResult] = useState({ conversationId, collection: empty, error: "" });
  const [loading, setLoading] = useState(false), [refresh, setRefresh] = useState(0);
  useEffect(() => {
    if (!conversationId || busy) return;
    const controller = new AbortController(); let timedOut = false;
    setLoading(true);
    const timer = setTimeout(() => { timedOut = true; controller.abort(); }, 15_000);
    void listChatFiles(conversationId, controller.signal).then(collection => {
      if (!controller.signal.aborted) setResult({ conversationId, collection, error: "" });
    }).catch(failure => {
      if (!controller.signal.aborted || timedOut) setResult(previous => ({ conversationId,
        collection: previous.conversationId === conversationId ? previous.collection : empty,
        error: timedOut ? "读取文件列表超时，请刷新重试" : failure instanceof Error ? failure.message : "文件列表未能读取，请刷新重试" }));
    }).finally(() => { clearTimeout(timer); if (!controller.signal.aborted || timedOut) setLoading(false); });
    return () => { controller.abort(); clearTimeout(timer); };
  }, [conversationId, revision, busy, refresh]);
  return { collection: result.conversationId === conversationId ? result.collection : empty,
    error: result.conversationId === conversationId ? result.error : "", loading: loading && !busy,
    onRefresh: () => setRefresh(value => value + 1) };
}
