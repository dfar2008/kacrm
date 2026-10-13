import { useEffect, useMemo, useState } from "react";
import {
  AdminDepartment,
  adminListDepartments,
  adminCreateDepartment,
  adminUpdateDepartment,
  adminDeleteDepartment,
  adminListUsers,
  type AdminUser,
} from "@/lib/api";
import { toast } from "@/lib/toast";
import { useT } from "@/lib/i18n";

/**
 * Admin → Departments tree.
 *
 * Renders a nested list from the flat `parent_id` array returned by the
 * API. Supports create (at root or as child), inline rename, reparent,
 * and delete (the backend refuses if there are still members or
 * children).
 */
export function AdminDepartmentsPage() {
  const t = useT();
  const [depts, setDepts] = useState<AdminDepartment[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState<{ parentId: number | null } | null>(null);
  const [editing, setEditing] = useState<AdminDepartment | null>(null);
  const [query, setQuery] = useState("");
  // Track collapsed node ids so deep trees stay legible.
  const [collapsed, setCollapsed] = useState<Set<number>>(new Set());
  // 用户列表(经理多选 + 树上显示经理名)。
  const [users, setUsers] = useState<AdminUser[]>([]);

  async function refresh() {
    setLoading(true); setErr(null);
    try { setDepts((await adminListDepartments()).departments || []); }
    catch (e: any) { setErr(e.message); }
    finally { setLoading(false); }
  }
  useEffect(() => { refresh(); }, []);
  useEffect(() => {
    adminListUsers().then(r => setUsers(r.users || [])).catch(() => setUsers([]));
  }, []);

  // id → 显示名(realname > username > email)。
  const userName = useMemo(() => {
    const m = new Map<number, string>();
    for (const u of users) {
      m.set(Number(u.user_id), u.realname || u.username || u.email || `#${u.user_id}`);
    }
    return (id: number) => m.get(id) || `#${id}`;
  }, [users]);
  const managerLabel = (ids: number[]) =>
    (ids || []).map(userName).join("、");

  const tree = useMemo(() => buildTree(depts), [depts]);
  const stats = useMemo(() => {
    const totalMembers = depts.reduce((n, d) => n + (d.member_count || 0), 0);
    const rootCount = depts.filter(d => !d.parent_id).length;
    let maxDepth = 0;
    const walk = (ns: DeptTreeNode[], d: number) => {
      if (d > maxDepth) maxDepth = d;
      ns.forEach(n => walk(n.children, d + 1));
    };
    walk(tree, 1);
    return { totalMembers, rootCount, maxDepth, total: depts.length };
  }, [depts, tree]);

  // Highlight matches; when searching, auto-expand the matched path.
  const matchIds = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return null;
    const ids = new Set<number>();
    const byId = new Map(depts.map(d => [d.id, d] as const));
    for (const d of depts) {
      if (d.name.toLowerCase().includes(q)
          || (d.description || "").toLowerCase().includes(q)) {
        let cur: AdminDepartment | undefined = d;
        while (cur) {
          ids.add(cur.id);
          cur = cur.parent_id ? byId.get(cur.parent_id) : undefined;
        }
      }
    }
    return ids;
  }, [query, depts]);

  function toggleCollapse(id: number) {
    setCollapsed(prev => {
      const n = new Set(prev);
      if (n.has(id)) n.delete(id); else n.add(id);
      return n;
    });
  }
  function expandAll() { setCollapsed(new Set()); }
  function collapseAll() {
    setCollapsed(new Set(depts.filter(d =>
      depts.some(c => c.parent_id === d.id),
    ).map(d => d.id)));
  }

  async function onDelete(d: AdminDepartment) {
    if (!confirm(t("admin.departments.delete_confirm", { name: d.name }))) return;
    try { await adminDeleteDepartment(d.id); toast(t("admin.departments.toast.deleted"), "success"); refresh(); }
    catch (e: any) { toast(e.message, "error"); }
  }

  return (
    <div style={{ padding: 24, overflow: "auto", height: "100%" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
        <h2 style={{ margin: 0 }}>{t("admin.departments.title")}</h2>
        <button className="primary" onClick={() => setShowCreate({ parentId: null })}>{t("admin.departments.new")}</button>
      </div>
      <p className="muted" style={{ margin: "4px 0 16px", fontSize: 14 }}>
        {t("admin.departments.subtitle")}
      </p>

      {err && <div className="alert alert-error" style={{ marginBottom: 12 }}>{err}</div>}
      {loading && <div className="muted">{t("common.loading")}</div>}

      {/* Stats banner — quick glance at org shape. */}
      {!loading && depts.length > 0 && (
        <div style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))",
          gap: 10, marginBottom: 14,
        }}>
          {/* theme-guard:ignore-start */}
          <StatCard label={t("admin.departments.stat.total")}   value={stats.total}       accent="#818cf8" />
          <StatCard label={t("admin.departments.stat.root")}    value={stats.rootCount}   accent="#9C8CFF" />
          <StatCard label={t("admin.departments.stat.depth")}   value={stats.maxDepth}    accent="#5eead4" />
          <StatCard label={t("admin.departments.stat.members")} value={stats.totalMembers} accent="#fbbf24" />
          {/* theme-guard:ignore-end */}
        </div>
      )}

      {!loading && depts.length === 0 && (
        <div style={{
          padding: "40px 24px", textAlign: "center",
          border: "1px dashed var(--border)", borderRadius: 10,
          background: "var(--bg)",
        }}>
          <div style={{ fontSize: 32, marginBottom: 8 }}>🏢</div>
          <div style={{ fontSize: 15, fontWeight: 500, marginBottom: 4 }}>
            {t("admin.departments.empty.title")}
          </div>
          <div className="muted" style={{ fontSize: 14, marginBottom: 16 }}>
            {t("admin.departments.empty.desc")}
          </div>
          <button className="primary" onClick={() => setShowCreate({ parentId: null })}>
            {t("admin.departments.empty.cta")}
          </button>
        </div>
      )}

      {/* Tool bar — search + expand / collapse all. */}
      {!loading && tree.length > 0 && (
        <div style={{
          display: "flex", gap: 8, alignItems: "center",
          marginBottom: 10,
        }}>
          <input
            type="search"
            placeholder={t("admin.departments.search_placeholder")}
            value={query}
            onChange={e => setQuery(e.target.value)}
            style={{
              flex: 1, maxWidth: 320,
              padding: "7px 11px", fontSize: 14,
              background: "var(--bg)", border: "1px solid var(--border)",
              borderRadius: 7, color: "inherit",
            }}
          />
          <button style={ghostBtn} onClick={expandAll}>{t("admin.departments.expand_all")}</button>
          <button style={ghostBtn} onClick={collapseAll}>{t("admin.departments.collapse_all")}</button>
        </div>
      )}

      {!loading && tree.length > 0 && (
        <div style={{
          border: "1px solid var(--border)", borderRadius: 8,
          background: "var(--bg)", padding: 8,
        }}>
          {tree.map(node => (
            <DeptNode
              key={node.dept.id}
              node={node}
              depth={0}
              collapsed={collapsed}
              matchIds={matchIds}
              onToggleCollapse={toggleCollapse}
              onAddChild={(pid) => setShowCreate({ parentId: pid })}
              onEdit={setEditing}
              onDelete={onDelete}
              managerLabel={managerLabel}
            />
          ))}
        </div>
      )}

      {showCreate && (
        <DeptDialog
          mode="create"
          parentId={showCreate.parentId}
          depts={depts}
          users={users}
          onClose={() => setShowCreate(null)}
          onSaved={() => { setShowCreate(null); refresh(); }}
        />
      )}
      {editing && (
        <DeptDialog
          mode="edit"
          dept={editing}
          depts={depts}
          users={users}
          onClose={() => setEditing(null)}
          onSaved={() => { setEditing(null); refresh(); }}
        />
      )}
    </div>
  );
}

// ── Tree plumbing ─────────────────────────────────────────────────────

interface DeptTreeNode {
  dept: AdminDepartment;
  children: DeptTreeNode[];
}
function buildTree(list: AdminDepartment[]): DeptTreeNode[] {
  const byId = new Map<number, DeptTreeNode>();
  list.forEach(d => byId.set(d.id, { dept: d, children: [] }));
  const roots: DeptTreeNode[] = [];
  list.forEach(d => {
    const node = byId.get(d.id)!;
    if (d.parent_id && byId.has(d.parent_id)) {
      byId.get(d.parent_id)!.children.push(node);
    } else {
      roots.push(node);
    }
  });
  const sort = (ns: DeptTreeNode[]) => {
    ns.sort((a, b) => a.dept.sort_order - b.dept.sort_order || a.dept.name.localeCompare(b.dept.name));
    ns.forEach(n => sort(n.children));
  };
  sort(roots);
  return roots;
}

function DeptNode({
  node, depth, collapsed, matchIds,
  onToggleCollapse, onAddChild, onEdit, onDelete, managerLabel,
}: {
  node: DeptTreeNode;
  depth: number;
  collapsed: Set<number>;
  matchIds: Set<number> | null;
  onToggleCollapse: (id: number) => void;
  onAddChild: (parentId: number) => void;
  onEdit: (d: AdminDepartment) => void;
  onDelete: (d: AdminDepartment) => void;
  managerLabel: (ids: number[]) => string;
}) {
  const t = useT();
  const { dept, children } = node;
  const isCollapsed = collapsed.has(dept.id);
  const hasChildren = children.length > 0;
  const highlight = matchIds != null && matchIds.has(dept.id);
  const dimmed = matchIds != null && !matchIds.has(dept.id);
  return (
    <div style={{ opacity: dimmed ? 0.35 : 1 }}>
      <div style={{
        display: "flex", alignItems: "center", gap: 8,
        padding: "8px 10px",
        paddingLeft: 10 + depth * 20,
        borderRadius: 6,
        background: highlight ? "var(--accent-soft)" : "transparent",
        transition: "background 120ms",
      }}
        onMouseEnter={e => { if (!highlight) e.currentTarget.style.background = "var(--panel)"; }}
        onMouseLeave={e => { e.currentTarget.style.background = highlight ? "var(--accent-soft)" : "transparent"; }}
      >
        <button
          onClick={() => hasChildren && onToggleCollapse(dept.id)}
          disabled={!hasChildren}
          style={{
            width: 18, height: 18,
            display: "grid", placeItems: "center",
            background: "transparent", border: "none",
            color: "var(--muted)", fontSize: 14,
            cursor: hasChildren ? "pointer" : "default",
            padding: 0, flexShrink: 0,
          }}
          title={hasChildren ? (isCollapsed ? t("admin.departments.tip.expand") : t("admin.departments.tip.collapse")) : undefined}
        >
          {hasChildren ? (isCollapsed ? "▸" : "▾") : "·"}
        </button>
        <span style={{ fontWeight: 500 }}>{dept.name}</span>
        <span className="muted" style={{ fontSize: 13 }}>
          {t("admin.departments.member_count", { n: dept.member_count })}
          {hasChildren ? ` · ${t("admin.departments.child_count", { n: children.length })}` : ""}
        </span>
        {dept.description && (
          <span className="muted" style={{ fontSize: 13, fontStyle: "italic" }}>
            — {dept.description}
          </span>
        )}
        {dept.manager_ids?.length > 0 && (
          <span style={{ fontSize: 13, color: "var(--accent)" }}>
            · {t("admin.departments.managers_prefix")}{managerLabel(dept.manager_ids)}
          </span>
        )}
        <div style={{ marginLeft: "auto", display: "flex", gap: 4 }}>
          <button style={ghostBtn} onClick={() => onAddChild(dept.id)}>{t("admin.departments.add_child")}</button>
          <button style={ghostBtn} onClick={() => onEdit(dept)}>{t("common.edit")}</button>
          <button
            style={{ ...ghostBtn, color: "var(--danger)", borderColor: "color-mix(in srgb, var(--danger) 25%, transparent)" }}
            onClick={() => onDelete(dept)}
          >{t("common.delete")}</button>
        </div>
      </div>
      {!isCollapsed && children.map(c => (
        <DeptNode key={c.dept.id} node={c} depth={depth + 1}
                  collapsed={collapsed} matchIds={matchIds}
                  onToggleCollapse={onToggleCollapse}
                  onAddChild={onAddChild} onEdit={onEdit} onDelete={onDelete}
                  managerLabel={managerLabel} />
      ))}
    </div>
  );
}

function StatCard({ label, value, accent }: {
  label: string; value: number; accent: string;
}) {
  return (
    <div style={{
      padding: "12px 14px",
      background: "var(--bg)",
      border: "1px solid var(--border)",
      borderLeft: `3px solid ${accent}`,
      borderRadius: 8,
    }}>
      <div style={{ fontSize: 22, fontWeight: 600, color: accent }}>
        {value}
      </div>
      <div className="muted" style={{ fontSize: 13, marginTop: 2 }}>
        {label}
      </div>
    </div>
  );
}

// ── Create/Edit dialog ────────────────────────────────────────────────

function DeptDialog({
  mode, dept, parentId, depts, users, onClose, onSaved,
}: {
  mode: "create" | "edit";
  dept?: AdminDepartment;
  parentId?: number | null;
  depts: AdminDepartment[];
  users: AdminUser[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const t = useT();
  const [name, setName] = useState(dept?.name || "");
  const [managerIds, setManagerIds] = useState<number[]>(dept?.manager_ids || []);
  const [mgrSearch, setMgrSearch] = useState("");
  const [parent, setParent] = useState<string>(
    dept ? (dept.parent_id ? String(dept.parent_id) : "") : (parentId ? String(parentId) : "")
  );
  const [description, setDescription] = useState(dept?.description || "");
  const [sortOrder, setSortOrder] = useState<string>(String(dept?.sort_order ?? 0));
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // When editing, exclude self + descendants from parent options.
  const parentOptions = useMemo(() => {
    if (!dept) return depts;
    const banned = new Set<number>([dept.id]);
    let grew = true;
    while (grew) {
      grew = false;
      depts.forEach(d => {
        if (d.parent_id && banned.has(d.parent_id) && !banned.has(d.id)) {
          banned.add(d.id); grew = true;
        }
      });
    }
    return depts.filter(d => !banned.has(d.id));
  }, [depts, dept]);

  async function submit() {
    setBusy(true); setErr(null);
    try {
      const patch = {
        name: name.trim(),
        parent_id: parent ? Number(parent) : null,
        description: description.trim() || null,
        sort_order: Number(sortOrder) || 0,
        manager_ids: managerIds,
      };
      if (!patch.name) throw new Error(t("admin.departments.error.name_required"));
      if (mode === "create") {
        await adminCreateDepartment(patch);
      } else if (dept) {
        await adminUpdateDepartment(dept.id, patch);
      }
      toast(t("admin.departments.toast.saved"), "success");
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
        minWidth: 380, border: "1px solid var(--border)",
      }} onClick={e => e.stopPropagation()}>
        <h3 style={{ marginTop: 0 }}>
          {mode === "create"
            ? t("admin.departments.dialog.create")
            : t("admin.departments.dialog.edit", { name: dept?.name || "" })}
        </h3>
        {err && <div className="alert alert-error">{err}</div>}
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <label style={fieldLabel}>
            <span className="muted" style={{ fontSize: 13 }}>{t("admin.departments.field.name")}</span>
            <input value={name} onChange={e => setName(e.target.value)} autoFocus />
          </label>
          <label style={fieldLabel}>
            <span className="muted" style={{ fontSize: 13 }}>{t("admin.departments.field.parent")}</span>
            <select value={parent} onChange={e => setParent(e.target.value)}>
              <option value="">{t("admin.departments.root_option")}</option>
              {parentOptions.map(d => (
                <option key={d.id} value={String(d.id)}>{d.name}</option>
              ))}
            </select>
          </label>
          <label style={fieldLabel}>
            <span className="muted" style={{ fontSize: 13 }}>{t("admin.departments.field.desc")}</span>
            <input value={description} onChange={e => setDescription(e.target.value)} />
          </label>
          {/* 经理多选:可搜索的复选框列表。外层用 div,每行复选框各自一个 label
              (合法稳定)。勾选 onChange 用 e.target.checked,即勾即写 managerIds。
              (曾试过 chips+combobox,但有保存时序问题,已回退到这个稳定版本。) */}
          <div style={fieldLabel}>
            <span className="muted" style={{ fontSize: 13 }}>{t("admin.departments.field.managers")}</span>
            <input
              placeholder={t("admin.departments.managers_search")}
              value={mgrSearch}
              onChange={e => setMgrSearch(e.target.value)}
              style={{ marginBottom: 4 }}
            />
            <div style={{
              maxHeight: 200, overflowY: "auto", border: "1px solid var(--border)",
              borderRadius: 6, padding: 4,
            }}>
              {users.length === 0 && (
                <div className="muted" style={{ fontSize: 13, padding: 6 }}>
                  {t("admin.departments.managers_empty")}
                </div>
              )}
              {users
                .filter(u => {
                  const q = mgrSearch.trim().toLowerCase();
                  if (!q) return true;
                  return (u.realname || "").toLowerCase().includes(q)
                    || (u.username || "").toLowerCase().includes(q)
                    || (u.email || "").toLowerCase().includes(q);
                })
                .map(u => {
                  const uid = Number(u.user_id);
                  const checked = managerIds.includes(uid);
                  return (
                    <label key={uid} style={{
                      display: "flex", alignItems: "center", gap: 8,
                      padding: "4px 6px", cursor: "pointer", fontSize: 14,
                    }}>
                      <input type="checkbox" checked={checked}
                        onChange={e => setManagerIds(prev =>
                          e.target.checked ? [...prev, uid] : prev.filter(i => i !== uid))} />
                      <span>{u.realname || u.username || u.email}</span>
                      {u.realname && u.username && (
                        <span className="muted" style={{ fontSize: 12 }}>@{u.username}</span>
                      )}
                    </label>
                  );
                })}
            </div>
            {managerIds.length > 0 && (
              <span className="muted" style={{ fontSize: 12 }}>
                {t("admin.departments.managers_selected", { n: managerIds.length })}
              </span>
            )}
          </div>
          <label style={fieldLabel}>
            <span className="muted" style={{ fontSize: 13 }}>{t("admin.departments.field.sort")}</span>
            <input type="number" value={sortOrder} onChange={e => setSortOrder(e.target.value)} />
          </label>
          <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 4 }}>
            <button className="ghost" onClick={onClose}>{t("common.cancel")}</button>
            <button className="primary" onClick={submit} disabled={busy || !name.trim()}>
              {busy ? t("admin.departments.saving") : t("common.save")}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

const ghostBtn: React.CSSProperties = {
  padding: "4px 10px", fontSize: 14,
  background: "transparent", color: "inherit",
  border: "1px solid var(--border)",
  borderRadius: 6, cursor: "pointer",
};
const fieldLabel: React.CSSProperties = {
  display: "flex", flexDirection: "column", gap: 4,
};
