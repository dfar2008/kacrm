import { useEffect, useMemo, useState } from "react";
import type { CrmFieldMeta, ListFieldsConfig } from "@/lib/api";
import { getModuleFields, updateModuleListFields, ApiError } from "@/lib/api";
import {
  getPersonalListFields,
  setPersonalListFields,
  clearPersonalListFields,
  resolveListFields,
  defaultListFields,
} from "@/lib/listFields";
import { fieldLabel } from "@/lib/crmLabels";
import { toast } from "@/lib/toast";
import { useT } from "@/lib/i18n";

/**
 * List-item field customizer (Spec 1) — let users pick which CRM fields show
 * on each list row, split across two rows (row1 = title line, row2 = sub).
 *
 * Two scopes via tabs:
 *   - "我的视图" (personal): localStorage override, resolves personal → org →
 *     built-in default.
 *   - "组织默认" (org): admin-only, persisted via PUT list-fields.
 *
 * Field count is uncapped. Body is a two-column grid: left = the two ordered
 * drop-rows (↑ / ↓ / ⇅ move-other-row / × remove); right = candidate fields
 * (visible, non-hidden, NOT already placed) with a search box; click to append
 * to row2. Mirrors HeadlineCustomizer's modal shell.
 */

interface Props {
  open: boolean;
  onClose: () => void;
  module: string;
  isAdmin: boolean;
  /** 组织默认(后端 list_fields),null=未设 */
  orgDefault: ListFieldsConfig | null;
  /** 保存组织默认后回调,把新值传回父组件 */
  onOrgSaved: (cfg: ListFieldsConfig | null) => void;
  /** 保存个人覆盖或恢复默认后回调,父组件据此刷新列表渲染 */
  onPersonalChanged: () => void;
}

type Tab = "personal" | "org";
type RowKey = "row1" | "row2";

export function ListFieldsCustomizer(props: Props) {
  const { open, onClose, module, isAdmin, orgDefault, onOrgSaved, onPersonalChanged } = props;
  const t = useT();

  const [tab, setTab] = useState<Tab>("personal");
  const [fields, setFields] = useState<CrmFieldMeta[]>([]);
  const [edited, setEdited] = useState<ListFieldsConfig>({ row1: [], row2: [] });
  const [filter, setFilter] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);

  // Initial edit object for the active tab.
  const initialFor = useMemo(() => {
    return (which: Tab): ListFieldsConfig => {
      if (which === "org") {
        return orgDefault ?? defaultListFields(module);
      }
      return getPersonalListFields(module) ?? resolveListFields(module, null, orgDefault);
    };
  }, [module, orgDefault]);

  // Load field metadata when the modal opens / module changes.
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoading(true);
    getModuleFields(module)
      .then(res => {
        if (cancelled) return;
        setFields(res.fields.filter(f => !f.is_hidden));
      })
      .catch(e => {
        if (cancelled) return;
        const msg = e instanceof ApiError
          ? (typeof e.detail === "string" ? e.detail : JSON.stringify(e.detail))
          : t("listcfg.load_failed");
        toast(msg, "error");
        setFields([]);
      })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [open, module, t]);

  // Reset tab + edit object whenever the modal reopens.
  useEffect(() => {
    if (!open) return;
    const start: Tab = isAdmin ? "personal" : "personal";
    setTab(start);
    setEdited(clone(initialFor(start)));
    setFilter("");
  }, [open, isAdmin, initialFor]);

  // ESC to close.
  useEffect(() => {
    if (!open) return;
    const h = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [open, onClose]);

  if (!open) return null;

  const byName = new Map(fields.map(f => [f.field_name, f]));
  const placed = new Set([...edited.row1, ...edited.row2]);

  function switchTab(next: Tab) {
    if (next === "org" && !isAdmin) {
      toast(t("listcfg.org_admin_only"), "error");
      return;
    }
    setTab(next);
    setEdited(clone(initialFor(next)));
    setFilter("");
  }

  const candidates = fields
    .filter(f => !placed.has(f.field_name))
    .filter(f => {
      if (!filter.trim()) return true;
      const q = filter.trim().toLowerCase();
      return f.field_name.toLowerCase().includes(q)
        || (f.field_label || "").toLowerCase().includes(q)
        || fieldLabel(t, f, module).toLowerCase().includes(q);
    });

  function move(row: RowKey, name: string, delta: -1 | 1) {
    setEdited(prev => {
      const arr = prev[row].slice();
      const i = arr.indexOf(name);
      const j = i + delta;
      if (i < 0 || j < 0 || j >= arr.length) return prev;
      [arr[i], arr[j]] = [arr[j], arr[i]];
      return { ...prev, [row]: arr };
    });
  }

  function moveOther(row: RowKey, name: string) {
    const other: RowKey = row === "row1" ? "row2" : "row1";
    setEdited(prev => ({
      ...prev,
      [row]: prev[row].filter(n => n !== name),
      [other]: [...prev[other], name],
    }));
  }

  function remove(row: RowKey, name: string) {
    setEdited(prev => ({ ...prev, [row]: prev[row].filter(n => n !== name) }));
  }

  function add(name: string) {
    setEdited(prev => {
      if (prev.row1.includes(name) || prev.row2.includes(name)) return prev;
      return { ...prev, row2: [...prev.row2, name] };
    });
  }

  function labelOf(name: string): string {
    const f = byName.get(name);
    return f ? fieldLabel(t, f, module) : name;
  }

  async function onSave() {
    if (tab === "personal") {
      setPersonalListFields(module, edited);
      toast(t("listcfg.saved"), "success");
      onPersonalChanged();
      onClose();
      return;
    }
    // org default
    setSaving(true);
    try {
      const res = await updateModuleListFields(module, edited);
      toast(t("listcfg.saved"), "success");
      onOrgSaved(res.fields ?? null);
      onClose();
    } catch (e) {
      const msg = e instanceof ApiError
        ? (typeof e.detail === "string" ? e.detail : JSON.stringify(e.detail))
        : t("listcfg.save_failed");
      toast(msg, "error");
    } finally {
      setSaving(false);
    }
  }

  async function onRestore() {
    if (tab === "personal") {
      clearPersonalListFields(module);
      toast(t("listcfg.reset_done"), "success");
      onPersonalChanged();
      onClose();
      return;
    }
    setSaving(true);
    try {
      await updateModuleListFields(module, null);
      toast(t("listcfg.reset_done"), "success");
      onOrgSaved(null);
      onClose();
    } catch (e) {
      const msg = e instanceof ApiError
        ? (typeof e.detail === "string" ? e.detail : JSON.stringify(e.detail))
        : t("listcfg.save_failed");
      toast(msg, "error");
    } finally {
      setSaving(false);
    }
  }

  const renderRow = (row: RowKey, heading: string) => {
    const names = edited[row];
    return (
      <div>
        <div className="small" style={{ fontWeight: 600, marginBottom: 6 }}>
          {heading}
        </div>
        {names.length === 0 && (
          <div className="muted small" style={{
            padding: 12, border: "1px dashed var(--border)", borderRadius: 6,
          }}>
            {t("listcfg.empty_row")}
          </div>
        )}
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {names.map((name, i) => (
            <div
              key={name}
              className="row"
              style={{
                gap: 6, alignItems: "center", padding: "6px 8px",
                border: "1px solid var(--border)", borderRadius: 6,
                background: byName.has(name)
                  ? "var(--card-bg)"
                  : "var(--danger-soft)",
              }}
            >
              <span style={{ flex: 1, fontSize: 14 }}>
                {labelOf(name)}
                <span className="muted small" style={{ marginLeft: 6 }}>{name}</span>
              </span>
              <button
                className="ghost"
                onClick={() => move(row, name, -1)}
                disabled={i === 0}
                title={t("listcfg.move_up")}
                style={{ fontSize: 14, padding: "2px 6px" }}
              >↑</button>
              <button
                className="ghost"
                onClick={() => move(row, name, +1)}
                disabled={i === names.length - 1}
                title={t("listcfg.move_down")}
                style={{ fontSize: 14, padding: "2px 6px" }}
              >↓</button>
              <button
                className="ghost"
                onClick={() => moveOther(row, name)}
                title={t("listcfg.move_other")}
                style={{ fontSize: 14, padding: "2px 6px" }}
              >⇅</button>
              <button
                className="ghost"
                onClick={() => remove(row, name)}
                title={t("listcfg.remove")}
                style={{ fontSize: 14, padding: "2px 6px", color: "var(--danger)" }}
              >×</button>
            </div>
          ))}
        </div>
      </div>
    );
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
        aria-label={t("listcfg.title", { module })}
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
          <div>
            <div style={{ fontSize: 15, fontWeight: 600 }}>
              {t("listcfg.title", { module })}
            </div>
            <div className="muted small" style={{ marginTop: 2 }}>
              {t("listcfg.subtitle")}
            </div>
          </div>
          <button className="ghost" onClick={onClose} style={{ fontSize: 18, padding: "2px 10px" }}>
            ×
          </button>
        </div>

        {/* Tabs */}
        <div style={{
          display: "flex", gap: 8, padding: "10px 16px 0",
        }}>
          <button
            className={tab === "personal" ? "primary" : "ghost"}
            onClick={() => switchTab("personal")}
            style={{ fontSize: 14, padding: "4px 12px" }}
          >
            {t("listcfg.tab.personal")}
          </button>
          {isAdmin && (
            <button
              className={tab === "org" ? "primary" : "ghost"}
              onClick={() => switchTab("org")}
              style={{ fontSize: 14, padding: "4px 12px" }}
            >
              {t("listcfg.tab.org")}
            </button>
          )}
        </div>

        {/* Body — two columns */}
        <div style={{
          display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12,
          padding: 16, overflow: "auto", flex: 1,
        }}>
          {/* Left: the two rows */}
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            {renderRow("row1", t("listcfg.row1"))}
            {renderRow("row2", t("listcfg.row2"))}
          </div>

          {/* Right: candidates */}
          <div>
            <input
              type="text"
              value={filter}
              onChange={e => setFilter(e.target.value)}
              placeholder={t("listcfg.search_ph")}
              style={{
                width: "100%", padding: "6px 8px",
                border: "1px solid var(--border)", borderRadius: 6,
                background: "transparent", color: "inherit",
                marginBottom: 8,
              }}
            />
            <div style={{
              display: "flex", flexDirection: "column", gap: 4,
              maxHeight: "52vh", overflowY: "auto",
            }}>
              {loading && (
                <div className="muted small" style={{ padding: 8 }}>…</div>
              )}
              {!loading && candidates.length === 0 && (
                <div className="muted small" style={{ padding: 8 }}>
                  {t("listcfg.empty_row")}
                </div>
              )}
              {candidates.map(f => (
                <button
                  key={f.field_name}
                  className="ghost"
                  onClick={() => add(f.field_name)}
                  style={{
                    display: "flex", justifyContent: "space-between",
                    alignItems: "center",
                    padding: "6px 8px", fontSize: 14,
                    border: "1px solid var(--border)", borderRadius: 6,
                    textAlign: "left",
                  }}
                >
                  <span>{fieldLabel(t, f, module)}</span>
                  <span className="muted small">{f.field_name}</span>
                </button>
              ))}
            </div>
          </div>
        </div>

        {/* Footer */}
        <div style={{
          display: "flex", justifyContent: "space-between", alignItems: "center",
          padding: "10px 16px", borderTop: "1px solid var(--border)",
        }}>
          <button
            className="ghost"
            onClick={onRestore}
            disabled={saving}
            style={{ fontSize: 14, color: "var(--muted)" }}
          >
            {t("listcfg.restore")}
          </button>
          <div className="row" style={{ gap: 8 }}>
            <button className="ghost" onClick={onClose} disabled={saving}>
              {t("common.cancel")}
            </button>
            <button
              className="primary"
              onClick={onSave}
              disabled={saving}
            >
              {t("common.save")}
            </button>
          </div>
        </div>
      </div>
    </>
  );
}

function clone(cfg: ListFieldsConfig): ListFieldsConfig {
  return { row1: cfg.row1.slice(), row2: cfg.row2.slice() };
}
