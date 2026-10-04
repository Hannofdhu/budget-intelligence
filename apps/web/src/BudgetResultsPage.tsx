import { useEffect, useMemo, useRef, useState } from "react";
import {
  fetchAnnualBudget, fetchAuditEvents, fetchHeadcountResultVersion, fetchOpeningStudentVersion, fetchRevenueResultVersion,
  fetchRevision, fetchTargetClosureVersion, parseResponse,
  type ApiIdentity, type RevenueResultVersion, type SeasonHeadcountResult, type TargetClosureVersion,
} from "./api";
import { PublishedBudgetRecord } from "./PublishedBudgetRecord";
import { BudgetAnalysisView } from "./BudgetAnalysisView";
import { normalizePublishedFacts, type PublishedRenewalContext } from "./budgetAnalysis";

// This page reads existing published results; no calculation or budget write is performed.
export function BudgetResultsPage({ revisionId, active }: { revisionId: string; active: boolean }) {
  const [identity, setIdentity] = useState<ApiIdentity | null>(null);
  const [annualBudgetId, setAnnualBudgetId] = useState("");
  const [result, setResult] = useState<RevenueResultVersion | null>(null);
  const [headcounts, setHeadcounts] = useState<SeasonHeadcountResult[]>([]);
  const [renewalContext, setRenewalContext] = useState<PublishedRenewalContext | undefined>(undefined);
  const [closure, setClosure] = useState<TargetClosureVersion | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [countNotice, setCountNotice] = useState("");
  const pageTitleRef = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    const controller = new AbortController();
    void (async () => {
      setLoading(true); setError(""); setCountNotice(""); setHeadcounts([]); setRenewalContext(undefined); setClosure(null); setResult(null);
      try {
        const who = await parseResponse<ApiIdentity>(await fetch("/api/v1/local-trial/session", { signal: controller.signal }));
        if (!revisionId) throw new Error("没有找到要查看的预算版本。");
        const revision = await fetchRevision(revisionId, who, controller.signal);
        const budget = await fetchAnnualBudget(revision.annual_budget_id, who, controller.signal);
        if (revisionId !== budget.current_published_revision_id) {
          const publication = await fetchAuditEvents(budget.annual_budget_id, { revisionId, eventType: "PUBLISHED" }, who, controller.signal);
          if (publication.items.length === 0) throw new Error("这版预算尚未定版，请返回年度预算继续审阅。");
        }
        const revenueId = revision.component_manifest.revenue_result_version_id;
        if (typeof revenueId !== "string") throw new Error("该预算版本还没有收入计算结果。");
        const nextResult = await fetchRevenueResultVersion(revenueId, who, controller.signal);
        let countItems: SeasonHeadcountResult[] = [];
        let nextRenewalContext: PublishedRenewalContext | undefined;
        let nextNotice = "";
        if (nextResult.headcount_result_version_id) {
          try {
            const counts = await fetchHeadcountResultVersion(nextResult.headcount_result_version_id, who, controller.signal);
            if (counts.headcount_result_version_id !== nextResult.headcount_result_version_id) throw new Error("人数版本不匹配");
            countItems = counts.items;
            // Opening enrollment must belong to the exact saved count dependency,
            // not to a newer draft or its editable opening workspace.
            if (counts.rate_parameter_version_id) {
              try {
                const countRevision = await fetchRevision(counts.budget_revision_id, who, controller.signal);
                if (countRevision.annual_budget_id !== budget.annual_budget_id) throw new Error("期初版本不匹配");
                const openingId = countRevision.component_manifest.opening_student_version_id;
                if (typeof openingId !== "string") throw new Error("期初版本缺失");
                const opening = await fetchOpeningStudentVersion(openingId, who, controller.signal);
                if (opening.opening_student_version_id !== openingId) throw new Error("期初版本不匹配");
                nextRenewalContext = { fiscalYear: nextResult.fiscal_year, rateParameterVersionId: counts.rate_parameter_version_id, openingContent: opening.content };
              } catch {
                if (controller.signal.aborted) return;
                nextNotice = "暑假续班基数暂时无法读取，相关续班率显示为 —；其他定版指标不受影响。";
              }
            } else nextNotice = "这版人数结果未包含期初依赖，暑假续班率显示为 —。";
          } catch {
            if (controller.signal.aborted) return;
            nextNotice = "老生、新生人数暂时无法读取；收入和总人次仍按定版结果显示。";
          }
        } else nextNotice = "这版结果未包含老生、新生拆解，相关指标显示为 —。";
        const closureId = revision.component_manifest.target_closure_result_version_id;
        const nextClosure = typeof closureId === "string" ? await fetchTargetClosureVersion(closureId, who, controller.signal) : null;
        if (!controller.signal.aborted) {
          setIdentity(who); setAnnualBudgetId(budget.annual_budget_id); setResult(nextResult);
          setHeadcounts(countItems); setRenewalContext(nextRenewalContext); setClosure(nextClosure); setCountNotice(nextNotice);
        }
      } catch (reason) {
        if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : String(reason));
      } finally { if (!controller.signal.aborted) setLoading(false); }
    })();
    return () => controller.abort();
  }, [revisionId]);
  useEffect(() => {
    if (!active || !result || loading) return;
    const frame = window.requestAnimationFrame(() => pageTitleRef.current?.focus({ preventScroll: true }));
    return () => window.cancelAnimationFrame(frame);
  }, [active, loading, result]);
  const facts = useMemo(() => normalizePublishedFacts(result?.items ?? [], headcounts, renewalContext), [result, headcounts, renewalContext]);
  const target = closure?.items.find(item => item.scope_level === "DEPARTMENT")?.target_revenue_yuan;
  return <section className="budget-overview budget-overview--embedded" aria-label="第五步：预算概览">
    {loading && <section className="result-state" role="status"><span className="result-loader" /><h1>正在读取预算</h1></section>}
    {error && <section className="result-state result-state--error" role="alert"><h1>预算暂时无法读取</h1><p>{error}</p><a href={`/budget/workspace?revision=${encodeURIComponent(revisionId)}&section=home`}>返回年度预算</a></section>}
    {result && !loading && !error && <>
      <header className="ba-page-heading"><div><p>FY{String(result.fiscal_year).slice(-2)} 学校版</p><h1 ref={pageTitleRef} tabIndex={-1}>预算概览</h1></div><a href={`/budget/audit?revision=${encodeURIComponent(revisionId)}`}>变化记录</a></header>
      {countNotice && <p className="ba-notice" role="status">{countNotice}</p>}
      <BudgetAnalysisView facts={facts} contextKey={`budget:${annualBudgetId}:${revisionId}`} fiscalYear={result.fiscal_year}
        annualTarget={target === undefined ? null : Number(target)}
        recordContent={identity ? <PublishedBudgetRecord revisionId={revisionId} annualBudgetId={annualBudgetId} identity={identity} /> : undefined} />
    </>}
  </section>;
}
