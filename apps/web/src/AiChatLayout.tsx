import { useEffect, useRef, useState, type CSSProperties, type PointerEvent, type ReactNode } from "react";

const storageKey = "budget.ai-chat-sidebar-width.v1";
type WidthStorage = Pick<Storage, "getItem" | "setItem" | "removeItem">;
function widthStorage(): WidthStorage | null {
  try { return typeof window === "undefined" ? null : window.localStorage; }
  catch { return null; }
}
export function sidebarBounds(available: number, knowledgeOpen: boolean) {
  const safe = Number.isFinite(available) ? available : 1280;
  return { min: 220, max: Math.max(220, Math.min(520, Math.floor(safe - (knowledgeOpen ? 804 : 480)))) };
}
export function sidebarWidth(preferred: number | null, available: number, knowledgeOpen: boolean) {
  const bounds = sidebarBounds(available, knowledgeOpen);
  const value = preferred !== null && Number.isFinite(preferred) ? preferred : available <= 1100 ? 236 : 264;
  return Math.round(Math.max(bounds.min, Math.min(bounds.max, value)));
}
export function readSidebarWidth(storage = widthStorage()): number | null {
  try {
    const raw = storage?.getItem(storageKey);
    if (!raw?.trim() || !Number.isFinite(Number(raw))) return null;
    return Math.round(Math.max(220, Math.min(520, Number(raw))));
  } catch { return null; }
}
export function saveSidebarWidth(width: number | null, storage = widthStorage()): void {
  try {
    if (width === null) storage?.removeItem(storageKey);
    else if (Number.isFinite(width)) storage?.setItem(storageKey, String(Math.round(Math.max(220, Math.min(520, width)))));
  } catch { /* Layout remains usable when browser preference storage is unavailable. */ }
}

type Drag = { id: number; x: number; width: number; before: number | null; target: HTMLDivElement };
export function AiChatLayout({ sidebarOpen, knowledgeOpen, children }: { sidebarOpen: boolean; knowledgeOpen: boolean; children: ReactNode }) {
  const [preferred, setPreferred] = useState(readSidebarWidth);
  const [geometry, setGeometry] = useState(() => ({ width: typeof window === "undefined" ? 1280 : window.innerWidth, viewport: typeof window === "undefined" ? 1280 : window.innerWidth }));
  const [resizing, setResizing] = useState(false);
  const layout = useRef<HTMLDivElement>(null), drag = useRef<Drag | null>(null);
  const width = sidebarWidth(preferred, geometry.width, knowledgeOpen), bounds = sidebarBounds(geometry.width, knowledgeOpen);
  const adjustable = sidebarOpen && geometry.viewport >= 960 && (!knowledgeOpen || geometry.viewport >= 1280);
  const release = () => {
    const current = drag.current; drag.current = null;
    if (current?.target.hasPointerCapture(current.id)) current.target.releasePointerCapture(current.id);
    return current;
  };
  const cancel = () => { const current = release(); if (current) { setPreferred(current.before); setResizing(false); } };
  useEffect(() => {
    const measure = () => {
      cancel();
      const next = { width: layout.current?.clientWidth || window.innerWidth, viewport: window.innerWidth };
      setGeometry(old => old.width === next.width && old.viewport === next.viewport ? old : next);
    };
    measure();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    if (layout.current) observer?.observe(layout.current);
    window.addEventListener("resize", measure); window.addEventListener("blur", cancel);
    return () => { release(); observer?.disconnect(); window.removeEventListener("resize", measure); window.removeEventListener("blur", cancel); };
  }, []);
  useEffect(() => { cancel(); }, [sidebarOpen, knowledgeOpen]);
  const commit = (next: number | null) => { setPreferred(next); saveSidebarWidth(next); };
  const pointerWidth = (event: PointerEvent<HTMLDivElement>) => {
    const current = drag.current;
    return current ? sidebarWidth(current.width + event.clientX - current.x, geometry.width, knowledgeOpen) : width;
  };
  return <div ref={layout} className={`ai-chat-layout ${sidebarOpen ? "" : "is-sidebar-closed"} ${knowledgeOpen ? "is-knowledge-open" : ""} ${resizing ? "is-sidebar-resizing" : ""}`}
    style={{ "--ai-chat-sidebar-width": `${width}px` } as CSSProperties}>
    {children}
    {adjustable && <div className="ai-chat-sidebar-resizer" role="separator" aria-label="调整对话侧栏宽度" aria-orientation="vertical" aria-controls="ai-chat-sidebar"
      aria-valuemin={bounds.min} aria-valuemax={bounds.max} aria-valuenow={width} aria-valuetext={`${width}像素`} tabIndex={0}
      title="拖动调整侧栏宽度；双击恢复默认。方向键调整，Shift加快，Home最窄，End最宽，Enter恢复默认，Escape取消拖动。"
      onPointerDown={event => {
        if (event.button !== 0 || !event.isPrimary || drag.current) return;
        event.preventDefault(); event.currentTarget.focus({ preventScroll: true });
        event.currentTarget.setPointerCapture(event.pointerId);
        drag.current = { id: event.pointerId, x: event.clientX, width, before: preferred, target: event.currentTarget }; setResizing(true);
      }}
      onPointerMove={event => { if (drag.current?.id === event.pointerId) setPreferred(pointerWidth(event)); }}
      onPointerUp={event => { if (drag.current?.id !== event.pointerId) return; const next = pointerWidth(event); release(); setResizing(false); commit(next); }}
      onPointerCancel={cancel} onLostPointerCapture={cancel}
      onDoubleClick={() => { cancel(); commit(null); }}
      onKeyDown={event => {
        if (event.key === "Escape") { if (drag.current) { event.preventDefault(); cancel(); } return; }
        let next: number | null;
        if (event.key === "Enter") next = null;
        else if (event.key === "Home") next = bounds.min;
        else if (event.key === "End") next = bounds.max;
        else if (event.key === "ArrowLeft" || event.key === "ArrowRight") next = sidebarWidth(width + (event.key === "ArrowRight" ? 1 : -1) * (event.shiftKey ? 50 : 10), geometry.width, knowledgeOpen);
        else return;
        event.preventDefault(); cancel(); commit(next);
      }}><span className="sr-only">拖动或使用左右方向键调整，双击或按Enter恢复默认宽度。</span></div>}
  </div>;
}
