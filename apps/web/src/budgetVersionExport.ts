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
      ...(typeof value.sourceId === "string" ? { sourceId: value.sourceId } : {}), ...(typeof value.sourceLabel === "string" ? { sourceLabel: value.sourceLabel } : {}),
      ...(Number.isInteger(value.sourceFiscalYear) && value.sourceFiscalYear >= 2000 && value.sourceFiscalYear <= 2200 ? { sourceFiscalYear: value.sourceFiscalYear } : {}),
      ...(Number.isSafeInteger(value.sourceSequence) && value.sourceSequence > 0 ? { sourceSequence: value.sourceSequence } : {}),
      ...(Number.isInteger(value.sourceFiscalSeason) && value.sourceFiscalSeason >= 20001 && value.sourceFiscalSeason <= 22004 && value.sourceFiscalSeason % 10 >= 1 && value.sourceFiscalSeason % 10 <= 4 ? { sourceFiscalSeason: value.sourceFiscalSeason } : {}),
      ...Object.fromEntries(["sourceObject", "sourceDataset", "sourcePartition"].filter(key => typeof value[key] === "string").map(key => [key, value[key]])) };
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

/** Analytical exports keep version identity, state and source fields independent. */
function versionDetailMetadata(context: VersionExportContext): { column: ExportColumn; value: string | number | null }[] {
  return [
    { column: { key: "_budget_fiscal_year", label: "来源财年", format: "count" }, value: context.fiscalYear },
    { column: { key: "_budget_source", label: "来源模块" }, value: context.object },
    { column: { key: "_budget_version", label: "结果版本" }, value: context.sequence == null ? null : versionLabel(context.sequence) },
    { column: { key: "_budget_result_state", label: "结果数据状态" }, value: context.preview ? "未保存预览" : "已保存" },
    { column: { key: "_budget_version_id", label: context.preview ? "保存基版标识" : "精确版本标识" }, value: context.id ?? null },
    { column: { key: "_budget_saved_at", label: "保存时间（北京时间）" }, value: versionTimestamp(context.createdAt) === "未记录" ? null : versionTimestamp(context.createdAt) },
    { column: { key: "_budget_as_of", label: "数据截至" }, value: context.asOf ?? null },
    ...(context.sourceLabel ? [{ column: { key: "_budget_basis", label: "来源说明" }, value: context.sourceLabel }] : []),
    ...(context.sourceId ? [{ column: { key: "_budget_basis_id", label: "精确来源标识" }, value: context.sourceId }] : []),
    ...(context.sourceFiscalYear != null ? [{ column: { key: "_budget_basis_year", label: "基准财年", format: "count" as const }, value: context.sourceFiscalYear }] : []),
    ...(context.sourceObject ? [{ column: { key: "_budget_basis_object", label: "基准模块" }, value: context.sourceObject }] : []),
    ...(context.sourceSequence != null ? [{ column: { key: "_budget_basis_sequence", label: "基准版本序号", format: "count" as const }, value: context.sourceSequence }] : []),
    ...(context.sourceFiscalSeason != null ? [{ column: { key: "_budget_basis_season", label: "来源财季编号", format: "count" as const }, value: context.sourceFiscalSeason }] : []),
    ...(context.sourceDataset ? [{ column: { key: "_budget_basis_dataset", label: "来源数据集" }, value: context.sourceDataset }] : []),
    ...(context.sourcePartition ? [{ column: { key: "_budget_basis_partition", label: "来源分区" }, value: context.sourcePartition }] : []),
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
  const metadata = versionDetailMetadata(context);
  const keys = new Set(data.columns.map(column => column.key));
  const fields = metadata.filter(({ column }) => !keys.has(column.key));
  const stem = versionFileStem(context);
  return { ...data, columns: [...data.columns, ...fields.map(({ column }) => column)],
    rows: data.rows.map(row => ({ ...row, ...Object.fromEntries(fields.map(({ column, value }) => [column.key, value])) })),
    filename: data.filename.startsWith(stem) ? data.filename : `${stem}-${data.filename}` };
}
