import type { ReactNode } from "react";
import type { NextYearBulkScope } from "./nextYearBulk";

export type NextYearBulkPanelProps = {
  scope: NextYearBulkScope;
  field: string;
  value: string;
  paste: string;
  mode: "uniform" | "paste";
  disabled: boolean;
  error?: string;
  onFieldChange: (field: string) => void;
  onValueChange: (value: string) => void;
  onPasteChange: (value: string) => void;
  onModeChange: (mode: "uniform" | "paste") => void;
  onApply: () => void;
  onCopyTemplate: () => void;
};

/** Presentation only: the page owns edits, validation, feedback, preview and saving. */
export function renderNextYearBulkPanel(props: NextYearBulkPanelProps): ReactNode {
  const { scope, value, paste, disabled } = props;
  const selectedField = scope.fields.find(item => item.key === props.field) ?? scope.fields[0];
  if (!selectedField) return null;
  const mode = selectedField.action ? "uniform" : props.mode;
  const headings = scope.template[0] ?? [];
  const matchingColumns = scope.identityHeaders;
  const referenceColumns = scope.referenceHeaders;
  const editableColumns = scope.editableHeaders;
  const valueLabel = selectedField.options ? "设为" : `设为${selectedField.unit ? `（${selectedField.unit}）` : ""}`;
  const error = props.error || scope.validationError;
  const cannotApply = disabled || Boolean(scope.validationError) || scope.applicableCount === 0 || (!selectedField.action && !(mode === "paste" ? paste : value).trim());
  const changeField = (field: string) => {
    props.onFieldChange(field);
    if (scope.fields.find(item => item.key === field)?.action) props.onModeChange("uniform");
  };

  return <section className="ny-bulk-panel ny-bulk-editor" aria-label={`${scope.label}批量修改`}>
    <p className="ny-bulk-scope">{scope.scopeLabel}</p>
    <p className="ny-bulk-counts">
      <span>当前筛选 {scope.candidateCount} 条</span>
      <span>{scope.applicableCount} 条适用</span>
      {scope.skippedCount > 0 && <span>{scope.skippedCount} 条不适用</span>}
    </p>
    {scope.scopeNote && <p className="ny-bulk-scope-note">{scope.target === "class" ? <strong>{scope.scopeNote}</strong> : scope.scopeNote}</p>}
    {scope.notes.length > 0 && <ul className="ny-bulk-notes">{scope.notes.map((note, index) => <li key={`${index}-${note}`}>{note}</li>)}</ul>}

    {!selectedField.action && <div className="ny-tool-modes ny-bulk-modes" role="group" aria-label="批量修改方式">
      <button type="button" aria-pressed={mode === "uniform"} disabled={disabled} onClick={() => props.onModeChange("uniform")}>统一设值</button>
      <button type="button" aria-pressed={mode === "paste"} disabled={disabled} onClick={() => props.onModeChange("paste")}>分别设值（Excel）</button>
    </div>}

    <div className="ny-bulk-toolbar ny-bulk-controls">
      {!(mode === "paste" && scope.target === "rates") && <label>修改字段<select aria-label="批量修改字段" value={selectedField.key} disabled={disabled} onChange={event => changeField(event.target.value)}>
        {scope.fields.map(item => <option key={item.key} value={item.key}>{item.label}</option>)}
      </select></label>}
      {mode === "uniform" && !selectedField.action && <label>{valueLabel}
        {selectedField.options
          ? <select aria-label={`批量修改${selectedField.label}`} value={value} disabled={disabled} onChange={event => props.onValueChange(event.target.value)}>
            <option value="">请选择</option>
            {selectedField.options.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}
          </select>
          : <input aria-label={selectedField.unit === "%" ? "批量修改比例" : `批量修改${selectedField.label}`} value={value} inputMode={selectedField.unit === "人次" ? "numeric" : "decimal"} disabled={disabled} onChange={event => props.onValueChange(event.target.value)} />}
      </label>}
      {mode === "uniform" && <button type="button" disabled={cannotApply} onClick={props.onApply}>应用到当前筛选</button>}
    </div>

    {mode === "uniform" && selectedField.unit === "%" && <p className="ny-bulk-value-help">例如 8 表示 8%。</p>}
    {selectedField.action && <p className="ny-bulk-action-help">恢复自动分配将清除当前筛选内手动指定的入口人数，按现有规则重新分配。</p>}

    {mode === "paste" && <div className="ny-bulk-paste">
      <p>复制含参考数据的编辑模板，在 Excel 中判断并填写，再粘贴回来。</p>
      <div className="ny-tool-entries"><button type="button" disabled={disabled || Boolean(scope.validationError) || scope.applicableCount === 0} onClick={props.onCopyTemplate}>复制编辑模板</button></div>
      <p id="ny-bulk-paste-help">匹配列：{matchingColumns.join("、")}，请勿修改。只编辑{editableColumns.map(column => `“${column}”`).join("、")}列；空值保持不变，可调整行顺序。</p>
      {referenceColumns.length > 0 && <>
        <p id="ny-bulk-reference-help">参考列仅供判断，粘贴时不会写入。</p>
        <details className="ny-bulk-reference-columns"><summary>查看参考列（{referenceColumns.length} 列）</summary><p>{referenceColumns.map(column => column.replace(/^参考[·・]\s*/, "")).join("、")}</p></details>
      </>}
      {selectedField.options && <p>填写：{selectedField.options.map(item => item.label).join(" / ")}。</p>}
      {selectedField.unit === "%" && <p>例如 8 表示 8%。</p>}
      <label className="ny-field ny-bulk-paste-field"><span>粘贴 Excel 表格（{headings.length} 列，保留表头）</span>
        <textarea aria-label={scope.target === "rates" ? "粘贴招生参数表格" : `粘贴${scope.label}表格`} aria-describedby={`ny-bulk-paste-help${referenceColumns.length ? " ny-bulk-reference-help" : ""}`} rows={7} value={paste} disabled={disabled} onChange={event => props.onPasteChange(event.target.value)} />
      </label>
      <div className="ny-tool-entries">
        <button type="button" disabled={cannotApply} onClick={props.onApply}>校验并应用</button>
        {paste && <button type="button" disabled={disabled} onClick={() => props.onPasteChange("")}>清空表格</button>}
      </div>
    </div>}
    {error && <p className="ny-bulk-error" role="alert">{error}</p>}
    <p className="ny-bulk-submit-help">应用后加入改动清单，查看影响和保存仍使用页面底部操作栏。</p>
  </section>;
}
