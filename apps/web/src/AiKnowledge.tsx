import { useEffect, useRef, useState } from "react";
import { chatRequestId } from "./aiChatApi";
import { knowledgeDate, knowledgeModuleLabel, type KnowledgeCollection, type KnowledgeDraft,
  type KnowledgeEntry, type KnowledgeSnapshot, type KnowledgeSource } from "./aiKnowledgeApi";
import "./aiKnowledge.css";

type Action = "approve" | "disable" | "dismiss" | "delete";
type EditingState = { base: KnowledgeEntry; draft: KnowledgeDraft; keywords: string; year: string };
type NewState = Omit<EditingState, "base"> & { requestId: string };
export type KnowledgeAction = (item: KnowledgeEntry, action: Action, replace?: boolean) => Promise<boolean>;

export function KnowledgeCard({ item, working, onAction, onEdit }: {
  item: KnowledgeEntry; working: boolean; onAction: KnowledgeAction; onEdit: (id: string) => void;
}) {
  if (item.state === "deleted" || item.state === "dismissed") return <p className="ai-knowledge-inline-note">
    {item.state === "deleted" ? "这条候选已移除，原对话保留。" : "这次纠正仅用于原对话，不作为跨对话知识加载。"}</p>;
  const pending = item.state === "pending";
  return <><section className="ai-knowledge-card" aria-label={`记忆口径：${item.title}`}>
    <div className="ai-knowledge-card-label"><span>{item.kind === "preference" ? "建议记住的个人偏好" : "建议记住的业务口径"}</span>
      <span className={`ai-knowledge-state is-${item.state}`}>{({ pending: "待确认", active: "已保存", disabled: "已停用" })[item.state]}</span></div>
    <h3>{item.title}</h3><p>{item.content}</p>
    <dl><div><dt>适用范围</dt><dd>{knowledgeModuleLabel(item.module)}{item.fiscal_year ? ` · FY${String(item.fiscal_year).slice(-2)}` : ""}</dd></div>
      <div><dt>来源</dt><dd>本次对话 · {knowledgeDate(item.created_at)}</dd></div><div><dt>保存范围</dt><dd>当前浏览器</dd></div></dl>
    {pending ? <><div className="ai-knowledge-actions"><button className="ai-knowledge-primary" disabled={working} onClick={() => { void onAction(item, "approve", Boolean(item.conflicts?.length)); }}>
      {working ? "正在保存…" : item.conflicts?.length ? "确认并替换同名口径" : "确认记住"}</button>
      <button disabled={working} onClick={() => onEdit(item.id)}>修改内容</button><button disabled={working} onClick={() => { void onAction(item, "dismiss"); }}>仅用于本对话</button></div>
      {Boolean(item.conflicts?.length) && <p className="ai-knowledge-warning">确认后将停用同名且范围重叠的旧口径：{item.conflicts!.map(conflict => conflict.title).join("、")}。</p>}
      </>
      : <p className="ai-knowledge-inline-note">{item.state === "active" ? "保存成功。后续提问将按相关范围加载，可在右侧查看、修改或停用。" : "后续提问不再加载；原对话和回答时的引用记录仍保留。"}
        <button className="ai-knowledge-text-action" onClick={() => onEdit(item.id)}>管理这条知识</button></p>}
  </section>{pending && <p className="ai-knowledge-inline-note">确认前不会作为长期知识应用，也不会修改预算数据和计算规则。</p>}</>;
}

export function KnowledgePanel({ open, collection, error, loading, workingId, editingId, snapshot, view,
  onClose, onRefresh, onEditing, onAction, onCreate, onSave, onSource, onView }: {
  open: boolean; collection: KnowledgeCollection | null; error: string; loading: boolean; workingId: string;
  editingId: string | null; snapshot: KnowledgeSnapshot | null; view: "manage" | "snapshot";
  onClose: () => void; onRefresh: () => void; onEditing: (id: string | null) => void;
  onAction: KnowledgeAction; onCreate: (draft: KnowledgeDraft, requestId: string) => Promise<KnowledgeEntry>;
  onSave: (item: KnowledgeEntry, draft: KnowledgeDraft) => Promise<void>;
  onSource: (source: KnowledgeSource) => void; onView: (view: "manage" | "snapshot") => void;
}) {
  const [search, setSearch] = useState(""), [detailId, setDetailId] = useState<string | null>(null);
  const [editing, setEditing] = useState<EditingState | null>(null);
  const [creating, setCreating] = useState<NewState | null>(null);
  const editingDrafts = useRef(new Map<string, EditingState>());
  const [removeId, setRemoveId] = useState<string | null>(null);
  const closeButton = useRef<HTMLButtonElement>(null);
  useEffect(() => { if (open) closeButton.current?.focus(); }, [open]);
  useEffect(() => {
    const isChanged = (item: EditingState) => item.draft.title !== item.base.title || item.draft.content !== item.base.content
      || item.draft.kind !== item.base.kind || item.draft.module !== item.base.module
      || item.year !== (item.base.fiscal_year ? String(item.base.fiscal_year) : "") || item.keywords !== item.base.keywords.join("，");
    const changed = Boolean(editing && isChanged(editing)) || [...editingDrafts.current.values()].some(isChanged)
      || Boolean(creating && (creating.draft.title.trim() || creating.draft.content.trim() || creating.year || creating.keywords));
    const warn = (event: BeforeUnloadEvent) => { if (changed) event.preventDefault(); };
    window.addEventListener("beforeunload", warn); return () => window.removeEventListener("beforeunload", warn);
  }, [editing, creating]);
  useEffect(() => {
    if (editing) editingDrafts.current.set(editing.base.id, editing);
    if (!editingId) { if (editing) editingDrafts.current.delete(editing.base.id); setEditing(null); return; }
    const item = collection?.items.find(item => item.id === editingId);
    if (!item || item.state === "deleted" || item.state === "dismissed") return;
    setDetailId(item.id); setRemoveId(null);
    setEditing(editingDrafts.current.get(item.id) ?? { base: item, draft: { title: item.title, content: item.content, kind: item.kind,
      module: item.module, fiscal_year: item.fiscal_year, keywords: item.keywords }, keywords: item.keywords.join("，"), year: item.fiscal_year ? String(item.fiscal_year) : "" });
  }, [editingId]);
  const items = [...(collection?.items ?? []), ...(collection?.builtin ?? [])].filter(item =>
    `${item.title} ${item.content}`.toLocaleLowerCase().includes(search.toLocaleLowerCase()));
  const groups = [["待确认", "pending"], ["已生效", "active"], ["已停用", "disabled"]] as const;
  const update = (values: Partial<KnowledgeDraft>) => setEditing(current => current ? { ...current, draft: { ...current.draft, ...values } } : current);
  return <aside className="ai-knowledge-panel" hidden={!open} aria-label="业务知识与偏好" onKeyDown={event => {
    if (event.key === "Escape" && !event.nativeEvent.isComposing) { event.stopPropagation(); onClose(); }
  }}>
    <header><div><h2>业务知识与偏好</h2><p>管理已记住的口径与个人偏好</p></div><button ref={closeButton} onClick={onClose} className="ai-knowledge-close">关闭</button></header>
    <div className="ai-knowledge-panel-scroll">
      {error && <div className="ai-knowledge-error" role="alert"><p>{error}</p><button onClick={onRefresh} disabled={loading}>重新读取</button></div>}
      {loading && !collection && <p role="status">正在读取知识…</p>}
      {snapshot && <div className="ai-knowledge-views"><button aria-pressed={view === "manage"} onClick={() => onView("manage")}>知识管理</button><button aria-pressed={view === "snapshot"} onClick={() => onView("snapshot")}>本轮已加载</button></div>}
      {view === "snapshot" && snapshot ? <section className="ai-knowledge-snapshot"><h3>回答时的知识快照</h3><p>加载于 {knowledgeDate(snapshot.captured_at)}。记录的是提供给AI的参考，不代表答案已经核验。</p>
        {snapshot.items.map(item => { const current = collection?.items.find(current => current.id === item.id); return <article key={item.id}><h4>{item.title}</h4><p>{item.content}</p>
          <small>{item.builtin ? "回答时的初始预置口径" : `回答时第${item.revision}次修订`}{current && (current.state !== "active" || current.revision !== item.revision) ? " · 当前已停用、修改或删除" : ""}</small>
          {item.source.conversation_id && <button className="ai-knowledge-text-action" onClick={() => onSource(item.source)}>查看来源对话</button>}</article>; })}</section>
        : <><div className="ai-knowledge-manage-tools"><button className="ai-knowledge-add" disabled={!collection || Boolean(workingId)} onClick={() => setCreating(current => current ?? {
          requestId: chatRequestId(), draft: { title: "", content: "", kind: "preference", module: "all", fiscal_year: null, keywords: [] }, year: "", keywords: "",
        })}>新增知识或偏好</button></div>
          {creating && <form className="ai-knowledge-form ai-knowledge-create" aria-label="新增知识或偏好" onSubmit={event => { event.preventDefault();
            const draft = { ...creating.draft, fiscal_year: creating.year.trim() ? Number(creating.year) : null,
              keywords: creating.keywords.split(/[,，、]/).map(word => word.trim()).filter(Boolean) };
            void onCreate(draft, creating.requestId).then(item => { setCreating(null); setSearch(""); setDetailId(item.id); }).catch(() => { /* Keep input and retry identity on errors. */ });
          }}><h3>新增知识或偏好</h3><p>先保存为待确认，再确认记住。只保存长期口径或偏好，不保存临时预算数字。</p>
            <fieldset disabled={Boolean(workingId)}><label>名称<input autoFocus required maxLength={80} value={creating.draft.title} onChange={event => setCreating({ ...creating, draft: { ...creating.draft, title: event.target.value } })}/></label>
              <label>口径或偏好<textarea required rows={4} maxLength={1200} value={creating.draft.content} onChange={event => setCreating({ ...creating, draft: { ...creating.draft, content: event.target.value } })}/></label>
              <label>类别<select value={creating.draft.kind} onChange={event => setCreating({ ...creating, draft: { ...creating.draft, kind: event.target.value as KnowledgeDraft["kind"] } })}><option value="preference">业务偏好</option><option value="business">业务口径</option></select></label>
              <details className="ai-knowledge-create-scope"><summary>适用范围（默认全部预算分析）</summary>
                <label>适用模块<select value={creating.draft.module} onChange={event => setCreating({ ...creating, draft: { ...creating.draft, module: event.target.value as KnowledgeDraft["module"] } })}><option value="all">全部预算分析</option><option value="budget">定版预算</option><option value="forecast">全年预测</option><option value="next-year">预算编制</option></select></label>
                <label>财年（选填，如2028）<input type="number" min={2000} max={2200} value={creating.year} onChange={event => setCreating({ ...creating, year: event.target.value })}/></label>
                <label>关键词（选填，逗号分隔）<input maxLength={200} value={creating.keywords} onChange={event => setCreating({ ...creating, keywords: event.target.value })}/></label>
              </details>
              <div className="ai-knowledge-actions"><button type="submit" className="ai-knowledge-primary" disabled={!creating.draft.title.trim() || !creating.draft.content.trim()}>{workingId === "new" ? "正在保存…" : "保存为待确认"}</button><button type="button" onClick={() => setCreating(null)}>取消新增</button></div>
            </fieldset>
          </form>}
          <label className="ai-knowledge-search"><span className="sr-only">搜索知识与偏好</span><input value={search} onChange={event => setSearch(event.target.value)} placeholder="搜索知识与偏好"/></label>
          {groups.map(([label, state]) => { const entries = items.filter(item => item.state === state); if (!entries.length) return null; return <section key={state} className="ai-knowledge-group"><h3>{label} · {entries.length}条</h3>
            {entries.map(item => <article className="ai-knowledge-entry" key={item.id}>
              <button className="ai-knowledge-entry-heading" aria-expanded={detailId === item.id} onClick={() => { setDetailId(detailId === item.id ? null : item.id); setRemoveId(null); }}>
                <strong>{item.title}</strong><span className={`ai-knowledge-state is-${item.state}`}>{state === "pending" ? "待确认" : state === "active" ? "生效中" : "已停用"}</span></button>
              <div className="ai-knowledge-entry-toolbar"><p className="ai-knowledge-entry-meta">{item.preset || item.builtin ? "初始预置 · 由你管理" : `${item.kind === "preference" ? "业务偏好" : knowledgeModuleLabel(item.module)} · 当前浏览器`}{item.fiscal_year ? ` · FY${String(item.fiscal_year).slice(-2)}` : ""}</p>
                <button className="ai-knowledge-delete" aria-label={`删除：${item.title}`} aria-expanded={removeId === item.id} disabled={Boolean(workingId)} onClick={() => setRemoveId(removeId === item.id ? null : item.id)}>删除</button></div>
              {removeId === item.id && <div className="ai-knowledge-remove" role="group" aria-label={`确认删除：${item.title}`}><p>删除此条知识或偏好及其修订记录？删除后不再加载，原对话和历史回答中的引用仍保留。</p><button disabled={Boolean(workingId)} onClick={() => { void onAction(item, "delete").then(success => { if (success) {
                setRemoveId(null); setDetailId(null); editingDrafts.current.delete(item.id); if (editingId === item.id) onEditing(null);
              } }); }}>确认删除</button><button disabled={Boolean(workingId)} onClick={() => setRemoveId(null)}>取消</button></div>}
              {detailId === item.id && <div className="ai-knowledge-entry-detail"><p>{item.content}</p>
                <small>{item.preset && item.revision === 1 ? "系统初始预置" : item.approved_at ? "本人确认" : "待本人确认"} · {knowledgeDate(item.approved_at || item.created_at)}</small>
                {item.source.excerpt && <details><summary>查看来源原话</summary><blockquote>{item.source.excerpt}</blockquote><small>{item.source.label}</small>{item.source.conversation_id && <button className="ai-knowledge-text-action" onClick={() => onSource(item.source)}>打开来源对话</button>}</details>}
                {item.history.length > 0 && <details><summary>修订记录 · {item.history.length}次</summary><ol>{[...item.history].reverse().map((event, index) => <li key={index}><small>{knowledgeDate(event.at)} · 第{event.revision}次修订 · {({ edit: "编辑前", approve: "确认前", disable: "停用前", replaced: "被替换前" } as Record<string,string>)[event.action] || "修改前"}</small><p>{event.content}</p></li>)}</ol></details>}
                <div className="ai-knowledge-row-actions">
                  {(state === "pending" || state === "disabled") && <button className="ai-knowledge-primary" disabled={Boolean(workingId) || Boolean(error)} onClick={() => { void onAction(item, "approve", Boolean(item.conflicts?.length)); }}>{item.conflicts?.length ? "确认并替换同名口径" : state === "pending" ? "确认记住" : "确认启用"}</button>}
                  <button disabled={Boolean(workingId) || Boolean(error)} onClick={() => onEditing(item.id)}>编辑</button>
                  {state === "active" && <button disabled={Boolean(workingId) || Boolean(error)} onClick={() => { void onAction(item, "disable"); }}>停用</button>}
                  {state === "pending" && item.source.conversation_id && <button disabled={Boolean(workingId) || Boolean(error)} onClick={() => { void onAction(item, "dismiss"); }}>仅用于本对话</button>}
                  </div>
                  {Boolean(item.conflicts?.length) && <p className="ai-knowledge-warning">存在同名且范围重叠的旧口径。确认将停用：{item.conflicts!.map(conflict => conflict.title).join("、")}。不同名称的语义冲突仍需人工核对。</p>}
              </div>}
              {editing?.base.id === item.id && <form className="ai-knowledge-form" onSubmit={event => { event.preventDefault();
                const draft = { ...editing.draft, fiscal_year: editing.year.trim() ? Number(editing.year) : null,
                  keywords: editing.keywords.split(/[,，、]/).map(word => word.trim()).filter(Boolean) };
                void onSave(editing.base, draft).then(() => onEditing(null)).catch(() => { /* Keep unsaved fields on errors. */ });
              }}><h4>修改后重新确认</h4><p>编辑后先回到待确认状态，确认前暂不应用。</p>
                <label>名称<input autoFocus required maxLength={80} value={editing.draft.title} onChange={event => update({ title: event.target.value })}/></label>
                <label>口径或偏好<textarea required rows={4} maxLength={1200} value={editing.draft.content} onChange={event => update({ content: event.target.value })}/></label>
                <label>类别<select value={editing.draft.kind} onChange={event => update({ kind: event.target.value as KnowledgeDraft["kind"] })}><option value="business">业务口径</option><option value="preference">个人偏好</option></select></label>
                <label>适用模块<select value={editing.draft.module} onChange={event => update({ module: event.target.value as KnowledgeDraft["module"] })}><option value="all">全部预算分析</option><option value="budget">定版预算</option><option value="forecast">全年预测</option><option value="next-year">预算编制</option></select></label>
                <label>财年（选填，如2028）<input type="number" min={2000} max={2200} value={editing.year} onChange={event => setEditing({ ...editing, year: event.target.value })}/></label>
                <label>关键词（选填，逗号分隔）<input maxLength={200} value={editing.keywords} onChange={event => setEditing({ ...editing, keywords: event.target.value })}/></label>
                <div className="ai-knowledge-actions"><button type="submit" disabled={Boolean(workingId) || !editing.draft.title.trim() || !editing.draft.content.trim()}>保存为待确认</button><button type="button" disabled={Boolean(workingId)} onClick={() => onEditing(null)}>取消编辑</button></div>
              </form>}
            </article>)}</section>; })}
          {collection && !items.some(item => ["pending", "active", "disabled"].includes(item.state)) && <p className="ai-knowledge-empty">{search ? "没有匹配的知识或偏好。" : "尚无已确认知识。可点击新增，或在纠正AI后从回答下方整理。"}</p>}
          {collection && !collection.items.some(item => ["pending", "active", "disabled"].includes(item.state)) && !search && <p className="ai-knowledge-empty">知识和偏好均由你管理。可点击“新增知识或偏好”手动记录，也可在对话中让AI整理候选。删除后不再加载，也不会自动补回。</p>}</>}
    </div>
    <footer>确认后的相关知识将在后续提问中加载。<br/>停用后不再加载，历史对话仍保留；不改变预算计算规则。</footer>
  </aside>;
}
