import { useCallback, useEffect, useRef, useState } from "react";
import { QRCodeSVG } from "qrcode.react";
import {
  deleteProfileChannel, getProfileChannel,
  pollChannelQR, putProfileChannel, startChannelQR,
  type ChannelEntry, type QRPollStatus,
  ApiError,
} from "@/lib/api";
import { toast } from "@/lib/toast";
import { useT, type T } from "@/lib/i18n";

type Phase =
  | { kind: "loading" }
  | { kind: "empty" }
  | { kind: "qr"; sessionId: string; qrUrl: string; expiresAt: number }
  | { kind: "manual" }
  | { kind: "connected"; entry: ChannelEntry }
  | { kind: "error"; message: string };

interface Props { profileId: string; onChange?: () => void; }

export function DingtalkSetup({ profileId, onChange }: Props) {
  const t = useT();
  const friendlyQRError = useCallback((e: unknown): string => {
    if (e instanceof ApiError && e.detail && typeof e.detail === "object") {
      const code = (e.detail as { error?: { code?: string } })?.error?.code;
      if (code === "qr_session_not_found") return t("channelqr.qr_session_expired");
      if (code === "qr_session_mismatch") return t("channelqr.qr_session_mismatch");
    }
    return e instanceof Error ? e.message : String(e);
  }, [t]);
  const [phase, setPhase] = useState<Phase>({ kind: "loading" });
  const pollTimer = useRef<number | null>(null);
  const expiryTimer = useRef<number | null>(null);
  const terminalReached = useRef(false);

  const clearTimers = useCallback(() => {
    if (pollTimer.current !== null) { window.clearInterval(pollTimer.current); pollTimer.current = null; }
    if (expiryTimer.current !== null) { window.clearInterval(expiryTimer.current); expiryTimer.current = null; }
  }, []);

  const reload = useCallback(async () => {
    try {
      const entry = await getProfileChannel(profileId, "dingtalk");
      setPhase(entry.configured ? { kind: "connected", entry } : { kind: "empty" });
    } catch (e) {
      setPhase({ kind: "error", message: e instanceof Error ? e.message : String(e) });
    }
  }, [profileId]);

  useEffect(() => {
    reload();
    return clearTimers;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [profileId]);

  const startScan = useCallback(async () => {
    clearTimers(); terminalReached.current = false; setPhase({ kind: "loading" });
    try {
      const r = await startChannelQR(profileId, "dingtalk", {});
      setPhase({ kind: "qr", sessionId: r.session_id, qrUrl: r.qr_url, expiresAt: r.expires_at });
      pollTimer.current = window.setInterval(async () => {
        if (terminalReached.current) return;
        try {
          const status: QRPollStatus = await pollChannelQR(profileId, "dingtalk", r.session_id);
          if (terminalReached.current) return;
          if (status.status === "completed") {
            terminalReached.current = true; clearTimers();
            toast(t("channelqr.connect_ok_dingtalk")); await reload(); onChange?.();
          } else if (status.status === "denied") {
            terminalReached.current = true; clearTimers();
            setPhase({ kind: "error", message: t("channelqr.qr_denied") });
          } else if (status.status === "expired") {
            terminalReached.current = true; clearTimers();
            setPhase({ kind: "error", message: t("channelqr.qr_expired") });
          } else if (status.status === "error") {
            terminalReached.current = true; clearTimers();
            setPhase({ kind: "error", message: status.message });
          }
        } catch (e) {
          if (terminalReached.current) return;
          terminalReached.current = true; clearTimers();
          setPhase({ kind: "error", message: friendlyQRError(e) });
        }
      }, Math.max(2000, r.interval * 1000));
      expiryTimer.current = window.setInterval(() => {
        setPhase((p) => (p.kind === "qr" ? { ...p } : p));
      }, 1000);
    } catch (e) {
      setPhase({ kind: "error", message: e instanceof Error ? e.message : String(e) });
    }
  }, [profileId, reload, onChange, clearTimers, t, friendlyQRError]);

  const [clientId, setClientId] = useState("");
  const [clientSecret, setClientSecret] = useState("");
  const [robotCode, setRobotCode] = useState("");

  const submitManual = useCallback(async () => {
    if (!clientId.trim() || !clientSecret.trim()) { toast(t("channelqr.required_both"), "error"); return; }
    try {
      await putProfileChannel(profileId, "dingtalk", {
        client_id: clientId.trim(), client_secret: clientSecret.trim(), robot_code: robotCode.trim(),
      });
      toast(t("channelqr.saved_dingtalk"));
      setClientId(""); setClientSecret(""); setRobotCode("");
      await reload(); onChange?.();
    } catch (e) {
      toast(t("channelqr.save") + "：" + (e instanceof Error ? e.message : String(e)), "error");
    }
  }, [profileId, clientId, clientSecret, robotCode, reload, onChange, t]);

  const handleDisconnect = useCallback(async () => {
    if (!window.confirm(t("channelqr.confirm_disconnect"))) return;
    try { await deleteProfileChannel(profileId, "dingtalk"); await reload(); onChange?.(); }
    catch (e) { toast(e instanceof Error ? e.message : String(e), "error"); }
  }, [profileId, reload, onChange, t]);

  if (phase.kind === "loading") return <div className="muted" style={{ padding: 16 }}>…</div>;
  if (phase.kind === "error")
    return (
      <div style={{ padding: 20 }}>
        <div style={{ color: "var(--err)", marginBottom: 12 }}>⚠️ {phase.message}</div>
        <button className="btn" onClick={reload}>{t("channelqr.retry")}</button>
      </div>
    );
  if (phase.kind === "connected") {
    const { entry } = phase;
    return (
      <div style={{ padding: 20 }}>
        <Header status="connected" subtitle={t("channelqr.connected_dingtalk")} t={t} />
        <div style={{ fontSize: 14, marginBottom: 8 }}>Client ID: {entry.extra?.client_id || "—"}</div>
        <button className="btn btn-danger" onClick={handleDisconnect}>{t("channelqr.disconnect")}</button>
      </div>
    );
  }
  if (phase.kind === "qr") {
    const sec = Math.max(0, Math.floor(phase.expiresAt - Date.now() / 1000));
    return (
      <div style={{ padding: 20 }}>
        <Header status="qr" subtitle={t("channelqr.scan_hint_dingtalk", { sec })} t={t} />
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", /* theme-guard:ignore */ background: "#fff", padding: 24, borderRadius: 8 }}>
          <QRCodeSVG value={phase.qrUrl} size={240} level="M" />
          <a href={phase.qrUrl} target="_blank" rel="noreferrer" style={{ marginTop: 12, fontSize: 14 }}>
            {t("channelqr.open_in_browser")}
          </a>
        </div>
        <button className="btn" style={{ marginTop: 16 }} onClick={() => { clearTimers(); setPhase({ kind: "empty" }); }}>
          {t("channelqr.cancel")}
        </button>
      </div>
    );
  }
  const isManual = phase.kind === "manual";
  return (
    <div style={{ padding: 20 }}>
      <Header status="empty" subtitle={t("channelqr.subtitle_dingtalk")} t={t} />
      <button className="btn btn-primary" onClick={startScan} style={{ width: "100%", marginBottom: 12 }}>
        {t("channelqr.scan_dingtalk")}
      </button>
      <details
        open={isManual}
        onToggle={(e) => {
          if ((e.target as HTMLDetailsElement).open) setPhase({ kind: "manual" });
          else if (phase.kind === "manual") setPhase({ kind: "empty" });
        }}
      >
        <summary style={{ cursor: "pointer", color: "var(--muted)", padding: "8px 0" }}>
          {t("channelqr.manual_toggle")}
        </summary>
        <div style={{ marginTop: 12 }}>
          <FormField label={t("channelqr.dingtalk_client_id")} value={clientId} onChange={setClientId} />
          <FormField label={t("channelqr.dingtalk_client_secret")} value={clientSecret} onChange={setClientSecret} type="password" />
          <FormField label={t("channelqr.dingtalk_robot_code")} value={robotCode} onChange={setRobotCode} type="password" />
          <button className="btn btn-primary" onClick={submitManual} style={{ marginTop: 12 }}>
            {t("channelqr.save")}
          </button>
        </div>
      </details>
    </div>
  );
}

// ─── small layout helpers ──────────────────────────────────────────────

function Header({
  status, subtitle, t,
}: { status: "connected" | "qr" | "empty"; subtitle: string; t: T }) {
  const dot = status === "connected"
    ? { bg: "var(--success)", text: t("channelqr.status_connected") }
    : status === "qr"
    ? { bg: "var(--warning)", text: t("channelqr.status_waiting") }
    : { bg: "var(--muted)", text: t("channelqr.status_empty") };
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 16 }}>
      <span style={{ display: "inline-block", width: 8, height: 8, borderRadius: "50%", background: dot.bg }} />
      <span style={{ fontWeight: 600 }}>{dot.text}</span>
      <span style={{ color: "var(--muted)" }}>· {subtitle}</span>
    </div>
  );
}

function FormField({ label, value, onChange, type }: {
  label: string; value: string; onChange: (v: string) => void; type?: "text" | "password";
}) {
  return (
    <div style={{ marginBottom: 10 }}>
      <label style={{ display: "block", fontSize: 14, color: "var(--muted)", marginBottom: 4 }}>{label}</label>
      <input
        type={type || "text"}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        style={{
          width: "100%", padding: "6px 10px",
          border: "1px solid var(--border)", borderRadius: 4,
          background: "var(--surface-inset)", color: "var(--text)",
        }}
      />
    </div>
  );
}
