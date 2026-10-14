/** Layout only: sticky metric headers follow the actual quarter-header height.
 * A callback ref returns its observer cleanup for React; no table values or
 * interaction state are read, rewritten or calculated here.
 */
export function observeBudgetTableHeader(container: HTMLDivElement | null): (() => void) | undefined {
  const row = container?.querySelector<HTMLTableRowElement>("thead > tr");
  if (!container || !row) return;
  let active = true;
  const measure = () => {
    if (!active) return;
    const height = row.getBoundingClientRect().height;
    if (Number.isFinite(height) && height > 0) container.style.setProperty("--ba-header-row-height", `${height}px`);
  };
  measure();
  const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
  observer?.observe(row);
  const view = container.ownerDocument?.defaultView;
  view?.addEventListener("resize", measure);
  return () => {
    active = false;
    observer?.disconnect();
    view?.removeEventListener("resize", measure);
    container.style.removeProperty("--ba-header-row-height");
  };
}
