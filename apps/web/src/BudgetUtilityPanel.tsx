import { useEffect, useId, useRef, type ReactNode, type RefObject } from "react";
import "./budgetUtility.css";

type Props = { open: boolean; title: string; onClose: () => void; children: ReactNode; busy?: boolean; initialFocusRef?: RefObject<HTMLElement | null> };

/** Shared modal for secondary inspection. Callers keep drafts in their own state. */
export function BudgetUtilityPanel({ open, title, onClose, children, busy = false, initialFocusRef }: Props) {
  const dialog = useRef<HTMLDialogElement>(null), heading = useRef<HTMLHeadingElement>(null);
  const launcher = useRef<HTMLElement | null>(null);
  const titleId = useId();
  useEffect(() => {
    const element = dialog.current;
    if (!element) return;
    if (open) {
      if (!element.open) {
        launcher.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
        element.showModal();
        (initialFocusRef?.current ?? heading.current)?.focus();
      }
    } else if (element.open) {
      element.close();
      if (launcher.current?.isConnected) launcher.current.focus();
      launcher.current = null;
    }
  }, [open, initialFocusRef]);
  useEffect(() => () => {
    if (dialog.current?.open) dialog.current.close();
    if (launcher.current?.isConnected) launcher.current.focus();
  }, []);
  return <dialog ref={dialog} className="budget-utility-panel" aria-labelledby={titleId} aria-busy={busy || undefined}
    onCancel={event => { event.preventDefault(); if (!busy) onClose(); }}>
    <header className="budget-utility-heading"><h2 id={titleId} ref={heading} tabIndex={-1}>{title}</h2>
      <button type="button" disabled={busy} onClick={onClose} aria-label={`关闭${title}`}>关闭</button></header>
    <div className="budget-utility-content">{children}</div>
  </dialog>;
}
