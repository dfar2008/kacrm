import { useEffect, useState } from "react";
import { useRouter } from "@/lib/router";
import { listAgentProfiles, AgentProfile } from "@/lib/api";
import { ChannelsTab } from "@/pages/agent-detail/ChannelsTab";
import { ModelTab } from "@/pages/agent-detail/ModelTab";
import { OverviewTab } from "@/pages/agent-detail/OverviewTab";
import { SchedulingTab } from "@/pages/agent-detail/SchedulingTab";
import { SkillsTab } from "@/pages/agent-detail/SkillsTab";
import { WorkspaceTab } from "@/pages/agent-detail/WorkspaceTab";
import { useT, type DictKey } from "@/lib/i18n";

// Trust/Feedback/Training's natural dimension is specialist (six pro
// agents), not profile. Removed from the tab nav and moved to
// `/agents/specialist/:name`. `agent-detail/{Trust,Feedback,Training}Tab.tsx`
// are kept for now so we can port profile-specific extras (trust-threshold
// editor, reward curve) before deleting them.
const TABS: { key: TabKey; labelKey: DictKey }[] = [
  { key: "overview",   labelKey: "agent.detail.tab.overview" },
  { key: "channels",   labelKey: "agent.detail.tab.channels" },
  { key: "skills",     labelKey: "agent.detail.tab.skills" },
  { key: "workspace",  labelKey: "agent.detail.tab.workspace" },
  { key: "model",      labelKey: "agent.detail.tab.model" },
  { key: "scheduling", labelKey: "agent.detail.tab.scheduling" },
];
type TabKey = "overview" | "channels" | "skills" | "workspace" | "model" | "scheduling";

export function AgentDetailPage({ profileId, tab }: { profileId: string; tab: string }) {
  const { navigate } = useRouter();
  const t = useT();
  const [profile, setProfile] = useState<AgentProfile | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const activeTab = (TABS.find(t => t.key === tab)?.key || "overview") as TabKey;

  async function loadProfile() {
    try {
      const r = await listAgentProfiles();
      const p = (r.profiles || []).find(x => x.id === profileId);
      if (!p) { setNotFound(true); return; }
      setProfile(p);
    } catch (e: any) { setErr(e.message); }
  }
  useEffect(() => { loadProfile(); }, [profileId]);

  if (notFound) {
    return (
      <div style={{ padding: 24 }}>
        <h2>{t("agent.detail.not_found")}</h2>
      </div>
    );
  }
  if (err) return <div className="alert alert-error" style={{ margin: 24 }}>{err}</div>;
  if (!profile) return <div className="muted" style={{ padding: 24 }}>{t("agent.detail.loading")}</div>;

  const proc = profile.process;
  const running = !!proc?.running;
  // Multiplex: one shared gateway serves every profile, so there is no
  // per-profile process to start or stop — hide those controls (keep the
  // status pill so port/PID stays visible).
  const multiplex = !!profile.multiplex;

  async function handleStart() {
    try {
      const { startMyProfile } = await import("@/lib/api");
      await startMyProfile(profile!.id);
      await loadProfile();
    } catch (e: any) { setErr(e.message); }
  }
  async function handleStop() {
    try {
      const { stopMyProfile } = await import("@/lib/api");
      await stopMyProfile(profile!.id);
      await loadProfile();
    } catch (e: any) { setErr(e.message); }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden" }}>
      <div style={{ padding: "16px 24px 0", borderBottom: "1px solid var(--border)" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
          <h2 style={{ margin: 0 }}>{profile.id}</h2>
          {profile.is_default && (
            <span style={{ fontSize: 13, padding: "2px 8px", border: "1px solid var(--border)", borderRadius: 999 }}>{t("agent.detail.default_badge")}</span>
          )}
          {/* Process status pill + start/stop — mirrors UserProfileModal */}
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginLeft: 4, fontSize: 13 }}>
            {running ? (
              <>
                <span style={{ color: "#4ade80" }}>● {t("agent.detail.process.running")}</span>
                <span className="muted">
                  {t("agent.detail.process.port_pid", { port: String(proc?.port ?? ""), pid: String(proc?.pid ?? "") })}
                  {typeof proc?.idle_seconds === "number" ? t("agent.detail.process.idle_suffix", { sec: Math.round(proc.idle_seconds) }) : ""}
                </span>
                {!multiplex && (
                  <button onClick={handleStop}
                          style={{ background: "transparent", color: "var(--danger)", border: "1px solid var(--danger-soft)",
                                   borderRadius: 5, padding: "2px 10px", cursor: "pointer", fontSize: 13 }}>
                    {t("agent.detail.process.stop")}
                  </button>
                )}
              </>
            ) : (
              <>
                <span className="muted">○ {t("agent.detail.process.stopped")}</span>
                {!multiplex && (
                  <button onClick={handleStart}
                          style={{ background: "transparent", color: "var(--success)", border: "1px solid var(--success-soft)",
                                   borderRadius: 5, padding: "2px 10px", cursor: "pointer", fontSize: 13 }}>
                    {t("agent.detail.process.start")}
                  </button>
                )}
              </>
            )}
          </div>
        </div>
        <div style={{ display: "flex", gap: 4, marginTop: 12 }}>
          {TABS.map(tab => (
            <button key={tab.key}
                    className={activeTab === tab.key ? "primary" : "ghost"}
                    onClick={() => navigate(`/agents/${encodeURIComponent(profile.id)}/${tab.key}`)}>
              {t(tab.labelKey)}
            </button>
          ))}
        </div>
      </div>
      <div style={{ flex: 1, overflow: "auto", padding: 24 }}>
        {activeTab === "overview" && <OverviewTab profile={profile} onRefresh={loadProfile} />}
        {activeTab === "channels" && <ChannelsTab profileId={profile.id} />}
        {activeTab === "skills" && <SkillsTab profileId={profile.id} />}
        {activeTab === "workspace" && <WorkspaceTab profileId={profile.id} />}
        {activeTab === "model" && <ModelTab profileId={profile.id} />}
        {activeTab === "scheduling" && <SchedulingTab profileId={profile.id} />}
      </div>
    </div>
  );
}
