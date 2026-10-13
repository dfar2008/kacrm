import { RouterProvider, useRouter, match } from "@/lib/router";
import { AppShell } from "@/components/AppShell";
import { LoginPage } from "@/pages/Login";
import { RegisterPage } from "@/pages/Register";
import { CrmPage } from "@/pages/Crm";
import { AgentDetailPage } from "@/pages/AgentDetail";
import { ChatInboxPage } from "@/pages/ChatInbox";
import { ApprovalsPage } from "@/pages/Approvals";
import { MyApprovalsPage } from "@/pages/MyApprovals";
import { AiAuditPage } from "@/pages/AiAudit";
// DecisionsPage removed in consolidation Phase 2.D (2026-05-16).
// Functionality migrated to HomeChatBar (on /crm) + TodayPriority
// (already reading coach.proposal cards from gateway_actions).
import { DashboardsPage } from "@/pages/Dashboards";
import { AdminUsagePage } from "@/pages/AdminUsage";
import { AdminExportAuditPage } from "@/pages/AdminExportAudit";
import { MemoryPage } from "@/pages/Memory";
import { MemoryEntityDetailPage } from "@/pages/MemoryEntityDetail";
import { MemoryDebugPage } from "@/pages/MemoryDebug";
import { TasksPage } from "@/pages/Tasks";
import { AdminUsersPage } from "@/pages/AdminUsers";
import { AdminDepartmentsPage } from "@/pages/AdminDepartments";
import { AdminRolesPage } from "@/pages/AdminRoles";
import { AdminOnboardingTemplatePage } from "@/pages/AdminOnboardingTemplate";
import { AdminSchemaHub } from "@/pages/AdminSchemaHub";
import { Placeholder } from "@/pages/Placeholder";
import { OnboardingPage } from "@/pages/Onboarding";
import { useOnboardingGuard } from "@/lib/onboardingGuard";
import { CurrentUserProvider, useCurrentUser } from "@/lib/currentUser";
import { PermissionsProvider } from "@/lib/permissions";
import { ToastProvider } from "@/lib/toast";
import { LocaleProvider, useT } from "@/lib/i18n";
import { ThemeProvider } from "@/theme";

/**
 * App routing.
 *
 *   CurrentUserProvider
 *     └─ probes /api/me on mount (cookie auth, no localStorage)
 *        ├─ loading  → splash
 *        ├─ user=null → /login, /register
 *        └─ user!=null → authed shell
 *
 *   Logout is just "call /auth/logout, refresh()" — the server clears
 *   the HttpOnly cookie and the next /api/me probe returns 401, which
 *   flips us back to the login page.
 */
function AppBody() {
  const { path, navigate } = useRouter();
  const { user, loading, refresh } = useCurrentUser();
  const t = useT();
  const onboarding = useOnboardingGuard(user, loading);

  // Initial session probe — avoid flashing the login page before we
  // know if the cookie is valid.
  if (loading) {
    return (
      <div style={{
        display: "grid", placeItems: "center",
        height: "100vh", color: "var(--muted)",
      }}>{t("common.loading")}</div>
    );
  }

  const onLogin = () => { void refresh(); };

  // Unauth views (no shell, no approval poller)
  if (!user || path === "/login") {
    if (path === "/register") return <RegisterPage onLogin={onLogin} />;
    return <LoginPage onLogin={onLogin} />;
  }
  if (path === "/register") {
    return <RegisterPage onLogin={onLogin} />;
  }

  const onUnauth = () => { void refresh(); navigate("/login", true); };

  // First-run guard: a user whose default profile has no model configured
  // is routed to the /setup wizard once. While the probe is in flight we
  // hold the root path on a splash to avoid flashing the inbox; once a
  // model is configured (or the user skips) the redirect self-clears.
  const onRoot = match("/", path) || path === "";
  if (onRoot && onboarding.loading) {
    return (
      <div style={{
        display: "grid", placeItems: "center",
        height: "100vh", color: "var(--muted)",
      }}>{t("common.loading")}</div>
    );
  }
  if (onRoot && onboarding.needsSetup && !onboarding.dismissed) {
    navigate("/setup", true);
  }

  let page;
  if (match("/setup", path)) {
    page = (
      <OnboardingPage
        profileId={onboarding.profileId}
        onEnterChat={() => { onboarding.dismiss(); navigate("/", true); }}
        onEnterCrm={() => { onboarding.dismiss(); navigate("/crm", true); }}
      />
    );
  } else if (match("/", path) || path === "") {
    page = <ChatInboxPage />;
  } else if (path.startsWith("/agents/profiles/")) {
    // The user's own agent-profile detail page (Personality / Channels /
    // Skills / Workspace / Model / Cron tabs). Charter v2 = 1 user 1
    // profile, so the slug is always the user's username. Reached via
    // the bottom avatar menu's "我的 Agent Profile" entry.
    const rest = path.slice("/agents/profiles/".length);
    const [pid, subtab] = rest.split("/");
    if (pid) {
      page = <AgentDetailPage profileId={decodeURIComponent(pid)} tab={subtab || "overview"} />;
    } else {
      page = <Placeholder title={t("app.route.not_found_title")} phase="404"
        description={t("app.route.not_found_desc", { path })} />;
    }
  } else if (path.startsWith("/agents/")) {
    // Back-compat: old /agents/<profile_id>[/<tab>] links still work.
    const rest = path.slice("/agents/".length);
    const [pid, subtab] = rest.split("/");
    if (pid) {
      page = <AgentDetailPage profileId={decodeURIComponent(pid)} tab={subtab || "overview"} />;
    } else {
      page = <Placeholder title={t("app.route.not_found_title")} phase="404"
        description={t("app.route.not_found_desc", { path })} />;
    }
  } else if (match("/crm", path) || path.startsWith("/crm")) {
    page = <CrmPage onUnauth={onUnauth} />;
  } else if (match("/approvals", path)) {
    page = <ApprovalsPage />;
  } else if (match("/my-approvals", path)) {
    page = <MyApprovalsPage />;
  } else if (match("/ai-audit", path)) {
    page = <AiAuditPage />;
  } else if (match("/decisions", path) || path.startsWith("/decisions")) {
    // /decisions removed — redirect to /crm where chat + signals live now.
    navigate("/crm", true);
    page = <CrmPage onUnauth={onUnauth} />;
  } else if (match("/dashboards", path) || path.startsWith("/dashboards")) {
    page = <DashboardsPage />;
  } else if (match("/admin/usage", path)) {
    page = <AdminUsagePage />;
  } else if (match("/admin/export-audit", path)) {
    page = <AdminExportAuditPage />;
  } else if (match("/tasks", path)) {
    page = <TasksPage />;
  } else if (match("/memory", path)) {
    page = <MemoryPage />;
  } else if (match("/memory/debug", path)) {
    page = <MemoryDebugPage />;
  } else if (path.startsWith("/memory/entity/")) {
    const id = Number(path.slice("/memory/entity/".length));
    if (Number.isFinite(id) && id > 0) {
      page = <MemoryEntityDetailPage entityId={id} />;
    } else {
      page = <Placeholder title={t("app.route.not_found_title")} phase="404"
        description={t("app.route.invalid_entity", { path })} />;
    }
  } else if (match("/admin/users", path) || match("/admin/processes", path)) {
    // /admin/processes is a legacy alias — process management merged into
    // the user-detail drawer in AdminUsersPage.
    page = <AdminUsersPage />;
  } else if (match("/admin/departments", path)) {
    page = <AdminDepartmentsPage />;
  } else if (match("/admin/roles", path)) {
    page = <AdminRolesPage />;
  } else if (match("/admin/onboarding", path)) {
    page = <AdminOnboardingTemplatePage />;
  } else if (match("/admin/schema", path) || match("/admin/schema/:module", path)) {
    page = <AdminSchemaHub />;
  } else if (match("/admin/field-proposals", path)) {
    // Legacy direct link → hub on proposals tab
    page = <AdminSchemaHub initialTab="proposals" />;
  } else if (match("/admin/schema-changelog", path)) {
    // Legacy direct link → hub on changelog tab
    page = <AdminSchemaHub initialTab="changelog" />;
  } else {
    page = <Placeholder title={t("app.route.not_found_title")} phase="404"
      description={t("app.route.not_found_desc", { path })} />;
  }
  return (
    <>
      <AppShell>{page}</AppShell>
    </>
  );
}

export default function App() {
  return (
    <LocaleProvider>
      <ThemeProvider>
        <ToastProvider>
          <RouterProvider>
            <CurrentUserProvider>
              <PermissionsProvider>
                <AppBody />
              </PermissionsProvider>
            </CurrentUserProvider>
          </RouterProvider>
        </ToastProvider>
      </ThemeProvider>
    </LocaleProvider>
  );
}
