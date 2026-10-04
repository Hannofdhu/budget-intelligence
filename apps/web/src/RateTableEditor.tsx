import { useEffect, useMemo, useState } from "react";
import { meanings, type Value } from "./BusinessForm";

type RecordValue = Record<string, Value>;
type RateAction = "lock" | "unlock";

function toPercent(value: Value): string {
  if (typeof value !== "number") return String(value ?? "");
  return String(value / 10000);
}

function parsePercent(value: string): number | string {
  const match = /^(\d+)(?:\.(\d{0,4}))?$/.exec(value.trim());
  if (!match) return value;
  return Number(match[1]) * 10000 + Number((match[2] ?? "").padEnd(4, "0"));
}

function PercentInput({ value, onCommit, label, disabled }: { value: Value; onCommit: (value: Value) => void; label: string; disabled?: boolean }) {
  const [text, setText] = useState(toPercent(value));
  useEffect(() => setText(toPercent(value)), [value]);
  return <div className="rate-percent"><input aria-label={label} inputMode="decimal" disabled={disabled} value={text} onChange={event => setText(event.target.value)} onBlur={() => onCommit(parsePercent(text))} /><span>%</span></div>;
}

function rowKey(row: RecordValue, index: number): string {
  return String(row.rate_reference_id ?? row.rate_parameter_stable_key ?? index);
}

export function RateTableEditor({ value, referenceRows, disabled, dirty, onChange, onAction }: {
  value: Value;
  referenceRows: RecordValue[];
  disabled?: boolean;
  dirty: boolean;
  onChange: (value: Value) => void;
  onAction: (mode: RateAction, keys: string[], reason: string) => void;
}) {
  const object = value && typeof value === "object" && !Array.isArray(value) ? value as RecordValue : {};
  const items = Array.isArray(object.items) ? object.items as RecordValue[] : [];
  const metadata = useMemo(() => new Map(referenceRows.map((row, index) => [rowKey(row, index), row])), [referenceRows]);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [reason, setReason] = useState("");
  const [batchCurrent, setBatchCurrent] = useState("");
  const [batchLower, setBatchLower] = useState("");
  const [batchUpper, setBatchUpper] = useState("");

  const rows = items.map((item, index) => ({ item, index, key: rowKey(item, index), meta: metadata.get(rowKey(item, index)) ?? {} }));
  const filtered = rows.filter(({ item, meta }) => [meta.quarter_name, meta.target_fiscal_season, meta.campus, meta.track, meta.product, meta.grade, meta.rate_type, item.boundary_reason, item.manual_reason].map(String).join(" ").toLowerCase().includes(query.trim().toLowerCase()));
  const complete = items.filter(item => ["current_value_ppm", "lower_bound_ppm", "upper_bound_ppm"].every(key => typeof item[key] === "number")).length;
  const updateRow = (index: number, key: string, next: Value) => onChange({ ...object, items: items.map((item, itemIndex) => itemIndex === index ? { ...item, [key]: next } : item) });
  const applyBatch = () => {
    const changes: Record<string, Value> = {};
    if (batchCurrent.trim()) changes.current_value_ppm = parsePercent(batchCurrent);
    if (batchLower.trim()) changes.lower_bound_ppm = parsePercent(batchLower);
    if (batchUpper.trim()) changes.upper_bound_ppm = parsePercent(batchUpper);
    if (!Object.keys(changes).length || !selected.size) return;
    onChange({ ...object, items: items.map((item, index) => selected.has(rowKey(item, index)) ? { ...item, ...changes } : item) });
    setBatchCurrent(""); setBatchLower(""); setBatchUpper("");
  };
  const selectedStableKeys = rows.filter(row => selected.has(row.key)).map(row => String(row.meta.rate_parameter_stable_key ?? "")).filter(Boolean);
  const allVisibleSelected = filtered.length > 0 && filtered.every(row => selected.has(row.key));

  return <div className="rate-editor">
    <div className="rate-summary" aria-label="比例参数填写进度">
      <div><strong>{complete}/{items.length}</strong><span>已完整填写</span></div>
      <div><strong>{referenceRows.filter(row => row.locked === true).length}</strong><span>已锁定</span></div>
      <div><strong>{referenceRows.filter(row => row.boundary_confirmed === true).length}</strong><span>范围已确认</span></div>
    </div>
    <div className="rate-toolbar"><label className="rate-search"><span className="sr-only">筛选比例参数</span><input placeholder="筛选季节、校区、产品或年级" value={query} onChange={event => setQuery(event.target.value)} /></label><span>{filtered.length} 条结果</span></div>
    {selected.size > 0 && <section className="rate-batch" aria-label="批量修改选中比例">
      <div><strong>已选 {selected.size} 项</strong></div>
      <label>当前值<input inputMode="decimal" placeholder="例如 80" value={batchCurrent} onChange={event => setBatchCurrent(event.target.value)} /></label>
      <label>下限<input inputMode="decimal" placeholder="例如 70" value={batchLower} onChange={event => setBatchLower(event.target.value)} /></label>
      <label>上限<input inputMode="decimal" placeholder="例如 90" value={batchUpper} onChange={event => setBatchUpper(event.target.value)} /></label>
      <button type="button" disabled={disabled || (!batchCurrent && !batchLower && !batchUpper)} onClick={applyBatch}>应用到选中项</button>
    </section>}
    <div className="rate-table-wrap"><table className="rate-table"><thead><tr>
      <th><input type="checkbox" aria-label="选择当前筛选结果" checked={allVisibleSelected} onChange={() => setSelected(current => { const next = new Set(current); for (const row of filtered) allVisibleSelected ? next.delete(row.key) : next.add(row.key); return next; })} /></th>
      <th>季节</th><th>校区与业务</th><th>年级</th><th>指标</th><th>历史参考</th><th>当前值</th><th>允许范围</th><th>调整依据</th><th>状态</th>
    </tr></thead><tbody>{filtered.map(({ item, index, key, meta }) => <tr key={key}>
      <td><input type="checkbox" aria-label={`选择${meta.quarter_name ?? "该"}比例`} checked={selected.has(key)} onChange={() => setSelected(current => { const next = new Set(current); next.has(key) ? next.delete(key) : next.add(key); return next; })} /></td>
      <td><strong>{String(meta.quarter_name ?? meta.target_fiscal_season ?? "—")}</strong><small>FY{String(meta.fiscal_year ?? "").slice(-2)}</small></td>
      <td><strong>{String(meta.campus ?? "—")}</strong><small>{[meta.track, meta.product].filter(Boolean).join(" · ")}</small></td>
      <td>{String(meta.grade ?? "—")}</td><td>{meanings[String(meta.rate_type)] ?? String(meta.rate_type ?? "—")}</td>
      <td className="rate-reference">{typeof meta.system_value_ppm === "number" ? `${toPercent(meta.system_value_ppm)}%` : "人工确定"}</td>
      <td><PercentInput label="当前比例" disabled={disabled} value={item.current_value_ppm} onCommit={next => updateRow(index, "current_value_ppm", next)} /></td>
      <td><div className="rate-range"><PercentInput label="比例下限" disabled={disabled} value={item.lower_bound_ppm} onCommit={next => updateRow(index, "lower_bound_ppm", next)} /><span>至</span><PercentInput label="比例上限" disabled={disabled} value={item.upper_bound_ppm} onCommit={next => updateRow(index, "upper_bound_ppm", next)} /></div></td>
      <td><div className="rate-reasons"><input disabled={disabled} aria-label="范围依据" value={String(item.boundary_reason ?? "")} placeholder="范围依据" onChange={event => updateRow(index, "boundary_reason", event.target.value)} /><input disabled={disabled} aria-label="人工调整原因" value={String(item.manual_reason ?? "")} placeholder="调整原因" onChange={event => updateRow(index, "manual_reason", event.target.value)} /></div></td>
      <td><span className={`status-tag ${meta.locked === true ? "status-tag--locked" : ""}`}>{meta.locked === true ? "已锁定" : meta.boundary_confirmed === true ? "已确认" : "待确认"}</span></td>
    </tr>)}</tbody></table></div>
    {selected.size > 0 && <section className="rate-lockbar" aria-label="选中比例的锁定操作"><div><strong>管理选中比例</strong>{dirty && <span role="status">请先保存比例</span>}</div><label><span className="sr-only">锁定或解锁原因</span><input disabled={dirty || disabled} placeholder="填写锁定或解锁原因" value={reason} onChange={event => setReason(event.target.value)} /></label><button type="button" disabled={dirty || disabled || !reason.trim() || !selectedStableKeys.length} onClick={() => onAction("lock", selectedStableKeys, reason.trim())}>锁定选中项</button><button type="button" disabled={dirty || disabled || !reason.trim() || !selectedStableKeys.length} onClick={() => onAction("unlock", selectedStableKeys, reason.trim())}>解锁选中项</button></section>}
  </div>;
}
