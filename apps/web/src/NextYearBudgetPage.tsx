import { Children, isValidElement, useEffect, useMemo, useRef, useState, type MouseEvent, type ReactNode } from "react";
import { parseResponse, type ApiIdentity } from "./api";
import { FY27Header } from "./FY27Header";
import { BudgetPageNavigation, useBudgetPagePosition } from "./BudgetPageNavigation";
import { BudgetScopeBar } from "./BudgetScopeBar";
import { BudgetDataSection } from "./BudgetDataSection";
import { skuIdentityCells, skuIdentityHeaders } from "./SkuIdentity";
import { BudgetUtilityPanel } from "./BudgetUtilityPanel";
import { QuarterlyBusinessTable } from "./QuarterlyBusinessTable";
import { downloadBudgetWorkbook, type ExportColumn } from "./budgetExport";
import { forecastNumber } from "./forecastApi";
import { BudgetAnalysisView } from "./BudgetAnalysisView";
import { ActionTable } from "./ActionTable";
import { copyTableText } from "./tableTransfer";
import { skuText } from "./budgetDisplay";
import type { BudgetFact } from "./budgetAnalysisTypes";
import { allocateNextYearTrackTargets, applyNextYearEdits, getNextYearBudget, nextYearEditKey, nextYearRowTotal, nextYearSeasons,
  prepareNextYearBudget, previewNextYearBasis, previewNextYearBudget, reviseNextYearBudget, getNextYearCatalog, getNextYearVersion, restoreNextYearVersion, balanceNextYearBudget, checkNextYearClassReadiness, proposeNextYearClassRepair,
  type NextYearBasisPreview, type NextYearCalculation, type NextYearEdits, type NextYearEnvelope, type NextYearInputs, type NextYearNumber, type NextYearRate, type NextYearFinance, type NextYearRow, type NextYearCatalog, type NextYearCombination, type NextYearClassReadiness, type NextYearClassReadinessRow, type NextYearClassRepairResult, type NextYearEditSummary, type NextYearEditItem } from "./nextYearApi";
import "./nextYear.css";
import { summarizeNextYearChanges, undoNextYearChange, nextYearEditTask } from "./nextYearChanges";
import { applyNextYearClassRepairSelection, nextYearActionKey, nextYearBalanceActions, assertNextYearSelectionPreview, type NextYearSelectableAction } from "./nextYearSelection";
import { readNextYearFilters, persistNextYearFilters, writeNextYearFilters, validateNextYearFilters } from "./nextYearFilters";

export const nextYearTasks = ["定目标和SKU", "入口新生定多少", "续班率、新生占比", "学费、折扣、班额", "看结果 / 对目标"] as const;
export const nextYearTaskShortLabels = ["目标", "新生", "续班", "学费", "结果"] as const;
export const nextYearTaskPrompts = ["全年要做多少钱？哪些校区、产品参与？", "哪些年级是入口？各校区分到多少新生？", "老生留几成？新生占多大比例？", "每人收多少学费？多少人成班？", "结转收入够不够目标？哪里有差额？"] as const;
type Task = 1 | 2 | 3 | 4 | 5;
type Dimension = "season" | "track" | "campus" | "product" | "grade" | "subject";
type Filters = Record<Dimension, string>;
const dimensionLabels: Record<Dimension, string> = { season: "季度", track: "赛道", campus: "校区", product: "产品", grade: "年级", subject: "科目" };
const emptyFilters: Filters = { season: "", track: "", campus: "", product: "", grade: "", subject: "" };
const emptyNewBusiness = { campus: "", track: "", product: "", grade: "七年级", subject: "数学", price: "", classSize: "", reason: "新增业务" };
const fy = (year: number) => `FY${String(year).slice(-2)}`;
const readYear = () => { const value = Number(new URLSearchParams(location.search).get("year")); return Number.isInteger(value) && value >= 2028 && value <= 2100 ? value : 2028; };
export const effectiveNextYearEntry = (row: NextYearCombination) => row.entry_override ?? row.entry;
const readTask = (): Task => { const number = Number(new URLSearchParams(location.search).get("nyTask")); return number >= 1 && number <= 5 && Number.isInteger(number) ? number as Task : 5; };
const format = (value: NextYearNumber | undefined, digits = 0) => { const n = forecastNumber(value); return n === null ? "—" : n.toLocaleString("zh-CN", { maximumFractionDigits: digits }); };
const money = (value: NextYearNumber | undefined) => { const n = forecastNumber(value); return n === null ? "—" : format(n / 10000, 2); };
const textDimensions = (row: Partial<Record<Exclude<Dimension, "season">, string>>) => [row.campus, row.track, row.product, row.grade, row.subject].filter(Boolean).join(" / ");
const matches = (row: Partial<Record<Dimension, string>>, filters: Filters) => (Object.keys(filters) as Dimension[]).every(key => !filters[key] || row[key] === undefined || row[key] === filters[key]);
/** Presentation only: keep partial/invalid input visible and never stage a new budget edit. */
export function nextYearQuarterInputValue(raw: string | undefined, original: NextYearNumber, percent = false) {
  if (raw === undefined) return percent && original !== null ? Number(original) / 1000000 : original;
  const value = forecastNumber(raw);
  return !raw.trim() || value === null ? raw : percent ? value / 100 : raw;
}
type QuarterLayout = "quarters" | "details";
export function NextYearQuarterLayoutSwitch({ label, value, onChange, readOnly, busy }: { label: string; value: QuarterLayout; onChange(value: QuarterLayout): void; readOnly: boolean; busy: boolean }) {
  return <div className="ny-quarter-layout" role="group" aria-label={label}><button type="button" aria-pressed={value === "quarters"} disabled={busy} onClick={() => onChange("quarters")}>季度对照</button><button type="button" aria-pressed={value === "details"} disabled={busy} onClick={() => onChange("details")}>{readOnly ? "逐季明细" : "逐季编辑"}</button></div>;
}
export const nextYearClassStatus = (status: string) => ({ PASS: "达标", NO_PLANNED_CLASS: "未计划开班", BELOW_MINIMUM_CLASS_SIZE: "未达班均", HEADCOUNT_UNAVAILABLE: "人次待核对", PARAMETER_MISSING: "班均待填写" } as Record<string, string>)[status] ?? "待核对";
export function nextYearRateUsage(inputs: NextYearInputs, rate: NextYearRate) {
  if (rate.season !== "暑假") return { renewal: true, share: true };
  const cohort = inputs.combinations.filter(row => row.enabled && ["track", "product", "grade", "campus"].every(key => row[key as keyof typeof row] === rate[key as keyof NextYearRate]));
  const targets = new Set(cohort.map(row => row.id));
  return { renewal: inputs.opening_sources.some(row => row.target_id !== null && targets.has(row.target_id)), share: cohort.length > 0 && !cohort.every(effectiveNextYearEntry) };
}
const gradeOrder = ["一年级", "二年级", "三年级", "四年级", "五年级", "六年级", "七年级", "八年级", "九年级", "高一", "高二", "高三"];
const classIssueLabel = (code: string) => ({ BELOW_MINIMUM_CLASS_SIZE: "人数不足", ENABLED_COMBINATION_ALL_SEASONS_ZERO: "四季均零" } as Record<string, string>)[code] ?? "需核对";
type ClassCheckScope = { code: string; season: string; campus: string; group: "code" | "campus" | "track" };
const emptyClassCheckScope: ClassCheckScope = { code: "", season: "", campus: "", group: "code" };

/** Read-only fallback while the server checks current edits. Closure is never inferred here. */
export function nextYearClassIssueRows(calculation: NextYearCalculation, inputs: NextYearInputs): NextYearClassReadinessRow[] {
  return calculation.issues.filter(issue => ["BELOW_MINIMUM_CLASS_SIZE", "ENABLED_COMBINATION_ALL_SEASONS_ZERO"].includes(issue.code)).map(issue => {
    const combination = inputs.combinations.find(row => row.id === issue.combination_id);
    const row = calculation.rows.find(item => item.combination_id === issue.combination_id && item.season === issue.season);
    const detail = issue.detail && typeof issue.detail === "object" ? issue.detail as Record<string, unknown> : {};
    const detailNumber = (value: unknown) => typeof value === "string" || typeof value === "number" ? forecastNumber(value) : null;
    const minimum = detailNumber(detail.minimum_class_size) ?? inputs.class_sizes.find(item => item.track === combination?.track && item.product === combination?.product)?.minimum ?? null;
    const total = issue.code === "ENABLED_COMBINATION_ALL_SEASONS_ZERO" ? 0 : row?.total_students ?? null;
    return { code: issue.code, combination_id: String(issue.combination_id ?? ""), season: issue.code === "ENABLED_COMBINATION_ALL_SEASONS_ZERO" ? null : issue.season ?? null,
      track: combination?.track ?? row?.track ?? "", product: combination?.product ?? row?.product ?? "", campus: combination?.campus ?? row?.campus ?? "", grade: combination?.grade ?? row?.grade ?? "", subject: combination?.subject ?? row?.subject ?? "",
      current_students: total, old_students: issue.code === "ENABLED_COMBINATION_ALL_SEASONS_ZERO" ? 0 : row?.old_students ?? null, new_students: issue.code === "ENABLED_COMBINATION_ALL_SEASONS_ZERO" ? 0 : row?.new_students ?? null, minimum,
      gap: detailNumber(detail.class_size_gap) ?? (issue.code === "BELOW_MINIMUM_CLASS_SIZE" && total !== null && minimum !== null ? Math.max(0, minimum - total) : issue.code === "ENABLED_COMBINATION_ALL_SEASONS_ZERO" ? 0 : null),
      carryover_yuan: combination?.opening_carryover_yuan ?? null, closure_allowed: null, plan_locked: false, seed_plan: false,
      entry_locked: Boolean(combination?.entry_locked), rate_locked: false, combination_locked: false, lock_reasons: [], rate_parameters: [],
      actions: [issue.code === "ENABLED_COMBINATION_ALL_SEASONS_ZERO" ? "核对是否开展；检查后查看可处理方式" : "检查实际可调范围，再调整人次或招生参数"],
    };
  });
}

const classTreatment = (row: NextYearClassReadinessRow, checked: boolean) => row.bound_proof?.unreachable ? "当前范围无法成班" : Number(row.carryover_yuan) > 0 ? "有结转须保留" : row.lock_reasons.length ? "含锁定项" : row.closure_allowed ? "可考虑停开" : row.seed_plan ? "需核对计划人数" : checked ? "需调整人次或参数" : "检查后查看处理方式";
const classExportColumns: ExportColumn[] = [
  ...["code", "campus", "track", "product", "grade", "subject", "season"].map((key, index) => ({ key, label: ["问题", "校区", "赛道", "产品", "年级", "科目", "季度"][index] })),
  ...["current_students", "old_students", "new_students", "minimum", "gap"].map((key, index) => ({ key, label: ["总人次", "老生人次", "新生人次", "最低成班人数", "缺口"][index], format: "count" as const })),
  { key: "carryover_yuan", label: "期初结转（元）", format: "money" }, { key: "treatment", label: "可处理方式" }, { key: "proof", label: "可调范围检查" },
];

export function NextYearClassReadinessPanel({ rows, report, scope, onScopeChange, checked, stale, busy, readOnly, hasTarget, onCheck, onNavigate, onClose, onGenerate, generating = false, embedded = false }: {
  rows: NextYearClassReadinessRow[]; report: NextYearClassReadiness | null; scope: ClassCheckScope; onScopeChange: (value: ClassCheckScope) => void;
  checked: boolean; stale: boolean; busy: boolean; readOnly: boolean; hasTarget: boolean; onCheck: () => void; onNavigate: (row: NextYearClassReadinessRow, action: "manual" | "rates" | "range") => void; onClose: () => void;
  onGenerate?: () => void; generating?: boolean; embedded?: boolean;
}) {
  const selected = rows.filter(row => (!scope.code || row.code === scope.code) && (!scope.season || row.season === scope.season) && (!scope.campus || row.campus === scope.campus));
  const groupName = (row: NextYearClassReadinessRow) => scope.group === "code" ? classIssueLabel(row.code) : row[scope.group];
  const ordered = [...selected].sort((a, b) => groupName(a).localeCompare(groupName(b), "zh-CN") || a.campus.localeCompare(b.campus, "zh-CN") || nextYearSeasons.indexOf(a.season as typeof nextYearSeasons[number]) - nextYearSeasons.indexOf(b.season as typeof nextYearSeasons[number]));
  return <section className="ny-class-check" id="ny-class-check" aria-labelledby="ny-class-check-title"><div className="ny-section-heading"><div><h2 id="ny-class-check-title" tabIndex={-1}>成班检查</h2><span>{readOnly ? "历史存档结果 · 只读" : checked ? "当前输入检查结果" : stale ? "上次计算结果 · 重新检查后更新" : "已保存预算结果"}</span></div><div className="ny-heading-actions">{!readOnly && <><button disabled={busy} onClick={onCheck}>{busy && !generating ? "正在检查…" : "重新检查"}</button>{onGenerate && <button className="decision-primary" disabled={busy} onClick={onGenerate}>{generating ? "正在生成…" : "生成成班方案"}</button>}</>}{!embedded && <button disabled={busy} onClick={onClose}>收起</button>}</div></div>
    <div className="ny-class-check-summary"><span>待处理 <strong>{report?.issue_count ?? rows.length}</strong></span><span>涉及业务 <strong>{report?.combination_count ?? new Set(rows.map(row => row.combination_id)).size}</strong></span><span>有结转须保留 <strong>{report ? report.carry_protected_count : "—"}</strong></span><span>可核对是否关闭 <strong>{report ? report.closure_allowed_count : "—"}</strong></span></div>
    {!hasTarget && !readOnly && <p className="ny-range-note">未设经营目标，仍可检查成班</p>}
    {checked && report?.calculation_complete === false && <p className="ny-range-note" role="status">人次或参数尚不完整；当前可核对 {rows.length} 项成班问题，仍有 {report.blocking_issue_count} 项待处理</p>}
    {rows.length === 0 ? <p className="ny-empty">{busy ? "正在检查当前输入…" : checked && report?.calculation_complete === false ? "当前检查未完整，请核对人次和参数后重新检查" : checked && report?.calculation_complete ? "当前输入没有成班问题" : readOnly ? "存档未列出成班问题" : stale ? "输入已有修改，请重新检查成班" : "已保存结果未列出成班问题，可重新检查"}</p> : <><div className="ny-class-check-filters"><label>问题<select aria-label="成班问题筛选" value={scope.code} onChange={event => onScopeChange({ ...scope, code: event.target.value })}><option value="">全部</option>{[...new Set(rows.map(row => row.code))].map(code => <option key={code} value={code}>{classIssueLabel(code)}</option>)}</select></label><label>季度<select aria-label="成班季度筛选" value={scope.season} onChange={event => onScopeChange({ ...scope, season: event.target.value })}><option value="">全部</option>{nextYearSeasons.map(season => <option key={season}>{season}</option>)}</select></label><label>校区<select aria-label="成班校区筛选" value={scope.campus} onChange={event => onScopeChange({ ...scope, campus: event.target.value })}><option value="">全部</option>{[...new Set(rows.map(row => row.campus))].sort((a, b) => a.localeCompare(b, "zh-CN")).map(campus => <option key={campus}>{campus}</option>)}</select></label><label>分组<select aria-label="成班分组" value={scope.group} onChange={event => onScopeChange({ ...scope, group: event.target.value as ClassCheckScope["group"] })}><option value="code">按问题</option><option value="campus">按校区</option><option value="track">按赛道</option></select></label>{(scope.code || scope.season || scope.campus) && <button onClick={() => onScopeChange({ ...emptyClassCheckScope, group: scope.group })}>清除筛选</button>}</div>
      <PagedTable rows={ordered} label="成班问题明细" exportColumns={classExportColumns} exportRow={row => ({ ...row, code: classIssueLabel(row.code), season: row.season ?? "全年 · 四季均零", treatment: classTreatment(row, checked), proof: skuText(row.bound_proof?.message ?? "未检查") })} headers={["分组", "问题", "校区 / 赛道 / 产品 / 年级 / 科目", "季度", "总人次", "最低成班人数", "缺口", "期初结转（元）", "可处理方式"]} render={row => <><th>{groupName(row)}</th><td>{classIssueLabel(row.code)}</td><td>{textDimensions(row)}</td><td>{row.season ?? "全年 · 四季均零"}</td><td>{format(row.current_students)}</td><td>{format(row.minimum)}</td><td>{format(row.gap)}</td><td>{format(row.carryover_yuan, 2)}</td><td><div className="ny-class-actions"><strong>{classTreatment(row, checked)}</strong>
        <details><summary>查看说明与可调范围</summary>{row.bound_proof && <p>{skuText(row.bound_proof.message)}</p>}{row.actions.filter(action => !action.includes("经营目标")).map((action, index) => <p key={index}>{skuText(action)}</p>)}{row.lock_reasons.length > 0 && <p>已有用户锁定，不会自动修改</p>}{row.closure_allowed === false && Number(row.carryover_yuan) > 0 && <p>有期初结转，不能直接关闭</p>}{row.rate_parameters.map(parameter => <p key={parameter.rate_id}>{parameter.season}：续班率 {format(parameter.renewal.lower_bound_ppm / 10000, 2)}%—{format(parameter.renewal.upper_bound_ppm / 10000, 2)}%{parameter.renewal.locked ? "（已锁定）" : ""}；新生占比 {format(parameter.share.lower_bound_ppm / 10000, 2)}%—{format(parameter.share.upper_bound_ppm / 10000, 2)}%{parameter.share.locked ? "（已锁定）" : ""}</p>)}</details>
        {!readOnly && <div><button disabled={busy} onClick={() => onNavigate(row, "manual")}>{row.plan_locked || row.entry_locked ? "查看锁定人次" : "调整人次"}</button><button disabled={busy} onClick={() => onNavigate(row, "rates")}>{row.rate_locked ? "查看锁定参数" : "调整参数"}</button><button disabled={busy} onClick={() => onNavigate(row, "range")}>{row.closure_allowed ? "核对是否关闭" : "查看SKU"}</button></div>}</div></td></>}/>
    </>}
  </section>;
}
const classRepairConstraints = ["enabled_scopes_preserved", "source_preserved", "opening_carryover_preserved", "minimum_preserved", "entry_total_preserved", "price_and_discount_fixed", "user_locks_preserved", "operating_target_unchanged"];
export const canApplyNextYearClassRepair = (result: NextYearClassRepairResult) => result.status === "READY" && result.issue_count.after === 0 && Boolean(result.proposed_inputs) && result.calculation.complete && classRepairConstraints.every(key => result.constraints[key] === true);
const repairFieldLabel = (field: string) => ({ entry_students: "入口人次", students: "新生目标", "plans.students": "新生目标", renewal_ppm: "续班率", share_ppm: "新生占比", renewal_lower_ppm: "续班率下限", renewal_upper_ppm: "续班率上限", share_lower_ppm: "新生占比下限", share_upper_ppm: "新生占比上限" } as Record<string, string>)[field] ?? "人数调整";
const repairValue = (value: NextYearNumber, field: string) => `${format(field.endsWith("_ppm") ? forecastNumber(value) === null ? null : Number(value) / 10000 : value, field.endsWith("_ppm") ? 2 : 0)}${field.endsWith("_ppm") ? "%" : ""}`;
const repairActionColumns: ExportColumn[] = [
  ...["campus", "track", "product", "grade", "subject", "season", "field_label"].map((key, index) => ({ key, label: ["校区", "赛道", "产品", "年级", "科目", "季度", "调整项"][index] })),
  { key: "unit", label: "单位" }, { key: "before_value", label: "原计算值 / 参数值" }, { key: "after_value", label: "建议值" }, { key: "before_input", label: "原录入计划 / 入口人次", format: "count" }, { key: "before_total_students", label: "原总人次", format: "count" }, { key: "after_total_students", label: "建议总人次", format: "count" }, { key: "minimum", label: "最低成班人数", format: "count" }, { key: "stable_old_floor", label: "比例范围内老生下限", format: "count" }, { key: "stability_reserve_students", label: "调平预留人次", format: "count" }, { key: "reason", label: "原因" },
];

export function NextYearClassRepairPanel({ result, originalInputs, busy, onApply, onCancel }: { result: NextYearClassRepairResult; originalInputs?: NextYearInputs; busy: boolean; onApply: (keys?: Set<string>) => void; onCancel: () => void }) {
  const ready = canApplyNextYearClassRepair(result);
  const problems = [...result.lock_conflicts.map(row => ({ ...row, kind: "锁定冲突" })), ...result.unresolved.map(row => ({ ...row, kind: "尚待处理" })), ...(result.stability_unresolved ?? []).map(row => ({ ...row, kind: "预留范围提示" }))];
  return <section className="ny-class-proposal" aria-labelledby="ny-class-proposal-title"><div className="ny-section-heading"><div><h2 id="ny-class-proposal-title" tabIndex={-1}>成班方案</h2><span>建议预览 · 未应用 · 未保存</span></div><button disabled={busy} onClick={onCancel}>取消方案</button></div>
    <div className="ny-proposal-outcome"><div><span>成班问题</span><strong>{format(result.issue_count.before)} <span>→</span> {format(result.issue_count.after)}</strong></div><p>{skuText(result.message)}</p></div>
    <p className="ny-range-note">{classRepairConstraints.every(key => result.constraints[key] === true) ? "保留全部业务、入口总量、最低成班人数和已锁定项。" : "方案尚未满足全部保留条件，暂不能应用。"}新增人次是招生目标，不是实际招生。</p>
    <div className="ny-class-check-summary"><span>入口重分配 <strong>{format(result.summary.entry_reallocated_students)}</strong></span><span>新生目标 <strong>{format(result.summary.new_target_count)} 项</strong></span><span>新增招生目标 <strong>{format(result.summary.additional_new_students)} 人次</strong>{result.summary.stability_reserve_students !== undefined && <small>含调平预留 {format(result.summary.stability_reserve_students)} 人次</small>}</span><span>边界调整 <strong>{format(result.summary.changed_rate_bound_count)} 项</strong></span></div>
    {result.adjustable_range_class_safe === false ? <p className="ny-range-note" role="status">当前已成班；锁定项无法保证整个可调范围内持续成班，后续调平仍逐项校验。</p> : result.stability_scope && <p className="ny-range-note">预留适用：入口分配不变、建议比例范围内。不保证任意经营目标可达。</p>}
    <ImpactComparison before={result.before_calculation} after={result.calculation}/>
    <NextYearActionSelection actions={result.actions} originalInputs={originalInputs} proposedInputs={result.proposed_inputs} busy={busy} ready={ready} onApply={onApply}/>
    {result.entry_soft_deviations.length > 0 && <details className="ny-repair-problems"><summary>入口分配超出历史参考范围 · {result.entry_soft_deviations.length} 项</summary><PagedTable rows={result.entry_soft_deviations} label="入口参考范围偏离明细" headers={["校区 / 业务", "原入口人次", "建议入口人次", "历史参考范围", "超出人次", "原因"]} render={row => <><th>{textDimensions(row)}</th><td>{format(row.before)}</td><td>{format(row.after)}</td><td>{format(row.soft_lower)}—{format(row.soft_upper)}</td><td>{format(row.deviation_students)}</td><td>{skuText(row.reason)}</td></>}/></details>}
    {problems.length > 0 && <details className="ny-repair-problems" open><summary>{result.lock_conflicts.length || result.unresolved.length ? "尚待处理" : "范围提示"} · {problems.length} 项</summary><PagedTable rows={problems} label="成班方案未解决明细" headers={["状态", "校区 / 业务", "季度", "问题"]} render={row => <><th>{row.kind}</th><td>{textDimensions(row) || "全部预算"}</td><td>{row.season ?? "全年"}</td><td>{skuText(row.message)}</td></>}/></details>}
    <div className="ny-proposal-footer"><span>{ready ? "以上结果为完整建议；部分勾选将重新校验，应用后仍需保存为新版本" : result.issue_count.after > 0 ? `仍有 ${format(result.issue_count.after)} 项成班问题，原预算未修改` : "方案仍需核对，原预算未修改"}</span></div>
  </section>;
}
const dimensionSort = (key: Dimension, a: string, b: string) => key === "grade" && gradeOrder.includes(a) && gradeOrder.includes(b) ? gradeOrder.indexOf(a) - gradeOrder.indexOf(b) : a.localeCompare(b, "zh-CN");
export const nextYearExportColumns: ExportColumn[] = [
  ...(Object.entries(dimensionLabels).map(([key, label]) => ({ key, label }))),
  { key: "old_students", label: "老生人次", format: "count" }, { key: "new_students", label: "新生人次", format: "count" },
  { key: "total_students", label: "总人次", format: "count" }, { key: "revenue_yuan", label: "预算结转收入（元）", format: "money" },
  { key: "price_yuan", label: "学费（元）", format: "money" }, { key: "discount", label: "明细折扣", format: "rate" },
  { key: "renewal", label: "续班率", format: "rate" }, { key: "share", label: "新生占比", format: "rate" },
  { key: "class_size_status", label: "开班状态" },
  { key: "class_count", label: "班量", format: "count" }, { key: "planned_class_size", label: "计划班额", format: "count" },
];
export const nextYearExportRows = (rows: NextYearRow[]) => rows.map(row => ({ ...row,
  discount: row.discount_ppm === null ? null : row.discount_ppm / 1000000,
  renewal: row.renewal_ppm === null ? null : row.renewal_ppm / 1000000,
  share: row.share_ppm === null ? null : row.share_ppm / 1000000,
  class_size_status: nextYearClassStatus(row.class_size_status),
}));

/** Projection only: the backend remains the owner of budget calculations. */
export function nextYearAnalysisFacts(calculation: NextYearCalculation, inputs?: NextYearInputs): BudgetFact[] {
  return calculation.rows.map(row => {
    const rate = forecastNumber(row.renewal_ppm), index = nextYearSeasons.indexOf(row.season);
    const previous = index > 0 ? calculation.rows.find(item => item.combination_id === row.combination_id && item.season === nextYearSeasons[index - 1])?.total_students
      : inputs?.opening_sources.filter(item => item.target_id === row.combination_id).reduce((sum, item) => sum + item.students, 0);
    const total = forecastNumber(row.total_students), price = forecastNumber(row.price_yuan), discount = forecastNumber(row.discount_ppm);
    return { id: `${row.combination_id}:${row.season}`, season: row.season, track: row.track, campus: row.campus, product: row.product, grade: row.grade, subject: row.subject,
      revenue: row.revenue_yuan, old: row.old_students, new: row.new_students, total: row.total_students,
      listRevenue: total === null || price === null ? null : total * price,
      business: total === null || price === null || discount === null ? null : total * price * discount / 1000000,
      detailDiscount: discount === null ? null : discount / 1000000, price: row.price_yuan,
      renewal: rate === null ? null : rate / 1000000, share: row.share_ppm === null ? null : row.share_ppm / 1000000,
      renewalDenominator: previous ?? null, renewalNumerator: previous === undefined || previous === null || rate === null ? null : previous * rate / 1000000 };
  });
}

export function nextYearImpactRows(before: NextYearCalculation, after: NextYearCalculation) {
  const keys = new Set([...before.rows, ...after.rows].map(row => `${row.combination_id}:${row.season}`));
  return [...keys].flatMap(key => {
    const first = before.rows.find(row => `${row.combination_id}:${row.season}` === key), next = after.rows.find(row => `${row.combination_id}:${row.season}` === key);
    const differences = ["revenue_yuan", "old_students", "new_students", "total_students", "class_count"].some(field => forecastNumber((first as unknown as Record<string, NextYearNumber> | undefined)?.[field]) !== forecastNumber((next as unknown as Record<string, NextYearNumber> | undefined)?.[field]));
    if (!differences) return [];
    const delta = (field: "revenue_yuan" | "total_students" | "class_count") => { const a = first ? forecastNumber(first[field]) : 0, b = next ? forecastNumber(next[field]) : 0; return a === null || b === null ? null : b - a; };
    return [{ key, name: textDimensions(next ?? first ?? {}), season: (next ?? first)!.season, before: first?.revenue_yuan ?? 0, after: next?.revenue_yuan ?? 0,
      revenue_delta: delta("revenue_yuan"), student_delta: delta("total_students"), class_delta: delta("class_count") }];
  });
}

function ImpactComparison({ before, after }: { before: NextYearCalculation; after: NextYearCalculation }) {
  const rows = nextYearImpactRows(before, after), delta = (field: keyof typeof before.annual) => { const first = forecastNumber(before.annual[field]), next = forecastNumber(after.annual[field]); return first === null || next === null ? null : next - first; };
  const sign = (value: number | null, isMoney = false) => value === null ? "—" : `${value > 0 ? "+" : ""}${isMoney ? money(value) : format(value)}`;
  return <section className="ny-impact"><div className="ny-section-heading"><h2>修改影响对比</h2><span>{rows.length} 条结果变化</span></div>
    <div className="ny-impact-metrics"><div><span>结转收入变化（万元）</span><strong>{sign(delta("revenue_yuan"), true)}</strong><small>{money(before.annual.revenue_yuan)} → {money(after.annual.revenue_yuan)}</small></div>{([["old_students", "老生变化"], ["new_students", "新生变化"], ["total_students", "总人次变化"], ["class_count", "班量变化"]] as const).map(([field, label]) => <div key={field}><span>{label}</span><strong>{sign(delta(field))}</strong><small>{format(before.annual[field])} → {format(after.annual[field])}</small></div>)}</div>
    <details><summary>查看变化明细</summary><PagedTable rows={rows} label="预算修改影响明细" headers={["校区 / 业务", "季度", "原结转收入（万元）", "新结转收入（万元）", "结转收入变化（万元）", "人次变化", "班量变化"]} render={row => <><th>{row.name}</th><td>{row.season}</td><td>{money(row.before)}</td><td>{money(row.after)}</td><td>{sign(row.revenue_delta, true)}</td><td>{sign(row.student_delta)}</td><td>{sign(row.class_delta)}</td></>}/></details>
  </section>;
}

export function parseNextYearParameterPaste(text: string, rows: NextYearRate[]): NextYearEdits {
  const edits: NextYearEdits = {}, lines = text.trim().split(/\r?\n/).filter(line => line.trim());
  if (!lines.length) throw new Error("先粘贴参数");
  const seen = new Set<string>();
  for (const [index, line] of lines.entries()) {
    const cells = line.split("\t").map(value => value.trim()), id = cells[0];
    if (index === 0 && ["参数ID", "ID", "id"].includes(id)) continue;
    if (cells.length !== 3) throw new Error(`第 ${index + 1} 行须为参数ID、续班率、新生占比三列`);
    if (!rows.some(row => row.id === id)) throw new Error(`第 ${index + 1} 行参数不在当前筛选范围`);
    if (seen.has(id)) throw new Error(`第 ${index + 1} 行参数ID重复`); seen.add(id);
    for (const [column, field] of [[1, "renewal_ppm"], [2, "share_ppm"]] as const) if (cells[column] !== "") edits[nextYearEditKey("rates", id, field)] = cells[column].replace(/%$/, "");
  }
  if (!Object.keys(edits).length) throw new Error("没有可修改的参数");
  return edits;
}

export function NextYearTaskNavigation({ task, onChange, fiscalYear = 2028 }: { task: Task; onChange: (task: Task) => void; fiscalYear?: number }) {
  return <BudgetPageNavigation labels={nextYearTasks} current={task} onChange={next => onChange(next as Task)} label={`${fy(fiscalYear)} 预算页面`}/>;
}

export function ChangeDiffPanel({ summary, onJump, onUndo, onUndoAll, busy, canEdit, hasChanges, embedded = false }: {
  summary: NextYearEditSummary;
  onJump: (item: NextYearEditItem) => void;
  onUndo: (item: NextYearEditItem) => void;
  onUndoAll: () => void;
  busy: boolean;
  canEdit: boolean;
  hasChanges: boolean;
  embedded?: boolean;
}) {
  const [openCategories, setOpenCategories] = useState<Record<string, boolean>>({});
  const [expanded, setExpanded] = useState(false);
  const toggle = (category: string) => setOpenCategories(previous => ({ ...previous, [category]: !previous[category] }));
  const invalidCount = summary.groups.flatMap(group => group.items).filter(item => item.invalid).length;
  const Shell = embedded ? "div" : "aside";
  return <Shell className={embedded ? "ny-diff-embedded" : `ny-diff-panel${expanded ? " is-open" : ""}`} aria-label="未保存改动清单">
    {!embedded && <button className="ny-panel-toggle" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>未保存改动 · {summary.totalCount} 项<span>{expanded ? "收起" : "展开"}</span></button>}
    <div className="ny-diff-body">
    <div className="ny-diff-head">
      <div><strong>未保存改动</strong><span>{summary.totalCount} 项{invalidCount > 0 && <em> · {invalidCount} 项需修正</em>}</span></div>
      {hasChanges && canEdit && <button className="ny-diff-undo-all" disabled={busy} onClick={onUndoAll}>全部放弃</button>}
    </div>
    {!hasChanges ? <p className="ny-diff-empty">当前没有未保存的改动</p> : <ul className="ny-diff-groups">
      {summary.groups.map(group => {
        const open = openCategories[group.category] ?? false;
        const groupInvalid = group.items.some(item => item.invalid);
        return <li key={group.category} className={groupInvalid ? "is-invalid" : ""}>
          <button className="ny-diff-group-head" aria-expanded={open} onClick={() => toggle(group.category)}>
            <span className="ny-diff-dot" aria-hidden="true"/>
            <span className="ny-diff-group-label">{group.label}</span>
            <span className="ny-diff-group-count">{group.items.length} 条</span>
            {group.scale && <small>{group.scale}</small>}
          </button>
          {open && <ul className="ny-diff-items">{group.items.map(item => <li key={item.key} className={item.invalid ? "is-invalid" : ""}>
            <button className="ny-diff-item-main" title="点击跳到对应输入" disabled={busy} onClick={() => { onJump(item); setExpanded(false); }}>
              <span className="ny-diff-item-label">{item.label}</span>
              <span className="ny-diff-item-change">{item.beforeDisplay ?? "—"} <em>→</em> {item.afterDisplay}</span>
              {item.invalid && <small role="alert">需修正</small>}
            </button>
            {canEdit && <button className="ny-diff-item-undo" title="撤销此项" aria-label={`撤销 ${item.label}`} disabled={busy} onClick={() => onUndo(item)}>×</button>}
          </li>)}</ul>}
        </li>;
      })}
    </ul>}
    {hasChanges && invalidCount === 0 && <p className="ny-diff-hint">全年影响请使用底部“查看影响”。</p>}
    </div>
  </Shell>;
}

type SideTab = "changes" | "tools";
export function NextYearSidePanel({ title, expanded, onClose, children }: {
  title: string; expanded: boolean; onClose: () => void; children: ReactNode;
}) {
  const titleRef = useRef<HTMLHeadingElement>(null);
  useEffect(() => { if (expanded) titleRef.current?.focus({ preventScroll: true }); }, [expanded, title]);
  if (!expanded) return null;
  return <aside className="ny-context-panel is-open" id="ny-context-panel" aria-labelledby="ny-context-title" onKeyDown={event => { if (event.key === "Escape") { event.stopPropagation(); onClose(); } }}>
    <div className="ny-context-heading"><h2 id="ny-context-title" ref={titleRef} tabIndex={-1}>{title}</h2><button aria-label={`关闭${title}`} onClick={onClose}>关闭</button></div>
    <div className="ny-context-content">{children}</div>
  </aside>;
}

function cellText(node: ReactNode): string {
  if (node === null || node === undefined || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(cellText).join(" ");
  if (!isValidElement(node)) return "";
  const props = node.props as { children?: ReactNode; value?: unknown; checked?: boolean; type?: string };
  if (node.type === "input") return props.type === "checkbox" ? props.checked ? "是" : "否" : String(props.value ?? "");
  if (node.type === "label") { const input = Children.toArray(props.children).find(child => isValidElement(child) && child.type === "input"); if (input) return cellText(input); }
  return cellText(props.children);
}
export function nextYearTableData<T>(rows: T[], headers: string[], render: (row: T, index: number) => ReactNode) {
  return rows.map((row, index) => {
    const node = render(row, index), cells = isValidElement(node) ? Children.toArray((node.props as { children: ReactNode }).children) : Children.toArray(node);
    return Object.fromEntries(headers.map((_header, column) => { const value = cellText(cells[column]).trim(), numeric = value.replaceAll(",", ""); return [`c${column}`, /^[+-]?\d+(?:\.\d+)?$/.test(numeric) ? Number(numeric) : value === "—" ? null : value]; }));
  });
}

function PagedTable<T>({ rows, headers, render, label, invalidPath, editPath, exportColumns, exportRow, section }: { rows: T[]; headers: string[]; render: (row: T, index: number) => ReactNode; label: string; invalidPath?: string; editPath?: (row: T, index: number) => (string | number)[]; exportColumns?: ExportColumn[]; exportRow?: (row: T) => Record<string, unknown>;
  section?: { id: string; title: string; actions?: ReactNode } }) {
  const [page, setPage] = useState(0), [query, setQuery] = useState("");
  const invalidIndex = invalidPath && editPath ? rows.findIndex((row, index) => { try { const path = JSON.parse(invalidPath); return editPath(row, index).every((value, n) => path[n] === value); } catch { return false; } }) : -1;
  useEffect(() => { if (invalidIndex >= 0) { setQuery(""); setPage(Math.floor(invalidIndex / 25)); } }, [invalidPath, invalidIndex]);
  const filtered = rows.map((row, index) => ({ row, index })).filter(({ row }) => !query || JSON.stringify(row).includes(query));
  const safePage = Math.min(page, Math.max(0, Math.ceil(filtered.length / 25) - 1));
  const columns = exportColumns ?? headers.map((header, index) => ({ key: `c${index}`, label: header }));
  const values = exportRow ? filtered.map(({ row }) => exportRow(row)) : nextYearTableData(filtered.map(item => item.row), headers, (row, index) => render(row, filtered[index].index));
  const search = <input aria-label={`筛选${label}`} value={query} placeholder="筛选明细" onChange={event => { setQuery(event.target.value); setPage(0); }}/>;
  const table = <ActionTable keyboardNavigation tableLabel={label} aria-label={section ? `${label}明细表` : undefined}
    toolbarStart={section ? <label><span>筛选 SKU</span>{search}</label> : undefined} scrollClassName={section ? "budget-data-section__scroll" : undefined}
    copyRows={[columns.map(column => column.label), ...values.map(row => columns.map(column => String(row[column.key] ?? "")))]} exportData={{ columns, rows: values, filename: label }}>
    <thead><tr>{headers.map((header, index) => <th key={`${header}-${index}`}>{header}</th>)}</tr></thead><tbody>{filtered.slice(safePage * 25, safePage * 25 + 25).map(({ row, index }) => <tr key={index}>{render(row, index)}</tr>)}</tbody>
  </ActionTable>;
  const content = <>{!section && <div className="ny-table-tools"><span>{filtered.length}条</span>{search}</div>}
    {section ? table : <div className="ny-table-wrap" aria-label={label}>{table}</div>}
    {!filtered.length && <p className="ny-empty">当前范围没有明细</p>}<div className={section ? "budget-data-section__pagination" : "ny-pagination"}><button disabled={safePage === 0} onClick={() => setPage(safePage - 1)}>上一页</button><span>{filtered.length ? safePage + 1 : 0} / {Math.ceil(filtered.length / 25)}</span><button disabled={(safePage + 1) * 25 >= filtered.length} onClick={() => setPage(safePage + 1)}>下一页</button></div>
  </>;
  return section ? <BudgetDataSection {...section} count={filtered.length}>{content}</BudgetDataSection> : <div className="ny-data">{content}</div>;
}

const suggestionGroups = [["ENTRY_REALLOCATION", "入口重新分配"], ["NEW_STUDENT_TARGET", "新生招生目标"], ["RENEWAL_ADJUSTMENT", "续班率与新生占比"], ["RATE_BOUNDARY", "参数边界建议"], ["TRACK_TARGET", "赛道目标分配"]] as const;
export function NextYearActionSelection({ actions, originalInputs, proposedInputs, busy, ready, onApply }: {
  actions: NextYearSelectableAction[]; originalInputs?: NextYearInputs; proposedInputs: NextYearInputs | null;
  busy: boolean; ready: boolean; onApply: (selected: Set<string>) => void;
}) {
  const keyed = useMemo(() => actions.map((action, index) => ({ action, key: nextYearActionKey(action, index) })), [actions]);
  const [selected, setSelected] = useState(() => new Set(keyed.map(item => item.key)));
  useEffect(() => setSelected(new Set(keyed.map(item => item.key))), [keyed]);
  const toggle = (keys: string[], checked: boolean) => setSelected(previous => { const next = new Set(previous); keys.forEach(key => checked ? next.add(key) : next.delete(key)); return next; });
  let conflict = "";
  if (originalInputs && proposedInputs && selected.size) { try { applyNextYearClassRepairSelection(originalInputs, proposedInputs, actions, selected); } catch (failure) { conflict = failure instanceof Error ? failure.message : "勾选项存在冲突"; } }
  return <div className="ny-action-selection">
    <div className="ny-selection-tools"><strong>已选 {selected.size} / {actions.length} 项</strong><button disabled={busy} onClick={() => setSelected(new Set(keyed.map(item => item.key)))}>全选</button><button disabled={busy} onClick={() => setSelected(new Set())}>全不选</button></div>
    <div className="ny-repair-changes">{suggestionGroups.map(([kind, title]) => {
      const members = keyed.filter(item => item.action.kind === kind); if (!members.length) return null;
      const count = members.filter(item => selected.has(item.key)).length;
      return <section className="ny-suggestion-group" key={kind}>
        <label className="ny-selection-group"><input type="checkbox" aria-label={`选择全部${title}`} aria-checked={count === 0 ? false : count === members.length ? true : "mixed"} checked={count === members.length} ref={node => { if (node) node.indeterminate = count > 0 && count < members.length; }} disabled={busy} onChange={event => toggle(members.map(item => item.key), event.target.checked)}/><strong>{title}</strong><span>{count} / {members.length} 项</span></label>
        <details><summary>查看建议明细</summary><PagedTable rows={members} label={`建议${title}明细`} exportColumns={[{ key: "selected", label: "已勾选" }, ...repairActionColumns]} exportRow={({ action, key }) => ({ ...action, reason: skuText(action.reason), selected: selected.has(key) ? "是" : "否", field_label: kind === "TRACK_TARGET" ? "赛道目标" : repairFieldLabel(action.field), unit: kind === "TRACK_TARGET" ? "元" : action.field.endsWith("_ppm") ? "%" : "人次", before_value: action.field.endsWith("_ppm") && forecastNumber(action.before) !== null ? Number(action.before) / 10000 : forecastNumber(action.before), after_value: action.field.endsWith("_ppm") && forecastNumber(action.after) !== null ? Number(action.after) / 10000 : forecastNumber(action.after) })} headers={["选择", "校区 / 业务", "季度", "调整项", "原值", "建议值", "调平预留人次", "原因"]} render={({ action, key }) => <>
          <td><input type="checkbox" aria-label={`选择${textDimensions(action)}${action.season ?? ""}${repairFieldLabel(action.field)}`} checked={selected.has(key)} disabled={busy} onChange={event => toggle([key], event.target.checked)}/></td><th>{textDimensions(action)}</th><td>{action.season ?? (kind === "TRACK_TARGET" ? "全年" : "暑假")}</td><td>{kind === "TRACK_TARGET" ? "赛道目标" : repairFieldLabel(action.field)}</td><td>{repairValue(action.before, action.field)}</td><td>{repairValue(action.after, action.field)}</td><td>{format(action.stability_reserve_students ?? null)}</td><td><details><summary>查看原因</summary><p>{skuText(action.reason)}</p></details></td>
        </>}/></details>
      </section>;
    })}</div>
    {conflict && <p role="alert" className="ny-selection-conflict">{conflict}</p>}
    <div className="ny-proposal-footer"><span>{selected.size ? "只应用勾选项；不合规时保留原输入。" : "请先勾选建议"}</span>{ready && <button className="decision-primary" disabled={busy || !selected.size || Boolean(conflict)} onClick={() => onApply(selected)}>{busy ? "正在校验…" : selected.size === actions.length ? "应用并预览" : `应用勾选的 ${selected.size} 项并预览`}</button>}</div>
  </div>;
}

export function NextYearTopMetrics({ calculation, status, previous, onSetTarget, compact = false }: { calculation: NextYearCalculation; status: string; previous?: NextYearEnvelope["draft"]; onSetTarget?: () => void; compact?: boolean }) {
  const annual = calculation.annual, gap = forecastNumber(annual.gap_yuan), hasTarget = forecastNumber(annual.target_revenue_yuan) !== null;
  const delta = (field: keyof typeof annual, monetary = true) => {
    const before = forecastNumber(previous?.calculation.annual[field]), after = forecastNumber(annual[field]);
    if (!previous || before === null || after === null) return "";
    const value = after - before;
    return `相对 N${previous.sequence} ${value >= 0 ? "+" : ""}${monetary ? money(value) : format(value)}${monetary ? " 万元" : " 人次"}`;
  };
  const previousGap = forecastNumber(previous?.calculation.annual.gap_yuan);
  const gapChange = previous && previousGap !== null && gap !== null ? previousGap >= 0 && gap >= 0 ? `相对 N${previous.sequence}，缺口${gap > previousGap ? "增加" : "减少"} ${money(Math.abs(gap - previousGap))} 万元` : previousGap < 0 && gap < 0 ? `相对 N${previous.sequence}，超额${gap < previousGap ? "增加" : "减少"} ${money(Math.abs(gap - previousGap))} 万元` : `N${previous.sequence} ${previousGap > 0 ? "缺口" : "超额"} ${money(Math.abs(previousGap))} → ${gap > 0 ? "缺口" : "超额"} ${money(Math.abs(gap))} 万元` : "";
  const metrics = [
    { label: "全年结转收入（万元）", value: money(annual.revenue_yuan), change: delta("revenue_yuan") },
    { label: "经营目标（万元）", value: hasTarget ? money(annual.target_revenue_yuan) : "待设置", change: delta("target_revenue_yuan") },
    { label: gap === null ? "目标差额（万元）" : gap > 0 ? "缺口（万元）" : gap < 0 ? "超额（万元）" : "差额（万元）", value: hasTarget && gap !== null ? money(Math.abs(gap)) : "—", change: hasTarget ? gapChange : "" },
    { label: "全年总人次", value: format(annual.total_students), change: delta("total_students", false) },
  ];
  return <section className={`ny-top-results${status === "未保存预览" ? " is-preview" : ""}${compact ? " ny-summary-compact" : ""}`} aria-label="全年预算总览">
    <div className="ny-top-status"><span className="ny-scope-label">全年 · 全部业务</span><span>{status}</span>{!calculation.complete && <span>计算未完整</span>}</div>
    <div className="ny-metrics ny-metrics--top">{metrics.map((metric, index) => <div key={metric.label} title={metric.change || undefined}><span>{metric.label}</span><strong>{metric.value}</strong>{metric.change && <small>{metric.change}</small>}{index === 1 && !hasTarget && onSetTarget && <button onClick={onSetTarget}>设置目标</button>}</div>)}</div>
  </section>;
}

export function NextYearOverview({ calculation, filters, onSetTarget, summaryOnly = false }: { calculation: NextYearCalculation; filters: Filters; onSetTarget?: () => void; summaryOnly?: boolean }) {
  const [dimension, setDimension] = useState<"track" | "campus">("track");
  const rows = calculation.rows.filter(row => matches(row, filters)), narrowed = Object.values(filters).some(Boolean);
  const revenue = narrowed ? nextYearRowTotal(rows, "revenue_yuan") : calculation.annual.revenue_yuan;
  const total = narrowed ? nextYearRowTotal(rows, "total_students") : calculation.annual.total_students;
  const old = narrowed ? nextYearRowTotal(rows, "old_students") : calculation.annual.old_students;
  const fresh = narrowed ? nextYearRowTotal(rows, "new_students") : calculation.annual.new_students;
  const goals = dimension === "track" ? calculation.tracks : calculation.campuses;
  const names = [...new Set(rows.map(row => row[dimension]))].sort((a, b) => a.localeCompare(b, "zh-CN"));
  const narrowedBeyondGroup = (Object.entries(filters) as [Dimension, string][]).some(([key, value]) => value && key !== dimension);
  const hasTarget = forecastNumber(calculation.annual.target_revenue_yuan) !== null;
  const showComparison = !narrowedBeyondGroup && names.some(name => forecastNumber(goals.find(item => (item as unknown as Record<string, unknown>)[dimension] === name)?.target_revenue_yuan) !== null);
  return <>
    <section className="ny-metrics" aria-label={narrowed ? "当前范围预算" : "FY28 全年预算"}>
      <div className="ny-metric-primary"><span>{narrowed ? "当前范围结转收入（万元）" : "全年预算结转收入（万元）"}</span><strong>{money(revenue)}</strong></div>
      <div><span>总人次</span><strong>{format(total)}</strong></div><div><span>老生人次</span><strong>{format(old)}</strong></div><div><span>新生人次</span><strong>{format(fresh)}</strong></div>
    </section>
    {!narrowed && <div className="ny-target-summary">{hasTarget ? <><span>全年经营目标 <strong>{money(calculation.annual.target_revenue_yuan)}万元</strong></span>{forecastNumber(calculation.annual.gap_yuan) !== null && <span>目标差额 <strong>{money(calculation.annual.gap_yuan)}万元</strong></span>}</> : <><span>经营目标待填写</span>{onSetTarget && <button onClick={onSetTarget}>设置经营目标</button>}</>}</div>}
    {!summaryOnly && <><section className="ny-section"><h2>季度预算</h2><div className="ny-quarter-grid">{nextYearSeasons.map(season => {
      const selected = rows.filter(row => row.season === season), quarter = calculation.quarters.find(item => item.season === season);
      const value = narrowed ? nextYearRowTotal(selected, "revenue_yuan") : quarter?.revenue_yuan;
      const people = narrowed ? nextYearRowTotal(selected, "total_students") : quarter?.total_students;
      return <div key={season}><h3>{season}</h3><span>结转收入（万元）</span><strong>{money(value)}</strong><dl><div><dt>人次</dt><dd>{format(people)}</dd></div></dl></div>;
    })}</div></section>
    <section className="ny-section"><div className="ny-section-heading"><h2>维度预算</h2><div className="ny-segment" aria-label="汇总维度">{(["track", "campus"] as const).map(key => <button key={key} aria-pressed={dimension === key} onClick={() => setDimension(key)}>{dimensionLabels[key]}</button>)}</div></div>
      <div className="ny-table-wrap" tabIndex={0} aria-label={`${dimensionLabels[dimension]}预算对比`}><ActionTable tableLabel={`${dimensionLabels[dimension]}预算对比`}><thead><tr><th>{dimensionLabels[dimension]}</th><th>预算结转收入（万元）</th>{showComparison && <><th>目标（万元）</th><th>差额（万元）</th></>}<th>总人次</th></tr></thead><tbody>{names.map(name => {
        const groupRows = rows.filter(row => row[dimension] === name), income = nextYearRowTotal(groupRows, "revenue_yuan");
        const goal = goals.find(item => (item as unknown as Record<string, unknown>)[dimension] === name);
        const targetValue = narrowedBeyondGroup ? null : goal?.target_revenue_yuan;
        const parsedTarget = forecastNumber(targetValue), gap = income === null || parsedTarget === null ? null : parsedTarget - income;
        return <tr key={name}><th>{name}</th><td>{money(income)}</td>{showComparison && <><td>{money(targetValue)}</td><td>{money(gap)}</td></>}<td>{format(nextYearRowTotal(groupRows, "total_students"))}</td></tr>;
      })}</tbody></ActionTable></div>{!rows.length && <p className="ny-empty">当前范围没有预算结果</p>}
    </section>
    <section className="ny-section"><h2>预算明细</h2><PagedTable rows={rows} label="FY28 预算明细" headers={["校区 / 业务", "季度", "老生", "新生", "总人次", "结转收入（元）", "开班"]} render={row => <><th>{textDimensions(row)}</th><td>{row.season}</td><td>{format(row.old_students)}</td><td>{format(row.new_students)}</td><td>{format(row.total_students)}</td><td>{format(row.revenue_yuan, 2)}</td><td>{nextYearClassStatus(row.class_size_status)}</td></>}/></section></>}
  </>;
}

export function NextYearBudgetPage() {
  const [identity, setIdentity] = useState<ApiIdentity | null>(null), [data, setData] = useState<NextYearEnvelope | null>(null);
  const [inputs, setInputs] = useState<NextYearInputs | null>(null), [edits, setEdits] = useState<NextYearEdits>({});
  const [busy, setBusy] = useState("load"), [error, setError] = useState(""), [notice, setNotice] = useState("");
  const [preview, setPreview] = useState<NextYearCalculation | null>(null), [reason, setReason] = useState("");
  const [task, setTask] = useState<Task>(readTask), [filters, setFilters] = useState<Filters>(() => readNextYearFilters(readYear(), location.search));
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({}), [pendingNavigation, setPendingNavigation] = useState("");
  const [exporting, setExporting] = useState(false), leaveDialog = useRef<HTMLDialogElement>(null), inFlight = useRef(false), heading = useRef<HTMLHeadingElement>(null);
  const [catalog, setCatalog] = useState<NextYearCatalog | null>(null), [fiscalYear, setFiscalYear] = useState(readYear);
  const [historical, setHistorical] = useState<NextYearEnvelope | null>(null), [versionsOpen, setVersionsOpen] = useState(false);
  const [bulkField, setBulkField] = useState("renewal_ppm"), [bulkValue, setBulkValue] = useState(""), [paste, setPaste] = useState("");
  const [manualOpen, setManualOpen] = useState(false), [balanceMessage, setBalanceMessage] = useState("");
  const [newBusiness, setNewBusiness] = useState({ ...emptyNewBusiness });
  const [selectedSourceId, setSelectedSourceId] = useState("");
  const [balanceIssues, setBalanceIssues] = useState<NonNullable<NonNullable<Awaited<ReturnType<typeof balanceNextYearBudget>>["constraints"]>["class_diagnostics"]>>([]);
  const [classReadiness, setClassReadiness] = useState<{ key: string; report: NextYearClassReadiness } | null>(null);
  const [classCheckOpen, setClassCheckOpen] = useState(false), [classCheckScope, setClassCheckScope] = useState<ClassCheckScope>({ ...emptyClassCheckScope });
  const [classRepair, setClassRepair] = useState<{ key: string; result: NextYearClassRepairResult } | null>(null);
  const [focusPath, setFocusPath] = useState<string | undefined>();
  const [balanceProposal, setBalanceProposal] = useState<{ key: string; result: Awaited<ReturnType<typeof balanceNextYearBudget>>; original: NextYearInputs; before: NextYearCalculation; actions: NextYearSelectableAction[] } | null>(null);
  const [selectionReview, setSelectionReview] = useState<NextYearCalculation | null>(null);
  const [sideTab, setSideTab] = useState<SideTab>("changes"), [sideExpanded, setSideExpanded] = useState(false);
  const [toolMode, setToolMode] = useState<"bulk" | "paste" | "bounds" | "plans" | "business">("bulk");
  const [compareOpen, setCompareOpen] = useState(false);
  const [comparison, setComparison] = useState<{ key: string; envelope?: NextYearEnvelope; error?: string } | null>(null);
  const [basisPreview, setBasisPreview] = useState<{ key: string; result: NextYearBasisPreview } | null>(null);
  const [rateLayout, setRateLayout] = useState<QuarterLayout>("quarters");
  const [financeLayout, setFinanceLayout] = useState<QuarterLayout>("quarters");
  const comparisonCache = useRef(new Map<string, NextYearEnvelope>());
  const filterYear = useRef(readYear());
  const loadGeneration = useRef(0);
  const navigationGeneration = useRef(0);
  const contextLauncher = useRef<HTMLElement | null>(null);
  const shellRef = useRef<HTMLDivElement>(null), actionBarRef = useRef<HTMLElement>(null);
  const draft = data?.draft, source = historical?.draft?.source ?? draft?.source ?? data?.source;
  const visibleInputs = historical?.draft?.inputs ?? inputs;
  const editSummary = useMemo<NextYearEditSummary>(() => draft && visibleInputs && !historical ? summarizeNextYearChanges(draft.inputs, visibleInputs, edits, fieldErrors) : { groups: [], totalCount: 0 }, [draft, visibleInputs, edits, fieldErrors, historical]);
  const inputChanged = editSummary.totalCount > 0;
  const pendingBusiness = Object.entries(newBusiness).some(([key, value]) => value !== emptyNewBusiness[key as keyof typeof emptyNewBusiness]);
  const pendingTools = !historical && (pendingBusiness || Boolean(paste.trim()));
  const unsaved = inputChanged || Boolean(reason.trim()) || pendingTools, canEdit = !historical && Boolean(identity?.roles.includes("DATA_MAINTAINER") || identity?.roles.includes("DEPARTMENT_BUDGET_OWNER"));
  const calculation = historical?.draft?.calculation ?? preview ?? draft?.calculation;
  const shownDraft = historical?.draft ?? draft;
  const comparisonKey = `${identity?.userId}:${identity?.departmentId}:${fiscalYear}:${shownDraft?.parent_id ?? ""}`;
  const previousVersion = comparison?.key === comparisonKey ? comparison.envelope?.draft : undefined;
  const resultStatus = historical ? `历史 N${historical.draft?.sequence} · 只读` : preview ? "未保存预览" : inputChanged ? `待重算 · 原结果 N${draft?.sequence}` : `已保存 N${draft?.sequence}`;
  const classCheckKey = JSON.stringify({ version: historical?.draft?.id ?? draft?.id, inputs: visibleInputs, edits: historical ? {} : edits });
  const currentClassReadiness = classReadiness?.key === classCheckKey ? classReadiness.report : null;
  const currentClassRepair = !historical && classRepair?.key === classCheckKey ? classRepair.result : null;
  const currentBalanceProposal = !historical && balanceProposal?.key === classCheckKey ? balanceProposal : null;
  const classRows = currentClassReadiness?.rows ?? (calculation && visibleInputs ? nextYearClassIssueRows(calculation, visibleInputs) : []);
  useEffect(() => {
    const shell = shellRef.current, bar = actionBarRef.current;
    if (!shell || !bar) return;
    const measure = () => shell.style.setProperty("--ny-action-bar-height", `${Math.ceil(bar.getBoundingClientRect().height)}px`);
    measure();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    observer?.observe(bar); window.addEventListener("resize", measure);
    return () => { observer?.disconnect(); window.removeEventListener("resize", measure); shell.style.removeProperty("--ny-action-bar-height"); };
  }, [Boolean(draft), Boolean(visibleInputs), Boolean(calculation)]);
  function accept(next: NextYearEnvelope) { setData(next); setInputs(next.draft ? structuredClone(next.draft.inputs) : null); setEdits({}); setPreview(null); setReason(""); setFieldErrors({}); setHistorical(null); setBalanceMessage(""); setBalanceIssues([]); setClassReadiness(null); setClassRepair(null); setClassCheckOpen(false); setClassCheckScope({ ...emptyClassCheckScope }); setSelectedSourceId(next.draft?.source.id ?? next.source?.id ?? ""); setSideExpanded(false); setBasisPreview(null); setVersionsOpen(false); }
  async function load(signal?: AbortSignal, year = fiscalYear) {
    const generation = ++loadGeneration.current;
    setBusy("load"); setError("");
    try { const who = await parseResponse<ApiIdentity>(await fetch("/api/v1/local-trial/session", { signal })); const next = await getNextYearBudget(who, signal, year);
      if (signal?.aborted || generation !== loadGeneration.current) return false;
      setIdentity(who); accept(next); setFiscalYear(year);
      try { const available = await getNextYearCatalog(who, signal); if (!signal?.aborted && generation === loadGeneration.current) setCatalog(available); } catch { /* Budget reading remains available if the catalog cannot load. */ }
      return !signal?.aborted && generation === loadGeneration.current;
    } catch (failure) { if (!signal?.aborted && generation === loadGeneration.current) setError(failure instanceof Error ? failure.message : "读取失败"); return false; }
    finally { if (!signal?.aborted && generation === loadGeneration.current) setBusy(""); }
  }
  useEffect(() => { const controller = new AbortController(); void load(controller.signal); return () => controller.abort(); }, []);
  useEffect(() => { const restore = async () => {
    const navigation = ++navigationGeneration.current, search = location.search;
    const year = readYear(), nextTask = readTask();
    if (year === fiscalYear) { if (data) { ++loadGeneration.current; setBusy(value => value === "load" ? "" : value); } setTask(nextTask); setSideExpanded(false); setFilters(readNextYearFilters(year, search)); return; }
    const rollback = () => { const url = new URL(location.href); url.search = writeNextYearFilters(url.search, filters); url.searchParams.set("year", String(fiscalYear)); url.searchParams.set("nyTask", String(task)); history.replaceState({}, "", url); };
    if (unsaved) { rollback(); setError("先保存或放弃修改，再切换财年"); return; }
    const loaded = await load(undefined, year);
    if (navigation !== navigationGeneration.current) return;
    if (loaded) { filterYear.current = year; setTask(nextTask); setFilters(readNextYearFilters(year, search)); } else rollback();
  }; window.addEventListener("popstate", restore); return () => window.removeEventListener("popstate", restore); }, [fiscalYear, unsaved, task, filters, data]);
  useEffect(() => { if (!unsaved) return; const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; }; window.addEventListener("beforeunload", warn); return () => window.removeEventListener("beforeunload", warn); }, [unsaved]);
  useEffect(() => { const dialog = leaveDialog.current; if (pendingNavigation && dialog && !dialog.open) dialog.showModal(); if (!pendingNavigation && dialog?.open) dialog.close(); }, [pendingNavigation]);
  useEffect(() => { const restore = () => setPendingNavigation(""); window.addEventListener("pageshow", restore); return () => window.removeEventListener("pageshow", restore); }, []);
  const preparePageNavigation = useBudgetPagePosition(task, Boolean(data?.draft) && busy !== "load", heading);
  function changeTask(next: Task) { if (next === task) return; preparePageNavigation(); setTask(next); setSideExpanded(false); const url = new URL(location.href); url.searchParams.set("nyTask", String(next)); history.pushState({}, "", url); }
  function beforeLeave(event: MouseEvent<HTMLAnchorElement>) { if (!unsaved || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.currentTarget.target === "_blank") return; event.preventDefault(); if (!busy) setPendingNavigation(event.currentTarget.href); }
  function edit(path: (string | number)[], value: string) { const key = nextYearEditKey(...path); setEdits(previous => ({ ...previous, [key]: value, ...(path[0] === "target" && !value.trim() ? Object.fromEntries(Object.keys(inputs?.track_targets ?? {}).map(track => [nextYearEditKey("track-target", track), ""])) : {}) })); setFieldErrors(previous => { const next = { ...previous }; delete next[key]; return next; }); setPreview(null); setNotice(""); setBalanceIssues([]); }
  const invalidPath = (section: string) => {
    for (const key of [...Object.keys(fieldErrors), ...(focusPath ? [focusPath] : [])]) { try { const path = JSON.parse(key); const owner = path[0] === "class-size" ? "class" : path[0] === "rate-lock" ? "rates" : path[0]; if (owner === section) return owner === path[0] ? key : nextYearEditKey(owner, path[1]); } catch { /* invalid raw keys stay in the change list */ } }
    return undefined;
  };
  function field(path: (string | number)[], original: string | number | null, label: string, percent = false, placeholder?: string, visibleLabel?: string) {
    const key = nextYearEditKey(...path), message = fieldErrors[key];
    const errorId = `ny-error-${encodeURIComponent(key)}`;
    return <label className={`ny-field${path[0] === "tax" ? " ny-global-input" : ""}`}><span>{visibleLabel ?? label}{path[0] === "tax" && <small>全部业务</small>}</span><input aria-label={label} data-ny-edit-key={key} type="text" inputMode="decimal" value={edits[key] ?? (original === null ? "" : String(percent ? Number(original) / 10000 : original))} placeholder={placeholder} disabled={Boolean(busy) || !canEdit} aria-invalid={Boolean(message)} aria-describedby={message ? errorId : undefined} onChange={event => edit(path, event.target.value)}/>{message && <small id={errorId} role="alert">{message}</small>}</label>;
  }
  async function run(mode: "prepare" | "preview" | "save", forecastId = selectedSourceId || data?.source?.id) {
    if (!identity || !canEdit || inFlight.current) return;
    if (pendingTools) { changeTask(pendingBusiness ? 1 : 3); openTools(pendingBusiness ? "business" : "paste"); setError(pendingBusiness ? "还有新业务未加入预算，请先加入或清空表单" : "粘贴参数尚未暂存，请先校验暂存或清空"); return; }
    if (mode === "prepare" && (!forecastId || unsaved)) return;
    if (mode === "prepare" && draft && (!currentBasisPreview || forecastId !== currentBasisPreview.source_id)) { setError("先预览基准变化，再确认生成新草稿"); return; }
    const prepared = mode === "prepare" ? null : inputs ? applyNextYearEdits(inputs, edits) : null;
    if (mode !== "prepare" && (!draft || !prepared?.inputs)) {
      const errors = prepared?.errors ?? {}; setFieldErrors(errors);
      const first = Object.keys(errors)[0]; let section = ""; try { section = first ? JSON.parse(first)[0] : ""; } catch { /* malformed edit keys stay validation errors */ }
      const nextTask = nextYearEditTask(first ?? "");
      if (nextTask === 3) setRateLayout("details");
      if (nextTask === 4) setFinanceLayout("details");
      setFilters({ ...emptyFilters }); setFocusPath(first); if (section === "manual") setManualOpen(true); changeTask(nextTask); if (section === "bounds" || section === "plan") openTools(section === "bounds" ? "bounds" : "plans"); setError(first ? `${nextYearTasks[nextTask - 1]}：${errors[first]}` : "请补齐输入"); return;
    }
    inFlight.current = true; setBusy(mode); setError(""); setNotice("");
    try {
      if (mode === "prepare") { accept(await prepareNextYearBudget(identity, forecastId!, draft?.id ?? null, fiscalYear)); changeTask(5); setNotice(`${fy(fiscalYear)} 新草稿已生成，原版本已保留`); }
      else if (mode === "preview") { const result = await previewNextYearBudget(identity, draft!.id, prepared!.inputs!); setPreview(result.calculation); setSideExpanded(false); requestAnimationFrame(() => { if (typeof document !== "undefined") { const impact = document.getElementById("ny-impact-preview"); impact?.focus({ preventScroll: true }); impact?.scrollIntoView({ block: "start", behavior: window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" }); } }); }
      else { accept(await reviseNextYearBudget(identity, draft!.id, prepared!.inputs!, reason.trim() || (balanceMessage ? "采用自动调平建议" : "调整预算参数"))); setNotice("已保存为新版本"); }
    } catch (failure) { setError(failure instanceof Error ? failure.message : "操作未完成"); }
    finally { inFlight.current = false; setBusy(""); }
  }
  const combinations = visibleInputs?.combinations ?? [], comboById = useMemo(() => new Map(combinations.map(row => [row.id, row])), [combinations]);
  function locateQuarterRate(row: NextYearRate, metric = "renewal_ppm") {
    if (!canEdit || busy) return;
    setRateLayout("details"); setSideExpanded(false);
    setFilters({ ...filters, season: row.season, track: row.track, campus: row.campus, product: row.product, grade: row.grade });
    setFocusPath(metric === "locked" ? nextYearEditKey("rate-lock", row.id) : nextYearEditKey("rates", row.id, metric));
  }
  function locateQuarterFinance(row: NextYearFinance, metric = "price_yuan") {
    if (!canEdit || busy) return;
    const combo = comboById.get(row.combination_id);
    if (!combo) return;
    setFinanceLayout("details"); setSideExpanded(false);
    setFilters({ season: row.season, track: combo.track, campus: combo.campus, product: combo.product, grade: combo.grade, subject: combo.subject });
    setFocusPath(nextYearEditKey("finance", row.id, metric));
  }
  const planRows = useMemo(() => combinations.filter(row => row.enabled).flatMap(combination => nextYearSeasons.filter(season => {
    if (season === "暑假" && effectiveNextYearEntry(combination)) return false;
    const result = calculation?.rows.find(row => row.combination_id === combination.id && row.season === season);
    return result?.planned_new_applied || (!result && combination.added);
  }).map(season => ({ combination, season, students: visibleInputs?.plans.find(row => row.combination_id === combination.id && row.season === season)?.students }))), [combinations, visibleInputs?.plans, calculation]);
  function distributeTargets() {
    if (!inputs || !draft || !canEdit || busy) return;
    try { const allocated = allocateNextYearTrackTargets(edits[nextYearEditKey("target")] ?? inputs.target_revenue_yuan ?? "", Object.fromEntries(draft.calculation.tracks.map(row => [row.track, String(Math.max(0, forecastNumber(row.revenue_yuan) ?? 0))])), 2);
      setEdits(previous => ({ ...previous, ...Object.fromEntries(Object.entries(allocated).map(([name, value]) => [nextYearEditKey("track-target", name), value])) }));
      setFieldErrors({}); setError(""); setPreview(null); setNotice("");
    } catch (failure) { setError(failure instanceof Error ? failure.message : "目标分配未完成"); }
  }
  async function download() {
    if (!calculation || exporting) return; setExporting(true); setError("");
    try { const rows = calculation.rows.filter(row => matches(row, filters)); await downloadBudgetWorkbook(nextYearExportColumns, nextYearExportRows(rows), preview ? `${fy(fiscalYear)}-未保存预览明细` : `${fy(fiscalYear)}-N${historical?.draft?.sequence ?? draft?.sequence}-预算明细`); }
    catch (failure) { setError(failure instanceof Error ? failure.message : "导出失败"); } finally { setExporting(false); }
  }
  function stage(next: NextYearInputs, message: string, discard: (path: (string | number)[]) => boolean = () => false) { const remaining = Object.fromEntries(Object.entries(edits).filter(([key]) => { try { return !discard(JSON.parse(key)); } catch { return true; } })); const prepared = applyNextYearEdits(next, remaining); if (!prepared.inputs) { setFieldErrors(prepared.errors); setError(Object.values(prepared.errors)[0]); return; } setInputs(prepared.inputs); setEdits({}); setPreview(null); setFieldErrors({}); setError(""); setNotice(message); setBalanceMessage(""); setBalanceIssues([]); }
  function stageEdits(next: NextYearEdits) {
    if (!inputs || !canEdit || busy) return false;
    const merged = { ...edits, ...next }, checked = applyNextYearEdits(inputs, merged);
    if (!checked.inputs) { setFieldErrors(checked.errors); setError(Object.values(checked.errors)[0]); return false; }
    setEdits(merged); setPreview(null); setNotice(`已暂存 ${Object.keys(next).length} 项修改，预览后保存`); setError(""); return true;
  }
  function bulkApply() {
    if (!inputs || !bulkValue.trim()) { setError("先填写批量修改值"); return; }
    const changes = Object.fromEntries(inputs.rates.filter(row => matches(row, filters) && nextYearRateUsage(inputs, row)[bulkField === "renewal_ppm" ? "renewal" : "share"]).map(row => [nextYearEditKey("rates", row.id, bulkField), bulkValue]));
    if (!Object.keys(changes).length) { setError("当前范围没有适用参数"); return; } stageEdits(changes);
  }
  function pasteParameters() { try { if (inputs && stageEdits(parseNextYearParameterPaste(paste, inputs.rates.filter(row => matches(row, filters))))) setPaste(""); } catch (failure) { setError(failure instanceof Error ? failure.message : "粘贴失败"); } }
  async function copyParameters() {
    try { if (!inputs) return;
      const current = applyNextYearEdits(inputs, edits); if (!current.inputs) throw new Error(Object.values(current.errors)[0]);
      const rows = current.inputs.rates.filter(row => matches(row, filters));
      await copyTableText([["参数ID", "续班率", "新生占比"], ...rows.map(row => [row.id, row.renewal_ppm / 10000, row.share_ppm / 10000])].map(row => row.map(String))); setNotice(`已复制 ${rows.length} 行，可在 Excel 编辑后粘贴回来`);
    } catch (failure) { setError(failure instanceof Error ? failure.message : "复制失败"); }
  }
  function addBusiness() {
    if (!inputs || !canEdit || busy) return;
    const parsed = applyNextYearEdits(inputs, edits); if (!parsed.inputs) { setError(Object.values(parsed.errors)[0]); return; }
    const dims = Object.fromEntries((["campus", "track", "product", "grade", "subject"] as const).map(key => [key, newBusiness[key].trim()])) as Pick<NextYearCombination, "campus" | "track" | "product" | "grade" | "subject">;
    const price = forecastNumber(newBusiness.price), size = forecastNumber(newBusiness.classSize);
    if (Object.values(dims).some(value => !value) || !newBusiness.reason.trim()) { setError("填写新业务的五个维度和新增原因"); return; }
    if (price === null || price < 0 || price > 1000000000 || size === null || !Number.isInteger(size) || size < 1 || size > 10000) { setError("填写有效学费和计划班额"); return; }
    if (inputs.combinations.some(row => Object.entries(dims).every(([key, value]) => row[key as keyof typeof dims] === value))) { setError("该业务组合已存在，请直接编辑"); return; }
    const id = `added-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`, next = parsed.inputs;
    next.combinations.push({ id, ...dims, entry: false, entry_override: false, enabled: true, source_entry_weight: 0,
      source_new_by_season: Object.fromEntries(nextYearSeasons.map(season => [season, 0])) as NextYearCombination["source_new_by_season"], opening_carryover_yuan: "0", added: true, reason: newBusiness.reason.trim() });
    for (const [index, season] of nextYearSeasons.entries()) {
      if (!next.rates.some(row => row.season === season && row.track === dims.track && row.product === dims.product && row.campus === dims.campus && row.grade === dims.grade)) {
        const template = next.rates.find(row => row.season === season && row.track === dims.track && row.grade === dims.grade);
        next.rates.push({ id: `${id}-rate-${index}`, season, track: dims.track, product: dims.product, campus: dims.campus, grade: dims.grade, renewal_ppm: template?.renewal_ppm ?? 0, share_ppm: template?.share_ppm ?? 0 });
      }
      next.finance.push({ id: `${id}-finance-${index}`, combination_id: id, season, price_yuan: String(price), discount_ppm: 1000000, recognition_ppm: 1000000 });
      next.plans.push({ combination_id: id, season, students: 0, locked: true, reason: newBusiness.reason.trim() });
    }
    const classSetting = next.class_sizes.find(row => row.track === dims.track && row.product === dims.product);
    if (classSetting) classSetting.planned_class_size = size;
    else next.class_sizes.push({ track: dims.track, product: dims.product, minimum: 1, planned_class_size: size });
    if (!Object.hasOwn(next.track_targets, dims.track)) next.track_targets[dims.track] = Object.values(next.track_targets).some(value => value !== null) ? "0" : null;
    stage(next, `新业务已加入；${dims.product}共用计划班额 ${size} 人。填写入口或计划新生后预览`); setNewBusiness({ ...emptyNewBusiness }); setManualOpen(true);
  }
  async function balance() {
    if (!identity || !inputs || !draft || !canEdit || inFlight.current) return;
    const prepared = applyNextYearEdits(inputs, edits); if (!prepared.inputs) { setFieldErrors(prepared.errors); setError(Object.values(prepared.errors)[0]); return; }
    if (forecastNumber(prepared.inputs.target_revenue_yuan) === null) { changeTask(1); setError("先填写全年经营目标"); return; }
    inFlight.current = true; setBusy("balance"); setError(""); setBalanceIssues([]);
    try {
      const before = inputChanged && !preview ? (await previewNextYearBudget(identity, draft.id, prepared.inputs)).calculation : calculation!;
      const result = await balanceNextYearBudget(identity, draft.id, prepared.inputs);
      if (!result.proposed_inputs) { setError(result.message || "本次尚未找到可应用的调平建议，原预算未修改"); setBalanceIssues(result.constraints?.class_diagnostics ?? []); if (result.constraints?.class_diagnostics?.length) setClassCheckOpen(true); return; }
      const actions = nextYearBalanceActions(prepared.inputs, result.proposed_inputs, before, result.calculation);
      setBalanceProposal({ key: classCheckKey, result, original: prepared.inputs, before, actions }); setSelectionReview(null);
      setNotice("调平建议已准备，请勾选后应用；原输入未修改");
    } catch (failure) { setError(failure instanceof Error ? failure.message : "调平未完成"); }
    finally { setBusy(""); inFlight.current = false; }
  }
  async function checkClasses() {
    setSideExpanded(false);
    setVersionsOpen(false);
    if (historical) { setClassCheckOpen(true); return; }
    if (!identity || !visibleInputs || !draft || inFlight.current) return;
    const prepared = applyNextYearEdits(visibleInputs, edits);
    if (!prepared.inputs) { setFieldErrors(prepared.errors); setError(Object.values(prepared.errors)[0] || "先修正输入，再检查成班"); return; }
    inFlight.current = true; setBusy("class-check"); setError(""); setClassCheckOpen(true); setClassCheckScope({ ...emptyClassCheckScope });
    try { const report = await checkNextYearClassReadiness(identity, draft.id, prepared.inputs); setClassReadiness({ key: classCheckKey, report }); }
    catch (failure) { setError(failure instanceof Error ? failure.message : "成班检查未完成"); }
    finally { inFlight.current = false; setBusy(""); }
  }
  async function generateClassRepair() {
    if (!identity || !inputs || !draft || !canEdit || inFlight.current) return;
    const prepared = applyNextYearEdits(inputs, edits);
    if (!prepared.inputs) { setFieldErrors(prepared.errors); setError(Object.values(prepared.errors)[0] || "先修正输入，再生成成班方案"); return; }
    inFlight.current = true; setBusy("class-repair"); setError(""); setNotice(""); setClassRepair(null);
    try {
      const result = await proposeNextYearClassRepair(identity, draft.id, prepared.inputs);
      setClassRepair({ key: classCheckKey, result }); setClassCheckOpen(true);
      requestAnimationFrame(() => { if (typeof document !== "undefined") { const title = document.getElementById("ny-class-proposal-title"); title?.focus({ preventScroll: true }); title?.scrollIntoView({ block: "start", behavior: window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" }); } });
    } catch (failure) { setError(failure instanceof Error ? failure.message : "成班方案未生成，原预算未修改"); }
    finally { inFlight.current = false; setBusy(""); }
  }
  async function applyClassRepair(keys?: Set<string>) {
    if (!canEdit || busy || !currentClassRepair || !canApplyNextYearClassRepair(currentClassRepair)) return;
    if (!inputs || !identity || !draft || inFlight.current) return;
    const original = applyNextYearEdits(inputs, edits).inputs; if (!original) return;
    const selected = keys ?? new Set(currentClassRepair.actions.map(nextYearActionKey));
    inFlight.current = true; setBusy("selection"); setError("");
    try {
    const merged = applyNextYearClassRepairSelection(original, currentClassRepair.proposed_inputs!, currentClassRepair.actions, selected);
    let checked = currentClassRepair.calculation;
    if (selected.size !== currentClassRepair.actions.length) {
      checked = (await previewNextYearBudget(identity, draft.id, merged)).calculation; setSelectionReview(checked);
      assertNextYearSelectionPreview(original, currentClassRepair.before_calculation, merged, currentClassRepair.actions, selected, checked);
    }
    setInputs(merged); setEdits({}); setPreview(checked);
    setClassRepair(null); setClassReadiness(null); setClassCheckOpen(false); setBalanceIssues([]); setBalanceMessage(""); setFieldErrors({}); setError("");
    if (!reason.trim()) setReason("采用成班方案");
    setNotice("成班方案已应用为未保存预览，保存后生效"); changeTask(5);
    setSelectionReview(null);
    } catch (failure) { setError(failure instanceof Error ? failure.message : "勾选建议未通过校验，原输入未修改"); }
    finally { inFlight.current = false; setBusy(""); }
  }
  async function applyBalanceSelection(selected: Set<string>) {
    if (!currentBalanceProposal || !canEdit || !identity || !draft || inFlight.current) return;
    inFlight.current = true; setBusy("selection"); setError("");
    try {
      const { original, result, actions, before } = currentBalanceProposal;
      const merged = applyNextYearClassRepairSelection(original, result.proposed_inputs!, actions, selected);
      const checked = selected.size === actions.length ? result.calculation : (await previewNextYearBudget(identity, draft.id, merged)).calculation;
      setSelectionReview(checked); assertNextYearSelectionPreview(original, before, merged, actions, selected, checked);
      setInputs(merged); setEdits({}); setPreview(checked); setBalanceProposal(null); setSelectionReview(null);
      setBalanceMessage(result.message || "调平建议已应用"); setNotice("勾选建议已应用为未保存预览，保存后生效"); changeTask(5);
    } catch (failure) { setError(failure instanceof Error ? failure.message : "勾选建议未通过校验，原输入未修改"); }
    finally { inFlight.current = false; setBusy(""); }
  }
  function navigateClassIssue(row: NextYearClassReadinessRow, action: "manual" | "rates" | "range") {
    if (!canEdit || busy) return;
    setFilters({ season: row.season ?? "", track: row.track, campus: row.campus, product: row.product, grade: row.grade, subject: row.subject }); setClassCheckOpen(false);
    if (action === "range") changeTask(1);
    else if (action === "rates") changeTask(3);
    else if (row.season === "暑假" && combinations.some(item => item.id === row.combination_id && effectiveNextYearEntry(item))) changeTask(2);
    else { setManualOpen(true); changeTask(3); }
  }
  async function viewVersion(id: string) {
    if (!identity || busy) return; if (unsaved) { setError("先保存或放弃修改，再查看历史版本"); return; }
    if (id === draft?.id) { setHistorical(null); setVersionsOpen(false); return; }
    setBusy("version"); setError(""); try { setHistorical(await getNextYearVersion(identity, id, fiscalYear)); setVersionsOpen(false); setClassCheckOpen(false); changeTask(5); } catch (failure) { setError(failure instanceof Error ? failure.message : "读取版本失败"); } finally { setBusy(""); }
  }
  async function restoreVersion() {
    if (!identity || !draft || !historical?.draft || busy) return;
    setBusy("restore"); setError(""); try { const sequence = historical.draft.sequence;
      accept(await restoreNextYearVersion(identity, draft.id, historical.draft.id, reason.trim() || `从 N${sequence} 恢复为新草稿`, fiscalYear)); setNotice(`已从 N${sequence} 恢复为新草稿`);
    } catch (failure) { setError(failure instanceof Error ? failure.message : "恢复失败"); } finally { setBusy(""); }
  }
  const manualRows: NextYearRow[] = combinations.filter(row => row.enabled).flatMap(combination => nextYearSeasons.filter(season => !(season === "暑假" && effectiveNextYearEntry(combination))).map(season => calculation?.rows.find(row => row.combination_id === combination.id && row.season === season) ?? {
    ...combination, combination_id: combination.id, season, old_students: null, new_students: null, total_students: null, revenue_yuan: null,
    price_yuan: null, discount_ppm: null, renewal_ppm: null, share_ppm: null, class_size_status: "NO_PLANNED_CLASS", planned_new_applied: true,
  }));
  const blockers = calculation?.issues.filter(issue => issue.severity === "BLOCKING") ?? [], warnings = calculation?.issues.filter(issue => issue.severity === "WARNING") ?? [];
  const sourceOptions = catalog?.sources.filter(item => (item.fiscal_year ?? catalog.source_fiscal_year) === fiscalYear - 1) ?? (data?.source ? [data.source] : []);
  const selectedSource = sourceOptions.find(item => item.id === selectedSourceId) ?? source ?? data?.source;
  const basisPreviewKey = JSON.stringify({ parent: draft?.id ?? null, source: selectedSource?.id, snapshot: selectedSource?.snapshot_hash, calculation: selectedSource?.calculation_hash, year: fiscalYear });
  const currentBasisPreview = basisPreview?.key === basisPreviewKey ? basisPreview.result : null;
  const basisPreviewIssues = currentBasisPreview?.calculation.issues.map(issue => ({ severity: issue.severity === "BLOCKING" ? "需处理" : "提醒", season: issue.season ?? "全年", business: textDimensions(currentBasisPreview.inputs.combinations.find(item => item.id === issue.combination_id) ?? {}), message: issue.message })) ?? [];
  async function previewBasis() {
    if (!identity || !draft || !selectedSource || !canEdit || unsaved || inFlight.current) return;
    inFlight.current = true; setBusy("basis-preview"); setError(""); setBasisPreview(null);
    try {
      const result = await previewNextYearBasis(identity, selectedSource.id, draft.id, fiscalYear);
      if (result.read_only !== true || result.parent_id !== draft.id || result.source_id !== selectedSource.id || result.source.id !== selectedSource.id || result.source.snapshot_hash !== selectedSource.snapshot_hash || result.source.calculation_hash !== selectedSource.calculation_hash || result.source.baseline_hash !== selectedSource.baseline_hash || result.fiscal_year !== fiscalYear || result.inputs.fiscal_year !== fiscalYear || result.calculation.fiscal_year !== fiscalYear) throw new Error("基准预览与所选版本不一致，请重新预览");
      setBasisPreview({ key: basisPreviewKey, result });
    } catch (failure) { setError(failure instanceof Error ? failure.message : "基准预览未完成，原预算未修改"); }
    finally { inFlight.current = false; setBusy(""); }
  }
  function openVersions() { if (busy) return; setSideExpanded(false); setClassCheckOpen(false); setVersionsOpen(true); }
  function openProblems() { if (busy) return; setSideExpanded(false); setVersionsOpen(false); setClassCheckOpen(true); }
  const sourcePicker = canEdit && sourceOptions.length > 0 && <div className="ny-source-picker"><label className="ny-field"><span>基准预测</span><select aria-label="基准预测版本" value={selectedSourceId || selectedSource?.id || ""} disabled={Boolean(busy) || unsaved} onChange={event => { setSelectedSourceId(event.target.value); setBasisPreview(null); }}>{sourceOptions.map(item => <option key={item.id} value={item.id}>{fy(item.fiscal_year ?? fiscalYear - 1)} · P{item.sequence} · {item.as_of} · {money(item.forecast_revenue)} 万元</option>)}</select></label>{draft && <button disabled={Boolean(busy) || unsaved || !selectedSource} onClick={() => void previewBasis()}>{busy === "basis-preview" ? "正在预览基准…" : "预览基准变化"}</button>}</div>;
  const scopeBar = (keys: Dimension[], count?: number, label = "当前SKU", selectLabelPrefix = "筛选") => <BudgetScopeBar filters={filters} dimensions={keys.map(key => ({ key, label: dimensionLabels[key], options: key === "season" ? [...nextYearSeasons] : [...new Set(combinations.map(row => row[key]))].sort((a, b) => dimensionSort(key, a, b)) }))} onChange={setFilters} label={label} count={count} disabled={Boolean(busy)} selectLabelPrefix={selectLabelPrefix}/>;
  const entryState = visibleInputs ? applyNextYearEdits(visibleInputs, edits).inputs ?? visibleInputs : null;
  useEffect(() => {
    if (!focusPath || typeof document === "undefined") return;
    let cancelled = false;
    requestAnimationFrame(() => requestAnimationFrame(() => {
      if (cancelled) return;
      const candidates = [...document.querySelectorAll<HTMLInputElement>("[data-ny-edit-key]")];
      let node = candidates.find(item => item.dataset.nyEditKey === focusPath);
      if (!node && inputs) {
        const [section, id, season] = JSON.parse(focusPath), combo = inputs.combinations.find(row => row.id === id), rate = inputs.rates.find(row => row.id === id);
        const label = ["enabled", "business"].includes(section) && combo ? `${textDimensions(combo)}参与预算` : section === "entry-scope" && combo ? `${textDimensions(combo)}设为入口` : section === "rate-lock" && rate ? `${rate.season}${textDimensions(rate)}调平锁定` : section === "plan-lock" && combo ? `${season}${textDimensions(combo)}手动人数锁定` : "";
        node = [...document.querySelectorAll<HTMLInputElement>('input[type="checkbox"]')].find(item => label && item.getAttribute("aria-label") === label);
        if (section === "entry-lock") node = candidates.find(item => item.dataset.nyEditKey === nextYearEditKey("entry-count", id));
      }
      if (!node) return;
      if (node.closest(".ny-quarter-editor[hidden]")) return;
      let parent = node.parentElement;
      while (parent) { if (parent instanceof HTMLDetailsElement) parent.open = true; parent = parent.parentElement; }
      node.focus({ preventScroll: true }); node.scrollIntoView({ block: "center", behavior: window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
    }));
    return () => { cancelled = true; };
  }, [focusPath, task, filters, rateLayout, financeLayout]);
  useEffect(() => {
    if (!compareOpen || !identity || !shownDraft?.parent_id) return;
    let cancelled = false;
    const cached = comparisonCache.current.get(comparisonKey);
    if (cached) { setComparison({ key: comparisonKey, envelope: cached }); return; }
    setComparison({ key: comparisonKey });
    void getNextYearVersion(identity, shownDraft.parent_id, fiscalYear).then(envelope => {
      if (cancelled) return;
      if (envelope.draft?.id !== shownDraft.parent_id || envelope.draft.inputs.fiscal_year !== fiscalYear) throw new Error("上版快照与当前预算不匹配");
      comparisonCache.current.set(comparisonKey, envelope); setComparison({ key: comparisonKey, envelope });
    }).catch(failure => { if (!cancelled) setComparison({ key: comparisonKey, error: failure instanceof Error ? failure.message : "读取上版失败" }); });
    return () => { cancelled = true; };
  }, [compareOpen, comparisonKey]);
  useEffect(() => {
    if (filterYear.current !== fiscalYear || readYear() !== fiscalYear) return;
    persistNextYearFilters(fiscalYear, filters);
    const url = new URL(location.href); url.search = writeNextYearFilters(url.search, filters);
    if (url.search !== location.search) history.replaceState({}, "", url);
  }, [filters, fiscalYear]);
  useEffect(() => {
    if (!visibleInputs || visibleInputs.fiscal_year !== fiscalYear || filterYear.current !== fiscalYear) return;
    const valid = validateNextYearFilters(filters, visibleInputs.combinations);
    if ((Object.keys(dimensionLabels) as Dimension[]).some(key => valid[key] !== filters[key])) { setFilters(valid); setNotice("已清除当前财年不存在的筛选"); }
  }, [visibleInputs, fiscalYear, filters]);
  function diffFocusInput(editKey: string) {
    setFocusPath(editKey);
  }
  function jumpToDiffItem(item: NextYearEditItem) {
    let path: (string | number)[] = [];
    try { path = JSON.parse(item.key); } catch { changeTask(item.task); return; }
    const section = String(path[0] ?? "");
    const next: Partial<Filters> = {};
    if (section === "rates" || section === "bounds" || section === "rate-lock") {
      setRateLayout("details");
      const rate = inputs?.rates.find(row => row.id === path[1]);
      if (rate) Object.assign(next, { season: rate.season, track: rate.track, campus: rate.campus, product: rate.product, grade: rate.grade });
    } else if (section === "finance") {
      setFinanceLayout("details");
      const fin = inputs?.finance.find(row => row.id === path[1]);
      const combo = fin ? inputs?.combinations.find(row => row.id === fin.combination_id) : undefined;
      if (combo) Object.assign(next, { season: fin!.season, track: combo.track, campus: combo.campus, product: combo.product, grade: combo.grade, subject: combo.subject });
    } else if (["entry-count", "business", "enabled", "entry-scope", "entry-lock"].includes(section)) {
      const combo = inputs?.combinations.find(row => row.id === path[1]);
      if (combo) Object.assign(next, { track: combo.track, campus: combo.campus, product: combo.product, grade: combo.grade, subject: combo.subject });
    } else if (section === "plan" || section === "manual" || section === "plan-lock") {
      const combo = inputs?.combinations.find(row => row.id === path[1]);
      const season = String(path[2] ?? "");
      if (combo) Object.assign(next, { season, track: combo.track, campus: combo.campus, product: combo.product, grade: combo.grade, subject: combo.subject });
    } else if (section === "class" || section === "class-size") {
      const cls = inputs?.class_sizes[Number(path[1])];
      if (cls) Object.assign(next, { track: cls.track, product: cls.product });
    }
    setFilters({ ...emptyFilters, ...next });
    changeTask(item.task);
    if (section === "bounds" || section === "plan") openTools(section === "bounds" ? "bounds" : "plans");
    else setSideExpanded(false);
    if (section === "manual" || section === "plan-lock") setManualOpen(true);
    diffFocusInput(item.key);
  }
  function undoDiffItem(item: NextYearEditItem) {
    if (!draft || !inputs || !canEdit || busy) return;
    const reverted = undoNextYearChange(draft.inputs, inputs, edits, item);
    setInputs(reverted.inputs); setEdits(reverted.edits); setFieldErrors({});
    setPreview(null);
    setNotice("");
  }
  function undoAllDiff() {
    setInputs(draft ? structuredClone(draft.inputs) : null);
    setEdits({});
    setFieldErrors({});
    setPreview(null);
    setReason("");
    setNotice("");
    setError("");
    setBalanceMessage("");
    setBalanceIssues([]);
    setClassReadiness(null);
    setClassRepair(null);
    setClassCheckOpen(false);
    setBalanceProposal(null); setSelectionReview(null); setNewBusiness({ ...emptyNewBusiness }); setPaste(""); setBulkValue("");
  }
  function openContext(tab: SideTab) { if (typeof document !== "undefined" && typeof HTMLElement !== "undefined" && document.activeElement instanceof HTMLElement) contextLauncher.current = document.activeElement; setVersionsOpen(false); setClassCheckOpen(false); setSideTab(tab); setSideExpanded(true); }
  function closeContext() { setSideExpanded(false); if (contextLauncher.current?.isConnected) contextLauncher.current.focus({ preventScroll: true }); }
  function openTools(mode: typeof toolMode) { setToolMode(mode); openContext("tools"); }
  const applicableRates = inputs?.rates.filter(row => matches(row, filters) && nextYearRateUsage(inputs, row)[bulkField === "renewal_ppm" ? "renewal" : "share"]).length ?? 0;
  const toolTitle = { bulk: "批量修改", paste: "粘贴表格", bounds: "自动调平范围", plans: "计划新生", business: "新增校区 / 产品" }[toolMode];
  const toolsContent = sideExpanded && sideTab === "tools" && visibleInputs ? <div className="ny-tools-panel">
    {toolMode === "business" && canEdit && <section className="ny-add-business"><div className="ny-form-grid">{(["campus", "track", "product", "grade", "subject", "price", "classSize", "reason"] as const).map(key => <label className="ny-field" key={key}><span>{{ campus: "校区", track: "赛道", product: "产品", grade: "年级", subject: "科目", price: "学费（元）", classSize: "产品计划班额（共用）", reason: "新增原因" }[key]}</span><input value={newBusiness[key]} disabled={Boolean(busy)} onChange={event => setNewBusiness({ ...newBusiness, [key]: event.target.value })}/></label>)}</div><button disabled={Boolean(busy)} onClick={addBusiness}>加入预算范围</button></section>}
    {canEdit && toolMode === "bulk" && <section className="ny-bulk-panel"><p className="ny-tool-scope">当前筛选 · {applicableRates} 条适用参数</p><div className="ny-bulk-toolbar"><label>修改字段<select aria-label="批量修改字段" value={bulkField} disabled={Boolean(busy)} onChange={event => setBulkField(event.target.value)}><option value="renewal_ppm">续班率</option><option value="share_ppm">新生占比</option></select></label><label>设为（%）<input aria-label="批量修改比例" value={bulkValue} inputMode="decimal" disabled={Boolean(busy)} onChange={event => setBulkValue(event.target.value)}/></label><button disabled={Boolean(busy) || !bulkValue.trim() || !applicableRates} onClick={bulkApply}>应用到当前筛选</button></div></section>}
    {canEdit && toolMode === "paste" && <section className="ny-paste-panel"><p className="ny-tool-scope">当前筛选 · {visibleInputs.rates.filter(row => matches(row, filters)).length} 条参数</p><button disabled={Boolean(busy)} onClick={() => void copyParameters()}>复制粘贴模板</button><label className="ny-field"><span>参数ID、续班率、新生占比（三列，Tab 分隔）</span><textarea aria-label="粘贴招生参数表格" rows={7} value={paste} disabled={Boolean(busy)} onChange={event => setPaste(event.target.value)}/></label><div className="ny-tool-entries"><button disabled={Boolean(busy) || !paste.trim()} onClick={pasteParameters}>校验并暂存</button>{paste && <button disabled={Boolean(busy)} onClick={() => setPaste("")}>清空粘贴</button>}</div></section>}
    {toolMode === "bounds" && <section className="ny-bounds"><h3>自动调平范围</h3><p className="ny-range-note">未设范围：当前值 ±5 个百分点；留空恢复默认</p><PagedTable rows={visibleInputs.rates.filter(row => matches(row, filters))} label="自动调平参数范围" headers={["校区 / 业务", "季度", "续班率下限（%）", "续班率上限（%）", "新生占比下限（%）", "新生占比上限（%）"]} render={row => <><th>{textDimensions(row)}</th><td>{row.season}</td>{(["renewal_lower_ppm", "renewal_upper_ppm", "share_lower_ppm", "share_upper_ppm"] as const).map((key, index) => {
            const current = forecastNumber(edits[nextYearEditKey("rates", row.id, key.startsWith("share") ? "share_ppm" : "renewal_ppm")]) ?? row[key.startsWith("share") ? "share_ppm" : "renewal_ppm"] / 10000;
            const fallback = index % 2 ? Math.min(key.startsWith("share") ? 99.99 : 100, current + 5) : Math.max(0, current - 5);
            const label = ["续班率下限", "续班率上限", "新生占比下限", "新生占比上限"][index];
            return <td key={key}>{field(["bounds", row.id, key], row[key] ?? null, `${row.season}${textDimensions(row)}${label}（%）`, true, `默认 ${format(fallback, 2)}`, label)}</td>;
          })}</>}/></section>}
    {toolMode === "plans" && <section className="ny-plans"><h3>计划新生 · 零老生范围</h3>{scopeBar(["season", "track", "campus", "product", "grade", "subject"], planRows.filter(row => matches({ ...row.combination, season: row.season }, filters)).length, "计划新生范围", "计划新生筛选")}<PagedTable rows={planRows.filter(row => matches({ ...row.combination, season: row.season }, filters))} label="FY28 计划新生" invalidPath={invalidPath("plan")} editPath={row => ["plan", row.combination.id, row.season]} headers={["校区 / 业务", "季度", "计划新生人次"]} render={row => <><th>{textDimensions(row.combination)}</th><td>{row.season}</td><td>{field(["plan", row.combination.id, row.season], row.students ?? "", `${row.season} ${textDimensions(row.combination)}计划新生人次`)}</td></>}/></section>}
  </div> : null;
  const quarterRateValue = (row: NextYearRate, key: "renewal_ppm" | "share_ppm") => {
    const usage = visibleInputs && nextYearRateUsage(visibleInputs, row);
    if (!usage?.[key === "renewal_ppm" ? "renewal" : "share"]) return key === "share_ppm" ? "入口分配" : "不适用";
    return nextYearQuarterInputValue(edits[nextYearEditKey("rates", row.id, key)], row[key], true);
  };
  const quarterRateText = (row: NextYearRate, key: "renewal_ppm" | "share_ppm") => {
    const value = quarterRateValue(row, key), raw = edits[nextYearEditKey("rates", row.id, key)];
    return typeof value === "string" ? value.trim() ? value : "未填写" : raw !== undefined ? `${raw}%` : `${String(row[key] / 10000)}%`;
  };
  const rateQuarterly = visibleInputs && <QuarterlyBusinessTable rows={visibleInputs.rates.filter(row => matches(row, filters))}
    rowKey={row => JSON.stringify([row.track, row.campus, row.product, row.grade])} season={row => row.season}
    businessColumns={(["campus", "track", "product", "grade"] as const).map(key => ({ key, label: dimensionLabels[key], value: (row: NextYearRate) => row[key] }))}
    metrics={[
      { key: "renewal_ppm", label: "续班率", format: "rate", value: row => quarterRateValue(row, "renewal_ppm"), render: row => quarterRateText(row, "renewal_ppm") },
      { key: "share_ppm", label: "新生占比", format: "rate", value: row => quarterRateValue(row, "share_ppm"), render: row => quarterRateText(row, "share_ppm") },
      { key: "locked", label: "调平锁定", value: row => Boolean(row.locked) },
    ]} tableLabel={`${fy(fiscalYear)}招生参数季度对照`} filename={`${fy(fiscalYear)}-招生参数季度对照`}
    onCellAction={canEdit && !busy ? locateQuarterRate : undefined}
    canCellAction={(row, key) => key === "locked" || Boolean(visibleInputs && nextYearRateUsage(visibleInputs, row)[key === "renewal_ppm" ? "renewal" : "share"])} actionLabel="转到逐季编辑"/>;
  const financeQuarterly = visibleInputs && <QuarterlyBusinessTable rows={visibleInputs.finance.filter(row => matches({ ...comboById.get(row.combination_id), season: row.season }, filters))}
    rowKey={row => row.combination_id} season={row => row.season}
    businessColumns={(["campus", "track", "product", "grade", "subject"] as const).map(key => ({ key, label: dimensionLabels[key], value: (row: NextYearFinance) => comboById.get(row.combination_id)?.[key] }))}
    metrics={[
      { key: "price_yuan", label: "学费（元）", format: "money", value: row => nextYearQuarterInputValue(edits[nextYearEditKey("finance", row.id, "price_yuan")], row.price_yuan), render: row => { const raw = edits[nextYearEditKey("finance", row.id, "price_yuan")]; return raw !== undefined ? raw.trim() ? raw : "未填写" : format(row.price_yuan, 2); } },
      ...(["discount_ppm", "recognition_ppm"] as const).map(key => ({ key, label: key === "discount_ppm" ? "折扣" : "当季结转", format: "rate" as const,
        value: (row: NextYearFinance) => nextYearQuarterInputValue(edits[nextYearEditKey("finance", row.id, key)], row[key], true),
        render: (row: NextYearFinance) => { const raw = edits[nextYearEditKey("finance", row.id, key)]; return raw !== undefined ? raw.trim() ? `${raw}%` : "未填写" : `${String(row[key] / 10000)}%`; } })),
    ]} tableLabel={`${fy(fiscalYear)}财务参数季度对照`} filename={`${fy(fiscalYear)}-财务参数季度对照`}
    onCellAction={canEdit && !busy ? locateQuarterFinance : undefined} canCellAction={row => comboById.has(row.combination_id)} actionLabel="转到逐季编辑"/>;
  const specifiedEntry = entryState?.combinations.filter(row => row.enabled && effectiveNextYearEntry(row) && row.entry_students !== null && row.entry_students !== undefined).reduce((sum, row) => sum + (row.entry_students ?? 0), 0) ?? 0;
  const automaticEntryCount = entryState?.combinations.filter(row => row.enabled && effectiveNextYearEntry(row) && (row.entry_students === null || row.entry_students === undefined)).length ?? 0;
  return <div ref={shellRef} className={`budget-shell decision-shell next-year-shell${sideExpanded ? " ny-context-open" : ""}`}><a className="skip-link" href="#next-year-content">跳到主要内容</a>
    <FY27Header active="next-year" fiscalYear={fiscalYear} onBeforeLeave={beforeLeave}><div className="decision-version"><span className="status-dot"/><span>{historical ? "历史只读" : "预算草稿"}</span>{draft && <small>N{historical?.draft?.sequence ?? draft.sequence}</small>}</div></FY27Header>
    <main className="decision-workspace" id="next-year-content">
      {draft && <NextYearTaskNavigation task={task} onChange={changeTask} fiscalYear={fiscalYear}/>}
      <header className="ny-heading"><div><h1 ref={heading} tabIndex={-1}>{draft ? nextYearTasks[task - 1] : `${fy(fiscalYear)} 预算`}</h1>{source && <span className="ny-source-label">{fy(source.fiscal_year ?? fiscalYear - 1)} 预测 P{source.sequence}</span>}</div>{draft && <div className="ny-heading-actions"><button aria-expanded={classCheckOpen} disabled={Boolean(busy)} onClick={openProblems}>待处理{blockers.length > 0 && <span className="ny-problem-count">{blockers.length}</span>}{warnings.length > 0 && <small>提醒 {warnings.length}</small>}</button><button aria-expanded={versionsOpen} disabled={Boolean(busy)} onClick={openVersions}>版本记录{!historical && data?.source_changed && <span className="ny-version-dot" aria-label="有新基准预测"/>}</button><button disabled={Boolean(busy) || exporting || !calculation?.rows.length} onClick={() => void download()}>{exporting ? "正在导出…" : "导出明细"}</button></div>}</header>
      {shownDraft && calculation && <NextYearTopMetrics calculation={calculation} status={resultStatus} onSetTarget={() => changeTask(1)} compact={task !== 5}/>}
      {error && <div className="ny-message" role="alert"><span>{skuText(error)}</span>{!data && <button disabled={Boolean(busy)} onClick={() => void load()}>重新读取</button>}<button aria-label="关闭错误" onClick={() => setError("")}>关闭</button></div>}
      {currentBalanceProposal && <section className="ny-class-proposal" aria-label="调平建议预览"><div className="ny-section-heading"><h2>调平建议 · 未应用</h2><button disabled={Boolean(busy)} onClick={() => { setBalanceProposal(null); setSelectionReview(null); }}>取消建议</button></div><p>以下是全部建议的结果。取消任意项后，会重新检查所选组合。</p><ImpactComparison before={currentBalanceProposal.before} after={currentBalanceProposal.result.calculation}/><NextYearActionSelection actions={currentBalanceProposal.actions} originalInputs={currentBalanceProposal.original} proposedInputs={currentBalanceProposal.result.proposed_inputs} busy={Boolean(busy)} ready onApply={keys => void applyBalanceSelection(keys)}/></section>}
      {selectionReview && currentBalanceProposal && <section aria-label="上次勾选检查结果"><h2>上次勾选检查结果</h2><ImpactComparison before={currentBalanceProposal.before} after={selectionReview}/></section>}
      {notice && <div className="ny-message is-success" role="status"><span>{skuText(notice)}</span><button aria-label="关闭提示" onClick={() => setNotice("")}>关闭</button></div>}
      {busy === "load" && <div className="ny-loading" aria-busy="true"><div/><div/><p role="status">正在读取 FY28 预算…</p></div>}
      {!busy && data && !draft && <section className="ny-start">{data.source ? <>{sourcePicker}<div className="ny-metrics"><div className="ny-metric-primary"><span>{fy(fiscalYear - 1)} 预计结转收入（万元）</span><strong>{money(selectedSource?.forecast_revenue)}</strong></div><div><span>{fy(fiscalYear - 1)} 预计人次</span><strong>{format(selectedSource?.forecast_total)}</strong></div></div><button className="decision-primary" disabled={!canEdit} onClick={() => void run("prepare")}>生成 {fy(fiscalYear)} 草稿</button></> : <div className="ny-empty"><h2>先确认 {fy(fiscalYear - 1)} 预测</h2><a className="decision-primary" href="/budget/forecast?archive=fy27">前往达成预测</a></div>}</section>}
      {busy === "prepare" && !draft && <p role="status">正在生成 FY28 草稿…</p>}
      {draft && inputs && visibleInputs && calculation && <>
        {historical?.draft && <div className="ny-history-banner"><strong>正在查看 N{historical.draft.sequence} · 只读</strong><button disabled={Boolean(busy)} onClick={() => setHistorical(null)}>返回当前草稿</button></div>}
        <div className="ny-task-content">
        {task === 1 && <><section className="ny-section"><div className="ny-global-fields" aria-label="全年目标 · 全部业务"><h3>全年目标 · 全部业务</h3><div className="ny-form-grid">{field(["target"], visibleInputs.target_revenue_yuan, "全年经营目标（元）")}{Object.entries(visibleInputs.track_targets).map(([track, amount]) => <div key={track}>{field(["track-target", track], amount, `${track}目标（元）`)}</div>)}</div><button disabled={Boolean(busy) || !canEdit || forecastNumber(edits[nextYearEditKey("target")] ?? inputs.target_revenue_yuan) === null} onClick={distributeTargets}>按预算结转收入分配赛道目标</button></div>{scopeBar(["track", "campus", "product", "grade", "subject"], combinations.filter(row => matches(row, filters)).length)}</section>
          <PagedTable rows={combinations.filter(row => matches(row, filters))} label={`${fy(fiscalYear)}SKU`}
            section={{ id: "next-year-sku", title: "SKU", actions: canEdit && <button className="ny-tool-entry" disabled={Boolean(busy)} aria-expanded={sideExpanded && sideTab === "tools" && toolMode === "business"} aria-controls="ny-context-panel" onClick={() => openTools("business")}>新增校区 / 产品</button> }}
            headers={[...skuIdentityHeaders, "入口", "参与预算"]} render={row => <>{skuIdentityCells(row, row.added && <small>新业务</small>)}<td>{effectiveNextYearEntry(row) ? "是" : "否"}</td><td><label className="ny-checkbox-target"><input type="checkbox" aria-label={`${textDimensions(row)}参与预算`} checked={row.enabled} disabled={Boolean(busy) || !canEdit} onChange={event => { setInputs({ ...inputs, combinations: inputs.combinations.map(item => item.id === row.id ? { ...item, enabled: event.target.checked } : item) }); setPreview(null); setNotice(""); setBalanceIssues([]); }}/></label></td></>}/>
        </>}
        {task === 2 && <section className="ny-section"><div className="ny-global-fields"><div className="ny-form-grid">{field(["entry"], visibleInputs.entry_total, "年度入口人次", false, undefined, "年度入口人次 · 全部业务")}</div></div>{scopeBar(["track", "campus", "product", "grade", "subject"], combinations.filter(row => matches(row, filters)).length)}<h3>入口年级 · 当前SKU</h3><div className="ny-grade-picks">{[...new Set(combinations.filter(row => matches(row, filters)).map(row => row.grade))].sort((a, b) => dimensionSort("grade", a, b)).map(grade => { const rows = combinations.filter(row => row.grade === grade && matches(row, filters)), selected = rows.every(effectiveNextYearEntry); return <button key={grade} aria-pressed={selected} disabled={!canEdit || Boolean(busy)} onClick={() => stage({ ...inputs, combinations: inputs.combinations.map(row => row.grade === grade && matches(row, filters) ? { ...row, entry_override: !selected, ...(!selected ? {} : { entry_students: null, entry_locked: false }) } : row) }, `${grade}入口范围已调整`, path => selected && (path[0] === "entry-grade" && path[1] === grade || path[0] === "entry-count" && rows.some(row => row.id === path[1])))}>{grade}{selected ? " ✓" : ""}</button>; })}</div>
          <h3>年级入口人次 · 全部校区</h3><div className="ny-entry-status" role="status"><span>年度入口 {format(entryState?.entry_total)}</span><span>已指定 {format(specifiedEntry)}</span><span>{specifiedEntry > (entryState?.entry_total ?? 0) ? `超出 ${format(specifiedEntry - (entryState?.entry_total ?? 0))}，请减少指定人数` : automaticEntryCount ? `余下 ${format((entryState?.entry_total ?? 0) - specifiedEntry)} 自动分配` : specifiedEntry !== entryState?.entry_total ? "指定人数尚未等于年度入口" : "人数已分配完整"}</span>{inputChanged && !preview && <span>已计算列为上次结果，预览后更新</span>}</div>
          <PagedTable rows={[...new Set(combinations.filter(row => row.enabled && effectiveNextYearEntry(row)).map(row => row.grade))].sort((a, b) => dimensionSort("grade", a, b))} label="年级入口分配" headers={["年级", "指定入口人次", "已计算入口人次", "入口业务数"]} render={grade => { const members = combinations.filter(row => row.enabled && effectiveNextYearEntry(row) && row.grade === grade), assigned = members.every(row => row.entry_students !== null && row.entry_students !== undefined) ? members.reduce((sum, row) => sum + (row.entry_students ?? 0), 0) : null;
            const calculated = calculation.entry_details ? calculation.entry_details.filter(row => row.grade === grade).reduce((sum, row) => sum + row.students, 0) : calculation.rows.filter(row => row.season === "暑假" && row.entry && row.grade === grade).reduce((sum, row) => sum + (row.new_students ?? 0), 0);
            return <><th>{grade}</th><td>{field(["entry-grade", grade], assigned, `${grade}入口人次`, false, "自动分配")}</td><td>{format(calculated)}</td><td>{members.length}</td></>; }}/>
          <details><summary>按具体业务调整</summary><PagedTable rows={combinations.filter(row => matches(row, filters))} label="入口分配明细" headers={["校区 / 业务", "入口", "指定人次", "分配方式"]} render={row => <><th>{textDimensions(row)}</th><td><label className="ny-checkbox-target"><input type="checkbox" checked={effectiveNextYearEntry(row)} aria-label={`${textDimensions(row)}设为入口`} disabled={!canEdit || Boolean(busy)} onChange={event => stage({ ...inputs, combinations: inputs.combinations.map(item => item.id === row.id ? { ...item, entry_override: event.target.checked, ...(!event.target.checked ? { entry_students: null, entry_locked: false } : {}) } : item) }, "入口范围已调整", path => !event.target.checked && (path[0] === "entry-count" && path[1] === row.id || path[0] === "entry-grade" && path[1] === row.grade))}/></label></td><td>{effectiveNextYearEntry(row) ? field(["entry-count", row.id], row.entry_students ?? null, `${textDimensions(row)}入口人次（留空自动分配）`) : "—"}</td><td>{edits[nextYearEditKey("entry-count", row.id)]?.trim() || row.entry_locked ? "已锁定" : row.source_entry_weight ? "自动分配" : "需指定人次"}</td></>}/></details>
          <h3>校区入口 · 仅按校区</h3><PagedTable rows={calculation.entry_allocations.filter(row => !filters.campus || row.campus === filters.campus)} label="FY28 校区入口" headers={["校区", "分配人次", "参考入口人次"]} render={row => <><th>{row.campus}</th><td>{format(row.students)}</td><td>{format(row.source_weight)}</td></>}/><div className="ny-opening"><span>春季预测可续人次 <strong>{format(calculation.opening.eligible_students)}</strong></span><span>暑假期初老生 <strong>{format(calculation.opening.summer_old_students)}</strong></span></div></section>}
        {task === 3 && <section className="ny-section">{scopeBar(["season", "track", "campus", "product", "grade"], visibleInputs.rates.filter(row => matches(row, filters)).length)}
          <div className="ny-quarter-toolbar"><NextYearQuarterLayoutSwitch label="招生参数阅读方式" value={rateLayout} onChange={setRateLayout} readOnly={!canEdit} busy={Boolean(busy)}/>
          <div className="ny-tool-entries">{canEdit && <><button disabled={Boolean(busy)} aria-expanded={sideExpanded && sideTab === "tools" && toolMode === "bulk"} aria-controls="ny-context-panel" onClick={() => openTools("bulk")}>批量修改</button><button disabled={Boolean(busy)} aria-expanded={sideExpanded && sideTab === "tools" && toolMode === "paste"} aria-controls="ny-context-panel" onClick={() => openTools("paste")}>粘贴表格</button></>}<button disabled={Boolean(busy)} aria-expanded={sideExpanded && sideTab === "tools" && toolMode === "bounds"} aria-controls="ny-context-panel" onClick={() => openTools("bounds")}>调平范围</button><button disabled={Boolean(busy)} aria-expanded={sideExpanded && sideTab === "tools" && toolMode === "plans"} aria-controls="ny-context-panel" onClick={() => openTools("plans")}>计划新生</button></div>
          </div>{rateLayout === "quarters" && rateQuarterly}
          <div className="ny-quarter-editor" hidden={rateLayout !== "details"}><PagedTable rows={visibleInputs.rates.filter(row => matches(row, filters))} label={`${fy(fiscalYear)}招生参数`} invalidPath={invalidPath("rates")} editPath={row => ["rates", row.id]} headers={["校区 / 业务", "季度", "续班率（%）", "新生占比（%）", "调平锁定"]} render={row => { const usage = nextYearRateUsage(visibleInputs, row); return <><th>{textDimensions(row)}</th><td>{row.season}</td><td>{usage.renewal ? field(["rates", row.id, "renewal_ppm"], row.renewal_ppm, `${row.season} ${textDimensions(row)}续班率（%）`, true) : "—"}</td><td>{usage.share ? field(["rates", row.id, "share_ppm"], row.share_ppm, `${row.season} ${textDimensions(row)}新生占比（%）`, true) : "入口分配"}</td><td><label className="ny-checkbox-target"><input type="checkbox" aria-label={`${row.season}${textDimensions(row)}调平锁定`} checked={Boolean(row.locked)} disabled={!canEdit || Boolean(busy)} onChange={event => { setInputs({ ...inputs, rates: inputs.rates.map(item => item.id === row.id ? { ...item, locked: event.target.checked } : item) }); setPreview(null); setBalanceIssues([]); }}/></label></td></>; }}/></div>

</section>}
        {(task === 4 || task === 5) && scopeBar(["season", "track", "campus", "product", "grade", "subject"], calculation.rows.filter(row => matches(row, filters)).length)}
        {task === 4 && <section className="ny-section"><div className="ny-form-grid">{field(["tax"], visibleInputs.tax_factor, "预算含税转换系数")}</div><h3>价格与折扣</h3>
          <NextYearQuarterLayoutSwitch label="财务参数阅读方式" value={financeLayout} onChange={setFinanceLayout} readOnly={!canEdit} busy={Boolean(busy)}/>
          {financeLayout === "quarters" && financeQuarterly}
          <div className="ny-quarter-editor" hidden={financeLayout !== "details"}><PagedTable rows={visibleInputs.finance.filter(row => matches({ ...comboById.get(row.combination_id), season: row.season }, filters))} label="FY28 财务参数" invalidPath={invalidPath("finance")} editPath={row => ["finance", row.id]} headers={["校区 / 业务", "季度", "学费（元）", "折扣（%）", "当季结转（%）"]} render={row => { const title = textDimensions(comboById.get(row.combination_id) ?? {}); return <><th>{title}</th><td>{row.season}</td><td>{field(["finance", row.id, "price_yuan"], row.price_yuan, `${row.season} ${title}学费（元）`)}</td><td>{field(["finance", row.id, "discount_ppm"], row.discount_ppm, `${row.season} ${title}折扣（%）`, true)}</td><td>{field(["finance", row.id, "recognition_ppm"], row.recognition_ppm, `${row.season} ${title}当季结转（%）`, true)}</td></>; }}/></div>
          <h3>班量与班额 · 按赛道、产品</h3><PagedTable rows={visibleInputs.class_sizes.map((row, index) => ({ ...row, index })).filter(row => matches(row, filters))} label="班量参数" invalidPath={invalidPath("class")} editPath={row => ["class", row.index]} headers={["赛道 / 产品", "最低成班人数", "计划班额"]} render={row => <><th>{row.track} / {row.product}</th><td>{field(["class", row.index], row.minimum, `${row.track} ${row.product}最低成班人数`)}</td><td>{field(["class-size", row.index], row.planned_class_size ?? null, `${row.track} ${row.product}计划班额`)}</td></>}/><h3>季度班量</h3><PagedTable rows={calculation.rows.filter(row => matches(row, filters))} label="班量计算明细" headers={["校区 / 业务", "季度", "人次", "计划班额", "班量", "开班状态"]} render={row => <><th>{textDimensions(row)}</th><td>{row.season}</td><td>{format(row.total_students)}</td><td>{format(row.planned_class_size)}</td><td>{format(row.class_count)}</td><td>{nextYearClassStatus(row.class_size_status)}</td></>}/></section>}
        {task === 5 && <>{Object.values(filters).some(Boolean) && <NextYearOverview calculation={calculation} filters={filters} summaryOnly/>}<div className="ny-class-summary"><span>全年班量 · 全部业务 <strong>{format(calculation.annual.class_count)}</strong></span>{calculation.annual.class_count === null || calculation.annual.class_count === undefined ? <button onClick={() => changeTask(4)}>设置计划班额</button> : <button onClick={() => changeTask(4)}>查看班量</button>}</div><BudgetAnalysisView key={`${historical?.draft?.id ?? draft.id}-${preview ? "preview" : "saved"}`} facts={nextYearAnalysisFacts(calculation, visibleInputs)} contextKey={`next-year:${historical?.draft?.id ?? draft.id}:${preview ? "preview" : "saved"}`} fiscalYear={fiscalYear} annualTarget={forecastNumber(calculation.annual.target_revenue_yuan)} controlledFilters={filters} onFiltersChange={setFilters} hideFilters hideSummary hideScopeSummary/></>}
        </div>
        {!historical && canEdit && <section className="ny-adjustment"><div className="ny-section-heading"><h2>预算调节</h2><div className="ny-heading-actions"><button aria-expanded={manualOpen} disabled={Boolean(busy)} onClick={() => setManualOpen(!manualOpen)}>手动调节</button><button className="decision-primary" disabled={Boolean(busy)} onClick={() => void balance()}>{busy === "balance" ? "正在计算…" : "按经营目标自动调平"}</button></div></div>
          {manualOpen && <>{scopeBar(["season", "track", "campus", "product", "grade", "subject"], manualRows.filter(row => matches(row, filters)).length, "手动调节范围", "手动调节筛选")}<PagedTable rows={manualRows.filter(row => matches(row, filters))} label="预算手动调节明细" headers={["校区 / 业务", "季度", "当前新生人次", "指定新生人次", "锁定"]} render={row => { const plan = visibleInputs.plans.find(item => item.combination_id === row.combination_id && item.season === row.season), pending = edits[nextYearEditKey("manual", row.combination_id, row.season)] !== undefined; return <><th>{textDimensions(row)}</th><td>{row.season}</td><td>{format(row.new_students)}</td><td>{field(["manual", row.combination_id, row.season], plan?.locked ? plan.students : null, `${row.season}${textDimensions(row)}指定新生人次`)}</td><td><label className="ny-checkbox-target"><input type="checkbox" checked={pending || Boolean(plan?.locked)} disabled={Boolean(busy)} aria-label={`${row.season}${textDimensions(row)}手动人数锁定`} onChange={event => { if (!event.target.checked) stage({ ...inputs, plans: inputs.plans.map(item => item.combination_id === row.combination_id && item.season === row.season ? { ...item, locked: false } : item) }, "已解除手动锁定", path => path[0] === "manual" && path[1] === row.combination_id && path[2] === row.season); else stageEdits({ [nextYearEditKey("manual", row.combination_id, row.season)]: String(row.new_students ?? 0) }); }}/></label></td></>; }}/></>}</section>}
        {preview && !historical && <div className="ny-impact-preview" id="ny-impact-preview" tabIndex={-1}><ImpactComparison before={draft.calculation} after={preview}/></div>}
        {historical?.draft && <ImpactComparison before={historical.draft.calculation} after={draft.calculation}/>}
        <footer ref={actionBarRef} className="ny-action-bar" aria-label="预算保存操作"><div className="ny-action-status">{historical ? <span>历史版本 N{historical.draft?.sequence} · 只读</span> : <><button className={inputChanged ? "ny-edit-state" : ""} disabled={!inputChanged || Boolean(busy)} aria-expanded={sideExpanded && sideTab === "changes"} aria-controls="ny-context-panel" onClick={() => openContext("changes")}>改动 {editSummary.totalCount} 项</button>{pendingTools && <span>有内容待暂存</span>}{!unsaved && <span>N{draft.sequence} 已保存</span>}{unsaved && <button disabled={Boolean(busy)} onClick={undoAllDiff}>放弃修改</button>}</>}</div>{canEdit && <div className="ny-action-buttons"><label className="ny-field"><span>调整备注（选填）</span><input value={reason} placeholder="调整备注（选填）" maxLength={1000} disabled={Boolean(busy)} onChange={event => setReason(event.target.value)}/></label><button disabled={Boolean(busy) || !inputChanged} onClick={() => void run("preview")}>{busy === "preview" ? "正在预览…" : "查看影响"}</button><button className="decision-primary" disabled={Boolean(busy) || !inputChanged} onClick={() => void run("save")}>{busy === "save" ? "正在保存…" : "保存新版本"}</button></div>}</footer>
        <details className="ny-how"><summary>拆解怎么算</summary><dl><div><dt>入口</dt><dd>指定入口优先，其余按参考人次分配，合计保持不变</dd></div><div><dt>期初老生</dt><dd>{fy(fiscalYear - 1)} 春季预测升年级续入暑假</dd></div><div><dt>后续季度</dt><dd>按续班率、新生占比推算；手动锁定的新生按指定值计算</dd></div><div><dt>班量</dt><dd>人次除以计划班额，向上取整；最低成班人数单独校验</dd></div><div><dt>结转收入</dt><dd>按学费、折扣、结转比例和税务系数计算</dd></div><div><dt>调平</dt><dd>保留锁定项，不改学费与折扣，不强行放宽调整范围</dd></div><div><dt>状态</dt><dd>{fy(fiscalYear)} 试算草稿，不发布定版</dd></div></dl>{[...new Set(visibleInputs.notes.map(note => note.message).filter((message): message is string => typeof message === "string" && Boolean(message)))].map(message => <p key={message}>{skuText(message)}</p>)}</details>
      </>}
    </main>
    {versionsOpen && data && draft && shownDraft && <BudgetUtilityPanel open title="版本记录" busy={Boolean(busy)} onClose={() => setVersionsOpen(false)}>
      <div className="ny-version-context"><strong>{historical ? "历史只读" : "当前草稿"} N{shownDraft.sequence}</strong><span>基准 {fy(shownDraft.source.fiscal_year ?? fiscalYear - 1)} 预测 P{shownDraft.source.sequence}</span></div>
      {unsaved && <p role="status" className="ny-range-note">先保存或放弃修改，再切换版本或更换基准。</p>}
      {error && <p role="alert" className="ny-range-note">{skuText(error)}</p>}
      <section className="ny-section ny-versions"><h3>已保存版本</h3><PagedTable rows={data.history} label={`${fy(fiscalYear)}预算版本`} headers={["版本", "保存时间", "调整原因", "操作"]} render={row => <><th>N{row.sequence}{row.id === draft.id ? " · 当前" : ""}</th><td>{new Date(row.created_at).toLocaleString("zh-CN")}</td><td>{String(row.reason ?? "预算调整")}</td><td><button disabled={Boolean(busy) || unsaved} onClick={() => void viewVersion(row.id)}>{row.id === historical?.draft?.id ? "正在查看" : "查看"}</button></td></>}/></section>
      {historical?.draft && <section className="ny-section"><h3>恢复为新草稿</h3><p>恢复 N{historical.draft.sequence} 的保存输入，当前和历史版本都会保留。</p>{Boolean(identity?.roles.some(role => ["DATA_MAINTAINER", "DEPARTMENT_BUDGET_OWNER"].includes(role))) && <button disabled={Boolean(busy) || unsaved} onClick={() => void restoreVersion()}>以此版本恢复新草稿</button>}</section>}
      {shownDraft.parent_id && <section className="ny-section"><button aria-expanded={compareOpen} onClick={() => setCompareOpen(!compareOpen)}>{compareOpen ? "收起上版对比" : "对比上版"}</button>{compareOpen && (comparison?.key !== comparisonKey || !comparison.envelope && !comparison.error) && <span role="status">正在读取上版…</span>}{compareOpen && comparison?.key === comparisonKey && comparison.error && <p role="alert">{skuText(comparison.error)}<button onClick={() => { setComparison(null); setCompareOpen(false); }}>关闭后重试</button></p>}{compareOpen && previousVersion && <><p>已保存 N{previousVersion.sequence} → N{shownDraft.sequence}</p><ImpactComparison before={previousVersion.calculation} after={shownDraft.calculation}/></>}</section>}
      {!historical && canEdit && <section className="ny-section"><h3>更换基准预测</h3>{data.source_changed && <p role="status">{fy(fiscalYear - 1)} 已确认预测更新至 P{data.source?.sequence}</p>}{sourcePicker}<p className="ny-range-note">新草稿的经营目标留空，入口和参数重新准备；当前调整不带入，已有版本保留。</p>{currentBasisPreview && <div className="ny-basis-preview"><h3>基准变化预览 · 未生成草稿</h3><p>{currentBasisPreview.calculation.complete ? "计算已完成" : "计算尚未完整"} · 需处理 {currentBasisPreview.calculation.issues.filter(issue => issue.severity === "BLOCKING").length} · 提醒 {currentBasisPreview.calculation.issues.filter(issue => issue.severity === "WARNING").length}</p><ImpactComparison before={draft.calculation} after={currentBasisPreview.calculation}/>{basisPreviewIssues.length > 0 && <details><summary>查看预览问题 {basisPreviewIssues.length} 项</summary><PagedTable rows={basisPreviewIssues} label="基准预览问题明细" headers={["级别", "季度", "业务", "问题"]} render={issue => <><td>{issue.severity}</td><td>{issue.season}</td><td>{issue.business || "全部业务"}</td><td>{skuText(issue.message)}</td></>}/></details>}<button className="decision-primary" disabled={Boolean(busy) || unsaved} onClick={() => void run("prepare", currentBasisPreview.source_id)}>以此预测生成新草稿</button></div>}</section>}
    </BudgetUtilityPanel>}
    {classCheckOpen && calculation && visibleInputs && <BudgetUtilityPanel open title="待处理" busy={Boolean(busy)} onClose={() => setClassCheckOpen(false)}>
      <div className={`ny-problem-overview${blockers.length || warnings.length ? " has-issues" : ""}`}><span>需处理 <strong>{blockers.length}</strong></span><span>提醒 <strong>{warnings.length}</strong></span><span>{historical ? "历史存档 · 只读" : inputChanged && !preview ? "保存结果，修改后需重新检查" : "当前计算结果"}</span></div>
      {error && <p role="alert" className="ny-range-note">{skuText(error)}</p>}
      {!historical && !currentClassRepair && balanceIssues.length > 0 && <section className="ny-balance-diagnostics" aria-label="调平前需处理"><h3>先处理成班问题</h3>{balanceIssues.map(issue => <p key={issue.code}>{issue.count} 项 · {skuText(issue.message)}</p>)}<button disabled={Boolean(busy)} onClick={() => void checkClasses()}>处理成班问题</button></section>}
      {currentClassRepair ? <NextYearClassRepairPanel result={currentClassRepair} originalInputs={entryState ?? undefined} busy={Boolean(busy)} onApply={keys => void applyClassRepair(keys)} onCancel={() => { setClassRepair(null); setSelectionReview(null); }}/>
        : <NextYearClassReadinessPanel embedded rows={classRows} report={currentClassReadiness} scope={classCheckScope} onScopeChange={setClassCheckScope} checked={Boolean(currentClassReadiness)} stale={inputChanged || Boolean(classReadiness && !currentClassReadiness)} busy={Boolean(busy)} readOnly={!canEdit} hasTarget={forecastNumber(entryState?.target_revenue_yuan) !== null} onCheck={() => void checkClasses()} onGenerate={generateClassRepair} generating={busy === "class-repair"} onNavigate={navigateClassIssue} onClose={() => { if (!busy) setClassCheckOpen(false); }}/>
      }
      {selectionReview && currentClassRepair && <section aria-label="上次勾选检查结果"><h3>上次勾选检查结果</h3><ImpactComparison before={currentClassRepair.before_calculation} after={selectionReview}/></section>}
      {!historical && classRepair && !currentClassRepair && <p role="status">输入已变化，上一份成班方案已作废。<button disabled={Boolean(busy)} onClick={() => void generateClassRepair()}>重新生成方案</button></p>}
      {(["BLOCKING", "WARNING"] as const).map(severity => { const items = calculation.issues.filter(issue => issue.severity === severity && !["BELOW_MINIMUM_CLASS_SIZE", "ENABLED_COMBINATION_ALL_SEASONS_ZERO"].includes(issue.code)); return items.length > 0 && <section className="ny-section" key={severity}><h3>{severity === "BLOCKING" ? "其他需处理" : "提醒"}</h3><PagedTable rows={items} label={severity === "BLOCKING" ? "预算待处理明细" : "预算提醒明细"} headers={["季度", "业务", "问题", "操作"]} render={issue => { const combo = combinations.find(item => item.id === issue.combination_id); return <><td>{issue.season ?? "全年"}</td><td>{combo ? textDimensions(combo) : "全部业务"}</td><td>{skuText(issue.message)}</td><td>{combo && <button disabled={Boolean(busy)} onClick={() => { setClassCheckOpen(false); changeTask(1); setFilters({ ...emptyFilters, season: issue.season ?? "", track: combo.track, campus: combo.campus, product: combo.product, grade: combo.grade, subject: combo.subject }); }}>查看SKU</button>}</td></>; }}/></section>; })}
    </BudgetUtilityPanel>}
    {draft && sideExpanded && <NextYearSidePanel title={sideTab === "changes" ? "未保存改动" : toolTitle} expanded onClose={closeContext}>{sideTab === "changes" ? <ChangeDiffPanel embedded summary={editSummary} onJump={jumpToDiffItem} onUndo={undoDiffItem} onUndoAll={undoAllDiff} busy={Boolean(busy)} canEdit={canEdit} hasChanges={inputChanged}/> : toolsContent}</NextYearSidePanel>}
    <dialog ref={leaveDialog} className="ny-leave-dialog" aria-labelledby="ny-leave-title" onCancel={event => { event.preventDefault(); if (!busy) setPendingNavigation(""); }}><h2 id="ny-leave-title">修改未保存</h2><div><button autoFocus disabled={Boolean(busy)} onClick={() => setPendingNavigation("")}>继续编辑</button><button disabled={Boolean(busy)} onClick={() => { const destination = pendingNavigation; if (destination) { setReason(""); setEdits({}); setPendingNavigation(""); window.location.assign(destination); } }}>放弃修改并离开</button></div></dialog>
  </div>;
}
