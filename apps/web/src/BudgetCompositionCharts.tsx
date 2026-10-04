import { useMemo } from "react";
import { aggregateBudgetFacts } from "./budgetAnalysis";
import { compareBudgetGrades } from "./budgetDisplay";
import type { BudgetDimension, BudgetFact, BudgetMetricValues } from "./budgetAnalysisTypes";

type Props = {
  facts: BudgetFact[];
  onSelect: (path: Partial<Record<BudgetDimension, string>>, label: string) => void;
};
type CompositionRow = { label: string; values: BudgetMetricValues };
type CompositionDimension = "grade" | "campus";

const number = (value: number | null) => value === null ? "—" : new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 8 }).format(value);
const collator = new Intl.Collator("zh-CN", { numeric: true });

function rowsFor(facts: BudgetFact[], dimension: CompositionDimension): CompositionRow[] {
  const groups = new Map<string, BudgetFact[]>();
  for (const fact of facts) {
    const group = groups.get(fact[dimension]) ?? [];
    group.push(fact);
    groups.set(fact[dimension], group);
  }
  return [...groups].map(([label, group]) => ({ label, values: aggregateBudgetFacts(group) })).sort((left, right) => {
    if (dimension === "grade") {
      return compareBudgetGrades(left.label, right.label);
    }
    if (left.values.total === null && right.values.total !== null) return 1;
    if (right.values.total === null && left.values.total !== null) return -1;
    return (right.values.total ?? 0) - (left.values.total ?? 0) || collator.compare(left.label, right.label);
  });
}

// Only complete, non-negative counts may form a stacked bar. The numbers beside
// an unplotted row remain their recorded aggregates, including negative counts.
function canPlot(values: BudgetMetricValues): boolean {
  return values.old !== null && values.new !== null && values.total !== null && values.old >= 0 && values.new >= 0 && values.total >= 0;
}

function CompositionChart({ facts, dimension, onSelect }: Props & { dimension: CompositionDimension }) {
  const rows = useMemo(() => rowsFor(facts, dimension), [facts, dimension]);
  const total = useMemo(() => aggregateBudgetFacts(facts), [facts]);
  const maximum = Math.max(0, ...rows.map(row => canPlot(row.values) ? row.values.old! + row.values.new! : 0));
  const title = dimension === "grade" ? "年级招生结构" : "校区招生结构";
  const label = dimension === "grade" ? "年级" : "校区";
  const accessibleCounts = (values: BudgetMetricValues) => `老生${number(values.old)}人次，新生${number(values.new)}人次，合计${number(values.total)}人次`;

  return <section className="ba-panel ba-composition-panel">
    <header><h3>{title}</h3><div className="ba-composition-legend"><span className="ba-composition-legend-old">老生</span><span className="ba-composition-legend-new">新生</span><small>人次</small></div></header>
    {rows.length === 0 ? <p className="ba-composition-empty">暂无招生明细</p> : <div className="ba-composition-plot" role="group" aria-label={title} data-scale-max={maximum}>
      <div className="ba-composition-columns ba-composition-head"><span>{label}</span><span className="ba-composition-axis"><span>0</span><span>{maximum > 0 ? number(maximum) : ""}</span></span><span>老生</span><span>新生</span><span>合计</span></div>
      <div className="ba-composition-scroll" role="region" aria-label={`${title}明细`} tabIndex={0}>
        {rows.map(row => <button className="ba-composition-columns ba-composition-row" key={row.label} aria-label={`${row.label}，${accessibleCounts(row.values)}`}
          onClick={() => onSelect({ [dimension]: row.label }, `${row.label} · 招生人次`)}>
          <span className="ba-composition-name">{row.label}</span>
          <span className="ba-composition-track" aria-hidden="true">{canPlot(row.values) && <><span className="ba-composition-old" style={{ width: `${maximum > 0 ? row.values.old! / maximum * 100 : 0}%` }} /><span className="ba-composition-new" style={{ width: `${maximum > 0 ? row.values.new! / maximum * 100 : 0}%` }} /></>}</span>
          <span className="ba-composition-number">{number(row.values.old)}</span><span className="ba-composition-number">{number(row.values.new)}</span><strong className="ba-composition-number">{number(row.values.total)}</strong>
        </button>)}
      </div>
      <button className="ba-composition-columns ba-composition-row ba-composition-total" aria-label={`${title}合计，${accessibleCounts(total)}`} onClick={() => onSelect({}, `${title} · 合计`)}>
        <strong>合计</strong><span /><strong className="ba-composition-number">{number(total.old)}</strong><strong className="ba-composition-number">{number(total.new)}</strong><strong className="ba-composition-number">{number(total.total)}</strong>
      </button>
    </div>}
  </section>;
}

/** Reads the already-filtered facts; never derives a revision or changes inputs. */
export function BudgetCompositionCharts(props: Props) {
  return <div className="ba-board-grid ba-composition-grid"><CompositionChart {...props} dimension="grade" /><CompositionChart {...props} dimension="campus" /></div>;
}
