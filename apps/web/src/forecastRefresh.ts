/** A module switch detaches the page, not the user's submitted refresh. */
import { useSyncExternalStore } from "react";
import { newRequestId, type ApiIdentity } from "./api.ts";
import { budgetActorKey } from "./budgetReadCache.ts";
import { forecastFailure, refreshForecast, type ForecastEnvelope, type ForecastRequestError, type ForecastSeason, type ForecastState } from "./forecastApi.ts";

export type ForecastRefreshTask = {
  state: "running" | "succeeded" | "failed";
  intent: { requestId: string; states: Record<ForecastSeason, ForecastState>; reason: string };
  result: ForecastEnvelope | null;
  error: ForecastRequestError | null;
  promise: Promise<void>;
};
const tasks = new Map<string, ForecastRefreshTask>();
const listeners = new Set<() => void>();
export function subscribeForecastRefresh(listener: () => void) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}
export function getForecastRefresh(actor: ApiIdentity | null): ForecastRefreshTask | null {
  return actor ? tasks.get(budgetActorKey(actor)) ?? null : null;
}
export function useForecastRefresh(actor: ApiIdentity | null) {
  return useSyncExternalStore(subscribeForecastRefresh, () => getForecastRefresh(actor), () => null);
}
export function startForecastRefresh(actor: ApiIdentity, states: Record<ForecastSeason, ForecastState>, reason: string, retry = false): Promise<void> {
  const key = budgetActorKey(actor), previous = tasks.get(key);
  if (previous?.state === "running") return previous.promise;
  // A lost response may already have saved a version: retry the same intent/key.
  const intent = retry && previous?.state === "failed" ? previous.intent : { requestId: newRequestId(), states: { ...states }, reason };
  const task: ForecastRefreshTask = { state: "running", intent, result: null, error: null, promise: Promise.resolve() };
  const publish = (next: ForecastRefreshTask) => { tasks.set(key, next); listeners.forEach(listener => listener()); };
  task.promise = refreshForecast(actor, intent.states, intent.reason, intent.requestId).then(
    result => { publish({ ...task, state: "succeeded", result }); },
    error => { publish({ ...task, state: "failed", error: forecastFailure(error) }); },
  );
  publish(task);
  return task.promise;
}
