import { useEffect, useId, useRef, useState, type ComponentProps, type ReactNode, type Ref } from "react";
import { downloadBudgetWorkbook, type ExportColumn } from "./budgetExport";
import { copyTableText, readRenderedTable, tableClipboardText, tableCopyRows, tableExportData } from "./tableTransfer";
import { moveTableSelection, renderedCellGrid, scrollTableViewportToEdge, selectedTableRows, tableMoveKeys, tableSelectionBounds, type TableMove, type TableSelection } from "./tableKeyboard";

type TransferData = { columns: ExportColumn[]; rows: object[]; filename: string };
type Props = ComponentProps<"table"> & { tableLabel?: string; exportData?: TransferData; copyRows?: string[][]; keyboardNavigation?: boolean;
  exportLabel?: string; toolbarStart?: ReactNode; scrollClassName?: string; scrollRef?: Ref<HTMLDivElement>; scrollLabel?: string };

/** All tables share honest, visible-table transfers; paged details may supply the full filtered dataset. */
export function ActionTable({ tableLabel, exportData, exportLabel, copyRows, keyboardNavigation = false, toolbarStart, scrollClassName, scrollRef, scrollLabel, children, ref: outerRef,
  onKeyDown, onClick, onFocus, onCopy, ...props }: Props) {
  const table = useRef<HTMLTableElement>(null), statusId = useId(), keyboardId = useId();
  const selection = useRef<{ range: TableSelection; signature: string } | null>(null);
  const [busy, setBusy] = useState(""), [notice, setNotice] = useState("");
  const [selectionLabel, setSelectionLabel] = useState("");
  const label = () => tableLabel || table.current?.parentElement?.getAttribute("aria-label") || "当前表格";
  const visible = () => table.current ? readRenderedTable(table.current).grid : [];
  const hasTextSelection = () => {
    const text = table.current?.ownerDocument.defaultView?.getSelection();
    return Boolean(text && !text.isCollapsed && text.toString());
  };
  const editingTable = () => Boolean(table.current?.isContentEditable || table.current?.closest("[contenteditable]:not([contenteditable='false'])"));
  const clearSelection = () => {
    if (table.current) for (const row of table.current.rows) for (const cell of row.cells) {
      cell.removeAttribute("data-table-selected"); cell.removeAttribute("data-table-active");
    }
    selection.current = null; setSelectionLabel("");
  };
  const select = (range: TableSelection, grid: string[][], scroll = false) => {
    if (!table.current) return;
    const owners = renderedCellGrid([...table.current.rows].map(row => [...row.cells]));
    const bounds = tableSelectionBounds(range), selected = new Set<HTMLTableCellElement>();
    for (let row = bounds.top; row <= bounds.bottom; row++) for (let column = bounds.left; column <= bounds.right; column++) {
      const cell = owners[row]?.[column]; if (cell) selected.add(cell);
    }
    const active = owners[range.focus.row]?.[range.focus.column];
    for (const row of table.current.rows) for (const cell of row.cells) {
      if (selected.has(cell)) cell.setAttribute("data-table-selected", "true"); else cell.removeAttribute("data-table-selected");
      if (cell === active) cell.setAttribute("data-table-active", "true"); else cell.removeAttribute("data-table-active");
    }
    selection.current = { range, signature: JSON.stringify(grid) };
    setSelectionLabel(`第 ${range.focus.row + 1} 行，第 ${range.focus.column + 1} 列 · 已选 ${(bounds.bottom - bounds.top + 1) * (bounds.right - bounds.left + 1)} 格`);
    if (scroll) active?.scrollIntoView({ block: "nearest", inline: "nearest" });
  };
  // Pagination, sorting and filtering replace visible cells; never retain a selection on old data.
  useEffect(() => { clearSelection(); }, [children, keyboardNavigation]);
  const copySelected = async () => {
    if (busy || !selection.current) return;
    const grid = visible();
    if (JSON.stringify(grid) !== selection.current.signature) { clearSelection(); setNotice("表格已变化，请重新选择"); return; }
    const rows = selectedTableRows(grid, selection.current.range);
    if (!rows.length) return;
    setBusy("selection"); setNotice("");
    try { await copyTableText(rows); setNotice(`已复制所选 ${rows.length} 行 × ${rows[0].length} 列`); }
    catch (failure) { setNotice(failure instanceof Error ? failure.message : "复制失败，请使用导出"); }
    finally { setBusy(""); }
  };
  const transfer = async (kind: "copy" | "export") => {
    if (!table.current || busy) return;
    setBusy(kind); setNotice("");
    try {
      const values = readRenderedTable(table.current);
      if (kind === "copy") { await copyTableText(copyRows ?? (exportData ? tableCopyRows(exportData.columns, exportData.rows) : values.grid)); setNotice("已复制"); }
      else {
        const data = exportData ?? { ...tableExportData(values.headings, values.body), filename: label() };
        await downloadBudgetWorkbook(data.columns, data.rows, data.filename);
        setNotice(`已导出${data.rows.length}条`);
      }
    } catch (failure) { setNotice(failure instanceof Error ? failure.message : "操作失败，请重试"); }
    finally { setBusy(""); }
  };
  const tableElement = <table {...props} tabIndex={keyboardNavigation ? props.tabIndex ?? 0 : props.tabIndex}
    aria-label={keyboardNavigation ? props["aria-label"] ?? tableLabel ?? "可选择复制的表格" : props["aria-label"]}
    aria-describedby={keyboardNavigation ? [props["aria-describedby"], keyboardId, statusId].filter(Boolean).join(" ") : props["aria-describedby"]}
    data-table-keyboard={keyboardNavigation || undefined}
    onFocus={event => {
      onFocus?.(event);
      if (!keyboardNavigation || event.defaultPrevented || event.target !== event.currentTarget || selection.current || editingTable() || hasTextSelection()) return;
      const grid = visible(); if (grid.length && grid[0]?.length) select({ anchor: { row: 0, column: 0 }, focus: { row: 0, column: 0 } }, grid);
    }}
    onKeyDown={event => {
      onKeyDown?.(event);
      if (!keyboardNavigation || event.defaultPrevented || event.target !== event.currentTarget || event.nativeEvent.isComposing || editingTable() || hasTextSelection()) return;
      const command = event.metaKey || event.ctrlKey;
      if (event.key === "Escape" && !command && !event.altKey && selection.current) { event.preventDefault(); clearSelection(); setNotice(""); return; }
      if (command && !event.altKey && !event.shiftKey && event.key.toLowerCase() === "c" && selection.current) { event.preventDefault(); void copySelected(); return; }
      if (!tableMoveKeys.has(event.key) || event.altKey || command && event.key !== "Home" && event.key !== "End") return;
      const grid = visible(), current = selection.current?.signature === JSON.stringify(grid) ? selection.current.range : null;
      const next = moveTableSelection(current, event.key as TableMove, grid.length, Math.max(0, ...grid.map(row => row.length)), event.shiftKey, command);
      if (next) {
        event.preventDefault(); setNotice("");
        const edge = command && (event.key === "Home" || event.key === "End");
        // The owned viewport must settle before revealing a sticky edge cell above fixed page chrome.
        if (scrollClassName && edge && table.current) {
          scrollTableViewportToEdge(table.current, event.key === "Home" ? "start" : "end");
        }
        select(next, grid, true);
        if (!scrollClassName && edge && table.current) {
          scrollTableViewportToEdge(table.current, event.key === "Home" ? "start" : "end");
        }
      }
    }}
    onCopy={event => {
      onCopy?.(event);
      if (!keyboardNavigation || event.defaultPrevented || busy || event.target !== event.currentTarget ||
        table.current?.ownerDocument.activeElement !== table.current || !selection.current || editingTable() || hasTextSelection()) return;
      const grid = visible();
      if (JSON.stringify(grid) !== selection.current.signature) { clearSelection(); setNotice("表格已变化，请重新选择"); return; }
      const rows = selectedTableRows(grid, selection.current.range);
      if (!rows.length) return;
      try {
        if (!event.clipboardData?.setData) throw new Error("浏览器无法直接复制，请点击“复制所选”或导出");
        // Native menu/system-copy commands may emit copy without a keydown in embedded browsers.
        event.clipboardData.setData("text/plain", tableClipboardText(rows));
        event.preventDefault(); setNotice(`已复制所选 ${rows.length} 行 × ${rows[0].length} 列`);
      } catch (failure) {
        event.preventDefault(); setNotice(failure instanceof Error ? failure.message : "复制失败，请点击“复制所选”或导出");
      }
    }}
    onClick={event => {
      onClick?.(event);
      if (!keyboardNavigation || event.defaultPrevented || event.detail > 1 || event.altKey || event.ctrlKey || event.metaKey || hasTextSelection()) return;
      const target = event.target as HTMLElement;
      if (target.closest("button,a,input,select,textarea,summary,[contenteditable]:not([contenteditable='false']),[role='button']")) return;
      const cell = target.closest<HTMLTableCellElement>("th,td");
      if (!cell || cell.closest("table") !== table.current) return;
      const owners = renderedCellGrid([...table.current!.rows].map(row => [...row.cells]));
      const row = owners.findIndex(cells => cells.includes(cell)), column = row >= 0 ? owners[row].indexOf(cell) : -1;
      if (row < 0 || column < 0) return;
      const grid = visible(), current = selection.current?.signature === JSON.stringify(grid) ? selection.current.range : null;
      const point = { row, column };
      setNotice(""); select({ anchor: event.shiftKey && current ? current.anchor : point, focus: point }, grid);
      table.current!.focus({ preventScroll: true });
    }} ref={node => {
    table.current = node;
    if (typeof outerRef === "function") return outerRef(node);
    if (outerRef) outerRef.current = node;
  }}>{children}</table>;
  return <><div className="table-transfer" role="group" aria-label={`${tableLabel ?? "表格"}复制与导出`}>
    {toolbarStart && <div className="table-transfer-start">{toolbarStart}</div>}
    <span id={statusId} role="status" aria-live="polite">{notice || selectionLabel}</span>
    {keyboardNavigation && <details className="table-keyboard-help"><summary>快捷键</summary><p id={keyboardId}>点击单元格或按 Tab 进入表格；方向键移动，Shift 连选，Home / End 到行首尾，Ctrl / ⌘ + Home / End 到表首尾，Ctrl / ⌘ + C 复制所选，Esc 清除。选区仅限当前页，一键复制和导出仍包含完整范围。</p></details>}
    {keyboardNavigation && selectionLabel && <button type="button" disabled={Boolean(busy)} onClick={() => void copySelected()}>{busy === "selection" ? "正在复制…" : "复制所选"}</button>}
    <button type="button" disabled={Boolean(busy)} onClick={() => void transfer("copy")}>{busy === "copy" ? "正在复制…" : "一键复制"}</button>
    <button type="button" disabled={Boolean(busy)} onClick={() => void transfer("export")}>{busy === "export" ? "正在导出…" : exportLabel ?? (exportData ? "导出明细" : "导出当前表格")}</button>
  </div>{scrollClassName ? <div className={scrollClassName} ref={scrollRef} aria-label={scrollLabel}>{tableElement}</div> : tableElement}</>;
}
