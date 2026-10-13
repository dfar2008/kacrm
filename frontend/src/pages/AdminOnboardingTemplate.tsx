import { useEffect, useMemo, useState } from "react";
import {
  getOnboardingTemplate, putOnboardingTemplate,
  type OnboardingTemplate, type OnboardingCronJob,
} from "@/lib/api";
import { toast } from "@/lib/toast";
import { useT } from "@/lib/i18n";
import { SkillsTemplateEditor } from "./onboarding/SkillsTemplateEditor";
import { CronTemplateEditor } from "./onboarding/CronTemplateEditor";
import { ModelTemplateEditor } from "./onboarding/ModelTemplateEditor";

/**
 * 新用户模板 — admin-only. Edits the deployment-global defaults every
 * newly created agent profile inherits (SOUL.md / Personality / disabled
 * skills / model). Two-pane file-manager layout mirroring WorkspaceTab:
 * left sidebar = sections, right = the active section's editor + Save.
 *
 * 技能 / 模型两个标签用富编辑器(SkillsTemplateEditor / ModelTemplateEditor):
 * 它们只更新本页持有的 state,由顶部统一的「保存模板」按钮经
 * putOnboardingTemplate 一次性落盘 —— 单一保存按钮、按 tab 的 dirty 圆点
 * 模型保持不变.
 */

type TabId = "soul" | "personality" | "skills" | "cron" | "model";

export function AdminOnboardingTemplatePage() {
  const t = useT();
  const [loaded, setLoaded] = useState<OnboardingTemplate | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [active, setActive] = useState<TabId>("soul");

  const [soul, setSoul] = useState("");
  const [personality, setPersonality] = useState("");
  const [skillsDisabled, setSkillsDisabled] = useState<string[]>([]);
  const [cronJobs, setCronJobs] = useState<OnboardingCronJob[]>([]);
  // model.providers_json 的字符串形式 —— ModelTemplateEditor 解析/序列化它.
  // 模板的 legacy model/api_key/base_url 不在 UI 暴露,保存时原值回传.
  const [providers, setProviders] = useState("");

  function hydrate(d: OnboardingTemplate) {
    setLoaded(d);
    setSoul(d.soul_md || "");
    setPersonality(d.system_prompt || "");
    setSkillsDisabled(d.skills_disabled || []);
    setCronJobs(d.cron_jobs || []);
    setProviders(d.model?.providers_json || "");
  }

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getOnboardingTemplate()
      .then(d => { if (!cancelled) hydrate(d); })
      .catch((e: any) => toast(e.message, "error"))
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  const dirty = useMemo(() => {
    if (!loaded) {
      return { soul: false, personality: false, skills: false, cron: false, model: false };
    }
    const m = loaded.model || {};
    // skills_disabled 是无序集合 —— 排序后比较,避免顺序差异误判 dirty.
    const baseSkills = [...(loaded.skills_disabled || [])].sort().join("\n");
    const curSkills = [...skillsDisabled].sort().join("\n");
    // cron_jobs 是有序列表(展示顺序保留),直接 JSON 比较即可.
    const baseCron = JSON.stringify(loaded.cron_jobs || []);
    const curCron = JSON.stringify(cronJobs);
    return {
      soul: soul !== (loaded.soul_md || ""),
      personality: personality !== (loaded.system_prompt || ""),
      skills: curSkills !== baseSkills,
      cron: curCron !== baseCron,
      model: providers !== (m.providers_json || ""),
    };
  }, [loaded, soul, personality, skillsDisabled, cronJobs, providers]);
  const anyDirty = dirty.soul || dirty.personality || dirty.skills || dirty.cron || dirty.model;

  async function save() {
    setSaving(true);
    try {
      // 模型只编辑 providers_json. legacy model/api_key/base_url 不派生、
      // 不改动 —— 用 loaded 的原值回传(api_key 是掩码值,后端 is_masked
      // 判定为「未改」会保留已存储的真实 key).
      const m = loaded?.model;
      await putOnboardingTemplate({
        soul_md: soul,
        system_prompt: personality,
        skills_disabled: skillsDisabled,
        cron_jobs: cronJobs,
        model: {
          model: m?.model ?? null,
          api_key: m?.api_key ?? null,
          base_url: m?.base_url ?? null,
          providers_json: providers.trim() || null,
        },
      });
      const fresh = await getOnboardingTemplate();
      hydrate(fresh);
      toast(t("onboarding.saved"), "success");
    } catch (e: any) {
      toast(e.message, "error");
    } finally {
      setSaving(false);
    }
  }

  const TABS: { id: TabId; label: string }[] = [
    { id: "soul", label: t("onboarding.soul") },
    { id: "personality", label: t("onboarding.personality") },
    { id: "skills", label: t("onboarding.skills") },
    { id: "cron", label: t("onboarding.cron") },
    { id: "model", label: t("onboarding.model") },
  ];
  const activeLabel = TABS.find(x => x.id === active)?.label || "";

  return (
    <div style={{
      padding: "24px 28px", height: "100%", boxSizing: "border-box",
      display: "flex", flexDirection: "column",
    }}>
      <div style={{ marginBottom: 16, flexShrink: 0 }}>
        <h2 style={{ margin: 0, fontSize: 20 }}>{t("onboarding.title")}</h2>
        <p className="muted" style={{ margin: "4px 0 0", fontSize: 14 }}>
          {t("onboarding.desc")}
        </p>
      </div>

      {loading ? (
        <div style={cardStyle}>
          <div className="muted">{t("onboarding.loading")}</div>
        </div>
      ) : (
        <div style={{
          ...cardStyle, padding: 0, overflow: "hidden",
          display: "grid", gridTemplateColumns: "240px 1fr",
          flex: 1, minHeight: 0,
        }}>
          {/* ── Sidebar ─────────────────────────────────────── */}
          <div style={{
            borderRight: "1px solid var(--border)", background: "var(--bg)",
            display: "flex", flexDirection: "column", padding: "8px 0",
            overflow: "auto", minHeight: 0,
          }}>
            {TABS.map(tab => {
              const isActive = tab.id === active;
              const isDirty = dirty[tab.id];
              return (
                <button
                  key={tab.id}
                  onClick={() => setActive(tab.id)}
                  style={{
                    display: "flex", alignItems: "center", gap: 8,
                    padding: "10px 14px", textAlign: "left",
                    background: isActive ? "var(--panel-2)" : "transparent",
                    color: isActive ? "var(--fg-strong)" : "var(--muted)",
                    border: "none",
                    borderLeft: `3px solid ${isActive ? "var(--accent)" : "transparent"}`,
                    cursor: "pointer", fontSize: 14,
                    transition: "background 120ms",
                  }}
                  onMouseEnter={e => {
                    if (!isActive) e.currentTarget.style.background = "var(--panel)";
                  }}
                  onMouseLeave={e => {
                    if (!isActive) e.currentTarget.style.background = "transparent";
                  }}
                >
                  <span style={{
                    flex: 1, minWidth: 0, overflow: "hidden",
                    textOverflow: "ellipsis", whiteSpace: "nowrap",
                  }}>
                    {tab.label}
                  </span>
                  {isDirty && (
                    <span style={{
                      width: 6, height: 6, borderRadius: "50%",
                      background: "var(--warning)", flexShrink: 0,
                    }} />
                  )}
                </button>
              );
            })}
          </div>

          {/* ── Editor pane ─────────────────────────────────── */}
          <div style={{
            display: "flex", flexDirection: "column", minWidth: 0, minHeight: 0,
          }}>
            <div style={{
              padding: "10px 16px",
              display: "flex", alignItems: "center", gap: 12,
              borderBottom: "1px solid var(--border)", background: "var(--bg)",
            }}>
              <div style={{ minWidth: 0, flex: 1, fontSize: 14, fontWeight: 600 }}>
                {activeLabel}
              </div>
              <button
                onClick={save}
                disabled={saving || !anyDirty}
                style={{ ...primaryBtn, opacity: saving || !anyDirty ? 0.5 : 1 }}
              >
                {saving ? t("onboarding.saving") : t("onboarding.save")}
              </button>
            </div>

            <div style={{
              flex: 1, display: "flex", flexDirection: "column",
              minWidth: 0, minHeight: 0,
            }}>
              {active === "soul" && (
                <Editor value={soul} onChange={setSoul} />
              )}
              {active === "personality" && (
                <Editor value={personality} onChange={setPersonality} />
              )}
              {active === "skills" && (
                <SkillsTemplateEditor
                  disabled={skillsDisabled}
                  baseline={loaded?.skills_disabled || []}
                  onChange={setSkillsDisabled}
                />
              )}
              {active === "cron" && (
                <CronTemplateEditor
                  jobs={cronJobs}
                  baseline={loaded?.cron_jobs || []}
                  onChange={setCronJobs}
                />
              )}
              {active === "model" && (
                <ModelTemplateEditor value={providers} onChange={setProviders} />
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function Editor({
  value, onChange,
}: { value: string; onChange: (v: string) => void }) {
  return (
    <textarea
      value={value}
      onChange={e => onChange(e.target.value)}
      spellCheck={false}
      style={{
        flex: 1, minHeight: 460, width: "100%", boxSizing: "border-box",
        background: "var(--bg)", color: "inherit", border: "none", outline: "none",
        padding: "14px 16px", resize: "none",
        fontFamily: "ui-monospace, monospace", fontSize: 14, lineHeight: 1.55,
      }}
    />
  );
}

const cardStyle: React.CSSProperties = {
  background: "var(--panel)", border: "1px solid var(--border)",
  borderRadius: 10, padding: "16px 18px",
};
const primaryBtn: React.CSSProperties = {
  padding: "6px 16px", fontSize: 14, background: "var(--accent)", color: "var(--fg-on-accent)",
  border: "none", borderRadius: 6, cursor: "pointer", whiteSpace: "nowrap",
};
