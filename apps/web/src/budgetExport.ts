export type ExportColumn = { key: string; label: string; format?: "money" | "count" | "rate" };
const formats = { money: "#,##0.00", count: "#,##0.########", rate: "0.00%" };

// The export is a view of saved results, not a new calculation or budget revision.
export async function budgetWorkbook(columns: ExportColumn[], rows: object[], sheetName = "预算明细") {
  const { default: ExcelJS } = await import("exceljs");
  const workbook = new ExcelJS.Workbook();
  const sheet = workbook.addWorksheet(sheetName, { views: [{ state: "frozen", ySplit: 1, xSplit: 1 }] });
  sheet.columns = columns.map(column => ({
    header: column.label, key: column.key, width: column.format ? 24 : 18,
    style: column.format ? { numFmt: formats[column.format] } : {},
  }));
  for (const row of rows) {
    const record = row as Record<string, unknown>;
    sheet.addRow(columns.map(column => {
      const value = record[column.key];
      if (value === null || value === undefined || value === "") return null;
      // Decimal strings in API results become numbers only for explicitly numeric columns.
      if (column.format && typeof value === "string" && /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(value)) {
        const numeric = Number(value);
        if (Number.isFinite(numeric)) return numeric;
      }
      if (typeof value === "number") {
        if (!Number.isFinite(value)) throw new Error(`${column.label}存在无效数值，未导出`);
        return value;
      }
      // String cells are literal strings, never formula objects. Keep '-' and identifiers intact.
      return typeof value === "boolean" ? value : String(value);
    }));
  }
  sheet.getRow(1).font = { bold: true, color: { argb: "FFFFFFFF" } };
  sheet.getRow(1).fill = { type: "pattern", pattern: "solid", fgColor: { argb: "FF173F36" } };
  sheet.getRow(1).height = 30;
  sheet.getRow(1).alignment = { vertical: "middle", wrapText: true };
  sheet.autoFilter = { from: { row: 1, column: 1 }, to: { row: rows.length + 1, column: columns.length } };
  return workbook;
}

export async function downloadBudgetWorkbook(columns: ExportColumn[], rows: object[], filename: string, sheetName?: string) {
  const workbook = await budgetWorkbook(columns, rows, sheetName);
  const bytes = await workbook.xlsx.writeBuffer();
  const url = URL.createObjectURL(new Blob([new Uint8Array(bytes)], { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" }));
  const link = document.createElement("a");
  link.href = url; link.download = `${filename}.xlsx`; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
