import type { MouseEvent, ReactNode } from "react";
import { BudgetBrand } from "./BudgetBrand";
import { navigateBudgetModule } from "./budgetNavigation";
import { observeBudgetChrome } from "./BudgetPageNavigation";
import { aiBudgetHref, type AiBudgetVersion } from "./aiBudgetContext";
import { budgetModules, budgetModuleName, budgetModuleTitle, budgetModuleYears, fiscalYearLabel, type BudgetModuleYears } from "./budgetModules";
import "./fy27Navigation.css";

export function FY27Header({ active, children, fiscalYear, moduleFiscalYears, onHome, onBeforeLeave, sourceVersion }: {
  active: "cockpit" | "budget" | "forecast" | "next-year" | "ai";
  children: ReactNode;
  fiscalYear?: number;
  moduleFiscalYears?: Partial<BudgetModuleYears>;
  onHome?: () => void;
  onBeforeLeave?: (event: MouseEvent<HTMLAnchorElement>) => void;
  sourceVersion?: AiBudgetVersion | null;
}) {
  const years = budgetModuleYears({ ...moduleFiscalYears, ...(active !== "ai" && active !== "cockpit" && fiscalYear !== undefined ? { [active]: fiscalYear } : {}) });
  const aiHref = () => {
    if (typeof location === "undefined") return "/budget/ai";
    try { return aiBudgetHref(`${location.pathname ?? new URL(location.href).pathname}${location.search ?? ""}`, sourceVersion); }
    catch { return "/budget/ai"; }
  };
  const refreshAiHref = (anchor: HTMLAnchorElement | undefined) => { if (anchor) anchor.href = aiHref(); };
  const brand = <BudgetBrand />;
  return <header className="decision-topbar fy27-topbar" ref={node => observeBudgetChrome(node, "--budget-header-height")}>
    {onHome ? <button className="decision-brand" onClick={onHome}>{brand}</button>
      : <a className="decision-brand" href={budgetModules[0].href} onClick={event => { onBeforeLeave?.(event); navigateBudgetModule(event); }}>{brand}</a>}
    <nav className="fy27-module-switch" aria-label="预算模块">
      {budgetModules.map(module => <a key={module.key} href={module.key === "ai" && active !== "ai" ? aiHref() : module.href}
        aria-current={active === module.key ? "page" : undefined}
        aria-label={module.key === "cockpit" ? "预算驾驶舱" : module.key === "ai" ? "AI智解" : budgetModuleName(module.key, years[module.key])}
        onPointerDown={module.key === "ai" && active !== "ai" ? event => refreshAiHref(event.currentTarget) : undefined}
        onFocus={module.key === "ai" && active !== "ai" ? event => refreshAiHref(event.currentTarget) : undefined}
        onClick={event => { if (active === module.key) event.preventDefault(); else { if (module.key === "ai") refreshAiHref(event.currentTarget); onBeforeLeave?.(event); navigateBudgetModule(event); } }}>
        {module.key === "cockpit" ? <><span>预算</span><span>驾驶舱</span></> : module.key === "ai" ? "AI智解" : <><span className="fy27-module-year">{fiscalYearLabel(years[module.key])}</span><span className="fy27-module-title">{budgetModuleTitle(module.key)}</span></>}
      </a>)}
    </nav>
    <div className="fy27-header-context"><span className="fy27-year-label">{active === "cockpit" ? "管理视图" : active === "ai" ? "产品助手" : `${fiscalYearLabel(years[active])} 学校版`}</span>{children}</div>
  </header>;
}
