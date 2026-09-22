import { useEffect, useRef, useState } from "react";
import { fetchOpeningWorkspace, newRequestId, saveOpening, type OpeningDimension, type OpeningWorkspace, type ApiIdentity } from "./api";

const dimensionKey = (r: OpeningDimension) => JSON.stringify([r.track, r.product, r.grade, r.subject, r.campus]);
function target(r: OpeningDimension): OpeningDimension | null {
  if (r.grade === "九年级" || (r.grade === "八年级" && ["地理", "生物"].includes(r.subject))) return null;
  return { ...r, grade: r.grade === "七年级" ? "八年级" : "九年级" };
}

export function OpeningStudentsPage() {
  const [revision, setRevision] = useState(new URLSearchParams(location.search).get("revision") ?? "");
  const [identity, setIdentity] = useState<ApiIdentity>({ userId: localStorage.getItem("budget.userId") ?? "budget-maintainer", departmentId: localStorage.getItem("budget.departmentId") ?? "zhihui-learning", roles: ["DATA_MAINTAINER"] });
  const [data, setData] = useState<OpeningWorkspace | null>(null);
  const [sourceId, setSourceId] = useState("");
  const [zeros, setZeros] = useState<string[]>([]);
  const [reason, setReason] = useState("");
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [receipt, setReceipt] = useState("");
  const attempt = useRef<{ body: string; key: string } | null>(null);
  const load = async (id: string) => {
    setBusy(true); setData(null); setError(""); setZeros([]);
    try {
      const result = await fetchOpeningWorkspace(id, identity);
      setData(result); setSourceId(result.sources.length === 1 ? result.sources[0].source_component_id : "");
      localStorage.setItem("budget.userId", identity.userId); localStorage.setItem("budget.departmentId", identity.departmentId);
      history.replaceState({}, "", `/budget/opening?revision=${encodeURIComponent(id)}`);
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };
  useEffect(() => { if (revision) void load(revision); }, []);
  const source = data?.sources.find(s => s.source_component_id === sourceId);
  const mapped = new Set(source?.rows.flatMap(r => { const t = target(r); return t ? [dimensionKey(t)] : []; }));
  const uncovered = data?.combinations.filter(c => !mapped.has(dimensionKey(c))) ?? [];
  const invalid = !source || source.rows.some(r => r.enrollment === null || !r.rate_parameter_id || r.renewal_rate_ppm === null || !["七年级", "八年级", "九年级"].includes(r.grade));
  const save = async () => {
    if (!source || !data) return;
    const body = { source_component_id: sourceId, source_fiscal_season: data.source_fiscal_season, rows: source.rows.map(({ renewal_rate_ppm: _rate, ...r }) => r), zero_combination_ids: zeros, reason: reason.trim() };
    const serialized = JSON.stringify([revision, identity, body]);
    if (attempt.current?.body !== serialized) attempt.current = { body: serialized, key: newRequestId() };
    setBusy(true); setError("");
    try {
      const result = await saveOpening(revision, identity, body, attempt.current.key);
      setReceipt(`期初版本已保存：${result.opening_student_version_id}。下游计算和原正式确认已失效，请重新计算。`);
      setRevision(result.revision.new_revision_id); setReason("");
      await load(result.revision.new_revision_id);
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };
  return <div className="app-frame">
    <header className="topbar"><a className="brand" href={`/budget/workspace?revision=${encodeURIComponent(revision)}`}>年度预算工作区</a><nav aria-label="主要导航"><a className="is-current" href={`/budget/opening?revision=${encodeURIComponent(revision)}`}>期初老生</a><a href={`/budget/rebalancing?revision=${encodeURIComponent(revision)}`}>调平方案</a><a href={`/budget/audit?revision=${encodeURIComponent(revision)}`}>版本审计</a></nav></header>
    <main className="workspace opening-workspace">
      <div className="page-heading"><div><h1>期初老生维护</h1><p>从上一财年春季招生人次续入暑假。FY27 已定版，仅 FY28 及以后适用。</p></div></div>
      <fieldset disabled={busy || data !== null} className="opening-connection"><legend>本地开发身份与预算修订</legend>
        <label>部门 ID<input value={identity.departmentId} onChange={e => setIdentity({ ...identity, departmentId: e.target.value })} /></label>
        <label>数据维护人员 ID<input value={identity.userId} onChange={e => setIdentity({ ...identity, userId: e.target.value })} /></label>
        <label>预算修订 ID<input value={revision} onChange={e => setRevision(e.target.value.trim())} /></label>
      </fieldset>
      <button className="button" disabled={busy || !revision} onClick={() => data ? (setData(null), setReceipt("")) : void load(revision)}>{data ? "切换修订" : "读取维护数据"}</button>
      <p className="connect-note">开发身份请求头仅用于本地试用，不代表生产权限配置。</p>
      {error && <p className="inline-error" role="alert">{error}</p>}
      {receipt && <p className="opening-receipt" role="status">{receipt}</p>}
      {data && <>
        <p role="status">{data.opening_status}</p>
        {data.opening_student_version_id && <p className="opening-hash">当前期初版本：{data.opening_student_version_id}</p>}
        {!data.is_current_draft && <p role="alert">这不是当前草稿，仅可核阅，不能保存。请切换到最新修订。</p>}
        <section className="ledger opening-section"><h2>冻结来源核验 · {data.source_fiscal_season}</h2>
          <label>选择来源<select disabled={busy} value={sourceId} onChange={e => { setSourceId(e.target.value); setZeros([]); }}><option value="">请选择冻结来源</option>{data.sources.map(s => <option key={s.source_component_id} value={s.source_component_id}>{s.dataset_name} / {s.partition}</option>)}</select></label>
          {data.sources.length === 0 && <p role="alert">快照缺少上一财年春季普通口径招生人次，请先维护来源快照；不可替用出勤口径或目标值。</p>}
          {source && <><p>快照日期：{source.snapshot_date}　来源批次：{source.source_batch}</p><p className="opening-hash">来源指纹：{source.content_hash}</p>
            <label>筛选校区、产品或科目<input value={query} onChange={e => setQuery(e.target.value)} placeholder="输入关键词" /></label>
            <div className="opening-table"><table><thead><tr>{["校区", "赛道 / 产品", "科目", "来源年级", "续入年级", "招生人次", "春续暑续班率", "理论老生人次"].map(h => <th key={h}>{h}</th>)}</tr></thead><tbody>
              {source.rows.filter(r => [r.campus, r.product, r.track, r.subject, r.grade].join(" ").includes(query)).map((r, i) => <tr key={i}><td>{r.campus}</td><td>{r.track} / {r.product}</td><td>{r.subject}</td><td>{r.grade}</td><td>{target(r)?.grade ?? (r.grade === "九年级" ? "毕业，不续入" : "学科结束，不续入")}</td><td>{r.enrollment ?? "缺失"}</td><td>{r.renewal_rate_ppm === null ? "待维护" : `${r.renewal_rate_ppm / 10000}%`}</td><td>{r.enrollment === null || r.renewal_rate_ppm === null ? "无法计算" : target(r) ? (r.enrollment * r.renewal_rate_ppm / 1000000).toLocaleString("zh-CN", { maximumFractionDigits: 6 }) : "—"}</td></tr>)}
            </tbody></table></div><p>共 {source.rows.length} 条来源。这里展示理论人数；最终整数由人数计算按组分配，不逐行提前取整。</p></>}
        </section>
        {source && <section className="ledger opening-section"><h2>无来源覆盖的明细 · {uncovered.length} 条</h2><p>逐项确认确实没有期初老生后勾选；未勾选视为缺失，不会自动填零。</p>
          <div className="opening-zero-list">{uncovered.map(c => <label key={c.combination_id}><input type="checkbox" disabled={busy || !data.is_current_draft} checked={zeros.includes(c.combination_id)} onChange={e => setZeros(e.target.checked ? [...zeros, c.combination_id] : zeros.filter(id => id !== c.combination_id))} />{c.campus} / {c.track} / {c.product} / {c.grade} / {c.subject}：确认期初为 0</label>)}</div>
          <label>维护原因（必填）<textarea disabled={busy} rows={3} maxLength={500} value={reason} onChange={e => setReason(e.target.value)} /></label>
          {invalid && <p role="alert">来源人数或续班率绑定不完整，请维护上游数据后重新读取。</p>}
          <button className="button button--primary" disabled={busy || !data.is_current_draft || invalid || zeros.length !== uncovered.length || !reason.trim()} onClick={() => void save()}>{busy ? "处理中…" : "保存期初版本"}</button><p>保存会生成新修订，不覆盖历史版本，也不会自动计算或发布预算。</p>
        </section>}
      </>}
    </main>
  </div>;
}
