import { useEffect, useRef, useState, type CSSProperties } from "react";
import { ActionTable } from "./ActionTable";
import type { ApiIdentity } from "./api";
import type { ExportColumn } from "./budgetExport";
import { forecastFailure, forecastNumber, forecastSeasons, previewForecast, type ForecastAssumptions, type ForecastCampus, type ForecastPreview, type ForecastVersion } from "./forecastApi";
import { campusForecastTotals, campusRateBase, campusRateCount, campusRateKey, campusRateKinds, campusRateLabels, campusRateToCount, campusRateValue, restoreCampusRates, setCampusRate } from "./forecastRateInputs";
import "./forecastCampusRates.css";

export type CampusRateDraftStatus = { invalid: boolean; previewBusy: boolean; previewError: boolean };
const format = (value: number | null, digits = 0) => value === null ? "—" : value.toLocaleString("zh-CN", { maximumFractionDigits: digits });
const rateText = (value: number | null) => value === null ? "" : String(Number(value.toFixed(3)));
const sameCampus = (row: { season: string; campus: string }, season: string, campus: string) => row.season === season && row.campus === campus;

export function forecastCampusTrialTransfer(rows: ForecastCampus[], version: ForecastVersion) {
  const columns: ExportColumn[] = [{ key: "season", label: "季度" }, { key: "campus", label: "校区" },
    ...(["budget_new", "current_business_new", "budgeted_additional", "unbudgeted_additional", "new_withdrawal", "old_withdrawal"] as const).map((key, index) => ({ key,
      label: ["预算新生人次", "当前业务新生人次", "预算内后续新增人次", "独立业务新增人次", "后续新生退班人次", "后续老生退班人次"][index], format: "count" as const })),
    { key: "result_status", label: "结果状态" }, { key: "basis_sequence", label: "基准预测版号", format: "count" },
    { key: "basis_id", label: "基准预测精确标识" }, { key: "as_of", label: "数据截至日期" }];
  return { columns, rows: rows.map(row => ({ ...row, result_status: "未保存试算", basis_sequence: version.sequence, basis_id: version.id, as_of: version.as_of })), filename: "校区预测人数试算明细" };
}

/** This is the existing adjustment drawer's input adapter, not another saved workspace. */
export function ForecastCampusRates({ identity, version, assumptions, active, disabled, onChange, onStatus }: {
  identity: ApiIdentity; version: ForecastVersion; assumptions: ForecastAssumptions; active: boolean; disabled: boolean;
  onChange: (value: ForecastAssumptions) => void; onStatus: (value: CampusRateDraftStatus) => void;
}) {
  const eligible = forecastSeasons.filter(value => ["OPEN", "ENDED"].includes(assumptions.states[value]));
  const [selectedSeason, setSelectedSeason] = useState(eligible[0] ?? "秋季");
  const season = eligible.includes(selectedSeason) ? selectedSeason : eligible[0];
  const [selectedCampus, setSelectedCampus] = useState("");
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [trial, setTrial] = useState<{ key: string; result: ForecastPreview } | null>(null);
  const [failure, setFailure] = useState<{ key: string; message: string } | null>(null);
  const [retry, setRetry] = useState(0);
  const generation = useRef(0);
  const requestKey = JSON.stringify([identity.userId, identity.departmentId, identity.roles, version.id, assumptions, retry]);
  const ready = trial?.key === requestKey;
  const error = failure?.key === requestKey ? failure.message : "";
  const pending = active && !ready && !error;
  useEffect(() => {
    if (!active || disabled) return;
    const controller = new AbortController(), ticket = ++generation.current;
    const timer = setTimeout(() => {
      void previewForecast(identity, version.id, assumptions, controller.signal).then(result => {
        if (!controller.signal.aborted && ticket === generation.current) { setTrial({ key: requestKey, result }); setFailure(null); }
      }).catch(error => {
        if (!controller.signal.aborted && ticket === generation.current) setFailure({ key: requestKey, message: forecastFailure(error).message });
      });
    }, 400);
    return () => { ++generation.current; clearTimeout(timer); controller.abort(); };
    // Serialized identity and ALL pending assumptions, not just the visible campus.
  }, [requestKey, active, disabled]);
  const reference = trial?.result.system_campuses ?? version.calculation.campuses;
  const campuses = [...new Set(reference.filter(row => row.season === season).map(row => row.campus))].sort((a, b) => a.localeCompare(b, "zh-CN"));
  const campus = campuses.includes(selectedCampus) ? selectedCampus : campuses[0] ?? "";
  const row = reference.find(row => sameCampus(row, season, campus));
  const originalRow = version.calculation.campuses.find(row => sameCampus(row, season, campus));
  const invalidFields = Object.entries(drafts).filter(([key, value]) => {
    const [draftSeason, draftCampus, kind] = JSON.parse(key) as [string, string, typeof campusRateKinds[number]];
    const base = reference.find(row => sameCampus(row, draftSeason, draftCampus));
    return !base || campusRateToCount(base, kind, value) === null;
  });
  const invalid = invalidFields.length > 0;
  useEffect(() => { onStatus({ invalid, previewBusy: pending, previewError: Boolean(error) }); }, [invalid, pending, error, onStatus]);
  const adjustmentKeys = new Set([...assumptions.adjustments, ...version.assumptions.adjustments].map(item => campusRateKey(item.season, item.campus, item.kind)));
  const changed = [...adjustmentKeys].filter(key => JSON.stringify(assumptions.adjustments.find(item => campusRateKey(item.season, item.campus, item.kind) === key)) !== JSON.stringify(version.assumptions.adjustments.find(item => campusRateKey(item.season, item.campus, item.kind) === key))).length;
  function resetInputs() {
    setDrafts(current => Object.fromEntries(Object.entries(current).filter(([key]) => { const [s, c] = JSON.parse(key); return s !== season || c !== campus; })));
  }
  function restore(system: boolean) {
    onChange(restoreCampusRates(assumptions, version.assumptions, season, campus, system));
    resetInputs();
  }
  const original = campusForecastTotals(version.calculation, season, campus);
  const calculated = ready ? campusForecastTotals(trial.result.calculation, season, campus) : null;
  const comparison = [
    { label: `${campus || "本校区"} · ${season ?? "当前季度"}预计人次`, old: original.people, next: calculated?.people ?? null, unit: "人次", digits: 0 },
    { label: "本校区 · 本季预计结转收入", old: original.revenue === null ? null : original.revenue / 10000, next: calculated?.revenue == null ? null : calculated.revenue / 10000, unit: "万元", digits: 2 },
    { label: "全部门 · 全年预计结转收入", old: forecastNumber(version.calculation.annual.forecast_revenue), next: ready ? forecastNumber(trial.result.calculation.annual.forecast_revenue) : null, unit: "万元", digits: 2, money: true },
    { label: "全部门 · 全年预计缺口 / 超额", old: forecastNumber(version.calculation.annual.gap), next: ready ? forecastNumber(trial.result.calculation.annual.gap) : null, unit: "万元", digits: 2, money: true, gap: true },
  ];
  const blockers = ready ? trial.result.calculation.issues.filter(issue => issue.severity === "BLOCKING") : [];
  return <div className="forecast-campus-rates">
    <div className="forecast-rate-intro"><strong>按校区调整本次预测</strong><p>只改本次试算；保存后生成新草稿。下次自动或手动更新，仍按原算法重新预测。</p></div>
    <div className="forecast-rate-scope"><label>调整季度<select aria-label="调整季度" value={season ?? ""} disabled={disabled || !eligible.length} onChange={event => setSelectedSeason(event.target.value as typeof selectedSeason)}>{eligible.length ? eligible.map(value => <option key={value}>{value}</option>) : <option value="">暂无可调整季度</option>}</select></label><label>调整校区<select aria-label="调整校区" value={campus} disabled={disabled || !campuses.length} onChange={event => setSelectedCampus(event.target.value)}>{campuses.map(value => <option key={value}>{value}</option>)}</select></label></div>
    <p className="forecast-rate-context">数据截至 {version.as_of} · 可切换校区连续调整，最后统一保存。{changed > 0 && `已调整 ${changed} 项。`}</p>
    {invalid && <p className="forecast-rate-hint" role="alert">待补全：{invalidFields.map(([key]) => { const [s, c, k] = JSON.parse(key) as [string, string, typeof campusRateKinds[number]]; return `${c} · ${s} · ${campusRateLabels[k]}`; }).join("；")}。请切回对应校区补全，或撤销该校区修改。</p>}
    {row ? <div className="forecast-rate-controls">{campusRateKinds.map(kind => {
      const key = campusRateKey(row.season, row.campus, kind);
      const override = assumptions.adjustments.find(item => campusRateKey(item.season, item.campus, item.kind) === key);
      const count = override?.value ?? campusRateCount(row, kind);
      const saved = originalRow ? campusRateCount(originalRow, kind) : null;
      const value = drafts[key] ?? rateText(campusRateValue(row, kind, count));
      const recommendation = campusRateValue(row, kind), base = campusRateBase(row, kind);
      const available = base !== null && base > 0 && recommendation !== null && Boolean(trial);
      const invalidValue = available && campusRateToCount(row, kind, value) === null;
      const max = kind === "additional" ? Math.max(200, Math.ceil(Math.max(Number(value) || 0, recommendation ?? 0) / 50) * 50) : 100;
      const update = (value: string) => {
        setDrafts(current => ({ ...current, [key]: value }));
        onChange(setCampusRate(assumptions, row, kind, value, count, originalRow ? { assumptions: version.assumptions, row: originalRow } : undefined));
      };
      return <section key={kind} className="forecast-rate-control" aria-labelledby={`rate-title-${kind}`}>
        <div className="forecast-rate-title"><label id={`rate-title-${kind}`} htmlFor={`rate-number-${kind}`}>{campusRateLabels[kind]}</label><div className="forecast-rate-number"><input id={`rate-number-${kind}`} aria-label={`${campusRateLabels[kind]}百分比`} type="number" inputMode="decimal" min="0" max={kind === "additional" ? 1000000 : 100} step="0.1" value={value} disabled={disabled || !available} aria-invalid={invalidValue || undefined} onChange={event => update(event.target.value)}/><span>%</span></div></div>
        <div className="forecast-rate-track" style={{ "--rate-recommendation": `${Math.min(100, (recommendation ?? 0) / max * 100)}%` } as CSSProperties}><input type="range" aria-label={`${campusRateLabels[kind]}滑块`} aria-valuetext={`${value || "未填写"}%；${format(count)}人次`} min="0" max={max} step="0.1" value={Number(value) || 0} disabled={disabled || !available} onChange={event => update(event.target.value)}/><i aria-hidden="true"/></div>
        <div className="forecast-rate-caption"><span>{kind === "additional" ? `预算新生 ${format(base)} · 已完成 ${format(forecastNumber(row.current_business_new))}` : `当前${kind === "new_withdrawal" ? "新生" : "老生"}在班 ${format(base)} 人次`}</span><span>系统建议 {recommendation === null ? "—" : `${format(recommendation, 2)}%`}{override && " · 人工设定"}</span></div>
        <p className="forecast-rate-conversion">{kind === "additional" ? "预算内后续新增" : kind === "new_withdrawal" ? "后续新生退班" : "后续老生退班"} <strong>{format(invalidValue ? null : count)}</strong> 人次{!invalidValue && count !== null && saved !== null && <span> · 比保存值{count - saved >= 0 ? "增加" : "减少"} {format(Math.abs(count - saved))} 人次</span>}</p>
        {!available && <p className="forecast-rate-hint">{trial ? "缺少有效基数，不能用率值调整；请在数据核对中检查。" : "正在准备参考基数…"}</p>}
        {invalidValue && <p className="forecast-rate-hint" role="alert">{kind === "additional" ? "请输入有效百分比，最多三位小数；换算人次不能超过 1,000 万。" : "请输入 0–100 之间的百分比，最多三位小数。"}</p>}
        {!invalidValue && kind === "additional" && Number(value) < (forecastNumber(row.current_business_new) ?? 0) / (base || 1) * 100 && <p className="forecast-rate-hint">已完成新生不会减少，后续新增最低为 0 人次。</p>}
      </section>;
    })}</div> : <p>当前没有可调整的校区数据。已定版和未开始季度不使用这组滑块。</p>}
    <div className="forecast-rate-actions"><button type="button" disabled={disabled || !row} onClick={() => restore(false)}>撤销本校区修改</button><button type="button" disabled={disabled || !row || !trial} onClick={() => restore(true)}>恢复本校区系统建议</button></div>
    <p className="forecast-rate-context">新生达成率只作用于预算内业务；独立业务新增仍在“业务参数”调整。退班率只估算后续退班，不改已发生的实际数据。人次按整数取整，率值微调可能不改变人次。</p>
    <section className="forecast-rate-preview" aria-label="本次试算对比" aria-busy={pending}>
      <header><strong>本次试算 <span>未保存</span></strong><span role="status">{invalid ? "先补全率值" : error ? "试算未完成" : pending ? "正在后台试算…" : ready ? "已保存 → 本次试算" : "打开人数调整后试算"}</span></header>
      {error && <div className="forecast-rate-hint" role="alert">{error}<button type="button" onClick={() => setRetry(value => value + 1)}>重新试算</button></div>}
      <dl>{comparison.map(item => { const old = item.old === null ? null : item.old / (item.money ? 10000 : 1), next = invalid || item.next === null ? null : item.next / (item.money ? 10000 : 1); const value = (amount: number | null) => item.gap && amount !== null ? `${amount < 0 ? "超额" : "缺口"} ${format(Math.abs(amount), item.digits)}` : format(amount, item.digits); return <div key={item.label}><dt>{item.label}</dt><dd><span>{value(old)}</span><span aria-hidden="true">→</span><strong>{value(next)}</strong><small>{item.unit}</small></dd></div>; })}</dl>
      {blockers.length > 0 && <details><summary>试算有 {blockers.length} 项数据需核对</summary><ul>{blockers.slice(0, 6).map((issue, index) => <li key={index}>{issue.season} {issue.dims?.campus} · {issue.message}</li>)}</ul><p>未补齐的结果保持“—”，不会当作 0。可到“数据维护”核对完整问题。</p></details>}
    </section>
    <details className="forecast-rate-details"><summary>查看调整明细</summary>{ready && !invalid ? <ActionTable keyboardNavigation tableLabel="校区预测人数试算明细" exportData={forecastCampusTrialTransfer(trial.result.calculation.campuses, version)} exportLabel="导出当前表格" className="forecast-campus-table" scrollClassName="forecast-table-wrap"><thead><tr>{["季度", "校区", "预算新生", "当前业务新生", "预算内后续新增", "独立业务新增", "后续新生退班", "后续老生退班"].map(label => <th key={label} scope="col">{label}</th>)}</tr></thead><tbody>{trial.result.calculation.campuses.map((item: ForecastCampus) => <tr key={`${item.season}-${item.campus}`}><th scope="row">{item.season}</th><td className="budget-cell-text">{item.campus}</td>{[item.budget_new, item.current_business_new, item.budgeted_additional, item.unbudgeted_additional, item.new_withdrawal, item.old_withdrawal].map((value, index) => <td key={index}>{format(forecastNumber(value))}</td>)}</tr>)}</tbody></ActionTable> : <p>试算完成后，可查看、复制和导出本次明细。</p>}<p>本次未保存试算，基于第{version.sequence}版保存结果。</p></details>
  </div>;
}
