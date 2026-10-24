import { useEffect, type MouseEvent } from "react";

export const budgetNavigationEvent = "budget:module-navigation";
export const budgetHistoryLeaveEvent = "budget:history-leave";

/** Soft navigation must keep the same dirty/write protection as document navigation. */
export function useBudgetModuleHistoryProtection(blocked: boolean, onBlocked: () => void) {
  useEffect(() => {
    if (!blocked) return;
    const returnUrl = window.location.href;
    const protect = (event: Event) => {
      event.preventDefault();
      window.history.pushState({}, "", returnUrl);
      onBlocked();
    };
    window.addEventListener(budgetHistoryLeaveEvent, protect);
    return () => window.removeEventListener(budgetHistoryLeaveEvent, protect);
  });
}
export function navigateBudgetModule(event: MouseEvent<HTMLAnchorElement>) {
  if (typeof window === "undefined" || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.currentTarget.target === "_blank") return;
  const url = new URL(event.currentTarget.href, window.location.href);
  if (url.origin !== window.location.origin) return;
  // Only the budget modules and read-only cockpit have explicit soft-navigation dirty
  // guards. Keep AI's unsent prompts and legacy editors behind their existing
  // native beforeunload protection; do not silently bypass it.
  const canSwitchInShell = (value: URL) => ["/budget/cockpit", "/budget/forecast", "/budget/next-year"].includes(value.pathname)
    || value.pathname === "/budget/workspace" && value.searchParams.get("archive") === "fy27";
  if (!canSwitchInShell(new URL(window.location.href)) || !canSwitchInShell(url)) return;
  event.preventDefault();
  window.history.pushState({}, "", url);
  window.dispatchEvent(new Event(budgetNavigationEvent));
  window.scrollTo({ top: 0, behavior: "instant" });
}
