import { useCallback, useEffect, useRef, useState } from "react";
import { QRCodeSVG } from "qrcode.react";
import {
  deleteProfileChannel, getProfileChannel,
  pollChannelQR, startChannelQR, testProfileChannel,
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
 * Per-profile WeChat (个人微信) bot setup via iLink Bot API.
 *
 * Tencent's iLink is the official "PersonalAgent" channel for
 * personal WeChat accounts — user scans the QR code with WeChat
 * mobile, bot identity binds to their account, hermes' WeixinAdapter
 * uses long-polling to receive messages.
 *
 * Manual paste is supported but unusual: account_id + token only
 * come from the iLink QR flow, there's no developer portal to copy
 * them from. Manual mode mainly exists for re-importing creds from
 * a previous environment.
 */
type Phase =
  | { kind: "loading" }
  | { kind: "empty" }
  | { kind: "qr"; sessionId: string; qrUrl: string; expiresAt: number; scanned: boolean }
  | { kind: "connected"; entry: ChannelEntry }
  | { kind: "error"; message: string };

interface Props {
  profileId: string;
  onChange?: () => void;
}

export function WeixinSetup({ profileId, onChange }: Props) {
  const [phase, setPhase] = useState<Phase>({ kind: "loading" });
  const [testing, setTesting] = useState(false);
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
      const entry = await getProfileChannel(profileId, "weixin");
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

  const startScan = useCallback(async () => {
    clearTimers();
    terminalReached.current = false;
    setPhase({ kind: "loading" });
    try {
      const r = await startChannelQR(profileId, "weixin");
      setPhase({
        kind: "qr",
        sessionId: r.session_id,
        qrUrl: r.qr_url,
        expiresAt: r.expires_at,
        scanned: false,
      });
      pollTimer.current = window.setInterval(async () => {
        if (terminalReached.current) return;
        try {
          const status: QRPollStatus = await pollChannelQR(
            profileId, "weixin", r.session_id,
          );
          if (terminalReached.current) return;
          if (status.status === "completed") {
            terminalReached.current = true;
            clearTimers();
            toast("微信 bot 连接成功");
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
          } else if (status.status === "scanned") {
            setPhase((p) => p.kind === "qr" ? { ...p, scanned: true } : p);
          }
        } catch (e) {
          if (terminalReached.current) return;
          terminalReached.current = true;
          clearTimers();
          setPhase({ kind: "error", message: friendlyQRError(e) });
        }
      }, Math.max(2000, r.interval * 1000));
      expiryTimer.current = window.setInterval(() => {
        setPhase((p) => (p.kind === "qr" ? { ...p } : p));
      }, 1000);
    } catch (e) {
      setPhase({
        kind: "error",
        message: e instanceof Error ? e.message : String(e),
      });
    }
  }, [profileId, reload, onChange, clearTimers]);

  const handleTest = useCallback(async () => {
    setTesting(true);
    try {
      const r = await testProfileChannel(profileId, "weixin");
      if (r.ok) toast("连接正常");
      else toast("连接失败：" + r.error, "error");
    } catch (e) {
      toast("测试失败：" + (e instanceof Error ? e.message : String(e)), "error");
    } finally {
      setTesting(false);
    }
  }, [profileId]);

  const handleDisconnect = useCallback(async () => {
    if (!window.confirm("断开微信 bot？")) return;
    try {
      await deleteProfileChannel(profileId, "weixin");
      toast("已断开");
      await reload();
      onChange?.();
    } catch (e) {
      toast("断开失败：" + (e instanceof Error ? e.message : String(e)), "error");
    }
  }, [profileId, reload, onChange]);

  if (phase.kind === "loading") {
    return <div className="muted" style={{ padding: 16 }}>加载中…</div>;
  }

  if (phase.kind === "error") {
    return (
      <Box>
        <div style={{ color: "var(--err)", marginBottom: 12 }}>⚠️ {phase.message}</div>
        <button className="btn" onClick={reload}>重试</button>
      </Box>
    );
  }

  if (phase.kind === "connected") {
    const { entry } = phase;
    return (
      <Box>
        <Header status="connected" subtitle="个人微信 (iLink)" />
        <Field label="Account ID" value={entry.extra?.account_id || "—"} mono />
        <Field label="Base URL" value={entry.extra?.base_url || "—"} />
        <div style={{ display: "flex", gap: 8, marginTop: 16 }}>
          {entry.supports_probe && (
            <button className="btn" onClick={handleTest} disabled={testing}>
              {testing ? "测试中…" : "测试连接"}
            </button>
          )}
          <button className="btn btn-danger" onClick={handleDisconnect}>
            断开
          </button>
        </div>
      </Box>
    );
  }

  if (phase.kind === "qr") {
    const secondsLeft = Math.max(0, Math.floor(phase.expiresAt - Date.now() / 1000));
    return (
      <Box>
        <Header
          status="qr"
          subtitle={phase.scanned
            ? "已扫码，请在微信里点击确认…"
            : `请在 ${secondsLeft} 秒内用微信扫码`}
        />
        <div style={{
          display: "flex", flexDirection: "column", alignItems: "center",
          // theme-guard:ignore-start
          padding: 24, background: "#fff", borderRadius: 8,
          // theme-guard:ignore-end
        }}>
          <QRCodeSVG value={phase.qrUrl} size={240} level="M" />
          <a href={phase.qrUrl} target="_blank" rel="noreferrer"
             style={{ marginTop: 12, fontSize: 14 }}>
            或点这里在浏览器打开
          </a>
        </div>
        <div style={{ marginTop: 16 }}>
          <button className="btn" onClick={() => {
            clearTimers();
            setPhase({ kind: "empty" });
          }}>
            取消
          </button>
        </div>
      </Box>
    );
  }

  // empty
  return (
    <Box>
      <Header status="empty" subtitle="为此 profile 接入专属微信 bot（iLink）" />
      <button
        className="btn btn-primary"
        onClick={startScan}
        style={{ width: "100%" }}
      >
        📱 用微信扫码连接
      </button>
      <div style={{
        marginTop: 12, fontSize: 14, color: "var(--muted)",
      }}>
        扫码后 Tencent iLink 会将一个 bot identity 绑定到您当前的微信账号，
        消息会通过长轮询同步给 hermes。
      </div>
    </Box>
  );
}

function Box({ children }: { children: React.ReactNode }) {
  return (
    <div style={{
      padding: 20, border: "1px solid var(--border)", borderRadius: 8,
      background: "var(--surface)",
    }}>{children}</div>
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
