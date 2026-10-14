import { useState } from "react";
import { useT } from "@/lib/i18n";
import { TodayPriority } from "@/components/TodayPriority";
import { TodayCoachCards } from "@/components/TodayCoachCards";
import { TodayDecisionPrompts } from "@/components/TodayDecisionPrompts";
import { CrmIcon } from "./CrmIcon";

/**
 * Tab strip at the top of DailyFeed that hosts three AI-driven surfaces
 * side by side:
 *   - 🚨 今天最该处理 — SQL-deterministic cross-module ranking
 *   - 📋 今日 Coach   — crm-daily-coach skill output (copy-ready drafts)
 *   - 🎯 决策提示     — agent-emitted verb-led prompts; click prefills Copilot
 *
 * All three children mount and fetch immediately so their tab counts
 * show up even when the user hasn't clicked over yet. The inactive
 * tab's content is hidden via display:none rather than unmounted to
 * preserve fetch state when the user toggles back.
 *
 * Renders nothing when all three surfaces are empty — keeps the feed
 * uncluttered on quiet days.
 */

type TabKey = "priority" | "coach" | "decisions";

export function TodayPriorityTabs({ refreshKey, module, onAskCopilot }: {
  refreshKey?: number;
  module?: string;
  /** Called when the user clicks a decision-prompt chip. Should set
   *  the right-rail Copilot prefill state (owned by Crm.tsx). */
  onAskCopilot?: (text: string) => void;
}) {
  const t = useT();
  const [active, setActive] = useState<TabKey>("priority");
  const [priorityN, setPriorityN] = useState<number | null>(null);
  const [coachN, setCoachN] = useState<number | null>(null);
  const [decisionsN, setDecisionsN] = useState<number | null>(null);

  // Hide the whole strip when every surface is empty (silent days).
  const allEmpty = priorityN === 0 && coachN === 0 && decisionsN === 0;
  if (allEmpty) return null;

  const tabBtn = (key: TabKey, label: string, n: number | null) => {
    const isActive = effective === key;
    const disabled = n === 0;
    return (
      <button
        key={key}
        className="kacrm-priority-tab"
        aria-pressed={isActive}
        onClick={() => !disabled && setActive(key)}
        disabled={disabled}
        style={{
          background: isActive ? "var(--accent-soft-strong)" : "transparent",
          border: "1px solid",
          borderColor: isActive ? "var(--accent)" : "var(--border)",
          color: disabled ? "var(--muted)" : "inherit",
          padding: "4px 12px",
          borderRadius: 999,
          fontSize: 14,
          fontWeight: isActive ? 600 : 400,
          cursor: disabled ? "default" : "pointer",
          opacity: disabled ? 0.5 : 1,
        }}
      >
        <CrmIcon name={key === "priority" ? "alert" : key === "coach" ? "clipboard" : "target"} size={16} />
        {label}{n != null && <span className="kacrm-tab-count">{n}</span>}
      </button>
    );
  };

  // If the user-picked active tab is empty, fall through to the first
  // non-empty one in the natural order.
  const order: TabKey[] = ["priority", "coach", "decisions"];
  const counts: Record<TabKey, number | null> = {
    priority: priorityN, coach: coachN, decisions: decisionsN,
  };
  const effective: TabKey =
    counts[active] !== 0
      ? active
      : (order.find(k => (counts[k] ?? 0) > 0) ?? active);

  const hintKey: Record<TabKey, string> = {
    priority:  "today_priority.hint",
    coach:     "brief.coach_cards_hint",
    decisions: "decision_prompts.hint",
  };

  return (
    <section className="kacrm-priorities">
      <div style={{
        display: "flex", alignItems: "center", gap: 8, marginBottom: 10,
        flexWrap: "wrap",
      }}>
        {tabBtn("priority",  t("today_priority.title_short"),    priorityN)}
        {tabBtn("coach",     t("brief.coach_cards_title_short"), coachN)}
        {tabBtn("decisions", t("decision_prompts.title_short"),  decisionsN)}
      </div>
      <p className="kacrm-priority-hint">{t(hintKey[effective] as any)}</p>

      <div style={{ display: effective === "priority" ? "block" : "none" }}>
        <TodayPriority
          refreshKey={refreshKey}
          module={module}
          headerless
          onCount={setPriorityN}
        />
      </div>
      <div style={{ display: effective === "coach" ? "block" : "none" }}>
        <TodayCoachCards
          refreshKey={refreshKey}
          module={module}
          headerless
          onCount={setCoachN}
        />
      </div>
      <div style={{ display: effective === "decisions" ? "block" : "none" }}>
        <TodayDecisionPrompts
          refreshKey={refreshKey}
          module={module}
          headerless
          onCount={setDecisionsN}
          onAsk={onAskCopilot}
        />
      </div>
    </section>
  );
}
