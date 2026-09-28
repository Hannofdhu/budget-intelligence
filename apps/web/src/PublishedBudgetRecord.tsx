import { useEffect, useState } from "react";
import { fetchAuditEvents, fetchPublicationReadiness, type ApiIdentity, type AuditEvent, type PublicationReadiness } from "./api";

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
      <div><strong>{event.event_type === "PUBLISHED" ? "预算已定版" : "方案已确认"}</strong><time dateTime={event.occurred_at}>{new Date(event.occurred_at).toLocaleString("zh-CN", { hour12: false })}</time></div>
      <p>{event.actor_id}{event.acting_role === "DEPARTMENT_BUDGET_OWNER" ? " · 部门预算负责人" : ""}</p>
      {event.reason && <p>{event.reason}</p>}
      <details><summary>查看依据</summary><pre>{JSON.stringify(event.detail, null, 2)}</pre></details>
    </li>)}</ol>}
    {readiness && <details className="overview-checks"><summary>当前复核 · {readiness.blocker_count ? `${readiness.blocker_count} 项需关注` : `${readiness.checks.length} 项通过`}</summary>
      <p>以下为当前读取的检查结果，定版事实以上方记录为准。</p>
      <ul>{readiness.checks.map(check => <li key={check.code}><span>{check.passed ? "通过" : "需关注"}</span><div>{check.rule}<details><summary>检查依据</summary><pre>{JSON.stringify(check.actual, null, 2)}</pre></details></div></li>)}</ul>
    </details>}
  </div>;
}
