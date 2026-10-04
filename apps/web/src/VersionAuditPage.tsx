import { useCallback, useEffect, useMemo, useState } from "react";

import {
  ApiError,
  downloadAuditArchive,
  exportAuditArchive,
  fetchAnnualBudget,
  fetchAuditEvents,
  fetchJob,
  fetchRevision,
  fetchRevisionDetailDiff,
  fetchRevisionDiff,
  fetchRevisions,
  type AnnualBudgetResponse,
  type ApiIdentity,
  type AuditEvent,
  type AuditEventFilters,
  type JobResponse,
  type RevisionComponentDiff,
  type RevisionDetailDiff,
  type RevisionDetailDiffItem,
  type RevisionDetailState,
  type RevisionDiff,
  type RevisionResponse,
} from "./api";
import { BudgetContextHeader } from "./BudgetContextHeader";

const OWNER_ROLE = "DEPARTMENT_BUDGET_OWNER";

const COMMAND_LABELS: Record<string, string> = {
  CREATE_ANNUAL_BUDGET: "建立年度预算",
  DERIVE_BUDGET_REVISION: "派生预算草稿",
  REPLACE_TARGET_SET: "锁定学校版目标",
  GENERATE_COMBINATION_DRAFT: "生成预算组合",
  REPLACE_COMBINATION_DRAFT: "修改预算组合",
  CONFIRM_COMBINATION_SET: "确认预算组合",
  SAVE_AND_LOCK_ENTRY_TOTAL: "锁定入口总值",
  REPLACE_CAMPUS_ENTRY_DRAFT: "修改校区入口",
  CONFIRM_CAMPUS_ENTRY_SET: "确认校区入口",
  REPLACE_ENTRY_SCOPE_DRAFT: "修改入口范围",
  CONFIRM_ENTRY_SCOPE: "确认入口范围",
  REPLACE_RATE_PARAMETER_DRAFT: "修改比例参数",
  CHANGE_AND_LOCK_RATE_PARAMETERS: "修改并锁定比例",
  APPLY_REBALANCING_SCENARIO: "应用调平方案",
  FINALIZE_FORMAL_PLAN_CONFIRMATION: "正式方案确认",
  CALCULATE_HEADCOUNT_RESULT: "计算预算人数",
  CALCULATE_CLASS_SIZE_RESULT: "核对班均",
  CALCULATE_REVENUE_RESULT: "计算预算收入",
  VALIDATE_TARGET_CLOSURE: "核对收入目标",
  VALIDATE_CLASS_SIZE_RESULT: "核对班均",
};

function commandLabel(command: string): string {
  return COMMAND_LABELS[command] ?? (command.startsWith("CALCULATE_") || command.startsWith("VALIDATE_") ? "系统计算" : "预算内容更新");
}

function isMilestoneRevision(revision: RevisionResponse, selectedId: string, publishedIds: Set<string>): boolean {
  return revision.budget_revision_id === selectedId
    || publishedIds.has(revision.budget_revision_id)
    || typeof revision.component_manifest.formal_confirmation_id === "string"
    || !/^(CALCULATE_|VALIDATE_)/.test(revision.command_type);
}

const EVENT_LABELS: Record<string, string> = {
  ANNUAL_BUDGET_CREATED: "建立年度预算",
  BUDGET_REVISION_DERIVED: "派生预算草稿",
  BUDGET_CALCULATION_QUEUED: "登记完整复算",
  FORMAL_PLAN_CONFIRMATION_REQUESTED: "发起正式方案确认",
  FORMAL_PLAN_CONFIRMED: "正式方案已确认",
  PUBLISHED: "发布年度预算基线",
  TARGET_SET_REPLACED_AND_LOCKED: "学校版目标已锁定",
  COMBINATION_SET_CONFIRMED: "预算组合已确认",
  CAMPUS_ENTRY_SET_CONFIRMED: "校区入口已确认",
  ENTRY_SCOPE_CONFIRMED: "入口范围已确认",
  RATE_BOUNDARIES_CONFIRMED: "比例边界已确认",
  RATE_PARAMETERS_LOCKED: "比例参数已锁定",
  PLANNED_NEW_STUDENTS_CHANGED_AND_LOCKED: "计划新生人次已锁定",
  RESOURCE_FINANCE_CONFIRMED: "资源财务参数已确认",
  DISCOUNT_PARAMETER_VERSION_CONFIRMED: "折扣参数已确认",
  SCENARIO_APPLIED: "调平方案已应用",
  AUDIT_ARCHIVE_QUEUED: "审计归档已登记",
  AUDIT_ARCHIVE_CREATED: "审计归档已封存",
};

const COMPONENT_LABELS: Record<string, string> = {
  source_snapshot_id: "来源快照",
  parameter_version_id: "参数总版本",
  target_set_version_id: "学校版目标",
  combination_set_version_id: "预算组合",
  entry_total_version_id: "财年入口总值",
  campus_entry_suggestion_version_id: "校区入口建议",
  campus_entry_set_version_id: "校区入口确定值",
  entry_scope_version_id: "入口范围",
  entry_detail_set_version_id: "入口明细",
  rate_parameter_version_id: "续班率与新生占比",
  planned_new_student_version_id: "计划新生人次",
  opening_student_version_id: "期初老生",
  resource_finance_parameter_version_id: "班均与财务参数",
  discount_parameter_version_id: "折扣参数",
  headcount_result_version_id: "整数人次结果",
  class_size_result_version_id: "班均校验结果",
  revenue_result_version_id: "收入结果",
  target_closure_result_version_id: "目标闭合结果",
  formal_confirmation_id: "正式方案确认",
};

const CHANGE_LABELS: Record<RevisionComponentDiff["change_type"], string> = {
  ADDED: "新增",
  REMOVED: "失效",
  CHANGED: "替换",
  UNCHANGED: "沿用",
};

const DETAIL_COMPONENTS = new Set([
  "rate_parameter_version_id",
  "planned_new_student_version_id",
  "opening_student_version_id",
  "resource_finance_parameter_version_id",
  "discount_parameter_version_id",
]);

const DETAIL_TYPE_LABELS: Record<string, string> = {
  RENEWAL_RATE: "续班率",
  NEW_STUDENT_SHARE: "新生占比",
  PLANNED_NEW_STUDENTS: "计划新生人次",
  BUDGET_TAX_FACTOR: "预算含税转换",
  CLASS_SIZE: "最低班均",
  REVENUE_PARAMETER: "学费与结转参数",
  OPENING_CARRYOVER: "期初结转收入",
  DETAIL_DISCOUNT: "明细折扣率",
  COMPOSITE_DISCOUNT_TARGET: "综合折扣目标",
};

const STATUS_LABELS: Record<string, string> = {
  LOCKED: "已锁定",
  EDITABLE: "可调整",
  CONFIRMED: "已确认",
  PENDING_CONFIRMATION: "待确认",
  FILLED_PENDING_LOCK: "待锁定",
  LOCKED_ZERO: "已锁定为 0",
  LOCKED_POSITIVE: "已锁定",
  NOT_PRESENT: "该版本无此行",
};

const ROLE_LABELS: Record<string, string> = {
  DATA_MAINTAINER: "数据维护人员",
  DEPARTMENT_BUDGET_OWNER: "部门预算负责人",
  PERMISSION_ADMIN: "权限管理员",
};

const TERMINAL_JOB_STATUSES = new Set(["SUCCEEDED", "INCOMPLETE", "FAILED", "CANCELLED"]);

interface AuditFilterState {
  selectedRevisionOnly: boolean;
  eventType: string;
  objectType: string;
  actorId: string;
  actingRole: string;
  reasonContains: string;
  occurredFrom: string;
  occurredTo: string;
}

const EMPTY_FILTERS: AuditFilterState = {
  selectedRevisionOnly: true,
  eventType: "",
  objectType: "",
  actorId: "",
  actingRole: "",
  reasonContains: "",
  occurredFrom: "",
  occurredTo: "",
};

function storedSetting(key: string, fallback: string): string {
  const value = window.localStorage.getItem(key)?.trim();
  return value || fallback;
}

function queryValue(key: string): string {
  return new URLSearchParams(window.location.search).get(key) ?? "";
}

function shortId(value: unknown, size = 7): string {
  if (typeof value !== "string" || !value) return value === null ? "无" : String(value ?? "无");
  if (value.length <= size * 2 + 1) return value;
  return `${value.slice(0, size)}…${value.slice(-size)}`;
}

function fullDate(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

function dayLabel(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", { month: "long", day: "numeric" }).format(new Date(value));
}

function clockLabel(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

function fileSizeLabel(value: unknown): string {
  if (typeof value !== "number") return "";
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function errorText(error: unknown): string {
  if (error instanceof ApiError) return `${error.message}（${error.code}）`;
  return error instanceof Error ? error.message : "读取失败";
}

function valueText(value: unknown): string {
  if (value === null || value === undefined || value === "") return "无";
  if (typeof value === "boolean") return value ? "是" : "否";
  if (Array.isArray(value)) return value.length ? value.map(String).join("、") : "无";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function detailValueText(value: unknown, detailType: string): string {
  if (value === null || value === undefined || value === "") return "未记录";
  if (
    typeof value === "number"
    && ["RENEWAL_RATE", "NEW_STUDENT_SHARE", "DETAIL_DISCOUNT", "COMPOSITE_DISCOUNT_TARGET"].includes(detailType)
  ) {
    return `${(value / 10_000).toFixed(2)}%`;
  }
  if (typeof value === "object") {
    return Object.entries(value as Record<string, unknown>)
      .map(([key, item]) => `${key}: ${String(item)}`)
      .join(" / ");
  }
  return String(value);
}

function businessKeyText(key: Record<string, unknown>): string {
  return Object.entries(key).map(([name, value]) => `${name} ${String(value)}`).join(" · ");
}

function revisionStatus(
  revision: RevisionResponse,
  budget: AnnualBudgetResponse,
  publishedIds: Set<string>,
): { label: string; tone: string }[] {
  const statuses = [];
  if (budget.current_draft_revision_id === revision.budget_revision_id && budget.current_published_revision_id !== revision.budget_revision_id) {
    statuses.push({ label: "当前草稿", tone: "draft" });
  }
  if (budget.current_published_revision_id === revision.budget_revision_id) {
    statuses.push({ label: "当前正式版", tone: "published" });
  } else if (publishedIds.has(revision.budget_revision_id)) {
    statuses.push({ label: "曾发布", tone: "historic" });
  }
  if (typeof revision.component_manifest.formal_confirmation_id === "string") {
    statuses.push({ label: "已正式确认", tone: "confirmed" });
  }
  return statuses.length ? statuses : [{ label: "历史草稿", tone: "historic" }];
}

function ComponentDiffRow({
  item,
  selected,
  onInspect,
}: {
  item: RevisionComponentDiff;
  selected: boolean;
  onInspect: (componentKey: string) => void;
}) {
  const drillable = DETAIL_COMPONENTS.has(item.component_key);
  return (
    <article className={`component-diff component-diff--${item.change_type.toLowerCase()}${selected ? " is-detail-selected" : ""}`}>
      <div className="component-diff__summary">
        <span className="component-diff__type">{CHANGE_LABELS[item.change_type]}</span>
        <strong className="component-diff__name">{COMPONENT_LABELS[item.component_key] ?? "预算组件"}</strong>
        {drillable && <button type="button" onClick={() => onInspect(item.component_key)}>查看逐行变化</button>}
      </div>
      <details className="component-diff__technical"><summary>查看版本依据</summary><dl><div><dt>组件</dt><dd>{item.component_key}</dd></div><div><dt>比较版本</dt><dd>{valueText(item.against_value)}</dd></div><div><dt>当前版本</dt><dd>{valueText(item.revision_value)}</dd></div></dl></details>
    </article>
  );
}

function DetailStatePanel({
  label,
  state,
  detailType,
}: {
  label: string;
  state: RevisionDetailState;
  detailType: string;
}) {
  const evidence = { ...state.source_evidence, ...state.provenance };
  return (
    <section className={`detail-state${state.present ? "" : " detail-state--absent"}`}>
      <header><span>{label}</span><strong>{STATUS_LABELS[state.status] ?? state.status}</strong></header>
      <dl>
        <div><dt>系统值</dt><dd>{detailValueText(state.system_value, detailType)}</dd></div>
        <div><dt>人工值</dt><dd>{detailValueText(state.manual_value, detailType)}</dd></div>
        <div><dt>最终值</dt><dd>{detailValueText(state.final_value, detailType)}</dd></div>
      </dl>
      <footer>
        <span>{state.confirmed ? "已确认" : "未确认"}</span>
        {state.reason && <p>{state.reason}</p>}
        {Object.keys(evidence).length > 0 && (
          <details>
            <summary>来源与过程证据</summary>
            <dl>{Object.entries(evidence).map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{valueText(value)}</dd></div>)}</dl>
          </details>
        )}
      </footer>
    </section>
  );
}

function DetailDiffRow({ item }: { item: RevisionDetailDiffItem }) {
  return (
    <article className={`detail-diff-row detail-diff-row--${item.change_type.toLowerCase()}`}>
      <header>
        <span>{CHANGE_LABELS[item.change_type]}</span>
        <div>
          <strong>{DETAIL_TYPE_LABELS[item.detail_type] ?? item.detail_type}</strong>
          <p>{businessKeyText(item.business_key)}</p>
        </div>
        <code title={item.stable_key}>{shortId(item.stable_key, 9)}</code>
      </header>
      <div className="detail-diff-row__states">
        <DetailStatePanel label="比较基准" state={item.against} detailType={item.detail_type} />
        <DetailStatePanel label="选定修订" state={item.revision} detailType={item.detail_type} />
      </div>
    </article>
  );
}

function AuditEventRow({ event, revisionNo }: { event: AuditEvent; revisionNo?: number }) {
  const evidence = { ...event.object_key, ...event.detail };
  return (
    <article className="audit-event">
      <time dateTime={event.occurred_at}>
        <strong>{clockLabel(event.occurred_at)}</strong>
        <span>{dayLabel(event.occurred_at)}</span>
      </time>
      <div className="audit-event__dot" aria-hidden="true" />
      <div className="audit-event__body">
        <div className="audit-event__heading">
          <h3>{EVENT_LABELS[event.event_type] ?? "预算记录更新"}</h3>
          {revisionNo && <span>R{revisionNo}</span>}
        </div>
        <p className="audit-event__actor">{event.actor_id} · {ROLE_LABELS[event.acting_role] ?? event.acting_role}</p>
        {event.reason && <blockquote>{event.reason}</blockquote>}
        <details>
          <summary>查看审计证据</summary>
          <dl>
            {Object.entries(evidence).map(([key, value]) => (
              <div key={key}><dt>{key}</dt><dd title={valueText(value)}>{valueText(value)}</dd></div>
            ))}
            <div><dt>request_id</dt><dd>{event.request_id}</dd></div>
            <div><dt>event_id</dt><dd>{event.event_id}</dd></div>
            <div><dt>操作类型</dt><dd>{event.event_type}</dd></div>
            <div><dt>对象类型</dt><dd>{event.object_type}</dd></div>
          </dl>
        </details>
      </div>
    </article>
  );
}

export function VersionAuditPage() {
  const [revisionInput] = useState(() => queryValue("revision"));
  const [identity] = useState<ApiIdentity>(() => ({
    userId: storedSetting("budget.userId", "budget-owner"),
    departmentId: storedSetting("budget.departmentId", "zhihui-learning"),
    roles: [OWNER_ROLE],
  }));
  const [budget, setBudget] = useState<AnnualBudgetResponse | null>(null);
  const [revisions, setRevisions] = useState<RevisionResponse[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [againstId, setAgainstId] = useState("");
  const [diff, setDiff] = useState<RevisionDiff | null>(null);
  const [detailComponent, setDetailComponent] = useState("");
  const [detailDiff, setDetailDiff] = useState<RevisionDetailDiff | null>(null);
  const [detailQuery, setDetailQuery] = useState("");
  const [detailChangedOnly, setDetailChangedOnly] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [publishedIds, setPublishedIds] = useState<Set<string>>(new Set());
  const [auditEvents, setAuditEvents] = useState<AuditEvent[]>([]);
  const [auditCursor, setAuditCursor] = useState<string | null>(null);
  const [filters, setFilters] = useState<AuditFilterState>(EMPTY_FILTERS);
  const [appliedFilters, setAppliedFilters] = useState<AuditFilterState>(EMPTY_FILTERS);
  const [showUnchanged, setShowUnchanged] = useState(false);
  const [loading, setLoading] = useState(false);
  const [auditLoading, setAuditLoading] = useState(false);
  const [archiveJob, setArchiveJob] = useState<JobResponse | null>(null);
  const [archiveBusy, setArchiveBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const revisionById = useMemo(
    () => new Map(revisions.map((revision) => [revision.budget_revision_id, revision])),
    [revisions],
  );
  const selectedRevision = revisionById.get(selectedId) ?? null;

  const auditApiFilters = useCallback((state: AuditFilterState, cursor?: string): AuditEventFilters => ({
    revisionId: state.selectedRevisionOnly ? selectedId : undefined,
    eventType: state.eventType.trim() || undefined,
    objectType: state.objectType.trim() || undefined,
    actorId: state.actorId.trim() || undefined,
    actingRole: state.actingRole || undefined,
    reasonContains: state.reasonContains.trim() || undefined,
    occurredFrom: state.occurredFrom ? new Date(`${state.occurredFrom}T00:00:00`).toISOString() : undefined,
    occurredTo: state.occurredTo ? new Date(`${state.occurredTo}T23:59:59`).toISOString() : undefined,
    cursor,
    limit: 50,
  }), [selectedId]);

  const loadAudits = useCallback(async (
    annualBudgetId: string,
    state: AuditFilterState,
    append = false,
    cursor?: string,
  ) => {
    setAuditLoading(true);
    try {
      const page = await fetchAuditEvents(
        annualBudgetId,
        auditApiFilters(state, cursor),
        identity,
      );
      setAuditEvents((current) => append ? [...current, ...page.items] : page.items);
      setAuditCursor(page.next_cursor);
    } finally {
      setAuditLoading(false);
    }
  }, [auditApiFilters, identity]);

  const loadDiff = useCallback(async (revisionId: string, compareId: string) => {
    if (!compareId || revisionId === compareId) {
      setDiff(null);
      return;
    }
    setDiff(await fetchRevisionDiff(revisionId, compareId, identity));
  }, [identity]);

  const loadDetailDiff = useCallback(async (
    componentKey: string,
    changedOnly = detailChangedOnly,
    query = detailQuery,
    append = false,
    cursor?: string,
  ) => {
    if (!selectedId || !againstId) return;
    setDetailLoading(true);
    setError(null);
    try {
      const result = await fetchRevisionDetailDiff(
        selectedId,
        againstId,
        componentKey,
        changedOnly,
        query,
        identity,
        cursor,
      );
      setDetailComponent(componentKey);
      setDetailDiff((current) => append && current
        ? { ...result, items: [...current.items, ...result.items] }
        : result);
    } catch (nextError) {
      setError(errorText(nextError));
    } finally {
      setDetailLoading(false);
    }
  }, [againstId, detailChangedOnly, detailQuery, identity, selectedId]);

  const pollArchiveJob = useCallback(async (jobId: string): Promise<JobResponse> => {
    for (;;) {
      const nextJob = await fetchJob(jobId, identity);
      setArchiveJob(nextJob);
      if (TERMINAL_JOB_STATUSES.has(nextJob.status)) return nextJob;
      await new Promise((resolve) => window.setTimeout(resolve, 1200));
    }
  }, [identity]);

  const startArchive = async () => {
    if (!selectedRevision) return;
    setArchiveBusy(true);
    setArchiveJob(null);
    setError(null);
    try {
      const queued = await exportAuditArchive(
        selectedRevision.budget_revision_id,
        `R${selectedRevision.revision_no} 版本审计归档`,
        identity,
      );
      const terminal = await pollArchiveJob(queued.job_id);
      if (terminal.status !== "SUCCEEDED") {
        throw new ApiError(
          terminal.last_error_safe ?? "审计归档未生成",
          terminal.last_error_code ?? terminal.status,
          422,
        );
      }
      if (budget) await loadAudits(budget.annual_budget_id, appliedFilters);
    } catch (nextError) {
      setError(errorText(nextError));
    } finally {
      setArchiveBusy(false);
    }
  };

  const downloadArchive = async () => {
    const archiveId = archiveJob?.result?.audit_archive_id;
    if (typeof archiveId !== "string") return;
    setArchiveBusy(true);
    setError(null);
    try {
      const { blob, fileName } = await downloadAuditArchive(archiveId, identity);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = fileName;
      document.body.append(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 0);
    } catch (nextError) {
      setError(errorText(nextError));
    } finally {
      setArchiveBusy(false);
    }
  };

  const openWorkspace = useCallback(async (revisionId: string) => {
    setLoading(true);
    setError(null);
    try {
      const currentRevision = await fetchRevision(revisionId, identity);
      const currentBudget = await fetchAnnualBudget(currentRevision.annual_budget_id, identity);
      const [history, publications] = await Promise.all([
        fetchRevisions(currentBudget.annual_budget_id, identity),
        fetchAuditEvents(currentBudget.annual_budget_id, { eventType: "PUBLISHED", limit: 500 }, identity),
      ]);
      const ordered = [...history].sort((a, b) => b.revision_no - a.revision_no);
      const requestedAgainst = queryValue("against");
      const fallback = currentRevision.parent_revision_id
        ?? ordered.find((item) => item.budget_revision_id !== revisionId)?.budget_revision_id
        ?? "";
      const compareId = ordered.some((item) => item.budget_revision_id === requestedAgainst)
        && requestedAgainst !== revisionId ? requestedAgainst : fallback;
      setBudget(currentBudget);
      setRevisions(ordered);
      setSelectedId(revisionId);
      setAgainstId(compareId);
      setDetailComponent("");
      setDetailDiff(null);
      setArchiveJob(null);
      setPublishedIds(new Set(publications.items.map((event) => event.budget_revision_id).filter((id): id is string => Boolean(id))));
      window.localStorage.setItem("budget.userId", identity.userId);
      window.localStorage.setItem("budget.departmentId", identity.departmentId);
      const url = new URL(window.location.href);
      url.pathname = "/budget/audit";
      url.searchParams.set("revision", revisionId);
      if (compareId) url.searchParams.set("against", compareId);
      window.history.replaceState({}, "", url);
      await Promise.all([
        loadDiff(revisionId, compareId),
        fetchAuditEvents(
          currentBudget.annual_budget_id,
          { revisionId, limit: 50 },
          identity,
        ).then((page) => {
          setAuditEvents(page.items);
          setAuditCursor(page.next_cursor);
        }),
      ]);
    } catch (nextError) {
      setError(errorText(nextError));
    } finally {
      setLoading(false);
    }
  }, [identity, loadDiff]);

  useEffect(() => {
    const initial = queryValue("revision");
    if (initial) void openWorkspace(initial);
    // Read the initial route once; later navigation is handled explicitly.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const selectRevision = async (revisionId: string) => {
    if (!budget || revisionId === selectedId) return;
    setLoading(true);
    setError(null);
    try {
      const revision = revisionById.get(revisionId);
      const compareId = revision?.parent_revision_id
        ?? revisions.find((item) => item.budget_revision_id !== revisionId)?.budget_revision_id
        ?? "";
      setSelectedId(revisionId);
      setAgainstId(compareId);
      setDetailComponent("");
      setDetailDiff(null);
      setArchiveJob(null);
      const nextFilters = { ...appliedFilters, selectedRevisionOnly: true };
      setFilters(nextFilters);
      setAppliedFilters(nextFilters);
      const url = new URL(window.location.href);
      url.searchParams.set("revision", revisionId);
      if (compareId) url.searchParams.set("against", compareId);
      else url.searchParams.delete("against");
      window.history.replaceState({}, "", url);
      const [nextDiff, nextAudits] = await Promise.all([
        compareId ? fetchRevisionDiff(revisionId, compareId, identity) : Promise.resolve(null),
        fetchAuditEvents(budget.annual_budget_id, { revisionId, limit: 50 }, identity),
      ]);
      setDiff(nextDiff);
      setAuditEvents(nextAudits.items);
      setAuditCursor(nextAudits.next_cursor);
    } catch (nextError) {
      setError(errorText(nextError));
    } finally {
      setLoading(false);
    }
  };

  const changeAgainst = async (compareId: string) => {
    if (!selectedId) return;
    setAgainstId(compareId);
    setDetailComponent("");
    setDetailDiff(null);
    setError(null);
    try {
      await loadDiff(selectedId, compareId);
      const url = new URL(window.location.href);
      if (compareId) url.searchParams.set("against", compareId);
      else url.searchParams.delete("against");
      window.history.replaceState({}, "", url);
    } catch (nextError) {
      setError(errorText(nextError));
    }
  };

  const applyFilters = async () => {
    if (!budget) return;
    setError(null);
    setAppliedFilters(filters);
    try {
      await loadAudits(budget.annual_budget_id, filters);
    } catch (nextError) {
      setError(errorText(nextError));
    }
  };

  if (!budget || !selectedRevision) {
    if (revisionInput && !error) return <div className="decision-shell"><header className="decision-topbar"><a className="decision-brand" href="/budget/workspace"><span aria-hidden="true">智</span><strong>预算智解</strong></a></header><main className="decision-workspace" aria-busy="true"><div className="decision-skeleton decision-skeleton--progress" /><div className="decision-skeleton decision-skeleton--title" /><div className="decision-skeleton decision-skeleton--body" /><p role="status">正在读取变化记录…</p></main></div>;
    return (
      <div className="decision-shell"><header className="decision-topbar"><a className="decision-brand" href="/budget/workspace"><span aria-hidden="true">智</span><strong>预算智解</strong></a></header><main className="decision-workspace publication-entry"><p className="decision-kicker">年度预算 / 变化记录</p><h1>{error ? "变化记录读取失败" : "请先打开年度预算"}</h1>{error && <p role="alert">{error}</p>}<a className="decision-primary" href="/budget/workspace">返回年度预算</a>{revisionInput && <button className="publication-retry" onClick={() => void openWorkspace(revisionInput)}>重试读取</button>}</main></div>
    );
  }

  const visibleComponents = diff?.components.filter((item) => showUnchanged || item.change_type !== "UNCHANGED") ?? [];
  const relatedEvents = auditEvents;
  const milestoneRevisions = [selectedRevision, ...revisions.filter((item) => item.budget_revision_id !== selectedId && isMilestoneRevision(item, selectedId, publishedIds))].slice(0, 8);
  const hiddenCalculationCount = revisions.filter((item) => !isMilestoneRevision(item, selectedId, publishedIds)).length;
  const isPublished = budget.current_published_revision_id === selectedId || publishedIds.has(selectedId);
  const returnHref = isPublished ? `/budget/results?revision=${encodeURIComponent(selectedId)}` : `/budget/workspace?revision=${encodeURIComponent(selectedId)}&section=home`;
  const compareRevision = revisionById.get(againstId);
  const suggestedComparisons = [compareRevision, ...milestoneRevisions].filter((item, index, list): item is RevisionResponse => Boolean(item) && item?.budget_revision_id !== selectedId && list.findIndex((candidate) => candidate?.budget_revision_id === item?.budget_revision_id) === index).slice(0, 9);
  const businessChangeCount = visibleComponents.filter((item) => item.component_key !== "formal_confirmation_id").length;

  return (
    <div className="app-frame audit-app decision-shell">
      <a className="skip-link" href="#audit-main">跳到变化记录</a>
      <BudgetContextHeader fiscalYear={budget.fiscal_year} revisionNo={selectedRevision.revision_no} revisionId={selectedId} status={isPublished ? "已定版" : "草稿"} />

      <main className="audit-workspace" id="audit-main" aria-busy={loading}>
        <section className="audit-main">
          <div className="context-return"><a href={returnHref}>返回{isPublished ? "预算结果" : "年度预算"}</a><span>变化记录</span></div>
          <header className="audit-heading">
            <div>
              <p>FY{String(budget.fiscal_year).slice(-2)} / 精确修订</p>
              <h1>R{selectedRevision.revision_no} 相比 {compareRevision ? `R${compareRevision.revision_no}` : "所选版本"}</h1>

            </div>
            <a href="#audit-versions">选择其他版本</a>
          </header>

          {error && <div className="inline-error" role="alert"><strong>读取未完成</strong><span>{error}</span><button onClick={() => setError(null)}>×</button></div>}

          <section className="comparison-ribbon" aria-labelledby="comparison-title">
            <div className="comparison-ribbon__revision">
              <span>当前版本</span><strong>R{selectedRevision.revision_no}</strong><small>{commandLabel(selectedRevision.command_type)}</small>
            </div>
            <div className="comparison-ribbon__relation"><span>对比</span><i aria-hidden="true" /></div>
            <label>
              <span id="comparison-title">比较版本</span>
              <select value={againstId} onChange={(event) => void changeAgainst(event.target.value)}>
                <option value="">不比较</option>
                {suggestedComparisons.map((item) => <option key={item.budget_revision_id} value={item.budget_revision_id}>R{item.revision_no} · {commandLabel(item.command_type)}</option>)}
              </select>
            </label>
            <dl>
              <div><dt>变化</dt><dd>{diff?.changed_count ?? 0}</dd></div>
              {diff && <div><dt>业务变化</dt><dd>{businessChangeCount}</dd></div>}
              <div><dt>失效</dt><dd>{diff?.removed_count ?? 0}</dd></div>
              <div><dt>沿用</dt><dd>{diff?.unchanged_count ?? 0}</dd></div>
            </dl>
          </section>

          <details className="audit-evidence">
            <summary>技术依据与审计归档</summary>
            <label className="audit-all-compare"><span>比较任意修订</span><select value={againstId} onChange={(event) => void changeAgainst(event.target.value)}><option value="">不比较</option>{revisions.filter((item) => item.budget_revision_id !== selectedId).map((item) => <option key={item.budget_revision_id} value={item.budget_revision_id}>R{item.revision_no} · {commandLabel(item.command_type)}</option>)}</select></label>
            <div className="audit-heading__side">
              <div className="audit-heading__hash">
                <span>修订内容哈希</span>
                <code title={selectedRevision.revision_content_hash}>{shortId(selectedRevision.revision_content_hash, 10)}</code>
              </div>
              <section className={`archive-signout${archiveJob?.status === "SUCCEEDED" ? " is-sealed" : ""}`} aria-label="审计归档包">
                <span className="archive-signout__seal" aria-hidden="true">封</span>
                <div>
                  <strong>{archiveJob?.status === "SUCCEEDED" ? "归档已封存" : archiveBusy ? "正在汇集证据" : "签出审计归档"}</strong>
                  {archiveJob?.status === "SUCCEEDED" ? (
                    <small>{fileSizeLabel(archiveJob.result?.size_bytes)} · SHA {shortId(archiveJob.result?.content_hash, 6)}</small>
                  ) : (
                    <small>ZIP</small>
                  )}
                </div>
                {archiveJob?.status === "SUCCEEDED"
                  ? <button onClick={() => void downloadArchive()} disabled={archiveBusy}>下载归档包</button>
                  : <button onClick={() => void startArchive()} disabled={archiveBusy}>{archiveBusy ? "后台生成中…" : "生成归档包"}</button>}
              </section>
            </div>
          </details>

          <div className="audit-grid">
            <section className="change-ledger" aria-labelledby="change-ledger-title">
              <header>
                <div><p>组件版本差异</p><h2 id="change-ledger-title">这次改了什么</h2></div>
                <label className="unchanged-toggle"><input type="checkbox" checked={showUnchanged} onChange={(event) => setShowUnchanged(event.target.checked)} /><span>显示沿用项</span></label>
              </header>
              <div className="change-ledger__columns"><span>状态</span><span>业务内容</span><span>查看变化</span></div>
              <div className="change-ledger__body">
                {!diff ? <p className="audit-empty">请选择另一个修订作为比较基准。</p>
                  : visibleComponents.length === 0 ? <p className="audit-empty">两个修订的组件清单完全一致。打开“显示沿用项”可核对完整链路。</p>
                    : visibleComponents.map((item) => (
                      <ComponentDiffRow
                        key={item.component_key}
                        item={item}
                        selected={detailComponent === item.component_key}
                        onInspect={(componentKey) => void loadDetailDiff(componentKey)}
                      />
                    ))}
              </div>
              {diff && <details className="audit-diff-hash"><summary>查看差异校验依据</summary><code title={diff.content_hash}>{diff.content_hash}</code></details>}
            </section>

            <section className="audit-timeline" aria-labelledby="audit-timeline-title">
              <header>
                <div><p>追加式审计</p><h2 id="audit-timeline-title">谁在何时做了什么</h2></div>
                <span>{relatedEvents.length} 条</span>
              </header>
              <details className="audit-filters">
                <summary>筛选审计事件</summary>
                <div className="audit-filters__grid">
                  <label className="audit-filter-check"><input type="checkbox" checked={filters.selectedRevisionOnly} onChange={(event) => setFilters({ ...filters, selectedRevisionOnly: event.target.checked })} /><span>仅当前修订</span></label>
                  <label><span>事件</span><input value={filters.eventType} onChange={(event) => setFilters({ ...filters, eventType: event.target.value })} placeholder="如 PUBLISHED" /></label>
                  <label><span>对象</span><input value={filters.objectType} onChange={(event) => setFilters({ ...filters, objectType: event.target.value })} placeholder="如 RATE_PARAMETER" /></label>
                  <label><span>操作者</span><input value={filters.actorId} onChange={(event) => setFilters({ ...filters, actorId: event.target.value })} /></label>
                  <label><span>角色</span><select value={filters.actingRole} onChange={(event) => setFilters({ ...filters, actingRole: event.target.value })}><option value="">全部角色</option><option value="DATA_MAINTAINER">数据维护人员</option><option value="DEPARTMENT_BUDGET_OWNER">部门预算负责人</option><option value="PERMISSION_ADMIN">权限管理员</option></select></label>
                  <label><span>原因包含</span><input value={filters.reasonContains} onChange={(event) => setFilters({ ...filters, reasonContains: event.target.value })} /></label>
                  <label><span>开始日期</span><input type="date" value={filters.occurredFrom} onChange={(event) => setFilters({ ...filters, occurredFrom: event.target.value })} /></label>
                  <label><span>结束日期</span><input type="date" value={filters.occurredTo} onChange={(event) => setFilters({ ...filters, occurredTo: event.target.value })} /></label>
                </div>
                <div className="audit-filters__actions"><button onClick={() => { setFilters(EMPTY_FILTERS); }}>清空</button><button className="button--filter" onClick={() => void applyFilters()} disabled={auditLoading}>{auditLoading ? "筛选中…" : "应用筛选"}</button></div>
              </details>
              <div className="audit-timeline__body">
                {relatedEvents.length === 0 ? <p className="audit-empty">没有符合条件的审计事件。</p> : relatedEvents.map((event) => (
                  <AuditEventRow key={event.event_id} event={event} revisionNo={event.budget_revision_id ? revisionById.get(event.budget_revision_id)?.revision_no : undefined} />
                ))}
              </div>
              {auditCursor && <button className="load-more" disabled={auditLoading} onClick={() => budget && void loadAudits(budget.annual_budget_id, appliedFilters, true, auditCursor)}>{auditLoading ? "读取中…" : "加载更早记录"}</button>}
            </section>
          </div>

          {(detailComponent || detailLoading) && (
            <section className="detail-drawer" aria-labelledby="detail-drawer-title">
              <header>
                <div>
                  <p>逐行变化证据</p>
                  <h2 id="detail-drawer-title">{detailDiff?.component_label ?? COMPONENT_LABELS[detailComponent] ?? "读取明细"}</h2>

                </div>
                <button className="detail-drawer__close" onClick={() => { setDetailComponent(""); setDetailDiff(null); }} aria-label="关闭逐行差异">×</button>
              </header>
              <div className="detail-drawer__toolbar">
                <label>
                  <span>搜索业务维度或原因</span>
                  <input value={detailQuery} onChange={(event) => setDetailQuery(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") void loadDetailDiff(detailComponent); }} placeholder="校区、产品、季度或原因" />
                </label>
                <label className="detail-changed-toggle">
                  <input
                    type="checkbox"
                    checked={detailChangedOnly}
                    onChange={(event) => {
                      setDetailChangedOnly(event.target.checked);
                      void loadDetailDiff(detailComponent, event.target.checked, detailQuery);
                    }}
                  />
                  <span>只看业务变化</span>
                </label>
                <button onClick={() => void loadDetailDiff(detailComponent)} disabled={detailLoading}>{detailLoading ? "读取中…" : "筛选明细"}</button>
                {detailDiff && (
                  <dl>
                    <div><dt>业务变化</dt><dd>{detailDiff.changed_count}</dd></div>
                    <div><dt>新增</dt><dd>{detailDiff.added_count}</dd></div>
                    <div><dt>失效</dt><dd>{detailDiff.removed_count}</dd></div>
                    <div><dt>全部行</dt><dd>{detailDiff.total_count}</dd></div>
                  </dl>
                )}
              </div>
              <div className="detail-drawer__body" aria-live="polite">
                {detailLoading && !detailDiff ? <p className="audit-empty">正在按稳定键核对两个版本…</p>
                  : detailDiff?.items.length === 0 ? <p className="audit-empty">当前筛选下没有业务明细变化。关闭“只看业务变化”可核对沿用行。</p>
                    : detailDiff?.items.map((item) => <DetailDiffRow key={item.stable_key} item={item} />)}
              </div>
              {detailDiff?.next_cursor && (
                <button
                  className="load-more detail-drawer__more"
                  disabled={detailLoading}
                  onClick={() => void loadDetailDiff(detailComponent, detailChangedOnly, detailQuery, true, detailDiff.next_cursor ?? undefined)}
                >
                  {detailLoading ? "读取中…" : "加载更多明细"}
                </button>
              )}
              {detailDiff && <footer><span>明细差异指纹</span><code title={detailDiff.content_hash}>{shortId(detailDiff.content_hash, 14)}</code></footer>}
            </section>
          )}
        </section>
        <aside className="revision-spine" id="audit-versions" aria-label="选择预算版本">
          <div className="revision-spine__title"><p>FY{String(budget.fiscal_year).slice(-2)} · 版本</p><h2>重要记录</h2><span>当前查看 R{selectedRevision.revision_no}</span></div>
          <div className="revision-spine__list">
            {milestoneRevisions.map((item) => {
              const statuses = revisionStatus(item, budget, publishedIds);
              return <button key={item.budget_revision_id} className={item.budget_revision_id === selectedId ? "is-selected" : ""} onClick={() => void selectRevision(item.budget_revision_id)}><span className="revision-spine__number">R{item.revision_no}</span><span className="revision-spine__meta"><strong>{commandLabel(item.command_type)}</strong><small>{fullDate(item.created_at)}</small><i>{statuses.map((status) => <em className={`status-tag status-tag--${status.tone}`} key={status.label}>{status.label}</em>)}</i></span></button>;
            })}
          </div>
          <details className="audit-history-all"><summary>查看全部 {revisions.length} 次修订{hiddenCalculationCount ? `（含 ${hiddenCalculationCount} 次系统计算）` : ""}</summary><div>{revisions.map((item) => <button key={item.budget_revision_id} className={item.budget_revision_id === selectedId ? "is-selected" : ""} onClick={() => void selectRevision(item.budget_revision_id)}>R{item.revision_no} · {commandLabel(item.command_type)} <small>{fullDate(item.created_at)}</small></button>)}</div></details>
        </aside>
      </main>
    </div>
  );
}
