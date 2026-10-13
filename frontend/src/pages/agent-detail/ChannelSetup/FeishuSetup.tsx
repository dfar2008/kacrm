import { useCallback, useEffect, useRef, useState } from "react";
import { QRCodeSVG } from "qrcode.react";
import {
  deleteProfileChannel, getProfileChannel,
  pollChannelQR, putProfileChannel,
  startChannelQR, testProfileChannel,
  type ChannelEntry, type QRPollStatus,
  ApiError,
} from "@/lib/api";
import { toast } from "@/lib/toast";

function friendlyQRError(e: unknown): string {
  if (e instanceof ApiError && e.detail && typeof e.detail === "object") {
    const code = (e.detail as { error?: { code?: string } })?.error?.code;
    if (code === "qr_session_not_found") return "二维码会话已过期，请点击重试重新扫码";
    if (code === "qr_session_mismatch") return "二维码会话信息不匹配，请点击重试";
  }
  return e instanceof Error ? e.message : String(e);
}

/**
 * Per-profile Feishu (Lark) bot setup.
 *
 * Two onboarding paths:
 *   1. **Scan** (recommended): user scans a QR code with Feishu mobile;
 *      Feishu auto-provisions a "PersonalAgent" bot app and returns
 *      its credentials. Zero IT involvement.
 *   2. **Manual**: user pastes app_id + app_secret from a self-built
 *      app they already have. Useful for shared org bots or when QR
 *      provisioning is restricted by tenant admin.
 *
 * After successful connect, shows status + Test Connection + Disconnect.
 * Hermes' next inbound message picks up the new creds automatically
 * (config.yaml is re-read on every request).
 */
type Phase =
  | { kind: "loading" }
  | { kind: "empty" }
  | { kind: "qr"; sessionId: string; qrUrl: string; userCode?: string; expiresAt: number }
  | { kind: "manual" }
  | { kind: "connected"; entry: ChannelEntry }
  | { kind: "error"; message: string };

interface Props {
  profileId: string;
  onChange?: () => void;
}

export function FeishuSetup({ profileId, onChange }: Props) {
  const [phase, setPhase] = useState<Phase>({ kind: "loading" });
  const [testing, setTesting] = useState(false);
  const [domain, setDomain] = useState<"feishu" | "lark">("feishu");
  const pollTimer = useRef<number | null>(null);
  const expiryTimer = useRef<number | null>(null);
  // Set once a poll returns a terminal status (completed/denied/expired/error)
  // so in-flight racing polls don't override the final UI state.
  const terminalReached = useRef(false);

  const clearTimers = useCallback(() => {
    if (pollTimer.current !== null) {
      window.clearInterval(pollTimer.current);
      pollTimer.current = null;
    }
    if (expiryTimer.current !== null) {
      window.clearInterval(expiryTimer.current);
      expiryTimer.current = null;
    }
  }, []);

  const reload = useCallback(async () => {
    try {
      const entry = await getProfileChannel(profileId, "feishu");
      if (entry.configured) {
        setPhase({ kind: "connected", entry });
      } else {
        setPhase({ kind: "empty" });
      }
    } catch (e) {
      setPhase({
        kind: "error",
        message: e instanceof Error ? e.message : String(e),
      });
    }
  }, [profileId]);

  useEffect(() => {
    reload();
    return clearTimers;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [profileId]);

  // ── QR scan flow ────────────────────────────────────────────────────
  const startScan = useCallback(async () => {
    clearTimers();
    terminalReached.current = false;
    setPhase({ kind: "loading" });
    try {
      const r = await startChannelQR(profileId, "feishu", { domain });
      setPhase({
        kind: "qr",
        sessionId: r.session_id,
        qrUrl: r.qr_url,
        userCode: r.user_code,
        expiresAt: r.expires_at,
      });
      pollTimer.current = window.setInterval(async () => {
        if (terminalReached.current) return;
        try {
          const status: QRPollStatus = await pollChannelQR(
            profileId, "feishu", r.session_id,
          );
          if (terminalReached.current) return;
          if (status.status === "completed") {
            terminalReached.current = true;
            clearTimers();
            toast("飞书 bot 连接成功");
            await reload();
            onChange?.();
          } else if (status.status === "denied") {
            terminalReached.current = true;
            clearTimers();
            setPhase({ kind: "error", message: "您在手机上拒绝了授权" });
          } else if (status.status === "expired") {
            terminalReached.current = true;
            clearTimers();
            setPhase({ kind: "error", message: "二维码已过期，请重新扫码" });
          } else if (status.status === "error") {
            terminalReached.current = true;
            clearTimers();
            setPhase({ kind: "error", message: status.message });
          }
          // pending / scanned — keep polling
        } catch (e) {
          if (terminalReached.current) return;
          terminalReached.current = true;
          clearTimers();
          setPhase({ kind: "error", message: friendlyQRError(e) });
        }
      }, Math.max(2000, r.interval * 1000));
      // Force re-render on countdown so the SPA shows time-to-expiry
      expiryTimer.current = window.setInterval(() => {
        setPhase((p) => (p.kind === "qr" ? { ...p } : p));
      }, 1000);
    } catch (e) {
      setPhase({
        kind: "error",
        message: e instanceof Error ? e.message : String(e),
      });
    }
  }, [profileId, domain, reload, onChange, clearTimers]);

  // ── Manual paste flow ───────────────────────────────────────────────
  const [manualAppId, setManualAppId] = useState("");
  const [manualAppSecret, setManualAppSecret] = useState("");
  const [manualEncryptKey, setManualEncryptKey] = useState("");
  const [manualVerifyToken, setManualVerifyToken] = useState("");

  const submitManual = useCallback(async () => {
    if (!manualAppId.trim() || !manualAppSecret.trim()) {
      toast("App ID 和 App Secret 都是必填项", "error");
      return;
    }
    try {
      await putProfileChannel(profileId, "feishu", {
        app_id: manualAppId.trim(),
        app_secret: manualAppSecret.trim(),
        encrypt_key: manualEncryptKey.trim(),
        verification_token: manualVerifyToken.trim(),
        domain,
        connection_mode: "websocket",
      });
      toast("飞书 bot 配置已保存");
      setManualAppId("");
      setManualAppSecret("");
      setManualEncryptKey("");
      setManualVerifyToken("");
      await reload();
      onChange?.();
    } catch (e) {
      toast(
        "保存失败：" + (e instanceof Error ? e.message : String(e)),
      );
    }
  }, [
    profileId, manualAppId, manualAppSecret, manualEncryptKey,
    manualVerifyToken, domain, reload, onChange,
  ]);

  // ── Connected actions ──────────────────────────────────────────────
  const handleTest = useCallback(async () => {
    setTesting(true);
    try {
      const r = await testProfileChannel(profileId, "feishu");
      if (r.ok) {
        toast(
          r.bot_name ? `连接正常 (${r.bot_name})` : "连接正常",
        );
      } else {
        toast("连接失败：" + r.error, "error");
      }
    } catch (e) {
      toast(
        "测试失败：" + (e instanceof Error ? e.message : String(e)),
      );
    } finally {
      setTesting(false);
    }
  }, [profileId]);

  const handleDisconnect = useCallback(async () => {
    if (!window.confirm(
      "断开飞书 bot 连接？现有的对话历史会保留，但 bot 不再收发消息。"
    )) return;
    try {
      await deleteProfileChannel(profileId, "feishu");
      toast("已断开");
      await reload();
      onChange?.();
    } catch (e) {
      toast(
        "断开失败：" + (e instanceof Error ? e.message : String(e)),
      );
    }
  }, [profileId, reload, onChange]);

  // ── Render ─────────────────────────────────────────────────────────
  if (phase.kind === "loading") {
    return <div className="muted" style={{ padding: 16 }}>加载中…</div>;
  }

  if (phase.kind === "error") {
    return (
      <Box>
        <div style={{ color: "var(--err)", marginBottom: 12 }}>
          ⚠️ {phase.message}
        </div>
        <button className="btn" onClick={reload}>重试</button>
      </Box>
    );
  }

  if (phase.kind === "connected") {
    const { entry } = phase;
    return (
      <Box>
        <Header
          status="connected"
          subtitle={entry.bot_name || "飞书 bot 已连接"}
        />
        <Field label="App ID" value={entry.extra?.app_id || "—"} />
        <Field
          label="App Secret"
          value={entry.extra?.app_secret || "—"}
          mono
        />
        <Field label="Domain" value={entry.extra?.domain || "feishu"} />
        <div style={{ display: "flex", gap: 8, marginTop: 16 }}>
          {entry.supports_probe && (
            <button
              className="btn"
              onClick={handleTest}
              disabled={testing}
            >
              {testing ? "测试中…" : "测试连接"}
            </button>
          )}
          <button
            className="btn btn-danger"
            onClick={handleDisconnect}
          >
            断开
          </button>
        </div>
      </Box>
    );
  }

  if (phase.kind === "qr") {
    const secondsLeft = Math.max(
      0, Math.floor(phase.expiresAt - Date.now() / 1000),
    );
    return (
      <Box>
        <Header
          status="qr"
          subtitle={`请在 ${secondsLeft} 秒内用飞书 App 扫码`}
        />
        <div style={{
          display: "flex", flexDirection: "column", alignItems: "center",
          // theme-guard:ignore-start
          padding: 24, background: "#fff", borderRadius: 8,
          // theme-guard:ignore-end
        }}>
          <QRCodeSVG value={phase.qrUrl} size={240} level="M" />
          {phase.userCode && (
            <div style={{ marginTop: 12, fontFamily: "monospace", color: "var(--muted)" }}>
              确认码：{phase.userCode}
            </div>
          )}
          <a
            href={phase.qrUrl}
            target="_blank"
            rel="noreferrer"
            style={{ marginTop: 12, fontSize: 14 }}
          >
            或点这里在浏览器打开
          </a>
        </div>
        <div style={{ marginTop: 16 }}>
          <button
            className="btn"
            onClick={() => {
              clearTimers();
              setPhase({ kind: "empty" });
            }}
          >
            取消
          </button>
        </div>
      </Box>
    );
  }

  // empty | manual
  const isManual = phase.kind === "manual";
  return (
    <Box>
      <Header
        status="empty"
        subtitle="为此 profile 接入专属飞书 bot"
      />
      <div style={{ display: "flex", gap: 8, marginBottom: 16, alignItems: "center" }}>
        <label style={{ fontSize: 14 }}>Domain：</label>
        <select
          value={domain}
          onChange={(e) => setDomain(e.target.value as "feishu" | "lark")}
          style={{ padding: "4px 8px" }}
        >
          <option value="feishu">飞书 (国内)</option>
          <option value="lark">Lark (海外)</option>
        </select>
      </div>
      <button
        className="btn btn-primary"
        onClick={startScan}
        style={{ width: "100%", marginBottom: 12 }}
      >
        📱 扫码连接飞书（推荐）
      </button>
      <details
        open={isManual}
        onToggle={(e) => {
          if ((e.target as HTMLDetailsElement).open) {
            setPhase({ kind: "manual" });
          } else if (phase.kind === "manual") {
            setPhase({ kind: "empty" });
          }
        }}
        style={{ marginTop: 8 }}
      >
        <summary style={{ cursor: "pointer", color: "var(--muted)", padding: "8px 0" }}>
          或手动填入凭证
        </summary>
        <div style={{ marginTop: 12 }}>
          <FormField
            label="App ID"
            value={manualAppId}
            onChange={setManualAppId}
            placeholder="cli_xxx"
          />
          <FormField
            label="App Secret"
            value={manualAppSecret}
            onChange={setManualAppSecret}
            type="password"
          />
          <FormField
            label="Encrypt Key（可选）"
            value={manualEncryptKey}
            onChange={setManualEncryptKey}
            type="password"
          />
          <FormField
            label="Verification Token（可选）"
            value={manualVerifyToken}
            onChange={setManualVerifyToken}
          />
          <button
            className="btn btn-primary"
            onClick={submitManual}
            style={{ marginTop: 12 }}
          >
            保存
          </button>
        </div>
      </details>
    </Box>
  );
}

// ─── small layout helpers ──────────────────────────────────────────────

function Box({ children }: { children: React.ReactNode }) {
  return (
    <div style={{
      padding: 20, border: "1px solid var(--border)", borderRadius: 8,
      background: "var(--surface)",
    }}>
      {children}
    </div>
  );
}

function Header({
  status, subtitle,
}: { status: "connected" | "qr" | "empty"; subtitle: string }) {
  const dot = status === "connected"
    ? { bg: "var(--success)", text: "已连接" }
    : status === "qr"
    ? { bg: "var(--warning)", text: "等待扫码" }
    : { bg: "var(--muted)", text: "未连接" };
  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 8, marginBottom: 16,
    }}>
      <span style={{
        display: "inline-block", width: 8, height: 8,
        borderRadius: "50%", background: dot.bg,
      }} />
      <span style={{ fontWeight: 600 }}>{dot.text}</span>
      <span style={{ color: "var(--muted)" }}>· {subtitle}</span>
    </div>
  );
}

function Field({
  label, value, mono,
}: { label: string; value: string; mono?: boolean }) {
  return (
    <div style={{ marginBottom: 8, fontSize: 14 }}>
      <span style={{ color: "var(--muted)", marginRight: 8 }}>{label}：</span>
      <span style={{
        fontFamily: mono ? "monospace" : "inherit", wordBreak: "break-all",
      }}>{value}</span>
    </div>
  );
}

function FormField({
  label, value, onChange, type, placeholder,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  type?: "text" | "password";
  placeholder?: string;
}) {
  return (
    <div style={{ marginBottom: 10 }}>
      <label style={{ display: "block", fontSize: 14, color: "var(--muted)", marginBottom: 4 }}>
        {label}
      </label>
      <input
        type={type || "text"}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        style={{
          width: "100%", padding: "6px 10px",
          border: "1px solid var(--border)", borderRadius: 4,
          background: "var(--surface-inset)", color: "var(--text)",
        }}
      />
    </div>
  );
}
