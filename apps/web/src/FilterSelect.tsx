import { useEffect, useId, useLayoutEffect, useRef, useState, type KeyboardEvent } from "react";
import { createPortal } from "react-dom";
import { filterLabel, filterValues, toggleFilterValue, type FilterValue } from "./filterSelection";
import "./filterSelect.css";

type Option = { value: string; label: string };
type Props = {
  value: FilterValue;
  onChange: (value: FilterValue) => void;
  options: readonly (string | Option)[];
  "aria-label": string;
  disabled?: boolean;
  allLabel?: string;
};

// Focus must never scroll the page or a containing dialog. Reveal keyboard
// targets by moving only this listbox's own scrollport.
function focusOption(option: HTMLButtonElement | undefined | null, menu: HTMLDivElement | null) {
  if (!option || !menu) return;
  option.focus({ preventScroll: true });
  const rect = option.getBoundingClientRect(), top = menu.getBoundingClientRect().top + menu.clientTop;
  const bottom = top + menu.clientHeight;
  if (rect.top < top) menu.scrollTop += rect.top - top;
  else if (rect.bottom > bottom) menu.scrollTop += rect.bottom - bottom;
}

/** Same compact dropdown face; its menu selects several values without changing budget input. */
export function FilterSelect({ value, onChange, options, "aria-label": label, disabled = false, allLabel = "全部" }: Props) {
  const [open, setOpen] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null), menu = useRef<HTMLDivElement>(null), id = useId();
  const selected = filterValues(value), known = options.map(option => typeof option === "string" ? { value: option, label: option } : option);
  const entries = [{ value: "", label: allLabel }, ...[...new Map([...known,
    ...selected.filter(item => !known.some(option => option.value === item)).map(item => ({ value: item, label: item }))]
    .filter(option => option.value).map(option => [option.value, option])).values()]];
  const summary = selected.length ? selected.map(item => entries.find(option => option.value === item)?.label ?? item).join("、") : allLabel;
  const close = (restoreFocus = false) => { setOpen(false); if (restoreFocus) trigger.current?.focus({ preventScroll: true }); };
  useLayoutEffect(() => {
    if (!open) return;
    const popup = menu.current; if (!popup) return;
    const locate = () => {
      const rect = trigger.current?.getBoundingClientRect(); if (!rect) return;
      const width = Math.min(Math.max(rect.width, 224), window.innerWidth - 16);
      const below = Math.max(0, window.innerHeight - rect.bottom - 12), above = Math.max(0, rect.top - 12);
      const opensBelow = below >= 240 || below >= above;
      const height = Math.min(320, window.innerHeight - 16, opensBelow ? below : above);
      // Apply geometry synchronously before focusing. A state update here can
      // flush passive effects before a second positioning commit is painted.
      Object.assign(popup.style, { width: `${width}px`, maxHeight: `${height}px`,
        left: `${Math.max(8, Math.min(rect.left, window.innerWidth - width - 8))}px`,
        top: opensBelow ? `${rect.bottom + 4}px` : "auto",
        bottom: opensBelow ? "auto" : `${Math.max(8, window.innerHeight - rect.top + 4)}px` });
    };
    locate(); focusOption(popup.querySelector<HTMLButtonElement>('[aria-selected="true"]'), popup);
    window.addEventListener("resize", locate); window.addEventListener("scroll", locate, true);
    return () => { window.removeEventListener("resize", locate); window.removeEventListener("scroll", locate, true); };
  }, [open]);
  useEffect(() => {
    if (!open) return;
    const outside = (event: Event) => {
      const node = event.target as Node;
      if (!trigger.current?.contains(node) && !menu.current?.contains(node)) close();
    };
    document.addEventListener("pointerdown", outside); document.addEventListener("focusin", outside);
    return () => { document.removeEventListener("pointerdown", outside); document.removeEventListener("focusin", outside); };
  }, [open]);
  useEffect(() => { if (disabled) setOpen(false); }, [disabled]);
  const menuKeys = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.nativeEvent.isComposing || event.altKey || event.metaKey || event.ctrlKey) return;
    if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); close(true); }
    else if (event.key === "Tab") { close(true); }
    else if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
      event.preventDefault();
      const buttons = [...(menu.current?.querySelectorAll<HTMLButtonElement>('[role="option"]') ?? [])];
      const index = buttons.indexOf(document.activeElement as HTMLButtonElement);
      focusOption(buttons[event.key === "Home" ? 0 : event.key === "End" ? buttons.length - 1 : (index + (event.key === "ArrowDown" ? 1 : -1) + buttons.length) % buttons.length], menu.current);
    }
  };
  return <div className="budget-filter-select">
    <button ref={trigger} type="button" role="combobox" aria-label={label} aria-expanded={open} aria-haspopup="listbox" aria-controls={open ? id : undefined}
      aria-describedby={`${id}-value`} disabled={disabled} className="budget-filter-select__trigger" title={summary}
      onClick={() => setOpen(current => !current)} onKeyDown={event => {
        if ((event.key === "ArrowDown" || event.key === "ArrowUp") && !event.nativeEvent.isComposing) { event.preventDefault(); setOpen(true); }
        if (event.key === "Escape" && open) { event.preventDefault(); close(true); }
      }}><span>{summary}</span><span aria-hidden="true" className="budget-filter-select__arrow"/></button>
    <span id={`${id}-value`} className="budget-filter-select__value">{selected.length ? `已选：${filterLabel(value)}` : "未限制范围"}</span>
    {open && typeof document !== "undefined" && createPortal(<div ref={menu} id={id} role="listbox" aria-label={label} aria-multiselectable="true"
      className="budget-filter-select__menu" style={{ position: "fixed" }} onKeyDown={menuKeys}>
      {entries.map(option => {
        const active = option.value ? selected.includes(option.value) : selected.length === 0;
        return <button type="button" role="option" aria-selected={active} tabIndex={-1} key={option.value} className={active ? "is-selected" : ""}
          onClick={() => onChange(toggleFilterValue(value, option.value))}><span aria-hidden="true" className="budget-filter-select__check">{active ? "✓" : ""}</span><span>{option.label}</span></button>;
      })}
    </div>, trigger.current?.closest("dialog") ?? document.body)}
  </div>;
}
