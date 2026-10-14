import { useEffect, useState } from "react";
import { getRelatedRecords, type RelatedRecord } from "@/lib/api";
import { useRouter } from "@/lib/router";
import { useT, type T } from "@/lib/i18n";

/**
 * Charter v2 step-2 narrative cards (2026-05-08).
 *
 * Renders sibling records (contacts / opportunities / tasks / quotes /
 * orders / receivables) that point at the current account. Per
 * Charter §定律 1, this is NOT a Salesforce-style related-records
 * dashboard — it's an "events-first" projection:
 *   - Empty modules don't render at all (no placeholder rows).
 *   - Each row shows just enough state to make a follow-up decision
 *     (status, due_at, key contact info), not a full field dump.
 *   - Click navigates to that record's detail page in the same
 *     module's tab.
 *
 * Currently scoped to ``module === "accounts"`` because that's the
 * primary "customer 360" entry. Other modules (leads / contacts /
 * potentials) get this treatment in a Phase-3 follow-up.
 */

interface Props {
  module: string;
  recordId: number;
  /** Bumped by the parent to trigger a refresh after writes. */
  refreshKey?: number;
}

const MODULE_TITLE_KEY: Record<string, string> = {
  contacts: "related.contacts",
  potentials: "related.potentials",
  tasks: "related.tasks",
  quotes: "related.quotes",
  salesorders: "related.salesorders",
  receivables: "related.receivables",
};

// Module-name → emoji-free SVG icon glyph in Lucide stroke style.
function GroupIcon({ module: m }: { module: string }) {
  const p = {
    width: 14, height: 14, viewBox: "0 0 24 24", fill: "none" as const,
    stroke: "currentColor", strokeWidth: 2,
    strokeLinecap: "round" as const, strokeLinejoin: "round" as const,
    "aria-hidden": true,
  };
  switch (m) {
    case "contacts":
      return (<svg {...p}><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>);
    case "potentials":
      return (<svg {...p}><polyline points="22 7 13.5 15.5 8.5 10.5 2 17"/><polyline points="16 7 22 7 22 13"/></svg>);
    case "tasks":
      return (<svg {...p}><polyline points="9 11 12 14 22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg>);
    case "quotes":
      return (<svg {...p}><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="8" y1="13" x2="16" y2="13"/></svg>);
    case "salesorders":
      return (<svg {...p}><path d="M9 2h6a1 1 0 0 1 1 1v2H8V3a1 1 0 0 1 1-1z"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><polyline points="9 14 11 16 15 12"/></svg>);
    case "receivables":
      return (<svg {...p}><path d="M21 12V7H5a2 2 0 0 1 0-4h14v4"/><path d="M3 5v14a2 2 0 0 0 2 2h16v-5"/><path d="M18 12a2 2 0 0 0 0 4h4v-4z"/></svg>);
    default:
      return null;
  }
}

function isClosedStatus(status: any): boolean {
  if (!status) return false;
  const s = String(status).toLowerCase();
  return [
    "won", "lost", "closed", "completed", "done", "cancelled",
    "已成交", "已丢单", "已关闭", "已完成", "已取消",
  ].includes(s);
}

function formatDue(t: T, due: any): string {
  if (!due) return "";
  try {
    const d = new Date(String(due));
    if (Number.isNaN(d.getTime())) return String(due);
    const diff = d.getTime() - Date.now();
    const days = Math.round(diff / 86_400_000);
    if (days < 0) return t("related.due.overdue", { n: -days });
    if (days === 0) return t("related.due.today");
    if (days === 1) return t("related.due.tomorrow");
    if (days < 7) return t("related.due.in_days", { n: days });
    return d.toLocaleDateString();
  } catch { return String(due); }
}

function summarizeRow(t: T, mod: string, r: RelatedRecord): string {
  switch (mod) {
    case "contacts": {
      const bits: string[] = [];
      if (r.mobile) bits.push(String(r.mobile));
      if (r.email) bits.push(String(r.email));
      return bits.join(" · ");
    }
    case "potentials": {
      const bits: string[] = [];
      if (r.status) bits.push(String(r.status));
      if (r.amount != null && r.amount !== 0) {
        bits.push(`¥${Number(r.amount).toLocaleString()}`);
      }
      return bits.join(" · ");
    }
    case "tasks": {
      const bits: string[] = [];
      if (r.status) bits.push(String(r.status));
      if (r.priority) bits.push(`P:${r.priority}`);
      const due = formatDue(t, r.due_at);
      if (due) bits.push(due);
      return bits.join(" · ");
    }
    case "quotes":
    case "salesorders":
      return r.status ? String(r.status) : "";
    default:
      return "";
  }
}

export function RelatedEntities({ module, recordId, refreshKey }: Props) {
  const t = useT();
  const { navigate } = useRouter();
  const [groups, setGroups] = useState<Record<string, RelatedRecord[]>>({});
  const [groupTitles, setGroupTitles] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);

  // Backend only returns useful groups for accounts / contacts /
  // potentials / leads. Other modules (tasks, products, …) get an
  // empty payload — the early-return below prevents a wasted fetch.
  // P4 §6.5:不再用硬编码 PARENT_MODULES 门 —— 任意模块(含自定义)都拉
  // 关联记录,后端 related_records 元数据驱动决定有无;无组时下方 filtered
  // 为空会 return null,不会渲染空面板,也不会浪费太多(单次轻查询)。
  useEffect(() => {
    setLoading(true);
    getRelatedRecords(module, recordId)
      .then(r => { setGroups(r.groups || {}); setGroupTitles(r.group_titles || {}); })
      .catch(() => { setGroups({}); setGroupTitles({}); })
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [module, recordId, refreshKey]);

  // Filter: drop closed/lost potentials & quotes etc., drop done tasks
  const filtered: Array<[string, RelatedRecord[]]> = [];
  for (const [mod, items] of Object.entries(groups)) {
    let kept = items;
    if (mod === "potentials" || mod === "quotes" || mod === "salesorders") {
      kept = items.filter(r => !isClosedStatus(r.status));
    }
    if (mod === "tasks") {
      kept = items.filter(r => String(r.status || "").toLowerCase() !== "done");
    }
    if (kept.length > 0) filtered.push([mod, kept]);
  }

  if (!loading && filtered.length === 0) return null;

  return (
    <div className="col kacrm-detail-related" style={{ gap: 8, marginBottom: 12 }}>
      {filtered.map(([mod, items]) => (
        <div key={mod} style={{
          border: "1px solid var(--border)",
          borderRadius: 8,
          padding: "8px 10px",
        }}>
          <div className="row" style={{ alignItems: "center", gap: 6, marginBottom: 6 }}>
            <GroupIcon module={mod} />
            <span style={{ fontWeight: 600, fontSize: 14 }}>
              {(() => {
                // 内置组用 related.* key;自定义模块无 key → 用 crm.module.<mod>
                // 回退,再缺则用模块名本身(t() 缺失返回 key 本身,判等回退)。
                const k = MODULE_TITLE_KEY[mod];
                if (k) return t(k as any);
                const mk = `crm.module.${mod}`;
                const tr = t(mk as any);
                if (tr !== mk) return tr;
                // 自定义模块:用后端返回的 collections.title,再缺才用 slug
                return groupTitles[mod] || mod;
              })()}
            </span>
            <span className="muted" style={{ fontSize: 13 }}>· {items.length}</span>
          </div>
          <div className="col" style={{ gap: 4 }}>
            {items.map(r => {
              const summary = summarizeRow(t, mod, r);
              return (
                <button key={r.id} className="ghost kacrm-related-record"
                        onClick={() => navigate(`/crm/${mod}/${r.id}`)}
                        style={{
                          display: "flex", alignItems: "baseline", gap: 8,
                          padding: "4px 6px", textAlign: "left",
                          fontSize: 14,
                        }}>
                  <span style={{ fontWeight: 500 }}>{r.name || `#${r.id}`}</span>
                  {summary && (
                    <span className="muted" style={{ fontSize: 13 }}>· {summary}</span>
                  )}
                  <span className="muted" style={{
                    marginLeft: "auto", fontSize: 13,
                  }}>#{r.id}</span>
                </button>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}
