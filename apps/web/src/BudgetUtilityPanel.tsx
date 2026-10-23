import { useEffect, useId, useRef, type ReactNode, type RefObject } from "react";
import "./budgetUtility.css";

type Props = { open: boolean; title: string; onClose: () => void; children: ReactNode; busy?: boolean; initialFocusRef?: RefObject<HTMLElement | null>; className?: string; footer?: ReactNode };

/** Non-modal inspection tool. Callers keep drafts; the shared layout reserves its space. */
export function BudgetUtilityPanel({ open, title, onClose, children, busy = false, initialFocusRef, className = "", footer }: Props) {
  const dialog = useRef<HTMLDialogElement>(null), heading = useRef<HTMLHeadingElement>(null);
  const launcher = useRef<HTMLElement | null>(null);
  const wasOpen = useRef(false);
  const titleId = useId();
  useEffect(() => {
    const element = dialog.current;
    if (!element) return;
    if (open) {
      if (!wasOpen.current) {
        launcher.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
        (initialFocusRef?.current ?? heading.current)?.focus({ preventScroll: true });
        if (window.matchMedia?.("(width < 1440px)").matches) heading.current?.scrollIntoView({ block: "start" });
      }
    } else if (wasOpen.current) {
      if (launcher.current?.isConnected) {
        launcher.current.focus({ preventScroll: true });
        if (window.matchMedia?.("(width < 1440px)").matches) launcher.current.scrollIntoView({ block: "center" });
      }
      launcher.current = null;
    }
    wasOpen.current = open;
  }, [open, initialFocusRef]);
  useEffect(() => () => {
    if (launcher.current?.isConnected) launcher.current.focus({ preventScroll: true });
  }, []);
  return <dialog ref={dialog} open={open} className={`budget-utility-panel${className ? ` ${className}` : ""}`} aria-labelledby={titleId} aria-busy={busy || undefined}
    onKeyDown={event => { if (event.key === "Escape" && !event.defaultPrevented) { event.preventDefault(); event.stopPropagation(); if (!busy) onClose(); } }}
    onCancel={event => { event.preventDefault(); if (!busy) onClose(); }}>
    <header className="budget-utility-heading"><h2 id={titleId} ref={heading} tabIndex={-1}>{title}</h2>
      <button type="button" disabled={busy} onClick={onClose} aria-label={`关闭${title}`}>关闭</button></header>
    <div className="budget-utility-content">{children}</div>
    {footer && <footer className="budget-utility-footer">{footer}</footer>}
  </dialog>;
}
