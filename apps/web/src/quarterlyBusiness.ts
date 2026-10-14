import type { ExportColumn } from "./budgetExport";

export type QuarterlyValue = string | number | boolean | null | undefined;
export type QuarterlyValueColumn<Row> = {
  key: string;
  label: string;
  format?: ExportColumn["format"];
  value: (row: Row) => QuarterlyValue;
};
export type QuarterlyBusinessRow<Row> = {
  key: string;
  businessKey: string;
  sample: Row;
  bySeason: ReadonlyMap<string, Row>;
  duplicate: boolean;
};

/** A reading arrangement only: no sums, averages, inferred quarters or input changes. */
export function arrangeQuarterlyBusiness<Row>(rows: readonly Row[], rowKey: (row: Row) => string,
  season: (row: Row) => string, preferredSeasons: readonly string[] = ["暑假", "秋季", "寒假", "春季"]) {
  const seasons = [...new Set([...preferredSeasons, ...rows.map(season)])];
  const businesses = new Map<string, Row[]>();
  for (const row of rows) {
    const key = rowKey(row);
    const members = businesses.get(key) ?? [];
    members.push(row); businesses.set(key, members);
  }
  const groups: QuarterlyBusinessRow<Row>[] = [];
  let duplicateCount = 0;
  for (const [businessKey, members] of businesses) {
    const seen = new Set<string>();
    const duplicate = members.some(row => { const value = season(row); if (seen.has(value)) return true; seen.add(value); return false; });
    if (duplicate) {
      // Exact identity is ambiguous: keep every source line separate, including other quarters.
      // Pairing the nth occurrence across quarters would invent a business relationship.
      duplicateCount++;
      members.forEach((row, index) => groups.push({ key: JSON.stringify([businessKey, index]), businessKey,
        sample: row, bySeason: new Map([[season(row), row]]), duplicate: true }));
    } else {
      groups.push({ key: JSON.stringify([businessKey]), businessKey, sample: members[0],
        bySeason: new Map(members.map(row => [season(row), row])), duplicate: false });
    }
  }
  return { seasons, groups, duplicateCount };
}

/** Full filtered range, raw values and numeric export formats; absent quarters stay blank. */
export function quarterlyBusinessExport<Row>(groups: readonly QuarterlyBusinessRow<Row>[], seasons: readonly string[],
  businessColumns: readonly QuarterlyValueColumn<Row>[], metrics: readonly QuarterlyValueColumn<Row>[]) {
  const columns: ExportColumn[] = businessColumns.map((column, index) => ({ key: `business-${index}`, label: column.label, ...(column.format ? { format: column.format } : {}) }));
  seasons.forEach((season, seasonIndex) => metrics.forEach((metric, metricIndex) => columns.push({
    key: `quarter-${seasonIndex}-${metricIndex}`, label: `${season} / ${metric.label}`, ...(metric.format ? { format: metric.format } : {}),
  })));
  const rows = groups.map(group => {
    const result: Record<string, QuarterlyValue> = {};
    businessColumns.forEach((column, index) => { result[`business-${index}`] = column.value(group.sample); });
    seasons.forEach((season, seasonIndex) => metrics.forEach((metric, metricIndex) => {
      const source = group.bySeason.get(season);
      result[`quarter-${seasonIndex}-${metricIndex}`] = source === undefined ? null : metric.value(source);
    }));
    return result;
  });
  return { columns, rows };
}

export function quarterlyBusinessCopyValue<Row>(row: Row | undefined, metric: QuarterlyValueColumn<Row>) {
  if (row === undefined) return "";
  const value = metric.value(row);
  if (value === null || value === undefined) return "";
  if (metric.format === "rate" && /^[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[-+]?\d+)?$/i.test(String(value))) return `${Number(value) * 100}%`;
  return typeof value === "boolean" ? value ? "是" : "否" : String(value);
}
