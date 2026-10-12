import { ActionTable } from "./ActionTable";
import { Fragment, useEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import {
  aggregateBudgetFacts, buildBudgetPivot, defaultAnalysisState, dimensionLabels,
  emptyBudgetFilters, filterBudgetFacts, metricLabels, numericValue, pivotExport,
  readAnalysisState, seasons, validateAnalysisState, writeAnalysisState,
} from "./budgetAnalysis";
import type { BudgetAnalysisState, BudgetDimension, BudgetFact, BudgetFilters, BudgetMetric, BudgetPivotConfig, BudgetPivotRow } from "./budgetAnalysisTypes";
import { downloadBudgetWorkbook, type ExportColumn } from "./budgetExport";
import { budgetFactsForCells, budgetSelectionStatistics } from "./budgetAnalysisSelection";
import { BudgetPeopleBoard } from "./BudgetPeopleBoard";
import { BudgetCompositionCharts } from "./BudgetCompositionCharts";
import { budgetPeriodLabel, compareBudgetGrades, renewalStageLabel } from "./budgetDisplay";

type Props = {
  facts: BudgetFact[]; contextKey: string; fiscalYear: number; annualTarget?: number | null;
  initialFilters?: BudgetFilters; onFiltersChange?: (filters: BudgetFilters) => void; recordContent?: ReactNode; hideSummary?: boolean;
};
type SavedView = { id: string; name: string; contextKey: string; fiscalYear: number; state: BudgetAnalysisState };
type CellPosition = { row: string; column: string; metric: BudgetMetric };
type CellRange = { anchor: CellPosition; focus: CellPosition };
type BoardSelection = { path: Partial<Record<BudgetDimension, string>>; label: string };
const dimensions: BudgetDimension[] = ["track", "campus", "product", "grade", "subject", "season"];
const rowDimensions = dimensions.filter(dimension => dimension !== "season");
const metrics: BudgetMetric[] = ["revenue", "business", "old", "new", "total", "discount", "renewal"];
const ratioMetrics = new Set<BudgetMetric>(["discount", "renewal"]);
const moneyMetrics = new Set<BudgetMetric>(["revenue", "business"]);
const detailColumns: ExportColumn[] = [
  ...dimensions.map(key => ({ key, label: dimensionLabels[key] })),
  { key: "old", label: "老生人次", format: "count" }, { key: "new", label: "新生人次", format: "count" },
  { key: "total", label: "总人次", format: "count" },
  { key: "revenue", label: "预算确认收入（元）", format: "money" },
  { key: "business", label: "业务收入（含税，元）", format: "money" },
  { key: "listRevenue", label: "原价收入（元）", format: "money" },
  { key: "detailDiscount", label: "明细折扣", format: "rate" },
  { key: "renewal", label: "预算续班率", format: "rate" },
];
const matchesPath = (fact: BudgetFact, path: Partial<Record<BudgetDimension, string>>) => Object.entries(path).every(([key, value]) => fact[key as BudgetDimension] === value);
const localNumber = (value: number, digits = 2) => new Intl.NumberFormat("zh-CN", { maximumFractionDigits: digits }).format(value);
const stateFromLocation = (facts: BudgetFact[], initialFilters?: BudgetFilters) => {
  const search = new URLSearchParams(location.search);
  const parsed = readAnalysisState(location.search);
  if (initialFilters && ![...search.keys()].some(key => key.startsWith("ba") || key.startsWith("bf_"))) parsed.filters = { ...initialFilters };
  return validateAnalysisState(parsed, facts).state;
};

/** Read-only analysis. View state is the only state this component writes. */
export function BudgetAnalysisView({ facts, contextKey, fiscalYear, annualTarget, initialFilters, onFiltersChange, recordContent, hideSummary = false }: Props) {
  const [state, setState] = useState<BudgetAnalysisState>(() => stateFromLocation(facts, initialFilters));
  const isRecordLocation = () => { const params = new URLSearchParams(location.search); return Boolean(recordContent) && (params.get("baRecord") === "1" || params.get("view") === "record"); };
  const [recordView, setRecordView] = useState(isRecordLocation);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [range, setRange] = useState<CellRange | null>(null);
  const [boardSelection, setBoardSelection] = useState<BoardSelection | null>(null);
  const [detailOpen, setDetailOpen] = useState(() => state.view === "board");
  const [rawMode, setRawMode] = useState(() => new URLSearchParams(location.search).get("baTable") === "detail");
  const [detailPage, setDetailPage] = useState(0);
  const [showAllProducts, setShowAllProducts] = useState(false);
  const [savedViews, setSavedViews] = useState<SavedView[]>([]);
  const [saveName, setSaveName] = useState("");
  const [notice, setNotice] = useState("");
  const [exporting, setExporting] = useState(false);
  const [storageReady, setStorageReady] = useState(false);
  const drag = useRef<{ active: boolean; moved: boolean; wasSelected: boolean }>({ active: false, moved: false, wasSelected: false });
  const tableRef = useRef<HTMLTableElement>(null);
  const detailRef = useRef<HTMLElement>(null);
  const lastContext = useRef(contextKey);
  const storageKey = "budget-analysis-personal-views-v1";

  const clearSelection = () => { setRange(null); setBoardSelection(null); setDetailPage(0); };
  const commit = (next: BudgetAnalysisState, replace = false) => {
    const valid = validateAnalysisState(next, facts);
    setState(valid.state); setRecordView(false);
    if (next.view !== state.view) setDetailOpen(next.view === "board");
    if (next.unit === state.unit || JSON.stringify(next.filters) !== JSON.stringify(state.filters) || JSON.stringify(next.config) !== JSON.stringify(state.config) || next.view !== state.view) clearSelection();
    const url = new URL(location.href);
    url.search = writeAnalysisState(url.search, valid.state);
    url.searchParams.delete("baRecord");
    if (next.view !== state.view) url.searchParams.delete("baTable");
    if (url.searchParams.get("view") === "record") url.searchParams.delete("view");
    history[replace ? "replaceState" : "pushState"]({}, "", url);
    if (JSON.stringify(state.filters) !== JSON.stringify(valid.state.filters)) onFiltersChange?.(valid.state.filters);
    if (valid.removedFilters.length) setNotice(`已清除不可用筛选：${valid.removedFilters.join("、")}`);
  };
  useEffect(() => {
    const restore = () => {
      const restored = stateFromLocation(facts, initialFilters);
      setState(restored); clearSelection(); setCollapsed(new Set());
      setDetailOpen(restored.view === "board");
      setRawMode(new URLSearchParams(location.search).get("baTable") === "detail");
      setRecordView(isRecordLocation());
      onFiltersChange?.(restored.filters);
    };
    window.addEventListener("popstate", restore);
    return () => window.removeEventListener("popstate", restore);
  }, [facts, contextKey]);
  useEffect(() => {
    if (lastContext.current === contextKey) return;
    lastContext.current = contextKey;
    const restored = stateFromLocation(facts, initialFilters);
    setState(restored); clearSelection(); setCollapsed(new Set());
    setDetailOpen(restored.view === "board"); setRawMode(false); setRecordView(false); setNotice("");
  }, [contextKey, facts]);
  useEffect(() => {
    setStorageReady(false);
    try {
      const raw = localStorage.getItem(storageKey);
      const parsed: unknown = raw ? JSON.parse(raw) : [];
      setSavedViews(Array.isArray(parsed) ? parsed.filter((item): item is SavedView => typeof item === "object" && item !== null && typeof item.id === "string" && typeof item.name === "string" && typeof item.contextKey === "string" && typeof item.fiscalYear === "number" && typeof item.state === "object" && item.state !== null) : []);
    } catch { setSavedViews([]); setNotice("无法读取个人视图，仍可继续分析。"); }
    setStorageReady(true);
  }, [storageKey]);
  useEffect(() => {
    const up = () => { drag.current.active = false; };
    window.addEventListener("pointerup", up); window.addEventListener("pointercancel", up);
    return () => { window.removeEventListener("pointerup", up); window.removeEventListener("pointercancel", up); };
  }, []);
  useEffect(() => {
    if (!boardSelection || !detailOpen) return;
    const frame = window.requestAnimationFrame(() => detailRef.current?.scrollIntoView({ block: "start" }));
    return () => window.cancelAnimationFrame(frame);
  }, [boardSelection, detailOpen]);

  const filtered = useMemo(() => filterBudgetFacts(facts, state.filters), [facts, state.filters]);
  const totals = useMemo(() => aggregateBudgetFacts(filtered), [filtered]);
  const pivot = useMemo(() => buildBudgetPivot(filtered, state.config), [filtered, state.config]);
  const boardPivot = useMemo(() => buildBudgetPivot(filtered, { rows: ["campus"], column: "season", metrics: ["revenue"], sort: "revenue-desc" }), [filtered]);
  const products = useMemo(() => buildBudgetPivot(filtered, { rows: ["product"], column: "none", metrics: ["revenue"], sort: "revenue-desc" }).rows, [filtered]);
  const rowMap = useMemo(() => new Map(pivot.rows.map(row => [row.key, row])), [pivot]);
  const visibleRows = useMemo(() => pivot.rows.filter(row => {
    let parent = row.parentKey;
    while (parent) { if (collapsed.has(parent)) return false; parent = rowMap.get(parent)?.parentKey ?? null; }
    return true;
  }), [pivot, collapsed, rowMap]);
  const pivotColumns = useMemo(() => [...pivot.columns, ...(state.config.column === "none" ? [] : [{ key: "__total", label: "合计", value: null }])], [pivot, state.config.column]);
  const quarterData = seasons.map(season => {
    const column = boardPivot.columns.find(item => item.value === season);
    return { season, revenue: column ? boardPivot.columnTotals[column.key]?.revenue ?? null : null };
  });
  const maxQuarter = Math.max(1, ...quarterData.map(item => Math.abs(item.revenue ?? 0)));
  const maxProduct = Math.max(1, ...products.map(item => Math.abs(item.total.revenue ?? 0)));
  const activeFilters = dimensions.filter(key => state.filters[key]);
  const format = (value: number | null, metric: BudgetMetric) => value === null || !Number.isFinite(value) ? "—" : ratioMetrics.has(metric) ? `${localNumber(value * 100)}%` : moneyMetrics.has(metric) ? localNumber(state.unit === "wan" ? value / 10000 : value) : localNumber(value, 8);
  const unitLabel = state.unit === "wan" ? "万元" : "元";
  const metricHeading = (metric: BudgetMetric) => `${metricLabels[metric]}${moneyMetrics.has(metric) ? ` · ${unitLabel}` : ""}`;
  const periodHeading = (label: string, metric: BudgetMetric) => state.config.column === "season" ? budgetPeriodLabel(label, metric) : label;
  const columnHeading = (label: string) => state.config.metrics.length === 1 ? periodHeading(label, state.config.metrics[0]) : label;
  const columnMetricHeading = (label: string, metric: BudgetMetric) => metric === "renewal" && state.config.column === "season" && state.config.metrics.length > 1 && seasons.includes(label) ? `${renewalStageLabel(label)}续班率` : metricHeading(metric);
  const setFilter = (dimension: BudgetDimension, value: string) => commit({ ...state, filters: { ...state.filters, [dimension]: value } });
  const setConfig = (config: BudgetPivotConfig) => { setCollapsed(new Set()); commit({ ...state, config }); };

  const selectedCells = useMemo(() => {
    if (!range) return [];
    const aRow = visibleRows.findIndex(row => row.key === range.anchor.row);
    const bRow = visibleRows.findIndex(row => row.key === range.focus.row);
    const aCol = pivotColumns.findIndex(column => column.key === range.anchor.column);
    const bCol = pivotColumns.findIndex(column => column.key === range.focus.column);
    if ([aRow, bRow, aCol, bCol].some(index => index < 0)) return [];
    const cells: { row: BudgetPivotRow; column: typeof pivotColumns[number]; value: number | null }[] = [];
    for (let ri = Math.min(aRow, bRow); ri <= Math.max(aRow, bRow); ri++) for (let ci = Math.min(aCol, bCol); ci <= Math.max(aCol, bCol); ci++) {
      const row = visibleRows[ri], column = pivotColumns[ci];
      cells.push({ row, column, value: (column.key === "__total" ? row.total : row.cells[column.key])?.[range.anchor.metric] ?? null });
    }
    return cells;
  }, [range, visibleRows, pivotColumns]);
  const selectedFacts = useMemo(() => {
    if (boardSelection) return filtered.filter(fact => matchesPath(fact, boardSelection.path));
    if (!selectedCells.length) return filtered;
    return budgetFactsForCells(filtered, selectedCells, state.config.column);
  }, [boardSelection, filtered, selectedCells, state.config.column]);
  const selected = Boolean(boardSelection || selectedCells.length);
  const selectionLabel = boardSelection?.label ?? (selectedCells.length === 1 ? `${Object.entries(selectedCells[0].row.path).map(([key, value]) => `${dimensionLabels[key as BudgetDimension]}：${value}`).join(" / ")} · ${periodHeading(selectedCells[0].column.label, range!.anchor.metric)} · ${metricLabels[range!.anchor.metric]}` : selectedCells.length ? `${metricLabels[range!.anchor.metric]} · ${selectedCells.length} 个单元格` : "当前范围全部明细");
  const detailTotals = useMemo(() => aggregateBudgetFacts(selectedFacts), [selectedFacts]);
  const selectedStats = useMemo(() => range ? budgetSelectionStatistics(filtered, selectedCells, range.anchor.metric, state.config.column) : null,
    [filtered, selectedCells, range?.anchor.metric, state.config.column]);
  const selectionHasOverlap = selectedStats?.hasOverlap ?? false;
  const pageCount = Math.max(1, Math.ceil(selectedFacts.length / 25));
  const safePage = Math.min(detailPage, pageCount - 1);
  const pagedFacts = selectedFacts.slice(safePage * 25, (safePage + 1) * 25);
  const exportColumns = detailColumns;
  const detailTransfer = { columns: exportColumns, rows: selectedFacts, filename: `FY${fiscalYear}-预算明细` };

  function selectCell(row: BudgetPivotRow, columnKey: string, metric: BudgetMetric, extend = false) {
    const next = { row: row.key, column: columnKey, metric };
    setBoardSelection(null); setDetailOpen(true); setDetailPage(0);
    setRange(current => extend && current?.anchor.metric === metric ? { anchor: current.anchor, focus: next } : { anchor: next, focus: next });
  }
  function selectBoard(path: Partial<Record<BudgetDimension, string>>, label: string) {
    setRange(null); setBoardSelection(path && boardSelection && JSON.stringify(path) === JSON.stringify(boardSelection.path) ? null : { path, label });
    setDetailOpen(true); setDetailPage(0);
  }
  const cellIsSelected = (rowKey: string, columnKey: string, metric: BudgetMetric) => range?.anchor.metric === metric && selectedCells.some(cell => cell.row.key === rowKey && cell.column.key === columnKey);
  const numericCell = (row: BudgetPivotRow, columnKey: string, metric: BudgetMetric, ri: number, ci: number) => {
    const value = (columnKey === "__total" ? row.total : row.cells[columnKey])?.[metric] ?? null;
    return <button type="button" className={`ba-cell${cellIsSelected(row.key, columnKey, metric) ? " ba-cell-selected" : ""}`} aria-pressed={cellIsSelected(row.key, columnKey, metric)} aria-label={`${Object.entries(row.path).map(([key, member]) => `${dimensionLabels[key as BudgetDimension]}：${member}`).join("，")}，${periodHeading(pivotColumns[ci].label, metric)}，${metricLabels[metric]}，${format(value, metric)}`} data-row={ri} data-col={ci} data-metric={metric}
      onPointerDown={event => { if (event.button !== 0) return; drag.current = { active: true, moved: false, wasSelected: !event.shiftKey && range?.anchor.row === row.key && range.focus.row === row.key && range.anchor.column === columnKey && range.focus.column === columnKey && range.anchor.metric === metric }; selectCell(row, columnKey, metric, event.shiftKey); }}
      onPointerEnter={() => { if (!drag.current.active || range?.anchor.metric !== metric) return; drag.current.moved = true; selectCell(row, columnKey, metric, true); }}
      onClick={event => { if (drag.current.moved) { drag.current.moved = false; return; } if (event.detail !== 0 && drag.current.wasSelected) { clearSelection(); return; } if (event.detail === 0 || event.shiftKey) { if (!event.shiftKey && range?.anchor.row === row.key && range.focus.row === row.key && range.anchor.column === columnKey && range.focus.column === columnKey && range.anchor.metric === metric) clearSelection(); else selectCell(row, columnKey, metric, event.shiftKey); } }}
      onKeyDown={event => {
        const direction = { ArrowUp: [-1, 0], ArrowDown: [1, 0], ArrowLeft: [0, -1], ArrowRight: [0, 1] }[event.key];
        if (!direction) return; event.preventDefault();
        const nextRi = Math.max(0, Math.min(visibleRows.length - 1, ri + direction[0]));
        const nextCi = Math.max(0, Math.min(pivotColumns.length - 1, ci + direction[1]));
        selectCell(visibleRows[nextRi], pivotColumns[nextCi].key, metric, event.shiftKey);
        tableRef.current?.querySelector<HTMLButtonElement>(`button[data-row="${nextRi}"][data-col="${nextCi}"][data-metric="${metric}"]`)?.focus();
      }}>{format(value, metric)}</button>;
  };
  async function doExport(kind: "pivot" | "all" | "selection") {
    setExporting(true); setNotice("");
    try {
      if (kind === "pivot") {
        const exported = pivotExport(pivot, state.config);
        await downloadBudgetWorkbook(exported.columns, exported.rows, `FY${String(fiscalYear).slice(-2)}预算透视`, "预算透视");
      } else await downloadBudgetWorkbook(exportColumns, kind === "selection" ? selectedFacts : filtered, `FY${String(fiscalYear).slice(-2)}预算-${kind === "selection" ? "所选" : "当前范围"}明细`, "预算明细");
      setNotice("已导出。数值可直接计算。");
    } catch (error) { setNotice(error instanceof Error ? error.message : "导出失败，请重试。"); }
    finally { setExporting(false); }
  }
  async function copyPivot() {
    try {
      const exported = pivotExport(pivot, state.config);
      const text = [exported.columns.map(column => column.label), ...exported.rows.map(row => exported.columns.map(column => {
        const value = (row as Record<string, unknown>)[column.key];
        return value === null || value === undefined ? "" : String(value).replace(/[\t\r\n]/g, " ");
      }))].map(row => row.join("\t")).join("\n");
      if (!navigator.clipboard?.writeText) throw new Error("此浏览器无法复制。请使用导出透视。");
      await navigator.clipboard.writeText(text); setNotice("透视已复制，金额为元，可直接粘贴。");
    } catch (error) { setNotice(error instanceof Error ? error.message : "复制失败，请使用导出。"); }
  }
  async function copyLink() {
    try {
      const url = new URL(location.href); url.search = writeAnalysisState(url.search, state);
      if (!navigator.clipboard?.writeText) throw new Error("此浏览器无法复制链接。可以直接复制地址栏。");
      await navigator.clipboard.writeText(url.toString()); setNotice("链接已复制，包含当前视图和筛选。");
    } catch (error) { setNotice(error instanceof Error ? error.message : "链接复制失败。"); }
  }
  const storeViews = (next: SavedView[]) => {
    try { localStorage.setItem(storageKey, JSON.stringify(next)); setSavedViews(next); return true; }
    catch { setNotice("此浏览器无法保存个人视图。当前配置仍有效。"); return false; }
  };
  const saveView = () => {
    const name = saveName.trim(); if (!name) { setNotice("先给视图起个名字。"); return; }
    const existing = savedViews.find(view => view.name === name && view.contextKey === contextKey);
    const item = { id: existing?.id ?? `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`, name, contextKey, fiscalYear, state };
    if (storeViews([...savedViews.filter(view => !(view.name === name && view.contextKey === contextKey)), item])) { setSaveName(""); setNotice(existing ? "个人视图已更新，仅保存在本浏览器。" : "个人视图已保存，仅保存在本浏览器。"); }
  };
  const loadView = (view: SavedView) => {
    const validated = validateAnalysisState(view.state, facts);
    commit(validated.state); setCollapsed(new Set()); setRawMode(false);
    setNotice(validated.removedFilters.length ? `已载入“${view.name}”；清除不可用筛选：${validated.removedFilters.join("、")}` : `已载入“${view.name}”。`);
  };
  const changeView = (view: "board" | "pivot") => { setRawMode(false); commit({ ...state, view }); };
  const showRecord = () => { const url = new URL(location.href); url.searchParams.set("baRecord", "1"); if (url.searchParams.get("view") === "record") url.searchParams.delete("view"); history.pushState({}, "", url); setRecordView(true); };
  const changeTableMode = (detail: boolean) => {
    clearSelection(); setRawMode(detail); if (detail) setDetailOpen(true);
    const url = new URL(location.href); if (detail) url.searchParams.set("baTable", "detail"); else url.searchParams.delete("baTable");
    history.pushState({}, "", url);
  };
  const activeTab = recordView ? "record" : state.view;
  const onTabKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    const order = recordContent ? ["board", "pivot", "record"] : ["board", "pivot"];
    let index = order.indexOf(activeTab);
    if (event.key === "ArrowRight") index = (index + 1) % order.length;
    else if (event.key === "ArrowLeft") index = (index - 1 + order.length) % order.length;
    else if (event.key === "Home") index = 0;
    else if (event.key === "End") index = order.length - 1;
    else return;
    event.preventDefault(); const next = order[index];
    next === "record" ? showRecord() : changeView(next as "board" | "pivot");
    document.getElementById(`ba-tab-${next}`)?.focus();
  };

  return <section className="ba-root" aria-label="预算看板与透视">
    <div className="ba-view-tabs" role="tablist" aria-label="预算视图">
      <button role="tab" id="ba-tab-board" tabIndex={activeTab === "board" ? 0 : -1} onKeyDown={onTabKeyDown} aria-selected={!recordView && state.view === "board"} aria-controls="ba-view-panel" onClick={() => changeView("board")}>看板</button>
      <button role="tab" id="ba-tab-pivot" tabIndex={activeTab === "pivot" ? 0 : -1} onKeyDown={onTabKeyDown} aria-selected={!recordView && state.view === "pivot"} aria-controls="ba-view-panel" onClick={() => changeView("pivot")}>透视</button>
      {recordContent && <button role="tab" id="ba-tab-record" tabIndex={activeTab === "record" ? 0 : -1} onKeyDown={onTabKeyDown} aria-selected={recordView} aria-controls="ba-view-panel" onClick={showRecord}>定版记录</button>}
    </div>
    {recordView ? <div id="ba-view-panel" role="tabpanel" aria-labelledby="ba-tab-record">{recordContent}</div> : <div id="ba-view-panel" role="tabpanel" aria-labelledby={`ba-tab-${state.view}`}>
      <div className="ba-filterbar" aria-label="共同筛选">
        {dimensions.map(dimension => <label key={dimension}>{dimensionLabels[dimension]}<select aria-label={`筛选${dimensionLabels[dimension]}`} value={state.filters[dimension]} onChange={event => setFilter(dimension, event.target.value)}><option value="">全部</option>{[...new Set(facts.map(fact => fact[dimension]))].filter(Boolean).sort((a, b) => dimension === "season" ? seasons.indexOf(a) - seasons.indexOf(b) : dimension === "grade" ? compareBudgetGrades(a, b) : a.localeCompare(b, "zh-CN", { numeric: true })).map(value => <option key={value} value={value}>{value}</option>)}</select></label>)}
        <button className="ba-text-button" disabled={!activeFilters.length} onClick={() => commit({ ...state, filters: { ...emptyBudgetFilters } })}>重置筛选</button>
      </div>
      <div className="ba-scope-row"><div className="ba-filter-chips" aria-label="当前范围">{activeFilters.length ? activeFilters.map(key => <button className="ba-chip" key={key} onClick={() => setFilter(key, "")} aria-label={`清除${dimensionLabels[key]}${state.filters[key]}`}>{dimensionLabels[key]}：{state.filters[key]} <span aria-hidden="true">×</span></button>) : <span>全部预算</span>}<small>{filtered.length.toLocaleString("zh-CN")} 条明细</small></div><div className="ba-toolbar"><label>金额单位<select aria-label="金额单位" value={state.unit} onChange={event => commit({ ...state, unit: event.target.value as "yuan" | "wan" })}><option value="wan">万元</option><option value="yuan">元</option></select></label><button onClick={copyLink}>复制视图链接</button><button disabled={exporting || !filtered.length} onClick={() => doExport("all")}>{exporting ? "导出中…" : "导出当前范围明细"}</button></div></div>
      {notice && <div className="ba-notice" role="status"><span>{notice}</span><button aria-label="关闭提示" onClick={() => setNotice("")}>×</button></div>}
      {!filtered.length ? <div className="ba-empty"><h3>这个范围没有预算明细</h3><button onClick={() => commit({ ...state, filters: { ...emptyBudgetFilters } })}>清除筛选</button></div> : state.view === "board" ? <>
        {!hideSummary && <div className="ba-kpis">
          <article className="ba-kpi ba-kpi-primary"><span>{state.filters.season ? state.filters.season : "全年"}确认收入 · {unitLabel}</span><strong>{format(totals.revenue, "revenue")}</strong>{activeFilters.length === 0 && annualTarget !== null && annualTarget !== undefined && Number.isFinite(annualTarget) && <small>年度目标 {format(annualTarget, "revenue")} · 差额 {format(totals.revenue === null ? null : totals.revenue - annualTarget, "revenue")}</small>}</article>
          <article className="ba-kpi"><span>老生人次</span><strong>{format(totals.old, "old")}</strong></article>
          <article className="ba-kpi"><span>新生人次</span><strong>{format(totals.new, "new")}</strong></article>
          <article className="ba-kpi"><span>预算总人次</span><strong>{format(totals.total, "total")}</strong></article>
          <article className="ba-kpi"><span>综合折扣</span><strong>{format(totals.discount, "discount")}</strong></article>
        </div>}
        <div className="ba-board-grid">
          <section className="ba-panel ba-quarter-panel"><header><h3>季度收入</h3><small>单位：{unitLabel}</small></header><div className="ba-quarter-chart" role="group" aria-label={quarterData.map(item => `${item.season} ${format(item.revenue, "revenue")} ${unitLabel}`).join("；")}>
            {quarterData.map(item => <div className="ba-quarter-item" key={item.season}><button className="ba-quarter-value" disabled={item.revenue === null} aria-label={`查看${item.season}确认收入${format(item.revenue, "revenue")}的明细`} onClick={() => selectBoard({ season: item.season }, `${item.season} · 确认收入`)}><strong>{format(item.revenue, "revenue")}</strong>{(item.revenue ?? 0) < 0 && <small>负值</small>}</button><div className="ba-quarter-track"><button className={`ba-quarter-bar${(item.revenue ?? 0) < 0 ? " ba-negative" : ""}`} style={{ height: `${Math.max(item.revenue ?? 0, 0) / maxQuarter * 100}%`, minHeight: 0 }} onClick={() => selectBoard({ season: item.season }, `${item.season} · 确认收入`)} aria-label={`查看${item.season}收入明细`} disabled={item.revenue === null} /></div><button className="ba-text-button" onClick={() => setFilter("season", item.season)}>{item.season}</button></div>)}
          </div></section>
          <section className="ba-panel ba-product-panel"><header><h3>产品收入</h3><small>单位：{unitLabel}</small></header><div className="ba-product-bars">{(showAllProducts ? products : products.slice(0, 6)).map(row => <button className="ba-product-item" key={row.key} onClick={() => setFilter("product", row.label)} aria-label={`筛选产品${row.label}`}><span>{row.label}</span><div className="ba-product-track"><span style={{ width: `${Math.max(row.total.revenue ?? 0, 0) / maxProduct * 100}%` }} className={(row.total.revenue ?? 0) < 0 ? "ba-negative" : ""} /></div><strong>{format(row.total.revenue, "revenue")}{(row.total.revenue ?? 0) < 0 && <small>负值</small>}</strong><small>{totals.revenue ? `${localNumber((row.total.revenue ?? 0) / totals.revenue * 100, 1)}%` : "—"}</small></button>)}</div>{products.length > 6 && <button className="ba-text-button" onClick={() => setShowAllProducts(current => !current)}>{showAllProducts ? "收起产品" : `全部 ${products.length} 个产品`}</button>}</section>
        </div>
        <BudgetPeopleBoard facts={filtered} onSelect={selectBoard} onFilterCampus={campus => setFilter("campus", campus)}
          onPivot={metric => { setCollapsed(new Set()); setRawMode(false); commit({ ...state, view: "pivot", config: { rows: metric === "renewal" ? ["track", "grade"] : ["campus"], column: "season", metrics: metric === "renewal" ? ["renewal"] : ["old", "new", "total"], sort: "name-asc" } }); }} />
        <BudgetCompositionCharts facts={filtered} onSelect={selectBoard} />
        <section className="ba-panel ba-matrix"><header><h3>各校区预算</h3><div className="ba-toolbar"><small>确认收入 · {unitLabel}</small><button onClick={() => { setCollapsed(new Set()); setRawMode(false); commit({ ...state, view: "pivot", config: { rows: ["campus"], column: "season", metrics: ["revenue"], sort: "revenue-desc" } }); }}>用透视查看</button></div></header><div className="ba-table-wrap" tabIndex={0} aria-label="校区季度收入表，可横向滚动"><ActionTable className="ba-table"><thead><tr><th scope="col">校区</th>{seasons.map(season => <th key={season} scope="col">{season}</th>)}<th scope="col">合计</th></tr></thead><tbody>{boardPivot.rows.map(row => <tr key={row.key}><th scope="row"><button className="ba-text-button" onClick={() => setFilter("campus", row.label)}>{row.label}</button></th>{seasons.map(season => { const column = boardPivot.columns.find(item => item.value === season); const value = column ? row.cells[column.key]?.revenue ?? null : null; return <td key={season}><button aria-label={`${row.label}，${season}，确认收入，${format(value, "revenue")}`} className={`ba-cell${boardSelection?.path.campus === row.label && boardSelection.path.season === season ? " ba-cell-selected" : ""}`} onClick={() => selectBoard({ campus: row.label, season }, `${row.label} / ${season} · 确认收入`)}>{format(value, "revenue")}</button></td>; })}<td><button className="ba-cell ba-total-cell" aria-label={`${row.label}，合计确认收入，${format(row.total.revenue, "revenue")}`} onClick={() => selectBoard({ campus: row.label }, `${row.label} · 确认收入`)}>{format(row.total.revenue, "revenue")}</button></td></tr>)}</tbody><tfoot><tr><th scope="row">合计</th>{quarterData.map(item => <td key={item.season}>{format(item.revenue, "revenue")}</td>)}<td>{format(totals.revenue, "revenue")}</td></tr></tfoot></ActionTable></div></section>
        <div className="ba-toolbar ba-board-actions"><button onClick={() => { clearSelection(); setDetailOpen(current => !current); }}>{detailOpen && !selected ? "收起明细" : "查看全部明细"}</button></div>
      </> : <>
        <section className="ba-pivot-controls" aria-label="透视设置"><div><label>行维度</label><div className="ba-dimension-chips">{state.config.rows.map((dimension, index) => <span className="ba-chip" key={dimension}><span>{dimensionLabels[dimension]}</span><button aria-label={`向左移动${dimensionLabels[dimension]}`} disabled={index === 0} onClick={() => { const rows = [...state.config.rows]; [rows[index - 1], rows[index]] = [rows[index], rows[index - 1]]; setConfig({ ...state.config, rows }); }}>←</button><button aria-label={`向右移动${dimensionLabels[dimension]}`} disabled={index === state.config.rows.length - 1} onClick={() => { const rows = [...state.config.rows]; [rows[index], rows[index + 1]] = [rows[index + 1], rows[index]]; setConfig({ ...state.config, rows }); }}>→</button><button aria-label={`移除${dimensionLabels[dimension]}`} disabled={state.config.rows.length === 1} onClick={() => setConfig({ ...state.config, rows: state.config.rows.filter(key => key !== dimension) })}>×</button></span>)}<select aria-label="添加行维度" value="" onChange={event => { if (event.target.value) setConfig({ ...state.config, rows: [...state.config.rows, event.target.value as BudgetDimension] }); }}><option value="">＋ 添加维度</option>{rowDimensions.filter(dimension => !state.config.rows.includes(dimension) && state.config.column !== dimension).map(dimension => <option key={dimension} value={dimension}>{dimensionLabels[dimension]}</option>)}</select></div></div><label>列维度<select aria-label="列维度" value={state.config.column} onChange={event => setConfig({ ...state.config, column: event.target.value as BudgetPivotConfig["column"] })}><option value="none">无 · 分组汇总</option>{dimensions.filter(dimension => !state.config.rows.includes(dimension)).map(dimension => <option key={dimension} value={dimension}>{dimensionLabels[dimension]}</option>)}</select></label><label>排序<select aria-label="透视排序" value={state.config.sort} onChange={event => setConfig({ ...state.config, sort: event.target.value as BudgetPivotConfig["sort"] })}><option value="revenue-desc">收入从高到低</option><option value="name-asc">名称顺序</option></select></label><fieldset className="ba-metrics"><legend>指标</legend>{metrics.map(metric => <label key={metric}><input type="checkbox" checked={state.config.metrics.includes(metric)} disabled={state.config.metrics.length === 1 && state.config.metrics[0] === metric} onChange={event => setConfig({ ...state.config, metrics: event.target.checked ? [...state.config.metrics, metric] : state.config.metrics.filter(key => key !== metric) })} />{metricLabels[metric]}</label>)}</fieldset></section>
        <div className="ba-toolbar ba-pivot-actions"><div className="ba-presets"><span>预设</span><button onClick={() => setConfig({ ...defaultAnalysisState().config })}>校区收入</button><button onClick={() => setConfig({ rows: ["product"], column: "season", metrics: ["revenue"], sort: "revenue-desc" })}>产品收入</button><button onClick={() => setConfig({ rows: ["campus", "product"], column: "season", metrics: ["old", "new", "total"], sort: "revenue-desc" })}>招生人数</button><button onClick={() => setConfig({ rows: ["track", "grade"], column: "season", metrics: ["renewal"], sort: "name-asc" })}>续班率</button></div><div><button aria-pressed={!rawMode} onClick={() => changeTableMode(false)}>透视表</button><button aria-pressed={rawMode} onClick={() => changeTableMode(true)}>原始明细</button></div><button onClick={copyPivot}>复制透视</button><button disabled={exporting} onClick={() => doExport("pivot")}>导出透视</button></div>
        {!rawMode && <section className="ba-panel ba-pivot-panel"><header><h3>预算透视</h3><div className="ba-toolbar"><button disabled={!state.config.rows.length || state.config.rows.length === 1} onClick={() => { clearSelection(); setCollapsed(new Set()); }}>展开全部</button><button disabled={state.config.rows.length === 1} onClick={() => { clearSelection(); setCollapsed(new Set(pivot.rows.filter(row => !row.leaf).map(row => row.key))); }}>折叠全部</button></div></header><div className="ba-table-wrap" tabIndex={0} aria-label="预算透视表，可横向滚动"><ActionTable className="ba-table ba-pivot-table" ref={tableRef}><thead><tr><th scope="col" rowSpan={2}>{state.config.rows.map(key => dimensionLabels[key]).join(" / ")}</th>{pivotColumns.map(column => <th key={column.key} scope="colgroup" colSpan={state.config.metrics.length}>{columnHeading(column.label)}</th>)}</tr><tr>{pivotColumns.map(column => <Fragment key={column.key}>{state.config.metrics.map(metric => <th scope="col" key={metric}>{columnMetricHeading(column.label, metric)}</th>)}</Fragment>)}</tr></thead><tbody>{visibleRows.map((row, ri) => <tr className={row.leaf ? "ba-leaf-row" : "ba-subtotal-row"} key={row.key}><th scope="row"><span className="ba-row-name" style={{ paddingLeft: row.depth * 18 }}>{!row.leaf ? <button className="ba-tree-toggle" aria-label={`${collapsed.has(row.key) ? "展开" : "折叠"}${row.label}`} aria-expanded={!collapsed.has(row.key)} onClick={() => { clearSelection(); setCollapsed(current => { const next = new Set(current); next.has(row.key) ? next.delete(row.key) : next.add(row.key); return next; }); }}>{collapsed.has(row.key) ? "▸" : "▾"}</button> : <span className="ba-tree-spacer" />}<span>{row.label}</span>{!row.leaf && <small>小计</small>}</span></th>{pivotColumns.map((column, ci) => <Fragment key={column.key}>{state.config.metrics.map(metric => <td key={metric}>{numericCell(row, column.key, metric, ri, ci)}</td>)}</Fragment>)}</tr>)}</tbody><tfoot><tr><th scope="row">合计</th>{pivotColumns.map(column => <Fragment key={column.key}>{state.config.metrics.map(metric => <td key={metric}><button className="ba-cell ba-total-cell" aria-label={`${periodHeading(column.label, metric)}，${metricLabels[metric]}，合计`} onClick={() => { setRange(null); const path = column.value !== null && state.config.column !== "none" ? { [state.config.column]: column.value } : {}; setBoardSelection({ path, label: `${periodHeading(column.label, metric)} · ${metricLabels[metric]} · 合计` }); setDetailOpen(true); setDetailPage(0); }}>{format((column.key === "__total" ? pivot.totals : pivot.columnTotals[column.key])?.[metric] ?? null, metric)}</button></td>)}</Fragment>)}</tr></tfoot></ActionTable></div></section>}
        {selectedStats && range && <div className="ba-selection-stats" role="status"><strong>{metricLabels[range.anchor.metric]}</strong><span>{selectedStats.count} 个有效数值</span>{!ratioMetrics.has(range.anchor.metric) && <><span>合计 {format(selectedStats.sum, range.anchor.metric)}</span>{selectedStats.average !== null && <span>平均 {format(selectedStats.average, range.anchor.metric)}</span>}</>}<span>最小 {format(selectedStats.min, range.anchor.metric)}</span><span>最大 {format(selectedStats.max, range.anchor.metric)}</span>{ratioMetrics.has(range.anchor.metric) && <span>{range.anchor.metric === "renewal" ? "预算续班率" : "综合折扣"} {range.anchor.metric === "renewal" ? format(detailTotals.renewal, "renewal") : format(detailTotals.discount, "discount")}</span>}{selectionHasOverlap && <span>去重明细 {selectedFacts.length} 条</span>}<button onClick={clearSelection}>清除选择</button></div>}
        <details className="ba-named-views"><summary>我的视图 <small>仅本浏览器</small></summary><div className="ba-save-view"><label>视图名称<input value={saveName} onChange={event => setSaveName(event.target.value)} maxLength={60} placeholder="例如：校区产品收入" onKeyDown={event => { if (event.key === "Enter") saveView(); }} /></label><button disabled={!storageReady} onClick={saveView}>保存当前视图</button></div><ul>{savedViews.map(view => <li key={view.id}><button onClick={() => loadView(view)}>{view.name} <small>FY{String(view.fiscalYear).slice(-2)}{view.contextKey === contextKey ? "" : " · 其他预算"}</small></button><button aria-label={`删除个人视图${view.name}`} onClick={() => storeViews(savedViews.filter(item => item.id !== view.id))}>删除</button></li>)}</ul>{!savedViews.length && <p>暂无个人视图</p>}</details>
      </>}
      {(detailOpen || rawMode) && filtered.length > 0 && <section className="ba-panel ba-detail" ref={detailRef} aria-label="构成明细"><header><div><h3>{selected ? "所选明细" : "预算明细"}</h3><span className="ba-selection-label">{selectionLabel}</span><small>{selectedFacts.length.toLocaleString("zh-CN")} 条 · 确认收入 {format(detailTotals.revenue, "revenue")} {unitLabel}</small></div><div className="ba-toolbar">{selected && <button onClick={clearSelection}>清除选择</button>}<button disabled={exporting || !selectedFacts.length} onClick={() => doExport(selected ? "selection" : "all")}>{selected ? "导出所选明细" : "导出当前范围明细"}</button>{!rawMode && <button onClick={() => { clearSelection(); setDetailOpen(false); }}>收起</button>}</div></header>{selectedFacts.length ? <><div className="ba-table-wrap" tabIndex={0} aria-label="预算明细表，可横向滚动"><ActionTable className="ba-table ba-detail-table" exportData={detailTransfer}><thead><tr>{dimensions.map(dimension => <th scope="col" key={dimension}>{dimensionLabels[dimension]}</th>)}{["old", "new", "total", "revenue", "business", "renewal", "discount"].map(metric => <th scope="col" key={metric}>{metric === "discount" ? "明细折扣" : metricHeading(metric as BudgetMetric)}</th>)}</tr></thead><tbody>{pagedFacts.map(fact => <tr key={fact.id}>{dimensions.map(dimension => <td key={dimension}>{fact[dimension]}</td>)}{(["old", "new", "total", "revenue", "business", "renewal"] as Exclude<BudgetMetric, "discount">[]).map(metric => <td key={metric}>{numericValue(fact[metric]) === null && fact[metric] !== null ? String(fact[metric]) || "—" : format(numericValue(fact[metric]), metric)}</td>)}<td>{numericValue(fact.detailDiscount) === null && fact.detailDiscount !== null && fact.detailDiscount !== undefined ? String(fact.detailDiscount) || "—" : format(numericValue(fact.detailDiscount), "discount")}</td></tr>)}</tbody></ActionTable></div><div className="ba-pagination"><button disabled={safePage === 0} onClick={() => setDetailPage(current => Math.max(0, current - 1))}>上一页</button><span>{safePage + 1} / {pageCount} · 每页 25 条</span><button disabled={safePage >= pageCount - 1} onClick={() => setDetailPage(current => current + 1)}>下一页</button></div></> : <div className="ba-empty">这个单元格没有明细。</div>}</section>}
    </div>}
  </section>;
}
