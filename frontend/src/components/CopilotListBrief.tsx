import { useEffect, useState } from "react";
import { getListBrief, type ListBrief } from "@/lib/api";
import { useT } from "@/lib/i18n";

/**
 * Renders the Copilot panel's empty state on /crm and per-module
 * dashboards. Mirrors RecordInsightCard shape (summary + AI-noticed
 * + suggestions) so list-mode and record-mode feel like one product.
 *
 * Charter §定律 5: AI is proactive everywhere. Without this, opening
 * a list page sat the user in front of a silent input box; with it,
 * they immediately see "AI's read on the day" plus 3 verb-led
 * prompts they can click straight into.
 *
 * Loading delay: 250ms grace window (cache hits resolve in ~10ms;
 * skeleton only shows for true LLM-fresh / llm_pending paths).
 */

interface Props {
  module?: string;
  onAsk: (text: string) => void;
}

export function CopilotListBrief({ module, onAsk }: Props) {
  const t = useT();
  const [data, setData] = useState<ListBrief | null>(null);
  const [err, setErr] = useState(false);
  const [showLoader, setShowLoader] = useState(false);

  useEffect(() => {
    let alive = true;
    setData(null);
    setErr(false);
    setShowLoader(false);
    const loaderTimer = window.setTimeout(() => {
      if (alive) setShowLoader(true);
    }, 250);
    getListBrief(module)
      .then(r => { if (alive) setData(r); })
      .catch(() => { if (alive) setErr(true); })
      .finally(() => {
        window.clearTimeout(loaderTimer);
        if (alive) setShowLoader(false);
      });
    return () => {
      alive = false;
      window.clearTimeout(loaderTimer);
    };
  }, [module]);

  if (err) return null;
  if (!data) {
    if (!showLoader) return null;
    return <Skeleton t={t} />;
  }

  const hasAnything = !!(
    data.summary ||
    (data.ai_noticed && data.ai_noticed.length) ||
    (data.suggestions && data.suggestions.length)
  );
  // Note: data.coach_cards is also returned by /api/insights/list_brief
  // and consumed by <TodayCoachCards/> in DailyFeed (main area tabs).
  // Don't render coach cards here — right rail stays focused on
  // summary + signals + suggestion chips.
  if (!hasAnything) return null;

  return (
    <div style={{
      border: "1px solid var(--border)",
      borderRadius: 8,
      background: "var(--surface-hover)",
      padding: "10px 12px",
      display: "flex", flexDirection: "column", gap: 10,
    }}>
      <div style={{
        display: "flex", alignItems: "center", gap: 6,
        fontSize: 14, color: "var(--muted)",
      }}>
        <span style={{ fontSize: 14 }}>🤖</span>
        <span>{t("brief.seen")}</span>
      </div>

      {data.summary && (
        <div style={{ fontSize: 14, lineHeight: 1.6 }}>{data.summary}</div>
      )}

      {data.ai_noticed && data.ai_noticed.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
          {data.ai_noticed.map((n, i) => <NoticedRow key={i} item={n} />)}
        </div>
      )}

      {/* Coach cards moved to main-area <TodayCoachCards/> tab. */}

      {data.suggestions && data.suggestions.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
          <div style={{ fontSize: 13, color: "var(--muted)" }}>
            {t("brief.ask_ai")}
          </div>
          {data.suggestions.map((s, i) => (
            <button
              key={i}
              onClick={() => onAsk(s.text)}
              style={{
                textAlign: "left",
                padding: "6px 10px",
                background: "var(--accent-soft)",
                border: "1px solid color-mix(in srgb, var(--accent) 25%, transparent)",
                borderRadius: 6,
                color: "inherit",
                cursor: "pointer",
                fontSize: 14,
              }}
              onMouseEnter={e => (e.currentTarget.style.background = "var(--accent-soft-strong)")}
              onMouseLeave={e => (e.currentTarget.style.background = "var(--accent-soft)")}
            >
              → {s.text}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function NoticedRow({ item }: { item: ListBrief["ai_noticed"][number] }) {
  const sevColor =
    item.severity === "high"   ? "var(--danger)" :
    item.severity === "medium" ? "var(--warning)" :
                                 "var(--muted)";
  const icon =
    item.signal.includes("silent")     ? "⏳" :
    item.signal.includes("uncontact")  ? "👋" :
    item.signal.includes("repeat")     ? "✏️" :
    item.signal.includes("unread")     ? "🔔" :
    "•";
  return (
    <div style={{
      display: "grid", gridTemplateColumns: "20px 1fr", gap: 8,
      alignItems: "start",
      padding: "6px 8px",
      background: "var(--surface-hover)",
      borderLeft: `3px solid ${sevColor}`,
      borderRadius: 4,
      fontSize: 14, lineHeight: 1.5,
    }}>
      <span style={{ fontSize: 14 }}>{icon}</span>
      <span>{item.text}</span>
    </div>
  );
}

function Skeleton({ t }: { t: ReturnType<typeof useT> }) {
  const bar = (w: string) => (
    <div style={{
      height: 12, width: w, borderRadius: 4,
      background: "var(--surface-hover)",
      animation: "brief-shimmer 1.2s ease-in-out infinite",
    }} />
  );
  return (
    <div style={{
      border: "1px solid var(--border)",
      borderRadius: 8,
      background: "var(--surface-hover)",
      padding: "10px 12px",
      display: "flex", flexDirection: "column", gap: 10,
    }}>
      <style>{`@keyframes brief-shimmer{0%,100%{opacity:.5}50%{opacity:1}}`}</style>
      <div style={{
        display: "flex", alignItems: "center", gap: 6,
        fontSize: 14, color: "var(--muted)",
      }}>
        <span style={{ fontSize: 14 }}>🤖</span>
        <span>{t("brief.thinking")}</span>
      </div>
      {bar("85%")}
      {bar("65%")}
      <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 4 }}>
        {bar("90%")}
        {bar("80%")}
        {bar("85%")}
      </div>
    </div>
  );
}
