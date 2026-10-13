import { useState } from "react";
import { useT } from "@/lib/i18n";
import {
  submitApproval, approveChain, rejectChain, backChain, transferChain,
  type ApprovalDetail, type ApprovalStep, type ApprovalLog, ApiError,
} from "@/lib/api";

/** 审批链状态 → 配色(对齐 CSS 变量)。 */
function statusColor(status: string): string {
  switch (status) {
    case "passed": return "var(--ok, #16a34a)";
    case "rejected": return "var(--danger, #dc2626)";
    case "pending": return "var(--accent, #2563eb)";
    case "backed": return "var(--warn, #d97706)";
    default: return "var(--muted)";  // waiting
  }
}

function approverIds(step: ApprovalStep): number[] {
  return String(step.approver_ids || "")
    .split(",").map(s => s.trim()).filter(Boolean).map(Number);
}

export function ApprovalChainPanel({
  module, recordId, approval, currentUid, onChanged,
}: {
  module: string;
  recordId: number;
  /** detail_query.approval:有实例时为 ApprovalDetail,否则 {}。 */
  approval: ApprovalDetail | Record<string, never>;
  currentUid: number;
  onChanged: () => void;
}) {
  const t = useT();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const instance = (approval as ApprovalDetail).approval;
  const steps = (approval as ApprovalDetail).steps || [];
  const logs: ApprovalLog[] = (approval as ApprovalDetail).logs || [];

  async function run(fn: () => Promise<unknown>) {
    setBusy(true); setErr(null);
    try { await fn(); onChanged(); }
    catch (e) { setErr(e instanceof ApiError ? e.message : t("approval.err")); }
    finally { setBusy(false); }
  }

  const card: React.CSSProperties = {
    border: "1px solid var(--border)", borderRadius: 8,
    padding: 16, marginTop: 16, background: "var(--surface, transparent)",
  };
  const title: React.CSSProperties = {
    fontWeight: 600, fontSize: 14, marginBottom: 12,
    display: "flex", alignItems: "center", justifyContent: "space-between",
  };
  const btn: React.CSSProperties = {
    padding: "5px 12px", borderRadius: 6, border: "1px solid var(--border)",
    fontSize: 13, cursor: busy ? "default" : "pointer", opacity: busy ? 0.6 : 1,
  };

  // 无实例 → 提交审批入口。
  if (!instance) {
    return (
      <div style={card}>
        <div style={title}>{t("approval.title")}</div>
        <div style={{ fontSize: 13, color: "var(--muted)", marginBottom: 12 }}>
          {t("approval.not_submitted")}
        </div>
        <button
          style={{ ...btn, background: "var(--accent, #2563eb)", color: "#fff", border: "none" }}
          disabled={busy}
          onClick={() => run(async () => {
            const r = await submitApproval(module, recordId);
            if (r.skipped === "no_config") setErr(t("approval.no_config"));
          })}
        >
          {t("approval.submit")}
        </button>
        {err && <div style={{ color: "var(--danger, #dc2626)", fontSize: 13, marginTop: 8 }}>{err}</div>}
      </div>
    );
  }

  const overall = instance.status;  // pending | passed | rejected | cancelled
  const curStep = instance.current_step;

  return (
    <div style={card}>
      <div style={title}>
        <span>{t("approval.title")}</span>
        <span style={{ fontSize: 12, color: statusColor(overall), fontWeight: 600 }}>
          {t(`approval.status.${overall}`)}
        </span>
      </div>

      <ol style={{ listStyle: "none", padding: 0, margin: 0 }}>
        {steps.map(s => {
          const isCurrent = overall === "pending" && s.step_order === curStep;
          const canAct = isCurrent && approverIds(s).includes(currentUid);
          return (
            <li key={s.id} style={{
              display: "flex", gap: 10, padding: "8px 0",
              borderTop: s.step_order > 1 ? "1px solid var(--border)" : "none",
            }}>
              <span style={{
                minWidth: 20, height: 20, borderRadius: "50%", fontSize: 12,
                display: "inline-flex", alignItems: "center", justifyContent: "center",
                background: statusColor(s.status), color: "#fff", flexShrink: 0,
              }}>{s.step_order}</span>
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 13, fontWeight: 500 }}>
                  {s.step_name}
                  <span style={{ marginLeft: 8, color: statusColor(s.status), fontSize: 12 }}>
                    {t(`approval.status.${s.status}`)}
                  </span>
                </div>
                {s.actual_approver_id && (s.status === "passed" || s.status === "rejected" || s.status === "backed") && (
                  <div style={{ fontSize: 12, color: "var(--muted)", marginTop: 2 }}>
                    {t("approval.acted_by", {
                      name: s.actual_approver_name || `#${s.actual_approver_id}`,
                    })}
                    {s.approved_at && ` · ${s.approved_at.slice(0, 16)}`}
                  </div>
                )}
                {(s.status === "pending" || s.status === "waiting") && s.approver_names && s.approver_names.length > 0 && (
                  <div style={{ fontSize: 12, color: "var(--muted)", marginTop: 2 }}>
                    {t(s.status === "pending" ? "approval.awaiting" : "approval.will_review", {
                      names: s.approver_names.join("、"),
                    })}
                  </div>
                )}
                {s.remark && (
                  <div style={{ fontSize: 12, color: "var(--muted)", marginTop: 2 }}>{s.remark}</div>
                )}
                {canAct && (
                  <div style={{ display: "flex", gap: 8, marginTop: 8, flexWrap: "wrap" }}>
                    <button
                      style={{ ...btn, background: "var(--ok, #16a34a)", color: "#fff", border: "none" }}
                      disabled={busy}
                      onClick={() => run(() => approveChain(instance.id, ""))}
                    >{t("approval.approve")}</button>
                    <button
                      style={{ ...btn, color: "var(--danger, #dc2626)" }}
                      disabled={busy}
                      onClick={() => run(() => rejectChain(instance.id, window.prompt(t("approval.reject_reason")) || ""))}
                    >{t("approval.reject")}</button>
                    {s.step_order > 1 && (
                      <button
                        style={btn}
                        disabled={busy}
                        onClick={() => {
                          const ts = Number(window.prompt(t("approval.back_to_step"), String(s.step_order - 1)));
                          if (ts >= 1 && ts < s.step_order) run(() => backChain(instance.id, ts, ""));
                        }}
                      >{t("approval.back")}</button>
                    )}
                    <button
                      style={btn}
                      disabled={busy}
                      onClick={() => {
                        const to = Number(window.prompt(t("approval.transfer_to")));
                        if (to > 0) run(() => transferChain(instance.id, currentUid, to));
                      }}
                    >{t("approval.transfer")}</button>
                  </div>
                )}
              </div>
            </li>
          );
        })}
      </ol>

      {logs.length > 0 && (
        <details style={{ marginTop: 12 }}>
          <summary style={{ fontSize: 12, color: "var(--muted)", cursor: "pointer" }}>
            {t("approval.history", { n: logs.length })}
          </summary>
          <ol style={{ listStyle: "none", padding: "8px 0 0", margin: 0 }}>
            {logs.map(lg => (
              <li key={lg.id} style={{
                display: "flex", gap: 8, fontSize: 12, color: "var(--muted)",
                padding: "3px 0",
              }}>
                <span style={{ color: statusColor(
                  lg.action === "approve" ? "passed"
                    : lg.action === "reject" ? "rejected"
                    : lg.action === "back" ? "backed" : "pending",
                ), fontWeight: 600, minWidth: 40 }}>
                  {t(`approval.log.${lg.action}`)}
                </span>
                <span style={{ flex: 1 }}>
                  {lg.actor_name || (lg.actor_id ? `#${lg.actor_id}` : "")}
                  {lg.action === "transfer" && lg.target_name && ` → ${lg.target_name}`}
                  {lg.step_name && lg.action !== "transfer" && lg.action !== "cancel"
                    && ` · ${lg.step_name}`}
                  {lg.remark && ` · ${lg.remark}`}
                </span>
                {lg.created_at && (
                  <span style={{ flexShrink: 0 }}>{lg.created_at.slice(5, 16)}</span>
                )}
              </li>
            ))}
          </ol>
        </details>
      )}

      {err && <div style={{ color: "var(--danger, #dc2626)", fontSize: 13, marginTop: 8 }}>{err}</div>}
    </div>
  );
}
