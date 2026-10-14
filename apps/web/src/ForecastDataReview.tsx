import { ActionTable } from "./ActionTable";
import { skuText } from "./budgetDisplay";
import { BudgetDataSection } from "./BudgetDataSection";
import { skuIdentityCells, skuIdentityHeaders } from "./SkuIdentity";
import type { ApiIdentity } from "./api";
import { canConfirmForecast, forecastDimensionLabels, forecastSeasons, groupForecastIssues, type ForecastDimension, type ForecastDimensions, type ForecastIssue, type ForecastVersion } from "./forecastApi";
import "./forecastReview.css";

const dimensions = Object.keys(forecastDimensionLabels) as ForecastDimension[];
const missingActualCode = "FINALIZED_ACTUAL_MISSING";
const businessTitles: Record<string, string> = {
  FINALIZED_ACTUAL_MISSING: "已定版实际待核对", FINALIZED_ACTUAL_INVALID: "已定版实际无效",
  FINALIZED_TOTAL_INVALID: "部门实际待核对", FINALIZED_TOTAL_MISMATCH: "部门实际与明细不一致",
  SOURCE_UNMAPPED: "实际业务待对应", FROZEN_CARRYIN_UNMAPPED: "结转实际待对应",
  CURRENT_SOURCE_MISSING: "当前实际待补齐", UPSTREAM_HEADCOUNT_UNAVAILABLE: "等待前季人数",
  WITHDRAWAL_HISTORY_MISSING: "退班预测待补齐", WITHDRAWAL_METRICS_UNVERIFIED: "退班数据待核对",
  REVENUE_PARAMETER_INVALID: "结转收入参数待补齐", RENEWAL_RATE_INVALID: "续班率待核对",
  NEW_STUDENT_SHARE_INVALID: "新生占比待核对", MINIMUM_SAMPLE_INVALID: "历史样本门槛待核对",
  SOURCE_NEGATIVE_VALUE: "来源负数待核对", SOURCE_COUNT_INVALID: "来源人次待核对",
  SOURCE_KEY_DUPLICATED: "来源明细重复", SOURCE_DIMENSIONS_MISSING: "来源业务维度待补齐",
  SOURCE_DIMENSIONS_INCOMPLETE: "来源业务维度待补齐", SOURCE_ROW_INVALID: "来源明细待核对",
  SOURCE_SEASON_INVALID: "来源季度待核对", SOURCE_CLASS_BASE_MISMATCH: "来源人数基数不一致",
  BASELINE_EMPTY: "定版预算基准缺失", BASELINE_KEY_DUPLICATED: "定版预算明细重复",
  BASELINE_DIMENSIONS_INVALID: "定版预算维度待核对", BASELINE_SEASONS_INCOMPLETE: "定版预算季度不完整",
  BASELINE_NEGATIVE_VALUE: "原预算负数记录", BUSINESS_NEW_MISSING: "业务新生人次待补齐",
  BUSINESS_NEW_INVALID: "业务新生人次待核对", CARRYOVER_NEGATIVE: "结转负数待核对",
  ACTUAL_CARRYOUT_REQUIRED: "实际结转待补齐", ACTUAL_CARRYOUT_INVALID: "实际结转待核对",
  MAPPING_INVALID: "业务对应待核对", MAPPING_DUPLICATED: "业务对应重复", MAPPING_TARGET_NOT_FOUND: "对应预算范围不存在",
  ADJUSTMENT_INVALID: "人数调整待核对", ADJUSTMENT_DUPLICATED: "人数调整重复",
  ADDITIONAL_ALLOCATION_INFEASIBLE: "新增人数无法分配", WITHDRAWAL_ADJUSTMENT_INFEASIBLE: "退班人数无法分配",
  FINALIZED_ADJUSTMENT_FORBIDDEN: "已定版季度不能调整", FINANCIAL_ONLY_SOURCE_HAS_STUDENTS: "纯财务范围混入学生人次",
  FINANCIAL_ONLY_ADDITIONAL_FORBIDDEN: "纯财务范围不能新增学生", FORECAST_SCOPE_INVALID: "独立SKU待核对",
  FORECAST_SCOPE_DUPLICATED: "独立SKU重复", FORECAST_SCOPE_ADDITIONAL_INVALID: "独立业务新增待核对",
  FIXED_RULES_VERSION_INVALID: "预测规则版本待核对", FIXED_RULES_TRIAL_CONFLICT: "预测规则状态冲突",
  QUARTER_STATE_REQUIRED: "季度状态待补齐", FROZEN_CARRYIN_AMOUNT_INVALID: "冻结结转金额待核对",
  FROZEN_CARRYIN_DIMENSIONS_MISSING: "冻结结转维度待补齐", FROZEN_CARRYIN_INVALID: "冻结结转待核对",
  CLASS_BASE_MISMATCH: "人数基数不一致", HISTORY_COUNT_INVALID: "历史人次待核对",
};
const fieldLabels: Record<string, string> = { opening: "期初结转", old: "老生人次", new: "新生人次", total: "总人次", price: "学费", discount: "折扣", recognition: "当季结转比例", renewal: "续班率", share: "新生占比" };
const sortSeasons = (values: string[]) => [...new Set(values)].sort((a, b) => {
  const orderA = forecastSeasons.indexOf(a as typeof forecastSeasons[number]), orderB = forecastSeasons.indexOf(b as typeof forecastSeasons[number]);
  return (orderA < 0 ? 4 : orderA) - (orderB < 0 ? 4 : orderB) || a.localeCompare(b, "zh-CN");
});
const issueDetail = (issue: ForecastIssue): Record<string, unknown> => {
  const detail = (issue as ForecastIssue & { detail?: unknown }).detail;
  return detail && typeof detail === "object" && !Array.isArray(detail) ? detail as Record<string, unknown> : {};
};
const dimensionText = (dims?: Partial<ForecastDimensions>, includeCampus = true) => dimensions.filter(key => includeCampus || key !== "campus").map(key => dims?.[key] || `${forecastDimensionLabels[key]}未填写`).join(" / ");

export const forecastReviewIssueTitle = (code: string) => businessTitles[code] ?? "其他数据待核对";

export interface ForecastMissingActualGroup { campus: string; seasons: string[]; issues: ForecastIssue[] }
export function groupForecastMissingActuals(issues: ForecastIssue[]): ForecastMissingActualGroup[] {
  const groups = new Map<string, ForecastMissingActualGroup>();
  for (const issue of issues) {
    if (issue.severity !== "BLOCKING" || issue.code !== missingActualCode) continue;
    const campus = issue.dims?.campus || "校区未填写", group = groups.get(campus) ?? { campus, seasons: [], issues: [] };
    group.issues.push(issue); group.seasons = sortSeasons([...group.seasons, ...(issue.season ? [issue.season] : [])]); groups.set(campus, group);
  }
  return [...groups.values()].sort((a, b) => a.campus.localeCompare(b.campus, "zh-CN"));
}

export interface ForecastWarningRecord {
  id: string; code: string; title: string; seasons: string[]; dims?: Partial<ForecastDimensions>;
  field?: string; value?: unknown; issues: ForecastIssue[];
}
/** Merge only complete, identical source identities; retain every original issue as evidence. */
export function groupForecastWarningRecords(issues: ForecastIssue[]): ForecastWarningRecord[] {
  const records = new Map<string, ForecastWarningRecord>();
  issues.forEach((issue, index) => {
    if (issue.severity !== "WARNING") return;
    const detail = issueDetail(issue), field = typeof detail.field === "string" ? detail.field : undefined;
    const complete = dimensions.every(key => typeof issue.dims?.[key] === "string" && Boolean(issue.dims[key]?.trim()));
    const canMerge = issue.code === "BASELINE_NEGATIVE_VALUE" && complete && field !== undefined && Object.hasOwn(detail, "value");
    const id = canMerge ? JSON.stringify([issue.code, ...dimensions.map(key => issue.dims?.[key]), field, detail.value]) : `record-${index}`;
    const record = records.get(id) ?? { id, code: issue.code, title: forecastReviewIssueTitle(issue.code), seasons: [], dims: issue.dims, field, value: detail.value, issues: [] };
    record.issues.push(issue); record.seasons = sortSeasons([...record.seasons, ...(issue.season ? [issue.season] : [])]); records.set(id, record);
  });
  return [...records.values()];
}

export function ForecastDataNotice({ issues, onOpen }: { issues: ForecastIssue[]; onOpen?: () => void }) {
  const blockers = issues.filter(issue => issue.severity === "BLOCKING");
  if (!blockers.length) return null;
  const onlyMissingActual = blockers.every(issue => issue.code === missingActualCode);
  const seasons = sortSeasons(blockers.map(issue => issue.season ?? "").filter(Boolean));
  const title = onlyMissingActual ? `${seasons.join("、") || "已定版"}实际待核对 · ${blockers.length}项` : `数据待核对 · ${blockers.length}项`;
  return <button type="button" className="forecast-data-notice" onClick={onOpen} disabled={!onOpen} aria-label={`${title}，查看明细`}><span className="forecast-data-notice-dot" aria-hidden="true"/><span>{title}</span><span className="forecast-data-notice-action">查看</span></button>;
}

function IssueEvidence({ issues }: { issues: ForecastIssue[] }) {
  return <details className="forecast-review-evidence"><summary>技术信息</summary>{issues.map((issue, index) => <div key={index}><code>{issue.code}</code><p>{skuText(issue.message)}</p><pre>{JSON.stringify(issue, null, 2)}</pre></div>)}</details>;
}

const recordValue = (record: ForecastWarningRecord) => {
  if (record.value === undefined || record.value === null) return "未提供";
  const value = typeof record.value === "string" || typeof record.value === "number" ? String(record.value) : JSON.stringify(record.value);
  const unit = record.field === "opening" || record.field === "price" ? " 元" : ["old", "new", "total"].includes(record.field ?? "") ? " 人次" : "";
  return `${value}${unit}`;
};

export function ForecastDataReview({ issues, onUpdate, busy = false }: { issues: ForecastIssue[]; onUpdate?: () => void; busy?: boolean }) {
  const missingGroups = groupForecastMissingActuals(issues), missingCount = missingGroups.reduce((sum, group) => sum + group.issues.length, 0);
  const otherGroups = groupForecastIssues(issues.filter(issue => issue.severity === "BLOCKING" && issue.code !== missingActualCode));
  const warnings = groupForecastWarningRecords(issues);
  const warningTitle = warnings.every(record => record.code === "BASELINE_NEGATIVE_VALUE") ? "原预算记录" : "其他提醒";
  return <div className="forecast-data-review">
    {missingCount > 0 && <BudgetDataSection id="forecast-review-missing" className="forecast-review-missing" title="已定版实际" count={missingCount} countLabel="项待核对" headingLevel={3}>
      <div className="forecast-review-next"><p>目前按0展示；核对来源后更新预测。此处不能补录已定版实际。</p>{onUpdate && <button type="button" className="forecast-primary" disabled={busy} onClick={onUpdate}>{busy ? "正在更新…" : "更新预测"}</button>}</div>
      <div className="forecast-review-campus-list">{missingGroups.map(group => <details className="forecast-review-campus" key={group.campus}><summary><strong>{group.campus}</strong><span>{group.issues.length}项</span></summary><div aria-label={`${group.campus}待核对实际`}><ActionTable keyboardNavigation tableLabel={`${group.campus}待核对实际`} aria-label={`${group.campus}待核对实际表`} scrollClassName="forecast-review-table-wrap"><thead><tr><th scope="col">季度</th>{skuIdentityHeaders.map(label => <th scope="col" key={label}>{label}</th>)}<th scope="col">缺少数据</th></tr></thead><tbody>{group.issues.map((issue, index) => <tr key={index}><td>{issue.season || "未填写"}</td>{skuIdentityCells(Object.fromEntries(dimensions.map(key => [key, issue.dims?.[key] || `${forecastDimensionLabels[key]}未填写`])) as ForecastDimensions, <IssueEvidence issues={[issue]}/>)}<td>结转收入、人次</td></tr>)}</tbody></ActionTable></div></details>)}</div>
    </BudgetDataSection>}
    {otherGroups.length > 0 && <BudgetDataSection id="forecast-review-other" className="forecast-review-other" title="其他待核对数据" count={otherGroups.reduce((sum, group) => sum + group.issues.length, 0)} countLabel="项待核对" headingLevel={3}>{otherGroups.map(group => <details className="forecast-review-problem" key={group.key}><summary><strong>{forecastReviewIssueTitle(group.code)}</strong>{group.season && <span>{group.season}</span>}<span>{group.issues.length}项</span></summary><ul>{group.issues.map((issue, index) => <li key={index}><span>{issue.dims ? dimensionText(issue.dims) : "部门数据"}</span><IssueEvidence issues={[issue]}/></li>)}</ul></details>)}</BudgetDataSection>}
    {warnings.length > 0 && <details className="forecast-review-records"><summary><strong>{warningTitle}</strong><span>{warnings.length}项</span></summary>{warnings.map(record => <article key={record.id}><h3>{record.dims ? dimensionText(record.dims) : record.title}</h3><dl><div><dt>季度</dt><dd>{record.seasons.join("、") || "未填写"}</dd></div>{record.field && <div><dt>{fieldLabels[record.field] ?? (record.code === "BASELINE_NEGATIVE_VALUE" ? "原预算字段" : "相关字段")}</dt><dd>{recordValue(record)}</dd></div>}{!record.field && <div><dt>记录</dt><dd>{record.title}</dd></div>}</dl><IssueEvidence issues={record.issues}/></article>)}</details>}
    {!missingCount && !otherGroups.length && !warnings.length && <p className="forecast-review-empty">没有待核对数据</p>}
  </div>;
}

/** Explain the existing guard; display defaults never make a blocked forecast confirmable. */
export function forecastConfirmationHint(version: ForecastVersion | null, identity: ApiIdentity | null, pending = false): string {
  if (!version) return "先生成预测";
  if (version.status === "CONFIRMED") return "本次预测已确认";
  if (pending) return "先保存本次修改";
  if (!identity?.roles.includes("DEPARTMENT_BUDGET_OWNER")) return "仅部门预算负责人可确认";
  const blockers = version.calculation.issues.filter(issue => issue.severity === "BLOCKING"), missing = blockers.filter(issue => issue.code === missingActualCode);
  if (missing.length) {
    const seasons = sortSeasons(missing.map(issue => issue.season ?? "").filter(Boolean));
    return `${seasons.join("、") || "已定版"}实际还有${missing.length}项待核对`;
  }
  const other = blockers.filter(issue => !issue.code.startsWith("BASELINE_"));
  if (other.length) return `还有${other.length}项数据待核对`;
  if (version.assumptions.trial_mode) return "试用预测不能正式确认";
  if (blockers.some(issue => issue.code.startsWith("BASELINE_"))) return "定版预算基准待核对";
  if (canConfirmForecast(version, identity, pending)) return "";
  return "预测尚未通过正式检查";
}
