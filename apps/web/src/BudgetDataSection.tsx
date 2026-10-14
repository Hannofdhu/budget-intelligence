import type { ComponentProps, ReactNode } from "react";
import "./budgetDataSection.css";

type Props = Omit<ComponentProps<"section">, "id" | "title" | "children"> & {
  id: string;
  title: string;
  count: number;
  actions?: ReactNode;
  children: ReactNode;
};

/** Presentation only. Data, permissions, transfers and edits stay with existing owners. */
export function BudgetDataSection({ id, title, count, actions, children, className = "", ...props }: Props) {
  return <section {...props} id={id} className={`budget-data-section${className ? ` ${className}` : ""}`} aria-labelledby={`${id}-title`}>
    <header className="budget-data-section__heading">
      <div className="budget-data-section__identity"><h2 id={`${id}-title`} className="budget-data-section__title">{title}</h2>
        <span className="budget-data-section__count">{count.toLocaleString("zh-CN")} 条 SKU</span></div>
      {actions && <div className="budget-data-section__actions">{actions}</div>}
    </header>
    {children}
  </section>;
}
