/** Presentation only: never renumber versions or substitute an ID/source. */
export function versionLabel(sequence: number | null | undefined): string {
  return typeof sequence === "number" && Number.isSafeInteger(sequence) && sequence > 0 ? `第${sequence}版` : "版本序号未记录";
}

/** An as-of day or a timezone-less timestamp is not an authoritative save time. */
export function versionTimestamp(value: string | null | undefined, seconds = false): string {
  if (!value || !/T\d{2}:\d{2}.*(?:Z|[+-]\d{2}:?\d{2})$/i.test(value)) return "未记录";
  const day = /^(\d{4})-(\d{2})-(\d{2})T/.exec(value);
  if (!day || new Date(Date.UTC(Number(day[1]), Number(day[2]) - 1, Number(day[3]))).toISOString().slice(0, 10) !== value.slice(0, 10)) return "未记录";
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return "未记录";
  const parts = new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", ...(seconds ? { second: "2-digit" } : {}), hourCycle: "h23" }).formatToParts(date);
  const field = (key: string) => parts.find(part => part.type === key)?.value;
  return `${field("year")}-${field("month")}-${field("day")} ${field("hour")}:${field("minute")}${seconds ? `:${field("second")}` : ""}`;
}

export function versionStatusLabel(status: string | null | undefined): string {
  return ({ DRAFT: "草稿", CONFIRMED: "已确认", PUBLISHED: "已定版", FINALIZED: "已定版" } as Record<string, string>)[status ?? ""] ?? "状态未记录";
}

export function versionUpdateLabel(reason?: string | null, generation?: string | null): string {
  if (generation === "AUTOMATIC") return "自动更新";
  return reason?.trim() || (generation === "MANUAL" ? "手动更新" : "更新说明未记录");
}

/** Existing P/N/R codes remain the transport protocol; only their visible name changes. */
export function versionCodeLabel(code: string): string {
  const match = /^[PNR](\d{1,10})$/.exec(code);
  return match ? versionLabel(Number(match[1])) : code;
}

export interface VersionExportContext {
  fiscalYear: number;
  object: string;
  sequence?: number | null;
  createdAt?: string | null;
  preview?: boolean;
  id?: string | null;
  asOf?: string | null;
  sourceId?: string | null;
  sourceLabel?: string | null;
}
export function versionFileStem(context: VersionExportContext): string {
  const year = Number.isInteger(context.fiscalYear) ? `FY${String(context.fiscalYear).slice(-2)}` : "财年未记录";
  const saved = versionTimestamp(context.createdAt);
  return [year, context.object, ...(context.sequence == null ? [] : [versionLabel(context.sequence)]), saved === "未记录" ? "保存时间未记录" : saved.replace(" ", "_").replace(":", ""), ...(context.preview ? ["未保存预览"] : [])]
    .join("-").replace(/[\\/:*?"<>|\u0000-\u001f]/g, "-");
}
