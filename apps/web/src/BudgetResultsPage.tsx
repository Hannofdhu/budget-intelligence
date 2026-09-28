import { useEffect, useMemo, useRef, useState } from "react";
import {
  fetchAnnualBudget,
  fetchAuditEvents,
  fetchRevenueResultVersion,
  fetchRevision,
  fetchTargetClosureVersion,
  parseResponse,
  type ApiIdentity,
  type RevenueResultVersion,
  type SeasonRevenueResult,
  type TargetClosureVersion,
} from "./api";
import { PublishedBudgetRecord } from "./PublishedBudgetRecord";

type DimensionKey = "track" | "campus" | "product" | "grade" | "subject";
type Filters = { fiscalSeason: string; track: string; campus: string; product: string; grade: string; subject: string };
type GroupRow = { name: string; revenue: number; listRevenue: number; businessRevenue: number; students: number; rows: number };

const dimensionLabels: Record<DimensionKey, string> = { track: "赛道", campus: "校区", product: "产品", grade: "年级", subject: "科目" };
const emptyFilters: Filters = { fiscalSeason: "", track: "", campus: "", product: "", grade: "", subject: "" };

function money(value: number, compact = false): string {
  if (compact) return `${new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 1 }).format(value / 10_000)} 万`;
  return new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 }).format(value);
}

function percent(value: number | null): string {
  return value === null || !Number.isFinite(value) ? "—" : `${value.toFixed(1)}%`;
}

function values(rows: SeasonRevenueResult[], key: keyof SeasonRevenueResult): string[] {
  return [...new Set(rows.map(row => String(row[key] ?? "")).filter(Boolean))].sort((a, b) => a.localeCompare(b, "zh-CN", { numeric: true }));
}

function sum(rows: SeasonRevenueResult[], key: "quarter_recognized_revenue_yuan" | "list_price_revenue_yuan" | "business_quarter_revenue_yuan"): number {
  return rows.reduce((total, row) => total + Number(row[key] ?? 0), 0);
}

function downloadCsv(rows: GroupRow[], dimension: DimensionKey, fiscalYear: number) {
  const header = [dimensionLabels[dimension], "收入（元）", "原价收入（元）", "业务收入（元）", "人次", "收入占比"];
  const total = rows.reduce((value, row) => value + row.revenue, 0);
  const body = rows.map(row => [row.name, row.revenue.toFixed(2), row.listRevenue.toFixed(2), row.businessRevenue.toFixed(2), String(row.students), total ? `${(row.revenue / total * 100).toFixed(2)}%` : "0%"]);
  const csv = [header, ...body].map(line => line.map(cell => `"${String(cell).replaceAll('"', '""')}"`).join(",")).join("\n");
  const link = document.createElement("a");
  link.href = URL.createObjectURL(new Blob(["\ufeff", csv], { type: "text/csv;charset=utf-8" }));
  link.download = `FY${String(fiscalYear).slice(-2)}预算结果-${dimensionLabels[dimension]}.csv`;
  link.click();
  URL.revokeObjectURL(link.href);
}

export function BudgetResultsPage({ revisionId, active }: { revisionId: string; active: boolean }) {
  const [identity, setIdentity] = useState<ApiIdentity | null>(null);
  const [budgetContext, setBudgetContext] = useState<{ annualBudgetId: string; fiscalYear: number } | null>(null);
  const [view, setView] = useState(() => new URLSearchParams(location.search).get("view") === "record" ? "record" : "results");
  const [result, setResult] = useState<RevenueResultVersion | null>(null);
  const [closure, setClosure] = useState<TargetClosureVersion | null>(null);
  const [dimension, setDimension] = useState<DimensionKey>("track");
  const [filters, setFilters] = useState<Filters>(emptyFilters);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const pageTitleRef = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    const controller = new AbortController();
    void (async () => {
      setLoading(true); setError("");
      try {
        const who = await parseResponse<ApiIdentity>(await fetch("/api/v1/local-trial/session", { signal: controller.signal }));
        setIdentity(who);
        const activeId = revisionId;
        if (!activeId) throw new Error("没有找到要查看的预算版本。");
        const revision = await fetchRevision(activeId, who, controller.signal);
        const budget = await fetchAnnualBudget(revision.annual_budget_id, who, controller.signal);
        if (activeId !== budget.current_published_revision_id) {
          const publication = await fetchAuditEvents(budget.annual_budget_id, { revisionId: activeId, eventType: "PUBLISHED" }, who, controller.signal);
          if (publication.items.length === 0) throw new Error("这版预算尚未定版，请返回年度预算继续审阅。");
        }
        setBudgetContext({ annualBudgetId: budget.annual_budget_id, fiscalYear: budget.fiscal_year });
        const revenueId = revision.component_manifest.revenue_result_version_id;
        if (typeof revenueId !== "string") throw new Error("该预算版本还没有收入计算结果。");
        const nextResult = await fetchRevenueResultVersion(revenueId, who, controller.signal);
        setResult(nextResult);
        const closureId = revision.component_manifest.target_closure_result_version_id;
        if (typeof closureId === "string") setClosure(await fetchTargetClosureVersion(closureId, who, controller.signal));
      } catch (nextError) {
        if (!controller.signal.aborted) setError(nextError instanceof Error ? nextError.message : String(nextError));
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    })();
    return () => controller.abort();
  }, []);

  useEffect(() => {
    const restoreView = () => setView(new URLSearchParams(location.search).get("view") === "record" ? "record" : "results");
    window.addEventListener("popstate", restoreView);
    return () => window.removeEventListener("popstate", restoreView);
  }, []);
  const changeView = (next: string) => {
    if (next === view) return;
    const url = new URL(location.href);
    if (next === "record") url.searchParams.set("view", "record"); else url.searchParams.delete("view");
    history.pushState({}, "", url);
    setView(next);
  };

  useEffect(() => {
    if (!active || !result || loading) return;
    const frame = window.requestAnimationFrame(() => {
      window.scrollTo({ top: 0, behavior: "auto" });
      pageTitleRef.current?.focus({ preventScroll: true });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [active, loading, result]);

  const rows = result?.items ?? [];
  const filtered = useMemo(() => rows.filter(row =>
    (!filters.fiscalSeason || String(row.fiscal_season) === filters.fiscalSeason)
    && (!filters.track || row.track === filters.track)
    && (!filters.campus || row.campus === filters.campus)
    && (!filters.product || row.product === filters.product)
    && (!filters.grade || row.grade === filters.grade)
    && (!filters.subject || row.subject === filters.subject)
  ), [rows, filters]);
  const totalRevenue = sum(filtered, "quarter_recognized_revenue_yuan");
  const listRevenue = sum(filtered, "list_price_revenue_yuan");
  const businessRevenue = sum(filtered, "business_quarter_revenue_yuan");
  const totalStudents = filtered.reduce((total, row) => total + Number(row.integer_total_students ?? 0), 0);
  const compositeDiscount = listRevenue ? businessRevenue / listRevenue * 100 : null;
  const annualTarget = Number(closure?.items.find(item => item.scope_level === "DEPARTMENT")?.target_revenue_yuan ?? 0);
  const targetVariance = !Object.values(filters).some(Boolean) && annualTarget ? totalRevenue - annualTarget : null;

  const quarters = useMemo(() => values(rows, "fiscal_season").map(season => {
    const quarterRows = filtered.filter(row => String(row.fiscal_season) === season);
    return { season, name: rows.find(row => String(row.fiscal_season) === season)?.quarter_name ?? season, revenue: sum(quarterRows, "quarter_recognized_revenue_yuan") };
  }), [rows, filtered]);
  const maxQuarter = Math.max(...quarters.map(item => item.revenue), 1);

  const grouped = useMemo(() => {
    const map = new Map<string, GroupRow>();
    for (const row of filtered) {
      const name = String(row[dimension] ?? "未分类");
      const current = map.get(name) ?? { name, revenue: 0, listRevenue: 0, businessRevenue: 0, students: 0, rows: 0 };
      current.revenue += Number(row.quarter_recognized_revenue_yuan ?? 0);
      current.listRevenue += Number(row.list_price_revenue_yuan ?? 0);
      current.businessRevenue += Number(row.business_quarter_revenue_yuan ?? 0);
      current.students += Number(row.integer_total_students ?? 0);
      current.rows += 1;
      map.set(name, current);
    }
    return [...map.values()].sort((a, b) => b.revenue - a.revenue || a.name.localeCompare(b.name, "zh-CN"));
  }, [filtered, dimension]);

  const setFilter = (key: keyof Filters, value: string) => setFilters(current => ({ ...current, [key]: value }));
  const filterFields: Array<{ key: keyof Filters; label: string; source: keyof SeasonRevenueResult }> = [
    { key: "fiscalSeason", label: "季度", source: "fiscal_season" }, { key: "track", label: "赛道", source: "track" },
    { key: "campus", label: "校区", source: "campus" }, { key: "product", label: "产品", source: "product" },
    { key: "grade", label: "年级", source: "grade" }, { key: "subject", label: "科目", source: "subject" },
  ];

  return <section className="result-app budget-overview budget-overview--embedded" aria-label="第五步：预算概览">
    <div className="result-main" id="result-main">
      {loading && <section className="result-state"><span className="result-loader" /><h1>正在汇总定版预算</h1><p>读取人数、收入和目标结果。</p></section>}
      {error && <section className="result-state result-state--error" role="alert"><h1>预算结果暂时无法读取</h1><p>{error}</p><a href={`/budget/workspace?revision=${encodeURIComponent(revisionId)}&section=home`}>返回年度预算</a></section>}
      {result && !loading && !error && <>
        <div className="overview-context"><span>任务 5 / 5</span><a href={`/budget/audit?revision=${encodeURIComponent(revisionId)}`}>变化记录</a></div>
        <header className="result-hero">
          <div><p>FY{String(result.fiscal_year).slice(-2)} · 学校版</p><h1 ref={pageTitleRef} tabIndex={-1}>预算概览</h1><span>预算已定版，查看结果或追溯定版记录。</span></div>
        </header>
        <nav className="overview-tabs" aria-label="预算概览"><button aria-pressed={view === "results"} onClick={() => changeView("results")}>预算结果</button><button aria-pressed={view === "record"} onClick={() => changeView("record")}>定版记录</button></nav>
        {view === "record" && identity && budgetContext && <section aria-label="定版记录"><PublishedBudgetRecord revisionId={revisionId} annualBudgetId={budgetContext.annualBudgetId} identity={identity} /></section>}
        <div hidden={view !== "results"} aria-label="预算结果">
        <section className="result-filters" aria-label="预算结果筛选">
          {filterFields.map(field => <label key={field.key}><span>{field.label}</span><select value={filters[field.key]} onChange={event => setFilter(field.key, event.target.value)}><option value="">全部</option>{values(rows, field.source).map(value => <option key={value} value={value}>{field.key === "fiscalSeason" ? rows.find(row => String(row.fiscal_season) === value)?.quarter_name : value}</option>)}</select></label>)}
          {Object.values(filters).some(Boolean) && <button onClick={() => setFilters(emptyFilters)}>清空筛选</button>}
        </section>

        <section className="result-kpis" aria-label="关键预算结果">
          <article className="result-kpi result-kpi--primary"><span>确认收入</span><strong>{money(totalRevenue, true)}</strong><small>{targetVariance === null ? "当前筛选结果" : `较年度目标 ${targetVariance >= 0 ? "+" : ""}${money(targetVariance, true)}`}</small></article>
          <article className="result-kpi"><span>业务收入</span><strong>{money(businessRevenue, true)}</strong><small>折后、税务转换前</small></article>
          <article className="result-kpi"><span>预算总人次</span><strong>{new Intl.NumberFormat("zh-CN").format(totalStudents)}</strong><small>各季度人次合计</small></article>
          <article className="result-kpi"><span>综合折扣</span><strong>{percent(compositeDiscount)}</strong><small>按收入加权计算</small></article>
        </section>

        <section className="result-quarterly">
          <header><div><p>01 / 季度收入</p><h2>全年收入节奏</h2></div><span>单位：万元</span></header>
          <div className="quarter-bars">{quarters.map(item => <article key={item.season}><div><span>{item.name}</span><strong>{money(item.revenue / 10_000)}</strong></div><i><b style={{ transform: `scaleX(${item.revenue / maxQuarter})` }} /></i></article>)}</div>
        </section>

        <section className="result-breakdown">
          <header><div><p>02 / 维度汇总</p><h2>收入构成</h2></div><div className="dimension-tabs" role="tablist">{(Object.keys(dimensionLabels) as DimensionKey[]).map(key => <button role="tab" aria-selected={dimension === key} className={dimension === key ? "is-active" : ""} key={key} onClick={() => setDimension(key)}>按{dimensionLabels[key]}</button>)}</div><button className="result-export" onClick={() => downloadCsv(grouped, dimension, result.fiscal_year)}>导出当前汇总</button></header>
          {grouped.length === 0 ? <div className="result-empty"><strong>当前筛选下没有结果</strong><span>请减少筛选条件后再查看。</span></div> : <><p className="result-table-hint">可横向滑动查看全部指标</p><div className="result-table-wrap" tabIndex={0} aria-label={`${dimensionLabels[dimension]}收入汇总表，可横向滚动`}><table><thead><tr><th>{dimensionLabels[dimension]}</th><th>确认收入</th><th>收入占比</th><th>业务收入</th><th>原价收入</th><th>人次</th><th>综合折扣</th></tr></thead><tbody>{grouped.map(row => <tr key={row.name}><th>{row.name}</th><td>{money(row.revenue, true)}</td><td><span className="share-cell"><i><b style={{ transform: `scaleX(${totalRevenue ? row.revenue / totalRevenue : 0})` }} /></i>{percent(totalRevenue ? row.revenue / totalRevenue * 100 : 0)}</span></td><td>{money(row.businessRevenue, true)}</td><td>{money(row.listRevenue, true)}</td><td>{new Intl.NumberFormat("zh-CN").format(row.students)}</td><td>{percent(row.listRevenue ? row.businessRevenue / row.listRevenue * 100 : null)}</td></tr>)}</tbody><tfoot><tr><th>合计</th><td>{money(totalRevenue, true)}</td><td>100.0%</td><td>{money(businessRevenue, true)}</td><td>{money(listRevenue, true)}</td><td>{new Intl.NumberFormat("zh-CN").format(totalStudents)}</td><td>{percent(compositeDiscount)}</td></tr></tfoot></table></div></>}
        </section>
        </div>
      </>}
    </div>
  </section>;
}
