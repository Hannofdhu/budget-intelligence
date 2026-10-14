import { useEffect, useRef, type RefObject } from "react";

const positionKey = "budgetPagePosition";

/** Measure the actual chrome height, including the two-row narrow-screen header. */
export function observeBudgetChrome(node: HTMLElement | null, property: string) {
  if (!node) return;
  const shell = node.closest<HTMLElement>(".decision-shell");
  if (!shell) return;
  const measure = () => shell.style.setProperty(property, `${node.getBoundingClientRect().height}px`);
  measure();
  const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
  observer?.observe(node);
  window.addEventListener("resize", measure);
  return () => { observer?.disconnect(); window.removeEventListener("resize", measure); };
}

function rememberPosition() {
  history.replaceState({ ...history.state, [positionKey]: { x: window.scrollX, y: window.scrollY } }, "", new URL(location.href));
}

/** Only deliberate page navigation moves focus; back/forward restores the reading position. */
export function useBudgetPagePosition(_page: number, ready: boolean, heading: RefObject<HTMLHeadingElement | null>) {
  const navigate = useRef(false), restoring = useRef(false);
  const restorePosition = useRef<{ x: number; y: number; href: string } | null>(null);
  useEffect(() => {
    // Keep native restoration available for URL entries created by filters/linked views.
    let timer: ReturnType<typeof setTimeout> | undefined;
    const remember = () => {
      clearTimeout(timer);
      timer = setTimeout(() => { if (!restoring.current) rememberPosition(); }, 120);
    };
    const restore = (event: PopStateEvent) => {
      navigate.current = false;
      const position = event.state?.[positionKey];
      restorePosition.current = position && Number.isFinite(position.x) && Number.isFinite(position.y)
        ? { ...position, href: location.href } : null;
      restoring.current = Boolean(restorePosition.current);
    };
    rememberPosition();
    window.addEventListener("scroll", remember, { passive: true });
    window.addEventListener("popstate", restore);
    return () => {
      window.removeEventListener("scroll", remember);
      window.removeEventListener("popstate", restore);
      clearTimeout(timer);
    };
  }, []);
  useEffect(() => {
    if (!ready || (!navigate.current && !restorePosition.current)) return;
    const position = restorePosition.current;
    let restoreFrame: number | undefined;
    const frame = requestAnimationFrame(() => {
      if (position) {
        // Let the new DOM and the browser's native popstate restoration settle first.
        restoreFrame = requestAnimationFrame(() => {
          // Cross-year loading/failed leave guards may change the URL again before it is ready.
          if (restorePosition.current === position && location.href === position.href) {
            window.scrollTo({ left: position.x, top: position.y, behavior: "instant" });
          }
          restorePosition.current = null;
          restoring.current = false;
        });
        return;
      }
      const title = heading.current;
      if (!title) return;
      navigate.current = false;
      title.focus({ preventScroll: true });
      title.scrollIntoView({ block: "start", behavior: "instant" });
      rememberPosition();
    });
    return () => { cancelAnimationFrame(frame); if (restoreFrame !== undefined) cancelAnimationFrame(restoreFrame); };
  });
  return () => { rememberPosition(); restorePosition.current = null; restoring.current = false; navigate.current = true; };
}

export function BudgetPageNavigation({ labels, current, onChange, label }: {
  labels: readonly string[]; current: number; onChange: (page: number) => void; label: string;
}) {
  const navigation = useRef<HTMLElement | null>(null);
  useEffect(() => {
    const nav = navigation.current;
    if (!nav) return;
    const revealCurrent = () => {
      const active = nav.querySelector<HTMLElement>("[aria-current='page']");
      if (!active) return;
      const outer = nav.getBoundingClientRect(), inner = active.getBoundingClientRect();
      if (inner.left < outer.left) nav.scrollLeft -= outer.left - inner.left + 8;
      else if (inner.right > outer.right) nav.scrollLeft += inner.right - outer.right + 8;
    };
    revealCurrent();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(revealCurrent);
    observer?.observe(nav);
    window.addEventListener("resize", revealCurrent);
    return () => { observer?.disconnect(); window.removeEventListener("resize", revealCurrent); };
  }, [current]);
  return <nav className="budget-page-navigation" aria-label={label} ref={node => {
    navigation.current = node;
    return observeBudgetChrome(node, "--budget-page-nav-height");
  }}>{labels.map((name, index) => <button type="button" key={name}
    aria-current={current === index + 1 ? "page" : undefined}
    onClick={() => { if (current !== index + 1) onChange(index + 1); }}>
    <span aria-hidden="true">{index + 1}</span><strong>{name}</strong>
  </button>)}</nav>;
}
