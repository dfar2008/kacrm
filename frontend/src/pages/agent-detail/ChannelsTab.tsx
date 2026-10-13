import { useEffect, useState } from "react";
import { listProfileChannels, type ChannelEntry } from "@/lib/api";
import { useT, type T } from "@/lib/i18n";
import { ChannelSetup } from "./ChannelSetup";

/**
 * Channels & platforms tab.
 *
 * Per-profile bot model: each profile owns its channel credentials in
 * config.yaml. Channels are listed as collapsible accordions; expanding
 * one renders its per-channel setup component (QR scan for Feishu /
 * WeChat, form for the rest).
 */
export function ChannelsTab({ profileId }: { profileId: string }) {
  const translate = useT();
  const [perUserChannels, setPerUserChannels] = useState<ChannelEntry[] | null>(null);
  const [expandedChannel, setExpandedChannel] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  async function reload() {
    try {
      const r = await listProfileChannels(profileId);
      setPerUserChannels(r.channels || []);
      setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setPerUserChannels([]);
    }
  }

  useEffect(() => {
    reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [profileId]);

  return (
    <div style={{ padding: "8px 4px 32px", maxWidth: 860 }}>
      <div style={{ marginBottom: 4, fontSize: 20, fontWeight: 700 }}>{translate("channels.title")}</div>
      <div className="muted" style={{ fontSize: 14, marginBottom: 20 }}>
        {translate("channels.subtitle")}
      </div>

      <ValueBanner t={translate} />

      <div style={{ height: 24 }} />

      {err && <div className="alert alert-error">{err}</div>}
      {perUserChannels === null && !err && (
        <div className="muted">{translate("channels.loading")}</div>
      )}

      {/* Per-profile channel bot setup. Each row expands into a per-
       *  channel setup component (QR scan for Feishu/WeChat, form for
       *  the rest).  */}
      {perUserChannels && perUserChannels.length > 0 && (
        <>
          <div style={{
            fontSize: 13, fontWeight: 600, color: "var(--muted)",
            textTransform: "uppercase", letterSpacing: "0.08em",
            marginBottom: 12,
          }}>
            {translate("channels.connect_new")}
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {perUserChannels.map((ch) => (
              <div key={ch.channel}>
                <div
                  onClick={() => setExpandedChannel(
                    expandedChannel === ch.channel ? null : ch.channel,
                  )}
                  style={{
                    cursor: "pointer", padding: "10px 12px",
                    border: "1px solid var(--border)", borderRadius: 6,
                    display: "flex", alignItems: "center", gap: 12,
                    background: "var(--surface)",
                  }}
                >
                  <span style={{
                    display: "inline-block", width: 8, height: 8,
                    borderRadius: "50%",
                    background: ch.configured ? "var(--success)" : "var(--muted)",
                  }} />
                  <span style={{ fontWeight: 500 }}>{ch.display_name}</span>
                  {ch.configured && ch.bot_name && (
                    <span style={{ color: "var(--muted)", fontSize: 14 }}>
                      ({ch.bot_name})
                    </span>
                  )}
                  {ch.supports_qr && (
                    <span style={{
                      fontSize: 13, padding: "2px 6px", borderRadius: 3,
                      background: "var(--accent-soft)",
                      color: "var(--accent)",
                    }}>扫码</span>
                  )}
                  <span style={{ marginLeft: "auto", color: "var(--muted)" }}>
                    {expandedChannel === ch.channel ? "▲" : "▼"}
                  </span>
                </div>
                {expandedChannel === ch.channel && (
                  <div style={{ marginTop: 8 }}>
                    {/* Standing reminder for an already-configured WeChat
                     *  channel — the WeChat push rate-limit warning is
                     *  also shown during QR setup, but a user who has
                     *  already connected should still see it here.  */}
                    {ch.channel === "weixin" && ch.configured && (
                      <WeixinPushLimitNote t={translate} />
                    )}
                    <ChannelSetup
                      profileId={profileId}
                      channel={ch.channel}
                      onChange={reload}
                    />
                  </div>
                )}
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

function ValueBanner({ t: translate }: { t: T }) {
  const items = [
    { icon: "📱", title: translate("channels.value.chat.title"), desc: translate("channels.value.chat.desc") },
    { icon: "🔔", title: translate("channels.value.push.title"), desc: translate("channels.value.push.desc") },
  ];
  return (
    <div style={{
      display: "grid",
      gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
      gap: 12,
    }}>
      {items.map(it => (
        <div key={it.title} style={{
          background: "var(--panel)",
          border: "1px solid var(--border)",
          borderRadius: 10, padding: "14px 16px",
        }}>
          <div style={{ fontSize: 22, marginBottom: 6 }}>{it.icon}</div>
          <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 4 }}>{it.title}</div>
          <div className="muted" style={{ fontSize: 14, lineHeight: 1.5 }}>{it.desc}</div>
        </div>
      ))}
    </div>
  );
}

/**
 * Amber callout that restates the WeChat push rate-limit caveat. Shown
 * for an already-configured WeChat channel (the same warning also
 * appears during the QR setup flow in WeixinQRModal).
 */
function WeixinPushLimitNote({ t: translate }: { t: T }) {
  return (
    <div style={{
      marginBottom: 8,
      background: "var(--warning-soft)",
      border: "1px solid var(--warning)",
      borderRadius: 6,
      padding: "8px 10px",
      fontSize: 13,
      lineHeight: 1.5,
    }}>
      <div style={{ fontWeight: 600, color: "var(--warning)", marginBottom: 2 }}>
        {translate("weixin.push_limitation_title")}
      </div>
      <div style={{ color: "var(--muted)" }}>
        {translate("weixin.push_limitation_body")}
      </div>
      <div style={{ color: "var(--muted)", marginTop: 4 }}>
        {translate("weixin.push_limitation_workaround")}
      </div>
    </div>
  );
}
