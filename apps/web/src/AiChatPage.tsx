import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { FY27Header } from "./FY27Header";
import { Xiaoyu } from "./Xiaoyu";
import { forgetXiaoyuHelp, readXiaoyuHelp, readXiaoyuHelpDisplay, xiaoyuHelpPrompt, xiaoyuHelpQuestion } from "./xiaoyuHelpHandoff";
import { xiaoyuHelpState, xiaoyuHelpTopic } from "./xiaoyuHelpTopics";
import "./xiaoyuHelp.css";
import { aiBudgetContextPrompt, readAiBudgetContext } from "./aiBudgetContext";
import { getAiAgentStatus, type AiAgentStatus } from "./aiAgentApi";
import { chatIsBusy, chatMarkdown, chatRequestId, createChat, getChat, listChats, readChatResult, sendChat, stopChat, updateChat,
  type ChatContext, type ChatConversation, type ChatSummary } from "./aiChatApi";
import { AiChatMessage, chatSafeUrl } from "./AiChatMessage";
import { chatTaskLabel, mergeChatHistory, newChatResults } from "./aiChatActivity";
import { KnowledgeCard, KnowledgePanel } from "./AiKnowledge";
import { useAiKnowledge } from "./useAiKnowledge";
import type { KnowledgeSnapshot, KnowledgeSource } from "./aiKnowledgeApi";
import "./aiChat.css";

function Icon({ name }: { name: "add" | "sidebar" | "send" | "stop" | "search" | "more" | "pin" }) {
  const paths = { add: "M12 5v14M5 12h14", sidebar: "M9 3v18M5 3h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2Z",
    send: "M12 19V5M5 12l7-7 7 7", stop: "M7 7h10v10H7Z", search: "m16 16 4 4M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0Z",
    more: "M5 12h.01M12 12h.01M19 12h.01", pin: "m8 3 8 0-1 6 3 4H6l3-4-1-6ZM12 13v8" };
  return <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[name]}/></svg>;
}
const sorted = (items: ChatSummary[]) => [...items].sort((a, b) => Number(b.pinned) - Number(a.pinned) || b.updated_at.localeCompare(a.updated_at));
export function chatHistoryGroups(items: ChatSummary[], current = new Date()) {
  const day = new Date(current); day.setHours(0, 0, 0, 0);
  const groups: Record<string, ChatSummary[]> = { "置顶": [], "今天": [], "最近7天": [], "更早": [] };
  for (const item of sorted(items)) {
    const time = new Date(item.updated_at).getTime();
    groups[item.pinned ? "置顶" : time >= day.getTime() ? "今天" : time >= day.getTime() - 7 * 86_400_000 ? "最近7天" : "更早"].push(item);
  }
  return Object.entries(groups).filter(([, values]) => values.length);
}
const initialId = () => { const value = typeof location === "undefined" ? null : new URLSearchParams(location.search).get("chat"); return value && /^[a-f0-9]{32}$/.test(value) ? value : null; };

export function AiChatPage() {
  const [helpEntry] = useState(() => readXiaoyuHelp(typeof location === "undefined" ? "" : location.search));
  const context = useMemo(() => helpEntry.status === "ready" ? helpEntry.budget : readAiBudgetContext(typeof location === "undefined" ? "" : location.search), [helpEntry]);
  const help = helpEntry.status === "ready" ? helpEntry.handoff : null;
  const newContext: ChatContext | null = context ? { label: `${context.moduleLabel} · ${context.versionLabel}${help ? ` · ${xiaoyuHelpTopic(help.topicId)!.title}` : ""}`,
    scope: context.scopeLabel, href: context.sourceHref, prompt: help ? xiaoyuHelpPrompt(help, context) : aiBudgetContextPrompt(context) } : null;
  const [status, setStatus] = useState<AiAgentStatus | null>(null), [loading, setLoading] = useState(true);
  const [chats, setChats] = useState<ChatSummary[]>([]), [selectedId, setSelectedId] = useState<string | null>(initialId);
  const [record, setRecord] = useState<ChatConversation | null>(null), [reading, setReading] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(() => typeof window === "undefined" || window.innerWidth >= 960);
  const [search, setSearch] = useState(""), [archived, setArchived] = useState(false), [draft, setDraft] = useState(() => help ? xiaoyuHelpQuestion(help) : context?.module === "cockpit" ? "请分析驾驶舱当前范围的预算缺口、全年预计和下一年预算增长，指出值得继续下钻的业务，区分事实与待核对的业务假设。" : "");
  const [error, setError] = useState(""), [readError, setReadError] = useState(""), [historyError, setHistoryError] = useState(""), [notice, setNotice] = useState("");
  const [sending, setSending] = useState(false), [stopping, setStopping] = useState(false), [menu, setMenu] = useState(false);
  const [renaming, setRenaming] = useState(false), [title, setTitle] = useState(""), [reload, setReload] = useState(0);
  const [nearBottom, setNearBottom] = useState(true);
  const [notificationsOpen, setNotificationsOpen] = useState(false), [arrival, setArrival] = useState<ChatSummary | null>(null);
  const [visibilityRevision, setVisibilityRevision] = useState(0);
  const [knowledgeOpen, setKnowledgeOpen] = useState(false), [knowledgeView, setKnowledgeView] = useState<"manage" | "snapshot">("manage");
  const [knowledgeEditing, setKnowledgeEditing] = useState<string | null>(null), [knowledgeSnapshot, setKnowledgeSnapshot] = useState<KnowledgeSnapshot | null>(null);
  const knowledge = useAiKnowledge(!loading && Boolean(status), record?.messages.map(message => message.knowledge_proposal_id || "").join(",") || "", selectedId || "new");
  const selected = useRef(selectedId), editor = useRef<HTMLTextAreaElement>(null), messages = useRef<HTMLDivElement>(null);
  const drafts = useRef(new Map<string, string>()), newId = useRef("");
  const composing = useRef(false), operation = useRef(false), alive = useRef(true), atBottom = useRef(true);
  const pending = useRef<{ id: string; text: string; chatId: string } | null>(null);
  const historyItems = useRef<ChatSummary[]>([]), acknowledging = useRef(new Set<string>());

  useEffect(() => {
    const fold = () => { if (knowledgeOpen && window.innerWidth < 1280) setSidebarOpen(false);
      if (knowledgeOpen && window.innerWidth < 960) { setNotificationsOpen(false); setArrival(null); } };
    fold(); window.addEventListener("resize", fold); return () => window.removeEventListener("resize", fold);
  }, [knowledgeOpen]);

  const ingest = useCallback((items: ChatSummary[], notify = false) => {
    const previous = historyItems.current;
    const visible = typeof document !== "undefined" && document.visibilityState === "visible" && atBottom.current ? selected.current : null;
    const results = notify ? newChatResults(previous, items, visible) : [];
    historyItems.current = mergeChatHistory(previous, items);
    setChats(historyItems.current);
    if (results.length) setArrival(results[results.length - 1]);
  }, []);
  const upsert = useCallback((next: ChatSummary) => ingest([next], true), [ingest]);
  const load = useCallback(async () => {
    setLoading(true); setHistoryError("");
    try {
      const nextStatus = await getAiAgentStatus();
      if (!alive.current) return;
      setStatus(nextStatus);
      const items = await listChats();
      if (alive.current) ingest(items);
    } catch (failure) { if (alive.current) setHistoryError(failure instanceof Error ? failure.message : "历史对话未能读取"); }
    finally { if (alive.current) setLoading(false); }
  }, [ingest]);
  useEffect(() => { alive.current = true; void load(); return () => { alive.current = false; }; }, [load]);
  useEffect(() => {
    if (loading || !status) return;
    const controller = new AbortController(); let timer: ReturnType<typeof setTimeout> | undefined, inFlight = false;
    const refreshHistory = async () => {
      if (controller.signal.aborted || inFlight) return;
      clearTimeout(timer); inFlight = true;
      let delay = 5_000;
      try {
        const items = await listChats(controller.signal);
        if (controller.signal.aborted) return;
        ingest(items, true); setHistoryError("");
        if (items.some(item => chatIsBusy(item.state))) delay = 1_000;
      } catch (failure) {
        if (!controller.signal.aborted) setHistoryError(failure instanceof Error ? failure.message : "对话进度同步中断，请重新连接");
      } finally {
        inFlight = false;
        if (!controller.signal.aborted) timer = setTimeout(() => { void refreshHistory(); }, document.visibilityState === "hidden" ? 8_000 : delay);
      }
    };
    const focus = () => { if (document.visibilityState === "visible") { setVisibilityRevision(value => value + 1); void refreshHistory(); } };
    void refreshHistory();
    window.addEventListener("focus", focus);
    document.addEventListener("visibilitychange", focus);
    return () => { controller.abort(); clearTimeout(timer); window.removeEventListener("focus", focus); document.removeEventListener("visibilitychange", focus); };
  }, [loading, Boolean(status), ingest]);

  useEffect(() => {
    if (!selectedId || loading) return;
    const controller = new AbortController(); let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      let delay = 800;
      try {
        const next = await getChat(selectedId, controller.signal);
        if (controller.signal.aborted || selected.current !== selectedId) return;
        setRecord(previous => previous?.id === next.id && (previous.revision > next.revision || previous.revision === next.revision && previous.queue_position === next.queue_position) ? previous : next);
        setReading(false); setReadError(""); upsert(next);
        delay = chatIsBusy(next.state) ? 800 : 10_000;
      } catch (failure) {
        if (!controller.signal.aborted && selected.current === selectedId) {
          setReading(false); setReadError(failure instanceof Error ? failure.message : "对话未能读取");
        }
        delay = 10_000;
      }
      if (!controller.signal.aborted) timer = setTimeout(() => { void poll(); }, delay);
    };
    void poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [selectedId, loading, reload, upsert]);

  useEffect(() => {
    if (!editor.current) return;
    editor.current.style.height = "auto";
    editor.current.style.height = `${Math.min(200, editor.current.scrollHeight)}px`;
    drafts.current.set(selectedId || "new", draft);
  }, [draft, selectedId]);
  useEffect(() => {
    if (atBottom.current && messages.current) messages.current.scrollTop = messages.current.scrollHeight;
  }, [record?.revision]);
  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => { if ([...drafts.current.values()].some(value => value.trim())) event.preventDefault(); };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, []);
  useEffect(() => {
    const sync = () => {
      const id = initialId(); selected.current = id; setSelectedId(id); setRecord(null); setError(""); setReadError("");
      setDraft(drafts.current.get(id || "new") || ""); setReading(Boolean(id));
    };
    window.addEventListener("popstate", sync);
    return () => window.removeEventListener("popstate", sync);
  }, []);

  const select = (id: string | null) => {
    drafts.current.set(selected.current || "new", draft);
    if (!id) { setArchived(false); setSearch(""); }
    selected.current = id; setSelectedId(id); setRecord(null); setReading(Boolean(id)); setError(""); setReadError(""); setNotice("");
    setMenu(false); setRenaming(false); setDraft(drafts.current.get(id || "new") || "");
    atBottom.current = true; setNearBottom(true);
    const url = new URL(location.href); if (id) url.searchParams.set("chat", id); else url.searchParams.delete("chat");
    history.pushState({}, "", `${url.pathname}${url.search}`);
    if (window.innerWidth < 960) setSidebarOpen(false);
  };
  const apply = (next: ChatConversation) => { upsert(next); if (selected.current === next.id) setRecord(previous => previous?.id === next.id && previous.revision > next.revision ? previous : next); };
  useEffect(() => {
    const completion = record?.completion;
    if (!record || !record.unread || !completion || reading || !nearBottom || document.visibilityState !== "visible") return;
    const key = `${record.id}:${completion.id}`;
    if (acknowledging.current.has(key)) return;
    acknowledging.current.add(key);
    void readChatResult(record.id, completion.id).then(next => {
      if (!alive.current) return;
      apply(next);
      setArrival(previous => previous?.id === next.id && previous.completion?.id === completion.id ? null : previous);
    }).catch(() => { /* Leave the unread marker on the server for reconnection. */ })
      .finally(() => acknowledging.current.delete(key));
  }, [record?.id, record?.revision, nearBottom, reading, visibilityRevision, upsert]);
  const unread = chats.filter(item => item.unread && item.completion);
  useEffect(() => {
    const original = document.title;
    if (unread.length) document.title = `(${unread.length}) AI智解 · 有新结果`;
    return () => { document.title = original; };
  }, [unread.length]);
  const openResult = (item: ChatSummary) => { setSearch(""); setArchived(item.archived); setNotificationsOpen(false); setArrival(null); select(item.id); };
  const toggleNotifications = () => {
    if (!notificationsOpen && window.innerWidth < 960) { setSidebarOpen(false); setArrival(null); setKnowledgeOpen(false); }
    setNotificationsOpen(!notificationsOpen);
  };
  const toggleSidebar = () => {
    if (!sidebarOpen && window.innerWidth < 1280) { setNotificationsOpen(false); setKnowledgeOpen(false); }
    setSidebarOpen(!sidebarOpen);
  };
  const send = async () => {
    const text = draft.trim();
    if (!text || operation.current || !status?.available || chatIsBusy(record?.state) || record?.needs_stop || record?.archived || reading || selectedId && !record) return;
    operation.current = true; setSending(true); setError(""); setNotice("");
    const origin = selected.current;
    let id = origin;
    try {
      if (!id) {
        if (!newId.current) newId.current = chatRequestId();
        const created = await createChat(newId.current, newContext);
        if (helpEntry.status === "ready") forgetXiaoyuHelp(helpEntry.token);
        id = created.id; upsert(created);
        if (selected.current === origin) {
          selected.current = id; setSelectedId(id); setRecord(created);
          const url = new URL(location.href); url.searchParams.set("chat", id); url.searchParams.delete("xiaoyuHelp"); history.replaceState({}, "", `${url.pathname}${url.search}`);
        }
      }
      const attempt = pending.current?.chatId === id && pending.current.text === text ? pending.current : { id: chatRequestId(), text, chatId: id };
      pending.current = attempt;
      const next = await sendChat(id, text, attempt.id);
      if (!alive.current) return;
      apply(next); pending.current = null; newId.current = "";
      drafts.current.delete("new"); drafts.current.delete(id);
      if (selected.current === id) { setDraft(""); atBottom.current = true; setNearBottom(true); setReload(value => value + 1); }
    } catch (failure) {
      if (!alive.current) return;
      // A lost HTTP response is ambiguous. Read back by request ID; never auto-submit again.
      let accepted = false;
      if (id && pending.current?.chatId === id) {
        try {
          const next = await getChat(id); accepted = next.messages.some(message => message.id === pending.current?.id);
          apply(next);
          if (accepted && selected.current === id) { setDraft(""); drafts.current.delete(id); drafts.current.delete("new"); setReload(value => value + 1); }
        } catch { /* Keep the editor and request ID available for a deliberate retry. */ }
      }
      if (accepted) { pending.current = null; setNotice("消息已接收，正在读取回答"); }
      else setError(failure instanceof Error ? failure.message : "消息未确认发送，输入内容仍保留，请重试");
    } finally { operation.current = false; if (alive.current) setSending(false); }
  };
  const stop = async () => {
    if (!selectedId || stopping) return;
    setStopping(true); setError("");
    try { apply(await stopChat(selectedId)); }
    catch (failure) { setError(failure instanceof Error ? failure.message : "停止状态尚未确认，请重试"); }
    finally { setStopping(false); }
  };
  const change = async (values: Parameters<typeof updateChat>[1]) => {
    if (!record) return;
    setError("");
    try { const next = await updateChat(record.id, values); apply(next); setRenaming(false); setMenu(false); }
    catch (failure) { setError(failure instanceof Error ? failure.message : "对话设置未保存"); }
  };
  const conversationTitle = record ? chats.find(item => item.id === record.id && item.revision > record.revision)?.title ?? record.title : undefined;
  const exportChat = () => {
    if (!record) return;
    const blob = new Blob([chatMarkdown({ ...record, title: conversationTitle || record.title })], { type: "text/markdown;charset=utf-8" });
    const href = URL.createObjectURL(blob), anchor = document.createElement("a");
    anchor.href = href; anchor.download = `${(conversationTitle || record.title).replace(/[\\/:*?"<>|]/g, "-").slice(0,60)}.md`; anchor.click();
    setTimeout(() => URL.revokeObjectURL(href), 1000); setNotice("已导出当前对话");
  };
  const shown = chats.filter(item => item.archived === archived && `${item.title} ${item.context?.label || ""}`.toLowerCase().includes(search.toLowerCase()));
  const runningCount = chats.filter(item => ["preparing", "running", "stopping"].includes(item.state)).length;
  const queuedCount = chats.filter(item => item.state === "queued").length;
  const model = status?.models.find(item => `${item.provider}/${item.id}` === status.default_model || item.id === status.default_model)?.name || status?.default_model || "模型待连接";
  const busy = chatIsBusy(record?.state) || Boolean(record?.needs_stop), activeContext = record?.context ?? (!selectedId ? newContext : null);
  const helpDisplay = activeContext ? readXiaoyuHelpDisplay(activeContext.prompt) : null;
  const hasConnectionProblem = Boolean(error || readError || historyError || (!loading && status && !status.available));
  const latestKnowledge = [...(record?.messages ?? [])].reverse().find(message => message.knowledge)?.knowledge ?? null;
  const openKnowledge = (view: "manage" | "snapshot" = "manage", editing: string | null | undefined = undefined, snapshot: KnowledgeSnapshot | null = latestKnowledge) => {
    setKnowledgeOpen(true); setKnowledgeView(view); setKnowledgeSnapshot(snapshot); if (editing !== undefined) setKnowledgeEditing(editing);
    if (window.innerWidth < 1280) setSidebarOpen(false);
    if (window.innerWidth < 960) { setNotificationsOpen(false); setArrival(null); }
    void knowledge.refresh();
  };
  const openKnowledgeSource = (source: KnowledgeSource) => {
    if (!source.conversation_id) return;
    const origin = chats.find(item => item.id === source.conversation_id);
    if (origin) setArchived(origin.archived);
    select(source.conversation_id);
  };
  const suggestions = ["帮我梳理已保存预算的收入结构和招生人次。", "全年预测的结转收入缺口主要来自哪些业务？", "下一年预算距离经营目标还差多少，可以从哪些参数入手？"];

  return <div className="decision-shell ai-chat-shell">
    <a className="skip-link" href="#ai-chat-content">跳到对话内容</a>
    <FY27Header active="ai" moduleFiscalYears={context ? { [context.module]: context.fiscalYear } : undefined}><span className="ai-chat-header-model">{model}</span></FY27Header>
    <div className={`ai-chat-layout ${sidebarOpen ? "" : "is-sidebar-closed"} ${knowledgeOpen ? "is-knowledge-open" : ""}`}>
      <aside className="ai-chat-sidebar" hidden={!sidebarOpen} aria-label="对话历史">
        <div className="ai-chat-sidebar-top"><strong>AI智解</strong><button className="ai-chat-icon" aria-label="收起对话列表" onClick={() => setSidebarOpen(false)}><Icon name="sidebar"/></button></div>
        <button className="ai-chat-new" onClick={() => select(null)}><Icon name="add"/>新对话</button>
        <label className="ai-chat-search"><Icon name="search"/><input aria-label="搜索对话标题" value={search} onChange={event => setSearch(event.target.value)} placeholder="搜索对话"/></label>
        <div className="ai-chat-history-tabs" aria-label="历史对话范围"><button aria-pressed={!archived} onClick={() => setArchived(false)}>最近对话</button><button aria-pressed={archived} onClick={() => setArchived(true)}>已归档</button></div>
        {(runningCount > 0 || queuedCount > 0) && <p className="ai-chat-task-count" role="status">本浏览器：{runningCount}段处理中{queuedCount > 0 ? `，${queuedCount}段排队` : ""}</p>}
        <nav className="ai-chat-history-list" aria-label="已保存对话">
          {loading ? <p role="status">正在读取 AI 状态与对话…</p> : shown.length ? chatHistoryGroups(shown).map(([label, items]) => <section key={label}><h2>{label}</h2>{items.map(item => <button key={item.id} title={item.title} onClick={() => select(item.id)} aria-current={selectedId === item.id ? "page" : undefined} aria-label={`打开对话：${item.title}`}>
            <span className="ai-chat-history-entry"><span className="ai-chat-history-title">{item.title}</span><small><span className={`ai-chat-task-state is-${item.state}`}>{chatTaskLabel(item)}</span><span>{item.pinned && <Icon name="pin"/>}{new Date(item.updated_at).toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" })}</span></small></span>{item.unread && <span className="ai-chat-unread">未读</span>}
          </button>)}</section>) : <p>{search ? "没有匹配的对话标题" : archived ? "暂无归档对话" : "你的对话会保存在这里"}</p>}
        </nav>
        <footer><button className="ai-knowledge-sidebar-link" aria-expanded={knowledgeOpen} onClick={() => openKnowledge()}>业务知识与偏好</button><span>本机保存 · 当前浏览器</span><details><summary>记录与数据说明</summary><p>每段新对话自动保存，可回看、继续提问或归档。清除浏览器数据后可能无法找回身份。原终端记录仍保留，不将归属不明的旧记录混入此列表。预算数据仅可读取，不会自动保存或修改预算。</p></details></footer>
      </aside>
      <main id="ai-chat-content" className="ai-chat-main">
        <header className="ai-chat-heading">
          <div><button className="ai-chat-icon" aria-label={sidebarOpen ? "收起对话列表" : "展开对话列表"} aria-expanded={sidebarOpen} onClick={toggleSidebar}><Icon name="sidebar"/></button><h1 title={conversationTitle}>{conversationTitle || (selectedId ? reading ? "正在读取对话" : "对话暂不可用" : "新对话")}</h1></div>
          <div>{record && <span className="ai-chat-save-state" role="status">{record.archived ? "已归档" : chatTaskLabel(record)}</span>}<button className="ai-knowledge-toggle" aria-expanded={knowledgeOpen} onClick={() => knowledgeOpen ? setKnowledgeOpen(false) : openKnowledge()}>知识与偏好</button><button className="ai-chat-notification-toggle" aria-expanded={notificationsOpen} onClick={toggleNotifications}>通知{unread.length ? ` ${unread.length}` : ""}</button><button className="ai-chat-icon" aria-label="对话操作" aria-expanded={menu} disabled={!record} onClick={() => setMenu(value => !value)}><Icon name="more"/></button></div>
        </header>
        {arrival && <div className="ai-chat-arrival" role="status">{!hasConnectionProblem && <Xiaoyu pose={arrival.completion?.state === "idle" ? "done" : "notice"}/>}<div><strong>{arrival.completion?.state === "idle" ? "后台回答已完成" : "后台任务需要查看"}</strong><span>{arrival.title}</span></div><button onClick={() => openResult(arrival)}>查看结果</button><button aria-label="收起本次提醒，保留未读" onClick={() => setArrival(null)}>收起</button></div>}
        {notificationsOpen && <section className="ai-chat-notifications" aria-label="未读结果"><h2>未读结果</h2>{unread.length ? <ul>{unread.map(item => <li key={item.id}><button onClick={() => openResult(item)}><span>{item.title}</span><small>{chatTaskLabel(item)} · {new Date(item.completion!.at).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}</small></button></li>)}</ul> : <p>没有未读结果。后台任务完成后会在这里提醒。</p>}</section>}
        {menu && record && <div className="ai-chat-options" aria-label="对话操作"><small>更新于 {new Date(record.updated_at).toLocaleString("zh-CN")}</small><button onClick={() => { setRenaming(true); setTitle(conversationTitle || record.title); }}>重命名</button><button onClick={() => { void change({ pinned: !record.pinned }); }}>{record.pinned ? "取消置顶" : "置顶对话"}</button><button onClick={exportChat}>导出对话</button><button disabled={busy} onClick={() => { void change({ archived: !record.archived }); }}>{record.archived ? "恢复对话" : "归档对话"}</button></div>}
        {renaming && <form className="ai-chat-rename" onSubmit={event => { event.preventDefault(); void change({ title: title.trim() }); }}><label>对话名称<input autoFocus maxLength={80} value={title} onChange={event => setTitle(event.target.value)}/></label><button type="submit" disabled={!title.trim()}>保存名称</button><button type="button" onClick={() => setRenaming(false)}>取消</button></form>}
        {helpEntry.status === "unavailable" && !selectedId && <div className="ai-chat-error" role="alert">{helpEntry.reason}</div>}
        {activeContext && <details key={selectedId || "new-source"} className="ai-chat-source"><summary><span>引用来源</span><strong>{activeContext.label}</strong></summary><p>{activeContext.scope}</p>{chatSafeUrl(activeContext.href) && <a href={activeContext.href} target="_blank" rel="noreferrer">打开来源页面</a>}
          {activeContext.prompt.startsWith("【小予讲解｜") ? <div className="ai-chat-help-context"><strong>已带入小予讲解上下文</strong><p>{record ? "这里保留的是提问时的页面快照，后续预算调整不会自动同步到这段对话。" : "问题已准备好，点击发送后才会调用 AI。原预算页及未保存修改仍保留。"}</p>
              {helpDisplay && <details><summary>查看带入的页面状态与修改摘要</summary><p>{xiaoyuHelpState(helpDisplay.snapshot)}</p><p>编制基准：{helpDisplay.snapshot.basisLabel} · 数据截至 {helpDisplay.snapshot.basisAsOf || "待核对"}</p><p>采集于 {new Date(helpDisplay.capturedAt).toLocaleString("zh-CN")}</p>
                {helpDisplay.snapshot.trackTargetAllocation && <><p>分配用全年目标（元）：{helpDisplay.snapshot.trackTargetAllocation.annualTargetYuan || "未填写"}</p><p>收入权重来源：{helpDisplay.snapshot.trackTargetAllocation.weightVersionLabel}已保存的测算结果，覆盖全部赛道。</p><ul>{helpDisplay.snapshot.trackTargetAllocation.weights.map(row => <li key={row.track}>{row.track}测算收入（元）：{row.revenueYuan}</li>)}</ul></>}
                {helpDisplay.snapshot.changes.length > 0 ? <ul>{helpDisplay.snapshot.changes.map((change, index) => <li key={index}>{change.label}：{change.before} → {change.after}</li>)}</ul> : <p>没有带入参数改动。</p>}
                <p>修改摘要 {helpDisplay.snapshot.changes.length} / {helpDisplay.snapshot.changeCount} 项{helpDisplay.snapshot.changes.length < helpDisplay.snapshot.changeCount ? "，未完整带入" : ""}。不包含整张预算表和预览计算结果。</p></details>}</div>
            : <p>仅包含已保存的版本与范围，不包含其他页面未保存的改动。</p>}</details>}
        {latestKnowledge && <div className="ai-knowledge-applied"><button onClick={() => openKnowledge("snapshot")}>本轮已加载 {latestKnowledge.items.length} 条口径与偏好 · 查看</button></div>}
        {knowledge.error && !knowledgeOpen && <div className="ai-chat-error" role="alert"><span>{knowledge.error}</span><button onClick={() => openKnowledge()}>查看知识</button></div>}
        {(error || readError || historyError) && <div className="ai-chat-error xiaoyu-feedback" role="alert"><Xiaoyu pose="notice"/><span>{error || readError || historyError}</span><button onClick={() => { setError(""); setReadError(""); setReload(value => value + 1); void load(); }}>重新连接</button></div>}
        {!loading && status && !status.available && <div className="ai-chat-error xiaoyu-feedback" role="status">{!(error || readError || historyError) && <Xiaoyu pose="notice"/>}<span>{status.reason || "AI运行环境暂不可用"}。历史对话仍可回看，输入内容会保留。</span></div>}
        <div ref={messages} className="ai-chat-messages" aria-label="对话消息" onScroll={event => { const node = event.currentTarget; const near = node.scrollHeight - node.scrollTop - node.clientHeight < 100; atBottom.current = near; setNearBottom(near); }}>
          {reading && !record ? <p className="ai-chat-reading xiaoyu-feedback" role="status">{!hasConnectionProblem && !arrival && <Xiaoyu pose="focus"/>}<span>正在读取这段对话…</span></p> : record?.messages.length ? <div className="ai-chat-transcript">{record.messages.map(message => { const candidate = knowledge.collection?.items.find(item => item.id === message.knowledge_proposal_id); return <div key={message.id}>
            <AiChatMessage message={message} showCompanion={!hasConnectionProblem && !arrival}/>
            {candidate ? <KnowledgeCard item={candidate} working={Boolean(knowledge.workingId) || Boolean(knowledge.error)} onAction={knowledge.action} onEdit={id => openKnowledge("manage", id)}/>
              : message.knowledge_proposal_id ? <p className="ai-knowledge-inline-note">候选知识状态待同步。<button className="ai-knowledge-text-action" onClick={() => openKnowledge()}>查看知识</button></p> : null}
            {message.knowledge_notice && !candidate && <p className="ai-knowledge-inline-note">{message.knowledge_notice}</p>}
            {message.role === "assistant" && message.state === "complete" && !message.knowledge_proposal_id && <button className="ai-knowledge-text-action" disabled={busy || Boolean(knowledge.workingId)} onClick={() => { if (record) void knowledge.propose(record.id, message.id).then(() => setReload(value => value + 1)); }}>记住这次纠正</button>}
            {message.knowledge && <button className="ai-knowledge-text-action" onClick={() => openKnowledge("snapshot", undefined, message.knowledge!)}>查看本条回答的口径依据</button>}
          </div>; })}</div> : !selectedId || record ? <div className="ai-chat-empty">{!hasConnectionProblem && !arrival && <Xiaoyu pose={help ? "focus" : "hello"} size="welcome"/>}<h2>{help ? `先把“${xiaoyuHelpTopic(help.topicId)!.title}”弄明白。` : "把预算问题，聊清楚。"}</h2><p>{help ? "问题和上下文都准备好了，可以直接发送，也可以改写。" : "我是小予，陪你看结构、查差异、找下一步。"}</p>{!help && <div className="ai-chat-suggestions">{suggestions.map((question, index) => <button key={question} onClick={() => { setDraft(question); editor.current?.focus(); }}><span>{["看预算结构", "找预测缺口", "拆经营目标"][index]}</span><small>{question}</small></button>)}</div>}<p className="ai-chat-empty-note">先准备问题，点击发送后才会调用AI。</p></div> : null}
        </div>
        {!nearBottom && <button className="ai-chat-latest" onClick={() => { if (messages.current) messages.current.scrollTop = messages.current.scrollHeight; atBottom.current = true; setNearBottom(true); }}>回到最新消息</button>}
        <div className="ai-chat-composer-wrap"><div className="ai-chat-composer">
          <label className="sr-only" htmlFor="ai-chat-input">向AI智解提问</label><textarea id="ai-chat-input" ref={editor} value={draft} maxLength={32_000} placeholder={record?.archived ? "此对话已归档，恢复后可继续提问" : "问一个预算问题，也可以继续追问…"} rows={2} disabled={record?.archived} enterKeyHint="send"
            onChange={event => setDraft(event.target.value)} onCompositionStart={() => { composing.current = true; }} onCompositionEnd={() => { composing.current = false; }}
            onKeyDown={event => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing && !composing.current && event.keyCode !== 229) { event.preventDefault(); void send(); } }}/>
          <div className="ai-chat-composer-tools"><span title="使用现有Pi模型配置">{model}</span><div>{busy || stopping ? <button className="ai-chat-send" aria-label={record?.state === "queued" ? "取消排队" : "停止回答"} disabled={stopping} onClick={() => { void stop(); }}><Icon name="stop"/></button> : <button className="ai-chat-send" aria-label="发送消息" disabled={!draft.trim() || sending || reading || loading || !status?.available || record?.archived || Boolean(selectedId && !record)} onClick={() => { void send(); }}><Icon name="send"/></button>}</div></div>
        </div><div className="ai-chat-composer-note"><span>预算只读 · AI回答请核对来源</span><span className="ai-chat-key-hint">Enter发送，Shift+Enter换行</span></div><p className="ai-chat-notice" role="status">{knowledge.notice || notice}</p></div>
      </main>
      <KnowledgePanel open={knowledgeOpen} collection={knowledge.collection} error={knowledge.error} loading={knowledge.loading} workingId={knowledge.workingId}
        editingId={knowledgeEditing} snapshot={knowledgeSnapshot} view={knowledgeView} onClose={() => { setKnowledgeOpen(false); document.querySelector<HTMLButtonElement>(".ai-knowledge-toggle")?.focus(); }} onView={setKnowledgeView}
        onRefresh={() => { void knowledge.refresh(); }} onEditing={setKnowledgeEditing} onAction={knowledge.action} onCreate={knowledge.create} onSave={knowledge.save} onSource={openKnowledgeSource}/>
    </div>
  </div>;
}
