import type { ExportColumn } from "./budgetExport";
import { skuDetailColumns } from "./budgetDetailExport.ts";

const common = skuDetailColumns;
const season: ExportColumn = { key: "season", label: "季度" };
const students: ExportColumn[] = [
  { key: "old", label: "老生人次", format: "count" }, { key: "new", label: "新生人次", format: "count" },
  { key: "total", label: "总人次", format: "count" },
];
const revenue: ExportColumn = { key: "revenue", label: "预算结转收入（不含税，元）", format: "money" };
/** A saved '-' is applicability, never a percentage or an invented zero. */
export function historicalRenewalExport(value: unknown): { value: number | string | null; status: string } {
  if (value === "-") return { value: null, status: "不适用" };
  if (value === null || value === undefined || value === "") return { value: null, status: "未填写" };
  const numeric = typeof value === "number" ? value
    : typeof value === "string" && /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(value) ? Number(value) : NaN;
  if (!Number.isFinite(numeric)) return { value: null, status: "待核对" };
  return { value: value as number | string, status: numeric >= 0 && numeric <= 1 ? "适用" : "待核对" };
}
export function historicalExportRows<T extends { renewal?: unknown }>(step: number, rows: T[]) {
  return step === 3 ? rows.map(row => { const renewal = historicalRenewalExport(row.renewal);
    return { ...row, renewal: renewal.value, renewal_status: renewal.status }; }) : rows;
}
export function historicalExportColumns(step: number): ExportColumn[] {
  if (step === 1) return [...common, { key: "entry", label: "是否入口" }, { key: "annual", label: "全年结转收入（不含税，元）", format: "money" }];
  if (step === 2) return [...common, { key: "new", label: "暑假入口新生人次", format: "count" }];
  if (step === 3) return [...common, season, { key: "renewal", label: "续班率", format: "rate" }, { key: "renewal_status", label: "续班率状态" }, { key: "share", label: "新生占比", format: "rate" }, ...students];
  if (step === 4) return [...common, season,
    { key: "minimum", label: "班均", format: "count" }, { key: "price", label: "学费原价（元）", format: "money" },
    { key: "discount", label: "折扣率", format: "rate" }, { key: "recognition", label: "当季确认比例", format: "rate" },
    { key: "business", label: "业务收入（含税，元）", format: "money" }, revenue];
  return [...common, season, ...students, revenue];
}
