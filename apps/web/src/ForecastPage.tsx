import { useEffect, useMemo, useRef, useState } from "react";
import { parseResponse, type ApiIdentity } from "./api";
import { downloadBudgetWorkbook, type ExportColumn } from "./budgetExport";
import {
  confirmForecast, defaultForecastStates, forecastDimensionLabels, forecastNumber, forecastSeasons, forecastStateLabels,
  getForecast, groupForecastRows, groupForecastIssues, forecastMappingIssues, prepareForecastMapping, forecastPresentationRow, refreshForecast, reviseForecast,
  prepareTrialForecast, trialFinancialDraft, applyTrialFinancialDraft, canConfirmForecast, type ForecastAdjustmentKind, type ForecastAssumptions,
  type ForecastCalculation, type ForecastDimension, type ForecastDimensions, type ForecastEnvelope, type ForecastMapping,
  type ForecastNumber, type ForecastRow, type ForecastSeason, type ForecastState, type ForecastIssue, type ForecastScope, type TrialFinancialDraft, type TrialAssumption,
} from "./forecastApi";
import "./forecast.css";

const dimensions = Object.keys(forecastDimensionLabels) as ForecastDimension[];
const displayNumber = (value: ForecastNumber | undefined, digits = 0) => {
  const parsed = forecastNumber(value);
  return parsed === null ? "—" : parsed.toLocaleString("zh-CN", { maximumFractionDigits: digits });
};
const money = (value: ForecastNumber | undefined) => {
  const parsed = forecastNumber(value);
  return parsed === null ? "—" : `${displayNumber(parsed / 10000, 2)} 万`;
};
const achievement = (value: ForecastNumber | undefined) => {
  const parsed = forecastNumber(value);
  return parsed === null ? "—" : `${displayNumber(parsed * 100, 2)}%`;
};
export function forecastGapLabel(value: ForecastNumber | undefined, finalized = false) {
  const parsed = forecastNumber(value);
  if (parsed === null) return "待预测";
  if (Math.abs(parsed) <= 10000) return "无显著缺口";
  return parsed > 0 ? finalized ? "实际缺口" : "预计缺口" : finalized ? "实际超额" : "预计超额";
}
const gapMoney = (value: ForecastNumber | undefined) => {
  const parsed = forecastNumber(value);
  return parsed === null ? "—" : money(Math.abs(parsed));
};
const methodLabels: Record<ForecastRow["method"], string> = { FROZEN_ACTUAL: "冻结实际", TERMINAL_FORECAST: "季末预测", PARENT_PARAMETER_PROJECTION: "参数推演", UNMAPPED_ACTUAL: "未对应实际", UNMAPPED_FROZEN_CARRYIN: "未对应结转实际" };
export function forecastRowLabel(row: ForecastRow) {
  if (row.data_status === "ACTUAL_UNAVAILABLE") return "实际未对应";
  if (row.data_status === "BASELINE_ESTIMATE") return "预算参数估计";
  if (row.data_status === "TRIAL_SIZE_HELD") return "暂按上一季人数";
  return methodLabels[row.method];
}
const exportColumns: ExportColumn[] = [
  { key: "season", label: "季度" }, ...dimensions.map(key => ({ key, label: forecastDimensionLabels[key] })),
  { key: "budget_revenue", label: "定版预算收入（元）", format: "money" }, { key: "forecast_revenue", label: "预计收入（元）", format: "money" },
  { key: "budget_total", label: "预算人次", format: "count" }, { key: "forecast_total", label: "预计人次", format: "count" },
  { key: "additional", label: "预计后续新增新生", format: "count" }, { key: "new_withdrawal", label: "预计后续新生退班", format: "count" },
  { key: "old_withdrawal", label: "预计后续老生退班", format: "count" }, { key: "method_label", label: "预测方式" },
];

export function ForecastLogic() {
  return <section className="forecast-logic" aria-label="预测逻辑"><h2>预测逻辑</h2><ol>
    <li><strong>暑假</strong><span>已定版实际</span></li>
    <li><strong>秋季</strong><span>当前人次 ＋ 后续新增 − 预计退班</span></li>
    <li><strong>寒假、春季</strong><span>承接上一季预测人数</span></li>
  </ol></section>;
}

export function ForecastTrialStatus({ ready, onAdjust, busy = false }: { ready: boolean; onAdjust?: () => void; busy?: boolean }) {
  return <section className="forecast-trial-status" role="status"><div><strong>试用试算</strong><span>{ready ? "完整预测已准备" : "试用条件待补充"}</span><span>非正式预测</span></div>{onAdjust && <button disabled={busy} onClick={onAdjust}>调整试用假设</button>}</section>;
}

export function TrialAssumptionNotes({ assumptions }: { assumptions: TrialAssumption[] }) {
  const [limits, setLimits] = useState<Record<string, number>>({});
  const groups = new Map<string, TrialAssumption[]>();
  for (const item of assumptions) groups.set(item.code, [...(groups.get(item.code) ?? []), item]);
  return assumptions.length ? <details className="forecast-trial-notes"><summary>查看试用说明 {assumptions.length} 条</summary>{[...groups].map(([code, values]) => <details key={code}><summary><strong>{values[0].message}</strong><span>{values.length} 条</span></summary><ul>{values.slice(0, limits[code] ?? 25).map((item, index) => {
    const detail = item.detail && typeof item.detail === "object" ? item.detail as Record<string, unknown> : {};
    const unapplied = detail.unapplied_renewal != null || detail.unapplied_share != null;
    return <li key={`${code}-${index}`}><span>{[item.season, ...dimensions.map(key => item.dims?.[key])].filter(Boolean).join(" / ")}</span>{item.message !== values[0].message && <span>{item.message}</span>}{unapplied && <strong className="forecast-scope-note">已填写比例暂未用于计算</strong>}</li>;
  })}</ul>{values.length > (limits[code] ?? 25) && <button onClick={() => setLimits({ ...limits, [code]: (limits[code] ?? 25) + 25 })}>再显示 25 条</button>}</details>)}</details> : null;
}

/** Explicit trial settings only. Dimensions and frozen actuals are never editable here. */
export function ForecastTrialScopeEditor({ scopes, states, onChange, disabled = false }: { scopes: ForecastScope[]; states: Record<ForecastSeason, ForecastState>; onChange: (scopes: ForecastScope[]) => void; disabled?: boolean }) {
  const [season, setSeason] = useState<ForecastSeason>("秋季");
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(0);
  const [selected, setSelected] = useState<number | null>(null);
  const [draft, setDraft] = useState<TrialFinancialDraft | null>(null);
  const [error, setError] = useState("");
  const editorRef = useRef<HTMLDivElement>(null);
  const matches = scopes.map((scope, index) => ({ scope, index })).filter(({ scope }) => !query.trim() || dimensions.some(key => scope.dims[key].includes(query.trim())));
  const pageRows = matches.slice(page * 20, (page + 1) * 20);
  const currentScope = selected === null ? null : scopes[selected];
  function selectScope(index: number) { setSelected(index); setDraft(trialFinancialDraft(scopes[index], season)); setError(""); requestAnimationFrame(() => editorRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" })); }
  function changeSeason(next: ForecastSeason) { setSeason(next); setPage(0); setError(""); if (selected !== null) setDraft(trialFinancialDraft(scopes[selected], next)); }
  function apply() {
    if (!currentScope || selected === null || !draft || disabled) return;
    const result = applyTrialFinancialDraft(currentScope, season, states[season], draft);
    if (!result.scope) { setError(result.error); return; }
    onChange(scopes.map((item, index) => index === selected ? result.scope! : item)); setSelected(null); setDraft(null); setError("");
  }
  return <div className="forecast-trial-scopes">
    <div className="forecast-trial-scope-filters"><label>季度<select value={season} onChange={event => changeSeason(event.target.value as ForecastSeason)}>{forecastSeasons.map(value => <option key={value}>{value}</option>)}</select></label><label>业务明细<input value={query} placeholder="校区、产品、年级或科目" onChange={event => { setQuery(event.target.value); setPage(0); }}/></label></div>
    <div className="forecast-table-wrap" tabIndex={0} aria-label="试用假设明细表"><table><thead><tr><th>业务维度</th><th>学费（元）</th><th>折扣</th><th>确认比例</th><th>续班率</th><th>新生占比</th><th>后续新增</th><th>操作</th></tr></thead><tbody>{pageRows.map(({ scope, index }) => {
      const financial = scope.financial[season], readonly = scope.kind === "FINANCIAL_ONLY" || states[season] === "FINALIZED";
      return <tr key={JSON.stringify(scope.dims)}><th>{scope.dims.campus}<small>{scope.dims.track} / {scope.dims.product} / {scope.dims.grade} / {scope.dims.subject}</small></th><td>{displayNumber(financial?.price, 2)}</td><td>{achievement(financial?.discount)}</td><td>{achievement(financial?.recognition)}</td><td>{achievement(financial?.renewal)}</td><td>{achievement(financial?.share)}</td><td>{scope.additional[season] === undefined ? "—" : displayNumber(scope.additional[season])}</td><td>{readonly ? <span className="forecast-scope-readonly">{scope.kind === "FINANCIAL_ONLY" ? "结转实际" : "已定版实际"}</span> : <button disabled={disabled} aria-label={`调整${season} ${dimensions.map(key => scope.dims[key]).join(" / ")}试用假设`} onClick={() => selectScope(index)}>调整</button>}</td></tr>;
    })}</tbody></table>{!matches.length && <p className="forecast-empty">当前范围没有独立试用明细</p>}</div>
    <div className="forecast-pagination"><button disabled={page === 0} onClick={() => setPage(page - 1)}>上一页</button><span>{matches.length ? page + 1 : 0} / {Math.ceil(matches.length / 20)} 页</span><button disabled={(page + 1) * 20 >= matches.length} onClick={() => setPage(page + 1)}>下一页</button></div>
    {currentScope && draft && <div className="forecast-scope-editor" ref={editorRef}><header><div><strong>{season} · {currentScope.dims.campus}</strong><span>{currentScope.dims.track} / {currentScope.dims.product} / {currentScope.dims.grade} / {currentScope.dims.subject}</span></div><button disabled={disabled} onClick={() => { setSelected(null); setDraft(null); setError(""); }}>取消</button></header>
      <fieldset disabled={disabled || states[season] === "FINALIZED" || currentScope.kind === "FINANCIAL_ONLY"}><div className="forecast-scope-fields">{(["price", "discount", "recognition", "renewal", "share"] as const).map(key => <label key={key}>{{ price: "学费（元）", discount: "折扣（%）", recognition: "确认比例（%）", renewal: "续班率（%）", share: "新生占比（%）" }[key]}<input type="number" min="0" max={key === "price" ? "1000000000" : "100"} step="any" placeholder={key === "renewal" || key === "share" ? "未设定" : undefined} value={draft[key]} onChange={event => setDraft({ ...draft, [key]: event.target.value })}/></label>)}
      {(states[season] === "OPEN" || states[season] === "ENDED") && <label>后续新增新生（人次）<input type="number" min="0" max="10000000" step="1" value={draft.additional} onChange={event => setDraft({ ...draft, additional: event.target.value })}/></label>}</div>
      {(!draft.renewal.trim() || !draft.share.trim()) && <p className="forecast-scope-note">未设定项自动参考同产品、同年级；无可用参数时暂按上季人数。</p>}
      <label>试用调整原因<input maxLength={1000} value={draft.reason} onChange={event => setDraft({ ...draft, reason: event.target.value })}/></label>{error && <p className="forecast-scope-error" role="alert">{error}</p>}<button type="button" onClick={apply}>加入本次修改</button></fieldset>
    </div>}
  </div>;
}

export function ForecastIssueGroups({ issues }: { issues: ForecastIssue[] }) {
  const [opened, setOpened] = useState<Set<string>>(new Set());
  const [limits, setLimits] = useState<Record<string, number>>({});
  const groups = groupForecastIssues(issues);
  return <div className="forecast-issue-groups">{groups.map((group, index) => {
    const open = opened.has(group.key), limit = limits[group.key] ?? 25;
    return <section key={group.key}><button className="forecast-issue-group-toggle" aria-expanded={open} aria-controls={`forecast-issue-group-${index}`} onClick={() => setOpened(previous => { const next = new Set(previous); if (next.has(group.key)) next.delete(group.key); else next.add(group.key); return next; })}>
      <span aria-hidden="true">{open ? "−" : "+"}</span><strong>{group.title}</strong>{group.season && <span>{group.season}</span>}{group.code === "UPSTREAM_HEADCOUNT_UNAVAILABLE" && <span className="forecast-auto-condition">无需单独处理</span>}<span className={`forecast-issue-state is-${group.severity.toLowerCase()}`}>{group.issues.length} 条明细{group.severity === "WARNING" ? "提示" : ""}</span>
    </button>{open && <div id={`forecast-issue-group-${index}`}><ul>{group.issues.slice(0, limit).map((issue, itemIndex) => <li key={`${group.key}-${itemIndex}`}><div><strong>{issue.message}</strong>{(issue.season || issue.dims) && <span>{[issue.season, ...dimensions.map(key => issue.dims?.[key])].filter(Boolean).join(" / ")}</span>}</div></li>)}</ul>
      {group.issues.length > limit && <button onClick={() => setLimits({ ...limits, [group.key]: limit + 25 })}>再显示 25 项</button>}<details className="forecast-group-code"><summary>技术信息</summary><code>{group.code}</code></details>
    </div>}</section>;
  })}</div>;
}

/** Presentation only: the server owns the forecasting rules and official readiness. */
export function ForecastDashboard({ calculation, busy = false, onAdjust, trialMode = false }: { calculation: ForecastCalculation; busy?: boolean; onAdjust?: () => void; trialMode?: boolean }) {
  const [group, setGroup] = useState<ForecastDimension>("track");
  const [season, setSeason] = useState(() => trialMode ? calculation.quarters.find(quarter => quarter.state === "OPEN")?.season ?? "" : "");
  const previousTrialMode = useRef(trialMode);
  useEffect(() => { if (trialMode && !previousTrialMode.current) setSeason(calculation.quarters.find(quarter => quarter.state === "OPEN")?.season ?? ""); previousTrialMode.current = trialMode; }, [trialMode, calculation.quarters]);
  const [scope, setScope] = useState<Partial<ForecastDimensions>>({});
  const [exportError, setExportError] = useState("");
  const [exporting, setExporting] = useState(false);
  const [detailPage, setDetailPage] = useState(0);
  const rows = useMemo(() => calculation.rows.filter(row => (!season || row.season === season) && dimensions.every(key => !scope[key] || row[key] === scope[key])), [calculation, season, scope]);
  const grouped = useMemo(() => groupForecastRows(rows, group), [rows, group]);
  const max = Math.max(1, ...calculation.quarters.flatMap(quarter => [forecastNumber(quarter.budget_revenue), forecastNumber(quarter.forecast_revenue)]).filter((value): value is number => value !== null).map(Math.abs));
  const blockers = calculation.issues.filter(issue => issue.severity === "BLOCKING");
  const blockingGroups = groupForecastIssues(blockers).length;
  const complete = calculation.ready || (trialMode && calculation.trial_ready === true);
  const pageRows = rows.slice(detailPage * 30, (detailPage + 1) * 30).map(forecastPresentationRow);
  async function download() {
    setExporting(true); setExportError("");
    try { await downloadBudgetWorkbook(exportColumns, rows.map(row => ({ ...forecastPresentationRow(row), method_label: forecastRowLabel(row) })), trialMode ? "FY27-试用预测明细" : "FY27-达成预测明细"); }
    catch (error) { setExportError(error instanceof Error ? error.message : "导出失败"); }
    finally { setExporting(false); }
  }
  return <>
    <section className="forecast-metrics" aria-label="FY27 部门全年预测">
      <div><span>定版预算</span><strong>{money(calculation.annual.budget_revenue)}</strong></div>
      <div className={complete ? "forecast-key-metric" : ""}><span>预计全年收入</span><strong>{money(calculation.annual.forecast_revenue)}</strong></div>
      <div className={forecastNumber(calculation.annual.gap) !== null && forecastNumber(calculation.annual.gap)! > 10000 ? "forecast-gap" : ""}><span>{forecastGapLabel(calculation.annual.gap)}</span><strong>{gapMoney(calculation.annual.gap)}</strong></div>
      <div><span>预计达成率</span><strong>{achievement(calculation.annual.achievement)}</strong></div>
    </section>
    {!complete && <section className="forecast-blocked" role="status"><strong>{blockingGroups ? `${blockingGroups} 类预测条件待处理` : "尚不能完成预测"}</strong>{onAdjust && <button type="button" disabled={busy} onClick={onAdjust}>处理预测条件</button>}</section>}
    <section className="forecast-section"><header><h2>季度达成</h2><div className="forecast-legend"><span><i className="is-budget"/>预算</span><span><i/>预计 / 实际</span></div></header>
      <div className="forecast-quarter-bars" role="img" aria-label={calculation.quarters.map(quarter => `${quarter.season}，${forecastStateLabels[quarter.state]}，预算${money(quarter.budget_revenue)}，${quarter.state === "FINALIZED" ? "冻结实际" : "预计"}${money(quarter.forecast_revenue)}`).join("；")}>
        {calculation.quarters.map(quarter => <div key={quarter.season}><div className="forecast-quarter-name"><strong>{quarter.season}</strong><span className={`forecast-state is-${quarter.state.toLowerCase()}`}>{forecastStateLabels[quarter.state]}</span></div>
          <div className="forecast-bar-row"><span>预算</span><div className="forecast-bar-track">{forecastNumber(quarter.budget_revenue) !== null ? <i className={`is-budget ${forecastNumber(quarter.budget_revenue)! < 0 ? "is-negative" : ""}`} style={{ transform: `scaleX(${Math.abs(forecastNumber(quarter.budget_revenue)!) / max})` }}/> : <em>未提供</em>}</div><strong>{money(quarter.budget_revenue)}</strong></div>
          <div className="forecast-bar-row"><span>{quarter.state === "FINALIZED" ? "实际" : "预计"}</span><div className="forecast-bar-track">{forecastNumber(quarter.forecast_revenue) !== null ? <i className={forecastNumber(quarter.forecast_revenue)! < 0 ? "is-negative" : ""} style={{ transform: `scaleX(${Math.abs(forecastNumber(quarter.forecast_revenue)!) / max})` }}/> : <em>待预测</em>}</div><strong>{money(quarter.forecast_revenue)}</strong></div>
          <div className="forecast-quarter-gap"><span>{forecastGapLabel(quarter.gap, quarter.state === "FINALIZED")}</span><strong>{gapMoney(quarter.gap)}</strong></div>
        </div>)}
      </div>
      <div className="forecast-table-wrap" tabIndex={0} aria-label="季度达成明细表"><table><thead><tr><th>季度</th><th>状态</th><th>预算收入（万元）</th><th>预计 / 实际收入（万元）</th><th>缺口 / 超额（万元）</th><th>预算人次</th><th>预计 / 实际人次</th></tr></thead><tbody>{calculation.quarters.map(quarter => <tr key={quarter.season}><th>{quarter.season}</th><td>{forecastStateLabels[quarter.state]}</td><td>{displayNumber(forecastNumber(quarter.budget_revenue) === null ? null : forecastNumber(quarter.budget_revenue)! / 10000, 2)}</td><td>{displayNumber(forecastNumber(quarter.forecast_revenue) === null ? null : forecastNumber(quarter.forecast_revenue)! / 10000, 2)}</td><td>{forecastNumber(quarter.gap) === null ? "—" : `${forecastNumber(quarter.gap)! >= 0 ? "缺口" : "超额"} ${displayNumber(Math.abs(forecastNumber(quarter.gap)!) / 10000, 2)}`}</td><td>{displayNumber(quarter.budget_total)}</td><td>{displayNumber(quarter.forecast_total)}</td></tr>)}</tbody></table></div>
    </section>
    <section className="forecast-section"><header><h2>维度达成</h2><div className="forecast-group-switch" role="group" aria-label="分组维度">{(["track", "campus", "grade"] as ForecastDimension[]).map(key => <button key={key} aria-pressed={group === key} onClick={() => setGroup(key)}>{forecastDimensionLabels[key]}</button>)}</div></header>
      <div className="forecast-filters"><label>季度<select value={season} onChange={event => { setSeason(event.target.value); setDetailPage(0); }}><option value="">全部</option>{forecastSeasons.map(value => <option key={value}>{value}</option>)}</select></label>{dimensions.map(key => <label key={key}>{forecastDimensionLabels[key]}<select value={scope[key] ?? ""} onChange={event => { setScope({ ...scope, [key]: event.target.value }); setDetailPage(0); }}><option value="">全部</option>{[...new Set(calculation.rows.map(row => row[key]))].sort((a, b) => a.localeCompare(b, "zh-CN")).map(value => <option key={value}>{value}</option>)}</select></label>)}</div>
      {(season || dimensions.some(key => scope[key])) && <div className="forecast-filter-scope"><span>{[season, ...dimensions.map(key => scope[key])].filter(Boolean).join(" / ")}</span><button onClick={() => { setSeason(""); setScope({}); setDetailPage(0); }}>清除筛选</button></div>}
      {!season && calculation.rows.some(row => row.season === "暑假" && row.data_status === "ACTUAL_UNAVAILABLE") && <p className="forecast-scope-note">部分暑假明细未对应，全年分组暂不合计；可按季度查看。</p>}
      <div className="forecast-table-wrap" tabIndex={0} aria-label="维度达成表"><table><thead><tr><th>{forecastDimensionLabels[group]}</th><th>预算收入（万元）</th><th>预计 / 实际收入（万元）</th><th>缺口 / 超额（万元）</th><th>达成率</th><th>预算人次</th><th>预计 / 实际人次</th></tr></thead><tbody>{grouped.map(item => <tr key={item.name}><th>{item.name || "未填写"}{item.unmapped && <span className="forecast-unmapped">含未对应实际</span>}{!item.unmapped && item.hasUnbudgeted && <span className="forecast-unmapped">含未单列业务</span>}</th><td>{item.hasUnbudgeted && !item.unmapped && item.budget_revenue === null ? "未单列" : displayNumber(forecastNumber(item.budget_revenue) === null ? null : forecastNumber(item.budget_revenue)! / 10000, 2)}</td><td>{displayNumber(forecastNumber(item.forecast_revenue) === null ? null : forecastNumber(item.forecast_revenue)! / 10000, 2)}</td><td>{forecastNumber(item.gap) === null ? "—" : `${forecastNumber(item.gap)! >= 0 ? "缺口" : "超额"} ${displayNumber(Math.abs(forecastNumber(item.gap)!) / 10000, 2)}`}</td><td>{achievement(item.achievement)}</td><td>{item.hasUnbudgeted && !item.unmapped && item.budget_total === null ? "未单列" : displayNumber(item.budget_total)}</td><td>{displayNumber(item.forecast_total)}</td></tr>)}</tbody></table>{!rows.length && <p className="forecast-empty">当前范围暂无预测明细</p>}</div>
    </section>
    {calculation.issues.length > 0 && <section className="forecast-section forecast-issues"><details open={!complete}><summary><span>{trialMode ? "正式确认条件" : "预测条件"} {blockingGroups} 类</span><span>阻断明细 {blockers.length}</span><span>提示 {calculation.issues.length - blockers.length}</span></summary><ForecastIssueGroups issues={calculation.issues}/></details></section>}
    <section className="forecast-section"><details><summary className="forecast-detail-summary"><h2>预测明细</h2><span>{rows.length} 条</span></summary><header><div/><button disabled={busy || exporting || !rows.length} onClick={() => void download()}>{exporting ? "正在导出…" : "导出明细"}</button></header>{exportError && <p role="alert">{exportError}</p>}
      <div className="forecast-table-wrap" tabIndex={0} aria-label="预测明细表"><table><thead><tr><th>季度</th><th>校区 / 赛道 / 产品</th><th>年级 / 科目</th><th>预算收入（元）</th><th>预计 / 实际收入（元）</th><th>预算人次</th><th>预计 / 实际人次</th><th>预测方式</th></tr></thead><tbody>{pageRows.map((row, index) => <tr key={`${row.season}-${JSON.stringify(dimensions.map(key => row[key]))}-${index}`}><th>{row.season}</th><td>{row.campus}<small>{row.track} / {row.product}</small></td><td>{row.grade}<small>{row.subject}</small></td><td>{row.budget_covered === false && !row.unmapped ? "未单列" : displayNumber(row.budget_revenue, 2)}</td><td>{displayNumber(row.forecast_revenue, 2)}</td><td>{row.counts_not_applicable ? "—" : row.budget_covered === false && !row.unmapped ? "未单列" : displayNumber(row.budget_total)}</td><td>{row.counts_not_applicable ? "—" : displayNumber(row.forecast_total)}</td><td>{forecastRowLabel(row)}</td></tr>)}</tbody></table></div>
      <div className="forecast-pagination"><button disabled={detailPage === 0} onClick={() => setDetailPage(detailPage - 1)}>上一页</button><span>{rows.length ? detailPage + 1 : 0} / {Math.ceil(rows.length / 30)} 页</span><button disabled={(detailPage + 1) * 30 >= rows.length} onClick={() => setDetailPage(detailPage + 1)}>下一页</button></div>
    </details></section>
  </>;
}

const emptyDims = (): ForecastDimensions => ({ track: "", product: "", grade: "", subject: "", campus: "" });
export function ForecastPage() {
  const [identity, setIdentity] = useState<ApiIdentity | null>(null);
  const [data, setData] = useState<ForecastEnvelope | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [trialSettingsOpen, setTrialSettingsOpen] = useState(false);
  const [assumptions, setAssumptions] = useState<ForecastAssumptions>({ states: { ...defaultForecastStates }, minimum_sample: 30, mappings: [], adjustments: [] });
  const [reason, setReason] = useState("");
  const [mapping, setMapping] = useState<ForecastMapping>({ source: emptyDims(), target: emptyDims(), reason: "" });
  const [adjustment, setAdjustment] = useState({ season: "秋季", campus: "", kind: "additional" as ForecastAdjustmentKind, value: "", reason: "" });
  const [showConfirm, setShowConfirm] = useState(false);
  const [confirmationReason, setConfirmationReason] = useState("");
  const lock = useRef(false);
  const settingsRef = useRef<HTMLDetailsElement>(null);
  const trialSettingsRef = useRef<HTMLDetailsElement>(null);
  const version = data?.version ?? null;
  const trialMode = version?.assumptions.trial_mode === true;
  const canMaintain = Boolean(identity?.roles.some(role => ["DATA_MAINTAINER", "DEPARTMENT_BUDGET_OWNER"].includes(role)));
  const canEditBasis = Boolean(identity?.roles.includes("DATA_MAINTAINER"));
  useEffect(() => {
    const controller = new AbortController();
    void (async () => {
      try {
        const actor = await parseResponse<ApiIdentity>(await fetch("/api/v1/local-trial/session", { signal: controller.signal }));
        const current = await getForecast(actor, controller.signal);
        if (!controller.signal.aborted) { setIdentity(actor); setData(current); if (current.version) setAssumptions(current.version.assumptions); }
      } catch (failure) { if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : "读取失败"); }
    })();
    return () => controller.abort();
  }, []);
  async function perform(label: string, operation: () => Promise<ForecastEnvelope>) {
    if (lock.current) return;
    lock.current = true; setBusy(label); setError(""); setNotice("");
    try { const current = await operation(); setData(current); if (current.version) setAssumptions(current.version.assumptions); setReason(""); setShowConfirm(false); setConfirmationReason(""); setNotice(label === "确认预测" ? "预测已确认" : label === "准备试用预测" ? "试用预测已准备" : label === "重新读取" ? "已读取最新预测" : "新预测已保存"); }
    catch (failure) { setError(failure instanceof Error ? failure.message : "操作未完成"); }
    finally { lock.current = false; setBusy(""); }
  }
  function openSettings() { setSettingsOpen(true); requestAnimationFrame(() => settingsRef.current?.scrollIntoView({ behavior: "smooth", block: "start" })); }
  function openTrialSettings() { setTrialSettingsOpen(true); requestAnimationFrame(() => trialSettingsRef.current?.scrollIntoView({ behavior: "smooth", block: "start" })); }
  const existingCampuses = [...new Set(version?.calculation.campuses.map(item => item.campus) ?? [])].sort((a, b) => a.localeCompare(b, "zh-CN"));
  const sourceOptions = forecastMappingIssues(version?.calculation.issues ?? []);
  const budgetDimensionOptions = useMemo(() => {
    const budgetRows = version?.calculation.rows.filter(row => !row.unmapped && !["UNMAPPED_ACTUAL", "UNMAPPED_FROZEN_CARRYIN"].includes(row.method)) ?? [];
    return Object.fromEntries(dimensions.map(key => [key, [...new Set(budgetRows.map(row => row[key]))].sort((a, b) => a.localeCompare(b, "zh-CN"))])) as Record<ForecastDimension, string[]>;
  }, [version]);
  const settingChanged = version ? JSON.stringify(assumptions) !== JSON.stringify(version.assumptions) : false;
  return <div className="budget-shell decision-shell forecast-shell">
    <a className="skip-link" href="#forecast-content">跳到主要内容</a>
    <header className="decision-topbar"><a className="decision-brand" href="/budget/workspace?archive=fy27&step=5"><span aria-hidden="true">智</span><strong>预算智解</strong></a><span className="forecast-year">FY27 学校版</span><div className="decision-version"><span className={`status-dot ${version?.status === "CONFIRMED" && !trialMode ? "is-published" : ""}`}/><span>{trialMode ? "试用试算" : version ? version.status === "CONFIRMED" ? "预测已确认" : "预测草稿" : "尚未预测"}</span>{version && <small>P{version.sequence}</small>}</div></header>
    <main className="decision-workspace" id="forecast-content">
      <nav className="forecast-navigation" aria-label="FY27 功能"><a href="/budget/workspace?archive=fy27&step=5">查看预算</a><a href="/budget/forecast?archive=fy27" aria-current="page">达成预测</a></nav>
      <header className="forecast-heading"><h1>达成预测</h1><div>{version && <span>截至 {version.as_of}</span>}{version && !trialMode && <button className="forecast-primary" disabled={!identity || !canEditBasis || Boolean(busy) || settingChanged} onClick={() => identity && void perform("准备试用预测", () => prepareTrialForecast(identity, version.id, "准备 FY27 试用预测"))}>{busy === "准备试用预测" ? "正在准备…" : "准备试用预测"}</button>}<button className={version && !trialMode ? "" : "forecast-primary"} disabled={!identity || !canEditBasis || Boolean(busy) || settingChanged} onClick={() => identity && void perform("更新预测", () => refreshForecast(identity, version?.states ?? defaultForecastStates, version ? "更新 FY27 达成预测" : "首次 FY27 自然预测"))}>{busy === "更新预测" ? "正在更新…" : version ? "更新预测" : "生成预测"}</button></div></header>
      {busy && <div className="forecast-working" role="status" aria-live="polite"><span/>{busy === "更新预测" ? "正在读取最新数据并生成预测…" : `${busy}中…`}</div>}
      {error && <section className="forecast-error" role="alert"><strong>操作未完成</strong><p>{error}</p><button disabled={Boolean(busy)} onClick={() => identity ? void perform("重新读取", () => getForecast(identity)) : location.reload()}>重新读取</button></section>}
      {notice && <p className="forecast-notice" role="status">{notice}</p>}
      {!data && !error ? <div className="forecast-skeleton" aria-label="正在读取预测" role="status"><div/><div/><div/></div> : !version ? <section className="forecast-start"><h2>先生成 FY27 预测</h2><div className="forecast-start-states">{forecastSeasons.map(season => <div key={season}><strong>{season}</strong><span>{forecastStateLabels[defaultForecastStates[season]]}</span></div>)}</div></section> : <>
        {trialMode && <ForecastTrialStatus ready={version.calculation.trial_ready === true} busy={Boolean(busy)} onAdjust={canMaintain ? openTrialSettings : undefined}/>}
        <ForecastDashboard calculation={version.calculation} trialMode={trialMode} busy={Boolean(busy)} onAdjust={canMaintain ? trialMode ? openTrialSettings : openSettings : undefined}/>
        <ForecastLogic/>
        {trialMode && <details className="forecast-section forecast-trial-settings" open={trialSettingsOpen} onToggle={event => setTrialSettingsOpen(event.currentTarget.open)} ref={trialSettingsRef}><summary>试用假设 {(assumptions.forecast_scopes ?? []).length} 项</summary>
          <TrialAssumptionNotes assumptions={version.calculation.trial_assumptions ?? []}/>
          <ForecastTrialScopeEditor key={version.id} scopes={assumptions.forecast_scopes ?? []} states={assumptions.states} disabled={Boolean(busy) || !canMaintain} onChange={scopes => { setAssumptions({ ...assumptions, forecast_scopes: scopes }); if (!reason.trim()) setReason("调整 FY27 试用预测假设"); }}/>
          <fieldset disabled={Boolean(busy) || !canMaintain}><label>本次修改原因<input value={reason} onChange={event => setReason(event.target.value)}/></label><div className="forecast-settings-actions"><button type="button" className="forecast-primary" disabled={!settingChanged || !reason.trim()} onClick={() => identity && void perform("保存预测", () => reviseForecast(identity, version.id, assumptions, reason.trim()))}>保存并重算</button>{settingChanged && <span>未保存</span>}</div></fieldset>
        </details>}
        <details className="forecast-section forecast-settings" open={settingsOpen} onToggle={event => setSettingsOpen(event.currentTarget.open)} ref={settingsRef}><summary>{trialMode ? "高级预测设置" : "预测设置"}</summary>
          <fieldset disabled={Boolean(busy) || !canMaintain}><legend>季度状态</legend><div className="forecast-state-fields">{forecastSeasons.map(season => <label key={season}>{season}<select value={assumptions.states[season]} disabled={!canEditBasis || version.states[season] === "FINALIZED"} onChange={event => setAssumptions({ ...assumptions, states: { ...assumptions.states, [season]: event.target.value as ForecastState } })}>{Object.entries(forecastStateLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>)}</div>
          <label className="forecast-sample-field">历史最小样本<input type="number" min="1" max="1000000" step="1" disabled={!canEditBasis} value={assumptions.minimum_sample} onChange={event => setAssumptions({ ...assumptions, minimum_sample: Number(event.target.value) })}/></label>
          <details className="forecast-adjustments"><summary>调整人数 {assumptions.adjustments.length}</summary>
            {version.calculation.campuses.length > 0 && <div className="forecast-table-wrap" tabIndex={0} aria-label="校区预测人数"><table><thead><tr><th>季度</th><th>校区</th><th>预算新生</th><th>当前业务新生</th><th>预算内后续新增</th><th>独立业务新增</th><th>后续新增合计</th><th>后续新生退班</th><th>后续老生退班</th></tr></thead><tbody>{version.calculation.campuses.map(item => <tr key={`${item.season}-${item.campus}`}><th>{item.season}</th><td>{item.campus}</td><td>{displayNumber(item.budget_new)}</td><td>{displayNumber(item.current_business_new)}</td><td>{displayNumber(item.budgeted_additional)}</td><td>{displayNumber(item.unbudgeted_additional)}</td><td>{displayNumber(item.additional)}</td><td>{displayNumber(item.new_withdrawal)}</td><td>{displayNumber(item.old_withdrawal)}</td></tr>)}</tbody></table></div>}
            <div className="forecast-adjustment-fields"><label>季度<select value={adjustment.season} onChange={event => setAdjustment({ ...adjustment, season: event.target.value })}>{forecastSeasons.filter(season => assumptions.states[season] !== "FINALIZED" && assumptions.states[season] !== "FUTURE").map(season => <option key={season}>{season}</option>)}</select></label><label>校区<select value={adjustment.campus} onChange={event => setAdjustment({ ...adjustment, campus: event.target.value })}><option value="">选择校区</option>{existingCampuses.map(campus => <option key={campus}>{campus}</option>)}</select></label><label>项目<select value={adjustment.kind} onChange={event => setAdjustment({ ...adjustment, kind: event.target.value as ForecastAdjustmentKind })}><option value="additional">预算内后续新增</option><option value="new_withdrawal">后续新生退班</option><option value="old_withdrawal">后续老生退班</option></select></label><label>人次<input type="number" min="0" step="1" value={adjustment.value} onChange={event => setAdjustment({ ...adjustment, value: event.target.value })}/></label></div>
            <label>调整原因<input value={adjustment.reason} onChange={event => setAdjustment({ ...adjustment, reason: event.target.value })}/></label>
            <button type="button" disabled={!adjustment.campus || !/^\d+$/.test(adjustment.value) || !adjustment.reason.trim() || !Number.isSafeInteger(Number(adjustment.value)) || !["OPEN", "ENDED"].includes(assumptions.states[adjustment.season as ForecastSeason])} onClick={() => { const next = { ...adjustment, value: Number(adjustment.value), reason: adjustment.reason.trim() }; setAssumptions({ ...assumptions, adjustments: [...assumptions.adjustments.filter(item => !(item.season === next.season && item.campus === next.campus && item.kind === next.kind)), next] }); setAdjustment({ ...adjustment, value: "", reason: "" }); }}>加入本次调整</button>
            {assumptions.adjustments.length > 0 && <ul className="forecast-edits">{assumptions.adjustments.map((item, index) => <li key={`${item.season}-${item.campus}-${item.kind}`}><span>{item.season} / {item.campus} / {item.kind === "additional" ? "预算内后续新增" : item.kind === "new_withdrawal" ? "新生退班" : "老生退班"}：{item.value} 人次</span><button type="button" onClick={() => setAssumptions({ ...assumptions, adjustments: assumptions.adjustments.filter((_, i) => i !== index) })}>移除</button></li>)}</ul>}
          </details>
          <details className="forecast-mappings"><summary>业务维度对应 {assumptions.mappings.length}</summary><fieldset disabled={!canEditBasis}>
            {sourceOptions.length > 0 && <label>待对应明细<select value="" onChange={event => { const issue = sourceOptions[Number(event.target.value)]; if (event.target.value && issue?.dims) setMapping(prepareForecastMapping({ ...emptyDims(), ...issue.dims })); }}><option value="">选择一条明细</option>{sourceOptions.map((issue, index) => <option key={`${issue.code}-${index}`} value={String(index)}>{[issue.season, ...dimensions.map(key => issue.dims?.[key])].filter(Boolean).join(" / ")}</option>)}</select></label>}
            <div className="forecast-mapping-pair">{(["source", "target"] as const).map(side => <div key={side}><h3>{side === "source" ? "实际数据维度" : "预算维度"}</h3>{dimensions.map(key => <label key={key}>{forecastDimensionLabels[key]}<input value={mapping[side][key]} list={side === "target" ? `forecast-budget-${key}` : undefined} onChange={event => setMapping({ ...mapping, [side]: { ...mapping[side], [key]: event.target.value } })}/></label>)}</div>)}</div>
            {dimensions.map(key => <datalist id={`forecast-budget-${key}`} key={key}>{budgetDimensionOptions[key].map(value => <option key={value} value={value}/>)}</datalist>)}
            <label>对应原因<input value={mapping.reason} onChange={event => setMapping({ ...mapping, reason: event.target.value })}/></label>
            <button type="button" disabled={!mapping.reason.trim() || dimensions.some(key => !mapping.source[key].trim() || !mapping.target[key].trim())} onClick={() => { const next = { source: Object.fromEntries(dimensions.map(key => [key, mapping.source[key].trim()])) as ForecastDimensions, target: Object.fromEntries(dimensions.map(key => [key, mapping.target[key].trim()])) as ForecastDimensions, reason: mapping.reason.trim() }; setAssumptions({ ...assumptions, mappings: [...assumptions.mappings.filter(item => JSON.stringify(item.source) !== JSON.stringify(next.source)), next] }); setMapping({ source: emptyDims(), target: emptyDims(), reason: "" }); }}>加入维度对应</button>
            {assumptions.mappings.length > 0 && <ul className="forecast-edits">{assumptions.mappings.map((item, index) => <li key={JSON.stringify(item.source)}><span>{dimensions.map(key => item.source[key]).join(" / ")}<br/>→ {dimensions.map(key => item.target[key]).join(" / ")}</span><button type="button" onClick={() => setAssumptions({ ...assumptions, mappings: assumptions.mappings.filter((_, i) => i !== index) })}>移除</button></li>)}</ul>}
          </fieldset></details>
          <label>本次修改原因<input value={reason} onChange={event => setReason(event.target.value)}/></label><div className="forecast-settings-actions"><button className="forecast-primary" type="button" disabled={!settingChanged || !reason.trim() || !Number.isInteger(assumptions.minimum_sample) || assumptions.minimum_sample < 1} onClick={() => identity && void perform("保存预测", () => reviseForecast(identity, version.id, assumptions, reason.trim()))}>保存为新预测</button>{settingChanged && <span>未保存</span>}</div>
          </fieldset>
        </details>
        <section className="forecast-confirm"><div><strong>{trialMode ? "试用试算不可正式确认" : version.status === "CONFIRMED" ? "预测已确认" : "确认这版预测"}</strong><span>P{version.sequence} · {version.as_of}</span></div>{version.status !== "CONFIRMED" && <button className="forecast-primary" disabled={Boolean(busy) || !canConfirmForecast(version, identity, settingChanged)} onClick={() => setShowConfirm(true)}>确认预测</button>}
          {showConfirm && !trialMode && <form onSubmit={event => { event.preventDefault(); if (identity && confirmationReason.trim() && canConfirmForecast(version, identity, settingChanged)) void perform("确认预测", () => confirmForecast(identity, version.id, confirmationReason.trim())); }}><label>确认说明<input autoFocus required value={confirmationReason} onChange={event => setConfirmationReason(event.target.value)}/></label><div><button className="forecast-primary" disabled={Boolean(busy) || !confirmationReason.trim() || !canConfirmForecast(version, identity, settingChanged)} type="submit">确认</button><button disabled={Boolean(busy)} type="button" onClick={() => setShowConfirm(false)}>取消</button></div></form>}
        </section>
        <details className="forecast-history"><summary>预测记录 {data?.history.length ?? 0}</summary><ol>{data?.history.map(item => <li key={item.id}><strong>P{item.sequence}</strong><span>{item.status === "CONFIRMED" ? "已确认" : "草稿"}</span><time>{item.as_of}</time>{item.id === version.id && <span>当前</span>}</li>)}</ol></details>
      </>}
    </main>
  </div>;
}
