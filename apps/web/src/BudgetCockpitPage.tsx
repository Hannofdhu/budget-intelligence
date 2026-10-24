import { useEffect, useMemo, useRef, useState } from "react";
import type { ApiIdentity } from "./api";
import { FY27Header } from "./FY27Header";
import { BudgetScopeBar } from "./BudgetScopeBar";
import { ForecastGroupSelector } from "./ForecastGroupSelector";
import { BudgetUtilityPanel } from "./BudgetUtilityPanel";
import { ActionTable } from "./ActionTable";
import { CockpitChart } from "./CockpitChart";
import { aiBudgetHref } from "./aiBudgetContext";
import { getBudgetSession, peekBudgetSession } from "./budgetReadCache";
import { filterLabel, hasFilter } from "./filterSelection";
import { versionLabel, versionTimestamp } from "./budgetVersionDisplay";
import type { BudgetDimension } from "./budgetAnalysisTypes";
import type { ExportColumn } from "./budgetExport";
import { cockpitDimensions, cockpitLabels, cockpitMetrics, cockpitMetricLabels, cockpitRows, cockpitGroups, cockpitTotals, cockpitHref, cockpitSourceHref, cockpitValue, readCockpitView, cockpitPath, drillCockpit, backCockpit, getCockpit, peekCockpit,
  type CockpitData, type CockpitView, type CockpitPath, type CockpitMetric, type CockpitGroup, type CockpitRow } from "./cockpit";
import "./forecast.css";
import "./cockpit.css";

const percent = (value: number | null) => value === null ? "—" : `${(value * 100).toLocaleString("zh-CN", { maximumFractionDigits: 2 })}%`;
const series = { current: ["budget", "actual", "forecast"], next: ["budget", "forecast", "next_budget"] } as const;
const fy = (year: number) => `FY${String(year).slice(-2)}`;
const selectionText = (path: CockpitPath) => Object.entries(path).map(([key, value]) => `${cockpitLabels[key as BudgetDimension]}：${value}`).join("、");
const sourceText = (source: CockpitData["sources"][CockpitMetric]) => !source ? "尚无保存结果" : source.status === "FINALIZED" ? "已定版" : `${versionLabel(source.sequence)} · ${source.status === "CONFIRMED" ? "已确认" : "草稿"}`;
const unitLabel = (view: CockpitView) => view.unit === "wan" ? "万元" : "元";

export function cockpitTransfer(data: CockpitData, view: CockpitView, rows: (CockpitGroup | CockpitRow)[], details = false, selected: CockpitPath = {}) {
  const labels = cockpitMetricLabels(data), dimensions = details ? [...cockpitDimensions] : view.groups;
  const divisor = view.unit === "wan" ? 10000 : 1;
  const columns: ExportColumn[] = [...dimensions.map(key => ({ key, label: cockpitLabels[key] })),
    ...cockpitMetrics.map(key => ({ key, label: `${labels[key]}结转收入（${unitLabel(view)}）`, format: "money" as const })),
    ...(!details ? [{ key: "gap", label: `预计缺口（${unitLabel(view)}）`, format: "money" as const }, { key: "achievement", label: "预计达成率", format: "rate" as const }] : []),
    { key: "forecast_source", label: "全年预测版本" }, { key: "forecast_as_of", label: "实际与预测数据截至" },
    { key: "next_source", label: "下一年预算版本" }, { key: "next_basis", label: "下一年编制基准" },
    { key: "source_ids", label: "精确来源标识" }, { key: "scope", label: "当前筛选及下钻范围" }];
  const metadata = { forecast_source: sourceText(data.sources.forecast), forecast_as_of: data.sources.actual?.as_of ?? "未记录", next_source: sourceText(data.sources.next_budget),
    next_basis: data.sources.next_budget?.source ? `${fy(data.fiscal_year)}全年预测 · ${versionLabel(data.sources.next_budget.source.sequence)}` : "未记录",
    source_ids: JSON.stringify(Object.fromEntries(cockpitMetrics.map(key => [key, data.sources[key]?.id ?? null]))),
    scope: [...cockpitDimensions.filter(key => hasFilter(view.filters[key])).map(key => `${cockpitLabels[key]}：${filterLabel(view.filters[key])}`), ...view.trail.map(step => selectionText(step.path)), selectionText(selected)].filter(Boolean).join("；") || "全部部门预算" };
  return { columns, rows: rows.map(row => ({ ...Object.fromEntries(dimensions.map(key => [key, "path" in row ? row.path[key] : row[key]])),
    ...Object.fromEntries(cockpitMetrics.map(key => [key, row[key] === null ? null : Number(row[key]) / divisor])),
    ...("gap" in row ? { gap: row.gap === null ? null : row.gap / divisor, achievement: row.achievement } : {}), ...metadata })),
    filename: `预算驾驶舱-${fy(data.fiscal_year)}-${fy(data.next_fiscal_year)}-${details ? "当前范围明细" : "维度对比"}` };
}

function CockpitTable({ data, view, groups, onDrill, onDetails }: { data: CockpitData; view: CockpitView; groups: CockpitGroup[]; onDrill: (row: CockpitGroup) => void; onDetails: (path: CockpitPath) => void }) {
  const labels = cockpitMetricLabels(data), [page, setPage] = useState(0), signature = groups.map(row => row.key).join();
  useEffect(() => { setPage(0); }, [signature]);
  const totalPages = Math.max(1, Math.ceil(groups.length / 30)), currentPage = Math.min(page, totalPages - 1);
  return <><ActionTable tableLabel="驾驶舱维度对比" keyboardNavigation exportData={cockpitTransfer(data, view, groups)} scrollClassName="cockpit-table-scroll">
    <thead><tr>{view.groups.map(key => <th key={key} className="budget-cell-text" scope="col">{cockpitLabels[key]}</th>)}{cockpitMetrics.map(key => <th key={key} className="budget-cell-number" scope="col">{labels[key]}<small>结转收入（{unitLabel(view)}）</small></th>)}<th className="budget-cell-number">预计缺口 / 超额</th><th className="budget-cell-number">预计达成率</th><th className="budget-cell-center">查看</th></tr></thead>
    <tbody>{groups.slice(currentPage * 30, currentPage * 30 + 30).map(row => <tr key={row.key}>{view.groups.map(key => <th key={key} className="budget-cell-text" scope="row">{row.path[key]}</th>)}{cockpitMetrics.map(key => <td key={key} className="budget-cell-number">{cockpitValue(row[key], view.unit)}</td>)}<td className="budget-cell-number">{row.gap === null ? "—" : `${row.gap >= 0 ? "缺口" : "超额"} ${cockpitValue(Math.abs(row.gap), view.unit)}`}</td><td className="budget-cell-number">{percent(row.achievement)}</td><td className="budget-cell-center cockpit-row-actions"><button onClick={() => onDrill(row)} aria-label={`下钻${row.label}`}>下钻</button><button onClick={() => onDetails(row.path)} aria-label={`查看${row.label}明细`}>明细</button></td></tr>)}</tbody>
  </ActionTable><div className="cockpit-pagination"><span>{groups.length} 个分组 · 第{currentPage + 1}/{totalPages}页</span><button disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}>上一页</button><button disabled={currentPage + 1 >= totalPages} onClick={() => setPage(currentPage + 1)}>下一页</button></div></>;
}

export function BudgetCockpitPage() {
  const [identity, setIdentity] = useState<ApiIdentity | null>(peekBudgetSession);
  const [data, setData] = useState<CockpitData | null>(() => { const actor = peekBudgetSession(); return actor ? peekCockpit(actor, location.search) : null; });
  const [view, setView] = useState<CockpitView>(() => readCockpitView(location.search));
  const [loading, setLoading] = useState(!data), [error, setError] = useState(""), [notice, setNotice] = useState("");
  const [sourcesOpen, setSourcesOpen] = useState(false), [details, setDetails] = useState<CockpitPath | null>(null), [detailPage, setDetailPage] = useState(0);
  const [nextDimension, setNextDimension] = useState<BudgetDimension>("campus");
  const generation = useRef(0), viewRef = useRef(view); viewRef.current = view;
  const dataRef = useRef(data); dataRef.current = data;
  async function load(search = location.search, fresh = false, signal?: AbortSignal) {
    const request = ++generation.current; setLoading(true); setError("");
    try {
      const actor = await getBudgetSession(signal), result = await getCockpit(actor, search, signal, fresh);
      if (signal?.aborted || request !== generation.current) return;
      setIdentity(actor); setData(result);
      history.replaceState(history.state, "", cockpitHref(viewRef.current, result));
    } catch (failure) { if (!signal?.aborted && request === generation.current) setError(failure instanceof Error ? failure.message : "驾驶舱读取失败"); }
    finally { if (!signal?.aborted && request === generation.current) setLoading(false); }
  }
  useEffect(() => {
    const controller = new AbortController(); void load(location.search, false, controller.signal);
    const restore = () => {
      if (location.pathname !== "/budget/cockpit") return;
      const current = dataRef.current, search = location.search, params = new URLSearchParams(search);
      const sameSources = current && params.get("forecast_version") === (current.sources.forecast?.id ?? "none") && params.get("nyVersion") === (current.sources.next_budget?.id ?? "none");
      // Browser back owns the selected snapshot even if a newer read finishes later.
      ++generation.current; setLoading(false); setError("");
      setView(readCockpitView(search)); setDetails(null);
      if (!sameSources) { setData(null); void load(search, false, controller.signal); }
    };
    window.addEventListener("popstate", restore);
    return () => { ++generation.current; controller.abort(); window.removeEventListener("popstate", restore); };
  }, []);
  const update = (next: CockpitView) => { setView(next); setDetails(null); setNotice(""); history.pushState(history.state, "", cockpitHref(next, data, location.search)); };
  const rows = useMemo(() => data ? cockpitRows(data, view) : [], [data, view]);
  const totals = useMemo(() => cockpitTotals(rows), [rows]);
  const groups = useMemo(() => cockpitGroups(rows, view.groups, view.sort), [rows, view.groups, view.sort]);
  const quarters = useMemo(() => cockpitGroups(rows, ["season"], "name").sort((a, b) => ["暑假", "秋季", "寒假", "春季"].indexOf(a.label) - ["暑假", "秋季", "寒假", "春季"].indexOf(b.label)), [rows]);
  const detailRows = useMemo(() => data && details ? cockpitRows(data, view, details) : [], [data, view, details]);
  const path = cockpitPath(view), availableDimensions = cockpitDimensions.filter(key => !path[key] && !view.groups.includes(key));
  const effectiveNextDimension = availableDimensions.includes(nextDimension) ? nextDimension : availableDimensions[0];
  const drill = (group: CockpitGroup) => {
    const fixed = { ...path, ...group.path };
    if (cockpitDimensions.every(key => fixed[key])) { setSourcesOpen(false); setDetails(group.path); setDetailPage(0); return; }
    update(drillCockpit(view, group.path, effectiveNextDimension));
  };
  const readLatest = () => { const p = new URLSearchParams(location.search); p.delete("forecast_version"); p.delete("nyVersion"); void load(`?${p}`, true); };
  const aiHref = aiBudgetHref(cockpitHref(view, data, location.search));
  const openDetails = (selected: CockpitPath = {}) => { setSourcesOpen(false); setDetails(selected); setDetailPage(0); };
  const copyLink = async () => { try { await navigator.clipboard.writeText(new URL(cockpitHref(view, data, location.search), location.origin).href); setNotice("已复制当前范围和精确版本链接"); } catch { setNotice("链接复制失败，请复制浏览器地址"); } };
  const labels = data ? cockpitMetricLabels(data) : null;
  const metrics = [...series[view.comparison]] as CockpitMetric[];
  const wholeYear = !view.trail.length && cockpitDimensions.every(key => !hasFilter(view.filters[key]));
  return <div className="decision-shell budget-shell cockpit-shell">
    <a className="skip-link" href="#cockpit-content">跳到驾驶舱内容</a>
    <FY27Header active="cockpit"><span className="decision-version">只读汇总</span></FY27Header>
    <main id="cockpit-content" className="decision-workspace cockpit-workspace" aria-busy={loading || undefined}>
      <header className="cockpit-heading"><div><h1 className="budget-page-title">预算驾驶舱</h1><p>定版目标、实际完成、全年预计与下一年预算，同范围查看。</p></div><div className="cockpit-actions"><button disabled={loading} onClick={readLatest}>{loading ? "正在读取…" : "读取最新成果"}</button><button onClick={() => { setDetails(null); setSourcesOpen(true); }}>数据来源</button><a className="decision-primary" href={aiHref}>问问 AI智解</a></div></header>
      {error && <p className="cockpit-error" role="alert">{error} <button onClick={() => void load()}>重试读取</button></p>}
      {notice && <p className="cockpit-notice" role="status">{notice}</p>}
      {!data && !error && <div className="cockpit-loading" role="status">正在汇集已保存成果…</div>}
      {data && labels && <>
        <div className="cockpit-source-line"><span>{fy(data.fiscal_year)}预测：{sourceText(data.sources.forecast)} · 数据截至 {data.sources.forecast?.as_of ?? "未记录"}</span><span>{fy(data.next_fiscal_year)}预算：{sourceText(data.sources.next_budget)}</span><button onClick={() => { setDetails(null); setSourcesOpen(true); }}>查看引用关系</button></div>
        <BudgetScopeBar label="当前范围" summary={view.trail.length ? `下钻：${selectionText(path)}` : undefined} filters={view.filters} dimensions={cockpitDimensions.map(key => ({ key, label: cockpitLabels[key], options: key === "season" ? ["暑假", "秋季", "寒假", "春季"] : [...new Set(data.rows.map(row => row[key]))] }))}
          onChange={filters => update({ ...view, filters })} count={rows.length} countLabel="条跨模块明细"/>
        <section className="cockpit-metrics" aria-label="当前范围经营指标">{cockpitMetrics.map(metric => <article key={metric} className={`cockpit-metric cockpit-metric--${metric}`}>
          <h2>{labels[metric]}<span>结转收入（{unitLabel(view)}）</span></h2><strong>{cockpitValue(totals[metric], view.unit)}</strong>
          <p>{metric === "budget" ? "已定版 · 保持原口径" : metric === "actual" ? `实际达成率 ${percent(totals.actualAchievement)}` : metric === "forecast" ? `预计达成率 ${percent(totals.achievement)}` : wholeYear && data.sources.next_budget?.target_revenue_yuan != null ? `经营目标 ${cockpitValue(data.sources.next_budget.target_revenue_yuan, view.unit)} ${unitLabel(view)}` : `较今年全年预计 ${totals.nextGrowth === null ? "—" : `${totals.nextGrowth >= 0 ? "+" : ""}${percent(totals.nextGrowth)}`}`}</p>
        </article>)}</section>
        <section className="cockpit-explore budget-surface" aria-label="自由维度下钻">
          <div className="cockpit-section-heading"><div><h2 className="budget-section-title">经营全景</h2><p>{groups.length} 个分组 · 预计{totals.gap === null ? "缺口待核对" : `${totals.gap >= 0 ? "缺口" : "超额"} ${cockpitValue(Math.abs(totals.gap), view.unit)} ${unitLabel(view)}`}</p></div><ForecastGroupSelector value={view.groups} onChange={groups => update({ ...view, groups })}/></div>
          <nav className="cockpit-breadcrumbs" aria-label="下钻路径"><button onClick={() => update(backCockpit(view, 0))} disabled={!view.trail.length}>{wholeYear ? "全部部门预算" : "当前筛选范围"}</button>{view.trail.map((step, index) => <span key={index}><span aria-hidden="true"> / </span><button onClick={() => update(backCockpit(view, index + 1))} aria-current={index === view.trail.length - 1 ? "location" : undefined}>{selectionText(step.path)}</button></span>)}{view.trail.length > 0 && <button className="cockpit-back" onClick={() => update(backCockpit(view, view.trail.length - 1))}>返回上一级</button>}</nav>
          <div className="cockpit-controls"><div className="cockpit-toggle" aria-label="展示方式">{(["chart", "table"] as const).map(mode => <button key={mode} aria-pressed={view.view === mode} onClick={() => update({ ...view, view: mode })}>{mode === "chart" ? "图表" : "数据表"}</button>)}</div>
            <label>对比内容<select aria-label="对比内容" value={view.comparison} onChange={event => update({ ...view, comparison: event.target.value as CockpitView["comparison"] })}><option value="current">今年目标 / 实际 / 预计</option><option value="next">今年目标 / 预计 / 下一年预算</option></select></label>
            <label>排序<select aria-label="分组排序" value={view.sort} onChange={event => update({ ...view, sort: event.target.value as CockpitView["sort"] })}><option value="gap">预计缺口从大到小</option><option value="budget">定版目标从大到小</option><option value="forecast">全年预计从大到小</option><option value="next_budget">下一年预算从大到小</option><option value="name">名称顺序</option></select></label>
            <label>下钻到<select aria-label="下一层下钻维度" value={effectiveNextDimension ?? ""} disabled={!availableDimensions.length} onChange={event => setNextDimension(event.target.value as BudgetDimension)}>{!availableDimensions.length && <option value="">查看最细明细</option>}{availableDimensions.map(key => <option key={key} value={key}>{cockpitLabels[key]}</option>)}</select></label>
            <label>单位<select aria-label="驾驶舱金额单位" value={view.unit} onChange={event => update({ ...view, unit: event.target.value as CockpitView["unit"] })}><option value="wan">万元</option><option value="yuan">元</option></select></label>
          </div>
          {!groups.length ? <p className="cockpit-empty">当前筛选和下钻路径没有匹配结果。可返回上一级或清除筛选，不会自动扩大范围。</p> : view.view === "chart" ? <><div className="cockpit-legend">{metrics.map(metric => <span key={metric} data-series={metric}><i/>{labels[metric]}</span>)}</div><CockpitChart groups={groups} metrics={metrics} labels={labels} unit={view.unit} onDrill={drill}/><div className="cockpit-chart-help"><span>点击柱形继续下钻；可自由切换维度组合。{groups.length > 8 ? "滚动图表可查看其余分组。" : ""}</span><button onClick={() => update({ ...view, view: "table" })}>用数据表下钻</button></div></> : <CockpitTable data={data} view={view} groups={groups} onDrill={drill} onDetails={openDetails}/>}
          <div className="cockpit-chart-footer"><button onClick={() => openDetails()}>查看当前范围明细</button><button onClick={() => void copyLink()}>复制当前视图链接</button><a href={aiHref}>让 AI分析当前范围</a></div>
        </section>
        <section className="cockpit-quarter budget-surface" aria-label="季度分布"><div className="cockpit-section-heading"><h2 className="budget-section-title">季度分布</h2><span>结转收入 · {unitLabel(view)}</span></div><div className="cockpit-legend">{metrics.map(metric => <span key={metric} data-series={metric}><i/>{labels[metric]}</span>)}</div>{quarters.length ? <CockpitChart groups={quarters} metrics={metrics} labels={labels} unit={view.unit} quarters onDrill={group => update(drillCockpit(view, group.path, availableDimensions.find(key => key !== "season")))}/> : <p className="cockpit-empty">当前范围没有季度数据。</p>}<p className="cockpit-caption">点击季度可继续分析。同名季度分属各自财年；下一年预算是草稿测算，不是实际完成。</p></section>
        <p className="cockpit-caption">实际与预计截至 {data.sources.forecast?.as_of ?? "未记录"}；所有图表和导出跟随当前范围。下一年预算的编制基准：{data.sources.next_budget?.source ? `${fy(data.fiscal_year)}全年预测 · ${versionLabel(data.sources.next_budget.source.sequence)}` : "未记录"}。{data.notes.length > 0 && <button onClick={() => { setDetails(null); setSourcesOpen(true); }}>查看 {data.notes.length} 项数据说明</button>}</p>
      </>}
    </main>
    <BudgetUtilityPanel open={sourcesOpen} title="驾驶舱数据来源" onClose={() => setSourcesOpen(false)}>{data ? <>{cockpitMetrics.map(metric => <section className="cockpit-source" key={metric}><h3 className="budget-subsection-title">{cockpitMetricLabels(data)[metric]}</h3><p>{data.definitions[metric]}</p><dl><dt>版本状态</dt><dd>{sourceText(data.sources[metric])}</dd><dt>数据截至</dt><dd>{data.sources[metric]?.as_of ?? "定版档案／不适用"}</dd>{data.sources[metric]?.created_at && <><dt>保存时间</dt><dd>{versionTimestamp(data.sources[metric]!.created_at)}</dd></>}</dl>{cockpitSourceHref(metric, data, view) && <a href={cockpitSourceHref(metric, data, view)!}>查看同范围来源结果</a>}</section>)}{data.sources.next_budget?.source && <p>下一年草稿仍引用{fy(data.fiscal_year)}全年预测{versionLabel(data.sources.next_budget.source.sequence)}，不会自动替换为当前预测。</p>}{data.notes.map(note => <p key={note} className="cockpit-caption">{note}</p>)}<details><summary>精确来源标识</summary><pre>{JSON.stringify(Object.fromEntries(cockpitMetrics.map(metric => [metric, data.sources[metric]])), null, 2)}</pre></details></> : <p>尚未读取到来源，请返回后重试。</p>}</BudgetUtilityPanel>
    <BudgetUtilityPanel open={details !== null} title="驾驶舱范围明细" onClose={() => setDetails(null)}>{data && details !== null && <><p>{selectionText({ ...path, ...details }) || "当前筛选范围"} · {detailRows.length} 条明细</p><p className="cockpit-caption">“未覆盖”表示该来源没有这条业务；“待核对”表示存在明细但数值不可用，不按零处理。</p><ActionTable tableLabel="驾驶舱范围明细" keyboardNavigation exportLabel="导出明细" exportData={cockpitTransfer(data, view, detailRows, true, details)} scrollClassName="cockpit-detail-scroll"><thead><tr>{cockpitDimensions.map(key => <th className="budget-cell-text" key={key}>{cockpitLabels[key]}</th>)}{cockpitMetrics.map(metric => <th className="budget-cell-number" key={metric}>{cockpitMetricLabels(data)[metric]}（{unitLabel(view)}）</th>)}</tr></thead><tbody>{detailRows.slice(detailPage * 30, detailPage * 30 + 30).map(row => <tr key={row.id}>{cockpitDimensions.map(key => <td className="budget-cell-text" key={key}>{row[key]}</td>)}{cockpitMetrics.map(metric => <td className="budget-cell-number" key={metric}>{!row.covered[metric] ? "未覆盖" : row[metric] === null ? "待核对" : cockpitValue(row[metric], view.unit)}</td>)}</tr>)}</tbody></ActionTable><div className="cockpit-pagination"><span>第{detailPage + 1}/{Math.max(1, Math.ceil(detailRows.length / 30))}页</span><button disabled={detailPage === 0} onClick={() => setDetailPage(detailPage - 1)}>上一页</button><button disabled={(detailPage + 1) * 30 >= detailRows.length} onClick={() => setDetailPage(detailPage + 1)}>下一页</button></div></>}</BudgetUtilityPanel>
  </div>;
}

export default BudgetCockpitPage;
