import { useEffect, useRef, useState } from "react";
import { BusinessForm, ReadTable, labels, meanings, type Schema, type Value } from "./BusinessForm";
import { identityHeaders, parseResponse, fetchJob, type ApiIdentity } from "./api";

type RecordValue = Record<string, Value>;
type Section = { key: string; label: string; schema: Schema; value: Value; data: RecordValue; path: string; confirm: { path: string; body: RecordValue } | null };
type Workspace = { revision_id: string; revision_no: number; fiscal_year: number; is_current_draft: boolean; is_published: boolean; manifest: Record<string, string>; source: RecordValue; sections: Section[] };
type Budget = { annual_budget_id: string; fiscal_year: number; draft_revision_id: string | null; published_revision_id: string | null };
const order = ["source", "targets", "combinations", "entry-total", "campus-suggestions", "campus", "entry-scope", "entry-detail", "rates", "rate-lock", "rate-change-lock", "rate-unlock", "planned", "planned-unlock", "resources", "discount", "results"];
const tips: Record<string, string> = {
  targets: "维护学校版部门与各赛道收入目标。每项都需要来源依据和变更原因。保存时锁定整套目标。",
  combinations: "开启或关闭具体业务范围。保存后由负责人单独确认；新增和关闭都必须填写原因。",
  "entry-total": "财年入口总值是固定起点。保存并锁定后，重新生成校区建议、确定校区分配并确认。",
  "campus-suggestions": "按已有校区生成建议；资源仅作依据，不作为隐藏权重。重新生成会影响下游，请核对后操作。",
  campus: "校区确定值必须合计等于入口总值，偏离建议时填写调整原因。保存不等于负责人确认。",
  "entry-scope": "默认入口为七年级首季度。特殊产品或校区用完整维度逐项纳入或排除；无例外可保存空列表。",
  "entry-detail": "新产品人工比例需明确指定，其余按历史分配。执行后生成入口初始值；不增加财年总值。",
  rates: "以百分数填写，例如 80 表示 80%。续班率和新生占比步长 0.01 个百分点；保存后须确认整套边界。",
  "rate-lock": "添加需要锁定的具体比例并填写原因；未选中的比例不受影响。",
  "rate-change-lock": "添加具体比例，填写新百分比及原因；操作后期初与下游结果可能失效。",
  "rate-unlock": "仅解锁列表中选定的比例，必须逐项说明原因。",
  planned: "计划新生不是自动调平杠杆。只有需要人工绝对人数的明细才添加，全部维度和原因必填。",
  "planned-unlock": "按完整维度解锁计划新生。修改或解锁使下游计算和原确认失效。",
  resources: "金额用元填写。期初结转已经不含税，不再除以 1.06。班均仅校验，不自动补人。",
  discount: "明细折扣步长 0.1 个百分点。综合折扣容差仍为 0.05 个百分点；不能通过显示四舍五入绕过。",
};
const resultRoutes: Record<string, string> = { "入口分配": "entry-detail-set", "人数": "headcount-result", "班均": "class-size-result", "收入": "revenue-result", "目标差额": "target-closure-result" };

export function WorkspacePage() {
  const [identity, setIdentity] = useState<ApiIdentity | null>(null);
  const [budgets, setBudgets] = useState<Budget[]>([]);
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [selected, setSelected] = useState("source");
  const [draft, setDraft] = useState<Value>({});
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [jobStatus, setJobStatus] = useState("");
  const [result, setResult] = useState<RecordValue | null>(null);
  const [resultTitle, setResultTitle] = useState("");
  const attempt = useRef<{ serialized: string; key: string } | null>(null);
  const sequence = useRef(0);
  const section = workspace?.sections.find(s => s.key === selected);
  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);
  const request = async <T,>(path: string, who: ApiIdentity, body?: unknown, key?: string): Promise<T> => parseResponse(await fetch(`/api/v1${path}`, { method: body === undefined ? "GET" : "POST", headers: { ...identityHeaders(who), ...(body === undefined ? {} : { "Content-Type": "application/json", "Idempotency-Key": key ?? crypto.randomUUID(), "If-Match": `"${workspace?.revision_id ?? ""}"` }) }, ...(body === undefined ? {} : { body: JSON.stringify(body) }) }));
  const load = async (revision: string, who: ApiIdentity, active = selected) => {
    const seq = ++sequence.current;
    const data = await request<Workspace>(`/revisions/${encodeURIComponent(revision)}/workspace`, who);
    if (seq !== sequence.current) return;
    setWorkspace(data); setDraft(data.sections.find(s => s.key === active)?.value ?? {}); setDirty(false); setResult(null);
    localStorage.setItem("budget.activeRevision", revision);
    history.replaceState({}, "", `/budget/workspace?revision=${encodeURIComponent(revision)}&section=${encodeURIComponent(active)}`);
  };
  useEffect(() => {
    let stopped = false;
    void (async () => {
      setBusy(true);
      try {
        const who = await parseResponse<ApiIdentity>(await fetch("/api/v1/local-trial/session"));
        if (stopped) return;
        setIdentity(who); localStorage.setItem("budget.userId", who.userId); localStorage.setItem("budget.departmentId", who.departmentId);
        const list = await request<Budget[]>("/workspace/budgets", who); if (stopped) return; setBudgets(list);
        const params = new URLSearchParams(location.search);
        const active = params.get("section") ?? "source"; setSelected(order.includes(active) ? active : "source");
        const id = params.get("revision") ?? list[0]?.draft_revision_id;
        if (id) await load(id, who, order.includes(active) ? active : "source");
      } catch (e) { if (!stopped) setError(e instanceof Error ? e.message : String(e)); }
      finally { if (!stopped) setBusy(false); }
    })();
    return () => { stopped = true; sequence.current += 1; };
  }, []);
  const changeSection = (key: string) => {
    if (dirty && !window.confirm("当前表单尚未保存，切换会放弃本次编辑。是否继续？")) return;
    setSelected(key); setDraft(workspace?.sections.find(s => s.key === key)?.value ?? {}); setDirty(false); setError(""); setNotice(""); setResult(null);
    if (workspace) history.replaceState({}, "", `/budget/workspace?revision=${workspace.revision_id}&section=${key}`);
  };
  const execute = async (path: string, body: unknown, prepare = false) => {
    if (!identity || !workspace) return;
    setBusy(true); setError(""); setNotice(""); setJobStatus("");
    try {
      let payload = body;
      if (prepare) payload = await request(`/workspace/forms/${selected}:prepare`, identity, body);
      const serialized = JSON.stringify([workspace.revision_id, path, payload]);
      if (attempt.current?.serialized !== serialized) attempt.current = { serialized, key: crypto.randomUUID() };
      const response = await request<any>(`/revisions/${workspace.revision_id}${path}`, identity, payload, attempt.current.key);
      let id = response.revision?.new_revision_id ?? response.new_revision_id ?? response.applied_revision_id ?? workspace.revision_id;
      if (response.job_id) {
        localStorage.setItem("budget.activeJob", response.job_id);
        const deadline = Date.now() + 20 * 60 * 1000;
        for (;;) {
          const job = await fetchJob(response.job_id, identity); setJobStatus(meanings[job.status] ?? job.status);
          if (job.status === "SUCCEEDED") { id = job.result?.final_revision_id ?? id; localStorage.removeItem("budget.activeJob"); break; }
          if (["FAILED", "INCOMPLETE", "CANCELLED"].includes(job.status)) { localStorage.removeItem("budget.activeJob"); throw new Error(job.last_error_safe ?? "计算未完成，请检查输入或 Worker 状态"); }
          if (Date.now() > deadline) throw new Error("等待时间已到，后台任务未被取消。请使用“查询上次任务”，不要重复提交。");
          await new Promise(r => setTimeout(r, 1000));
        }
      }
      await load(String(id), identity);
      setBudgets(await request<Budget[]>("/workspace/budgets", identity));
      setNotice(response.job_id ? "完整计算完成。请核阅人数、班均、收入与目标差额；计算完成不等于允许发布。" : "操作成功。已读取当前修订；保存不会代替负责人确认，上游变化后请重算下游。");
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };
  const queryJob = async () => {
    if (!identity) return;
    const id = localStorage.getItem("budget.activeJob"); if (!id) return setNotice("没有待查询的任务。");
    setBusy(true);
    try { const job = await fetchJob(id, identity); setJobStatus(meanings[job.status] ?? job.status); if (job.status === "SUCCEEDED" && typeof job.result?.final_revision_id === "string") { await load(job.result.final_revision_id, identity); localStorage.removeItem("budget.activeJob"); } }
    catch (e) { setError(String(e)); } finally { setBusy(false); }
  };
  const choices: Record<string, string> = {};
  const rateData = workspace?.sections.find(s => s.key === "rates")?.data.items;
  if (Array.isArray(rateData)) for (const entry of rateData as RecordValue[]) {
    const title = [entry.target_fiscal_season, entry.campus, entry.track, entry.product, entry.grade, meanings[String(entry.rate_type)]].join(" / ");
    choices[String(entry.rate_reference_id)] = title; choices[String(entry.rate_parameter_stable_key)] = title;
  }
  const activeBudget = budgets.find(b => b.fiscal_year === workspace?.fiscal_year);
  const latest = activeBudget?.draft_revision_id;
  const sourceRows = workspace?.source.components as RecordValue[] | undefined;
  return <div className="trial-app"><header className="trial-header"><a href="/">预算智解</a><strong>本机完整流程试用</strong><span>合成数据 · 仅本机访问</span></header>
    <main className="trial-main"><div className="trial-heading"><div><h1>年度预算工作区</h1><p>先维护输入，再确认、计算和调平。每次修改保留历史，不覆盖正式版本。</p></div>
      <label>选择年度预算<select disabled={busy} value={workspace?.fiscal_year ?? ""} onChange={e => { if (dirty && !window.confirm("放弃尚未保存的编辑？")) return; const budget = budgets.find(b => b.fiscal_year === Number(e.target.value)); if (budget?.draft_revision_id && identity) { setBusy(true); void load(budget.draft_revision_id, identity).catch(e => setError(String(e))).finally(() => setBusy(false)); } }}><option value="">请选择</option>{budgets.map(b => <option value={b.fiscal_year} key={b.annual_budget_id}>FY{String(b.fiscal_year).slice(-2)} 学校版合成试用</option>)}</select></label></div>
      {error && <div className="inline-error" role="alert">{error}</div>}{notice && <div className="trial-notice" role="status">{notice}</div>}
      {!workspace && !busy && <p>请使用项目中的“启动本机试用.command”启动。它会准备隔离样例和本地服务，不连接线上 BI。</p>}
      {workspace && <><div className="trial-revision"><span>当前修订 R{workspace.revision_no}</span><strong>{workspace.is_published ? "已发布，仅核阅" : workspace.is_current_draft ? "当前草稿" : "历史修订，仅核阅"}</strong>
        {latest && <button disabled={busy} onClick={() => { if (dirty && !window.confirm("刷新会放弃尚未保存的编辑，是否继续？")) return; if (identity) { setBusy(true); void load(latest, identity).catch(e => setError(String(e))).finally(() => setBusy(false)); } }}>读取最新草稿</button>}
        <button disabled={busy || !workspace.is_current_draft || dirty} onClick={() => void execute(":derive", { reason: "从当前版本开始新的本机试算" })}>新建试算修订</button>
        <a href={`/budget/opening?revision=${workspace.revision_id}`}>期初老生维护</a><a href={`/budget/rebalancing?revision=${workspace.revision_id}`}>自动调平</a><a href={`/budget/publication?revision=${workspace.revision_id}`}>确认与发布</a><a href={`/budget/audit?revision=${workspace.revision_id}`}>版本审计</a></div>
      <div className="trial-layout"><nav className="trial-nav" aria-label="预算操作步骤">{order.map(key => <button key={key} disabled={busy} className={selected === key ? "is-active" : ""} onClick={() => changeSection(key)}>{key === "source" ? "冻结来源" : key === "results" ? "计算与核阅结果" : workspace.sections.find(s => s.key === key)?.label}</button>)}</nav>
        <section className="trial-content" aria-busy={busy}>
          {selected === "source" && <><h2>冻结来源核验</h2><p>当前示例使用合成历史。已冻结的来源不可修改；真实 BI 抽取需要单独授权，不会在本机试用中自动触发。</p><dl className="trial-facts">{["status", "cutoff_at", "frozen_at"].map(k => <div key={k}><dt>{labels[k]}</dt><dd>{meanings[String(workspace.source[k])] ?? String(workspace.source[k] ?? "—")}</dd></div>)}</dl><ReadTable rows={sourceRows ?? []} /><h3>来源对账</h3><ReadTable rows={(workspace.source.reconciliations as RecordValue[]) ?? []} /><p>期初老生使用上一财年春季普通“招生人次”；新生占比使用三分之二出勤口径作分母。不要混用。</p></>}
          {section && <><h2>{section.label}</h2><p>{tips[selected]}</p><p className="trial-form-status">{section.data.confirmed === true ? "该参数版本已确认；修改后须按新版本重新确认。" : section.data.record_count ? `当前 ${section.data.record_count} 条记录` : "请核对完整输入"}</p>
            <fieldset disabled={busy || !workspace.is_current_draft || workspace.is_published} className="trial-fieldset"><BusinessForm key={`${workspace.revision_id}:${selected}`} schema={section.schema} value={draft} choices={choices} onChange={next => { setDraft(next); setDirty(true); }} />
            <div className="trial-actions"><button className="button button--primary" onClick={() => void execute(section.path, draft, ["targets", "resources", "discount"].includes(selected))}>{busy ? "处理中…" : ["entry-total", "targets", "planned"].includes(selected) ? "保存并锁定" : selected === "entry-detail" ? "执行入口分配" : selected === "campus-suggestions" ? "生成校区建议" : selected.includes("lock") ? "执行选定操作" : "保存草稿"}</button>
            {section.confirm && <button className="button" disabled={dirty} onClick={() => { if (window.confirm(`以部门预算负责人身份确认当前整套“${section.label}”？保存草稿不等于确认。`)) void execute(section.confirm!.path, section.confirm!.body); }}>负责人确认当前整套{selected === "rates" ? "边界" : "参数"}</button>}</div></fieldset>
            {dirty && <p role="status">有未保存的编辑，先保存再确认。保存失败时输入会保留。</p>}
            {Array.isArray(section.data.items) && <details className="trial-current"><summary>核阅当前已入库明细（编辑不会即时改变此表）</summary><ReadTable rows={section.data.items as RecordValue[]} /></details>}
          </>}
          {selected === "results" && <><h2>计算与结果核阅</h2><p>计算依次执行人数、班均、收入、目标差额。发现缺失先修复输入，不会自动补人或放宽容差。</p><button className="button button--primary" disabled={busy || !workspace.is_current_draft || workspace.is_published} onClick={() => void execute(":calculate", {})}>执行完整预算计算</button><button className="button" disabled={busy} onClick={() => void queryJob()}>查询上次任务</button><p role="status">{jobStatus}</p>
            <div className="trial-result-tabs">{Object.entries(resultRoutes).map(([label, prefix]) => { const id = workspace.manifest[`${prefix.replaceAll("-", "_")}_version_id`]; return <button disabled={!id || busy} key={prefix} onClick={() => { if (!identity) return; setBusy(true); setError(""); void request<RecordValue>(`/${prefix}-versions/${id}`, identity).then(data => { setResult(data); setResultTitle(label); }).catch(e => setError(String(e))).finally(() => setBusy(false)); }}>{label}{!id && "（待计算）"}</button>; })}</div>
            {result && <><h3>{resultTitle}</h3><ReadTable rows={(result.items as RecordValue[]) ?? []} /></>}
          </>}
        </section></div></>}
      {busy && <p className="trial-working" role="status">正在处理，请勿重复提交… {jobStatus}</p>}
    </main></div>;
}
