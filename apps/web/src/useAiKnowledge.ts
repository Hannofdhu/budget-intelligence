import { useCallback, useEffect, useRef, useState } from "react";
import { actOnKnowledge, createKnowledge, editKnowledge, listKnowledge, proposeKnowledge, type KnowledgeCollection,
  type KnowledgeDraft, type KnowledgeEntry } from "./aiKnowledgeApi";

export function useAiKnowledge(enabled: boolean, proposalKey: string, contextKey: string) {
  const [collection, setCollection] = useState<KnowledgeCollection | null>(null);
  const [readError, setReadError] = useState(""), [writeError, setWriteError] = useState("");
  const [loading, setLoading] = useState(false), [workingId, setWorkingId] = useState("");
  const [noticeValue, setNoticeValue] = useState<{ context: string; text: string } | null>(null);
  const setNotice = (text: string) => setNoticeValue({ context: contextKey, text });
  const alive = useRef(true), operation = useRef(false);
  const refresh = useCallback(async (signal?: AbortSignal) => {
    if (!enabled) return;
    setLoading(true);
    try { const next = await listKnowledge(signal); if (alive.current && !signal?.aborted) {
      setCollection(previous => previous && previous.revision > next.revision ? previous : next); setReadError("");
    } } catch (failure) { if (alive.current && !signal?.aborted) setReadError(failure instanceof Error ? failure.message : "知识暂时无法读取，原记录仍保留"); }
    finally { if (alive.current && !signal?.aborted) setLoading(false); }
  }, [enabled]);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    void refresh(controller.signal);
    const focus = () => { if (document.visibilityState === "visible") void refresh(controller.signal); };
    const timer = setInterval(focus, 10_000);
    window.addEventListener("focus", focus);
    return () => { controller.abort(); clearInterval(timer); window.removeEventListener("focus", focus); };
  }, [enabled, proposalKey, refresh]);
  const received = (item: KnowledgeEntry) => setCollection(previous => previous ? { ...previous, revision: Math.max(previous.revision, item.collection_revision ?? previous.revision),
    items: previous.items.some(current => current.id === item.id) ? previous.items.map(current => current.id === item.id ? item : current) : [...previous.items, item] } : previous);
  const action = async (item: KnowledgeEntry, action: "approve" | "disable" | "dismiss" | "delete", replace = false) => {
    if (operation.current) return false;
    operation.current = true; setWorkingId(item.id); setWriteError(""); setNotice("");
    try { received(await actOnKnowledge(item, action, replace));
      setNotice(({ approve: "知识已确认保存，后续相关提问将加载。", disable: "已停用，后续提问不再加载；历史对话保留。", dismiss: "仅用于原对话，不作为跨对话知识加载。", delete: "知识或偏好已删除，后续提问不再加载；原对话和历史引用保留。" })[action]);
      await refresh();
      return true;
    } catch (failure) { setWriteError(failure instanceof Error ? failure.message : "知识操作未确认保存，请重新读取核对"); return false; }
    finally { operation.current = false; if (alive.current) setWorkingId(""); }
  };
  const create = async (draft: KnowledgeDraft, requestId: string) => {
    if (operation.current) throw new Error("请等待当前知识操作完成");
    operation.current = true; setWorkingId("new"); setWriteError(""); setNotice("");
    try { const item = await createKnowledge(draft, requestId); received(item);
      setNotice("记录已保存为待确认，点击确认记住后才会应用。"); await refresh(); return item;
    } catch (failure) { setWriteError(failure instanceof Error ? failure.message : "记录未确认保存，输入内容仍保留"); throw failure; }
    finally { operation.current = false; if (alive.current) setWorkingId(""); }
  };
  const save = async (item: KnowledgeEntry, draft: KnowledgeDraft) => {
    if (operation.current) throw new Error("请等待当前知识操作完成");
    operation.current = true; setWorkingId(item.id); setWriteError(""); setNotice("");
    try { received(await editKnowledge(item, draft)); setNotice("修改已保存为待确认。确认前暂不应用。"); await refresh(); }
    catch (failure) { setWriteError(failure instanceof Error ? failure.message : "修改未确认保存，输入内容仍保留"); throw failure; }
    finally { operation.current = false; if (alive.current) setWorkingId(""); }
  };
  const propose = async (chatId: string, answerId: string) => {
    if (operation.current) return;
    operation.current = true; setWorkingId(answerId); setWriteError(""); setNotice("");
    try { received(await proposeKnowledge(chatId, answerId)); setNotice("已从你的原话整理候选，请核对、编辑并确认；尚未生效。"); await refresh(); }
    catch (failure) { setWriteError(failure instanceof Error ? failure.message : "候选未能整理，原对话仍保留"); }
    finally { operation.current = false; if (alive.current) setWorkingId(""); }
  };
  const reload = useCallback(async () => { setWriteError(""); await refresh(); }, [refresh]);
  return { collection, error: writeError || readError, loading, workingId,
    notice: noticeValue?.context === contextKey ? noticeValue.text : "", refresh: reload, action, create, save, propose };
}
