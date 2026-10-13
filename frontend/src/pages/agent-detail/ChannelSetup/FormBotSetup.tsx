import { useCallback, useEffect, useState } from "react";
import {
  deleteProfileChannel, getProfileChannel, putProfileChannel,
  testProfileChannel, type ChannelEntry,
} from "@/lib/api";
import { toast } from "@/lib/toast";

/**
 * Generic form-paste channel setup for platforms without QR
 * provisioning: WeCom, Telegram, Discord, Slack, Dingtalk.
 *
 * Each channel passes its field spec — label, type (text/password),
 * placeholder, optional help link. The component handles read-then-
 * edit, masked-secret round-trip, validation errors from the API
 * (rendered inline), Test Connection, and Disconnect.
 */
export interface FormFieldPreset {
  /** Stable id for React keys — never persisted (only the resolved text is). */
  id: string;
  label: string;
  text: string;
}

export interface FormField {
  /** Storage key — matches the API's expected ``extra`` field name. */
  name: string;
  label: string;
  type?: "text" | "password";
  placeholder?: string;
  required?: boolean;
  /** Help text shown beneath the input. */
  hint?: string;
  /** Render as a multi-line textarea instead of a single-line input. */
  multiline?: boolean;
  /** If provided, show a preset picker that fills the textarea on select. */
  presets?: FormFieldPreset[];
}

interface Props {
  profileId: string;
  channel: string;
  /** SPA-facing channel name, e.g. "Telegram", "WeCom 企微" */
  channelDisplay: string;
  /** Link to the platform's developer-portal credential page. */
  setupGuideUrl?: string;
  fields: FormField[];
  onChange?: () => void;
}

export function FormBotSetup({
  profileId, channel, channelDisplay,
  setupGuideUrl, fields, onChange,
}: Props) {
  const [entry, setEntry] = useState<ChannelEntry | null>(null);
  const [loading, setLoading] = useState(true);
  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [formValues, setFormValues] = useState<Record<string, string>>({});

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const r = await getProfileChannel(profileId, channel);
      setEntry(r);
      // Pre-populate form with masked values so users see "is
      // configured" without retyping. Masked values (●●●●●●) are
      // stripped on save by the API's "skip masked" logic.
      setFormValues(r.extra || {});
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [profileId, channel]);

  useEffect(() => {
    reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [profileId, channel]);

  const onSubmit = useCallback(async () => {
    if (saving) return;
    // Strip masked values — caller didn't actually retype them, so
    // we don't send them (API's merge keeps the real value on disk).
    const payload: Record<string, string> = {};
    for (const k of Object.keys(formValues)) {
      const v = formValues[k];
      if (typeof v === "string" && !v.startsWith("●●●●")) {
        payload[k] = v;
      }
    }
    setSaving(true);
    try {
      await putProfileChannel(profileId, channel, payload);
      toast(`${channelDisplay} 配置已保存`);
      await reload();
      onChange?.();
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      toast("保存失败：" + msg, "error");
      setError(msg);
    } finally {
      setSaving(false);
    }
  }, [saving, profileId, channel, channelDisplay, formValues, reload, onChange]);

  const onTest = useCallback(async () => {
    setTesting(true);
    try {
      const r = await testProfileChannel(profileId, channel);
      if (r.ok) {
        toast(r.bot_name ? `连接正常 (${r.bot_name})` : "连接正常");
      } else {
        toast("连接失败：" + r.error, "error");
      }
    } catch (e) {
      toast("测试失败：" + (e instanceof Error ? e.message : String(e)), "error");
    } finally {
      setTesting(false);
    }
  }, [profileId, channel]);

  const onDisconnect = useCallback(async () => {
    if (!window.confirm(
      `断开 ${channelDisplay}？现有对话历史会保留，但 bot 不再收发消息。`
    )) return;
    try {
      await deleteProfileChannel(profileId, channel);
      toast("已断开");
      setFormValues({});
      await reload();
      onChange?.();
    } catch (e) {
      toast("断开失败：" + (e instanceof Error ? e.message : String(e)), "error");
    }
  }, [profileId, channel, channelDisplay, reload, onChange]);

  if (loading) {
    return (
      <div className="muted" style={{ padding: 16 }}>加载中…</div>
    );
  }

  const configured = entry?.configured;

  return (
    <div style={{
      padding: 20, border: "1px solid var(--border)", borderRadius: 8,
      background: "var(--surface)",
    }}>
      <Header configured={configured} channelDisplay={channelDisplay} />

      {setupGuideUrl && !configured && (
        <details style={{ marginBottom: 12 }}>
          <summary style={{ cursor: "pointer", color: "var(--muted)", fontSize: 14 }}>
            如何获取凭证？
          </summary>
          <div style={{
            padding: "8px 12px", marginTop: 8, background: "var(--surface-inset)",
            borderRadius: 4, fontSize: 14,
          }}>
            前往{" "}
            <a href={setupGuideUrl} target="_blank" rel="noreferrer">
              {channelDisplay} 开发者后台
            </a>
            {" "}，按提示创建应用并获取下面所需的字段。
          </div>
        </details>
      )}

      {error && (
        <div style={{
          color: "var(--err)", marginBottom: 12, fontSize: 14,
          padding: "8px 12px", background: "var(--danger-soft)", borderRadius: 4,
        }}>
          ⚠️ {error}
        </div>
      )}

      {fields.map((f) => (
        <div key={f.name} style={{ marginBottom: 10 }}>
          <label style={{
            display: "block", fontSize: 14, color: "var(--muted)", marginBottom: 4,
          }}>
            {f.label}{f.required && " *"}
          </label>

          {f.presets && f.presets.length > 0 && (
            <select
              value=""
              onChange={(e) => {
                const id = e.target.value;
                if (!id) return;
                if (id === "__custom__") {
                  setFormValues({ ...formValues, [f.name]: "" });
                  return;
                }
                const preset = f.presets!.find((p) => p.id === id);
                if (preset) {
                  setFormValues({ ...formValues, [f.name]: preset.text });
                }
              }}
              style={{
                width: "100%", padding: "6px 10px", marginBottom: 6,
                border: "1px solid var(--border)", borderRadius: 4,
                background: "var(--surface-inset)", color: "var(--text)",
              }}
            >
              <option value="">— 选一个 preset 风格填入下方 —</option>
              {f.presets.map((p) => (
                <option key={p.id} value={p.id}>{p.label}</option>
              ))}
              <option value="__custom__">自定义(清空)</option>
            </select>
          )}

          {f.multiline ? (
            <textarea
              value={formValues[f.name] || ""}
              onChange={(e) => setFormValues({
                ...formValues, [f.name]: e.target.value,
              })}
              placeholder={f.placeholder}
              rows={5}
              style={{
                width: "100%", padding: "6px 10px",
                border: "1px solid var(--border)", borderRadius: 4,
                background: "var(--surface-inset)", color: "var(--text)",
                fontFamily: "inherit", resize: "vertical",
              }}
            />
          ) : (
            <input
              type={f.type || "text"}
              value={formValues[f.name] || ""}
              onChange={(e) => setFormValues({
                ...formValues, [f.name]: e.target.value,
              })}
              placeholder={f.placeholder}
              style={{
                width: "100%", padding: "6px 10px",
                border: "1px solid var(--border)", borderRadius: 4,
                background: "var(--surface-inset)", color: "var(--text)",
                fontFamily: f.type === "password" ? "monospace" : "inherit",
              }}
            />
          )}

          {f.hint && (
            <div style={{ fontSize: 13, color: "var(--muted)", marginTop: 2 }}>
              {f.hint}
            </div>
          )}
        </div>
      ))}

      <div style={{ display: "flex", gap: 8, marginTop: 16 }}>
        <button className="btn btn-primary" onClick={onSubmit} disabled={saving}>
          {saving ? "保存中…" : (configured ? "更新" : "保存")}
        </button>
        {configured && (
          <>
            {entry?.supports_probe && (
              <button className="btn" onClick={onTest} disabled={testing || saving}>
                {testing ? "测试中…" : "测试连接"}
              </button>
            )}
            <button className="btn btn-danger" onClick={onDisconnect} disabled={saving}>
              断开
            </button>
          </>
        )}
      </div>
    </div>
  );
}

function Header({
  configured, channelDisplay,
}: { configured?: boolean; channelDisplay: string }) {
  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 8, marginBottom: 16,
    }}>
      <span style={{
        display: "inline-block", width: 8, height: 8, borderRadius: "50%",
        background: configured ? "var(--success)" : "var(--muted)",
      }} />
      <span style={{ fontWeight: 600 }}>
        {configured ? "已连接" : "未连接"}
      </span>
      <span style={{ color: "var(--muted)" }}>· {channelDisplay}</span>
    </div>
  );
}
