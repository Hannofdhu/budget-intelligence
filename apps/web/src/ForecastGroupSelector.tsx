import { useEffect, useId, useRef, useState } from "react";
import { forecastGroupDimensions, forecastGroupDimensionLabels, type ForecastGroupDimension } from "./forecastApi";

type Reorder = { key: ForecastGroupDimension; order: readonly ForecastGroupDimension[]; target: number | null;
  pointer?: { id: number; x: number; y: number; lastX: number; lastY: number; moved: boolean; handle: HTMLButtonElement } };

/** Ordered reading dimensions only. No draft, budget command or second editing flow. */
export function ForecastGroupSelector({ value, onChange, disabled = false }: {
  value: readonly ForecastGroupDimension[]; onChange: (value: ForecastGroupDimension[]) => void; disabled?: boolean;
}) {
  const id = useId(), panel = useRef<HTMLDivElement>(null), trigger = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  const list = useRef<HTMLOListElement>(null), reorder = useRef<Reorder | null>(null);
  const scrollFrame = useRef<number | null>(null);
  const latest = useRef({ value, disabled, open }); latest.current = { value, disabled, open };
  const [preview, setPreview] = useState<{ key: ForecastGroupDimension; target: number | null } | null>(null);
  const [announcement, setAnnouncement] = useState("");
  const release = (session: Reorder | null) => {
    if (scrollFrame.current !== null) { window.cancelAnimationFrame(scrollFrame.current); scrollFrame.current = null; }
    const pointer = session?.pointer;
    if (pointer?.handle.hasPointerCapture(pointer.id)) pointer.handle.releasePointerCapture(pointer.id);
  };
  const valid = (session: Reorder) => !latest.current.disabled && latest.current.open
    && session.order.join() === latest.current.value.join();
  const finish = (commit = false) => {
    const session = reorder.current; reorder.current = null; setPreview(null); release(session);
    if (!session) return;
    const from = session.order.indexOf(session.key), target = session.target;
    if (!commit || !valid(session) || target === null || target === from || (session.pointer && !session.pointer.moved)) {
      setAnnouncement("排序未改变"); return;
    }
    const next = session.order.filter(key => key !== session.key); next.splice(target, 0, session.key);
    setAnnouncement(`${forecastGroupDimensionLabels[session.key]}已移到第${target + 1}位`); onChange(next);
  };
  const pointTarget = (x: number, y: number) => {
    const element = list.current, bounds = element?.getBoundingClientRect(), popupBounds = panel.current?.getBoundingClientRect();
    if (!element || !bounds || !popupBounds || !element.children.length || x < bounds.left || x > bounds.right
      || y < Math.max(bounds.top, popupBounds.top) || y > Math.min(bounds.bottom, popupBounds.bottom)) return null;
    const rows = Array.from(element.children);
    return rows.reduce((closest, row, index) => {
      const rect = row.getBoundingClientRect(), previous = rows[closest].getBoundingClientRect();
      return Math.abs(y - (rect.top + rect.bottom) / 2) < Math.abs(y - (previous.top + previous.bottom) / 2) ? index : closest;
    }, 0);
  };
  // Keep all six rows reachable in a short popup without scrolling the page.
  const scrollWhileDragging = () => {
    scrollFrame.current = null;
    const session = reorder.current, pointer = session?.pointer, popup = panel.current;
    if (!session || !pointer?.moved || !popup || !valid(session)) return;
    const bounds = popup.getBoundingClientRect();
    if (pointer.lastX < bounds.left || pointer.lastX > bounds.right || pointer.lastY < bounds.top || pointer.lastY > bounds.bottom) return;
    const delta = pointer.lastY < bounds.top + 32 ? -8 : pointer.lastY > bounds.bottom - 32 ? 8 : 0;
    if (!delta) return;
    const before = popup.scrollTop; popup.scrollTop += delta;
    if (popup.scrollTop === before) return;
    session.target = pointTarget(pointer.lastX, pointer.lastY); setPreview({ key: session.key, target: session.target });
    scrollFrame.current = window.requestAnimationFrame(scrollWhileDragging);
  };
  const updatePointer = (event: React.PointerEvent<HTMLButtonElement>) => {
    const session = reorder.current, pointer = session?.pointer;
    if (!session || !pointer || pointer.id !== event.pointerId) return;
    if (!valid(session)) { finish(); return; }
    pointer.moved ||= Math.hypot(event.clientX - pointer.x, event.clientY - pointer.y) >= 4;
    pointer.lastX = event.clientX; pointer.lastY = event.clientY;
    session.target = pointTarget(event.clientX, event.clientY);
    setPreview({ key: session.key, target: session.target });
    if (scrollFrame.current === null) scrollWhileDragging();
  };
  const locate = () => {
    const popup = panel.current, rect = trigger.current?.getBoundingClientRect();
    if (!popup || !rect) return;
    const width = Math.min(360, window.innerWidth - 32);
    const below = Math.max(0, window.innerHeight - rect.bottom - 12), above = Math.max(0, rect.top - 12);
    const opensBelow = below >= 360 || below >= above;
    Object.assign(popup.style, { width: `${width}px`, maxHeight: `${Math.min(520, opensBelow ? below : above)}px`,
      left: `${Math.max(16, Math.min(rect.right - width, window.innerWidth - width - 16))}px`,
      top: opensBelow ? `${rect.bottom + 4}px` : "auto", bottom: opensBelow ? "auto" : `${window.innerHeight - rect.top + 4}px` });
  };
  const close = () => { finish(); panel.current?.hidePopover(); trigger.current?.focus({ preventScroll: true }); };
  useEffect(() => {
    if (!open) return;
    window.addEventListener("resize", locate); window.addEventListener("scroll", locate, true);
    return () => { window.removeEventListener("resize", locate); window.removeEventListener("scroll", locate, true); };
  }, [open]);
  useEffect(() => { if (disabled) panel.current?.hidePopover(); }, [disabled]);
  useEffect(() => { if (reorder.current && !valid(reorder.current)) finish(); }, [disabled, open, value.join()]);
  useEffect(() => () => { const session = reorder.current; reorder.current = null; release(session); }, []);
  const pin = (key: ForecastGroupDimension) => {
    finish();
    if (latest.current.disabled || latest.current.value[0] === key || !latest.current.value.includes(key)) return;
    onChange([key, ...latest.current.value.filter(item => item !== key)]);
    setAnnouncement(`${forecastGroupDimensionLabels[key]}已置顶，显示在第一列`);
  };
  return <div className="forecast-group-selector">
    <button ref={trigger} type="button" popoverTarget={id} aria-controls={id} aria-expanded={open} disabled={disabled}
      className="forecast-group-trigger" aria-label={`分组维度：${value.map(key => forecastGroupDimensionLabels[key]).join("、")}，设置`}>
      <span><span className="forecast-group-caption">分组维度：</span>{value.map(key => forecastGroupDimensionLabels[key]).join("、")}</span><span>设置</span>
    </button>
    <div ref={panel} id={id} popover="auto" role="group" aria-label="分组维度设置" className="forecast-group-panel" style={{ position: "fixed" }}
      onBeforeToggle={event => { if ((event.nativeEvent as ToggleEvent).newState === "open") locate(); }}
      onToggle={event => { const isOpen = (event.nativeEvent as ToggleEvent).newState === "open"; if (!isOpen) finish(); setOpen(isOpen); }}
      onKeyDown={event => { if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); close(); } }}>
      <fieldset disabled={disabled}><legend>选择分组维度</legend><div className="forecast-group-options">
        {forecastGroupDimensions.map(key => <label key={key}><input type="checkbox" checked={value.includes(key)} disabled={value.length === 1 && value[0] === key}
          onChange={event => { finish(); onChange(event.target.checked ? [...value, key] : value.filter(item => item !== key)); }}/>{forecastGroupDimensionLabels[key]}</label>)}
      </div></fieldset>
      <p id={`${id}-order-help`} className="forecast-group-order-label">拖动调整列顺序，置顶后显示在第一列</p>
      <ol ref={list} className="forecast-group-order">{value.map((key, index) => <li key={key}
        className={preview?.key === key ? "is-dragging" : undefined}
        data-drop-position={preview?.target === index && preview.target !== value.indexOf(preview.key)
          ? (index < value.indexOf(preview.key) ? "before" : "after") : undefined}>
        <div className="forecast-group-order-identity">
          <button type="button" className="forecast-group-drag-handle" disabled={disabled || value.length === 1}
            aria-label={`拖动排序${forecastGroupDimensionLabels[key]}`} aria-describedby={`${id}-keyboard-help`}
            aria-pressed={preview?.key === key} title="拖动排序；也可按空格开始，方向键调整，空格确认"
            onPointerDown={event => {
              if (event.button !== 0 || !event.isPrimary || latest.current.disabled || value.length < 2) return;
              finish(); event.preventDefault(); event.currentTarget.focus({ preventScroll: true });
              reorder.current = { key, order: [...value], target: index,
                pointer: { id: event.pointerId, x: event.clientX, y: event.clientY, lastX: event.clientX, lastY: event.clientY, moved: false, handle: event.currentTarget } };
              event.currentTarget.setPointerCapture(event.pointerId); setPreview({ key, target: index });
            }}
            onPointerMove={updatePointer} onPointerUp={event => {
              if (reorder.current?.pointer?.id !== event.pointerId) return;
              updatePointer(event); finish(true);
            }}
            onPointerCancel={event => { if (reorder.current?.pointer?.id === event.pointerId) finish(); }}
            onLostPointerCapture={event => { if (reorder.current?.pointer?.id === event.pointerId) finish(); }}
            onBlur={() => { if (reorder.current && !reorder.current.pointer) finish(); }}
            onKeyDown={event => {
              const session = reorder.current;
              if (event.key === "Escape" && session) { event.preventDefault(); event.stopPropagation(); finish(); return; }
              if (latest.current.disabled || value.length < 2 || session?.pointer) return;
              if (event.key === " " || event.key === "Enter") {
                event.preventDefault(); event.stopPropagation();
                if (session) finish(true);
                else { reorder.current = { key, order: [...value], target: index }; setPreview({ key, target: index });
                  setAnnouncement(`${forecastGroupDimensionLabels[key]}已选中，使用方向键调整，空格确认，Escape取消`); }
              } else if (session?.key === key && ["ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) {
                event.preventDefault(); event.stopPropagation();
                session.target = event.key === "Home" ? 0 : event.key === "End" ? value.length - 1
                  : Math.max(0, Math.min(value.length - 1, (session.target ?? index) + (event.key === "ArrowUp" ? -1 : 1)));
                setPreview({ key, target: session.target }); setAnnouncement(`将移到第${session.target + 1}位，空格确认`);
              }
            }}><svg aria-hidden="true" width="16" height="20" viewBox="0 0 16 20" fill="currentColor">
              {[5, 10, 15].map(y => <g key={y}><circle cx="5" cy={y} r="1.4"/><circle cx="11" cy={y} r="1.4"/></g>)}
            </svg></button>
          <span>{index + 1}. {forecastGroupDimensionLabels[key]}</span>
        </div>
        <button type="button" className="forecast-group-pin" disabled={disabled || index === 0}
          aria-label={`置顶${forecastGroupDimensionLabels[key]}`} onClick={() => pin(key)}>{index === 0 ? "已在首位" : "置顶"}</button>
      </li>)}</ol>
      <span id={`${id}-keyboard-help`} className="forecast-group-sr-only">按空格开始排序，使用上下方向键调整，空格确认，Escape取消。至少保留一个维度。</span>
      <span role="status" className="forecast-group-sr-only">{announcement}</span>
      <div className="forecast-group-panel-footer"><span>选择后即时更新，仅改变展示</span><button type="button" onClick={close}>完成</button></div>
    </div>
  </div>;
}
