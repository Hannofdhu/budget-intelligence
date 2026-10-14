import type { ReactNode } from "react";

type SkuIdentity = { campus: string; track: string; product: string; grade: string; subject: string };

export const skuIdentityHeaders = ["校区 / 赛道 / 产品", "年级 / 科目"] as const;

/** Identity only: each page retains ownership of its business columns and actions. */
export function skuIdentityCells(row: SkuIdentity, annotation?: ReactNode) {
  // Native sibling cells also let the existing transfer reader keep column boundaries.
  return [
    <th key="sku-location" scope="row"><strong>{row.campus}</strong><small>{row.track} / {row.product}</small>{annotation}</th>,
    <td key="sku-study">{row.grade}<small>{row.subject}</small></td>,
  ];
}
