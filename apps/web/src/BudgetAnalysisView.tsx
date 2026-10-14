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
import { BudgetScopeBar } from "./BudgetScopeBar";
import { filterLabel, hasFilter, normalizeFilter, type FilterValue } from "./filterSelection";
import { skuIdentityCells, skuIdentityHeaders } from "./SkuIdentity";
import { observeBudgetTableHeader } from "./budgetTableLayout";

type Props = {
  facts: BudgetFact[]; contextKey: string; fiscalYear: number; annualTarget?: number | null;
  initialFilters?: BudgetFilters; onFiltersChange?: (filters: BudgetFilters) => void; recordContent?: ReactNode; hideSummary?: boolean;
  controlledFilters?: BudgetFilters; hideFilters?: boolean; hideScopeSummary?: boolean;
};
type SavedView = { id: string; name: string; contextKey: string; fiscalYear: number; state: BudgetAnalysisState; includesFilters?: boolean };
type CellPosition = { row: string; column: string; metric: BudgetMetric };
type CellRange = { anchor: CellPosition; focus: CellPosition };
type BoardSelection = { path: Partial<Record<BudgetDimension, string>>; label: string };
const dimensions: BudgetDimension[] = ["track", "campus", "product", "grade", "subject", "season"];
const rowDimensions = dimensions.filter(dimension => dimension !== "season");
const metrics: BudgetMetric[] = ["revenue", "business", "old", "new", "total", "discount", "renewal"];
const ratioMetrics = new Set<BudgetMetric>(["discount", "renewal"]);
const moneyMetrics = new Set<BudgetMetric>(["revenue", "business"]);
const pivotPresets: { id: string; name: string; config: BudgetPivotConfig }[] = [
  { id: "campus", name: "校区结转收入", config: defaultAnalysisState().config },
  { id: "product", name: "产品结转收入", config: { rows: ["product"], column: "season", metrics: ["revenue"], sort: "revenue-desc" } },
  { id: "people", name: "招生人次", config: { rows: ["campus", "product"], column: "season", metrics: ["old", "new", "total"], sort: "revenue-desc" } },
  { id: "renewal", name: "续班率", config: { rows: ["track", "grade"], column: "season", metrics: ["renewal"], sort: "name-asc" } },
];
const viewSignature = (state: BudgetAnalysisState, includesFilters = true) => JSON.stringify({
  config: state.config, unit: state.unit, ...(includesFilters ? { filters: dimensions.map(dimension => state.filters[dimension]) } : {}),
});
const detailColumns: ExportColumn[] = [
  ...dimensions.map(key => ({ key, label: dimensionLabels[key] })),
  { key: "old", label: "老生人次", format: "count" }, { key: "new", label: "新生人次", format: "count" },
  { key: "total", label: "总人次", format: "count" },
  { key: "revenue", label: "预算结转收入（元）", format: "money" },
  { key: "business", label: "业务收入（含税，元）", format: "money" },
  { key: "listRevenue", label: "原价收入（元）", format: "money" },
  { key: "detailDiscount", label: "明细折扣", format: "rate" },
  { key: "renewal", label: "预算续班率", format: "rate" },
];
const matchesPath = (fact: BudgetFact, path: Partial<Record<BudgetDimension, string>>) => Object.entries(path).every(([key, value]) => fact[key as BudgetDimension] === value);
const localNumber = (value: number, digits = 2) => new Intl.NumberFormat("zh-CN", { maximumFractionDigits: digits }).format(value);
const validateViewState = (state: BudgetAnalysisState, facts: BudgetFact[], parentOwnsFilters = false) => {
  const validated = validateAnalysisState(state, facts);
  // Parent filters may name a disabled or newly added business with no calculated facts yet.
  // Absence of facts means an empty result, never permission to widen the parent's scope.
  return parentOwnsFilters ? { state: { ...validated.state, filters: { ...emptyBudgetFilters, ...state.filters } }, removedFilters: [] }
    : validated;
};
const stateFromLocation = (facts: BudgetFact[], initialFilters?: BudgetFilters, controlledFilters?: BudgetFilters) => {
  const search = new URLSearchParams(location.search);
  const parsed = readAnalysisState(location.search);
  if (controlledFilters !== undefined) parsed.filters = { ...controlledFilters };
  else if (initialFilters && ![...search.keys()].some(key => key.startsWith("ba") || key.startsWith("bf_"))) parsed.filters = { ...initialFilters };
  return validateViewState(parsed, facts, controlledFilters !== undefined).state;
};

/** Read-only analysis. View state is the only state this component writes. */
export function BudgetAnalysisView({ facts, contextKey, fiscalYear, annualTarget, initialFilters, onFiltersChange, recordContent, hideSummary = false, controlledFilters, hideFilters = false, hideScopeSummary = false }: Props) {
  const [internalState, setState] = useState<BudgetAnalysisState>(() => stateFromLocation(facts, initialFilters, controlledFilters));
  const controlled = controlledFilters !== undefined;
  // The parent owns filters in controlled mode; stale URL or local view state never wins a render.
  const state = useMemo(() => controlledFilters === undefined ? internalState
    : validateViewState({ ...internalState, filters: controlledFilters }, facts, true).state, [internalState, controlledFilters, facts]);
  const filterKey = JSON.stringify(dimensions.map(dimension => state.filters[dimension]));
  const [selectionScope, setSelectionScope] = useState(filterKey);
  const isRecordLocation = () => { const params = new URLSearchParams(location.search); return Boolean(recordContent) && (params.get("baRecord") === "1" || params.get("view") === "record"); };
  const [recordView, setRecordView] = useState(isRecordLocation);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [storedRange, setStoredRange] = useState<CellRange | null>(null);
  const [storedBoardSelection, setStoredBoardSelection] = useState<BoardSelection | null>(null);
  const range = selectionScope === filterKey ? storedRange : null;
  const boardSelection = selectionScope === filterKey ? storedBoardSelection : null;
  const setRange = (next: CellRange | null | ((current: CellRange | null) => CellRange | null)) => {
    setSelectionScope(filterKey);
    setStoredRange(current => typeof next === "function" ? next(selectionScope === filterKey ? current : null) : next);
  };
  const setBoardSelection = (next: BoardSelection | null) => { setSelectionScope(filterKey); setStoredBoardSelection(next); };
  const [detailSelection, setDetailSelection] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [viewEditor, setViewEditor] = useState<"save" | "manage" | null>(null);
  const [activeSavedId, setActiveSavedId] = useState(() => new URLSearchParams(location.search).get("baSavedView") ?? "");
  const [viewMoreOpen, setViewMoreOpen] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<Pick<SavedView, "id" | "name" | "contextKey"> | null>(null);
  const [saveIncludesFilters, setSaveIncludesFilters] = useState(true);
  const [detailPage, setDetailPage] = useState(0);
  const [showAllProducts, setShowAllProducts] = useState(false);
  const [savedViews, setSavedViews] = useState<SavedView[]>([]);
  const [saveName, setSaveName] = useState("");
  const [notice, setNotice] = useState("");
  const [exporting, setExporting] = useState(false);
  const [storageReady, setStorageReady] = useState(false);
  const drag = useRef<{ active: boolean; moved: boolean; wasSelected: boolean }>({ active: false, moved: false, wasSelected: false });
  const tableRef = useRef<HTMLTableElement>(null);
  const viewMoreRef = useRef<HTMLDivElement>(null);
  const viewMoreButtonRef = useRef<HTMLButtonElement>(null);
  const deleteTriggerRef = useRef<HTMLButtonElement | null>(null);
  const deleteCancelRef = useRef<HTMLButtonElement>(null);
  const pivotScroll = useRef({ top: 0, left: 0 });
  const pageScroll = useRef<Partial<Record<BudgetAnalysisState["view"], number>>>({});
  const lastContext = useRef(contextKey);
  const controlledFiltersRef = useRef(controlledFilters), filtersChangeRef = useRef(onFiltersChange);
  controlledFiltersRef.current = controlledFilters; filtersChangeRef.current = onFiltersChange;
  const storageKey = "budget-analysis-personal-views-v1";

  const clearSelection = () => { setRange(null); setBoardSelection(null); setDetailPage(0); setDetailSelection(false); };
  const commit = (next: BudgetAnalysisState, replace = false, selectedDetail = detailSelection, savedId = activeSavedId) => {
    const valid = validateViewState(next, facts, controlled);
    setState(valid.state); setRecordView(false);
    const selectionInvalidated = JSON.stringify(next.filters) !== JSON.stringify(state.filters) || JSON.stringify(next.config) !== JSON.stringify(state.config);
    if (selectionInvalidated) clearSelection();
    const url = new URL(location.href);
    const parentFilters = [...url.searchParams.entries()].filter(([key]) => key.startsWith("bf_"));
    url.search = writeAnalysisState(url.search, valid.state);
    // The parent persists bf_* (and its explicit-empty marker). This child only writes ba* view configuration.
    if (controlled) {
      [...url.searchParams.keys()].filter(key => key.startsWith("bf_")).forEach(key => url.searchParams.delete(key));
      parentFilters.forEach(([key, value]) => url.searchParams.append(key, value));
    }
    url.searchParams.delete("baRecord");
    if (savedId) url.searchParams.set("baSavedView", savedId); else url.searchParams.delete("baSavedView");
    if (url.searchParams.get("view") === "record") url.searchParams.delete("view");
    // History tracks the temporary detail mode, never copies of business data or cell values.
    history[replace ? "replaceState" : "pushState"]({ baDetailSelection: next.view === "detail" && selectedDetail && !selectionInvalidated }, "", url);
    if (JSON.stringify(state.filters) !== JSON.stringify(valid.state.filters)) filtersChangeRef.current?.(valid.state.filters);
    if (valid.removedFilters.length) setNotice(`当前数据未包含部分已选值，筛选已保留：${valid.removedFilters.join("、")}。可手动清除筛选。`);
  };
  useEffect(() => {
    const restore = () => {
      const restored = stateFromLocation(facts, initialFilters, controlledFiltersRef.current);
      const scopeChanged = JSON.stringify(restored.filters) !== JSON.stringify(state.filters);
      const configChanged = JSON.stringify(restored.config) !== JSON.stringify(state.config);
      setState(restored);
      if (scopeChanged || configChanged) clearSelection();
      if (configChanged) setCollapsed(new Set());
      setDetailSelection(restored.view === "detail" && history.state?.baDetailSelection === true && !scopeChanged && !configChanged && Boolean(range || boardSelection));
      setRecordView(isRecordLocation());
      setActiveSavedId(new URLSearchParams(location.search).get("baSavedView") ?? "");
      setPendingDelete(null); setViewMoreOpen(false); setViewEditor(null);
      // In controlled mode the parent handles the same event and restores filters once.
      if (!controlled) filtersChangeRef.current?.(restored.filters);
    };
    window.addEventListener("popstate", restore);
    return () => window.removeEventListener("popstate", restore);
  }, [facts, contextKey, controlled, state.filters, state.config, range, boardSelection]);
  useEffect(() => {
    if (lastContext.current === contextKey) return;
    lastContext.current = contextKey;
    const restored = stateFromLocation(facts, initialFilters, controlledFiltersRef.current);
    setState(restored); clearSelection(); setCollapsed(new Set());
    setDetailSelection(false); setRecordView(false); setNotice("");
    setActiveSavedId(new URLSearchParams(location.search).get("baSavedView") ?? "");
    setViewEditor(null); setPendingDelete(null); setViewMoreOpen(false);
  }, [contextKey, facts]);
  useEffect(() => { clearSelection(); }, [filterKey]);
  useEffect(() => {
    setStorageReady(false);
    try {
      const raw = localStorage.getItem(storageKey);
      const parsed: unknown = raw ? JSON.parse(raw) : [];
      const views: SavedView[] = [];
      if (Array.isArray(parsed)) for (const item of parsed) {
        if (typeof item !== "object" || item === null || typeof item.id !== "string" || typeof item.name !== "string" || typeof item.contextKey !== "string" || typeof item.fiscalYear !== "number" || !Number.isFinite(item.fiscalYear) || typeof item.state !== "object" || item.state === null) continue;
        // Local storage is not trusted input. Keep only dimension selections, including
        // legitimate disabled/new-business labels; those still belong to the parent.
        const filters = { ...emptyBudgetFilters };
        for (const dimension of dimensions) filters[dimension] = normalizeFilter(item.state.filters?.[dimension]);
        const clean = validateViewState({ ...defaultAnalysisState(), ...item.state, filters }, facts, true).state;
        views.push({ id: item.id, name: item.name, contextKey: item.contextKey, fiscalYear: item.fiscalYear, state: clean, includesFilters: item.includesFilters !== false });
      }
      setSavedViews(views);
    } catch { setSavedViews([]); setNotice("无法读取个人视图，仍可继续分析。"); }
    setStorageReady(true);
  }, [storageKey]);
  useEffect(() => {
    const outside = (event: PointerEvent) => {
      if (viewMoreRef.current && !viewMoreRef.current.contains(event.target as Node)) setViewMoreOpen(false);
    };
    window.addEventListener("pointerdown", outside);
    return () => window.removeEventListener("pointerdown", outside);
  }, []);
  useEffect(() => { if (pendingDelete) deleteCancelRef.current?.focus(); }, [pendingDelete]);
  useEffect(() => {
    const up = () => { drag.current.active = false; };
    window.addEventListener("pointerup", up); window.addEventListener("pointercancel", up);
    return () => { window.removeEventListener("pointerup", up); window.removeEventListener("pointercancel", up); };
  }, []);
  useEffect(() => {
    if (recordView || state.view !== "pivot" || !tableRef.current) return;
    const wrap = tableRef.current.closest(".ba-table-wrap");
    if (wrap) { wrap.scrollTop = pivotScroll.current.top; wrap.scrollLeft = pivotScroll.current.left; }
  }, [state.view, recordView]);

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
  const activeFilters = dimensions.filter(key => hasFilter(state.filters[key]));
  const format = (value: number | null, metric: BudgetMetric) => value === null || !Number.isFinite(value) ? "—" : ratioMetrics.has(metric) ? `${localNumber(value * 100)}%` : moneyMetrics.has(metric) ? localNumber(state.unit === "wan" ? value / 10000 : value) : localNumber(value, 8);
  const unitLabel = state.unit === "wan" ? "万元" : "元";
  const metricHeading = (metric: BudgetMetric) => `${metricLabels[metric]}${moneyMetrics.has(metric) ? ` · ${unitLabel}` : ""}`;
  const periodHeading = (label: string, metric: BudgetMetric) => state.config.column === "season" ? budgetPeriodLabel(label, metric) : label;
  const columnHeading = (label: string) => state.config.metrics.length === 1 ? periodHeading(label, state.config.metrics[0]) : label;
  const columnMetricHeading = (label: string, metric: BudgetMetric) => metric === "renewal" && state.config.column === "season" && state.config.metrics.length > 1 && seasons.includes(label) ? `${renewalStageLabel(label)}续班率` : metricHeading(metric);
  const setFilter = (dimension: BudgetDimension, value: FilterValue) => commit({ ...state, filters: { ...state.filters, [dimension]: value } });
  const setConfig = (config: BudgetPivotConfig, savedId = activeSavedId) => { setCollapsed(new Set()); commit({ ...state, config }, false, false, savedId); };

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
  const detailFacts = detailSelection ? selectedFacts : filtered;
  const detailTotals = useMemo(() => aggregateBudgetFacts(detailFacts), [detailFacts]);
  const selectedTotals = useMemo(() => aggregateBudgetFacts(selectedFacts), [selectedFacts]);
  const selectedStats = useMemo(() => range ? budgetSelectionStatistics(filtered, selectedCells, range.anchor.metric, state.config.column) : null,
    [filtered, selectedCells, range?.anchor.metric, state.config.column]);
  const selectionHasOverlap = selectedStats?.hasOverlap ?? false;
  const pageCount = Math.max(1, Math.ceil(detailFacts.length / 25));
  const safePage = selectionScope === filterKey ? Math.min(detailPage, pageCount - 1) : 0;
  const pagedFacts = detailFacts.slice(safePage * 25, (safePage + 1) * 25);
  const exportColumns = detailColumns;
  const detailTransfer = { columns: exportColumns, rows: detailFacts, filename: `FY${String(fiscalYear).slice(-2)}-${detailSelection ? "所选" : "预算"}明细` };

  function selectCell(row: BudgetPivotRow, columnKey: string, metric: BudgetMetric, extend = false) {
    const next = { row: row.key, column: columnKey, metric };
    setBoardSelection(null); setDetailPage(0);
    setRange(current => extend && current?.anchor.metric === metric ? { anchor: current.anchor, focus: next } : { anchor: next, focus: next });
  }
  function selectBoard(path: Partial<Record<BudgetDimension, string>>, label: string) {
    if (controlled) {
      commit({ ...state, view: "detail", filters: { ...state.filters, ...path } });
      setDetailSelection(false); setDetailPage(0);
      return;
    }
    setRange(null); setBoardSelection({ path, label });
    commit({ ...state, view: "detail" }, false, true); setDetailSelection(true); setDetailPage(0);
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
  async function doExport() {
    setExporting(true); setNotice("");
    try {
      const exported = pivotExport(pivot, state.config);
      await downloadBudgetWorkbook(exported.columns, exported.rows, `FY${String(fiscalYear).slice(-2)}预算透视`, "预算透视");
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
      // A local view identity is not portable. Shared links carry the visible layout and scope only.
      url.searchParams.delete("baSavedView");
      if (!navigator.clipboard?.writeText) throw new Error("此浏览器无法复制链接。可以直接复制地址栏。");
      await navigator.clipboard.writeText(url.toString()); setNotice(detailSelection ? "链接已复制，包含当前视图和筛选，不含临时选择。" : "链接已复制，包含当前视图和筛选。");
    } catch (error) { setNotice(error instanceof Error ? error.message : "链接复制失败。"); }
  }
  const storeViews = (next: SavedView[]) => {
    try { localStorage.setItem(storageKey, JSON.stringify(next)); setSavedViews(next); return true; }
    catch { setNotice("此浏览器无法保存个人视图。当前配置仍有效。"); return false; }
  };
  const personalViews = savedViews.filter(view => view.contextKey === contextKey);
  const activeSaved = personalViews.find(view => view.id === activeSavedId);
  const matchingPreset = pivotPresets.find(preset => JSON.stringify(preset.config) === JSON.stringify(state.config));
  const savedModified = Boolean(activeSaved && viewSignature(activeSaved.state, activeSaved.includesFilters !== false) !== viewSignature(state, activeSaved.includesFilters !== false));
  const viewValue = activeSaved ? `saved:${activeSaved.id}` : matchingPreset ? `preset:${matchingPreset.id}` : "custom";
  const rememberSavedView = (id: string) => {
    setActiveSavedId(id);
    const url = new URL(location.href);
    if (id) url.searchParams.set("baSavedView", id); else url.searchParams.delete("baSavedView");
    history.replaceState(history.state, "", url);
  };
  useEffect(() => {
    // Restore only the local identity. The URL remains the source of the visible
    // configuration and filters, including unsaved edits and history entries.
    const locationId = new URLSearchParams(location.search).get("baSavedView") ?? "";
    if (storageReady && activeSavedId && locationId === activeSavedId && !savedViews.some(view => view.id === activeSavedId && view.contextKey === contextKey)) rememberSavedView("");
  }, [storageReady, activeSavedId, savedViews, contextKey]);
  const saveView = (update = false) => {
    if (!storageReady) return;
    if (update && activeSaved) {
      if (!savedModified) return;
      const item = { ...activeSaved, state: { ...state, view: "pivot" as const } };
      if (storeViews(savedViews.map(view => view.id === item.id ? item : view))) setNotice(`已保存“${item.name}”，仅保存在本浏览器。`);
      return;
    }
    const name = saveName.trim(); if (!name) { setNotice("先给视图起个名字。"); return; }
    const existing = savedViews.find(view => view.name === name && view.contextKey === contextKey);
    if (existing) { setNotice("这个名称已存在，请换一个名称；更新原视图请使用“保存修改”。"); return; }
    const item = { id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`, name, contextKey, fiscalYear,
      state: { ...state, view: "pivot" as const }, includesFilters: saveIncludesFilters };
    if (storeViews([...savedViews, item])) { rememberSavedView(item.id); setSaveName(""); setViewEditor(null); setViewMoreOpen(false); setNotice("个人透视已保存，仅保存在本浏览器。"); }
  };
  const loadView = (view: SavedView) => {
    const validated = validateViewState({ ...view.state, view: "pivot", filters: view.includesFilters === false ? state.filters : view.state.filters }, facts, controlled);
    commit(validated.state, false, false, view.id); setCollapsed(new Set()); setActiveSavedId(view.id); setViewEditor(null); setViewMoreOpen(false); setPendingDelete(null);
    setNotice(validated.removedFilters.length ? `已载入“${view.name}”；当前数据未包含部分已选值，筛选已保留：${validated.removedFilters.join("、")}` : `已载入“${view.name}”。`);
  };
  const choosePivotView = (value: string) => {
    if (value.startsWith("saved:")) { const saved = personalViews.find(view => `saved:${view.id}` === value); if (saved) loadView(saved); }
    else { const preset = pivotPresets.find(item => `preset:${item.id}` === value); if (preset) { setActiveSavedId(""); setConfig(preset.config, ""); setViewEditor(null); setViewMoreOpen(false); setPendingDelete(null); } }
  };
  const openSaveView = () => {
    const base = activeSaved ? `${activeSaved.name.slice(0, 57)} 副本` : matchingPreset?.name ?? "自定义透视";
    let name = base, suffix = 2;
    while (personalViews.some(view => view.name === name)) { const ending = ` ${suffix++}`; name = `${base.slice(0, 60 - ending.length)}${ending}`; }
    setSaveName(name); setSaveIncludesFilters(activeSaved?.includesFilters !== false);
    setViewEditor("save"); setViewMoreOpen(false); setPendingDelete(null);
  };
  const requestDelete = (view: SavedView, trigger: HTMLButtonElement) => {
    deleteTriggerRef.current = trigger;
    setPendingDelete({ id: view.id, name: view.name, contextKey: view.contextKey }); setViewMoreOpen(false);
  };
  const restoreDeleteFocus = () => {
    const trigger = deleteTriggerRef.current;
    if (trigger?.isConnected) trigger.focus();
    else if (viewMoreButtonRef.current?.isConnected) viewMoreButtonRef.current.focus();
    else document.getElementById("ba-pivot-view")?.focus();
  };
  const cancelDelete = () => { setPendingDelete(null); restoreDeleteFocus(); };
  const confirmDelete = () => {
    if (!pendingDelete || pendingDelete.contextKey !== contextKey) { setPendingDelete(null); return; }
    const target = savedViews.find(view => view.id === pendingDelete.id && view.contextKey === pendingDelete.contextKey);
    if (!target) { setPendingDelete(null); return; }
    if (!storeViews(savedViews.filter(view => view !== target))) return;
    if (activeSavedId === target.id) rememberSavedView("");
    setPendingDelete(null); setNotice(`已删除“${target.name}”。当前透视和预算数据未改变。`);
    window.requestAnimationFrame(restoreDeleteFocus);
  };
  const rememberViewScroll = () => {
    if (recordView) return;
    pageScroll.current[state.view] = window.scrollY;
    if (state.view === "pivot" && tableRef.current) {
      const wrap = tableRef.current.closest(".ba-table-wrap");
      if (wrap) pivotScroll.current = { top: wrap.scrollTop, left: wrap.scrollLeft };
    }
  };
  const changeView = (view: BudgetAnalysisState["view"], selectedDetail = false) => {
    rememberViewScroll();
    setDetailSelection(selectedDetail); setViewEditor(null); setViewMoreOpen(false); setPendingDelete(null); commit({ ...state, view }, false, selectedDetail);
    window.requestAnimationFrame(() => window.scrollTo?.({ top: pageScroll.current[view] ?? pageScroll.current[state.view] ?? 0, behavior: "instant" }));
  };
  const openSelectedDetails = () => changeView("detail", true);
  const showRecord = () => { rememberViewScroll(); setViewEditor(null); setViewMoreOpen(false); setPendingDelete(null); const url = new URL(location.href); url.searchParams.set("baRecord", "1"); if (url.searchParams.get("view") === "record") url.searchParams.delete("view"); history.pushState({}, "", url); setRecordView(true); };
  const activeTab = recordView ? "record" : state.view;
  const onTabKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    const order = recordContent ? ["board", "pivot", "detail", "record"] : ["board", "pivot", "detail"];
    let index = order.indexOf(activeTab);
    if (event.key === "ArrowRight") index = (index + 1) % order.length;
    else if (event.key === "ArrowLeft") index = (index - 1 + order.length) % order.length;
    else if (event.key === "Home") index = 0;
    else if (event.key === "End") index = order.length - 1;
    else return;
    event.preventDefault(); const next = order[index];
    next === "record" ? showRecord() : changeView(next as BudgetAnalysisState["view"]);
    document.getElementById(`ba-tab-${next}`)?.focus();
  };

  return <section className="ba-root" aria-label="预算看板与透视">
    <div className="ba-view-tabs" role="tablist" aria-label="预算视图">
      <button role="tab" id="ba-tab-board" tabIndex={activeTab === "board" ? 0 : -1} onKeyDown={onTabKeyDown} aria-selected={!recordView && state.view === "board"} aria-controls="ba-view-panel" onClick={() => changeView("board")}>看板</button>
      <button role="tab" id="ba-tab-pivot" tabIndex={activeTab === "pivot" ? 0 : -1} onKeyDown={onTabKeyDown} aria-selected={!recordView && state.view === "pivot"} aria-controls="ba-view-panel" onClick={() => changeView("pivot")}>透视</button>
      <button role="tab" id="ba-tab-detail" tabIndex={activeTab === "detail" ? 0 : -1} onKeyDown={onTabKeyDown} aria-selected={!recordView && state.view === "detail"} aria-controls="ba-view-panel" onClick={() => changeView("detail")}>明细</button>
      {recordContent && <button role="tab" id="ba-tab-record" tabIndex={activeTab === "record" ? 0 : -1} onKeyDown={onTabKeyDown} aria-selected={recordView} aria-controls="ba-view-panel" onClick={showRecord}>定版记录</button>}
    </div>
    {recordView ? <div id="ba-view-panel" role="tabpanel" aria-labelledby="ba-tab-record">{recordContent}</div> : <div id="ba-view-panel" role="tabpanel" aria-labelledby={`ba-tab-${state.view}`}>
      {!hideFilters ? <BudgetScopeBar filters={state.filters} count={filtered.length}
        dimensions={dimensions.map(key => ({ key, label: dimensionLabels[key], options: [...new Set(facts.map(fact => fact[key]))].filter(Boolean).sort((a, b) => key === "season" ? seasons.indexOf(a) - seasons.indexOf(b) : key === "grade" ? compareBudgetGrades(a, b) : a.localeCompare(b, "zh-CN", { numeric: true })) }))}
        onChange={filters => commit({ ...state, filters })} onClear={() => commit({ ...state, filters: { ...emptyBudgetFilters } })}>
        <label>金额单位<select aria-label="金额单位" value={state.unit} onChange={event => commit({ ...state, unit: event.target.value as "yuan" | "wan" })}><option value="wan">万元</option><option value="yuan">元</option></select></label><button onClick={copyLink}>复制视图链接</button>
      </BudgetScopeBar> : <div className="ba-scope-row">{!hideScopeSummary && <div className="ba-filter-chips" aria-label="当前范围">{activeFilters.length ? activeFilters.map(key => <button className="ba-chip" key={key} onClick={() => setFilter(key, "")} aria-label={`清除${dimensionLabels[key]}${filterLabel(state.filters[key])}`}>{dimensionLabels[key]}：{filterLabel(state.filters[key])} <span aria-hidden="true">×</span></button>) : <span>全部预算</span>}<small>{filtered.length.toLocaleString("zh-CN")} 条明细</small>{activeFilters.length > 0 && <button className="ba-text-button" onClick={() => commit({ ...state, filters: { ...emptyBudgetFilters } })}>清除筛选</button>}</div>}<div className="ba-toolbar"><label>金额单位<select aria-label="金额单位" value={state.unit} onChange={event => commit({ ...state, unit: event.target.value as "yuan" | "wan" })}><option value="wan">万元</option><option value="yuan">元</option></select></label><button onClick={copyLink}>复制视图链接</button></div></div>}
      {notice && <div className="ba-notice" role="status"><span>{notice}</span><button aria-label="关闭提示" onClick={() => setNotice("")}>×</button></div>}
      {!filtered.length ? <div className="ba-empty"><h3>这个范围没有预算明细</h3><button onClick={() => commit({ ...state, filters: { ...emptyBudgetFilters } })}>清除筛选</button></div> : state.view === "board" ? <>
        {!hideSummary && <div className="ba-kpis">
          <article className="ba-kpi ba-kpi-primary"><span>{filterLabel(state.filters.season) || "全年"}结转收入 · {unitLabel}</span><strong>{format(totals.revenue, "revenue")}</strong>{activeFilters.length === 0 && annualTarget !== null && annualTarget !== undefined && Number.isFinite(annualTarget) && <small>年度目标 {format(annualTarget, "revenue")} · 差额 {format(totals.revenue === null ? null : totals.revenue - annualTarget, "revenue")}</small>}</article>
          <article className="ba-kpi"><span>老生人次</span><strong>{format(totals.old, "old")}</strong></article>
          <article className="ba-kpi"><span>新生人次</span><strong>{format(totals.new, "new")}</strong></article>
          <article className="ba-kpi"><span>预算总人次</span><strong>{format(totals.total, "total")}</strong></article>
          <article className="ba-kpi"><span>综合折扣</span><strong>{format(totals.discount, "discount")}</strong></article>
        </div>}
        <div className="ba-board-grid">
          <section className="ba-panel ba-quarter-panel"><header><h3>季度结转收入</h3><small>单位：{unitLabel}</small></header><div className="ba-quarter-chart" role="group" aria-label={quarterData.map(item => `${item.season} ${format(item.revenue, "revenue")} ${unitLabel}`).join("；")}>
            {quarterData.map(item => <div className="ba-quarter-item" key={item.season}><button className="ba-quarter-value" disabled={item.revenue === null} aria-label={`查看${item.season}结转收入${format(item.revenue, "revenue")}的明细`} onClick={() => selectBoard({ season: item.season }, `${item.season} · 结转收入`)}><strong>{format(item.revenue, "revenue")}</strong>{(item.revenue ?? 0) < 0 && <small>负值</small>}</button><div className="ba-quarter-track"><button className={`ba-quarter-bar${(item.revenue ?? 0) < 0 ? " ba-negative" : ""}`} style={{ height: `${Math.max(item.revenue ?? 0, 0) / maxQuarter * 100}%`, minHeight: 0 }} onClick={() => selectBoard({ season: item.season }, `${item.season} · 结转收入`)} aria-label={`查看${item.season}结转收入明细`} disabled={item.revenue === null} /></div><button className="ba-text-button" onClick={() => setFilter("season", item.season)}>{item.season}</button></div>)}
          </div></section>
          <section className="ba-panel ba-product-panel"><header><h3>产品结转收入</h3><small>单位：{unitLabel}</small></header><div className="ba-product-bars">{(showAllProducts ? products : products.slice(0, 6)).map(row => <button className="ba-product-item" key={row.key} onClick={() => setFilter("product", row.label)} aria-label={`筛选产品${row.label}`}><span>{row.label}</span><div className="ba-product-track"><span style={{ width: `${Math.max(row.total.revenue ?? 0, 0) / maxProduct * 100}%` }} className={(row.total.revenue ?? 0) < 0 ? "ba-negative" : ""} /></div><strong>{format(row.total.revenue, "revenue")}{(row.total.revenue ?? 0) < 0 && <small>负值</small>}</strong><small>{totals.revenue ? `${localNumber((row.total.revenue ?? 0) / totals.revenue * 100, 1)}%` : "—"}</small></button>)}</div>{products.length > 6 && <button className="ba-text-button" onClick={() => setShowAllProducts(current => !current)}>{showAllProducts ? "收起产品" : `全部 ${products.length} 个产品`}</button>}</section>
        </div>
        <BudgetPeopleBoard facts={filtered} unit={state.unit} fiscalYear={fiscalYear} onSelect={selectBoard} onFilterCampus={campus => setFilter("campus", campus)}
          onPivot={metric => { setCollapsed(new Set()); setActiveSavedId(""); commit({ ...state, view: "pivot", config: { rows: metric === "renewal" ? ["track", "grade"] : ["campus"], column: "season", metrics: metric === "renewal" ? ["renewal"] : ["total", "new", "old", "revenue"], sort: "name-asc" } }, false, false, ""); }} />
        <BudgetCompositionCharts facts={filtered} onSelect={selectBoard} />
      </> : state.view === "pivot" ? <section className="ba-panel ba-pivot-panel" aria-label="预算透视工作区">
        <h3 className="sr-only">预算透视</h3>
        <div className="ba-pivot-workbar">
          <div className="ba-view-picker"><label htmlFor="ba-pivot-view">透视视图</label><select id="ba-pivot-view" aria-label="透视视图" value={viewValue} onChange={event => choosePivotView(event.target.value)}>
            {!activeSaved && !matchingPreset && <option value="custom">自定义透视</option>}
            <optgroup label="系统预设">{pivotPresets.map(preset => <option key={preset.id} value={`preset:${preset.id}`}>{preset.name}</option>)}</optgroup>
            <optgroup label="我的透视">{personalViews.length ? personalViews.map(view => <option key={view.id} value={`saved:${view.id}`}>{view.name}</option>) : <option disabled value="empty">暂无保存的透视</option>}</optgroup>
          </select>{savedModified && <span className="ba-unsaved">未保存</span>}
            {activeSaved && <div className="ba-view-more" ref={viewMoreRef} onKeyDown={event => { if (event.key === "Escape") { event.preventDefault(); setViewMoreOpen(false); viewMoreButtonRef.current?.focus(); } }}>
              <button ref={viewMoreButtonRef} aria-label="视图更多操作" aria-expanded={viewMoreOpen} aria-controls="ba-view-more-actions" onClick={() => { setViewMoreOpen(current => !current); setPendingDelete(null); }}>更多 <span aria-hidden="true">⋯</span></button>
              {viewMoreOpen && <div className="ba-view-more-actions" id="ba-view-more-actions" role="group" aria-label="更多视图操作"><button onClick={openSaveView}>另存为</button><button className="ba-delete-action" onClick={event => requestDelete(activeSaved, event.currentTarget)}>删除</button></div>}
            </div>}
          </div>
          <div className="ba-view-actions"><button disabled={!storageReady || Boolean(activeSaved && !savedModified)} onClick={() => activeSaved ? saveView(true) : openSaveView()}>{activeSaved ? "保存修改" : "保存视图"}</button><button aria-expanded={viewEditor === "manage"} aria-controls="ba-view-manager" onClick={() => { setViewMoreOpen(false); setPendingDelete(null); setViewEditor(current => current === "manage" ? null : "manage"); }}>管理</button></div>
          <div className="ba-pivot-transfers"><button onClick={copyPivot}>复制透视</button><button disabled={exporting} onClick={() => doExport()}>{exporting ? "导出中…" : "导出透视"}</button></div>
        </div>
        {viewEditor === "save" && <form className="ba-view-editor" aria-label="保存透视" onSubmit={event => { event.preventDefault(); saveView(); }}>
          <label>视图名称<input aria-label="视图名称" autoFocus value={saveName} onChange={event => setSaveName(event.target.value)} maxLength={60} placeholder="例如：校区产品结转收入" /></label>
          <label className="ba-check-label"><input type="checkbox" checked={saveIncludesFilters} onChange={event => setSaveIncludesFilters(event.target.checked)} />包含当前筛选</label>
          <button type="submit" disabled={!saveName.trim()}>保存视图</button><button type="button" onClick={() => setViewEditor(null)}>取消</button><small>仅保存在本浏览器</small>
        </form>}
        {viewEditor === "manage" && <div className="ba-view-manager" id="ba-view-manager" aria-label="管理我的透视"><div><strong>我的透视</strong><small>仅保存在本浏览器</small><button className="ba-text-button" aria-label="关闭视图管理" onClick={() => { setViewEditor(null); setPendingDelete(null); }}>关闭</button></div>{personalViews.length ? <ul>{personalViews.map(view => <li key={view.id}><button className="ba-text-button" onClick={() => loadView(view)}>{view.name}</button><small>{view.includesFilters === false ? "仅布局" : "含筛选"}</small><button aria-label={`删除个人视图${view.name}`} onClick={event => requestDelete(view, event.currentTarget)}>删除</button></li>)}</ul> : <p>暂无保存的透视</p>}</div>}
        {pendingDelete && <section className="ba-view-delete" role="group" aria-label="删除视图确认" onKeyDown={event => { if (event.key === "Escape") { event.preventDefault(); cancelDelete(); } }}><div><strong>删除“{pendingDelete.name}”？</strong><small>仅删除本浏览器的视图配置，不影响预算数据。</small></div><div><button ref={deleteCancelRef} onClick={cancelDelete}>取消</button><button className="ba-delete-action" onClick={confirmDelete}>确认删除</button></div></section>}
        <div className="ba-pivot-configbar"><span className="ba-config-summary">行：{state.config.rows.map(key => dimensionLabels[key]).join(" / ")}<span>列：{state.config.column === "none" ? "汇总" : dimensionLabels[state.config.column]}</span><span>{state.config.metrics.map(key => metricLabels[key]).join("、")}</span></span><button aria-expanded={settingsOpen} aria-controls="ba-pivot-settings" onClick={() => setSettingsOpen(current => !current)}>透视设置</button>{state.config.rows.length > 1 && <><button onClick={() => { clearSelection(); setCollapsed(new Set()); }}>展开全部</button><button onClick={() => { clearSelection(); setCollapsed(new Set(pivot.rows.filter(row => !row.leaf).map(row => row.key))); }}>折叠全部</button></>}</div>
        {settingsOpen && <section className="ba-pivot-controls" id="ba-pivot-settings" aria-label="透视设置"><div><label>行维度</label><div className="ba-dimension-chips">{state.config.rows.map((dimension, index) => <span className="ba-chip" key={dimension}><span>{dimensionLabels[dimension]}</span><button aria-label={`向左移动${dimensionLabels[dimension]}`} disabled={index === 0} onClick={() => { const rows = [...state.config.rows]; [rows[index - 1], rows[index]] = [rows[index], rows[index - 1]]; setConfig({ ...state.config, rows }); }}>←</button><button aria-label={`向右移动${dimensionLabels[dimension]}`} disabled={index === state.config.rows.length - 1} onClick={() => { const rows = [...state.config.rows]; [rows[index], rows[index + 1]] = [rows[index + 1], rows[index]]; setConfig({ ...state.config, rows }); }}>→</button><button aria-label={`移除${dimensionLabels[dimension]}`} disabled={state.config.rows.length === 1} onClick={() => setConfig({ ...state.config, rows: state.config.rows.filter(key => key !== dimension) })}>×</button></span>)}<select aria-label="添加行维度" value="" onChange={event => { if (event.target.value) setConfig({ ...state.config, rows: [...state.config.rows, event.target.value as BudgetDimension] }); }}><option value="">＋ 添加维度</option>{rowDimensions.filter(dimension => !state.config.rows.includes(dimension) && state.config.column !== dimension).map(dimension => <option key={dimension} value={dimension}>{dimensionLabels[dimension]}</option>)}</select></div></div><label>列维度<select aria-label="列维度" value={state.config.column} onChange={event => setConfig({ ...state.config, column: event.target.value as BudgetPivotConfig["column"] })}><option value="none">无 · 分组汇总</option>{dimensions.filter(dimension => !state.config.rows.includes(dimension)).map(dimension => <option key={dimension} value={dimension}>{dimensionLabels[dimension]}</option>)}</select></label><label>排序<select aria-label="透视排序" value={state.config.sort} onChange={event => setConfig({ ...state.config, sort: event.target.value as BudgetPivotConfig["sort"] })}><option value="revenue-desc">结转收入从高到低</option><option value="name-asc">名称顺序</option></select></label><fieldset className="ba-metrics"><legend>指标</legend>{metrics.map(metric => <label key={metric}><input type="checkbox" checked={state.config.metrics.includes(metric)} disabled={state.config.metrics.length === 1 && state.config.metrics[0] === metric} onChange={event => setConfig({ ...state.config, metrics: event.target.checked ? [...state.config.metrics, metric] : state.config.metrics.filter(key => key !== metric) })} />{metricLabels[metric]}</label>)}</fieldset></section>}
        <div className="ba-table-wrap" ref={observeBudgetTableHeader} tabIndex={0} aria-label="预算透视表，可横向滚动" onScroll={event => { pivotScroll.current = { top: event.currentTarget.scrollTop, left: event.currentTarget.scrollLeft }; }}><table className="ba-table ba-pivot-table" ref={tableRef}><thead><tr><th scope="col" rowSpan={2}>{state.config.rows.map(key => dimensionLabels[key]).join(" / ")}</th>{pivotColumns.map(column => <th key={column.key} scope="colgroup" colSpan={state.config.metrics.length}>{columnHeading(column.label)}</th>)}</tr><tr>{pivotColumns.map(column => <Fragment key={column.key}>{state.config.metrics.map(metric => <th scope="col" key={metric}>{columnMetricHeading(column.label, metric)}</th>)}</Fragment>)}</tr></thead><tbody>{visibleRows.map((row, ri) => <tr className={row.leaf ? "ba-leaf-row" : "ba-subtotal-row"} key={row.key}><th scope="row"><span className="ba-row-name" style={{ paddingLeft: row.depth * 18 }}>{!row.leaf ? <button className="ba-tree-toggle" aria-label={`${collapsed.has(row.key) ? "展开" : "折叠"}${row.label}`} aria-expanded={!collapsed.has(row.key)} onClick={() => { clearSelection(); setCollapsed(current => { const next = new Set(current); next.has(row.key) ? next.delete(row.key) : next.add(row.key); return next; }); }}>{collapsed.has(row.key) ? "▸" : "▾"}</button> : <span className="ba-tree-spacer" />}<span>{row.label}</span>{!row.leaf && <small>小计</small>}</span></th>{pivotColumns.map((column, ci) => <Fragment key={column.key}>{state.config.metrics.map(metric => <td key={metric}>{numericCell(row, column.key, metric, ri, ci)}</td>)}</Fragment>)}</tr>)}</tbody><tfoot><tr><th scope="row">合计</th>{pivotColumns.map(column => <Fragment key={column.key}>{state.config.metrics.map(metric => <td key={metric}><button className="ba-cell ba-total-cell" aria-label={`${periodHeading(column.label, metric)}，${metricLabels[metric]}，合计`} onClick={() => { setRange(null); const path = column.value !== null && state.config.column !== "none" ? { [state.config.column]: column.value } : {}; setBoardSelection({ path, label: `${periodHeading(column.label, metric)} · ${metricLabels[metric]} · 合计` }); setDetailPage(0); }}>{format((column.key === "__total" ? pivot.totals : pivot.columnTotals[column.key])?.[metric] ?? null, metric)}</button></td>)}</Fragment>)}</tr></tfoot></table></div>
        {selected && <div className="ba-selection-stats" role="status">{selectedStats && range ? <><strong>{metricLabels[range.anchor.metric]}</strong><span>{selectedStats.count} 个有效数值</span>{!ratioMetrics.has(range.anchor.metric) && <><span>合计 {format(selectedStats.sum, range.anchor.metric)}</span>{selectedStats.average !== null && <span>平均 {format(selectedStats.average, range.anchor.metric)}</span>}</>}<span>最小 {format(selectedStats.min, range.anchor.metric)}</span><span>最大 {format(selectedStats.max, range.anchor.metric)}</span>{ratioMetrics.has(range.anchor.metric) && <span>{range.anchor.metric === "renewal" ? "预算续班率" : "综合折扣"} {range.anchor.metric === "renewal" ? format(selectedTotals.renewal, "renewal") : format(selectedTotals.discount, "discount")}</span>}{selectionHasOverlap && <span>去重明细 {selectedFacts.length} 条</span>}</> : <span>{selectionLabel}</span>}<div><button onClick={openSelectedDetails}>查看所选明细</button><button onClick={clearSelection}>清除选择</button></div></div>}
      </section> : <section className="ba-panel ba-detail" aria-label="构成明细"><header><div><h3>{detailSelection && selected ? "所选明细" : "预算明细"}</h3>{detailSelection && selected && <span className="ba-selection-label">{selectionLabel}</span>}<small>{detailFacts.length.toLocaleString("zh-CN")} 条 · 结转收入 {format(detailTotals.revenue, "revenue")} {unitLabel}</small></div>{detailSelection && selected && <div className="ba-toolbar"><button onClick={() => changeView("pivot")}>返回透视</button><button onClick={() => { setDetailSelection(false); setDetailPage(0); }}>查看当前范围全部明细</button></div>}</header>{detailFacts.length ? <><ActionTable keyboardNavigation className="ba-table ba-detail-table" scrollClassName="ba-table-wrap" tableLabel="预算明细" exportData={detailTransfer}><thead><tr>{skuIdentityHeaders.map(label => <th scope="col" key={label}>{label}</th>)}<th scope="col">季度</th>{["old", "new", "total", "revenue", "business", "renewal", "discount"].map(metric => <th scope="col" key={metric}>{metric === "discount" ? "明细折扣" : metricHeading(metric as BudgetMetric)}</th>)}</tr></thead><tbody>{pagedFacts.map(fact => <tr key={fact.id}>{skuIdentityCells(fact)}<td>{fact.season}</td>{(["old", "new", "total", "revenue", "business", "renewal"] as Exclude<BudgetMetric, "discount">[]).map(metric => <td key={metric}>{numericValue(fact[metric]) === null && fact[metric] !== null ? String(fact[metric]) || "—" : format(numericValue(fact[metric]), metric)}</td>)}<td>{numericValue(fact.detailDiscount) === null && fact.detailDiscount !== null && fact.detailDiscount !== undefined ? String(fact.detailDiscount) || "—" : format(numericValue(fact.detailDiscount), "discount")}</td></tr>)}</tbody></ActionTable><div className="ba-pagination"><button disabled={safePage === 0} onClick={() => setDetailPage(current => Math.max(0, current - 1))}>上一页</button><span>{safePage + 1} / {pageCount} · 每页 25 条</span><button disabled={safePage >= pageCount - 1} onClick={() => setDetailPage(current => current + 1)}>下一页</button></div></> : <div className="ba-empty">这个选择没有明细。<button onClick={() => setDetailSelection(false)}>查看当前范围全部明细</button></div>}</section>}
    </div>}
  </section>;
}
