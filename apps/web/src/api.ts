export type HealthStatus = "ok" | "unavailable";

export interface HealthResponse {
  status: HealthStatus;
}

export interface ApiIdentity {
  userId: string;
  departmentId: string;
  roles: string[];
}

/** Generate an idempotency key in HTTPS, localhost, and ordinary LAN browser contexts. */
export function newRequestId(): string {
  if (typeof globalThis.crypto?.randomUUID === "function") return globalThis.crypto.randomUUID();
  const bytes = new Uint8Array(16);
  if (typeof globalThis.crypto?.getRandomValues === "function") {
    globalThis.crypto.getRandomValues(bytes);
  } else {
    for (let index = 0; index < bytes.length; index += 1) bytes[index] = Math.floor(Math.random() * 256);
  }
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = [...bytes].map(value => value.toString(16).padStart(2, "0"));
  return `${hex.slice(0, 4).join("")}-${hex.slice(4, 6).join("")}-${hex.slice(6, 8).join("")}-${hex.slice(8, 10).join("")}-${hex.slice(10).join("")}`;
}

export interface RevisionResponse {
  budget_revision_id: string;
  annual_budget_id: string;
  revision_no: number;
  parent_revision_id: string | null;
  command_type: string;
  source_snapshot_id: string;
  parameter_version_id: string;
  revision_content_hash: string;
  component_manifest: Record<string, unknown>;
  created_by: string;
  created_at: string;
}

export interface AnnualBudgetResponse {
  annual_budget_id: string;
  department_id: string;
  fiscal_year: number;
  current_draft_revision_id: string | null;
  current_published_revision_id: string | null;
  created_by: string;
  created_at: string;
}

export interface PublicationCheck {
  code: string;
  group: string;
  passed: boolean;
  actual: Record<string, unknown>;
  rule: string;
  repair_route: string;
}

export interface PlannedNewStudentSummary {
  fiscal_season: number;
  track: string;
  product: string;
  entry_attribute: string;
  grade: string;
  subject: string;
  campus: string;
  planned_new_students: number;
  reason: string;
  locked: boolean;
  handling_status: string;
}

export interface PublicationReadiness {
  budget_revision_id: string;
  input_fingerprint: string;
  gate_rule_version: string;
  status: "READY" | "BLOCKED";
  blocker_count: number;
  checks: PublicationCheck[];
  planned_new_students: PlannedNewStudentSummary[];
}

export interface BudgetCalculationJob {
  job_id: string;
  source_revision_id: string;
  calculation_pipeline_version: string;
  status: string;
  links: Record<string, string>;
}

export interface JobResponse {
  job_id: string;
  job_type: string;
  status: "QUEUED" | "RUNNING" | "SUCCEEDED" | "INCOMPLETE" | "FAILED" | "CANCELLED";
  result: Record<string, unknown> | null;
  last_error_code: string | null;
  last_error_safe: string | null;
  attempt_no: number;
  created_at: string;
  finished_at: string | null;
}

export interface AuditArchiveExport {
  job_id: string;
  budget_revision_id: string;
  audit_cutoff_at: string;
  archive_schema_version: string;
  status: string;
  links: Record<string, string>;
}

export interface PublishedBudgetBaseline {
  annual_budget_id: string;
  fiscal_year: number;
  published_revision_id: string;
  previous_published_revision_id: string | null;
  formal_plan_confirmation_id: string;
  publication_input_fingerprint: string;
  published_by: string;
  published_at: string;
  content_hash: string;
  links: Record<string, string>;
}

export interface TargetClosureItem {
  target_stable_key: string;
  scope_level: string;
  track: string | null;
  target_revenue_yuan: string;
  calculated_revenue_yuan: string;
  variance_yuan: string;
  tolerance_yuan: string;
  check_status: string;
}

export interface TargetClosureVersion {
  target_closure_result_version_id: string;
  budget_revision_id: string;
  closure_status: string;
  publishable: boolean;
  input_fingerprint: string;
  created_at: string;
  items: TargetClosureItem[];
}

export interface SeasonRevenueResult {
  season_headcount_result_id?: string;
  fiscal_season: number;
  quarter_name: string;
  track: string;
  product: string;
  entry_attribute: string;
  grade: string;
  subject: string;
  campus: string;
  integer_total_students: number | null;
  list_price_yuan: string;
  detail_discount_rate_ppm: number;
  list_price_revenue_yuan: string | null;
  business_quarter_revenue_yuan: string | null;
  quarter_recognized_revenue_yuan: string | null;
  calculation_status: string;
}

export interface RevenueAggregateResult {
  scope_level: string;
  track: string | null;
  period_type: string;
  fiscal_season: number | null;
  list_price_revenue_yuan: string;
  discounted_revenue_yuan: string;
  business_revenue_yuan: string;
  recognized_revenue_yuan: string;
  composite_discount_rate_ppm: string | null;
  target_rate_ppm: number | null;
  variance_ppm: string | null;
  target_status: string;
}

export interface RevenueResultVersion {
  revenue_result_version_id: string;
  budget_revision_id: string;
  headcount_result_version_id?: string;
  fiscal_year: number;
  status: string;
  publishable: boolean;
  record_count: number;
  aggregate_count: number;
  created_at: string;
  items: SeasonRevenueResult[];
  aggregates: RevenueAggregateResult[];
}

export interface SeasonHeadcountResult {
  season_headcount_result_id: string;
  fiscal_season: number;
  quarter_name: string;
  track: string; product: string; entry_attribute: string; grade: string; subject: string; campus: string;
  integer_old_students: number | null;
  integer_new_students: number | null;
  integer_total_students: number | null;
}

export interface HeadcountResultVersion {
  headcount_result_version_id: string;
  budget_revision_id: string;
  items: SeasonHeadcountResult[];
}

export interface ScenarioChange {
  scenario_change_id: string;
  object_type: "ENTRY" | "RATE" | "DISCOUNT";
  object_stable_key: string;
  original_value: number;
  suggested_value: number;
  adjustment: number;
  content_hash: string;
}

export interface RebalancingScenario {
  rebalancing_scenario_id: string;
  result_hash: string;
  scenario_types: string[];
  target_variances: Array<{ scope: string; variance_yuan: string }>;
  retention_contribution_yuan: string;
  new_share_contribution_yuan: string;
  headcount_signature: Array<{ row_key: string; integer_total_students: number }>;
  objective_values: Record<string, unknown>;
  content_hash: string;
  changes: ScenarioChange[];
}

export interface InfeasibilityProof {
  infeasibility_proof_id: string;
  stage: string;
  profile: string;
  proof_code: string;
  model_hash: string;
  hard_constraint_groups: string[];
  proof_summary: Record<string, unknown>;
  content_hash: string;
}

export interface OptimizationRun {
  optimization_run_id: string;
  budget_revision_id: string;
  target_closure_result_version_id: string;
  optimization_model_version: string;
  terminal_stage: string;
  status: "OPTIMAL" | "INFEASIBLE" | "INCOMPLETE" | "FAILED";
  discount_unlock_required: boolean;
  discount_unlock_confirmation_id: string | null;
  reason_code: string | null;
  solver_adapter: string;
  evaluated_state_count: number;
  attempts: Array<Record<string, unknown>>;
  input_fingerprint: string;
  content_hash: string;
  created_by: string;
  created_at: string;
  scenarios: RebalancingScenario[];
  infeasibility_proofs: InfeasibilityProof[];
}

export interface DiscountUnlock {
  discount_unlock_request_id: string;
  budget_revision_id: string;
  optimization_run_id: string;
  infeasibility_proof_id: string;
  input_fingerprint: string;
  reason: string;
  status: "REQUESTED" | "ACTIVE";
  requested_by: string;
  requested_at: string;
  request_content_hash: string;
  discount_unlock_confirmation_id: string | null;
  confirmed_by: string | null;
  confirmed_at: string | null;
  confirmation_content_hash: string | null;
}

export interface ScenarioApplication {
  scenario_application_id: string;
  rebalancing_scenario_id: string;
  optimization_run_id: string;
  source_revision_id: string;
  applied_revision_id: string;
  reason: string;
  change_count: number;
  entry_change_count: number;
  rate_change_count: number;
  discount_change_count: number;
  campus_confirmation_required: boolean;
  invalidated_components: string[];
  calculation_job_id: string;
  calculation_job_status: string;
}

export interface ScenarioApplicationRevert {
  scenario_application_revert_id: string;
  scenario_application_id: string;
  reverted_from_revision_id: string;
  restored_source_revision_id: string;
  reverted_revision_id: string;
  reason: string;
  restored_component_keys: string[];
  invalidated_components: string[];
  calculation_job_id: string;
  calculation_job_status: string;
}

export interface RevisionLifecycleEvent {
  revision_lifecycle_event_id: string;
  budget_revision_id: string;
  event_type: string;
  actor_id: string;
  detail: Record<string, unknown>;
  occurred_at: string;
}

export interface RevisionState {
  revision: RevisionResponse;
  is_current_draft: boolean;
  is_current_published: boolean;
  is_ever_published: boolean;
  is_formally_confirmed: boolean;
  lifecycle_events: RevisionLifecycleEvent[];
}

export interface RevisionComponentDiff {
  component_key: string;
  change_type: "ADDED" | "REMOVED" | "CHANGED" | "UNCHANGED";
  against_value: unknown;
  revision_value: unknown;
}

export interface RevisionDiff {
  revision: RevisionState;
  against: RevisionState;
  relationship: string;
  changed_count: number;
  added_count: number;
  removed_count: number;
  unchanged_count: number;
  components: RevisionComponentDiff[];
  content_hash: string;
}

export interface RevisionDetailState {
  present: boolean;
  system_value: unknown;
  manual_value: unknown;
  final_value: unknown;
  confirmed: boolean;
  status: string;
  reason: string | null;
  source_evidence: Record<string, unknown>;
  provenance: Record<string, unknown>;
  content_hash: string | null;
}

export interface RevisionDetailDiffItem {
  stable_key: string;
  detail_type: string;
  change_type: "ADDED" | "REMOVED" | "CHANGED" | "UNCHANGED";
  business_key: Record<string, unknown>;
  against: RevisionDetailState;
  revision: RevisionDetailState;
}

export interface RevisionDetailDiff {
  revision_id: string;
  against_revision_id: string;
  component_key: string;
  component_label: string;
  total_count: number;
  changed_count: number;
  added_count: number;
  removed_count: number;
  unchanged_count: number;
  items: RevisionDetailDiffItem[];
  next_cursor: string | null;
  content_hash: string;
}

export interface AuditEvent {
  event_id: string;
  event_type: string;
  annual_budget_id: string | null;
  budget_revision_id: string | null;
  actor_id: string;
  acting_role: string;
  role_snapshot: Record<string, unknown>;
  object_type: string;
  object_key: Record<string, unknown>;
  detail: Record<string, unknown>;
  reason: string | null;
  request_id: string;
  occurred_at: string;
}

export interface AuditEventPage {
  items: AuditEvent[];
  next_cursor: string | null;
}

export interface AuditEventFilters {
  revisionId?: string;
  eventType?: string;
  objectType?: string;
  actorId?: string;
  actingRole?: string;
  reasonContains?: string;
  occurredFrom?: string;
  occurredTo?: string;
  cursor?: string;
  limit?: number;
}

export class ApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly traceId?: string;

  constructor(message: string, code: string, status: number, traceId?: string) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
    this.traceId = traceId;
  }
}

export interface OpeningDimension { track: string; product: string; grade: string; subject: string; campus: string }
export interface OpeningSourceRow extends OpeningDimension { enrollment: number | null; rate_parameter_id: string | null; renewal_rate_ppm: number | null }
export interface OpeningWorkspace {
  opening_status: string; opening_student_version_id: string | null;
  fiscal_year: number; source_fiscal_season: number; is_current_draft: boolean;
  sources: Array<{ source_component_id: string; dataset_name: string; snapshot_date: string; source_batch: string; partition: string; content_hash: string; rows: OpeningSourceRow[] }>;
  combinations: Array<OpeningDimension & { combination_id: string }>;
}
export async function fetchOpeningWorkspace(revisionId: string, identity: ApiIdentity): Promise<OpeningWorkspace> {
  return parseResponse(await fetch(`/api/v1/revisions/${encodeURIComponent(revisionId)}/opening-students/workspace`, { headers: identityHeaders(identity) }));
}
export async function saveOpening(revisionId: string, identity: ApiIdentity, body: unknown, key: string): Promise<{ opening_student_version_id: string; revision: { new_revision_id: string } }> {
  return parseResponse(await fetch(`/api/v1/revisions/${encodeURIComponent(revisionId)}/opening-students`, {
    method: "POST", headers: { ...identityHeaders(identity), "Content-Type": "application/json", "If-Match": `"${revisionId}"`, "Idempotency-Key": key }, body: JSON.stringify(body),
  }));
}

export function identityHeaders(identity: ApiIdentity): HeadersInit {
  if (!/^[\x20-\x7E]+$/.test(identity.userId) || !/^[\x20-\x7E]+$/.test(identity.departmentId)) {
    throw new ApiError("本地开发身份 ID 仅支持英文、数字及常用半角符号", "INVALID_DEV_HEADER", 400);
  }
  return {
    "X-User-ID": identity.userId,
    "X-Department-ID": identity.departmentId,
    "X-Roles": identity.roles.join(","),
  };
}

export async function parseResponse<T>(response: Response): Promise<T> {
  if (response.ok) {
    return (await response.json()) as T;
  }

  let payload: Record<string, unknown> = {};
  try {
    payload = (await response.json()) as Record<string, unknown>;
  } catch {
    // Keep the response-status fallback below for non-JSON errors.
  }
  const detail = payload.detail;
  if (Array.isArray(detail)) {
    throw new ApiError(detail.map((item: {loc?: unknown[]; msg?: string}) => `${item.loc?.slice(1).join(" / ") ?? "表单"}：${item.msg ?? "输入不合法"}`).join("；"), "VALIDATION_ERROR", response.status);
  }
  if (detail && typeof detail === "object" && !Array.isArray(detail)) {
    payload = detail as Record<string, unknown>;
  }
  const message = typeof payload.message === "string" ? payload.message : `API 返回 ${response.status}`;
  const code = typeof payload.code === "string" ? payload.code : "HTTP_ERROR";
  const traceId = typeof payload.trace_id === "string" ? payload.trace_id : undefined;
  throw new ApiError(message, code, response.status, traceId);
}

export async function fetchLiveness(signal?: AbortSignal): Promise<HealthResponse> {
  const response = await fetch("/api/v1/health/live", { signal });
  return parseResponse<HealthResponse>(response);
}

export async function fetchRevision(
  revisionId: string,
  identity: ApiIdentity,
  signal?: AbortSignal,
): Promise<RevisionResponse> {
  const response = await fetch(`/api/v1/revisions/${encodeURIComponent(revisionId)}`, {
    headers: identityHeaders(identity),
    signal,
  });
  return parseResponse<RevisionResponse>(response);
}

export async function fetchRevisions(
  annualBudgetId: string,
  identity: ApiIdentity,
  signal?: AbortSignal,
): Promise<RevisionResponse[]> {
  const response = await fetch(
    `/api/v1/annual-budgets/${encodeURIComponent(annualBudgetId)}/revisions`,
    { headers: identityHeaders(identity), signal },
  );
  return parseResponse<RevisionResponse[]>(response);
}

export async function fetchRevisionDiff(
  revisionId: string,
  againstRevisionId: string,
  identity: ApiIdentity,
  signal?: AbortSignal,
): Promise<RevisionDiff> {
  const params = new URLSearchParams({ against: againstRevisionId });
  const response = await fetch(
    `/api/v1/revisions/${encodeURIComponent(revisionId)}/diff?${params}`,
    { headers: identityHeaders(identity), signal },
  );
  return parseResponse<RevisionDiff>(response);
}

export async function fetchRevisionDetailDiff(
  revisionId: string,
  againstRevisionId: string,
  componentKey: string,
  changedOnly: boolean,
  query: string,
  identity: ApiIdentity,
  cursor?: string,
  signal?: AbortSignal,
): Promise<RevisionDetailDiff> {
  const params = new URLSearchParams({
    against: againstRevisionId,
    component_key: componentKey,
    changed_only: String(changedOnly),
    limit: "100",
  });
  if (query.trim()) params.set("q", query.trim());
  if (cursor) params.set("cursor", cursor);
  const response = await fetch(
    `/api/v1/revisions/${encodeURIComponent(revisionId)}/detail-diff?${params}`,
    { headers: identityHeaders(identity), signal },
  );
  return parseResponse<RevisionDetailDiff>(response);
}

export async function fetchAuditEvents(
  annualBudgetId: string,
  filters: AuditEventFilters,
  identity: ApiIdentity,
  signal?: AbortSignal,
): Promise<AuditEventPage> {
  const params = new URLSearchParams({ annual_budget_id: annualBudgetId });
  const optionalParams: Array<[string, string | number | undefined]> = [
    ["revision_id", filters.revisionId],
    ["event_type", filters.eventType],
    ["object_type", filters.objectType],
    ["actor_id", filters.actorId],
    ["acting_role", filters.actingRole],
    ["reason_contains", filters.reasonContains],
    ["occurred_from", filters.occurredFrom],
    ["occurred_to", filters.occurredTo],
    ["cursor", filters.cursor],
    ["limit", filters.limit ?? 50],
  ];
  for (const [key, value] of optionalParams) {
    if (value !== undefined && value !== "") params.set(key, String(value));
  }
  const response = await fetch(`/api/v1/audit-events?${params}`, {
    headers: identityHeaders(identity),
    signal,
  });
  return parseResponse<AuditEventPage>(response);
}

export async function fetchAnnualBudget(
  annualBudgetId: string,
  identity: ApiIdentity,
  signal?: AbortSignal,
): Promise<AnnualBudgetResponse> {
  const response = await fetch(`/api/v1/annual-budgets/${encodeURIComponent(annualBudgetId)}`, {
    headers: identityHeaders(identity),
    signal,
  });
  return parseResponse<AnnualBudgetResponse>(response);
}

export async function fetchPublicationReadiness(
  revisionId: string,
  identity: ApiIdentity,
  signal?: AbortSignal,
): Promise<PublicationReadiness> {
  const response = await fetch(
    `/api/v1/revisions/${encodeURIComponent(revisionId)}/publication-readiness`,
    { headers: identityHeaders(identity), signal },
  );
  return parseResponse<PublicationReadiness>(response);
}

export async function fetchTargetClosureVersion(
  versionId: string,
  identity: ApiIdentity,
  signal?: AbortSignal,
): Promise<TargetClosureVersion> {
  const response = await fetch(
    `/api/v1/target-closure-result-versions/${encodeURIComponent(versionId)}`,
    { headers: identityHeaders(identity), signal },
  );
  return parseResponse<TargetClosureVersion>(response);
}

export async function fetchRevenueResultVersion(
  versionId: string,
  identity: ApiIdentity,
  signal?: AbortSignal,
): Promise<RevenueResultVersion> {
  const response = await fetch(
    `/api/v1/revenue-result-versions/${encodeURIComponent(versionId)}`,
    { headers: identityHeaders(identity), signal },
  );
  return parseResponse<RevenueResultVersion>(response);
}

export async function fetchHeadcountResultVersion(
  versionId: string,
  identity: ApiIdentity,
  signal?: AbortSignal,
): Promise<HeadcountResultVersion> {
  return parseResponse<HeadcountResultVersion>(await fetch(`/api/v1/headcount-result-versions/${encodeURIComponent(versionId)}`, {
    headers: identityHeaders(identity), signal,
  }));
}

export async function fetchLatestOptimizationRun(
  revisionId: string,
  identity: ApiIdentity,
  signal?: AbortSignal,
): Promise<OptimizationRun | null> {
  const response = await fetch(
    `/api/v1/revisions/${encodeURIComponent(revisionId)}/optimization-runs/latest`,
    { headers: identityHeaders(identity), signal },
  );
  if (response.status === 404) return null;
  return parseResponse<OptimizationRun>(response);
}

export async function createOptimizationRun(
  revisionId: string,
  identity: ApiIdentity,
): Promise<OptimizationRun> {
  const response = await fetch(
    `/api/v1/revisions/${encodeURIComponent(revisionId)}/optimization-runs`,
    {
      method: "POST",
      headers: {
        ...identityHeaders(identity),
        "Content-Type": "application/json",
        "Idempotency-Key": newRequestId(),
      },
      body: JSON.stringify({ solver_adapter: "CP_SAT_ENTRY_AND_RATES" }),
    },
  );
  return parseResponse<OptimizationRun>(response);
}

export async function fetchCurrentDiscountUnlock(
  revisionId: string,
  inputFingerprint: string,
  identity: ApiIdentity,
  signal?: AbortSignal,
): Promise<DiscountUnlock | null> {
  const params = new URLSearchParams({ input_fingerprint: inputFingerprint });
  const response = await fetch(
    `/api/v1/revisions/${encodeURIComponent(revisionId)}/discount-unlocks/current?${params}`,
    { headers: identityHeaders(identity), signal },
  );
  if (response.status === 404) return null;
  return parseResponse<DiscountUnlock>(response);
}

export async function requestDiscountUnlock(
  revisionId: string,
  runId: string,
  proofId: string,
  reason: string,
  identity: ApiIdentity,
): Promise<DiscountUnlock> {
  const response = await fetch(
    `/api/v1/revisions/${encodeURIComponent(revisionId)}/discount-unlocks:request`,
    {
      method: "POST",
      headers: {
        ...identityHeaders(identity),
        "Content-Type": "application/json",
        "Idempotency-Key": newRequestId(),
        "If-Match": `"${revisionId}"`,
      },
      body: JSON.stringify({
        optimization_run_id: runId,
        infeasibility_proof_id: proofId,
        reason,
      }),
    },
  );
  return parseResponse<DiscountUnlock>(response);
}

export async function confirmDiscountUnlock(
  revisionId: string,
  requestId: string,
  identity: ApiIdentity,
): Promise<DiscountUnlock> {
  const response = await fetch(
    `/api/v1/revisions/${encodeURIComponent(revisionId)}/discount-unlocks:confirm`,
    {
      method: "POST",
      headers: {
        ...identityHeaders(identity),
        "Content-Type": "application/json",
        "Idempotency-Key": newRequestId(),
        "If-Match": `"${revisionId}"`,
      },
      body: JSON.stringify({ discount_unlock_request_id: requestId }),
    },
  );
  return parseResponse<DiscountUnlock>(response);
}

export async function applyRebalancingScenario(
  revisionId: string,
  scenarioId: string,
  reason: string,
  identity: ApiIdentity,
): Promise<ScenarioApplication> {
  const response = await fetch(
    `/api/v1/revisions/${encodeURIComponent(revisionId)}/scenarios/${encodeURIComponent(scenarioId)}:apply`,
    {
      method: "POST",
      headers: {
        ...identityHeaders(identity),
        "Content-Type": "application/json",
        "Idempotency-Key": newRequestId(),
        "If-Match": `"${revisionId}"`,
      },
      body: JSON.stringify({ reason }),
    },
  );
  return parseResponse<ScenarioApplication>(response);
}

export interface ScenarioApplicationStatus {
  current_revision_id: string;
  can_revert: boolean;
  blocking_code: string | null;
  application: ScenarioApplication;
}

export async function fetchScenarioApplicationStatus(
  revisionId: string, identity: ApiIdentity,
): Promise<ScenarioApplicationStatus | null> {
  const response = await fetch(
    `/api/v1/revisions/${encodeURIComponent(revisionId)}/scenario-application/current`,
    { headers: identityHeaders(identity) },
  );
  if (response.status === 404) return null;
  return parseResponse<ScenarioApplicationStatus>(response);
}

export async function revertScenarioApplication(
  revisionId: string,
  applicationId: string,
  reason: string,
  identity: ApiIdentity,
): Promise<ScenarioApplicationRevert> {
  const response = await fetch(
    `/api/v1/revisions/${encodeURIComponent(revisionId)}/scenario-applications/${encodeURIComponent(applicationId)}:revert`,
    {
      method: "POST",
      headers: {
        ...identityHeaders(identity),
        "Content-Type": "application/json",
        "Idempotency-Key": newRequestId(),
        "If-Match": `"${revisionId}"`,
      },
      body: JSON.stringify({ reason }),
    },
  );
  return parseResponse<ScenarioApplicationRevert>(response);
}

export async function confirmFormalPlan(
  revisionId: string,
  reason: string,
  identity: ApiIdentity,
): Promise<BudgetCalculationJob> {
  const response = await fetch(
    `/api/v1/revisions/${encodeURIComponent(revisionId)}:confirm-formal-plan`,
    {
      method: "POST",
      headers: {
        ...identityHeaders(identity),
        "Content-Type": "application/json",
        "Idempotency-Key": newRequestId(),
        "If-Match": `"${revisionId}"`,
      },
      body: JSON.stringify({ reason }),
    },
  );
  return parseResponse<BudgetCalculationJob>(response);
}

export async function fetchJob(
  jobId: string,
  identity: ApiIdentity,
  signal?: AbortSignal,
): Promise<JobResponse> {
  const response = await fetch(`/api/v1/jobs/${encodeURIComponent(jobId)}`, {
    headers: identityHeaders(identity),
    signal,
  });
  return parseResponse<JobResponse>(response);
}

export async function exportAuditArchive(
  revisionId: string,
  reason: string,
  identity: ApiIdentity,
): Promise<AuditArchiveExport> {
  const response = await fetch(`/api/v1/revisions/${encodeURIComponent(revisionId)}:export`, {
    method: "POST",
    headers: {
      ...identityHeaders(identity),
      "Content-Type": "application/json",
      "Idempotency-Key": newRequestId(),
    },
    body: JSON.stringify({ reason }),
  });
  return parseResponse<AuditArchiveExport>(response);
}

export async function downloadAuditArchive(
  auditArchiveId: string,
  identity: ApiIdentity,
): Promise<{ blob: Blob; fileName: string }> {
  const response = await fetch(
    `/api/v1/audit-archives/${encodeURIComponent(auditArchiveId)}/download`,
    { headers: identityHeaders(identity) },
  );
  if (!response.ok) await parseResponse<never>(response);
  const disposition = response.headers.get("Content-Disposition") ?? "";
  const match = disposition.match(/filename="([^"]+)"/);
  return {
    blob: await response.blob(),
    fileName: match?.[1] ?? `budget-audit-${auditArchiveId.slice(0, 8)}.zip`,
  };
}

export async function publishBudget(
  revisionId: string,
  publicationNote: string,
  identity: ApiIdentity,
): Promise<PublishedBudgetBaseline> {
  const response = await fetch(`/api/v1/revisions/${encodeURIComponent(revisionId)}:publish`, {
    method: "POST",
    headers: {
      ...identityHeaders(identity),
      "Content-Type": "application/json",
      "Idempotency-Key": newRequestId(),
      "If-Match": `"${revisionId}"`,
    },
    body: JSON.stringify({ publication_note: publicationNote.trim() || null }),
  });
  return parseResponse<PublishedBudgetBaseline>(response);
}
