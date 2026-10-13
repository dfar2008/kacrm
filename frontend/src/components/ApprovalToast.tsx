import { useEffect, useRef, useState } from "react";
import { listGatewayActions, approveAction, rejectAction, GatewayAction } from "@/lib/api";
import { useCurrentUser } from "@/lib/currentUser";
import { toast } from "@/lib/toast";
import { useRouter } from "@/lib/router";
import { useT } from "@/lib/i18n";

/**
 * Bottom-right popup that polls /api/gateway/actions every 10 s and
 * surfaces pending-approval items with inline ✓/✗ buttons. Matches the
 * original dashboard's `startApprovalPoller()` behaviour.
 *
 * Only renders when there's at least one unseen pending item. A local
 * "seen" set keeps dismissed items from re-popping without being acted on.
 */
// 后端 approve/reject 的 state 校验只允许 pending_approval → 其他 state
// 都会抛 "action X is <state>, not pending_approval"。常见触发：
// 1) 用户快速双击（网络在途时第二次点击）；2) StrictMode 双调用；
// 3) poller 还没把已处理的 action 清掉。这种情况下本次点击是幂等的——
// 说明"已经处理过了"，UI 侧当成 success 处理，不要再弹红 toast。
const ALREADY_HANDLED_RE = /is (approved|executed|rejected|blocked|reverted),? not pending_approval/i;

export function ApprovalToast() {
  const t = useT();
  const { navigate } = useRouter();
  const { user } = useCurrentUser();
  const [pending, setPending] = useState<GatewayAction[]>([]);
  const [hidden, setHidden] = useState(false);
  const seenRef = useRef<Set<string>>(new Set());
  // 在途集合 —— 每个 action 同一时间只能有一次 approve/reject 请求，
  // 避免双击导致第二次请求吃到"已 approved"的 stale 错误。
  const [busyIds, setBusyIds] = useState<Set<string>>(new Set());
  const timerRef = useRef<number | null>(null);

  async function poll() {
    if (!user) return;  // not authed yet — wait for CurrentUserProvider
    try {
      const r = await listGatewayActions({ state: "pending_approval", limit: 10 });
      const unseen = (r.data || []).filter(a => !seenRef.current.has(a.id));
      setPending(unseen);
    } catch { /* silent */ }
  }

  function markBusy(id: string, v: boolean) {
    setBusyIds(prev => {
      const next = new Set(prev);
      if (v) next.add(id); else next.delete(id);
      return next;
    });
  }

  useEffect(() => {
    if (!user) return;
    poll();
    timerRef.current = window.setInterval(poll, 10_000);
    return () => {
      if (timerRef.current !== null) window.clearInterval(timerRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user?.user_id]);

  if (hidden || pending.length === 0) return null;

  async function onApprove(a: GatewayAction) {
    if (busyIds.has(a.id)) return;
    markBusy(a.id, true);
    // 乐观地从列表里拿掉 —— 即便请求还在途，也不再让第二次点击打到按钮；
    // 万一真失败会通过 poll() 重新出现。
    seenRef.current.add(a.id);
    setPending(prev => prev.filter(x => x.id !== a.id));
    try {
      await approveAction(a.id);
      toast(t("approval.toast.approved"), "success");
    } catch (e: any) {
      const msg = e.message || "";
      if (ALREADY_HANDLED_RE.test(msg)) {
        // 幂等：已被处理过，别再吓唬用户
        toast(t("approval.toast.already"), "info");
      } else {
        toast(msg || t("approval.toast.approve_failed"), "error");
      }
    } finally {
      markBusy(a.id, false);
      poll();
    }
  }
  async function onReject(a: GatewayAction) {
    if (busyIds.has(a.id)) return;
    markBusy(a.id, true);
    seenRef.current.add(a.id);
    setPending(prev => prev.filter(x => x.id !== a.id));
    try {
      await rejectAction(a.id);
      toast(t("approval.toast.rejected"), "success");
    } catch (e: any) {
      const msg = e.message || "";
      if (ALREADY_HANDLED_RE.test(msg)) {
        toast(t("approval.toast.already"), "info");
      } else {
        toast(msg || t("approval.toast.reject_failed"), "error");
      }
    } finally {
      markBusy(a.id, false);
      poll();
    }
  }
  function dismiss() {
    pending.forEach(a => seenRef.current.add(a.id));
    setHidden(true);
    // Allow re-show next poll cycle when a genuinely new item arrives
    setTimeout(() => setHidden(false), 11_000);
  }

  const items = pending.slice(0, 5);

  return (
    <div style={{
      position: "fixed",
      bottom: 24, right: 24,
      width: 380, maxHeight: 420, overflowY: "auto",
      background: "var(--panel-2)",
      border: "1px solid var(--warning)",
      borderRadius: 12,
      boxShadow: "var(--shadow-modal)",
      fontSize: 14, color: "var(--fg-strong)",
      zIndex: 10000,
    }}>
      <div style={{
        padding: "10px 14px",
        borderBottom: "1px solid color-mix(in srgb, var(--warning) 30%, transparent)",
        display: "flex", justifyContent: "space-between", alignItems: "center",
      }}>
        <span style={{ fontWeight: 600, color: "var(--warning)" }}>
          {t("approval.count", { n: pending.length })}
        </span>
        <button onClick={dismiss}
                style={{
                  background: "none", border: "none", color: "var(--muted)",
                  cursor: "pointer", fontSize: 16,
                }}>✕</button>
      </div>
      {items.map(a => {
        const params = a.params || {};
        const target = a.target || {};
        const moduleName = target.module || params.module_id || "—";
        const op = params.op || "—";
        const dataFields = params.data || {};
        const fieldSummary = Object.entries(dataFields).slice(0, 3)
          .map(([k, v]) => `${k}: ${String(v).substring(0, 20)}`)
          .join(", ");
        return (
          <div key={a.id} style={{
            padding: "10px 14px",
            borderBottom: "1px solid var(--border-subtle)",
          }}>
            <div style={{ marginBottom: 4 }}>
              <span style={{ fontWeight: 600 }}>{moduleName}</span>
              <span style={{ color: "var(--muted)", fontSize: 13, marginLeft: 4 }}>
                / {op}
              </span>
            </div>
            <div style={{
              fontSize: 13, color: "var(--muted)", marginBottom: 6,
              fontFamily: "monospace", wordBreak: "break-all",
            }}>
              {fieldSummary || "—"}
            </div>
            <div style={{ display: "flex", gap: 6 }}>
              <button onClick={() => onApprove(a)}
                      disabled={busyIds.has(a.id)}
                      style={{
                        background: "var(--success)", color: "var(--fg-strong)",
                        border: "none", borderRadius: 4,
                        padding: "3px 10px", fontSize: 14,
                        cursor: busyIds.has(a.id) ? "wait" : "pointer",
                        opacity: busyIds.has(a.id) ? 0.6 : 1,
                      }}>{t("approval.approve")}</button>
              <button onClick={() => onReject(a)}
                      disabled={busyIds.has(a.id)}
                      style={{
                        background: "var(--danger)", color: "var(--on-danger)",
                        border: "none", borderRadius: 4,
                        padding: "3px 10px", fontSize: 14,
                        cursor: busyIds.has(a.id) ? "wait" : "pointer",
                        opacity: busyIds.has(a.id) ? 0.6 : 1,
                      }}>{t("approval.reject")}</button>
            </div>
          </div>
        );
      })}
      {pending.length > 5 && (
        <div style={{ padding: "8px 14px", textAlign: "center" }}>
          <a href="#"
             onClick={e => { e.preventDefault(); navigate("/approvals"); setHidden(true); setTimeout(()=>setHidden(false), 500); }}
             style={{ color: "var(--warning)", fontSize: 14 }}>
            {t("approval.view_all", { n: pending.length })}
          </a>
        </div>
      )}
    </div>
  );
}
