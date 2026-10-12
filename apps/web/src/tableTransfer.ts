import type { ExportColumn } from "./budgetExport";

export type TableCellValue = { value: string; rowSpan?: number; colSpan?: number };

/** Expand merged headings and row groups for a rectangular Excel clipboard. */
export function expandTableCells(rows: TableCellValue[][]): string[][] {
  const grid: string[][] = [];
  rows.forEach((cells, rowIndex) => {
    grid[rowIndex] ??= [];
    let column = 0;
    for (const cell of cells) {
      while (grid[rowIndex][column] !== undefined) column++;
      const rowSpan = Math.max(1, Math.min(cell.rowSpan || 1, rows.length - rowIndex));
      const colSpan = Math.max(1, Math.min(cell.colSpan || 1, 1000));
      for (let down = 0; down < rowSpan; down++) {
        grid[rowIndex + down] ??= [];
        for (let across = 0; across < colSpan; across++) grid[rowIndex + down][column + across] = cell.value;
      }
      column += colSpan;
    }
  });
  const width = Math.max(0, ...grid.map(row => row.length));
  return grid.map(row => Array.from({ length: width }, (_, column) => row[column] ?? ""));
}

const plainNumber = /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i;
const escapedCell = (value: string) => {
  // Text copied to Excel must not become an executable formula. Real negatives stay numeric.
  const safe = /^[=+@-]/.test(value) && !plainNumber.test(value.replaceAll(",", "")) ? `'${value}` : value;
  return /[\t\n\r"]/.test(safe) ? `"${safe.replaceAll('"', '""')}"` : safe;
};
export const tableClipboardText = (rows: string[][]) => rows.map(row => row.map(escapedCell).join("\t")).join("\n");

/** Use the complete filtered dataset for paged tables; headings retain export units. */
export function tableCopyRows(columns: ExportColumn[], rows: object[]): string[][] {
  return [columns.map(column => column.label), ...rows.map(row => columns.map(column => {
    const value = (row as Record<string, unknown>)[column.key];
    if (value === null || value === undefined) return "";
    if (typeof value === "boolean") return value ? "是" : "否";
    if (column.format === "rate" && plainNumber.test(String(value))) return `${Number(value) * 100}%`;
    return String(value);
  }))];
}

export function tableExportData(headings: string[], body: string[][]) {
  const columns: ExportColumn[] = headings.map((label, index) => {
    const textual = /季度|季节|年级|校区|赛道|产品|科目|版本|修订|编号|\bID\b|时间|日期|操作|状态|业务维度|范围/.test(label) && !/收入|人次|班量|班额|率|占比|折扣|金额|差额|目标|缺口/.test(label);
    const values = body.map(row => (row[index] ?? "").trim()).filter(value => value && value !== "—" && value !== "-");
    const percent = values.length > 0 && values.every(value => plainNumber.test(value.replaceAll(",", "").replace(/%$/, "")) && value.endsWith("%"));
    const numeric = values.length > 0 && values.every(value => plainNumber.test(value.replaceAll(",", "")));
    return { key: String(index), label, ...(!textual && (percent || numeric) ? { format: percent ? "rate" as const : /元|金额|收入|差额|目标|缺口/.test(label) ? "money" as const : "count" as const } : {}) };
  });
  const rows = body.map(cells => Object.fromEntries(columns.map((column, index) => {
    const raw = cells[index] ?? "";
    if (!column.format) return [column.key, raw];
    if (!raw.trim() || raw === "—" || raw === "-") return [column.key, raw === "-" ? "-" : null];
    const number = Number(raw.replaceAll(",", "").replace(/%$/, ""));
    return [column.key, Number.isFinite(number) ? column.format === "rate" ? number / 100 : number : raw];
  })));
  return { columns, rows };
}

function cellText(cell: HTMLTableCellElement): string {
  const fields = [...cell.querySelectorAll<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>("input,select,textarea")];
  if (fields.length) return fields.map(field => {
    if (field.tagName === "SELECT") return [...(field as HTMLSelectElement).selectedOptions].map(option => option.text).join("、");
    if (field instanceof HTMLInputElement && field.type === "checkbox") return field.checked ? "是" : "否";
    return field.value;
  }).join(" / ");
  return (cell.innerText ?? cell.textContent ?? "").replace(/\s+/g, " ").trim();
}

export function readRenderedTable(table: HTMLTableElement) {
  const rows = [...table.rows];
  const grid = expandTableCells(rows.map(row => [...row.cells].map(cell => ({ value: cellText(cell), rowSpan: cell.rowSpan, colSpan: cell.colSpan }))));
  const headingCount = table.tHead?.rows.length ?? 0;
  const width = grid[0]?.length ?? 0;
  const headings = Array.from({ length: width }, (_, column) => [...new Set(grid.slice(0, headingCount).map(row => row[column]).filter(Boolean))].join(" / ") || `列${column + 1}`);
  return { grid, headings, body: grid.slice(headingCount) };
}

export async function copyTableText(rows: string[][]): Promise<void> {
  if (!navigator.clipboard?.writeText) throw new Error("当前浏览器不能复制，请使用导出");
  await navigator.clipboard.writeText(tableClipboardText(rows));
}
