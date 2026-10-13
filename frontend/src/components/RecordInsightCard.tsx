import { useEffect, useState } from "react";
import {
  getRecordInsight,
  type RecordInsight,
  type CoachHint,
  type SuggestedQuestion,
} from "@/lib/api";
import { useT } from "@/lib/i18n";
import { severityColor, signalIcon } from "@/lib/coachHintVisuals";

/**
 * 记录详情 Copilot 首屏卡片 —— 定律 5 (AI 驾驶，人类监督) 的落点。
 *
 * 用户一打开某条记录，这里就已经：
 *   1. 用一句话复述"这条记录是什么状态"（summary）
 *   2. 列出值得此刻关注的异常（hints，动词开头、有下一步）
 *   3. 提供 3 条动词开头的快捷提问（点了就发，由 router agent 自动分流）
 *
 * 渲染是**模块无关**的——数据层按模块分派（见后端
 * insights/scanners/<module>.py），前端只按 shape 渲染。
 *
 * Charter v2 §简化原则 (1 user = 1 profile): scanners no longer hint
 * which agent should answer. The Copilot's intake router decides.
 *
 * 无数据时不渲染任何东西（父组件决定空态兜底）。
 */
interface Props {
  module: string;
  recordId: number;
  onAsk: (text: string) => void;
  /** Compact mode hides the summary + hints, leaving only the
   *  suggested-question chips. Used in CopilotPanel after the user
   *  has already started a conversation — keeps quick prompts
   *  reachable without re-rendering the whole insight block. */
  compact?: boolean;
}

export function RecordInsightCard({ module, recordId, onAsk, compact }: Props) {
  const t = useT();
  const [data, setData] = useState<RecordInsight | null>(null);
  const [err, setErr] = useState(false);
  // Delayed loader: cache hits resolve in ~10ms — don't flash a
  // spinner for those. LLM-fresh generation takes 1-2s, where the
  // user benefits from visible "AI 正在分析…" feedback. Show after
  // 250ms only.
  const [showLoader, setShowLoader] = useState(false);

  useEffect(() => {
    let alive = true;
    setData(null);
    setErr(false);
    setShowLoader(false);
    const loaderTimer = window.setTimeout(() => {
      if (alive) setShowLoader(true);
    }, 250);
    getRecordInsight(module, recordId)
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
  }, [module, recordId]);

  if (err) return null;
  if (!data) {
    // Still loading. Render a placeholder ONLY after the 250ms grace
    // window so cache-hit refreshes don't show a flicker.
    if (!showLoader) return null;
    return <InsightSkeleton compact={!!compact} t={t} />;
  }
  if (!data.has_data) return null;

  const { summary, hints, suggested_questions } = data;
  const nothingToShow =
    !summary && hints.length === 0 && suggested_questions.length === 0;
  if (nothingToShow) return null;

  // Compact mode: just the suggested-question chips (one-liner each),
  // no header / summary / hints. Renders nothing if there are no
  // suggestions on this record.
  if (compact) {
    if (suggested_questions.length === 0) return null;
    return (
      <div style={{
        display: "flex", flexDirection: "column", gap: 4,
        padding: "6px 0",
      }}>
        <div style={{ fontSize: 13, color: "var(--muted)" }}>
          {t("insight.ask_ai")}
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          {suggested_questions.map((q, i) => (
            <QuestionRow key={i} q={q} onClick={() => onAsk(q.text)} />
          ))}
        </div>
      </div>
    );
  }

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
        <span>{t("insight.seen")}</span>
      </div>

      {summary && (
        <div style={{ fontSize: 14, lineHeight: 1.6 }}>{summary}</div>
      )}

      {hints.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
          {hints.map((h, i) => <HintRow key={i} hint={h} />)}
        </div>
      )}

      {suggested_questions.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
          <div style={{ fontSize: 13, color: "var(--muted)" }}>
            {t("insight.ask_ai")}
          </div>
          {suggested_questions.map((q, i) => (
            <QuestionRow key={i} q={q} onClick={() => onAsk(q.text)} />
          ))}
        </div>
      )}
    </div>
  );
}

// Loading placeholder while the LLM generates contextual questions
// (cache miss path, ~1-2s on big-pickle). Shimmer-style chips so the
// user knows something's coming.
function InsightSkeleton({ compact, t }: { compact: boolean; t: ReturnType<typeof useT> }) {
  const bar = (w: string) => (
    <div style={{
      height: 12, width: w, borderRadius: 4,
      background: "var(--surface-hover)",
      animation: "insight-shimmer 1.2s ease-in-out infinite",
    }} />
  );
  if (compact) {
    return (
      <div style={{
        display: "flex", flexDirection: "column", gap: 6,
        padding: "6px 0",
      }}>
        <style>{`@keyframes insight-shimmer{0%,100%{opacity:.5}50%{opacity:1}}`}</style>
        <div style={{ fontSize: 13, color: "var(--muted)",
                       display: "flex", alignItems: "center", gap: 6 }}>
          <span>🤖</span>
          <span>{t("insight.thinking")}</span>
        </div>
        {bar("90%")}
        {bar("75%")}
        {bar("85%")}
      </div>
    );
  }
  return (
    <div style={{
      border: "1px solid var(--border)",
      borderRadius: 8,
      background: "var(--surface-hover)",
      padding: "10px 12px",
      display: "flex", flexDirection: "column", gap: 10,
    }}>
      <style>{`@keyframes insight-shimmer{0%,100%{opacity:.5}50%{opacity:1}}`}</style>
      <div style={{
        display: "flex", alignItems: "center", gap: 6,
        fontSize: 14, color: "var(--muted)",
      }}>
        <span style={{ fontSize: 14 }}>🤖</span>
        <span>{t("insight.thinking")}</span>
      </div>
      {bar("80%")}
      {bar("60%")}
      <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 4 }}>
        {bar("95%")}
        {bar("85%")}
        {bar("90%")}
      </div>
    </div>
  );
}

function HintRow({ hint }: { hint: CoachHint }) {
  const sevColor = severityColor(hint.severity);
  const icon = signalIcon(hint.signal);
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
      <span>{hint.hint}</span>
    </div>
  );
}

function QuestionRow({ q, onClick }: { q: SuggestedQuestion; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
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
      → {q.text}
    </button>
  );
}
