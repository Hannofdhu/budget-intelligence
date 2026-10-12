import { useEffect, useMemo, useRef, useState, type MouseEvent, type ReactNode } from "react";
import { parseResponse, type ApiIdentity } from "./api";
import { FY27Header } from "./FY27Header";
import { downloadBudgetWorkbook, type ExportColumn } from "./budgetExport";
import { forecastNumber } from "./forecastApi";
import { allocateNextYearTrackTargets, applyNextYearEdits, getNextYearBudget, nextYearEditKey, nextYearRowTotal, nextYearSeasons,
  prepareNextYearBudget, previewNextYearBudget, reviseNextYearBudget,
  type NextYearCalculation, type NextYearEdits, type NextYearEnvelope, type NextYearInputs, type NextYearNumber, type NextYearRate, type NextYearRow } from "./nextYearApi";
import "./nextYear.css";

export const nextYearTasks = ["目标与范围", "入口人数", "招生参数", "资源与财务", "预算概览"] as const;
type Task = 1 | 2 | 3 | 4 | 5;
type Dimension = "season" | "track" | "campus" | "product" | "grade" | "subject";
type Filters = Record<Dimension, string>;
const dimensionLabels: Record<Dimension, string> = { season: "季度", track: "赛道", campus: "校区", product: "产品", grade: "年级", subject: "科目" };
const emptyFilters: Filters = { season: "", track: "", campus: "", product: "", grade: "", subject: "" };
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
  return { renewal: inputs.opening_sources.some(row => row.target_id !== null && targets.has(row.target_id)), share: cohort.length > 0 && !cohort.every(row => row.entry) };
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
];
export const nextYearExportRows = (rows: NextYearRow[]) => rows.map(row => ({ ...row,
  discount: row.discount_ppm === null ? null : row.discount_ppm / 1000000,
  renewal: row.renewal_ppm === null ? null : row.renewal_ppm / 1000000,
  share: row.share_ppm === null ? null : row.share_ppm / 1000000,
  class_size_status: nextYearClassStatus(row.class_size_status),
}));

export function NextYearTaskNavigation({ task, onChange }: { task: Task; onChange: (task: Task) => void }) {
  return <nav className="decision-progress ny-progress" aria-label="FY28 预算任务">{nextYearTasks.map((label, index) => <button key={label} className={task === index + 1 ? "is-active" : ""} aria-current={task === index + 1 ? "step" : undefined} onClick={() => onChange(index + 1 as Task)}><span>{index + 1}</span><strong>{label}</strong><em className="decision-progress-short">{label.slice(0, 2)}</em></button>)}</nav>;
}

function PagedTable<T>({ rows, headers, render, label, invalidPath, editPath }: { rows: T[]; headers: string[]; render: (row: T, index: number) => ReactNode; label: string; invalidPath?: string; editPath?: (row: T, index: number) => (string | number)[] }) {
  const [page, setPage] = useState(0), [query, setQuery] = useState("");
  const invalidIndex = invalidPath && editPath ? rows.findIndex((row, index) => { try { const path = JSON.parse(invalidPath); return editPath(row, index).every((value, n) => path[n] === value); } catch { return false; } }) : -1;
  useEffect(() => { if (invalidIndex >= 0) { setQuery(""); setPage(Math.floor(invalidIndex / 25)); } }, [invalidPath, invalidIndex]);
  const filtered = rows.map((row, index) => ({ row, index })).filter(({ row }) => !query || JSON.stringify(row).includes(query));
  const safePage = Math.min(page, Math.max(0, Math.ceil(filtered.length / 25) - 1));
  return <div className="ny-data"><div className="ny-table-tools"><span>{filtered.length}条</span><input aria-label={`筛选${label}`} value={query} placeholder="筛选明细" onChange={event => { setQuery(event.target.value); setPage(0); }}/></div>
    <div className="ny-table-wrap" tabIndex={0} aria-label={label}><table><thead><tr>{headers.map((header, index) => <th key={`${header}-${index}`}>{header}</th>)}</tr></thead><tbody>{filtered.slice(safePage * 25, safePage * 25 + 25).map(({ row, index }) => <tr key={index}>{render(row, index)}</tr>)}</tbody></table></div>
    {!filtered.length && <p className="ny-empty">当前范围没有明细</p>}<div className="ny-pagination"><button disabled={safePage === 0} onClick={() => setPage(safePage - 1)}>上一页</button><span>{filtered.length ? safePage + 1 : 0} / {Math.ceil(filtered.length / 25)}</span><button disabled={(safePage + 1) * 25 >= filtered.length} onClick={() => setPage(safePage + 1)}>下一页</button></div>
  </div>;
}

export function NextYearOverview({ calculation, filters, onSetTarget }: { calculation: NextYearCalculation; filters: Filters; onSetTarget?: () => void }) {
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
    <section className="ny-section"><h2>季度预算</h2><div className="ny-quarter-grid">{nextYearSeasons.map(season => {
      const selected = rows.filter(row => row.season === season), quarter = calculation.quarters.find(item => item.season === season);
      const value = narrowed ? nextYearRowTotal(selected, "revenue_yuan") : quarter?.revenue_yuan;
      const people = narrowed ? nextYearRowTotal(selected, "total_students") : quarter?.total_students;
      return <div key={season}><h3>{season}</h3><span>收入（万元）</span><strong>{money(value)}</strong><dl><div><dt>人次</dt><dd>{format(people)}</dd></div></dl></div>;
    })}</div></section>
    <section className="ny-section"><div className="ny-section-heading"><h2>维度预算</h2><div className="ny-segment" aria-label="汇总维度">{(["track", "campus"] as const).map(key => <button key={key} aria-pressed={dimension === key} onClick={() => setDimension(key)}>{dimensionLabels[key]}</button>)}</div></div>
      <div className="ny-table-wrap" tabIndex={0} aria-label={`${dimensionLabels[dimension]}预算对比`}><table><thead><tr><th>{dimensionLabels[dimension]}</th><th>预算收入（万元）</th>{showComparison && <><th>目标（万元）</th><th>差额（万元）</th></>}<th>总人次</th></tr></thead><tbody>{names.map(name => {
        const groupRows = rows.filter(row => row[dimension] === name), income = nextYearRowTotal(groupRows, "revenue_yuan");
        const goal = goals.find(item => (item as unknown as Record<string, unknown>)[dimension] === name);
        const targetValue = narrowedBeyondGroup ? null : goal?.target_revenue_yuan;
        const parsedTarget = forecastNumber(targetValue), gap = income === null || parsedTarget === null ? null : parsedTarget - income;
        return <tr key={name}><th>{name}</th><td>{money(income)}</td>{showComparison && <><td>{money(targetValue)}</td><td>{money(gap)}</td></>}<td>{format(nextYearRowTotal(groupRows, "total_students"))}</td></tr>;
      })}</tbody></table></div>{!rows.length && <p className="ny-empty">当前范围没有预算结果</p>}
    </section>
    <section className="ny-section"><h2>预算明细</h2><PagedTable rows={rows} label="FY28 预算明细" headers={["校区 / 业务", "季度", "老生", "新生", "总人次", "收入（元）", "开班"]} render={row => <><th>{textDimensions(row)}</th><td>{row.season}</td><td>{format(row.old_students)}</td><td>{format(row.new_students)}</td><td>{format(row.total_students)}</td><td>{format(row.revenue_yuan, 2)}</td><td>{nextYearClassStatus(row.class_size_status)}</td></>}/></section>
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
  const draft = data?.draft, source = draft?.source ?? data?.source;
  const inputChanged = Boolean(draft && inputs && (Object.keys(edits).length || JSON.stringify(inputs) !== JSON.stringify(draft.inputs)));
  const unsaved = inputChanged || Boolean(reason.trim()), canEdit = Boolean(identity?.roles.includes("DATA_MAINTAINER") || identity?.roles.includes("DEPARTMENT_BUDGET_OWNER"));
  const calculation = preview ?? draft?.calculation;
  function accept(next: NextYearEnvelope) { setData(next); setInputs(next.draft ? structuredClone(next.draft.inputs) : null); setEdits({}); setPreview(null); setReason(""); setFieldErrors({}); }
  async function load(signal?: AbortSignal) {
    setBusy("load"); setError("");
    try { const who = await parseResponse<ApiIdentity>(await fetch("/api/v1/local-trial/session", { signal })); const next = await getNextYearBudget(who, signal);
      if (!signal?.aborted) { setIdentity(who); accept(next); }
    } catch (failure) { if (!signal?.aborted) setError(failure instanceof Error ? failure.message : "读取失败"); }
    finally { if (!signal?.aborted) setBusy(""); }
  }
  useEffect(() => { const controller = new AbortController(); void load(controller.signal); return () => controller.abort(); }, []);
  useEffect(() => { const restore = () => setTask(readTask()); window.addEventListener("popstate", restore); return () => window.removeEventListener("popstate", restore); }, []);
  useEffect(() => { if (!unsaved) return; const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; }; window.addEventListener("beforeunload", warn); return () => window.removeEventListener("beforeunload", warn); }, [unsaved]);
  useEffect(() => { const dialog = leaveDialog.current; if (pendingNavigation && dialog && !dialog.open) dialog.showModal(); if (!pendingNavigation && dialog?.open) dialog.close(); }, [pendingNavigation]);
  useEffect(() => { const restore = () => setPendingNavigation(""); window.addEventListener("pageshow", restore); return () => window.removeEventListener("pageshow", restore); }, []);
  function changeTask(next: Task) { setTask(next); const url = new URL(location.href); url.searchParams.set("nyTask", String(next)); history.pushState({}, "", url); requestAnimationFrame(() => heading.current?.focus({ preventScroll: true })); }
  function beforeLeave(event: MouseEvent<HTMLAnchorElement>) { if (!unsaved || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.currentTarget.target === "_blank") return; event.preventDefault(); if (!busy) setPendingNavigation(event.currentTarget.href); }
  function edit(path: (string | number)[], value: string) { const key = nextYearEditKey(...path); setEdits(previous => ({ ...previous, [key]: value, ...(path[0] === "target" && !value.trim() ? Object.fromEntries(Object.keys(inputs?.track_targets ?? {}).map(track => [nextYearEditKey("track-target", track), ""])) : {}) })); setFieldErrors(previous => { const next = { ...previous }; delete next[key]; return next; }); setPreview(null); setNotice(""); }
  const invalidPath = (section: string) => Object.keys(fieldErrors).find(key => { try { return JSON.parse(key)[0] === section; } catch { return false; } });
  function field(path: (string | number)[], original: string | number | null, label: string, percent = false) {
    const key = nextYearEditKey(...path), message = fieldErrors[key];
    const errorId = `ny-error-${encodeURIComponent(key)}`;
    return <label className="ny-field"><span>{label}</span><input type="text" inputMode="decimal" value={edits[key] ?? (original === null ? "" : String(percent ? Number(original) / 10000 : original))} disabled={Boolean(busy) || !canEdit} aria-invalid={Boolean(message)} aria-describedby={message ? errorId : undefined} onChange={event => edit(path, event.target.value)}/>{message && <small id={errorId} role="alert">{message}</small>}</label>;
  }
  async function run(mode: "prepare" | "preview" | "save") {
    if (!identity || !canEdit || inFlight.current) return;
    if (mode === "prepare" && (!data?.source || unsaved)) return;
    const prepared = mode === "prepare" ? null : inputs ? applyNextYearEdits(inputs, edits) : null;
    if (mode !== "prepare" && (!draft || !prepared?.inputs)) {
      const errors = prepared?.errors ?? {}; setFieldErrors(errors);
      const first = Object.keys(errors)[0]; let section = ""; try { section = first ? JSON.parse(first)[0] : ""; } catch { /* malformed edit keys stay validation errors */ }
      const nextTask: Task = section === "entry" ? 2 : ["rates", "plan"].includes(section) ? 3 : ["finance", "class", "tax"].includes(section) ? 4 : 1;
      setFilters({ ...emptyFilters }); changeTask(nextTask); setError(first ? `${nextYearTasks[nextTask - 1]}：${errors[first]}` : "请补齐输入"); return;
    }
    if (mode === "save" && !reason.trim()) { setError("填写调整原因"); return; }
    inFlight.current = true; setBusy(mode); setError(""); setNotice("");
    try {
      if (mode === "prepare") { accept(await prepareNextYearBudget(identity, data!.source!.id, draft?.id ?? null)); changeTask(5); setNotice("FY28 草稿已生成"); }
      else if (mode === "preview") { const result = await previewNextYearBudget(identity, draft!.id, prepared!.inputs!); setPreview(result.calculation); changeTask(5); }
      else { accept(await reviseNextYearBudget(identity, draft!.id, prepared!.inputs!, reason.trim())); setNotice("已保存并重算"); }
    } catch (failure) { setError(failure instanceof Error ? failure.message : "操作未完成"); }
    finally { inFlight.current = false; setBusy(""); }
  }
  const combinations = inputs?.combinations ?? [], comboById = useMemo(() => new Map(combinations.map(row => [row.id, row])), [combinations]);
  const planRows = useMemo(() => combinations.flatMap(combination => nextYearSeasons.filter(season => !(season === "暑假" && combination.entry) && calculation?.rows.some(row => row.combination_id === combination.id && row.season === season && row.planned_new_applied)).map(season => ({ combination, season, students: inputs?.plans.find(row => row.combination_id === combination.id && row.season === season)?.students }))), [combinations, inputs?.plans, calculation]);
  function distributeTargets() {
    if (!inputs || !draft || !canEdit || busy) return;
    try { const allocated = allocateNextYearTrackTargets(edits[nextYearEditKey("target")] ?? inputs.target_revenue_yuan ?? "", Object.fromEntries(draft.calculation.tracks.map(row => [row.track, String(Math.max(0, forecastNumber(row.revenue_yuan) ?? 0))])));
      setEdits(previous => ({ ...previous, ...Object.fromEntries(Object.entries(allocated).map(([name, value]) => [nextYearEditKey("track-target", name), value])) }));
      setFieldErrors({}); setError(""); setPreview(null); setNotice("");
    } catch (failure) { setError(failure instanceof Error ? failure.message : "目标分配未完成"); }
  }
  async function download() {
    if (!calculation || exporting) return; setExporting(true); setError("");
    try { const rows = calculation.rows.filter(row => matches(row, filters)); await downloadBudgetWorkbook(nextYearExportColumns, nextYearExportRows(rows), preview ? "FY28-未保存预览明细" : `FY28-N${draft?.sequence}-预算明细`); }
    catch (failure) { setError(failure instanceof Error ? failure.message : "导出失败"); } finally { setExporting(false); }
  }
  const blockers = calculation?.issues.filter(issue => issue.severity === "BLOCKING") ?? [], warnings = calculation?.issues.filter(issue => issue.severity === "WARNING") ?? [];
  const belowClassSize = calculation?.rows.filter(row => row.class_size_status === "BELOW_MINIMUM_CLASS_SIZE" || row.class_size_status === "UNDER_MINIMUM").length ?? 0;
  return <div className="budget-shell decision-shell next-year-shell"><a className="skip-link" href="#next-year-content">跳到主要内容</a>
    <FY27Header active="next-year" onBeforeLeave={beforeLeave}><span className="ny-year-context">FY28 学校版</span><div className="decision-version"><span className="status-dot"/><span>预算草稿</span>{draft && <small>N{draft.sequence}</small>}</div></FY27Header>
    <main className="decision-workspace" id="next-year-content">
      {draft && <NextYearTaskNavigation task={task} onChange={changeTask}/>}
      <header className="ny-heading"><div><h1 ref={heading} tabIndex={-1}>FY28 预算</h1>{source && <span className="ny-source-label">FY27 预测 P{source.sequence}</span>}</div>{draft && <div className="ny-heading-actions"><button disabled={Boolean(busy) || exporting || !calculation?.rows.length} onClick={() => void download()}>{exporting ? "正在导出…" : "导出明细"}</button></div>}</header>
      {error && <div className="ny-message" role="alert"><span>{error}</span>{!data && <button disabled={Boolean(busy)} onClick={() => void load()}>重新读取</button>}<button aria-label="关闭错误" onClick={() => setError("")}>关闭</button></div>}
      {notice && <div className="ny-message is-success" role="status"><span>{notice}</span><button aria-label="关闭提示" onClick={() => setNotice("")}>关闭</button></div>}
      {busy === "load" && <div className="ny-loading" aria-busy="true"><div/><div/><p role="status">正在读取 FY28 预算…</p></div>}
      {!busy && data && !draft && <section className="ny-start">{data.source ? <><div className="ny-metrics"><div className="ny-metric-primary"><span>FY27 预计收入（万元）</span><strong>{money(data.source.forecast_revenue)}</strong></div><div><span>FY27 预计人次</span><strong>{format(data.source.forecast_total)}</strong></div></div><button className="decision-primary" disabled={!canEdit} onClick={() => void run("prepare")}>生成 FY28 草稿</button></> : <div className="ny-empty"><h2>先确认 FY27 预测</h2><a className="decision-primary" href="/budget/forecast?archive=fy27">前往达成预测</a></div>}</section>}
      {busy === "prepare" && !draft && <p role="status">正在生成 FY28 草稿…</p>}
      {draft && inputs && calculation && <>
        {data?.source_changed && <div className="ny-message" role="status"><span>FY27 已确认预测更新至 P{data.source?.sequence}</span><button disabled={Boolean(busy) || unsaved || !canEdit} onClick={() => void run("prepare")}>用新预测生成草稿</button></div>}
        <div className="ny-task-heading"><h2>{nextYearTasks[task - 1]}</h2>{preview ? <span className="ny-edit-state" role="status">未保存预览</span> : inputChanged ? <span className="ny-edit-state" role="status">待重算 · 原结果 N{draft.sequence}</span> : <span>已保存 N{draft.sequence}</span>}</div>
        <section className="ny-filters" aria-label="FY28 预算筛选">{(Object.entries(dimensionLabels) as [Dimension, string][]).map(([key, label]) => <label key={key}>{label}<select value={filters[key]} onChange={event => setFilters({ ...filters, [key]: event.target.value })}><option value="">全部</option>{(key === "season" ? [...nextYearSeasons] : [...new Set(combinations.map(row => row[key]))].sort((a, b) => dimensionSort(key, a, b))).map(value => <option key={value}>{value}</option>)}</select></label>)}{Object.values(filters).some(Boolean) && <button onClick={() => setFilters({ ...emptyFilters })}>清除筛选</button>}</section>
        {task === 1 && <section className="ny-section">{source && <div className="ny-opening"><span>FY27 预测收入（万元） <strong>{money(source.forecast_revenue)}</strong></span></div>}<div className="ny-form-grid">{field(["target"], inputs.target_revenue_yuan, "全年经营目标（元）")}{Object.entries(inputs.track_targets).map(([track, amount]) => <div key={track}>{field(["track-target", track], amount, `${track}目标（元）`)}</div>)}</div><button disabled={Boolean(busy) || !canEdit || forecastNumber(edits[nextYearEditKey("target")] ?? inputs.target_revenue_yuan) === null} onClick={distributeTargets}>按预算收入分配赛道目标</button><h3>业务范围</h3><PagedTable rows={combinations.filter(row => matches(row, filters))} label="FY28 业务范围" headers={["校区 / 业务", "入口", "参与预算"]} render={row => <><th>{textDimensions(row)}</th><td>{row.entry ? "是" : "否"}</td><td><input type="checkbox" aria-label={`${textDimensions(row)}参与预算`} checked={row.enabled} disabled={Boolean(busy) || !canEdit} onChange={event => { setInputs({ ...inputs, combinations: inputs.combinations.map(item => item.id === row.id ? { ...item, enabled: event.target.checked } : item) }); setPreview(null); setNotice(""); }}/></td></>}/></section>}
        {task === 2 && <section className="ny-section"><div className="ny-form-grid">{field(["entry"], inputs.entry_total, "年度入口人次")}</div><h3>校区入口</h3><PagedTable rows={calculation.entry_allocations.filter(row => !filters.campus || row.campus === filters.campus)} label="FY28 校区入口" headers={["校区", "分配人次", "参考入口人次"]} render={row => <><th>{row.campus}</th><td>{format(row.students)}</td><td>{format(row.source_weight)}</td></>}/><div className="ny-opening"><span>春季预测可续人次 <strong>{format(calculation.opening.eligible_students)}</strong></span><span>暑假期初老生 <strong>{format(calculation.opening.summer_old_students)}</strong></span></div></section>}
        {task === 3 && <section className="ny-section"><h3>续班率与新生占比</h3><PagedTable rows={inputs.rates.filter(row => matches(row, filters))} label="FY28 招生参数" invalidPath={invalidPath("rates")} editPath={row => ["rates", row.id]} headers={["校区 / 业务", "季度", "续班率（%）", "新生占比（%）"]} render={row => { const usage = nextYearRateUsage(inputs, row); return <><th>{textDimensions(row)}</th><td>{row.season}</td><td>{usage.renewal ? field(["rates", row.id, "renewal_ppm"], row.renewal_ppm, `${row.season} ${textDimensions(row)}续班率（%）`, true) : "—"}</td><td>{usage.share ? field(["rates", row.id, "share_ppm"], row.share_ppm, `${row.season} ${textDimensions(row)}新生占比（%）`, true) : "入口分配"}</td></>; }}/><details className="ny-plans" open={invalidPath("plan") ? true : undefined}><summary>计划新生 · 零老生范围</summary><PagedTable rows={planRows.filter(row => matches({ ...row.combination, season: row.season }, filters))} label="FY28 计划新生" invalidPath={invalidPath("plan")} editPath={row => ["plan", row.combination.id, row.season]} headers={["校区 / 业务", "季度", "计划新生人次"]} render={row => <><th>{textDimensions(row.combination)}</th><td>{row.season}</td><td>{field(["plan", row.combination.id, row.season], row.students ?? "", `${row.season} ${textDimensions(row.combination)}计划新生人次`)}</td></>}/></details></section>}
        {task === 4 && <section className="ny-section"><div className="ny-form-grid">{field(["tax"], inputs.tax_factor, "预算含税转换系数")}</div><h3>价格与折扣</h3><PagedTable rows={inputs.finance.filter(row => matches({ ...comboById.get(row.combination_id), season: row.season }, filters))} label="FY28 财务参数" invalidPath={invalidPath("finance")} editPath={row => ["finance", row.id]} headers={["校区 / 业务", "季度", "学费（元）", "折扣（%）", "当季结转（%）"]} render={row => { const title = textDimensions(comboById.get(row.combination_id) ?? {}); return <><th>{title}</th><td>{row.season}</td><td>{field(["finance", row.id, "price_yuan"], row.price_yuan, `${row.season} ${title}学费（元）`)}</td><td>{field(["finance", row.id, "discount_ppm"], row.discount_ppm, `${row.season} ${title}折扣（%）`, true)}</td><td>{field(["finance", row.id, "recognition_ppm"], row.recognition_ppm, `${row.season} ${title}当季结转（%）`, true)}</td></>; }}/><h3>班均</h3><PagedTable rows={inputs.class_sizes.map((row, index) => ({ ...row, index })).filter(row => matches(row, filters))} label="FY28 班均参数" invalidPath={invalidPath("class")} editPath={row => ["class", row.index]} headers={["赛道 / 产品", "最低班均"]} render={row => <><th>{row.track} / {row.product}</th><td>{field(["class", row.index], row.minimum, `${row.track} ${row.product}最低班均`)}</td></>}/><div className="ny-opening"><span>春季预测转入暑假（元） <strong>{format(calculation.opening.carryover_yuan, 2)}</strong></span></div></section>}
        {task === 5 && <NextYearOverview calculation={calculation} filters={filters} onSetTarget={() => changeTask(1)}/>}
        {(blockers.length > 0 || warnings.length > 0) && <details className="ny-issues"><summary>{blockers.length ? `需处理 ${blockers.length}项` : `提醒 ${warnings.length}项`}{belowClassSize > 0 && <span>开班未达标 {belowClassSize}项</span>}</summary><ul>{calculation.issues.map((issue, index) => <li key={`${issue.code}-${index}`}><strong>{issue.severity === "BLOCKING" ? "需处理" : "提醒"}</strong><div>{issue.combination_id && <small>{textDimensions(comboById.get(issue.combination_id) ?? {})}</small>}<span>{issue.season} {issue.message}</span></div></li>)}</ul></details>}
        <footer className="ny-save-bar"><div>{unsaved ? <span className="ny-edit-state">未保存</span> : <span>预算草稿 N{draft.sequence}</span>}{inputChanged && <button disabled={Boolean(busy)} onClick={() => { setInputs(structuredClone(draft.inputs)); setEdits({}); setReason(""); setPreview(null); setFieldErrors({}); setError(""); }}>放弃修改</button>}</div>{canEdit && <div className="ny-save-actions"><label className="ny-field"><span>调整原因</span><input value={reason} maxLength={1000} disabled={Boolean(busy)} onChange={event => setReason(event.target.value)}/></label><button disabled={Boolean(busy) || !inputChanged} onClick={() => void run("preview")}>{busy === "preview" ? "正在预览…" : "预览"}</button><button className="decision-primary" disabled={Boolean(busy) || !inputChanged || !reason.trim()} onClick={() => void run("save")}>{busy === "save" ? "正在保存…" : "保存并重算"}</button></div>}</footer>
        <details className="ny-how"><summary>拆解怎么算</summary><dl><div><dt>入口</dt><dd>按参考人次分配，合计保持不变</dd></div><div><dt>期初老生</dt><dd>FY27 春季预测升年级续入暑假</dd></div><div><dt>后续季度</dt><dd>按续班率、新生占比推算；零老生范围使用计划新生</dd></div><div><dt>收入</dt><dd>按学费、折扣、结转比例和税务系数计算</dd></div><div><dt>状态</dt><dd>FY28 试算草稿，不发布定版</dd></div></dl>{[...new Set(inputs.notes.map(note => note.message).filter((message): message is string => typeof message === "string" && Boolean(message)))].map(message => <p key={message}>{message}</p>)}</details>
      </>}
    </main>
    <dialog ref={leaveDialog} className="ny-leave-dialog" aria-labelledby="ny-leave-title" onCancel={event => { event.preventDefault(); if (!busy) setPendingNavigation(""); }}><h2 id="ny-leave-title">修改未保存</h2><div><button autoFocus disabled={Boolean(busy)} onClick={() => setPendingNavigation("")}>继续编辑</button><button disabled={Boolean(busy)} onClick={() => { const destination = pendingNavigation; if (destination) { setReason(""); setEdits({}); setPendingNavigation(""); window.location.assign(destination); } }}>放弃修改并离开</button></div></dialog>
  </div>;
}
