import { BudgetQuarterTable } from "./BudgetQuarterTable";
import type { BudgetDimension, BudgetFact } from "./budgetAnalysisTypes";

type Props = {
  facts: BudgetFact[];
  unit: "yuan" | "wan";
  fiscalYear: number;
  onSelect: (path: Partial<Record<BudgetDimension, string>>, label: string) => void;
  onFilterCampus: (campus: string) => void;
  onPivot: () => void;
};
const rowDimensions = ["campus"] as const;
const metrics = [
  { key: "total", label: "总人次" }, { key: "new", label: "新生人次" },
  { key: "old", label: "老生人次" }, { key: "revenue", label: "结转收入" },
] as const;

export function BudgetCampusBoard({ onFilterCampus, onPivot, ...props }: Props) {
  return <BudgetQuarterTable {...props} rowDimensions={rowDimensions} metrics={metrics}
    title="校区招生预算" tableLabel="校区季度招生预算表" tableClassName="ba-campus-budget-table"
    onRowSelect={path => { if (path.campus) onFilterCampus(path.campus); }}
    headerActions={<div className="ba-toolbar"><button type="button" onClick={onPivot}>透视查看</button></div>} />;
}
