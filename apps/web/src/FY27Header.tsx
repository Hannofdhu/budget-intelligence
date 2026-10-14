import type { MouseEvent, ReactNode } from "react";
import { observeBudgetChrome } from "./BudgetPageNavigation";
import "./fy27Navigation.css";

const modules = [
  { key: "budget", label: "查看预算", href: "/budget/workspace?archive=fy27&step=5" },
  { key: "forecast", label: "达成预测", href: "/budget/forecast?archive=fy27" },
  { key: "next-year", label: "下一年预算", href: "/budget/next-year" },
  { key: "ai", label: "AI智解", href: "/budget/ai" },
] as const;

export function FY27Header({ active, children, fiscalYear = active === "next-year" ? 2028 : 2027, onHome, onBeforeLeave }: {
  active: "budget" | "forecast" | "next-year" | "ai";
  children: ReactNode;
  fiscalYear?: number;
  onHome?: () => void;
  onBeforeLeave?: (event: MouseEvent<HTMLAnchorElement>) => void;
}) {
  const brand = <><span aria-hidden="true">智</span><strong>预算智解</strong></>;
  return <header className="decision-topbar fy27-topbar" ref={node => observeBudgetChrome(node, "--budget-header-height")}>
    {onHome ? <button className="decision-brand" onClick={onHome}>{brand}</button>
      : <a className="decision-brand" href={modules[0].href} onClick={onBeforeLeave}>{brand}</a>}
    <nav className="fy27-module-switch" aria-label="预算模块">
      {modules.map(module => <a key={module.key} href={module.href}
        aria-current={active === module.key ? "page" : undefined}
        onClick={event => { if (active === module.key) event.preventDefault(); else onBeforeLeave?.(event); }}>
        {module.label}
      </a>)}
    </nav>
    <div className="fy27-header-context"><span className="fy27-year-label">{active === "ai" ? "产品助手" : `FY${String(fiscalYear).slice(-2)} 学校版`}</span>{children}</div>
  </header>;
}
