import { useEffect, useMemo, useState } from "react";
import {
  listGatewayActions, approveAction, rejectAction, revertAction,
  GatewayAction,
} from "@/lib/api";
import { toast } from "@/lib/toast";
import { useT, type DictKey } from "@/lib/i18n";

// Filters keep the raw ``state`` value (used in the API call) plus an
// i18n key for their label — resolved per-render so language switching
// doesn't require remounting the page.
const FILTERS: Array<{ value: string; labelKey: DictKey }> = [
  { value: "pending_approval", labelKey: "approvals.filter.pending" },
  { value: "executed",         labelKey: "approvals.filter.executed" },
  { value: "rejected",         labelKey: "approvals.filter.rejected" },
  { value: "reverted",         labelKey: "approvals.filter.reverted" },
  { value: "",                 labelKey: "approvals.filter.all" },
];

// Charter §1 渠道闭环: pending actions submitted on a non-WebUI channel
// must be approved on that channel — the WebUI dashboard SHOWS them
// (visibility) but cannot ACT on them (buttons disabled, banner shown).
// The dispatcher's cross-channel guard would 409 the request anyway;
// disabling the buttons keeps the UI honest.
const CHANNEL_LABEL_KEYS: Record<string, DictKey> = {
  feishu:      "approvals.channel.feishu",
  wecom_ilink: "approvals.channel.wecom_ilink",
  dingtalk:    "approvals.channel.dingtalk",
  email:       "approvals.channel.email",
};
// Discord/Telegram/Slack are brand names — no translation needed.
const CHANNEL_LITERAL: Record<string, string> = {
  discord:  "Discord",
  telegram: "Telegram",
  slack:    "Slack",
};

function channelDisplay(t: ReturnType<typeof useT>, channel: string | undefined | null): string {
  if (!channel) return t("approvals.channel.other");
  if (CHANNEL_LABEL_KEYS[channel]) return t(CHANNEL_LABEL_KEYS[channel]);
  if (CHANNEL_LITERAL[channel]) return CHANNEL_LITERAL[channel];
  return channel;
}

// 状态语义色走主题变量(index.css),深浅模式各自达标,不再硬编码。
const STATE_BADGE: Record<string, { labelKey: DictKey; color: string }> = {
  pending_approval: { labelKey: "approvals.state.pending",  color: "var(--warning)" },
  executed:         { labelKey: "approvals.state.executed", color: "var(--success)" },
  rejected:         { labelKey: "approvals.state.rejected", color: "var(--danger)" },
  reverted:         { labelKey: "approvals.state.reverted", color: "var(--muted)" },
  blocked:          { labelKey: "approvals.state.blocked",  color: "var(--danger)" },
};

export function ApprovalsPage() {
  const t = useT();
  const [filter, setFilter] = useState("pending_approval");
  const [rows, setRows] = useState<GatewayAction[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  async function load() {
    setLoading(true); setErr(null);
    try {
      const r = await listGatewayActions({ state: filter });
      setRows(r.data || []);
    } catch (e: any) { setErr(e.message); }
    finally { setLoading(false); }
  }
  useEffect(() => { load(); }, [filter]);

  // Matches ApprovalToast: double-click / StrictMode replay causes
  // approve/reject on an already-settled row — that's idempotent, not
  // a failure, so we surface an info toast rather than a red error.
  const ALREADY_HANDLED_RE =
    /is (approved|executed|rejected|blocked|reverted),? not pending_approval/i;

  const prompts = useMemo(() => ({
    approve: t("approvals.approve_confirm"),
    reject:  t("approvals.reject_confirm"),
    revert:  t("approvals.revert_confirm"),
  }), [t]);
  const doneMsg = useMemo(() => ({
    approve: t("approvals.toast.approved"),
    reject:  t("approvals.toast.rejected"),
    revert:  t("approvals.toast.reverted"),
  }), [t]);

  async function doAction(id: string, kind: "approve" | "reject" | "revert") {
    if (!confirm(prompts[kind])) return;
    try {
      if (kind === "approve") await approveAction(id);
      else if (kind === "reject") await rejectAction(id);
      else await revertAction(id);
      toast(doneMsg[kind], "success");
      load();
    } catch (e: any) {
      const msg = e.message || "";
      if (ALREADY_HANDLED_RE.test(msg)) {
        toast(t("approvals.toast.already_handled"), "info");
        load();
      } else {
        toast(msg || t("toast.generic_error"), "error");
      }
    }
  }

  return (
    <div style={{ padding: 24, overflow: "auto", height: "100%" }}>
      <div style={{ marginBottom: 16 }}>
        <h2 style={{ margin: 0 }}>{t("approvals.title")}</h2>
        <p className="muted" style={{ marginTop: 4 }}>
          {t("approvals.subtitle")}
        </p>
      </div>

      <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 16 }}>
        <select value={filter} onChange={e => setFilter(e.target.value)} style={{ flex: 1, minWidth: 0 }}>
          {FILTERS.map(f => <option key={f.value || "all"} value={f.value}>{t(f.labelKey)}</option>)}
        </select>
        <button className="ghost" onClick={load}
                style={{ whiteSpace: "nowrap", flexShrink: 0 }}>
          {t("approvals.button.refresh")}
        </button>
      </div>

      {err && <div className="alert alert-error">{err}</div>}
      {loading && <div className="muted">{t("common.loading")}</div>}

      {!loading && rows.length === 0 && (
        <div className="muted" style={{ padding: "24px", textAlign: "center" }}>
          {filter === "pending_approval"
            ? t("approvals.empty_pending")
            : t("approvals.empty_generic")}
        </div>
      )}

      <div style={{ border: "1px solid var(--border)", borderRadius: 6 }}>
        {rows.map(a => {
          const params = a.params || {};
          const target = a.target || {};
          const moduleName = target.module || params.module_id || "—";
          const op = params.op || "—";
          const dataFields = params.data || {};
          const entries = Object.entries(dataFields);
          const fieldSummary = entries.slice(0, 4)
            .map(([k, v]) => (
              <span key={k}>
                <span className="muted">{k}:</span> {String(v).substring(0, 30)}
                {entries.indexOf([k, v] as any) < Math.min(entries.length, 4) - 1 ? ", " : ""}
              </span>
            ));
          const more = entries.length > 4 ? ` (+${entries.length - 4})` : "";
          const badgeMeta = STATE_BADGE[a.state];
          const badgeLabel = badgeMeta ? t(badgeMeta.labelKey) : a.state;
          const badgeColor = badgeMeta ? badgeMeta.color : "var(--muted)";
          const timeStr = a.created_at ? new Date(a.created_at).toLocaleString(undefined, { hour12: false }) : "—";

          return (
            <div key={a.id} style={{
              padding: "12px 16px",
              borderBottom: "1px solid var(--border)",
            }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 6 }}>
                <div>
                  <span style={{ fontWeight: 600 }}>{a.kind}</span>
                  <span className="muted" style={{ marginLeft: 8, fontSize: 14 }}>
                    {moduleName} / {op}
                  </span>
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                  <span style={{ color: badgeColor, fontWeight: 600 }}>{badgeLabel}</span>
                  <span className="muted" style={{ fontSize: 13 }}>{timeStr}</span>
                </div>
              </div>

              <div style={{
                fontSize: 14, marginBottom: 8,
                fontFamily: "monospace", wordBreak: "break-all",
              }}>
                {fieldSummary}{more}
              </div>

              {(() => {
                const channel = a.origin?.channel || "webui";
                const offChannel = a.state === "pending_approval" && channel !== "webui";
                const channelLabel = channelDisplay(t, channel);
                return (
                  <>
                    <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13 }}>
                      <span className="muted">Agent: {a.agent_name || "—"}</span>
                      <span className="muted">ID: {String(a.id).substring(0, 8)}…</span>
                      <span className="muted">{t("approvals.origin_label")}: {channelLabel}</span>
                      {a.state === "pending_approval" && !offChannel && (
                        <>
                          <button onClick={() => doAction(a.id, "approve")}
                                  style={{
                                    background: "var(--success)", color: "var(--fg-strong)",
                                    border: "none", borderRadius: 4,
                                    padding: "3px 10px", fontSize: 14, cursor: "pointer",
                                  }}>{t("approvals.button.approve")}</button>
                          <button onClick={() => doAction(a.id, "reject")}
                                  style={{
                                    background: "var(--danger)", color: "var(--on-danger)",
                                    border: "none", borderRadius: 4,
                                    padding: "3px 10px", fontSize: 14, cursor: "pointer",
                                  }}>{t("approvals.button.reject")}</button>
                        </>
                      )}
                      {a.state === "executed" && (
                        <button className="ghost" onClick={() => doAction(a.id, "revert")}>{t("approvals.button.revert")}</button>
                      )}
                    </div>
                    {offChannel && (
                      <div
                        title={t("approvals.off_channel_tooltip")}
                        style={{
                          marginTop: 6, padding: "4px 8px",
                          fontSize: 13, borderRadius: 4,
                          background: "var(--warning-soft)",
                          border: "1px solid color-mix(in srgb, var(--warning) 40%, transparent)",
                          color: "var(--text)",
                        }}
                      >
                        🔒 {t("approvals.off_channel_banner").replace("{channel}", channelLabel)}
                      </div>
                    )}
                  </>
                );
              })()}
            </div>
          );
        })}
      </div>
    </div>
  );
}
