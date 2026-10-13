/**
 * Shared visual helpers for rendering ``CoachHint`` rows.
 *
 * The two HintRow components in the SPA (in ``RecordInsightCard.tsx``
 * and ``WeeklyAiReportCard.tsx``) have different layouts and
 * interaction shapes (one is read-only, the other has unread state +
 * dismiss button), but they always agreed on two derivations:
 *
 *   1. severity → border color
 *   2. signal name → emoji icon
 *
 * Centralising them here keeps the icon catalog in one place so a new
 * scanner signal only needs one map edit, and the third surface
 * (``AIFocus.tsx`` on record detail) can opt into the same icons.
 */

/** Severity → left-border color (also used as chip background tint). */
export function severityColor(severity: string | undefined): string {
  // theme-guard:ignore-start  — severity-semantic palette, data-driven map
  switch ((severity || "").toLowerCase()) {
    case "high":   return "#e66";
    case "medium": return "#f5a623";
    default:       return "#8a9ab0";
  }
  // theme-guard:ignore-end
}

/**
 * Signal name → single-emoji icon.
 *
 * Matches by substring (``signal.includes(...)``) so scanner authors
 * can coin new signals like ``pipeline_silence_legacy`` or
 * ``orphan_commitment_email`` and they fall into the right visual
 * bucket automatically. Unknown signals fall through to a plain
 * bullet — never crashes the row.
 */
export function signalIcon(signal: string | undefined): string {
  const s = (signal || "").toLowerCase();
  if (s.includes("silence"))    return "⏳";
  if (s.includes("stuck"))      return "🧱";
  if (s.includes("regression")) return "📉";
  if (s.includes("orphan"))     return "🤝";
  if (s.includes("commitment")) return "🤝";
  if (s.includes("cold"))       return "❄️";
  if (s.includes("missing"))    return "⚠️";
  if (s.includes("vague"))      return "💬";
  if (s.includes("convert"))    return "🚀";
  if (s.includes("correction")) return "✏️";
  if (s.includes("first_touch"))return "📞";
  return "•";
}
