import { ActionTable } from "./ActionTable";
import { BudgetBrand } from "./BudgetBrand";
import { useEffect, useRef, useState } from "react";
import { fetchOpeningWorkspace, newRequestId, saveOpening, type OpeningDimension, type OpeningWorkspace, type ApiIdentity } from "./api";
import { BudgetDataSection } from "./BudgetDataSection";
import { skuIdentityCells, skuIdentityHeaders } from "./SkuIdentity";
import { BudgetVersionTrace } from "./BudgetVersionIdentity";
import { budgetVersionExportAttributes } from "./budgetVersionExport";
import "./auxiliaryDesign.css";

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
      setReceipt("期初版本已保存。下游计算和原正式确认已失效，请重新计算；精确标识可在追溯信息中查看。");
      setRevision(result.revision.new_revision_id); setReason("");
      await load(result.revision.new_revision_id);
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };
  return <div className="app-frame budget-auxiliary">
    <header className="topbar"><a className="brand" href={`/budget/workspace?revision=${encodeURIComponent(revision)}`} aria-label="预算智解，返回年度预算工作区"><BudgetBrand /></a><nav aria-label="主要导航"><a className="is-current" href={`/budget/opening?revision=${encodeURIComponent(revision)}`}>期初老生</a><a href={`/budget/rebalancing?revision=${encodeURIComponent(revision)}`}>调平方案</a><a href={`/budget/audit?revision=${encodeURIComponent(revision)}`}>版本记录</a></nav></header>
    <main className="workspace opening-workspace">
      <header className="budget-auxiliary-heading"><h1 className="budget-page-title">期初老生维护</h1></header>
      <details className="opening-technical" open={!data}><summary>连接与追溯设置</summary><fieldset disabled={busy || data !== null} className="opening-connection"><legend>本地开发身份与预算修订</legend>
        <label>部门 ID<input value={identity.departmentId} onChange={e => setIdentity({ ...identity, departmentId: e.target.value })} /></label>
        <label>数据维护人员 ID<input value={identity.userId} onChange={e => setIdentity({ ...identity, userId: e.target.value })} /></label>
        <label>预算修订 ID<input value={revision} onChange={e => setRevision(e.target.value.trim())} /></label>
      </fieldset>
      <button className="button" disabled={busy || !revision} onClick={() => data ? (setData(null), setReceipt("")) : void load(revision)}>{data ? "切换修订" : "读取维护数据"}</button></details>

      {error && <p className="budget-status" role="alert">{error}</p>}
      {receipt && <p className="opening-receipt budget-status" role="status">{receipt}</p>}
      {data && <>
        <p role="status">{data.opening_status}</p>
        {data.opening_student_version_id && <><p className="budget-status">已保存期初老生数据 · 版本序号未记录</p><BudgetVersionTrace id={data.opening_student_version_id} /></>}
        {!data.is_current_draft && <p role="alert">这不是当前草稿，仅可核阅，不能保存。请切换到最新修订。</p>}
        <BudgetDataSection id="opening-frozen-source" title={`冻结来源核验 · ${data.source_fiscal_season}`}
          {...budgetVersionExportAttributes({ fiscalYear: data.fiscal_year, object: "期初来源（已保存事实）", id: revision,
            sourceId: source?.source_component_id, sourceLabel: source ? `冻结来源 ${data.source_fiscal_season} · ${source.dataset_name} / ${source.partition}` : "冻结来源未选择", asOf: source?.snapshot_date })}>
          <label>选择来源<select disabled={busy} value={sourceId} onChange={e => { setSourceId(e.target.value); setZeros([]); }}><option value="">请选择冻结来源</option>{data.sources.map(s => <option key={s.source_component_id} value={s.source_component_id}>{s.dataset_name} / {s.partition}</option>)}</select></label>
          {data.sources.length === 0 && <p role="alert">快照缺少上一财年春季普通口径招生人次，请先维护来源快照；不可替用出勤口径或目标值。</p>}
          {source && <><p>快照日期：{source.snapshot_date}　来源批次：{source.source_batch}</p><details><summary>来源追溯信息</summary><p className="opening-hash">来源指纹：{source.content_hash}</p></details>
            <ActionTable tableLabel="冻结来源明细" aria-label="冻结来源核验表" keyboardNavigation scrollClassName="budget-data-section__scroll opening-table" toolbarStart={<label>筛选校区、产品或科目<input value={query} onChange={e => setQuery(e.target.value)} placeholder="输入关键词" /></label>}><thead><tr>{["校区", "赛道 / 产品", "科目", "来源年级", "续入年级", "招生人次", "春续暑续班率", "理论老生人次"].map(h => <th key={h} scope="col" className={["招生人次", "春续暑续班率", "理论老生人次"].includes(h) ? "budget-cell-number" : "budget-cell-text"}>{h}</th>)}</tr></thead><tbody>
              {source.rows.filter(r => [r.campus, r.product, r.track, r.subject, r.grade].join(" ").includes(query)).map((r, i) => <tr key={i}><td>{r.campus}</td><td>{r.track} / {r.product}</td><td>{r.subject}</td><td>{r.grade}</td><td>{target(r)?.grade ?? (r.grade === "九年级" ? "毕业，不续入" : "学科结束，不续入")}</td><td className="budget-cell-number">{r.enrollment ?? "缺失"}</td><td className="budget-cell-number">{r.renewal_rate_ppm === null ? "待维护" : `${r.renewal_rate_ppm / 10000}%`}</td><td className="budget-cell-number">{r.enrollment === null || r.renewal_rate_ppm === null ? "无法计算" : target(r) ? (r.enrollment * r.renewal_rate_ppm / 1000000).toLocaleString("zh-CN", { maximumFractionDigits: 6 }) : "—"}</td></tr>)}
            </tbody></ActionTable><p className="budget-status">共 {source.rows.length} 条来源</p></>}
        </BudgetDataSection>
        {source && <BudgetDataSection id="opening-uncovered" title="无来源覆盖的明细" count={uncovered.length} countLabel="条明细"
          {...budgetVersionExportAttributes({ fiscalYear: data.fiscal_year, object: "期初覆盖核对", id: revision, preview: zeros.length > 0,
            sourceId: source.source_component_id, sourceLabel: `冻结来源 ${data.source_fiscal_season} · ${source.dataset_name} / ${source.partition}`, asOf: source.snapshot_date })}>
          <ActionTable tableLabel="无来源覆盖的明细" aria-label="无来源覆盖的明细表" keyboardNavigation scrollClassName="budget-data-section__scroll opening-uncovered-table"><thead><tr>{skuIdentityHeaders.map(label => <th scope="col" key={label}>{label}</th>)}<th scope="col" className="budget-cell-center">确认期初为 0</th></tr></thead><tbody>{uncovered.map(c => <tr key={c.combination_id}>{skuIdentityCells(c)}<td className="budget-cell-center"><label className="opening-zero-confirm"><input type="checkbox" aria-label={`${c.campus} / ${c.track} / ${c.product} / ${c.grade} / ${c.subject}：确认期初为 0`} disabled={busy || !data.is_current_draft} checked={zeros.includes(c.combination_id)} onChange={e => setZeros(e.target.checked ? [...zeros, c.combination_id] : zeros.filter(id => id !== c.combination_id))} /><span className="sr-only">确认期初为 0</span></label></td></tr>)}</tbody></ActionTable>
          <label>维护原因（必填）<textarea disabled={busy} rows={3} maxLength={500} value={reason} onChange={e => setReason(e.target.value)} /></label>
          {invalid && <p role="alert">来源人数或续班率绑定不完整，请维护上游数据后重新读取。</p>}
          <button className="button button--primary" disabled={busy || !data.is_current_draft || invalid || zeros.length !== uncovered.length || !reason.trim()} onClick={() => void save()}>{busy ? "处理中…" : "保存期初版本"}</button>
        </BudgetDataSection>}
      </>}
    </main>
  </div>;
}
