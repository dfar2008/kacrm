/**
 * TeamFootprintCard — Phase 5.2.B (v1.4: history-follows-record)
 *
 * Record-detail page card showing "谁还跟进过这条记录". Renders when
 * the API returns at least 1 non-self interaction. Backend gates
 * access at the record level (caller must own/manage the record),
 * so a non-manager who inherits a customer from a departed colleague
 * sees the full history — that's the v1.4 "history follows record"
 * semantic.
 *
 * v1.4 changed: ``isManager`` prop is now optional/advisory only.
 * The component renders whenever the API returns rows; the backend
 * is the single source of truth on visibility.
 *
 * Two render modes:
 *   · ``mode="card"`` — full panel for the record-detail page
 *   · ``mode="badge"`` — collapsed dismiss-able sticky for 5.2.E
 *     (Copilot panel top sticky region)
 */

import { CrmIcon } from "./CrmIcon";
import React, { useEffect, useState } from "react";
import { fetchTeamActivity, type TeamActivityRow } from "../lib/api";

interface Props {
  module: string;
  recordId: number;
  /** v1.4: kept for backward-compat with existing call sites; ignored. */
  isManager?: boolean;
  mode?: "card" | "badge";
  /** When ``mode="badge"``, called on dismiss. */
  onDismiss?: () => void;
}

export function TeamFootprintCard({
  module, recordId, mode = "card", onDismiss,
}: Props) {
  const [rows, setRows] = useState<TeamActivityRow[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [expanded, setExpanded] = useState(mode === "card");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    fetchTeamActivity(module, recordId, 8)
      .then(({ activities }) => setRows(activities))
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, [module, recordId]);

  // Render gates — purely data-driven per v1.4
  if (loading || !rows) return null;
  if (rows.length === 0) return null;

  // ── Badge mode (5.2.E) — sticky compact in Copilot panel ────────
  if (mode === "badge") {
    return (
      <div style={{
        background: "var(--panel)",
        border: "1px solid var(--border)",
        borderRadius: 6,
        padding: "8px 12px",
        marginBottom: 8,
        fontSize: 14,
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <CrmIcon name="users" size={16} />
          <span style={{ flex: 1 }}>
            团队足迹（{rows.length}）
            <span
              style={{ marginLeft: 8, color: "var(--accent, #6366f1)", cursor: "pointer", fontSize: 14 }}
              onClick={() => setExpanded((v) => !v)}
            >
              {expanded ? "收起" : "展开"}
            </span>
          </span>
          <button
            onClick={onDismiss}
            aria-label="不再提醒此条"
            title="不再提醒此条"
            style={{
              background: "transparent", border: "none",
              color: "var(--muted)", cursor: "pointer", fontSize: 14,
            }}
          >×</button>
        </div>
        {expanded && (
          <div style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 4, fontSize: 14 }}>
            {rows.slice(0, 5).map((r) => (
              <ActivityLine key={r.id} row={r} />
            ))}
          </div>
        )}
      </div>
    );
  }

  // ── Card mode (5.2.B) — full panel for record-detail page ───────
  return (
    <div className="kacrm-team-footprint" style={{
      background: "var(--panel)",
      border: "1px solid var(--border)",
      borderRadius: 8,
      padding: 16,
      marginBottom: 16,
    }}>
      <h3 style={{ margin: 0, marginBottom: 12, fontSize: 14, fontWeight: 600 }}>
        <CrmIcon name="users" size={16} /> 团队足迹（{rows.length}）
      </h3>
      <div style={{ display: "flex", flexDirection: "column", gap: 6, fontSize: 14 }}>
        {rows.map((r) => (
          <ActivityLine key={r.id} row={r} />
        ))}
      </div>
      {error && (
        <div style={{ color: "tomato", fontSize: 14, marginTop: 8 }}>
          {error}
        </div>
      )}
    </div>
  );
}

function ActivityLine({ row }: { row: TeamActivityRow }) {
  const date = row.occurred_at
    ? row.occurred_at.slice(0, 10)
    : "";
  const userLabel = row.user_id ? `#${row.user_id}` : "(unknown)";
  const kindLabel = (() => {
    switch (row.kind) {
      case "note": return "记笔记";
      case "email": return "发邮件";
      case "call": return "通话";
      case "meeting": return "会议";
      case "action": return "更新信息";
      case "ai_abstain": return "AI 暂未补全";
      case "copilot_reply": return "Copilot 回复";
      case "message":
      case "im_message": {
        // Tag with the channel name extracted from source='channel:feishu'.
        const ch = row.source.startsWith("channel:") ? row.source.slice(8) : "";
        const chLabel: Record<string, string> = {
          feishu: "飞书", wechat: "微信", wecom_ilink: "iLink",
          slack: "Slack", telegram: "Telegram",
          dingtalk: "钉钉", discord: "Discord",
        };
        return `${chLabel[ch] || ch || "IM"} 沟通`;
      }
      default: return row.kind;
    }
  })();
  return (
    <div className="kacrm-footprint-row" style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
      <span style={{ color: "var(--muted)", fontSize: 13, minWidth: 80 }}>{date}</span>
      <span style={{ minWidth: 60 }}>{userLabel}</span>
      <span style={{ color: "var(--muted)", minWidth: 90 }}>{kindLabel}</span>
      <span style={{
        color: "var(--fg)", overflow: "hidden", textOverflow: "ellipsis",
        whiteSpace: "nowrap", flex: 1,
      }}>
        {row.text || "(无正文)"}
      </span>
    </div>
  );
}

export default TeamFootprintCard;
