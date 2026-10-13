import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  adminListUsers, adminCreateUser, adminUpdateUser,
  adminDeleteUser, adminResetPassword, adminProvisionApiKey,
  adminImpersonateUser, applyOnboardingTemplate,
  adminUserDetail, adminStopProfileProcess, adminStartProfileProcess,
  adminListDepartments, adminListRoles, getMe,
  adminUserRecentActions,
  AdminUser, AdminUserDetail, AdminUserProfile,
  AdminDepartment, AdminRole, AdminUserRecentActions,
  AdminUserInteractionEvent,
} from "@/lib/api";
import { toast } from "@/lib/toast";
import { useT, type T } from "@/lib/i18n";

export function AdminUsersPage() {
  const t = useT();
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [depts, setDepts] = useState<AdminDepartment[]>([]);
  const [roles, setRoles] = useState<AdminRole[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [selectedUserId, setSelectedUserId] = useState<string | number | null>(null);
  const [currentUserId, setCurrentUserId] = useState<string | number | null>(null);
  useEffect(() => { getMe().then(u => setCurrentUserId(u.user_id)).catch(() => {}); }, []);

  // Filters
  const [search, setSearch] = useState("");
  const [filterDept, setFilterDept] = useState<string>("");   // "" = all
  const [filterRole, setFilterRole] = useState<string>("");
  const [onlineOnly, setOnlineOnly] = useState(false);

  async function refresh() {
    setLoading(true); setErr(null);
    try {
      const [u, d, r] = await Promise.all([
        adminListUsers(),
        adminListDepartments().catch(() => ({ departments: [] as AdminDepartment[] })),
        adminListRoles().catch(() => ({ roles: [] as AdminRole[] })),
      ]);
      setUsers(u.users || []);
      setDepts(d.departments || []);
      setRoles(r.roles || []);
    } catch (e: any) { setErr(e.message); }
    finally { setLoading(false); }
  }
  useEffect(() => { refresh(); }, []);

  const filteredUsers = useMemo(() => {
    const q = search.trim().toLowerCase();
    return users.filter(u => {
      if (filterDept) {
        const d = u.department?.id;
        if (filterDept === "none" ? d != null : String(d) !== filterDept) return false;
      }
      if (filterRole) {
        const r = u.role?.id;
        if (filterRole === "none" ? r != null : String(r) !== filterRole) return false;
      }
      if (onlineOnly && !(u.agent_status && u.agent_status.running > 0)) return false;
      if (!q) return true;
      return [u.email, u.realname, u.mobile, u.user_id, u.role?.name, u.department?.name]
        .filter(Boolean)
        .some(v => String(v).toLowerCase().includes(q));
    });
  }, [users, search, filterDept, filterRole, onlineOnly]);

  return (
    <div style={{ padding: 24, overflow: "auto", height: "100%" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
        <h2 style={{ margin: 0 }}>{t("admin.users.title")}</h2>
        <button className="primary" onClick={() => setShowCreate(true)}>{t("admin.users.new")}</button>
      </div>
      <p className="muted" style={{ margin: "4px 0 16px", fontSize: 14 }}>
        {t("admin.users.subtitle")}
      </p>

      {err && <div className="alert alert-error" style={{ marginBottom: 12 }}>{err}</div>}
      {loading && <div className="muted">{t("common.loading")}</div>}

      {showCreate && (
        <CreateUserDialog
          depts={depts} roles={roles}
          onClose={() => setShowCreate(false)}
          onCreated={() => { setShowCreate(false); refresh(); }}
        />
      )}

      {/* Filter bar — one row */}
      {!loading && users.length > 0 && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 12, alignItems: "center" }}>
          <input
            placeholder={t("admin.users.search_placeholder")}
            value={search} onChange={e => setSearch(e.target.value)}
            style={{ flex: "2 1 200px", minWidth: 160, width: "auto" }}
          />
          <select value={filterDept} onChange={e => setFilterDept(e.target.value)}
                  style={{ flex: "1 1 130px", minWidth: 120, width: "auto" }}>
            <option value="">{t("admin.users.filter.all_depts")}</option>
            <option value="none">{t("admin.users.filter.no_dept")}</option>
            {depts.map(d => <option key={d.id} value={String(d.id)}>{d.name}</option>)}
          </select>
          <select value={filterRole} onChange={e => setFilterRole(e.target.value)}
                  style={{ flex: "1 1 130px", minWidth: 120, width: "auto" }}>
            <option value="">{t("admin.users.filter.all_roles")}</option>
            <option value="none">{t("admin.users.filter.no_role")}</option>
            {roles.map(r => <option key={r.id} value={String(r.id)}>{r.name}</option>)}
          </select>
          <label style={{ display: "flex", gap: 6, alignItems: "center", fontSize: 14, cursor: "pointer", userSelect: "none", whiteSpace: "nowrap", flexShrink: 0 }}>
            <input type="checkbox" checked={onlineOnly} onChange={e => setOnlineOnly(e.target.checked)} style={{ width: "auto", flex: "none" }} />
            {t("admin.users.filter.online_only")}
          </label>
          <span className="muted" style={{ fontSize: 14, whiteSpace: "nowrap", flexShrink: 0 }}>
            {filteredUsers.length}/{users.length}
          </span>
        </div>
      )}

      {!loading && users.length > 0 && (
        <div style={{ overflow: "auto", border: "1px solid var(--border)", borderRadius: 8 }}>
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr style={{
                fontSize: 13, textTransform: "uppercase", letterSpacing: 0.5,
                color: "var(--muted)", background: "var(--panel)",
              }}>
                <Th>{t("admin.users.col.id")}</Th><Th>{t("admin.users.col.person")}</Th><Th>{t("admin.users.col.dept_role")}</Th>
                <Th>{t("admin.users.col.agent")}</Th><Th>{t("admin.users.col.profile")}</Th><Th>{t("admin.users.col.status")}</Th>
                <Th style={{ textAlign: "right", whiteSpace: "nowrap" }}>{t("admin.users.col.actions")}</Th>
                <Th style={{ textAlign: "right" }}>›</Th>
              </tr>
            </thead>
            <tbody>
              {filteredUsers.map(u => (
                <tr
                  key={u.user_id}
                  onClick={() => setSelectedUserId(u.user_id)}
                  style={{ cursor: "pointer", transition: "background 120ms" }}
                  onMouseEnter={e => e.currentTarget.style.background = "var(--surface-hover)"}
                  onMouseLeave={e => e.currentTarget.style.background = "transparent"}
                >
                  <Td><code style={{ fontSize: 14, color: "var(--muted)" }}>{u.user_id}</code></Td>
                  <Td>
                    <ActivityCell user={u} />
                  </Td>
                  <Td>
                    <div style={{ fontSize: 14 }}>
                      {u.department?.name || <span className="muted">—</span>}
                    </div>
                    <div style={{ fontSize: 13, marginTop: 2, display: "flex", gap: 4, flexWrap: "wrap" }}>
                      {u.is_admin && <Badge variant="accent">{t("admin.users.badge.admin")}</Badge>}
                      {u.role?.name && <Badge variant="accent-2">{u.role.name}</Badge>}
                      {!u.is_admin && !u.role?.name && <span className="muted">{t("admin.users.badge.unassigned")}</span>}
                    </div>
                  </Td>
                  <Td>
                    <AgentDot status={u.agent_status} />
                  </Td>
                  <Td>
                    <ProfileIdCell user={u} />
                  </Td>
                  <Td>
                    {u.is_active ? <Badge variant="success">{t("admin.users.badge.active")}</Badge> : <Badge variant="muted">{t("admin.users.badge.inactive")}</Badge>}
                  </Td>
                  <Td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                    <RowActions
                      user={u}
                      isSelf={currentUserId != null && String(currentUserId) === String(u.user_id)}
                      onChanged={refresh}
                    />
                  </Td>
                  <Td style={{ textAlign: "right", color: "var(--muted)", fontSize: 18 }}>›</Td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {!loading && !users.length && !err && (
        <div className="muted" style={{ padding: 24, textAlign: "center" }}>
          {t("admin.users.empty.none")}
        </div>
      )}
      {!loading && users.length > 0 && filteredUsers.length === 0 && (
        <div className="muted" style={{ padding: 24, textAlign: "center", fontSize: 14 }}>
          {t("admin.users.empty.filter")}
        </div>
      )}

      {selectedUserId !== null && (
        <UserDetailDrawer
          userId={selectedUserId}
          currentUserId={currentUserId}
          depts={depts}
          roles={roles}
          userRow={users.find(u => String(u.user_id) === String(selectedUserId)) || null}
          onClose={() => setSelectedUserId(null)}
          onUserChanged={() => refresh()}
        />
      )}
    </div>
  );
}

// ── Per-row quick actions: impersonate + delete ──────────────────────
// Stops click-propagation so the buttons don't also open the drawer.
// Self-row disables both — admin can't delete or impersonate themselves.
function RowActions({
  user, isSelf, onChanged,
}: {
  user: AdminUser;
  isSelf: boolean;
  onChanged: () => void;
}) {
  const t = useT();
  const [busy, setBusy] = useState(false);

  async function impersonate(ev: React.MouseEvent) {
    ev.stopPropagation();
    if (isSelf || busy) return;
    if (!confirm(t("admin.users.confirm.impersonate", { email: user.email }))) return;
    setBusy(true);
    try {
      await adminImpersonateUser(user.user_id);
      // Cookie has been swapped server-side — hard reload so every
      // in-memory client (currentUser, models, sessions…) re-bootstraps
      // as the target user.
      window.location.assign("/app/");
    } catch (e: any) {
      toast(e.message, "error");
      setBusy(false);
    }
  }

  async function remove(ev: React.MouseEvent) {
    ev.stopPropagation();
    if (isSelf || busy) return;
    if (!confirm(t("admin.users.confirm.delete", { email: user.email }))) return;
    setBusy(true);
    try {
      await adminDeleteUser(user.user_id);
      toast(t("admin.users.toast.deleted"), "success");
      onChanged();
    } catch (e: any) {
      toast(e.message, "error");
    } finally {
      setBusy(false);
    }
  }

  const selfTitle = isSelf ? t("admin.users.action.row_self_disabled") : "";
  return (
    <div style={{ display: "inline-flex", gap: 6 }}>
      <button
        onClick={impersonate}
        disabled={isSelf || busy}
        title={selfTitle}
        style={{
          padding: "4px 10px", fontSize: 14, borderRadius: 6,
          border: "1px solid var(--border)",
          background: isSelf ? "transparent" : "var(--accent-soft-strong)",
          color: isSelf ? "var(--muted)" : "var(--accent-2)",
          cursor: isSelf || busy ? "not-allowed" : "pointer",
        }}
      >{t("admin.users.action.impersonate")}</button>
      <button
        onClick={remove}
        disabled={isSelf || busy}
        title={selfTitle}
        style={{
          padding: "4px 10px", fontSize: 14, borderRadius: 6,
          border: "1px solid var(--border)",
          background: isSelf ? "transparent" : "var(--danger-soft)",
          color: isSelf ? "var(--muted)" : "var(--danger)",
          cursor: isSelf || busy ? "not-allowed" : "pointer",
        }}
      >{t("admin.users.action.row_delete")}</button>
    </div>
  );
}

// ── Detail drawer ─────────────────────────────────────────────────────
function UserDetailDrawer({
  userId, currentUserId, depts, roles, userRow, onClose, onUserChanged,
}: {
  userId: string | number;
  currentUserId: string | number | null;
  depts: AdminDepartment[];
  roles: AdminRole[];
  userRow: AdminUser | null;
  onClose: () => void;
  onUserChanged: () => void;
}) {
  const t = useT();
  const isSelf = currentUserId != null && String(currentUserId) === String(userId);
  const [data, setData] = useState<AdminUserDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function refresh() {
    setLoading(true); setErr(null);
    try { setData(await adminUserDetail(userId)); }
    catch (e: any) { setErr(e.message); }
    finally { setLoading(false); }
  }
  useEffect(() => { refresh(); }, [userId]);

  async function toggleActive() {
    if (!data) return;
    setBusy(true);
    try {
      await adminUpdateUser(userId, { is_active: !data.user.is_active });
      await refresh();
      onUserChanged();
    } catch (e: any) { toast(e.message, "error"); }
    finally { setBusy(false); }
  }
  async function toggleAdmin() {
    if (!data) return;
    setBusy(true);
    try {
      await adminUpdateUser(userId, { is_admin: !data.user.is_admin });
      await refresh();
      onUserChanged();
    } catch (e: any) { toast(e.message, "error"); }
    finally { setBusy(false); }
  }
  async function resetPwd() {
    const pwd = prompt(t("admin.users.prompt.reset_pwd", { email: data?.user.email || "" }));
    if (!pwd) return;
    try { await adminResetPassword(userId, pwd); toast(t("admin.users.toast.pwd_reset"), "success"); }
    catch (e: any) { toast(e.message, "error"); }
  }
  async function resetApiKey() {
    if (!confirm(t("admin.users.prompt.reset_apikey", { email: data?.user.email || "" }))) return;
    try {
      const r = await adminProvisionApiKey(userId);
      // New key only appears once — show it in a copy-able prompt.
      window.prompt(t("admin.users.prompt.new_apikey"), r.api_key);
      toast(t("admin.users.toast.apikey_reset"), "success");
    } catch (e: any) { toast(e.message, "error"); }
  }
  async function applyTemplate() {
    if (!confirm(t("admin.users.confirm.apply_template"))) return;
    setBusy(true);
    try {
      await applyOnboardingTemplate(userId);
      toast(t("admin.users.toast.template_applied"), "success");
    } catch (e: any) { toast(e.message, "error"); }
    finally { setBusy(false); }
  }
  async function removeUser() {
    if (!data) return;
    if (!confirm(t("admin.users.confirm.delete", { email: data.user.email }))) return;
    try {
      await adminDeleteUser(userId);
      toast(t("admin.users.toast.deleted"), "success");
      onUserChanged();
      onClose();
    } catch (e: any) { toast(e.message, "error"); }
  }
  async function stopProfile(pid: string) {
    if (!confirm(t("admin.users.confirm.stop_profile"))) return;
    try {
      await adminStopProfileProcess(pid);
      toast(t("admin.users.toast.profile_stopped"), "success");
      await refresh();
    } catch (e: any) { toast(e.message, "error"); }
  }
  async function startProfile(pid: string) {
    try {
      await adminStartProfileProcess(pid);
      toast(t("admin.users.toast.profile_started"), "success");
      await refresh();
    } catch (e: any) { toast(e.message, "error"); }
  }

  return (
    <div
      onClick={onClose}
      style={{
        position: "fixed", inset: 0, background: "var(--overlay)",
        display: "flex", justifyContent: "flex-end", zIndex: 1000,
      }}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={{
          width: "min(520px, 92vw)", height: "100%",
          background: "var(--bg)",
          borderLeft: "1px solid var(--border)",
          display: "flex", flexDirection: "column",
          boxShadow: "var(--shadow-modal)",
          overflow: "hidden",
        }}
      >
        {/* Header */}
        <div style={{
          padding: "16px 20px", borderBottom: "1px solid var(--border)",
          display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12,
        }}>
          <div style={{ minWidth: 0 }}>
            <div className="muted" style={{ fontSize: 13, letterSpacing: 0.5 }}>{t("admin.users.detail.heading")}</div>
            <div style={{
              fontSize: 16, fontWeight: 600, marginTop: 2,
              whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
            }}>
              {data?.user.email || "…"}
            </div>
          </div>
          <button
            className="icon-close"
            onClick={onClose}
            style={{
              background: "transparent", color: "var(--muted)",
              border: "1px solid var(--border)", borderRadius: 6,
              cursor: "pointer",
            }}
            aria-label={t("admin.users.detail.close")}
          >×</button>
        </div>

        {/* Body */}
        <div style={{ flex: 1, overflow: "auto", padding: 20 }}>
          {loading ? (
            <div className="muted">{t("common.loading")}</div>
          ) : err ? (
            <div className="alert alert-error">{err}</div>
          ) : data ? (
            <>
              {/* Meta */}
              <div style={{
                display: "grid", gridTemplateColumns: "auto 1fr",
                gap: "10px 16px", fontSize: 14, marginBottom: 16,
              }}>
                <span className="muted">{t("admin.users.col.id")}</span>
                <span><code style={{ fontSize: 14 }}>{data.user.id}</code></span>
                {/* role removed: shown again as a select in OrgEditor below */}
                <span className="muted">{t("admin.users.detail.status")}</span>
                <span>{data.user.is_active ? <Badge variant="success">{t("admin.users.badge.active")}</Badge> : <Badge variant="muted">{t("admin.users.badge.inactive")}</Badge>}</span>
                <span className="muted">{t("admin.users.detail.created")}</span>
                <span style={{ fontSize: 14 }}>
                  {data.user.created_at ? new Date(data.user.created_at).toLocaleString() : "—"}
                </span>
              </div>

              {/* Actions */}
              <div style={{
                display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 24,
              }}>
                <button style={ghostBtn} onClick={toggleActive} disabled={busy}>
                  {data.user.is_active ? t("admin.users.action.deactivate") : t("admin.users.action.activate")}
                </button>
                <button style={ghostBtn} onClick={toggleAdmin} disabled={busy}>
                  {data.user.is_admin ? t("admin.users.action.revoke_admin") : t("admin.users.action.make_admin")}
                </button>
                <button style={ghostBtn} onClick={resetPwd}>{t("admin.users.action.reset_pwd")}</button>
                <button style={ghostBtn} onClick={resetApiKey}>{t("admin.users.action.reset_apikey")}</button>
                <button style={ghostBtn} onClick={applyTemplate} disabled={busy}>{t("admin.users.action.apply_template")}</button>
                <button
                  style={{ ...ghostBtn, color: isSelf ? "var(--muted-2)" : "var(--danger)", borderColor: isSelf ? "var(--border)" : "var(--border)", cursor: isSelf ? "not-allowed" : "pointer", opacity: isSelf ? 0.5 : 1 }}
                  onClick={removeUser}
                  disabled={isSelf}
                  title={isSelf ? t("admin.users.action.delete_self_disabled") : ""}
                >
                  {t("admin.users.action.delete")}
                </button>
              </div>

              {/* Organization / profile editor */}
              <OrgEditor
                userId={userId} userRow={userRow}
                depts={depts} roles={roles}
                onSaved={() => { onUserChanged(); }}
              />

              {/* Interaction Log — charter Law 1: events are the primary view */}
              <ActivitySection userId={userId} />

              {/* Agents */}
              <div style={{
                display: "flex", alignItems: "baseline", gap: 8, marginBottom: 8,
              }}>
                <h3 style={{ margin: 0, fontSize: 14, fontWeight: 600 }}>{t("admin.users.agents.heading")}</h3>
                <span className="muted" style={{ fontSize: 14 }}>
                  {t("admin.users.agents.count", { n: data.profiles.length, running: data.profiles.filter(p => p.process.running).length })}
                </span>
              </div>

              {data.profiles.length === 0 ? (
                <div className="muted" style={{ fontSize: 14, padding: "12px 0" }}>
                  {t("admin.users.agents.empty")}
                </div>
              ) : (
                <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                  {data.profiles.map(p => (
                    <ProfileRow key={p.id} profile={p}
                      onStop={() => stopProfile(p.id)}
                      onStart={() => startProfile(p.id)} />
                  ))}
                </div>
              )}
            </>
          ) : null}
        </div>
      </div>
    </div>
  );
}

// Detail-drawer Interaction Log — shows the event stream plus a muted
// "今日指标" strip (projections are secondary per charter).
function ActivitySection({ userId }: { userId: string | number }) {
  const t = useT();
  const [data, setData] = useState<AdminUserRecentActions | null>(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    adminUserRecentActions(userId, 20)
      .then(r => { if (!cancelled) setData(r); })
      .catch(() => {})
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [userId]);

  return (
    <div style={{ margin: "18px 0" }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginBottom: 8 }}>
        <h3 style={{ margin: 0, fontSize: 14, fontWeight: 600 }}>{t("admin.users.activity.title")}</h3>
        <span className="muted" style={{ fontSize: 14 }}>
          {t("admin.users.activity.subtitle")}
        </span>
      </div>
      {loading && <div className="muted" style={{ fontSize: 14 }}>{t("common.loading")}</div>}
      {!loading && data && data.events.length === 0 && (
        <div className="muted" style={{ fontSize: 14, padding: "12px 0" }}>{t("admin.users.activity.empty")}</div>
      )}
      {!loading && data && data.events.length > 0 && (
        <div style={{
          display: "flex", flexDirection: "column", gap: 6,
          padding: 10, background: "var(--panel)",
          border: "1px solid var(--border)", borderRadius: 7,
        }}>
          {data.events.map((e, i) => <EventRow key={i} ev={e} />)}
        </div>
      )}
      {!loading && data && data.today.total > 0 && (
        <div style={{
          marginTop: 8,
          fontSize: 13, color: "var(--muted)",
          display: "flex", gap: 10, flexWrap: "wrap",
        }}>
          <span>{t("admin.users.activity.today", { n: data.today.total })}</span>
          {Object.entries(data.today.by_kind).map(([k, n]) => (
            <span key={k}>· {k} {n}</span>
          ))}
        </div>
      )}
    </div>
  );
}

function ProfileRow({
  profile, onStop, onStart,
}: { profile: AdminUserProfile; onStop: () => void; onStart: () => void }) {
  const t = useT();
  const running = profile.process.running;
  const { port, pid, idle_seconds } = profile.process;
  return (
    <div style={{
      padding: "10px 12px",
      background: "var(--panel)",
      border: "1px solid var(--border)",
      borderLeft: `3px solid ${running ? "var(--success)" : "var(--border)"}`,
      borderRadius: 7,
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 10, alignItems: "baseline" }}>
        <div style={{ minWidth: 0 }}>
          <div style={{
            fontSize: 14, fontWeight: 600,
            whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
          }}>
            {profile.name || profile.id}
            {profile.is_default && (
              <span className="muted" style={{ fontSize: 13, marginLeft: 6 }}>{t("admin.users.profile.default")}</span>
            )}
          </div>
          {(profile.slug || profile.description) && (
            <div className="muted" style={{
              fontSize: 13, marginTop: 2,
              whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
            }}>
              {profile.slug ? `@${profile.slug}` : ""}
              {profile.slug && profile.description ? " · " : ""}
              {profile.description || ""}
            </div>
          )}
        </div>
        <code style={{ fontSize: 13, color: "var(--muted)", flexShrink: 0 }}>
          {String(profile.id).slice(0, 12)}
        </code>
      </div>
      <div style={{
        display: "flex", alignItems: "center", gap: 8, marginTop: 6,
        fontSize: 13,
      }}>
        {running ? (
          <>
            <span style={{ color: "var(--success)" }}>{t("admin.users.profile.running")}</span>
            <span className="muted">
              {t("admin.users.profile.port_pid", { port: String(port ?? ""), pid: String(pid ?? "") })}
              {typeof idle_seconds === "number" ? t("admin.users.profile.idle_suffix", { sec: Math.round(idle_seconds) }) : ""}
            </span>
            <button
              onClick={onStop}
              style={{
                marginLeft: "auto",
                background: "transparent", color: "var(--danger)",
                border: "1px solid var(--border)", borderRadius: 5,
                padding: "2px 10px", cursor: "pointer", fontSize: 13,
              }}
            >{t("admin.users.profile.stop")}</button>
          </>
        ) : (
          <>
            <span className="muted">{t("admin.users.profile.stopped")}</span>
            <button
              onClick={onStart}
              style={{
                marginLeft: "auto",
                background: "transparent", color: "var(--success)",
                border: "1px solid var(--border)", borderRadius: 5,
                padding: "2px 10px", cursor: "pointer", fontSize: 13,
              }}
            >{t("admin.users.profile.start")}</button>
          </>
        )}
      </div>
    </div>
  );
}

// ── Small UI helpers ──────────────────────────────────────────────────
function Th({ children, style }: { children: React.ReactNode; style?: React.CSSProperties }) {
  return (
    <th style={{
      textAlign: "left", padding: "10px 14px",
      borderBottom: "1px solid var(--border)",
      fontWeight: 500,
      ...style,
    }}>{children}</th>
  );
}
function Td({ children, style }: { children: React.ReactNode; style?: React.CSSProperties }) {
  return (
    <td style={{
      padding: "10px 14px",
      borderBottom: "1px solid var(--border)",
      ...style,
    }}>{children}</td>
  );
}
type BadgeVariant = "accent" | "accent-2" | "success" | "muted" | "danger" | "warning";
const BADGE_VARS: Record<BadgeVariant, { fg: string; bg: string; border: string }> = {
  "accent":   { fg: "var(--accent)",   bg: "var(--accent-soft)",        border: "color-mix(in srgb, var(--accent) 35%, transparent)" },
  "accent-2": { fg: "var(--accent-2)", bg: "var(--accent-2-soft)",      border: "color-mix(in srgb, var(--accent-2) 35%, transparent)" },
  "success":  { fg: "var(--success)",  bg: "var(--success-soft)",       border: "color-mix(in srgb, var(--success) 35%, transparent)" },
  "muted":    { fg: "var(--muted)",    bg: "var(--border-subtle)",      border: "var(--border)" },
  "danger":   { fg: "var(--danger)",   bg: "var(--danger-soft)",        border: "color-mix(in srgb, var(--danger) 35%, transparent)" },
  "warning":  { fg: "var(--warning)",  bg: "var(--warning-soft)",       border: "color-mix(in srgb, var(--warning) 35%, transparent)" },
};
function Badge({ children, variant = "accent" }: { children: React.ReactNode; variant?: BadgeVariant }) {
  const v = BADGE_VARS[variant];
  return (
    <span style={{
      display: "inline-block", padding: "2px 8px",
      borderRadius: 999, fontSize: 13, fontWeight: 500,
      background: v.bg, color: v.fg,
      border: `1px solid ${v.border}`,
    }}>{children}</span>
  );
}
const ghostBtn: React.CSSProperties = {
  padding: "6px 12px", fontSize: 14,
  background: "transparent", color: "inherit",
  border: "1px solid var(--border)",
  borderRadius: 6, cursor: "pointer",
};

// Small running-dot + M/N indicator for a user's agent processes.
function AgentDot({ status }: { status?: AdminUser["agent_status"] }) {
  const t = useT();
  const running = status?.running ?? 0;
  const total = status?.total ?? 0;
  const on = running > 0;
  const color = on ? "var(--success)" : "var(--muted-2)";
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 14 }}>
      <span style={{
        width: 8, height: 8, borderRadius: "50%",
        background: color, flexShrink: 0,
        boxShadow: on ? "0 0 6px var(--success-soft)" : "none",
      }}/>
      <span style={{ color: on ? "inherit" : "var(--muted)" }}>
        {on
          ? t("admin.users.agentdot.running", { running, total: total || running })
          : total
            ? t("admin.users.agentdot.stopped", { total })
            : t("admin.users.agentdot.never")}
      </span>
    </span>
  );
}

// Profile id (Charter v2: profile.id == users.username).
function ProfileIdCell({ user }: { user: AdminUser }) {
  return user.username
    ? <span style={{ fontSize: 14, fontFamily: "ui-monospace, monospace" }}>{user.username}</span>
    : <span className="muted" style={{ fontSize: 14 }}>—</span>;
}

// Primary column: name + last event line + hover-preview of recent Interaction Log.
// Charter Law 1 — the event stream IS the main view; the popover just surfaces
// more of the same event data instead of bolting on derived metrics.
function ActivityCell({ user: u }: { user: AdminUser }) {
  const t = useT();
  const [hover, setHover] = useState(false);
  const [data, setData] = useState<AdminUserRecentActions | null>(null);
  const [loading, setLoading] = useState(false);
  const anchorRef = useRef<HTMLDivElement>(null);
  const hideTimer = useRef<number | undefined>(undefined);
  // Portal placement: computed from the anchor rect so the popover escapes the
  // table's overflow clip box; flips above the row when the viewport bottom is close.
  const [pos, setPos] = useState<{ left: number; top?: number; bottom?: number; maxHeight: number } | null>(null);

  const show = () => { window.clearTimeout(hideTimer.current); setHover(true); };
  // Small grace period so the pointer can travel from the row into the popover.
  const hide = () => { hideTimer.current = window.setTimeout(() => setHover(false), 120); };
  useEffect(() => () => window.clearTimeout(hideTimer.current), []);

  useLayoutEffect(() => {
    if (!hover || !anchorRef.current) { setPos(null); return; }
    const r = anchorRef.current.getBoundingClientRect();
    const below = window.innerHeight - r.bottom - 8;
    const above = r.top - 8;
    const placeBelow = below >= 340 || below >= above;
    setPos({
      left: Math.max(8, Math.min(r.left, window.innerWidth - 428)),
      ...(placeBelow
        ? { top: r.bottom + 4 }
        : { bottom: window.innerHeight - r.top + 4 }),
      maxHeight: Math.max(120, placeBelow ? below : above),
    });
  }, [hover]);

  // Lazy-fetch once on first hover so large lists don't hammer the API.
  useEffect(() => {
    if (!hover || data || loading) return;
    setLoading(true);
    adminUserRecentActions(u.user_id, 10)
      .then(setData)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [hover, data, loading, u.user_id]);

  return (
    <div
      ref={anchorRef}
      onMouseEnter={show}
      onMouseLeave={hide}
    >
      <div style={{ fontWeight: 500, display: "flex", alignItems: "center", gap: 6 }}>
        <LiveDot on={(u.agent_status?.running ?? 0) > 0} />
        <span>{u.realname || <span className="muted">{t("admin.users.cell.unnamed")}</span>}</span>
        <span className="muted" style={{ fontSize: 13, fontWeight: 400 }}>· {u.email}</span>
      </div>
      <div className="muted" style={{ fontSize: 13, marginTop: 3 }}>
        {lastActionLine(t, u)}
      </div>
      {hover && pos && createPortal(
        <div
          onClick={e => e.stopPropagation()}
          onMouseEnter={show}
          onMouseLeave={hide}
          style={{
            position: "fixed",
            left: pos.left,
            top: pos.top,
            bottom: pos.bottom,
            maxHeight: pos.maxHeight,
            overflowY: "auto",
            zIndex: 900,
            minWidth: 340,
            maxWidth: 420,
            background: "var(--popover-bg)",
            border: "1px solid var(--border)",
            borderRadius: 8,
            boxShadow: "var(--shadow-modal)",
            padding: 10,
            cursor: "default",
          }}
        >
          <div style={{
            fontSize: 13, textTransform: "uppercase", letterSpacing: 0.6,
            color: "var(--muted)", marginBottom: 6,
          }}>
            {t("admin.users.cell.popover_title")}
          </div>
          {loading && <div className="muted" style={{ fontSize: 14 }}>{t("common.loading")}</div>}
          {!loading && data && data.events.length === 0 && (
            <div className="muted" style={{ fontSize: 14 }}>{t("admin.users.cell.popover_empty")}</div>
          )}
          {!loading && data && data.events.length > 0 && (
            <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
              {data.events.map((e, i) => (
                <EventRow key={i} ev={e} />
              ))}
            </div>
          )}
          {!loading && data && (
            <div style={{
              marginTop: 8, paddingTop: 6, borderTop: "1px solid var(--border)",
              fontSize: 13, color: "var(--muted)",
              display: "flex", justifyContent: "space-between",
            }}>
              <span>{t("admin.users.cell.today_count", { n: data.today.total })}</span>
              <span style={{ fontStyle: "italic" }}>
                {Object.entries(data.today.by_kind).slice(0, 3).map(([k, n]) =>
                  `${k} ${n}`).join(" · ") || "—"}
              </span>
            </div>
          )}
        </div>,
        document.body
      )}
    </div>
  );
}

function EventRow({ ev }: { ev: AdminUserInteractionEvent }) {
  const t = useT();
  const when = ev.at ? relativeTime(t, ev.at) : "—";
  const op = ev.op === "create" ? t("admin.users.event.op.create")
           : ev.op === "update" ? t("admin.users.event.op.update")
           : ev.op === "delete" ? t("admin.users.event.op.delete")
           : ev.kind || t("admin.users.event.op.fallback");
  const mod = ev.module ? moduleLabel(t, ev.module) : "";
  const agent = ev.source?.startsWith("agent:");
  const fields = Array.isArray(ev.fields) && ev.fields.length > 0
    ? ` [${ev.fields.slice(0, 3).join(", ")}${ev.fields.length > 3 ? "…" : ""}]`
    : "";
  return (
    <div style={{ fontSize: 14, display: "flex", gap: 6, alignItems: "baseline" }}>
      <span style={{ flexShrink: 0, width: 14 }}>{agent ? "🤖" : "👤"}</span>
      <span className="muted" style={{ flexShrink: 0, minWidth: 64, fontSize: 13 }}>{when}</span>
      <span style={{ flex: 1, minWidth: 0 }}>
        {op}{mod ? ` ${mod}` : ""}{ev.target_id ? ` #${ev.target_id}` : ""}
        <span className="muted" style={{ fontSize: 13 }}>{fields}</span>
      </span>
    </div>
  );
}

// Tiny green/grey dot beside the name. Pulsing when an agent process is up.
function LiveDot({ on }: { on: boolean }) {
  const color = on ? "var(--success)" : "var(--muted-2)";
  return (
    <span style={{
      width: 7, height: 7, borderRadius: "50%", flexShrink: 0,
      background: color,
      boxShadow: on ? "0 0 6px var(--success-soft)" : "none",
    }}/>
  );
}

// Human-friendly module label for Interaction Log subjects.
function moduleLabel(t: T, mod: string): string {
  const map: Record<string, DictKeyMod> = {
    accounts: "admin.users.module.accounts",
    contacts: "admin.users.module.contacts",
    opportunities: "admin.users.module.opportunities",
    leads: "admin.users.module.leads",
    activities: "admin.users.module.activities",
    products: "admin.users.module.products",
  };
  const k = map[mod];
  return k ? t(k) : mod;
}
type DictKeyMod =
  | "admin.users.module.accounts"
  | "admin.users.module.contacts"
  | "admin.users.module.opportunities"
  | "admin.users.module.leads"
  | "admin.users.module.activities"
  | "admin.users.module.products";

// Charter Law 1: show the event, not the derived state. Compose the latest
// Interaction Log row into a short "N min ago updated Account·#17" phrase.
function lastActionLine(t: T, u: AdminUser): string {
  const la = u.agent_status?.last_action;
  if (la && la.at) {
    const when = relativeTime(t, la.at);
    const op = la.op === "create" ? t("admin.users.last.op.create")
             : la.op === "update" ? t("admin.users.last.op.update")
             : la.op === "delete" ? t("admin.users.last.op.delete")
             : la.kind === "message" ? t("admin.users.last.op.message")
             : la.kind === "action" ? t("admin.users.last.op.action")
             : t("admin.users.last.op.fallback");
    const mod = la.module ? moduleLabel(t, la.module) : "";
    const who = la.source?.startsWith("agent:") ? "🤖 " : "";
    const target = mod ? ` ${mod}${la.target_id ? `·#${la.target_id}` : ""}` : "";
    return `${who}${when} ${op}${target}`;
  }
  if (u.agent_status?.last_active_at) {
    return t("admin.users.cell.active_suffix", { when: relativeTime(t, u.agent_status.last_active_at) });
  }
  return t("admin.users.cell.no_activity");
}

function relativeTime(t: T, secWall: number): string {
  const diffMs = Date.now() - secWall * 1000;
  if (diffMs < 0) return t("admin.users.rel.now");
  if (diffMs < 60_000) return t("admin.users.rel.now");
  if (diffMs < 3_600_000) return t("admin.users.rel.min", { n: Math.floor(diffMs / 60_000) });
  if (diffMs < 86_400_000) return t("admin.users.rel.hour", { n: Math.floor(diffMs / 3_600_000) });
  return t("admin.users.rel.day", { n: Math.floor(diffMs / 86_400_000) });
}

// ── Organization / profile editor ─────────────────────────────────────
function OrgEditor({
  userId, userRow, depts, roles, onSaved,
}: {
  userId: string | number;
  userRow: AdminUser | null;
  depts: AdminDepartment[];
  roles: AdminRole[];
  onSaved: () => void;
}) {
  const t = useT();
  const [realname, setRealname] = useState(userRow?.realname || "");
  const [mobile, setMobile] = useState(userRow?.mobile || "");
  const [deptId, setDeptId] = useState<string>(userRow?.department?.id ? String(userRow.department.id) : "");
  const [roleId, setRoleId] = useState<string>(userRow?.role?.id ? String(userRow.role.id) : "");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setRealname(userRow?.realname || "");
    setMobile(userRow?.mobile || "");
    setDeptId(userRow?.department?.id ? String(userRow.department.id) : "");
    setRoleId(userRow?.role?.id ? String(userRow.role.id) : "");
  }, [userRow?.user_id]);

  const dirty = useMemo(() => {
    return (
      (realname || "") !== (userRow?.realname || "") ||
      (mobile || "") !== (userRow?.mobile || "") ||
      deptId !== (userRow?.department?.id ? String(userRow.department.id) : "") ||
      roleId !== (userRow?.role?.id ? String(userRow.role.id) : "")
    );
  }, [realname, mobile, deptId, roleId, userRow]);

  async function save() {
    setBusy(true);
    try {
      const patch: any = {};
      if ((realname || "") !== (userRow?.realname || "")) patch.realname = realname || null;
      if ((mobile || "") !== (userRow?.mobile || "")) patch.mobile = mobile || null;
      if (deptId !== (userRow?.department?.id ? String(userRow.department.id) : "")) {
        patch.department_id = deptId ? Number(deptId) : null;
      }
      if (roleId !== (userRow?.role?.id ? String(userRow.role.id) : "")) {
        patch.role_id = roleId ? Number(roleId) : null;
      }
      await adminUpdateUser(userId, patch);
      toast(t("admin.users.toast.saved"), "success");
      onSaved();
    } catch (e: any) { toast(e.message, "error"); }
    finally { setBusy(false); }
  }

  return (
    <div style={{
      marginBottom: 24, padding: 14,
      background: "var(--panel)", border: "1px solid var(--border)", borderRadius: 8,
    }}>
      <h3 style={{ margin: "0 0 10px", fontSize: 14, fontWeight: 600 }}>{t("admin.users.org.title")}</h3>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
        <label style={fieldLabel}>
          <span className="muted" style={{ fontSize: 13 }}>{t("admin.users.org.realname")}</span>
          <input value={realname} onChange={e => setRealname(e.target.value)} placeholder={t("admin.users.org.realname_ph")} />
        </label>
        <label style={fieldLabel}>
          <span className="muted" style={{ fontSize: 13 }}>{t("admin.users.org.mobile")}</span>
          <input value={mobile} onChange={e => setMobile(e.target.value)} placeholder={t("admin.users.org.mobile_ph")} />
        </label>
        <label style={fieldLabel}>
          <span className="muted" style={{ fontSize: 13 }}>{t("admin.users.org.dept")}</span>
          <select value={deptId} onChange={e => setDeptId(e.target.value)}>
            <option value="">{t("admin.users.org.none_dept")}</option>
            {depts.map(d => <option key={d.id} value={String(d.id)}>{d.name}</option>)}
          </select>
        </label>
        <label style={fieldLabel}>
          <span className="muted" style={{ fontSize: 13 }}>{t("admin.users.org.role")}</span>
          <select value={roleId} onChange={e => setRoleId(e.target.value)}>
            <option value="">{t("admin.users.org.none_role")}</option>
            {roles.map(r => <option key={r.id} value={String(r.id)}>{r.name}</option>)}
          </select>
        </label>
      </div>
      <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 12 }}>
        <button className="primary" onClick={save} disabled={!dirty || busy}>
          {busy ? t("admin.users.org.saving") : t("admin.users.org.save")}
        </button>
      </div>
    </div>
  );
}

const fieldLabel: React.CSSProperties = {
  display: "flex", flexDirection: "column", gap: 4,
};

// ── Create dialog ─────────────────────────────────────────────────────
function CreateUserDialog({
  depts, roles, onClose, onCreated,
}: {
  depts: AdminDepartment[];
  roles: AdminRole[];
  onClose: () => void;
  onCreated: () => void;
}) {
  const t = useT();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [realname, setRealname] = useState("");
  const [mobile, setMobile] = useState("");
  const [deptId, setDeptId] = useState<string>("");
  const [roleId, setRoleId] = useState<string>("");
  const [isAdmin, setIsAdmin] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  // Pick a sensible default role — first non-Admin role.
  useEffect(() => {
    if (roleId || !roles.length) return;
    const first = roles.find(r => r.name && r.name.toLowerCase() !== "admin");
    if (first) setRoleId(String(first.id));
  }, [roles, roleId]);

  // Form complete? All required fields filled.
  const canSubmit = !!(email.trim() && password && realname.trim()
    && mobile.trim() && deptId && roleId);

  async function submit() {
    setBusy(true); setErr(null);
    try {
      // Charter v2: 1 user ↔ 1 profile, no quota.
      // auto_start is always true — no UI toggle needed.
      const payload: any = {
        email: email.trim(),
        password,
        is_admin: isAdmin,
        auto_start: true,
        realname: realname.trim(),
        mobile: mobile.trim(),
        department_id: Number(deptId),
        role_id: Number(roleId),
      };
      await adminCreateUser(payload);
      onCreated();
    } catch (e: any) { setErr(e.message); }
    finally { setBusy(false); }
  }

  const fieldLbl: React.CSSProperties = { fontSize: 13, color: "var(--muted)", marginBottom: 4, textTransform: "uppercase", letterSpacing: 0.4 };
  const input: React.CSSProperties = {
    width: "100%", padding: "6px 8px",
    background: "var(--panel-2)", border: "1px solid var(--border)",
    borderRadius: 6, color: "inherit", fontSize: 14, boxSizing: "border-box",
  };
  const sectionHeader: React.CSSProperties = {
    fontSize: 14, fontWeight: 600, color: "var(--fg)",
    margin: "4px 0 8px", borderBottom: "1px solid var(--border)", paddingBottom: 4,
  };

  return (
    <div style={{
      position: "fixed", inset: 0, background: "var(--overlay)",
      display: "grid", placeItems: "center", zIndex: 1000,
    }} onClick={onClose}>
      <div style={{
        background: "var(--panel)", padding: 22, borderRadius: 10,
        width: 460, maxWidth: "calc(100vw - 32px)",
        maxHeight: "calc(100vh - 48px)", overflowY: "auto",
        border: "1px solid var(--border)",
      }} onClick={e => e.stopPropagation()}>
        <h3 style={{ marginTop: 0, marginBottom: 6 }}>{t("admin.users.create.title")}</h3>
        <p className="muted" style={{ fontSize: 14, margin: "0 0 14px" }}>
          {t("admin.users.create.intro")}
        </p>
        {err && <div className="alert alert-error" style={{ marginBottom: 10 }}>{err}</div>}

        <div style={sectionHeader}>{t("admin.users.create.section.basic")}</div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginBottom: 14 }}>
          <div>
            <div style={fieldLbl}>{t("admin.users.create.email")}</div>
            <input style={input} placeholder={t("admin.users.create.email_ph")} value={email} onChange={e => setEmail(e.target.value)} />
          </div>
          <div>
            <div style={fieldLbl}>{t("admin.users.create.pwd")}</div>
            <input style={input} type="password" placeholder={t("admin.users.create.pwd_ph")} value={password} onChange={e => setPassword(e.target.value)} />
          </div>
          <div>
            <div style={fieldLbl}>{t("admin.users.create.realname")} *</div>
            <input style={input} placeholder={t("admin.users.create.realname_ph_required")} value={realname} onChange={e => setRealname(e.target.value)} />
          </div>
          <div>
            <div style={fieldLbl}>{t("admin.users.create.mobile")} *</div>
            <input style={input} placeholder={t("admin.users.create.mobile_ph_required")} value={mobile} onChange={e => setMobile(e.target.value)} />
          </div>
        </div>

        <div style={sectionHeader}>{t("admin.users.create.section.org")}</div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginBottom: 10 }}>
          <div>
            <div style={fieldLbl}>{t("admin.users.org.dept")} *</div>
            <select style={input} value={deptId} onChange={e => setDeptId(e.target.value)}>
              <option value="">{t("admin.users.org.none_dept")}</option>
              {depts.map(d => <option key={d.id} value={String(d.id)}>{d.name}</option>)}
            </select>
          </div>
          <div>
            <div style={fieldLbl}>{t("admin.users.org.role")} *</div>
            <select style={input} value={roleId} onChange={e => setRoleId(e.target.value)}>
              <option value="">{t("admin.users.org.none_role")}</option>
              {roles.map(r => <option key={r.id} value={String(r.id)}>{r.name}</option>)}
            </select>
          </div>
        </div>
        <label style={{ display: "flex", gap: 6, alignItems: "center", fontSize: 14, marginBottom: 14 }}>
          <input type="checkbox" checked={isAdmin} onChange={e => setIsAdmin(e.target.checked)} />
          {t("admin.users.create.is_admin")}
        </label>

        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 8 }}>
          <button className="ghost" onClick={onClose} disabled={busy}>{t("common.cancel")}</button>
          <button className="primary" onClick={submit} disabled={busy || !canSubmit}>
            {busy ? t("admin.users.create.saving") : t("admin.users.create.submit")}
          </button>
        </div>
      </div>
    </div>
  );
}
