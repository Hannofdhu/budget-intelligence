import { useCallback, useEffect, useMemo, useState } from "react";

import {
  ApiError,
  applyRebalancingScenario,
  confirmDiscountUnlock,
  createOptimizationRun,
  fetchCurrentDiscountUnlock,
  fetchLatestOptimizationRun,
  fetchRevision,
  fetchTargetClosureVersion,
  requestDiscountUnlock,
  revertScenarioApplication,
  fetchScenarioApplicationStatus,
  type ApiIdentity,
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
    { key: "ENTRY_ONLY", index: "01", title: "入口结构", note: "财年入口总值不变" },
    { key: "ENTRY_AND_RATES", index: "02", title: "续班率 · 新生占比", note: "并列、等百分点推进" },
    { key: "ENTRY_RATES_AND_DISCOUNT", index: "03", title: "折扣率", note: "0.1 个百分点步长" },
  ];
  return (
    <ol className="lever-rail" aria-label="调平杠杆阶段">
      {stages.map((stage) => {
        const state = stageState(run, stage.key, unlock);
        return (
          <li className={`lever-rail__stage lever-rail__stage--${state.tone}`} key={stage.key}>
            <span className="lever-rail__index">{stage.index}</span>
            <div><strong>{stage.title}</strong><small>{stage.note}</small></div>
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
      <div className="contribution__track" aria-label={retentionShare === null ? "无可计算的收入贡献" : `实际收入贡献 ${retentionShare.toFixed(1)} 比 ${(100 - retentionShare).toFixed(1)}`}>
        <span style={{ width: `${retentionShare ?? 50}%` }} />
      </div>
      <small>{retentionShare === null ? "本候选无可计算的比例收入贡献" : target ? `目标贡献 ${target[0]}/${target[1]}` : "本阶段不设比例贡献目标"}</small>
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
            <h3>{scenarioName(scenario.scenario_types, index)}</h3>
          </div>
          <em>{selected ? "正在比较" : "选择"}</em>
        </div>
        <ContributionBar scenario={scenario} />
        <dl className="candidate-card__metrics">
          <div><dt>参数变更</dt><dd>{scenario.changes.length}</dd><small>条</small></div>
          <div><dt>入口调整</dt><dd>{counts.ENTRY ?? 0}</dd><small>条</small></div>
          <div><dt>最大比例变化</dt><dd>{maxRate.toFixed(2)}</dd><small>百分点</small></div>
          <div><dt>最大折扣变化</dt><dd>{maxDiscount.toFixed(1)}</dd><small>百分点</small></div>
        </dl>
        <div className="candidate-card__result">
          <span>部门目标差额</span>
          <strong>{formatMoney(departmentVariance?.variance_yuan ?? 0, true)}</strong>
          <small>{boundaryHits.length ? `${boundaryHits.length} 项触及确认边界` : "未触及确认边界"}</small>
        </div>
      </button>
    </article>
  );
}

function ChangeTable({ changes }: { changes: ScenarioChange[] }) {
  return (
    <div className="change-table" role="table" aria-label="候选方案参数变更">
      <div className="change-table__row change-table__head" role="row">
        <span>杠杆</span><span>对象键</span><span>原值</span><span>建议值</span><span>变化</span>
      </div>
      {changes.map((change) => (
        <div className="change-table__row" role="row" key={change.scenario_change_id}>
          <strong>{CHANGE_LABELS[change.object_type]}</strong>
          <code title={change.object_stable_key}>{shortKey(change.object_stable_key)}</code>
          <span>{formatValue(change, change.original_value)}</span>
          <span>{formatValue(change, change.suggested_value)}</span>
          <em>{change.adjustment > 0 ? "+" : ""}{formatValue(change, change.adjustment)}</em>
        </div>
      ))}
    </div>
  );
}

function ProofPanel({ proofs }: { proofs: InfeasibilityProof[] }) {
  if (!proofs.length) return null;
  return (
    <section className="proof-panel">
      <header><p>不可行证据</p><span>{proofs.length} 份不可变证明</span></header>
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
  const [identity, setIdentity] = useState<ApiIdentity>(() => ({
    userId: readSetting("budget.userId", "budget-maintainer"),
    departmentId: readSetting("budget.departmentId", "zhihui-learning"),
    roles: [MAINTAINER_ROLE, OWNER_ROLE],
  }));
  const [state, setState] = useState<PageState>("idle");
  const [revision, setRevision] = useState<RevisionResponse | null>(null);
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

  const load = useCallback(async (revisionId: string, nextIdentity = identity) => {
    if (!revisionId) return;
    setState("loading");
    setError(null);
    setApplication(null);
    setCanRevertApplication(false);
    setApplicationRevert(null);
    try {
      const nextRevision = await fetchRevision(revisionId, nextIdentity);
      const closureId = nextRevision.component_manifest.target_closure_result_version_id;
      const [nextRun, nextClosure, applicationStatus] = await Promise.all([
        fetchLatestOptimizationRun(revisionId, nextIdentity),
        typeof closureId === "string" ? fetchTargetClosureVersion(closureId, nextIdentity) : Promise.resolve(null),
        typeof nextRevision.component_manifest.scenario_application_id === "string"
          ? fetchScenarioApplicationStatus(revisionId, nextIdentity)
          : Promise.resolve(null),
      ]);
      const nextUnlock = nextRun
        ? await fetchCurrentDiscountUnlock(revisionId, nextRun.input_fingerprint, nextIdentity)
        : null;
      setRevision(nextRevision);
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
    if (!revision || !selected || !applyReason.trim() || run?.status !== "OPTIMAL") return;
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
      window.history.replaceState(null, "", `/budget/rebalancing?revision=${encodeURIComponent(applied.applied_revision_id)}`);
      setState("ready");
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

  if (!revision) {
    return (
      <main className="rebalance-connect">
        <section>
          <p className="kicker">P10 · 确定性自动调平</p>
          <h1>先看证据，<br />再动预算。</h1>
          <p>连接一个完成目标闭合校验的预算修订，生成可比较、不静默应用的候选方案。</p>
        </section>
        <form onSubmit={(event) => { event.preventDefault(); void load(revisionInput); }}>
          <span className="section-index">CONTROL DESK / FY27</span>
          <label><span>预算修订 ID</span><input value={revisionInput} onChange={(event) => setRevisionInput(event.target.value.trim())} autoComplete="off" /></label>
          <div>
            <label><span>当前用户</span><input value={identity.userId} onChange={(event) => setIdentity({ ...identity, userId: event.target.value })} /></label>
            <label><span>部门 ID</span><input value={identity.departmentId} onChange={(event) => setIdentity({ ...identity, departmentId: event.target.value })} /></label>
          </div>
          <small>当前开发身份同时具备数据维护人员与部门预算负责人角色；解锁申请与确认仍是两个独立动作。</small>
          <button className="button button--primary" disabled={!revisionInput || state === "loading"}>{state === "loading" ? "读取中…" : "进入调平桌"}</button>
          {error && <p className="global-error" role="alert">{error}</p>}
        </form>
      </main>
    );
  }

  return (
    <div className="app-frame rebalance-app">
      <header className="topbar">
        <a className="brand" href="/" aria-label="预算智解首页"><span className="brand__seal">智</span><span>预算智解</span></a>
        <nav aria-label="主要导航">
          <a href="/budget/workspace">年度预算</a>
          <a className="is-current" href={`/budget/rebalancing?revision=${revision.budget_revision_id}`}>调平方案</a>
          <a href={`/budget/publication?revision=${revision.budget_revision_id}`}>发布检查</a>
          <a href={`/budget/audit?revision=${revision.budget_revision_id}`}>版本与审计</a>
        </nav>
        <div className="topbar__identity"><span>双角色受控操作</span><strong>{identity.userId}</strong></div>
      </header>

      <main className="rebalance-workspace">
        <section className="rebalance-hero">
          <div>
            <p className="breadcrumb">年度预算工作区 <span>/</span> 调平方案</p>
            <h1>调平决策台</h1>
            <p>固定入口总值内先调结构，再并列推进续班率与新生占比；折扣始终是最后的次级杠杆。</p>
          </div>
          <div className={`gap-ledger ${gap !== null && Math.abs(gap) <= 10_000 ? "gap-ledger--closed" : ""}`}>
            <span>{gap === null ? "当前修订" : gap < 0 ? "部门预算缺口" : gap > 0 ? "部门预算超额" : "部门目标"}</span>
            <strong>{gap === null ? `R${revision.revision_no}` : formatCompactMoney(Math.abs(gap))}</strong>
            <small>{gap !== null && Math.abs(gap) <= 10_000 ? "无显著缺口" : `目标容差 ±${formatCompactMoney(departmentClosure?.tolerance_yuan ?? 10_000)}`}</small>
          </div>
        </section>

        <section className="control-strip">
          <div className="control-strip__meta">
            <span>修订 R{revision.revision_no}</span>
            <code>{revision.budget_revision_id}</code>
            <span>{run ? `${STATUS_LABELS[run.status]} · ${run.evaluated_state_count.toLocaleString("zh-CN")} 状态` : "尚未求解"}</span>
          </div>
          <button className="button button--primary" onClick={() => void generate()} disabled={state === "working" || !closure}>
            {state === "working" ? "正在处理…" : run ? "重新生成候选" : "生成候选方案"}
          </button>
        </section>

        {!closure && <div className="rebalance-notice rebalance-notice--blocked">当前修订缺少目标闭合结果，请先完成人数、班均、收入与目标校验。</div>}
        {error && <div className="global-error" role="alert">{error}</div>}
        {application && !applicationRevert && (
          <section className="application-receipt" aria-live="polite">
            <span>{canRevertApplication ? "已应用到新草稿" : "历史候选应用记录"}</span>
            <strong>{canRevertApplication ? `${application.change_count} 条参数差异已写入，完整复算作业已登记` : "后续输入或正式状态已变化，该历史应用不可直接撤销"}</strong>
            <button className="application-receipt__revert" onClick={() => void revertApplied()} disabled={state === "working" || !canRevertApplication}>一键撤销本次应用</button>
            <a href={`/budget/rebalancing?revision=${application.applied_revision_id}`}>查看应用时修订 →</a>
          </section>
        )}
        {applicationRevert && (
          <section className="application-receipt application-receipt--reverted" aria-live="polite">
            <span>已生成撤销修订</span>
            <strong>已恢复应用前输入并清除下游结果，完整复算作业已登记</strong>
            <a href={`/budget/rebalancing?revision=${applicationRevert.reverted_revision_id}`}>进入撤销后的修订 →</a>
          </section>
        )}

        <StageRail run={run} unlock={unlock} />

        {run?.status !== "OPTIMAL" && run?.discount_unlock_required && (
          <section className="unlock-desk">
            <div><span>03 / 折扣门禁</span><h2>前两级已精确穷尽</h2><p>不可行证明只能开启当前修订、当前输入指纹的折扣候选。上游变化后解锁立即失效。</p></div>
            <div className="unlock-desk__actions">
              {!unlock && <><textarea rows={3} value={unlockReason} onChange={(event) => setUnlockReason(event.target.value)} /><button onClick={() => void requestUnlock()} disabled={!exactRateProof || state === "working"}>数据维护人员发起解锁</button></>}
              {unlock?.status === "REQUESTED" && <><p><strong>已申请</strong><span>{unlock.reason}</span></p><button onClick={() => void confirmUnlock()} disabled={state === "working"}>部门预算负责人确认</button></>}
              {unlock?.status === "ACTIVE" && <><p><strong>折扣候选已解锁</strong><span>需重新求解才会进入第三阶段</span></p><button onClick={() => void generate()} disabled={state === "working"}>生成含折扣候选</button></>}
            </div>
          </section>
        )}

        <section className="candidate-section">
          <header>
            <div><span>CANDIDATE LEDGER</span><h2>候选对比</h2></div>
            <p>{run?.scenarios.length ? `${run.scenarios.length} 个去重后结果；相同结果的收敛标签合并展示。` : "只有 OPTIMAL 运行会产生可应用候选。"}</p>
          </header>
          {run?.scenarios.length ? (
            <div className="candidate-grid">
              {run.scenarios.map((scenario, index) => <CandidateCard key={scenario.rebalancing_scenario_id} scenario={scenario} index={index} selected={selectedId === scenario.rebalancing_scenario_id} onSelect={() => { setSelectedId(scenario.rebalancing_scenario_id); setShowDetails(true); }} />)}
            </div>
          ) : (
            <div className="candidate-empty"><span>{run ? STATUS_LABELS[run.status] : "未生成"}</span><h3>{run?.status === "INFEASIBLE" ? "在已确认边界和步长内暂无可行方案" : "等待一次确定性求解"}</h3><p>{run?.reason_code ?? "求解不会直接改动当前草稿。"}</p></div>
          )}
        </section>

        {selected && showDetails && (
          <section className="scenario-detail">
            <header><div><span>差异明细</span><h2>{scenarioName(selected.scenario_types, run?.scenarios.indexOf(selected) ?? 0)}</h2></div><button onClick={() => setShowDetails(false)} aria-label="关闭明细">×</button></header>
            <div className="scenario-detail__summary">
              <dl>
                <div><dt>收入结果</dt><dd>{selected.objective_values.department_revenue_yuan === undefined ? "—" : formatCompactMoney(asNumber(selected.objective_values.department_revenue_yuan))}</dd></div>
                <div><dt>班均与整数人次</dt><dd>{selected.objective_values.validation_codes === undefined ? "历史记录未单列" : asStringArray(selected.objective_values.validation_codes).length ? "需复核" : "候选复算通过"}</dd></div>
                <div><dt>人数结果行</dt><dd>{selected.headcount_signature.length.toLocaleString("zh-CN")}</dd></div>
                <div><dt>结果指纹</dt><dd><code>{shortKey(selected.result_hash)}</code></dd></div>
              </dl>
              <div className="scenario-detail__apply">
                <label><span>应用原因</span><textarea rows={2} value={applyReason} onChange={(event) => setApplyReason(event.target.value)} maxLength={500} /></label>
                <button className="button button--primary" onClick={() => void applySelected()} disabled={state === "working" || run?.status !== "OPTIMAL" || !applyReason.trim()}>应用到草稿</button>
                <small>将生成新的不可变预算修订；不会自动正式确认或发布。</small>
              </div>
            </div>
            <ChangeTable changes={selected.changes} />
          </section>
        )}

        <ProofPanel proofs={run?.infeasibility_proofs ?? []} />
      </main>
    </div>
  );
}
