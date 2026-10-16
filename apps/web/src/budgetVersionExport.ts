import type { ExportColumn } from "./budgetExport";
import { versionFileStem, versionLabel, versionTimestamp, type VersionExportContext } from "./budgetVersionDisplay.ts";

/** Pure export context on an existing container; no extra state, wrapper or data loading. */
export function budgetVersionExportAttributes(context: VersionExportContext | null | undefined) {
  return context ? { "data-budget-export-context": JSON.stringify(context) } : {};
}

export function readVersionExportContext(element: Element): VersionExportContext | null {
  const raw = element.closest?.("[data-budget-export-context]")?.getAttribute("data-budget-export-context");
  if (!raw || raw.length > 4096) return null;
  try {
    const value = JSON.parse(raw);
    if (!value || !Number.isInteger(value.fiscalYear) || value.fiscalYear < 2000 || value.fiscalYear > 2200 || typeof value.object !== "string" || !value.object.trim() || value.object.length > 120) return null;
    if (value.sequence != null && (!Number.isSafeInteger(value.sequence) || value.sequence <= 0)) return null;
    return { fiscalYear: value.fiscalYear, object: value.object, sequence: value.sequence,
      createdAt: typeof value.createdAt === "string" ? value.createdAt : null, preview: value.preview === true,
      id: typeof value.id === "string" ? value.id : null, asOf: typeof value.asOf === "string" ? value.asOf : null,
      ...(typeof value.sourceId === "string" ? { sourceId: value.sourceId } : {}), ...(typeof value.sourceLabel === "string" ? { sourceLabel: value.sourceLabel } : {}) };
  } catch { return null; }
}

function versionExportMetadata(context: VersionExportContext): string[][] {
  const label = context.sequence == null ? "版本序号未记录" : versionLabel(context.sequence);
  return [
    ["_budget_source", "来源模块", `FY${String(context.fiscalYear).slice(-2)}${context.object}`],
    ["_budget_version", "结果版本", context.preview ? `未保存预览（基于${label}）` : label],
    ["_budget_version_id", context.preview ? "保存基版标识" : "精确版本标识", context.id || "未提供"],
    ["_budget_saved_at", "保存时间（北京时间）", versionTimestamp(context.createdAt)],
    ["_budget_as_of", "数据截至", context.asOf || "未记录"],
    ...(context.sourceLabel ? [["_budget_basis", "编制基准", context.sourceLabel]] : []),
    ...(context.sourceId ? [["_budget_basis_id", "精确来源标识", context.sourceId]] : []),
  ];
}

/** Preserve the complete explicit rectangular grid, including merged-heading expansion. */
export function withVersionCopyContext(rows: string[][], context: VersionExportContext | null): string[][] {
  if (!context || !rows.length) return rows;
  const metadata = versionExportMetadata(context);
  return rows.map((row, index) => [...row, ...metadata.map(([, label, value]) => index === 0 ? label : value)]);
}

export function withVersionExportContext<T extends { columns: ExportColumn[]; rows: object[]; filename: string }>(data: T, context: VersionExportContext | null): T {
  if (!context) return data;
  const metadata = versionExportMetadata(context);
  const keys = new Set(data.columns.map(column => column.key));
  const fields = metadata.filter(([key]) => !keys.has(key));
  const stem = versionFileStem(context);
  return { ...data, columns: [...data.columns, ...fields.map(([key, label]) => ({ key, label }))],
    rows: data.rows.map(row => ({ ...row, ...Object.fromEntries(fields.map(([key, , value]) => [key, value])) })),
    filename: data.filename.startsWith(stem) ? data.filename : `${stem}-${data.filename}` };
}
