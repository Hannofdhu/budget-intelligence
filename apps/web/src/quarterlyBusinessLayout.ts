type ScrollGeometry = {
  wrapperTop: number;
  wrapperHeight: number;
  headerHeight: number;
  chromeBottom: number;
  footerHeight: number;
  viewportHeight: number;
};

/** Positions one real table header below the product chrome, never beyond its scroll container. */
export function quarterlyScrollGeometry({ wrapperTop, wrapperHeight, headerHeight, chromeBottom, footerHeight, viewportHeight }: ScrollGeometry) {
  const maxHeight = Math.max(96, viewportHeight - Math.max(0, chromeBottom) - Math.max(0, footerHeight) - 24);
  const headerOffset = Math.min(Math.max(0, chromeBottom - wrapperTop),
    Math.max(0, Math.min(wrapperHeight, maxHeight) - headerHeight - 1));
  return { maxHeight, headerOffset };
}

/** Stable React callback-ref: layout-only listeners also cover keyboard scrollIntoView. */
export function observeQuarterlyBusinessScroll(node: HTMLDivElement | null) {
  if (!node) return;
  const container = node;
  const ownerView = container.ownerDocument.defaultView;
  if (!ownerView) return;
  const view = ownerView;
  const shell = container.closest<HTMLElement>(".decision-shell");
  const observed = new Set<Element>();
  const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(() => measure());
  const observe = (element: Element) => {
    if (!observed.has(element)) { observed.add(element); observer?.observe(element); }
  };
  const set = (name: string, value: number) => {
    const pixels = `${value}px`;
    if (container.style.getPropertyValue(name) !== pixels) container.style.setProperty(name, pixels);
  };
  function measure() {
    const viewportHeight = view.innerHeight;
    const topChrome = [...(shell?.querySelectorAll<HTMLElement>(".fy27-topbar, .budget-page-navigation") ?? [])];
    const footers = [...(shell?.querySelectorAll<HTMLElement>(".ny-action-bar, .forecast-confirm, .forecast-confirmation-bar") ?? [])];
    const head = container.querySelector<HTMLElement>("thead");
    const chromeBottom = topChrome.reduce((bottom, element) => {
      observe(element);
      const rect = element.getBoundingClientRect();
      return rect.height > 0 && rect.bottom > 0 && rect.top < viewportHeight ? Math.max(bottom, rect.bottom) : bottom;
    }, 0);
    const footerHeight = footers.reduce((height, element) => {
      observe(element);
      const rect = element.getBoundingClientRect();
      return rect.height > 0 && rect.top < viewportHeight && rect.bottom > 0 ? Math.max(height, viewportHeight - Math.max(0, rect.top)) : height;
    }, 0);
    if (head) observe(head);
    const wrapper = container.getBoundingClientRect();
    const headerHeight = head?.getBoundingClientRect().height ?? 36;
    const geometry = quarterlyScrollGeometry({ wrapperTop: wrapper.top, wrapperHeight: wrapper.height,
      headerHeight, chromeBottom, footerHeight, viewportHeight });
    set("--qbt-header-height", headerHeight);
    set("--qbt-header-offset", geometry.headerOffset);
    set("--qbt-max-height", geometry.maxHeight);
  }
  observe(container); measure();
  // Capture nested scroll events as well as page scroll, including keyboard selection reveals.
  view.addEventListener("scroll", measure, { capture: true, passive: true });
  view.addEventListener("resize", measure);
  return () => {
    observer?.disconnect();
    view.removeEventListener("scroll", measure, true);
    view.removeEventListener("resize", measure);
    for (const name of ["--qbt-header-height", "--qbt-header-offset", "--qbt-max-height"]) container.style.removeProperty(name);
  };
}
