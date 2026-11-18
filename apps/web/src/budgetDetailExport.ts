import type { ExportColumn } from "./budgetExport";

/** Detail exports use source fields, independently of the compact cell layout. */
export const skuDetailColumns: ExportColumn[] = [
  { key: "campus", label: "校区" },
  { key: "track", label: "赛道" },
  { key: "product", label: "产品" },
  { key: "grade", label: "年级" },
  { key: "subject", label: "科目" },
];

export function skuDetailValues(row: object): Record<string, unknown> {
  const source = row as Record<string, unknown>;
  return Object.fromEntries(skuDetailColumns.map(({ key }) => [key, source[key] ?? null]));
}
