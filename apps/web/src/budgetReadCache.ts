/** Session-memory only: no budget records or credentials are persisted in browser storage. */
import { parseResponse, type ApiIdentity } from "./api.ts";

type Entry = { value?: unknown; at: number; promise?: Promise<unknown> };
const entries = new Map<string, Entry>();
const freshMs = 30_000, retainedMs = 5 * 60_000;
export const budgetActorKey = (actor: ApiIdentity) => JSON.stringify([actor.userId, actor.departmentId, [...actor.roles].sort()]);
export const budgetReadKey = (actor: ApiIdentity, path: string) => `${budgetActorKey(actor)}:${path}`;

export function peekBudgetRead<T>(key: string): T | null {
  const entry = entries.get(key);
  return entry?.value !== undefined && Date.now() - entry.at < retainedMs ? entry.value as T : null;
}
export function storeBudgetRead<T>(key: string, value: T): T {
  // Replacing the entry also prevents an older in-flight read from publishing over a command.
  entries.delete(key);
  if (entries.size >= 24) entries.delete(entries.keys().next().value!);
  entries.set(key, { value, at: Date.now() });
  return value;
}
export function invalidateBudgetReads(prefix = "") {
  for (const key of entries.keys()) if (key.includes(prefix)) entries.delete(key);
}
function subscribed<T>(promise: Promise<T>, signal?: AbortSignal): Promise<T> {
  if (!signal) return promise;
  return new Promise((resolve, reject) => {
    const abort = () => reject(new DOMException("Aborted", "AbortError"));
    if (signal.aborted) { abort(); return; }
    signal.addEventListener("abort", abort, { once: true });
    promise.then(resolve, reject).finally(() => signal.removeEventListener("abort", abort));
  });
}
export function readBudgetResource<T>(key: string, loader: (signal: AbortSignal) => Promise<T>, signal?: AbortSignal): Promise<T> {
  let entry = entries.get(key);
  if (entry?.value !== undefined && Date.now() - entry.at < freshMs) return subscribed(Promise.resolve(entry.value as T), signal);
  if (!entry?.promise) {
    entry = { value: entry?.value, at: entry?.at ?? 0 };
    entries.set(key, entry);
    const current = entry;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 60_000);
    // Node-side diagnostics/tests need not stay alive solely for a browser read deadline.
    (timeout as unknown as { unref?: () => void }).unref?.();
    current.promise = loader(controller.signal).then(value => {
      if (entries.get(key) === current) storeBudgetRead(key, value);
      return value;
    }).catch(error => {
      if (entries.get(key) === current) {
        if (current.value !== undefined) entries.set(key, { value: current.value, at: current.at });
        else entries.delete(key);
      }
      if ([401, 403].includes(error?.status)) invalidateBudgetReads();
      if (controller.signal.aborted) throw Object.assign(new Error("读取已保存结果超时，请重试。"), { status: 0, code: "READ_TIMEOUT" });
      throw error;
    }).finally(() => clearTimeout(timeout));
  }
  // A page leaving cancels its subscription, not another page's shared background read.
  return subscribed(entry.promise as Promise<T>, signal);
}
export function getBudgetSession(signal?: AbortSignal): Promise<ApiIdentity> {
  return readBudgetResource("session", async sharedSignal => parseResponse<ApiIdentity>(await fetch("/api/v1/local-trial/session", { signal: sharedSignal })), signal);
}
export const peekBudgetSession = () => peekBudgetRead<ApiIdentity>("session");
