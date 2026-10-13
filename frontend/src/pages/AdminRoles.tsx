import { useEffect, useMemo, useState } from "react";
import {
  AdminRole, RolePermissionMatrix,
  adminListRoles, adminCreateRole, adminUpdateRole, adminDeleteRole,
  adminGetRolePermissions, adminSetRolePermissions,
} from "@/lib/api";
import { toast } from "@/lib/toast";
import { useT } from "@/lib/i18n";

/**
 * Admin → Roles & permissions.
 *
 * Left: scrollable list of roles with clone / edit / delete actions.
 * Right: module × permission matrix grouped into tabs by ``kind``
 * (CRM · Admin) so the 20+ modules stay legible.
 *
 * Workflow:
 *   - Checkbox toggles update a local ``draft``; a Save button flushes
 *     the whole matrix to ``PUT /api/admin/roles/{id}/permissions``.
 *   - Switching roles with unsaved changes prompts a dirty-guard
 *     confirmation (browser ``confirm()``) rather than silently
 *     discarding the draft.
 *   - Clone copies both the role metadata and its permission grants in
 *     one atomic UI step (two API calls behind the scenes).
 */
export function AdminRolesPage() {
  const t = useT();
  const [roles, setRoles] = useState<AdminRole[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [editing, setEditing] = useState<AdminRole | null>(null);
  // Bumped by the matrix when the user saves; used as a key to remount
  // the matrix after a clone so it picks up the fresh grants.
  const [matrixVersion, setMatrixVersion] = useState(0);
  // Set by the child matrix when it has unsaved changes — parent reads
  // this in the row-switch guard. Simple ref-like state.
  const [dirty, setDirty] = useState(false);

  async function refresh() {
    setLoading(true); setErr(null);
    try {
      const r = (await adminListRoles()).roles || [];
      setRoles(r);
      if (selectedId == null && r.length > 0) setSelectedId(r[0].id);
    } catch (e: any) { setErr(e.message); }
    finally { setLoading(false); }
  }
  useEffect(() => { refresh(); }, []);

  async function onDelete(r: AdminRole) {
    if (r.is_default) { toast(t("admin.roles.builtin_cannot_delete"), "error"); return; }
    if (!confirm(t("admin.roles.delete_confirm", { name: r.name }))) return;
    try {
      await adminDeleteRole(r.id);
      toast(t("admin.roles.toast.deleted"), "success");
      if (selectedId === r.id) setSelectedId(null);
      refresh();
    } catch (e: any) { toast(e.message, "error"); }
  }

  async function onClone(r: AdminRole) {
    const copyName = prompt(
      t("admin.roles.clone_prompt", { name: r.name }),
      t("admin.roles.clone_default_name", { name: r.name }),
    );
    if (!copyName || !copyName.trim()) return;
    try {
      const src = await adminGetRolePermissions(r.id);
      const created = await adminCreateRole({
        name: copyName.trim(),
        description: r.description,
      });
      // Flatten the nested grants map into the PUT shape.
      const grants: Record<string, number[]> = {};
      for (const [modId, perms] of Object.entries(src.grants || {})) {
        const ids: number[] = [];
        for (const [pid, has] of Object.entries(perms || {})) {
          if (has) ids.push(Number(pid));
        }
        if (ids.length) grants[modId] = ids;
      }
      await adminSetRolePermissions(created.id, grants);
      toast(t("admin.roles.toast.cloned"), "success");
      await refresh();
      setSelectedId(created.id);
    } catch (e: any) { toast(e.message, "error"); }
  }

  function selectRole(id: number) {
    if (id === selectedId) return;
    if (dirty && !confirm(t("admin.roles.switch_dirty_confirm"))) return;
    setSelectedId(id);
    setDirty(false);
  }

  return (
    <div style={{ padding: 24, overflow: "auto", height: "100%" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
        <h2 style={{ margin: 0 }}>{t("admin.roles.title")}</h2>
        <button className="primary" onClick={() => setShowCreate(true)}>{t("admin.roles.new")}</button>
      </div>
      <p className="muted" style={{ margin: "4px 0 16px", fontSize: 14 }}>
        {t("admin.roles.subtitle")}
      </p>

      {err && <div className="alert alert-error" style={{ marginBottom: 12 }}>{err}</div>}
      {loading && <div className="muted">{t("common.loading")}</div>}

      {!loading && (
        <div style={{
          display: "grid", gridTemplateColumns: "240px 1fr",
          gap: 16, minHeight: 400,
        }}>
          {/* Role list */}
          <div style={{
            border: "1px solid var(--border)", borderRadius: 8,
            background: "var(--bg)", overflow: "hidden",
          }}>
            {roles.length === 0 ? (
              <div className="muted" style={{ padding: 16, fontSize: 14 }}>
                {t("admin.roles.empty")}
              </div>
            ) : roles.map(r => (
              <div
                key={r.id}
                onClick={() => selectRole(r.id)}
                style={{
                  padding: "10px 12px",
                  borderBottom: "1px solid var(--border)",
                  cursor: "pointer",
                  background: selectedId === r.id ? "var(--panel-2)" : "transparent",
                  borderLeft: selectedId === r.id ? "3px solid var(--accent)" : "3px solid transparent",
                  transition: "background 120ms",
                }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: 6, fontWeight: 500 }}>
                  {r.name}
                  {r.is_default && (
                    <span style={{
                      fontSize: 13, padding: "1px 6px", borderRadius: 999,
                      background: "color-mix(in srgb, var(--accent) 13%, transparent)", color: "var(--accent-2)",
                      border: "1px solid color-mix(in srgb, var(--accent) 33%, transparent)",
                    }}>{t("admin.roles.builtin_badge")}</span>
                  )}
                </div>
                <div className="muted" style={{ fontSize: 13, marginTop: 2 }}>
                  {t("admin.roles.member_count", { n: r.member_count })}
                  {" · "}
                  {/* Profile cap: ∞ for NULL (unlimited), otherwise numeric. */}
                  <span title={t("admin.roles.profile_quota_title")}>
                    🎯 {r.profile_quota == null ? t("admin.roles.profile_quota_unlimited") : r.profile_quota}
                  </span>
                  {r.description ? ` · ${r.description}` : ""}
                </div>
                <div style={{ display: "flex", gap: 4, marginTop: 6 }}>
                  <button
                    style={ghostBtn}
                    onClick={e => { e.stopPropagation(); setEditing(r); }}
                  >{t("admin.roles.action.edit")}</button>
                  <button
                    style={ghostBtn}
                    onClick={e => { e.stopPropagation(); onClone(r); }}
                    title={t("admin.roles.action.clone_title")}
                  >{t("admin.roles.action.clone")}</button>
                  <button
                    style={{ ...ghostBtn, color: r.is_default ? "var(--muted)" : "var(--danger)", borderColor: r.is_default ? "var(--border)" : "color-mix(in srgb, var(--danger) 25%, transparent)" }}
                    disabled={r.is_default}
                    onClick={e => { e.stopPropagation(); onDelete(r); }}
                  >{t("admin.roles.action.delete")}</button>
                </div>
              </div>
            ))}
          </div>

          {/* Permission matrix */}
          <div>
            {selectedId == null ? (
              <div className="muted" style={{ padding: 24, textAlign: "center" }}>
                {t("admin.roles.select_hint")}
              </div>
            ) : (
              <PermissionMatrix
                key={`${selectedId}-${matrixVersion}`}
                roleId={selectedId}
                onDirtyChange={setDirty}
                onSaved={() => { setDirty(false); setMatrixVersion(v => v + 1); }}
              />
            )}
          </div>
        </div>
      )}

      {showCreate && (
        <RoleDialog
          mode="create"
          onClose={() => setShowCreate(false)}
          onSaved={() => { setShowCreate(false); refresh(); }}
        />
      )}
      {editing && (
        <RoleDialog
          mode="edit"
          role={editing}
          onClose={() => setEditing(null)}
          onSaved={() => { setEditing(null); refresh(); }}
        />
      )}
    </div>
  );
}

// ── Matrix ────────────────────────────────────────────────────────────

const KIND_TABS: { key: string; labelKey: "admin.roles.tab.crm" | "admin.roles.tab.admin"; icon: string }[] = [
  { key: "crm",   labelKey: "admin.roles.tab.crm",   icon: "📇" },
  { key: "admin", labelKey: "admin.roles.tab.admin", icon: "🏢" },
];

function PermissionMatrix({
  roleId, onDirtyChange, onSaved,
}: {
  roleId: number;
  onDirtyChange?: (dirty: boolean) => void;
  onSaved?: () => void;
}) {
  const t = useT();
  const [data, setData] = useState<RolePermissionMatrix | null>(null);
  const [draft, setDraft] = useState<Record<string, Set<number>>>({});
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [activeKind, setActiveKind] = useState<string>("crm");

  async function refresh() {
    setLoading(true); setErr(null);
    try {
      const d = await adminGetRolePermissions(roleId);
      setData(d);
      const draft0: Record<string, Set<number>> = {};
      for (const [modId, perms] of Object.entries(d.grants || {})) {
        const s = new Set<number>();
        for (const [permId, has] of Object.entries(perms || {})) {
          if (has) s.add(Number(permId));
        }
        draft0[modId] = s;
      }
      setDraft(draft0);
    } catch (e: any) { setErr(e.message); }
    finally { setLoading(false); }
  }
  useEffect(() => { refresh(); }, [roleId]);

  const dirty = useMemo(() => {
    if (!data) return false;
    const orig: Record<string, Set<number>> = {};
    for (const [modId, perms] of Object.entries(data.grants || {})) {
      const s = new Set<number>();
      for (const [permId, has] of Object.entries(perms || {})) {
        if (has) s.add(Number(permId));
      }
      orig[modId] = s;
    }
    const keys = new Set([...Object.keys(orig), ...Object.keys(draft)]);
    for (const k of keys) {
      const a = orig[k] || new Set();
      const b = draft[k] || new Set();
      if (a.size !== b.size) return true;
      for (const x of a) if (!b.has(x)) return true;
    }
    return false;
  }, [data, draft]);

  // Propagate dirty state up so the parent can block role-switching mid-edit.
  useEffect(() => { onDirtyChange?.(dirty); }, [dirty, onDirtyChange]);

  // Warn on leaving the page with unsaved changes.
  useEffect(() => {
    if (!dirty) return;
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [dirty]);

  function toggle(moduleId: number, permId: number) {
    setDraft(prev => {
      const next = { ...prev };
      const cur = new Set(next[String(moduleId)] || []);
      if (cur.has(permId)) cur.delete(permId); else cur.add(permId);
      next[String(moduleId)] = cur;
      return next;
    });
  }

  function toggleRow(moduleId: number, allIds: number[], allOn: boolean) {
    setDraft(prev => ({
      ...prev,
      [String(moduleId)]: new Set(allOn ? [] : allIds),
    }));
  }

  async function save() {
    setSaving(true);
    try {
      const payload: Record<string, number[]> = {};
      for (const [k, v] of Object.entries(draft)) {
        if (v.size > 0) payload[k] = [...v];
      }
      await adminSetRolePermissions(roleId, payload);
      toast(t("admin.roles.toast.perms_saved"), "success");
      await refresh();
      onSaved?.();
    } catch (e: any) { toast(e.message, "error"); }
    finally { setSaving(false); }
  }

  if (loading) return <div className="muted">{t("common.loading")}</div>;
  if (err) return <div className="alert alert-error">{err}</div>;
  if (!data) return null;

  // Count modules per kind for tab badges + filter the visible matrix.
  const kindCounts: Record<string, number> = {};
  for (const m of data.modules) {
    const k = m.kind || "crm";
    kindCounts[k] = (kindCounts[k] || 0) + 1;
  }
  const visibleModules = data.modules.filter(m => (m.kind || "crm") === activeKind);
  const visiblePerms = data.permissions;

  return (
    <div style={{
      border: "1px solid var(--border)", borderRadius: 8,
      background: "var(--bg)", overflow: "hidden",
    }}>
      {/* Header */}
      <div style={{
        padding: "14px 16px", borderBottom: "1px solid var(--border)",
        display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10,
      }}>
        <div>
          <div style={{ fontSize: 15, fontWeight: 600, display: "flex", alignItems: "center", gap: 8 }}>
            {data.role.name}
            {dirty && (
              <span style={{
                fontSize: 13, padding: "1px 8px", borderRadius: 999,
                background: "color-mix(in srgb, var(--warning) 13%, transparent)", color: "var(--warning)",
                border: "1px solid color-mix(in srgb, var(--warning) 33%, transparent)",
              }}>{t("admin.roles.dirty_badge")}</span>
            )}
          </div>
          {data.role.description && (
            <div className="muted" style={{ fontSize: 14, marginTop: 2 }}>{data.role.description}</div>
          )}
        </div>
        <button className="primary" onClick={save} disabled={!dirty || saving}>
          {saving ? t("admin.roles.saving") : t("admin.roles.save_perms")}
        </button>
      </div>

      {/* Kind tabs — only surface tabs that have at least one module. */}
      <div style={{
        display: "flex", gap: 2, padding: "8px 12px 0",
        borderBottom: "1px solid var(--border)",
        background: "var(--bg)",
      }}>
        {KIND_TABS.filter(tab => (kindCounts[tab.key] || 0) > 0).map(tab => (
          <button
            key={tab.key}
            onClick={() => setActiveKind(tab.key)}
            style={{
              padding: "8px 14px", fontSize: 14,
              background: activeKind === tab.key ? "var(--panel-2)" : "transparent",
              color: activeKind === tab.key ? "inherit" : "var(--muted)",
              border: "none",
              borderBottom: activeKind === tab.key ? "2px solid var(--accent)" : "2px solid transparent",
              cursor: "pointer",
              display: "flex", alignItems: "center", gap: 6,
            }}
          >
            <span>{tab.icon}</span>
            <span>{t(tab.labelKey)}</span>
            <span className="muted" style={{ fontSize: 13 }}>({kindCounts[tab.key]})</span>
          </button>
        ))}
      </div>

      {/* Matrix table */}
      {visibleModules.length === 0 ? (
        <div className="muted" style={{ padding: 24, textAlign: "center" }}>
          {t("admin.roles.no_modules")}
        </div>
      ) : (
        <div style={{ overflow: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 14 }}>
            <thead>
              <tr style={{
                fontSize: 13, textTransform: "uppercase", letterSpacing: 0.5,
                color: "var(--muted)", background: "var(--panel)",
              }}>
                <th style={thStyle}>{t("admin.roles.col.module")}</th>
                {visiblePerms.map(p => {
                  const moduleIds = visibleModules.map(m => m.id);
                  const allOnCol = moduleIds.length > 0
                    && moduleIds.every(mid => (draft[String(mid)] || new Set()).has(p.id));
                  return (
                    <th key={p.id} style={{ ...thStyle, textAlign: "center" }}>
                      <div style={{ display: "flex", flexDirection: "column", gap: 4, alignItems: "center" }}>
                        <span>{p.name}</span>
                        <button
                          style={{ ...ghostBtn, padding: "1px 6px", fontSize: 13 }}
                          title={allOnCol ? t("admin.roles.col.clear_title") : t("admin.roles.col.fill_title")}
                          onClick={() => {
                            setDraft(prev => {
                              const next = { ...prev };
                              for (const mid of moduleIds) {
                                const cur = new Set(next[String(mid)] || []);
                                if (allOnCol) cur.delete(p.id); else cur.add(p.id);
                                next[String(mid)] = cur;
                              }
                              return next;
                            });
                          }}
                        >{allOnCol ? t("admin.roles.btn.clear") : t("admin.roles.btn.select_all")}</button>
                      </div>
                    </th>
                  );
                })}
                <th style={{ ...thStyle, textAlign: "center" }}>{t("admin.roles.col.row")}</th>
              </tr>
            </thead>
            <tbody>
              {visibleModules.map(m => renderRow(m))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );

  function renderRow(m: typeof visibleModules[number]) {
    const cur = draft[String(m.id)] || new Set<number>();
    const allIds = visiblePerms.map(p => p.id);
    const allOn = allIds.length > 0 && allIds.every(id => cur.has(id));
    const label = m.title || m.name;
    return (
      <tr key={m.id}>
        <td style={tdStyle}>
          <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
            <strong>{label}</strong>
            <span className="muted" style={{ fontSize: 13, fontFamily: "ui-monospace, monospace" }}>{m.name}</span>
            {m.description && (
              <span className="muted" style={{ fontSize: 13 }}>{m.description}</span>
            )}
          </div>
        </td>
        {visiblePerms.map(p => {
          const on = cur.has(p.id);
          return (
            <td key={p.id} style={{ ...tdStyle, textAlign: "center" }}>
              <input
                type="checkbox"
                checked={on}
                onChange={() => toggle(m.id, p.id)}
                aria-label={`${m.name} ${p.name}`}
              />
            </td>
          );
        })}
        <td style={{ ...tdStyle, textAlign: "center" }}>
          <button
            style={ghostBtn}
            onClick={() => toggleRow(m.id, allIds, allOn)}
          >{allOn ? t("admin.roles.btn.clear") : t("admin.roles.btn.select_all")}</button>
        </td>
      </tr>
    );
  }
}

// ── Role create/edit dialog ───────────────────────────────────────────

function RoleDialog({
  mode, role, onClose, onSaved,
}: {
  mode: "create" | "edit";
  role?: AdminRole;
  onClose: () => void;
  onSaved: () => void;
}) {
  const t = useT();
  const [name, setName] = useState(role?.name || "");
  const [description, setDescription] = useState(role?.description || "");
  // Quota UI model: empty string = "unlimited" (sends null to server);
  // a numeric string = the cap (sends as number, validated client-side
  // mirroring the server-side CHECK >= 1).
  const [quotaStr, setQuotaStr] = useState(
    role?.profile_quota != null ? String(role.profile_quota) : "",
  );
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  function parseQuota(): number | null {
    const s = quotaStr.trim();
    if (!s) return null;
    const n = Number(s);
    if (!Number.isInteger(n) || n < 1) {
      throw new Error(t("admin.roles.error.quota_invalid"));
    }
    return n;
  }

  async function submit() {
    setBusy(true); setErr(null);
    try {
      if (!name.trim()) throw new Error(t("admin.roles.error.name_required"));
      const quota = parseQuota();
      if (mode === "create") {
        await adminCreateRole({
          name: name.trim(),
          description: description.trim() || null,
          profile_quota: quota,
        });
      } else if (role) {
        await adminUpdateRole(role.id, {
          name: name.trim(),
          description: description.trim() || null,
          profile_quota: quota,
        });
      }
      toast(t("admin.roles.toast.saved"), "success");
      onSaved();
    } catch (e: any) { setErr(e.message); }
    finally { setBusy(false); }
  }

  return (
    <div style={{
      position: "fixed", inset: 0, background: "var(--overlay)",
      display: "grid", placeItems: "center", zIndex: 1000,
    }} onClick={onClose}>
      <div style={{
        background: "var(--panel)", padding: 20, borderRadius: 10,
        minWidth: 360, border: "1px solid var(--border)",
      }} onClick={e => e.stopPropagation()}>
        <h3 style={{ marginTop: 0 }}>{mode === "create" ? t("admin.roles.dialog.create") : t("admin.roles.dialog.edit", { name: role?.name || "" })}</h3>
        {err && <div className="alert alert-error">{err}</div>}
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <label style={fieldLabel}>
            <span className="muted" style={{ fontSize: 13 }}>{t("admin.roles.field.name")}</span>
            <input value={name} onChange={e => setName(e.target.value)} autoFocus />
          </label>
          <label style={fieldLabel}>
            <span className="muted" style={{ fontSize: 13 }}>{t("admin.roles.field.desc")}</span>
            <input value={description} onChange={e => setDescription(e.target.value)} />
          </label>
          <label style={fieldLabel}>
            <span className="muted" style={{ fontSize: 13 }}>
              {t("admin.roles.field.quota")}
            </span>
            <input
              type="number"
              min={1}
              step={1}
              placeholder={t("admin.roles.field.quota_placeholder")}
              value={quotaStr}
              onChange={e => setQuotaStr(e.target.value)}
            />
          </label>
          <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 4 }}>
            <button className="ghost" onClick={onClose}>{t("common.cancel")}</button>
            <button className="primary" onClick={submit} disabled={busy || !name.trim()}>
              {busy ? t("admin.roles.saving") : t("common.save")}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

const thStyle: React.CSSProperties = {
  textAlign: "left", padding: "10px 14px",
  borderBottom: "1px solid var(--border)",
  fontWeight: 500,
};
const tdStyle: React.CSSProperties = {
  padding: "8px 14px", borderBottom: "1px solid var(--border)",
};
const ghostBtn: React.CSSProperties = {
  padding: "3px 10px", fontSize: 13,
  background: "transparent", color: "inherit",
  border: "1px solid var(--border)",
  borderRadius: 5, cursor: "pointer",
};
const fieldLabel: React.CSSProperties = {
  display: "flex", flexDirection: "column", gap: 4,
};
