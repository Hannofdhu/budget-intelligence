import { versionLabel } from "./budgetVersionDisplay";
import { BudgetBrand } from "./BudgetBrand";

interface BudgetContextHeaderProps {
  fiscalYear: number;
  revisionNo: number;
  revisionId: string;
  status: string;
}

export function BudgetContextHeader({ fiscalYear, revisionNo, revisionId, status }: BudgetContextHeaderProps) {
  return (
    <header className="decision-topbar">
      <a className="decision-brand" href={`/budget/workspace?revision=${encodeURIComponent(revisionId)}&section=home`} aria-label="返回年度预算首页">
        <BudgetBrand />
      </a>
      <div className="publication-topbar-title">FY{String(fiscalYear).slice(-2)} 学校版</div>
      <div className="decision-version"><span className={`status-dot ${status === "已定版" ? "is-published" : ""}`} /><span>{status}</span><small>{versionLabel(revisionNo)}</small></div>
    </header>
  );
}
