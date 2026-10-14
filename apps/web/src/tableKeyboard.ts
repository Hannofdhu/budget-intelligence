/** Read-only table coordinates. Values are never parsed or written back to budget inputs. */
export type TablePoint = { row: number; column: number };
export type TableSelection = { anchor: TablePoint; focus: TablePoint };
export type TableMove = "ArrowUp" | "ArrowDown" | "ArrowLeft" | "ArrowRight" | "Home" | "End";

const bounded = (value: number, size: number) => Math.max(0, Math.min(Number.isFinite(value) ? Math.trunc(value) : 0, size - 1));
export function clampTableSelection(selection: TableSelection | null, rows: number, columns: number): TableSelection | null {
  if (!selection || rows < 1 || columns < 1) return null;
  const point = (value: TablePoint) => ({ row: bounded(value.row, rows), column: bounded(value.column, columns) });
  return { anchor: point(selection.anchor), focus: point(selection.focus) };
}

export function tableSelectionBounds(selection: TableSelection) {
  return { top: Math.min(selection.anchor.row, selection.focus.row), bottom: Math.max(selection.anchor.row, selection.focus.row),
    left: Math.min(selection.anchor.column, selection.focus.column), right: Math.max(selection.anchor.column, selection.focus.column) };
}

/** Home/End stay in the current row. Ctrl/Cmd+Home/End reach the first/last cell. */
export function moveTableSelection(selection: TableSelection | null, key: TableMove, rows: number, columns: number,
  extend = false, tableEdge = false): TableSelection | null {
  if (rows < 1 || columns < 1) return null;
  const current = clampTableSelection(selection, rows, columns) ?? { anchor: { row: 0, column: 0 }, focus: { row: 0, column: 0 } };
  const focus = { ...current.focus };
  if (key === "ArrowUp") focus.row--;
  if (key === "ArrowDown") focus.row++;
  if (key === "ArrowLeft") focus.column--;
  if (key === "ArrowRight") focus.column++;
  if (key === "Home") { focus.column = 0; if (tableEdge) focus.row = 0; }
  if (key === "End") { focus.column = columns - 1; if (tableEdge) focus.row = rows - 1; }
  focus.row = bounded(focus.row, rows); focus.column = bounded(focus.column, columns);
  return { anchor: extend ? current.anchor : { ...focus }, focus };
}

/** Return only the visible rectangle, retaining zero, blanks, negative and precision strings. */
export function selectedTableRows(grid: string[][], selection: TableSelection | null): string[][] {
  const safe = clampTableSelection(selection, grid.length, Math.max(0, ...grid.map(row => row.length)));
  if (!safe) return [];
  const { top, bottom, left, right } = tableSelectionBounds(safe);
  return grid.slice(top, bottom + 1).map(row => Array.from({ length: right - left + 1 }, (_, index) => row[left + index] ?? ""));
}

/** Merged cells keep one DOM owner while exposing their logical spreadsheet positions. */
export function renderedCellGrid<T extends { rowSpan: number; colSpan: number }>(rows: T[][]): T[][] {
  const grid: T[][] = [];
  rows.forEach((cells, row) => {
    grid[row] ??= [];
    let column = 0;
    for (const cell of cells) {
      while (grid[row][column]) column++;
      const down = Math.max(1, Math.min(cell.rowSpan || 1, rows.length - row));
      const across = Math.max(1, Math.min(cell.colSpan || 1, 1000));
      for (let r = row; r < row + down; r++) {
        grid[r] ??= [];
        for (let c = column; c < column + across; c++) grid[r][c] = cell;
      }
      column += across;
    }
  });
  return grid;
}

export const tableMoveKeys = new Set<string>(["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Home", "End"]);

/** Sticky headings/footers may already be visible: edge navigation must move the table viewport itself. */
export function scrollTableViewportToEdge(table: HTMLTableElement, edge: "start" | "end"): boolean {
  const document = table.ownerDocument, view = document.defaultView;
  if (!view) return false;
  let node = table.parentElement;
  // Never reset page/window scrolling or unrelated tables.
  while (node && node !== document.body && node !== document.documentElement) {
    const style = view.getComputedStyle(node);
    if (/(?:auto|scroll|overlay)/.test(`${style.overflowX} ${style.overflowY}`)) {
      node.scrollTop = edge === "start" ? 0 : Math.max(0, node.scrollHeight - node.clientHeight);
      node.scrollLeft = edge === "start" ? 0 : Math.max(0, node.scrollWidth - node.clientWidth);
      return true;
    }
    node = node.parentElement;
  }
  return false;
}
