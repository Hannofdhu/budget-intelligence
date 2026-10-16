import { versionLabel, versionStatusLabel, versionTimestamp, versionUpdateLabel } from "./budgetVersionDisplay";
import "./budgetVersion.css";

/** Shared information order, independent of each module's approval/permission rules. */
export function BudgetVersionIdentity({ sequence, status, createdAt, reason, generation, latest = false, viewing = false, readonly = false }: {
  sequence: number; status?: string; createdAt?: string | null; reason?: string | null; generation?: string | null;
  latest?: boolean; viewing?: boolean; readonly?: boolean;
}) {
  return <span className="budget-version-identity">
    <strong><time dateTime={versionTimestamp(createdAt) === "未记录" ? undefined : createdAt ?? undefined}>保存于 {versionTimestamp(createdAt)}</time><span>{versionUpdateLabel(reason, generation)}</span></strong>
    <span className="budget-version-relations"><span>{versionLabel(sequence)}</span>{status && <span>{versionStatusLabel(status)}</span>}{latest && <span>最新保存</span>}{viewing && <span>正在查看</span>}{readonly && <span>只读</span>}</span>
  </span>;
}

export function BudgetVersionTrace({ code, id, createdAt, createdBy, confirmedAt, confirmedBy }: {
  code?: string | null; id?: string | null; createdAt?: string | null; createdBy?: string | null; confirmedAt?: string | null; confirmedBy?: string | null;
}) {
  return <details className="budget-version-trace"><summary>追溯信息</summary><dl>
    {code && <><dt>内部编号</dt><dd>{code}</dd></>}{id && <><dt>精确版本标识</dt><dd>{id}</dd></>}
    <dt>保存时间（北京时间）</dt><dd>{versionTimestamp(createdAt, true)}</dd>
    <dt>保存身份</dt><dd>{createdBy || "未记录"}</dd>
    {confirmedAt && <><dt>确认时间（北京时间）</dt><dd>{versionTimestamp(confirmedAt, true)}</dd><dt>确认身份</dt><dd>{confirmedBy || "未记录"}</dd></>}
  </dl></details>;
}
