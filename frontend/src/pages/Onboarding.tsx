/**
 * First-run setup wizard (`/setup`).
 *
 * Three COMPACT cards — Models / Channels / Skills — modelled on the
 * reference onboarding page: each column is a small widget (a picker +
 * an action + a "currently configured" list), NOT the full agent-detail
 * tabs. Only a model is required to start chatting; the prominent
 * "enter chat" CTA leaves for the inbox at any time.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  getModelConfig, putModelConfig,
  listProfileChannels, type ChannelEntry,
} from "@/lib/api";
import {
  KNOWN_PROVIDERS, hydratePcfg, type ModelPcfg,
} from "@/pages/agent-detail/modelPresets";
import { ChannelSetup } from "@/pages/agent-detail/ChannelSetup";
import { useT, useLocale } from "@/lib/i18n";
import { useRouter } from "@/lib/router";
import { ThemePicker } from "@/components/ThemePicker";
import { OnboardingDemo } from "@/components/OnboardingDemo";
import { CrmTourVideo } from "@/components/CrmTourVideo";
import { usePermissions } from "@/lib/permissions";
import { toast } from "@/lib/toast";

interface Props {
  profileId: string | null;
  onEnterChat: () => void;
  onEnterCrm: () => void;
}

type SetupTab = "setup" | "demo" | "tour";

export function OnboardingPage({ profileId, onEnterChat, onEnterCrm }: Props) {
  const t = useT();
  const { can } = usePermissions();
  const [tab, setTab] = useState<SetupTab>("setup");

  const tabs: { key: SetupTab; label: string }[] = [
    { key: "setup", label: t("setup.tab_setup") },
    { key: "demo", label: t("setup.showcase_tab_demo") },
    { key: "tour", label: t("setup.showcase_tab_tour") },
  ];

  return (
    <div style={{ padding: 24, maxWidth: 1280, margin: "0 auto" }}>
      <div style={{
        display: "flex", alignItems: "flex-start", justifyContent: "space-between",
        gap: 16, marginBottom: 20, flexWrap: "wrap",
      }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 22 }}>{t("setup.title")}</h1>
          <div style={{ color: "var(--muted)", marginTop: 6, fontSize: 14 }}>
            {t("setup.subtitle")}
          </div>
        </div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          {can("accounts", "index") && (
            <button className="btn" onClick={onEnterCrm}>
              {t("setup.enter_crm")} →
            </button>
          )}
          <button className="btn btn-primary" onClick={onEnterChat}>
            {t("setup.enter_chat")} →
          </button>
        </div>
      </div>

      {!profileId ? (
        <div style={{ color: "var(--muted)", padding: 24 }}>{t("setup.no_profile")}</div>
      ) : (
        <>
          {/* 顶层标签:快速设置 / 对话演示 / CRM 界面导览 */}
          <div style={{
            display: "flex", gap: 4, marginBottom: 16,
            borderBottom: "1px solid var(--border)",
          }}>
            {tabs.map(({ key, label }) => (
              <button key={key} onClick={() => setTab(key)}
                style={{
                  fontSize: 15, padding: "8px 16px", cursor: "pointer",
                  background: "transparent", border: "none",
                  borderBottom: tab === key
                    ? "2px solid var(--accent)" : "2px solid transparent",
                  marginBottom: -1,
                  color: tab === key ? "var(--text)" : "var(--muted)",
                  fontWeight: tab === key ? 600 : 400,
                }}>
                {label}
              </button>
            ))}
          </div>

          {tab === "setup" && (
            <div style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))",
              gap: 16, alignItems: "start",
            }}>
              <Col step="1" title={t("setup.models")} desc={t("setup.models_desc")} required>
                <ModelColumn profileId={profileId} />
              </Col>
              <Col step="2" title={t("setup.channels")} desc={t("setup.channels_desc")}>
                <ChannelColumn profileId={profileId} />
              </Col>
              {/* 技能面板已从向导移除(新用户默认技能已启用,管理入口在
                  「我的 Agent → 技能」);第 3 步 = 快捷操作。 */}
              <Col step="3" title={t("setup.qa_title")} desc={t("setup.qa_desc")}>
                <QuickActions />
              </Col>
            </div>
          )}
          {tab === "demo" && <OnboardingDemo onEnterChat={onEnterChat} />}
          {tab === "tour" && <CrmTourVideo onEnterCrm={onEnterCrm} />}
        </>
      )}
    </div>
  );
}

// ─── column shell ───────────────────────────────────────────────────────

function Col({
  step, title, desc, required, children,
}: {
  step: string; title: string; desc: string; required?: boolean; children: React.ReactNode;
}) {
  const t = useT();
  return (
    <section style={{
      border: "1px solid var(--border)", borderRadius: 10,
      background: "var(--surface)", padding: 16, minWidth: 0,
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{
          display: "inline-grid", placeItems: "center", width: 22, height: 22,
          borderRadius: "50%", background: "var(--surface-inset)",
          color: "var(--muted)", fontSize: 14, fontWeight: 700,
        }}>{step}</span>
        <span style={{ fontWeight: 600, fontSize: 16 }}>{title}</span>
        {required && (
          <span style={{
            fontSize: 13, color: "var(--err)", border: "1px solid var(--err)",
            borderRadius: 4, padding: "0 5px",
          }}>{t("setup.required")}</span>
        )}
      </div>
      <div style={{ color: "var(--muted)", fontSize: 14, margin: "6px 0 14px" }}>{desc}</div>
      {children}
    </section>
  );
}

const fieldStyle: React.CSSProperties = {
  width: "100%", padding: "7px 10px", borderRadius: 6,
  border: "1px solid var(--border)", background: "var(--surface-inset)",
  color: "var(--text)", fontSize: 14,
};
const labelStyle: React.CSSProperties = {
  display: "block", fontSize: 14, color: "var(--muted)", margin: "0 0 4px",
};

// ─── Models ─────────────────────────────────────────────────────────────

function ModelColumn({ profileId }: { profileId: string }) {
  const t = useT();
  const [pcfg, setPcfg] = useState<ModelPcfg | null>(null);
  const [providerId, setProviderId] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [modelId, setModelId] = useState("");
  const [saving, setSaving] = useState(false);

  const provider = useMemo(
    () => KNOWN_PROVIDERS.find((p) => p.id === providerId),
    [providerId],
  );

  // Seed the three controls for a provider from saved state, else preset.
  const seedFor = useCallback((cfg: ModelPcfg, prov: string, model?: string) => {
    const preset = KNOWN_PROVIDERS.find((p) => p.id === prov);
    const saved = cfg.providers?.[prov];
    setApiKey(saved?.api_key || preset?.defaultApiKey || "");
    setModelId(model || saved?.models?.[0]?.id || preset?.defaultModels[0]?.id || "");
  }, []);

  // On load, pre-select the CURRENT default model; if none, the first
  // enabled provider; else the first preset.
  useEffect(() => {
    let on = true;
    (async () => {
      let cfg: ModelPcfg;
      try {
        const r = await getModelConfig(profileId);
        let raw: unknown = r?.providers_json ?? null;
        if (typeof raw === "string") { try { raw = JSON.parse(raw); } catch { raw = null; } }
        cfg = hydratePcfg(raw);
      } catch { cfg = hydratePcfg(null); }
      if (!on) return;
      setPcfg(cfg);
      const dm = (cfg.default_model || "").trim();
      let prov = "", model = "";
      if (dm.includes("::")) { [prov, model] = dm.split("::"); }
      if (!prov) {
        const enabled = Object.entries(cfg.providers || {}).find(([, v]) => v?.enabled);
        if (enabled) prov = enabled[0];
      }
      if (!prov || !KNOWN_PROVIDERS.some((p) => p.id === prov)) prov = KNOWN_PROVIDERS[0].id;
      setProviderId(prov);
      seedFor(cfg, prov, model);
    })();
    return () => { on = false; };
  }, [profileId, seedFor]);

  const onProviderChange = useCallback((id: string) => {
    setProviderId(id);
    if (pcfg) seedFor(pcfg, id);
  }, [pcfg, seedFor]);

  const setAsDefault = useCallback(async () => {
    if (!provider || !modelId) return;
    setSaving(true);
    try {
      const base: ModelPcfg = pcfg ?? hydratePcfg(null);
      const next: ModelPcfg = {
        default_model: `${provider.id}::${modelId}`,
        providers: {
          ...base.providers,
          [provider.id]: {
            enabled: true,
            api_key: apiKey.trim(),
            base_url: provider.defaultBase,
            api_format: provider.defaultFormat,
            models: provider.defaultModels,
          },
        },
      };
      await putModelConfig(profileId, next);
      setPcfg(next);
      toast(t("setup.saved"));
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e), "error");
    } finally {
      setSaving(false);
    }
  }, [provider, modelId, apiKey, pcfg, profileId, t]);

  const currentDefault = pcfg?.default_model?.trim() || "";

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div>
        <label style={labelStyle}>{t("setup.model_provider")}</label>
        <select style={fieldStyle} value={providerId}
          onChange={(e) => onProviderChange(e.target.value)}>
          {KNOWN_PROVIDERS.map((p) => (
            <option key={p.id} value={p.id}>{p.icon} {p.name}</option>
          ))}
        </select>
      </div>
      <div>
        <label style={labelStyle}>{t("setup.model_apikey")}</label>
        <input style={fieldStyle} type="password" value={apiKey}
          onChange={(e) => setApiKey(e.target.value)} placeholder="sk-…" />
      </div>
      <div>
        <label style={labelStyle}>{t("setup.model_pick")}</label>
        <select style={fieldStyle} value={modelId}
          onChange={(e) => setModelId(e.target.value)}>
          {(() => {
            const opts = (provider?.defaultModels ?? []).map((m) => ({ id: m.id, name: m.name || m.id }));
            if (modelId && !opts.some((o) => o.id === modelId)) opts.unshift({ id: modelId, name: modelId });
            return opts.map((o) => <option key={o.id} value={o.id}>{o.name}</option>);
          })()}
        </select>
      </div>
      <button className="btn btn-primary" onClick={setAsDefault}
        disabled={saving || !apiKey.trim() || !modelId} style={{ marginTop: 2 }}>
        {saving ? t("setup.saving") : t("setup.set_default")}
      </button>
      <div style={{
        marginTop: 6, paddingTop: 10, borderTop: "1px solid var(--border)",
        fontSize: 14,
      }}>
        <span style={{ color: "var(--muted)" }}>{t("setup.current_default")}：</span>
        {currentDefault ? (
          <span style={{ fontWeight: 600 }}> {currentDefault}</span>
        ) : (
          <span style={{ color: "var(--muted)" }}> {t("setup.none_yet")}</span>
        )}
      </div>
    </div>
  );
}

// ─── Channels ───────────────────────────────────────────────────────────

function ChannelColumn({ profileId }: { profileId: string }) {
  const t = useT();
  const [channels, setChannels] = useState<ChannelEntry[]>([]);
  const [sel, setSel] = useState("");
  const [err, setErr] = useState(false);

  const reload = useCallback(async () => {
    try {
      const r = await listProfileChannels(profileId);
      setChannels(r.channels);
      setErr(false);
      setSel((cur) => cur || r.channels[0]?.channel || "");
    } catch { setErr(true); }
  }, [profileId]);

  useEffect(() => { void reload(); }, [reload]);

  const connected = channels.filter((c) => c.configured);

  if (err) return <div style={{ color: "var(--muted)", fontSize: 14 }}>{t("setup.load_failed")}</div>;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div>
        <label style={labelStyle}>{t("setup.channel_pick")}</label>
        <select style={fieldStyle} value={sel} onChange={(e) => setSel(e.target.value)}>
          {channels.map((c) => (
            <option key={c.channel} value={c.channel}>{c.display_name}</option>
          ))}
        </select>
      </div>
      {sel && <ChannelSetup profileId={profileId} channel={sel} onChange={reload} />}
      <div style={{ paddingTop: 10, borderTop: "1px solid var(--border)" }}>
        <div style={{ fontSize: 14, color: "var(--muted)", marginBottom: 6 }}>
          {t("setup.connected_channels")}
        </div>
        {connected.length === 0 ? (
          <div style={{ fontSize: 14, color: "var(--muted)" }}>{t("setup.no_connected")}</div>
        ) : connected.map((c) => (
          <div key={c.channel} style={{
            display: "flex", alignItems: "center", gap: 8, fontSize: 14, padding: "3px 0",
          }}>
            <span style={{
              width: 8, height: 8, borderRadius: "50%", background: "#10b981",
              display: "inline-block",
            }} />
            <span>{c.display_name}</span>
            {c.bot_name && <span style={{ color: "var(--muted)" }}>· {c.bot_name}</span>}
          </div>
        ))}
      </div>
    </div>
  );
}

// ─── Quick actions ──────────────────────────────────────────────────────
// 常见初始化动作的入口。简单的（主题/语言）就地生效；管理类跳已有管理页,
// 权限不足直接隐藏（同侧边栏门控:admin_users.index / admin_roles.index）。

function QuickActions() {
  const t = useT();
  const { navigate } = useRouter();
  const { can } = usePermissions();
  const { locale, toggle } = useLocale();
  const [themeOpen, setThemeOpen] = useState(false);

  const actions = [
    { key: "theme", icon: "🎨", label: t("setup.qa_theme"),
      show: true, run: () => setThemeOpen(true) },
    { key: "locale", icon: "🌐", label: locale === "zh-CN" ? t("setup.qa_locale_en") : t("setup.qa_locale_zh"),
      show: true, run: toggle },
    { key: "users", icon: "👤", label: t("setup.qa_add_user"),
      show: can("admin_users", "index"), run: () => navigate("/admin/users") },
    { key: "modules", icon: "🧩", label: t("setup.qa_modules"),
      show: can("admin_roles", "index"), run: () => navigate("/admin/schema") },
  ].filter((a) => a.show);

  return (
    <div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
        {actions.map((a) => (
          <button key={a.key} className="btn" onClick={a.run}
            style={{ justifyContent: "flex-start", fontSize: 14 }}>
            {a.icon} {a.label}
          </button>
        ))}
      </div>
      <ThemePicker open={themeOpen} onClose={() => setThemeOpen(false)} />
    </div>
  );
}
