import { CrmIcon } from "./CrmIcon";
import { useEffect, useState } from "react";
import { getListBrief, type ListBriefSuggestion } from "@/lib/api";
import { useT } from "@/lib/i18n";

/**
 * "🎯 决策提示 · N" — third tab next to 今天最该处理 / 今日 Coach.
 *
 * Charter v2 §定律 5 ("AI 自动执行 · 事后过目") evolved: the deleted
 * /app/decisions surface used to host an "ask AI about today" chat;
 * after consolidation the chat input lives in the right-rail Copilot.
 * This tab is the proactive bridge — agent-emitted verb-led prompts
 * grounded in "what happened today / what's stuck" that the user can
 * one-tap-prefill into Copilot's input box. Same data shape as the
 * smaller "试试问 AI" chips in CopilotListBrief, just promoted to a
 * tab so it has more room and parity with the other two tabs.
 *
 * Click a chip → ``onAskCopilot(text)`` propagates to Crm.tsx which
 * sets ``copilotPrefill``. The right-rail AgentWorkbench reads that
 * and drops the text into its input, cursor focused, ready to send.
 */

export function TodayDecisionPrompts({
  refreshKey, module, headerless, onCount, onAsk,
}: {
  refreshKey?: number;
  module?: string;
  headerless?: boolean;
  onCount?: (n: number) => void;
  /** Click handler — typically wired to Copilot prefill. When omitted
   *  the chip is rendered as a static hint (no click) so the component
   *  stays useful in contexts that haven't plumbed the callback yet. */
  onAsk?: (text: string) => void;
}) {
  const t = useT();
  const [items, setItems] = useState<ListBriefSuggestion[] | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    setErr(null);
    getListBrief(module)
      .then(r => {
        const list = r.suggestions || [];
        setItems(list);
        onCount?.(list.length);
      })
      .catch(e => {
        setErr(e.message || String(e));
        onCount?.(0);
      });
  }, [refreshKey, module]);

  if (err) return null;
  if (!items || items.length === 0) return null;

  const rows = (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {items.map((s, i) => (
        <PromptChip key={i} text={s.text} onAsk={onAsk} />
      ))}
    </div>
  );

  if (headerless) return rows;

  return (
    <div style={{
      border: "1px solid var(--border)",
      borderRadius: 8,
      padding: "12px 14px",
      marginBottom: 12,
      // theme-guard:ignore-start
      background: "linear-gradient(135deg, rgba(124,92,230,0.06), rgba(92,200,180,0.04))",
      // theme-guard:ignore-end
    }}>
      <div className="row" style={{ alignItems: "baseline", gap: 6, marginBottom: 8 }}>
        <span style={{ fontSize: 14, fontWeight: 600 }}>
          {t("decision_prompts.title", { n: items.length })}
        </span>
      </div>
      {rows}
    </div>
  );
}

function PromptChip({ text, onAsk }: {
  text: string;
  onAsk?: (text: string) => void;
}) {
  const t = useT();
  const clickable = !!onAsk;
  return (
    <button
      onClick={clickable ? () => onAsk!(text) : undefined}
      disabled={!clickable}
      title={clickable ? t("decision_prompts.click_hint") : undefined}
      style={{
        textAlign: "left",
        padding: "10px 14px",
        background: clickable
          ? "var(--accent-soft)"
          : "var(--surface-hover)",
        border: `1px solid ${clickable ? "var(--accent-soft-strong)" : "var(--border)"}`,
        borderRadius: 8,
        color: "inherit",
        cursor: clickable ? "pointer" : "default",
        fontSize: 14, lineHeight: 1.5,
        transition: "background 120ms",
      }}
      onMouseEnter={
        clickable
          ? (e) => (e.currentTarget.style.background = "var(--accent-soft-strong)")
          : undefined
      }
      onMouseLeave={
        clickable
          ? (e) => (e.currentTarget.style.background = "var(--accent-soft)")
          : undefined
      }
    >
      <CrmIcon name="arrow" size={16} /> {text}
    </button>
  );
}
