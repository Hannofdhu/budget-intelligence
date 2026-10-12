import { useId, useRef, useState, type ComponentProps } from "react";
import { downloadBudgetWorkbook, type ExportColumn } from "./budgetExport";
import { copyTableText, readRenderedTable, tableCopyRows, tableExportData } from "./tableTransfer";

type TransferData = { columns: ExportColumn[]; rows: object[]; filename: string };
type Props = ComponentProps<"table"> & { tableLabel?: string; exportData?: TransferData; copyRows?: string[][] };

/** All tables share honest, visible-table transfers; paged details may supply the full filtered dataset. */
export function ActionTable({ tableLabel, exportData, copyRows, children, ref: outerRef, ...props }: Props) {
  const table = useRef<HTMLTableElement>(null), statusId = useId();
  const [busy, setBusy] = useState(""), [notice, setNotice] = useState("");
  const label = () => tableLabel || table.current?.parentElement?.getAttribute("aria-label") || "当前表格";
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
  return <><div className="table-transfer" role="group" aria-label={`${tableLabel ?? "表格"}复制与导出`}>
    <span id={statusId} role="status" aria-live="polite">{notice}</span>
    <button type="button" disabled={Boolean(busy)} onClick={() => void transfer("copy")}>{busy === "copy" ? "正在复制…" : "一键复制"}</button>
    <button type="button" disabled={Boolean(busy)} onClick={() => void transfer("export")}>{busy === "export" ? "正在导出…" : exportData ? "导出明细" : "导出当前表格"}</button>
  </div><table {...props} ref={node => {
    table.current = node;
    if (typeof outerRef === "function") return outerRef(node);
    if (outerRef) outerRef.current = node;
  }}>{children}</table></>;
}
