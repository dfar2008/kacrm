import { useEffect, useState } from "react";
import {
  getMe, putMe, changeMyPassword, type CurrentUser,
  getMyPermissions, type MyPermissions,
  listAgentProfiles, startMyProfile, stopMyProfile,
  type AgentProfile,
} from "@/lib/api";
import { useT, type DictKey } from "@/lib/i18n";

interface Props {
  open: boolean;
  onClose: () => void;
  /** Optional pre-fetched user (skip network). */
  user?: CurrentUser | null;
}

type Tab = "basic" | "password" | "perms" | "agent";

type KindLabelKey =
  | "profile.perms.kind.crm" | "profile.perms.kind.admin";

const PERMS_KIND_LABEL_KEY: Record<string, KindLabelKey> = {
  crm: "profile.perms.kind.crm",
  admin: "profile.perms.kind.admin",
};

/**
 * 个人信息 modal with three tabs: 基本信息 / 我的权限 / 我的 Agent。
 * 邮件账号已经从这里下线 — 由 channel 设置(Agent 详情 → Channels →
 * 邮件 Email)统一配置。
 */
export function UserProfileModal({ open, onClose, user: provided }: Props) {
  const t = useT();
  // 模块显示名:优先 crm.module.<name> 本地化 key,缺失再回退后端 title/name。
  // 否则标准模块会直接显示后端 collections.title(中英混杂,见 #i18n)。
  const moduleDisplay = (m: { name: string; title?: string | null }) => {
    const k = `crm.module.${m.name}` as DictKey;
    const tr = t(k);
    return tr === k ? (m.title || m.name) : tr;
  };
  const [user, setUser] = useState<CurrentUser | null>(provided ?? null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [tab, setTab] = useState<Tab>("basic");

  // ── Profile edit state (basic tab) ───────────────────────────
  const [editing, setEditing] = useState(false);
  const [profileBusy, setProfileBusy] = useState(false);
  const [profileDraft, setProfileDraft] = useState({ realname: "", mobile: "", description: "" });
  const [profileMsg, setProfileMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);

  // ── Password change state (basic tab) ────────────────────────
  const [pwd, setPwd] = useState({ current: "", next: "", confirm: "" });
  const [pwdBusy, setPwdBusy] = useState(false);
  const [pwdMsg, setPwdMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);

  // ── Permissions tab ──────────────────────────────────────────
  const [perms, setPerms] = useState<MyPermissions | null>(null);
  const [permsLoading, setPermsLoading] = useState(false);
  const [permsErr, setPermsErr] = useState<string | null>(null);

  // ── Agent tab ────────────────────────────────────────────────
  const [agentProfiles, setAgentProfiles] = useState<AgentProfile[] | null>(null);
  const [agentLoading, setAgentLoading] = useState(false);
  const [agentErr, setAgentErr] = useState<string | null>(null);
  const [agentBusyId, setAgentBusyId] = useState<string | null>(null);
  async function refreshAgentProfiles() {
    setAgentLoading(true); setAgentErr(null);
    try {
      const r = await listAgentProfiles();
      setAgentProfiles(r.profiles || []);
    } catch (e: any) { setAgentErr(e.message); }
    finally { setAgentLoading(false); }
  }
  const [permsKind, setPermsKind] = useState<string>("crm");

  useEffect(() => {
    if (!open) return;
    // Always refetch — the passed-in `provided` user (from AppShell) only
    // has the minimal legacy shape; the modal needs department/role joined.
    setLoading(true);
    setErr(null);
    getMe()
      .then(u => {
        setUser(u);
        setProfileDraft({
          realname: u.realname || "",
          mobile: u.mobile || "",
          description: u.description || "",
        });
      })
      .catch(e => setErr(e.message || t("profile.err_load")))
      .finally(() => setLoading(false));
  }, [open, provided, t]);

  // Lazy-load permissions when the tab is opened the first time.
  useEffect(() => {
    if (!open || tab !== "perms" || perms || permsLoading) return;
    setPermsLoading(true);
    setPermsErr(null);
    getMyPermissions()
      .then(setPerms)
      .catch(e => setPermsErr(e instanceof Error ? e.message : String(e)))
      .finally(() => setPermsLoading(false));
  }, [open, tab, perms, permsLoading]);

  // Lazy-load agent profiles when the agent tab is opened.
  useEffect(() => {
    if (!open || tab !== "agent" || agentProfiles || agentLoading) return;
    refreshAgentProfiles();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, tab]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  const role = user?.is_admin ? t("profile.role_admin") : t("profile.role_user");
  const userId = user?.user_id ?? t("profile.unknown_user");
  const email = user?.email || t("profile.no_email");
  const displayName = (user?.realname || user?.username || user?.email || "").trim() || t("profile.unnamed");
  const initial = displayName.charAt(0).toUpperCase();

  async function saveProfile() {
    setProfileBusy(true);
    setProfileMsg(null);
    try {
      const patch = {
        realname: profileDraft.realname.trim() || null,
        mobile: profileDraft.mobile.trim() || null,
        description: profileDraft.description.trim() || null,
      };
      const u = await putMe(patch);
      setUser(u);
      setProfileDraft({
        realname: u.realname || "",
        mobile: u.mobile || "",
        description: u.description || "",
      });
      setEditing(false);
      setProfileMsg({ kind: "ok", text: t("profile.saved") });
    } catch (e) {
      setProfileMsg({ kind: "err", text: e instanceof Error ? e.message : String(e) });
    } finally {
      setProfileBusy(false);
    }
  }

  function cancelEdit() {
    setEditing(false);
    setProfileMsg(null);
    setProfileDraft({
      realname: user?.realname || "",
      mobile: user?.mobile || "",
      description: user?.description || "",
    });
  }

  async function submitPassword() {
    setPwdMsg(null);
    if (pwd.next.length < 8) {
      setPwdMsg({ kind: "err", text: t("profile.password.too_short") });
      return;
    }
    if (pwd.next !== pwd.confirm) {
      setPwdMsg({ kind: "err", text: t("profile.password.mismatch") });
      return;
    }
    setPwdBusy(true);
    try {
      await changeMyPassword(pwd.current, pwd.next);
      setPwd({ current: "", next: "", confirm: "" });
      setPwdMsg({ kind: "ok", text: t("profile.password.success") });
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      const text =
        /invalid_current_password/.test(msg) ? t("profile.password.wrong_current")
        : /password_too_short/.test(msg) ? t("profile.password.too_short")
        : msg;
      setPwdMsg({ kind: "err", text });
    } finally {
      setPwdBusy(false);
    }
  }

  const lbl: React.CSSProperties = { fontSize: 14, color: "var(--muted)", marginBottom: 4 };
  const inputStyle: React.CSSProperties = {
    width: "100%", padding: "6px 8px",
    background: "var(--panel-2)", border: "1px solid var(--border)",
    borderRadius: 6, color: "inherit", fontSize: 14,
  };
  const sectionHeader: React.CSSProperties = {
    fontSize: 14, fontWeight: 600, marginBottom: 10,
    color: "var(--fg)",
  };

  const TAB_LABEL_KEY: Record<Tab, DictKey> = {
    basic: "profile.tab.basic",
    password: "profile.tab.password",
    perms: "profile.tab.my_perms",
    agent: "profile.tab.my_agent",
  };

  return (
    <div
      onClick={onClose}
      style={{
        position: "fixed", inset: 0,
        background: "var(--overlay)",
        display: "flex", alignItems: "center", justifyContent: "center",
        zIndex: 10001,
      }}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={{
          width: 560, maxWidth: "calc(100vw - 32px)",
          maxHeight: "calc(100vh - 48px)", overflowY: "auto",
          background: "var(--panel)",
          border: "1px solid var(--border)",
          borderRadius: 12,
          boxShadow: "var(--shadow-modal)",
          padding: "20px 24px",
          position: "relative",
          color: "inherit",
        }}
      >
        <button
          className="icon-close"
          onClick={onClose}
          aria-label={t("profile.close_aria")}
          style={{
            position: "absolute", top: 10, right: 12,
            background: "none", border: "none",
            color: "var(--muted)", fontSize: 22,
            cursor: "pointer",
          }}
        >×</button>

        <h3 style={{ margin: "0 0 14px" }}>{t("profile.title")}</h3>

        {/* ─── Tabs ─────────────────────────────────────────── */}
        <div style={{
          display: "flex", gap: 4,
          borderBottom: "1px solid var(--border)",
          marginBottom: 18,
        }}>
          {(["basic", "password", "perms", "agent"] as Tab[]).map(id => {
            const active = tab === id;
            return (
              <button
                key={id}
                onClick={() => setTab(id)}
                style={{
                  background: "none", border: "none",
                  padding: "8px 14px", fontSize: 14,
                  color: active ? "var(--fg)" : "var(--muted)",
                  borderBottom: active
                    ? "2px solid var(--accent)"
                    : "2px solid transparent",
                  marginBottom: -1,
                  cursor: "pointer",
                  fontWeight: active ? 600 : 400,
                }}
              >
                {t(TAB_LABEL_KEY[id])}
              </button>
            );
          })}
        </div>

        {loading && <div className="muted">{t("common.loading")}</div>}
        {err && <div className="alert alert-error">{err}</div>}

        {/* ─── Tab: 基本信息 ───────────────────────────────── */}
        {tab === "basic" && user && (
          <div>
            {/* Profile header: avatar + name + badges */}
            <div style={{ display: "flex", alignItems: "center", gap: 14, marginBottom: 18 }}>
              {/* theme-guard:ignore-start  (装饰性头像渐变,恒定色) */}
              <div style={{
                width: 56, height: 56, borderRadius: "50%",
                background: "linear-gradient(135deg, #60a5fa 0%, #8b5cf6 100%)",
                display: "flex", alignItems: "center", justifyContent: "center",
                fontSize: 22, fontWeight: 600, color: "#fff", flexShrink: 0,
              }}>{initial}</div>
              {/* theme-guard:ignore-end */}
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 17, fontWeight: 600, lineHeight: 1.2 }}>
                  {displayName}
                </div>
                <div style={{ fontSize: 14, color: "var(--muted)", marginTop: 4, display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
                  {user.role?.name && (
                    <span style={{
                      padding: "2px 8px", borderRadius: 10,
                      background: "var(--accent-2-soft)", color: "var(--accent-2)",
                      fontSize: 13, fontWeight: 500,
                    }}>{user.role.name}</span>
                  )}
                  {user.is_admin && (
                    <span style={{
                      padding: "2px 8px", borderRadius: 10,
                      background: "var(--danger-soft)", color: "var(--danger)",
                      fontSize: 13, fontWeight: 500,
                    }}>{t("profile.role_admin")}</span>
                  )}
                  {!user.role?.name && !user.is_admin && <span>{role}</span>}
                  {user.department?.name && (
                    <span style={{ color: "var(--muted)" }}>· {user.department.name}</span>
                  )}
                  {user.plan && user.plan !== "free" && (
                    <span style={{ color: "var(--muted)" }}>· {user.plan}</span>
                  )}
                </div>
              </div>
              {!editing && (
                <button onClick={() => setEditing(true)}
                        style={{ padding: "6px 14px", fontSize: 14, background: "none", border: "1px solid var(--border)", borderRadius: 6, color: "inherit", cursor: "pointer" }}>
                  {t("profile.edit")}
                </button>
              )}
            </div>

            {/* Info grid — view mode */}
            {!editing && (
              <div style={{ display: "grid", gridTemplateColumns: "90px 1fr", rowGap: 10, columnGap: 12, fontSize: 14 }}>
                <span className="muted">{t("profile.field.userid")}</span>
                <span style={{ fontFamily: "ui-monospace, monospace" }}>{userId}</span>
                <span className="muted">{t("profile.field.username")}</span>
                <span style={{ fontFamily: "ui-monospace, monospace" }}>{user.username || t("profile.dash")}</span>
                <span className="muted">{t("profile.field.email")}</span>
                <span>{email}</span>
                <span className="muted">{t("profile.field.mobile")}</span>
                <span>{user.mobile || <span className="muted">{t("profile.empty.mobile")}</span>}</span>
                <span className="muted">{t("profile.field.dept")}</span>
                <span>{user.department?.name || <span className="muted">{t("profile.empty.dept")}</span>}</span>
                <span className="muted">{t("profile.field.report_to")}</span>
                <span>{user.report_to?.length
                  ? user.report_to.map(r => r.name || `#${r.id}`).join("、")
                  : <span className="muted">{t("profile.empty.report_to")}</span>}</span>
                {user.description && (<>
                  <span className="muted">{t("profile.field.description")}</span>
                  <span style={{ whiteSpace: "pre-wrap", lineHeight: 1.5 }}>{user.description}</span>
                </>)}
                <span className="muted">{t("profile.field.registered")}</span>
                <span>{user.created_at ? new Date(user.created_at).toLocaleDateString() : t("profile.dash")}</span>
              </div>
            )}

            {/* Edit mode */}
            {editing && (
              <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                <div>
                  <div style={{ fontSize: 14, color: "var(--muted)", marginBottom: 4 }}>{t("profile.edit.realname_label")}</div>
                  <input style={{ width: "100%", padding: "6px 8px", background: "var(--panel-2)", border: "1px solid var(--border)", borderRadius: 6, color: "inherit", fontSize: 14 }}
                         value={profileDraft.realname} maxLength={100}
                         onChange={e => setProfileDraft(d => ({ ...d, realname: e.target.value }))}
                         placeholder={t("profile.edit.realname_ph")}/>
                </div>
                <div>
                  <div style={{ fontSize: 14, color: "var(--muted)", marginBottom: 4 }}>{t("profile.edit.mobile_label")}</div>
                  <input style={{ width: "100%", padding: "6px 8px", background: "var(--panel-2)", border: "1px solid var(--border)", borderRadius: 6, color: "inherit", fontSize: 14 }}
                         value={profileDraft.mobile} maxLength={100}
                         onChange={e => setProfileDraft(d => ({ ...d, mobile: e.target.value }))}
                         placeholder={t("profile.edit.mobile_ph")}/>
                </div>
                <div>
                  <div style={{ fontSize: 14, color: "var(--muted)", marginBottom: 4 }}>{t("profile.edit.desc_label")}</div>
                  <textarea style={{ width: "100%", padding: "6px 8px", background: "var(--panel-2)", border: "1px solid var(--border)", borderRadius: 6, color: "inherit", fontSize: 14, resize: "vertical", minHeight: 60, fontFamily: "inherit" }}
                         value={profileDraft.description}
                         onChange={e => setProfileDraft(d => ({ ...d, description: e.target.value }))}
                         placeholder={t("profile.edit.desc_ph")}/>
                </div>
                <div className="muted" style={{ fontSize: 13 }}>
                  {t("profile.edit.admin_hint")}
                </div>
                <div style={{ display: "flex", gap: 8 }}>
                  <button onClick={saveProfile} disabled={profileBusy}
                          style={{ padding: "6px 16px", fontSize: 14 }}>
                    {profileBusy ? t("profile.edit.saving") : t("profile.edit.save")}
                  </button>
                  <button onClick={cancelEdit} disabled={profileBusy}
                          style={{ padding: "6px 16px", fontSize: 14, background: "none", border: "1px solid var(--border)", borderRadius: 6, color: "inherit", cursor: "pointer" }}>
                    {t("profile.edit.cancel")}
                  </button>
                </div>
              </div>
            )}

            {profileMsg && (
              <div style={{ marginTop: 12, fontSize: 14,
                            color: profileMsg.kind === "ok" ? "var(--success)" : "var(--danger)" }}>
                {profileMsg.text}
              </div>
            )}
          </div>
        )}

        {/* ─── Tab: 修改密码 ───────────────────────────────── */}
        {tab === "password" && (
          <div>
            <div style={sectionHeader}>{t("profile.password.title")}</div>
            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              <div>
                <div style={lbl}>{t("profile.password.current")}</div>
                <input type="password" autoComplete="current-password" style={inputStyle}
                       value={pwd.current}
                       onChange={e => setPwd(d => ({ ...d, current: e.target.value }))}/>
              </div>
              <div>
                <div style={lbl}>{t("profile.password.new")}</div>
                <input type="password" autoComplete="new-password" style={inputStyle}
                       value={pwd.next}
                       onChange={e => setPwd(d => ({ ...d, next: e.target.value }))}/>
              </div>
              <div>
                <div style={lbl}>{t("profile.password.confirm")}</div>
                <input type="password" autoComplete="new-password" style={inputStyle}
                       value={pwd.confirm}
                       onChange={e => setPwd(d => ({ ...d, confirm: e.target.value }))}/>
              </div>
              <div>
                <button onClick={submitPassword}
                        disabled={pwdBusy || !pwd.current || !pwd.next || !pwd.confirm}
                        style={{ padding: "6px 16px", fontSize: 14 }}>
                  {pwdBusy ? t("profile.password.submitting") : t("profile.password.submit")}
                </button>
              </div>
              {pwdMsg && (
                <div style={{ fontSize: 14,
                              color: pwdMsg.kind === "ok" ? "var(--success)" : "var(--danger)" }}>
                  {pwdMsg.text}
                </div>
              )}
            </div>
          </div>
        )}

        {/* ─── Tab: 我的权限 ───────────────────────────────── */}
        {tab === "perms" && (
          <div>
            {permsLoading && <div className="muted">{t("common.loading")}</div>}
            {permsErr && <div style={{ color: "var(--danger)", fontSize: 14 }}>{t("profile.perms.load_failed", { err: permsErr })}</div>}
            {perms && (
              <>
                <div style={{
                  display: "flex", alignItems: "center", gap: 10,
                  padding: "10px 12px", marginBottom: 16,
                  background: "var(--panel-2)", border: "1px solid var(--border)",
                  borderRadius: 8, fontSize: 14,
                }}>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: 14, fontWeight: 500, color: "var(--fg)" }}>
                      {perms.is_admin
                        ? t("profile.role_admin")
                        : (perms.role?.name || t("profile.perms.no_role"))}
                    </div>
                    <div className="muted" style={{ fontSize: 13, marginTop: 2 }}>
                      {perms.is_admin
                        ? t("profile.perms.admin_all")
                        : (perms.role?.description || (perms.role ? t("profile.perms.no_role_desc") : t("profile.perms.contact_admin")))}
                    </div>
                  </div>
                </div>

                {perms.modules.length === 0 ? (
                  <div className="muted" style={{ fontSize: 14 }}>{t("profile.perms.no_modules")}</div>
                ) : (() => {
                  const KIND_ORDER = ["crm", "hermes", "admin", "skill"];
                  // Group modules by kind
                  const byKind = new Map<string, typeof perms.modules>();
                  for (const m of perms.modules) {
                    const k = m.kind || "other";
                    if (!byKind.has(k)) byKind.set(k, []);
                    byKind.get(k)!.push(m);
                  }
                  const kinds = [...byKind.keys()].sort((a, b) => {
                    const ia = KIND_ORDER.indexOf(a); const ib = KIND_ORDER.indexOf(b);
                    return (ia < 0 ? 999 : ia) - (ib < 0 ? 999 : ib);
                  });

                  const renderCard = (m: typeof perms.modules[number]) => {
                    const grantedCount = m.permissions.filter(p => p.granted).length;
                    const total = m.permissions.length;
                    const allGranted = grantedCount === total;
                    const noneGranted = grantedCount === 0;
                    return (
                      <details key={m.id} style={{
                        border: "1px solid var(--border)", borderRadius: 6,
                        background: noneGranted ? "transparent" : "var(--panel-2)",
                        padding: "8px 10px",
                      }}>
                        <summary style={{ cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "space-between", userSelect: "none", fontSize: 14 }}>
                          <span style={{ fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{moduleDisplay(m)}</span>
                          <span style={{
                            fontSize: 13,
                            color: allGranted ? "var(--success)" : noneGranted ? "var(--muted)" : "var(--warning)",
                            fontWeight: 500, flexShrink: 0, marginLeft: 8,
                          }}>
                            {allGranted ? t("profile.perms.all") : noneGranted ? t("profile.perms.none") : t("profile.perms.ratio", { g: grantedCount, total })}
                          </span>
                        </summary>
                        <div style={{ marginTop: 8, display: "flex", flexWrap: "wrap", gap: 6 }}>
                          {m.permissions.map(p => (
                            <span key={p.code} style={{
                              padding: "2px 8px", borderRadius: 10, fontSize: 13,
                              background: p.granted ? "var(--success-soft)" : "transparent",
                              border: `1px solid ${p.granted ? "color-mix(in srgb, var(--success) 40%, transparent)" : "var(--border)"}`,
                              color: p.granted ? "var(--success)" : "var(--muted)",
                              textDecoration: p.granted ? "none" : "line-through",
                            }}>
                              {p.name}
                            </span>
                          ))}
                        </div>
                      </details>
                    );
                  };

                  const activeKind = kinds.includes(permsKind) ? permsKind : kinds[0];
                  const mods = byKind.get(activeKind) || [];

                  // Skill kind: further group by category (skill.<cat>.<slug>)
                  let body: React.ReactNode;
                  if (activeKind === "skill") {
                    const byCat = new Map<string, typeof mods>();
                    for (const m of mods) {
                      const parts = (m.name || "").split(".");
                      const cat = parts.length >= 3 ? parts[1] : t("profile.perms.skill_other");
                      if (!byCat.has(cat)) byCat.set(cat, []);
                      byCat.get(cat)!.push(m);
                    }
                    const cats = [...byCat.keys()].sort();
                    body = (
                      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                        {cats.map(cat => {
                          const catMods = byCat.get(cat)!;
                          const catGranted = catMods.filter(m => m.permissions.some(p => p.granted)).length;
                          return (
                            <details key={cat} open={catGranted > 0}>
                              <summary style={{ cursor: "pointer", fontSize: 14, color: "var(--muted)", userSelect: "none", marginBottom: 6 }}>
                                {t("profile.perms.cat", { cat })} <span style={{ fontSize: 13 }}>({catGranted}/{catMods.length})</span>
                              </summary>
                              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, marginTop: 6 }}>
                                {catMods.map(renderCard)}
                              </div>
                            </details>
                          );
                        })}
                      </div>
                    );
                  } else {
                    body = (
                      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
                        {mods.map(renderCard)}
                      </div>
                    );
                  }

                  return (
                    <div>
                      {/* Kind tabs */}
                      <div style={{ display: "flex", gap: 4, borderBottom: "1px solid var(--border)", marginBottom: 14, flexWrap: "wrap" }}>
                        {kinds.map(k => {
                          const ms = byKind.get(k)!;
                          const g = ms.filter(m => m.permissions.some(p => p.granted)).length;
                          const active = k === activeKind;
                          const kindLabelKey = PERMS_KIND_LABEL_KEY[k];
                          return (
                            <button key={k} onClick={() => setPermsKind(k)}
                              style={{
                                background: "none", border: "none",
                                padding: "6px 12px", fontSize: 14,
                                color: active ? "var(--fg)" : "var(--muted)",
                                borderBottom: active ? "2px solid var(--accent)" : "2px solid transparent",
                                marginBottom: -1, cursor: "pointer",
                                fontWeight: active ? 600 : 400,
                              }}>
                              {kindLabelKey ? t(kindLabelKey) : k} <span style={{ fontSize: 13, opacity: 0.7 }}>({g}/{ms.length})</span>
                            </button>
                          );
                        })}
                      </div>
                      {body}
                    </div>
                  );
                })()}
              </>
            )}
          </div>
        )}

        {tab === "agent" && (
          <div>
            <div style={sectionHeader}>{t("profile.agent.heading")}</div>
            <p className="muted" style={{ fontSize: 14, marginTop: -2, marginBottom: 14 }}>
              {/* Under multiplex there is no per-profile stop/start, so drop the
                  "停止后自动重启" copy that describes a control the UI no longer shows. */}
              {agentProfiles?.some(p => p.multiplex)
                ? t("profile.agent.intro_multiplex")
                : t("profile.agent.intro")}
            </p>
            {agentLoading && !agentProfiles && (
              <div className="muted">{t("common.loading")}</div>
            )}
            {agentErr && (
              <div className="alert alert-error" style={{ marginBottom: 10 }}>{agentErr}</div>
            )}
            {agentProfiles && agentProfiles.length === 0 && (
              <div className="muted">{t("profile.agent.empty")}</div>
            )}
            {agentProfiles && agentProfiles.map(p => {
              const running = p.process?.running;
              const port = p.process?.port;
              const pid = p.process?.pid;
              const idle = p.process?.idle_seconds;
              const busy = agentBusyId === p.id;
              // Multiplex: one shared gateway serves every profile — there is
              // no per-profile process to start/stop, so hide those controls
              // (the running/port/PID status stays visible).
              const multiplex = !!p.multiplex;
              return (
                <div key={p.id} style={{
                  padding: "12px 14px",
                  background: "var(--panel)",
                  border: "1px solid var(--border)",
                  borderLeft: `3px solid ${running ? "var(--success)" : "var(--border)"}`,
                  borderRadius: 8,
                  marginBottom: 10,
                }}>
                  <div style={{ display: "flex", justifyContent: "space-between", gap: 10, alignItems: "baseline" }}>
                    <div style={{ minWidth: 0 }}>
                      <div style={{ fontSize: 14, fontWeight: 600 }}>
                        {p.name || p.id}
                        {p.is_default && (
                          <span className="muted" style={{ fontSize: 13, marginLeft: 6 }}>{t("profile.agent.default_badge")}</span>
                        )}
                      </div>
                      {p.slug && (
                        <div className="muted" style={{ fontSize: 13, marginTop: 2 }}>@{p.slug}</div>
                      )}
                    </div>
                  </div>
                  <div style={{
                    display: "flex", alignItems: "center", gap: 8, marginTop: 8,
                    fontSize: 13,
                  }}>
                    {running ? (
                      <>
                        <span style={{ color: "var(--success)" }}>{t("profile.agent.running")}</span>
                        <span className="muted">
                          {t("profile.agent.port_pid", { port: String(port ?? ""), pid: String(pid ?? "") })}
                          {typeof idle === "number" ? t("profile.agent.idle_suffix", { sec: Math.round(idle) }) : ""}
                        </span>
                        {!multiplex && (
                          <button
                            disabled={busy}
                            onClick={async () => {
                              setAgentBusyId(p.id);
                              try {
                                await stopMyProfile(p.id);
                                await refreshAgentProfiles();
                              } catch (e: any) { setAgentErr(e.message); }
                              finally { setAgentBusyId(null); }
                            }}
                            style={{
                              marginLeft: "auto",
                              background: "transparent", color: "var(--danger)",
                              border: "1px solid var(--border)", borderRadius: 5,
                              padding: "2px 10px", cursor: busy ? "not-allowed" : "pointer", fontSize: 13,
                              opacity: busy ? 0.6 : 1,
                            }}
                          >{t("profile.agent.stop")}</button>
                        )}
                      </>
                    ) : (
                      <>
                        <span className="muted">{t("profile.agent.stopped")}</span>
                        {!multiplex && (
                          <button
                            disabled={busy}
                            onClick={async () => {
                              setAgentBusyId(p.id);
                              try {
                                await startMyProfile(p.id);
                                await refreshAgentProfiles();
                              } catch (e: any) { setAgentErr(e.message); }
                              finally { setAgentBusyId(null); }
                            }}
                            style={{
                              marginLeft: "auto",
                              background: "transparent", color: "var(--success)",
                              border: "1px solid var(--border)", borderRadius: 5,
                              padding: "2px 10px", cursor: busy ? "not-allowed" : "pointer", fontSize: 13,
                              opacity: busy ? 0.6 : 1,
                            }}
                          >{t("profile.agent.start")}</button>
                        )}
                      </>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
