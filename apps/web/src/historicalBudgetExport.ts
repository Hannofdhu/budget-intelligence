import type { ExportColumn } from "./budgetExport";

const common: ExportColumn[] = [
  { key: "track", label: "赛道" }, { key: "campus", label: "校区" },
  { key: "product", label: "产品" }, { key: "grade", label: "年级" }, { key: "subject", label: "科目" },
];
const season: ExportColumn = { key: "season", label: "季度" };
const students: ExportColumn[] = [
  { key: "old", label: "老生人次", format: "count" }, { key: "new", label: "新生人次", format: "count" },
  { key: "total", label: "总人次", format: "count" },
];
const revenue: ExportColumn = { key: "revenue", label: "预算结转收入（不含税，元）", format: "money" };
export function historicalExportColumns(step: number): ExportColumn[] {
  if (step === 1) return [...common, { key: "entry", label: "是否入口" }, { key: "annual", label: "全年结转收入（不含税，元）", format: "money" }];
  if (step === 2) return [...common, { key: "new", label: "暑假入口新生人次", format: "count" }];
  if (step === 3) return [...common, season, { key: "renewal", label: "续班率", format: "rate" }, { key: "share", label: "新生占比", format: "rate" }, ...students];
  if (step === 4) return [...common, season,
    { key: "minimum", label: "班均", format: "count" }, { key: "price", label: "学费原价（元）", format: "money" },
    { key: "discount", label: "折扣率", format: "rate" }, { key: "recognition", label: "当季确认比例", format: "rate" },
    { key: "business", label: "业务收入（含税，元）", format: "money" }, revenue];
  return [...common, season, ...students, revenue];
}
