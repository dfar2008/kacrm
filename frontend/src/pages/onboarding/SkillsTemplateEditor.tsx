import { useEffect, useMemo, useState } from "react";
import { getOnboardingBundledSkills, type SkillEntry } from "@/lib/api";
import { useT } from "@/lib/i18n";

/**
 * 新用户模板 — 技能编辑器.
 *
 * 数据源是 hermes/skills/ 的 bundled 集合 (51 个) —— 也就是 hermes 启动时
 * skills_sync 会自动复制到每个新 profile 的那一份。与 admin 或任何
 * profile 的 skills/ 目录无关:admin 自己 hub-installed 或手动加的技能
 * 不会传给新用户,所以这里不能展示。
 *
 * 模板的语义就是默认禁用名单 (skills_disabled):列在里面的 → 新用户
 * config.yaml.skills.disabled 默认禁用;不在里面的 → 默认启用。
 *
 * 顶部统一的「保存模板」按钮经 putOnboardingTemplate 一次性落盘。
 */

interface Props {
  /** 当前被禁用的技能名(模板的 skills_disabled). */
  disabled: string[];
  /** 加载时的基线,用于每张卡片的「已改」黄点. */
  baseline: string[];
  onChange: (next: string[]) => void;
}

export function SkillsTemplateEditor({ disabled, baseline, onChange }: Props) {
  const t = useT();
  const [skills, setSkills] = useState<SkillEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [query, setQuery] = useState("");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getOnboardingBundledSkills()
      .then(r => {
        if (cancelled) return;
        // Bundled endpoint returns just {name, description}; pad to
        // SkillEntry shape. enabled here is meaningless (template uses
        // disabledSet) — pin true so the type checker is happy.
        setSkills((r.skills || []).map(s => ({ ...s, enabled: true })));
        setErr(null);
      })
      .catch(e => { if (!cancelled) setErr(e instanceof Error ? e.message : String(e)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  const disabledSet = useMemo(() => new Set(disabled), [disabled]);
  const baselineSet = useMemo(() => new Set(baseline), [baseline]);

  const enabledCount = useMemo(
    () => skills.filter(s => !disabledSet.has(s.name)).length,
    [skills, disabledSet],
  );

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return skills;
    return skills.filter(s =>
      s.name.toLowerCase().includes(q)
      || (s.description || "").toLowerCase().includes(q),
    );
  }, [skills, query]);

  /** 把一批技能名设为禁用(disable=true)或启用(disable=false). */
  function applyDisabled(names: string[], disable: boolean) {
    const next = new Set(disabled);
    for (const n of names) {
      if (disable) next.add(n);
      else next.delete(n);
    }
    onChange([...next].sort());
  }
  function toggle(name: string) {
    applyDisabled([name], !disabledSet.has(name));
  }
  function setAll(enabled: boolean) {
    applyDisabled(filtered.map(s => s.name), !enabled);
  }
  function invert() {
    const next = new Set(disabled);
    for (const s of filtered) {
      if (next.has(s.name)) next.delete(s.name);
      else next.add(s.name);
    }
    onChange([...next].sort());
  }

  if (loading) {
    return (
      <div style={{ padding: 16 }}>
        <div style={cardStyle}><div className="muted">{t("onboarding.loading")}</div></div>
      </div>
    );
  }
  if (err) {
    return <div style={{ padding: 16 }}><div className="alert alert-error">{err}</div></div>;
  }
  if (skills.length === 0) {
    return (
      <div style={{ padding: 16 }}>
        <div style={cardStyle}>
          <div className="muted">{t("onboarding.skills.empty")}</div>
        </div>
      </div>
    );
  }

  return (
    <div style={{ flex: 1, minHeight: 0, overflow: "auto", padding: 16 }}>
      {/* ── Stats + search + bulk ─────────────────────────── */}
      <div style={{
        ...cardStyle,
        display: "flex", flexWrap: "wrap", alignItems: "center",
        gap: 12, marginBottom: 12, padding: "12px 16px",
      }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 6 }}>
          <span style={{ fontSize: 22, fontWeight: 600, color: "var(--accent-2)" }}>
            {enabledCount}
          </span>
          <span className="muted" style={{ fontSize: 14 }}>
            {t("skills.enabled_of", { total: skills.length })}
          </span>
        </div>

        <div style={{ flex: 1, minWidth: 180 }}>
          <input
            type="search"
            value={query}
            onChange={e => setQuery(e.target.value)}
            placeholder={t("skills.search")}
            style={{
              width: "100%", background: "var(--bg)",
              border: "1px solid var(--border)", borderRadius: 7,
              color: "inherit", padding: "7px 11px", fontSize: 14,
            }}
          />
        </div>

        <div style={{ display: "flex", gap: 6 }}>
          <button onClick={() => setAll(true)} style={ghostBtn}
                  title={t("skills.bulk.enable_all.title")}>
            {t("skills.bulk.enable_all")}
          </button>
          <button onClick={() => setAll(false)} style={ghostBtn}
                  title={t("skills.bulk.disable_all.title")}>
            {t("skills.bulk.disable_all")}
          </button>
          <button onClick={invert} style={ghostBtn}
                  title={t("skills.bulk.invert.title")}>
            {t("skills.bulk.invert")}
          </button>
        </div>
      </div>

      {query && filtered.length === 0 && (
        <div style={{ ...cardStyle, marginBottom: 12 }}>
          <div className="muted">{t("skills.no_match")}</div>
        </div>
      )}

      {/* Bundled skills only — hermes auto-syncs these to every new
          profile. Admin's hub-installed extras don't appear here
          because they don't propagate. */}
      <SectionHeader
        icon="📦"
        title={t("onboarding.skills.builtin")}
        hint={t("onboarding.skills.builtin_hint", { n: filtered.length })}
      />
      <Grid>
        {filtered.map(s => (
          <SkillCard
            key={s.name}
            skill={s}
            enabled={!disabledSet.has(s.name)}
            dirty={baselineSet.has(s.name) !== disabledSet.has(s.name)}
            dirtyTitle={t("skills.dirty_dot")}
            onToggle={() => toggle(s.name)}
          />
        ))}
      </Grid>
    </div>
  );
}

function SectionHeader({ icon, title, hint }: {
  icon: string; title: string; hint?: string;
}) {
  return (
    <div style={{
      display: "flex", alignItems: "baseline", gap: 8,
      margin: "14px 0 8px", fontSize: 14,
    }}>
      <span style={{ fontSize: 16 }}>{icon}</span>
      <span style={{ fontWeight: 600 }}>{title}</span>
      {hint && <span className="muted" style={{ fontSize: 14 }}>· {hint}</span>}
    </div>
  );
}

function Grid({ children }: { children: React.ReactNode }) {
  return (
    <div style={{
      display: "grid",
      gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))",
      gap: 10,
    }}>
      {children}
    </div>
  );
}

function SkillCard({
  skill, enabled, dirty, dirtyTitle, onToggle,
}: {
  skill: SkillEntry;
  enabled: boolean;
  dirty: boolean;
  dirtyTitle: string;
  onToggle: () => void;
}) {
  const isGlobal = skill.origin === "global";
  return (
    <div
      onClick={onToggle}
      style={{
        position: "relative",
        display: "flex", gap: 10, alignItems: "flex-start",
        padding: "12px 14px",
        background: enabled ? "var(--accent-soft)" : "var(--panel)",
        border: "1px solid " + (enabled ? "var(--accent-soft-strong)" : "var(--border)"),
        borderLeft: "3px solid " + (enabled ? "var(--accent)" : "var(--border)"),
        borderRadius: 8,
        cursor: "pointer",
        transition: "background 120ms, border-color 120ms",
        opacity: enabled ? 1 : 0.72,
      }}
    >
      <Toggle on={enabled} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{
          display: "flex", alignItems: "center", gap: 6,
          fontSize: 14, fontWeight: 600,
        }}>
          <span style={{
            fontFamily: "ui-monospace, monospace",
            wordBreak: "break-all",
          }}>{skill.name}</span>
          {isGlobal && (
            <span style={{
              fontSize: 13, padding: "1px 6px", borderRadius: 4,
              background: "var(--accent-soft-strong)", color: "var(--accent-2)",
              fontWeight: 500, flexShrink: 0,
            }}>ORG</span>
          )}
          {dirty && (
            <span
              title={dirtyTitle}
              style={{
                width: 6, height: 6, borderRadius: "50%",
                background: "var(--warning)", flexShrink: 0,
              }}
            />
          )}
        </div>
        {skill.description && (
          <div className="muted" style={{
            fontSize: 14, marginTop: 3, lineHeight: 1.45,
            display: "-webkit-box",
            WebkitLineClamp: 3,
            WebkitBoxOrient: "vertical",
            overflow: "hidden",
          }}>
            {skill.description}
          </div>
        )}
      </div>
    </div>
  );
}

function Toggle({ on }: { on: boolean }) {
  return (
    <div
      aria-hidden
      style={{
        flexShrink: 0, marginTop: 2,
        width: 30, height: 18,
        borderRadius: 999,
        background: on ? "var(--accent)" : "var(--border)",
        position: "relative",
        transition: "background 120ms",
      }}
    >
      <div style={{
        position: "absolute",
        top: 2, left: on ? 14 : 2,
        width: 14, height: 14,
        background: on ? "var(--fg-on-accent)" : "var(--fg-strong)",
        borderRadius: "50%",
        transition: "left 120ms",
      }} />
    </div>
  );
}

const cardStyle: React.CSSProperties = {
  background: "var(--panel)", border: "1px solid var(--border)",
  borderRadius: 10, padding: "16px 18px",
};
const ghostBtn: React.CSSProperties = {
  padding: "6px 12px", fontSize: 14,
  background: "transparent", color: "inherit",
  border: "1px solid var(--border)",
  borderRadius: 6, cursor: "pointer",
};
