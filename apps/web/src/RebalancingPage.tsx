import { useCallback, useEffect, useMemo, useState } from "react";
import { ActionTable } from "./ActionTable";
import "./auxiliaryDesign.css";

import {
  ApiError,
  applyRebalancingScenario,
  confirmDiscountUnlock,
  createOptimizationRun,
  fetchAnnualBudget,
  fetchAuditEvents,
  fetchCurrentDiscountUnlock,
  fetchLatestOptimizationRun,
  fetchRevision,
  fetchTargetClosureVersion,
  requestDiscountUnlock,
  revertScenarioApplication,
  fetchScenarioApplicationStatus,
  type ApiIdentity,
  type AnnualBudgetResponse,
  type DiscountUnlock,
  type InfeasibilityProof,
  type OptimizationRun,
  type RebalancingScenario,
  type RevisionResponse,
  type ScenarioApplication,
  type ScenarioApplicationRevert,
  type ScenarioChange,
  type TargetClosureVersion,
} from "./api";
import { BudgetContextHeader } from "./BudgetContextHeader";
import { BudgetVersionIdentity, BudgetVersionTrace } from "./BudgetVersionIdentity";
import { versionLabel } from "./budgetVersionDisplay";
import { revisionCommandLabel, revisionRelations } from "./budgetRevisionDisplay";
import { budgetVersionExportAttributes } from "./budgetVersionExport";

type PageState = "idle" | "loading" | "ready" | "working" | "error";

const MAINTAINER_ROLE = "DATA_MAINTAINER";
const OWNER_ROLE = "DEPARTMENT_BUDGET_OWNER";

const STATUS_LABELS: Record<string, string> = {
  OPTIMAL: "已形成可行候选",
  INFEASIBLE: "确认边界内不可行",
  INCOMPLETE: "求解未完成",
  FAILED: "求解失败",
};

const STAGE_LABELS: Record<string, string> = {
  ENTRY_ONLY: "入口结构",
  ENTRY_AND_RATES: "比例并列调节",
  ENTRY_RATES_AND_DISCOUNT: "折扣调节",
};

const PROFILE_LABELS: Record<string, string> = {
  ENTRY_STRUCTURE: "入口结构",
  BALANCED: "平衡 50/50",
  RETENTION_WEIGHTED: "留存侧 70/30",
  ACQUISITION_WEIGHTED: "招生侧 30/70",
  DISCOUNT: "折扣",
};

const CHANGE_LABELS: Record<string, string> = {
  ENTRY: "入口",
  RATE: "比例",
  DISCOUNT: "折扣",
};

function readSetting(key: string, fallback: string): string {
  return window.localStorage.getItem(key)?.trim() || fallback;
}

function initialRevisionId(): string {
  return new URLSearchParams(window.location.search).get("revision")?.trim() ?? "";
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return `${error.message}（${error.code}）`;
  return error instanceof Error ? error.message : "发生未知错误";
}

function asNumber(value: unknown): number {
  if (typeof value === "number") return value;
  if (typeof value === "string") return Number(value) || 0;
  return 0;
}

function asArray(value: unknown): Array<Record<string, unknown>> {
  return Array.isArray(value)
    ? value.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object")
    : [];
}

function asStringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function formatMoney(value: string | number, sign = false): string {
  const number = Number(value);
  const prefix = sign && number > 0 ? "+" : "";
  return `${prefix}${new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 0 }).format(number)} 元`;
}

function formatCompactMoney(value: string | number): string {
  const number = Number(value);
  const absolute = Math.abs(number);
  if (absolute >= 100_000_000) return `${(number / 100_000_000).toFixed(2)} 亿`;
  if (absolute >= 10_000) return `${(number / 10_000).toFixed(1)} 万`;
  return formatMoney(number);
}

function formatValue(change: ScenarioChange, value: number): string {
  if (change.object_type === "ENTRY") return `${value.toLocaleString("zh-CN")} 人`;
  return `${(value / 10_000).toFixed(change.object_type === "DISCOUNT" ? 1 : 2)}%`;
}

function contributionTarget(types: string[]): [number, number] | null {
  if (types.includes("BALANCED")) return [50, 50];
  if (types.includes("RETENTION_WEIGHTED")) return [70, 30];
  if (types.includes("ACQUISITION_WEIGHTED")) return [30, 70];
  return null;
}

function scenarioName(types: string[], index: number): string {
  if (types.includes("BALANCED")) return "平衡方案";
  if (types.includes("RETENTION_WEIGHTED")) return "留存侧方案";
  if (types.includes("ACQUISITION_WEIGHTED")) return "招生侧方案";
  if (types.includes("ENTRY_STRUCTURE")) return "入口结构方案";
  return `候选 ${index + 1}`;
}

function shortKey(value: string): string {
  if (value.length <= 28) return value;
  return `${value.slice(0, 12)}…${value.slice(-10)}`;
}

function stageState(run: OptimizationRun | null, stage: string, unlock: DiscountUnlock | null) {
  if (!run) return { tone: "waiting", label: "等待求解" };
  const stageOrder = ["ENTRY_ONLY", "ENTRY_AND_RATES", "ENTRY_RATES_AND_DISCOUNT"];
  const reachedByTerminalStage = stageOrder.indexOf(stage) <= stageOrder.indexOf(run.terminal_stage);
  const attempted = run.attempts.some((item) => item.stage === stage) || reachedByTerminalStage;
  if (stage === "ENTRY_RATES_AND_DISCOUNT") {
    if (run.discount_unlock_confirmation_id || unlock?.status === "ACTIVE") {
      return { tone: attempted ? "done" : "open", label: attempted ? "已运行" : "已解锁" };
    }
    if (unlock?.status === "REQUESTED") return { tone: "pending", label: "待负责人确认" };
    if (run.discount_unlock_required) return { tone: "action", label: "可申请解锁" };
    return { tone: "locked", label: "未到达解锁条件" };
  }
  if (attempted) return { tone: "done", label: "已精确评估" };
  return { tone: "waiting", label: "等待前级" };
}

function StageRail({ run, unlock }: { run: OptimizationRun | null; unlock: DiscountUnlock | null }) {
  const stages = [
    { key: "ENTRY_ONLY", index: "01", title: "入口结构" },
    { key: "ENTRY_AND_RATES", index: "02", title: "续班率 · 新生占比" },
    { key: "ENTRY_RATES_AND_DISCOUNT", index: "03", title: "折扣率" },
  ];
  return (
    <ol className="lever-rail" aria-label="调平杠杆阶段">
      {stages.map((stage) => {
        const state = stageState(run, stage.key, unlock);
        return (
          <li className={`lever-rail__stage lever-rail__stage--${state.tone}`} key={stage.key}>
            <span className="lever-rail__index">{stage.index}</span>
            <div><strong>{stage.title}</strong></div>
            <em>{state.label}</em>
          </li>
        );
      })}
    </ol>
  );
}

function ContributionBar({ scenario }: { scenario: RebalancingScenario }) {
  const retention = Math.abs(Number(scenario.retention_contribution_yuan));
  const acquisition = Math.abs(Number(scenario.new_share_contribution_yuan));
  const total = retention + acquisition;
  const retentionShare = total ? Math.round((retention / total) * 1000) / 10 : null;
  const target = contributionTarget(scenario.scenario_types);
  return (
    <div className="contribution">
      <div className="contribution__labels">
        <span>续班率 <strong>{retentionShare === null ? "—" : `${retentionShare.toFixed(1)}%`}</strong></span>
        <span>新生占比 <strong>{retentionShare === null ? "—" : `${(100 - retentionShare).toFixed(1)}%`}</strong></span>
      </div>
      <div className="contribution__track" aria-label={retentionShare === null ? "无可计算的结转收入贡献" : `实际结转收入贡献 ${retentionShare.toFixed(1)} 比 ${(100 - retentionShare).toFixed(1)}`}>
        <span style={{ width: `${retentionShare ?? 50}%` }} />
      </div>
      {retentionShare !== null && target && <small>目标贡献 {target[0]}/{target[1]}</small>}
    </div>
  );
}

function CandidateCard({
  scenario,
  index,
  selected,
  onSelect,
}: {
  scenario: RebalancingScenario;
  index: number;
  selected: boolean;
  onSelect: () => void;
}) {
  const counts = scenario.changes.reduce<Record<string, number>>((acc, item) => {
    acc[item.object_type] = (acc[item.object_type] ?? 0) + 1;
    return acc;
  }, {});
  const maxRate = Math.max(
    asNumber(scenario.objective_values.maximum_rate_adjustment_ppm),
    ...scenario.changes.filter((item) => item.object_type === "RATE").map((item) => Math.abs(item.adjustment)),
  ) / 10_000;
  const maxDiscount = Math.max(
    asNumber(scenario.objective_values.maximum_discount_adjustment_ppm),
    ...scenario.changes.filter((item) => item.object_type === "DISCOUNT").map((item) => Math.abs(item.adjustment)),
  ) / 10_000;
  const boundaryHits = asArray(scenario.objective_values.boundary_hits);
  const departmentVariance = scenario.target_variances.find((item) => item.scope.startsWith("DEPARTMENT"));
  return (
    <article className={`candidate-card ${selected ? "is-selected" : ""}`}>
      <button className="candidate-card__select" onClick={onSelect} aria-pressed={selected}>
        <div className="candidate-card__head">
          <span>{String(index + 1).padStart(2, "0")}</span>
          <div>
            <p>{scenario.scenario_types.map((item) => PROFILE_LABELS[item] ?? item).join(" · ")}</p>
            <h3 className="budget-subsection-title">{scenarioName(scenario.scenario_types, index)}</h3>
          </div>
          <em>{selected ? "正在比较" : "选择"}</em>
        </div>
        <dl className="candidate-card__metrics">
          <div><dt>调整明细</dt><dd>{scenario.changes.length}</dd><small>条</small></div>
          <div><dt>触及边界</dt><dd>{boundaryHits.length}</dd><small>项</small></div>
        </dl>
        <div className="candidate-card__result">
          <span>应用后目标差额</span>
          <strong>{formatMoney(departmentVariance?.variance_yuan ?? 0, true)}</strong>
          <small>{counts.ENTRY ?? 0} 条入口调整 · 最大比例变化 {maxRate.toFixed(2)} 个百分点 · 最大折扣变化 {maxDiscount.toFixed(1)} 个百分点</small>
        </div>
      </button>
    </article>
  );
}

export function ChangeTable({ changes }: { changes: ScenarioChange[] }) {
  return (
    <ActionTable tableLabel="候选方案参数变更" aria-label="候选方案参数变更" keyboardNavigation scrollClassName="budget-auxiliary-table-scroll" className="budget-auxiliary-table">
      <thead><tr>{["杠杆", "对象键", "原值", "建议值", "变化"].map(label => <th scope="col" key={label}>{label}</th>)}</tr></thead>
      <tbody>
      {changes.map((change) => (
        <tr key={change.scenario_change_id}>
          <th scope="row">{CHANGE_LABELS[change.object_type]}</th>
          <td><code>{change.object_stable_key}</code></td>
          <td className="budget-cell-number">{formatValue(change, change.original_value)}</td>
          <td className="budget-cell-number">{formatValue(change, change.suggested_value)}</td>
          <td className="budget-cell-number">{change.adjustment > 0 ? "+" : ""}{formatValue(change, change.adjustment)}</td>
        </tr>
      ))}
      </tbody>
    </ActionTable>
  );
}

function ProofPanel({ proofs }: { proofs: InfeasibilityProof[] }) {
  if (!proofs.length) return null;
  return (
    <section className="proof-panel">
      <header><h2 className="budget-section-title">不可行证据</h2><span>{proofs.length} 份不可变证明</span></header>
      {proofs.map((proof) => (
        <details key={proof.infeasibility_proof_id}>
          <summary>
            <span>{STAGE_LABELS[proof.stage] ?? proof.stage} · {PROFILE_LABELS[proof.profile] ?? proof.profile}</span>
            <strong>{proof.proof_code}</strong>
          </summary>
          <div>
            <p>硬约束组：{proof.hard_constraint_groups.join("、") || "未列出"}</p>
            <pre>{JSON.stringify(proof.proof_summary, null, 2)}</pre>
          </div>
        </details>
      ))}
    </section>
  );
}

export function RebalancingPage() {
  const [revisionInput, setRevisionInput] = useState(initialRevisionId);
  const [identity] = useState<ApiIdentity>(() => ({
    userId: readSetting("budget.userId", "budget-maintainer"),
    departmentId: readSetting("budget.departmentId", "zhihui-learning"),
    roles: [MAINTAINER_ROLE, OWNER_ROLE],
  }));
  const [state, setState] = useState<PageState>("idle");
  const [revision, setRevision] = useState<RevisionResponse | null>(null);
  const [budget, setBudget] = useState<AnnualBudgetResponse | null>(null);
  const [closure, setClosure] = useState<TargetClosureVersion | null>(null);
  const [run, setRun] = useState<OptimizationRun | null>(null);
  const [unlock, setUnlock] = useState<DiscountUnlock | null>(null);
  const [selectedId, setSelectedId] = useState("");
  const [showDetails, setShowDetails] = useState(false);
  const [unlockReason, setUnlockReason] = useState("入口结构及比例确认边界已精确穷尽，申请启用折扣次级杠杆");
  const [applyReason, setApplyReason] = useState("选定该候选方案并应用到预算草稿");
  const [application, setApplication] = useState<ScenarioApplication | null>(null);
  const [canRevertApplication, setCanRevertApplication] = useState(false);
  const [applicationRevert, setApplicationRevert] = useState<ScenarioApplicationRevert | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [publicationFacts, setPublicationFacts] = useState<{ publishedIds: Set<string>; known: boolean }>({ publishedIds: new Set(), known: false });

  const load = useCallback(async (revisionId: string, nextIdentity = identity) => {
    if (!revisionId) return;
    setState("loading");
    setError(null);
    setApplication(null);
    setCanRevertApplication(false);
    setApplicationRevert(null);
    try {
      const nextRevision = await fetchRevision(revisionId, nextIdentity);
      const nextBudget = await fetchAnnualBudget(nextRevision.annual_budget_id, nextIdentity);
      const closureId = nextRevision.component_manifest.target_closure_result_version_id;
      const [nextRun, nextClosure, applicationStatus, publications] = await Promise.all([
        fetchLatestOptimizationRun(revisionId, nextIdentity),
        typeof closureId === "string" ? fetchTargetClosureVersion(closureId, nextIdentity) : Promise.resolve(null),
        typeof nextRevision.component_manifest.scenario_application_id === "string"
          ? fetchScenarioApplicationStatus(revisionId, nextIdentity)
          : Promise.resolve(null),
        nextBudget.current_published_revision_id === revisionId
          ? Promise.resolve({ publishedIds: new Set([revisionId]), known: true })
          : fetchAuditEvents(nextBudget.annual_budget_id, { revisionId, eventType: "PUBLISHED" }, nextIdentity)
            .then(page => ({ publishedIds: new Set(page.items.filter(event => event.budget_revision_id === revisionId && event.event_type === "PUBLISHED").map(event => event.budget_revision_id!)), known: true }))
            .catch(() => ({ publishedIds: new Set<string>(), known: false })),
      ]);
      const nextUnlock = nextRun
        ? await fetchCurrentDiscountUnlock(revisionId, nextRun.input_fingerprint, nextIdentity)
        : null;
      setRevision(nextRevision);
      setBudget(nextBudget);
      setPublicationFacts(publications);
      setApplication(applicationStatus?.application ?? null);
      setCanRevertApplication(applicationStatus?.can_revert ?? false);
      setClosure(nextClosure);
      setRun(nextRun);
      setUnlock(nextUnlock);
      setSelectedId(nextRun?.scenarios[0]?.rebalancing_scenario_id ?? "");
      setRevisionInput(revisionId);
      setState("ready");
      window.history.replaceState(null, "", `/budget/rebalancing?revision=${encodeURIComponent(revisionId)}`);
      window.localStorage.setItem("budget.userId", nextIdentity.userId);
      window.localStorage.setItem("budget.departmentId", nextIdentity.departmentId);
    } catch (nextError) {
      setError(errorMessage(nextError));
      setState("error");
    }
  }, [identity]);

  useEffect(() => {
    if (revisionInput) void load(revisionInput);
    // The initial query parameter is loaded once; explicit actions own subsequent refreshes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const selected = useMemo(
    () => run?.scenarios.find((item) => item.rebalancing_scenario_id === selectedId) ?? null,
    [run, selectedId],
  );
  const departmentClosure = closure?.items.find((item) => item.scope_level === "DEPARTMENT") ?? null;
  const gap = departmentClosure ? Number(departmentClosure.variance_yuan) : null;
  const exactRateProof = run?.infeasibility_proofs.find((item) => item.stage === "ENTRY_AND_RATES") ?? null;

  async function generate() {
    if (!revision) return;
    setState("working");
    setError(null);
    try {
      const nextRun = await createOptimizationRun(revision.budget_revision_id, identity);
      setRun(nextRun);
      setSelectedId(nextRun.scenarios[0]?.rebalancing_scenario_id ?? "");
      setUnlock(await fetchCurrentDiscountUnlock(revision.budget_revision_id, nextRun.input_fingerprint, identity));
      setState("ready");
    } catch (nextError) {
      setError(errorMessage(nextError));
      setState("ready");
    }
  }

  async function requestUnlock() {
    if (!revision || !run || !exactRateProof || !unlockReason.trim()) return;
    setState("working");
    setError(null);
    try {
      setUnlock(await requestDiscountUnlock(
        revision.budget_revision_id,
        run.optimization_run_id,
        exactRateProof.infeasibility_proof_id,
        unlockReason.trim(),
        identity,
      ));
      setState("ready");
    } catch (nextError) {
      setError(errorMessage(nextError));
      setState("ready");
    }
  }

  async function confirmUnlock() {
    if (!revision || !unlock) return;
    setState("working");
    setError(null);
    try {
      setUnlock(await confirmDiscountUnlock(
        revision.budget_revision_id,
        unlock.discount_unlock_request_id,
        identity,
      ));
      setState("ready");
    } catch (nextError) {
      setError(errorMessage(nextError));
      setState("ready");
    }
  }

  async function applySelected() {
    if (!revision || budget?.current_draft_revision_id !== revision.budget_revision_id || budget.current_published_revision_id === revision.budget_revision_id || !selected || !applyReason.trim() || run?.status !== "OPTIMAL") return;
    setState("working");
    setError(null);
    setApplicationRevert(null);
    try {
      const applied = await applyRebalancingScenario(
        revision.budget_revision_id,
        selected.rebalancing_scenario_id,
        applyReason.trim(),
        identity,
      );
      setApplication(applied);
      setCanRevertApplication(true);
      await load(applied.applied_revision_id);
    } catch (nextError) {
      setError(errorMessage(nextError));
      setState("ready");
    }
  }

  async function revertApplied() {
    if (!application) return;
    setState("working");
    setError(null);
    try {
      const latest = await fetchScenarioApplicationStatus(application.applied_revision_id, identity);
      if (!latest?.can_revert) {
        throw new Error(`不能撤销：${latest?.blocking_code ?? "候选应用不存在"}。请刷新查看当前预算。`);
      }
      setApplicationRevert(await revertScenarioApplication(
        latest.current_revision_id,
        application.scenario_application_id,
        "撤销本次候选应用，恢复应用前预算输入",
        identity,
      ));
      setState("ready");
    } catch (nextError) {
      setError(errorMessage(nextError));
      setState("ready");
    }
  }

  if (!revision || !budget) {
    if (revisionInput && state !== "error") return <div className="decision-shell"><header className="decision-topbar"><a className="decision-brand" href="/budget/workspace"><span aria-hidden="true">智</span><strong>预算智解</strong></a></header><main className="decision-workspace" aria-busy="true"><div className="decision-skeleton decision-skeleton--progress" /><div className="decision-skeleton decision-skeleton--title" /><div className="decision-skeleton decision-skeleton--body" /><p role="status">正在读取预算缺口…</p></main></div>;
    return (
      <div className="decision-shell"><header className="decision-topbar"><a className="decision-brand" href="/budget/workspace"><span aria-hidden="true">智</span><strong>预算智解</strong></a></header><main className="decision-workspace publication-entry"><p className="decision-kicker">年度预算 / 处理缺口</p><h1>{error ? "预算缺口读取失败" : "请先打开年度预算"}</h1>{error && <p role="alert">{error}</p>}<a className="decision-primary" href="/budget/workspace">返回年度预算</a>{revisionInput && <button className="publication-retry" onClick={() => void load(revisionInput)}>重试读取</button>}</main></div>
    );
  }

  const isCurrentPublished = budget.current_published_revision_id === revision.budget_revision_id;
  const isPublished = isCurrentPublished || publicationFacts.publishedIds.has(revision.budget_revision_id);
  // The existing pointer-based editing gate is unchanged; audit facts describe history only.
  const isCurrentDraft = !isCurrentPublished && budget.current_draft_revision_id === revision.budget_revision_id;
  const tolerance = departmentClosure ? Number(departmentClosure.tolerance_yuan) : 0;
  const needsBalancing = gap !== null && Math.abs(gap) > tolerance;
  const canGenerate = isCurrentDraft && needsBalancing && Boolean(closure);
  const resultHref = `/budget/results?revision=${encodeURIComponent(revision.budget_revision_id)}`;
  const workspaceHref = `/budget/workspace?revision=${encodeURIComponent(revision.budget_revision_id)}&section=results`;
  const title = isPublished ? "这版预算已定版" : !isCurrentDraft ? "这是历史修订" : !closure ? "先完成预算计算" : needsBalancing ? gap !== null && gap < 0 ? "预算还有缺口" : "预算超出目标" : "无需调平";
  const versionExportContext = { fiscalYear: budget.fiscal_year, object: "年度预算", sequence: revision.revision_no, createdAt: revision.created_at, id: revision.budget_revision_id };

  return (
    <div className="app-frame rebalance-app decision-shell budget-auxiliary">
      <a className="skip-link" href="#rebalance-main">跳到缺口处理</a>
      <BudgetContextHeader fiscalYear={budget.fiscal_year} revisionNo={revision.revision_no} revisionId={revision.budget_revision_id} status={isPublished ? "已定版" : publicationFacts.known || isCurrentDraft ? "草稿" : "定版状态待核对"} />

      <main className="rebalance-workspace" id="rebalance-main" {...budgetVersionExportAttributes(versionExportContext)}>
        <div className="context-return"><a href={isPublished ? resultHref : workspaceHref}>返回{isPublished ? "预算结果" : "计算结果"}</a><span>缺口处理</span></div>
        <BudgetVersionIdentity sequence={revision.revision_no} createdAt={revision.created_at} reason={revisionCommandLabel(revision.command_type)} viewing readonly={!isCurrentDraft} />
        <p className="budget-status">{revisionRelations(revision, budget, publicationFacts.publishedIds, publicationFacts.known).map(item => item.label).join(" · ")}{!isCurrentDraft ? " · 只读查看" : ""}{budget.current_draft_revision_id && budget.current_draft_revision_id !== revision.budget_revision_id && <> · <a href={`/budget/rebalancing?revision=${encodeURIComponent(budget.current_draft_revision_id)}`}>返回最新草稿</a></>}</p>
        <BudgetVersionTrace code={`R${revision.revision_no}`} id={revision.budget_revision_id} createdAt={revision.created_at} createdBy={revision.created_by} />
        <section className="rebalance-hero">
          <div>
            <h1 className="budget-page-title">{title}</h1>

          </div>
          <div className={`gap-ledger ${gap !== null && !needsBalancing ? "gap-ledger--closed" : ""}`}>
            <span>{gap === null ? "当前修订" : gap < 0 ? "部门预算缺口" : gap > 0 ? "部门预算超额" : "与目标差异"}</span>
            <strong>{gap === null ? versionLabel(revision.revision_no) : formatCompactMoney(Math.abs(gap))}</strong>
            <small>{gap !== null && !needsBalancing ? "在确认容差内" : `目标容差 ±${formatCompactMoney(tolerance)}`}</small>
          </div>
        </section>

        <section className="control-strip">
          <div className="control-strip__meta"><span>{isPublished ? "本次无需生成方案" : !isCurrentDraft ? "历史修订只读" : run ? STATUS_LABELS[run.status] : needsBalancing ? "等待生成方案" : "本次无需生成方案"}</span></div>
          {canGenerate ? <button className="decision-primary" onClick={() => void generate()} disabled={state === "working"}>{state === "working" ? "正在生成…" : run ? "重新生成方案" : "生成方案"}</button> : <a className="decision-primary" href={isPublished ? resultHref : !isCurrentDraft || !closure ? workspaceHref : `/budget/publication?revision=${encodeURIComponent(revision.budget_revision_id)}`}>{isPublished ? "查看预算结果" : !isCurrentDraft ? "返回年度预算" : !closure ? "继续预算计算" : "继续确认预算"}</a>}
        </section>

        {!closure && <div className="rebalance-notice rebalance-notice--blocked">当前修订缺少目标闭合结果，请先完成人数、班均、结转收入与目标校验。</div>}
        {error && <div className="global-error" role="alert">{error}</div>}
        {application && !applicationRevert && (
          <details className="application-receipt" key={canRevertApplication ? "reversible" : "historic"} open={canRevertApplication}>
            <summary>{canRevertApplication ? "已应用到新草稿" : "查看历史候选应用"}</summary>
            <strong>{canRevertApplication ? `${application.change_count} 条参数差异已写入，完整复算作业已登记` : "后续输入或正式状态已变化，该历史应用不可直接撤销"}</strong>
            {canRevertApplication && <button className="application-receipt__revert" onClick={() => void revertApplied()} disabled={state === "working"}>撤销本次应用</button>}
            <a href={`/budget/workspace?revision=${encodeURIComponent(application.applied_revision_id)}&section=results`}>查看应用后的预算 →</a>
          </details>
        )}
        {applicationRevert && (
          <section className="application-receipt application-receipt--reverted" aria-live="polite">
            <span>已生成撤销修订</span>
            <strong>已恢复应用前输入并清除下游结果，完整复算作业已登记</strong>
            <a href={`/budget/rebalancing?revision=${applicationRevert.reverted_revision_id}`}>进入撤销后的修订 →</a>
          </section>
        )}

        {(needsBalancing || run) && <details className="rebalance-evidence"><summary>调整顺序与求解依据</summary><StageRail run={run} unlock={unlock} /><ProofPanel proofs={run?.infeasibility_proofs ?? []} /></details>}

        {isCurrentDraft && needsBalancing && run?.status !== "OPTIMAL" && run?.discount_unlock_required && (
          <section className="unlock-desk">
            <div><h2 className="budget-section-title">前两级已精确穷尽</h2></div>
            <div className="unlock-desk__actions">
              {!unlock && <><textarea rows={3} value={unlockReason} onChange={(event) => setUnlockReason(event.target.value)} /><button onClick={() => void requestUnlock()} disabled={!exactRateProof || state === "working"}>数据维护人员发起解锁</button></>}
              {unlock?.status === "REQUESTED" && <><p><strong>已申请</strong><span>{unlock.reason}</span></p><button onClick={() => void confirmUnlock()} disabled={state === "working"}>部门预算负责人确认</button></>}
              {unlock?.status === "ACTIVE" && <><p><strong>折扣候选已解锁</strong></p><button onClick={() => void generate()} disabled={state === "working"}>生成含折扣候选</button></>}
            </div>
          </section>
        )}

        {isCurrentDraft && needsBalancing && <section className="candidate-section">
          <header>
            <div><h2 className="budget-section-title">先比较，再应用</h2></div>
            {Boolean(run?.scenarios.length) && <span>{run!.scenarios.length} 个方案</span>}
          </header>
          {run?.scenarios.length ? (
            <div className="candidate-grid">
              {run.scenarios.map((scenario, index) => <CandidateCard key={scenario.rebalancing_scenario_id} scenario={scenario} index={index} selected={selectedId === scenario.rebalancing_scenario_id} onSelect={() => { setSelectedId(scenario.rebalancing_scenario_id); setShowDetails(true); }} />)}
            </div>
          ) : (
            <div className="candidate-empty"><span>{run ? STATUS_LABELS[run.status] : "未生成"}</span><h3 className="budget-subsection-title">{run?.status === "INFEASIBLE" ? "当前约束下没有可行方案" : run?.status === "INCOMPLETE" ? "尚未求出结论" : run?.status === "FAILED" ? "方案生成失败" : "等待生成方案"}</h3>{run?.reason_code && <p role="status">{run.reason_code}</p>}</div>
          )}
        </section>}

        {isCurrentDraft && selected && showDetails && (
          <section className="scenario-detail budget-surface" {...budgetVersionExportAttributes({ ...versionExportContext, object: "年度预算调平建议", preview: true,
            sourceId: selected.rebalancing_scenario_id, sourceLabel: `候选方案（基于FY${String(budget.fiscal_year).slice(-2)}年度预算${versionLabel(revision.revision_no)}）` })}>
            <header><div><h2 className="budget-section-title">{scenarioName(selected.scenario_types, run?.scenarios.indexOf(selected) ?? 0)}差异明细</h2></div><button onClick={() => setShowDetails(false)} aria-label="关闭明细">×</button></header>
            <div className="scenario-detail__summary">
              <dl>
                <div><dt>结转收入结果</dt><dd>{selected.objective_values.department_revenue_yuan === undefined ? "—" : formatCompactMoney(asNumber(selected.objective_values.department_revenue_yuan))}</dd></div>
                <div><dt>班均与整数人次</dt><dd>{selected.objective_values.validation_codes === undefined ? "历史记录未单列" : asStringArray(selected.objective_values.validation_codes).length ? "需复核" : "候选复算通过"}</dd></div>
                <div><dt>人数结果行</dt><dd>{selected.headcount_signature.length.toLocaleString("zh-CN")}</dd></div>
                <div><dt>结果指纹</dt><dd><code>{shortKey(selected.result_hash)}</code></dd></div>
              </dl>
              <div className="scenario-detail__apply">
                <label><span>应用原因</span><textarea rows={2} value={applyReason} onChange={(event) => setApplyReason(event.target.value)} maxLength={500} /></label>
                <button className="button button--primary" onClick={() => void applySelected()} disabled={state === "working" || run?.status !== "OPTIMAL" || !applyReason.trim()}>应用到草稿</button>

              </div>
            </div>
            <ContributionBar scenario={selected} />
            <ChangeTable changes={selected.changes} />
          </section>
        )}

      </main>
    </div>
  );
}
