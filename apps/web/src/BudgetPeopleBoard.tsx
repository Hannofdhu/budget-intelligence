import { Fragment, useMemo } from "react";
import { aggregateBudgetFacts, buildBudgetPivot, seasons } from "./budgetAnalysis";
import { renewalStageLabel } from "./budgetDisplay";
import type { BudgetDimension, BudgetFact, BudgetMetric } from "./budgetAnalysisTypes";

type Props = {
  facts: BudgetFact[];
  onSelect: (path: Partial<Record<BudgetDimension, string>>, label: string) => void;
  onFilterCampus: (campus: string) => void;
  onPivot: (metric: "total" | "renewal") => void;
};
const peopleMetrics = ["old", "new", "total"] as const;
const peopleLabels = { old: "老生", new: "新生", total: "合计" };
const number = (value: number | null) => value === null ? "—" : new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 8 }).format(value);
const rate = (value: number | null) => value === null ? "—" : `${new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 }).format(value * 100)}%`;

/** Uses the same filtered facts as revenue. No parameters, calculations or budgets are written. */
export function BudgetPeopleBoard({ facts, onSelect, onFilterCampus, onPivot }: Props) {
  const quarters = useMemo(() => seasons.map(season => ({ season, ...aggregateBudgetFacts(facts.filter(fact => fact.season === season)) })), [facts]);
  const campusPivot = useMemo(() => buildBudgetPivot(facts, { rows: ["campus"], column: "season", metrics: ["old", "new", "total", "renewal"], sort: "name-asc" }), [facts]);
  const renewalPivot = useMemo(() => buildBudgetPivot(facts, { rows: ["track", "grade"], column: "season", metrics: ["renewal"], sort: "name-asc" }), [facts]);
  const renewalRows = renewalPivot.rows.filter(row => row.leaf);
  const maxPeople = Math.max(1, ...quarters.map(quarter => quarter.old !== null && quarter.new !== null && quarter.old >= 0 && quarter.new >= 0 ? quarter.old + quarter.new : 0));
  const rates = quarters.flatMap(quarter => quarter.renewal === null ? [] : [quarter.renewal]);
  const rateMin = Math.min(0, ...rates), rateMax = Math.max(1, ...rates);
  const rateY = (value: number) => 150 - (value - rateMin) / (rateMax - rateMin) * 120;
  const rateX = (index: number) => 64 + index * 96;
  // Missing quarters break the line; a missing rate is never plotted as zero.
  const segments: string[][] = [];
  quarters.forEach((quarter, index) => {
    if (quarter.renewal === null) { segments.push([]); return; }
    if (!segments.length) segments.push([]);
    segments[segments.length - 1].push(`${rateX(index)},${rateY(quarter.renewal)}`);
  });
  const cellValue = (row: typeof campusPivot.rows[number], season: string, metric: BudgetMetric, columns = campusPivot.columns) => {
    const column = columns.find(item => item.value === season);
    return column ? row.cells[column.key]?.[metric] ?? null : null;
  };
  const select = (campus: string, season: string | null, metric: BudgetMetric, value: number | null) => <button className="ba-cell" aria-label={`${campus}，${season ?? "合计"}，${metric === "renewal" ? "预算续班率" : `${peopleLabels[metric as keyof typeof peopleLabels]}人次`}，${metric === "renewal" ? rate(value) : number(value)}`}
    onClick={() => onSelect(season ? { campus, season } : { campus }, `${campus} / ${season ?? "合计"} · ${metric === "renewal" ? "预算续班率" : `${peopleLabels[metric as keyof typeof peopleLabels]}人次`}`)}>{metric === "renewal" ? rate(value) : number(value)}</button>;
  // Continuous intensity communicates only the magnitude; no performance thresholds are introduced.
  const renewalHeat = (value: number | null) => value !== null && Number.isFinite(value) && value >= 0 && value <= 1
    ? { backgroundColor: `rgba(54, 106, 85, ${0.04 + value * 0.16})` } : undefined;

  return <>
    <div className="ba-board-grid ba-people-charts">
      <section className="ba-panel"><header><h3>季度招生人次</h3><div className="ba-people-legend"><span className="ba-legend-old">老生</span><span className="ba-legend-new">新生</span><small>人次</small></div></header>
        <div className="ba-people-chart" role="group" aria-label="各季度老生与新生人次">
          {quarters.map(quarter => {
            const valid = quarter.old !== null && quarter.new !== null && quarter.old >= 0 && quarter.new >= 0;
            return <div className="ba-people-quarter" key={quarter.season}>
              <button className="ba-text-button" aria-label={`${quarter.season}总人次${number(quarter.total)}`} onClick={() => onSelect({ season: quarter.season }, `${quarter.season} · 招生人次`)}><strong>{number(quarter.total)}</strong></button>
              <div className="ba-people-track" aria-hidden="true">{valid && <><span className="ba-people-new" style={{ height: `${quarter.new! / maxPeople * 100}%` }} /><span className="ba-people-old" style={{ height: `${quarter.old! / maxPeople * 100}%` }} /></>}</div>
              <span>{quarter.season}</span>
              <div className="ba-people-values"><span>老 {number(quarter.old)}</span><span>新 {number(quarter.new)}</span></div>
            </div>;
          })}
        </div>
      </section>
      <section className="ba-panel"><header><h3>季度续班率</h3><small>%</small></header>
        <div className="ba-renewal-values">{quarters.map(quarter => <button key={quarter.season} className="ba-text-button" aria-label={`查看${renewalStageLabel(quarter.season)}预算续班率${rate(quarter.renewal)}的明细`} onClick={() => onSelect({ season: quarter.season }, `${renewalStageLabel(quarter.season)} · 预算续班率`)}><span>{renewalStageLabel(quarter.season)}</span><strong>{rate(quarter.renewal)}</strong></button>)}</div>
        <svg className="ba-renewal-chart" viewBox="0 0 400 174" role="img" aria-label={quarters.map(quarter => `${renewalStageLabel(quarter.season)}续班率${rate(quarter.renewal)}`).join("；")}>
          {[rateMin, (rateMin + rateMax) / 2, rateMax].map(value => <g key={value}><line x1="44" x2="376" y1={rateY(value)} y2={rateY(value)} /><text x="38" y={rateY(value) + 4} textAnchor="end">{Math.round(value * 100)}%</text></g>)}
          {segments.filter(segment => segment.length > 1).map((segment, index) => <polyline className="ba-renewal-line" key={index} points={segment.join(" ")} />)}
          {quarters.map((quarter, index) => quarter.renewal !== null && <circle className="ba-renewal-point" key={quarter.season} cx={rateX(index)} cy={rateY(quarter.renewal)} r="5" />)}
        </svg>
      </section>
    </div>
    <section className="ba-panel"><header><h3>季度招生预算</h3><small>人次 / %</small></header><div className="ba-table-wrap" tabIndex={0} aria-label="季度招生预算表"><table className="ba-table"><thead><tr><th scope="col">季度</th><th scope="col">老生人次</th><th scope="col">新生人次</th><th scope="col">总人次</th><th scope="col">续班阶段</th><th scope="col">续班率</th></tr></thead><tbody>{quarters.map(quarter => <tr key={quarter.season}><th scope="row">{quarter.season}</th>{peopleMetrics.map(metric => <td key={metric}>{number(quarter[metric])}</td>)}<td>{renewalStageLabel(quarter.season)}</td><td>{rate(quarter.renewal)}</td></tr>)}</tbody></table></div></section>
    <section className="ba-panel"><header><h3>校区招生人次</h3><div className="ba-toolbar"><small>人次</small><button onClick={() => onPivot("total")}>透视人次</button></div></header>
      <div className="ba-table-wrap" tabIndex={0} aria-label="校区季度老生新生人次表"><table className="ba-table ba-student-table"><thead><tr><th scope="col">校区</th><th scope="col" className="ba-people-metric">人次</th>{seasons.map(season => <th scope="col" key={season}>{season}</th>)}<th scope="col">合计</th></tr></thead><tbody>{campusPivot.rows.map(row => <Fragment key={row.key}>{peopleMetrics.map((metric, index) => <tr key={metric} className={metric === "total" ? "ba-people-total" : ""}>{index === 0 && <th scope="rowgroup" rowSpan={3}><button className="ba-text-button" onClick={() => onFilterCampus(row.label)}>{row.label}</button></th>}<th scope="row" className="ba-people-metric">{peopleLabels[metric]}</th>{seasons.map(season => <td key={season}>{select(row.label, season, metric, cellValue(row, season, metric))}</td>)}<td>{select(row.label, null, metric, row.total[metric])}</td></tr>)}</Fragment>)}</tbody><tfoot>{peopleMetrics.map((metric, index) => <tr key={metric}>{index === 0 && <th scope="rowgroup" rowSpan={3}>合计</th>}<th scope="row" className="ba-people-metric">{peopleLabels[metric]}</th>{quarters.map(quarter => <td key={quarter.season}>{number(quarter[metric])}</td>)}<td>{number(campusPivot.totals[metric])}</td></tr>)}</tfoot></table></div>
    </section>
    <section className="ba-panel"><header><h3>赛道与年级续班率</h3><div className="ba-toolbar"><small>%</small><button onClick={() => onPivot("renewal")}>透视续班率</button></div></header>
      <div className="ba-table-wrap" tabIndex={0} aria-label="赛道年级跨季续班率表"><table className="ba-table ba-renewal-table">
        <thead><tr><th scope="col">赛道</th><th scope="col">年级</th>{seasons.map(season => <th scope="col" key={season}>{renewalStageLabel(season)}</th>)}</tr></thead>
        <tbody>{renewalRows.map(row => <tr key={row.key}><th scope="row">{row.path.track}</th><th scope="row">{row.path.grade}</th>{seasons.map(season => {
          const value = cellValue(row, season, "renewal", renewalPivot.columns);
          const label = `${row.path.track} / ${row.path.grade} / ${renewalStageLabel(season)} · 预算续班率`;
          return <td className="ba-renewal-heatcell" style={renewalHeat(value)} key={season}><button className="ba-cell" aria-label={`${row.path.track}，${row.path.grade}，${renewalStageLabel(season)}，预算续班率，${rate(value)}`} onClick={() => onSelect({ ...row.path, season }, label)}>{rate(value)}</button></td>;
        })}</tr>)}</tbody>
        <tfoot><tr><th scope="row" colSpan={2}>合计</th>{quarters.map(quarter => <td key={quarter.season}>{rate(quarter.renewal)}</td>)}</tr></tfoot>
      </table></div>
    </section>
  </>;
}
