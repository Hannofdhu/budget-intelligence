import { BudgetQuarterTable } from "./BudgetQuarterTable";
import type { BudgetDimension, BudgetFact } from "./budgetAnalysisTypes";

type Props = {
  facts: BudgetFact[];
  unit: "yuan" | "wan";
  fiscalYear: number;
  onSelect: (path: Partial<Record<BudgetDimension, string>>, label: string) => void;
};
const rowDimensions = ["track", "subject"] as const;
const metrics = [{ key: "total", label: "招生人次" }, { key: "revenue", label: "结转收入" }] as const;

export function BudgetTrackSubjectBoard(props: Props) {
  return <BudgetQuarterTable {...props} rowDimensions={rowDimensions} metrics={metrics}
    title="赛道科目招生预算" tableLabel="赛道科目季度招生预算表" tableClassName="ba-track-subject-table" />;
}
