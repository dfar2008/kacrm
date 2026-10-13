import { useEffect, useMemo, useState } from "react";
import type { CrmFieldMeta, ModuleView, ViewFilter, ViewWriteBody, AdminRole, ViewType, ViewConfig } from "@/lib/api";
import { getModuleFields, createView, updateView, adminListRoles } from "@/lib/api";
import { fieldLabel } from "@/lib/crmLabels";
import { toast } from "@/lib/toast";
import { useT } from "@/lib/i18n";
import {
  FilterConditionRows, rowsToFilters, filtersToRows, buildCandidateFields,
  type RowState,
} from "./views/FilterConditionRows";

/**
 * Create / edit a CRM custom view (a saved filter set on a module list).
 *
 * Mechanics
 * ---------
 * Top: view name. A logic toggle (all / any) maps to ``filter_logic``.
 * A list of condition rows, each = field + operator + value. The value
 * input adapts to the field type (select → dropdown, else text) and to a
 * "value kind" selector that lets a condition reference dynamic values
 * (current user, my team, today, this week, last N days) instead of a
 * literal — these serialize to magic strings the backend resolves.
 *
 * Visibility: private / public-everyone / public-roles (the public
 * options are admin-only; non-admins can only save private views).
 * Admins may also flag the view as the module default.
 *
 * Save assembles a ``ViewWriteBody`` and calls ``createView`` or
 * ``updateView``; the parent refreshes its view list via ``onSaved``.
 */

interface Props {
  open: boolean;
  onClose: () => void;
  module: string;
  isAdmin: boolean;
  /** 传入则为编辑模式,否则新建 */
  editing?: ModuleView | null;
  /** 新建时预填的筛选条件(高级搜索「保存为视图」用)。 */
  seedFilters?: ViewFilter[] | null;
  /** 保存成功后回调,父组件刷新视图列表并可切到该视图 */
  onSaved: (view: ModuleView) => void;
}

// View render types (Spec 3).
const VIEW_TYPES: ViewType[] = ["table", "card", "kanban", "timeline", "calendar"];

// Scalar field types offered as a view's default sort. Excludes
// multiSelect / relation / user / department / file (not meaningful to
// order by raw id/json). created_at / updated_at are added separately.
const SORTABLE_FIELD_TYPES = new Set([
  "text", "textarea", "email", "phone", "url",
  "number", "integer", "float", "double", "percent",
  "currency", "currencyJson", "date", "datetime",
  "select", "picklist", "autoNumber", "boolean",
]);

export function ViewBuilder(props: Props) {
  const { open, onClose, module, isAdmin, editing, seedFilters, onSaved } = props;
  const t = useT();

  const [fields, setFields] = useState<CrmFieldMeta[]>([]);
  const [roles, setRoles] = useState<AdminRole[] | null>(null);
  const [rolesAvailable, setRolesAvailable] = useState(false);

  const [name, setName] = useState("");
  const [type, setType] = useState<ViewType>("table");
  const [cfg, setCfg] = useState<ViewConfig>({});
  const [logic, setLogic] = useState<"all" | "any">("all");
  const [rows, setRows] = useState<RowState[]>([]);
  const [visibility, setVisibility] = useState<"private" | "public" | "roles">("private");
  const [roleIds, setRoleIds] = useState<number[]>([]);
  const [setDefault, setSetDefault] = useState(false);
  const [saving, setSaving] = useState(false);
  // The card-fields multi-select is the modal's biggest space hog — keep it
  // collapsed by default so the dialog reads cleanly; expand on demand.
  const [cardFieldsOpen, setCardFieldsOpen] = useState(false);

  // Load module fields + (admin) roles when opened.
  useEffect(() => {
    if (!open) return;
    let alive = true;
    getModuleFields(module)
      .then(res => { if (alive) setFields(res.fields || []); })
      .catch(() => { if (alive) setFields([]); });
    if (isAdmin) {
      adminListRoles()
        .then(res => { if (!alive) return; setRoles(res.roles || []); setRolesAvailable(true); })
        .catch(() => { if (alive) { setRoles(null); setRolesAvailable(false); } });
    } else {
      setRoles(null);
      setRolesAvailable(false);
    }
    return () => { alive = false; };
  }, [open, module, isAdmin]);

  // Seed form state from editing target (or reset for new) on open.
  useEffect(() => {
    if (!open) return;
    if (editing) {
      setName(editing.name || "");
      setType(editing.type ?? "table");
      setCfg(editing.view_config ?? {});
      setLogic(editing.filter_logic === "any" ? "any" : "all");
      setRows(filtersToRows(editing.filters || []));
      if (editing.role_ids && editing.role_ids.length) {
        setVisibility("roles");
        setRoleIds(editing.role_ids);
      } else if (editing.is_public) {
        setVisibility("public");
        setRoleIds([]);
      } else {
        setVisibility("private");
        setRoleIds([]);
      }
      setSetDefault(!!editing.is_default);
    } else {
      setName("");
      setType("table");
      setCfg({});
      setLogic("all");
      setRows(seedFilters && seedFilters.length ? filtersToRows(seedFilters) : []);
      setVisibility("private");
      setRoleIds([]);
      setSetDefault(false);
    }
  }, [open, editing, seedFilters]);

  // ESC to close.
  useEffect(() => {
    if (!open) return;
    const h = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [open, onClose]);

  // Candidate fields (visible + system) — shared with the filter builder.
  const candidateFields = useMemo(
    () => buildCandidateFields(t, fields, module),
    [fields, module, t],
  );

  // Field subsets for type-specific config (Spec 3).
  const selectFields = useMemo(
    () => fields.filter(f => !f.is_hidden && (f.field_type === "select" || f.field_type === "picklist")),
    [fields],
  );
  // Kanban can group by any categorical field — select/picklist plus
  // owner(user) / creator / department / relation (负责人/创建人/部门/关联).
  const groupableFields = useMemo(
    () => fields.filter(f => !f.is_hidden &&
      ["select", "picklist", "user", "createdBy", "department", "relation"].includes(f.field_type)),
    [fields],
  );
  const dateFields = useMemo(
    () => fields.filter(f => !f.is_hidden && (f.field_type === "date" || f.field_type === "datetime")),
    [fields],
  );
  // Default-sort options: scalar module fields + the always-available
  // created/updated timestamps. Mirrors the list toolbar's SortControl.
  const sortOptions = useMemo(() => {
    const seen = new Set<string>();
    const out: { value: string; label: string }[] = [];
    const push = (value: string, label: string) => {
      if (value && !seen.has(value)) { seen.add(value); out.push({ value, label }); }
    };
    push("created_at", t("sort.field.created_at"));
    push("updated_at", t("sort.field.updated_at"));
    for (const f of fields) {
      if (!f.is_hidden && SORTABLE_FIELD_TYPES.has(f.field_type)) {
        push(f.field_name, f.display_label || f.field_label || f.field_name);
      }
    }
    return out;
  }, [fields, t]);

  if (!open) return null;

  function toggleRole(id: number) {
    setRoleIds(prev => prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id]);
  }

  async function save() {
    if (!name.trim()) {
      toast(t("viewbuilder.name_required"), "error");
      return;
    }
    if (type === "kanban" && !cfg.group_by) {
      toast(t("viewbuilder.need_group_by"), "error");
      return;
    }
    if ((type === "timeline" || type === "calendar") && !cfg.date_field) {
      toast(t("viewbuilder.need_date_field"), "error");
      return;
    }
    const filters: ViewFilter[] = rowsToFilters(rows);

    // Persist sort even for table views (which otherwise carry no
    // view_config). For table we keep ONLY the sort keys; richer types
    // keep their full cfg (group_by / date_field / card_* …) plus sort.
    let view_config: ViewConfig | null;
    const sortKeys = cfg.sort_field
      ? { sort_field: cfg.sort_field, sort_direction: cfg.sort_direction === "ASC" ? "ASC" as const : "DESC" as const }
      : null;
    if (type === "table") {
      view_config = sortKeys;
    } else {
      const base: ViewConfig = { ...cfg };
      delete base.sort_field; delete base.sort_direction;
      view_config = sortKeys ? { ...base, ...sortKeys } : base;
    }

    const isPublic = visibility === "public" || visibility === "roles";
    const body: ViewWriteBody = {
      name: name.trim(),
      filters,
      filter_logic: logic,
      type,
      view_config,
      is_public: isPublic,
    };
    if (isAdmin) body.is_default = setDefault;
    if (visibility === "roles") body.role_ids = roleIds;

    setSaving(true);
    try {
      const res = editing
        ? await updateView(module, editing.id, body)
        : await createView(module, body);
      toast(t("viewbuilder.saved"), "success");
      onSaved(res.view);
      onClose();
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e), "error");
    } finally {
      setSaving(false);
    }
  }

  const inputStyle: React.CSSProperties = {
    padding: "6px 8px",
    border: "1px solid var(--border)", borderRadius: 6,
    background: "transparent", color: "inherit",
  };

  return (
    <>
      {/* Backdrop */}
      <div
        onClick={onClose}
        style={{ position: "fixed", inset: 0, background: "var(--overlay)", zIndex: 60 }}
      />
      {/* Modal */}
      <div
        role="dialog"
        aria-label={editing ? t("viewbuilder.title_edit") : t("viewbuilder.title_new")}
        style={{
          position: "fixed",
          top: "50%", left: "50%", transform: "translate(-50%, -50%)",
          width: "min(720px, 92vw)", maxHeight: "86vh",
          background: "var(--bg)", color: "var(--fg)",
          border: "1px solid var(--border)", borderRadius: 10,
          boxShadow: "var(--shadow-modal)",
          zIndex: 70,
          display: "flex", flexDirection: "column",
        }}
      >
        {/* Header */}
        <div style={{
          display: "flex", justifyContent: "space-between", alignItems: "center",
          padding: "12px 16px", borderBottom: "1px solid var(--border)",
        }}>
          <div style={{ fontSize: 15, fontWeight: 600 }}>
            {editing ? t("viewbuilder.title_edit") : t("viewbuilder.title_new")}
          </div>
          <button className="ghost" onClick={onClose} style={{ fontSize: 18, padding: "2px 10px" }}>
            ×
          </button>
        </div>

        {/* Body */}
        <div style={{ padding: 16, overflow: "auto", flex: 1, display: "flex", flexDirection: "column", gap: 16 }}>
          {/* Name */}
          <div>
            <div className="small" style={{ fontWeight: 600, marginBottom: 6 }}>
              {t("viewbuilder.name")}
            </div>
            <input
              type="text"
              value={name}
              onChange={e => setName(e.target.value)}
              placeholder={t("viewbuilder.name_ph")}
              style={{ ...inputStyle, width: "100%" }}
            />
          </div>

          {/* View type + type-specific config (Spec 3) */}
          <div>
            <div className="small" style={{ fontWeight: 600, marginBottom: 6 }}>
              {t("viewbuilder.type")}
            </div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 8 }}>
              {VIEW_TYPES.map(vt => (
                <button
                  key={vt}
                  className={type === vt ? "primary" : "ghost"}
                  onClick={() => setType(vt)}
                  style={{ fontSize: 14, padding: "5px 12px" }}
                >
                  {t(`viewtype.${vt}`)}
                </button>
              ))}
            </div>
            {/* Per-type config */}
            {type === "kanban" && (
              <label style={{ display: "block" }}>
                <span className="muted small">{t("viewbuilder.group_by")}</span>
                <select value={cfg.group_by || ""} onChange={e => setCfg(c => ({ ...c, group_by: e.target.value }))} style={{ ...inputStyle, width: "100%", marginTop: 4 }}>
                  <option value="">—</option>
                  {groupableFields.map(f => <option key={f.field_name} value={f.field_name}>{fieldLabel(t, f, module)}</option>)}
                </select>
              </label>
            )}
            {type === "timeline" && (
              <label style={{ display: "block" }}>
                <span className="muted small">{t("viewbuilder.date_field")}</span>
                <select value={cfg.date_field || ""} onChange={e => setCfg(c => ({ ...c, date_field: e.target.value }))} style={{ ...inputStyle, width: "100%", marginTop: 4 }}>
                  <option value="">—</option>
                  {dateFields.map(f => <option key={f.field_name} value={f.field_name}>{fieldLabel(t, f, module)}</option>)}
                </select>
              </label>
            )}
            {type === "calendar" && (
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                <label style={{ display: "block" }}>
                  <span className="muted small">{t("viewbuilder.date_field")}</span>
                  <select value={cfg.date_field || ""} onChange={e => setCfg(c => ({ ...c, date_field: e.target.value }))} style={{ ...inputStyle, width: "100%", marginTop: 4 }}>
                    <option value="">—</option>
                    {dateFields.map(f => <option key={f.field_name} value={f.field_name}>{fieldLabel(t, f, module)}</option>)}
                  </select>
                </label>
                <label style={{ display: "block" }}>
                  <span className="muted small">{t("viewbuilder.end_date_field")}</span>
                  <select value={cfg.end_date_field || ""} onChange={e => setCfg(c => ({ ...c, end_date_field: e.target.value || null }))} style={{ ...inputStyle, width: "100%", marginTop: 4 }}>
                    <option value="">{t("viewbuilder.cfg_none")}</option>
                    {dateFields.map(f => <option key={f.field_name} value={f.field_name}>{fieldLabel(t, f, module)}</option>)}
                  </select>
                </label>
                <label style={{ display: "block" }}>
                  <span className="muted small">{t("viewbuilder.color_field")}</span>
                  <select value={cfg.color_field || ""} onChange={e => setCfg(c => ({ ...c, color_field: e.target.value || null }))} style={{ ...inputStyle, width: "100%", marginTop: 4 }}>
                    <option value="">{t("viewbuilder.cfg_none")}</option>
                    {selectFields.map(f => <option key={f.field_name} value={f.field_name}>{fieldLabel(t, f, module)}</option>)}
                  </select>
                </label>
              </div>
            )}
            {type === "card" && (
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                <label style={{ display: "block" }}>
                  <span className="muted small">{t("viewbuilder.card_cover_field")}</span>
                  <select value={cfg.card_cover_field || ""} onChange={e => setCfg(c => ({ ...c, card_cover_field: e.target.value || null }))} style={{ ...inputStyle, width: "100%", marginTop: 4 }}>
                    <option value="">{t("viewbuilder.cfg_none")}</option>
                    {candidateFields.filter(c => c.field).map(c => <option key={c.value} value={c.value}>{c.label}</option>)}
                  </select>
                </label>
                <label style={{ display: "block" }}>
                  <span className="muted small">{t("viewbuilder.card_per_row")}</span>
                  <select value={String(cfg.card_per_row || 1)} onChange={e => setCfg(c => ({ ...c, card_per_row: Number(e.target.value) }))} style={{ ...inputStyle, width: "100%", marginTop: 4 }}>
                    {[1, 2, 3, 4, 5, 6, 7, 8].map(n => <option key={n} value={n}>{n}</option>)}
                  </select>
                </label>
                <div>
                  <button
                    type="button"
                    className="ghost"
                    onClick={() => setCardFieldsOpen(o => !o)}
                    style={{
                      display: "flex", alignItems: "center", gap: 6, width: "100%",
                      padding: "2px 0", fontSize: 13, border: "none",
                      background: "transparent", color: "var(--muted)", cursor: "pointer",
                    }}
                  >
                    <span style={{ fontSize: 11, width: 10 }}>{cardFieldsOpen ? "▾" : "▸"}</span>
                    {t("viewbuilder.card_fields")}
                    <span style={{ opacity: 0.7 }}>
                      （{cfg.card_fields?.length
                          ? cfg.card_fields.length
                          : t("viewbuilder.inherit_list_fields")}）
                    </span>
                  </button>
                  {cardFieldsOpen && (
                    <div style={{ display: "flex", flexWrap: "wrap", gap: 6, maxHeight: 160, overflowY: "auto", marginTop: 6 }}>
                      {candidateFields.filter(c => c.field).map(c => {
                        const on = (cfg.card_fields || []).includes(c.value);
                        return (
                          <button key={c.value}
                            className={on ? "primary" : "ghost"}
                            onClick={() => setCfg(prev => {
                              const cur = prev.card_fields || [];
                              return { ...prev, card_fields: on ? cur.filter(x => x !== c.value) : [...cur, c.value] };
                            })}
                            style={{ fontSize: 13, padding: "3px 8px" }}
                          >{c.label}</button>
                        );
                      })}
                    </div>
                  )}
                </div>
              </div>
            )}
            {/* Default record sort — a view-level property (table/card/kanban).
                Timeline/calendar order by their date_field instead. */}
            {["table", "card", "kanban"].includes(type) && (
              <div style={{ borderTop: "1px solid var(--border)", paddingTop: 12, marginTop: 4 }}>
                <label style={{ display: "block" }}>
                  <span className="muted small">{t("viewbuilder.sort_by")}</span>
                  <select
                    value={cfg.sort_field || ""}
                    onChange={e => {
                      const v = e.target.value;
                      setCfg(c => {
                        if (!v) {
                          const { sort_field: _sf, sort_direction: _sd, ...rest } = c;
                          void _sf; void _sd;
                          return rest;
                        }
                        return { ...c, sort_field: v, sort_direction: c.sort_direction === "ASC" ? "ASC" : "DESC" };
                      });
                    }}
                    style={{ ...inputStyle, width: "100%", marginTop: 4 }}
                  >
                    <option value="">{t("sort.default")}</option>
                    {sortOptions.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                  </select>
                </label>
                {cfg.sort_field && (
                  <div style={{ display: "flex", gap: 6, marginTop: 6 }}>
                    {(["DESC", "ASC"] as const).map(d => (
                      <button
                        key={d}
                        type="button"
                        className={(cfg.sort_direction === "ASC" ? "ASC" : "DESC") === d ? "primary" : "ghost"}
                        onClick={() => setCfg(c => ({ ...c, sort_direction: d }))}
                        style={{ fontSize: 13, padding: "4px 12px" }}
                      >
                        {d === "DESC" ? t("sort.desc") : t("sort.asc")}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Conditions — shared builder. */}
          <div style={{ borderTop: "1px solid var(--border)", paddingTop: 16 }}>
            <FilterConditionRows
              fields={fields}
              module={module}
              rows={rows}
              onChange={setRows}
              logic={logic}
              onLogicChange={setLogic}
            />
          </div>

          {/* Visibility — segmented buttons (cleaner than raw radios). */}
          <div style={{ borderTop: "1px solid var(--border)", paddingTop: 16 }}>
            <div className="small" style={{ fontWeight: 600, marginBottom: 6 }}>
              {t("viewbuilder.visibility")}
            </div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
              <button
                className={visibility === "private" ? "primary" : "ghost"}
                onClick={() => setVisibility("private")}
                style={{ fontSize: 14, padding: "5px 12px" }}
              >
                {t("viewbuilder.vis_private")}
              </button>
              {isAdmin && (
                <button
                  className={visibility === "public" ? "primary" : "ghost"}
                  onClick={() => setVisibility("public")}
                  style={{ fontSize: 14, padding: "5px 12px" }}
                >
                  {t("viewbuilder.vis_public")}
                </button>
              )}
              {isAdmin && rolesAvailable && (
                <button
                  className={visibility === "roles" ? "primary" : "ghost"}
                  onClick={() => setVisibility("roles")}
                  style={{ fontSize: 14, padding: "5px 12px" }}
                >
                  {t("viewbuilder.vis_roles")}
                </button>
              )}
            </div>

            {visibility === "roles" && rolesAvailable && (
              <div style={{ marginTop: 8 }}>
                <div className="muted small" style={{ marginBottom: 4 }}>
                  {t("viewbuilder.roles")}
                </div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                  {(roles || []).map(r => (
                    <label key={r.id} className="row" style={{ gap: 4, alignItems: "center", fontSize: 14 }}>
                      <input
                        type="checkbox"
                        checked={roleIds.includes(r.id)}
                        onChange={() => toggleRole(r.id)}
                      />
                      {r.name}
                    </label>
                  ))}
                </div>
              </div>
            )}
          </div>

          {/* Default + admin hint */}
          {isAdmin ? (
            <label className="row" style={{ gap: 6, alignItems: "center", fontSize: 14 }}>
              <input
                type="checkbox"
                checked={setDefault}
                onChange={e => setSetDefault(e.target.checked)}
              />
              {t("viewbuilder.set_default")}
            </label>
          ) : (
            <div className="muted small">{t("viewbuilder.admin_only_hint")}</div>
          )}
        </div>

        {/* Footer */}
        <div style={{
          display: "flex", justifyContent: "flex-end", alignItems: "center", gap: 8,
          padding: "10px 16px", borderTop: "1px solid var(--border)",
        }}>
          <button className="ghost" onClick={onClose} disabled={saving}>
            {t("common.cancel")}
          </button>
          <button className="primary" onClick={save} disabled={saving}>
            {t("common.save")}
          </button>
        </div>
      </div>
    </>
  );
}
