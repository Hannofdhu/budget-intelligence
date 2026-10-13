import { ActionTable } from "./ActionTable";
import { useEffect, useMemo, useState } from "react";
import { identityHeaders, parseResponse, type ApiIdentity } from "./api";
import { FY27Header } from "./FY27Header";
import { budgetRowsInScope } from "./budgetBrowse";
import { BudgetAnalysisView } from "./BudgetAnalysisView";
import { normalizeHistoricalFacts, readAnalysisState, writeAnalysisState } from "./budgetAnalysis";
import { downloadBudgetWorkbook } from "./budgetExport";
import { historicalExportColumns } from "./historicalBudgetExport";

type Cell = { cell: string; value: number | string | null; formula: string | null };
type Line = {
  row: number; track: string; product: string; entry: string; grade: string; source_grade: string;
  subject: string; campus: string; season: string; prior_students: number | string; opening: number;
  minimum: number; annual: number; renewal: number | string | null; share: number | null;
  old: number; new: number; total: number; price: number; discount: number; tuition: number;
  list_revenue: number; business: number; recognition: number; current: number; revenue: number;
  carryout: number; excel_topup: boolean; sources: Record<string, Cell>; parameters: Record<string, Cell | null>;
};
type Snapshot = { mode: string; source_file: string; sha256: string; extracted_at: string; business_snapshot_date: string | null;
  scope_count: number; season_count: number; annual_revenue: string; entry_total: string; prior_students: string; opening_old: string; rows: Line[] };
type Dimension = "track" | "campus" | "product" | "grade" | "subject";
const dimensions: Record<Dimension, string> = { track: "赛道", campus: "校区", product: "产品", grade: "年级", subject: "科目" };
const seasons = ["暑假", "秋季", "寒假", "春季"];
const steps = ["目标与范围", "入口人数", "招生参数", "资源与财务", "预算概览"];
const rates = new Set(["renewal", "share", "discount", "recognition"]);
const fmt = (n: number, digits = 2) => n.toLocaleString("zh-CN", { maximumFractionDigits: digits });
const sum = (rows: Line[], key: "revenue" | "business" | "list_revenue" | "old" | "new" | "total" | "prior_students" | "opening") => rows.reduce((a, b) => a + (typeof b[key] === "number" ? b[key] as number : 0), 0);
const shown = (value: number | string | null, key: string) => value === null ? "未填写" : typeof value !== "number" ? value : rates.has(key) ? `${fmt(value * 100, 2)}%` : fmt(value);
const getStep = () => { const s = Number(new URLSearchParams(location.search).get("step")); return s >= 1 && s <= 5 ? s : 5; };
const emptyFilters = { season: "", track: "", campus: "", product: "", grade: "", subject: "" };

export function HistoricalBudgetPage() {
  const [data, setData] = useState<Snapshot | null>(null);
  const [error, setError] = useState("");
  const [step, setStep] = useState(getStep);
  const [filters, setFilters] = useState(() => readAnalysisState(location.search).filters);
  const [page, setPage] = useState(0);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    void (async () => {
      try {
        const identity = await parseResponse<ApiIdentity>(await fetch("/api/v1/local-trial/session", { signal: controller.signal }));
        const snapshot = await parseResponse<Snapshot>(await fetch("/api/v1/historical-budgets/fy27", { headers: identityHeaders(identity), signal: controller.signal }));
        if (!controller.signal.aborted) setData(snapshot);
      } catch (err) { if (!controller.signal.aborted) setError(err instanceof Error ? err.message : String(err)); }
    })();
    const onPop = () => { setStep(getStep()); setPage(0); setFilters(readAnalysisState(location.search).filters); };
    window.addEventListener("popstate", onPop);
    return () => { controller.abort(); window.removeEventListener("popstate", onPop); };
  }, []);
  function updateFilters(next: typeof filters) {
    setFilters(next); setPage(0);
    const search = "?" + writeAnalysisState(location.search, { ...readAnalysisState(location.search), filters: next });
    if (search !== location.search) history.pushState({}, "", location.pathname + search);
  }
  function changeStep(next: number) {
    setStep(next); setPage(0);
    const url = new URL(location.href); url.searchParams.set("step", String(next)); history.pushState({}, "", url);
  }
  const filtered = useMemo(() => budgetRowsInScope(data?.rows ?? [], step < 3 ? { ...filters, season: "" } : filters), [data, filters, step]);
  const scope = filtered.filter(row => row.season === "暑假");
  const entries = scope.filter(row => row.entry === "是");
  const detailRows = step === 1 ? scope : step === 2 ? entries : filtered;
  const facts = useMemo(() => normalizeHistoricalFacts(data?.rows ?? []), [data]);
  const activeFilters = Object.entries(filters).filter(([key, value]) => value && (key !== "season" || step >= 3));
  const pageRows = detailRows.slice(page * 25, (page + 1) * 25);
  const tableTransfer = { columns: historicalExportColumns(step), rows: detailRows, filename: `FY27-${steps[step - 1]}明细` };
  const stat = (label: string, value: string) => <div><span>{label}</span><strong>{value}</strong></div>;
  async function download() {
    setExporting(true); setExportError("");
    try { await downloadBudgetWorkbook(historicalExportColumns(step), detailRows, `FY27-${steps[step - 1]}明细`); }
    catch (err) { setExportError(err instanceof Error ? err.message : "导出失败，请重试"); }
    finally { setExporting(false); }
  }
  return <div className="budget-shell decision-shell history-budget">
    <a className="skip-link" href="#history-content">跳到主要内容</a>
    <FY27Header active="budget" onHome={() => changeStep(5)}>
      <div className="decision-version"><span className="status-dot is-published"/><span>已定版</span><small>只读回看</small></div>
    </FY27Header>
    <main className="decision-workspace" id="history-content">
      <nav className="decision-progress" aria-label="预算拆解导航">{steps.map((label, index) => <button key={label} className={`is-done ${step === index + 1 ? "is-active" : ""}`} aria-current={step === index + 1 ? "step" : undefined} onClick={() => changeStep(index + 1)}><span>{index + 1}</span><strong>{label}</strong><em className="decision-progress-short">{label.slice(0, 2)}</em></button>)}</nav>
      {error ? <section className="decision-question" role="alert"><p>{error}</p><button onClick={() => location.reload()}>重新读取</button></section> : !data ? <p role="status">正在读取 FY27 预算…</p> : <>
        <header className="history-heading"><div><p>FY27 学校版</p><h1>{steps[step - 1]}</h1></div></header>
        {step !== 5 && <section className="history-filters" aria-label="预算筛选">{Object.entries({ season: "季度", ...dimensions }).filter(([key]) => key !== "season" || step >= 3).map(([key, label]) => <label key={key}>{label}<select aria-label={label} value={filters[key as keyof typeof filters]} onChange={event => updateFilters({ ...filters, [key]: event.target.value })}>{["", ...(key === "season" ? seasons : [...new Set(data.rows.map(row => String(row[key as keyof Line])))].sort((a, b) => a.localeCompare(b, "zh-CN")))].map(value => <option key={value} value={value}>{value || "全部"}</option>)}</select></label>)}</section>}
        {step !== 5 && activeFilters.length > 0 && <div className="history-filter-note"><span>当前范围：{activeFilters.map(([, value]) => value).join(" / ")}</span><button onClick={() => updateFilters(emptyFilters)}>清除筛选</button></div>}
        {step === 1 && <section className="history-section"><h2>全年目标与定版收入 · 全部预算</h2><div className="history-table-wrap"><ActionTable><thead><tr><th>范围</th><th>收入目标（元）</th><th>定版收入（元）</th><th>差额（元）</th></tr></thead><tbody>{[["部门合计", 113000000], ["智慧伴学", 37000000], ["智慧空间", 76000000]].map(([track, target]) => { const actual = sum(data.rows.filter(row => track === "部门合计" || row.track === track), "revenue"); return <tr key={track}><th>{track}</th><td>{fmt(Number(target))}</td><td>{fmt(actual)}</td><td>{fmt(actual - Number(target))}</td></tr>; })}</tbody></ActionTable></div></section>}
        {step === 2 && <><div className="history-metrics">{stat("入口新生", fmt(sum(entries, "new"), 0))}{stat("七年级入口", fmt(sum(entries.filter(row => row.grade === "七年级"), "new"), 0))}{stat("八、九年级入口", fmt(sum(entries.filter(row => row.grade !== "七年级"), "new"), 0))}{stat("春续暑基数 → 暑假老生", `${fmt(sum(scope, "prior_students"), 0)} → ${fmt(sum(scope, "old"), 0)}`)}</div><section className="history-section"><h2>校区入口分配</h2><div className="history-campus-list">{[...new Set(entries.map(row => row.campus))].map(campus => <button key={campus} onClick={() => updateFilters({ ...filters, campus })}><span>{campus}</span><strong>{fmt(sum(entries.filter(row => row.campus === campus), "new"), 0)} 人次</strong></button>)}</div></section></>}

        {step === 5 ? <BudgetAnalysisView facts={facts} contextKey="historical:fy27" fiscalYear={2027}
          annualTarget={113000000} initialFilters={filters} onFiltersChange={setFilters} /> : <div>
        <section className="history-section budget-linked-details" id="history-details" aria-label="预算明细"><div className="history-section-heading"><div><h2>{step === 1 ? "业务范围" : step === 2 ? "入口明细" : step === 3 ? "招生拆解" : "财务明细"}</h2><p>{detailRows.length} 条{step >= 3 ? "季度" : "业务"}明细</p></div><div><button disabled={exporting} onClick={() => void download()}>{exporting ? "正在导出…" : "导出明细"}</button></div></div>
          {exportError && <p role="alert">{exportError}</p>}
          <div className="history-table-wrap" tabIndex={0} aria-label="预算明细表，可横向滚动"><ActionTable exportData={tableTransfer}><thead><tr><th>校区 / 赛道 / 产品</th><th>年级 / 科目</th>{step >= 3 && <th>季度</th>}{step === 1 ? <><th>入口</th><th>全年收入（元）</th></> : step === 2 ? <th>入口新生</th> : step === 4 ? <><th>最低班均</th><th>学费原价</th><th>折扣</th><th>当季确认比例</th><th>确认收入（元）</th></> : <>{step === 3 && <><th>续班率</th><th>新生占比</th></>}<th>老生</th><th>新生</th><th>总人次</th>{step === 5 && <th>确认收入（元）</th>}</>}</tr></thead><tbody>{pageRows.map(row => <tr key={`${row.row}-${row.season}`}><th><strong>{row.campus}</strong><small>{row.track} / {row.product}</small></th><td>{row.grade}<small>{row.subject}</small></td>{step >= 3 && <td>{row.season}</td>}{step === 1 ? <><td>{row.entry}</td><td>{fmt(row.annual)}</td></> : step === 2 ? <td>{fmt(row.new, 0)}</td> : step === 4 ? <><td>{shown(row.minimum, "minimum")}</td><td>{fmt(row.price)}</td><td>{shown(row.discount, "discount")}</td><td>{shown(row.recognition, "recognition")}</td><td>{fmt(row.revenue)}</td></> : <>{step === 3 && <><td>{shown(row.renewal, "renewal")}</td><td>{shown(row.share, "share")}</td></>}<td>{fmt(row.old, 0)}</td><td>{fmt(row.new, 0)}</td><td>{fmt(row.total, 0)}</td>{step === 5 && <td>{fmt(row.revenue)}</td>}</>}</tr>)}</tbody></ActionTable>{!detailRows.length && <p className="history-empty">当前筛选下没有明细。清除筛选可查看全部。</p>}</div>
          <div className="history-pagination"><button disabled={page === 0} onClick={() => setPage(page - 1)}>上一页</button><span>{detailRows.length ? page + 1 : 0} / {Math.ceil(detailRows.length / 25)} 页</span><button disabled={(page + 1) * 25 >= detailRows.length} onClick={() => setPage(page + 1)}>下一页</button></div>

        </section>
        </div>}
      </>}
    </main>
  </div>;
}
