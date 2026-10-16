import { ActionTable } from "./ActionTable";
import { skuText } from "./budgetDisplay";
import { useEffect, useMemo, useRef, useState, type CSSProperties, type MouseEvent, type ReactNode } from "react";
import { newRequestId, parseResponse, type ApiIdentity } from "./api";
import { FY27Header } from "./FY27Header";
import { budgetModuleName, defaultBudgetModuleYears } from "./budgetModules";
import { BudgetScopeBar } from "./BudgetScopeBar";
import { BudgetDataSection } from "./BudgetDataSection";
import { skuIdentityCells, skuIdentityHeaders } from "./SkuIdentity";
import { FilterSelect } from "./FilterSelect";
import { ForecastGroupSelector } from "./ForecastGroupSelector";
import { filterValues, matchesFilter, type FilterValue } from "./filterSelection";
import { hasForecastFilterState, readForecastViewFilters, writeForecastViewFilters, readForecastGroupDimensions, writeForecastGroupDimensions, type ForecastViewFilters } from "./forecastViewState";
import { BudgetUtilityPanel } from "./BudgetUtilityPanel";
import { ForecastDataReview, forecastConfirmationHint } from "./ForecastDataReview";
import type { ExportColumn } from "./budgetExport";
import {
  confirmForecast, defaultForecastStates, forecastDimensionLabels, forecastNumber, forecastSeasons, forecastStateLabels,
  getForecast, getForecastVersion, downloadForecastSnapshot, forecastFailure, forecastFailureCategory, groupForecastRows, summarizeForecastRows, forecastGroupDimensionLabels, forecastQuarterHeadcounts, groupForecastIssues, forecastMappingIssues, prepareForecastMapping, forecastPresentationRow, forecastActualRevenueDefaultsToZero, forecastActualHeadcountDefaultsToZero, refreshForecast, reviseForecast,
  trialFinancialDraft, applyTrialFinancialDraft, canConfirmForecast, usesFixedForecastRules, forecastCalculationComplete, fixedForecastRulesVersion, type ForecastAdjustmentKind, type ForecastAssumptions,
  type ForecastCalculation, type ForecastDimension, type ForecastGroupDimension, type ForecastGroup, type ForecastSummary, type ForecastDimensions, type ForecastEnvelope, type ForecastMapping,
  type ForecastNumber, type ForecastRow, type ForecastSeason, type ForecastState, type ForecastIssue, type ForecastScope, type TrialFinancialDraft, type TrialAssumption, type ForecastVersion, type ForecastRequestError,
} from "./forecastApi";
import "./forecast.css";

const dimensions = Object.keys(forecastDimensionLabels) as ForecastDimension[];
type ForecastAction = "load" | "history" | "update" | "save" | "download" | "confirm";
type ForecastPageFailure = { action: ForecastAction; error: ForecastRequestError };
const readForecastVersion = () => typeof window === "undefined" ? "" : new URLSearchParams(window.location.search).get("forecast_version") ?? "";
const forecastSearch = () => typeof window === "undefined" ? "" : window.location.search;
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
  if (revenueZero && headcountZero) return "实际结转收入、人次按0计";
  if (revenueZero) return "实际结转收入按0计";
  if (headcountZero) return "实际人次按0计";
  if (row.data_status === "ACTUAL_UNAVAILABLE") return "实际未对应";
  if (row.data_status === "BASELINE_ESTIMATE") return "预算参数估计";
  if (row.data_status === "TRIAL_SIZE_HELD") return "暂按上一季人数";
  return methodLabels[row.method];
}
const exportColumns: ExportColumn[] = [
  { key: "season", label: "季度" }, ...dimensions.map(key => ({ key, label: forecastDimensionLabels[key] })),
  { key: "budget_revenue", label: "定版预算结转收入（元）", format: "money" }, { key: "forecast_revenue", label: "预计结转收入（元）", format: "money" },
  { key: "budget_total", label: "预算人次", format: "count" }, { key: "forecast_total", label: "预计人次", format: "count" },
  { key: "additional", label: "预计后续新增新生", format: "count" }, { key: "new_withdrawal", label: "预计后续新生退班", format: "count" },
  { key: "old_withdrawal", label: "预计后续老生退班", format: "count" }, { key: "method_label", label: "预测方式" },
];
const forecastDetailTransfer = (rows: ForecastRow[], trialMode = false) => ({ columns: exportColumns, rows: rows.map(row => ({ ...forecastPresentationRow(row), method_label: forecastRowLabel(row) })), filename: trialMode ? "FY27-试用预测明细" : "FY27-达成预测明细" });

/** Explicit formats keep known budget cells numeric even when another group is “未单列”. */
export function forecastScopeTransfer(dimensions: readonly ForecastGroupDimension[], groups: ForecastGroup[], totals: ForecastSummary) {
  const columns: ExportColumn[] = [...dimensions.map(key => ({ key, label: forecastGroupDimensionLabels[key] })),
    { key: "budget_revenue", label: "预算结转收入（万元）", format: "money" }, { key: "forecast_revenue", label: "预计 / 实际结转收入（万元）", format: "money" },
    { key: "gap_label", label: "缺口 / 超额（万元）" }, { key: "achievement", label: "达成率", format: "rate" },
    ...(["budget", "forecast"] as const).flatMap(kind => (["total", "new", "old"] as const).map(key => ({ key: `${kind}_${key}`,
      label: `${kind === "budget" ? "预算人次" : "预计 / 实际人次"} / ${{ total: "总人次", new: "新生人次", old: "老生人次" }[key]}`, format: "count" as const }))),
  ];
  const values = (item: ForecastSummary) => ({
    budget_revenue: item.budget_revenue === null ? item.hasUnbudgeted && !item.unmapped ? "未单列" : null : item.budget_revenue / 10000,
    forecast_revenue: item.forecast_revenue === null ? null : item.forecast_revenue / 10000,
    gap_label: item.gap === null ? null : `${item.gap >= 0 ? "缺口" : "超额"} ${displayNumber(Math.abs(item.gap) / 10000, 2)}`,
    achievement: item.achievement,
    budget_total: item.budget_total === null && item.hasUnbudgeted && !item.unmapped ? "未单列" : item.budget_total,
    budget_new: item.budget_new, budget_old: item.budget_old,
    forecast_total: item.forecast_total, forecast_new: item.forecast_new, forecast_old: item.forecast_old,
  });
  const rows = groups.map(item => ({ ...Object.fromEntries(dimensions.map(key => [key, item.dimensions[key] || "未填写"])), ...values(item) }));
  if (totals.records > 0) rows.push({ ...Object.fromEntries(dimensions.map((key, index) => [key, index === 0 ? "当前范围合计" : ""])), ...values(totals) });
  return { columns, rows, filename: "当前范围达成" };
}

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
      <div><dt>结转收入勾稽</dt><dd>{result(check.revenue_matched)}</dd></div>
      <div><dt>人次勾稽</dt><dd>{result(check.enrollment_matched)}</dd></div>
      <div><dt>不阻断的空组合</dt><dd>{check.excluded_empty_rows}项</dd></div>
      <div><dt>已知明细结转收入（元）</dt><dd>{rawValue(check.known_revenue)}</dd></div>
      <div><dt>部门实际结转收入（元）</dt><dd>{rawValue(check.actual_revenue)}</dd></div>
      <div><dt>已知明细人次</dt><dd>{rawValue(check.known_enrollment)}</dd></div>
      <div><dt>部门实际人次</dt><dd>{rawValue(check.actual_enrollment)}</dd></div>
    </dl></div>)}
    {records.length > 0 && <details className="forecast-how-details"><summary>处理记录 · {records.length}项</summary>{records.map((issue, index) => <details key={`${issue.code}-${issue.season}-${index}`}><summary>{[issue.season, ...dimensions.map(key => issue.dims?.[key])].filter(Boolean).join(" / ") || "部门记录"}</summary><code>{issue.code}</code><p>{skuText(issue.message)}</p><pre>{JSON.stringify(issue, null, 2)}</pre></details>)}</details>}
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
      <div><dt>已定版实际待核对</dt><dd>未知结转收入、人次在明细中按0展示，原始记录仍待核对；部门合计使用完整已定版实际。</dd></div>
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
  return assumptions.length ? <details className="forecast-trial-notes"><summary>{legacyTrial ? "查看试用说明" : "预测处理记录"} {assumptions.length} 条</summary>{[...groups].map(([code, values]) => <details key={code}><summary><strong>{skuText(values[0].message)}</strong><span>{values.length} 条</span></summary><ul>{values.slice(0, limits[code] ?? 25).map((item, index) => {
    const detail = item.detail && typeof item.detail === "object" ? item.detail as Record<string, unknown> : {};
    const unapplied = detail.unapplied_renewal != null || detail.unapplied_share != null;
    return <li key={`${code}-${index}`}><span>{[item.season, ...dimensions.map(key => item.dims?.[key])].filter(Boolean).join(" / ")}</span>{item.message !== values[0].message && <span>{skuText(item.message)}</span>}{unapplied && <strong className="forecast-scope-note">已填写比例暂未用于计算</strong>}</li>;
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
  const [seasonFilter, setSeasonFilter] = useState<FilterValue>("秋季");
  const editorRef = useRef<HTMLDivElement>(null);
  const matchedScopes = scopes.map((scope, index) => ({ scope, index })).filter(({ scope }) => !query.trim() || dimensions.some(key => scope.dims[key].includes(query.trim())));
  const visibleSeasons = forecastSeasons.filter(value => matchesFilter(value, seasonFilter));
  const matches = matchedScopes.flatMap(item => visibleSeasons.map(value => ({ ...item, season: value })));
  const pageRows = matches.slice(page * 20, (page + 1) * 20);
  const parameterTransfer = {
    columns: [
      { key: "season", label: "季度" }, ...dimensions.map(key => ({ key, label: forecastDimensionLabels[key] })),
      { key: "price", label: "学费（元）", format: "money" },
      ...(["discount", "recognition", "renewal", "share"] as const).map(key => ({ key, label: { discount: "折扣", recognition: "当季结转比例", renewal: "续班率", share: "新生占比" }[key], format: "rate" as const })),
      { key: "additional", label: "后续新增（人次）", format: "count" },
    ] as ExportColumn[],
    rows: matches.map(({ scope, season: rowSeason }) => ({ season: rowSeason, ...scope.dims, ...scope.financial[rowSeason], additional: scope.additional[rowSeason] })),
    filename: "FY27-预测参数明细",
  };
  const currentScope = selected === null ? null : scopes[selected];
  const draftChanged = Boolean(currentScope && draft && JSON.stringify(draft) !== JSON.stringify(trialFinancialDraft(currentScope, season, legacyTrial)));
  useEffect(() => { onDraftChange?.(draftChanged); }, [draftChanged, onDraftChange]);
  useEffect(() => () => onDraftChange?.(false), [onDraftChange]);
  function selectScope(index: number, rowSeason: ForecastSeason) { if (draftChanged) { setError("先加入本次修改，或放弃当前编辑，再切换明细。"); return; } setSeason(rowSeason); setSelected(index); setDraft(trialFinancialDraft(scopes[index], rowSeason, legacyTrial)); setError(""); requestAnimationFrame(() => editorRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" })); }
  function changeSeason(next: FilterValue) {
    if (draftChanged) { setError("先加入本次修改，或放弃当前编辑，再切换季度。"); return; }
    setSeasonFilter(next); setPage(0); setError("");
    const values = filterValues(next);
    if (values.length === 1) {
      const nextSeason = values[0] as ForecastSeason;
      setSeason(nextSeason); if (selected !== null) setDraft(trialFinancialDraft(scopes[selected], nextSeason, legacyTrial));
    } else if (!matchesFilter(season, next)) { setSelected(null); setDraft(null); }
  }
  function apply() {
    if (!currentScope || selected === null || !draft || disabled) return;
    const result = applyTrialFinancialDraft(currentScope, season, states[season], draft);
    if (!result.scope) { setError(result.error); return; }
    onChange(scopes.map((item, index) => index === selected ? result.scope! : item)); setSelected(null); setDraft(null); setError("");
  }
  return <BudgetDataSection id="forecast-independent-parameters" title={legacyTrial ? "试用假设" : "独立业务参数"} count={matches.length} countLabel="条季度明细" headingLevel={3} className="forecast-trial-scopes">
    <div className="forecast-trial-scope-filters"><label>季度<FilterSelect aria-label="筛选参数季度" value={seasonFilter} onChange={changeSeason} options={[...forecastSeasons]}/></label><label>筛选SKU<input value={query} placeholder="校区、产品、年级或科目" onChange={event => { setQuery(event.target.value); setPage(0); }}/></label></div>
    <ActionTable exportData={parameterTransfer} keyboardNavigation tableLabel={legacyTrial ? "试用假设明细" : "独立业务参数"} aria-label={legacyTrial ? "试用假设明细表" : "独立业务参数表"} className="forecast-sku-parameter-table" scrollClassName="forecast-table-wrap"><thead><tr><th scope="col">季度</th>{skuIdentityHeaders.map(label => <th scope="col" key={label}>{label}</th>)}{["学费（元）", "折扣", "当季结转比例", "续班率", "新生占比", "后续新增", "操作"].map(label => <th scope="col" key={label}>{label}</th>)}</tr></thead><tbody>{pageRows.map(({ scope, index, season: rowSeason }) => {
      const financial = scope.financial[rowSeason], readonly = scope.kind === "FINANCIAL_ONLY" || states[rowSeason] === "FINALIZED";
      return <tr key={`${rowSeason}-${JSON.stringify(scope.dims)}`}><td>{rowSeason}</td>{skuIdentityCells(scope.dims)}<td>{displayNumber(financial?.price, 2)}</td><td>{achievement(financial?.discount)}</td><td>{achievement(financial?.recognition)}</td><td>{achievement(financial?.renewal)}</td><td>{achievement(financial?.share)}</td><td>{scope.additional[rowSeason] === undefined ? "—" : displayNumber(scope.additional[rowSeason])}</td><td>{readonly ? <span className="forecast-scope-readonly">{scope.kind === "FINANCIAL_ONLY" ? "结转实际" : "已定版实际"}</span> : <button disabled={disabled} aria-label={`调整${rowSeason} ${dimensions.map(key => scope.dims[key]).join(" / ")}${legacyTrial ? "试用假设" : "预测参数"}`} onClick={() => selectScope(index, rowSeason)}>调整</button>}</td></tr>;
    })}</tbody></ActionTable>{!matches.length && <p className="forecast-empty" role="status">{legacyTrial ? "当前范围没有独立试用明细" : "当前范围没有独立业务明细"}</p>}
    <div className="budget-data-section__pagination forecast-pagination"><button disabled={page === 0} onClick={() => setPage(page - 1)}>上一页</button><span>{matches.length ? page + 1 : 0} / {Math.ceil(matches.length / 20)} 页</span><button disabled={(page + 1) * 20 >= matches.length} onClick={() => setPage(page + 1)}>下一页</button></div>
    {currentScope && draft && <div className="forecast-scope-editor" ref={editorRef}><header><div><strong>{season} · {currentScope.dims.campus}</strong><span>{currentScope.dims.track} / {currentScope.dims.product} / {currentScope.dims.grade} / {currentScope.dims.subject}</span></div><button disabled={disabled} onClick={() => { setSelected(null); setDraft(null); setError(""); }}>{draftChanged ? "放弃当前编辑" : "取消"}</button></header>
      <fieldset disabled={disabled || states[season] === "FINALIZED" || currentScope.kind === "FINANCIAL_ONLY"}><div className="forecast-scope-fields">{(["price", "discount", "recognition", "renewal", "share"] as const).map(key => <label key={key}>{{ price: "学费（元）", discount: "折扣（%）", recognition: "当季结转比例（%）", renewal: "续班率（%）", share: "新生占比（%）" }[key]}<input type="number" min="0" max={key === "price" ? "1000000000" : "100"} step="any" placeholder={key === "renewal" || key === "share" ? "未设定" : undefined} value={draft[key]} onChange={event => setDraft({ ...draft, [key]: event.target.value })}/></label>)}
      {(states[season] === "OPEN" || states[season] === "ENDED") && <label>后续新增新生（人次）<input type="number" min="0" max="10000000" step="1" value={draft.additional} onChange={event => setDraft({ ...draft, additional: event.target.value })}/></label>}</div>
      {(!draft.renewal.trim() || !draft.share.trim()) && <p className="forecast-scope-note">未设定项自动参考同产品、同年级；无可用参数时暂按上季人数。</p>}
      <label>{legacyTrial ? "试用调整原因" : "调整原因"}<input maxLength={1000} value={draft.reason} onChange={event => setDraft({ ...draft, reason: event.target.value })}/></label>{error && <p className="forecast-scope-error" role="alert">{error}</p>}<button type="button" onClick={apply}>加入本次修改</button></fieldset>
    </div>}
  </BudgetDataSection>;
}

export function ForecastIssueGroups({ issues }: { issues: ForecastIssue[] }) {
  const [opened, setOpened] = useState<Set<string>>(new Set());
  const [limits, setLimits] = useState<Record<string, number>>({});
  const groups = groupForecastIssues(issues);
  return <div className="forecast-issue-groups">{groups.map((group, index) => {
    const open = opened.has(group.key), limit = limits[group.key] ?? 25;
    return <section key={group.key}><button className="forecast-issue-group-toggle" aria-expanded={open} aria-controls={`forecast-issue-group-${index}`} onClick={() => setOpened(previous => { const next = new Set(previous); if (next.has(group.key)) next.delete(group.key); else next.add(group.key); return next; })}>
      <span aria-hidden="true">{open ? "−" : "+"}</span><strong>{group.title}</strong>{group.season && <span>{group.season}</span>}{group.code === "UPSTREAM_HEADCOUNT_UNAVAILABLE" && <span className="forecast-auto-condition">无需单独处理</span>}<span className={`forecast-issue-state is-${group.severity.toLowerCase()}`}>{group.issues.length} 条明细{group.severity === "WARNING" ? "提示" : ""}</span>
    </button>{open && <div id={`forecast-issue-group-${index}`}><ul>{group.issues.slice(0, limit).map((issue, itemIndex) => <li key={`${group.key}-${itemIndex}`}><div><strong>{skuText(issue.message)}</strong>{(issue.season || issue.dims) && <span>{[issue.season, ...dimensions.map(key => issue.dims?.[key])].filter(Boolean).join(" / ")}</span>}</div></li>)}</ul>
      {group.issues.length > limit && <button onClick={() => setLimits({ ...limits, [group.key]: limit + 25 })}>再显示 25 项</button>}<details className="forecast-group-code"><summary>技术信息</summary><code>{group.code}</code></details>
    </div>}</section>;
  })}</div>;
}

/** Presentation only: the server owns the forecasting rules and official readiness. */
export function ForecastDashboard({ calculation, busy = false, trialMode = false, onRecords }: { calculation: ForecastCalculation; busy?: boolean; trialMode?: boolean; onRecords?: () => void }) {
  const [group, setGroup] = useState<ForecastGroupDimension[]>(() => readForecastGroupDimensions(forecastSearch()));
  const preferOpenSeason = trialMode || calculation.fixed_rules_version === fixedForecastRulesVersion;
  const defaultSeason = preferOpenSeason ? calculation.quarters.find(quarter => quarter.state === "OPEN")?.season ?? "" : "";
  const [season, setSeason] = useState<FilterValue>(() => readForecastViewFilters(forecastSearch(), defaultSeason).season);
  const previousOpenDefault = useRef(preferOpenSeason);
  useEffect(() => { if (preferOpenSeason && !previousOpenDefault.current && !hasForecastFilterState(forecastSearch())) setSeason(defaultSeason); previousOpenDefault.current = preferOpenSeason; }, [preferOpenSeason, defaultSeason]);
  const [scope, setScope] = useState<Partial<Record<ForecastDimension, FilterValue>>>(() => {
    const saved = readForecastViewFilters(forecastSearch(), defaultSeason);
    return Object.fromEntries(dimensions.map(key => [key, saved[key]]));
  });
  const [detailPage, setDetailPage] = useState(0);
  const [view, setView] = useState<"board" | "details">("board");
  const rows = useMemo(() => calculation.rows.filter(row => matchesFilter(row.season, season) && dimensions.every(key => matchesFilter(row[key], scope[key] ?? ""))), [calculation, season, scope]);
  const grouped = useMemo(() => groupForecastRows(rows, group), [rows, group]);
  const totals = useMemo(() => summarizeForecastRows(rows), [rows]);
  const groupTableHead = useRef<HTMLTableSectionElement>(null);
  useEffect(() => {
    const head = groupTableHead.current, table = head?.closest("table");
    if (!head || !table) return;
    const measure = () => table.style.setProperty("--forecast-group-header-height", `${head.rows[0]?.getBoundingClientRect().height ?? 0}px`);
    measure();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    observer?.observe(head);
    return () => observer?.disconnect();
  }, [group, rows, view]);
  const max = Math.max(1, ...calculation.quarters.flatMap(quarter => [forecastNumber(quarter.budget_revenue), forecastNumber(quarter.forecast_revenue)]).filter((value): value is number => value !== null).map(Math.abs));
  const complete = forecastCalculationComplete(calculation, trialMode);
  const pageRows = rows.slice(detailPage * 30, (detailPage + 1) * 30).map(forecastPresentationRow);
  const filters = { season, ...Object.fromEntries(dimensions.map(key => [key, scope[key] ?? ""])) } as Record<ForecastDimension | "season", FilterValue>;
  function applyFilters(next: ForecastViewFilters) { setSeason(next.season); setScope(Object.fromEntries(dimensions.map(key => [key, next[key]]))); setDetailPage(0); }
  function updateFilters(next: ForecastViewFilters) {
    applyFilters(next);
    if (typeof window === "undefined") return;
    const url = new URL(window.location.href); url.search = writeForecastViewFilters(url.search, next);
    if (url.href !== window.location.href) window.history.pushState(window.history.state, "", url);
  }
  function updateGroup(next: ForecastGroupDimension[]) {
    setGroup(next);
    if (typeof window === "undefined") return;
    const url = new URL(window.location.href); url.search = writeForecastGroupDimensions(url.search, next);
    if (url.href !== window.location.href) window.history.pushState(window.history.state, "", url);
  }
  useEffect(() => {
    if (typeof window === "undefined") return;
    const syncDefaultScope = () => {
      if (hasForecastFilterState(forecastSearch())) return;
      const url = new URL(window.location.href);
      url.search = writeForecastViewFilters(url.search, readForecastViewFilters(url.search, defaultSeason));
      window.history.replaceState(window.history.state, "", url);
    };
    // A default is still the actual reading scope: expose it to deep links and AI,
    // without creating a navigation entry or fetching a different budget result.
    syncDefaultScope();
    const restore = () => { applyFilters(readForecastViewFilters(forecastSearch(), defaultSeason)); setGroup(readForecastGroupDimensions(forecastSearch())); syncDefaultScope(); };
    window.addEventListener("popstate", restore);
    return () => window.removeEventListener("popstate", restore);
  }, [defaultSeason]);
  const scopeDimensions = [{ key: "season" as const, label: "季度", options: [...forecastSeasons] }, ...dimensions.map(key => ({ key, label: forecastDimensionLabels[key], options: [...new Set(calculation.rows.map(row => row[key]))].sort((a, b) => a.localeCompare(b, "zh-CN")) }))];
  return <>
    <nav className="forecast-content-tabs" aria-label="预测展示方式"><button type="button" aria-current={view === "board" ? "page" : undefined} onClick={() => setView("board")}>看板</button><button type="button" aria-current={view === "details" ? "page" : undefined} onClick={() => setView("details")}>明细</button><button type="button" aria-haspopup="dialog" disabled={!onRecords || busy} onClick={onRecords}>记录</button></nav>
    <div hidden={view !== "board"} className="forecast-board-overview">
    <div className="forecast-global-scope"><strong>全年总览</strong><span>全部业务 · 不受下方筛选影响</span></div>
    <section className="forecast-metrics" aria-label="FY27 部门全年预测">
      <div><span>定版预算结转收入</span><strong>{money(calculation.annual.budget_revenue)}</strong></div>
      <div className={complete ? "forecast-key-metric" : ""}><span>预计全年结转收入</span><strong>{money(calculation.annual.forecast_revenue)}</strong></div>
      <div className={forecastNumber(calculation.annual.gap) !== null && forecastNumber(calculation.annual.gap)! > 10000 ? "forecast-gap" : ""}><span>{forecastGapLabel(calculation.annual.gap)}</span><strong>{gapMoney(calculation.annual.gap)}</strong></div>
      <div><span>预计达成率</span><strong>{achievement(calculation.annual.achievement)}</strong></div>
    </section>
    {!complete && <p className="forecast-overview-status" role="status">部分数据待核对，预测尚未完整</p>}
    <BudgetDataSection id="forecast-quarter-achievement" title="季度达成" className="forecast-section" actions={<div className="forecast-legend"><span><i className="is-budget"/>预算</span><span><i/>预计 / 实际</span></div>}>
      <div className="forecast-quarter-bars" role="img" aria-label={calculation.quarters.map(quarter => `${quarter.season}，${forecastStateLabels[quarter.state]}，预算结转收入${money(quarter.budget_revenue)}，${quarter.state === "FINALIZED" ? "冻结实际结转收入" : "预计结转收入"}${money(quarter.forecast_revenue)}`).join("；")}>
        {calculation.quarters.map(quarter => <div key={quarter.season}><div className="forecast-quarter-name"><strong>{quarter.season}</strong><span className={`forecast-state is-${quarter.state.toLowerCase()}`}>{forecastStateLabels[quarter.state]}</span></div>
          <div className="forecast-bar-row"><span>预算</span><div className="forecast-bar-track">{forecastNumber(quarter.budget_revenue) !== null ? <i className={`is-budget ${forecastNumber(quarter.budget_revenue)! < 0 ? "is-negative" : ""}`} style={{ transform: `scaleX(${Math.abs(forecastNumber(quarter.budget_revenue)!) / max})` }}/> : <em>未提供</em>}</div><strong>{money(quarter.budget_revenue)}</strong></div>
          <div className="forecast-bar-row"><span>{quarter.state === "FINALIZED" ? "实际" : "预计"}</span><div className="forecast-bar-track">{forecastNumber(quarter.forecast_revenue) !== null ? <i className={forecastNumber(quarter.forecast_revenue)! < 0 ? "is-negative" : ""} style={{ transform: `scaleX(${Math.abs(forecastNumber(quarter.forecast_revenue)!) / max})` }}/> : <em>待预测</em>}</div><strong>{money(quarter.forecast_revenue)}</strong></div>
          <div className="forecast-quarter-gap"><span>{forecastGapLabel(quarter.gap, quarter.state === "FINALIZED")}</span><strong>{gapMoney(quarter.gap)}</strong></div>
        </div>)}
      </div>
      <div className="forecast-table-region" aria-label="季度达成明细表"><ActionTable keyboardNavigation aria-label="季度达成明细表" className="forecast-quarter-table" tableLabel="季度达成" scrollClassName="forecast-table-wrap">
        <thead><tr>{["季度", "状态", "预算结转收入（万元）", "预计 / 实际结转收入（万元）", "缺口 / 超额（万元）"].map(label => <th key={label} rowSpan={2} scope="col">{label}</th>)}
          <th colSpan={3} scope="colgroup">预算人次</th><th colSpan={3} scope="colgroup">预计 / 实际人次</th>
        </tr><tr>{["budget", "forecast"].flatMap(kind => ["总人次", "新生人次", "老生人次"].map((label, index) => <th key={`${kind}-${label}`} scope="col" className={index === 0 ? "forecast-student-group-start" : undefined}>{label}</th>))}</tr></thead>
        <tbody>{calculation.quarters.map(quarter => {
          const counts = forecastQuarterHeadcounts(calculation.rows, quarter.season);
          return <tr key={quarter.season}><th scope="row">{quarter.season}</th><td>{forecastStateLabels[quarter.state]}</td>
            <td>{displayNumber(forecastNumber(quarter.budget_revenue) === null ? null : forecastNumber(quarter.budget_revenue)! / 10000, 2)}</td>
            <td>{displayNumber(forecastNumber(quarter.forecast_revenue) === null ? null : forecastNumber(quarter.forecast_revenue)! / 10000, 2)}</td>
            <td>{forecastNumber(quarter.gap) === null ? "—" : `${forecastNumber(quarter.gap)! >= 0 ? "缺口" : "超额"} ${displayNumber(Math.abs(forecastNumber(quarter.gap)!) / 10000, 2)}`}</td>
            <td className="forecast-student-group-start">{displayNumber(quarter.budget_total)}</td><td>{displayNumber(counts.budget_new)}</td><td>{displayNumber(counts.budget_old)}</td>
            <td className="forecast-student-group-start">{displayNumber(quarter.forecast_total)}</td><td>{displayNumber(counts.forecast_new)}</td><td>{displayNumber(counts.forecast_old)}</td>
          </tr>;
        })}</tbody>
      </ActionTable></div>
    </BudgetDataSection>
    </div>
    <BudgetScopeBar filters={filters} dimensions={scopeDimensions} count={rows.length} disabled={busy} onChange={updateFilters} onClear={() => updateFilters(readForecastViewFilters("?bf_scope=1"))}/>
    <BudgetDataSection id="forecast-scope-achievement" title="当前范围达成" count={grouped.length} countLabel="个分组" className="forecast-section" hidden={view !== "board"}
      actions={<ForecastGroupSelector value={group} onChange={updateGroup} disabled={busy}/> }>
      {!group.includes("season") && new Set(rows.map(row => row.season)).size > 1 && <p className="forecast-group-scope-note">已合并所选季度；人次为累计人次，不是去重人数。</p>}
      <div className="forecast-table-region" aria-label="维度达成表"><ActionTable keyboardNavigation aria-label="维度达成表" tableLabel="当前范围达成" className="forecast-scope-table" scrollClassName="forecast-table-wrap forecast-scope-table-wrap"
        style={{ "--forecast-group-count": group.length } as CSSProperties} exportData={forecastScopeTransfer(group, grouped, totals)} exportLabel="导出当前表格">
        <thead ref={groupTableHead}><tr>{group.map(key => <th key={key} scope="col" rowSpan={2} className="forecast-group-dimension">{forecastGroupDimensionLabels[key]}</th>)}
          {["预算结转收入（万元）", "预计 / 实际结转收入（万元）", "缺口 / 超额（万元）", "达成率"].map(label => <th key={label} rowSpan={2} scope="col" className="forecast-group-metric">{label}</th>)}
          <th scope="colgroup" colSpan={3}>预算人次</th><th scope="colgroup" colSpan={3}>预计 / 实际人次</th>
        </tr><tr>{["budget", "forecast"].flatMap(kind => ["总人次", "新生人次", "老生人次"].map((label, index) => <th key={`${kind}-${label}`} scope="col" className={index === 0 ? "forecast-student-group-start" : undefined}>{label}</th>))}</tr></thead>
        <tbody>{grouped.map(item => <tr key={item.key}>{group.map((key, index) => <th scope="row" key={key} className="forecast-group-dimension">{item.dimensions[key] || "未填写"}{index === 0 && item.unmapped && <span className="forecast-unmapped">含未对应实际</span>}</th>)}
          <td>{item.hasUnbudgeted && !item.unmapped && item.budget_revenue === null ? "未单列" : displayNumber(forecastNumber(item.budget_revenue) === null ? null : forecastNumber(item.budget_revenue)! / 10000, 2)}</td>
          <td>{displayNumber(forecastNumber(item.forecast_revenue) === null ? null : forecastNumber(item.forecast_revenue)! / 10000, 2)}</td>
          <td>{forecastNumber(item.gap) === null ? "—" : `${forecastNumber(item.gap)! >= 0 ? "缺口" : "超额"} ${displayNumber(Math.abs(forecastNumber(item.gap)!) / 10000, 2)}`}</td><td>{achievement(item.achievement)}</td>
          <td className="forecast-student-group-start">{item.hasUnbudgeted && !item.unmapped && item.budget_total === null ? "未单列" : displayNumber(item.budget_total)}</td><td>{displayNumber(item.budget_new)}</td><td>{displayNumber(item.budget_old)}</td>
          <td className="forecast-student-group-start">{displayNumber(item.forecast_total)}</td><td>{displayNumber(item.forecast_new)}</td><td>{displayNumber(item.forecast_old)}</td>
        </tr>)}</tbody>
        {rows.length > 0 && <tfoot><tr><th scope="row">当前范围合计</th>{group.slice(1).map(key => <td key={key} className="forecast-group-dimension"/>)}
          <td>{totals.hasUnbudgeted && !totals.unmapped && totals.budget_revenue === null ? "未单列" : displayNumber(forecastNumber(totals.budget_revenue) === null ? null : forecastNumber(totals.budget_revenue)! / 10000, 2)}</td>
          <td>{displayNumber(forecastNumber(totals.forecast_revenue) === null ? null : forecastNumber(totals.forecast_revenue)! / 10000, 2)}</td>
          <td>{forecastNumber(totals.gap) === null ? "—" : `${forecastNumber(totals.gap)! >= 0 ? "缺口" : "超额"} ${displayNumber(Math.abs(forecastNumber(totals.gap)!) / 10000, 2)}`}</td><td>{achievement(totals.achievement)}</td>
          <td className="forecast-student-group-start">{totals.hasUnbudgeted && !totals.unmapped && totals.budget_total === null ? "未单列" : displayNumber(totals.budget_total)}</td><td>{displayNumber(totals.budget_new)}</td><td>{displayNumber(totals.budget_old)}</td>
          <td className="forecast-student-group-start">{displayNumber(totals.forecast_total)}</td><td>{displayNumber(totals.forecast_new)}</td><td>{displayNumber(totals.forecast_old)}</td>
        </tr></tfoot>}
      </ActionTable>{!rows.length && <p className="forecast-empty" role="status">当前范围暂无预测明细</p>}</div>
    </BudgetDataSection>
    <BudgetDataSection id="forecast-details" title="预测明细" count={rows.length} countLabel="条明细" className="forecast-section" hidden={view !== "details"}>
      <div className="forecast-table-region" aria-label="预测明细表"><ActionTable keyboardNavigation aria-label="预测明细表" className="forecast-sku-table" tableLabel="预测明细" scrollClassName="forecast-table-wrap" exportData={forecastDetailTransfer(rows, trialMode)}><thead><tr><th scope="col">季度</th>{skuIdentityHeaders.map(label => <th key={label} scope="col">{label}</th>)}{["预算结转收入（元）", "预计 / 实际结转收入（元）", "预算人次", "预计 / 实际人次", "预测方式"].map(label => <th key={label} scope="col">{label}</th>)}</tr></thead><tbody>{pageRows.map((row, index) => <tr key={`${row.season}-${JSON.stringify(dimensions.map(key => row[key]))}-${index}`}><td>{row.season}</td>{skuIdentityCells(row)}<td>{row.budget_covered === false && !row.unmapped ? "未单列" : displayNumber(row.budget_revenue, 2)}</td><td>{displayNumber(row.forecast_revenue, 2)}</td><td>{row.counts_not_applicable ? "—" : row.budget_covered === false && !row.unmapped ? "未单列" : displayNumber(row.budget_total)}</td><td>{row.counts_not_applicable ? "—" : displayNumber(row.forecast_total)}</td><td>{forecastRowLabel(rows[detailPage * 30 + index])}</td></tr>)}</tbody></ActionTable></div>
      <div className="forecast-pagination"><button disabled={detailPage === 0} onClick={() => setDetailPage(detailPage - 1)}>上一页</button><span>{rows.length ? detailPage + 1 : 0} / {Math.ceil(rows.length / 30)} 页</span><button disabled={(detailPage + 1) * 30 >= rows.length} onClick={() => setDetailPage(detailPage + 1)}>下一页</button></div>
    </BudgetDataSection>
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
  const [versionsOpen, setVersionsOpen] = useState(false);
  const leaveDialog = useRef<HTMLDialogElement>(null);
  const confirmationBar = useRef<HTMLElement>(null);
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
    const bar = confirmationBar.current, shell = bar?.closest<HTMLElement>(".forecast-shell");
    if (!bar || !shell) return;
    const measure = () => shell.style.setProperty("--forecast-action-height", `${Math.ceil(bar.getBoundingClientRect().height)}px`);
    measure();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    observer?.observe(bar);
    window.addEventListener("resize", measure);
    return () => { observer?.disconnect(); window.removeEventListener("resize", measure); shell.style.removeProperty("--forecast-action-height"); };
  }, [Boolean(version), showConfirm]);
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
    if (lock.current || (readonly && !["load", "history"].includes(action))) return false;
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
      return true;
    }
    catch (error) { const detail = forecastFailure(error); setError(detail.message); setFailure({ action, error: detail }); return false; }
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
  useEffect(() => {
    if (typeof window === "undefined") return;
    const restoreVersion = () => {
      const requested = readForecastVersion();
      if (requested === selectedVersionId || !identity) return;
      if (busy || unsaved || lock.current) { writeForecastVersion(selectedVersionId); setNotice("先完成或放弃当前操作，再切换预测版本。"); return; }
      void loadSavedVersion(requested)?.then(loaded => {
        if (!loaded && readForecastVersion() === requested) writeForecastVersion(selectedVersionId);
      });
    };
    window.addEventListener("popstate", restoreVersion);
    return () => window.removeEventListener("popstate", restoreVersion);
  }, [identity, busy, unsaved, selectedVersionId]);
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
    <FY27Header active="forecast" fiscalYear={data?.baseline_year} onBeforeLeave={beforeLeave} sourceVersion={version ? { id: version.id, label: `P${version.sequence}` } : null}><div className="decision-version"><span className={`status-dot ${version?.status === "CONFIRMED" && !trialMode ? "is-published" : ""}`}/><span>{trialMode ? "试用试算" : version ? version.status === "CONFIRMED" ? "预测已确认" : "预测草稿" : "尚未预测"}</span>{version && <small>P{version.sequence}</small>}</div></FY27Header>
    <dialog className="forecast-leave-dialog" ref={leaveDialog} aria-labelledby="forecast-leave-title" onCancel={() => setPendingNavigation("")}>
      <h2 id="forecast-leave-title">修改尚未保存</h2>
      <div><button className="forecast-primary" autoFocus onClick={() => { setPendingNavigation(""); if (pendingPredictionChanges) openDrawer(drawerTab); }}>继续编辑</button><button onClick={() => { leaving.current = true; location.assign(pendingNavigation); }}>离开，不保存</button></div>
    </dialog>
    <main className="decision-workspace" id="forecast-content">
      <header className="forecast-heading"><h1>{budgetModuleName("forecast", data?.baseline_year ?? defaultBudgetModuleYears.forecast)}</h1><div>{version && <span>截至 {version.as_of}</span>}<button type="button" disabled={!data || Boolean(busy)} aria-haspopup="dialog" onClick={() => setVersionsOpen(true)}>版本记录{version ? ` P${version.sequence}` : ""}</button>{version && canMaintain && <button disabled={Boolean(busy)} onClick={() => openDrawer(pendingPredictionChanges ? drawerTab : "people")}>调整预测</button>}{!readonly && <button className="forecast-primary" disabled={!identity || !canEditBasis || Boolean(busy) || pendingPredictionChanges} onClick={() => void updateForecast()}>{["更新预测", "重试更新"].includes(busy) ? "正在更新…" : version ? "更新预测" : "生成预测"}</button>}</div></header>
      <BudgetUtilityPanel open={versionsOpen} title="预测版本与快照" onClose={() => setVersionsOpen(false)} busy={Boolean(busy)}>
        <div className="forecast-version-actions">{readonly && <button disabled={!identity || Boolean(busy) || unsaved} onClick={() => void loadSavedVersion("")}>返回当前版本</button>}{version && <button disabled={!identity || Boolean(busy)} onClick={() => void downloadSnapshot()}>{busy === "下载快照" ? "正在下载…" : "下载本次快照"}</button>}</div>
        {unsaved && <p className="forecast-version-pending" role="status">先保存或放弃当前修改，再切换版本。</p>}
        <section className="forecast-history" aria-label="预测记录"><h3>预测记录 {data?.history.length ?? 0}</h3><ol>{data?.history.map(item => <li key={item.id}><button disabled={!identity || Boolean(busy) || unsaved} aria-current={item.id === version?.id ? "true" : undefined} aria-label={`查看预测 P${item.sequence}`} onClick={() => void loadSavedVersion(item.id)}><strong>P{item.sequence}</strong><span>{item.status === "CONFIRMED" ? "已确认" : "草稿"}</span><time>{item.as_of}</time><small>保存于 {savedAt(item.created_at)}</small>{item.id === version?.id && <span>{readonly ? "正在查看" : "当前"}</span>}</button></li>)}</ol>{!data?.history.length && <p>暂无保存记录</p>}</section>
      </BudgetUtilityPanel>
      {readonly && <section className="forecast-readonly" role="status"><strong>{version ? `P${version.sequence} 保存结果 · 只读查看` : "历史版本 · 只读查看"}</strong><span>{version ? `保存于 ${savedAt(version.created_at) || version.as_of}，数据截至 ${version.as_of}` : busy ? "正在读取指定版本" : "未能读取指定版本"}</span><p>查看本版保存的结果和快照。返回当前版本后可更新或调整预测。</p></section>}
      {pendingPredictionChanges && !drawerOpen && <div className="forecast-pending" role="status"><span>修改未保存</span><button onClick={() => openDrawer(drawerTab)}>继续编辑</button></div>}
      {busy && <div className="forecast-working" role="status" aria-live="polite"><span/>{["更新预测", "重试更新"].includes(busy) ? "正在读取数据并生成预测，完成后保存新版本…" : `${busy}中…`}</div>}
      {error && <section className="forecast-error" role="alert"><strong>{updateFailed ? "更新未完成" : failure?.action === "download" ? "快照下载未完成" : "操作未完成"}</strong>{failure && <span className="forecast-error-category">{forecastFailureCategory(failure.error)}</span>}<p>{error}</p>{updateFailed && <p className="forecast-retained">{retained ? `上次保存 P${retained.sequence} · 数据截至 ${retained.as_of}${retained.created_at ? ` · 保存于 ${savedAt(retained.created_at)}` : ""}。` : "尚无已保存预测。"}{failure?.error.detail.retained_version !== undefined ? "该保存版本已保留。" : "本次更新是否完成尚未确认，重试将核对同一次更新。"}</p>}{loginRequired && <p>请先检查观远登录或数据权限，再更新预测。</p>}{failure?.error.detail.diagnostic_id && <small>核对编号：{failure.error.detail.diagnostic_id}</small>}<div className="forecast-error-actions">{updateFailed ? canEditBasis && !pendingPredictionChanges && failure?.error.detail.retryable !== false && <button disabled={Boolean(busy)} onClick={() => void updateForecast(true)}>重试更新</button> : failure?.action === "download" ? <button disabled={Boolean(busy)} onClick={() => void downloadSnapshot()}>重试下载</button> : <button disabled={Boolean(busy) || unsaved} onClick={() => identity ? void loadSavedVersion(selectedVersionId) : location.reload()}>{identity ? "重新读取保存结果" : "重新打开"}</button>}</div></section>}
      {notice && <p className="forecast-notice" role="status">{notice}</p>}
      {!data ? !error && <div className="forecast-skeleton" aria-label="正在读取预测" role="status"><div/><div/><div/></div> : !version ? <BudgetDataSection id="forecast-start" title="先生成 FY27 预测" className="forecast-start"><div className="forecast-start-states">{forecastSeasons.map(season => <div key={season}><strong>{season}</strong><span>{forecastStateLabels[defaultForecastStates[season]]}</span></div>)}</div></BudgetDataSection> : <>
        {trialMode && <ForecastTrialStatus ready={version.calculation.trial_ready === true} busy={Boolean(busy)}/>}
        <ForecastDashboard calculation={version.calculation} trialMode={trialMode} busy={Boolean(busy)} onRecords={() => setVersionsOpen(true)}/>
        <ForecastSettingsDrawer open={drawerOpen} tab={drawerTab} onTabChange={setDrawerTab} onClose={() => { if (!busy) setDrawerOpen(false); }} canMaintain={canMaintain} busy={Boolean(busy)} footer={canMaintain && <>
          <label>本次修改原因<input disabled={Boolean(busy)} value={reason} onChange={event => setReason(event.target.value)}/></label>
          <div className="forecast-drawer-save"><span role="status">{busy ? `${busy}中…` : unfinishedInputs ? "先完成当前明细调整" : pendingPredictionChanges ? "修改未保存" : "没有未保存的修改"}</span><button type="button" className="forecast-primary" disabled={Boolean(busy) || !settingChanged || unfinishedInputs || !reason.trim() || !Number.isInteger(assumptions.minimum_sample) || assumptions.minimum_sample < 1} onClick={() => identity && void perform("保存预测", () => reviseForecast(identity, version.id, assumptions, reason.trim()))}>保存并重算</button></div>
        </>}>
          {error && <p className="forecast-scope-error" role="alert">{error}</p>}
          <section role="tabpanel" id="forecast-panel-people" aria-labelledby="forecast-tab-people" hidden={drawerTab !== "people"} className="forecast-settings forecast-drawer-panel">
          <fieldset disabled={Boolean(busy) || !canMaintain}><legend>按校区调整人数</legend>
            {version.calculation.campuses.length > 0 && <div className="forecast-table-region" aria-label="校区预测人数"><ActionTable keyboardNavigation tableLabel="校区预测人数" className="forecast-campus-table" scrollClassName="forecast-table-wrap" aria-label="校区预测人数"><thead><tr>{["季度", "校区", "预算新生", "当前业务新生", "预算内后续新增", "独立业务新增", "后续新增合计", "后续新生退班", "后续老生退班"].map(label => <th key={label} scope="col">{label}</th>)}</tr></thead><tbody>{version.calculation.campuses.map(item => <tr key={`${item.season}-${item.campus}`}><th scope="row">{item.season}</th><td>{item.campus}</td><td>{displayNumber(item.budget_new)}</td><td>{displayNumber(item.current_business_new)}</td><td>{displayNumber(item.budgeted_additional)}</td><td>{displayNumber(item.unbudgeted_additional)}</td><td>{displayNumber(item.additional)}</td><td>{displayNumber(item.new_withdrawal)}</td><td>{displayNumber(item.old_withdrawal)}</td></tr>)}</tbody></ActionTable></div>}
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
        <footer ref={confirmationBar} className="forecast-confirm" aria-label="预测确认操作"><div className="forecast-confirm-status"><div><strong>{readonly ? version.status === "CONFIRMED" ? "本版已确认 · 只读查看" : "本版草稿 · 只读查看" : trialMode ? "试用试算不可正式确认" : version.status === "CONFIRMED" ? "预测已确认" : "确认本版预测结果"}</strong><span>P{version.sequence} · {version.as_of}</span></div>{!readonly && forecastConfirmationHint(version, identity, pendingPredictionChanges) && <p id="forecast-confirm-hint" role="status">{forecastConfirmationHint(version, identity, pendingPredictionChanges)}</p>}</div><div className="forecast-confirm-actions"><button type="button" disabled={Boolean(busy)} onClick={() => openDrawer("data")}>{version.calculation.issues.some(issue => issue.severity === "BLOCKING") ? `待处理 ${version.calculation.issues.filter(issue => issue.severity === "BLOCKING").length}` : "数据核对"}</button>{!readonly && version.status !== "CONFIRMED" && !showConfirm && <button className="forecast-primary" aria-describedby="forecast-confirm-hint" disabled={Boolean(busy) || !canConfirmForecast(version, identity, pendingPredictionChanges)} onClick={() => setShowConfirm(true)}>确认本版预测结果</button>}</div>
          {!readonly && showConfirm && !trialMode && <form onSubmit={event => { event.preventDefault(); if (identity && confirmationReason.trim() && canConfirmForecast(version, identity, pendingPredictionChanges)) void perform("确认本版预测结果", () => confirmForecast(identity, version.id, confirmationReason.trim()), "confirm"); }}><label>确认说明<input autoFocus required value={confirmationReason} onChange={event => setConfirmationReason(event.target.value)}/></label><div><button className="forecast-primary" disabled={Boolean(busy) || !confirmationReason.trim() || !canConfirmForecast(version, identity, pendingPredictionChanges)} type="submit">确认本版预测结果</button><button disabled={Boolean(busy)} type="button" onClick={() => { setShowConfirm(false); setConfirmationReason(""); }}>取消</button></div></form>}
        </footer>
        <div className="forecast-secondary">{fixedRules ? <ForecastFixedRules version={version}/> : <details className="forecast-how"><summary>预测怎么算</summary><ForecastLogic/><ForecastConfirmationEvidence calculation={version.calculation}/></details>}</div>
      </>}
    </main>
  </div>;
}
