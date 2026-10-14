/** Presentation labels only. Fiscal-season keys, filters and calculations stay unchanged. */
/** Old saved messages retain their original content; only their rendered terminology changes. */
export function skuText(value: string): string {
  return value.replaceAll("业务范围", "SKU");
}

export function renewalStageLabel(season: string): string {
  return ({ 暑假: "春续暑", 秋季: "暑续秋", 寒假: "秋续寒", 春季: "寒续春" } as Record<string, string>)[season] ?? season;
}

export function budgetPeriodLabel(period: string, metric: string): string {
  return metric === "renewal" ? renewalStageLabel(period) : period;
}

const gradeRanks = new Map([
  "一年级", "二年级", "三年级", "四年级", "五年级", "六年级", "七年级", "八年级", "九年级", "高一", "高二", "高三",
].map((grade, index) => [grade, index]));
const gradeRank = (grade: string) => gradeRanks.get(grade.replace(/高([一二三])年级$/, "高$1")) ?? Number.POSITIVE_INFINITY;
const gradeCollator = new Intl.Collator("zh-CN", { numeric: true });

export function compareBudgetGrades(left: string, right: string): number {
  const leftRank = gradeRank(left), rightRank = gradeRank(right);
  return leftRank === rightRank ? gradeCollator.compare(left, right) : leftRank < rightRank ? -1 : 1;
}
