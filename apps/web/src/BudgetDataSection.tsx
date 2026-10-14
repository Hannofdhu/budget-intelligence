import type { ComponentProps, ReactNode } from "react";
import "./budgetDataSection.css";

type Props = Omit<ComponentProps<"section">, "id" | "title" | "children"> & {
  id: string;
  title: string;
  count?: number;
  countLabel?: string;
  headingLevel?: 2 | 3;
  description?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
};

/** Presentation only. Data, permissions, transfers and edits stay with existing owners. */
export function BudgetDataSection({ id, title, count, countLabel = "条 SKU", headingLevel = 2, description, actions, children, className = "", ...props }: Props) {
  const Heading = headingLevel === 3 ? "h3" : "h2";
  return <section {...props} id={id} className={`budget-data-section${className ? ` ${className}` : ""}`} aria-labelledby={`${id}-title`}>
    <header className="budget-data-section__heading">
      <div className="budget-data-section__identity"><Heading id={`${id}-title`} className="budget-data-section__title">{title}</Heading>
        {count !== undefined && <span className="budget-data-section__count">{count.toLocaleString("zh-CN")} {countLabel}</span>}</div>
      {actions && <div className="budget-data-section__actions">{actions}</div>}
    </header>
    {description && <p className="budget-data-section__description">{description}</p>}
    {children}
  </section>;
}
