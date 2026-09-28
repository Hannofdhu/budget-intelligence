import { useEffect, useMemo, useRef, useState } from "react";
import { BusinessForm, ReadTable, meanings, type Schema, type Value } from "./BusinessForm";
import { identityHeaders, newRequestId, parseResponse, fetchJob, fetchRevision, fetchAuditEvents, type ApiIdentity } from "./api";
import { RateTableEditor } from "./RateTableEditor";
import { BudgetResultsPage } from "./BudgetResultsPage";

type RecordValue = Record<string, Value>;
type Section = { key: string; label: string; schema: Schema; value: Value; data: RecordValue; path: string; confirm: { path: string; body: RecordValue } | null };
type Workspace = { revision_id: string; revision_no: number; fiscal_year: number; is_current_draft: boolean; is_published: boolean; manifest: Record<string, string>; source: RecordValue; sections: Section[] };
type Budget = { annual_budget_id: string; fiscal_year: number; draft_revision_id: string | null; published_revision_id: string | null };

const stages = [
  { key: "foundation", no: "01", label: "来源与范围", sections: ["source", "targets", "combinations"] },
  { key: "entry", no: "02", label: "入口拆解", sections: ["entry-total", "campus-suggestions", "campus", "entry-scope", "entry-detail"] },
  { key: "rates", no: "03", label: "比例参数", sections: ["rates", "planned", "planned-unlock"] },
  { key: "finance", no: "04", label: "资源与财务", sections: ["resources", "discount"] },
  { key: "calculate", no: "05", label: "计算与调平", sections: ["results"] },
  { key: "publish", no: "06", label: "确认与发布", sections: [] },
] as const;

const tasks = [
  { key: "foundation", no: "1", label: "目标与范围", short: "目标", primary: "source", sections: ["source", "targets", "combinations"] },
  { key: "entry", no: "2", label: "入口人数", short: "入口", primary: "entry-total", sections: ["entry-total", "campus-suggestions", "campus", "entry-scope", "entry-detail"] },
  { key: "rates", no: "3", label: "招生参数", short: "参数", primary: "rates", sections: ["rates", "planned", "planned-unlock"] },
  { key: "finance", no: "4", label: "资源与财务", short: "财务", primary: "resources", sections: ["resources", "discount"] },
  { key: "review", no: "5", label: "审阅与发布", short: "发布", primary: "results", sections: ["results"] },
] as const;

const sectionNames: Record<string, string> = {
  source: "数据来源", targets: "收入目标", combinations: "业务范围", "entry-total": "年度入口总量",
  "campus-suggestions": "校区分配建议", campus: "校区入口人数", "entry-scope": "入口范围例外", "entry-detail": "入口人数拆分",
  rates: "续班率与新生占比", planned: "计划新生人数", "planned-unlock": "调整计划新生",
  resources: "班均、价格与结转", discount: "折扣与综合目标", results: "计算结果",
};

const pageCopy: Record<string, { title: string; intro: string; primary?: string }> = {
  targets: { title: "收入目标", intro: "核对部门和各赛道的收入目标，确认无误后继续。", primary: "保存目标" },
  combinations: { title: "业务范围", intro: "确认哪些校区、产品、年级和科目参与本次预算。", primary: "保存业务范围" },
  "entry-total": { title: "年度入口总量", intro: "确定本财年的入口总人次，后续只拆分结构，不改变总量。", primary: "保存入口总量" },
  "campus-suggestions": { title: "校区分配建议", intro: "根据历史表现和已维护资源生成各校区建议值。", primary: "生成校区建议" },
  campus: { title: "校区入口人数", intro: "核对各校区确定值，校区合计必须等于年度入口总量。", primary: "保存校区人数" },
  "entry-scope": { title: "入口范围例外", intro: "默认规则之外的产品、校区或年级在这里逐项指定。", primary: "保存例外" },
  "entry-detail": { title: "入口人数拆分", intro: "按已确认的校区总量和历史结构拆分至完整预算明细。", primary: "拆分入口人数" },
  rates: { title: "续班率与新生占比", intro: "直接比较历史参考、当前值和允许范围，可筛选或批量修改。", primary: "保存比例" },
  planned: { title: "计划新生人数", intro: "仅在需要人工指定绝对人数时填写，不参与自动调平。", primary: "保存计划人数" },
  "planned-unlock": { title: "调整计划新生", intro: "解锁后可以修改计划人数，相关下游结果需要重新计算。", primary: "解锁选中项" },
  resources: { title: "班均、价格与结转", intro: "维护班均、价格和期初结转；结转金额按不含税口径填写。", primary: "保存资源与财务参数" },
  discount: { title: "折扣与综合目标", intro: "维护明细折扣和综合折扣目标，系统会按精确值校验。", primary: "保存折扣参数" },
};

const resultRoutes: Record<string, string> = { "入口分配": "entry-detail-set", "人数": "headcount-result", "班均": "class-size-result", "收入": "revenue-result", "目标差额": "target-closure-result" };

function taskFor(section: string) { return tasks.find(task => task.sections.some(key => key === section)) ?? tasks[0]; }
function taskTarget(workspace: Workspace, task: typeof tasks[number], next: ReturnType<typeof nextStep> | null): string {
  if (taskState(workspace, task.key) !== "done" && next?.section && task.sections.some(key => key === next.section)) return next.section;
  return task.primary;
}
function sectionData(workspace: Workspace, key: string): RecordValue { return workspace.sections.find(section => section.key === key)?.data ?? {}; }
const sectionPointers: Record<string, string> = {
  targets: "target_set_version_id", combinations: "combination_set_version_id", "entry-total": "entry_total_version_id",
  "campus-suggestions": "campus_entry_suggestion_version_id", campus: "campus_entry_set_version_id", "entry-scope": "entry_scope_version_id",
  "entry-detail": "entry_detail_set_version_id", rates: "rate_parameter_version_id", planned: "planned_new_student_version_id",
  resources: "resource_finance_parameter_version_id", discount: "discount_parameter_version_id",
};
function hasSavedSection(workspace: Workspace, key: string): boolean {
  if (key === "campus-suggestions") return Boolean(workspace.manifest.campus_entry_suggestion_version_id || workspace.manifest.campus_entry_set_version_id);
  const pointer = sectionPointers[key];
  return Boolean(pointer && workspace.manifest[pointer]);
}
function sectionStatus(workspace: Workspace, section: Section, dirty: boolean, confirmed: boolean): string {
  if (confirmed) return "已确认";
  if (dirty) return "有修改";
  if (hasSavedSection(workspace, section.key)) {
    if (["campus-suggestions", "entry-detail"].includes(section.key)) return "已生成";
    if (section.key === "entry-total" && typeof section.data.fiscal_year_entry_total === "number") return `已保存 · ${section.data.fiscal_year_entry_total} 人次`;
    if (typeof section.data.record_count === "number") return `已保存 · ${section.data.record_count} 项`;
    if (section.key === "resources") {
      const count = ["class_size_record_count", "revenue_record_count", "opening_carryover_record_count"].reduce((sum, key) => sum + (typeof section.data[key] === "number" ? Number(section.data[key]) : 0), 0);
      return `已保存 · ${count} 项`;
    }
    if (section.key === "discount") {
      const count = ["detail_record_count", "target_record_count"].reduce((sum, key) => sum + (typeof section.data[key] === "number" ? Number(section.data[key]) : 0), 0);
      return `已保存 · ${count} 项`;
    }
    return "已保存";
  }
  if (["campus-suggestions", "entry-detail"].includes(section.key)) return "待生成";
  if (["entry-scope", "planned", "planned-unlock"].includes(section.key)) return "按需处理";
  return "待填写";
}
function isConfirmed(workspace: Workspace, key: string): boolean {
  const data = sectionData(workspace, key);
  if (data.confirmed === true || data.boundary_confirmed === true || data.approval_status === "CONFIRMED") return true;
  if (key === "targets") return data.lock_status === "LOCKED";
  if (key === "rates") return typeof data.record_count === "number" && data.record_count > 0 && data.confirmed_boundary_count === data.record_count;
  return Boolean(data.confirmation_id || data.confirmed_at);
}
function isSectionComplete(workspace: Workspace, key: string): boolean {
  if (["combinations", "campus", "entry-scope", "rates", "resources", "discount"].includes(key)) return isConfirmed(workspace, key);
  return hasSavedSection(workspace, key);
}
function sectionBlocker(workspace: Workspace, key: string): { text: string; section: string } | null {
  if (key !== "entry-detail") return null;
  if (!isConfirmed(workspace, "combinations")) return { text: "请先完成业务范围确认。", section: "combinations" };
  if (!isConfirmed(workspace, "campus")) return { text: "请先在“校区入口人数”完成部门预算负责人确认。", section: "campus" };
  if (!isConfirmed(workspace, "entry-scope")) return { text: "请先在“入口范围例外”完成部门预算负责人确认。", section: "entry-scope" };
  return null;
}
const campusBlockerLabels: Record<string, string> = {
  CAMPUS_VALUE_INCOMPLETE: "还有校区没有填写入口人数。",
  CAMPUS_TOTAL_NOT_BALANCED: "各校区入口人数合计必须等于年度入口总量。",
  CAMPUS_BASIS_INCOMPLETE: "每个校区都要填写历史业绩依据、教室资源和销售资源。",
  CAMPUS_ADJUSTMENT_REASON_REQUIRED: "校区人数与系统建议不一致时，要填写调整原因类型和调整说明。",
};
function confirmationIssues(workspace: Workspace, key: string): string[] {
  if (key !== "campus") return [];
  const reasons = sectionData(workspace, key).blocking_reasons;
  if (!Array.isArray(reasons)) return [];
  return reasons.map(reason => campusBlockerLabels[String(reason)] ?? `待处理：${String(reason)}`);
}
function hasEvidence(value: Value): boolean {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  return Object.values(value).some(item => item !== null && String(item).trim() !== "");
}
function resourceDraftIssues(value: Value): string[] {
  if (!value || typeof value !== "object" || Array.isArray(value)) return ["资源与财务参数尚未生成。"];
  const draft = value as RecordValue;
  const numberAtLeast = (candidate: Value, minimum: number) => candidate !== "" && candidate !== null && Number.isFinite(Number(candidate)) && Number(candidate) >= minimum;
  const rows = (key: string) => Array.isArray(draft[key]) ? draft[key] as RecordValue[] : [];
  const completeReason = (row: RecordValue) => typeof row.change_reason === "string" && row.change_reason.trim().length > 0;
  const classRows = rows("class_size_items");
  const revenueRows = rows("revenue_items");
  const carryoverRows = rows("opening_carryover_items");
  const incompleteClass = classRows.filter(row => !numberAtLeast(row.system_value, 1) || !numberAtLeast(row.final_value, 1) || !hasEvidence(row.source_evidence) || !completeReason(row)).length;
  const incompleteRevenue = revenueRows.filter(row => !numberAtLeast(row.list_price_yuan, 0) || !numberAtLeast(row.current_season_recognition_rate_ppm, 0) || Number(row.current_season_recognition_rate_ppm) > 1_000_000 || !hasEvidence(row.source_evidence) || !completeReason(row)).length;
  const incompleteCarryover = carryoverRows.filter(row => !numberAtLeast(row.amount_yuan, 0) || !hasEvidence(row.source_evidence) || !completeReason(row)).length;
  const issues: string[] = [];
  if (!numberAtLeast(draft.budget_tax_conversion_factor, 0.000000001)) issues.push("填写预算含税转换系数。");
  if (!hasEvidence(draft.tax_factor_source_evidence)) issues.push("填写税务转换依据。");
  if (typeof draft.tax_factor_change_reason !== "string" || !draft.tax_factor_change_reason.trim()) issues.push("填写转换系数说明。");
  if (classRows.length === 0) issues.push("缺少班均参数明细。"); else if (incompleteClass) issues.push(`补齐 ${incompleteClass} 条班均参数的数值、依据和说明。`);
  if (revenueRows.length === 0) issues.push("缺少价格与结转明细。"); else if (incompleteRevenue) issues.push(`补齐 ${incompleteRevenue} 条价格与结转参数的金额、比例、依据和说明。`);
  if (carryoverRows.length === 0) issues.push("缺少期初结转明细。"); else if (incompleteCarryover) issues.push(`补齐 ${incompleteCarryover} 条期初结转的金额、依据和说明；没有结转也要明确填 0。`);
  return issues;
}
function stageState(workspace: Workspace, stageKey: string): "done" | "active" | "todo" {
  if (stageKey === "foundation") return isConfirmed(workspace, "combinations") ? "done" : "active";
  if (stageKey === "entry") return workspace.manifest.entry_detail_set_version_id ? "done" : hasSavedSection(workspace, "entry-total") ? "active" : "todo";
  if (stageKey === "rates") return isConfirmed(workspace, "rates") ? "done" : hasSavedSection(workspace, "rates") ? "active" : "todo";
  if (stageKey === "finance") return isConfirmed(workspace, "resources") && isConfirmed(workspace, "discount") ? "done" : hasSavedSection(workspace, "resources") || hasSavedSection(workspace, "discount") ? "active" : "todo";
  if (stageKey === "calculate") return workspace.manifest.target_closure_result_version_id ? "done" : "todo";
  return workspace.is_published ? "done" : "todo";
}
function taskState(workspace: Workspace, taskKey: string): "done" | "active" | "todo" {
  if (taskKey === "review") {
    if (workspace.is_published) return "done";
    return workspace.manifest.target_closure_result_version_id ? "active" : "todo";
  }
  return stageState(workspace, taskKey);
}
function nextStep(workspace: Workspace): { section?: string; href?: string; label: string } {
  if (!hasSavedSection(workspace, "targets")) return { section: "targets", label: "填写收入目标" };
  if (!isConfirmed(workspace, "combinations")) return { section: "combinations", label: hasSavedSection(workspace, "combinations") ? "确认业务范围" : "填写业务范围" };
  if (!hasSavedSection(workspace, "entry-total")) return { section: "entry-total", label: "填写年度入口总量" };
  if (!hasSavedSection(workspace, "campus-suggestions")) return { section: "campus-suggestions", label: "生成校区分配建议" };
  if (!hasSavedSection(workspace, "campus")) return { section: "campus", label: "填写校区入口人数" };
  if (!isConfirmed(workspace, "campus")) return { section: "campus", label: "确认校区入口人数" };
  if (!hasSavedSection(workspace, "entry-scope")) return { section: "entry-scope", label: "保存入口范围" };
  if (!isConfirmed(workspace, "entry-scope")) return { section: "entry-scope", label: "确认入口范围" };
  if (!workspace.manifest.entry_detail_set_version_id) return { section: "entry-detail", label: "拆分入口人数" };
  if (!isConfirmed(workspace, "rates")) return { section: "rates", label: "确认比例范围" };
  if (!isConfirmed(workspace, "resources")) return { section: "resources", label: hasSavedSection(workspace, "resources") ? "确认资源与财务参数" : "填写资源与财务参数" };
  if (!isConfirmed(workspace, "discount")) return { section: "discount", label: hasSavedSection(workspace, "discount") ? "确认折扣参数" : "填写折扣参数" };
  if (!workspace.manifest.target_closure_result_version_id) return { section: "results", label: "执行预算计算" };
  return { href: `/budget/publication?revision=${encodeURIComponent(workspace.revision_id)}`, label: "检查并发布" };
}
function formatDateTime(value: Value): string {
  if (typeof value !== "string" || !value) return "—";
  const date = new Date(value); if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }).format(date);
}
function errorHelp(message: string): { text: string; section?: string } {
  if (/尚不满足确认条件|CAMPUS_ENTRY_SET_NOT_CONFIRMABLE/i.test(message)) return { text: "校区入口人数还缺少必填依据或调整原因。请按页面提示补齐并保存后再确认。", section: "campus" };
  if (/校区入口|CAMPUS_ENTRY/i.test(message)) return { text: "校区入口人数已保存，但尚未由部门预算负责人确认。", section: "campus" };
  if (/入口范围|ENTRY_SCOPE/i.test(message)) return { text: "入口范围已保存，但尚未由部门预算负责人确认。", section: "entry-scope" };
  if (/业务范围|组合|COMBINATION/i.test(message)) return { text: "业务范围尚未确认。完成确认后即可继续。", section: "combinations" };
  if (/目标|TARGET/i.test(message)) return { text: "收入目标还不完整。请先补齐部门和赛道目标。", section: "targets" };
  if (/入口总|ENTRY_TOTAL/i.test(message)) return { text: "年度入口总量尚未保存。请先完成入口总量。", section: "entry-total" };
  if (/比例|RATE/i.test(message)) return { text: "比例参数尚未保存或确认。请先处理比例范围。", section: "rates" };
  if (/tax_factor|class_size_items|revenue_items|opening_carryover_items|资源与财务/i.test(message)) return { text: "资源与财务参数没有填写完整。请按页面的保存前检查逐项补齐。", section: "resources" };
  return { text: message || "操作没有完成，请检查当前页面的必填内容。" };
}

export function WorkspacePage() {
  const [identity, setIdentity] = useState<ApiIdentity | null>(null);
  const [budgets, setBudgets] = useState<Budget[]>([]);
  const [workspace, setWorkspace] = useState<Workspace | null>(null);
  const [selected, setSelected] = useState("home");
  const [draft, setDraft] = useState<Value>({});
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [jobStatus, setJobStatus] = useState("");
  const [result, setResult] = useState<RecordValue | null>(null);
  const [resultTitle, setResultTitle] = useState("");
  const [confirmArmed, setConfirmArmed] = useState(false);
  const attempt = useRef<{ serialized: string; key: string } | null>(null);
  const sequence = useRef(0);
  const pageTitleRef = useRef<HTMLHeadingElement>(null);
  const section = workspace?.sections.find(item => item.key === selected);

  useEffect(() => {
    if (!workspace?.is_published) return;
    const restoreSection = () => {
      const key = new URLSearchParams(location.search).get("section") ?? "results";
      setSelected(key === "home" ? "results" : key);
      setDraft(workspace.sections.find(item => item.key === key)?.value ?? {});
    };
    window.addEventListener("popstate", restoreSection);
    return () => window.removeEventListener("popstate", restoreSection);
  }, [workspace]);

  useEffect(() => { if (!dirty) return; const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; }; window.addEventListener("beforeunload", warn); return () => window.removeEventListener("beforeunload", warn); }, [dirty]);
  useEffect(() => {
    if (!workspace) return;
    const frame = window.requestAnimationFrame(() => {
      window.scrollTo({ top: 0, behavior: "auto" });
      pageTitleRef.current?.focus({ preventScroll: true });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [selected, workspace?.revision_id]);
  const request = async <T,>(path: string, who: ApiIdentity, body?: unknown, key?: string): Promise<T> => parseResponse(await fetch(`/api/v1${path}`, { method: body === undefined ? "GET" : "POST", headers: { ...identityHeaders(who), ...(body === undefined ? {} : { "Content-Type": "application/json", "Idempotency-Key": key ?? newRequestId(), "If-Match": `"${workspace?.revision_id ?? ""}"` }) }, ...(body === undefined ? {} : { body: JSON.stringify(body) }) }));
  const load = async (revision: string, who: ApiIdentity, active = selected): Promise<Workspace | null> => {
    const seq = ++sequence.current; const data = await request<Workspace>(`/revisions/${encodeURIComponent(revision)}/workspace`, who); if (seq !== sequence.current) return null;
    if (!data.is_published && data.manifest.formal_confirmation_id) {
      const detail = await fetchRevision(revision, who);
      data.is_published = (await fetchAuditEvents(detail.annual_budget_id, { revisionId: revision, eventType: "PUBLISHED" }, who)).items.length > 0;
      if (seq !== sequence.current) return null;
    }
    if (data.is_published && active === "home") active = "results";
    setWorkspace(data); setSelected(active); setDraft(data.sections.find(item => item.key === active)?.value ?? {}); setDirty(false); setResult(null); setConfirmArmed(false);
    localStorage.setItem("budget.activeRevision", revision);
    const url = new URL(location.href); url.pathname = "/budget/workspace"; url.searchParams.set("revision", revision); url.searchParams.set("section", active);
    history.replaceState({}, "", url);
    return data;
  };
  useEffect(() => {
    let stopped = false;
    void (async () => { setBusy(true); try {
      const who = await parseResponse<ApiIdentity>(await fetch("/api/v1/local-trial/session")); if (stopped) return;
      setIdentity(who); localStorage.setItem("budget.userId", who.userId); localStorage.setItem("budget.departmentId", who.departmentId);
      const list = await request<Budget[]>("/workspace/budgets", who); if (stopped) return; setBudgets(list);
      const params = new URLSearchParams(location.search); const active = params.get("section") ?? "home";
      const allowed = ["home", "source", ...stages.flatMap(stage => [...stage.sections])]; const safeActive = allowed.includes(active) ? active : "home"; setSelected(safeActive);
      const id = params.get("revision") ?? list[0]?.draft_revision_id; if (id) await load(id, who, safeActive);
    } catch (nextError) { if (!stopped) setError(nextError instanceof Error ? nextError.message : String(nextError)); } finally { if (!stopped) setBusy(false); } })();
    return () => { stopped = true; sequence.current += 1; };
  }, []);
  const changeSection = (key: string) => {
    if (dirty && !window.confirm("当前修改尚未保存。离开后将放弃这些修改，是否继续？")) return;
    if (workspace?.is_published && key === "home") key = "results";
    setSelected(key); setDraft(workspace?.sections.find(item => item.key === key)?.value ?? {}); setDirty(false); setError(""); setNotice(""); setResult(null); setConfirmArmed(false);
    if (workspace) { const url = new URL(location.href); url.searchParams.set("revision", workspace.revision_id); url.searchParams.set("section", key); history.replaceState({}, "", url); }
  };
  const execute = async (path: string, body: unknown, prepare = false, success = "已保存。") => {
    if (!identity || !workspace) return; setBusy(true); setError(""); setNotice(""); setJobStatus("");
    try {
      let payload = body; if (prepare) payload = await request(`/workspace/forms/${selected}:prepare`, identity, body);
      const serialized = JSON.stringify([workspace.revision_id, path, payload]); if (attempt.current?.serialized !== serialized) attempt.current = { serialized, key: newRequestId() };
      const response = await request<any>(`/revisions/${workspace.revision_id}${path}`, identity, payload, attempt.current.key);
      let id = response.revision?.new_revision_id ?? response.new_revision_id ?? response.applied_revision_id ?? workspace.revision_id;
      if (response.job_id) {
        localStorage.setItem("budget.activeJob", response.job_id); const deadline = Date.now() + 20 * 60 * 1000;
        for (;;) { const job = await fetchJob(response.job_id, identity); setJobStatus(meanings[job.status] ?? job.status);
          if (job.status === "SUCCEEDED") { id = job.result?.final_revision_id ?? id; localStorage.removeItem("budget.activeJob"); break; }
          if (["FAILED", "INCOMPLETE", "CANCELLED"].includes(job.status)) { localStorage.removeItem("budget.activeJob"); throw new Error(job.last_error_safe ?? "计算未完成，请检查输入。"); }
          if (Date.now() > deadline) throw new Error("计算仍在后台进行。稍后可查询计算进度，请勿重复提交。");
          await new Promise(resolve => setTimeout(resolve, 1000));
        }
      }
      const loaded = await load(String(id), identity, selected);
      if (loaded) {
        const following = nextStep(loaded);
        if (following.section && following.section !== selected) {
          const staysInTask = taskFor(following.section).key === taskFor(selected).key;
          const destination = staysInTask ? following.section : "home";
          setSelected(destination); setDraft(loaded.sections.find(item => item.key === destination)?.value ?? {});
          history.replaceState({}, "", `/budget/workspace?revision=${encodeURIComponent(loaded.revision_id)}&section=${encodeURIComponent(destination)}`);
          if (!staysInTask) success = `${success} 下一步：${following.label}。`;
        } else if (response.job_id && following.href) {
          location.assign(following.href);
          return;
        }
      }
      setBudgets(await request<Budget[]>("/workspace/budgets", identity)); setNotice(response.job_id ? "预算计算已完成，请检查结果和目标差额。" : success);
    } catch (nextError) { setError(nextError instanceof Error ? nextError.message : String(nextError)); } finally { setBusy(false); }
  };
  const queryJob = async () => {
    if (!identity) return; const id = localStorage.getItem("budget.activeJob"); if (!id) return setNotice("当前没有正在计算的任务。");
    setBusy(true); try { const job = await fetchJob(id, identity); setJobStatus(meanings[job.status] ?? job.status); if (job.status === "SUCCEEDED" && typeof job.result?.final_revision_id === "string") { await load(job.result.final_revision_id, identity); localStorage.removeItem("budget.activeJob"); } } catch (nextError) { setError(String(nextError)); } finally { setBusy(false); }
  };

  const choices: Record<string, string> = {};
  const rateData = workspace?.sections.find(item => item.key === "rates")?.data.items;
  if (Array.isArray(rateData)) for (const entry of rateData as RecordValue[]) {
    const title = [entry.quarter_name ?? entry.target_fiscal_season, entry.campus, entry.track, entry.product, entry.grade, meanings[String(entry.rate_type)]].join(" / ");
    choices[String(entry.rate_reference_id)] = title; choices[String(entry.rate_parameter_stable_key)] = title;
  }
  const activeBudget = budgets.find(budget => budget.fiscal_year === workspace?.fiscal_year); const latest = activeBudget?.draft_revision_id;
  const sourceRows = workspace?.source.components as RecordValue[] | undefined;
  const next = workspace ? nextStep(workspace) : null; const help = errorHelp(error); const copy = pageCopy[selected];
  const activeTask = taskFor(selected === "home" ? next?.section ?? "results" : selected);
  const completedTasks = workspace ? tasks.filter(task => taskState(workspace, task.key) === "done").length : 0;
  const isReadOnly = Boolean(busy || !workspace?.is_current_draft || workspace?.is_published); const confirmed = section && workspace ? isConfirmed(workspace, selected) : false;
  const blocker = workspace ? sectionBlocker(workspace, selected) : null;
  const confirmIssues = workspace ? confirmationIssues(workspace, selected) : [];
  const inputIssues = selected === "resources" ? resourceDraftIssues(draft) : [];
  const repeatableAction = ["campus-suggestions", "entry-detail", "planned-unlock"].includes(selected);
  const noChangesToSave = Boolean(section && hasSavedSection(workspace!, selected) && !dirty && !repeatableAction);
  const sourceSummary = useMemo(() => sourceRows?.map(row => ({ name: String(row.dataset_name ?? "数据来源"), date: formatDateTime(row.extracted_at ?? row.cutoff_at), rows: row.row_count })) ?? [], [sourceRows]);

  return <div className="budget-shell decision-shell"><a className="skip-link" href="#workspace-content">跳到主要内容</a>
    <header className="decision-topbar">
      <button className="decision-brand" onClick={() => changeSection("home")}><span aria-hidden="true">智</span><strong>预算智解</strong></button>
      <label className="decision-budget-select"><span className="sr-only">年度预算</span><select disabled={busy} value={workspace?.fiscal_year ?? ""} onChange={event => { if (dirty && !window.confirm("放弃尚未保存的修改？")) return; const budget = budgets.find(item => item.fiscal_year === Number(event.target.value)); if (budget?.draft_revision_id && identity) { setBusy(true); void load(budget.draft_revision_id, identity, "home").catch(nextError => setError(String(nextError))).finally(() => setBusy(false)); } }}><option value="">选择预算</option>{budgets.map(budget => <option value={budget.fiscal_year} key={budget.annual_budget_id}>FY{String(budget.fiscal_year).slice(-2)} 学校版</option>)}</select></label>
      {workspace && <div className="decision-version"><span className={`status-dot ${workspace.is_published ? "is-published" : ""}`} /><span>{workspace.is_published ? "已定版" : "编辑中"}</span><small>R{workspace.revision_no}</small></div>}
    </header>
    <main className="decision-workspace" id="workspace-content">
      {workspace && <nav className="decision-progress" aria-label="预算任务进度">{tasks.map(task => { const state = taskState(workspace, task.key); const active = activeTask.key === task.key && selected !== "home"; const target = taskTarget(workspace, task, next); const taskLabel = workspace.is_published && task.key === "review" ? "预算概览" : task.label; const stateLabel = state === "done" ? "已完成" : active ? "正在处理" : "待处理"; return <button key={task.key} className={`is-${state} ${active ? "is-active" : ""}`} aria-current={active ? "step" : undefined} aria-label={`任务 ${task.no}：${taskLabel}，${stateLabel}`} onClick={() => changeSection(target)}><span>{task.no}</span><strong>{taskLabel}</strong><em className="decision-progress-short">{workspace.is_published && task.key === "review" ? "概览" : task.short}</em><small>{state === "done" ? "完成" : active ? "正在处理" : ""}</small></button>; })}</nav>}
      {error && <div className="decision-question" role="alert"><div><strong>还差一步</strong><p>{help.text}</p><details><summary>技术信息</summary><code>{error}</code></details></div>{help.section && <button onClick={() => changeSection(help.section!)}>去处理</button>}<button className="decision-dismiss" aria-label="关闭提示" onClick={() => setError("")}>关闭</button></div>}
      {notice && <div className="decision-notice" role="status"><span>{notice}</span><button aria-label="关闭提示" onClick={() => setNotice("")}>关闭</button></div>}
      {!workspace && !busy && <section className="workspace-empty"><h2>暂时无法读取预算</h2><p>请重新启动本机试用服务。</p></section>}
      {workspace && selected === "home" && <section className="decision-home">
        <div className="decision-home__copy"><p className="decision-kicker">FY{String(workspace.fiscal_year).slice(-2)} 学校版 · {completedTasks}/5</p><h1 ref={pageTitleRef} tabIndex={-1}>{workspace.is_published ? "预算已定版" : "预算已经准备好"}</h1><p>{workspace.is_published ? "可以查看最终结果，所有修改和确认记录都已保留。" : <>现在只需要完成：<strong>{next?.label}</strong></>}</p>{workspace.is_published ? <a className="decision-primary" href={`/budget/results?revision=${encodeURIComponent(workspace.revision_id)}`}>查看预算概览</a> : next?.href ? <a className="decision-primary" href={next.href}>继续</a> : <button className="decision-primary" onClick={() => next?.section && changeSection(next.section)}>继续</button>}</div>
        <div className="decision-home__status"><span>当前任务</span><strong>{activeTask.label}</strong><p>{workspace.is_published ? "流程已完成" : `第 ${activeTask.no} 项，共 5 项`}</p></div>
      </section>}
      {workspace?.is_published && <div hidden={selected !== "results"}><BudgetResultsPage key={workspace.revision_id} revisionId={workspace.revision_id} active={selected === "results"} /></div>}
      {workspace && selected !== "home" && !(workspace.is_published && selected === "results") && <section className="decision-focus">
        <header className="decision-focus__bar"><button onClick={() => changeSection("home")}>返回</button><div><span>任务 {activeTask.no} / 5</span><strong>{activeTask.label}</strong></div></header>
        <section className={`stage-content decision-content ${selected === "rates" ? "decision-content--wide" : ""}`} aria-busy={busy}>
          {selected === "source" && <><header className="content-heading"><div><span className="content-kicker">来源与范围 · 第一步</span><h2 ref={pageTitleRef} tabIndex={-1}>数据来源</h2><p>确认本次预算使用的数据范围和快照日期。</p></div><span className="content-status is-done">已冻结</span></header><div className="source-overview"><div><span>快照状态</span><strong>{meanings[String(workspace.source.status)] ?? "已冻结"}</strong></div><div><span>快照日期</span><strong>{formatDateTime(workspace.source.cutoff_at)}</strong></div><div><span>冻结时间</span><strong>{formatDateTime(workspace.source.frozen_at)}</strong></div></div><section className="source-list"><h3>本次使用的数据</h3>{sourceSummary.map((item, index) => <article key={`${item.name}-${index}`}><div><strong>{item.name}</strong><span>快照 {item.date}</span></div>{item.rows !== undefined && <em>{String(item.rows)} 条</em>}</article>)}</section><details className="rules-drawer"><summary>查看来源详情与口径</summary><div className="rules-drawer-body"><ReadTable rows={sourceRows ?? []} /><h3>来源核对</h3><ReadTable rows={(workspace.source.reconciliations as RecordValue[]) ?? []} /><p>期初老生使用上一财年春季普通招生人次；新生占比使用三分之二出勤口径作分母。</p></div></details></>}
          {section && <><header className="content-heading"><div><span className="content-kicker">只处理这一项</span><h2 ref={pageTitleRef} tabIndex={-1}>{copy?.title ?? sectionNames[selected] ?? section.label}</h2><p>{copy?.intro ?? "核对当前数据并完成本步骤。"}</p></div><span className={`content-status ${confirmed ? "is-done" : dirty ? "is-editing" : hasSavedSection(workspace, selected) ? "is-saved" : ""}`}>{sectionStatus(workspace, section, dirty, confirmed)}</span></header><fieldset disabled={isReadOnly} className="business-fieldset">
            {selected === "rates" ? <RateTableEditor value={draft} referenceRows={Array.isArray(section.data.items) ? section.data.items as RecordValue[] : []} disabled={isReadOnly} dirty={dirty} onChange={nextDraft => { setDraft(nextDraft); setDirty(true); }} onAction={(mode, keys, reason) => { const actionSection = workspace.sections.find(item => item.key === (mode === "lock" ? "rate-lock" : "rate-unlock")); if (actionSection) void execute(actionSection.path, { items: keys.map(key => ({ rate_parameter_stable_key: key, reason })) }, false, mode === "lock" ? "选中比例已锁定。" : "选中比例已解锁。"); }} /> : <BusinessForm key={`${workspace.revision_id}:${selected}`} schema={section.schema} value={draft} choices={choices} fixedArrays={selected === "resources"} readOnlyKeys={selected === "resources" ? ["fiscal_season", "track", "product", "grade", "subject", "campus"] : []} onChange={nextDraft => { setDraft(nextDraft); setDirty(true); }} />}
            {blocker && <div className="prerequisite-note" role="status"><span>{blocker.text}</span><button type="button" onClick={() => changeSection(blocker.section)}>去确认</button></div>}
            {confirmIssues.length > 0 && <div className="confirmation-blockers" role="status"><strong>保存成功，但确认前还要补齐：</strong><ul>{confirmIssues.map(issue => <li key={issue}>{issue}</li>)}</ul></div>}
            {inputIssues.length > 0 && <div className="confirmation-blockers" role="status"><strong>保存前还要完成：</strong><ul>{inputIssues.map(issue => <li key={issue}>{issue}</li>)}</ul></div>}
            <div className="primary-actions"><button className={`button ${dirty || !hasSavedSection(workspace, selected) || !section.confirm ? "button--primary" : ""}`} disabled={Boolean(blocker) || inputIssues.length > 0 || noChangesToSave} title={noChangesToSave ? "内容没有变化，无需重复保存" : inputIssues.length > 0 ? "请先补齐页面列出的必填内容" : undefined} onClick={() => void execute(section.path, draft, ["targets", "resources", "discount"].includes(selected), `${copy?.title ?? section.label}已保存。`)}>{busy ? "正在处理…" : noChangesToSave ? "已保存" : copy?.primary ?? "保存"}</button>{section.confirm && !confirmed && <button className={`button ${hasSavedSection(workspace, selected) && !dirty ? "button--primary" : ""}`} disabled={dirty || confirmIssues.length > 0 || inputIssues.length > 0} title={confirmIssues.length > 0 || inputIssues.length > 0 ? "请先补齐页面列出的确认条件" : undefined} onClick={() => setConfirmArmed(true)}>确认并继续</button>}</div></fieldset>
            {confirmArmed && section.confirm && <section className="confirmation-strip" role="group" aria-label="确认当前步骤"><div><strong>确认当前全部{copy?.title ?? section.label}？</strong><span>确认后仍可修改，但相关下游结果需要重新计算。</span></div><button className="button button--primary" onClick={() => { setConfirmArmed(false); void execute(section.confirm!.path, section.confirm!.body, false, `${copy?.title ?? section.label}已确认。`); }}>确认</button><button className="button" onClick={() => setConfirmArmed(false)}>取消</button></section>}
            {dirty && <p className="unsaved-note" role="status">修改尚未保存。保存失败时，当前输入会保留。</p>}{Array.isArray(section.data.items) && selected !== "rates" && <details className="rules-drawer"><summary>查看已保存数据</summary><div className="rules-drawer-body"><ReadTable rows={section.data.items as RecordValue[]} /></div></details>}</>}
          {selected === "results" && <><header className="content-heading"><div><span className="content-kicker">审阅与发布</span><h2 ref={pageTitleRef} tabIndex={-1}>计算结果</h2><p>依次计算人数、班均、收入和目标差额，不会自动补人或放宽容差。</p></div><span className={`content-status ${workspace.manifest.target_closure_result_version_id ? "is-done" : ""}`}>{workspace.is_published ? "已定版" : workspace.manifest.target_closure_result_version_id ? "已有结果" : "待计算"}</span></header>{<div className="calculation-callout"><div><strong>执行完整预算计算</strong><span>将使用当前已确认参数生成一套新的计算结果。</span></div><button className="button button--primary" disabled={isReadOnly} onClick={() => void execute(":calculate", {}, false, "预算计算已完成。")}>开始计算</button><button className="button" disabled={busy} onClick={() => void queryJob()}>查询计算进度</button>{workspace.manifest.target_closure_result_version_id && <a className="button" href={`/budget/rebalancing?revision=${encodeURIComponent(workspace.revision_id)}`}>查看缺口与调平方案</a>}</div>}{jobStatus && <p role="status">当前状态：{jobStatus}</p>}<div className="result-tabs">{Object.entries(resultRoutes).map(([label, prefix]) => { const id = workspace.manifest[`${prefix.replaceAll("-", "_")}_version_id`]; return <button disabled={!id || busy} key={prefix} className={resultTitle === label ? "is-active" : ""} onClick={() => { if (!identity) return; setBusy(true); setError(""); void request<RecordValue>(`/${prefix}-versions/${id}`, identity).then(data => { setResult(data); setResultTitle(label); }).catch(nextError => setError(String(nextError))).finally(() => setBusy(false)); }}>{label}{!id && <small>待计算</small>}</button>; })}</div>{result && <section className="result-panel"><h3>{resultTitle}</h3><ReadTable rows={(result.items as RecordValue[]) ?? []} /></section>}</>}
        </section>
        <details className="decision-advanced"><summary>高级维护</summary><div><p>仅在需要回看来源、修正历史步骤或查看版本时使用。</p>{tasks.map(task => <section key={task.key}><strong>{task.no}. {task.label}</strong><nav>{task.sections.map(key => <button key={key} className={selected === key ? "is-current" : ""} onClick={() => changeSection(key)}>{sectionNames[key] ?? key}{key !== "source" && isSectionComplete(workspace, key) ? " · 已完成" : ""}</button>)}</nav></section>)}<div className="decision-advanced__links"><a href={`/budget/opening?revision=${workspace.revision_id}`}>期初老生</a><a href={`/budget/audit?revision=${workspace.revision_id}`}>版本记录</a><a href={`/budget/rebalancing?revision=${workspace.revision_id}`}>自动调平</a>{latest && latest !== workspace.revision_id && <button disabled={busy} onClick={() => identity && void load(latest, identity, "home")}>返回最新草稿</button>}<button disabled={busy || !workspace.is_current_draft || dirty} onClick={() => void execute(":derive", { reason: "从当前版本创建试算版本" }, false, "试算版本已创建。")}>创建试算版本</button></div></div></details>
      </section>}
      {busy && <div className="working-bar" role="status"><span />正在处理，请勿重复提交 {jobStatus && `· ${jobStatus}`}</div>}
    </main>
  </div>;
}
