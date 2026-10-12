import { Children, isValidElement, useEffect, useMemo, useRef, useState, type MouseEvent, type ReactNode } from "react";
import { parseResponse, type ApiIdentity } from "./api";
import { FY27Header } from "./FY27Header";
import { downloadBudgetWorkbook, type ExportColumn } from "./budgetExport";
import { forecastNumber } from "./forecastApi";
import { BudgetAnalysisView } from "./BudgetAnalysisView";
import { ActionTable } from "./ActionTable";
import type { BudgetFact } from "./budgetAnalysisTypes";
import { allocateNextYearTrackTargets, applyNextYearEdits, getNextYearBudget, nextYearEditKey, nextYearRowTotal, nextYearSeasons,
  prepareNextYearBudget, previewNextYearBudget, reviseNextYearBudget, getNextYearCatalog, getNextYearVersion, restoreNextYearVersion, balanceNextYearBudget,
  type NextYearCalculation, type NextYearEdits, type NextYearEnvelope, type NextYearInputs, type NextYearNumber, type NextYearRate, type NextYearRow, type NextYearCatalog, type NextYearCombination } from "./nextYearApi";
import "./nextYear.css";

export const nextYearTasks = ["目标与范围", "入口人数", "招生参数", "资源与财务", "预算概览"] as const;
type Task = 1 | 2 | 3 | 4 | 5;
type Dimension = "season" | "track" | "campus" | "product" | "grade" | "subject";
type Filters = Record<Dimension, string>;
const dimensionLabels: Record<Dimension, string> = { season: "季度", track: "赛道", campus: "校区", product: "产品", grade: "年级", subject: "科目" };
const emptyFilters: Filters = { season: "", track: "", campus: "", product: "", grade: "", subject: "" };
const fy = (year: number) => `FY${String(year).slice(-2)}`;
const readYear = () => { const value = Number(new URLSearchParams(location.search).get("year")); return Number.isInteger(value) && value >= 2028 && value <= 2100 ? value : 2028; };
export const effectiveNextYearEntry = (row: NextYearCombination) => row.entry_override ?? row.entry;
const readTask = (): Task => { const number = Number(new URLSearchParams(location.search).get("nyTask")); return number >= 1 && number <= 5 && Number.isInteger(number) ? number as Task : 5; };
const format = (value: NextYearNumber | undefined, digits = 0) => { const n = forecastNumber(value); return n === null ? "—" : n.toLocaleString("zh-CN", { maximumFractionDigits: digits }); };
const money = (value: NextYearNumber | undefined) => { const n = forecastNumber(value); return n === null ? "—" : format(n / 10000, 2); };
const textDimensions = (row: Partial<Record<Dimension, string>>) => [row.campus, row.track, row.product, row.grade, row.subject].filter(Boolean).join(" / ");
const matches = (row: Partial<Record<Dimension, string>>, filters: Filters) => (Object.keys(filters) as Dimension[]).every(key => !filters[key] || row[key] === undefined || row[key] === filters[key]);
export const nextYearClassStatus = (status: string) => ({ PASS: "达标", NO_PLANNED_CLASS: "未计划开班", BELOW_MINIMUM_CLASS_SIZE: "未达班均", HEADCOUNT_UNAVAILABLE: "人次待核对", PARAMETER_MISSING: "班均待填写" } as Record<string, string>)[status] ?? "待核对";
export function nextYearRateUsage(inputs: NextYearInputs, rate: NextYearRate) {
  if (rate.season !== "暑假") return { renewal: true, share: true };
  const cohort = inputs.combinations.filter(row => row.enabled && ["track", "product", "grade", "campus"].every(key => row[key as keyof typeof row] === rate[key as keyof NextYearRate]));
  const targets = new Set(cohort.map(row => row.id));
  return { renewal: inputs.opening_sources.some(row => row.target_id !== null && targets.has(row.target_id)), share: cohort.length > 0 && !cohort.every(effectiveNextYearEntry) };
}
const gradeOrder = ["一年级", "二年级", "三年级", "四年级", "五年级", "六年级", "七年级", "八年级", "九年级", "高一", "高二", "高三"];
const dimensionSort = (key: Dimension, a: string, b: string) => key === "grade" && gradeOrder.includes(a) && gradeOrder.includes(b) ? gradeOrder.indexOf(a) - gradeOrder.indexOf(b) : a.localeCompare(b, "zh-CN");
export const nextYearExportColumns: ExportColumn[] = [
  ...(Object.entries(dimensionLabels).map(([key, label]) => ({ key, label }))),
  { key: "old_students", label: "老生人次", format: "count" }, { key: "new_students", label: "新生人次", format: "count" },
  { key: "total_students", label: "总人次", format: "count" }, { key: "revenue_yuan", label: "预算收入（元）", format: "money" },
  { key: "price_yuan", label: "学费（元）", format: "money" }, { key: "discount", label: "明细折扣", format: "rate" },
  { key: "renewal", label: "续班率", format: "rate" }, { key: "share", label: "新生占比", format: "rate" },
  { key: "class_size_status", label: "开班状态" },
  { key: "class_count", label: "班量", format: "count" }, { key: "planned_class_size", label: "计划班额", format: "count" },
];
export const nextYearExportRows = (rows: NextYearRow[]) => rows.map(row => ({ ...row,
  discount: row.discount_ppm === null ? null : row.discount_ppm / 1000000,
  renewal: row.renewal_ppm === null ? null : row.renewal_ppm / 1000000,
  share: row.share_ppm === null ? null : row.share_ppm / 1000000,
  class_size_status: nextYearClassStatus(row.class_size_status),
}));

/** Projection only: the backend remains the owner of budget calculations. */
export function nextYearAnalysisFacts(calculation: NextYearCalculation, inputs?: NextYearInputs): BudgetFact[] {
  return calculation.rows.map(row => {
    const rate = forecastNumber(row.renewal_ppm), index = nextYearSeasons.indexOf(row.season);
    const previous = index > 0 ? calculation.rows.find(item => item.combination_id === row.combination_id && item.season === nextYearSeasons[index - 1])?.total_students
      : inputs?.opening_sources.filter(item => item.target_id === row.combination_id).reduce((sum, item) => sum + item.students, 0);
    const total = forecastNumber(row.total_students), price = forecastNumber(row.price_yuan), discount = forecastNumber(row.discount_ppm);
    return { id: `${row.combination_id}:${row.season}`, season: row.season, track: row.track, campus: row.campus, product: row.product, grade: row.grade, subject: row.subject,
      revenue: row.revenue_yuan, old: row.old_students, new: row.new_students, total: row.total_students,
      listRevenue: total === null || price === null ? null : total * price,
      business: total === null || price === null || discount === null ? null : total * price * discount / 1000000,
      detailDiscount: discount === null ? null : discount / 1000000, price: row.price_yuan,
      renewal: rate === null ? null : rate / 1000000, share: row.share_ppm === null ? null : row.share_ppm / 1000000,
      renewalDenominator: previous ?? null, renewalNumerator: previous === undefined || previous === null || rate === null ? null : previous * rate / 1000000 };
  });
}

export function nextYearImpactRows(before: NextYearCalculation, after: NextYearCalculation) {
  const keys = new Set([...before.rows, ...after.rows].map(row => `${row.combination_id}:${row.season}`));
  return [...keys].flatMap(key => {
    const first = before.rows.find(row => `${row.combination_id}:${row.season}` === key), next = after.rows.find(row => `${row.combination_id}:${row.season}` === key);
    const differences = ["revenue_yuan", "old_students", "new_students", "total_students", "class_count"].some(field => forecastNumber((first as unknown as Record<string, NextYearNumber> | undefined)?.[field]) !== forecastNumber((next as unknown as Record<string, NextYearNumber> | undefined)?.[field]));
    if (!differences) return [];
    const delta = (field: "revenue_yuan" | "total_students" | "class_count") => { const a = first ? forecastNumber(first[field]) : 0, b = next ? forecastNumber(next[field]) : 0; return a === null || b === null ? null : b - a; };
    return [{ key, name: textDimensions(next ?? first ?? {}), season: (next ?? first)!.season, before: first?.revenue_yuan ?? 0, after: next?.revenue_yuan ?? 0,
      revenue_delta: delta("revenue_yuan"), student_delta: delta("total_students"), class_delta: delta("class_count") }];
  });
}

function ImpactComparison({ before, after }: { before: NextYearCalculation; after: NextYearCalculation }) {
  const rows = nextYearImpactRows(before, after), delta = (field: keyof typeof before.annual) => { const first = forecastNumber(before.annual[field]), next = forecastNumber(after.annual[field]); return first === null || next === null ? null : next - first; };
  const sign = (value: number | null, isMoney = false) => value === null ? "—" : `${value > 0 ? "+" : ""}${isMoney ? money(value) : format(value)}`;
  return <section className="ny-impact"><div className="ny-section-heading"><h2>修改影响对比</h2><span>{rows.length} 条结果变化</span></div>
    <div className="ny-impact-metrics"><div><span>收入变化（万元）</span><strong>{sign(delta("revenue_yuan"), true)}</strong><small>{money(before.annual.revenue_yuan)} → {money(after.annual.revenue_yuan)}</small></div>{([["old_students", "老生变化"], ["new_students", "新生变化"], ["total_students", "总人次变化"], ["class_count", "班量变化"]] as const).map(([field, label]) => <div key={field}><span>{label}</span><strong>{sign(delta(field))}</strong><small>{format(before.annual[field])} → {format(after.annual[field])}</small></div>)}</div>
    <details><summary>查看变化明细</summary><PagedTable rows={rows} label="预算修改影响明细" headers={["校区 / 业务", "季度", "原收入（万元）", "新收入（万元）", "收入变化（万元）", "人次变化", "班量变化"]} render={row => <><th>{row.name}</th><td>{row.season}</td><td>{money(row.before)}</td><td>{money(row.after)}</td><td>{sign(row.revenue_delta, true)}</td><td>{sign(row.student_delta)}</td><td>{sign(row.class_delta)}</td></>}/></details>
  </section>;
}

export function parseNextYearParameterPaste(text: string, rows: NextYearRate[]): NextYearEdits {
  const edits: NextYearEdits = {}, lines = text.trim().split(/\r?\n/).filter(line => line.trim());
  if (!lines.length) throw new Error("先粘贴参数");
  const seen = new Set<string>();
  for (const [index, line] of lines.entries()) {
    const cells = line.split("\t").map(value => value.trim()), id = cells[0];
    if (index === 0 && ["参数ID", "ID", "id"].includes(id)) continue;
    if (cells.length !== 3) throw new Error(`第 ${index + 1} 行须为参数ID、续班率、新生占比三列`);
    if (!rows.some(row => row.id === id)) throw new Error(`第 ${index + 1} 行参数不在当前筛选范围`);
    if (seen.has(id)) throw new Error(`第 ${index + 1} 行参数ID重复`); seen.add(id);
    for (const [column, field] of [[1, "renewal_ppm"], [2, "share_ppm"]] as const) if (cells[column] !== "") edits[nextYearEditKey("rates", id, field)] = cells[column].replace(/%$/, "");
  }
  if (!Object.keys(edits).length) throw new Error("没有可修改的参数");
  return edits;
}

export function NextYearTaskNavigation({ task, onChange, fiscalYear = 2028 }: { task: Task; onChange: (task: Task) => void; fiscalYear?: number }) {
  return <nav className="decision-progress ny-progress" aria-label={`${fy(fiscalYear)} 预算任务`}>{nextYearTasks.map((label, index) => <button key={label} className={task === index + 1 ? "is-active" : ""} aria-current={task === index + 1 ? "step" : undefined} onClick={() => onChange(index + 1 as Task)}><span>{index + 1}</span><strong>{label}</strong><em className="decision-progress-short">{label.slice(0, 2)}</em></button>)}</nav>;
}

function cellText(node: ReactNode): string {
  if (node === null || node === undefined || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(cellText).join(" ");
  if (!isValidElement(node)) return "";
  const props = node.props as { children?: ReactNode; value?: unknown; checked?: boolean; type?: string };
  if (node.type === "input") return props.type === "checkbox" ? props.checked ? "是" : "否" : String(props.value ?? "");
  if (node.type === "label") { const input = Children.toArray(props.children).find(child => isValidElement(child) && child.type === "input"); if (input) return cellText(input); }
  return cellText(props.children);
}
export function nextYearTableData<T>(rows: T[], headers: string[], render: (row: T, index: number) => ReactNode) {
  return rows.map((row, index) => {
    const node = render(row, index), cells = isValidElement(node) ? Children.toArray((node.props as { children: ReactNode }).children) : Children.toArray(node);
    return Object.fromEntries(headers.map((_header, column) => { const value = cellText(cells[column]).trim(), numeric = value.replaceAll(",", ""); return [`c${column}`, /^[+-]?\d+(?:\.\d+)?$/.test(numeric) ? Number(numeric) : value === "—" ? null : value]; }));
  });
}

function PagedTable<T>({ rows, headers, render, label, invalidPath, editPath }: { rows: T[]; headers: string[]; render: (row: T, index: number) => ReactNode; label: string; invalidPath?: string; editPath?: (row: T, index: number) => (string | number)[] }) {
  const [page, setPage] = useState(0), [query, setQuery] = useState("");
  const invalidIndex = invalidPath && editPath ? rows.findIndex((row, index) => { try { const path = JSON.parse(invalidPath); return editPath(row, index).every((value, n) => path[n] === value); } catch { return false; } }) : -1;
  useEffect(() => { if (invalidIndex >= 0) { setQuery(""); setPage(Math.floor(invalidIndex / 25)); } }, [invalidPath, invalidIndex]);
  const filtered = rows.map((row, index) => ({ row, index })).filter(({ row }) => !query || JSON.stringify(row).includes(query));
  const safePage = Math.min(page, Math.max(0, Math.ceil(filtered.length / 25) - 1));
  const values = nextYearTableData(filtered.map(item => item.row), headers, (row, index) => render(row, filtered[index].index));
  return <div className="ny-data"><div className="ny-table-tools"><span>{filtered.length}条</span><input aria-label={`筛选${label}`} value={query} placeholder="筛选明细" onChange={event => { setQuery(event.target.value); setPage(0); }}/></div>
    <div className="ny-table-wrap" tabIndex={0} aria-label={label}><ActionTable tableLabel={label} copyRows={[headers, ...values.map(row => headers.map((_header, index) => String(row[`c${index}`] ?? "")))]} exportData={{ columns: headers.map((header, index) => ({ key: `c${index}`, label: header })), rows: values, filename: label }}><thead><tr>{headers.map((header, index) => <th key={`${header}-${index}`}>{header}</th>)}</tr></thead><tbody>{filtered.slice(safePage * 25, safePage * 25 + 25).map(({ row, index }) => <tr key={index}>{render(row, index)}</tr>)}</tbody></ActionTable></div>
    {!filtered.length && <p className="ny-empty">当前范围没有明细</p>}<div className="ny-pagination"><button disabled={safePage === 0} onClick={() => setPage(safePage - 1)}>上一页</button><span>{filtered.length ? safePage + 1 : 0} / {Math.ceil(filtered.length / 25)}</span><button disabled={(safePage + 1) * 25 >= filtered.length} onClick={() => setPage(safePage + 1)}>下一页</button></div>
  </div>;
}

export function NextYearOverview({ calculation, filters, onSetTarget, summaryOnly = false }: { calculation: NextYearCalculation; filters: Filters; onSetTarget?: () => void; summaryOnly?: boolean }) {
  const [dimension, setDimension] = useState<"track" | "campus">("track");
  const rows = calculation.rows.filter(row => matches(row, filters)), narrowed = Object.values(filters).some(Boolean);
  const revenue = narrowed ? nextYearRowTotal(rows, "revenue_yuan") : calculation.annual.revenue_yuan;
  const total = narrowed ? nextYearRowTotal(rows, "total_students") : calculation.annual.total_students;
  const old = narrowed ? nextYearRowTotal(rows, "old_students") : calculation.annual.old_students;
  const fresh = narrowed ? nextYearRowTotal(rows, "new_students") : calculation.annual.new_students;
  const goals = dimension === "track" ? calculation.tracks : calculation.campuses;
  const names = [...new Set(rows.map(row => row[dimension]))].sort((a, b) => a.localeCompare(b, "zh-CN"));
  const narrowedBeyondGroup = (Object.entries(filters) as [Dimension, string][]).some(([key, value]) => value && key !== dimension);
  const hasTarget = forecastNumber(calculation.annual.target_revenue_yuan) !== null;
  const showComparison = !narrowedBeyondGroup && names.some(name => forecastNumber(goals.find(item => (item as unknown as Record<string, unknown>)[dimension] === name)?.target_revenue_yuan) !== null);
  return <>
    <section className="ny-metrics" aria-label={narrowed ? "当前范围预算" : "FY28 全年预算"}>
      <div className="ny-metric-primary"><span>{narrowed ? "当前范围收入（万元）" : "全年预算收入（万元）"}</span><strong>{money(revenue)}</strong></div>
      <div><span>总人次</span><strong>{format(total)}</strong></div><div><span>老生人次</span><strong>{format(old)}</strong></div><div><span>新生人次</span><strong>{format(fresh)}</strong></div>
    </section>
    {!narrowed && <div className="ny-target-summary">{hasTarget ? <><span>全年经营目标 <strong>{money(calculation.annual.target_revenue_yuan)}万元</strong></span>{forecastNumber(calculation.annual.gap_yuan) !== null && <span>目标差额 <strong>{money(calculation.annual.gap_yuan)}万元</strong></span>}</> : <><span>经营目标待填写</span>{onSetTarget && <button onClick={onSetTarget}>设置经营目标</button>}</>}</div>}
    {!summaryOnly && <><section className="ny-section"><h2>季度预算</h2><div className="ny-quarter-grid">{nextYearSeasons.map(season => {
      const selected = rows.filter(row => row.season === season), quarter = calculation.quarters.find(item => item.season === season);
      const value = narrowed ? nextYearRowTotal(selected, "revenue_yuan") : quarter?.revenue_yuan;
      const people = narrowed ? nextYearRowTotal(selected, "total_students") : quarter?.total_students;
      return <div key={season}><h3>{season}</h3><span>收入（万元）</span><strong>{money(value)}</strong><dl><div><dt>人次</dt><dd>{format(people)}</dd></div></dl></div>;
    })}</div></section>
    <section className="ny-section"><div className="ny-section-heading"><h2>维度预算</h2><div className="ny-segment" aria-label="汇总维度">{(["track", "campus"] as const).map(key => <button key={key} aria-pressed={dimension === key} onClick={() => setDimension(key)}>{dimensionLabels[key]}</button>)}</div></div>
      <div className="ny-table-wrap" tabIndex={0} aria-label={`${dimensionLabels[dimension]}预算对比`}><ActionTable tableLabel={`${dimensionLabels[dimension]}预算对比`}><thead><tr><th>{dimensionLabels[dimension]}</th><th>预算收入（万元）</th>{showComparison && <><th>目标（万元）</th><th>差额（万元）</th></>}<th>总人次</th></tr></thead><tbody>{names.map(name => {
        const groupRows = rows.filter(row => row[dimension] === name), income = nextYearRowTotal(groupRows, "revenue_yuan");
        const goal = goals.find(item => (item as unknown as Record<string, unknown>)[dimension] === name);
        const targetValue = narrowedBeyondGroup ? null : goal?.target_revenue_yuan;
        const parsedTarget = forecastNumber(targetValue), gap = income === null || parsedTarget === null ? null : parsedTarget - income;
        return <tr key={name}><th>{name}</th><td>{money(income)}</td>{showComparison && <><td>{money(targetValue)}</td><td>{money(gap)}</td></>}<td>{format(nextYearRowTotal(groupRows, "total_students"))}</td></tr>;
      })}</tbody></ActionTable></div>{!rows.length && <p className="ny-empty">当前范围没有预算结果</p>}
    </section>
    <section className="ny-section"><h2>预算明细</h2><PagedTable rows={rows} label="FY28 预算明细" headers={["校区 / 业务", "季度", "老生", "新生", "总人次", "收入（元）", "开班"]} render={row => <><th>{textDimensions(row)}</th><td>{row.season}</td><td>{format(row.old_students)}</td><td>{format(row.new_students)}</td><td>{format(row.total_students)}</td><td>{format(row.revenue_yuan, 2)}</td><td>{nextYearClassStatus(row.class_size_status)}</td></>}/></section></>}
  </>;
}

export function NextYearBudgetPage() {
  const [identity, setIdentity] = useState<ApiIdentity | null>(null), [data, setData] = useState<NextYearEnvelope | null>(null);
  const [inputs, setInputs] = useState<NextYearInputs | null>(null), [edits, setEdits] = useState<NextYearEdits>({});
  const [busy, setBusy] = useState("load"), [error, setError] = useState(""), [notice, setNotice] = useState("");
  const [preview, setPreview] = useState<NextYearCalculation | null>(null), [reason, setReason] = useState("");
  const [task, setTask] = useState<Task>(readTask), [filters, setFilters] = useState<Filters>({ ...emptyFilters });
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({}), [pendingNavigation, setPendingNavigation] = useState("");
  const [exporting, setExporting] = useState(false), leaveDialog = useRef<HTMLDialogElement>(null), inFlight = useRef(false), heading = useRef<HTMLHeadingElement>(null);
  const [catalog, setCatalog] = useState<NextYearCatalog | null>(null), [fiscalYear, setFiscalYear] = useState(readYear);
  const [historical, setHistorical] = useState<NextYearEnvelope | null>(null), [versionsOpen, setVersionsOpen] = useState(false);
  const [bulkField, setBulkField] = useState("renewal_ppm"), [bulkValue, setBulkValue] = useState(""), [paste, setPaste] = useState("");
  const [manualOpen, setManualOpen] = useState(false), [balanceMessage, setBalanceMessage] = useState("");
  const [newBusiness, setNewBusiness] = useState({ campus: "", track: "", product: "", grade: "七年级", subject: "数学", price: "", classSize: "", reason: "新增业务" });
  const [selectedSourceId, setSelectedSourceId] = useState("");
  const [balanceIssues, setBalanceIssues] = useState<NonNullable<NonNullable<Awaited<ReturnType<typeof balanceNextYearBudget>>["constraints"]>["class_diagnostics"]>>([]);
  const loadGeneration = useRef(0);
  const draft = data?.draft, source = historical?.draft?.source ?? draft?.source ?? data?.source;
  const inputChanged = Boolean(draft && inputs && (Object.keys(edits).length || JSON.stringify(inputs) !== JSON.stringify(draft.inputs)));
  const unsaved = inputChanged || Boolean(reason.trim()), canEdit = !historical && Boolean(identity?.roles.includes("DATA_MAINTAINER") || identity?.roles.includes("DEPARTMENT_BUDGET_OWNER"));
  const calculation = historical?.draft?.calculation ?? preview ?? draft?.calculation;
  const visibleInputs = historical?.draft?.inputs ?? inputs;
  function accept(next: NextYearEnvelope) { setData(next); setInputs(next.draft ? structuredClone(next.draft.inputs) : null); setEdits({}); setPreview(null); setReason(""); setFieldErrors({}); setHistorical(null); setBalanceMessage(""); setBalanceIssues([]); setSelectedSourceId(next.draft?.source.id ?? next.source?.id ?? ""); }
  async function load(signal?: AbortSignal, year = fiscalYear) {
    const generation = ++loadGeneration.current;
    setBusy("load"); setError("");
    try { const who = await parseResponse<ApiIdentity>(await fetch("/api/v1/local-trial/session", { signal })); const next = await getNextYearBudget(who, signal, year);
      if (signal?.aborted || generation !== loadGeneration.current) return false;
      setIdentity(who); accept(next); setFiscalYear(year);
      try { const available = await getNextYearCatalog(who, signal); if (!signal?.aborted && generation === loadGeneration.current) setCatalog(available); } catch { /* Budget reading remains available if the catalog cannot load. */ }
      return !signal?.aborted && generation === loadGeneration.current;
    } catch (failure) { if (!signal?.aborted && generation === loadGeneration.current) setError(failure instanceof Error ? failure.message : "读取失败"); return false; }
    finally { if (!signal?.aborted && generation === loadGeneration.current) setBusy(""); }
  }
  useEffect(() => { const controller = new AbortController(); void load(controller.signal); return () => controller.abort(); }, []);
  useEffect(() => { const restore = async () => {
    const year = readYear(), nextTask = readTask();
    if (year === fiscalYear) { setTask(nextTask); return; }
    const rollback = () => { const url = new URL(location.href); url.searchParams.set("year", String(fiscalYear)); url.searchParams.set("nyTask", String(task)); history.replaceState({}, "", url); };
    if (unsaved) { rollback(); setError("先保存或放弃修改，再切换财年"); return; }
    if (await load(undefined, year)) { setTask(nextTask); setFilters({ ...emptyFilters }); } else rollback();
  }; window.addEventListener("popstate", restore); return () => window.removeEventListener("popstate", restore); }, [fiscalYear, unsaved, task]);
  useEffect(() => { if (!unsaved) return; const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; }; window.addEventListener("beforeunload", warn); return () => window.removeEventListener("beforeunload", warn); }, [unsaved]);
  useEffect(() => { const dialog = leaveDialog.current; if (pendingNavigation && dialog && !dialog.open) dialog.showModal(); if (!pendingNavigation && dialog?.open) dialog.close(); }, [pendingNavigation]);
  useEffect(() => { const restore = () => setPendingNavigation(""); window.addEventListener("pageshow", restore); return () => window.removeEventListener("pageshow", restore); }, []);
  function changeTask(next: Task) { setTask(next); const url = new URL(location.href); url.searchParams.set("nyTask", String(next)); history.pushState({}, "", url); requestAnimationFrame(() => heading.current?.focus({ preventScroll: true })); }
  function beforeLeave(event: MouseEvent<HTMLAnchorElement>) { if (!unsaved || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.currentTarget.target === "_blank") return; event.preventDefault(); if (!busy) setPendingNavigation(event.currentTarget.href); }
  function edit(path: (string | number)[], value: string) { const key = nextYearEditKey(...path); setEdits(previous => ({ ...previous, [key]: value, ...(path[0] === "target" && !value.trim() ? Object.fromEntries(Object.keys(inputs?.track_targets ?? {}).map(track => [nextYearEditKey("track-target", track), ""])) : {}) })); setFieldErrors(previous => { const next = { ...previous }; delete next[key]; return next; }); setPreview(null); setNotice(""); setBalanceIssues([]); }
  const invalidPath = (section: string) => Object.keys(fieldErrors).find(key => { try { return JSON.parse(key)[0] === section; } catch { return false; } });
  function field(path: (string | number)[], original: string | number | null, label: string, percent = false, placeholder?: string) {
    const key = nextYearEditKey(...path), message = fieldErrors[key];
    const errorId = `ny-error-${encodeURIComponent(key)}`;
    return <label className="ny-field"><span>{label}</span><input type="text" inputMode="decimal" value={edits[key] ?? (original === null ? "" : String(percent ? Number(original) / 10000 : original))} placeholder={placeholder} disabled={Boolean(busy) || !canEdit} aria-invalid={Boolean(message)} aria-describedby={message ? errorId : undefined} onChange={event => edit(path, event.target.value)}/>{message && <small id={errorId} role="alert">{message}</small>}</label>;
  }
  async function run(mode: "prepare" | "preview" | "save", forecastId = selectedSourceId || data?.source?.id) {
    if (!identity || !canEdit || inFlight.current) return;
    if (mode === "prepare" && (!forecastId || unsaved)) return;
    const prepared = mode === "prepare" ? null : inputs ? applyNextYearEdits(inputs, edits) : null;
    if (mode !== "prepare" && (!draft || !prepared?.inputs)) {
      const errors = prepared?.errors ?? {}; setFieldErrors(errors);
      const first = Object.keys(errors)[0]; let section = ""; try { section = first ? JSON.parse(first)[0] : ""; } catch { /* malformed edit keys stay validation errors */ }
      const nextTask: Task = section === "entry" ? 2 : ["rates", "plan"].includes(section) ? 3 : ["finance", "class", "tax"].includes(section) ? 4 : 1;
      setFilters({ ...emptyFilters }); changeTask(nextTask); setError(first ? `${nextYearTasks[nextTask - 1]}：${errors[first]}` : "请补齐输入"); return;
    }
    inFlight.current = true; setBusy(mode); setError(""); setNotice("");
    try {
      if (mode === "prepare") { accept(await prepareNextYearBudget(identity, forecastId!, draft?.id ?? null, fiscalYear)); changeTask(5); setNotice(`${fy(fiscalYear)} 新草稿已生成，原版本已保留`); }
      else if (mode === "preview") { const result = await previewNextYearBudget(identity, draft!.id, prepared!.inputs!); setPreview(result.calculation); changeTask(5); }
      else { accept(await reviseNextYearBudget(identity, draft!.id, prepared!.inputs!, reason.trim() || (balanceMessage ? "采用自动调平建议" : "调整预算参数"))); setNotice("已保存并重算"); }
    } catch (failure) { setError(failure instanceof Error ? failure.message : "操作未完成"); }
    finally { inFlight.current = false; setBusy(""); }
  }
  const combinations = visibleInputs?.combinations ?? [], comboById = useMemo(() => new Map(combinations.map(row => [row.id, row])), [combinations]);
  const planRows = useMemo(() => combinations.filter(row => row.enabled).flatMap(combination => nextYearSeasons.filter(season => {
    if (season === "暑假" && effectiveNextYearEntry(combination)) return false;
    const result = calculation?.rows.find(row => row.combination_id === combination.id && row.season === season);
    return result?.planned_new_applied || (!result && combination.added);
  }).map(season => ({ combination, season, students: visibleInputs?.plans.find(row => row.combination_id === combination.id && row.season === season)?.students }))), [combinations, visibleInputs?.plans, calculation]);
  function distributeTargets() {
    if (!inputs || !draft || !canEdit || busy) return;
    try { const allocated = allocateNextYearTrackTargets(edits[nextYearEditKey("target")] ?? inputs.target_revenue_yuan ?? "", Object.fromEntries(draft.calculation.tracks.map(row => [row.track, String(Math.max(0, forecastNumber(row.revenue_yuan) ?? 0))])), 2);
      setEdits(previous => ({ ...previous, ...Object.fromEntries(Object.entries(allocated).map(([name, value]) => [nextYearEditKey("track-target", name), value])) }));
      setFieldErrors({}); setError(""); setPreview(null); setNotice("");
    } catch (failure) { setError(failure instanceof Error ? failure.message : "目标分配未完成"); }
  }
  async function download() {
    if (!calculation || exporting) return; setExporting(true); setError("");
    try { const rows = calculation.rows.filter(row => matches(row, filters)); await downloadBudgetWorkbook(nextYearExportColumns, nextYearExportRows(rows), preview ? `${fy(fiscalYear)}-未保存预览明细` : `${fy(fiscalYear)}-N${historical?.draft?.sequence ?? draft?.sequence}-预算明细`); }
    catch (failure) { setError(failure instanceof Error ? failure.message : "导出失败"); } finally { setExporting(false); }
  }
  function stage(next: NextYearInputs, message: string, discard: (path: (string | number)[]) => boolean = () => false) { const remaining = Object.fromEntries(Object.entries(edits).filter(([key]) => { try { return !discard(JSON.parse(key)); } catch { return true; } })); const prepared = applyNextYearEdits(next, remaining); if (!prepared.inputs) { setFieldErrors(prepared.errors); setError(Object.values(prepared.errors)[0]); return; } setInputs(prepared.inputs); setEdits({}); setPreview(null); setFieldErrors({}); setError(""); setNotice(message); setBalanceMessage(""); setBalanceIssues([]); }
  function stageEdits(next: NextYearEdits) {
    if (!inputs || !canEdit || busy) return;
    const merged = { ...edits, ...next }, checked = applyNextYearEdits(inputs, merged);
    if (!checked.inputs) { setFieldErrors(checked.errors); setError(Object.values(checked.errors)[0]); return; }
    setEdits(merged); setPreview(null); setNotice(`已暂存 ${Object.keys(next).length} 项修改，预览后保存`); setError("");
  }
  function bulkApply() {
    if (!inputs || !bulkValue.trim()) { setError("先填写批量修改值"); return; }
    const changes = Object.fromEntries(inputs.rates.filter(row => matches(row, filters) && nextYearRateUsage(inputs, row)[bulkField === "renewal_ppm" ? "renewal" : "share"]).map(row => [nextYearEditKey("rates", row.id, bulkField), bulkValue]));
    if (!Object.keys(changes).length) { setError("当前范围没有适用参数"); return; } stageEdits(changes);
  }
  function pasteParameters() { try { if (inputs) stageEdits(parseNextYearParameterPaste(paste, inputs.rates.filter(row => matches(row, filters)))); } catch (failure) { setError(failure instanceof Error ? failure.message : "粘贴失败"); } }
  async function copyParameters() {
    try { if (!inputs || !navigator.clipboard?.writeText) throw new Error("浏览器无法复制，请导出参数明细");
      const current = applyNextYearEdits(inputs, edits); if (!current.inputs) throw new Error(Object.values(current.errors)[0]);
      const rows = current.inputs.rates.filter(row => matches(row, filters));
      await navigator.clipboard.writeText([["参数ID", "续班率", "新生占比"], ...rows.map(row => [row.id, row.renewal_ppm / 10000, row.share_ppm / 10000])].map(row => row.join("\t")).join("\n")); setNotice(`已复制 ${rows.length} 行，可在 Excel 编辑后粘贴回来`);
    } catch (failure) { setError(failure instanceof Error ? failure.message : "复制失败"); }
  }
  function addBusiness() {
    if (!inputs || !canEdit || busy) return;
    const parsed = applyNextYearEdits(inputs, edits); if (!parsed.inputs) { setError(Object.values(parsed.errors)[0]); return; }
    const dims = Object.fromEntries((["campus", "track", "product", "grade", "subject"] as const).map(key => [key, newBusiness[key].trim()])) as Pick<NextYearCombination, "campus" | "track" | "product" | "grade" | "subject">;
    const price = forecastNumber(newBusiness.price), size = forecastNumber(newBusiness.classSize);
    if (Object.values(dims).some(value => !value) || !newBusiness.reason.trim()) { setError("填写新业务的五个维度和新增原因"); return; }
    if (price === null || price < 0 || price > 1000000000 || size === null || !Number.isInteger(size) || size < 1 || size > 10000) { setError("填写有效学费和计划班额"); return; }
    if (inputs.combinations.some(row => Object.entries(dims).every(([key, value]) => row[key as keyof typeof dims] === value))) { setError("该业务组合已存在，请直接编辑"); return; }
    const id = `added-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`, next = parsed.inputs;
    next.combinations.push({ id, ...dims, entry: false, entry_override: false, enabled: true, source_entry_weight: 0,
      source_new_by_season: Object.fromEntries(nextYearSeasons.map(season => [season, 0])) as NextYearCombination["source_new_by_season"], opening_carryover_yuan: "0", added: true, reason: newBusiness.reason.trim() });
    for (const [index, season] of nextYearSeasons.entries()) {
      if (!next.rates.some(row => row.season === season && row.track === dims.track && row.product === dims.product && row.campus === dims.campus && row.grade === dims.grade)) {
        const template = next.rates.find(row => row.season === season && row.track === dims.track && row.grade === dims.grade);
        next.rates.push({ id: `${id}-rate-${index}`, season, track: dims.track, product: dims.product, campus: dims.campus, grade: dims.grade, renewal_ppm: template?.renewal_ppm ?? 0, share_ppm: template?.share_ppm ?? 0 });
      }
      next.finance.push({ id: `${id}-finance-${index}`, combination_id: id, season, price_yuan: String(price), discount_ppm: 1000000, recognition_ppm: 1000000 });
      next.plans.push({ combination_id: id, season, students: 0, locked: true, reason: newBusiness.reason.trim() });
    }
    const classSetting = next.class_sizes.find(row => row.track === dims.track && row.product === dims.product);
    if (classSetting) classSetting.planned_class_size = size;
    else next.class_sizes.push({ track: dims.track, product: dims.product, minimum: 1, planned_class_size: size });
    if (!Object.hasOwn(next.track_targets, dims.track)) next.track_targets[dims.track] = Object.values(next.track_targets).some(value => value !== null) ? "0" : null;
    stage(next, `新业务已加入；${dims.product}共用计划班额 ${size} 人。填写入口或计划新生后预览`); setNewBusiness({ ...newBusiness, campus: "", product: "" }); setManualOpen(true);
  }
  async function balance() {
    if (!identity || !inputs || !draft || !canEdit || inFlight.current) return;
    const prepared = applyNextYearEdits(inputs, edits); if (!prepared.inputs) { setFieldErrors(prepared.errors); setError(Object.values(prepared.errors)[0]); return; }
    if (forecastNumber(prepared.inputs.target_revenue_yuan) === null) { changeTask(1); setError("先填写全年经营目标"); return; }
    inFlight.current = true; setBusy("balance"); setError(""); setBalanceIssues([]);
    try { const result = await balanceNextYearBudget(identity, draft.id, prepared.inputs);
      if (!result.proposed_inputs) { setError(result.message || "本次尚未找到可应用的调平建议，原预算未修改"); setBalanceIssues(result.constraints?.class_diagnostics ?? []); return; }
      setInputs(result.proposed_inputs); setEdits({}); setPreview(result.calculation); changeTask(5);
      const gap = forecastNumber(result.calculation.annual.gap_yuan);
      const message = gap !== null && Math.abs(gap) <= .01 ? "已生成调平建议，保存后生效" : result.message || `已生成可行建议，剩余目标差额 ${money(gap)} 万元`;
      setBalanceMessage(message); setNotice(message);
    } catch (failure) { setError(failure instanceof Error ? failure.message : "调平未完成"); }
    finally { setBusy(""); inFlight.current = false; }
  }
  async function viewVersion(id: string) {
    if (!identity || busy) return; if (unsaved) { setError("先保存或放弃修改，再查看历史版本"); return; }
    if (id === draft?.id) { setHistorical(null); return; }
    setBusy("version"); setError(""); try { setHistorical(await getNextYearVersion(identity, id, fiscalYear)); changeTask(5); } catch (failure) { setError(failure instanceof Error ? failure.message : "读取版本失败"); } finally { setBusy(""); }
  }
  async function restoreVersion() {
    if (!identity || !draft || !historical?.draft || busy) return;
    setBusy("restore"); setError(""); try { const sequence = historical.draft.sequence;
      accept(await restoreNextYearVersion(identity, draft.id, historical.draft.id, reason.trim() || `从 N${sequence} 恢复为新草稿`, fiscalYear)); setNotice(`已从 N${sequence} 恢复为新草稿`);
    } catch (failure) { setError(failure instanceof Error ? failure.message : "恢复失败"); } finally { setBusy(""); }
  }
  async function chooseYear(year: number) { if (unsaved) { setError("先保存或放弃修改，再切换财年"); return; } if (year === fiscalYear) return;
    if (await load(undefined, year)) { const url = new URL(location.href); url.searchParams.set("year", String(year)); url.searchParams.delete("nyTask"); history.pushState({}, "", url); setTask(5); setFilters({ ...emptyFilters }); }
  }
  const manualRows: NextYearRow[] = combinations.filter(row => row.enabled).flatMap(combination => nextYearSeasons.filter(season => !(season === "暑假" && effectiveNextYearEntry(combination))).map(season => calculation?.rows.find(row => row.combination_id === combination.id && row.season === season) ?? {
    ...combination, combination_id: combination.id, season, old_students: null, new_students: null, total_students: null, revenue_yuan: null,
    price_yuan: null, discount_ppm: null, renewal_ppm: null, share_ppm: null, class_size_status: "NO_PLANNED_CLASS", planned_new_applied: true,
  }));
  const blockers = calculation?.issues.filter(issue => issue.severity === "BLOCKING") ?? [], warnings = calculation?.issues.filter(issue => issue.severity === "WARNING") ?? [];
  const belowClassSize = calculation?.rows.filter(row => row.class_size_status === "BELOW_MINIMUM_CLASS_SIZE" || row.class_size_status === "UNDER_MINIMUM").length ?? 0;
  const sourceOptions = catalog?.sources.filter(item => (item.fiscal_year ?? catalog.source_fiscal_year) === fiscalYear - 1) ?? (data?.source ? [data.source] : []);
  const selectedSource = sourceOptions.find(item => item.id === selectedSourceId) ?? source ?? data?.source;
  const sourcePicker = canEdit && sourceOptions.length > 0 && <div className="ny-source-picker"><label className="ny-field"><span>基准预测</span><select aria-label="基准预测版本" value={selectedSourceId || selectedSource?.id || ""} disabled={Boolean(busy) || unsaved} onChange={event => setSelectedSourceId(event.target.value)}>{sourceOptions.map(item => <option key={item.id} value={item.id}>{fy(item.fiscal_year ?? fiscalYear - 1)} · P{item.sequence} · {item.as_of} · {money(item.forecast_revenue)} 万元</option>)}</select></label>{draft && <button disabled={Boolean(busy) || unsaved || !selectedSource} onClick={() => void run("prepare", selectedSource?.id)}>以此预测生成新草稿</button>}</div>;
  const entryState = visibleInputs ? applyNextYearEdits(visibleInputs, edits).inputs ?? visibleInputs : null;
  const specifiedEntry = entryState?.combinations.filter(row => row.enabled && effectiveNextYearEntry(row) && row.entry_students !== null && row.entry_students !== undefined).reduce((sum, row) => sum + (row.entry_students ?? 0), 0) ?? 0;
  const automaticEntryCount = entryState?.combinations.filter(row => row.enabled && effectiveNextYearEntry(row) && (row.entry_students === null || row.entry_students === undefined)).length ?? 0;
  return <div className="budget-shell decision-shell next-year-shell"><a className="skip-link" href="#next-year-content">跳到主要内容</a>
    <FY27Header active="next-year" onBeforeLeave={beforeLeave}><label className="ny-year-context"><span className="sr-only">预算财年</span><select aria-label="预算财年" value={fiscalYear} disabled={Boolean(busy)} onChange={event => void chooseYear(Number(event.target.value))}>{(catalog?.fiscal_years.length ? catalog.fiscal_years : [{ fiscal_year: fiscalYear }]).map(item => <option value={item.fiscal_year} key={item.fiscal_year}>{fy(item.fiscal_year)} 学校版</option>)}</select></label><div className="decision-version"><span className="status-dot"/><span>{historical ? "历史只读" : "预算草稿"}</span>{draft && <small>N{historical?.draft?.sequence ?? draft.sequence}</small>}</div></FY27Header>
    <main className="decision-workspace" id="next-year-content">
      {draft && <NextYearTaskNavigation task={task} onChange={changeTask} fiscalYear={fiscalYear}/>}
      <header className="ny-heading"><div><h1 ref={heading} tabIndex={-1}>{fy(fiscalYear)} 预算</h1>{source && <span className="ny-source-label">{fy(source.fiscal_year ?? fiscalYear - 1)} 预测 P{source.sequence}</span>}</div>{draft && <div className="ny-heading-actions"><button aria-expanded={versionsOpen} disabled={Boolean(busy)} onClick={() => setVersionsOpen(!versionsOpen)}>版本记录</button><button disabled={Boolean(busy) || exporting || !calculation?.rows.length} onClick={() => void download()}>{exporting ? "正在导出…" : "导出明细"}</button></div>}</header>
      {versionsOpen && data && <section className="ny-section ny-versions"><div className="ny-section-heading"><h2>版本记录</h2>{historical && <button disabled={Boolean(busy)} onClick={() => setHistorical(null)}>返回当前草稿</button>}</div><PagedTable rows={data.history} label={`${fy(fiscalYear)}预算版本`} headers={["版本", "保存时间", "调整原因", "操作"]} render={row => <><th>N{row.sequence}{row.id === draft?.id ? " · 当前" : ""}</th><td>{new Date(row.created_at).toLocaleString("zh-CN")}</td><td>{String(row.reason ?? "预算调整")}</td><td><button disabled={Boolean(busy)} onClick={() => void viewVersion(row.id)}>{row.id === historical?.draft?.id ? "正在查看" : "查看"}</button></td></>}/></section>}
      {error && <div className="ny-message" role="alert"><span>{error}</span>{!data && <button disabled={Boolean(busy)} onClick={() => void load()}>重新读取</button>}<button aria-label="关闭错误" onClick={() => setError("")}>关闭</button></div>}
      {balanceIssues.length > 0 && <section className="ny-balance-diagnostics" aria-label="调平前需处理"><h2>先处理成班问题</h2>{balanceIssues.map(issue => <div key={issue.code}><span>{issue.count} 项 · {issue.message}</span><span>{issue.action}</span></div>)}<div className="ny-heading-actions"><button onClick={() => { setFilters({ ...emptyFilters }); changeTask(4); }}>查看成班明细</button><button onClick={() => { setFilters({ ...emptyFilters }); setManualOpen(true); }}>手动调整人次</button><button onClick={() => { setFilters({ ...emptyFilters }); changeTask(1); }}>调整业务范围</button></div></section>}
      {notice && <div className="ny-message is-success" role="status"><span>{notice}</span><button aria-label="关闭提示" onClick={() => setNotice("")}>关闭</button></div>}
      {busy === "load" && <div className="ny-loading" aria-busy="true"><div/><div/><p role="status">正在读取 FY28 预算…</p></div>}
      {!busy && data && !draft && <section className="ny-start">{data.source ? <>{sourcePicker}<div className="ny-metrics"><div className="ny-metric-primary"><span>{fy(fiscalYear - 1)} 预计收入（万元）</span><strong>{money(selectedSource?.forecast_revenue)}</strong></div><div><span>{fy(fiscalYear - 1)} 预计人次</span><strong>{format(selectedSource?.forecast_total)}</strong></div></div><button className="decision-primary" disabled={!canEdit} onClick={() => void run("prepare")}>生成 {fy(fiscalYear)} 草稿</button></> : <div className="ny-empty"><h2>先确认 {fy(fiscalYear - 1)} 预测</h2><a className="decision-primary" href="/budget/forecast?archive=fy27">前往达成预测</a></div>}</section>}
      {busy === "prepare" && !draft && <p role="status">正在生成 FY28 草稿…</p>}
      {draft && inputs && visibleInputs && calculation && <>
        {historical?.draft && <div className="ny-history-banner"><strong>正在查看 N{historical.draft.sequence} · 只读</strong><button disabled={Boolean(busy)} onClick={() => setHistorical(null)}>返回当前草稿</button>{Boolean(identity?.roles.some(role => ["DATA_MAINTAINER", "DEPARTMENT_BUDGET_OWNER"].includes(role))) && <button disabled={Boolean(busy)} onClick={() => void restoreVersion()}>以此版本恢复新草稿</button>}</div>}
        {!historical && data?.source_changed && <div className="ny-message" role="status"><span>{fy(fiscalYear - 1)} 已确认预测更新至 P{data.source?.sequence}</span><button disabled={Boolean(busy) || unsaved || !canEdit} onClick={() => void run("prepare", data.source?.id)}>用新预测生成草稿</button></div>}
        <div className="ny-task-heading"><h2>{nextYearTasks[task - 1]}</h2>{historical ? <span>历史 N{historical.draft?.sequence}</span> : preview ? <span className="ny-edit-state" role="status">未保存预览</span> : inputChanged ? <span className="ny-edit-state" role="status">待重算 · 原结果 N{draft.sequence}</span> : <span>已保存 N{draft.sequence}</span>}</div>
        {task !== 5 && <section className="ny-filters" aria-label={`${fy(fiscalYear)}预算筛选`}>{(Object.entries(dimensionLabels) as [Dimension, string][]).map(([key, label]) => <label key={key}>{label}<select value={filters[key]} onChange={event => setFilters({ ...filters, [key]: event.target.value })}><option value="">全部</option>{(key === "season" ? [...nextYearSeasons] : [...new Set(combinations.map(row => row[key]))].sort((a, b) => dimensionSort(key, a, b))).map(value => <option key={value}>{value}</option>)}</select></label>)}{Object.values(filters).some(Boolean) && <button onClick={() => setFilters({ ...emptyFilters })}>清除筛选</button>}</section>}
        {task === 1 && <section className="ny-section">{sourcePicker}{source && <div className="ny-opening"><span>{fy(fiscalYear - 1)} 预测收入（万元） <strong>{money(source.forecast_revenue)}</strong></span></div>}<div className="ny-form-grid">{field(["target"], visibleInputs.target_revenue_yuan, "全年经营目标（元）")}{Object.entries(visibleInputs.track_targets).map(([track, amount]) => <div key={track}>{field(["track-target", track], amount, `${track}目标（元）`)}</div>)}</div><button disabled={Boolean(busy) || !canEdit || forecastNumber(edits[nextYearEditKey("target")] ?? inputs.target_revenue_yuan) === null} onClick={distributeTargets}>按预算收入分配赛道目标</button><h3>业务范围</h3><PagedTable rows={combinations.filter(row => matches(row, filters))} label={`${fy(fiscalYear)}业务范围`} headers={["校区 / 业务", "入口", "参与预算"]} render={row => <><th>{textDimensions(row)}{row.added && <small>新业务</small>}</th><td>{effectiveNextYearEntry(row) ? "是" : "否"}</td><td><label className="ny-checkbox-target"><input type="checkbox" aria-label={`${textDimensions(row)}参与预算`} checked={row.enabled} disabled={Boolean(busy) || !canEdit} onChange={event => { setInputs({ ...inputs, combinations: inputs.combinations.map(item => item.id === row.id ? { ...item, enabled: event.target.checked } : item) }); setPreview(null); setNotice(""); setBalanceIssues([]); }}/></label></td></>}/>
          {canEdit && <details className="ny-add-business"><summary>新增校区 / 产品</summary><div className="ny-form-grid">{(["campus", "track", "product", "grade", "subject", "price", "classSize", "reason"] as const).map(key => <label className="ny-field" key={key}><span>{{ campus: "校区", track: "赛道", product: "产品", grade: "年级", subject: "科目", price: "学费（元）", classSize: "产品计划班额（共用）", reason: "新增原因" }[key]}</span><input value={newBusiness[key]} disabled={Boolean(busy)} onChange={event => setNewBusiness({ ...newBusiness, [key]: event.target.value })}/></label>)}</div><button disabled={Boolean(busy)} onClick={addBusiness}>加入预算范围</button></details>}
        </section>}
        {task === 2 && <section className="ny-section"><div className="ny-form-grid">{field(["entry"], visibleInputs.entry_total, "年度入口人次")}</div><h3>入口年级</h3><div className="ny-grade-picks">{[...new Set(combinations.filter(row => matches(row, filters)).map(row => row.grade))].sort((a, b) => dimensionSort("grade", a, b)).map(grade => { const rows = combinations.filter(row => row.grade === grade && matches(row, filters)), selected = rows.every(effectiveNextYearEntry); return <button key={grade} aria-pressed={selected} disabled={!canEdit || Boolean(busy)} onClick={() => stage({ ...inputs, combinations: inputs.combinations.map(row => row.grade === grade && matches(row, filters) ? { ...row, entry_override: !selected, ...(!selected ? {} : { entry_students: null, entry_locked: false }) } : row) }, `${grade}入口范围已调整`, path => selected && (path[0] === "entry-grade" && path[1] === grade || path[0] === "entry-count" && rows.some(row => row.id === path[1])))}>{grade}{selected ? " ✓" : ""}</button>; })}</div>
          <h3>年级入口人次 · 全部校区</h3><div className="ny-entry-status" role="status"><span>年度入口 {format(entryState?.entry_total)}</span><span>已指定 {format(specifiedEntry)}</span><span>{specifiedEntry > (entryState?.entry_total ?? 0) ? `超出 ${format(specifiedEntry - (entryState?.entry_total ?? 0))}，请减少指定人数` : automaticEntryCount ? `余下 ${format((entryState?.entry_total ?? 0) - specifiedEntry)} 自动分配` : specifiedEntry !== entryState?.entry_total ? "指定人数尚未等于年度入口" : "人数已分配完整"}</span>{inputChanged && !preview && <span>已计算列为上次结果，预览后更新</span>}</div>
          <PagedTable rows={[...new Set(combinations.filter(row => row.enabled && effectiveNextYearEntry(row)).map(row => row.grade))].sort((a, b) => dimensionSort("grade", a, b))} label="年级入口分配" headers={["年级", "指定入口人次", "已计算入口人次", "入口业务数"]} render={grade => { const members = combinations.filter(row => row.enabled && effectiveNextYearEntry(row) && row.grade === grade), assigned = members.every(row => row.entry_students !== null && row.entry_students !== undefined) ? members.reduce((sum, row) => sum + (row.entry_students ?? 0), 0) : null;
            const calculated = calculation.entry_details ? calculation.entry_details.filter(row => row.grade === grade).reduce((sum, row) => sum + row.students, 0) : calculation.rows.filter(row => row.season === "暑假" && row.entry && row.grade === grade).reduce((sum, row) => sum + (row.new_students ?? 0), 0);
            return <><th>{grade}</th><td>{field(["entry-grade", grade], assigned, `${grade}入口人次`, false, "自动分配")}</td><td>{format(calculated)}</td><td>{members.length}</td></>; }}/>
          <details><summary>按具体业务调整</summary><PagedTable rows={combinations.filter(row => matches(row, filters))} label="入口分配明细" headers={["校区 / 业务", "入口", "指定人次", "分配方式"]} render={row => <><th>{textDimensions(row)}</th><td><label className="ny-checkbox-target"><input type="checkbox" checked={effectiveNextYearEntry(row)} aria-label={`${textDimensions(row)}设为入口`} disabled={!canEdit || Boolean(busy)} onChange={event => stage({ ...inputs, combinations: inputs.combinations.map(item => item.id === row.id ? { ...item, entry_override: event.target.checked, ...(!event.target.checked ? { entry_students: null, entry_locked: false } : {}) } : item) }, "入口范围已调整", path => !event.target.checked && (path[0] === "entry-count" && path[1] === row.id || path[0] === "entry-grade" && path[1] === row.grade))}/></label></td><td>{effectiveNextYearEntry(row) ? field(["entry-count", row.id], row.entry_students ?? null, `${textDimensions(row)}入口人次（留空自动分配）`) : "—"}</td><td>{edits[nextYearEditKey("entry-count", row.id)]?.trim() || row.entry_locked ? "已锁定" : row.source_entry_weight ? "自动分配" : "需指定人次"}</td></>}/></details>
          <h3>校区入口</h3><PagedTable rows={calculation.entry_allocations.filter(row => !filters.campus || row.campus === filters.campus)} label="FY28 校区入口" headers={["校区", "分配人次", "参考入口人次"]} render={row => <><th>{row.campus}</th><td>{format(row.students)}</td><td>{format(row.source_weight)}</td></>}/><div className="ny-opening"><span>春季预测可续人次 <strong>{format(calculation.opening.eligible_students)}</strong></span><span>暑假期初老生 <strong>{format(calculation.opening.summer_old_students)}</strong></span></div></section>}
        {task === 3 && <section className="ny-section"><h3>续班率与新生占比</h3>
          {canEdit && <div className="ny-bulk-panel"><div className="ny-bulk-toolbar"><label>批量修改<select aria-label="批量修改字段" value={bulkField} onChange={event => setBulkField(event.target.value)}><option value="renewal_ppm">续班率</option><option value="share_ppm">新生占比</option></select></label><label>设为（%）<input aria-label="批量修改比例" value={bulkValue} inputMode="decimal" onChange={event => setBulkValue(event.target.value)}/></label><button disabled={Boolean(busy)} onClick={bulkApply}>应用到当前筛选</button><button disabled={Boolean(busy)} onClick={() => void copyParameters()}>复制粘贴模板</button></div><details><summary>粘贴参数</summary><label className="ny-field"><span>参数ID、续班率、新生占比（三列，Tab 分隔）</span><textarea rows={5} value={paste} onChange={event => setPaste(event.target.value)}/></label><button disabled={Boolean(busy) || !paste.trim()} onClick={pasteParameters}>校验并暂存</button></details></div>}
          <PagedTable rows={visibleInputs.rates.filter(row => matches(row, filters))} label={`${fy(fiscalYear)}招生参数`} invalidPath={invalidPath("rates")} editPath={row => ["rates", row.id]} headers={["校区 / 业务", "季度", "续班率（%）", "新生占比（%）", "调平锁定"]} render={row => { const usage = nextYearRateUsage(visibleInputs, row); return <><th>{textDimensions(row)}</th><td>{row.season}</td><td>{usage.renewal ? field(["rates", row.id, "renewal_ppm"], row.renewal_ppm, `${row.season} ${textDimensions(row)}续班率（%）`, true) : "—"}</td><td>{usage.share ? field(["rates", row.id, "share_ppm"], row.share_ppm, `${row.season} ${textDimensions(row)}新生占比（%）`, true) : "入口分配"}</td><td><label className="ny-checkbox-target"><input type="checkbox" aria-label={`${row.season}${textDimensions(row)}调平锁定`} checked={Boolean(row.locked)} disabled={!canEdit || Boolean(busy)} onChange={event => { setInputs({ ...inputs, rates: inputs.rates.map(item => item.id === row.id ? { ...item, locked: event.target.checked } : item) }); setPreview(null); setBalanceIssues([]); }}/></label></td></>; }}/>
          <details className="ny-bounds"><summary>自动调平范围</summary><p className="ny-range-note">未设范围：当前值 ±5 个百分点；留空恢复默认</p><PagedTable rows={visibleInputs.rates.filter(row => matches(row, filters))} label="自动调平参数范围" headers={["校区 / 业务", "季度", "续班率下限（%）", "续班率上限（%）", "新生占比下限（%）", "新生占比上限（%）"]} render={row => <><th>{textDimensions(row)}</th><td>{row.season}</td>{(["renewal_lower_ppm", "renewal_upper_ppm", "share_lower_ppm", "share_upper_ppm"] as const).map((key, index) => {
            const current = forecastNumber(edits[nextYearEditKey("rates", row.id, key.startsWith("share") ? "share_ppm" : "renewal_ppm")]) ?? row[key.startsWith("share") ? "share_ppm" : "renewal_ppm"] / 10000;
            const fallback = index % 2 ? Math.min(key.startsWith("share") ? 99.99 : 100, current + 5) : Math.max(0, current - 5);
            const label = ["续班率下限", "续班率上限", "新生占比下限", "新生占比上限"][index];
            return <td key={key}>{field(["bounds", row.id, key], row[key] ?? null, `${row.season}${textDimensions(row)}${label}（%）`, true, `默认 ${format(fallback, 2)}`)}</td>;
          })}</>}/></details>
          <details className="ny-plans" open={invalidPath("plan") ? true : undefined}><summary>计划新生 · 零老生范围</summary><PagedTable rows={planRows.filter(row => matches({ ...row.combination, season: row.season }, filters))} label="FY28 计划新生" invalidPath={invalidPath("plan")} editPath={row => ["plan", row.combination.id, row.season]} headers={["校区 / 业务", "季度", "计划新生人次"]} render={row => <><th>{textDimensions(row.combination)}</th><td>{row.season}</td><td>{field(["plan", row.combination.id, row.season], row.students ?? "", `${row.season} ${textDimensions(row.combination)}计划新生人次`)}</td></>}/></details></section>}
        {task === 4 && <section className="ny-section"><div className="ny-form-grid">{field(["tax"], visibleInputs.tax_factor, "预算含税转换系数")}</div><h3>价格与折扣</h3><PagedTable rows={visibleInputs.finance.filter(row => matches({ ...comboById.get(row.combination_id), season: row.season }, filters))} label="FY28 财务参数" invalidPath={invalidPath("finance")} editPath={row => ["finance", row.id]} headers={["校区 / 业务", "季度", "学费（元）", "折扣（%）", "当季结转（%）"]} render={row => { const title = textDimensions(comboById.get(row.combination_id) ?? {}); return <><th>{title}</th><td>{row.season}</td><td>{field(["finance", row.id, "price_yuan"], row.price_yuan, `${row.season} ${title}学费（元）`)}</td><td>{field(["finance", row.id, "discount_ppm"], row.discount_ppm, `${row.season} ${title}折扣（%）`, true)}</td><td>{field(["finance", row.id, "recognition_ppm"], row.recognition_ppm, `${row.season} ${title}当季结转（%）`, true)}</td></>; }}/><h3>班量与班额</h3><PagedTable rows={visibleInputs.class_sizes.map((row, index) => ({ ...row, index })).filter(row => matches(row, filters))} label="班量参数" invalidPath={invalidPath("class")} editPath={row => ["class", row.index]} headers={["赛道 / 产品", "最低成班人数", "计划班额"]} render={row => <><th>{row.track} / {row.product}</th><td>{field(["class", row.index], row.minimum, `${row.track} ${row.product}最低成班人数`)}</td><td>{field(["class-size", row.index], row.planned_class_size ?? null, `${row.track} ${row.product}计划班额`)}</td></>}/><h3>季度班量</h3><PagedTable rows={calculation.rows.filter(row => matches(row, filters))} label="班量计算明细" headers={["校区 / 业务", "季度", "人次", "计划班额", "班量", "开班状态"]} render={row => <><th>{textDimensions(row)}</th><td>{row.season}</td><td>{format(row.total_students)}</td><td>{format(row.planned_class_size)}</td><td>{format(row.class_count)}</td><td>{nextYearClassStatus(row.class_size_status)}</td></>}/></section>}
        {task === 5 && <><NextYearOverview calculation={calculation} filters={filters} onSetTarget={() => changeTask(1)} summaryOnly/><div className="ny-class-summary"><span>全年班量 <strong>{format(calculation.annual.class_count)}</strong></span>{calculation.annual.class_count === null || calculation.annual.class_count === undefined ? <button onClick={() => changeTask(4)}>设置计划班额</button> : <button onClick={() => changeTask(4)}>查看班量</button>}</div><BudgetAnalysisView key={`${historical?.draft?.id ?? draft.id}-${preview ? "preview" : "saved"}`} facts={nextYearAnalysisFacts(calculation, visibleInputs)} contextKey={`next-year:${historical?.draft?.id ?? draft.id}:${preview ? "preview" : "saved"}`} fiscalYear={fiscalYear} annualTarget={forecastNumber(calculation.annual.target_revenue_yuan)} initialFilters={filters} onFiltersChange={setFilters} hideSummary/></>}
        {!historical && canEdit && <section className="ny-adjustment"><div className="ny-section-heading"><h2>预算调节</h2><div className="ny-heading-actions"><button aria-expanded={manualOpen} disabled={Boolean(busy)} onClick={() => setManualOpen(!manualOpen)}>手动调节</button><button className="decision-primary" disabled={Boolean(busy)} onClick={() => void balance()}>{busy === "balance" ? "正在计算…" : "按经营目标自动调平"}</button></div></div>
          {manualOpen && <PagedTable rows={manualRows.filter(row => matches(row, filters))} label="预算手动调节明细" headers={["校区 / 业务", "季度", "当前新生人次", "指定新生人次", "锁定"]} render={row => { const plan = visibleInputs.plans.find(item => item.combination_id === row.combination_id && item.season === row.season), pending = edits[nextYearEditKey("manual", row.combination_id, row.season)] !== undefined; return <><th>{textDimensions(row)}</th><td>{row.season}</td><td>{format(row.new_students)}</td><td>{field(["manual", row.combination_id, row.season], plan?.locked ? plan.students : null, `${row.season}${textDimensions(row)}指定新生人次`)}</td><td><label className="ny-checkbox-target"><input type="checkbox" checked={pending || Boolean(plan?.locked)} disabled={Boolean(busy)} aria-label={`${row.season}${textDimensions(row)}手动人数锁定`} onChange={event => { if (!event.target.checked) stage({ ...inputs, plans: inputs.plans.map(item => item.combination_id === row.combination_id && item.season === row.season ? { ...item, locked: false } : item) }, "已解除手动锁定", path => path[0] === "manual" && path[1] === row.combination_id && path[2] === row.season); else stageEdits({ [nextYearEditKey("manual", row.combination_id, row.season)]: String(row.new_students ?? 0) }); }}/></label></td></>; }}/>}</section>}
        {preview && !historical && <ImpactComparison before={draft.calculation} after={preview}/>}
        {historical?.draft && <ImpactComparison before={historical.draft.calculation} after={draft.calculation}/>}
        {(blockers.length > 0 || warnings.length > 0) && <details className="ny-issues"><summary>{blockers.length ? `需处理 ${blockers.length}项` : `提醒 ${warnings.length}项`}{belowClassSize > 0 && <span>开班未达标 {belowClassSize}项</span>}</summary><ul>{calculation.issues.map((issue, index) => <li key={`${issue.code}-${index}`}><strong>{issue.severity === "BLOCKING" ? "需处理" : "提醒"}</strong><div>{issue.combination_id && <small>{textDimensions(comboById.get(issue.combination_id) ?? {})}</small>}<span>{issue.season} {issue.message}</span></div></li>)}</ul></details>}
        <footer className="ny-save-bar"><div>{historical ? <span>历史版本 N{historical.draft?.sequence} · 只读</span> : unsaved ? <span className="ny-edit-state">未保存</span> : <span>预算草稿 N{draft.sequence}</span>}{inputChanged && !historical && <button disabled={Boolean(busy)} onClick={() => { setInputs(structuredClone(draft.inputs)); setEdits({}); setReason(""); setPreview(null); setFieldErrors({}); setError(""); setBalanceMessage(""); setBalanceIssues([]); }}>放弃修改</button>}</div>{canEdit && <div className="ny-save-actions"><label className="ny-field"><span>调整备注（选填）</span><input value={reason} maxLength={1000} disabled={Boolean(busy)} onChange={event => setReason(event.target.value)}/></label><button disabled={Boolean(busy) || !inputChanged} onClick={() => void run("preview")}>{busy === "preview" ? "正在预览…" : "预览"}</button><button className="decision-primary" disabled={Boolean(busy) || !inputChanged} onClick={() => void run("save")}>{busy === "save" ? "正在保存…" : "保存并重算"}</button></div>}</footer>
        <details className="ny-how"><summary>拆解怎么算</summary><dl><div><dt>入口</dt><dd>指定入口优先，其余按参考人次分配，合计保持不变</dd></div><div><dt>期初老生</dt><dd>{fy(fiscalYear - 1)} 春季预测升年级续入暑假</dd></div><div><dt>后续季度</dt><dd>按续班率、新生占比推算；手动锁定的新生按指定值计算</dd></div><div><dt>班量</dt><dd>人次除以计划班额，向上取整；最低成班人数单独校验</dd></div><div><dt>收入</dt><dd>按学费、折扣、结转比例和税务系数计算</dd></div><div><dt>调平</dt><dd>保留锁定项，不改学费与折扣，不强行放宽调整范围</dd></div><div><dt>状态</dt><dd>{fy(fiscalYear)} 试算草稿，不发布定版</dd></div></dl>{[...new Set(visibleInputs.notes.map(note => note.message).filter((message): message is string => typeof message === "string" && Boolean(message)))].map(message => <p key={message}>{message}</p>)}</details>
      </>}
    </main>
    <dialog ref={leaveDialog} className="ny-leave-dialog" aria-labelledby="ny-leave-title" onCancel={event => { event.preventDefault(); if (!busy) setPendingNavigation(""); }}><h2 id="ny-leave-title">修改未保存</h2><div><button autoFocus disabled={Boolean(busy)} onClick={() => setPendingNavigation("")}>继续编辑</button><button disabled={Boolean(busy)} onClick={() => { const destination = pendingNavigation; if (destination) { setReason(""); setEdits({}); setPendingNavigation(""); window.location.assign(destination); } }}>放弃修改并离开</button></div></dialog>
  </div>;
}
