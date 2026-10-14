import { ActionTable } from "./ActionTable";
import { useState } from "react";
import { tableExportData } from "./tableTransfer";

export type Value = null | boolean | string | number | Value[] | { [key: string]: Value };
export type Schema = { [key: string]: any };
export const labels: Record<string, string> = {
  items: "明细", campuses: "校区", fiscal_year: "财年", fiscal_season: "财季", source_fiscal_season: "来源季节", target_fiscal_season: "适用季节", track: "赛道", product: "产品", grade: "年级", subject: "科目", campus: "校区", entry_attribute: "入口属性", enabled: "参与预算", change_reason: "调整原因", reason: "操作原因", manual_reason: "人工调整原因", boundary_reason: "范围依据", source_evidence: "目标依据", reference: "依据", tax_factor_source_evidence: "税务转换依据", tax_factor_change_reason: "转换系数说明", budget_tax_conversion_factor: "预算含税转换系数", class_size_parameter_reference: "班均依据", scope_level: "目标层级", target_revenue_yuan: "结转收入目标（元）", fiscal_year_entry_total: "年度入口总人次", performance_evidence: "历史业绩依据", classroom_resource: "教室资源", sales_resource: "销售资源", rooms: "教室数", consultants: "销售人数", determined_entry_value: "校区入口人次", adjustment_reason_category: "调整原因类型", adjustment_reason_detail: "调整说明", exception_type: "例外类型", manual_reservations: "新产品人工分配", new_product_share_ppm: "新产品占比", reserved_entry_value: "预留入口人次", manual_share_ppm: "人工占比", current_value_ppm: "当前比例", lower_bound_ppm: "允许下限", upper_bound_ppm: "允许上限", new_value_ppm: "修改后比例", planned_new_students: "计划新生人次", manual_renewal_rate_ppm: "新产品人工续班率", class_size_items: "班均参数", revenue_items: "价格与结转", opening_carryover_items: "期初结转", system_value: "班均参考值", final_value: "班均确定值", list_price_yuan: "学费原价（元）", current_season_recognition_rate_ppm: "当季结转比例", amount_yuan: "期初不含税结转（元）", detail_items: "明细折扣", composite_targets: "综合折扣目标", discount_rate_ppm: "明细折扣率", target_rate_ppm: "综合折扣率目标", period_type: "期间", rate_parameter_stable_key: "选择比例", rate_reference_id: "历史比例", quarter_name: "季节", rate_type: "指标", locked: "锁定状态", boundary_confirmed: "范围状态", created_at: "创建时间", cutoff_at: "快照日期", frozen_at: "冻结时间", dataset_name: "数据集", task_batch_id: "来源批次", partition_value: "分区", extracted_at: "抽取时间", row_count: "记录数", passed: "通过", status: "状态", components: "来源组件", reconciliations: "来源核对", integer_old_students: "老生人次", integer_new_students: "新生人次", integer_total_students: "总人次", quarter_recognized_revenue_yuan: "季度结转收入（元）", calculated_revenue_yuan: "计算结转收入（元）", variance_yuan: "与目标差额（元）", check_status: "校验状态", theoretical_old_students: "理论老生", theoretical_new_students: "理论新生", calculation_status: "计算状态", record_count: "明细数", publishable: "可发布", approval_status: "确认状态", confirmed: "已确认", error_code: "问题代码", error_message: "问题说明", batch_content_hash: "整批指纹",
};
export const meanings: Record<string, string> = { ENTRY: "入口", NON_ENTRY: "非入口", DEPARTMENT: "部门合计", TRACK: "赛道", ANNUAL: "全年", SEASON: "季度", INCLUDE: "人工纳入", EXCLUDE: "人工排除", PERFORMANCE_JUDGMENT: "业绩判断", CLASSROOM_RESOURCE: "教室资源", SALES_RESOURCE: "销售资源", NEW_CAMPUS_OR_PRODUCT: "新校区或新产品", STRATEGIC_ADJUSTMENT: "战略调整", OTHER: "其他", RENEWAL_RATE: "续班率", NEW_STUDENT_SHARE: "新生占比", FROZEN: "已冻结", DRAFT: "草稿", PASSED: "通过", FAILED: "未通过", READY: "就绪", BLOCKED: "有阻断", SUCCEEDED: "完成", QUEUED: "排队中", RUNNING: "计算中", INCOMPLETE: "未完成" };
Object.assign(labels, { reservation_ratio_ppm: "人工预留比例", field_mapping_version: "字段口径版本", metric_code: "指标", left_source: "核对来源一", right_source: "核对来源二", left_numerator: "来源一分子", left_denominator: "来源一分母", right_numerator: "来源二分子", right_denominator: "来源二分母", checked_at: "核对时间", suggested_entry_value: "入口建议人次", initial_entry_value: "入口初始人次", current_entry_value: "当前入口人次", current_value: "当前人次", class_size: "最低班均", minimum_class_size: "最低班均", gap_to_minimum: "距班均缺口", next_season_carryover_revenue_yuan: "转入下季收入（元）", current_season_revenue_yuan: "当季收入（元）", tolerance_yuan: "允许差额（元）", handling_status: "处理状态", error_detail: "问题说明" });
Object.assign(meanings, { roster: "花名册", summary: "指标汇总宽表", NEW_STUDENT_SHARE_REFERENCE: "新生占比参考", RENEWAL_RATE_REFERENCE: "续班率参考", UNDER_MINIMUM: "低于班均", NOT_PLANNED: "未计划开班" });
const options: Record<string, string[]> = { entry_attribute: ["ENTRY", "NON_ENTRY"], scope_level: ["DEPARTMENT", "TRACK"], period_type: ["ANNUAL", "SEASON"], exception_type: ["INCLUDE", "EXCLUDE"] };
const hidden = (key: string) => (key.endsWith("_id") && key !== "task_batch_id") || key.endsWith("_hash") || key === "opening_carryover_revenue_yuan" || key.endsWith("_stable_key");
export function resolve(schema: Schema, root: Schema): Schema {
  if (schema.$ref) return resolve(root.$defs[schema.$ref.split("/").pop()], root);
  if (schema.anyOf) return resolve(schema.anyOf.find((s: Schema) => s.type === "string") ?? schema.anyOf.find((s: Schema) => s.type !== "null"), root);
  return schema;
}
function empty(schema: Schema, root: Schema): Value {
  if ("default" in schema) return schema.default;
  schema = resolve(schema, root);
  if ("default" in schema) return schema.default;
  if (schema.type === "object") return Object.fromEntries(Object.entries(schema.properties ?? {}).filter(([k]) => !hidden(k)).map(([k, s]) => [k, empty(s as Schema, root)]));
  if (schema.type === "array") return [];
  if (schema.type === "boolean") return false;
  return "";
}
export function rowTitle(value: Value, index: number, choices: Record<string, string>): string {
  if (!value || typeof value !== "object" || Array.isArray(value)) return `第 ${index + 1} 项`;
  const id = value.rate_reference_id ?? value.rate_parameter_stable_key;
  if (typeof id === "string" && choices[id]) return choices[id];
  const title = [value.fiscal_season, value.campus, value.track, value.product, value.grade, value.subject, value.scope_level, value.period_type].filter(v => v !== null && v !== undefined && v !== "").map(v => meanings[String(v)] ?? String(v)).join(" / ");
  return title || `第 ${index + 1} 项`;
}
export function BusinessForm({ schema, value, onChange, root = schema, name = "", required = false, choices = {}, fixedArrays = false, readOnlyKeys = [] }: { schema: Schema; value: Value; onChange: (v: Value) => void; root?: Schema; name?: string; required?: boolean; choices?: Record<string, string>; fixedArrays?: boolean; readOnlyKeys?: string[] }) {
  const [page, setPage] = useState(0);
  const [query, setQuery] = useState("");
  const [editing, setEditing] = useState("");
  const [focused, setFocused] = useState(false);
  const nullable = schema.anyOf?.some((s: Schema) => s.type === "null");
  const s = resolve(schema, root);
  const label = labels[name] ?? name;
  if (["rate_parameter_stable_key", "rate_reference_id"].includes(name)) return <label className="trial-field">{label}<select value={String(value ?? "")} onChange={e => onChange(e.target.value)}><option value="">请选择要操作的比例</option>{Object.entries(choices).filter(([id]) => name === "rate_parameter_stable_key" ? id.length === 64 : id.length === 36).map(([id, text]) => <option value={id} key={id}>{text}</option>)}</select></label>;
  if (hidden(name)) return null;
  if (s.type === "array") {
    const rows = Array.isArray(value) ? value : [];
    const filtered = rows.map((v, i) => ({ v, i })).filter(({ v, i }) => rowTitle(v, i, choices).includes(query));
    const currentPage = Math.min(page, Math.max(0, Math.ceil(filtered.length / 12) - 1));
    return <section className="trial-array"><div className="trial-array-heading"><h3>{label} <small>{rows.length} 项</small></h3>{!fixedArrays && <button type="button" onClick={() => { onChange([...rows, empty(s.items, root)]); setQuery(""); setPage(Math.floor(rows.length / 12)); }}>新增明细</button>}</div>
      {rows.length > 0 && <input aria-label={`筛选${label}`} placeholder="按校区、产品或年级筛选" value={query} onChange={e => { setQuery(e.target.value); setPage(0); }} />}
      {filtered.slice(currentPage * 12, currentPage * 12 + 12).map(({ v, i }) => <details className="trial-record" key={i}><summary>{rowTitle(v, i, choices)}</summary><BusinessForm schema={s.items} root={root} value={v} choices={choices} fixedArrays={fixedArrays} readOnlyKeys={readOnlyKeys} onChange={next => onChange(rows.map((old, j) => j === i ? next : old))} />{!fixedArrays && <button type="button" className="trial-remove" onClick={() => onChange(rows.filter((_, j) => j !== i))}>删除此项</button>}</details>)}
      {filtered.length > 12 && <div className="trial-pager"><button type="button" disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}>上一页</button><span>{currentPage + 1} / {Math.ceil(filtered.length / 12)}</span><button type="button" disabled={(currentPage + 1) * 12 >= filtered.length} onClick={() => setPage(currentPage + 1)}>下一页</button></div>}
    </section>;
  }
  if (s.type === "object") {
    const object = (value && typeof value === "object" && !Array.isArray(value) ? value : {}) as Record<string, Value>;
    const properties = s.properties ?? Object.fromEntries([...new Set(["reference", ...Object.keys(object)])].map(k => [k, { type: typeof object[k] === "number" ? "integer" : "string" }]));
    return <div className={name ? "trial-subform" : "trial-fields"}>{name && <h4>{label}</h4>}{Object.entries(properties).map(([k, field]) => <BusinessForm key={k} name={k} schema={field as Schema} root={root} value={object[k] ?? null} choices={choices} fixedArrays={fixedArrays} readOnlyKeys={readOnlyKeys} required={s.required?.includes(k)} onChange={next => { const updated = { ...object, [k]: next }; if (next === "" && !(s.required ?? []).includes(k)) delete updated[k]; onChange(updated); }} />)}</div>;
  }
  const enums = s.enum ?? options[name];
  if (enums) return <label className="trial-field">{label}{required && " *"}<select value={String(value ?? "")} onChange={e => onChange(e.target.value || null)}><option value="">{nullable ? "不适用 / 留空" : "请选择"}</option>{enums.map((v: string) => <option key={v} value={v}>{meanings[v] ?? v}</option>)}</select></label>;
  if (s.type === "boolean") return <label className="trial-check"><input type="checkbox" checked={value === true} onChange={e => onChange(e.target.checked)} />{label}</label>;
  const ppm = name.endsWith("_ppm");
  const numeric = s.type === "integer" || s.type === "number" || ppm;
  const rawDisplay = String(value ?? "");
  const display = ppm && typeof value === "number" ? String(value / 10000) : /_yuan$/.test(name) && /^-?\d+\.\d+$/.test(rawDisplay) ? rawDisplay.replace(/\.0+$/, "").replace(/(\.\d*?)0+$/, "$1") : rawDisplay;
  const update = (text: string) => {
    if (text === "") return onChange(nullable ? null : "");
    if (ppm) {
      // Exact decimal-to-ppm parsing: do not round away an unsupported step.
      const match = /^(\d+)(?:\.(\d{0,4}))?$/.exec(text);
      return onChange(match ? Number(match[1]) * 10000 + Number((match[2] ?? "").padEnd(4, "0")) : text);
    }
    onChange(numeric ? (/^-?\d+$/.test(text) && Number.isSafeInteger(Number(text)) ? Number(text) : text) : text);
  };
  const long = /reason|reference|说明|依据/.test(name + label);
  return <label className={`trial-field ${long ? "trial-field-wide" : ""}`}>{label}{ppm && "（%）"}{required && " *"}
    {long ? <textarea rows={2} value={display} maxLength={s.maxLength ?? 500} readOnly={readOnlyKeys.includes(name)} onChange={e => update(e.target.value)} /> : <input type="text" inputMode={numeric ? "decimal" : "text"} value={focused ? editing : display} readOnly={readOnlyKeys.includes(name)} placeholder={nullable ? "不适用时留空" : "请填写"} onFocus={() => { setEditing(display); setFocused(true); }} onBlur={() => setFocused(false)} onChange={e => { setEditing(e.target.value); update(e.target.value); }} />}
  </label>;
}

export function ReadTable({ rows }: { rows: Record<string, Value>[] }) {
  const [query, setQuery] = useState(""); const [page, setPage] = useState(0);
  if (!rows.length) return <p>暂无明细。</p>;
  const hiddenResultKeys = new Set(["current_season_revenue_yuan", "next_season_carryover_revenue_yuan"]);
  const keys = [...new Set(rows.flatMap(r => Object.keys(r)))].filter(k => !hidden(k) && !hiddenResultKeys.has(k) && rows.some(r => r[k] !== null && typeof r[k] !== "object"));
  const filtered = rows.filter(r => keys.some(key => typeof r[key] !== "object" && String(r[key]).includes(query)));
  const n = Math.min(page, Math.max(0, Math.ceil(filtered.length / 25) - 1));
  const displayValue = (key: string, cell: Value) => {
    if (cell === null || cell === undefined) return "—";
    if (typeof cell === "boolean") return cell ? "是" : "否";
    if (typeof cell === "number" && key.endsWith("_ppm")) return `${cell / 10000}%`;
    const text = meanings[String(cell)] ?? String(cell);
    if (/_yuan$/.test(key) && /^-?\d+(?:\.\d+)?$/.test(text)) return `${new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 }).format(Number(text))} 元`;
    if (/_at$/.test(key) && !Number.isNaN(new Date(text).getTime())) return new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(text));
    return text;
  };
  const transfer = { ...tableExportData(keys.map(key => labels[key] ?? key), filtered.map(row => keys.map(key => displayValue(key, row[key]).replace(/ 元$/, "")))), filename: "结果明细" };
  const numericKeys = new Set(keys.filter(key => !/(_id|_hash)$/.test(key) && rows.some(row => typeof row[key] === "number" || typeof row[key] === "string" && /^-?\d+(?:\.\d+)?$/.test(row[key] as string))));
  const toolbar = <label className="trial-read-search"><span>筛选结果明细</span><input aria-label="筛选结果明细" placeholder="筛选校区、科目或状态" value={query} onChange={e => { setQuery(e.target.value); setPage(0); }} /></label>;
  return <><ActionTable exportData={transfer} keyboardNavigation tableLabel="结果明细" aria-label="结果明细表" toolbarStart={toolbar} scrollClassName="trial-table" scrollLabel="结果明细表，可横向滚动"><thead><tr>{keys.map(k => <th scope="col" className={numericKeys.has(k) ? "budget-cell-number" : undefined} key={k}>{labels[k] ?? k}</th>)}</tr></thead><tbody>{filtered.slice(n * 25, (n + 1) * 25).map((r, i) => <tr key={i}>{keys.map(k => <td className={numericKeys.has(k) ? "budget-cell-number" : undefined} key={k}>{displayValue(k, r[k])}</td>)}</tr>)}</tbody></ActionTable><div className="trial-pager"><button disabled={n === 0} onClick={() => setPage(n - 1)}>上一页</button><span>{filtered.length} 条 · 第 {n + 1} 页</span><button disabled={(n + 1) * 25 >= filtered.length} onClick={() => setPage(n + 1)}>下一页</button></div></>;
}
