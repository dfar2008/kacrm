import { useEffect, useState } from "react";
import {
  listCoachHintsForRecord, markCoachHintRead, dismissCoachHint,
  type CoachHint,
} from "@/lib/api";
import { toast } from "@/lib/toast";
import { useT } from "@/lib/i18n";
import { signalIcon } from "@/lib/coachHintVisuals";

/**
 * Charter v2 step-3 "AI 焦点" card (2026-05-08).
 *
 * Surfaces undismissed/unread coach hints scoped to one record at the
 * top of the detail page. Renders nothing when there are no hints,
 * matching the "events-first, projections second" charter principle —
 * the panel only appears when AI has actually noticed something.
 *
 * Each hint:
 *   - severity-coloured left border (red/amber/blue);
 *   - signal slug as a small chip (e.g. "repeated_field_correction");
 *   - the hint text itself;
 *   - 已读 / 忽略 actions that POST to existing endpoints.
 */

interface Props {
  module: string;
  recordId: number;
  refreshKey?: number;
  /** Hand the user-friendly hint text to the Copilot panel as a
      prefilled question (e.g. "为什么会有这条提示?帮我起草下一步")
      so 已读 → 处理 is one click. */
  onAskCopilot?: (prefillText: string) => void;
}

const SEVERITY_BORDER: Record<string, string> = {
  high: "var(--danger)",
  medium: "var(--warning)",
  low: "var(--info)",
};

export function AIFocus({ module, recordId, refreshKey, onAskCopilot }: Props) {
  const t = useT();
  const [hints, setHints] = useState<CoachHint[]>([]);
  const [loading, setLoading] = useState(false);
  const [busyId, setBusyId] = useState<number | null>(null);

  async function reload() {
    setLoading(true);
    try {
      const r = await listCoachHintsForRecord(module, recordId);
      setHints(r.hints || []);
    } catch {
      setHints([]);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [module, recordId, refreshKey]);

  async function onRead(h: CoachHint) {
    if (busyId === h.id) return;
    setBusyId(h.id);
    try {
      await markCoachHintRead(h.id);
      // Local optimistic update — once read, hint disappears from this
      // panel (we requested unread-only; refresh re-fetches).
      reload();
    } catch (e: any) {
      toast(e.message || t("focus.action.failed"), "error");
    } finally { setBusyId(null); }
  }
  async function onDismiss(h: CoachHint) {
    if (busyId === h.id) return;
    setBusyId(h.id);
    try {
      await dismissCoachHint(h.id);
      reload();
    } catch (e: any) {
      toast(e.message || t("focus.action.failed"), "error");
    } finally { setBusyId(null); }
  }

  if (!loading && hints.length === 0) return null;

  return (
    <div className="col" style={{ gap: 6, marginBottom: 12 }}>
      <div className="row" style={{ alignItems: "center", gap: 6 }}>
        {/* Lucide "sparkles" — same family as CRM Copilot CTAs. */}
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
             stroke="currentColor" strokeWidth="2"
             strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M5.6 18.4l2.1-2.1M16.3 7.7l2.1-2.1" />
        </svg>
        <span style={{ fontWeight: 600, fontSize: 14 }}>
          {t("focus.title")}
        </span>
        <span className="muted" style={{ fontSize: 13 }}>· {hints.length}</span>
      </div>

      {hints.map(h => {
        const border = SEVERITY_BORDER[h.severity] || "var(--border)";
        return (
          <div key={h.id} style={{
            border: "1px solid var(--border)",
            borderLeft: `3px solid ${border}`,
            borderRadius: 8,
            padding: "8px 10px",
            background: "var(--surface-hover)",
          }}>
            <div className="row" style={{ alignItems: "baseline", gap: 6 }}>
              <span aria-hidden="true" style={{ fontSize: 14 }}>
                {signalIcon(h.signal)}
              </span>
              <span style={{
                fontSize: 13, padding: "1px 6px", borderRadius: 999,
                background: `color-mix(in srgb, ${border} 20%, transparent)`, color: border,
                textTransform: "uppercase", letterSpacing: 0.4,
              }}>{h.severity}</span>
              <span className="muted" style={{ fontSize: 13 }}>{h.signal}</span>
            </div>
            <div style={{
              fontSize: 14, marginTop: 4,
              whiteSpace: "pre-wrap", wordBreak: "break-word",
            }}>
              {h.hint}
            </div>
            <div className="row" style={{ gap: 6, marginTop: 6 }}>
              {onAskCopilot && (
                <button className="primary" disabled={busyId === h.id}
                        onClick={() => onAskCopilot(
                          `针对 ${module} #${recordId} 的提示:\n${h.hint}\n\n请帮我起草下一步,或解释为什么有这条提示。`
                        )}
                        style={{ fontSize: 13, padding: "2px 10px" }}>
                  {t("focus.action.handle")}
                </button>
              )}
              <button className="ghost" disabled={busyId === h.id}
                      onClick={() => onRead(h)}
                      style={{ fontSize: 13, padding: "2px 8px" }}>
                {t("focus.action.mark_read")}
              </button>
              <button className="ghost" disabled={busyId === h.id}
                      onClick={() => onDismiss(h)}
                      style={{ fontSize: 13, padding: "2px 8px" }}>
                {t("focus.action.dismiss")}
              </button>
            </div>
          </div>
        );
      })}
    </div>
  );
}
