import { useEffect, useState } from "react";
import { useRouter } from "@/lib/router";
import { getDailyFeed, DailyFeed as DailyFeedT, DailyFeedItem } from "@/lib/api";
import { useT, type T, type DictKey } from "@/lib/i18n";
import { CrmIcon, type CrmIconName } from "./CrmIcon";
import { TodayPriorityTabs } from "@/components/TodayPriorityTabs";

/**
 * ``/crm`` default + per-module 今日动态.
 *
 * Charter v2 alignment (2026-05-08):
 *   §定律 1 (主数据是事件流) — interactions are the spine.
 *   §定律 5 (AI 驾驶 + 人类监督) — AI auto-executes; "等你拍板" is gone.
 *     Agent actions surface RETROSPECTIVELY as interactions whose
 *     ``source`` starts with ``agent:`` (24h audit window).
 *
 * Sections per module are limited to:
 *   - 🤖 AI 刚处理 (retro audit, click-through to revert via timeline)
 *   - 🎓 AI 焦点  (coach_hints — pure post-hoc observations)
 *   - 💬 互动     (the event stream itself)
 *
 * No pre-action approval cards. No specialist queues (agent_drafts /
 * pending_drafts dimensions were deleted in Phase 3).
 */

type TypeKey =
  | "daily.type.interaction" | "daily.type.coach_hint"
  | "daily.type.outcome.won" | "daily.type.outcome.stage_advanced"
  | "daily.type.outcome.first_touch" | "daily.type.outcome.other";
const TYPE_LABEL_KEY: Record<string, TypeKey> = {
  interaction: "daily.type.interaction",
  coach_hint:  "daily.type.coach_hint",
  outcome_event: "daily.type.outcome.other",
};

function outcomeTypeKey(it: DailyFeedItem): TypeKey | null {
  if (it.item_type !== "outcome_event") return null;
  if (it.event_type === "deal.won") return "daily.type.outcome.won";
  if (it.event_type === "stage.advanced") return "daily.type.outcome.stage_advanced";
  if (it.event_type === "record.first_touch") return "daily.type.outcome.first_touch";
  return "daily.type.outcome.other";
}

function iconFor(item: DailyFeedItem): CrmIconName {
  if (isAgentInteraction(item)) return "sparkles";
  if (item.item_type === "coach_hint") return "target";
  if (item.event_type === "deal.won") return "trophy";
  if (item.event_type === "stage.advanced") return "trend";
  if (item.event_type === "record.first_touch") return "phone";
  if (item.kind === "email") return "mail";
  if (item.kind === "call") return "phone";
  if (item.kind === "meeting") return "calendar";
  return "message";
}

const SECTION_ICONS: Record<string, CrmIconName> = {
  agent_recent: "sparkles", coach: "target", won: "trophy",
  stages: "trend", first_touch: "phone",
};

function fmtMoney(t: T, n: number | null | undefined): string {
  if (!n && n !== 0) return t("daily.money.dash");
  if (Math.abs(n) >= 1e8) return (n / 1e8).toFixed(2) + " " + t("daily.money.yi");
  if (Math.abs(n) >= 1e4) return (n / 1e4).toFixed(1) + " " + t("daily.money.wan");
  return String(n);
}

// Mirrors backend ``insights.api._is_ai_source``: count AI-initiated
// activity, but exclude the user's own typed Copilot questions
// (``copilot:user``) — those are human input, not AI output.
const isAgentInteraction = (it: DailyFeedItem) => {
  if (it.item_type !== "interaction" || typeof it.source !== "string") {
    return false;
  }
  if (it.source.startsWith("agent:")) return true;
  if (it.source.startsWith("copilot") && it.source !== "copilot:user") return true;
  return false;
};

// Raw ``source`` strings ("agent:admin:agent:hermes",
// "copilot:surface:auto_drive") are internal provenance tokens —
// meaningless to an end user. Collapse them to a plain-language actor
// label for the feed row.
function sourceLabel(source: string | undefined): string | null {
  if (!source) return null;
  if (source === "copilot:user") return "你";
  if (source.startsWith("agent:") || source.startsWith("copilot")) return "AI 助手";
  if (source === "manual") return "手动录入";
  if (source.startsWith("workflow")) return "自动化";
  if (source.startsWith("channel:")) return "渠道";
  return null;
}

// -------------------------------------------------------------------------
// Per-module focus.
// -------------------------------------------------------------------------
type StatKey =
  | "won" | "stages" | "first_touch"
  | "agent_recent" | "interactions" | "coach_hints"
  | "ar_total_due" | "ar_received" | "ar_overdue" | "ar_overdue_count"
  | "ap_month_received";

interface SectionDef {
  key: string;
  titleKey: DictKey;
  hintKey: DictKey;
  emptyKey: DictKey;
  filter: (it: DailyFeedItem) => boolean;
}

interface FocusDef {
  subtitleKey: DictKey;
  stats: StatKey[];
  sections: SectionDef[];
}

const F = {
  agentRecent: (): SectionDef => ({
    key: "agent_recent",
    titleKey: "daily.sec.agent_recent.title",
    hintKey: "daily.sec.agent_recent.hint",
    emptyKey: "daily.sec.agent_recent.empty",
    filter: isAgentInteraction,
  }),
  coach: (): SectionDef => ({
    key: "coach",
    titleKey: "daily.sec.coach.title",
    hintKey: "daily.sec.coach.hint",
    emptyKey: "daily.sec.coach.empty",
    filter: it => it.item_type === "coach_hint",
  }),
  interactions: (titleKey: DictKey): SectionDef => ({
    key: "interactions:" + titleKey, titleKey,
    hintKey: "daily.sec.interact.hint",
    emptyKey: "daily.sec.interact.empty",
    // Human-only interactions (drop agent rows — they're shown in the
    // dedicated 🤖 AI 刚处理 section above to avoid double-counting).
    filter: it => it.item_type === "interaction" && !isAgentInteraction(it),
  }),
  // Outcome-event sections (drilled into from the matching stat tile).
  won: (): SectionDef => ({
    key: "won",
    titleKey: "daily.sec.won.title",
    hintKey: "daily.sec.won.hint",
    emptyKey: "daily.sec.won.empty",
    filter: it => it.item_type === "outcome_event" && it.event_type === "deal.won",
  }),
  stages: (): SectionDef => ({
    key: "stages",
    titleKey: "daily.sec.stages.title",
    hintKey: "daily.sec.stages.hint",
    emptyKey: "daily.sec.stages.empty",
    filter: it => it.item_type === "outcome_event" && it.event_type === "stage.advanced",
  }),
  firstTouch: (): SectionDef => ({
    key: "first_touch",
    titleKey: "daily.sec.first_touch.title",
    hintKey: "daily.sec.first_touch.hint",
    emptyKey: "daily.sec.first_touch.empty",
    filter: it => it.item_type === "outcome_event" && it.event_type === "record.first_touch",
  }),
};

const DEFAULT_FOCUS: FocusDef = {
  subtitleKey: "daily.sub.default",
  stats: ["won", "stages", "first_touch", "agent_recent"],
  sections: [
    F.agentRecent(),
    F.coach(),
    F.interactions("daily.sec.interact.title_today"),
  ],
};

const MODULE_FOCUS: Record<string, FocusDef> = {
  accounts: {
    subtitleKey: "daily.sub.accounts",
    stats: ["won", "stages", "first_touch", "agent_recent"],
    sections: [
      F.agentRecent(),
      F.coach(),
      F.interactions("daily.sec.interact.title_recent"),
    ],
  },
  contacts: {
    subtitleKey: "daily.sub.contacts",
    stats: ["first_touch", "interactions", "agent_recent"],
    sections: [
      F.agentRecent(),
      F.coach(),
      F.interactions("daily.sec.interact.title_recent"),
    ],
  },
  leads: {
    subtitleKey: "daily.sub.leads",
    stats: ["first_touch", "agent_recent", "coach_hints"],
    sections: [
      F.agentRecent(),
      F.coach(),
      F.interactions("daily.sec.interact.title_recent"),
    ],
  },
  potentials: {
    subtitleKey: "daily.sub.potentials",
    stats: ["won", "stages", "agent_recent", "coach_hints"],
    sections: [
      F.agentRecent(),
      F.coach(),
      F.interactions("daily.sec.interact.title_recent"),
    ],
  },
  quotes: {
    subtitleKey: "daily.sub.quotes",
    stats: ["interactions", "agent_recent", "coach_hints"],
    sections: [
      F.agentRecent(),
      F.coach(),
      F.interactions("daily.sec.interact.title_recent"),
    ],
  },
  salesorders: {
    subtitleKey: "daily.sub.salesorders",
    stats: ["interactions", "agent_recent", "coach_hints"],
    sections: [
      F.agentRecent(),
      F.coach(),
      F.interactions("daily.sec.interact.title_orders"),
    ],
  },
  tasks: {
    subtitleKey: "daily.sub.tasks",
    stats: ["interactions", "agent_recent", "coach_hints"],
    sections: [
      F.agentRecent(),
      F.coach(),
      F.interactions("daily.sec.interact.title_tasks"),
    ],
  },
  products: {
    subtitleKey: "daily.sub.products",
    stats: ["interactions", "agent_recent", "coach_hints"],
    sections: [
      F.agentRecent(),
      F.coach(),
      F.interactions("daily.sec.interact.title_products"),
    ],
  },
  receivables: {
    subtitleKey: "daily.sub.receivables",
    stats: ["ar_total_due", "ar_received", "ar_overdue",
            "ar_overdue_count", "agent_recent"],
    sections: [
      F.agentRecent(),
      F.coach(),
      F.interactions("daily.sec.interact.title_recent"),
    ],
  },
  payments: {
    subtitleKey: "daily.sub.payments",
    stats: ["ap_month_received", "interactions", "agent_recent"],
    sections: [
      F.agentRecent(),
      F.coach(),
      F.interactions("daily.sec.interact.title_payments"),
    ],
  },
};

function statFor(t: T, k: StatKey, d: DailyFeedT): {
  label: string; value: string; sub?: string;
} {
  switch (k) {
    case "won":
      return {
        label: t("daily.stat.won.label"),
        value: String(d.outcomes.deals_won),
        sub: d.outcomes.deals_won_amount !== null
          ? `¥${fmtMoney(t, d.outcomes.deals_won_amount)}`
          : undefined,
      };
    case "stages":
      return { label: t("daily.stat.stages.label"), value: String(d.outcomes.stages_advanced) };
    case "first_touch":
      return { label: t("daily.stat.first_touch.label"), value: String(d.outcomes.first_touch) };
    case "agent_recent": {
      // Defensive: fall back to client-side recount if the backend
      // response predates the agent_recent field (uvicorn not yet
      // reloaded after backend edit) → otherwise we'd render
      // "undefined" in the stat tile.
      const n = typeof d.counts.agent_recent === "number"
        ? d.counts.agent_recent
        : d.items.filter(i =>
            i.item_type === "interaction"
            && typeof i.source === "string"
            && i.source.startsWith("agent:")
          ).length;
      return {
        label: t("daily.stat.agent_recent.label"),
        value: String(n),
        sub: n > 0
          ? t("daily.stat.agent_recent.sub")
          : t("daily.stat.agent_recent.none"),
      };
    }
    case "interactions":
      return { label: t("daily.stat.interactions.label"), value: String(d.counts.interactions ?? 0) };
    case "coach_hints":
      return {
        label: t("daily.stat.coach_hints.label"),
        value: String(d.counts.coach_hints ?? 0),
        sub: t("daily.stat.coach_hints.sub"),
      };
    case "ar_total_due":
      return {
        label: t("daily.stat.ar_total_due.label"),
        value: `¥${fmtMoney(t, d.money?.total_due ?? 0)}`,
        sub: t("daily.stat.ar_total_due.sub"),
      };
    case "ar_received":
      return {
        label: t("daily.stat.ar_received.label"),
        value: `¥${fmtMoney(t, d.money?.total_received ?? 0)}`,
        sub: t("daily.stat.ar_received.sub"),
      };
    case "ar_overdue": {
      const v = d.money?.total_overdue ?? 0;
      const aging = d.money?.aging;
      const ageSub = aging
        ? t("daily.stat.ar_overdue.aging", {
            a: fmtMoney(t, aging["0_30"]),
            b: fmtMoney(t, aging["31_60"]),
            c: fmtMoney(t, aging["60_plus"]),
          })
        : null;
      return {
        label: t("daily.stat.ar_overdue.label"),
        value: `¥${fmtMoney(t, v)}`,
        sub: ageSub
          || (v > 0 ? t("daily.stat.ar_overdue.need_collect") : t("daily.stat.ar_overdue.none")),
      };
    }
    case "ar_overdue_count": {
      const n = d.money?.overdue_count ?? 0;
      return {
        label: t("daily.stat.ar_overdue_count.label"),
        value: String(n),
        sub: n > 0 ? t("daily.stat.ar_overdue_count.has") : t("daily.stat.ar_overdue_count.none"),
      };
    }
    case "ap_month_received":
      return {
        label: t("daily.stat.ap_month_received.label"),
        value: `¥${fmtMoney(t, d.money?.month_received ?? 0)}`,
        sub: t("daily.stat.ap_month_received.sub"),
      };
  }
}

type ModuleTitleKey =
  | "daily.module.accounts" | "daily.module.contacts" | "daily.module.leads"
  | "daily.module.potentials" | "daily.module.quotes" | "daily.module.salesorders"
  | "daily.module.tasks" | "daily.module.products" | "daily.module.receivables"
  | "daily.module.payments";

const MODULE_TITLE_KEY: Record<string, ModuleTitleKey> = {
  accounts: "daily.module.accounts",
  contacts: "daily.module.contacts",
  leads: "daily.module.leads",
  potentials: "daily.module.potentials",
  quotes: "daily.module.quotes",
  salesorders: "daily.module.salesorders",
  tasks: "daily.module.tasks",
  products: "daily.module.products",
  receivables: "daily.module.receivables",
  payments: "daily.module.payments",
};

export function DailyFeed(
  { onSwitchToList, module, compact, onAskCopilot }: {
    onSwitchToList?: () => void;
    module?: string;
    compact?: boolean;
    /** Prefill the right-rail Copilot input with this text. Wired only
     *  on the cross-module /crm landing page (Crm.tsx owns the prefill
     *  state and passes the callback down). Per-record DailyFeed below
     *  RecordDetail leaves it undefined — RecordDetail has its own
     *  Copilot prefill path. */
    onAskCopilot?: (text: string) => void;
  } = {},
) {
  const t = useT();
  const { navigate } = useRouter();
  const [data, setData] = useState<DailyFeedT | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    const refresh = () => {
      setErr(null);
      getDailyFeed(24, 200, module)
        .then(setData)
        .catch(e => setErr(e.message || String(e)));
    };
    refresh();
    const timer = window.setInterval(refresh, 30_000);
    return () => window.clearInterval(timer);
  }, [module]);

  const baseFocus = (module && MODULE_FOCUS[module]) || DEFAULT_FOCUS;
  // Auto-append outcome-event sections (won/stages/first_touch) when
  // the matching stat is shown in this view — saves us from listing
  // them in every MODULE_FOCUS config.
  const focus: FocusDef = (() => {
    const existingKeys = new Set(baseFocus.sections.map(s => s.key));
    const extras: SectionDef[] = [];
    for (const stat of baseFocus.stats) {
      const make = _OUTCOME_AUTO[stat];
      if (make) {
        const sec = make();
        if (!existingKeys.has(sec.key)) {
          extras.push(sec);
          existingKeys.add(sec.key);
        }
      }
    }
    return extras.length === 0
      ? baseFocus
      : { ...baseFocus, sections: [...baseFocus.sections, ...extras] };
  })();
  const heading = module
    ? t("daily.heading.module", {
        module: MODULE_TITLE_KEY[module] ? t(MODULE_TITLE_KEY[module]) : module,
      })
    : t("daily.heading.default");

  return (
    <div className={`kacrm-daily-feed${compact ? " is-compact" : ""}`}>
      <div className="kacrm-feed-content">
      <header className="kacrm-feed-header">
        <div>
          <h2 style={{ margin: 0, fontSize: compact ? 16 : undefined }}>
            {heading}
          </h2>
          <p className="muted" style={{ marginTop: 4, fontSize: 14 }}>
            {t(focus.subtitleKey)}
          </p>
        </div>
        {onSwitchToList && (
          <button className="ghost" onClick={onSwitchToList}
                  title={t("daily.list_btn_title")}>
            {t("daily.list_btn")}
          </button>
        )}
      </header>

      {err && <div className="alert alert-error">{err}</div>}
      {!data && <div className="muted">{t("common.loading")}</div>}

      {data && (() => {
        const openTarget = (it: DailyFeedItem) => {
          if (it.target?.module && it.target?.record_id) {
            navigate(`/crm/${it.target.module}/${it.target.record_id}`);
          }
        };
        return (
          <>
            {/* Phase 2.A HomeChatBar was removed (2026-05-17) — it
                duplicated the right-rail CopilotPanel which already
                offers list-mode chat (record_id=null) on /crm
                landing whenever the viewport is wide enough. */}
            {/* Charter v2 §定律 1+5: AI-driven urgency feed sits at
                the very top so the user never has to ask "what should
                I do today?" — the system already says so. Renders on
                every daily dashboard:
                  - global (/crm) → cross-module top 5
                  - per-module    → that module's top 5
                Same component, same UX, just a different scope. */}
            <FocusedSnapshot
              data={data}
              statKeys={focus.stats}
              sectionKeys={new Set(focus.sections.map(s => s.key))}
            />
            <TodayPriorityTabs module={module} onAskCopilot={onAskCopilot} />

            {focus.sections.map(sec => {
              const items = data.items.filter(sec.filter);
              return (
                <Section key={sec.key}
                         id={`daily-section-${sec.key}`}
                         icon={SECTION_ICONS[sec.key] || "message"}
                         title={t(sec.titleKey, { n: items.length })}
                         hint={t(sec.hintKey)}
                         emptyText={t(sec.emptyKey)}
                         items={items}
                         onOpen={openTarget} />
              );
            })}

            <details className="kacrm-activity-section kacrm-full-timeline">
              <summary><CrmIcon name="clock" size={16} /><span>{t("daily.timeline_label")}</span><CrmIcon name="chevron" size={16} /></summary>
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                {data.items.length === 0 ? (
                  <div className="muted" style={{ padding: 16, textAlign: "center" }}>
                    {t("daily.timeline_empty")}
                  </div>
                ) : (
                  data.items.map((it, i) => (
                    <FeedRow key={i} item={it} onOpen={() => openTarget(it)} />
                  ))
                )}
              </div>
            </details>
          </>
        );
      })()}
      </div>
    </div>
  );
}

// Some stats correspond to a section listed below — clicking the
// stat scrolls there + flashes a highlight, so users can drill from
// the headline number into the detail without hunting.
const STAT_TO_SECTION_KEY: Partial<Record<StatKey, string>> = {
  agent_recent: "agent_recent",
  coach_hints: "coach",
  won: "won",
  stages: "stages",
  first_touch: "first_touch",
};

// Outcome-event sections we'll auto-append to focus.sections when the
// matching stat is in focus.stats but no section was explicitly listed
// (so module configs don't have to repeat the wiring).
const _OUTCOME_AUTO: Partial<Record<StatKey, () => SectionDef>> = {
  won: F.won,
  stages: F.stages,
  first_touch: F.firstTouch,
};

function _flashSection(sectionKey: string) {
  const el = document.getElementById(`daily-section-${sectionKey}`);
  if (!el) return;
  if (el instanceof HTMLDetailsElement) el.open = true;
  el.scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth", block: "start" });
  el.classList.add("daily-section-flash");
  window.setTimeout(() => el.classList.remove("daily-section-flash"), 1400);
}

function FocusedSnapshot(
  { data, statKeys, sectionKeys }: {
    data: DailyFeedT;
    statKeys: StatKey[];
    /** Section keys present below in this view — only render a
     *  click affordance on stat cards whose target section actually
     *  exists. */
    sectionKeys: Set<string>;
  },
) {
  const t = useT();
  if (statKeys.length === 0) return null;
  return (
    <div className="kacrm-snapshot">
      {/* theme-guard:ignore-start — keyframe animation literal inside JSX string */}
      <style>{`
        .daily-section-flash {
          animation: daily-flash 1.4s ease-out;
        }
        @keyframes daily-flash {
          0% { background: rgba(92,134,230,0.18); }
          100% { background: transparent; }
        }
      `}</style>
      {/* theme-guard:ignore-end */}
      {statKeys.map(k => {
        const s = statFor(t, k, data);
        const target = STAT_TO_SECTION_KEY[k];
        const isClickable = !!(target && sectionKeys.has(target));
        return (
          <Stat key={k} icon={SECTION_ICONS[STAT_TO_SECTION_KEY[k] || ""] || "activity"} label={s.label} value={s.value} sub={s.sub}
                onClick={isClickable
                  ? () => _flashSection(target!)
                  : undefined} />
        );
      })}
    </div>
  );
}

function Section({ id, title, hint, items, emptyText, onOpen, icon }: {
  id?: string; title: string; hint?: string; items: DailyFeedItem[];
  emptyText: string; onOpen: (it: DailyFeedItem) => void; icon: CrmIconName;
}) {
  const heading = <><CrmIcon name={icon} size={18} /><span>{title}</span></>;
  if (items.length === 0) {
    return <details id={id} className="kacrm-activity-section is-empty">
      <summary>{heading}<CrmIcon name="chevron" size={16} /></summary>
      <p className="kacrm-empty-copy">{emptyText}</p>
    </details>;
  }
  return <section id={id} className="kacrm-activity-section">
    <div className="kacrm-section-heading">{heading}</div>
    {hint && <p className="kacrm-section-hint">{hint}</p>}
    <div className="col" style={{ gap: 6 }}>
      {items.map((it, i) => <FeedRow key={i} item={it} onOpen={() => onOpen(it)} />)}
    </div>
  </section>;
}

function Stat({ label, value, sub, onClick, icon }: {
  icon: CrmIconName;
  label: string; value: string; sub?: string;
  onClick?: () => void;
}) {
  return (
    <div className="kacrm-stat"
      onClick={onClick}
      role={onClick ? "button" : undefined}
      tabIndex={onClick ? 0 : undefined}
      onKeyDown={onClick ? (e => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onClick();
        }
      }) : undefined}
      style={{
        cursor: onClick ? "pointer" : "default",
        borderRadius: 6,
        padding: onClick ? "2px 4px" : 0,
        margin: onClick ? "-2px -4px" : 0,
        transition: "background 0.15s ease",
        ...(onClick ? {} : {}),
      }}
      onMouseEnter={onClick ? (e => {
        (e.currentTarget as HTMLDivElement).style.background = "var(--surface-hover)";
      }) : undefined}
      onMouseLeave={onClick ? (e => {
        (e.currentTarget as HTMLDivElement).style.background = "transparent";
      }) : undefined}
    >
      <div className="kacrm-stat-label"><CrmIcon name={icon} size={16} />{label}</div>
      <div className="kacrm-stat-value">{value}</div>
      {sub && <div className="muted" style={{ fontSize: 13 }}>{sub}</div>}
    </div>
  );
}

function FeedRow({ item, onOpen }: {
  item: DailyFeedItem; onOpen: () => void;
}) {
  const t = useT();
  const hasLink = !!(item.target?.module && item.target?.record_id);
  const at = item.at ? new Date(item.at) : null;
  const time = at && !isNaN(at.getTime())
    ? at.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    : "";
  const typeKey = outcomeTypeKey(item) || TYPE_LABEL_KEY[item.item_type];
  // Outcome events have a structured preview (event-specific blurb +
  // optional ¥ amount) instead of free-form text.
  let outcomeBlurb: string | null = null;
  if (item.item_type === "outcome_event") {
    if (item.event_type === "deal.won" && item.value) {
      outcomeBlurb = `成单 ¥${fmtMoney(t, item.value)}`;
    } else if (item.event_type === "deal.won") {
      outcomeBlurb = "成单";
    } else if (item.event_type === "stage.advanced") {
      outcomeBlurb = "推进了一步";
    } else if (item.event_type === "record.first_touch") {
      outcomeBlurb = "首次联系";
    }
  }
  // CRM-write rows (kind='action') carry no free text — render a blurb
  // from the op + changed field count so the row reads as plain language.
  let actionBlurb: string | null = null;
  if (item.item_type === "interaction" && item.kind === "action") {
    const n = item.fields?.length ?? 0;
    if (item.op === "create") {
      actionBlurb = "新建了一条记录";
    } else if (item.op === "delete") {
      actionBlurb = "删除了一条记录";
    } else if (item.op === "update") {
      actionBlurb = n > 0 ? `更新了 ${n} 项字段` : "更新了记录";
    } else {
      actionBlurb = "处理了这条记录";
    }
  }
  return (
    <div onClick={hasLink ? onOpen : undefined}
         role={hasLink ? "button" : undefined}
         tabIndex={hasLink ? 0 : undefined}
         onKeyDown={hasLink ? e => {
           if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onOpen(); }
         } : undefined}
         style={{
           display: "grid",
           gridTemplateColumns: "50px 24px 1fr auto",
           gap: 8, alignItems: "center",
           padding: "8px 10px",
           border: "1px solid var(--border)", borderRadius: 6,
           cursor: hasLink ? "pointer" : "default",
         }}>
      <span className="muted" style={{ fontSize: 13 }}>{time}</span>
      <CrmIcon name={iconFor(item)} />
      <div style={{ overflow: "hidden" }}>
        <div style={{ fontSize: 14 }}>
          {item.target?.name && (
            <strong>{item.target.name}</strong>
          )}
          {(outcomeBlurb || actionBlurb || item.preview) && (
            <span style={{ marginLeft: 6,
                            whiteSpace: "nowrap",
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            display: "inline-block",
                            maxWidth: "100%" }}>
              {outcomeBlurb || actionBlurb || item.preview}
            </span>
          )}
        </div>
        <div className="muted" style={{ fontSize: 13, marginTop: 2 }}>
          {typeKey ? t(typeKey) : item.item_type}
          {sourceLabel(item.source) && ` · ${sourceLabel(item.source)}`}
        </div>
      </div>
      <span className="muted" style={{ fontSize: 13 }}>
        {item.target?.module ? (() => {
          const key = `crm.module.${item.target.module}`;
          const label = t(key as DictKey);
          return label === key ? item.target.module : label;
        })() : ""}
      </span>
    </div>
  );
}
