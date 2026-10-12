import { ActionTable } from "./ActionTable";
import { useEffect, useMemo, useRef, useState, type MouseEvent, type ReactNode } from "react";
import { newRequestId, parseResponse, type ApiIdentity } from "./api";
import { FY27Header } from "./FY27Header";
import { ForecastDataNotice, ForecastDataReview, forecastConfirmationHint } from "./ForecastDataReview";
import { downloadBudgetWorkbook, type ExportColumn } from "./budgetExport";
import {
  confirmForecast, defaultForecastStates, forecastDimensionLabels, forecastNumber, forecastSeasons, forecastStateLabels,
  getForecast, getForecastVersion, downloadForecastSnapshot, forecastFailure, forecastFailureCategory, groupForecastRows, groupForecastIssues, forecastMappingIssues, prepareForecastMapping, forecastPresentationRow, forecastActualRevenueDefaultsToZero, forecastActualHeadcountDefaultsToZero, refreshForecast, reviseForecast,
  trialFinancialDraft, applyTrialFinancialDraft, canConfirmForecast, usesFixedForecastRules, forecastCalculationComplete, fixedForecastRulesVersion, type ForecastAdjustmentKind, type ForecastAssumptions,
  type ForecastCalculation, type ForecastDimension, type ForecastDimensions, type ForecastEnvelope, type ForecastMapping,
  type ForecastNumber, type ForecastRow, type ForecastSeason, type ForecastState, type ForecastIssue, type ForecastScope, type TrialFinancialDraft, type TrialAssumption, type ForecastVersion, type ForecastRequestError,
} from "./forecastApi";
import "./forecast.css";

const dimensions = Object.keys(forecastDimensionLabels) as ForecastDimension[];
type ForecastAction = "load" | "history" | "update" | "save" | "download" | "confirm";
type ForecastPageFailure = { action: ForecastAction; error: ForecastRequestError };
const readForecastVersion = () => typeof window === "undefined" ? "" : new URLSearchParams(window.location.search).get("forecast_version") ?? "";
function writeForecastVersion(id: string) {
  const url = new URL(window.location.href);
  if (id) url.searchParams.set("forecast_version", id); else url.searchParams.delete("forecast_version");
  window.history.replaceState(window.history.state, "", url);
}
const savedAt = (value: string | undefined) => {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
};
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
const methodLabels: Record<ForecastRow["method"], string> = { FROZEN_ACTUAL: "冻结实际", ACTUAL_UNAVAILABLE: "实际未对应", TERMINAL_FORECAST: "季末预测", PARENT_PARAMETER_PROJECTION: "参数推演", UNMAPPED_ACTUAL: "未对应实际", UNMAPPED_FROZEN_CARRYIN: "未对应结转实际" };
export function forecastRowLabel(row: ForecastRow) {
  const revenueZero = forecastActualRevenueDefaultsToZero(row), headcountZero = forecastActualHeadcountDefaultsToZero(row);
  if (revenueZero && headcountZero) return "实际收入、人次按0计";
  if (revenueZero) return "实际收入按0计";
  if (headcountZero) return "实际人次按0计";
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
const forecastDetailTransfer = (rows: ForecastRow[]) => ({ columns: exportColumns, rows: rows.map(row => ({ ...forecastPresentationRow(row), method_label: forecastRowLabel(row) })), filename: "FY27-达成预测明细" });

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

/** Saved confirmation evidence is traceable here, not a main-screen task. */
export function ForecastConfirmationEvidence({ calculation }: { calculation: ForecastCalculation }) {
  const checks = calculation.confirmation_checks ?? [], records = calculation.issues.filter(issue => issue.severity === "INFO");
  if (!checks.length && !records.length && !calculation.confirmation_policy_version) return null;
  const result = (passed: boolean) => passed ? "通过" : "未通过";
  const rawValue = (value: ForecastNumber) => value === null ? "未提供" : String(value);
  return <details className="forecast-how-details"><summary>确认核验记录</summary>
    {calculation.confirmation_policy_version && <dl><div><dt>核验规则</dt><dd><code>{calculation.confirmation_policy_version}</code></dd></div></dl>}
    {checks.map((check, index) => <div key={`${check.season}-${index}`}><h3>{check.season}</h3><dl>
      <div><dt>冻结来源完整</dt><dd>{result(check.source_complete)}</dd></div>
      <div><dt>预算金额、人次为零</dt><dd>{result(check.budget_components_zero)}</dd></div>
      <div><dt>无冻结结转</dt><dd>{result(check.no_frozen_carryins)}</dd></div>
      <div><dt>收入勾稽</dt><dd>{result(check.revenue_matched)}</dd></div>
      <div><dt>人次勾稽</dt><dd>{result(check.enrollment_matched)}</dd></div>
      <div><dt>不阻断的空组合</dt><dd>{check.excluded_empty_rows}项</dd></div>
      <div><dt>已知明细收入（元）</dt><dd>{rawValue(check.known_revenue)}</dd></div>
      <div><dt>部门实际收入（元）</dt><dd>{rawValue(check.actual_revenue)}</dd></div>
      <div><dt>已知明细人次</dt><dd>{rawValue(check.known_enrollment)}</dd></div>
      <div><dt>部门实际人次</dt><dd>{rawValue(check.actual_enrollment)}</dd></div>
    </dl></div>)}
    {records.length > 0 && <details className="forecast-how-details"><summary>处理记录 · {records.length}项</summary>{records.map((issue, index) => <details key={`${issue.code}-${issue.season}-${index}`}><summary>{[issue.season, ...dimensions.map(key => issue.dims?.[key])].filter(Boolean).join(" / ") || "部门记录"}</summary><code>{issue.code}</code><p>{issue.message}</p><pre>{JSON.stringify(issue, null, 2)}</pre></details>)}</details>}
  </details>;
}

/** Calculation help reads the saved version, never an uncommitted parameter draft. */
export function ForecastFixedRules({ version }: { version: ForecastVersion }) {
  if (!usesFixedForecastRules(version)) return null;
  const stateRules: Record<ForecastState, string> = {
    FINALIZED: "使用已定版实际", OPEN: "根据当前人数、后续招生和退班，预计季末结果",
    ENDED: "不再新增招生，扣除预计退班后计算季末结果", FUTURE: "从上一季预计人数继续推算",
  };
  const quarterGroups = forecastSeasons.reduce<{ state: ForecastState; seasons: ForecastSeason[] }[]>((groups, season) => {
    const state = version.states[season], existing = groups.find(item => item.state === state);
    if (existing) existing.seasons.push(season); else groups.push({ state, seasons: [season] });
    return groups;
  }, []);
  return <details className="forecast-how" aria-label="预测计算方法"><summary>预测怎么算</summary>
    <ul className="forecast-how-quarters">{quarterGroups.map(item => <li key={item.state}><strong>{item.seasons.join("、")}</strong><span>{stateRules[item.state]}</span></li>)}</ul>
    <details className="forecast-how-details"><summary>计算细节</summary><dl>
      <div><dt>已定版实际待核对</dt><dd>未知收入、人次在明细中按0展示，原始记录仍待核对；部门合计使用完整已定版实际。</dd></div>
      <div><dt>招生中缺少当前人数</dt><dd>先按原预算人数估计，不当作实际。</dd></div>
      <div><dt>业务人数与财务参数</dt><dd>优先使用已保存值，否则参考同季度、赛道、产品、年级的有效参数；没有可用人数参数时沿用上季人数，新增为0。</dd></div>
      <div><dt>退班参考</dt><dd>历史样本至少 {version.assumptions.minimum_sample} 人次，不足时扩大参考范围。</dd></div>
      <div><dt>人数取整</dt><dd>校区合计四舍五入，明细按最大余数分配，保持合计一致。</dd></div>
    </dl></details><ForecastConfirmationEvidence calculation={version.calculation}/>
  </details>;
}

export function ForecastParameterStatus({ pending }: { pending: boolean }) {
  return <span className={`forecast-parameter-status ${pending ? "is-pending" : ""}`} role="status">{pending ? "未保存" : "已应用"}</span>;
}

export function TrialAssumptionNotes({ assumptions, legacyTrial = true }: { assumptions: TrialAssumption[]; legacyTrial?: boolean }) {
  const [limits, setLimits] = useState<Record<string, number>>({});
  const groups = new Map<string, TrialAssumption[]>();
  for (const item of assumptions) groups.set(item.code, [...(groups.get(item.code) ?? []), item]);
  return assumptions.length ? <details className="forecast-trial-notes"><summary>{legacyTrial ? "查看试用说明" : "预测处理记录"} {assumptions.length} 条</summary>{[...groups].map(([code, values]) => <details key={code}><summary><strong>{values[0].message}</strong><span>{values.length} 条</span></summary><ul>{values.slice(0, limits[code] ?? 25).map((item, index) => {
    const detail = item.detail && typeof item.detail === "object" ? item.detail as Record<string, unknown> : {};
    const unapplied = detail.unapplied_renewal != null || detail.unapplied_share != null;
    return <li key={`${code}-${index}`}><span>{[item.season, ...dimensions.map(key => item.dims?.[key])].filter(Boolean).join(" / ")}</span>{item.message !== values[0].message && <span>{item.message}</span>}{unapplied && <strong className="forecast-scope-note">已填写比例暂未用于计算</strong>}</li>;
  })}</ul>{values.length > (limits[code] ?? 25) && <button onClick={() => setLimits({ ...limits, [code]: (limits[code] ?? 25) + 25 })}>再显示 25 条</button>}</details>)}</details> : null;
}

/** Independent forecast parameters. Dimensions and frozen actuals are never editable here. */
export function ForecastTrialScopeEditor({ scopes, states, onChange, onDraftChange, disabled = false, legacyTrial = true }: { scopes: ForecastScope[]; states: Record<ForecastSeason, ForecastState>; onChange: (scopes: ForecastScope[]) => void; onDraftChange?: (dirty: boolean) => void; disabled?: boolean; legacyTrial?: boolean }) {
  const [season, setSeason] = useState<ForecastSeason>("秋季");
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(0);
  const [selected, setSelected] = useState<number | null>(null);
  const [draft, setDraft] = useState<TrialFinancialDraft | null>(null);
  const [error, setError] = useState("");
  const editorRef = useRef<HTMLDivElement>(null);
  const matches = scopes.map((scope, index) => ({ scope, index })).filter(({ scope }) => !query.trim() || dimensions.some(key => scope.dims[key].includes(query.trim())));
  const pageRows = matches.slice(page * 20, (page + 1) * 20);
  const parameterTransfer = {
    columns: [
      { key: "season", label: "季度" }, ...dimensions.map(key => ({ key, label: forecastDimensionLabels[key] })),
      { key: "price", label: "学费（元）", format: "money" },
      ...(["discount", "recognition", "renewal", "share"] as const).map(key => ({ key, label: { discount: "折扣", recognition: "当季结转比例", renewal: "续班率", share: "新生占比" }[key], format: "rate" as const })),
      { key: "additional", label: "后续新增（人次）", format: "count" },
    ] as ExportColumn[],
    rows: matches.map(({ scope }) => ({ season, ...scope.dims, ...scope.financial[season], additional: scope.additional[season] })),
    filename: `FY27-${season}-预测参数明细`,
  };
  const currentScope = selected === null ? null : scopes[selected];
  const draftChanged = Boolean(currentScope && draft && JSON.stringify(draft) !== JSON.stringify(trialFinancialDraft(currentScope, season, legacyTrial)));
  useEffect(() => { onDraftChange?.(draftChanged); }, [draftChanged, onDraftChange]);
  useEffect(() => () => onDraftChange?.(false), [onDraftChange]);
  function selectScope(index: number) { if (draftChanged) { setError("先加入本次修改，或放弃当前编辑，再切换明细。"); return; } setSelected(index); setDraft(trialFinancialDraft(scopes[index], season, legacyTrial)); setError(""); requestAnimationFrame(() => editorRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" })); }
  function changeSeason(next: ForecastSeason) { if (draftChanged) { setError("先加入本次修改，或放弃当前编辑，再切换季度。"); return; } setSeason(next); setPage(0); setError(""); if (selected !== null) setDraft(trialFinancialDraft(scopes[selected], next, legacyTrial)); }
  function apply() {
    if (!currentScope || selected === null || !draft || disabled) return;
    const result = applyTrialFinancialDraft(currentScope, season, states[season], draft);
    if (!result.scope) { setError(result.error); return; }
    onChange(scopes.map((item, index) => index === selected ? result.scope! : item)); setSelected(null); setDraft(null); setError("");
  }
  return <div className="forecast-trial-scopes">
    <div className="forecast-trial-scope-filters"><label>季度<select value={season} onChange={event => changeSeason(event.target.value as ForecastSeason)}>{forecastSeasons.map(value => <option key={value}>{value}</option>)}</select></label><label>业务明细<input value={query} placeholder="校区、产品、年级或科目" onChange={event => { setQuery(event.target.value); setPage(0); }}/></label></div>
    <div className="forecast-table-wrap" tabIndex={0} aria-label={legacyTrial ? "试用假设明细表" : "独立业务参数表"}><ActionTable exportData={parameterTransfer}><thead><tr><th>业务维度</th><th>学费（元）</th><th>折扣</th><th>当季结转比例</th><th>续班率</th><th>新生占比</th><th>后续新增</th><th>操作</th></tr></thead><tbody>{pageRows.map(({ scope, index }) => {
      const financial = scope.financial[season], readonly = scope.kind === "FINANCIAL_ONLY" || states[season] === "FINALIZED";
      return <tr key={JSON.stringify(scope.dims)}><th>{scope.dims.campus}<small>{scope.dims.track} / {scope.dims.product} / {scope.dims.grade} / {scope.dims.subject}</small></th><td>{displayNumber(financial?.price, 2)}</td><td>{achievement(financial?.discount)}</td><td>{achievement(financial?.recognition)}</td><td>{achievement(financial?.renewal)}</td><td>{achievement(financial?.share)}</td><td>{scope.additional[season] === undefined ? "—" : displayNumber(scope.additional[season])}</td><td>{readonly ? <span className="forecast-scope-readonly">{scope.kind === "FINANCIAL_ONLY" ? "结转实际" : "已定版实际"}</span> : <button disabled={disabled} aria-label={`调整${season} ${dimensions.map(key => scope.dims[key]).join(" / ")}${legacyTrial ? "试用假设" : "预测参数"}`} onClick={() => selectScope(index)}>调整</button>}</td></tr>;
    })}</tbody></ActionTable>{!matches.length && <p className="forecast-empty">{legacyTrial ? "当前范围没有独立试用明细" : "当前范围没有独立业务明细"}</p>}</div>
    <div className="forecast-pagination"><button disabled={page === 0} onClick={() => setPage(page - 1)}>上一页</button><span>{matches.length ? page + 1 : 0} / {Math.ceil(matches.length / 20)} 页</span><button disabled={(page + 1) * 20 >= matches.length} onClick={() => setPage(page + 1)}>下一页</button></div>
    {currentScope && draft && <div className="forecast-scope-editor" ref={editorRef}><header><div><strong>{season} · {currentScope.dims.campus}</strong><span>{currentScope.dims.track} / {currentScope.dims.product} / {currentScope.dims.grade} / {currentScope.dims.subject}</span></div><button disabled={disabled} onClick={() => { setSelected(null); setDraft(null); setError(""); }}>{draftChanged ? "放弃当前编辑" : "取消"}</button></header>
      <fieldset disabled={disabled || states[season] === "FINALIZED" || currentScope.kind === "FINANCIAL_ONLY"}><div className="forecast-scope-fields">{(["price", "discount", "recognition", "renewal", "share"] as const).map(key => <label key={key}>{{ price: "学费（元）", discount: "折扣（%）", recognition: "当季结转比例（%）", renewal: "续班率（%）", share: "新生占比（%）" }[key]}<input type="number" min="0" max={key === "price" ? "1000000000" : "100"} step="any" placeholder={key === "renewal" || key === "share" ? "未设定" : undefined} value={draft[key]} onChange={event => setDraft({ ...draft, [key]: event.target.value })}/></label>)}
      {(states[season] === "OPEN" || states[season] === "ENDED") && <label>后续新增新生（人次）<input type="number" min="0" max="10000000" step="1" value={draft.additional} onChange={event => setDraft({ ...draft, additional: event.target.value })}/></label>}</div>
      {(!draft.renewal.trim() || !draft.share.trim()) && <p className="forecast-scope-note">未设定项自动参考同产品、同年级；无可用参数时暂按上季人数。</p>}
      <label>{legacyTrial ? "试用调整原因" : "调整原因"}<input maxLength={1000} value={draft.reason} onChange={event => setDraft({ ...draft, reason: event.target.value })}/></label>{error && <p className="forecast-scope-error" role="alert">{error}</p>}<button type="button" onClick={apply}>加入本次修改</button></fieldset>
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
export function ForecastDashboard({ calculation, busy = false, onAdjust, onReview, trialMode = false }: { calculation: ForecastCalculation; busy?: boolean; onAdjust?: () => void; onReview?: () => void; trialMode?: boolean }) {
  const [group, setGroup] = useState<ForecastDimension>("track");
  const preferOpenSeason = trialMode || calculation.fixed_rules_version === fixedForecastRulesVersion;
  const [season, setSeason] = useState(() => preferOpenSeason ? calculation.quarters.find(quarter => quarter.state === "OPEN")?.season ?? "" : "");
  const previousOpenDefault = useRef(preferOpenSeason);
  useEffect(() => { if (preferOpenSeason && !previousOpenDefault.current) setSeason(calculation.quarters.find(quarter => quarter.state === "OPEN")?.season ?? ""); previousOpenDefault.current = preferOpenSeason; }, [preferOpenSeason, calculation.quarters]);
  const [scope, setScope] = useState<Partial<ForecastDimensions>>({});
  const [exportError, setExportError] = useState("");
  const [exporting, setExporting] = useState(false);
  const [detailPage, setDetailPage] = useState(0);
  const rows = useMemo(() => calculation.rows.filter(row => (!season || row.season === season) && dimensions.every(key => !scope[key] || row[key] === scope[key])), [calculation, season, scope]);
  const grouped = useMemo(() => groupForecastRows(rows, group), [rows, group]);
  const max = Math.max(1, ...calculation.quarters.flatMap(quarter => [forecastNumber(quarter.budget_revenue), forecastNumber(quarter.forecast_revenue)]).filter((value): value is number => value !== null).map(Math.abs));
  const complete = forecastCalculationComplete(calculation, trialMode);
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
    {!complete && <section className="forecast-blocked" role="status"><strong>部分数据待核对，预测尚未完整</strong>{(onReview || onAdjust) && <button type="button" disabled={busy} onClick={onReview ?? onAdjust}>查看待核对数据</button>}</section>}
    <section className="forecast-section"><header><h2>季度达成</h2><div className="forecast-legend"><span><i className="is-budget"/>预算</span><span><i/>预计 / 实际</span></div></header>
      <div className="forecast-quarter-bars" role="img" aria-label={calculation.quarters.map(quarter => `${quarter.season}，${forecastStateLabels[quarter.state]}，预算${money(quarter.budget_revenue)}，${quarter.state === "FINALIZED" ? "冻结实际" : "预计"}${money(quarter.forecast_revenue)}`).join("；")}>
        {calculation.quarters.map(quarter => <div key={quarter.season}><div className="forecast-quarter-name"><strong>{quarter.season}</strong><span className={`forecast-state is-${quarter.state.toLowerCase()}`}>{forecastStateLabels[quarter.state]}</span></div>
          <div className="forecast-bar-row"><span>预算</span><div className="forecast-bar-track">{forecastNumber(quarter.budget_revenue) !== null ? <i className={`is-budget ${forecastNumber(quarter.budget_revenue)! < 0 ? "is-negative" : ""}`} style={{ transform: `scaleX(${Math.abs(forecastNumber(quarter.budget_revenue)!) / max})` }}/> : <em>未提供</em>}</div><strong>{money(quarter.budget_revenue)}</strong></div>
          <div className="forecast-bar-row"><span>{quarter.state === "FINALIZED" ? "实际" : "预计"}</span><div className="forecast-bar-track">{forecastNumber(quarter.forecast_revenue) !== null ? <i className={forecastNumber(quarter.forecast_revenue)! < 0 ? "is-negative" : ""} style={{ transform: `scaleX(${Math.abs(forecastNumber(quarter.forecast_revenue)!) / max})` }}/> : <em>待预测</em>}</div><strong>{money(quarter.forecast_revenue)}</strong></div>
          <div className="forecast-quarter-gap"><span>{forecastGapLabel(quarter.gap, quarter.state === "FINALIZED")}</span><strong>{gapMoney(quarter.gap)}</strong></div>
        </div>)}
      </div>
      <div className="forecast-table-wrap" tabIndex={0} aria-label="季度达成明细表"><ActionTable><thead><tr><th>季度</th><th>状态</th><th>预算收入（万元）</th><th>预计 / 实际收入（万元）</th><th>缺口 / 超额（万元）</th><th>预算人次</th><th>预计 / 实际人次</th></tr></thead><tbody>{calculation.quarters.map(quarter => <tr key={quarter.season}><th>{quarter.season}</th><td>{forecastStateLabels[quarter.state]}</td><td>{displayNumber(forecastNumber(quarter.budget_revenue) === null ? null : forecastNumber(quarter.budget_revenue)! / 10000, 2)}</td><td>{displayNumber(forecastNumber(quarter.forecast_revenue) === null ? null : forecastNumber(quarter.forecast_revenue)! / 10000, 2)}</td><td>{forecastNumber(quarter.gap) === null ? "—" : `${forecastNumber(quarter.gap)! >= 0 ? "缺口" : "超额"} ${displayNumber(Math.abs(forecastNumber(quarter.gap)!) / 10000, 2)}`}</td><td>{displayNumber(quarter.budget_total)}</td><td>{displayNumber(quarter.forecast_total)}</td></tr>)}</tbody></ActionTable></div>
    </section>
    <section className="forecast-section"><header><h2>维度达成</h2><div className="forecast-group-switch" role="group" aria-label="分组维度">{(["track", "campus", "grade"] as ForecastDimension[]).map(key => <button key={key} aria-pressed={group === key} onClick={() => setGroup(key)}>{forecastDimensionLabels[key]}</button>)}</div></header>
      <div className="forecast-filters"><label>季度<select value={season} onChange={event => { setSeason(event.target.value); setDetailPage(0); }}><option value="">全部</option>{forecastSeasons.map(value => <option key={value}>{value}</option>)}</select></label>{dimensions.map(key => <label key={key}>{forecastDimensionLabels[key]}<select value={scope[key] ?? ""} onChange={event => { setScope({ ...scope, [key]: event.target.value }); setDetailPage(0); }}><option value="">全部</option>{[...new Set(calculation.rows.map(row => row[key]))].sort((a, b) => a.localeCompare(b, "zh-CN")).map(value => <option key={value}>{value}</option>)}</select></label>)}</div>
      {(season || dimensions.some(key => scope[key])) && <div className="forecast-filter-scope"><span>{[season, ...dimensions.map(key => scope[key])].filter(Boolean).join(" / ")}</span><button onClick={() => { setSeason(""); setScope({}); setDetailPage(0); }}>清除筛选</button></div>}
      <div className="forecast-table-wrap" tabIndex={0} aria-label="维度达成表"><ActionTable><thead><tr><th>{forecastDimensionLabels[group]}</th><th>预算收入（万元）</th><th>预计 / 实际收入（万元）</th><th>缺口 / 超额（万元）</th><th>达成率</th><th>预算人次</th><th>预计 / 实际人次</th></tr></thead><tbody>{grouped.map(item => <tr key={item.name}><th>{item.name || "未填写"}{item.unmapped && <span className="forecast-unmapped">含未对应实际</span>}</th><td>{item.hasUnbudgeted && !item.unmapped && item.budget_revenue === null ? "未单列" : displayNumber(forecastNumber(item.budget_revenue) === null ? null : forecastNumber(item.budget_revenue)! / 10000, 2)}</td><td>{displayNumber(forecastNumber(item.forecast_revenue) === null ? null : forecastNumber(item.forecast_revenue)! / 10000, 2)}</td><td>{forecastNumber(item.gap) === null ? "—" : `${forecastNumber(item.gap)! >= 0 ? "缺口" : "超额"} ${displayNumber(Math.abs(forecastNumber(item.gap)!) / 10000, 2)}`}</td><td>{achievement(item.achievement)}</td><td>{item.hasUnbudgeted && !item.unmapped && item.budget_total === null ? "未单列" : displayNumber(item.budget_total)}</td><td>{displayNumber(item.forecast_total)}</td></tr>)}</tbody></ActionTable>{!rows.length && <p className="forecast-empty">当前范围暂无预测明细</p>}</div>
    </section>
    <ForecastDataNotice issues={calculation.issues} onOpen={onReview}/>
    <section className="forecast-section"><details><summary className="forecast-detail-summary"><h2>预测明细</h2><span>{rows.length} 条</span></summary><header><div/><button disabled={busy || exporting || !rows.length} onClick={() => void download()}>{exporting ? "正在导出…" : "导出明细"}</button></header>{exportError && <p role="alert">{exportError}</p>}
      <div className="forecast-table-wrap" tabIndex={0} aria-label="预测明细表"><ActionTable exportData={forecastDetailTransfer(rows)}><thead><tr><th>季度</th><th>校区 / 赛道 / 产品</th><th>年级 / 科目</th><th>预算收入（元）</th><th>预计 / 实际收入（元）</th><th>预算人次</th><th>预计 / 实际人次</th><th>预测方式</th></tr></thead><tbody>{pageRows.map((row, index) => <tr key={`${row.season}-${JSON.stringify(dimensions.map(key => row[key]))}-${index}`}><th>{row.season}</th><td>{row.campus}<small>{row.track} / {row.product}</small></td><td>{row.grade}<small>{row.subject}</small></td><td>{row.budget_covered === false && !row.unmapped ? "未单列" : displayNumber(row.budget_revenue, 2)}</td><td>{displayNumber(row.forecast_revenue, 2)}</td><td>{row.counts_not_applicable ? "—" : row.budget_covered === false && !row.unmapped ? "未单列" : displayNumber(row.budget_total)}</td><td>{row.counts_not_applicable ? "—" : displayNumber(row.forecast_total)}</td><td>{forecastRowLabel(rows[detailPage * 30 + index])}</td></tr>)}</tbody></ActionTable></div>
      <div className="forecast-pagination"><button disabled={detailPage === 0} onClick={() => setDetailPage(detailPage - 1)}>上一页</button><span>{rows.length ? detailPage + 1 : 0} / {Math.ceil(rows.length / 30)} 页</span><button disabled={(detailPage + 1) * 30 >= rows.length} onClick={() => setDetailPage(detailPage + 1)}>下一页</button></div>
    </details></section>
  </>;
}

const emptyDims = (): ForecastDimensions => ({ track: "", product: "", grade: "", subject: "", campus: "" });
type ForecastDrawerTab = "people" | "business" | "data";

/** Keep panel children mounted: closing or changing tabs must not discard edits. */
export function ForecastSettingsDrawer({ open, tab, onTabChange, onClose, canMaintain, busy = false, children, footer }: {
  open: boolean; tab: ForecastDrawerTab; onTabChange: (tab: ForecastDrawerTab) => void; onClose: () => void;
  canMaintain: boolean; busy?: boolean; children: ReactNode; footer?: ReactNode;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = dialogRef.current;
    if (!open || !dialog) return;
    dialog.showModal();
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { dialog.close(); document.body.style.overflow = previousOverflow; };
  }, [open]);
  return <dialog className="forecast-drawer" ref={dialogRef} aria-labelledby="forecast-drawer-title" onCancel={event => { event.preventDefault(); if (!busy) onClose(); }}>
    <header className="forecast-drawer-heading"><h2 id="forecast-drawer-title">{tab === "data" ? "数据核对" : "调整预测"}</h2><button type="button" autoFocus disabled={busy} onClick={onClose} aria-label="关闭预测侧栏">关闭</button></header>
    <div className="forecast-drawer-tabs" role="tablist" aria-label="预测调整项目">{(["people", "business", "data"] as const).map(value => <button type="button" key={value} role="tab" id={`forecast-tab-${value}`} aria-selected={tab === value} aria-controls={`forecast-panel-${value}`} tabIndex={tab === value ? 0 : -1} disabled={!canMaintain && value !== "data"} onClick={() => onTabChange(value)} onKeyDown={event => {
      const tabs: ForecastDrawerTab[] = canMaintain ? ["people", "business", "data"] : ["data"];
      const index = tabs.indexOf(value);
      const next = event.key === "Home" ? tabs[0] : event.key === "End" ? tabs[tabs.length - 1] : event.key === "ArrowRight" ? tabs[(index + 1) % tabs.length] : event.key === "ArrowLeft" ? tabs[(index + tabs.length - 1) % tabs.length] : undefined;
      if (next) { event.preventDefault(); onTabChange(next); dialogRef.current?.querySelector<HTMLButtonElement>(`#forecast-tab-${next}`)?.focus(); }
    }}>{{ people: "人数调整", business: "业务参数", data: "数据维护" }[value]}</button>)}</div>
    <div className="forecast-drawer-body">{children}</div>
    {footer && <footer className="forecast-drawer-footer">{footer}</footer>}
  </dialog>;
}

export function ForecastPage() {
  const [identity, setIdentity] = useState<ApiIdentity | null>(null);
  const [data, setData] = useState<ForecastEnvelope | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [drawerTab, setDrawerTab] = useState<ForecastDrawerTab>("people");
  const [assumptions, setAssumptions] = useState<ForecastAssumptions>({ states: { ...defaultForecastStates }, minimum_sample: 30, mappings: [], adjustments: [] });
  const [reason, setReason] = useState("");
  const [mapping, setMapping] = useState<ForecastMapping>({ source: emptyDims(), target: emptyDims(), reason: "" });
  const [adjustment, setAdjustment] = useState({ season: "秋季", campus: "", kind: "additional" as ForecastAdjustmentKind, value: "", reason: "" });
  const [showConfirm, setShowConfirm] = useState(false);
  const [confirmationReason, setConfirmationReason] = useState("");
  const [trialDraftChanged, setTrialDraftChanged] = useState(false);
  const [pendingNavigation, setPendingNavigation] = useState("");
  const [selectedVersionId, setSelectedVersionId] = useState(readForecastVersion);
  const [failure, setFailure] = useState<ForecastPageFailure | null>(null);
  const leaveDialog = useRef<HTMLDialogElement>(null);
  const lock = useRef(false);
  const leaving = useRef(false);
  const refreshIntent = useRef<{ requestId: string; states: Record<ForecastSeason, ForecastState>; reason: string } | null>(null);
  const version = data?.version ?? null;
  const readonly = Boolean(selectedVersionId);
  const trialMode = version?.assumptions.trial_mode === true;
  const fixedRules = Boolean(version && usesFixedForecastRules(version));
  const canMaintain = !readonly && Boolean(identity?.roles.some(role => ["DATA_MAINTAINER", "DEPARTMENT_BUDGET_OWNER"].includes(role)));
  const canEditBasis = !readonly && Boolean(identity?.roles.includes("DATA_MAINTAINER"));
  const settingChanged = version ? JSON.stringify(assumptions) !== JSON.stringify(version.assumptions) : false;
  const pendingPredictionChanges = settingChanged || trialDraftChanged || Boolean(reason.trim()
    || mapping.reason.trim() || dimensions.some(key => mapping.source[key] || mapping.target[key])
    || adjustment.value || adjustment.reason.trim());
  const unsaved = pendingPredictionChanges || Boolean(showConfirm && confirmationReason.trim());
  useEffect(() => {
    const restore = () => { leaving.current = false; setPendingNavigation(""); };
    window.addEventListener("pageshow", restore);
    return () => window.removeEventListener("pageshow", restore);
  }, []);
  useEffect(() => {
    if (!unsaved && !busy) return;
    const protect = (event: BeforeUnloadEvent) => { if (!leaving.current) { event.preventDefault(); event.returnValue = ""; } };
    window.addEventListener("beforeunload", protect);
    return () => window.removeEventListener("beforeunload", protect);
  }, [unsaved, busy]);
  useEffect(() => {
    if (!pendingNavigation) return;
    const dialog = leaveDialog.current;
    dialog?.showModal();
    return () => dialog?.close();
  }, [pendingNavigation]);
  function beforeLeave(event: MouseEvent<HTMLAnchorElement>) {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button !== 0) return;
    if (busy) { event.preventDefault(); setNotice("当前操作完成后再切换"); return; }
    if (unsaved) { event.preventDefault(); setPendingNavigation(event.currentTarget.href); return; }
  }
  useEffect(() => {
    const controller = new AbortController();
    setBusy("读取预测");
    void (async () => {
      try {
        const actor = await parseResponse<ApiIdentity>(await fetch("/api/v1/local-trial/session", { signal: controller.signal }));
        if (!controller.signal.aborted) setIdentity(actor);
        const requested = readForecastVersion();
        const current = requested ? await getForecastVersion(actor, requested, controller.signal) : await getForecast(actor, controller.signal);
        if (requested && current.version?.id !== requested) throw new Error("未读取到所选预测版本，请返回当前版本。");
        if (!controller.signal.aborted) { setData(current); if (current.version) setAssumptions(current.version.assumptions); }
      } catch (error) { if (!controller.signal.aborted) { const detail = forecastFailure(error); setError(detail.message); setFailure({ action: "load", error: detail }); } }
      finally { if (!controller.signal.aborted) setBusy(""); }
    })();
    return () => controller.abort();
  }, []);
  async function perform(label: string, operation: () => Promise<ForecastEnvelope>, action: ForecastAction = "save", selected?: string) {
    if (lock.current || (readonly && !["load", "history"].includes(action))) return;
    lock.current = true; setBusy(label); setError(""); setFailure(null); setNotice("");
    try {
      const current = await operation();
      if (selected && current.version?.id !== selected) throw new Error("未读取到所选预测版本，请重新选择。");
      setData(current); if (current.version) setAssumptions(current.version.assumptions);
      setReason(""); setTrialDraftChanged(false); setMapping({ source: emptyDims(), target: emptyDims(), reason: "" }); setAdjustment(previous => ({ ...previous, value: "", reason: "" })); setShowConfirm(false); setConfirmationReason("");
      if (["save", "history", "load"].includes(action)) setDrawerOpen(false);
      if (selected !== undefined) { setSelectedVersionId(selected); writeForecastVersion(selected); }
      if (action === "update") { refreshIntent.current = null; setNotice("新预测已保存"); }
      else setNotice(action === "confirm" ? "预测已确认" : action === "history" ? "已读取所选版本的保存结果" : action === "load" ? "已读取当前保存的预测" : "新预测已保存");
    }
    catch (error) { const detail = forecastFailure(error); setError(detail.message); setFailure({ action, error: detail }); }
    finally { lock.current = false; setBusy(""); }
  }
  function openDrawer(tab: ForecastDrawerTab) { setDrawerTab(tab); setDrawerOpen(true); }
  function updateForecast(retry = false) {
    if (!identity || !canEditBasis || pendingPredictionChanges || lock.current) return;
    if (!retry || !refreshIntent.current) refreshIntent.current = { requestId: newRequestId(), states: { ...(version?.states ?? defaultForecastStates) }, reason: version ? "更新 FY27 达成预测" : "首次 FY27 自然预测" };
    const intent = refreshIntent.current;
    return perform(retry ? "重试更新" : "更新预测", () => refreshForecast(identity, intent.states, intent.reason, intent.requestId), "update");
  }
  function loadSavedVersion(id: string) {
    if (!identity || unsaved || lock.current) return;
    return perform(id ? "读取历史版本" : "读取当前版本", () => id ? getForecastVersion(identity, id) : getForecast(identity), id ? "history" : "load", id);
  }
  async function downloadSnapshot() {
    if (!identity || !version || lock.current) return;
    lock.current = true; setBusy("下载快照"); setError(""); setFailure(null); setNotice("");
    try { await downloadForecastSnapshot(identity, version); setNotice(`P${version.sequence} 快照已下载`); }
    catch (error) { const detail = forecastFailure(error); setError(detail.message); setFailure({ action: "download", error: detail }); }
    finally { lock.current = false; setBusy(""); }
  }
  const retained = failure?.error.detail.retained_version ?? version;
  const updateFailed = failure?.action === "update";
  const loginRequired = failure && /登录|权限/.test(forecastFailureCategory(failure.error));
  const unfinishedInputs = trialDraftChanged || Boolean(mapping.reason.trim() || dimensions.some(key => mapping.source[key] || mapping.target[key]) || adjustment.value || adjustment.reason.trim());
  const existingCampuses = [...new Set(version?.calculation.campuses.map(item => item.campus) ?? [])].sort((a, b) => a.localeCompare(b, "zh-CN"));
  const sourceOptions = forecastMappingIssues(version?.calculation.issues ?? []);
  const budgetDimensionOptions = useMemo(() => {
    const budgetRows = version?.calculation.rows.filter(row => !row.unmapped && !["UNMAPPED_ACTUAL", "UNMAPPED_FROZEN_CARRYIN"].includes(row.method)) ?? [];
    return Object.fromEntries(dimensions.map(key => [key, [...new Set(budgetRows.map(row => row[key]))].sort((a, b) => a.localeCompare(b, "zh-CN"))])) as Record<ForecastDimension, string[]>;
  }, [version]);
  return <div className="budget-shell decision-shell forecast-shell">
    <a className="skip-link" href="#forecast-content">跳到主要内容</a>
    <FY27Header active="forecast" onBeforeLeave={beforeLeave}><span className="forecast-year">FY27 学校版</span><div className="decision-version"><span className={`status-dot ${version?.status === "CONFIRMED" && !trialMode ? "is-published" : ""}`}/><span>{trialMode ? "试用试算" : version ? version.status === "CONFIRMED" ? "预测已确认" : "预测草稿" : "尚未预测"}</span>{version && <small>P{version.sequence}</small>}</div></FY27Header>
    <dialog className="forecast-leave-dialog" ref={leaveDialog} aria-labelledby="forecast-leave-title" onCancel={() => setPendingNavigation("")}>
      <h2 id="forecast-leave-title">修改尚未保存</h2>
      <div><button className="forecast-primary" autoFocus onClick={() => { setPendingNavigation(""); if (pendingPredictionChanges) openDrawer(drawerTab); }}>继续编辑</button><button onClick={() => { leaving.current = true; location.assign(pendingNavigation); }}>离开，不保存</button></div>
    </dialog>
    <main className="decision-workspace" id="forecast-content">
      <header className="forecast-heading"><h1>达成预测</h1><div>{version && <span>截至 {version.as_of}</span>}{version && canMaintain && <button disabled={Boolean(busy)} onClick={() => openDrawer(pendingPredictionChanges ? drawerTab : "people")}>调整预测</button>}{!readonly && <button className="forecast-primary" disabled={!identity || !canEditBasis || Boolean(busy) || pendingPredictionChanges} onClick={() => void updateForecast()}>{["更新预测", "重试更新"].includes(busy) ? "正在更新…" : version ? "更新预测" : "生成预测"}</button>}</div></header>
      <div className="forecast-version-tools"><label>查看保存版本<select aria-label="查看保存版本" value={selectedVersionId} disabled={!identity || Boolean(busy) || unsaved} onChange={event => void loadSavedVersion(event.target.value)}><option value="">当前版本</option>{data?.history.map(item => <option key={item.id} value={item.id}>P{item.sequence} · {item.as_of} · {item.status === "CONFIRMED" ? "已确认" : "草稿"}</option>)}{selectedVersionId && !data?.history.some(item => item.id === selectedVersionId) && <option value={selectedVersionId}>指定历史版本</option>}</select></label><div>{readonly && <button disabled={!identity || Boolean(busy)} onClick={() => void loadSavedVersion("")}>返回当前版本</button>}{version && <button disabled={!identity || Boolean(busy)} onClick={() => void downloadSnapshot()}>{busy === "下载快照" ? "正在下载…" : "下载本次快照"}</button>}</div></div>
      {readonly && <section className="forecast-readonly" role="status"><strong>{version ? `P${version.sequence} 保存结果 · 只读查看` : "历史版本 · 只读查看"}</strong><span>{version ? `保存于 ${savedAt(version.created_at) || version.as_of}，数据截至 ${version.as_of}` : busy ? "正在读取指定版本" : "未能读取指定版本"}</span><p>查看本版保存的结果和快照。返回当前版本后可更新或调整预测。</p></section>}
      {pendingPredictionChanges && !drawerOpen && <div className="forecast-pending" role="status"><span>修改未保存</span><button onClick={() => openDrawer(drawerTab)}>继续编辑</button></div>}
      {busy && <div className="forecast-working" role="status" aria-live="polite"><span/>{["更新预测", "重试更新"].includes(busy) ? "正在读取数据并生成预测，完成后保存新版本…" : `${busy}中…`}</div>}
      {error && <section className="forecast-error" role="alert"><strong>{updateFailed ? "更新未完成" : failure?.action === "download" ? "快照下载未完成" : "操作未完成"}</strong>{failure && <span className="forecast-error-category">{forecastFailureCategory(failure.error)}</span>}<p>{error}</p>{updateFailed && <p className="forecast-retained">{retained ? `上次保存 P${retained.sequence} · 数据截至 ${retained.as_of}${retained.created_at ? ` · 保存于 ${savedAt(retained.created_at)}` : ""}。` : "尚无已保存预测。"}{failure?.error.detail.retained_version !== undefined ? "该保存版本已保留。" : "本次更新是否完成尚未确认，重试将核对同一次更新。"}</p>}{loginRequired && <p>请先检查观远登录或数据权限，再更新预测。</p>}{failure?.error.detail.diagnostic_id && <small>核对编号：{failure.error.detail.diagnostic_id}</small>}<div className="forecast-error-actions">{updateFailed ? canEditBasis && !pendingPredictionChanges && failure?.error.detail.retryable !== false && <button disabled={Boolean(busy)} onClick={() => void updateForecast(true)}>重试更新</button> : failure?.action === "download" ? <button disabled={Boolean(busy)} onClick={() => void downloadSnapshot()}>重试下载</button> : <button disabled={Boolean(busy) || unsaved} onClick={() => identity ? void loadSavedVersion(selectedVersionId) : location.reload()}>{identity ? "重新读取保存结果" : "重新打开"}</button>}</div></section>}
      {notice && <p className="forecast-notice" role="status">{notice}</p>}
      {!data ? !error && <div className="forecast-skeleton" aria-label="正在读取预测" role="status"><div/><div/><div/></div> : !version ? <section className="forecast-start"><h2>先生成 FY27 预测</h2><div className="forecast-start-states">{forecastSeasons.map(season => <div key={season}><strong>{season}</strong><span>{forecastStateLabels[defaultForecastStates[season]]}</span></div>)}</div></section> : <>
        {trialMode && <ForecastTrialStatus ready={version.calculation.trial_ready === true} busy={Boolean(busy)}/>}
        <ForecastDashboard calculation={version.calculation} trialMode={trialMode} busy={Boolean(busy)} onReview={() => openDrawer("data")}/>
        <ForecastSettingsDrawer open={drawerOpen} tab={drawerTab} onTabChange={setDrawerTab} onClose={() => { if (!busy) setDrawerOpen(false); }} canMaintain={canMaintain} busy={Boolean(busy)} footer={canMaintain && <>
          <label>本次修改原因<input disabled={Boolean(busy)} value={reason} onChange={event => setReason(event.target.value)}/></label>
          <div className="forecast-drawer-save"><span role="status">{busy ? `${busy}中…` : unfinishedInputs ? "先完成当前明细调整" : pendingPredictionChanges ? "修改未保存" : "没有未保存的修改"}</span><button type="button" className="forecast-primary" disabled={Boolean(busy) || !settingChanged || unfinishedInputs || !reason.trim() || !Number.isInteger(assumptions.minimum_sample) || assumptions.minimum_sample < 1} onClick={() => identity && void perform("保存预测", () => reviseForecast(identity, version.id, assumptions, reason.trim()))}>保存并重算</button></div>
        </>}>
          {error && <p className="forecast-scope-error" role="alert">{error}</p>}
          <section role="tabpanel" id="forecast-panel-people" aria-labelledby="forecast-tab-people" hidden={drawerTab !== "people"} className="forecast-settings forecast-drawer-panel">
          <fieldset disabled={Boolean(busy) || !canMaintain}><legend>按校区调整人数</legend>
            {version.calculation.campuses.length > 0 && <div className="forecast-table-wrap" tabIndex={0} aria-label="校区预测人数"><ActionTable><thead><tr><th>季度</th><th>校区</th><th>预算新生</th><th>当前业务新生</th><th>预算内后续新增</th><th>独立业务新增</th><th>后续新增合计</th><th>后续新生退班</th><th>后续老生退班</th></tr></thead><tbody>{version.calculation.campuses.map(item => <tr key={`${item.season}-${item.campus}`}><th>{item.season}</th><td>{item.campus}</td><td>{displayNumber(item.budget_new)}</td><td>{displayNumber(item.current_business_new)}</td><td>{displayNumber(item.budgeted_additional)}</td><td>{displayNumber(item.unbudgeted_additional)}</td><td>{displayNumber(item.additional)}</td><td>{displayNumber(item.new_withdrawal)}</td><td>{displayNumber(item.old_withdrawal)}</td></tr>)}</tbody></ActionTable></div>}
            <div className="forecast-adjustment-fields"><label>季度<select value={adjustment.season} onChange={event => setAdjustment({ ...adjustment, season: event.target.value })}>{forecastSeasons.filter(season => assumptions.states[season] !== "FINALIZED" && assumptions.states[season] !== "FUTURE").map(season => <option key={season}>{season}</option>)}</select></label><label>校区<select value={adjustment.campus} onChange={event => setAdjustment({ ...adjustment, campus: event.target.value })}><option value="">选择校区</option>{existingCampuses.map(campus => <option key={campus}>{campus}</option>)}</select></label><label>项目<select value={adjustment.kind} onChange={event => setAdjustment({ ...adjustment, kind: event.target.value as ForecastAdjustmentKind })}><option value="additional">预算内后续新增</option><option value="new_withdrawal">后续新生退班</option><option value="old_withdrawal">后续老生退班</option></select></label><label>人次<input type="number" min="0" step="1" value={adjustment.value} onChange={event => setAdjustment({ ...adjustment, value: event.target.value })}/></label></div>
            <label>调整原因<input value={adjustment.reason} onChange={event => setAdjustment({ ...adjustment, reason: event.target.value })}/></label>
            <button type="button" disabled={!adjustment.campus || !/^\d+$/.test(adjustment.value) || !adjustment.reason.trim() || !Number.isSafeInteger(Number(adjustment.value)) || !["OPEN", "ENDED"].includes(assumptions.states[adjustment.season as ForecastSeason])} onClick={() => { const next = { ...adjustment, value: Number(adjustment.value), reason: adjustment.reason.trim() }; setAssumptions({ ...assumptions, adjustments: [...assumptions.adjustments.filter(item => !(item.season === next.season && item.campus === next.campus && item.kind === next.kind)), next] }); if (!reason.trim()) setReason(next.reason); setAdjustment({ ...adjustment, value: "", reason: "" }); }}>加入本次调整</button>
            {assumptions.adjustments.length > 0 && <ul className="forecast-edits">{assumptions.adjustments.map((item, index) => <li key={`${item.season}-${item.campus}-${item.kind}`}><span>{item.season} / {item.campus} / {item.kind === "additional" ? "预算内后续新增" : item.kind === "new_withdrawal" ? "新生退班" : "老生退班"}：{item.value} 人次</span><button type="button" onClick={() => setAssumptions({ ...assumptions, adjustments: assumptions.adjustments.filter((_, i) => i !== index) })}>移除</button></li>)}</ul>}
          </fieldset></section>
          <section role="tabpanel" id="forecast-panel-business" aria-labelledby="forecast-tab-business" hidden={drawerTab !== "business"} className="forecast-drawer-panel">
            {(trialMode || fixedRules) ? <ForecastTrialScopeEditor key={version.id} scopes={assumptions.forecast_scopes ?? []} states={assumptions.states} legacyTrial={trialMode} disabled={Boolean(busy) || !canMaintain} onDraftChange={setTrialDraftChanged} onChange={scopes => { setAssumptions({ ...assumptions, forecast_scopes: scopes }); if (!reason.trim()) setReason(trialMode ? "调整 FY27 试用预测假设" : "调整 FY27 业务预测参数"); }}/> : <p className="forecast-empty">当前没有可单独调整的业务参数</p>}
          </section>
          <section role="tabpanel" id="forecast-panel-data" aria-labelledby="forecast-tab-data" hidden={drawerTab !== "data"} className="forecast-settings forecast-drawer-panel">
          <ForecastDataReview issues={version.calculation.issues} onUpdate={canEditBasis && !pendingPredictionChanges ? updateForecast : undefined} busy={Boolean(busy)}/>
          <details className="forecast-maintenance"><summary>数据维护设置</summary>
          <fieldset disabled={Boolean(busy) || !canMaintain}><legend>季度状态</legend><div className="forecast-state-fields">{forecastSeasons.map(season => <label key={season}>{season}<select value={assumptions.states[season]} disabled={!canEditBasis || version.states[season] === "FINALIZED"} onChange={event => setAssumptions({ ...assumptions, states: { ...assumptions.states, [season]: event.target.value as ForecastState } })}>{Object.entries(forecastStateLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>)}</div>
          <label className="forecast-sample-field">退班参考最少人次<input type="number" min="1" max="1000000" step="1" disabled={!canEditBasis} value={assumptions.minimum_sample} onChange={event => setAssumptions({ ...assumptions, minimum_sample: Number(event.target.value) })}/></label>
          <details className="forecast-mappings"><summary>业务维度对应 {assumptions.mappings.length}</summary><fieldset disabled={!canEditBasis}>
            {sourceOptions.length > 0 && <label>待对应明细<select value="" onChange={event => { const issue = sourceOptions[Number(event.target.value)]; if (event.target.value && issue?.dims) setMapping(prepareForecastMapping({ ...emptyDims(), ...issue.dims })); }}><option value="">选择一条明细</option>{sourceOptions.map((issue, index) => <option key={`${issue.code}-${index}`} value={String(index)}>{[issue.season, ...dimensions.map(key => issue.dims?.[key])].filter(Boolean).join(" / ")}</option>)}</select></label>}
            <div className="forecast-mapping-pair">{(["source", "target"] as const).map(side => <div key={side}><h3>{side === "source" ? "实际数据维度" : "预算维度"}</h3>{dimensions.map(key => <label key={key}>{forecastDimensionLabels[key]}<input value={mapping[side][key]} list={side === "target" ? `forecast-budget-${key}` : undefined} onChange={event => setMapping({ ...mapping, [side]: { ...mapping[side], [key]: event.target.value } })}/></label>)}</div>)}</div>
            {dimensions.map(key => <datalist id={`forecast-budget-${key}`} key={key}>{budgetDimensionOptions[key].map(value => <option key={value} value={value}/>)}</datalist>)}
            <label>对应原因<input value={mapping.reason} onChange={event => setMapping({ ...mapping, reason: event.target.value })}/></label>
            <button type="button" disabled={!mapping.reason.trim() || dimensions.some(key => !mapping.source[key].trim() || !mapping.target[key].trim())} onClick={() => { const next = { source: Object.fromEntries(dimensions.map(key => [key, mapping.source[key].trim()])) as ForecastDimensions, target: Object.fromEntries(dimensions.map(key => [key, mapping.target[key].trim()])) as ForecastDimensions, reason: mapping.reason.trim() }; setAssumptions({ ...assumptions, mappings: [...assumptions.mappings.filter(item => JSON.stringify(item.source) !== JSON.stringify(next.source)), next] }); if (!reason.trim()) setReason(next.reason); setMapping({ source: emptyDims(), target: emptyDims(), reason: "" }); }}>加入维度对应</button>
            {assumptions.mappings.length > 0 && <ul className="forecast-edits">{assumptions.mappings.map((item, index) => <li key={JSON.stringify(item.source)}><span>{dimensions.map(key => item.source[key]).join(" / ")}<br/>→ {dimensions.map(key => item.target[key]).join(" / ")}</span><button type="button" onClick={() => setAssumptions({ ...assumptions, mappings: assumptions.mappings.filter((_, i) => i !== index) })}>移除</button></li>)}</ul>}
          </fieldset></details>
          </fieldset></details>
          <TrialAssumptionNotes assumptions={fixedRules ? version.calculation.rule_notes ?? [] : version.calculation.trial_assumptions ?? []} legacyTrial={trialMode}/>
          </section>
        </ForecastSettingsDrawer>
        <section className="forecast-confirm"><div className="forecast-confirm-status"><div><strong>{readonly ? version.status === "CONFIRMED" ? "本版已确认 · 只读查看" : "本版草稿 · 只读查看" : trialMode ? "试用试算不可正式确认" : version.status === "CONFIRMED" ? "预测已确认" : "确认本版预测结果"}</strong><span>P{version.sequence} · {version.as_of}</span></div>{!readonly && forecastConfirmationHint(version, identity, pendingPredictionChanges) && <p id="forecast-confirm-hint" role="status">{forecastConfirmationHint(version, identity, pendingPredictionChanges)}</p>}</div>{!readonly && version.status !== "CONFIRMED" && <button className="forecast-primary" aria-describedby="forecast-confirm-hint" disabled={Boolean(busy) || !canConfirmForecast(version, identity, pendingPredictionChanges)} onClick={() => setShowConfirm(true)}>确认本版预测结果</button>}
          {!readonly && showConfirm && !trialMode && <form onSubmit={event => { event.preventDefault(); if (identity && confirmationReason.trim() && canConfirmForecast(version, identity, pendingPredictionChanges)) void perform("确认本版预测结果", () => confirmForecast(identity, version.id, confirmationReason.trim()), "confirm"); }}><label>确认说明<input autoFocus required value={confirmationReason} onChange={event => setConfirmationReason(event.target.value)}/></label><div><button className="forecast-primary" disabled={Boolean(busy) || !confirmationReason.trim() || !canConfirmForecast(version, identity, pendingPredictionChanges)} type="submit">确认本版预测结果</button><button disabled={Boolean(busy)} type="button" onClick={() => { setShowConfirm(false); setConfirmationReason(""); }}>取消</button></div></form>}
        </section>
        <div className="forecast-secondary">{fixedRules ? <ForecastFixedRules version={version}/> : <details className="forecast-how"><summary>预测怎么算</summary><ForecastLogic/><ForecastConfirmationEvidence calculation={version.calculation}/></details>}<details className="forecast-history"><summary>预测记录 {data?.history.length ?? 0}</summary><ol>{data?.history.map(item => <li key={item.id}><button disabled={Boolean(busy) || unsaved} aria-current={item.id === version.id ? "true" : undefined} onClick={() => void loadSavedVersion(item.id)}><strong>P{item.sequence}</strong><span>{item.status === "CONFIRMED" ? "已确认" : "草稿"}</span><time>{item.as_of}</time><small>保存于 {savedAt(item.created_at)}</small>{item.id === version.id && <span>{readonly ? "正在查看" : "当前"}</span>}</button></li>)}</ol></details></div>
      </>}
    </main>
  </div>;
}
