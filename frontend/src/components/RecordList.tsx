import { CrmIcon } from "./CrmIcon";
import { useEffect, useMemo, useState } from "react";
import {
  listRecords, listSegments, undoLastUpdate, getMe, fetchTeamMembers,
  getModuleFields, listViews, deleteView, updateView,
  type CrmRecord, type CurrentUser, type ScopeKind, type TeamMember,
  type CrmFieldMeta, type ModuleView, type ListFieldsConfig, type ViewWriteBody,
  ApiError,
} from "@/lib/api";
import { toast } from "@/lib/toast";
import { useT, enumLabel, type T, type DictKey } from "@/lib/i18n";
import {
  getPersonalListFields, resolveListFields, formatListFieldValue,
  listPrimaryText,
} from "@/lib/listFields";
import { fieldLabel } from "@/lib/crmLabels";
import { CreateRecordModal } from "./CreateRecordModal";
import { ListFieldsCustomizer } from "./ListFieldsCustomizer";
import { ViewBuilder } from "./ViewBuilder";
import { SegmentsConfig } from "./SegmentsConfig";
import { CardView } from "./views/CardView";
import { KanbanView } from "./views/KanbanView";
import { TimelineView } from "./views/TimelineView";
import { CalendarView } from "./views/CalendarView";
import { AdvancedSearch } from "./AdvancedSearch";
import { filtersToRows } from "./views/FilterConditionRows";
import type { ViewType, ViewFilter } from "@/lib/api";

interface Props {
  module: string;
  moduleLabel?: string;
  selectedId: number | null;
  onSelect: (id: number) => void;
  /** Bump to force a list refetch — wired to Copilot's
   *  ``onRecordUpdated`` so creating / editing a record from the chat
   *  pushes the row into the visible list immediately. */
  refreshNonce?: number;
  /** Called after the user creates a new record from the "+ New"
   *  button. Lets the parent select the new id and bump its own
   *  refresh nonce. */
  onCreated?: (newId: number) => void;
  /** Admin gate for org-level customization (list fields, segments, public views). */
  isAdmin?: boolean;
  /** Org-default list-item field layout (migration 0099). null = default. */
  orgListFields?: ListFieldsConfig | null;
  /** Called after an admin saves the org-default list fields. */
  onOrgListFieldsSaved?: (cfg: ListFieldsConfig | null) => void;
}

// Canonical column names match the DB schema (accounts_name with the
// trailing 's', not account_name). The singular form is also accepted
// as a fallback in case a custom module renames the field. ``name`` /
// ``subject`` / ``title`` / ``full_name`` are generic catch-alls.
const NAME_KEYS = [
  "accounts_name", "contacts_name", "leads_name", "potentials_name",
  "account_name",  "contact_name",  "lead_name",  "potential_name",
  "deal_name", "name", "subject", "title", "full_name",
];

// Friendly labels for interaction kinds — kept in sync with
// contact_stats._CONTACT_KINDS on the backend.
type KindKey =
  | "record.kind.email" | "record.kind.call" | "record.kind.call_transcript"
  | "record.kind.meeting" | "record.kind.meeting_notes" | "record.kind.im_message"
  | "record.kind.note" | "record.kind.contact_log" | "record.kind.voice_memo"
  | "record.kind.doc" | "record.kind.document" | "record.kind.sms";
const KIND_KEYS: Record<string, KindKey> = {
  email: "record.kind.email",
  call: "record.kind.call",
  call_transcript: "record.kind.call_transcript",
  meeting: "record.kind.meeting",
  meeting_notes: "record.kind.meeting_notes",
  im_message: "record.kind.im_message",
  note: "record.kind.note",
  contact_log: "record.kind.contact_log",
  voice_memo: "record.kind.voice_memo",
  doc: "record.kind.doc",
  document: "record.kind.document",
  sms: "record.kind.sms",
};

function kindLabel(t: T, k: string): string {
  const key = KIND_KEYS[k];
  return key ? t(key) : k;
}

function recordLabel(r: CrmRecord): string {
  for (const k of NAME_KEYS) {
    if (r[k]) return String(r[k]);
  }
  return `#${r.id}`;
}

function relativeDay(t: T, iso: unknown): string {
  if (!iso) return "";
  const d = new Date(String(iso));
  if (Number.isNaN(d.getTime())) return "";
  const now = new Date();
  const msDay = 86400000;
  const diffDays = Math.floor(
    (new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
      - new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime()) / msDay
  );
  if (diffDays < 0) {
    const n = -diffDays;
    if (n === 1) return t("record.rel.tomorrow");
    if (n < 7) return t("record.rel.days_later", { n });
    return t("record.rel.weeks_later", { n: Math.floor(n / 7) });
  }
  if (diffDays === 0) return t("record.rel.today");
  if (diffDays === 1) return t("record.rel.yesterday");
  if (diffDays < 7) return t("record.rel.days_ago", { n: diffDays });
  if (diffDays < 30) return t("record.rel.weeks_ago", { n: Math.floor(diffDays / 7) });
  if (diffDays < 365) return t("record.rel.months_ago", { n: Math.floor(diffDays / 30) });
  return t("record.rel.years_ago", { n: Math.floor(diffDays / 365) });
}

// Per-module row renderer. Law 4 — different agents, different lenses.
// We can't dispatch on the SQL column set alone because some modules
// (potentials, tasks, quotes) want a custom secondary line even when the
// charter projections aren't populated.
type RowLens = {
  primary: (r: CrmRecord) => string;
  secondary?: (t: T, r: CrmRecord) => { label: string; value: string; tone?: "danger" | "warn" | "ok" }[];
};
const MODULE_LENS: Record<string, RowLens> = {
  potentials: {
    primary: (r) => r.potential_name || r.deal_name || recordLabel(r),
    secondary: (t, r) => {
      const out: { label: string; value: string; tone?: "danger" | "warn" | "ok" }[] = [];
      if (r.amount != null && r.amount !== "") out.push({ label: t("record.stat.amount"), value: fmtMoney(t, r.amount), tone: "ok" });
      if (r.sales_stage || r.stage) out.push({ label: t("record.stat.stage"), value: enumLabel(t, r.sales_stage || r.stage) });
      if (r.close_date) {
        const tone = isPast(r.close_date) ? "danger" : undefined;
        out.push({ label: t("record.stat.close_date"), value: relativeDay(t, r.close_date), tone });
      }
      return out;
    },
  },
  tasks: {
    primary: (r) => r.subject || r.task_name || recordLabel(r),
    secondary: (t, r) => {
      const out: { label: string; value: string; tone?: "danger" | "warn" | "ok" }[] = [];
      if (r.priority) out.push({ label: t("record.stat.priority"), value: enumLabel(t, r.priority) });
      if (r.due_date) {
        const tone = isPast(r.due_date) ? "danger" : isSoon(r.due_date) ? "warn" : undefined;
        out.push({ label: t("record.stat.due"), value: relativeDay(t, r.due_date), tone });
      }
      if (r.status) out.push({ label: t("record.stat.status"), value: enumLabel(t, r.status) });
      return out;
    },
  },
  quotes: {
    primary: (r) => r.subject || r.quote_name || recordLabel(r),
    secondary: (t, r) => {
      const out: { label: string; value: string; tone?: "danger" | "warn" | "ok" }[] = [];
      if (r.grand_total != null) out.push({ label: t("record.stat.total"), value: fmtMoney(t, r.grand_total), tone: "ok" });
      if (r.quote_stage) out.push({ label: t("record.stat.stage"), value: enumLabel(t, r.quote_stage) });
      if (r.valid_till) {
        const tone = isPast(r.valid_till) ? "danger" : undefined;
        out.push({ label: t("record.stat.valid_till"), value: relativeDay(t, r.valid_till), tone });
      }
      return out;
    },
  },
  salesorders: {
    primary: (r) => r.subject || recordLabel(r),
    secondary: (t, r) => {
      const out: { label: string; value: string; tone?: "danger" | "warn" | "ok" }[] = [];
      if (r.grand_total != null) out.push({ label: t("record.stat.total"), value: fmtMoney(t, r.grand_total), tone: "ok" });
      if (r.status) out.push({ label: t("record.stat.status"), value: enumLabel(t, r.status) });
      return out;
    },
  },
};

function fmtMoney(t: T, v: unknown): string {
  if (v == null || v === "") return t("record.money.empty");
  const n = Number(v);
  if (!Number.isFinite(n)) return String(v);
  if (n >= 1_000_000) return t("record.money.m", { n: (n / 1_000_000).toFixed(1) });
  if (n >= 10_000) return t("record.money.wan", { n: (n / 10_000).toFixed(1) });
  return t("record.money.yuan", { n: n.toFixed(0) });
}
function isPast(iso: unknown): boolean {
  const d = new Date(String(iso)); if (Number.isNaN(d.getTime())) return false;
  return d.getTime() < Date.now();
}
function isSoon(iso: unknown): boolean {
  const d = new Date(String(iso)); if (Number.isNaN(d.getTime())) return false;
  const dt = d.getTime() - Date.now();
  return dt > 0 && dt < 48 * 3600 * 1000;
}

// Record sort — explicit field sort that overrides the charter (recent-
// activity) default. Only scalar columns are offered; multiSelect / relation
// / user / department / file are excluded (sorting them by raw id/json isn't
// meaningful to the user). created_at / updated_at are always available.
type SortValue = { field: string; dir: "ASC" | "DESC" };
const SORTABLE_TYPES = new Set([
  "text", "textarea", "email", "phone", "url",
  "number", "integer", "float", "double", "percent",
  "currency", "currencyJson", "date", "datetime",
  "select", "picklist", "autoNumber", "boolean",
]);

function SortControl({
  fields, value, onChange, canSaveDefault, savedDefaultActive, onSaveDefault, t,
}: {
  fields: CrmFieldMeta[];
  value: SortValue | null;
  onChange: (v: SortValue | null) => void;
  canSaveDefault: boolean;
  savedDefaultActive: boolean;
  onSaveDefault: () => void;
  t: T;
}) {
  const [open, setOpen] = useState(false);
  const options = useMemo(() => {
    const seen = new Set<string>();
    const out: { name: string; label: string }[] = [];
    for (const f of fields) {
      const n = (f.field_name || "").trim();
      if (!n || f.is_hidden || seen.has(n)) continue;
      if (!SORTABLE_TYPES.has(f.field_type)) continue;
      seen.add(n);
      out.push({ name: n, label: f.display_label || f.field_label || n });
    }
    for (const sys of [
      { name: "created_at", label: t("sort.field.created_at") },
      { name: "updated_at", label: t("sort.field.updated_at") },
    ]) {
      if (!seen.has(sys.name)) { seen.add(sys.name); out.push(sys); }
    }
    return out;
  }, [fields, t]);

  const arrow = value ? (value.dir === "ASC" ? "↑" : "↓") : "";
  const curLabel = value
    ? (options.find(o => o.name === value.field)?.label || value.field)
    : "";
  const btnText = value
    ? t("sort.current", { field: curLabel, arrow })
    : t("sort.button");

  function pick(name: string) {
    if (value && value.field === name) {
      onChange({ field: name, dir: value.dir === "ASC" ? "DESC" : "ASC" });
    } else {
      onChange({ field: name, dir: "DESC" });
    }
  }

  return (
    <div style={{ position: "relative", display: "inline-flex" }}>
      <button
        className="ghost"
        onClick={() => setOpen(o => !o)}
        // Icon-only to match the sibling toolbar buttons; active sort is
        // signalled by the accent border (like the filter funnel), and the
        // exact field/direction shows in the tooltip + dropdown.
        title={value ? btnText : t("sort.label")}
        aria-label={value ? btnText : t("sort.label")}
        style={{
          display: "inline-flex", alignItems: "center", justifyContent: "center",
          padding: "4px 8px", lineHeight: 1,
          opacity: value ? 1 : 0.7,
          border: `1px solid ${value ? "var(--accent)" : "var(--border)"}`,
          borderRadius: 6,
          color: value ? "var(--accent)" : "var(--muted)",
        }}
      >
        <CrmIcon name="sort" size={16} />
      </button>
      {open && (
        <>
          <div onClick={() => setOpen(false)}
               style={{ position: "fixed", inset: 0, zIndex: 40 }} />
          <div style={{
            position: "absolute", top: "100%", right: 0, marginTop: 4, zIndex: 41,
            background: "var(--panel)", border: "1px solid var(--border)",
            borderRadius: 8, minWidth: 200, maxHeight: 360, overflowY: "auto",
            boxShadow: "var(--shadow-popover)",
          }}>
            <button
              className="ghost"
              onClick={() => { onChange(null); setOpen(false); }}
              style={{
                display: "block", width: "100%", textAlign: "left",
                padding: "9px 12px", fontSize: 14, border: "none", borderRadius: 0,
                fontWeight: value ? 400 : 700,
              }}
            >
              {t("sort.default")}
              <span className="muted" style={{ marginLeft: 6, fontSize: 12 }}>
                {t("sort.default_hint")}
              </span>
            </button>
            <div style={{ borderTop: "1px solid var(--border)" }} />
            {options.map(o => {
              const active = value?.field === o.name;
              return (
                <button
                  key={o.name}
                  className="ghost"
                  onClick={() => pick(o.name)}
                  title={active ? (value!.dir === "ASC" ? t("sort.asc") : t("sort.desc")) : undefined}
                  style={{
                    display: "flex", width: "100%", alignItems: "center",
                    justifyContent: "space-between", gap: 8,
                    textAlign: "left", padding: "8px 12px", fontSize: 14,
                    border: "none", borderRadius: 0,
                    color: active ? "var(--accent)" : undefined,
                    fontWeight: active ? 700 : 400,
                  }}
                >
                  <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {o.label}
                  </span>
                  {active && (
                    <span aria-hidden="true">{value!.dir === "ASC" ? "↑" : "↓"}</span>
                  )}
                </button>
              );
            })}
            {canSaveDefault && (
              <>
                <div style={{ borderTop: "1px solid var(--border)" }} />
                <button
                  className="ghost"
                  onClick={() => { onSaveDefault(); setOpen(false); }}
                  disabled={savedDefaultActive}
                  style={{
                    display: "block", width: "100%", textAlign: "left",
                    padding: "9px 12px", fontSize: 13, border: "none", borderRadius: 0,
                    color: "var(--muted)", opacity: savedDefaultActive ? 0.5 : 1,
                  }}
                >⭑ {t("sort.save_as_default")}</button>
              </>
            )}
          </div>
        </>
      )}
    </div>
  );
}

export function RecordList({
  module, moduleLabel, selectedId, onSelect, refreshNonce, onCreated,
  isAdmin = false, orgListFields = null, onOrgListFieldsSaved,
}: Props) {
  const t = useT();
  const labelKey = `crm.module.${module}`;
  const translatedLabel = t(labelKey as DictKey);
  const listLabel = moduleLabel || (translatedLabel === labelKey ? module : translatedLabel);
  const [records, setRecords] = useState<CrmRecord[]>([]);
  const [total, setTotal] = useState(0);
  const [pageSize, setPageSize] = useState(50);
  const PAGE_STEP = 50;
  const [search, setSearch] = useState("");
  const [showCreate, setShowCreate] = useState(false);
  const [segment, setSegment] = useState<string>("all");
  const [segments, setSegments] = useState<{ id: string; label: string; count: number }[]>([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  // ── Field metadata (for configurable list-item rendering) ──────────
  const [fieldMeta, setFieldMeta] = useState<CrmFieldMeta[]>([]);
  useEffect(() => {
    let cancelled = false;
    getModuleFields(module)
      .then(r => { if (!cancelled) setFieldMeta(r.fields || []); })
      .catch(() => { if (!cancelled) setFieldMeta([]); });
    return () => { cancelled = true; };
  }, [module]);
  const fieldMap = useMemo(() => {
    const m: Record<string, CrmFieldMeta> = {};
    for (const f of fieldMeta) if (f.field_name) m[f.field_name] = f;
    return m;
  }, [fieldMeta]);

  // Resolved list-field layout: personal override → org default → built-in.
  // listFieldsNonce bumps after the customizer saves a personal override.
  const [listFieldsNonce, setListFieldsNonce] = useState(0);
  const listCfg = useMemo(
    () => resolveListFields(module, getPersonalListFields(module), orgListFields),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [module, orgListFields, listFieldsNonce],
  );
  const [showListCfg, setShowListCfg] = useState(false);

  // ── Custom views (module_views) ───────────────────────────────────
  const [views, setViews] = useState<ModuleView[]>([]);
  const [viewId, setViewId] = useState<number | null>(null);
  const [showViewBuilder, setShowViewBuilder] = useState(false);
  const [editingView, setEditingView] = useState<ModuleView | null>(null);
  const [showSegCfg, setShowSegCfg] = useState(false);
  const [viewMenuOpen, setViewMenuOpen] = useState(false);
  // Advanced search (Spec 4) — ad-hoc filters, mutually exclusive with a view.
  const [advOpen, setAdvOpen] = useState(false);
  const [adhocFilters, setAdhocFilters] = useState<ViewFilter[]>([]);
  const [adhocLogic, setAdhocLogic] = useState<"all" | "any">("all");
  const [viewBuilderSeed, setViewBuilderSeed] = useState<ViewFilter[] | null>(null);
  // Session sort override. Falls back to the active view's stored
  // view_config.sort_* when null; cleared on view/segment/module switch so
  // each context starts from its own saved default ("两者都要").
  const [adhocSort, setAdhocSort] = useState<SortValue | null>(null);
  useEffect(() => {
    let cancelled = false;
    listViews(module)
      .then(r => {
        if (cancelled) return;
        const vs = r.views || [];
        setViews(vs);
        // Restore last-selected view for this module if it still exists;
        // otherwise fall back to the admin-set default view (is_default).
        let restored: number | null = null;
        try {
          const saved = Number(localStorage.getItem(`crm_last_view_${module}`));
          if (Number.isFinite(saved) && vs.some(v => v.id === saved)) restored = saved;
        } catch { /* ignore */ }
        if (restored == null) {
          const def = vs.find(v => v.is_default);
          if (def) restored = def.id;
        }
        setViewId(restored);
        if (restored != null) setSegment("all");
      })
      .catch(() => { if (!cancelled) setViews([]); });
    return () => { cancelled = true; };
  }, [module, refreshKey]);

  // Active view + its render type (Spec 3). Non-table types render a
  // dedicated view component in the list column instead of the row list.
  const activeView = useMemo(() => views.find(v => v.id === viewId) || null, [views, viewId]);
  const activeType: ViewType = activeView?.type ?? "table";

  // Effective record sort: session override wins, else the active view's
  // stored default, else null (backend applies the charter recent-activity
  // sort). The view's default is read here and passed explicitly so the
  // backend stays stateless about per-view sort.
  const viewDefaultSort = useMemo<SortValue | null>(() => {
    const cfg = activeView?.view_config;
    if (cfg?.sort_field) {
      return { field: cfg.sort_field, dir: cfg.sort_direction === "ASC" ? "ASC" : "DESC" };
    }
    return null;
  }, [activeView]);
  const effectiveSort = adhocSort ?? viewDefaultSort;
  const sortMatchesViewDefault =
    (effectiveSort?.field ?? null) === (viewDefaultSort?.field ?? null) &&
    (effectiveSort?.dir ?? null) === (viewDefaultSort?.dir ?? null);

  async function handleSaveSortDefault() {
    if (!activeView) return;
    const v = activeView;
    const nextCfg = { ...(v.view_config || {}) } as Record<string, unknown>;
    if (effectiveSort) {
      nextCfg.sort_field = effectiveSort.field;
      nextCfg.sort_direction = effectiveSort.dir;
    } else {
      delete nextCfg.sort_field;
      delete nextCfg.sort_direction;
    }
    const body: ViewWriteBody = {
      name: v.name,
      filters: v.filters || [],
      filter_logic: v.filter_logic,
      type: v.type,
      view_config: nextCfg,
      is_default: v.is_default,
      is_public: v.is_public,
      role_ids: v.role_ids ?? null,
      sort_order: v.sort_order,
    };
    try {
      await updateView(module, v.id, body);
      toast(t("sort.saved_as_default"), "success");
      setAdhocSort(null);     // now baked into the view default
      setRefreshKey(k => k + 1);
    } catch (e: any) {
      toast(e?.message || t("sort.save_failed"), "error");
    }
  }

  function selectSegment(id: string) {
    setSegment(id);
    setViewId(null);
    setAdhocFilters([]);  // segment clears ad-hoc advanced search
    setAdhocSort(null);   // revert to default sort for the new context
    try { localStorage.removeItem(`crm_last_view_${module}`); } catch { /* ignore */ }
  }
  function selectView(id: number) {
    setViewId(id);
    setSegment("all");
    setAdhocFilters([]);  // selecting a view clears ad-hoc advanced search
    setAdhocSort(null);   // fall back to the view's stored default sort
    try { localStorage.setItem(`crm_last_view_${module}`, String(id)); } catch { /* ignore */ }
  }
  // Apply advanced search: ad-hoc filters replace any selected view (mutually
  // exclusive); set state directly (not via selectSegment) so we don't wipe
  // the filters we're applying.
  function applyAdvanced(f: ViewFilter[], logic: "all" | "any") {
    setViewId(null);
    setSegment("all");
    try { localStorage.removeItem(`crm_last_view_${module}`); } catch { /* ignore */ }
    setAdhocFilters(f);
    setAdhocLogic(logic);
    setAdhocSort(null);
    setAdvOpen(false);
  }
  function canEditView(v: ModuleView): boolean {
    if (isAdmin) return true;
    return !v.is_public;  // own private views (server enforces created_by)
  }
  async function handleDeleteView(v: ModuleView) {
    if (!window.confirm(t("views.delete_confirm", { name: v.name }))) return;
    try {
      await deleteView(module, v.id);
      toast(t("views.deleted"), "success");
      if (viewId === v.id) selectSegment("all");
      setRefreshKey(k => k + 1);
    } catch (e: any) {
      toast(e?.message || t("record.load_failed"), "error");
    }
  }

  // Phase 5.2.A — scope segment for managers / admins. v1.5: explicit
  // user choices live in localStorage; auto-defaults are derived from
  // /api/me.is_manager every render (don't write to localStorage on
  // mount — that would lock in 'self' before the manager-detection
  // effect can promote to 'team').
  const [me, setMe] = useState<CurrentUser | null>(null);
  const [scope, setScopeState] = useState<ScopeKind | null>(() => {
    const saved = localStorage.getItem("crm_list_scope");
    return (saved === "self" || saved === "team" || saved === "org")
      ? saved as ScopeKind : null;
  });
  useEffect(() => {
    getMe().then(setMe).catch(() => null);
  }, []);
  // ``setScope`` wraps state set + localStorage write so only explicit
  // user action (button clicks) persist.
  const setScope = (s: ScopeKind) => {
    localStorage.setItem("crm_list_scope", s);
    setScopeState(s);
  };
  // Effective scope = explicit choice OR role-based default.
  const effectiveScope: ScopeKind = scope || (me?.is_manager ? "team" : "self");

  // v1.7 — specific-user dropdown. Cleared whenever the user clicks a
  // scope segment (我的/团队/全部) so segment + dropdown can't disagree.
  const [ownerId, setOwnerId] = useState<number | undefined>(undefined);
  const [members, setMembers] = useState<TeamMember[]>([]);
  useEffect(() => {
    if (!me?.is_manager && !me?.is_admin) return;
    fetchTeamMembers()
      .then(r => setMembers(r.members || []))
      .catch(() => setMembers([]));
  }, [me?.is_manager, me?.is_admin]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setErr(null);
      try {
        // Non-table views (card/kanban/timeline/calendar) group/place all
        // records client-side, so pull a larger page; the table view paginates.
        const effPageSize = activeType === "table" ? pageSize : 500;
        const r = await listRecords(module, {
          page_size: effPageSize, search, segment, scope: effectiveScope,
          owner_id: ownerId, view_id: viewId ?? undefined,
          filters: adhocFilters.length ? adhocFilters : undefined,
          filter_logic: adhocLogic,
          sort_field: effectiveSort?.field,
          sort_direction: effectiveSort?.dir,
        });
        if (!cancelled) { setRecords(r.data || []); setTotal(typeof r.total === "number" ? r.total : (r.data || []).length); }
      } catch (e) {
        if (!cancelled) setErr(e instanceof ApiError ? `${e.status}` : t("record.load_failed"));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [module, search, segment, effectiveScope, ownerId, viewId, pageSize, activeType, adhocFilters, adhocLogic, effectiveSort?.field, effectiveSort?.dir, refreshKey, refreshNonce]);

  // Reset the page size whenever the filter inputs change, so switching
  // module / segment / view / scope starts back at the first page.
  useEffect(() => {
    setPageSize(50);
  }, [module, search, segment, effectiveScope, ownerId, viewId]);

  // Switching module clears any ad-hoc advanced search.
  useEffect(() => {
    setAdhocFilters([]);
    setAdhocSort(null);
    setAdvOpen(false);
  }, [module]);

  // Segment counts — re-fetch alongside module change but not on search.
  // v1.7: also include scope + ownerId so "全部 27" updates when the user
  // narrows to a specific owner.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await listSegments(module, { scope: effectiveScope, owner_id: ownerId });
        if (!cancelled) setSegments(r.segments || []);
      } catch { /* segments are optional UI sugar — swallow */ }
    })();
    return () => { cancelled = true; };
  }, [module, effectiveScope, ownerId, refreshKey, refreshNonce]);

  const lens = MODULE_LENS[module];
  const haveRecentEdits = useMemo(
    () => records.some(r => wasEditedRecently(r)),
    [records],
  );

  async function handleQuickUndo(recordId: number) {
    try {
      const r = await undoLastUpdate(module, recordId);
      toast(t("record.undo_toast", { n: r.reverted_fields.length }), "success");
      setRefreshKey(k => k + 1);
    } catch (e: any) {
      toast(e.message || t("record.undo_failed"), "error");
    }
  }

  return (
    <div className="col kacrm-record-list">
      <div className="kacrm-list-heading">
        <h2>{listLabel}</h2>
        <span aria-live="polite">
          {loading ? t("common.loading") : err ? "—" :
            segment !== "all" || viewId != null || adhocFilters.length > 0
              ? t("crm.list.showing", { n: records.length })
              : t("crm.list.count", { n: total })}
        </span>
      </div>
      {/* Charter v2 §定律 5: the old "Copilot 排优先级" / "从对话捕获"
          buttons that used to live here were removed (2026-05-08). Both
          asked the user to ask the AI to do something the AI should be
          doing on its own — list-level prioritization now lives in the
          event-stream's TodayPriority section, intake-from-chat is a
          backend job. The list is for browsing / filtering, not for
          firing AI prompts. */}

      {/* Phase 5.2.A — scope segment (managers + admins only). Renders
          above the existing event-driven segments so manager's first
          decision is "whose records am I looking at" before the slice. */}
      {(me?.is_manager || me?.is_admin) && (
        <div className="kacrm-list-scope" style={{
          display: "flex", flexWrap: "wrap", alignItems: "center",
          gap: 4, padding: 4,
          background: "var(--surface-soft)", borderRadius: 6,
          alignSelf: "flex-start",
        }}>
          <button
            onClick={() => { setScope("self"); setOwnerId(undefined); }}
            className={effectiveScope === "self" && ownerId == null ? "primary" : "ghost"}
            style={{ fontSize: 13, padding: "3px 10px", whiteSpace: "nowrap", flexShrink: 0 }}
          >我的</button>
          {(me?.is_manager || me?.is_admin) && (
            <button
              onClick={() => { setScope("team"); setOwnerId(undefined); }}
              className={effectiveScope === "team" && ownerId == null ? "primary" : "ghost"}
              style={{ fontSize: 13, padding: "3px 10px", whiteSpace: "nowrap", flexShrink: 0 }}
            >团队</button>
          )}
          {me?.is_admin && (
            <button
              onClick={() => { setScope("org"); setOwnerId(undefined); }}
              className={effectiveScope === "org" && ownerId == null ? "primary" : "ghost"}
              style={{ fontSize: 13, padding: "3px 10px", whiteSpace: "nowrap", flexShrink: 0 }}
            >全部</button>
          )}
          {/* v1.7 — narrow to a specific subordinate / user. */}
          {members.length > 1 && (
            <select
              value={ownerId ?? ""}
              onChange={(e) => {
                const v = e.target.value;
                setOwnerId(v ? Number(v) : undefined);
              }}
              style={{ fontSize: 13, padding: "3px 6px", marginLeft: 4, flexShrink: 0, maxWidth: 140 }}
              title="筛选具体成员"
            >
              <option value="">指定成员…</option>
              {members.map(m => (
                <option key={m.id} value={m.id}>
                  {m.is_self ? `${m.name}（我）` : m.name}
                </option>
              ))}
            </select>
          )}
        </div>
      )}

      {/* Unified view bar — built-in segments + custom views in one row. */}
      <div className="row kacrm-list-views" style={{ gap: 4, flexWrap: "wrap", alignItems: "center" }}>
        {segments.map(s => (
          <button
            key={`seg-${s.id}`}
            onClick={() => selectSegment(s.id)}
            className={viewId == null && segment === s.id ? "primary" : "ghost"}
            style={{
              fontSize: 13, padding: "3px 8px",
              opacity: s.count === 0 && s.id !== "all" ? 0.5 : 1,
            }}
            title={t("record.segment.title", { label: s.label, n: s.count })}
          >
            {/* 计数用 opacity 调淡而非 .muted 灰 —— 选中态是白字 accent 底,
                强制灰色会看不清;opacity 跟随当前文字色(选中=白/未选=正文)再调淡。 */}
            {s.label} <span style={{ marginLeft: 4, opacity: 0.65 }}>{s.count}</span>
          </button>
        ))}
        {views.map(v => (
          <span key={`view-${v.id}`} className="crm-view-tab" style={{ position: "relative", display: "inline-flex" }}>
            <button
              onClick={() => selectView(v.id)}
              className={viewId === v.id ? "primary" : "ghost"}
              style={{ fontSize: 13, padding: "3px 8px" }}
              title={v.name}
            >
              {v.name}
            </button>
            {canEditView(v) && (
              // Inline-on-hover (not an absolute popover anchored top:100%):
              // when the view bar wraps to a second row, a below-anchored
              // popover from a first-row tab covered the second-row tabs and
              // made them hard to click. Inline keeps the actions on the same
              // line as their own tab — no overlap with the row below.
              <span className="crm-view-actions" style={{ gap: 2, marginLeft: 1, alignItems: "center" }}>
                <button
                  className="ghost"
                  onClick={() => { setEditingView(v); setViewBuilderSeed(null); setShowViewBuilder(true); }}
                  title={t("views.edit")}
                  style={{ fontSize: 13, padding: "3px 5px", lineHeight: 1 }}
                ><CrmIcon name="edit" size={14} /></button>
                <button
                  className="ghost"
                  onClick={() => handleDeleteView(v)}
                  title={t("views.delete")}
                  style={{ fontSize: 13, padding: "3px 5px", lineHeight: 1, color: "var(--danger)" }}
                ><CrmIcon name="close" size={14} /></button>
              </span>
            )}
          </span>
        ))}
        {/* Overflow menu — keeps the bar clean and clearly separate from
            the search-row record/field buttons below. */}
        <div style={{ position: "relative", display: "inline-flex" }}>
          <button
            className="ghost"
            onClick={() => setViewMenuOpen(o => !o)}
            title={t("views.more")}
            aria-label={t("views.more")}
            style={{
              display: "inline-flex", alignItems: "center", justifyContent: "center",
              padding: "4px 8px", lineHeight: 1, opacity: 0.7,
              border: "1px solid var(--border)", borderRadius: 6,
              color: "var(--muted)",
            }}
          >
            <CrmIcon name="more" size={16} />
          </button>
          {viewMenuOpen && (
            <>
              <div onClick={() => setViewMenuOpen(false)}
                   style={{ position: "fixed", inset: 0, zIndex: 40 }} />
              <div style={{
                position: "absolute", top: "100%", left: 0, marginTop: 4, zIndex: 41,
                background: "var(--panel)", border: "1px solid var(--border)",
                borderRadius: 8, minWidth: 148, overflow: "hidden",
                boxShadow: "var(--shadow-popover)",
              }}>
                <button
                  className="ghost"
                  onClick={() => { setEditingView(null); setViewBuilderSeed(null); setShowViewBuilder(true); setViewMenuOpen(false); }}
                  style={{ display: "block", width: "100%", textAlign: "left", padding: "9px 12px", fontSize: 14, border: "none", borderRadius: 0 }}
                ><CrmIcon name="plus" size={14} /> {t("views.new_btn")}</button>
                {isAdmin && (
                  <button
                    className="ghost"
                    onClick={() => { setShowSegCfg(true); setViewMenuOpen(false); }}
                    style={{ display: "block", width: "100%", textAlign: "left", padding: "9px 12px", fontSize: 14, border: "none", borderRadius: 0, borderTop: "1px solid var(--border)" }}
                  ><CrmIcon name="settings" size={14} /> {t("views.manage_segments")}</button>
                )}
              </div>
            </>
          )}
        </div>
      </div>

      <div className="kacrm-list-tools">
        <label className="kacrm-list-search">
          <CrmIcon name="search" size={16} />
          <input
            placeholder={t("record.search_ph")}
            aria-label={t("record.search_ph")}
            value={search}
            onChange={e => setSearch(e.target.value)}
            style={{ flex: 1 }}
          />
        </label>
        <button
          className="ghost"
          onClick={() => setAdvOpen(o => !o)}
          title={t("advsearch.button")}
          aria-label={t("advsearch.button")}
          style={{
            position: "relative",
            display: "inline-flex", alignItems: "center", justifyContent: "center",
            padding: "4px 8px", lineHeight: 1,
            opacity: adhocFilters.length ? 1 : 0.7,
            border: `1px solid ${adhocFilters.length ? "var(--accent)" : "var(--border)"}`,
            borderRadius: 6,
            color: adhocFilters.length ? "var(--accent)" : "var(--muted)",
          }}
        >
          <CrmIcon name="filter" size={16} />
          {adhocFilters.length > 0 && (
            <span style={{
              position: "absolute", top: -6, right: -6, minWidth: 14, height: 14,
              padding: "0 3px", borderRadius: 7, background: "var(--accent)",
              color: "var(--fg-on-accent)", fontSize: 13, lineHeight: "14px", textAlign: "center", fontWeight: 700,
            }}>{adhocFilters.length}</span>
          )}
        </button>
        <SortControl
          fields={fieldMeta}
          value={effectiveSort}
          onChange={setAdhocSort}
          canSaveDefault={!!activeView && canEditView(activeView)}
          savedDefaultActive={sortMatchesViewDefault}
          onSaveDefault={handleSaveSortDefault}
          t={t}
        />
        <button
          className="ghost"
          onClick={() => setShowListCfg(true)}
          title={t("listcfg.button")}
          aria-label={t("listcfg.button")}
          style={{
            display: "inline-flex", alignItems: "center", justifyContent: "center",
            padding: "4px 8px", lineHeight: 1, opacity: 0.7,
            border: "1px solid var(--border)", borderRadius: 6,
            color: "var(--muted)",
          }}
        >
          <CrmIcon name="settings" size={16} />
        </button>
        <button
          className="ghost"
          onClick={() => setShowCreate(true)}
          title={t("crm.new_record_title", { module: listLabel })}
          aria-label={t("crm.new_record_title", { module: listLabel })}
          style={{
            display: "inline-flex", alignItems: "center", justifyContent: "center",
            padding: "4px 8px", lineHeight: 1, opacity: 0.7,
            border: "1px solid var(--border)", borderRadius: 6,
            color: "var(--muted)",
          }}
        >
          <CrmIcon name="plus" size={16} />
        </button>
      </div>

      <AdvancedSearch
        open={advOpen}
        fields={fieldMeta}
        module={module}
        initialRows={filtersToRows(adhocFilters)}
        initialLogic={adhocLogic}
        onApply={applyAdvanced}
        onClear={() => { setAdhocFilters([]); setAdvOpen(false); }}
        onSaveAsView={(f) => { setEditingView(null); setViewBuilderSeed(f); setShowViewBuilder(true); setAdvOpen(false); }}
      />
      {showCreate && (
        <CreateRecordModal
          module={module}
          moduleTitle={listLabel}
          onClose={() => setShowCreate(false)}
          onCreated={(id) => {
            setShowCreate(false);
            onSelect(id);
            setRefreshKey(k => k + 1);
            onCreated?.(id);
          }}
        />
      )}

      <div style={{ flex: 1, overflowY: "auto", minHeight: 0 }} className="col kacrm-list-results">
        {loading && <div className="muted small" style={{ padding: 8 }}>{t("common.loading")}</div>}
        {err && <div className="small" style={{ color: "var(--danger)", padding: 8 }}>{err}</div>}
        {/* Non-table view types render their own component (Spec 3). */}
        {!loading && !err && activeType === "card" && (
          <CardView records={records} fieldMap={fieldMap} viewConfig={activeView?.view_config}
            module={module} selectedId={selectedId} onSelect={onSelect} />
        )}
        {!loading && !err && activeType === "kanban" && (
          <KanbanView records={records} fieldMap={fieldMap} viewConfig={activeView?.view_config}
            module={module} selectedId={selectedId} onSelect={onSelect}
            onChanged={() => setRefreshKey(k => k + 1)} />
        )}
        {!loading && !err && activeType === "timeline" && (
          <TimelineView records={records} fieldMap={fieldMap} viewConfig={activeView?.view_config}
            module={module} selectedId={selectedId} onSelect={onSelect} />
        )}
        {!loading && !err && activeType === "calendar" && (
          <CalendarView records={records} fieldMap={fieldMap} viewConfig={activeView?.view_config}
            module={module} selectedId={selectedId} onSelect={onSelect} />
        )}
        {!loading && !err && activeType === "table" && records.length === 0 && (
          <div className="muted small" style={{ padding: 8 }}>
            {segment === "all" ? t("record.empty_all") : t("record.empty_segment")}
          </div>
        )}
        {!loading && !err && activeType === "table" && records.map((r) => {
          // Row 1 — primary title from configured row1 fields (falls back
          // to a name key, then #id). Row 2 — configured row2 fields as
          // chips, skipping missing/empty values.
          const primary = listPrimaryText(t, listCfg.row1, fieldMap, r);
          // Show every configured row2 field (with a "—" for empty values)
          // so customization is always visibly applied — not silently
          // dropped on records where the chosen field happens to be blank.
          // Only genuinely-unknown fields (hidden/deleted) are skipped.
          const row2Chips = listCfg.row2
            .map(fn => {
              const f = fieldMap[fn];
              if (!f) return null;
              const val = formatListFieldValue(t, f, r);
              return { key: fn, label: fieldLabel(t, f, module), value: val || "—" };
            })
            .filter(Boolean) as { key: string; label: string; value: string }[];
          const editedRecently = wasEditedRecently(r);
          const progress = String(r.latest_progress || "").trim();
          return (
            <button
              key={r.id}
              className="kacrm-record-row"
              aria-pressed={selectedId === r.id}
              onClick={() => onSelect(r.id)}
              style={{
                textAlign: "left",
                background: selectedId === r.id ? "var(--panel-2)" : "transparent",
                borderColor: selectedId === r.id ? "var(--accent)" : "transparent",
                padding: "8px 10px",
                display: "block",
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 8 }}>
                <div style={{ fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flex: 1 }}>
                  {primary}
                </div>
                {editedRecently && (
                  <span
                    role="button"
                    onClick={(e) => { e.stopPropagation(); handleQuickUndo(r.id); }}
                    title={t("record.recent_title")}
                    style={{
                      fontSize: 13, padding: "1px 6px", borderRadius: 4,
                      background: "var(--warning-soft)",
                      color: "var(--accent)",
                      border: "1px solid color-mix(in srgb, var(--warning) 40%, transparent)",
                      whiteSpace: "nowrap",
                      cursor: "pointer",
                    }}
                  >{t("record.recent_chip")}</span>
                )}
              </div>
              {row2Chips.length > 0 && (
                <div className="muted small" style={{
                  display: "flex", gap: 6, alignItems: "center",
                  flexWrap: "wrap", marginTop: 2,
                }}>
                  {row2Chips.map(c => (
                    <span className="kacrm-record-meta" key={c.key} style={{
                      padding: "1px 6px", borderRadius: 4,
                      background: "var(--panel-2)",
                      fontSize: 13,
                    }}>
                      {c.label}: {c.value}
                    </span>
                  ))}
                </div>
              )}
              {progress && (
                <div className="muted" style={{
                  fontSize: 13, marginTop: 3,
                  overflow: "hidden", textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                  opacity: 0.75,
                }}>
                  <CrmIcon name="message" size={14} /> {progress}
                </div>
              )}
            </button>
          );
        })}
        {!loading && !err && activeType === "table" && records.length > 0 && records.length >= pageSize && records.length < total && (
          <button
            className="ghost"
            onClick={() => setPageSize(p => p + PAGE_STEP)}
            style={{
              margin: "8px 4px 4px", padding: "8px 10px", fontSize: 14,
              border: "1px dashed var(--border)", borderRadius: 6,
              color: "var(--muted)", textAlign: "center",
            }}
          >
            {t("record.load_more")} · {t("record.shown_count", { n: records.length, total })}
          </button>
        )}
      </div>
      {haveRecentEdits && (
        <div className="muted" style={{ fontSize: 13, textAlign: "center" }}>
          {t("record.recent_hint")}
        </div>
      )}

      <ListFieldsCustomizer
        open={showListCfg}
        onClose={() => setShowListCfg(false)}
        module={module}
        isAdmin={isAdmin}
        orgDefault={orgListFields}
        onOrgSaved={(cfg) => onOrgListFieldsSaved?.(cfg)}
        onPersonalChanged={() => setListFieldsNonce(n => n + 1)}
      />
      <ViewBuilder
        open={showViewBuilder}
        onClose={() => setShowViewBuilder(false)}
        module={module}
        isAdmin={isAdmin}
        editing={editingView}
        seedFilters={viewBuilderSeed}
        onSaved={(v) => { setRefreshKey(k => k + 1); selectView(v.id); }}
      />
      <SegmentsConfig
        open={showSegCfg}
        onClose={() => setShowSegCfg(false)}
        module={module}
        onSaved={() => setRefreshKey(k => k + 1)}
      />
    </div>
  );
}

// "Was this row edited in the last 120 seconds by me (or anyone)?"
// We approximate with updated_at — an exact "was-it-me" check would need
// updated_by === current_user_id, which we don't have client-side.
// Over-showing is safe: the server enforces the 120s+same-actor rule on
// the actual undo call, and will reject if either constraint fails.
function wasEditedRecently(r: CrmRecord): boolean {
  const ts = r.updated_at || r.updatedAt;
  if (!ts) return false;
  const dt = Date.parse(String(ts));
  if (Number.isNaN(dt)) return false;
  return Date.now() - dt < 120_000;
}
