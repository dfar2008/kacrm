import { useEffect, useMemo, useState } from "react";
import type { CrmFieldMeta } from "@/lib/api";
import { updateModuleHeadline, ApiError } from "@/lib/api";
import {
  resolveHeadlineFields, headlineLabelOverride,
  getPersonalHeadline, setPersonalHeadline, clearPersonalHeadline,
} from "@/lib/moduleHeadlines";
import { toast } from "@/lib/toast";
import { useT, type T } from "@/lib/i18n";

/**
 * Headline chip-row customizer (detail page "顶部信息").
 *
 * Two scopes via tabs, mirroring the list-field customizer:
 *   - "我的视图" (personal): everyone can set; stored in localStorage,
 *     resolves personal → org → built-in default.
 *   - "组织默认" (org): admin-only, persisted via PUT /headline.
 *
 * Field count is uncapped. Left column = ordered picked fields
 * (↑ / ↓ / ×); right column = candidate fields (visible, non-system,
 * not the primary name field) with a search box; click to append.
 */

const SYSTEM_FIELD_NAMES = new Set([
  "createdtime", "modifiedtime", "created_time", "modified_time", "updated_time",
  "created_by", "modified_by", "deleted", "assigned_group", "assigned_user",
  "updatedby", "createdby",
]);

function isSystemish(f: CrmFieldMeta): boolean {
  if (SYSTEM_FIELD_NAMES.has(f.field_name.toLowerCase())) return true;
  if (f.field_name === "owner_by") return false;
  if (/(time|_at)$/i.test(f.field_name)) return true;
  if (/^modified|^created/i.test(f.field_name)) return true;
  return false;
}

function labelFor(f: CrmFieldMeta, t: T): string {
  return headlineLabelOverride(f.field_name, t)
    || f.field_label
    || f.field_name;
}

type Tab = "personal" | "org";

export function HeadlineCustomizer({
  open, onClose, module, fields, isAdmin, orgDefault, onOrgSaved, onPersonalChanged,
}: {
  open: boolean;
  onClose: () => void;
  module: string;
  /** The module's full field metadata (from getRecord). */
  fields: CrmFieldMeta[];
  /** Admin gate for the "组织默认" tab. */
  isAdmin: boolean;
  /** The module's ``collections.headline_fields`` org default (null = none). */
  orgDefault: string[] | null | undefined;
  /** Called after an admin saves the org default (server echo, or null). */
  onOrgSaved: (fields: string[] | null) => void;
  /** Called after a personal override save / reset so the parent re-renders. */
  onPersonalChanged: () => void;
}) {
  const t = useT();

  const initialFor = useMemo(() => {
    return (which: Tab): string[] => {
      if (which === "org") {
        return resolveHeadlineFields(module, orgDefault, null);
      }
      return getPersonalHeadline(module) ?? resolveHeadlineFields(module, orgDefault, null);
    };
  }, [module, orgDefault]);

  const [tab, setTab] = useState<Tab>("personal");
  const [picked, setPicked] = useState<string[]>([]);
  const [filter, setFilter] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return;
    setTab("personal");
    setPicked(initialFor("personal"));
    setFilter("");
  }, [open, initialFor]);

  useEffect(() => {
    if (!open) return;
    const h = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [open, onClose]);

  if (!open) return null;

  const byName = new Map(fields.map(f => [f.field_name, f]));

  function switchTab(next: Tab) {
    if (next === "org" && !isAdmin) {
      toast(t("listcfg.org_admin_only"), "error");
      return;
    }
    setTab(next);
    setPicked(initialFor(next));
    setFilter("");
  }

  const candidates = fields
    .filter(f => !f.is_hidden)
    .filter(f => !isSystemish(f))
    .filter(f => f.field_name !== `${module}_name`)
    .filter(f => !picked.includes(f.field_name))
    .filter(f => {
      if (!filter.trim()) return true;
      const q = filter.trim().toLowerCase();
      return f.field_name.toLowerCase().includes(q)
        || (f.field_label || "").toLowerCase().includes(q)
        || labelFor(f, t).toLowerCase().includes(q);
    });

  function move(name: string, delta: -1 | 1) {
    setPicked(prev => {
      const i = prev.indexOf(name);
      const j = i + delta;
      if (i < 0 || j < 0 || j >= prev.length) return prev;
      const out = prev.slice();
      [out[i], out[j]] = [out[j], out[i]];
      return out;
    });
  }
  function remove(name: string) {
    setPicked(prev => prev.filter(n => n !== name));
  }
  function add(name: string) {
    setPicked(prev => prev.includes(name) ? prev : [...prev, name]);
  }

  async function onSave() {
    if (tab === "personal") {
      setPersonalHeadline(module, picked);
      toast(t("headline.toast.saved"), "success");
      onPersonalChanged();
      onClose();
      return;
    }
    setSaving(true);
    try {
      const res = await updateModuleHeadline(module, picked);
      toast(t("headline.toast.saved"), "success");
      onOrgSaved(res.fields ?? null);
      onClose();
    } catch (e) {
      const msg = e instanceof ApiError
        ? (typeof e.detail === "string" ? e.detail : JSON.stringify(e.detail))
        : t("headline.toast.save_failed");
      toast(msg, "error");
    } finally {
      setSaving(false);
    }
  }

  async function onRestore() {
    if (tab === "personal") {
      clearPersonalHeadline(module);
      toast(t("headline.toast.reset"), "success");
      onPersonalChanged();
      onClose();
      return;
    }
    setSaving(true);
    try {
      await updateModuleHeadline(module, null);
      toast(t("headline.toast.reset"), "success");
      onOrgSaved(null);
      onClose();
    } catch (e) {
      const msg = e instanceof ApiError
        ? (typeof e.detail === "string" ? e.detail : JSON.stringify(e.detail))
        : t("headline.toast.save_failed");
      toast(msg, "error");
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      <div
        onClick={onClose}
        style={{ position: "fixed", inset: 0, background: "var(--overlay)", zIndex: 60 }}
      />
      <div
        role="dialog"
        aria-label={t("headline.aria")}
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
              {t("headline.title", { module })}
            </div>
            <div className="muted small" style={{ marginTop: 2 }}>
              {t("headline.sub")}
            </div>
          </div>
          <button className="ghost" onClick={onClose} style={{ fontSize: 18, padding: "2px 10px" }}>
            ×
          </button>
        </div>

        {/* Tabs */}
        <div style={{ display: "flex", gap: 8, padding: "10px 16px 0" }}>
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
          {/* Picked column */}
          <div>
            <div className="small" style={{ fontWeight: 600, marginBottom: 6 }}>
              {t("headline.picked.heading", { n: picked.length })}
            </div>
            {picked.length === 0 && (
              <div className="muted small" style={{
                padding: 12, border: "1px dashed var(--border)", borderRadius: 6,
              }}>
                {t("headline.picked.empty")}
              </div>
            )}
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {picked.map((name, i) => {
                const f = byName.get(name);
                const lbl = f ? labelFor(f, t) : name;
                const missing = !f;
                return (
                  <div
                    key={name}
                    className="row"
                    style={{
                      gap: 6, alignItems: "center", padding: "6px 8px",
                      border: "1px solid var(--border)", borderRadius: 6,
                      background: missing ? "var(--danger-soft)" : "var(--card-bg)",
                    }}
                  >
                    <span className="muted small" style={{ width: 16 }}>{i + 1}.</span>
                    <span style={{ flex: 1, fontSize: 14 }}>
                      {lbl}
                      <span className="muted small" style={{ marginLeft: 6 }}>{name}</span>
                      {missing && (
                        <span className="small" style={{ marginLeft: 6, color: "var(--danger)" }}>
                          {t("headline.missing")}
                        </span>
                      )}
                    </span>
                    <button className="ghost" onClick={() => move(name, -1)} disabled={i === 0}
                      title={t("headline.btn.up")} style={{ fontSize: 14, padding: "2px 6px" }}>↑</button>
                    <button className="ghost" onClick={() => move(name, +1)} disabled={i === picked.length - 1}
                      title={t("headline.btn.down")} style={{ fontSize: 14, padding: "2px 6px" }}>↓</button>
                    <button className="ghost" onClick={() => remove(name)}
                      title={t("headline.btn.remove")} style={{ fontSize: 14, padding: "2px 6px", color: "var(--danger)" }}>×</button>
                  </div>
                );
              })}
            </div>
          </div>

          {/* Candidates column */}
          <div>
            <div className="small" style={{ fontWeight: 600, marginBottom: 6 }}>
              {t("headline.candidates.heading")}
            </div>
            <input
              type="text"
              value={filter}
              onChange={e => setFilter(e.target.value)}
              placeholder={t("headline.candidates.ph")}
              style={{
                width: "100%", padding: "6px 8px",
                border: "1px solid var(--border)", borderRadius: 6,
                background: "transparent", color: "inherit", marginBottom: 8,
              }}
            />
            <div style={{ display: "flex", flexDirection: "column", gap: 4, maxHeight: "46vh", overflowY: "auto" }}>
              {candidates.length === 0 && (
                <div className="muted small" style={{ padding: 8 }}>
                  {t("headline.candidates.empty")}
                </div>
              )}
              {candidates.map(f => (
                <button
                  key={f.field_name}
                  className="ghost"
                  onClick={() => add(f.field_name)}
                  title={t("headline.candidates.add")}
                  style={{
                    display: "flex", justifyContent: "space-between", alignItems: "center",
                    padding: "6px 8px", fontSize: 14,
                    border: "1px solid var(--border)", borderRadius: 6, textAlign: "left",
                  }}
                >
                  <span>{labelFor(f, t)}</span>
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
          <button className="ghost" onClick={onRestore} disabled={saving}
            title={t("headline.reset.title")} style={{ fontSize: 14, color: "var(--muted)" }}>
            {t("headline.reset.label")}
          </button>
          <div className="row" style={{ gap: 8 }}>
            <button className="ghost" onClick={onClose} disabled={saving}>
              {t("common.cancel")}
            </button>
            <button className="primary" onClick={onSave} disabled={saving || picked.length === 0}>
              {saving ? t("headline.saving") : t("common.save")}
            </button>
          </div>
        </div>
      </div>
    </>
  );
}
