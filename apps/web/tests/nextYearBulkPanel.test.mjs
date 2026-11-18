import { test } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";

const require = createRequire(import.meta.url);
const compiled = await build({ entryPoints: [fileURLToPath(new URL("../src/NextYearBulkPanel.tsx", import.meta.url))],
  bundle: true, write: false, platform: "node", format: "cjs", jsx: "automatic", external: ["react"], logLevel: "silent" });
const module = { exports: {} };
new Function("require", "module", "exports", compiled.outputFiles[0].text)(require, module, module.exports);
const render = module.exports.renderNextYearBulkPanel;
const find = (node, predicate) => Array.isArray(node) ? node.flatMap(child => find(child, predicate)) : !node || typeof node !== "object" ? []
  : [...(predicate(node) ? [node] : []), ...find(node.props?.children, predicate)];
const text = node => Array.isArray(node) ? node.map(text).join("") : node && typeof node === "object" ? text(node.props?.children)
  : node == null || typeof node === "boolean" ? "" : String(node);
const base = {
  scope: { target: "sku", label: "SKU", scopeLabel: "当前筛选 · 全部匹配 SKU", scopeNote: "参与预算按整条 SKU 生效。",
    fields: [{ key: "enabled", label: "参与预算", options: [{ value: "true", label: "参与" }, { value: "false", label: "不参与" }] }],
    rows: [], candidateCount: 24, applicableCount: 22, skippedCount: 2, notes: ["有期初结转的 SKU 必须保留。"],
    identityHeaders: ["校区", "赛道", "产品", "年级", "科目"], referenceHeaders: [], editableHeaders: ["参与预算"],
    template: [["校区", "赛道", "产品", "年级", "科目", "参与预算"]] },
  field: "enabled", value: "false", paste: "", mode: "uniform", disabled: false,
  onFieldChange() {}, onValueChange() {}, onPasteChange() {}, onModeChange() {}, onApply() {}, onCopyTemplate() {},
};

test("boolean editing uses business options, full-filter scope and the existing apply flow", () => {
  let applied = 0;
  const tree = render({ ...base, onApply: () => applied++ });
  assert.match(text(tree), /当前筛选 24 条22 条适用2 条不适用/);
  assert.match(text(tree), /有期初结转的 SKU 必须保留/);
  const [valueSelect] = find(tree, node => node.type === "select" && node.props["aria-label"] === "批量修改参与预算");
  assert.deepEqual(find(valueSelect, node => node.type === "option").map(text), ["请选择", "参与", "不参与"]);
  const [apply] = find(tree, node => node.type === "button" && text(node) === "应用到当前筛选");
  assert.equal(apply.props.disabled, false, "false is a selected business value");
  apply.props.onClick();
  assert.equal(applied, 1);
  assert.equal(find(tree, node => node.type === "button" && /保存|查看影响/.test(text(node))).length, 0);
});

test("restoring automatic allocation is explicit, needs no numeric value and leaves no paste mode", () => {
  const changes = [];
  const scope = { ...base.scope, target: "entry", label: "SKU入口设置与分配", fields: [
    { key: "entry_students", label: "指定入口人数", unit: "人次" }, { key: "automatic", label: "恢复自动分配", action: true },
  ] };
  const original = render({ ...base, scope, field: "entry_students", onFieldChange: field => changes.push(field), onModeChange: mode => changes.push(mode) });
  find(original, node => node.type === "select" && node.props["aria-label"] === "批量修改字段")[0].props.onChange({ target: { value: "automatic" } });
  assert.deepEqual(changes, ["automatic", "uniform"]);
  const tree = render({ ...base, scope, field: "automatic", value: "", mode: "paste" });
  assert.equal(find(tree, node => node.type === "input" || node.type === "textarea").length, 0);
  assert.equal(find(tree, node => node.props?.["aria-label"] === "批量修改方式").length, 0);
  assert.match(text(tree), /恢复自动分配将清除当前筛选内手动指定的入口人数/);
  assert.equal(find(tree, node => node.type === "button" && text(node) === "应用到当前筛选")[0].props.disabled, false);
});

test("Excel mode explains exact independent identity columns and both original rate fields", () => {
  let copied = 0, pasted = "";
  const scope = { ...base.scope, target: "rates", label: "招生参数", fields: [{ key: "renewal_ppm", label: "续班率", unit: "%" }],
    identityHeaders: ["校区", "赛道", "产品", "年级", "季度"], referenceHeaders: ["参考·老生人次", "参考·新生人次"], editableHeaders: ["续班率（%）", "新生占比（%）"],
    template: [["校区", "赛道", "产品", "年级", "季度", "参考·老生人次", "参考·新生人次", "续班率（%）", "新生占比（%）"]] };
  const tree = render({ ...base, scope, field: "renewal_ppm", mode: "paste", paste: "a\tb", onCopyTemplate: () => copied++, onPasteChange: value => pasted = value });
  assert.match(text(tree), /匹配列：校区、赛道、产品、年级、季度/);
  assert.match(text(tree), /只编辑“续班率（%）”、“新生占比（%）”列；空值保持不变/);
  assert.match(text(tree), /9 列，保留表头/);
  const matchingHelp = find(tree, node => node.props?.id === "ny-bulk-paste-help")[0];
  assert.doesNotMatch(text(matchingHelp), /老生人次|新生人次/);
  assert.match(text(tree), /参考列仅供判断，粘贴时不会写入/);
  assert.match(text(tree), /查看参考列（2 列）/);
  assert.equal(find(tree, node => node.type === "select").length, 0);
  find(tree, node => node.type === "button" && text(node) === "复制编辑模板")[0].props.onClick();
  find(tree, node => node.type === "textarea")[0].props.onChange({ target: { value: "new" } });
  assert.equal(copied, 1); assert.equal(pasted, "new");
  find(tree, node => node.type === "button" && text(node) === "清空表格")[0].props.onClick();
  assert.equal(pasted, "");
});

test("SKU reference data is explained separately from identity and editable columns", () => {
  const referenceHeaders = ["参考·FY27结转收入（元）", "参考·FY27招生人次", "参考·FY27经营基础状态", "参考·FY28暑假老生人次", "参考·FY28老生承接状态", "参考·期初结转（元）", "参考·入口"];
  const scope = { ...base.scope, referenceHeaders, template: [[...base.scope.identityHeaders, ...referenceHeaders, ...base.scope.editableHeaders]] };
  const tree = render({ ...base, scope, mode: "paste" });
  const matchingHelp = find(tree, node => node.props?.id === "ny-bulk-paste-help")[0];
  assert.equal(text(matchingHelp), "匹配列：校区、赛道、产品、年级、科目，请勿修改。只编辑“参与预算”列；空值保持不变，可调整行顺序。");
  const [references] = find(tree, node => node.type === "details");
  assert.equal(references.props.open, undefined, "long reference names remain available without filling the editor with extra text");
  for (const label of ["FY27结转收入（元）", "FY27招生人次", "FY28暑假老生人次", "期初结转（元）"]) assert.ok(text(references).includes(label));
  assert.doesNotMatch(text(references), /参考·/);
  assert.match(text(tree), /13 列，保留表头/);
  const [textarea] = find(tree, node => node.type === "textarea");
  assert.equal(textarea.props["aria-describedby"], "ny-bulk-paste-help ny-bulk-reference-help");
  assert.equal(find(tree, node => node.type === "input").length, 0, "references never create editable controls");
  assert.equal(find(tree, node => node.type === "button" && /保存|查看影响/.test(text(node))).length, 0);
});

test("all bulk targets use declared reference roles rather than treating preceding columns as identity", () => {
  for (const target of ["rates", "finance", "bounds", "class", "plans", "sku", "entry"]) {
    const identityHeaders = target === "class" ? ["赛道", "产品"] : ["校区", "赛道", "产品", "年级", "科目"];
    const referenceHeaders = ["参考·当前值", "参考·结果状态"];
    const editableHeaders = target === "rates" ? ["续班率（%）", "新生占比（%）"] : ["修改值"];
    const tree = render({ ...base, mode: "paste", scope: { ...base.scope, target, identityHeaders, referenceHeaders, editableHeaders, template: [[...identityHeaders, ...referenceHeaders, ...editableHeaders]] } });
    const help = text(find(tree, node => node.props?.id === "ny-bulk-paste-help")[0]);
    assert.ok(help.startsWith(`匹配列：${identityHeaders.join("、")}，`), target);
    assert.doesNotMatch(help, /参考·|当前值|结果状态/, target);
    assert.match(text(tree), /参考列仅供判断，粘贴时不会写入/, target);
  }
  const withoutReferences = render({ ...base, mode: "paste" });
  assert.equal(find(withoutReferences, node => node.type === "details").length, 0);
});

test("no applicable rows and busy operations disable every mutation entry", () => {
  const noRows = render({ ...base, scope: { ...base.scope, applicableCount: 0 } });
  assert.equal(find(noRows, node => node.type === "button" && text(node) === "应用到当前筛选")[0].props.disabled, true);
  const busy = render({ ...base, disabled: true, mode: "paste", paste: "a\tb" });
  assert.equal(find(busy, node => ["button", "input", "select", "textarea"].includes(node.type)).every(node => node.props.disabled), true);
  const failed = render({ ...base, error: "存在期初结转，不能停用。" });
  assert.equal(text(find(failed, node => node.props?.role === "alert")[0]), "存在期初结转，不能停用。");
  const blocked = render({ ...base, scope: { ...base.scope, validationError: "当前筛选没有匹配到完整业务。" } });
  assert.match(text(blocked), /当前筛选没有匹配到完整业务/);
  assert.equal(find(blocked, node => node.type === "button" && text(node) === "应用到当前筛选")[0].props.disabled, true);
});
