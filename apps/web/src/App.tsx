import { BudgetBrand } from "./BudgetBrand";
import { budgetNavigationEvent, budgetHistoryLeaveEvent } from "./budgetNavigation";
import { preloadBudgetModules } from "./budgetPreload";
import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { OpeningStudentsPage } from "./OpeningStudentsPage";
import { WorkspacePage } from "./WorkspacePage";
import { HistoricalBudgetPage } from "./HistoricalBudgetPage";
import { ForecastPage } from "./ForecastPage";
import { NextYearBudgetPage } from "./NextYearBudgetPage";
import { FY27Header } from "./FY27Header";
import {
  ApiError,
  confirmFormalPlan,
  fetchAnnualBudget,
  fetchAuditEvents,
  fetchJob,
  fetchLiveness,
  fetchPublicationReadiness,
  fetchRevision,
  publishBudget,
  type AnnualBudgetResponse,
  type ApiIdentity,
  type HealthStatus,
  type JobResponse,
  type PublicationCheck,
  type PublicationReadiness,
  type PublishedBudgetBaseline,
  type RevisionResponse,
} from "./api";
import { VersionAuditPage } from "./VersionAuditPage";
import { RebalancingPage } from "./RebalancingPage";
import { BudgetContextHeader } from "./BudgetContextHeader";
import { BudgetVersionIdentity, BudgetVersionTrace } from "./BudgetVersionIdentity";
import { versionLabel } from "./budgetVersionDisplay";
import { revisionCommandLabel } from "./budgetRevisionDisplay";
import "./publicationDesign.css";

const AiAgentPage = lazy(() => import("./AiChatPage").then(module => ({ default: module.AiChatPage })));
const BudgetCockpitPage = lazy(() => import("./BudgetCockpitPage"));

type LoadState = "idle" | "loading" | "ready" | "error";
type CheckFilter = "all" | "blocked";

const OWNER_ROLE = "DEPARTMENT_BUDGET_OWNER";
const TERMINAL_JOB_STATUSES = new Set(["SUCCEEDED", "INCOMPLETE", "FAILED", "CANCELLED"]);

const GROUP_LABELS: Record<string, string> = {
  VERSION: "版本与完整性",
  SOURCE: "数据快照",
  COMBINATION: "预算组合",
  ENTRY: "校区与入口",
  RATE: "续班率与新生占比",
  HEADCOUNT: "整数人次",
  RESOURCE_FINANCE: "班均与财务参数",
  DISCOUNT: "折扣参数",
  CALCULATION: "计算结果链",
  REVENUE: "结转收入与折扣校验",
  TARGET: "目标闭合",
};

const ACTUAL_LABELS: Record<string, string> = {
  current_draft_revision_id: "当前草稿修订",
  status: "快照状态",
  missing: "缺失组件",
  record_count: "记录数",
  enabled_count: "启用组合",
  unique_count: "唯一组合",
  entry_total: "入口总值",
  campus_total: "校区合计",
  detail_total: "明细合计",
  confirmed_count: "已确认",
  copy_exception_count: "跨财年异常",
  locked_count: "已锁定",
  confirmed: "已确认",
  current: "结果链为当前",
  headcount_publishable: "人数可发布",
  class_size_publishable: "班均可发布",
  revenue_publishable: "结转收入可发布",
  closure_status: "闭合状态",
  department_count: "部门目标数",
  track_count: "赛道目标数",
  failed_targets: "未闭合目标",
};

const PUBLICATION_TASKS = [
  { no: "1", label: "目标与范围", short: "目标", section: "source", groups: ["SOURCE", "COMBINATION"] },
  { no: "2", label: "入口人数", short: "入口", section: "entry-total", groups: ["ENTRY"] },
  { no: "3", label: "招生参数", short: "参数", section: "rates", groups: ["RATE"] },
  { no: "4", label: "资源与财务", short: "财务", section: "resources", groups: ["RESOURCE_FINANCE", "DISCOUNT"] },
] as const;

const REPAIR_SECTIONS: Record<string, string> = {
  CURRENT_DRAFT_REVISION: "source",
  REQUIRED_COMPONENTS_PRESENT: "resources",
  SOURCE_SNAPSHOT_FROZEN: "source",
  BUDGET_COMBINATIONS_CONFIRMED: "combinations",
  BUDGET_COMBINATION_KEYS_EXACT: "combinations",
  CAMPUS_ENTRY_CONFIRMED_AND_BALANCED: "campus",
  ENTRY_SCOPE_CONFIRMED: "entry-scope",
  RATE_BOUNDARIES_CONFIRMED: "rates",
  PLANNED_NEW_STUDENTS_LOCKED: "planned",
  RESOURCE_FINANCE_CONFIRMED: "resources",
  DISCOUNT_PARAMETERS_CONFIRMED: "discount",
  CALCULATION_RESULT_CHAIN_CURRENT: "results",
  HEADCOUNT_AND_CLASS_SIZE_PUBLISHABLE: "results",
  REVENUE_AND_DISCOUNT_PUBLISHABLE: "results",
  ALL_TARGETS_WITHIN_TOLERANCE: "results",
};

function repairUrl(check: PublicationCheck, revisionId: string): string {
  if (check.code === "OPENING_CALCULATION_READY") {
    return `/budget/opening?revision=${encodeURIComponent(revisionId)}`;
  }
  const section = REPAIR_SECTIONS[check.code]
    ?? ({ SOURCE: "source", COMBINATION: "combinations", ENTRY: "entry-total", RATE: "rates", RESOURCE_FINANCE: "resources", DISCOUNT: "discount", HEADCOUNT: "results", CALCULATION: "results", REVENUE: "results", TARGET: "results" } as Record<string, string>)[check.group]
    ?? "source";
  return `/budget/workspace?revision=${encodeURIComponent(revisionId)}&section=${encodeURIComponent(section)}`;
}

function readSetting(key: string, fallback: string): string {
  const stored = window.localStorage.getItem(key)?.trim();
  return stored || fallback;
}

function initialRevisionId(): string {
  return new URLSearchParams(window.location.search).get("revision") ?? "";
}

function shortId(value: string | null | undefined, size = 8): string {
  if (!value) return "—";
  if (value.length <= size * 2 + 1) return value;
  return `${value.slice(0, size)}…${value.slice(-size)}`;
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

function readableValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "未提供";
  if (typeof value === "boolean") return value ? "是" : "否";
  if (Array.isArray(value)) return value.length ? value.map(String).join("、") : "无";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    return `${error.message}（${error.code}${error.traceId ? ` · ${shortId(error.traceId)}` : ""}）`;
  }
  if (error instanceof Error) return error.message;
  return "发生未知错误";
}

function StatusGlyph({ passed }: { passed: boolean }) {
  return passed ? (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <path d="m5 10.4 3.1 3.2L15.4 6" />
    </svg>
  ) : (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <path d="M10 5.2v5.4M10 14.7h.01" />
    </svg>
  );
}

function RefreshGlyph() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <path d="M15.7 8A6 6 0 1 0 16 11.8M15.7 4.7V8h-3.3" />
    </svg>
  );
}

function CheckRow({ check, revisionId }: { check: PublicationCheck; revisionId: string }) {
  const repairHref = repairUrl(check, revisionId);

  return (
    <article className={`check-row ${check.passed ? "check-row--passed" : "check-row--blocked"}`}>
      <div className="check-row__mark" aria-label={check.passed ? "通过" : "未通过"}>
        <StatusGlyph passed={check.passed} />
      </div>
      <div className="check-row__body">
        <div className="check-row__heading">
          <h4 className="publication-check-title">{check.rule}</h4>
          <span>{check.passed ? "通过" : "需处理"}</span>
        </div>
        <div className="check-row__foot">
          {!check.passed && (
            <a href={repairHref} aria-label={`前往处理：${check.rule}`}>
              去处理 <span aria-hidden="true">↗</span>
            </a>
          )}
        </div>
        <details className="check-row__evidence">
          <summary>查看检查依据</summary>
          <dl className="actual-values">
            {Object.entries(check.actual).map(([key, value]) => (
              <div key={key}>
                <dt>{ACTUAL_LABELS[key] ?? key}</dt>
                <dd title={readableValue(value)}>{readableValue(value)}</dd>
              </div>
            ))}
          </dl>
          <code>{check.code}</code>
        </details>
      </div>
    </article>
  );
}

function BudgetOverviewRedirect() {
  useEffect(() => {
    const url = new URL(window.location.href);
    url.pathname = "/budget/workspace";
    url.searchParams.set("section", "results");
    if (!url.searchParams.has("revision")) {
      const activeRevision = localStorage.getItem("budget.activeRevision");
      if (activeRevision) url.searchParams.set("revision", activeRevision);
    }
    window.location.replace(url);
  }, []);
  return <main className="decision-workspace" role="status">正在打开预算概览…</main>;
}

export function App() {
  const [path, setPath] = useState(window.location.pathname);
  const currentPath = useRef(path);
  currentPath.current = path;
  useEffect(() => {
    const route = (event: Event) => {
      if (event.type === "popstate" && currentPath.current !== window.location.pathname && !window.dispatchEvent(new Event(budgetHistoryLeaveEvent, { cancelable: true }))) return;
      setPath(window.location.pathname);
    };
    window.addEventListener(budgetNavigationEvent, route);
    window.addEventListener("popstate", route);
    preloadBudgetModules();
    return () => { window.removeEventListener(budgetNavigationEvent, route); window.removeEventListener("popstate", route); };
  }, []);
  if (path === "/budget/ai") return <Suspense fallback={<main className="decision-workspace" aria-busy="true"><h1 className="budget-page-title">AI智解</h1><p role="status">正在打开对话工作区…</p></main>}><AiAgentPage /></Suspense>;
  if (path === "/budget/forecast") return <ForecastPage />;
  if (path === "/budget/cockpit") return <Suspense fallback={<div className="decision-shell budget-shell"><FY27Header active="cockpit"><span>只读汇总</span></FY27Header><main className="decision-workspace" aria-busy="true"><h1 className="budget-page-title">预算驾驶舱</h1><p role="status">正在汇集已保存成果…</p></main></div>}><BudgetCockpitPage /></Suspense>;
  if (path === "/budget/next-year") return <NextYearBudgetPage />;
  if (path === "/budget/workspace" && new URLSearchParams(location.search).get("archive") === "fy27") return <HistoricalBudgetPage />;
  if (path === "/" || path === "/budget/workspace") return <WorkspacePage />;
  if (path === "/budget/revisions") return <VersionAuditPage />;
  if (window.location.pathname.startsWith("/budget/opening")) return <OpeningStudentsPage />;
  if (window.location.pathname.startsWith("/budget/rebalancing")) {
    return <RebalancingPage />;
  }
  if (window.location.pathname.startsWith("/budget/audit")) {
    return <VersionAuditPage />;
  }
  if (window.location.pathname.startsWith("/budget/results")) return <BudgetOverviewRedirect />;
  if (path === "/budget/publication") return <PublicationPage />;
  const section = ({ "/budget/targets": "targets", "/budget/combinations": "combinations", "/budget/entry": "entry-total", "/budget/entry-allocation": "campus", "/budget/entry-scope": "entry-scope", "/budget/rates": "rates", "/budget/resources": "resources", "/budget/resource-finance": "resources", "/budget/discounts": "discount", "/budget/headcount": "results", "/budget/class-size": "results", "/budget/revenue": "results" } as Record<string, string>)[path];
  if (section) { const url = new URL(location.href); url.pathname = "/budget/workspace"; url.searchParams.set("section", section); history.replaceState({}, "", url); return <WorkspacePage />; }
  return <main className="workspace"><h1>此页面尚未提供</h1><a href="/budget/workspace">返回年度预算工作区</a></main>;
}

function PublicationPage() {
  const [health, setHealth] = useState<HealthStatus | "loading" | "error">("loading");
  const [revisionInput, setRevisionInput] = useState(initialRevisionId);
  const [activeRevisionId, setActiveRevisionId] = useState("");
  const [identity] = useState<ApiIdentity>(() => ({
    userId: readSetting("budget.userId", "budget-owner"),
    departmentId: readSetting("budget.departmentId", "zhihui-learning"),
    roles: [OWNER_ROLE],
  }));
  const [revision, setRevision] = useState<RevisionResponse | null>(null);
  const [annualBudget, setAnnualBudget] = useState<AnnualBudgetResponse | null>(null);
  const [readiness, setReadiness] = useState<PublicationReadiness | null>(null);
  const [published, setPublished] = useState<PublishedBudgetBaseline | null>(null);
  const [loadState, setLoadState] = useState<LoadState>("idle");
  const [actionState, setActionState] = useState<"idle" | "confirming" | "publishing">("idle");
  const [job, setJob] = useState<JobResponse | null>(null);
  const [filter, setFilter] = useState<CheckFilter>("blocked");
  const [formalReason, setFormalReason] = useState("确认当前完整预算方案并进入发布检查");
  const [publicationNote, setPublicationNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const requestCounter = useRef(0);
  const pageTitleRef = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    const controller = new AbortController();
    fetchLiveness(controller.signal)
      .then(({ status }) => setHealth(status))
      .catch(() => setHealth("error"));
    return () => controller.abort();
  }, []);

  const loadRevision = useCallback(async (revisionId: string, nextIdentity = identity) => {
    const requestId = ++requestCounter.current;
    setLoadState("loading");
    setError(null);
    try {
      const nextRevision = await fetchRevision(revisionId, nextIdentity);
      const nextBudget = await fetchAnnualBudget(nextRevision.annual_budget_id, nextIdentity);
      if (requestId !== requestCounter.current) return;
      const wasPublished = nextBudget.current_published_revision_id === revisionId
        || (typeof nextRevision.component_manifest.formal_confirmation_id === "string"
          && (await fetchAuditEvents(nextBudget.annual_budget_id, { revisionId, eventType: "PUBLISHED" }, nextIdentity)).items.length > 0);
      if (requestId !== requestCounter.current) return;
      if (wasPublished) {
        window.location.replace(`/budget/workspace?revision=${encodeURIComponent(revisionId)}&section=results&view=record`);
        return;
      }
      const nextReadiness = await fetchPublicationReadiness(revisionId, nextIdentity);
      if (requestId !== requestCounter.current) return;
      setRevision(nextRevision);
      setReadiness(nextReadiness);
      setAnnualBudget(nextBudget);
      setActiveRevisionId(revisionId);
      setRevisionInput(revisionId);
      setLoadState("ready");
      window.localStorage.setItem("budget.userId", nextIdentity.userId);
      window.localStorage.setItem("budget.departmentId", nextIdentity.departmentId);
      const url = new URL(window.location.href);
      url.searchParams.set("revision", revisionId);
      window.history.replaceState({}, "", url);
      window.requestAnimationFrame(() => {
        window.scrollTo({ top: 0, behavior: "auto" });
        pageTitleRef.current?.focus({ preventScroll: true });
      });
    } catch (nextError) {
      if (requestId !== requestCounter.current) return;
      setLoadState("error");
      setError(errorMessage(nextError));
    }
  }, [identity]);

  useEffect(() => {
    const revisionId = initialRevisionId();
    if (revisionId) void loadRevision(revisionId);
    // The initial URL is intentionally read once.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const refresh = useCallback(() => {
    if (activeRevisionId) void loadRevision(activeRevisionId);
  }, [activeRevisionId, loadRevision]);

  const pollJob = useCallback(async (jobId: string): Promise<JobResponse> => {
    for (;;) {
      const nextJob = await fetchJob(jobId, identity);
      setJob(nextJob);
      if (TERMINAL_JOB_STATUSES.has(nextJob.status)) return nextJob;
      await new Promise((resolve) => window.setTimeout(resolve, 1200));
    }
  }, [identity]);

  const handleFormalConfirmation = async () => {
    if (!revision || !formalReason.trim()) return;
    setActionState("confirming");
    setError(null);
    setJob(null);
    try {
      const queued = await confirmFormalPlan(revision.budget_revision_id, formalReason.trim(), identity);
      const terminal = await pollJob(queued.job_id);
      if (terminal.status !== "SUCCEEDED") {
        throw new ApiError(
          terminal.last_error_safe ?? "完整复算未成功，正式方案尚未确认",
          terminal.last_error_code ?? terminal.status,
          422,
        );
      }
      const finalRevisionId = terminal.result?.final_revision_id;
      if (typeof finalRevisionId !== "string") {
        throw new Error("复算成功，但作业结果缺少最终修订 ID");
      }
      await loadRevision(finalRevisionId);
    } catch (nextError) {
      setError(errorMessage(nextError));
    } finally {
      setActionState("idle");
    }
  };

  const handlePublish = async () => {
    if (!revision) return;
    setActionState("publishing");
    setError(null);
    try {
      const result = await publishBudget(revision.budget_revision_id, publicationNote, identity);
      setPublished(result);
      window.location.replace(`/budget/workspace?revision=${encodeURIComponent(result.published_revision_id)}&section=results`);
    } catch (nextError) {
      setError(errorMessage(nextError));
    } finally {
      setActionState("idle");
    }
  };

  const groupedChecks = useMemo(() => {
    if (!readiness) return [];
    const visible = filter === "blocked" ? readiness.checks.filter((check) => !check.passed) : readiness.checks;
    const groups = new Map<string, PublicationCheck[]>();
    for (const check of visible) {
      groups.set(check.group, [...(groups.get(check.group) ?? []), check]);
    }
    return [...groups.entries()];
  }, [filter, readiness]);

  if (!revision || !readiness || !annualBudget) {
    if (revisionInput && loadState !== "error") {
      return <div className="decision-shell publication-loading"><header className="decision-topbar"><a className="decision-brand" href="/budget/workspace"><BudgetBrand /></a></header><main className="decision-workspace" aria-busy="true"><div className="decision-skeleton decision-skeleton--progress" /><div className="decision-skeleton decision-skeleton--title" /><div className="decision-skeleton decision-skeleton--body" /><p role="status">正在读取发布状态…</p></main></div>;
    }
    return (
      <div className="decision-shell publication-loading"><header className="decision-topbar"><a className="decision-brand" href="/budget/workspace"><BudgetBrand /></a></header><main className="decision-workspace publication-entry"><p className="decision-kicker">年度预算 / 确认与发布</p><h1 className="budget-page-title">{error ? "发布状态读取失败" : "请先打开年度预算"}</h1>{error && <p role="alert">{error}</p>}<a className="decision-primary" href="/budget/workspace">返回年度预算</a>{revisionInput && <button className="publication-retry" onClick={() => void loadRevision(revisionInput, identity)}>重试读取</button>}</main></div>
    );
  }

  const isFormallyConfirmed = typeof revision.component_manifest.formal_confirmation_id === "string";
  const isPublished = published?.published_revision_id === revision.budget_revision_id
    || annualBudget.current_published_revision_id === revision.budget_revision_id;
  const publishedWithAuditMismatch = isPublished && readiness.status === "BLOCKED";
  const passedCount = readiness.checks.length - readiness.blocker_count;
  const canConfirm = readiness.status === "READY" && !isFormallyConfirmed && !isPublished
    && formalReason.trim().length > 0 && actionState === "idle";
  const canPublish = readiness.status === "READY" && isFormallyConfirmed && !isPublished
    && actionState === "idle";
  const declaration = publishedWithAuditMismatch
    ? `正式版本复核异常：${readiness.blocker_count} 项`
    : isPublished
      ? "年度预算基线已发布"
    : readiness.status === "BLOCKED"
      ? `尚有 ${readiness.blocker_count} 项必须处理`
      : isFormallyConfirmed
        ? "已正式确认，可以发布"
        : "硬约束全部通过，等待正式确认";
  const declarationTone = publishedWithAuditMismatch
    ? "blocked"
    : isPublished
      ? "published"
      : readiness.status === "BLOCKED"
        ? "blocked"
      : "ready";
  const stageDone = (groups: readonly string[]) => readiness.checks
    .filter(check => groups.includes(check.group))
    .every(check => check.passed);

  return (
    <div className="app-frame publication-app publication-app--decision budget-shell">
      <a className="skip-link" href="#publication-main">跳到发布内容</a>
      <BudgetContextHeader fiscalYear={annualBudget.fiscal_year} revisionNo={revision.revision_no} revisionId={activeRevisionId} status={isPublished ? "已定版" : health === "ok" ? "待发布" : "本地模式"} />

      <main className="decision-workspace publication-decision-workspace" id="publication-main">
        <nav className="decision-progress" aria-label="预算任务进度">
          {PUBLICATION_TASKS.map(task => {
            const done = stageDone(task.groups);
            return <a key={task.no} className={done ? "is-done" : "is-todo"} href={`/budget/workspace?revision=${encodeURIComponent(activeRevisionId)}&section=${task.section}`} aria-label={`任务 ${task.no}：${task.label}，${done ? "已完成" : "待处理"}`}><span>{task.no}</span><strong>{task.label}</strong><em className="decision-progress-short">{task.short}</em></a>;
          })}
          <a className={`is-active ${isPublished ? "is-done" : "is-active"}`} aria-current="page" href={`/budget/publication?revision=${encodeURIComponent(activeRevisionId)}`} aria-label={`任务 5：审阅与发布，${isPublished ? "已完成" : "正在处理"}`}><span>5</span><strong>审阅与发布</strong><em className="decision-progress-short">发布</em></a>
        </nav>
        <header className="publication-focus__bar">
          <div><a href={`/budget/workspace?revision=${encodeURIComponent(activeRevisionId)}&section=home`}>返回年度预算</a><strong>确认与发布</strong></div>
          <button className="publication-refresh" onClick={refresh} disabled={loadState === "loading"} aria-label="刷新检查结果"><RefreshGlyph /><span>{loadState === "loading" ? "刷新中" : "刷新"}</span></button>
        </header>
        <div className="publication-heading">
          <div><p>任务 5 / 5</p><h1 className="budget-page-title" ref={pageTitleRef} tabIndex={-1}>确认并定版</h1></div>
          <a href={`/budget/audit?revision=${encodeURIComponent(activeRevisionId)}`}>版本记录</a>
        </div>

        <BudgetVersionIdentity sequence={revision.revision_no} createdAt={revision.created_at} reason={revisionCommandLabel(revision.command_type)} viewing readonly={isPublished || annualBudget.current_draft_revision_id !== activeRevisionId} />
        <BudgetVersionTrace code={`R${revision.revision_no}`} id={revision.budget_revision_id} createdAt={revision.created_at} createdBy={revision.created_by} />
        {annualBudget.current_draft_revision_id && annualBudget.current_draft_revision_id !== activeRevisionId && <p className="budget-status">正在查看{isPublished ? "已定版预算，只读" : "历史版本，只读"} · {versionLabel(revision.revision_no)}。<a href={`/budget/publication?revision=${encodeURIComponent(annualBudget.current_draft_revision_id)}`}>返回最新草稿</a></p>}

        <section className={`declaration declaration--${declarationTone}`} aria-live="polite">
          <div className="declaration__signal" aria-hidden="true">
            {isPublished ? "定" : readiness.status === "BLOCKED" ? readiness.blocker_count : "✓"}
          </div>
          <div className="declaration__copy">
            <p>{publishedWithAuditMismatch ? "正式版本复核" : isPublished ? "正式版本" : "发布门禁结论"}</p>
            <h2 className="budget-section-title">{declaration}</h2>
          </div>
          <dl className="declaration__facts">
            <div><dt>通过</dt><dd>{passedCount}<small> / {readiness.checks.length}</small></dd></div>
            <div><dt>版本</dt><dd>{versionLabel(revision.revision_no)}</dd></div>
            <div><dt>财年</dt><dd>FY{String(annualBudget.fiscal_year).slice(-2)}</dd></div>
          </dl>
        </section>

        {isPublished && !publishedWithAuditMismatch && <div className="publication-complete"><a className="decision-primary" href={`/budget/results?revision=${encodeURIComponent(activeRevisionId)}`}>查看预算结果</a></div>}

        {error && (
          <div className="inline-error" role="alert">
            <strong>操作未完成</strong>
            <span>{error}</span>
            <button onClick={() => setError(null)} aria-label="关闭错误提示">×</button>
          </div>
        )}

        {job && actionState === "confirming" && (
          <section className="job-strip" aria-live="polite">
            <span className="job-strip__pulse" aria-hidden="true" />
            <div><strong>正在执行完整复算</strong><span>人数 → 班均 → 结转收入 → 目标闭合</span></div>
            <code>{job.status} · 尝试 {job.attempt_no}</code>
          </section>
        )}

        <details className="publication-deep" key={isPublished ? "published" : "editing"} open={!isPublished || publishedWithAuditMismatch}>
          <summary>{isPublished && !publishedWithAuditMismatch ? "查看完整检查与确认记录" : "当前检查与操作"}</summary>
        <div className="work-grid publication-work-grid">
          <section className="ledger budget-surface" aria-labelledby="ledger-title">
            <div className="ledger__toolbar">
              <div>
                <h2 className="budget-section-title" id="ledger-title">逐项核验</h2>
              </div>
              <div className="segmented" aria-label="筛选检查项">
                <button className={filter === "blocked" ? "is-active" : ""} onClick={() => setFilter("blocked")}>阻断 {readiness.blocker_count}</button>
                <button className={filter === "all" ? "is-active" : ""} onClick={() => setFilter("all")}>全部 {readiness.checks.length}</button>
              </div>
            </div>

            <div className="ledger__body">
              {groupedChecks.length === 0 ? (
                <div className="empty-blockers"><StatusGlyph passed /><div><strong>没有发布阻断</strong></div><button className="button" onClick={() => setFilter("all")}>查看全部 {readiness.checks.length} 项</button></div>
              ) : groupedChecks.map(([group, checks]) => (
                <section className="check-group" key={group} aria-labelledby={`group-${group}`}>
                  <div className="check-group__title">
                    <h3 className="budget-subsection-title" id={`group-${group}`}>{GROUP_LABELS[group] ?? group}</h3>
                    <span>{checks.filter((item) => item.passed).length} / {checks.length}</span>
                  </div>
                  <div className="check-group__rows">
                    {checks.map((check) => <CheckRow key={check.code} check={check} revisionId={activeRevisionId} />)}
                  </div>
                </section>
              ))}
            </div>
          </section>

          <aside className="docket budget-surface" aria-labelledby="docket-title">
            <div className="docket__header">
              <h2 className="budget-section-title" id="docket-title">正式方案与发布</h2>
            </div>

            <details className="revision-ticket publication-technical">
              <summary>查看修订与规则依据</summary>
              <div>
                <span>当前修订</span>
                <strong>{versionLabel(revision.revision_no)}</strong>
              </div>
              <dl>
                <div><dt>修订 ID</dt><dd title={revision.budget_revision_id}>{shortId(revision.budget_revision_id)}</dd></div>
                <div><dt>输入指纹</dt><dd title={readiness.input_fingerprint}>{shortId(readiness.input_fingerprint, 6)}</dd></div>
                <div><dt>规则版本</dt><dd>{readiness.gate_rule_version}</dd></div>
                <div><dt>生成时间</dt><dd>{formatDate(revision.created_at)}</dd></div>
              </dl>
            </details>

            <ol className="approval-steps">
              <li className={readiness.status === "READY" ? "is-done" : "is-current"}>
                <span>1</span><div><strong>硬约束检查</strong><small>{readiness.status === "READY" ? "全部通过" : `${readiness.blocker_count} 项待处理`}</small></div>
              </li>
              <li className={isFormallyConfirmed ? "is-done" : readiness.status === "READY" ? "is-current" : ""}>
                <span>2</span><div><strong>正式方案确认</strong><small>{isFormallyConfirmed ? "确认有效" : "触发完整复算"}</small></div>
              </li>
              <li className={isPublished ? "is-done" : isFormallyConfirmed ? "is-current" : ""}>
                <span>3</span><div><strong>发布年度基线</strong><small>{isPublished ? "不可变版本已生成" : "确认后开放"}</small></div>
              </li>
            </ol>

            {!isFormallyConfirmed && !isPublished && (
              <div className="action-panel">
                <div className="confirmation-consequence"><strong>确认会重新计算整套预算</strong></div>
                <label>
                  <span>正式确认原因 <b>必填</b></span>
                  <textarea value={formalReason} onChange={(event) => setFormalReason(event.target.value)} maxLength={500} rows={3} />
                </label>
                <button className="button button--primary button--wide" onClick={() => void handleFormalConfirmation()} disabled={!canConfirm}>
                  {actionState === "confirming" ? "正在复算并确认…" : "重新计算并正式确认"}
                </button>
              </div>
            )}

            {isFormallyConfirmed && !isPublished && (
              <div className="action-panel action-panel--publish">
                <div className="formal-stamp"><span>正式方案</span><strong>已确认</strong></div>
                <label>
                  <span>发布说明 <i>选填</i></span>
                  <textarea value={publicationNote} onChange={(event) => setPublicationNote(event.target.value)} maxLength={500} rows={3} placeholder="记录本次发布需要说明的事项" />
                </label>
                <button className="button button--publish button--wide" onClick={() => void handlePublish()} disabled={!canPublish}>
                  {actionState === "publishing" ? "正在发布…" : "发布年度预算基线"}
                </button>

              </div>
            )}

            {isPublished && (
              <div className="published-receipt">
                <div className="formal-stamp formal-stamp--published"><span>FY{String(annualBudget.fiscal_year).slice(-2)}</span><strong>已定版</strong></div>
                {publishedWithAuditMismatch && <p role="alert">该正式版本按当前门禁复核存在 {readiness.blocker_count} 项异常，仅可查看并进入审计处理。</p>}
                {published && <small>发布于 {formatDate(published.published_at)} · {published.published_by}</small>}
                <a className="button button--primary published-result-link" href={`/budget/results?revision=${encodeURIComponent(revision.budget_revision_id)}`}>查看 FY{String(annualBudget.fiscal_year).slice(-2)} 预算结果</a>
              </div>
            )}

            {readiness.planned_new_students.length > 0 && (
              <details className="planned-summary">
                <summary>
                  <span>计划新生人次确认摘要</span>
                  <strong>{readiness.planned_new_students.length} 条</strong>
                </summary>
                <div className="planned-summary__list">
                  {readiness.planned_new_students.map((item, index) => (
                    <article key={`${item.fiscal_season}-${item.track}-${item.product}-${item.campus}-${index}`}>
                      <div><strong>{item.campus} · {item.product}</strong><span>{item.planned_new_students} 人</span></div>
                      <p>{item.fiscal_season} · {item.track} · {item.grade} · {item.subject}</p>
                      <small>{item.reason}</small>
                    </article>
                  ))}
                </div>
              </details>
            )}

            <button className="change-revision" onClick={() => {
              requestCounter.current += 1;
              setRevision(null);
              setAnnualBudget(null);
              setReadiness(null);
              setActiveRevisionId("");
              setLoadState("idle");
            }}>切换预算修订</button>
          </aside>
        </div>
        </details>
      </main>
    </div>
  );
}
