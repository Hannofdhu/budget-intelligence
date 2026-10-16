import { useEffect, useState } from "react";
import { fetchAuditEvents, fetchPublicationReadiness, type ApiIdentity, type AuditEvent, type PublicationReadiness } from "./api";
import { BudgetVersionTrace } from "./BudgetVersionIdentity";
import { versionTimestamp } from "./budgetVersionDisplay";

export function PublishedBudgetRecord({ revisionId, annualBudgetId, identity }: { revisionId: string; annualBudgetId: string; identity: ApiIdentity }) {
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [readiness, setReadiness] = useState<PublicationReadiness | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError("");
    void Promise.all([
      fetchAuditEvents(annualBudgetId, { revisionId, eventType: "PUBLISHED" }, identity, controller.signal),
      fetchAuditEvents(annualBudgetId, { revisionId, eventType: "FORMAL_PLAN_CONFIRMED" }, identity, controller.signal),
      fetchPublicationReadiness(revisionId, identity, controller.signal),
    ]).then(([publication, confirmation, checks]) => {
      if (controller.signal.aborted) return;
      setEvents([...confirmation.items, ...publication.items].sort((a, b) => a.occurred_at.localeCompare(b.occurred_at)));
      setReadiness(checks);
    }).catch(reason => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : String(reason)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [revisionId, annualBudgetId, identity, attempt]);

  if (loading) return <div className="overview-record" role="status" aria-busy="true">正在读取定版记录…</div>;
  if (error) return <div className="overview-record" role="alert"><h2>定版记录暂时无法读取</h2><p>{error}</p><button onClick={() => setAttempt(value => value + 1)}>重试</button></div>;
  return <div className="overview-record">
    <h2>确认与定版记录</h2>
    {events.length === 0 ? <p>暂未读取到这版预算的确认与定版记录。</p> : <ol className="overview-timeline">{events.map(event => <li key={event.event_id}>
      <div><strong>{event.event_type === "PUBLISHED" ? "预算已定版" : "方案已确认"}</strong><time dateTime={event.occurred_at}>{versionTimestamp(event.occurred_at)}</time></div>
      <p>{event.event_type === "PUBLISHED" ? "定版身份" : "确认身份"}：{event.actor_id || "未记录"}{event.acting_role === "DEPARTMENT_BUDGET_OWNER" ? " · 部门预算负责人" : ""}</p>
      {event.reason && <p>{event.reason}</p>}
    </li>)}</ol>}
    <BudgetVersionTrace id={revisionId} />
    {readiness && <details className="overview-checks"><summary>当前复核 · {readiness.blocker_count ? `${readiness.blocker_count} 项需关注` : `${readiness.checks.length} 项通过`}</summary>

      <ul>{readiness.checks.map(check => <li key={check.code}><span>{check.passed ? "通过" : "需关注"}</span><div>{check.rule}</div></li>)}</ul>
    </details>}
  </div>;
}
