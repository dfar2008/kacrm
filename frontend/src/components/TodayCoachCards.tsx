import { useEffect, useState } from "react";
import { useRouter } from "@/lib/router";
import { getListBrief, type ListBriefCoachCard } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { CrmIcon } from "./CrmIcon";

/**
 * "📋 今日 Coach · N" — main-area tab next to TodayPriority.
 *
 * Charter v2 §定律 4: single agent + skill emits coach.proposal cards
 * via the cron task. Cards live in gateway_actions (no parallel ledger).
 * This component is the user-facing surface for that work — full draft
 * text shown verbatim, single 📋 icon to copy to clipboard.
 *
 * Used inside <TodayPriorityTabs/>; renders only the row list when
 * `headerless` is set so the wrapper owns the outer card frame.
 */

export function TodayCoachCards({ refreshKey, module, headerless, onCount }: {
  refreshKey?: number;
  module?: string;
  headerless?: boolean;
  onCount?: (n: number) => void;
}) {
  const t = useT();
  const [cards, setCards] = useState<ListBriefCoachCard[] | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    setErr(null);
    getListBrief(module)
      .then(r => {
        const list = r.coach_cards || [];
        setCards(list);
        onCount?.(list.length);
      })
      .catch(e => {
        setErr(e.message || String(e));
        onCount?.(0);
      });
  }, [refreshKey, module]);

  if (err) return null;
  if (!cards || cards.length === 0) return null;

  const rows = (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {cards.map(c => <CoachRow key={c.id} card={c} />)}
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
      background: "linear-gradient(135deg, rgba(92,134,230,0.06), rgba(92,200,180,0.04))",
      // theme-guard:ignore-end
    }}>
      <div className="row" style={{ alignItems: "baseline", gap: 6, marginBottom: 8 }}>
        <span style={{ fontSize: 14, fontWeight: 600 }}>
          {t("brief.coach_cards_title", { n: cards.length })}
        </span>
      </div>
      {rows}
    </div>
  );
}

function CoachRow({ card }: { card: ListBriefCoachCard }) {
  const t = useT();
  const { navigate } = useRouter();
  const [copied, setCopied] = useState(false);

  const sevColor =
    card.severity === "high"   ? "var(--danger)" :
    card.severity === "medium" ? "var(--warning)" :
                                 "var(--muted)";

  const hint = card.hint || t("brief.coach_card_no_summary");
  const draft = (card.draft || "").trim();

  const onCopy = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!draft) return;
    try {
      await navigator.clipboard.writeText(draft);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      // Browser blocked clipboard (insecure context, perms). Fall back to
      // a prompt() so user can still Cmd-C the text manually.
      window.prompt(t("brief.coach_card_copy_fallback"), draft);
    }
  };

  const onOpen = () => {
    if (card.module && card.record_id) {
      navigate(`/crm/${card.module}/${card.record_id}`);
    }
  };

  return (
    <article className="kacrm-coach-card" style={{ borderLeftColor: sevColor }}>
      {/* headline + copy icon */}
      <div style={{ display: "flex", alignItems: "flex-start", gap: 8 }}>
        <button className="ghost kacrm-coach-title"
          onClick={onOpen} disabled={!card.module || !card.record_id}
          title={card.module && card.record_id
            ? t("brief.coach_card_open_title", {
                module: card.module, id: String(card.record_id),
              })
            : undefined}
        >
          {hint}
        </button>
        {draft && (
          <button
            onClick={onCopy}
            className="ghost kacrm-copy-action"
            title={copied ? t("brief.coach_card_copied") : t("brief.coach_card_copy")}
            aria-label={copied ? t("brief.coach_card_copied") : t("brief.coach_card_copy")}
            style={{
              border: "1px solid var(--border)",
              borderRadius: 6,
              padding: "2px 8px",
              background: copied ? "var(--success-soft)" : "transparent",
              color: "inherit",
              cursor: "pointer",
              fontSize: 14,
              lineHeight: 1.2,
            }}
          >
            <CrmIcon name={copied ? "check" : "copy"} size={16} />
            {copied ? t("brief.coach_card_copied") : t("brief.coach_card_copy")}
          </button>
        )}
      </div>
      {/* full draft text, verbatim */}
      {draft && (
        <pre className="kacrm-coach-draft">{draft}</pre>
      )}
      {card.reasoning && (
        <details className="kacrm-reasoning">
          <summary><CrmIcon name="chevron" size={16} />{t("brief.coach_card_reasoning")}</summary>
          <p>{card.reasoning}</p>
        </details>
      )}
    </article>
  );
}
