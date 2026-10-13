import { useState } from "react";
import { useRouter } from "@/lib/router";
import {
  patchAgentProfile, deleteAgentProfile,
  AgentProfile,
} from "@/lib/api";
import { toast } from "@/lib/toast";
import { useT } from "@/lib/i18n";

/**
 * Overview tab — basic profile info.
 *
 * Personality (agent.system_prompt) used to live here as a second card,
 * but it now sits in the Workspace tab next to SOUL.md and the memory
 * files, so this tab is just the basic-info card.
 *
 * Charter v2 = 1 user 1 profile, and nothing addresses a profile by a
 * display name or slug — the profile id is the only identifier that
 * matters. So the id is shown read-only and only the description is
 * editable here.
 */

export function OverviewTab({
  profile, onRefresh,
}: { profile: AgentProfile; onRefresh: () => void }) {
  const t = useT();
  return (
    <>
      <div style={{ marginBottom: 16 }}>
        <h2 style={{ margin: 0, fontSize: 20 }}>{t("overview.title")}</h2>
        <p className="muted" style={{ margin: "4px 0 0", fontSize: 14 }}>
          {t("overview.desc")}
        </p>
      </div>

      <BasicInfoCard profile={profile} onRefresh={onRefresh} />
    </>
  );
}

// ── Basic info ──────────────────────────────────────────────────────
function BasicInfoCard({
  profile, onRefresh,
}: { profile: AgentProfile; onRefresh: () => void }) {
  const t = useT();
  const { navigate } = useRouter();
  const [description, setDescription] = useState(profile.description || "");
  const [saving, setSaving] = useState(false);

  const dirty = description !== (profile.description || "");

  async function save() {
    if (!dirty) { toast(t("overview.err.no_change"), "info"); return; }
    setSaving(true);
    try {
      await patchAgentProfile(profile.id, { description });
      toast(t("overview.saved"), "success");
      onRefresh();
    } catch (e: any) { toast(e.message, "error"); }
    finally { setSaving(false); }
  }
  async function setDefault() {
    setSaving(true);
    try {
      await patchAgentProfile(profile.id, { is_default: true } as any);
      toast(t("overview.set_default_ok"), "success");
      onRefresh();
    } catch (e: any) { toast(e.message, "error"); }
    finally { setSaving(false); }
  }
  async function remove() {
    if (!confirm(t("overview.confirm_delete", { name: profile.name }))) return;
    try {
      await deleteAgentProfile(profile.id);
      toast(t("overview.deleted"), "success");
      navigate("/agents");
    } catch (e: any) { toast(e.message, "error"); }
  }

  return (
    <div style={cardStyle}>
      <div style={{ display: "grid", gap: 14 }}>
        <Field label={t("overview.field.name")}>
          <input value={profile.id} readOnly
                 style={{
                   ...inputStyle,
                   fontFamily: "ui-monospace, monospace",
                   color: "var(--muted)", cursor: "default",
                 }} />
        </Field>
        <Field label={t("overview.field.desc")}>
          <textarea rows={3} value={description}
                    placeholder={t("overview.field.desc_placeholder")}
                    style={{ ...inputStyle, resize: "vertical", lineHeight: 1.5 }}
                    onChange={e => setDescription(e.target.value)} />
        </Field>

        <div style={{
          display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap",
          paddingTop: 6, borderTop: "1px dashed var(--border)",
        }}>
          {!profile.is_default && (
            <>
              <button style={dangerBtn} onClick={remove}>{t("overview.delete")}</button>
              <button style={ghostBtn} onClick={setDefault} disabled={saving}>
                {t("overview.set_default")}
              </button>
            </>
          )}
          <div style={{ flex: 1 }} />
          <button
            style={{ ...primaryBtn, opacity: saving || !dirty ? 0.5 : 1 }}
            onClick={save}
            disabled={saving || !dirty}
          >
            {saving ? t("overview.saving") : dirty ? t("overview.save") : t("overview.already_saved")}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Shared little bits ───────────────────────────────────────────────
function Field({
  label, hint, children,
}: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <span style={{ fontSize: 14, color: "var(--muted)" }}>{label}</span>
      {children}
      {hint && (
        <span className="muted" style={{ fontSize: 13 }}>{hint}</span>
      )}
    </label>
  );
}

const cardStyle: React.CSSProperties = {
  background: "var(--panel)", border: "1px solid var(--border)",
  borderRadius: 10, padding: "16px 18px",
};
const inputStyle: React.CSSProperties = {
  width: "100%",
  background: "var(--bg)",
  border: "1px solid var(--border)",
  borderRadius: 7,
  color: "inherit",
  padding: "8px 11px",
  fontSize: 14,
};
const primaryBtn: React.CSSProperties = {
  padding: "8px 18px", fontSize: 14,
  background: "var(--accent)", color: "var(--fg-on-accent)",
  border: "none", borderRadius: 7, cursor: "pointer",
  whiteSpace: "nowrap",
};
const ghostBtn: React.CSSProperties = {
  padding: "6px 12px", fontSize: 14,
  background: "transparent", color: "inherit",
  border: "1px solid var(--border)",
  borderRadius: 6, cursor: "pointer",
  whiteSpace: "nowrap",
};
const dangerBtn: React.CSSProperties = {
  ...ghostBtn, color: "var(--danger)", borderColor: "var(--danger)",
};
