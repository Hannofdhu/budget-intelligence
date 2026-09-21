import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { OpeningStudentsPage } from "./OpeningStudentsPage";
import { WorkspacePage } from "./WorkspacePage";

import {
  ApiError,
  confirmFormalPlan,
  fetchAnnualBudget,
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
  REVENUE: "收入与折扣校验",
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
  revenue_publishable: "收入可发布",
  closure_status: "闭合状态",
  department_count: "部门目标数",
  track_count: "赛道目标数",
  failed_targets: "未闭合目标",
};

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
  const repairUrl = check.repair_route.startsWith("/budget/")
    ? `${check.repair_route}?revision=${encodeURIComponent(revisionId)}`
    : check.repair_route;

  return (
    <article className={`check-row ${check.passed ? "check-row--passed" : "check-row--blocked"}`}>
      <div className="check-row__mark" aria-label={check.passed ? "通过" : "未通过"}>
        <StatusGlyph passed={check.passed} />
      </div>
      <div className="check-row__body">
        <div className="check-row__heading">
          <h3>{check.rule}</h3>
          <span>{check.passed ? "通过" : "需处理"}</span>
        </div>
        <dl className="actual-values">
          {Object.entries(check.actual).map(([key, value]) => (
            <div key={key}>
              <dt>{ACTUAL_LABELS[key] ?? key}</dt>
              <dd title={readableValue(value)}>{readableValue(value)}</dd>
            </div>
          ))}
        </dl>
        <div className="check-row__foot">
          <code>{check.code}</code>
          {!check.passed && (
            <a href={repairUrl} aria-label={`前往处理：${check.rule}`}>
              前往处理 <span aria-hidden="true">↗</span>
            </a>
          )}
        </div>
      </div>
    </article>
  );
}

function EmptyConnection({
  revisionId,
  identity,
  onRevisionChange,
  onIdentityChange,
  onConnect,
  loadState,
}: {
  revisionId: string;
  identity: ApiIdentity;
  onRevisionChange: (value: string) => void;
  onIdentityChange: (value: ApiIdentity) => void;
  onConnect: () => void;
  loadState: LoadState;
}) {
  return (
    <main className="connect-shell">
      <div className="connect-copy">
        <p className="kicker">FY27 · 年度预算基线</p>
        <h1>把发布条件<br />摊在桌面上。</h1>
        <p>连接一个预算修订，逐项核对硬约束，再完成正式方案确认与发布。</p>
      </div>
      <section className="connect-form" aria-labelledby="connect-title">
        <p className="section-index">P11</p>
        <h2 id="connect-title">进入发布检查</h2>
        <label>
          <span>预算修订 ID</span>
          <input
            value={revisionId}
            onChange={(event) => onRevisionChange(event.target.value.trim())}
            placeholder="粘贴 budget_revision_id"
            autoComplete="off"
          />
        </label>
        <div className="connect-form__row">
          <label>
            <span>部门 ID</span>
            <input
              value={identity.departmentId}
              onChange={(event) => onIdentityChange({ ...identity, departmentId: event.target.value })}
              placeholder="zhihui-learning"
              autoComplete="off"
            />
          </label>
          <label>
            <span>当前用户</span>
            <input
              value={identity.userId}
              onChange={(event) => onIdentityChange({ ...identity, userId: event.target.value })}
              placeholder="budget-owner"
              autoComplete="off"
            />
          </label>
        </div>
        <p className="connect-note">以“部门预算负责人”角色进入；身份来自本地开发环境请求头。</p>
        <button
          className="button button--primary button--wide"
          onClick={onConnect}
          disabled={!revisionId || !identity.departmentId.trim() || !identity.userId.trim() || loadState === "loading"}
        >
          {loadState === "loading" ? "正在读取…" : "读取当前修订"}
        </button>
      </section>
    </main>
  );
}

export function App() {
  const path = window.location.pathname;
  if (path === "/" || path === "/budget/workspace") return <WorkspacePage />;
  if (path === "/budget/revisions") return <VersionAuditPage />;
  if (window.location.pathname.startsWith("/budget/opening")) return <OpeningStudentsPage />;
  if (window.location.pathname.startsWith("/budget/rebalancing")) {
    return <RebalancingPage />;
  }
  if (window.location.pathname.startsWith("/budget/audit")) {
    return <VersionAuditPage />;
  }
  if (path === "/budget/publication") return <PublicationPage />;
  const section = ({ "/budget/targets": "targets", "/budget/combinations": "combinations", "/budget/entry": "entry-total", "/budget/entry-allocation": "campus", "/budget/entry-scope": "entry-scope", "/budget/rates": "rates", "/budget/resources": "resources", "/budget/resource-finance": "resources", "/budget/discounts": "discount", "/budget/headcount": "results", "/budget/class-size": "results", "/budget/revenue": "results" } as Record<string, string>)[path];
  if (section) { const url = new URL(location.href); url.pathname = "/budget/workspace"; url.searchParams.set("section", section); history.replaceState({}, "", url); return <WorkspacePage />; }
  return <main className="workspace"><h1>此页面尚未提供</h1><p>请从年度预算工作区选择正确操作；不会把未知页面当成发布检查。</p><a href="/budget/workspace">返回年度预算工作区</a></main>;
}

function PublicationPage() {
  const [health, setHealth] = useState<HealthStatus | "loading" | "error">("loading");
  const [revisionInput, setRevisionInput] = useState(initialRevisionId);
  const [activeRevisionId, setActiveRevisionId] = useState("");
  const [identity, setIdentity] = useState<ApiIdentity>(() => ({
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
  const [filter, setFilter] = useState<CheckFilter>("all");
  const [formalReason, setFormalReason] = useState("确认当前完整预算方案并进入发布检查");
  const [publicationNote, setPublicationNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const requestCounter = useRef(0);

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
      const [nextRevision, nextReadiness] = await Promise.all([
        fetchRevision(revisionId, nextIdentity),
        fetchPublicationReadiness(revisionId, nextIdentity),
      ]);
      const nextBudget = await fetchAnnualBudget(nextRevision.annual_budget_id, nextIdentity);
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
      await loadRevision(result.published_revision_id);
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
    return (
      <>
        <EmptyConnection
          revisionId={revisionInput}
          identity={identity}
          onRevisionChange={setRevisionInput}
          onIdentityChange={setIdentity}
          onConnect={() => void loadRevision(revisionInput, identity)}
          loadState={loadState}
        />
        {error && <div className="global-error" role="alert">{error}</div>}
      </>
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

  return (
    <div className="app-frame">
      <header className="topbar">
        <a className="brand" href="/" aria-label="预算智解首页">
          <span className="brand__seal">智</span>
          <span>预算智解</span>
        </a>
        <nav aria-label="主要导航">
          <a href={`/budget/opening?revision=${encodeURIComponent(activeRevisionId)}`}>期初老生</a>
          <a href="/budget/workspace">年度预算</a>
          <a href={`/budget/rebalancing?revision=${encodeURIComponent(activeRevisionId)}`}>调平方案</a>
          <a href={`/budget/revisions?revision=${encodeURIComponent(activeRevisionId)}`}>预算版本</a>
          <a className="is-current" href={`/budget/publication?revision=${encodeURIComponent(activeRevisionId)}`}>发布检查</a>
        </nav>
        <div className="topbar__identity">
          <span>{health === "ok" ? "系统在线" : "本地模式"}</span>
          <strong>{identity.userId}</strong>
        </div>
      </header>

      <main className="workspace">
        <div className="page-heading">
          <div>
            <p className="breadcrumb">年度预算工作区 <span>/</span> 发布检查</p>
            <h1>发布检查</h1>
            <p className="page-heading__intro">所有硬约束在这里逐项落章。没有忽略异常，也没有强制发布。</p>
          </div>
          <button className="icon-button" onClick={refresh} disabled={loadState === "loading"} aria-label="刷新检查结果">
            <RefreshGlyph />
            <span>{loadState === "loading" ? "刷新中" : "刷新"}</span>
          </button>
        </div>

        <section className={`declaration declaration--${declarationTone}`} aria-live="polite">
          <div className="declaration__signal" aria-hidden="true">
            {isPublished ? "定" : readiness.status === "BLOCKED" ? readiness.blocker_count : "✓"}
          </div>
          <div className="declaration__copy">
            <p>{publishedWithAuditMismatch ? "正式版本复核" : isPublished ? "正式版本" : "发布门禁结论"}</p>
            <h2>{declaration}</h2>
          </div>
          <dl className="declaration__facts">
            <div><dt>通过</dt><dd>{passedCount}<small> / {readiness.checks.length}</small></dd></div>
            <div><dt>修订</dt><dd>R{revision.revision_no}</dd></div>
            <div><dt>财年</dt><dd>FY{String(annualBudget.fiscal_year).slice(-2)}</dd></div>
          </dl>
        </section>

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
            <div><strong>正在执行完整复算</strong><span>人数 → 班均 → 收入 → 目标闭合</span></div>
            <code>{job.status} · 尝试 {job.attempt_no}</code>
          </section>
        )}

        <div className="work-grid">
          <section className="ledger" aria-labelledby="ledger-title">
            <div className="ledger__toolbar">
              <div>
                <p className="section-index">01 / 硬约束清单</p>
                <h2 id="ledger-title">逐项核验</h2>
              </div>
              <div className="segmented" aria-label="筛选检查项">
                <button className={filter === "all" ? "is-active" : ""} onClick={() => setFilter("all")}>全部 {readiness.checks.length}</button>
                <button className={filter === "blocked" ? "is-active" : ""} onClick={() => setFilter("blocked")}>仅看阻断 {readiness.blocker_count}</button>
              </div>
            </div>

            <div className="ledger__body">
              {groupedChecks.length === 0 ? (
                <div className="empty-blockers"><StatusGlyph passed /><p>当前没有发布阻断。</p></div>
              ) : groupedChecks.map(([group, checks]) => (
                <section className="check-group" key={group} aria-labelledby={`group-${group}`}>
                  <div className="check-group__title">
                    <h2 id={`group-${group}`}>{GROUP_LABELS[group] ?? group}</h2>
                    <span>{checks.filter((item) => item.passed).length} / {checks.length}</span>
                  </div>
                  <div className="check-group__rows">
                    {checks.map((check) => <CheckRow key={check.code} check={check} revisionId={activeRevisionId} />)}
                  </div>
                </section>
              ))}
            </div>
          </section>

          <aside className="docket" aria-labelledby="docket-title">
            <div className="docket__header">
              <p className="section-index">02 / 审签卷宗</p>
              <h2 id="docket-title">正式方案与发布</h2>
            </div>

            <section className="revision-ticket">
              <div>
                <span>当前修订</span>
                <strong>R{revision.revision_no}</strong>
              </div>
              <dl>
                <div><dt>修订 ID</dt><dd title={revision.budget_revision_id}>{shortId(revision.budget_revision_id)}</dd></div>
                <div><dt>输入指纹</dt><dd title={readiness.input_fingerprint}>{shortId(readiness.input_fingerprint, 6)}</dd></div>
                <div><dt>规则版本</dt><dd>{readiness.gate_rule_version}</dd></div>
                <div><dt>生成时间</dt><dd>{formatDate(revision.created_at)}</dd></div>
              </dl>
            </section>

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
                <label>
                  <span>正式确认原因 <b>必填</b></span>
                  <textarea value={formalReason} onChange={(event) => setFormalReason(event.target.value)} maxLength={500} rows={3} />
                </label>
                <button className="button button--primary button--wide" onClick={() => void handleFormalConfirmation()} disabled={!canConfirm}>
                  {actionState === "confirming" ? "正在复算并确认…" : "正式方案确认"}
                </button>
                <p>点击后将基于当前修订重新执行四阶段计算。任一阻断都会终止确认。</p>
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
                <p>发布前系统会再次检查全部硬约束并比对确认指纹。</p>
              </div>
            )}

            {isPublished && (
              <div className="published-receipt">
                <div className="formal-stamp formal-stamp--published"><span>FY{String(annualBudget.fiscal_year).slice(-2)}</span><strong>已定版</strong></div>
                <p>{publishedWithAuditMismatch
                  ? `该正式版本按当前门禁复核存在 ${readiness.blocker_count} 项异常，仅可查看并进入审计处理。`
                  : "该修订已成为当前年度预算正式基线，仅可查看或派生新草稿。"}</p>
                {published && <small>发布于 {formatDate(published.published_at)} · {published.published_by}</small>}
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
      </main>
    </div>
  );
}
