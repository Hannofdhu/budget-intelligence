import type { ExportColumn } from "./budgetExport.ts";
import { budgetPeriodLabel, compareBudgetGrades } from "./budgetDisplay.ts";
import type {
  BudgetAnalysisState, BudgetDimension, BudgetFact, BudgetFilters, BudgetMetric,
  BudgetMetricValues, BudgetPivot, BudgetPivotConfig, BudgetPivotRow, BudgetValue,
} from "./budgetAnalysisTypes.ts";

export const dimensionLabels: Record<BudgetDimension, string> = {
  season: "季度", track: "赛道", campus: "校区", product: "产品", grade: "年级", subject: "科目",
};
export const metricLabels: Record<BudgetMetric, string> = {
  revenue: "确认收入", business: "业务收入（含税）", old: "老生人次", new: "新生人次", total: "总人次", discount: "综合折扣", renewal: "预算续班率",
};
export const seasons = ["暑假", "秋季", "寒假", "春季"];
export const emptyBudgetFilters: BudgetFilters = { season: "", track: "", campus: "", product: "", grade: "", subject: "" };
const dimensions = Object.keys(dimensionLabels) as BudgetDimension[];
const metrics = Object.keys(metricLabels) as BudgetMetric[];
const collator = new Intl.Collator("zh-CN", { numeric: true });
const emptyValues = (): BudgetMetricValues => ({ revenue: null, business: null, old: null, new: null, total: null, discount: null, renewal: null });

export function defaultAnalysisState(): BudgetAnalysisState {
  return { view: "board", filters: { ...emptyBudgetFilters }, config: { rows: ["campus"], column: "season", metrics: ["revenue"], sort: "revenue-desc" }, unit: "wan" };
}

// Avoid Number(null), Number("") and permissive coercion: these are not recorded zeroes.
export function numericValue(value: BudgetValue | undefined): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  if (!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(trimmed)) return null;
  const number = Number(trimmed);
  return Number.isFinite(number) ? number : null;
}

// Neumaier summation keeps small decimal amounts even alongside large amounts.
// A partially missing metric is not presented as a complete budget total.
function completeSum(facts: BudgetFact[], field: keyof BudgetFact): number | null {
  if (!facts.length) return null;
  let sum = 0, compensation = 0;
  for (const fact of facts) {
    const value = numericValue(fact[field] as BudgetValue | undefined);
    if (value === null) return null;
    const next = sum + value;
    compensation += Math.abs(sum) >= Math.abs(value) ? sum - next + value : value - next + sum;
    sum = next;
  }
  const result = sum + compensation;
  return Number.isFinite(result) ? result : null;
}

export function aggregateBudgetFacts(facts: BudgetFact[]): BudgetMetricValues {
  const values = emptyValues();
  for (const metric of metrics) if (metric !== "discount" && metric !== "renewal") values[metric] = completeSum(facts, metric);
  const list = completeSum(facts, "listRevenue");
  values.discount = values.business !== null && list !== null && list !== 0 ? values.business / list : null;
  const numerator = completeSum(facts, "renewalNumerator"), denominator = completeSum(facts, "renewalDenominator");
  values.renewal = numerator !== null && denominator !== null && denominator > 0 ? numerator / denominator : null;
  return values;
}

export function filterBudgetFacts(facts: BudgetFact[], filters: Partial<Record<BudgetDimension, string>>): BudgetFact[] {
  return facts.filter(fact => dimensions.every(dimension => !filters[dimension] || fact[dimension] === filters[dimension]));
}

function sanitizeConfig(config: Partial<BudgetPivotConfig> | undefined): BudgetPivotConfig {
  const defaults = defaultAnalysisState().config;
  const rows = [...new Set((Array.isArray(config?.rows) ? config.rows : defaults.rows).filter(value => dimensions.includes(value)))];
  if (!rows.length) rows.push("campus");
  let column = config?.column === "none" || dimensions.includes(config?.column as BudgetDimension) ? config!.column! : defaults.column;
  if (column !== "none" && rows.includes(column)) column = "none";
  const validMetrics = [...new Set((Array.isArray(config?.metrics) ? config.metrics : defaults.metrics).filter(value => metrics.includes(value)))];
  return { rows, column, metrics: validMetrics.length ? validMetrics : ["revenue"], sort: config?.sort === "name-asc" ? "name-asc" : "revenue-desc" };
}

function dimensionCompare(dimension: BudgetDimension, left: string, right: string): number {
  if (dimension === "grade") return compareBudgetGrades(left, right);
  if (dimension === "season") {
    const a = seasons.indexOf(left), b = seasons.indexOf(right);
    if (a >= 0 || b >= 0) return (a < 0 ? seasons.length : a) - (b < 0 ? seasons.length : b) || collator.compare(left, right);
  }
  return collator.compare(left, right);
}

export function buildBudgetPivot(facts: BudgetFact[], input: BudgetPivotConfig): BudgetPivot {
  const config = sanitizeConfig(input);
  const columns = config.column === "none" ? [{ key: "all", label: "合计", value: null }] :
    [...new Set(facts.map(fact => fact[config.column as BudgetDimension]))]
      .sort((a, b) => dimensionCompare(config.column as BudgetDimension, a, b))
      .map(value => ({ key: JSON.stringify([value]), label: value, value }));
  const columnFacts = (rows: BudgetFact[], value: string | null) => config.column === "none" ? rows : rows.filter(fact => fact[config.column as BudgetDimension] === value);
  const columnTotals = Object.fromEntries(columns.map(column => [column.key, aggregateBudgetFacts(columnFacts(facts, column.value))]));
  const rows: BudgetPivotRow[] = [];
  const walk = (groupFacts: BudgetFact[], depth: number, path: Partial<Record<BudgetDimension, string>>, values: string[], parentKey: string | null) => {
    const dimension = config.rows[depth];
    const groups = new Map<string, BudgetFact[]>();
    for (const fact of groupFacts) { const value = fact[dimension]; const group = groups.get(value) ?? []; group.push(fact); groups.set(value, group); }
    const ordered = [...groups].map(([label, children]) => ({ label, children, total: aggregateBudgetFacts(children) }));
    ordered.sort((a, b) => {
      if (dimension === "season" || config.sort === "name-asc") return dimensionCompare(dimension, a.label, b.label);
      if (a.total.revenue === null && b.total.revenue !== null) return 1;
      if (b.total.revenue === null && a.total.revenue !== null) return -1;
      return (b.total.revenue ?? 0) - (a.total.revenue ?? 0) || dimensionCompare(dimension, a.label, b.label);
    });
    for (const group of ordered) {
      const nextValues = [...values, group.label], key = JSON.stringify(nextValues), nextPath = { ...path, [dimension]: group.label };
      const leaf = depth === config.rows.length - 1;
      rows.push({ key, parentKey, depth, label: group.label, path: nextPath, leaf, total: group.total,
        cells: Object.fromEntries(columns.map(column => [column.key, aggregateBudgetFacts(columnFacts(group.children, column.value))])) });
      if (!leaf) walk(group.children, depth + 1, nextPath, nextValues, key);
    }
  };
  if (facts.length) walk(facts, 0, {}, [], null);
  return { columns, rows, totals: aggregateBudgetFacts(facts), columnTotals };
}

const rawValue = (value: unknown): BudgetValue => typeof value === "string" || typeof value === "number" ? value : null;
const dimensionValue = (value: unknown): string => value === null || value === undefined || value === "" ? "未分类" : String(value);
const record = (value: object): Record<string, unknown> => value as Record<string, unknown>;
function factDimensions(row: Record<string, unknown>, season: unknown) {
  return Object.fromEntries(dimensions.map(key => [key, dimensionValue(key === "season" ? season : row[key])])) as Record<BudgetDimension, string>;
}
function quarterName(row: Record<string, unknown>): string {
  if (typeof row.quarter_name === "string" && row.quarter_name) return row.quarter_name;
  if (typeof row.season === "string" && row.season) return row.season;
  const index = Number(row.fiscal_season) % 10 - 1;
  return Number.isInteger(index) && index >= 0 && index < seasons.length ? seasons[index] : "未分类";
}

// Keep only display facts. Annual row totals, workbook positions and provenance are
// deliberately excluded, preventing an annual value from being counted four times.
export function normalizeHistoricalFacts(records: object[]): BudgetFact[] {
  const sourceRows = records.map(record);
  const sourceKey = (row: Record<string, unknown>, season = row.season) => JSON.stringify([row.row ?? null, row.track, row.product, row.entry, row.grade, row.source_grade, row.subject, row.campus, season]);
  const bySeason = uniqueIndex(sourceRows, sourceKey);
  return records.map((source, index) => {
    const row = record(source), dims = factDimensions(row, row.season);
    const seasonIndex = seasons.indexOf(dims.season);
    const previous = seasonIndex > 0 ? bySeason.get(sourceKey(row, seasons[seasonIndex - 1])) : null;
    const base = seasonIndex === 0 ? row.prior_students : previous?.total;
    // The FY27 saved workbook explicitly uses '-' for rows without preceding
    // spring enrollment and an old count of zero. It is not a missing value.
    const notApplicable = seasonIndex === 0 && base === "-" && row.renewal === "-" && numericValue(rawValue(row.old)) === 0;
    const basis = notApplicable ? { renewalNumerator: 0, renewalDenominator: 0 } : renewalBasis(base, row.renewal);
    return { ...dims, id: `historical:${JSON.stringify(dimensions.map(key => dims[key]))}:${index}`,
      revenue: rawValue(row.revenue), business: rawValue(row.business), listRevenue: rawValue(Object.hasOwn(row, "list_revenue") ? row.list_revenue : row.listRevenue),
      old: rawValue(row.old), new: rawValue(row.new), total: rawValue(row.total), current: rawValue(row.current), carryout: rawValue(row.carryout),
      price: rawValue(row.price), renewal: rawValue(row.renewal), share: rawValue(row.share), detailDiscount: rawValue(row.discount), recognition: rawValue(row.recognition), ...basis };
  });
}

function fullPublishedKey(row: Record<string, unknown>): string {
  return JSON.stringify([row.fiscal_season ?? quarterName(row), row.track, row.product, row.entry_attribute ?? "", row.grade, row.subject, row.campus].map(value => String(value ?? "")));
}
function uniqueIndex(records: Record<string, unknown>[], key: (row: Record<string, unknown>) => string): Map<string, Record<string, unknown> | null> {
  const index = new Map<string, Record<string, unknown> | null>();
  for (const row of records) { const value = key(row); index.set(value, index.has(value) ? null : row); }
  return index;
}
function rateValue(value: unknown): number | null { const number = numericValue(rawValue(value)); return number === null ? null : number / 1_000_000; }

function renewalBasis(base: unknown, rate: unknown): Pick<BudgetFact, "renewalNumerator" | "renewalDenominator"> {
  const denominator = numericValue(rawValue(base)), value = numericValue(rawValue(rate));
  if (denominator === null || denominator < 0 || value === null || value < 0 || value > 1) return { renewalNumerator: null, renewalDenominator: null };
  return { renewalNumerator: denominator * value, renewalDenominator: denominator };
}

export type PublishedRenewalContext = { fiscalYear: number; rateParameterVersionId: string; openingContent: Record<string, unknown> | null };
function publishedOpeningBasis(count: Record<string, unknown>, countRows: Record<string, unknown>[], context?: PublishedRenewalContext) {
  const missing = { renewalNumerator: null, renewalDenominator: null, inputRate: null };
  const content = context?.openingContent;
  if (!context || !content || !context.rateParameterVersionId || content.fiscal_year !== context.fiscalYear || count.fiscal_season !== context.fiscalYear * 10 + 1 || !Array.isArray(content.rows) || !Array.isArray(content.zero_combination_ids)) return missing;
  const openingDimensions = ["track", "product", "grade", "subject", "campus"];
  const targetKey = (row: Record<string, unknown>) => JSON.stringify(openingDimensions.map(key => row[key]));
  const matchingCounts = countRows.filter(row => row.fiscal_season === count.fiscal_season && targetKey(row) === targetKey(count));
  if (matchingCounts.length !== 1) return missing;
  const mapped = (content.rows as object[]).map(record).filter(row => row.target_key && typeof row.target_key === "object" && targetKey(record(row.target_key as object)) === targetKey(count));
  const explicitZero = typeof count.combination_id === "string" && content.zero_combination_ids.includes(count.combination_id);
  if (explicitZero) return mapped.length === 0 && numericValue(rawValue(count.integer_old_students)) === 0 ? { renewalNumerator: 0, renewalDenominator: 0, inputRate: null } : missing;
  if (mapped.length !== 1 || mapped[0].exclusion !== null || mapped[0].rate_version_id !== context.rateParameterVersionId) return missing;
  const inputRate = rateValue(mapped[0].renewal_rate_ppm), basis = renewalBasis(mapped[0].enrollment, inputRate);
  return { ...basis, inputRate: basis.renewalDenominator === null ? null : inputRate };
}

export function normalizePublishedFacts(records: object[], headcountRows: object[] = [], context?: PublishedRenewalContext): BudgetFact[] {
  const countRows = headcountRows.map(record);
  const countById = uniqueIndex(countRows.filter(row => typeof row.season_headcount_result_id === "string" && row.season_headcount_result_id), row => String(row.season_headcount_result_id));
  const countByKey = uniqueIndex(countRows, fullPublishedKey);
  return records.map((source, index) => {
    const row = record(source), dims = factDimensions(row, quarterName(row));
    const id = typeof row.season_headcount_result_id === "string" && row.season_headcount_result_id ? row.season_headcount_result_id : null;
    let count = (id ? countById.get(id) : countByKey.get(fullPublishedKey(row))) ?? null;
    // IDs alone are not enough if an incompatible payload is accidentally supplied.
    if (count && (fullPublishedKey(count) !== fullPublishedKey(row) ||
      row.headcount_result_version_id && count.headcount_result_version_id && row.headcount_result_version_id !== count.headcount_result_version_id ||
      row.fiscal_year && count.fiscal_year && row.fiscal_year !== count.fiscal_year ||
      numericValue(rawValue(row.integer_total_students)) !== numericValue(rawValue(count.integer_total_students)))) count = null;
    let basis: Pick<BudgetFact, "renewalNumerator" | "renewalDenominator"> = { renewalNumerator: null, renewalDenominator: null };
    let inputRenewal = rateValue(count?.renewal_rate_ppm);
    if (count) {
      const fiscalSeason = numericValue(rawValue(count.fiscal_season));
      if (fiscalSeason !== null && fiscalSeason % 10 === 1) {
        const opening = publishedOpeningBasis(count, countRows, context);
        basis = { renewalNumerator: opening.renewalNumerator, renewalDenominator: opening.renewalDenominator };
        inputRenewal = opening.inputRate;
      }
      else if (fiscalSeason !== null && fiscalSeason % 10 >= 2 && fiscalSeason % 10 <= 4) {
        const previous = countByKey.get(fullPublishedKey({ ...count, fiscal_season: fiscalSeason - 1 }));
        if (previous && (!count.combination_id || !previous.combination_id || count.combination_id === previous.combination_id) &&
          (!count.fiscal_year || !previous.fiscal_year || count.fiscal_year === previous.fiscal_year) &&
          (!count.headcount_result_version_id || !previous.headcount_result_version_id || count.headcount_result_version_id === previous.headcount_result_version_id)) basis = renewalBasis(previous.integer_total_students, inputRenewal);
      }
    }
    return { ...dims, id: `published:${id ?? fullPublishedKey(row)}:${index}`,
      revenue: rawValue(row.quarter_recognized_revenue_yuan), business: rawValue(row.business_quarter_revenue_yuan), listRevenue: rawValue(row.list_price_revenue_yuan),
      old: rawValue(count?.integer_old_students), new: rawValue(count?.integer_new_students), total: rawValue(row.integer_total_students),
      price: rawValue(row.list_price_yuan), detailDiscount: rateValue(row.detail_discount_rate_ppm),
      renewal: inputRenewal, share: rateValue(count?.new_student_share_input_ppm), ...basis };
  });
}

export function readAnalysisState(search: string): BudgetAnalysisState {
  const params = new URLSearchParams(search), defaults = defaultAnalysisState();
  const rows = params.has("baRows") ? params.get("baRows")!.split(",") as BudgetDimension[] : defaults.config.rows;
  const requestedMetrics = params.has("baMetrics") ? params.get("baMetrics")!.split(",") as BudgetMetric[] : defaults.config.metrics;
  return { view: params.get("baView") === "pivot" ? "pivot" : "board", unit: params.get("baUnit") === "yuan" ? "yuan" : "wan",
    filters: Object.fromEntries(dimensions.map(dimension => [dimension, params.get(`bf_${dimension}`) ?? ""])) as BudgetFilters,
    config: sanitizeConfig({ rows, column: (params.get("baColumn") ?? defaults.config.column) as BudgetPivotConfig["column"],
      metrics: requestedMetrics, sort: params.get("baSort") as BudgetPivotConfig["sort"] }) };
}

export function writeAnalysisState(search: string, state: BudgetAnalysisState): string {
  const params = new URLSearchParams(search), config = sanitizeConfig(state.config);
  for (const [key, value] of Object.entries({ baView: state.view === "pivot" ? "pivot" : "board", baRows: config.rows.join(","), baColumn: config.column,
    baMetrics: config.metrics.join(","), baSort: config.sort, baUnit: state.unit === "yuan" ? "yuan" : "wan" })) params.set(key, value);
  for (const dimension of dimensions) {
    const value = state.filters[dimension];
    if (value) params.set(`bf_${dimension}`, value); else params.delete(`bf_${dimension}`);
  }
  return params.toString();
}

export function validateAnalysisState(state: BudgetAnalysisState, facts: BudgetFact[]): { state: BudgetAnalysisState; removedFilters: string[] } {
  const removedFilters: string[] = [], filters = { ...emptyBudgetFilters, ...state.filters };
  for (const dimension of dimensions) {
    const value = filters[dimension];
    if (value && !facts.some(fact => fact[dimension] === value)) { removedFilters.push(`${dimensionLabels[dimension]}：${value}`); filters[dimension] = ""; }
  }
  return { state: { view: state.view === "pivot" ? "pivot" : "board", unit: state.unit === "yuan" ? "yuan" : "wan", filters, config: sanitizeConfig(state.config) }, removedFilters };
}

export function pivotExport(pivot: BudgetPivot, input: BudgetPivotConfig): { columns: ExportColumn[]; rows: object[] } {
  const config = sanitizeConfig(input);
  const columns: ExportColumn[] = config.rows.map(dimension => ({ key: dimension, label: dimensionLabels[dimension] }));
  const format = (metric: BudgetMetric): ExportColumn["format"] => metric === "discount" || metric === "renewal" ? "rate" : metric === "revenue" || metric === "business" ? "money" : "count";
  for (const column of pivot.columns) for (const metric of config.metrics) columns.push({ key: `${column.key}:${metric}`, label: `${config.column === "season" ? budgetPeriodLabel(column.label, metric) : column.label} · ${metricLabels[metric]}${format(metric) === "money" ? "（元）" : ""}`, format: format(metric) });
  // With no column dimension, the single 'all' column already is the total.
  if (config.column !== "none") for (const metric of config.metrics) columns.push({ key: `total:${metric}`, label: `合计 · ${metricLabels[metric]}${format(metric) === "money" ? "（元）" : ""}`, format: format(metric) });
  const values = (cells: Record<string, BudgetMetricValues>, total: BudgetMetricValues) => {
    const row: Record<string, unknown> = {};
    for (const column of pivot.columns) for (const metric of config.metrics) row[`${column.key}:${metric}`] = cells[column.key]?.[metric] ?? null;
    if (config.column !== "none") for (const metric of config.metrics) row[`total:${metric}`] = total[metric];
    return row;
  };
  const rows: object[] = pivot.rows.filter(row => row.leaf).map(row => ({ ...row.path, ...values(row.cells, row.total) }));
  rows.push({ [config.rows[0]]: "合计", ...values(pivot.columnTotals, pivot.totals) });
  return { columns, rows };
}
