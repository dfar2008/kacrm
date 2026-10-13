import { type ReactNode, useEffect, useRef, useState } from "react";
import { useRouter } from "@/lib/router";
import { logout, getFieldProposalsPendingCount } from "@/lib/api";
import { useCurrentUser } from "@/lib/currentUser";
import { usePermissions } from "@/lib/permissions";
import { UserProfileModal } from "@/components/UserProfileModal";
import { ThemePicker } from "@/components/ThemePicker";
import { KacrmMark } from "@/components/KacrmMark";
import { useLocale, useT, type DictKey } from "@/lib/i18n";

/**
 * Slim navigation rail matching the original dashboard.
 *
 *   Top rail (primary modules):    Chat · Agents · CRM
 *   Account menu (bottom 👤):      个人信息 · 审批中心 · 用量统计
 *                                  用户管理  (admin only; includes per-user
 *                                  agent/process panel in detail drawer)
 *                                  注销登录
 *
 * The account menu collects meta / ops / admin features so the rail
 * stays focused on the three top-level product surfaces.
 */

// Lucide-style stroke SVG. Inline so we don't pull a whole icon
// library; same visual family as the CRM tab strip (see Crm.tsx
// ``ModuleIcon``) so rail + popup-menu + tab-strip read as one
// consistent icon system.
const _svgProps = (size: number) => ({
  width: size, height: size, viewBox: "0 0 24 24", fill: "none" as const,
  stroke: "currentColor", strokeWidth: 1.7,
  strokeLinecap: "round" as const, strokeLinejoin: "round" as const,
  "aria-hidden": true,
});

const SVG_USERS = (
  <svg {..._svgProps(22)}>
    <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" />
    <circle cx="9" cy="7" r="4" />
    <path d="M22 21v-2a4 4 0 0 0-3-3.87" />
    <path d="M16 3.13a4 4 0 0 1 0 7.75" />
  </svg>
);

// Compass icon for /decisions — chosen to evoke navigation / choice-making.
const SVG_COMPASS = (
  <svg {..._svgProps(22)}>
    <circle cx="12" cy="12" r="10"/>
    <polygon points="16.24 7.76 14.12 14.12 7.76 16.24 9.88 9.88 16.24 7.76"/>
  </svg>
);

// Bar-chart icon for /dashboards — same Lucide stroke as the existing
// MenuIcon "analytics" 16px variant, scaled to 22px for RAIL consistency.
const SVG_BAR_CHART = (
  <svg {..._svgProps(22)}>
    <line x1="12" y1="20" x2="12" y2="10"/>
    <line x1="18" y1="20" x2="18" y2="4"/>
    <line x1="6" y1="20" x2="6" y2="14"/>
    <line x1="3" y1="20" x2="21" y2="20"/>
  </svg>
);

// Per-menu-item icons. Each is a small (16px) Lucide stroke. Rendered
// via ``MenuIcon`` switch below. Keeping inline SVG so themes
// (dark/light) inherit ``currentColor`` and the popup looks crisp at
// any DPI without the OS-emoji rendering inconsistency.
function MenuIcon({ name }: { name: string }) {
  const p = _svgProps(16);
  switch (name) {
    case "profile":            // 👤 — single user
      return (<svg {...p}><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>);
    case "agent":              // 🤖 — bot
      return (<svg {...p}><rect x="3" y="11" width="18" height="10" rx="2"/><circle cx="12" cy="5" r="2"/><path d="M12 7v4"/><line x1="8" y1="16" x2="8" y2="16"/><line x1="16" y1="16" x2="16" y2="16"/></svg>);
    case "locale":             // 🌐 — globe
      return (<svg {...p}><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg>);
    case "approvals":          // ✅ — check-circle
      return (<svg {...p}><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>);
    case "notifications":      // 🔔 — bell
      return (<svg {...p}><path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg>);
    case "analytics":          // 📊 — bar-chart
      return (<svg {...p}><line x1="12" y1="20" x2="12" y2="10"/><line x1="18" y1="20" x2="18" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/><line x1="3" y1="20" x2="21" y2="20"/></svg>);
    case "memory":             // 🧠 — brain (simplified)
      return (<svg {...p}><path d="M9.5 2A2.5 2.5 0 0 1 12 4.5V20a2 2 0 0 1-2 2 2 2 0 0 1-2-2 2.5 2.5 0 0 1-2.5-2.5A2.5 2.5 0 0 1 3 15a2.5 2.5 0 0 1 .5-1.5 2.5 2.5 0 0 1-.5-1.5 2.5 2.5 0 0 1 1-2 2.5 2.5 0 0 1 2-3 2.5 2.5 0 0 1 3.5-2"/><path d="M14.5 2A2.5 2.5 0 0 0 12 4.5V20a2 2 0 0 0 2 2 2 2 0 0 0 2-2 2.5 2.5 0 0 0 2.5-2.5A2.5 2.5 0 0 0 21 15a2.5 2.5 0 0 0-.5-1.5 2.5 2.5 0 0 0 .5-1.5 2.5 2.5 0 0 0-1-2 2.5 2.5 0 0 0-2-3 2.5 2.5 0 0 0-3.5-2"/></svg>);
    case "tasks":              // 📋 — list-todo
      return (<svg {...p}><rect x="3" y="5" width="6" height="6" rx="1"/><path d="m3 17 2 2 4-4"/><path d="M13 6h8"/><path d="M13 12h8"/><path d="M13 18h8"/></svg>);
    case "admin_users":        // 👥 — multiple users
      return SVG_USERS_SMALL;
    case "admin_departments":  // 🏢 — building
      return (<svg {...p}><rect x="4" y="2" width="16" height="20" rx="2"/><path d="M9 22v-4h6v4"/><path d="M8 6h.01M16 6h.01M12 6h.01M12 10h.01M12 14h.01M16 10h.01M16 14h.01M8 10h.01M8 14h.01"/></svg>);
    case "admin_roles":        // 🔐 — shield
      return (<svg {...p}><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="m9 12 2 2 4-4"/></svg>);
    case "admin_schema":       // 📋 — database / sliders
      return (<svg {...p}><line x1="4" y1="21" x2="4" y2="14"/><line x1="4" y1="10" x2="4" y2="3"/><line x1="12" y1="21" x2="12" y2="12"/><line x1="12" y1="8" x2="12" y2="3"/><line x1="20" y1="21" x2="20" y2="16"/><line x1="20" y1="12" x2="20" y2="3"/><line x1="1" y1="14" x2="7" y2="14"/><line x1="9" y1="8" x2="15" y2="8"/><line x1="17" y1="16" x2="23" y2="16"/></svg>);
    case "logout":             // ↩︎ — log-out
      return (<svg {...p}><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>);
    case "setup":              // 🧭 — compass / quick setup
      return (<svg {...p}><circle cx="12" cy="12" r="10"/><polygon points="16.24 7.76 14.12 14.12 7.76 16.24 9.88 9.88 16.24 7.76"/></svg>);
    case "palette":
      return (<svg {...p}><circle cx="13.5" cy="6.5" r=".5"/><circle cx="17.5" cy="10.5" r=".5"/><circle cx="8.5" cy="7.5" r=".5"/><circle cx="6.5" cy="12.5" r=".5"/><path d="M12 2C6.5 2 2 6.5 2 12s4.5 10 10 10c.926 0 1.648-.746 1.648-1.688 0-.437-.18-.835-.437-1.125-.29-.289-.438-.652-.438-1.125a1.64 1.64 0 0 1 1.668-1.668h1.996c3.051 0 5.563-2.512 5.563-5.563C22 6.012 17.5 2 12 2z"/></svg>);
    default:
      return null;
  }
}

const SVG_USERS_SMALL = (
  <svg {..._svgProps(16)}>
    <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/>
    <circle cx="9" cy="7" r="4"/>
    <path d="M22 21v-2a4 4 0 0 0-3-3.87"/>
    <path d="M16 3.13a4 4 0 0 1 0 7.75"/>
  </svg>
);

interface RailItem {
  to: string;
  labelKey: DictKey;
  /** Plain string (emoji / text) OR a React node (inline SVG). */
  icon: React.ReactNode;
  /** Permission gate: ``module.action`` that must be granted. */
  require?: string;
}
// Chat has no backend gate (any authed user has a chat inbox); CRM gates
// on ``accounts.index`` as a representative CRM surface (fine-grained
// per-page gating lives inside the CRM router).
//
// Charter v2 Phase 3 (2026-05-06): the 🤖 Agents rail item was removed
// along with the Agent Pool dashboard. The user's own agent-profile
// (Personality / channels / model / etc.) is reachable via the bottom
// avatar menu's "我的 Agent Profile" entry now.
const RAIL: RailItem[] = [
  { to: "/",       labelKey: "shell.nav.chat",   icon: "💬" },
  { to: "/crm",    labelKey: "shell.nav.crm",    icon: SVG_USERS, require: "accounts.index" },
  // /decisions nav entry removed in consolidation Phase 2.C
  // (2026-05-16). The chat surface migrated to HomeChatBar on /crm
  // and the briefing cards already show via TodayPriority at the top
  // of /crm. Phase 2.D will delete the page + backend module.
  // Dashboards surface — AI-time forensic dashboard (replaces the old
  // /analytics MENU entry per spec
  // docs/superpowers/specs/2026-05-11-ai-dashboard-design.md §三).
  { to: "/dashboards", labelKey: "shell.nav.dashboards", icon: SVG_BAR_CHART, require: "accounts.index" },
];

interface MenuItem {
  labelKey: DictKey;
  icon: string;
  to?: string;
  require?: string;
  // Strictly admin-only (perms.is_admin). Use when no grant code captures
  // the audience — e.g. the org-shared memory graph, which exposes every
  // user's extracted entities and so must not be browsable by employees.
  adminOnly?: boolean;
  danger?: boolean;
  action?: "logout" | "profile" | "toggleLocale" | "myAgentProfile" | "openTheme";
}

const MENU: MenuItem[] = [
  { labelKey: "shell.menu.profile", icon: "profile", action: "profile" },
  // Direct entry to the user's own agent-profile detail page
  // (Personality / Channels / Skills / Workspace / Model / Cron tabs).
  // Charter v2 Phase 3 (2026-05-06): replaces the deleted Agents rail
  // item — 1 user = 1 profile, no list page needed.
  { labelKey: "shell.menu.my_agent_profile", icon: "agent", action: "myAgentProfile" },
  // Quick setup wizard — models / channels / skills in one page. Lives in
  // the avatar menu (not the rail) since it's an occasional, not daily, tool.
  { to: "/setup", labelKey: "shell.nav.setup", icon: "setup" },
  { labelKey: "shell.menu.locale_toggle", icon: "locale", action: "toggleLocale" },
  { labelKey: "theme.menu.open", icon: "palette", action: "openTheme" },
  { to: "/approvals", labelKey: "shell.menu.approvals", icon: "approvals", require: "approvals.index" },
  { to: "/my-approvals", labelKey: "nav.myApprovals", icon: "approvals" },
  // Token usage report — moved from /analytics (now /dashboards) to
  // /admin/usage per dashboards spec §10.b. Admin-only via the same
  // analytics.index gate (admins are the audience for billing reports).
  { to: "/admin/usage", labelKey: "shell.menu.admin_usage", icon: "analytics", require: "analytics.index" },
  // Dashboard CSV export audit — admin-only (per-export audit_log query).
  // Reuses analytics.index since the audience overlaps (admins reviewing
  // who exported what data).
  { to: "/admin/export-audit", labelKey: "shell.menu.admin_export_audit", icon: "analytics", require: "analytics.index" },
  // Memory graph is ORG-SHARED (entities default to scope='org'/全员), so
  // it surfaces every user's extracted companies/people/facts. That's an
  // org-wide knowledge view, not personal — restrict the menu to admins.
  // (Backend /api/memory/* also enforces admin; see memory/api.py.)
  { to: "/memory",    labelKey: "shell.menu.memory",    icon: "memory", adminOnly: true },
  { to: "/tasks",     labelKey: "shell.menu.tasks",     icon: "tasks" },
  { to: "/admin/users",             labelKey: "shell.menu.admin_users",             icon: "admin_users", require: "admin_users.index" },
  { to: "/admin/onboarding",        labelKey: "shell.menu.admin_onboarding",        icon: "admin_users", require: "admin_users.index" },
  { to: "/admin/departments",       labelKey: "shell.menu.admin_departments",       icon: "admin_departments", require: "admin_departments.index" },
  { to: "/admin/roles",             labelKey: "shell.menu.admin_roles",             icon: "admin_roles", require: "admin_roles.index" },
  // Schema-evo (ADR 2026-04-20): unified hub — field builder + proposal
  // review + changelog in one place. Gated on admin_roles.index since the
  // same population that grants/edits roles should curate schema.
  { to: "/admin/schema",            labelKey: "shell.menu.admin_schema_hub",        icon: "admin_schema", require: "admin_roles.index" },
  { labelKey: "shell.menu.logout", icon: "logout", action: "logout", danger: true },
];

function isActive(path: string, to: string): boolean {
  if (to === "/") return path === "/" || path === "";
  return path === to || path.startsWith(to + "/");
}

export function AppShell({ children }: { children: ReactNode }) {
  const { path, navigate } = useRouter();
  const [menuOpen, setMenuOpen] = useState(false);
  const [profileOpen, setProfileOpen] = useState(false);
  const [themeOpen, setThemeOpen] = useState(false);
  const accountRef = useRef<HTMLDivElement>(null);
  const t = useT();
  const { locale, toggle: toggleLocale } = useLocale();
  // Locale-toggle menu label shows the language you will switch TO, not
  // the current one — matches the typical "中 / En" toggle idiom.
  const localeSwitchLabel = locale === "zh-CN" ? "English" : "中文";

  // Single source of truth for who's logged in. The provider hydrates
  // this from /api/me when the authed tree mounts, so every gate
  // (admin menu, avatar, profile modal) reads the same fresh value.
  const { user: me } = useCurrentUser();
  const { grants, data: perms } = usePermissions();
  const hasGrant = (code: string | undefined): boolean => {
    if (!code) return true;
    if (perms?.is_admin) return true;
    return grants.has(code);
  };

  // Charter v2 Phase 3 (2026-05-06): Agent Pool inbox badge removed
  // along with the /api/agents/inbox endpoint. The "需要你关注" signal
  // is now surfaced via the Approvals page + coach_hints push only.

  // Pending field-proposal count → red-dot badge on the schema-hub menu
  // entry. Tells an admin the AI has filed schema-change proposals worth
  // a review. Gated on the same grant as the /admin/schema menu item.
  const canReviewSchema = hasGrant("admin_roles.index");
  const [pendingProposals, setPendingProposals] = useState(0);
  useEffect(() => {
    if (!canReviewSchema) return;
    let alive = true;
    const tick = () => {
      getFieldProposalsPendingCount()
        .then(r => { if (alive) setPendingProposals(r.count); })
        .catch(() => { /* badge is best-effort — ignore transient errors */ });
    };
    tick();
    const iv = setInterval(tick, 60_000);
    return () => { alive = false; clearInterval(iv); };
  }, [canReviewSchema]);

  // Click-outside to close the account menu.
  useEffect(() => {
    if (!menuOpen) return;
    const onDocClick = (e: MouseEvent) => {
      if (!accountRef.current?.contains(e.target as Node)) setMenuOpen(false);
    };
    document.addEventListener("click", onDocClick);
    return () => document.removeEventListener("click", onDocClick);
  }, [menuOpen]);

  async function onLogout() {
    await logout();
    navigate("/login", true);
  }

  function handleMenu(item: MenuItem) {
    setMenuOpen(false);
    if (item.action === "logout") { onLogout(); return; }
    if (item.action === "profile") { setProfileOpen(true); return; }
    if (item.action === "toggleLocale") { toggleLocale(); return; }
    if (item.action === "openTheme") { setThemeOpen(true); return; }
    if (item.action === "myAgentProfile") {
      // 1 user = 1 profile (charter v2). The user's profile slug is
      // their username, exposed on the /api/me payload.
      const slug = me?.username || "";
      if (slug) navigate(`/agents/profiles/${encodeURIComponent(slug)}`);
      return;
    }
    if (item.to) navigate(item.to);
  }

  // Pick a human-readable initial for the account rail button: first
  // letter of email before the @, else first digit of user_id.
  const accountInitial = (() => {
    if (!me) return "👤";
    const e = (me.email || "").trim();
    if (e) return e.charAt(0).toUpperCase();
    const uid = String(me.user_id || "");
    return uid.charAt(0) || "👤";
  })();

  // Highlight the account button when any menu-item route is active.
  const menuRouteActive = MENU.some(m =>
    m.to && hasGrant(m.require) && isActive(path, m.to));
  const visibleMenu = MENU.filter(m =>
    (m.action || hasGrant(m.require)) && (!m.adminOnly || !!perms?.is_admin),
  );
  const visibleRail = RAIL.filter(n => hasGrant(n.require));

  return (
    <div style={{
      display: "grid",
      gridTemplateColumns: "64px 1fr",
      height: "100vh",
    }}>
      <aside style={{
        borderRight: "1px solid var(--border)",
        background: "var(--sidebar-bg)",
        display: "flex", flexDirection: "column",
        alignItems: "center",
        padding: "10px 0",
      }}>
        <a href="/"
           onClick={e => { e.preventDefault(); navigate("/"); }}
           title="KaCRM"
           style={{
             width: 36, height: 36, borderRadius: 8,
             background: "var(--panel-2)",
             display: "grid", placeItems: "center",
             textDecoration: "none",
             marginBottom: 8,
           }}><KacrmMark size={28} title="KaCRM" /></a>

        <nav style={{ display: "flex", flexDirection: "column", gap: 4, flex: 1, width: "100%", alignItems: "center" }}>
          {visibleRail.map(n => {
            const active = isActive(path, n.to);
            const title = t(n.labelKey);
            return (
              <RailButton key={n.to}
                          icon={n.icon}
                          label={title}
                          active={active}
                          onClick={() => navigate(n.to)} />
            );
          })}
        </nav>

        <div ref={accountRef} style={{ position: "relative", width: "100%", display: "grid", placeItems: "center" }}>
          <RailButton icon={accountInitial}
                      label={me?.email || t("shell.avatar.default")}
                      active={menuRouteActive || menuOpen}
                      isAvatar={accountInitial !== "👤"}
                      badge={pendingProposals}
                      onClick={() => setMenuOpen(o => !o)} />
          {menuOpen && (
            <div style={{
              position: "absolute",
              left: "100%",
              bottom: 0,
              marginLeft: 6,
              minWidth: 180,
              background: "var(--panel)",
              border: "1px solid var(--border)",
              borderRadius: 8,
              padding: 4,
              boxShadow: "var(--shadow-popover)",
              zIndex: 50,
            }}>
              {visibleMenu.map((m, i) => {
                  const needDivider = m.danger && i > 0;
                  // Locale toggle shows the language you'll switch TO
                  // (not the active one), matching "中 / En" UX.
                  const displayLabel = m.action === "toggleLocale"
                    ? localeSwitchLabel
                    : t(m.labelKey);
                  // Visually group admin-* menu entries with a divider on
                  // the first one. "Admin" is detected by require starting
                  // with ``admin_``; skipped for logout which owns the
                  // divider above it.
                  const prev = visibleMenu[i - 1];
                  const isAdminStart = !!m.require?.startsWith("admin_")
                    && !prev?.require?.startsWith("admin_");
                  return (
                    <div key={m.labelKey}>
                      {(needDivider || isAdminStart) && (
                        <div style={{ height: 1, background: "var(--border)", margin: "4px 0" }} />
                      )}
                      <button
                        onClick={() => handleMenu(m)}
                        style={{
                          display: "flex", alignItems: "center", gap: 8,
                          width: "100%",
                          padding: "8px 10px",
                          background: "transparent",
                          border: "none",
                          color: m.danger ? "var(--danger)" : "inherit",
                          textAlign: "left",
                          borderRadius: 6,
                          cursor: "pointer",
                          fontSize: 14,
                        }}
                        onMouseEnter={e => (e.currentTarget.style.background = "var(--panel-2)")}
                        onMouseLeave={e => (e.currentTarget.style.background = "transparent")}
                      >
                        <span style={{ display: "inline-flex", width: 16, height: 16 }}>
                          <MenuIcon name={m.icon} />
                        </span>
                        <span>{displayLabel}</span>
                        {m.to === "/admin/schema" && pendingProposals > 0 && (
                          <span style={{
                            marginLeft: "auto",
                            minWidth: 18, height: 18, padding: "0 5px",
                            borderRadius: 9,
                            background: "var(--danger)", color: "var(--on-danger)",
                            fontSize: 13, fontWeight: 700,
                            display: "inline-flex", alignItems: "center", justifyContent: "center",
                            lineHeight: 1,
                          }}>{pendingProposals > 99 ? "99+" : pendingProposals}</span>
                        )}
                      </button>
                    </div>
                  );
                })}
            </div>
          )}
        </div>
      </aside>
      <main style={{ overflow: "hidden", minWidth: 0 }}>
        {children}
      </main>
      <UserProfileModal
        open={profileOpen}
        onClose={() => setProfileOpen(false)}
        user={me}
      />
      <ThemePicker open={themeOpen} onClose={() => setThemeOpen(false)} />
    </div>
  );
}

function RailButton({ icon, label, active, onClick, isAvatar = false, badge }: {
  icon: React.ReactNode;
  label: string;
  active?: boolean;
  onClick: () => void;
  /** Render `icon` as a text initial inside a colored circle, not an emoji. */
  isAvatar?: boolean;
  /** Small red-dot badge count shown top-right of the icon; ≤ 0 hides. */
  badge?: number;
}) {
  return (
    <button
      onClick={onClick}
      title={label}
      style={{
        width: 44, height: 44,
        display: "grid", placeItems: "center",
        borderRadius: 8,
        border: "none",
        cursor: "pointer",
        fontSize: 20,
        background: active ? "var(--sidebar-active)" : "transparent",
        color: active ? "var(--accent)" : "var(--muted)",
        position: "relative",
      }}
      onMouseEnter={e => { if (!active) e.currentTarget.style.background = "var(--surface-hover)"; }}
      onMouseLeave={e => { if (!active) e.currentTarget.style.background = "transparent"; }}
    >
      {isAvatar ? (
        <span style={{
          width: 28, height: 28, borderRadius: "50%",
          background: "var(--accent)",
          color: "var(--fg-on-accent)",
          display: "inline-flex", alignItems: "center", justifyContent: "center",
          fontWeight: 700, fontSize: 14,
        }}>{icon}</span>
      ) : icon}
      {badge !== undefined && badge > 0 && (
        <span style={{
          position: "absolute", top: 4, right: 4,
          minWidth: 16, height: 16, padding: "0 4px",
          borderRadius: 10,
          background: "var(--danger)", color: "var(--on-danger)",
          fontSize: 13, fontWeight: 700,
          display: "inline-flex", alignItems: "center", justifyContent: "center",
          lineHeight: 1, pointerEvents: "none",
        }}>{badge > 99 ? "99+" : badge}</span>
      )}
    </button>
  );
}
