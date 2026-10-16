/** Existing annual-budget (R) actions and pointers, for display only. */
const revisionActions: Record<string, string> = {
  CREATE_ANNUAL_BUDGET: "建立年度预算",
  DERIVE_BUDGET_REVISION: "派生预算草稿",
  REPLACE_TARGET_SET: "锁定学校版目标",
  GENERATE_COMBINATION_DRAFT: "生成预算组合",
  REPLACE_COMBINATION_DRAFT: "修改预算组合",
  CONFIRM_COMBINATION_SET: "确认预算组合",
  SAVE_AND_LOCK_ENTRY_TOTAL: "锁定入口总值",
  REPLACE_CAMPUS_ENTRY_DRAFT: "修改校区入口",
  CONFIRM_CAMPUS_ENTRY_SET: "确认校区入口",
  REPLACE_ENTRY_SCOPE_DRAFT: "修改入口范围",
  CONFIRM_ENTRY_SCOPE: "确认入口范围",
  REPLACE_RATE_PARAMETER_DRAFT: "修改比例参数",
  CHANGE_AND_LOCK_RATE_PARAMETERS: "修改并锁定比例",
  APPLY_REBALANCING_SCENARIO: "应用调平方案",
  FINALIZE_FORMAL_PLAN_CONFIRMATION: "正式方案确认",
  CALCULATE_HEADCOUNT_RESULT: "计算预算人数",
  CALCULATE_CLASS_SIZE_RESULT: "核对班均",
  CALCULATE_REVENUE_RESULT: "计算预算结转收入",
  VALIDATE_TARGET_CLOSURE: "核对结转收入目标",
  VALIDATE_CLASS_SIZE_RESULT: "核对班均",
};

export function revisionCommandLabel(command: string | null | undefined): string {
  if (!command) return "更新说明未记录";
  return revisionActions[command] ?? (/^(CALCULATE_|VALIDATE_)/.test(command) ? "系统计算" : "更新说明未记录");
}

export function revisionRelations(revision: { budget_revision_id: string; component_manifest: Record<string, unknown> },
  budget: { current_draft_revision_id: string | null; current_published_revision_id: string | null }, publishedIds: ReadonlySet<string> = new Set(), publicationKnown = true): { label: string; tone: string }[] {
  const statuses = [];
  const id = revision.budget_revision_id;
  if (budget.current_draft_revision_id === id && budget.current_published_revision_id !== id) statuses.push({ label: "当前草稿", tone: "draft" });
  if (budget.current_published_revision_id === id) statuses.push({ label: "当前定版", tone: "published" });
  else if (publishedIds.has(id)) statuses.push({ label: "曾定版", tone: "historic" });
  if (typeof revision.component_manifest.formal_confirmation_id === "string") statuses.push({ label: "已正式确认", tone: "confirmed" });
  if (!publicationKnown && !publishedIds.has(id) && budget.current_draft_revision_id !== id && budget.current_published_revision_id !== id) statuses.push({ label: "定版状态待核对", tone: "historic" });
  return statuses.length ? statuses : [{ label: "历史草稿", tone: "historic" }];
}
