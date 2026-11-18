import { useEffect, type MouseEvent } from "react";

export const budgetNavigationEvent = "budget:module-navigation";
export const budgetHistoryLeaveEvent = "budget:history-leave";
export const budgetModuleLeaveEvent = "budget:module-leave";

const canSwitchInShell = (url: URL) => ["/budget/cockpit", "/budget/forecast", "/budget/next-year", "/budget/ai"].includes(url.pathname)
  || url.pathname === "/budget/workspace" && url.searchParams.get("archive") === "fy27";

/** Soft navigation must keep the same dirty/write protection as document navigation. */
export function useBudgetModuleHistoryProtection(blocked: boolean, onBlocked: () => void) {
  useEffect(() => {
    if (!blocked) return;
    const returnUrl = window.location.href;
    const protect = (event: Event) => {
      if (!event.defaultPrevented) window.history.pushState({}, "", returnUrl);
      event.preventDefault();
      onBlocked();
    };
    window.addEventListener(budgetHistoryLeaveEvent, protect);
    return () => window.removeEventListener(budgetHistoryLeaveEvent, protect);
  });
}

/** Editors without an anchor-level guard must also protect in-document navigation. */
export function useBudgetModuleLeaveProtection(blocked: boolean, onBlocked: () => void) {
  useBudgetModuleHistoryProtection(blocked, onBlocked);
  useEffect(() => {
    if (!blocked) return;
    const protect = (event: Event) => { event.preventDefault(); onBlocked(); };
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener(budgetModuleLeaveEvent, protect);
    window.addEventListener("beforeunload", warn);
    return () => { window.removeEventListener(budgetModuleLeaveEvent, protect); window.removeEventListener("beforeunload", warn); };
  }, [blocked, onBlocked]);
}

/** Commit only after the originating page has allowed leaving (or the user confirmed discarding). */
export function commitBudgetModuleNavigation(href: string): boolean {
  if (typeof window === "undefined") return false;
  const url = new URL(href, window.location.href);
  if (url.origin !== window.location.origin || !canSwitchInShell(new URL(window.location.href)) || !canSwitchInShell(url)) return false;
  window.history.pushState({}, "", url);
  window.dispatchEvent(new Event(budgetNavigationEvent));
  window.scrollTo({ top: 0, behavior: "instant" });
  return true;
}

export function navigateBudgetModule(event: MouseEvent<HTMLAnchorElement>) {
  if (typeof window === "undefined" || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.currentTarget.target === "_blank") return;
  const url = new URL(event.currentTarget.href, window.location.href);
  if (url.origin !== window.location.origin) return;
  if (!canSwitchInShell(new URL(window.location.href)) || !canSwitchInShell(url)) return;
  event.preventDefault();
  if (!window.dispatchEvent(new Event(budgetModuleLeaveEvent, { cancelable: true }))) return;
  commitBudgetModuleNavigation(url.href);
}
