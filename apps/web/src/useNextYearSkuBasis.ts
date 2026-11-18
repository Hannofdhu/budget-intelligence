import { useEffect, useState } from "react";
import type { ApiIdentity } from "./api";
import { getForecastVersion, peekForecast, type ForecastIssue, type ForecastRow } from "./forecastApi";
import type { SkuBasisStatus } from "./nextYearSkuEvidence";

/** Exact saved source only. Reading evidence must never refresh or confirm a forecast. */
export function useNextYearSkuBasis(identity: ApiIdentity | null, sourceId: string | undefined, enabled: boolean) {
  const key = identity && sourceId ? JSON.stringify([identity.userId, identity.departmentId, identity.roles, sourceId]) : "";
  const [result, setResult] = useState<{ key: string; rows: ForecastRow[] | null; issues: ForecastIssue[]; status: SkuBasisStatus } | null>(null);
  useEffect(() => {
    if (!identity || !sourceId || !enabled) return;
    const controller = new AbortController();
    void getForecastVersion(identity, sourceId, controller.signal).then(envelope => {
      if (!controller.signal.aborted) setResult({ key, rows: envelope.version!.calculation.rows, issues: envelope.version!.calculation.issues ?? [], status: "ready" });
    }).catch(() => { if (!controller.signal.aborted) setResult({ key, rows: null, issues: [], status: "error" }); });
    return () => controller.abort();
  }, [key, enabled]);
  if (result?.key === key) return result;
  const cached = identity && sourceId ? peekForecast(identity, sourceId)?.version : null;
  return { key, rows: cached && cached.id === sourceId ? cached.calculation.rows : null, issues: cached && cached.id === sourceId ? cached.calculation.issues ?? [] : [], status: cached && cached.id === sourceId ? "ready" as const : "loading" as const };
}
