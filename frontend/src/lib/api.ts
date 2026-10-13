/**
 * Unified API client.
 *
 * Auth model: the backend sets an HttpOnly `hc_session` cookie on
 * successful /auth/login | /auth/register | /crm/auth/login. Every
 * subsequent request carries it automatically via
 * `credentials: "include"`. The SPA never sees the raw api_key and
 * persists *nothing* in localStorage — the cookie is the single source
 * of truth for session state, owned by the browser and cleared by
 * /auth/logout.
 *
 * Non-browser callers (the hermes binary, CLIs) still authenticate via
 * `Authorization: Bearer <api_key>`; the backend accepts either.
 */
export class ApiError extends Error {
  status: number;
  detail: unknown;
  constructor(status: number, detail: unknown) {
    super(typeof detail === "string" ? detail : JSON.stringify(detail));
    this.status = status;
    this.detail = detail;
  }
}

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
  opts: { profileId?: string | null } = {},
): Promise<T> {
  const headers: Record<string, string> = {};
  if (body !== undefined) headers["content-type"] = "application/json";
  if (opts.profileId) headers["X-Profile-Id"] = opts.profileId;
  // Explicit per-request locale: backend i18n middleware honours this
  // BEFORE session-stored locale, so a stale ``session["locale"]``
  // (left over from a previous toggle) can't shadow the user's current
  // localStorage pick. localStorage is the SPA's source of truth; this
  // header keeps the backend in sync without needing the session ping
  // to have succeeded earlier.
  try {
    const stored = window.localStorage.getItem("hermes.locale");
    if (stored === "zh-CN" || stored === "en-US") {
      headers["X-Locale"] = stored;
    }
  } catch { /* localStorage unavailable — let backend resolve */ }
  const res = await fetch(path, {
    method,
    headers,
    credentials: "include",  // send hc_session cookie
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let data: unknown = null;
  const text = await res.text();
  if (text) {
    try { data = JSON.parse(text); } catch { data = text; }
  }
  if (!res.ok) {
    const detail = (data && typeof data === "object" && "detail" in data)
      ? (data as { detail: unknown }).detail : data;
    throw new ApiError(res.status, detail ?? res.statusText);
  }
  return data as T;
}

// ── Auth ──────────────────────────────────────────────────────────────
export interface CurrentUser {
  user_id: string;
  email: string | null;
  is_admin: boolean;
  // Optional CRM enrichment (may be absent if CRM schema not reachable).
  id?: number;
  username?: string;
  realname?: string | null;
  mobile?: string | null;
  avatar?: number | null;
  description?: string | null;
  plan?: string;
  status?: number;
  created_at?: string | null;
  department?: { id: number; name: string } | null;
  role?: { id: number; name: string; description?: string | null } | null;
  report_to?: Array<{ id: number; name: string }>;
  // Active-profile count vs effective quota. `limit: null` = unlimited
  // (admins, or roles/users left at NULL). Used by the profile selector
  // to disable "new profile" when at capacity.
  profile_usage?: { used: number; limit: number | null };
  // Phase 5.2 — true when this user is in any departments.manager_ids
  // (recursive). Drives whether the SPA shows the (self/team/org)
  // scope segment switcher on list / dashboard pages.
  is_manager?: boolean;
}
export async function getMe(): Promise<CurrentUser> {
  return request<CurrentUser>("GET", "/api/me");
}
export async function putMe(patch: {
  realname?: string | null;
  mobile?: string | null;
  description?: string | null;
}): Promise<CurrentUser> {
  return request<CurrentUser>("PUT", "/api/me", patch);
}
export async function changeMyPassword(
  currentPassword: string,
  newPassword: string,
): Promise<{ ok: boolean }> {
  return request<{ ok: boolean }>("POST", "/api/me/password", {
    current_password: currentPassword,
    new_password: newPassword,
  });
}
export interface MyPermissionLeaf { code: string; name: string; granted: boolean }
export interface MyPermissionModule {
  id: number;
  name: string;
  title?: string | null;
  kind?: string | null;
  description?: string | null;
  permissions: MyPermissionLeaf[];
}
export interface MyPermissions {
  is_admin: boolean;
  role: { id: number; name: string; description?: string | null } | null;
  /** Flat ``["module.action", ...]`` — preferred for nav / button gating. */
  grants: string[];
  modules: MyPermissionModule[];
}
export async function getMyPermissions(): Promise<MyPermissions> {
  return request<MyPermissions>("GET", "/api/me/permissions");
}

/** A single entry in the user's profile-active LLM model list. */
export interface ActiveModel {
  /** Stable composite id: ``<provider_id>::<model_id>``. */
  id: string;
  /** Display name (the provider's preferred label, falls back to model id). */
  label: string;
  /** Provider key from ``providers_json.providers`` — e.g. "openai". */
  provider_id: string;
}

/** Response shape from ``GET /api/me/active-models``. */
export interface ActiveModelsResponse {
  models: ActiveModel[];
  /** ``"<provider_id>::<model_id>"`` of the profile's default model,
   *  or ``null`` if the user hasn't picked one. */
  default_id: string | null;
  /** ``false`` when the user has no providers_json yet — front-end
   *  should render a "go configure" hint instead of an empty picker. */
  configured: boolean;
}

export async function getActiveModels(): Promise<ActiveModelsResponse> {
  return request<ActiveModelsResponse>("GET", "/api/me/active-models");
}

// The `api_key` in these responses is returned for backward compat
// (legacy dashboard / CLIs); the SPA ignores it — auth lives entirely
// in the HttpOnly cookie the server sets on success.
export async function loginWithEmail(email: string, password: string) {
  return request<{ api_key: string; is_admin?: boolean }>(
    "POST", "/auth/login", { email, password },
  );
}
export async function loginWithUsername(username: string, password: string) {
  return request<{ api_key: string; is_admin?: boolean; user_id: number }>(
    "POST", "/crm/auth/login", { username, password },
  );
}
export async function registerUser(email: string, password: string) {
  return request<{ api_key: string; is_admin?: boolean }>(
    "POST", "/auth/register", { email, password },
  );
}
export async function logout() {
  try { await request<void>("POST", "/auth/logout"); } catch { /* noop */ }
}
export async function getRegistrationStatus() {
  return request<{ allow_registration: boolean }>(
    "GET", "/api/config/registration-status",
  );
}
export async function me() {
  return request<{ id?: number; user_id?: number; email?: string; username?: string; is_admin?: boolean }>(
    "GET", "/api/me",
  );
}

// ── CRM records ───────────────────────────────────────────────────────
export interface CrmRecord {
  id: number;
  [k: string]: any;
}
export async function listRecords(module: string, params: {
  page?: number; page_size?: number; search?: string; segment?: string;
  scope?: ScopeKind;  // 5.2.A — manager 切团队/全部 view
  owner_id?: number;  // v1.7 — narrow to specific subordinate/user
  view_id?: number;   // Spec 2 — custom saved view (module_views)
  filters?: ViewFilter[];           // Spec 4 — ad-hoc advanced search
  filter_logic?: "all" | "any";     // Spec 4
  sort_field?: string;              // field sort — real column; charter default when absent
  sort_direction?: "ASC" | "DESC";  // pairs with sort_field
} = {}) {
  const qs = new URLSearchParams();
  if (params.page) qs.set("page", String(params.page));
  if (params.page_size) qs.set("limit", String(params.page_size));
  if (params.search) qs.set("search", params.search);
  if (params.segment && params.segment !== "all") qs.set("segment", params.segment);
  if (params.scope && params.scope !== "self") qs.set("scope", params.scope);
  if (params.owner_id != null) qs.set("owner_id", String(params.owner_id));
  if (params.view_id != null) qs.set("view_id", String(params.view_id));
  if (params.sort_field) {
    qs.set("sort_field", params.sort_field);
    qs.set("sort_direction", params.sort_direction === "ASC" ? "ASC" : "DESC");
  }
  if (params.filters && params.filters.length) {
    qs.set("filters", JSON.stringify(params.filters));
    if (params.filter_logic) qs.set("filter_logic", params.filter_logic);
  }
  const q = qs.toString() ? `?${qs}` : "";
  return request<{ data: CrmRecord[]; total?: number; segment?: string }>(
    "GET", `/crm/records/${module}${q}`,
  );
}
export async function listSegments(module: string, params: {
  scope?: ScopeKind;
  owner_id?: number;
} = {}) {
  const qs = new URLSearchParams();
  if (params.scope && params.scope !== "self") qs.set("scope", params.scope);
  if (params.owner_id != null) qs.set("owner_id", String(params.owner_id));
  const q = qs.toString() ? `?${qs}` : "";
  return request<{
    module: string;
    segments: { id: string; label: string; count: number }[];
  }>("GET", `/crm/segments/${module}${q}`);
}
/**
 * Type-specific field configuration, stored in the ``module_fields.field_config``
 * JSON column. All keys optional — legacy rows have ``field_config = null``.
 *   label_i18n  — per-locale label overrides; resolver falls back to field_label.
 *   autonumber  — auto-number engine config (Phase P6 sub-panel).
 *   formula     — formula engine config (Phase P6 sub-panel).
 *   relation    — explicit relation target override (default uses naming convention).
 */
export interface AiFillConf {
  enabled: boolean;
  /** AI autofill mode (M1). Absent on legacy configs → treated as "prompt". */
  mode?: "classify" | "summarize" | "web_agent" | "prompt";
  output_type: "text" | "number" | "picklist" | "boolean";
  mission: string;
  context_rules?: string;
  /** @deprecated 退役:输出形状由 mode+output_type+字段选项 自动决定,不再让用户写。 */
  output_spec?: string;
  inputs?: { fields?: string[]; record?: { enabled: boolean }; interactions?: { enabled: boolean; limit?: number } };
  tools?: string[];
  refresh?: { on_create?: boolean; on_change?: boolean; schedule?: string | null };
  min_confidence?: number;
  /** classify mode: allow AI to introduce new picklist options not in the preset list. */
  allow_new_options?: boolean;
}

export interface AiFieldMeta {
  source?: string;
  confidence?: number;
  generated_at?: string;
  basis?: string[];
  written_value?: unknown;
  abstained?: boolean;
  reason?: string | null;
}

export interface FieldConfig {
  label_i18n?: Record<string, string>;
  autonumber?: { prefix: string; digits: number; start: number; date_token: string };
  formula?: { expression: string };
  relation?: { target_module: string };
  ai_fill?: AiFillConf;
}

export interface CrmFieldMeta {
  id: number;
  field_name: string;
  field_label: string;
  /** Locale-resolved label for display (admin Schema Hub list route).
   *  Falls back to ``field_label`` when absent (e.g. on local drafts). */
  display_label?: string;
  field_type: string;        // "text", "select", "user", "department", "relation", ...
  options?: string | null;   // CSV for select/picklist
  datainfo?: string | null;  // "<module_id>#..." for relation
  sort_order?: number;
  is_hidden?: boolean;
  is_readonly?: boolean;
  is_system?: boolean;
  is_required?: boolean;
  is_searchable?: boolean;
  description?: string | null;
  default_value?: string | null;
  field_config?: FieldConfig | null;
}
export interface LineItem {
  line_id: number;
  products_id: number;
  products_name: string;
  productno?: string;
  model?: string;
  specifications?: string;
  quantity: number;
  price?: number | null;
  tax_rate?: number | null;
  discount_rate?: number | null;
  subtotal?: number | null;
  total?: number | null;
  remark?: string;
}
export async function getRecord(module: string, id: number) {
  return request<{
    data: CrmRecord;
    fields?: CrmFieldMeta[];
    module_name?: string;
    has_products?: boolean;
    products?: LineItem[];
    /** generic 子明细行:{子明细名: [行]}(detail_query._load_subdetails)。 */
    details?: Record<string, Record<string, unknown>[]>;
    /** 该模块是否配置了审批链(approval_config 存在)。 */
    has_approval?: boolean;
    /** 该记录最新审批实例 + 步骤;无则为 {}。 */
    approval?: ApprovalDetail | Record<string, never>;
  }>("GET", `/crm/records/${module}/${id}`);
}

// ---- 审批链(approval chain)----

export interface ApprovalStep {
  id: number;
  step_order: number;
  step_name: string;
  approver_ids: string;
  approver_names?: string[];  // 配置的审批人姓名(待审/未来步骤显示「该由谁审」)
  status: string;  // pending | waiting | passed | rejected | backed
  remark?: string | null;
  actual_approver_id?: number | null;
  actual_approver_name?: string | null;
  approved_at?: string | null;
}

export interface ApprovalLog {
  id: number;
  action: string;  // submit | approve | reject | back | transfer | cancel
  step_order?: number | null;
  step_name?: string | null;
  actor_id?: number | null;
  actor_name?: string | null;
  target_id?: number | null;
  target_name?: string | null;
  remark?: string | null;
  created_at?: string | null;
}

export interface ApprovalDetail {
  approval: {
    id: number;
    module_id: number;
    data_id: number;
    status: string;  // pending | passed | rejected | cancelled
    current_step: number;
    created_by: number;
  };
  steps: ApprovalStep[];
  logs?: ApprovalLog[];
}

export interface PendingApproval {
  id: number;
  module_id: number;
  data_id: number;
  created_by: number;
  module_name: string;
  table_name: string;
  step_name: string;
  step_order: number;
  created_at: string;
}

export function submitApproval(module: string, recordId: number) {
  return request<{ submitted?: boolean; approval_id?: number; skipped?: string }>(
    "POST", "/crm/approvals/submit", { module, record_id: recordId });
}

export function fetchPendingApprovals() {
  return request<{ items: PendingApproval[]; count: number }>(
    "GET", "/crm/approvals/pending");
}

export function approveChain(id: number, remark = "") {
  return request<{ status: string; next_step: number | null }>(
    "POST", `/crm/approvals/${id}/approve`, { remark });
}

export function rejectChain(id: number, remark = "") {
  return request<{ status: string }>(
    "POST", `/crm/approvals/${id}/reject`, { remark });
}

export function backChain(id: number, targetStep: number, remark = "") {
  return request<{ status: string; from_step: number; to_step: number }>(
    "POST", `/crm/approvals/${id}/back`, { target_step: targetStep, remark });
}

export function transferChain(id: number, fromUserId: number, toUserId: number) {
  return request<{ touched_steps: number }>(
    "POST", `/crm/approvals/${id}/transfer`,
    { from_user_id: fromUserId, to_user_id: toUserId });
}

export function getApprovalConfig(module: string) {
  return request<{ config: { module_id: number; steps: ApprovalConfigStep[]; allow_edit_after_approve: number } | null }>(
    "GET", `/crm/modules/${module}/approval-config`);
}

export interface ApprovalConfigStep {
  step_name: string;
  approver_ids: number[];
}

export function putApprovalConfig(
  module: string,
  steps: ApprovalConfigStep[],
  allowEdit: number,
) {
  return request<{ ok: boolean }>(
    "PUT", `/crm/modules/${module}/approval-config`,
    { steps, allow_edit_after_approve: allowEdit });
}

export interface RelatedRecord {
  id: number;
  name: string;
  [k: string]: any;  // module-specific extra cols (status, due_at, …)
}

export async function getRelatedRecords(module: string, id: number) {
  return request<{
    groups: Record<string, RelatedRecord[]>;
    group_titles?: Record<string, string>;
  }>("GET", `/crm/records/${module}/${id}/related`);
}
export interface ListFieldsConfig {
  row1: string[];
  row2: string[];
}
export async function listModules() {
  return request<{ data: {
    name: string;
    label: string;
    has_access?: boolean;
    /** Admin-curated chip-row (migration 0020). null = use frontend default. */
    headline_fields?: string[] | null;
    /** Admin-curated list-item two-row layout (migration 0099). null = default. */
    list_fields?: ListFieldsConfig | null;
  }[] }>(
    "GET", "/crm/modules",
  );
}

export interface NewFieldSpec {
  field_name: string;
  field_label: string;
  field_type: string;
}

/** Admin: 新建顶层自定义模块。建表+注册+系统/初始字段+默认权限,原子。 */
export async function createModule(payload: {
  name: string;
  title: string;
  title_en?: string;
  initial_fields?: NewFieldSpec[];
}) {
  return request<{ ok: boolean; module_id: number; name: string }>(
    "POST", "/crm/modules", payload,
  );
}

/** Admin: 改模块显示名(中/英),不动物理结构。 */
export async function renameModule(
  name: string, payload: { title?: string; title_en?: string },
) {
  return request<{ ok: boolean; name: string; title: string }>(
    "PATCH", `/crm/modules/${encodeURIComponent(name)}`, payload,
  );
}

/** Admin: 安全删除顶层自定义模块(含子明细)。默认 archive=true,删前快照可还原。 */
export async function deleteModule(
  name: string, opts?: { archive?: boolean; reason?: string },
) {
  const q = new URLSearchParams();
  if (opts?.archive === false) q.set("archive", "false");
  if (opts?.reason) q.set("reason", opts.reason);
  const qs = q.toString();
  return request<{
    ok: boolean; name: string; module_id: number;
    subdetails: string[]; archive_id: number | null;
  }>("DELETE", `/crm/modules/${encodeURIComponent(name)}${qs ? "?" + qs : ""}`);
}

/** Admin: 内置核心模块不可删(镜像后端 PROTECTED_MODULES)。 */
export const PROTECTED_MODULES = new Set([
  "accounts", "contacts", "leads", "potentials", "quotes", "salesorders",
  "tasks", "products", "receivables", "payments", "expenses", "projects",
]);

/** Admin: 给模块挂子明细(product/generic)。 */
export async function createSubdetail(parent: string, payload: {
  name: string;
  title: string;
  title_en?: string;
  detail_kind?: "product" | "generic";
  initial_fields?: NewFieldSpec[];
}) {
  return request<{ ok: boolean; module_id: number; name: string }>(
    "POST", `/crm/modules/${encodeURIComponent(parent)}/details`, payload,
  );
}

export interface SubdetailField {
  field_name: string;
  field_label: string;
  field_type: string;
  options?: string | null;
  is_readonly?: boolean | null;
  /** 行内公式等类型配置;formula.expression 供编辑器实时预览。 */
  field_config?: FieldConfig | null;
}
export interface SubdetailDef {
  name: string;
  title: string;
  title_en?: string | null;
  detail_kind: "product" | "generic";
  fields: SubdetailField[];
}

/** 列出某模块的子明细 + 各自字段(供明细行编辑器渲染)。 */
export async function listSubdetails(module: string) {
  return request<{ module_name: string; subdetails: SubdetailDef[] }>(
    "GET", `/crm/modules/${encodeURIComponent(module)}/subdetails`,
  );
}

export interface WritebackRule {
  id: number;
  calc_method: string;
  source_module_name: string;
  source_field_name: string;
  source_field_label?: string;
  target_module_name: string;
  target_field_name: string;
  target_field_label?: string;
}

/** 列出以 sourceModule 为源的汇总规则。 */
export async function listWritebackRules(sourceModule: string) {
  return request<{ module_name: string; rules: WritebackRule[] }>(
    "GET", `/crm/modules/${encodeURIComponent(sourceModule)}/writeback-rules`,
  );
}

/** 建一条汇总规则:sourceModule(子明细)的 source_field 按 calc 聚合到
 *  target_module(父)的 target_field。 */
export async function createWritebackRule(sourceModule: string, body: {
  source_field: string; target_module: string;
  target_field: string; calc_method: string;
}) {
  return request<{ ok: boolean; id: number }>(
    "POST", `/crm/modules/${encodeURIComponent(sourceModule)}/writeback-rules`, body,
  );
}

export async function deleteWritebackRule(sourceModule: string, ruleId: number) {
  return request<{ ok: boolean }>(
    "DELETE", `/crm/modules/${encodeURIComponent(sourceModule)}/writeback-rules/${ruleId}`,
  );
}

export interface WritebackTarget {
  module: string;
  label?: string | null;
  /** 本模块上指向该 target 的外键列(= {target}_id)。 */
  fk_field: string;
  fields: { field_name: string; field_label?: string | null }[];
}

/** 本模块通过 {目标}_id 外键关联到的模块 + 各自数值字段(跨模块汇总的 target 候选)。 */
export async function fetchWritebackTargets(module: string) {
  return request<{ targets: WritebackTarget[] }>(
    "GET", `/crm/modules/${encodeURIComponent(module)}/writeback-targets`,
  );
}

/**
 * Admin-only: full field metadata for a module, independent of any record.
 * Backend route: ``GET /crm/modules/{module_name}/fields``. The Schema Hub
 * builder uses this instead of fishing for ``fields`` via a record-detail
 * call, so it works for modules with no rows.
 */
export async function getModuleFields(module: string) {
  return request<{
    module_name: string;
    fields: CrmFieldMeta[];
  }>("GET", `/crm/modules/${encodeURIComponent(module)}/fields`);
}

/**
 * Admin-only: overwrite (or clear) the chip-row pick list for a module.
 * Pass ``fields: null`` to drop the override and fall back to the
 * frontend default in ``moduleHeadlines.ts``.
 */
export async function updateModuleHeadline(name: string, fields: string[] | null) {
  return request<{ success: boolean; fields: string[] | null }>(
    "PUT",
    `/crm/modules/${encodeURIComponent(name)}/headline`,
    { fields },
  );
}

/**
 * Admin: overwrite (or clear) the two-row list-item field layout for a
 * module. ``fields: null`` clears the org default (frontend default takes
 * over). Field count is uncapped (unlike headline).
 */
export async function updateModuleListFields(
  name: string, fields: ListFieldsConfig | null,
) {
  return request<{ success: boolean; fields: ListFieldsConfig | null }>(
    "PUT",
    `/crm/modules/${encodeURIComponent(name)}/list-fields`,
    { fields },
  );
}

// ── Custom views (module_views) ───────────────────────────────────────
export interface ViewFilter {
  field: string;
  operator: string;
  value: unknown;
}
/** Saved-view render type (Spec 3). */
export type ViewType = "table" | "card" | "kanban" | "timeline" | "calendar";
/** Per-view type-specific config (Spec 3). Only the keys relevant to the
 *  view's type are populated. */
export interface ViewConfig {
  group_by?: string;                 // kanban: split into columns by this select field
  card_fields?: string[];            // card: which fields to show (empty = inherit list_fields)
  card_cover_field?: string | null;  // card: image field for cover
  card_per_row?: number;             // card: columns per row
  date_field?: string;               // timeline/calendar: primary date field
  end_date_field?: string | null;    // calendar: span end date
  color_field?: string | null;       // calendar: color-by field (select)
  content_fields?: string[];         // timeline/calendar: fields shown on the card/cell
  sort_field?: string;               // record sort: real column; absent = charter (recent-activity) default
  sort_direction?: "ASC" | "DESC";   // pairs with sort_field
}
export interface ModuleView {
  id: number;
  module_id?: number;
  name: string;
  filters: ViewFilter[];
  filter_logic?: "all" | "any";
  type?: ViewType;
  view_config?: ViewConfig | null;
  is_default?: boolean;
  is_public?: boolean;
  role_ids?: number[] | null;
  sort_order?: number;
  created_by?: number;
}
export interface ViewWriteBody {
  name: string;
  filters: ViewFilter[];
  filter_logic?: "all" | "any";
  type?: ViewType;
  view_config?: ViewConfig | null;
  is_default?: boolean;
  is_public?: boolean;
  role_ids?: number[] | null;
  sort_order?: number;
}
export async function listViews(module: string) {
  return request<{ module: string; views: ModuleView[] }>(
    "GET", `/crm/modules/${encodeURIComponent(module)}/views`,
  );
}
export async function createView(module: string, body: ViewWriteBody) {
  return request<{ success: boolean; view: ModuleView }>(
    "POST", `/crm/modules/${encodeURIComponent(module)}/views`, body,
  );
}
export async function updateView(module: string, viewId: number, body: ViewWriteBody) {
  return request<{ success: boolean; view: ModuleView }>(
    "PUT", `/crm/modules/${encodeURIComponent(module)}/views/${viewId}`, body,
  );
}
export async function deleteView(module: string, viewId: number) {
  return request<{ success: boolean }>(
    "DELETE", `/crm/modules/${encodeURIComponent(module)}/views/${viewId}`,
  );
}

// ── Built-in segment config ───────────────────────────────────────────
export interface SegmentItem {
  id: string;
  enabled: boolean;
  label: string;
  order: number;
  params?: { days: number };
}
export async function getSegmentsConfig(module: string) {
  return request<{ module: string; items: SegmentItem[] }>(
    "GET", `/crm/modules/${encodeURIComponent(module)}/segments-config`,
  );
}
export async function updateSegmentsConfig(
  module: string, config: { items: SegmentItem[] } | null,
) {
  return request<{ success: boolean; items: SegmentItem[] }>(
    "PUT", `/crm/modules/${encodeURIComponent(module)}/segments-config`,
    { config },
  );
}

// ── Interactions ──────────────────────────────────────────────────────
export interface Interaction {
  id: string;
  kind: string;
  occurred_at: string;
  actors?: any[];
  subjects?: any[];
  content?: any;
  source?: string;
  /** When this interaction was caused by an action originating in
   *  the decisions surface, ``provenance.caused_by_thread`` holds the
   *  decision thread UUID. Charter §一定律 1 (events-first) — the
   *  link is in the event itself, no shadow table. The timeline
   *  surfaces this so the manager sees "← AI 推荐 (来自决策 thread X)". */
  provenance?: any;
}
/**
 * Partial field update — `patch` is a {field_name: value} dict that the
 * server validates against module_fields metadata. Response carries the
 * updated record id on success; on 400 the server returns
 * `{detail: {field, message}}` which request() surfaces as ApiError.
 */
export async function updateRecord(module: string, id: number, patch: Record<string, any>) {
  return request<{ success: boolean; id: number }>(
    "PUT", `/crm/records/${encodeURIComponent(module)}/${id}`, patch,
  );
}

export async function aiFillRecord(
  module: string, id: number, fields?: string[],
) {
  return request<{ status: string; results: Array<{ field: string; ok?: boolean;
    value?: unknown; abstained?: boolean; reason?: string; skipped?: string }> }>(
    "POST",
    `/crm/modules/${encodeURIComponent(module)}/records/${id}/ai-fill`,
    { fields },
  );
}

/**
 * Generate a full AI-field config from a one-line intent. The backend
 * drafts output_type / mission / context_rules / output_spec (and picklist
 * options) so a non-technical admin can describe the need in plain language
 * and have every config box filled. Returns the same shape as a template.
 */
export async function generateAiFieldConfig(module: string, intent: string) {
  return request<{ config: {
    output_type: "text" | "number" | "picklist" | "boolean";
    label?: string; mission: string; context_rules?: string; output_spec: string;
    options?: string[]; with_interactions?: boolean;
  } }>("POST", "/crm/ai-field/draft-config", { module, intent });
}

/**
 * Create a new CRM record. ``data`` is a field_name → value map; the
 * server's Save layer validates types and required fields and returns
 * 400 / 403 via ApiError (detail = {field, message} for validation).
 */
export async function createRecord(module: string, data: Record<string, any>) {
  return request<{ success: boolean; id: number }>(
    "POST", `/crm/records/${encodeURIComponent(module)}`, data,
  );
}

/**
 * Field metadata + default values for a new-record form. The /edit
 * endpoint wraps everything in ``data`` and names the field array
 * ``form_fields`` (different shape from getRecord!), so we normalize.
 */
export async function getEditFormNew(module: string): Promise<{
  fields: CrmFieldMeta[];
  defaults: Record<string, any>;
  module_name?: string;
  /** True for quotes/salesorders; UI renders a LineItemsEditor when set. */
  has_products: boolean;
}> {
  const r = await request<{
    data?: { form_fields?: CrmFieldMeta[]; [k: string]: any };
    module_name?: string;
    has_products?: boolean;
  }>("GET", `/crm/records/${encodeURIComponent(module)}/edit`);
  const data = r.data || {};
  const fields = (data.form_fields || []) as CrmFieldMeta[];
  // Server pre-fills sensible defaults (owner_by = current user, etc).
  const defaults: Record<string, any> = {};
  for (const [k, v] of Object.entries(data)) {
    if (k === "form_fields" || k === "form_html" || k === "id"
        || k === "relation_field_data" || k === "products") continue;
    defaults[k] = v;
  }
  return {
    fields, defaults,
    module_name: r.module_name,
    has_products: Boolean(r.has_products),
  };
}

/** Fetch existing record's edit-form payload — same shape as getEditFormNew
 *  but with the record's current values + line items populated. Used by
 *  AllInfoDrawer to load products for inline edit on quotes/salesorders. */
export async function getEditFormExisting(module: string, id: number): Promise<{
  has_products: boolean;
  products: any[];
}> {
  const r = await request<{
    data?: { products?: any[]; [k: string]: any };
    has_products?: boolean;
  }>("GET", `/crm/records/${encodeURIComponent(module)}/${id}/edit`);
  return {
    has_products: Boolean(r.has_products),
    products: r.data?.products || [],
  };
}

/** Lightweight user picker (any auth user). For ownerby / user FK inputs. */
export async function listUsersPicker(search = "", limit = 50) {
  const qs = new URLSearchParams();
  if (search) qs.set("search", search);
  if (limit !== 50) qs.set("limit", String(limit));
  const q = qs.toString() ? `?${qs}` : "";
  return request<{ id: number; name: string; username?: string; department_name?: string }[]>(
    "GET", `/crm/users/picker${q}`,
  );
}

/**
 * Soft-delete a CRM record (sets ``deleted=true``, recoverable in DB).
 * Backend route: ``DELETE /crm/records/{module_name}/{record_id}``.
 *
 * Permission: caller must have the module's ``delete`` action; server
 * returns 403 otherwise. ApiError surfaces both 403 (permission) and
 * 404 (already gone / wrong id) with the server's ``detail`` text so
 * the UI can show "权限不足" vs "记录不存在".
 */
export async function deleteRecord(module: string, id: number) {
  return request<{ success: boolean }>(
    "DELETE", `/crm/records/${encodeURIComponent(module)}/${id}`,
  );
}

/**
 * Bulk soft-delete up to 200 records in one call. Each id is attempted
 * independently — a single forbidden/missing id doesn't roll back the
 * rest. Returns a per-id breakdown so the UI can summarize partial
 * success ("Deleted 18, 2 failed").
 *
 * Backend: ``POST /crm/records/{module}/bulk_delete`` (see
 * ``hermes_cloud/app/crm/routes/__init__.py``).
 */
export interface BulkDeleteFailure {
  id: number;
  reason: "not_found" | "forbidden" | "error";
  detail?: string;
}
export interface BulkDeleteResult {
  deleted: number;
  failures: BulkDeleteFailure[];
}
export async function bulkDeleteRecords(module: string, ids: number[]) {
  return request<BulkDeleteResult>(
    "POST", `/crm/records/${encodeURIComponent(module)}/bulk_delete`,
    { ids },
  );
}

// ── CRM record collaborators ──────────────────────────────────────────
export interface RecordCollaborator {
  user_id: number;
  username: string;
  realname: string | null;
  can_view: boolean;
  can_edit: boolean;
  created_at: string;
}

export async function listRecordCollaborators(module: string, recordId: number) {
  return request<{ collaborators: RecordCollaborator[] }>(
    "GET", `/crm/records/${encodeURIComponent(module)}/${recordId}/collaborators`,
  );
}

export async function addRecordCollaborator(
  module: string, recordId: number,
  payload: { user_id: number; can_view: boolean; can_edit: boolean },
) {
  return request<{ user_id: number; can_view: boolean; can_edit: boolean }>(
    "POST", `/crm/records/${encodeURIComponent(module)}/${recordId}/collaborators`,
    payload,
  );
}

export async function removeRecordCollaborator(
  module: string, recordId: number, userId: number,
) {
  return request<void>(
    "DELETE",
    `/crm/records/${encodeURIComponent(module)}/${recordId}/collaborators/${userId}`,
  );
}

// ── CRM Copilot (LLM tool-calling, Phase B) ──────────────────────────
export interface CopilotToolExecution {
  name: string;
  args: Record<string, any>;
  ok: boolean;
  result?: any;
  error?: string | null;
}
export interface CopilotSendResponse {
  reply: string;
  tool_executions: CopilotToolExecution[];
  /** True when the LLM call itself failed — caller should fall back to
   *  the regex parser. The backend has already done nothing. */
  fallback: boolean;
  error?: string;
}
export async function copilotCrmSend(payload: {
  module: string;
  record_id: number;
  message: string;
  history?: Array<{ role: "user" | "assistant"; content: string }>;
  /** ``"<provider_id>::<model_id>"`` from ModelPicker. When omitted,
   *  backend uses the user's profile default model. */
  model?: string | null;
  /** Normalized attachments from {@link uploadChatAttachments}. Images
   *  carry a ``data_url``; documents carry extracted ``text``. The
   *  backend inlines them into the dispatched user message. */
  attachments?: ChatAttachment[];
}): Promise<CopilotSendResponse> {
  return request<CopilotSendResponse>("POST", "/api/copilot/crm/send", payload);
}

// ── Scoped Copilot (schema / dashboard side panels) ──────────────────
// Runs the real agent scoped to a single module (schema) or dashboard
// tab. Same response shape as copilotCrmSend; the agent executes
// directly for admins and proposes for non-admins (decided backend-side).
export async function copilotScopedSend(payload: {
  scope: "schema" | "workflow" | "dashboard";
  context: { module?: string; tab_id?: string };
  message: string;
  history?: Array<{ role: "user" | "assistant"; content: string }>;
  model?: string | null;
  attachments?: ChatAttachment[];
}): Promise<CopilotSendResponse> {
  return request<CopilotSendResponse>("POST", "/api/copilot/scoped/send", payload);
}

// ── Copilot history (panel re-hydration) ─────────────────────────────
// Charter §定律 1: the conversation IS the event stream — re-hydrate
// from interactions where source LIKE 'copilot:%' for the open record.
export interface CopilotTurn {
  role: "user" | "assistant";
  text: string;
  at: string | null;
  tool_executions?: CopilotToolExecution[];
  /** Model id used for this assistant turn (assistant rows only). */
  model?: string | null;
}
export async function getCopilotHistory(
  module: string, recordId: number | undefined | null, limit = 40,
) {
  const q = new URLSearchParams({ module, limit: String(limit) });
  if (recordId !== undefined && recordId !== null) {
    q.set("record_id", String(recordId));
  }
  return request<{ turns: CopilotTurn[] }>(
    "GET", `/api/copilot/crm/history?${q}`,
  );
}

// Re-hydrate a scoped AI-assistant panel (schema builder / dashboard).
// Mirrors getCopilotHistory but hits the namespaced panel bucket
// (ai-schema:<module> / ai-dashboard:<tab>) so it never collides with
// the chatbox list-mode history. Same {turns} shape.
export async function copilotScopedHistory(
  scope: "schema" | "workflow" | "dashboard",
  ctx: { module?: string; tab_id?: string },
  limit = 40,
) {
  const q = new URLSearchParams({ scope, limit: String(limit) });
  if (scope === "schema" && ctx.module) q.set("module", ctx.module);
  if (scope === "dashboard" && ctx.tab_id) q.set("tab_id", ctx.tab_id);
  return request<{ turns: CopilotTurn[] }>(
    "GET", `/api/copilot/scoped/history?${q}`,
  );
}

export async function undoLastUpdate(module: string, recordId: number) {
  return request<{ success: boolean; reverted_fields: string[]; record_id: number }>(
    "POST", `/crm/records/${encodeURIComponent(module)}/${recordId}/undo`,
  );
}
export async function listInteractions(params: {
  module?: string; record_id?: number; kind?: string;
  source?: string; limit?: number; offset?: number;
}) {
  const qs = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== "") qs.set(k, String(v));
  });
  const q = qs.toString() ? `?${qs}` : "";
  return request<{ data: Interaction[]; total: number }>(
    "GET", `/api/interactions${q}`,
  );
}

// ── Touchpoints ───────────────────────────────────────────────────────
export async function postNote(payload: {
  module?: string; record_id?: number; content?: string; text?: string;
  next_contact_date?: string; progress?: string;
}) {
  return request<{ data: { interaction_id: string } }>(
    "POST", "/api/touchpoints/note", payload,
  );
}
export async function postChat(payload: {
  module?: string; record_id?: number; text: string;
}) {
  return request<{ data: { interaction_id: string } }>(
    "POST", "/api/touchpoints/chat", payload,
  );
}
export async function postPiiView(payload: {
  module?: string; record_id?: number; field: string;
}) {
  // Law 6 — emits kind='pii_view' with {field}, NO raw value, so the
  // audit log itself is not a PII exfiltration vector.
  return request<{ data: { interaction_id: string } }>(
    "POST", "/api/touchpoints/pii_view", payload,
  );
}

// ── Mail (IMAP receive + SMTP send) ───────────────────────────────────
export interface MailAccount {
  username: string;
  imap_host: string;
  imap_port: number;
  imap_use_ssl: boolean;
  imap_enabled: boolean;
  smtp_host: string | null;
  smtp_port: number | null;
  smtp_use_tls: boolean;
  has_password: boolean;
  last_poll_at: string | null;
  last_error: string | null;
}
export async function getMail() {
  return request<{ data: MailAccount | null }>("GET", "/api/mail");
}
export async function putMail(payload: {
  username: string;
  password?: string | null;
  imap_host: string;
  imap_port: number;
  imap_use_ssl: boolean;
  imap_enabled: boolean;
  smtp_host?: string | null;
  smtp_port?: number | null;
  smtp_use_tls: boolean;
}) {
  return request<{ data: MailAccount }>("PUT", "/api/mail", payload);
}
export async function deleteMail() {
  return request<{ data: { deleted: boolean } }>("DELETE", "/api/mail");
}
export interface MailTestLeg { ok: boolean; error?: string }
export interface MailTestResult {
  imap?: MailTestLeg;
  smtp?: MailTestLeg | null;
  skipped?: boolean;
  reason?: string;
}
export async function testMail() {
  return request<{ data: MailTestResult }>("POST", "/api/mail/test", {});
}

// ── Agents ────────────────────────────────────────────────────────────
// Charter v2 Phase 3 deleted /api/agents/* and the specialist concept.
// listAgents() (dev picker data source) and invokeAgent() (POST
// /api/agents/invoke fallback) both went with that. Copilot now uses
// /api/copilot/crm/send (see copilotCrmSend below) as its only path.
export interface AgentProfile {
  id: string;
  name: string;
  slug?: string;
  description?: string;
  is_default?: boolean;
  is_active?: boolean;
  created_at?: string;
  process?: {
    running: boolean;
    port?: number | null;
    pid?: number | null;
    idle_seconds?: number | null;
    // True when this row is served by the shared multiplex gateway (all
    // profiles share one port/pid). See `multiplex` below for the
    // deployment-level flag that survives the mux being down.
    multiplexed?: boolean | null;
  };
  // Deployment topology: true when the cloud runs one shared multiplex
  // gateway for every profile. The SPA hides per-profile start/stop controls
  // when true (there is no per-profile process to start or stop).
  multiplex?: boolean;
}
export async function listAgentProfiles() {
  return request<{ profiles: AgentProfile[] }>("GET", "/api/profiles");
}
export async function startMyProfile(profileId: string) {
  return request<{ ok: boolean; port?: number }>(
    "POST", `/api/profiles/${encodeURIComponent(profileId)}/start`,
  );
}
export async function stopMyProfile(profileId: string) {
  return request<{ ok: boolean }>(
    "DELETE", `/api/profiles/${encodeURIComponent(profileId)}/process`,
  );
}
export async function createAgentProfile(payload: { name: string; description?: string; slug?: string }) {
  return request<AgentProfile>("POST", "/api/profiles", payload);
}
export async function patchAgentProfile(id: string, payload: Partial<AgentProfile>) {
  return request<AgentProfile>("PATCH", `/api/profiles/${encodeURIComponent(id)}`, payload);
}
export async function deleteAgentProfile(id: string) {
  return request<void>("DELETE", `/api/profiles/${encodeURIComponent(id)}`);
}

// ── Per-agent config (requires X-Profile-Id) ──────────────────────────
export interface SkillEntry {
  name: string;
  description?: string;
  enabled: boolean;
  /** 'private' = agent-learned, user-owned. 'global' = org-shared.
   *  Older backends may omit this; treat as 'private'. */
  origin?: "private" | "global";
  /** 系统默认 CRM 技能(crm-*):只可增配、不可移除(后端 POST 同样强制)。
   *  Older backends omit this — fall back to the crm- name prefix. */
  locked?: boolean;
}

/** locked 的容错读取:老后端没有该字段时按 crm- 前缀判定。 */
export function isSkillLocked(s: Pick<SkillEntry, "name" | "locked">): boolean {
  return s.locked ?? s.name.startsWith("crm-");
}
export interface SkillsResponse {
  /** Backward-compat flat list (private + global). */
  skills: SkillEntry[];
  enabled: string[];
  /** Two-group split (preferred). May be missing on older backends. */
  private?: SkillEntry[];
  global?: SkillEntry[];
}
/**
 * Fetch the skill universe. ``profileId`` is optional — when omitted the
 * backend resolves the caller's default profile (see ``_resolve_profile_id``
 * in main.py). The skill *set* (bundled + org-global) is profile-independent,
 * so callers that only need the catalog (e.g. the onboarding template editor)
 * can leave it out and stay decoupled from any specific profile.
 */
export async function getSkills(profileId?: string) {
  return request<SkillsResponse>(
    "GET", "/api/user-config/skills", undefined, { profileId },
  );
}
export async function saveSkills(profileId: string, skills: string[]) {
  return request<{ ok: boolean }>(
    "POST", "/api/user-config/skills", { skills }, { profileId },
  );
}
export interface MemoryPayload {
  memory?: string;
  user?: string;
}
export async function getMemory(profileId: string) {
  return request<MemoryPayload>(
    "GET", "/api/memory", undefined, { profileId },
  );
}
export async function putMemory(profileId: string, payload: MemoryPayload) {
  return request<Record<string, string>>(
    "PUT", "/api/memory", payload, { profileId },
  );
}
export interface WorkspaceFile {
  name: string;
  path: string;
  content: string;
}
export async function listWorkspaceFiles(profileId: string) {
  return request<{ files: WorkspaceFile[] }>(
    "GET", "/api/workspace/files", undefined, { profileId },
  );
}
export async function putWorkspaceFile(profileId: string, path: string, content: string) {
  return request<{ path: string; content: string }>(
    "PUT", "/api/workspace/files", { path, content }, { profileId },
  );
}
// ── Trust / Feedback / Training (charter 新物种) ─────────────────────
export interface TrustLevelRow {
  agent_name: string;
  action_kind: string;
  level: number;
  rationale?: string;
  updated_at?: string;
}
export type TrustSnapshot = Record<string, Record<string, number>>;

export async function getTrustLevels(profileId: string) {
  return request<{ data: TrustLevelRow[] }>(
    "GET", `/api/trust/levels/${encodeURIComponent(profileId)}`);
}
export async function getTrustSnapshot(profileId: string) {
  return request<{ data: TrustSnapshot }>(
    "GET", `/api/trust/snapshot/${encodeURIComponent(profileId)}`);
}
export async function setTrustLevel(
  profileId: string, agent_name: string, action_kind: string,
  level: number, rationale = "",
) {
  return request<{ success: boolean; level: number }>(
    "PUT",
    `/api/trust/levels/${encodeURIComponent(profileId)}/${encodeURIComponent(agent_name)}/${encodeURIComponent(action_kind)}`,
    { level, rationale });
}
export async function triggerTrustAutoAdjust() {
  return request<{ count: number; adjustments: any[] }>(
    "POST", "/api/trust/auto-adjust", {});
}

// Trust decision audit trail — every call to trust.decide() logs one
// row; dashboard renders recent rows as a timeline under the grid.
export interface TrustDecisionRow {
  id: number;
  action_id: string;
  trust_level_snapshot: number;
  decision: "auto_execute" | "require_approval" | "block" | string;
  reason: string;
  decided_at: string;
  agent_name?: string | null;
  action_kind?: string | null;
  profile_id?: string | null;
  action_state?: string | null;
  target?: Record<string, any> | null;
}
export async function listTrustDecisions(
  opts: {
    profileId?: string;
    agentName?: string;
    actionKind?: string;
    decision?: string;
    limit?: number;
    offset?: number;
  } = {},
) {
  const qs = new URLSearchParams();
  if (opts.profileId) qs.set("profile_id", opts.profileId);
  if (opts.agentName) qs.set("agent_name", opts.agentName);
  if (opts.actionKind) qs.set("action_kind", opts.actionKind);
  if (opts.decision) qs.set("decision", opts.decision);
  if (opts.limit) qs.set("limit", String(opts.limit));
  if (opts.offset) qs.set("offset", String(opts.offset));
  const q = qs.toString() ? `?${qs}` : "";
  return request<{
    data: TrustDecisionRow[];
    count: number;
    summary: { by_decision: Record<string, number>; by_agent: Record<string, number> };
  }>("GET", `/api/trust/decisions${q}`);
}

export interface ModelConfigResponse {
  model?: string | null;
  api_key?: string | null;
  base_url?: string | null;
  /** Full multi-provider config (JSON object or stringified JSON). */
  providers_json?: unknown;
  /** 后端派生的生效模型角色（只读，供 UI 显示"自动/自定义"）。*/
  derived?: import("@/pages/agent-detail/modelPresets").DerivedRoles | null;
}
export async function getModelConfig(profileId: string) {
  return request<ModelConfigResponse>(
    "GET", "/api/user-config/model", undefined, { profileId },
  );
}
/** Put full multi-provider config; backend derives legacy model/api_key/base_url. */
export async function putModelConfig(profileId: string, providers_json: unknown) {
  return request<ModelConfigResponse>(
    "PUT", "/api/user-config/model", { providers_json }, { profileId },
  );
}
/** Smoke-test a provider via the gateway's /v1/models endpoint. */
export async function testModelsEndpoint(profileId: string) {
  return request<unknown>("GET", "/v1/models", undefined, { profileId });
}
export interface ScheduledJob {
  id: string;
  name?: string | null;
  prompt?: string;
  schedule_display?: string | null;
  state?: string;          // "active" | "paused" | "completed"
  last_status?: string;    // "ok" | "error" | ...
  last_run_at?: string | null;
  next_run_at?: string | null;
  /** Most-recent execution error (e.g. agent crash, script failure). */
  last_error?: string | null;
  /** Most-recent delivery error (push to wechat/feishu/etc. failed). */
  last_delivery_error?: string | null;
}
export async function listJobs(profileId: string, opts: { includeDisabled?: boolean } = {}) {
  const q = opts.includeDisabled ? "?include_disabled=true" : "";
  return request<{ data?: ScheduledJob[]; jobs?: ScheduledJob[] }>(
    "GET", `/api/jobs${q}`, undefined, { profileId },
  );
}
export async function createJob(profileId: string, payload: any) {
  return request<any>("POST", "/api/jobs", payload, { profileId });
}
export async function deleteJob(profileId: string, jobId: string) {
  return request<void>("DELETE", `/api/jobs/${encodeURIComponent(jobId)}`, undefined, { profileId });
}
export async function pauseJob(profileId: string, jobId: string) {
  return request<any>("POST", `/api/jobs/${encodeURIComponent(jobId)}/pause`, {}, { profileId });
}
export async function resumeJob(profileId: string, jobId: string) {
  return request<any>("POST", `/api/jobs/${encodeURIComponent(jobId)}/resume`, {}, { profileId });
}
export async function runJob(profileId: string, jobId: string) {
  return request<any>("POST", `/api/jobs/${encodeURIComponent(jobId)}/run`, {}, { profileId });
}
// ── Sessions ──────────────────────────────────────────────────────────
export interface Session {
  id: string;
  title: string;
  preview?: string;
  updated_at?: string;
  source?: string;
  platform?: string;
  message_count?: number;
  hidden_at?: string | null;
}
export async function listSessions(
  opts: { profileId?: string; includeHidden?: boolean } = {},
) {
  const q = opts.includeHidden ? "?include_hidden=1" : "";
  return request<{ data: Session[] }>("GET", `/api/sessions${q}`, undefined, opts);
}
export async function getSessionMessages(convId: string, opts: { profileId?: string } = {}) {
  return request<{ data: any[] }>(
    "GET", `/api/sessions/${encodeURIComponent(convId)}/messages`,
    undefined, opts,
  );
}
export async function archiveSession(convId: string, opts: { profileId?: string } = {}) {
  return request<{ ok: true }>(
    "POST", `/api/sessions/${encodeURIComponent(convId)}/archive`,
    undefined, opts,
  );
}
export async function unarchiveSession(convId: string, opts: { profileId?: string } = {}) {
  return request<{ ok: true }>(
    "POST", `/api/sessions/${encodeURIComponent(convId)}/unarchive`,
    undefined, opts,
  );
}
export async function deleteSession(convId: string, opts: { profileId?: string } = {}) {
  return request<{ ok: true }>(
    "DELETE", `/api/sessions/${encodeURIComponent(convId)}`,
    undefined, opts,
  );
}

// Returns {headers, body stream reader} — caller parses SSE.
/** 归一化后的聊天附件(由 POST /api/chat/attachments 返回)。 */
export interface ChatAttachment {
  kind: "image" | "document";
  filename: string;
  data_url?: string;   // image:base64 data URL
  text?: string;       // document:抽取出的文本
  id?: number;         // 持久化后的 attachment id(服务端落库)
}

/** 上传主对话附件:图片→data_url、PDF/Office/CSV→服务端抽文本。
 * conversationId 随上传一起带上,服务端据此把原文件与本轮 agent 录入的
 * 记录关联起来(两段请求用同一 conversation id)。 */
export async function uploadChatAttachments(
  files: File[],
  conversationId?: string | null,
): Promise<ChatAttachment[]> {
  const fd = new FormData();
  for (const f of files) fd.append("files", f);
  const headers: Record<string, string> = {};
  if (conversationId) headers["X-Conversation-Id"] = conversationId;
  const res = await fetch("/api/chat/attachments", {
    method: "POST", credentials: "include", headers, body: fd,
  });
  if (!res.ok) throw new ApiError(res.status, `attachment upload ${res.status}`);
  const d = await res.json();
  return (d?.attachments || []) as ChatAttachment[];
}

/** 某条记录关联的来源文件(由 agent 在对话中录入时自动关联)。 */
export interface RecordAttachment {
  id: number;
  filename: string;
  mime_type?: string;
  kind?: string;
  size_bytes?: number;
  created_at?: string;
}

export async function listRecordAttachments(
  module: string,
  recordId: number,
): Promise<RecordAttachment[]> {
  const d = await request<{ attachments: RecordAttachment[] }>(
    "GET",
    `/crm/modules/${encodeURIComponent(module)}/records/${recordId}/attachments`,
  );
  return d.attachments || [];
}

export async function openChatStream(
  text: string,
  opts: {
    profileId?: string;
    signal?: AbortSignal;
    conversationId?: string | null;
    /** ``"<provider_id>::<model_id>"`` from ModelPicker. Pass through to
     *  the OpenAI-shaped proxy as ``body.model``; backend resolves the
     *  provider creds from profile config. */
    model?: string | null;
    /** 已处理的附件 —— 图片转 image_url 块,文档文本拼进 prompt。 */
    attachments?: ChatAttachment[];
  },
): Promise<Response> {
  const headers: Record<string, string> = { "content-type": "application/json" };
  if (opts.profileId) headers["X-Profile-Id"] = opts.profileId;
  if (opts.conversationId) headers["X-Conversation-Id"] = opts.conversationId;
  // 组消息内容:有图片 → 多模态数组(text + image_url 块);文档文本拼进 text。
  const atts = opts.attachments || [];
  let userContent: unknown = text;
  if (atts.length) {
    const docs = atts.filter(a => a.kind === "document" && a.text);
    const imgs = atts.filter(a => a.kind === "image" && a.data_url);
    let textPart = text;
    for (const d of docs) textPart += `\n\n[附件:${d.filename}]\n${d.text}`;
    if (imgs.length) {
      userContent = [
        { type: "text", text: textPart },
        ...imgs.map(i => ({ type: "image_url", image_url: { url: i.data_url } })),
      ];
    } else {
      userContent = textPart;
    }
  }
  // OpenAI body shape. ``model`` is honoured downstream when set; absent
  // falls back to the user's profile default per the proxy resolver.
  const body: Record<string, unknown> = {
    messages: [{ role: "user", content: userContent }],
    stream: true,
  };
  if (opts.model) {
    // Strip ``provider::`` prefix when sending to hermes — the OpenAI
    // spec expects a bare model id. The provider context comes from
    // headers (profile-config sync wrote it to the subprocess yaml).
    const idOnly = opts.model.split("::").pop() || opts.model;
    body.model = idOnly;
  }
  // Auth via hc_session cookie (credentials: "include" below).
  const res = await fetch("/v1/chat/completions", {
    method: "POST",
    headers,
    credentials: "include",
    signal: opts.signal,
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    let msg = `Error ${res.status}`;
    try { const d = await res.json(); msg = (d as any)?.error?.message || msg; } catch {}
    throw new ApiError(res.status, msg);
  }
  return res;
}

// ── Analytics ─────────────────────────────────────────────────────────
export interface Analytics {
  daily: Array<{ day: string; input_tokens?: number; output_tokens?: number;
                 cache_read_tokens?: number; reasoning_tokens?: number;
                 estimated_cost_usd?: number; sessions?: number }>;
  by_model: Array<{ model: string; input_tokens?: number; output_tokens?: number;
                    estimated_cost_usd?: number; sessions?: number }>;
  totals: { input_tokens?: number; output_tokens?: number; cache_read_tokens?: number;
            reasoning_tokens?: number; estimated_cost_usd?: number; sessions?: number };
}
export async function getAnalytics(days = 30, profileId?: string) {
  return request<Analytics>("GET", `/api/analytics?days=${days}`, undefined, { profileId });
}

// ── Dashboard export audit (admin) ────────────────────────────────────
export interface DashboardExportAuditItem {
  id: number;
  caller_id: number;
  caller_name: string | null;
  scope_kind: "self" | "team" | "org";
  resource_filter: Record<string, any>;
  /** JOINed from dashboard_charts; null when chart was deleted. */
  chart_title: string | null;
  occurred_at: string | null;
}
export interface DashboardExportAuditPage {
  items: DashboardExportAuditItem[];
  total: number;
  limit: number;
  offset: number;
}
export async function listDashboardExportAudit(params: {
  caller_id?: number;
  since?: string;
  until?: string;
  limit?: number;
  offset?: number;
} = {}): Promise<DashboardExportAuditPage> {
  const qs = new URLSearchParams();
  if (params.caller_id != null) qs.set("caller_id", String(params.caller_id));
  if (params.since) qs.set("since", params.since);
  if (params.until) qs.set("until", params.until);
  qs.set("limit", String(params.limit ?? 50));
  qs.set("offset", String(params.offset ?? 0));
  return request<DashboardExportAuditPage>(
    "GET", `/api/dashboards/export-audit?${qs.toString()}`,
  );
}

// ── Admin ─────────────────────────────────────────────────────────────
export interface AdminUser {
  user_id: string | number;
  /** Charter v2 §简化原则: profile.id == users.username. */
  username?: string | null;
  email: string;
  is_active?: boolean;
  is_admin?: boolean;
  created_at?: string;
  api_key?: string;
  realname?: string | null;
  mobile?: string | null;
  plan?: string;
  department?: { id: number; name: string } | null;
  role?: { id: number; name: string } | null;
  // null when is_admin=true (unlimited); otherwise server returns the
  // three layers so the UI can show "effective X (inherits from role)".
  profile_quota?: null | {
    override: number | null;       // users.profile_quota — null if not set
    role_default: number | null;   // roles.profile_quota for this user's role
    effective: number;             // resolved cap (>= 1)
  };
  /** Live agent signal — "是不是在跟我说话". */
  agent_status?: {
    running: number;   // number of running profiles for this user
    total: number;     // active profiles registered in CRM
    last_active_at: number | null;  // unix seconds (wall clock) of most recent activity
    last_action?: {
      kind: string | null;       // "action" | "message" | ...
      op: string | null;         // "create" | "update" | "delete" for action rows
      module: string | null;     // e.g. "accounts", "contacts"
      target_id: number | null;  // subject record id
      source: string | null;     // "manual" | "agent:<profile>:<name>" | ...
      at: number | null;         // unix seconds
    } | null;
  };
}
export async function adminListUsers(opts?: { online?: boolean }) {
  const q = opts?.online ? "?online=true" : "";
  return request<{ users: AdminUser[] }>("GET", `/api/admin/users${q}`);
}
export interface AdminUserInteractionEvent {
  kind: string | null;
  op: string | null;
  fields: string[] | null;
  module: string | null;
  target_id: number | null;
  source: string | null;
  at: number | null;
}
export interface AdminUserRecentActions {
  events: AdminUserInteractionEvent[];
  today: { total: number; by_kind: Record<string, number> };
}
export async function adminUserRecentActions(userId: string | number, limit = 10) {
  return request<AdminUserRecentActions>(
    "GET",
    `/api/admin/users/${encodeURIComponent(String(userId))}/recent_actions?limit=${limit}`,
  );
}
export interface AdminCreateUserInput {
  email: string;
  password: string;
  is_admin?: boolean;
  realname?: string | null;
  mobile?: string | null;
  department_id?: number | null;
  role_id?: number | null;
  profile_quota?: number | null;
  auto_start?: boolean;
}
export async function adminCreateUser(input: AdminCreateUserInput) {
  return request<{ user_id: string; api_key: string; email: string }>(
    "POST", "/api/admin/users", input,
  );
}
export interface AdminUserPatch {
  is_active?: boolean;
  is_admin?: boolean;
  plan?: string;
  realname?: string | null;
  mobile?: string | null;
  description?: string | null;
  department_id?: number | null;
  role_id?: number | null;
  // null clears the per-user override (delegates back to role default).
  profile_quota?: number | null;
}
export async function adminUpdateUser(userId: string | number, patch: AdminUserPatch) {
  return request<AdminUser>("PUT", `/api/admin/users/${encodeURIComponent(String(userId))}`, patch);
}

// ── Admin: departments / roles / permissions ──────────────────────────
export interface AdminDepartment {
  id: number;
  name: string;
  parent_id: number | null;
  manager_ids: number[];
  sort_order: number;
  description: string | null;
  member_count: number;
}
export async function adminListDepartments() {
  return request<{ departments: AdminDepartment[] }>("GET", "/api/admin/departments");
}
export async function adminCreateDepartment(patch: { name: string; parent_id?: number | null; description?: string | null; sort_order?: number; manager_ids?: number[] }) {
  return request<{ id: number; name: string }>("POST", "/api/admin/departments", patch);
}
export async function adminUpdateDepartment(id: number, patch: Partial<{ name: string; parent_id: number | null; description: string | null; sort_order: number; manager_ids: number[] }>) {
  return request<{ ok: boolean }>("PUT", `/api/admin/departments/${id}`, patch);
}
export async function adminDeleteDepartment(id: number) {
  return request<void>("DELETE", `/api/admin/departments/${id}`);
}

export interface AdminRole {
  id: number;
  name: string;
  is_default: boolean;
  description: string | null;
  member_count: number;
  // null = unlimited; any positive integer = cap on active profiles
  // for users whose role is this one (unless overridden per-user).
  profile_quota: number | null;
}
export async function adminListRoles() {
  return request<{ roles: AdminRole[] }>("GET", "/api/admin/roles");
}
export async function adminCreateRole(patch: {
  name: string;
  description?: string | null;
  profile_quota?: number | null;
}) {
  return request<{ id: number; name: string }>("POST", "/api/admin/roles", patch);
}
export async function adminUpdateRole(
  id: number,
  patch: { name?: string; description?: string | null; profile_quota?: number | null },
) {
  return request<{ ok: boolean }>("PUT", `/api/admin/roles/${id}`, patch);
}
export async function adminDeleteRole(id: number) {
  return request<void>("DELETE", `/api/admin/roles/${id}`);
}

export interface RolePermissionMatrix {
  role: { id: number; name: string; description: string | null };
  modules: Array<{
    id: number;
    name: string;
    title?: string;
    kind?: string;
    description?: string | null;
  }>;
  permissions: Array<{ id: number; code: string; name: string }>;
  grants: Record<string, Record<string, boolean>>;  // module_id -> permission_id -> true
}
export async function adminGetRolePermissions(roleId: number) {
  return request<RolePermissionMatrix>("GET", `/api/admin/roles/${roleId}/permissions`);
}
export async function adminSetRolePermissions(roleId: number, grants: Record<string, number[]>) {
  return request<{ ok: boolean; count: number }>(
    "PUT", `/api/admin/roles/${roleId}/permissions`, { grants },
  );
}
export async function adminDeleteUser(userId: string | number) {
  return request<void>("DELETE", `/api/admin/users/${encodeURIComponent(String(userId))}`);
}
export async function adminResetPassword(userId: string | number, password: string) {
  return request<{ ok: boolean }>(
    "POST", `/api/admin/users/${encodeURIComponent(String(userId))}/reset-password`,
    { password },
  );
}
export async function adminProvisionApiKey(userId: string | number) {
  return request<{ user_id: string; api_key: string }>(
    "POST", `/api/admin/users/${encodeURIComponent(String(userId))}/provision`,
  );
}
export async function adminImpersonateUser(userId: string | number) {
  return request<{ user_id: string; email: string | null }>(
    "POST", `/api/admin/users/${encodeURIComponent(String(userId))}/impersonate`,
  );
}
export interface AdminUserProfile {
  id: string;
  name?: string;
  slug?: string;
  description?: string;
  is_default?: boolean;
  process: {
    running: boolean;
    port?: number | null;
    pid?: number | null;
    idle_seconds?: number | null;
  };
  [k: string]: any;
}
export interface AdminUserDetail {
  user: {
    id: string | number;
    email: string;
    is_admin?: boolean;
    is_active?: boolean;
    created_at?: string | null;
  };
  profiles: AdminUserProfile[];
}
export async function adminUserDetail(userId: string | number) {
  return request<AdminUserDetail>(
    "GET", `/api/admin/users/${encodeURIComponent(String(userId))}/detail`,
  );
}
export async function adminStopProfileProcess(profileId: string) {
  return request<void>("DELETE", `/api/admin/processes/profile/${encodeURIComponent(profileId)}`);
}
export async function adminStartProfileProcess(profileId: string) {
  return request<{ ok: boolean; port?: number }>(
    "POST", `/api/admin/processes/profile/${encodeURIComponent(profileId)}/start`,
  );
}

// ── 新用户模板 (onboarding template) ──────────────────────────────────
/** Cron-job spec the template carries. Mirrors hermes' job schema but
 * keeps only fields the admin actually edits/sees in the template UI;
 * runtime fields (id, last_run_at, next_run_at) get minted/cleared at
 * apply time. */
export interface OnboardingCronJob {
  name: string;
  prompt?: string;
  skill?: string;
  skills?: string[];
  schedule?: { kind: string; expr: string; display?: string } | null;
  schedule_display?: string;
  deliver?: string;
  deliver_render?: string | null;
  enabled?: boolean;
  // Pass-through fields kept so admin's customizations don't get
  // silently dropped on round-trip.
  model?: string | null;
  provider?: string | null;
  base_url?: string | null;
  script?: string | null;
  no_agent?: boolean;
  context_from?: string | null;
  enabled_toolsets?: string[] | null;
}
export interface OnboardingTemplate {
  soul_md: string;
  system_prompt: string;
  skills_disabled: string[];
  cron_jobs: OnboardingCronJob[];
  model: {
    model: string | null;
    api_key: string | null;
    base_url: string | null;
    providers_json: string | null;
  };
}
export async function getOnboardingTemplate() {
  return request<OnboardingTemplate>("GET", "/api/admin/onboarding-template");
}
export async function putOnboardingTemplate(body: OnboardingTemplate) {
  return request<{ ok: boolean }>("PUT", "/api/admin/onboarding-template", body);
}
export async function applyOnboardingTemplate(userId: string | number) {
  return request<{ ok: boolean; user_id: string; profile_id: string; note: string }>(
    "POST",
    `/api/admin/onboarding-template/apply/${encodeURIComponent(String(userId))}`,
  );
}
/** List the bundled skill set every new profile inherits (hermes/skills/).
 * Use this in the template editor instead of getSkills(), which reads a
 * per-profile dir and would show admin's personal extras that won't
 * propagate to new users. */
export async function getOnboardingBundledSkills() {
  return request<{ skills: Array<{ name: string; description: string }> }>(
    "GET", "/api/admin/onboarding-template/bundled-skills",
  );
}
/** Read admin's current cron/jobs.json — backs the 「从 admin 复制定时任务」
 * button. Pure read; admin reviews then saves the template to persist. */
export async function getOnboardingCronSnapshot() {
  return request<{ jobs: OnboardingCronJob[] }>(
    "GET", "/api/admin/onboarding-template/cron-snapshot",
  );
}

// ── Personality / SOUL (per-profile) ──────────────────────────────────
export async function getPersonality(profileId: string) {
  return request<{ system_prompt: string }>(
    "GET", "/api/user-config/personality", undefined, { profileId },
  );
}
export async function putPersonality(profileId: string, system_prompt: string) {
  return request<{ ok: boolean }>(
    "PUT", "/api/user-config/personality", { system_prompt }, { profileId },
  );
}
export async function getSoul(profileId: string) {
  return request<{ soul: string }>(
    "GET", "/api/user-config/soul", undefined, { profileId },
  );
}
export async function putSoul(profileId: string, soul: string) {
  return request<{ ok: boolean }>(
    "PUT", "/api/user-config/soul", { soul }, { profileId },
  );
}

// ── Gateway actions (approvals) ───────────────────────────────────────
export interface GatewayAction {
  id: string;
  kind?: string;
  state: "pending_approval" | "executed" | "rejected" | "reverted" | "blocked" | string;
  agent_name?: string;
  target?: { module?: string; [k: string]: any };
  params?: { op?: string; module_id?: string; data?: Record<string, any>; [k: string]: any };
  // Charter §1 渠道闭环: origin records WHERE the action was submitted
  // from. The dashboard uses ``origin.channel`` to decide whether the
  // approve/reject buttons are clickable here (channel === "webui") or
  // read-only with a "请到原渠道审批" banner.
  origin?: { channel?: string; [k: string]: any } | null;
  created_at?: string;
}
export async function listGatewayActions(params: { state?: string; limit?: number } = {}) {
  const qs = new URLSearchParams();
  if (params.state) qs.set("state", params.state);
  if (params.limit) qs.set("limit", String(params.limit));
  const q = qs.toString() ? `?${qs}` : "";
  return request<{ data: GatewayAction[] }>("GET", `/api/gateway/actions${q}`);
}
export async function approveAction(id: string) {
  return request<any>("POST", `/api/gateway/actions/${encodeURIComponent(id)}/approve`, {});
}
export async function rejectAction(id: string) {
  return request<any>("POST", `/api/gateway/actions/${encodeURIComponent(id)}/reject`, {});
}
export async function revertAction(id: string) {
  return request<any>("POST", `/api/gateway/actions/${encodeURIComponent(id)}/revert`, {});
}

// ── Channels (ops view) ───────────────────────────────────────────────
export async function listChannels() {
  return request<{ channels: string[] }>("GET", "/api/channels/");
}

// ── Memory Substrate ──────────────────────────────────────────────────
// Charter 原语 ②: vector memory + entity graph. The dashboard surfaces
// these for operators to inspect what the agents "remember" about an
// account. All reads are user-scoped server-side.
export interface MemoryHit {
  id: number;
  content: string;
  kind?: string | null;
  similarity: number;
  source?: string | null;
  metadata?: Record<string, any>;
  created_at?: string;
  title?: string | null;
  scope?: string | null;
}
export interface MemoryEntity {
  id: number;
  entity_type: string;
  canonical: string;
  /** Server field is ``attributes``; ``attrs`` kept as legacy alias. */
  attributes?: Record<string, any> | null;
  attrs?: Record<string, any>;
  aliases?: string[];
  scope?: string;
  user_id?: number | null;
  confidence?: number;
  first_seen_at?: string;
  last_seen_at?: string;
  /** Present on /browse rows (joined memory_entity_sources count). */
  memory_count?: number;
}
export interface MemoryFact {
  id: number;
  kind: string;
  title?: string;
  content: string;
  scope?: string;
  source_refs?: any;
  meta?: Record<string, any>;
  confidence?: number;
  created_at?: string;
  /** Role from the entity-sources link ("mention" | "subject" | …). */
  role?: string;
}
export interface MemoryTimelineItem {
  id: string;
  kind?: string;
  source?: string;
  occurred_at?: string;
  actors?: any;
  subjects?: any;
  content?: any;
  memory_id?: number;
  memory_title?: string;
}
export interface MemoryEdge {
  id?: number;
  /** Backend payload key — matches ``memory/entities.py::subgraph``. */
  src: number;
  dst: number;
  relation: string;
  attributes?: Record<string, any> | null;
  confidence?: number;
}
export interface MemorySubgraph {
  entities: MemoryEntity[];
  edges: MemoryEdge[];
}
export async function searchMemories(
  q: string,
  opts: { topK?: number; minSimilarity?: number; kind?: string } = {},
) {
  const qs = new URLSearchParams({ q });
  if (opts.topK) qs.set("top_k", String(opts.topK));
  if (opts.minSimilarity != null) qs.set("min_similarity", String(opts.minSimilarity));
  if (opts.kind) qs.set("kind", opts.kind);
  return request<{ data: MemoryHit[]; count: number }>(
    "GET", `/api/memory/search?${qs}`,
  );
}
export async function getEntity(entity_type: string, canonical: string) {
  const qs = new URLSearchParams({ entity_type, canonical });
  return request<{ data: MemoryEntity }>("GET", `/api/memory/entities?${qs}`);
}
export async function getSubgraph(
  entityId: number,
  opts: { depth?: number; maxEdges?: number } = {},
) {
  const qs = new URLSearchParams();
  if (opts.depth) qs.set("depth", String(opts.depth));
  if (opts.maxEdges) qs.set("max_edges", String(opts.maxEdges));
  const q = qs.toString() ? `?${qs}` : "";
  return request<{ data: MemorySubgraph }>(
    "GET", `/api/memory/entities/${entityId}/subgraph${q}`,
  );
}
export async function browseMemory(
  kind: "recent" | "org" | "person" | "fact" = "recent",
  opts: { limit?: number; offset?: number } = {},
) {
  const qs = new URLSearchParams({ kind });
  if (opts.limit) qs.set("limit", String(opts.limit));
  if (opts.offset) qs.set("offset", String(opts.offset));
  return request<{ data: (MemoryEntity | MemoryFact)[]; count: number; kind: string }>(
    "GET", `/api/memory/browse?${qs}`,
  );
}
/** One entity touched by a journey memory (for tag chips + drill-in). */
export interface MemoryJourneyEntity {
  entity_id: number;
  entity_type: string;
  canonical: string;
  role?: string | null;
}
/** A single learning-timeline row: a memory the agent distilled, with the
 *  entities it touched. Superset of MemoryFact (any ``kind``). */
export interface MemoryJourneyItem {
  id: number;
  kind: string;
  title?: string | null;
  content: string;
  scope?: string;
  source_refs?: any;
  meta?: Record<string, any>;
  confidence?: number;
  created_at?: string;
  entities: MemoryJourneyEntity[];
}
/** 学习时间线：agent 为当前用户沉淀的记忆(全部 kind),按时间倒序。 */
export async function getMemoryJourney(
  opts: { limit?: number; offset?: number } = {},
) {
  const qs = new URLSearchParams();
  if (opts.limit) qs.set("limit", String(opts.limit));
  if (opts.offset) qs.set("offset", String(opts.offset));
  const q = qs.toString() ? `?${qs}` : "";
  return request<{ data: MemoryJourneyItem[]; count: number }>(
    "GET", `/api/memory/journey${q}`,
  );
}
export async function getEntityById(id: number) {
  return request<{ data: MemoryEntity }>(
    "GET", `/api/memory/entities/${id}`,
  );
}
export async function getEntityFacts(id: number, limit = 50) {
  return request<{ data: MemoryFact[]; count: number }>(
    "GET", `/api/memory/entities/${id}/facts?limit=${limit}`,
  );
}
export interface MemoryCrmLink {
  module: string;        // "accounts" | "contacts" | "leads" | "potentials"
  module_label: string;  // 客户 / 联系人 / 线索 / 商机
  id: number;
  name: string;
}
export async function getEntityCrmLinks(id: number) {
  return request<{ data: MemoryCrmLink[]; count: number }>(
    "GET", `/api/memory/entities/${id}/crm-links`,
  );
}
export async function getEntityTimeline(id: number, limit = 30) {
  return request<{ data: MemoryTimelineItem[]; count: number }>(
    "GET", `/api/memory/entities/${id}/timeline?limit=${limit}`,
  );
}
export async function mergeEntities(dstId: number, srcId: number) {
  return request<{ data: MemoryEntity }>(
    "POST", `/api/memory/entities/${dstId}/merge`, { src_id: srcId },
  );
}
export async function triggerMemoryIngest(
  opts: { sinceMinutes?: number; limit?: number } = {},
) {
  const qs = new URLSearchParams();
  if (opts.sinceMinutes) qs.set("since_minutes", String(opts.sinceMinutes));
  if (opts.limit) qs.set("limit", String(opts.limit));
  const q = qs.toString() ? `?${qs}` : "";
  return request<{ data: { ingested: number } }>(
    "POST", `/api/memory/ingest${q}`, {},
  );
}

// Per-user weekly insight feed — Copilot home "本周 AI 表现" card.
// Scoped to the current user (owner_by). Replaces user-visible usage of
// the admin-only Outcomes / Agent Performance dashboards, per宪章 定律 5.
// --- Coach hint (persisted in coach_hints table) ---
// Charter v2: only persisted hints are exposed to the SPA. The legacy
// "transient per-record scanner hint without an id" branch is gone with
// rl_agent_drafts and friends. id is always present.
export interface CoachHint {
  id: number;
  severity: "high" | "medium" | "low";
  // Open-ended: per-module scanners each coin their own signal names.
  // Kept as string to avoid churning this union every time we add a
  // scanner. The frontend only switches on severity + icon mapping.
  signal: string;
  module?: string;
  record_id?: number;
  record_name?: string | null;
  hint: string;
  payload?: Record<string, any>;
  is_read?: boolean;
  created_at?: string | null;
  read_at?: string | null;
  dismissed_at?: string | null;
}

export async function listCoachHintsForRecord(
  module: string, record_id: number, opts: { include_read?: boolean } = {},
) {
  const q = new URLSearchParams();
  q.set("module", module);
  q.set("record_id", String(record_id));
  if (opts.include_read) q.set("include_read", "true");
  return request<{ hints: CoachHint[] }>(
    "GET", `/api/insights/coach-hints/by-record?${q.toString()}`,
  );
}

export async function markCoachHintRead(id: number) {
  return request<{ ok: true; id: number }>(
    "POST", `/api/insights/coach-hints/${id}/read`,
  );
}
export async function dismissCoachHint(id: number) {
  return request<{ ok: true; id: number }>(
    "POST", `/api/insights/coach-hints/${id}/dismiss`,
  );
}
export async function getCoachHintUnreadCount(
  opts: { module?: string; recordId?: number } = {},
) {
  const qs = new URLSearchParams();
  if (opts.module) qs.set("module", opts.module);
  if (opts.recordId !== undefined) qs.set("record_id", String(opts.recordId));
  const suffix = qs.toString() ? `?${qs}` : "";
  return request<{ unread: number }>(
    "GET", `/api/insights/coach-hints/unread-count${suffix}`,
  );
}
export interface WeeklyInsights {
  has_data: boolean;
  period_days: number;
  outcomes: {
    deals_won: number;
    deals_won_amount: number | null;
    stages_advanced: number;
    first_touch: number;
  };
  coach_hints: CoachHint[];
}
export async function getWeeklyInsights() {
  try {
    return await request<WeeklyInsights>("GET", "/api/insights/weekly");
  } catch (e) {
    // Optional endpoint in some dev stacks — degrade quietly.
    if (e instanceof ApiError && e.status === 404) {
      return { has_data: false, period_days: 7, outcomes: {
        deals_won: 0, deals_won_amount: 0, stages_advanced: 0, first_touch: 0,
      }, coach_hints: [] } as WeeklyInsights;
    }
    throw e;
  }
}

// Per-record AI briefing for CopilotPanel empty state. Payload is
// module-specific (see hermes_cloud/app/insights/scanners/<module>.py)
// but shape is uniform so the frontend renders a single card.
export interface SuggestedQuestion {
  text: string;
  agent: string | null;
}
export interface RecordInsight {
  has_data: boolean;
  module: string;
  record_id: number;
  summary: string | null;
  hints: CoachHint[];
  suggested_questions: SuggestedQuestion[];
}
export async function getRecordInsight(module: string, id: number) {
  const qs = new URLSearchParams({ module, id: String(id) });
  return request<RecordInsight>("GET", `/api/insights/record?${qs}`);
}

// ---------------------------------------------------------------------------
// Daily feed (CRM 今日动态). Charter v2 Phase 3 deleted the entire Agent
// Pool / Inbox / Specialist / By-Record API surface — the SPA stubs that
// shadowed those routes (AgentInbox, getAgentInbox, AgentDraftCard,
// SpecialistRow, SpecialistDetail, AgentCard, AgentsByRecord, …) all
// went with that purge. Only AgentTarget survives as the shared shape
// for DailyFeedItem.target.
// ---------------------------------------------------------------------------

export interface AgentTarget {
  module: string | null;
  record_id: number;
  name: string | null;
}

export interface DailyFeedItem {
  // Charter v2 Phase 3: agent_draft is gone. Daily feed carries:
  //   - interaction      — human/AI events on the event stream
  //   - coach_hint       — post-hoc AI observations
  //   - outcome_event    — deal.won / stage.advanced / record.first_touch
  //                        from rl_outcome_events, rendered in dedicated
  //                        "今日成单 / 阶段推进 / 首触达" sections
  item_type: "interaction" | "coach_hint" | "outcome_event";
  at: string;
  target?: AgentTarget | null;
  preview?: string | null;
  kind?: string;
  source?: string;
  specialist?: string;
  // interaction kind='action'-specific — CRM-write rows have no free text;
  // the SPA renders a blurb from the op + changed field names instead.
  op?: string;
  fields?: string[];
  hint_id?: number;
  severity?: string;
  signal?: string;
  is_read?: boolean;
  is_dismissed?: boolean;
  // outcome_event-specific
  event_type?: "deal.won" | "stage.advanced" | "record.first_touch" | string;
  value?: number | null;
}
export interface DailyFeed {
  range_hours: number;
  items: DailyFeedItem[];
  counts: {
    interactions: number;
    coach_hints: number;
    /** interactions in window with source LIKE 'agent:%' — retro audit. */
    agent_recent: number;
  };
  outcomes: {
    deals_won: number;
    deals_won_amount: number | null;
    stages_advanced: number;
    first_touch: number;
  };
  money: null | {
    total_due?: number;      // receivables (owner_by = me)
    total_received?: number; // receivables
    total_overdue?: number;  // receivables (金额)
    overdue_count?: number;  // receivables (单数)
    aging?: {                // receivables 账龄分布
      "0_30": number;        // 0-30 天逾期
      "31_60": number;       // 31-60 天
      "60_plus": number;     // 60+ 天
    };
    month_received?: number; // payments (owner_by = me，本月到账)
  };
}
export async function getDailyFeed(hours = 24, limit = 200, module?: string) {
  const q = new URLSearchParams({ hours: String(hours), limit: String(limit) });
  if (module) q.set("module", module);
  return request<DailyFeed>("GET", `/api/insights/daily_feed?${q}`);
}

// ── Cross-module today-priority feed ─────────────────────────────────
// Charter v2 §定律 1 + §定律 5: deterministic SQL ranking that surfaces
// what the user should look at right now, without them asking.
export interface TodayPriorityReason {
  kind: "coach_hint" | "overdue" | "silence" | string;
  text: string;
  weight: number;
  signal?: string;
}
export interface TodayPriorityNextAction {
  /** Verb-led 10-25 char action phrase, e.g. "拟一封紧迫跟进邮件". */
  text: string;
  /** Tool-class hint: draft / analyst / research / etc. — the SPA
   *  uses this only to pick an icon; the LLM on the record page
   *  decides the actual workflow. */
  kind: string;
  /** Signal (or "overdue"/"silence"/"fallback") this action was
   *  mapped from, for telemetry. */
  signal?: string;
}
export interface TodayPriorityItem {
  module: string;
  record_id: number;
  name: string | null;
  score: number;
  reasons: TodayPriorityReason[];
  /** Charter §定律 5: next concrete step the AI proposes. SPA
   *  renders this as a "→ 让 AI 处理" button that navigates to the
   *  record + prefills Copilot with text. */
  next_action?: TodayPriorityNextAction | null;
}
// ── List-mode Copilot brief ──────────────────────────────────────────
// Charter §定律 5: list-mode Copilot must be as proactive as record-
// mode. The empty state on /crm and per-module dashboards renders
// this brief: a one-line LLM summary, cross-record AI-noticed
// patterns, and 3 verb-led prompts the user can click.
export interface ListBriefNoticed {
  signal: string;
  severity: "high" | "medium" | "low" | string;
  text: string;
  payload?: Record<string, any>;
}
export interface ListBriefSuggestion {
  text: string;
  agent: string;
}
/** Today's coach.proposal cards inlined into list_brief so the
 *  CopilotListBrief surface can render "📋 复制话术" rows without
 *  a separate fetch. Comes from
 *  ``hermes_cloud.app.insights.coach_cards.read_coach_proposals`` —
 *  the canonical reader used by every coach surface. */
export interface ListBriefCoachCardRenderTargets {
  briefing?: boolean;       // CRM 默认页顶部 hero 区
  chip?: boolean;           // 右栏 / 边栏 chip
  inbox?: boolean;          // SPA inbox
  wechat_card?: boolean;    // 单独推一条微信
  wechat_digest?: boolean;  // 计入每日 digest
}
export interface ListBriefCoachCard {
  id: string;
  module: string | null;
  record_id: number | null;
  severity: "high" | "medium" | "low" | string;
  hint: string;
  reasoning?: string | null;
  draft?: string | null;
  intended_channel?: string | null;
  render_targets?: ListBriefCoachCardRenderTargets;
  is_read: boolean;
  created_at?: string | null;
}
export interface ListBrief {
  module: string | null;
  summary: string;
  ai_noticed: ListBriefNoticed[];
  suggestions: ListBriefSuggestion[];
  coach_cards: ListBriefCoachCard[];
  counts: Record<string, number>;
  source: "llm_cached" | "llm_fresh" | "llm_pending" | "scanner_template";
}
export async function getListBrief(module?: string) {
  const q = new URLSearchParams();
  if (module) q.set("module", module);
  const qs = q.toString();
  return request<ListBrief>(
    "GET", `/api/insights/list_brief${qs ? `?${qs}` : ""}`,
  );
}

export interface CronHealthJob {
  name: string;
  label: string;
  expected_interval_min: number;
  last_run_at: string | null;
  minutes_since: number | null;
  status: "green" | "yellow" | "red";
}
export async function getCronHealth() {
  return request<{ jobs: CronHealthJob[] }>("GET", "/api/insights/cron_health");
}

export async function getTodayPriority(limit = 5, module?: string) {
  const q = new URLSearchParams({ limit: String(limit) });
  if (module) q.set("module", module);
  return request<{ items: TodayPriorityItem[] }>(
    "GET", `/api/insights/today_priority?${q}`,
  );
}

// ── Schema evolution (ADR 2026-04-20) ─────────────────────────────────
//
// Backend routes in hermes_cloud/app/crm/routes/__init__.py §Schema evolution.
// Options are append-only for any authed user; fields and proposals are
// admin-only. Every write flows through schema_changelog.

export interface SchemaChangelogRow {
  id: number;
  ts: string;
  actor_type: "user" | "agent";
  actor_id: number | null;
  actor_label: string | null;
  module_id: number;
  module_name: string;
  field_id: number | null;
  field_name: string | null;
  op: "add_field" | "update_field" | "hide_field" | "add_option" | "hide_option";
  before_json: unknown;
  after_json: unknown;
  reason: string | null;
}

export interface FieldProposal {
  id: number;
  ts: string;
  proposer_type: "user" | "agent";
  proposer_id: number | null;
  proposer_label: string | null;
  module_id: number;
  module_name: string;
  payload: Record<string, unknown>;
  status: "pending" | "approved" | "rejected" | "withdrawn";
  reviewer_id: number | null;
  review_ts: string | null;
  review_note: string | null;
  created_field_id: number | null;
}

/** Append a picklist option. 200 with status='duplicate' on case-insensitive
 *  match; 400 on validation failure (length / comma / empty).
 *
 *  ``opts.label`` (with optional ``locale``, default zh-CN) saves the
 *  admin from a follow-up rename PATCH: one click writes both the
 *  ``options`` CSV and the ``field_config.option_labels[value][locale]``
 *  entry. The stored value remains whatever you pass — labels are
 *  display-only. */
export async function addFieldOption(
  module: string, field: string, value: string,
  opts?: { label?: string; locale?: string; reason?: string },
) {
  return request<{
    ok: true;
    status: "added" | "duplicate";
    options: string[];
    label?: string;
    locale?: string;
  }>(
    "POST",
    `/crm/modules/${encodeURIComponent(module)}/fields/${encodeURIComponent(field)}/options`,
    {
      value,
      label: opts?.label,
      locale: opts?.locale,
      reason: opts?.reason,
    },
  );
}

/** Delete a picklist option. 409 if value is a system option
 *  (``field_config.system_options``) or any live record still uses it. */
export async function deleteFieldOption(
  module: string, field: string, value: string, reason?: string,
) {
  const qs = reason ? `?reason=${encodeURIComponent(reason)}` : "";
  return request<{ ok: true; status: "deleted"; options: string[] }>(
    "DELETE",
    `/crm/modules/${encodeURIComponent(module)}/fields/${encodeURIComponent(field)}/options/${encodeURIComponent(value)}${qs}`,
  );
}

/** Rewrite the picklist options CSV in the given order. ``order`` must
 *  be a permutation of the field's current options (no adds, no drops —
 *  use addFieldOption / deleteFieldOption for those). Server responds
 *  with status='noop' when the order is unchanged. */
export async function reorderFieldOptions(
  module: string, field: string, order: string[], reason?: string,
) {
  return request<{
    ok: true;
    status: "reordered" | "noop";
    options: string[];
  }>(
    "PATCH",
    `/crm/modules/${encodeURIComponent(module)}/fields/${encodeURIComponent(field)}/options/order`,
    { order, reason },
  );
}

/** Rename a picklist option's display label. The stored ``value`` is
 *  never changed — every record that references it stays valid; only
 *  the requested ``locale`` slot inside
 *  ``field_config.option_labels[value]`` is updated. Other locales on
 *  the same option are preserved. System options can have their label
 *  edited (the value is what's locked). */
export async function updateFieldOptionLabel(
  module: string, field: string, value: string, label: string,
  opts?: { locale?: string; reason?: string },
) {
  return request<{ ok: true; status: "updated" | "noop"; label: string; locale?: string }>(
    "PATCH",
    `/crm/modules/${encodeURIComponent(module)}/fields/${encodeURIComponent(field)}/options/${encodeURIComponent(value)}`,
    { label, locale: opts?.locale, reason: opts?.reason },
  );
}

/** Admin-only: add a new field to a module. Goes through the backend
 *  ``provision_field`` helper — inserts metadata AND creates the physical
 *  column (and a SEQUENCE for auto-number fields). */
export async function addModuleField(
  module: string,
  body: {
    field_name: string;
    field_label: string;
    field_type: string;
    is_required?: boolean;
    is_searchable?: boolean;
    is_readonly?: boolean;
    options?: string[];
    default_value?: string | null;
    description?: string | null;
    field_config?: FieldConfig | null;
    reason?: string;
  },
) {
  return request<{ ok: true; id: number; field_name: string }>(
    "POST",
    `/crm/modules/${encodeURIComponent(module)}/fields`,
    body,
  );
}

/** Admin-only: edit metadata on an existing field. field_name and
 *  field_type are intentionally not patchable — renames and type changes
 *  need migrations. */
export async function patchModuleField(
  module: string, field: string,
  patch: {
    field_label?: string;
    is_hidden?: boolean;
    is_required?: boolean;
    is_searchable?: boolean;
    is_readonly?: boolean;
    description?: string | null;
    default_value?: string | null;
    field_config?: FieldConfig | null;
    reason?: string;
  },
) {
  return request<{ ok: true; field_name: string; after: Record<string, unknown> }>(
    "PATCH",
    `/crm/modules/${encodeURIComponent(module)}/fields/${encodeURIComponent(field)}`,
    patch,
  );
}

/** Admin-only: bulk reorder a module's NON-HIDDEN fields. ``order`` is the
 *  ordered list of non-hidden field_names — the backend writes
 *  ``sort_order = 0..N-1`` by index and logs one ``reorder_fields`` entry.
 *  Hidden fields keep their existing sort_order and must NOT appear here.
 *  Backend route: ``PUT /crm/modules/{m}/fields/order``. */
export async function reorderModuleFields(
  module: string, order: string[], reason?: string,
) {
  return request<{ ok: true; order: string[] }>(
    "PUT",
    `/crm/modules/${encodeURIComponent(module)}/fields/order`,
    { order, reason },
  );
}

export async function getSchemaChangelog(
  opts: { moduleName?: string; actorType?: "user" | "agent"; limit?: number } = {},
) {
  const qs = new URLSearchParams();
  if (opts.moduleName) qs.set("module_name", opts.moduleName);
  if (opts.actorType) qs.set("actor_type", opts.actorType);
  if (opts.limit) qs.set("limit", String(opts.limit));
  const q = qs.toString() ? `?${qs}` : "";
  return request<{ data: SchemaChangelogRow[] }>("GET", `/crm/schema_changelog${q}`);
}

export async function createFieldProposal(
  body: {
    module_name: string;
    field_name: string;
    field_label: string;
    field_type: string;
    reason: string;
    is_required?: boolean;
    options?: string[];
    default_value?: string | null;
    description?: string | null;
  },
) {
  return request<{ ok: true; id: number; status: "pending" }>(
    "POST", `/crm/field_proposals`, body,
  );
}

export async function listFieldProposals(
  status: "pending" | "approved" | "rejected" | "withdrawn" = "pending",
  limit = 100,
) {
  const q = new URLSearchParams({ status, limit: String(limit) }).toString();
  return request<{ data: FieldProposal[] }>("GET", `/crm/field_proposals?${q}`);
}

/** Admin-only: count of proposals awaiting review. Backs the nav badge. */
export async function getFieldProposalsPendingCount() {
  return request<{ count: number }>("GET", "/crm/field_proposals/pending_count");
}

export async function reviewFieldProposal(
  proposalId: number,
  decision: "approved" | "rejected",
  note?: string,
) {
  return request<{
    ok: true;
    status: "approved" | "rejected";
    field_id?: number;
    field_name?: string;
  }>("POST", `/crm/field_proposals/${proposalId}/review`, { decision, note });
}

// ── Phase 4.0c — agent_tasks ───────────────────────────────────────────
export interface AgentTask {
  id: number;
  profile_id: string;
  owner_user_id: number | null;
  title: string;
  description: string | null;
  due_at: string | null;
  status: "open" | "done" | "cancelled";
  related_module: string | null;
  related_record_id: number | null;
  source_agent: string | null;
  source_draft_id: number | null;
  gateway_action_id: string | null;
  meta: Record<string, any>;
  deleted: boolean;
  created_at: string;
  updated_at: string;
}

export async function listMyTasks(opts: {
  status?: "open" | "done" | "cancelled";
  limit?: number;
} = {}) {
  const qs = new URLSearchParams();
  if (opts.status) qs.set("status", opts.status);
  if (opts.limit) qs.set("limit", String(opts.limit));
  const q = qs.toString() ? `?${qs}` : "";
  return request<{ data: AgentTask[]; count: number }>(
    "GET", `/api/tasks${q}`,
  );
}

export async function markTaskDone(taskId: number) {
  return request<{ data: AgentTask }>(
    "POST", `/api/tasks/${taskId}/done`, {},
  );
}

export async function cancelTask(taskId: number) {
  return request<{ data: AgentTask }>(
    "POST", `/api/tasks/${taskId}/cancel`, {},
  );
}

// ── Phase 5.2/5.6 — team scope + AI audit dashboard ────────────────────
export type ScopeKind = "self" | "team" | "org";

export interface AIActionsSummary {
  total: number;
  approved: number;
  cancelled: number;
  pending: number;
  by_day: Array<{ day: string; n: number }>;
  by_member: Array<{ user_id: number; n: number }>;
  by_intent: Array<{ intent: string; n: number }>;
  filter: { scope?: ScopeKind; week_start?: string; week_end?: string };
}

export async function fetchAiActionsSummary(
  scope: ScopeKind = "self", weekOffset = 0, ownerId?: number,
) {
  const qs = new URLSearchParams({
    scope, week_offset: String(weekOffset),
  });
  if (ownerId != null) qs.set("owner_id", String(ownerId));
  return request<AIActionsSummary>(
    "GET", `/api/insights/ai_actions_summary?${qs.toString()}`,
  );
}

export interface SleepingDealsTrendRow {
  week_start: string;
  n: number;
}

export async function fetchSleepingDealsTrend(
  scope: ScopeKind = "self", weeks = 4, ownerId?: number,
) {
  const qs = new URLSearchParams({
    scope, weeks: String(weeks),
  });
  if (ownerId != null) qs.set("owner_id", String(ownerId));
  return request<{ weeks: SleepingDealsTrendRow[] }>(
    "GET", `/api/insights/sleeping_deals_trend?${qs.toString()}`,
  );
}

export interface PipelineFunnelRow {
  stage: string;
  n: number;
  amount_total: number;
  filter: { scope?: ScopeKind; milestone?: string };
}

export async function fetchPipelineFunnel(
  scope: ScopeKind = "team", ownerId?: number,
) {
  const qs = new URLSearchParams({ scope });
  if (ownerId != null) qs.set("owner_id", String(ownerId));
  return request<{ stages: PipelineFunnelRow[] }>(
    "GET", `/api/insights/team_pipeline_funnel?${qs.toString()}`,
  );
}

// v1.7 — list of users the caller may filter by (specific subordinate /
// any user for admin). Drives the dropdown next to 我的/团队/全部.
export interface TeamMember {
  id: number;
  name: string;
  department_id: number | null;
  is_self: boolean;
}

export async function fetchTeamMembers() {
  return request<{ members: TeamMember[] }>(
    "GET", `/api/insights/team_members`,
  );
}

// 5.2.B — team activity on a record
export interface TeamActivityRow {
  id: string;
  kind: string;
  source: string;
  occurred_at: string | null;
  user_id: number | null;
  text: string;
}

export async function fetchTeamActivity(
  module: string, recordId: number, limit = 10,
) {
  const qs = new URLSearchParams({ limit: String(limit) }).toString();
  return request<{ activities: TeamActivityRow[]; scope_size: number }>(
    "GET", `/api/insights/team_activity/${module}/${recordId}?${qs}`,
  );
}

// 5.2.C — latest team brief tick
export interface TeamBriefTick {
  id: string;
  occurred_at: string | null;
  content: {
    manager_id?: number;
    member_count?: number;
    signals_seen?: number;
    actions_planned?: number;
    actions_executed?: number;
    team_signals_count?: number;
    dry_run?: boolean;
    version?: number;
  };
}

export async function fetchTeamBriefLatest() {
  return request<{ tick: TeamBriefTick | null }>(
    "GET", `/api/insights/team_brief_latest`,
  );
}


// =============================================================================
// Dashboards (spec docs/superpowers/specs/2026-05-11-ai-dashboard-design.md)
// =============================================================================

export interface DashboardTab {
  id: string;
  owner_user_id: number;
  title: string;
  position: number;
  visibility: "private" | "shared";
  shared_with: Record<string, any>;
  is_system_preset: boolean;
  preset_key: string | null;
}

export interface DashboardChart {
  id: string;
  tab_id: string;
  position: number;
  title: string;
  dsl: Record<string, any>;
  /** Card column span on the dashboard grid (1-4). */
  grid_w?: number;
  /** Card row span on the dashboard grid (1-4). */
  grid_h?: number;
}

export interface ChartData {
  columns: string[];
  rows: any[][];
  /** Per-column display-label map for ref-typed dims (e.g.
   * department_id -> { "2": "销售部" }). Frontend renders the label
   * but uses the raw row value for drill callbacks so the SQL still
   * matches against the integer FK. */
  column_labels?: Record<string, Record<string, string>>;
  /** Per-dimension configured value order (picklist CSV order from
   * ``module_fields.options``). Funnel uses this to render in
   * admin-defined order instead of forcing y-desc; the backend has
   * already pre-sorted rows for funnel/composite/table when this is
   * set, so most callers can just trust row order. */
  dimension_orders?: Record<string, string[]>;
  chart_spec: {
    type:
      | "line" | "bar" | "stacked_bar" | "horizontal_bar"
      | "pie" | "donut" | "table" | "report_table"
      | "metric" | "funnel" | "text" | "composite";
    title: string;
    x?: string;
    y?: string;
    /** Secondary y measure for composite charts (rendered as a Line
     * over the bar chart) or column dim for report_table. */
    y2?: string;
    series?: string;
    /** Optional inline rich text for the "text" chart type. Plain
     * markdown is rendered as-is by the frontend. */
    text?: string;
  };
  scope_resolved: string;
}

export const listDashboardTabs = () =>
  request<{ tabs: DashboardTab[] }>("GET", "/api/dashboards/tabs");

export const createDashboardTab = (title: string) =>
  request<DashboardTab>("POST", "/api/dashboards/tabs", { title });

export const getDashboardTab = (id: string) =>
  request<{ tab: DashboardTab; charts: DashboardChart[] }>(
    "GET", `/api/dashboards/tabs/${id}`,
  );

export const patchDashboardTab = (id: string, body: Partial<DashboardTab>) =>
  request<DashboardTab>("PATCH", `/api/dashboards/tabs/${id}`, body);

export const deleteDashboardTab = (id: string) =>
  request<{ ok: boolean }>("DELETE", `/api/dashboards/tabs/${id}`);

export const forkDashboardTab = (id: string) =>
  request<DashboardTab>("POST", `/api/dashboards/tabs/${id}/fork`);

export const fetchChartData = (id: string, scope?: string) => {
  const q = scope ? `?scope=${encodeURIComponent(scope)}` : "";
  return request<ChartData>("GET", `/api/dashboards/charts/${id}/data${q}`);
};

export const refreshChart = (id: string, scope?: string) => {
  const q = scope ? `?scope=${encodeURIComponent(scope)}` : "";
  return request<ChartData>("POST", `/api/dashboards/charts/${id}/refresh${q}`);
};

export const deleteChart = (id: string) =>
  request<{ ok: boolean }>("DELETE", `/api/dashboards/charts/${id}`);

export const patchChart = (
  id: string,
  body: { title?: string; grid_w?: number; grid_h?: number;
          dsl?: Record<string, any> },
) =>
  request<{ ok: boolean; id: string; title: string;
            grid_w: number; grid_h: number }>(
    "PATCH", `/api/dashboards/charts/${id}`, body,
  );

export const drillChart = (
  id: string,
  dim_filters: Record<string, any>,
  extra_columns: string[] = [],
) =>
  request<{ columns: string[]; rows: any[][] }>(
    "POST", `/api/dashboards/charts/${id}/drill`,
    { dim_filters, extra_columns },
  );

// ── Channel credentials (per-user-channel-bot plan) ──────────────────
//
// Replaces the legacy "shared bot + binding" model. Each profile owns
// its channel credentials, stored in its config.yaml. The cloud
// endpoints under /api/profiles/{id}/channels/ read/write that YAML
// surgically (preserving other channels and top-level keys).

export interface ChannelEntry {
  channel: string;
  display_name: string;
  configured: boolean;
  supports_qr: boolean;
  /** True when the channel has a real test-connection probe. When
   *  false the SPA hides the "测试连接" button (no probe exists). */
  supports_probe: boolean;
  bot_name?: string;
  required_fields: string[];
  optional_fields: string[];
  secret_fields: string[];
  /** Present on GET /channels/{channel}: the per-channel extra block
   *  with secret fields masked as ●●●●●●last4. */
  extra?: Record<string, string>;
}

export const listProfileChannels = (profileId: string) =>
  request<{ channels: ChannelEntry[] }>(
    "GET", `/api/profiles/${profileId}/channels`,
  );

export const getProfileChannel = (profileId: string, channel: string) =>
  request<ChannelEntry>(
    "GET", `/api/profiles/${profileId}/channels/${channel}`,
  );

export const putProfileChannel = (
  profileId: string, channel: string, extra: Record<string, string>,
) =>
  request<ChannelEntry>(
    "PUT", `/api/profiles/${profileId}/channels/${channel}`, extra,
  );

export const deleteProfileChannel = (profileId: string, channel: string) =>
  request<{ channel: string; configured: false }>(
    "DELETE", `/api/profiles/${profileId}/channels/${channel}`,
  );

export interface ChannelTestResult {
  ok: boolean;
  error: string;
  latency_ms: number;
  bot_name: string;
}

export const testProfileChannel = (profileId: string, channel: string) =>
  request<ChannelTestResult>(
    "POST", `/api/profiles/${profileId}/channels/${channel}/test`,
  );

// ── Channel QR onboarding ────────────────────────────────────────────

export interface QRStartResult {
  session_id: string;
  qr_url: string;
  /** Optional human-readable code shown alongside QR (Feishu only). */
  user_code?: string;
  /** Recommended seconds between polls. */
  interval: number;
  /** Unix timestamp when device_code expires. */
  expires_at: number;
}

export const startChannelQR = (
  profileId: string, channel: string, opts: { domain?: string } = {},
) =>
  request<QRStartResult>(
    "POST", `/api/profiles/${profileId}/channels/${channel}/qr/start`,
    opts.domain ? { domain: opts.domain } : undefined,
  );

export type QRPollStatus =
  | { status: "pending" }
  | { status: "scanned" }
  | { status: "completed"; extra: Record<string, string> }
  | { status: "denied" }
  | { status: "expired" }
  | { status: "error"; message: string };

export const pollChannelQR = (
  profileId: string, channel: string, sessionId: string,
) =>
  request<QRPollStatus>(
    "GET",
    `/api/profiles/${profileId}/channels/${channel}/qr/poll?session_id=${encodeURIComponent(sessionId)}`,
  );

// ── CRM 自动化工作流(只读查看 + 启停;新建/编辑走 agent) ──────────────
export interface CrmWorkflow {
  id: number;
  name: string;
  trigger_type: "event" | "schedule";
  module: string;
  trigger_on?: string | null;        // event: created | updated
  schedule_cron?: string | null;     // schedule: cron 表达式
  condition?: { all?: unknown[]; any?: unknown[] } | null;
  actions?: { kind?: string; executor?: string; params?: unknown }[] | null;
  enabled: boolean;
  status?: "draft" | "live";
  created_at?: string;
  updated_at?: string;
}

export const listModuleWorkflows = (module: string) =>
  request<CrmWorkflow[]>("GET", `/crm/modules/${encodeURIComponent(module)}/workflows`);

// 全局列出工作流(跨模块);module 省略=全部。视图全局,数据仍绑各自模块。
export const listAllWorkflows = (module?: string | null) =>
  request<CrmWorkflow[]>(
    "GET", `/crm/workflows${module ? `?module=${encodeURIComponent(module)}` : ""}`);

// 删除(软删)一条工作流:删后从列表消失、不再触发,可后台恢复。
export const deleteWorkflow = (id: number) =>
  request<{ ok: boolean; id: number }>("DELETE", `/crm/workflows/${id}`);

export const toggleWorkflow = (id: number, enabled: boolean) =>
  request<{ ok: boolean; id: number; enabled: boolean }>(
    "PATCH", `/crm/workflows/${id}`, { enabled });

export const setWorkflowStatus = (id: number, status: "draft" | "live") =>
  request<{ ok: boolean }>("PATCH", `/crm/workflows/${id}`, { status });

/**
 * 结构化「点块改」保存:更新某条工作流的触发 / 条件 / 动作。
 * 只发与触发类型相关的字段(event 发 trigger_on,schedule 发 schedule_cron)。
 * 引擎只认 condition.all(AND),所以保存 all 列即可。
 */
export const updateWorkflowSpec = (id: number, patch: {
  name?: string; trigger_on?: "create" | "update" | null;
  schedule_cron?: string | null;
  condition?: { all: { field: string; op: string; value?: unknown }[] };
  actions?: { kind?: string; executor?: string; params?: Record<string, unknown>; instruction?: string }[];
}) => request<{ ok: boolean; id: number; workflow?: CrmWorkflow }>("PATCH", `/crm/workflows/${id}`, patch);

export const testRunWorkflow = (id: number, recordId: number) =>
  request<{ status: string; run_id?: number; results?: unknown[]; reason?: string }>(
    "POST", `/crm/workflows/${id}/test-run`, { record_id: recordId });

/** 单次工作流运行记录(触发 → 各动作结果)。 */
export interface CrmWorkflowRun {
  id: number;
  workflow_id: number;
  workflow_name: string;
  trigger_record_id: number | null;
  trigger: string;
  status: "running" | "done" | "failed" | "skipped";
  created_at: string;
  results: {
    kind?: string;
    ok?: boolean;
    error?: string;
    undo?: unknown | null;
    undoable?: boolean;
    reply?: string | null;
    summary?: string | null;
    [k: string]: unknown;
  }[];
}

export const undoWorkflowRun = (runId: number) =>
  request<{ ok: boolean; undone: unknown[]; skipped: unknown[]; failed: unknown[]; reason?: string }>(
    "POST", `/crm/workflows/runs/${runId}/undo`, {});

export const listModuleWorkflowRuns = (
  module: string,
  opts?: { workflowId?: number; limit?: number },
) => {
  const qs = new URLSearchParams();
  if (opts?.workflowId != null) qs.set("workflow_id", String(opts.workflowId));
  if (opts?.limit != null) qs.set("limit", String(opts.limit));
  const tail = qs.toString();
  return request<{ runs: CrmWorkflowRun[] }>(
    "GET",
    `/crm/modules/${encodeURIComponent(module)}/workflows/runs${tail ? `?${tail}` : ""}`,
  );
};
