import { useEffect, useMemo, useState } from "react";
import { getSkills, saveSkills, isSkillLocked, type SkillEntry } from "@/lib/api";
import { toast } from "@/lib/toast";
import { useT, type T } from "@/lib/i18n";

/**
 * Skills tab — private (agent-learned) + global (org-shared).
 */

type Skill = SkillEntry & { enabled: boolean };

export function SkillsTab({ profileId }: { profileId: string }) {
  const translate = useT();
  const [skills, setSkills] = useState<Skill[]>([]);
  const [original, setOriginal] = useState<Record<string, boolean>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [query, setQuery] = useState("");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getSkills(profileId)
      .then(r => {
        if (cancelled) return;
        const priv = (r.private ?? []).map(s => ({ ...s, origin: "private" as const }));
        const glob = (r.global ?? []).map(s => ({ ...s, origin: "global" as const }));
        const combined: Skill[] = (priv.length || glob.length)
          ? [...priv, ...glob]
          : (r.skills || []);
        setSkills(combined);
        setOriginal(Object.fromEntries(
          combined.map(s => [s.name, s.enabled]),
        ));
        setErr(null);
      })
      .catch(e => { if (!cancelled) setErr(e instanceof Error ? e.message : String(e)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [profileId]);

  const enabledCount = useMemo(() => skills.filter(s => s.enabled).length, [skills]);
  const dirtyCount = useMemo(
    () => skills.reduce((n, s) => n + (original[s.name] === s.enabled ? 0 : 1), 0),
    [skills, original],
  );

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return skills;
    return skills.filter(s =>
      s.name.toLowerCase().includes(q)
      || (s.description || "").toLowerCase().includes(q),
    );
  }, [skills, query]);

  function toggle(name: string) {
    const cur = skills.find(s => s.name === name);
    // 系统默认 CRM 技能只增不减(后端 skill_policy 同样强制)
    if (cur && cur.enabled && isSkillLocked(cur)) {
      toast(translate("skills.locked_toast"), "info");
      return;
    }
    setSkills(prev => prev.map(s =>
      s.name === name ? { ...s, enabled: !s.enabled } : s,
    ));
  }
  function setAll(enabled: boolean) {
    const targets = new Set(filtered.map(s => s.name));
    setSkills(prev => prev.map(s => targets.has(s.name)
      ? { ...s, enabled: enabled || (s.enabled && isSkillLocked(s)) }
      : s));
  }
  function invert() {
    const targets = new Set(filtered.map(s => s.name));
    setSkills(prev => prev.map(s => targets.has(s.name)
      ? { ...s, enabled: (s.enabled && isSkillLocked(s)) ? true : !s.enabled }
      : s));
  }
  function reset() {
    setSkills(prev => prev.map(s => ({ ...s, enabled: original[s.name] ?? s.enabled })));
  }

  async function save() {
    setSaving(true);
    try {
      const enabled = skills.filter(s => s.enabled).map(s => s.name);
      await saveSkills(profileId, enabled);
      setOriginal(Object.fromEntries(skills.map(s => [s.name, s.enabled])));
      toast(translate("skills.saved_toast"), "success");
    } catch (e: any) {
      toast(e instanceof Error ? e.message : String(e), "error");
    } finally { setSaving(false); }
  }

  return (
    <>
      <div style={{ marginBottom: 16 }}>
        <h2 style={{ margin: 0, fontSize: 20 }}>{translate("skills.title")}</h2>
        <p className="muted" style={{ margin: "4px 0 0", fontSize: 14 }}>
          {translate("skills.desc")}
        </p>
      </div>

      {loading ? (
        <div style={cardStyle}><div className="muted">{translate("skills.loading")}</div></div>
      ) : err ? (
        <div className="alert alert-error">{err}</div>
      ) : skills.length === 0 ? (
        <div style={cardStyle}>
          <div className="muted">{translate("skills.empty")}</div>
        </div>
      ) : (
        <>
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
                {translate("skills.enabled_of", { total: skills.length })}
              </span>
            </div>

            <div style={{ flex: 1, minWidth: 180 }}>
              <input
                type="search"
                value={query}
                onChange={e => setQuery(e.target.value)}
                placeholder={translate("skills.search")}
                style={{
                  width: "100%",
                  background: "var(--bg)",
                  border: "1px solid var(--border)",
                  borderRadius: 7,
                  color: "inherit",
                  padding: "7px 11px",
                  fontSize: 14,
                }}
              />
            </div>

            <div style={{ display: "flex", gap: 6 }}>
              <button onClick={() => setAll(true)} style={ghostBtn} title={translate("skills.bulk.enable_all.title")}>
                {translate("skills.bulk.enable_all")}
              </button>
              <button onClick={() => setAll(false)} style={ghostBtn} title={translate("skills.bulk.disable_all.title")}>
                {translate("skills.bulk.disable_all")}
              </button>
              <button onClick={invert} style={ghostBtn} title={translate("skills.bulk.invert.title")}>
                {translate("skills.bulk.invert")}
              </button>
            </div>
          </div>

          {query && filtered.length === 0 && (
            <div style={{ ...cardStyle, marginBottom: 12 }}>
              <div className="muted">{translate("skills.no_match")}</div>
            </div>
          )}

          {(() => {
            const priv = filtered.filter(s => (s.origin ?? "private") === "private");
            const glob = filtered.filter(s => s.origin === "global");
            return (
              <div style={{ marginBottom: 90 }}>
                {priv.length > 0 && (
                  <SectionHeader
                    icon="🧠"
                    title={translate("skills.section.private")}
                    hint={translate("skills.section.private.hint", { n: priv.length })}
                  />
                )}
                {priv.length > 0 && (
                  <Grid>
                    {priv.map(s => (
                      <SkillCard
                        key={s.name}
                        skill={s}
                        dirty={original[s.name] !== undefined && original[s.name] !== s.enabled}
                        onToggle={() => toggle(s.name)}
                        t={translate}
                      />
                    ))}
                  </Grid>
                )}
                {glob.length > 0 && (
                  <SectionHeader
                    icon="🏢"
                    title={translate("skills.section.global")}
                    hint={translate("skills.section.global.hint", { n: glob.length })}
                  />
                )}
                {glob.length > 0 && (
                  <Grid>
                    {glob.map(s => (
                      <SkillCard
                        key={s.name}
                        skill={s}
                        dirty={original[s.name] !== undefined && original[s.name] !== s.enabled}
                        onToggle={() => toggle(s.name)}
                        t={translate}
                      />
                    ))}
                  </Grid>
                )}
              </div>
            );
          })()}

          <div style={{
            position: "sticky", bottom: 0, marginTop: -90,
            display: "flex", alignItems: "center", gap: 12,
            padding: "12px 16px",
            background: "var(--panel)",
            border: "1px solid var(--border)",
            borderRadius: 10,
            boxShadow: "0 -4px 16px rgba(0,0,0,0.25)", // theme-guard:ignore
          }}>
            <div style={{ flex: 1, fontSize: 14 }}>
              {dirtyCount === 0 ? (
                <span className="muted">{translate("skills.clean")}</span>
              ) : (
                <span>
                  <strong style={{ color: "var(--warning)" }}>{dirtyCount}</strong>
                  <span className="muted"> {translate("skills.dirty")}</span>
                </span>
              )}
            </div>
            {dirtyCount > 0 && (
              <button onClick={reset} style={ghostBtn} disabled={saving}>
                {translate("skills.reset")}
              </button>
            )}
            <button
              onClick={save}
              disabled={saving || dirtyCount === 0}
              style={{ ...primaryBtn, opacity: saving || dirtyCount === 0 ? 0.5 : 1 }}
            >
              {saving ? translate("skills.save.saving") : translate("skills.save.label")}
            </button>
          </div>
        </>
      )}
    </>
  );
}

function SectionHeader({ icon, title, hint }: {
  icon: string; title: string; hint?: string;
}) {
  return (
    <div style={{
      display: "flex", alignItems: "baseline", gap: 8,
      margin: "14px 0 8px",
      fontSize: 14,
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
  skill, dirty, onToggle, t: translate,
}: {
  skill: Skill; dirty: boolean; onToggle: () => void; t: T;
}) {
  const isGlobal = skill.origin === "global";
  const locked = skill.enabled && isSkillLocked(skill);
  return (
    <div
      onClick={onToggle}
      title={locked ? translate("skills.locked_toast") : undefined}
      style={{
        position: "relative",
        display: "flex", gap: 10, alignItems: "flex-start",
        padding: "12px 14px",
        background: skill.enabled ? "var(--panel-2)" : "var(--bg)",
        border: "1px solid " + (skill.enabled ? "var(--accent)" : "var(--border)"),
        borderLeft: "3px solid " + (skill.enabled ? "var(--accent)" : "var(--border)"),
        borderRadius: 8,
        cursor: locked ? "default" : "pointer",
        transition: "background 120ms, border-color 120ms",
        opacity: skill.enabled ? 1 : 0.72,
      }}
    >
      <Toggle on={skill.enabled} />
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
              background: "var(--accent-soft)", color: "var(--accent-2)",
              fontWeight: 500, flexShrink: 0,
            }}>ORG</span>
          )}
          {locked && (
            <span style={{
              fontSize: 13, padding: "1px 6px", borderRadius: 4,
              border: "1px solid var(--border)", color: "var(--muted)",
              fontWeight: 500, flexShrink: 0,
            }}>{translate("skills.locked_badge")}</span>
          )}
          {dirty && (
            <span
              title={translate("skills.dirty_dot")}
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
const primaryBtn: React.CSSProperties = {
  padding: "8px 18px", fontSize: 14,
  background: "var(--accent)", color: "var(--fg-on-accent)",
  border: "none", borderRadius: 7, cursor: "pointer",
};
const ghostBtn: React.CSSProperties = {
  padding: "6px 12px", fontSize: 14,
  background: "transparent", color: "inherit",
  border: "1px solid var(--border)",
  borderRadius: 6, cursor: "pointer",
};
