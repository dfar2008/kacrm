import { useEffect, useMemo, useState } from "react";
import {
  listWorkspaceFiles, putWorkspaceFile,
  getPersonality, putPersonality,
  WorkspaceFile,
} from "@/lib/api";
import { toast } from "@/lib/toast";
import { useT } from "@/lib/i18n";

/**
 * Workspace tab — file-manager UI over the agent's editable surfaces.
 *
 * Left sidebar lists Personality (agent.system_prompt) plus every .md
 * surface the profile maintains (SOUL.md, memories/MEMORY.md,
 * memories/USER.md, plus any extras hermes drops in). Right side is a
 * per-entry editor with its own dirty tracking and save button.
 *
 * .md files are written back through `PUT /api/workspace/files`
 * (whitelisted to the profile root + memories/, .md paths only).
 * Personality is NOT a file — it's the config.yaml `agent.system_prompt`
 * field, so it gets a synthetic entry backed by get/putPersonality.
 * The list + editor UI is shared; only load and save branch on it.
 */

/** Sentinel path for the synthetic Personality entry. The backend only
 *  ever returns `.md` paths, so this never collides with a real file. */
const PERSONALITY_PATH = "agent.system_prompt";

interface FileState {
  file: WorkspaceFile;
  /** Edited content (diverges from file.content when dirty). */
  draft: string;
}

export function WorkspaceTab({ profileId }: { profileId: string }) {
  const translate = useT();
  const [files, setFiles] = useState<FileState[]>([]);
  const [activePath, setActivePath] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    Promise.all([listWorkspaceFiles(profileId), getPersonality(profileId)])
      .then(([r, p]) => {
        if (cancelled) return;
        // Personality 不是文件,作为合成条目置顶 —— 与 SOUL.md 等并列.
        const personality: FileState = {
          file: {
            name: "Personality",
            path: PERSONALITY_PATH,
            content: p.system_prompt || "",
          },
          draft: p.system_prompt || "",
        };
        const fileList = (r.files || []).map(f => ({ file: f, draft: f.content }));
        const list = [personality, ...fileList];
        setFiles(list);
        setActivePath(prev => prev && list.some(x => x.file.path === prev)
          ? prev
          : (list[0]?.file.path || ""));
        setErr(null);
      })
      .catch(e => { if (!cancelled) setErr(e instanceof Error ? e.message : String(e)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [profileId]);

  const active = useMemo(
    () => files.find(f => f.file.path === activePath) || null,
    [files, activePath],
  );
  const dirty = !!active && active.draft !== active.file.content;

  function updateDraft(path: string, v: string) {
    setFiles(prev => prev.map(f =>
      f.file.path === path ? { ...f, draft: v } : f));
  }

  async function save() {
    if (!active || !dirty) return;
    setSaving(true);
    try {
      let newContent: string;
      if (active.file.path === PERSONALITY_PATH) {
        // Personality 走 config.yaml 的 agent.system_prompt,不是文件接口.
        await putPersonality(profileId, active.draft);
        newContent = active.draft;
      } else {
        const r = await putWorkspaceFile(profileId, active.file.path, active.draft);
        newContent = r.content;
      }
      setFiles(prev => prev.map(f =>
        f.file.path === active.file.path
          ? { file: { ...f.file, content: newContent }, draft: newContent }
          : f));
      toast(translate("workspace.save.ok", { name: active.file.name }), "success");
    } catch (e: any) {
      toast(e instanceof Error ? e.message : String(e), "error");
    } finally { setSaving(false); }
  }

  const dirtyCount = files.filter(f => f.draft !== f.file.content).length;

  return (
    <>
      <div style={{ marginBottom: 16 }}>
        <h2 style={{ margin: 0, fontSize: 20 }}>{translate("workspace.title")}</h2>
        <p className="muted" style={{ margin: "4px 0 0", fontSize: 14 }}>
          {translate("workspace.desc")}
          {dirtyCount > 0 && (
            <span style={{ marginLeft: 8, color: "var(--warning)" }}>
              {" "}{translate("workspace.dirty_count", { n: dirtyCount })}
            </span>
          )}
        </p>
      </div>

      {loading ? (
        <div style={cardStyle}><div className="muted">{translate("workspace.loading")}</div></div>
      ) : err ? (
        <div className="alert alert-error">{err}</div>
      ) : files.length === 0 ? (
        <div style={cardStyle}>
          <div className="muted">{translate("workspace.empty")}</div>
        </div>
      ) : (
        <div style={{
          ...cardStyle, padding: 0, overflow: "hidden",
          display: "grid", gridTemplateColumns: "220px 1fr",
          minHeight: 520,
        }}>
          {/* ── Sidebar ─────────────────────────────────────── */}
          <div style={{
            borderRight: "1px solid var(--border)",
            background: "var(--bg)",
            display: "flex", flexDirection: "column",
            padding: "8px 0",
            overflow: "auto",
          }}>
            {files.map(f => {
              const isActive = f.file.path === activePath;
              const isDirty = f.draft !== f.file.content;
              return (
                <button
                  key={f.file.path}
                  onClick={() => setActivePath(f.file.path)}
                  style={{
                    display: "flex", alignItems: "center", gap: 8,
                    padding: "8px 14px",
                    textAlign: "left",
                    background: isActive ? "var(--panel-2)" : "transparent",
                    color: isActive ? "var(--fg-strong)" : "var(--muted)",
                    border: "none",
                    borderLeft: `3px solid ${isActive ? "var(--accent)" : "transparent"}`,
                    cursor: "pointer",
                    fontSize: 14,
                    fontFamily: "ui-monospace, monospace",
                    transition: "background 120ms",
                  }}
                  onMouseEnter={e => { if (!isActive) e.currentTarget.style.background = "var(--panel)"; }}
                  onMouseLeave={e => { if (!isActive) e.currentTarget.style.background = "transparent"; }}
                >
                  <span style={{ flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {f.file.name}
                  </span>
                  {isDirty && (
                    <span
                      title={translate("workspace.unsaved_dot")}
                      style={{
                        width: 6, height: 6, borderRadius: "50%",
                        background: "var(--warning)", flexShrink: 0,
                      }}
                    />
                  )}
                </button>
              );
            })}
          </div>

          {/* ── Editor ──────────────────────────────────────── */}
          <div style={{ display: "flex", flexDirection: "column", minWidth: 0 }}>
            {active && (
              <>
                <div style={{
                  padding: "10px 16px",
                  display: "flex", alignItems: "center", gap: 12,
                  borderBottom: "1px solid var(--border)",
                  background: "var(--bg)",
                }}>
                  <div style={{ minWidth: 0, flex: 1 }}>
                    <div style={{
                      fontSize: 14, fontWeight: 600,
                      fontFamily: "ui-monospace, monospace",
                    }}>{active.file.name}</div>
                    <div className="muted" style={{
                      fontSize: 13, marginTop: 1,
                      fontFamily: "ui-monospace, monospace",
                    }}>{active.file.path}</div>
                  </div>
                  <button
                    onClick={save}
                    disabled={saving || !dirty}
                    style={{ ...primaryBtn, opacity: saving || !dirty ? 0.5 : 1 }}
                  >
                    {saving ? translate("workspace.save.saving") : translate("workspace.save.label")}
                  </button>
                </div>

                <textarea
                  value={active.draft}
                  onChange={e => updateDraft(active.file.path, e.target.value)}
                  placeholder={translate("workspace.placeholder", { name: active.file.name })}
                  spellCheck={false}
                  style={{
                    flex: 1,
                    minHeight: 400,
                    width: "100%",
                    boxSizing: "border-box",
                    background: "var(--bg)",
                    color: "inherit",
                    border: "none",
                    outline: "none",
                    padding: "14px 16px",
                    resize: "none",
                    fontFamily: "ui-monospace, monospace",
                    fontSize: 14,
                    lineHeight: 1.55,
                  }}
                />
              </>
            )}
          </div>
        </div>
      )}
    </>
  );
}

const cardStyle: React.CSSProperties = {
  background: "var(--panel)", border: "1px solid var(--border)",
  borderRadius: 10, padding: "16px 18px",
};
const primaryBtn: React.CSSProperties = {
  padding: "6px 16px", fontSize: 14,
  background: "var(--accent)", color: "var(--fg-on-accent)",
  border: "none", borderRadius: 6, cursor: "pointer",
  whiteSpace: "nowrap",
};
