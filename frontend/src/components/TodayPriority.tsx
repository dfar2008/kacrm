import { useEffect, useState } from "react";
import { useRouter } from "@/lib/router";
import { getTodayPriority, type TodayPriorityItem } from "@/lib/api";
import { useT, type DictKey } from "@/lib/i18n";

/**
 * "🚨 今天最该处理 · N" — top-of-DailyFeed cross-module urgency feed.
 *
 * Charter v2 §定律 1 + §定律 5: SQL-deterministic ranking based on
 * events (coach_hints, interaction silence, overdue next_contact_date).
 * Surfaces automatically without the user having to filter / sort the
 * accounts list. **Replaces the old "users go to list page → click a
 * sort button" flow** which was a records-first, gate-shaped UX.
 *
 * Renders nothing if the backend returns no items.
 */

const MODULE_LABEL_KEY: Record<string, DictKey> = {
  accounts: "daily.module.accounts",
  contacts: "daily.module.contacts",
  leads: "daily.module.leads",
  potentials: "daily.module.potentials",
};

function reasonIcon(kind: string): string {
  if (kind === "coach_hint") return "🎓";
  if (kind === "overdue") return "🔴";
  if (kind === "silence") return "🧱";
  return "•";
}

export function TodayPriority({ refreshKey, module, headerless, onCount }: {
  refreshKey?: number;
  /** Pass to scope the priority feed to one module (per-module daily
   *  dashboard). Omit for the cross-module global view at /crm. */
  module?: string;
  /** When true, render only the row list (no outer card / title).
   *  Used by TodayPriorityTabs which provides its own card frame. */
  headerless?: boolean;
  /** Reports current item count to a parent tab strip so it can show
   *  "🚨 今天最该处理 · N" tab labels. Fires on every fetch. */
  onCount?: (n: number) => void;
}) {
  const t = useT();
  const { navigate } = useRouter();
  const [items, setItems] = useState<TodayPriorityItem[] | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    setErr(null);
    getTodayPriority(5, module)
      .then(r => {
        const list = r.items || [];
        setItems(list);
        onCount?.(list.length);
      })
      .catch(e => {
        setErr(e.message || String(e));
        onCount?.(0);
      });
  }, [refreshKey, module]);

  if (err) return null; // silent — not worth crowding the feed with errors
  if (!items || items.length === 0) return null;

  const rows = (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      {items.map((it, i) => (
        <Row key={`${it.module}:${it.record_id}`}
             item={it} rank={i + 1}
             moduleLabel={MODULE_LABEL_KEY[it.module]
               ? t(MODULE_LABEL_KEY[it.module])
               : it.module}
             onOpen={() => navigate(`/crm/${it.module}/${it.record_id}`)}
             onAct={(action) => {
               // Charter §定律 5: navigate to the record AND prefill
               // Copilot with the suggested action so the user just
               // presses Send. ?ask=... is read by Crm.tsx on mount
               // and translated to copilotPrefill state.
               const q = new URLSearchParams({ ask: action.text });
               navigate(`/crm/${it.module}/${it.record_id}?${q}`);
             }} />
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
      background: "linear-gradient(135deg, rgba(224,74,74,0.06), rgba(224,166,74,0.04))",
      // theme-guard:ignore-end
    }}>
      <div className="row" style={{ alignItems: "baseline", gap: 6, marginBottom: 8 }}>
        <span style={{ fontSize: 14, fontWeight: 600 }}>
          {t("today_priority.title", { n: items.length })}
        </span>
        <span className="muted" style={{ fontSize: 13 }}>
          {t("today_priority.hint")}
        </span>
      </div>
      {rows}
    </div>
  );
}

function Row({ item, rank, moduleLabel, onOpen, onAct }: {
  item: TodayPriorityItem;
  rank: number;
  moduleLabel: string;
  onOpen: () => void;
  onAct: (action: NonNullable<TodayPriorityItem["next_action"]>) => void;
}) {
  const top2 = item.reasons.slice(0, 2);
  const action = item.next_action || null;
  // Charter §定律 5: TodayPriority is "what to act on today". Default
  // row click goes into ACT mode (navigate + Copilot prefill) rather
  // than passive view. Users get to where they were going faster, the
  // small action button just previews what the prefill will be.
  const handleRowClick = () => {
    if (action) onAct(action);
    else onOpen();
  };
  return (
    <div onClick={handleRowClick}
         style={{
           display: "grid",
           gridTemplateColumns: "32px 1fr auto",
           gap: 10, alignItems: "center",
           padding: "8px 10px",
           border: "1px solid var(--border)", borderRadius: 6,
           background: "var(--surface-hover)",
           cursor: "pointer",
         }}>
      <span style={{
        fontSize: 14, fontWeight: 700,
        color: rank === 1 ? "var(--danger)" : rank === 2 ? "var(--warning)" : "var(--muted)",
      }}>#{rank}</span>
      <div style={{ overflow: "hidden" }}>
        <div style={{ fontSize: 14 }}>
          <strong>{item.name || `${moduleLabel} #${item.record_id}`}</strong>
          <span className="muted" style={{ marginLeft: 6, fontSize: 13 }}>
            · {moduleLabel}
          </span>
        </div>
        <div className="muted" style={{
          fontSize: 13, marginTop: 2,
          whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
        }}>
          {top2.map((r, i) => (
            <span key={i} style={{ marginRight: 8 }}>
              {reasonIcon(r.kind)} {r.text}
            </span>
          ))}
        </div>
        {action && (
          <div
            style={{
              marginTop: 6, padding: "2px 10px",
              fontSize: 13, fontWeight: 600,
              border: "1px solid var(--accent-soft-strong)",
              borderRadius: 999,
              background: "var(--accent-soft)",
              color: "var(--accent)",
              display: "inline-block",
            }}
            title="整行点击 = 跳到记录 + Copilot 已填好这句话"
          >
            🤖 → {action.text}
          </div>
        )}
      </div>
      <span style={{
        fontSize: 13, fontWeight: 700,
        padding: "2px 8px", borderRadius: 999,
        background: "var(--danger-soft)", color: "var(--danger)",
      }}>{item.score}</span>
    </div>
  );
}
