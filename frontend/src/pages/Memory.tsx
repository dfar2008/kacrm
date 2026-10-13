import { useEffect, useState } from "react";
import { useRouter } from "@/lib/router";
import { useCurrentUser } from "@/lib/currentUser";
import { usePermissions } from "@/lib/permissions";
import {
  browseMemory,
  getMemoryJourney,
  searchMemories,
  type MemoryEntity,
  type MemoryFact,
  type MemoryHit,
  type MemoryJourneyItem,
} from "@/lib/api";
import { useT, type T, type DictKey } from "@/lib/i18n";

/**
 * Memory home — "what the system knows about my world". Charter 原语 ②
 * (Memory Substrate) product surface. See git history for the previous
 * Chinese-only copy; all strings now resolve through ``useT()``.
 */

type Tab = "journey" | "recent" | "org" | "person" | "fact";

const TABS: { key: Tab; labelKey: DictKey; icon: string }[] = [
  { key: "journey", labelKey: "memory.tab.journey", icon: "🧭" },
  { key: "recent", labelKey: "memory.tab.recent", icon: "⏱" },
  { key: "org",    labelKey: "memory.tab.org",    icon: "🏢" },
  { key: "person", labelKey: "memory.tab.person", icon: "👤" },
  { key: "fact",   labelKey: "memory.tab.fact",   icon: "💡" },
];

export function MemoryPage() {
  const { navigate } = useRouter();
  const t = useT();
  const { user } = useCurrentUser();
  const { data: perms } = usePermissions();
  const isAdmin = !!perms?.is_admin;

  const [tab, setTab] = useState<Tab>("journey");
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<MemoryHit[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [rows, setRows] = useState<(MemoryEntity | MemoryFact)[]>([]);
  const [journey, setJourney] = useState<MemoryJourneyItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setErr(null);
    if (tab === "journey") {
      getMemoryJourney({ limit: 150 })
        .then(r => { if (!cancelled) setJourney(r.data); })
        .catch(e => { if (!cancelled) setErr(e?.message || t("memory.load_failed")); })
        .finally(() => { if (!cancelled) setLoading(false); });
    } else {
      browseMemory(tab, { limit: 60 })
        .then(r => { if (!cancelled) setRows(r.data); })
        .catch(e => { if (!cancelled) setErr(e?.message || t("memory.load_failed")); })
        .finally(() => { if (!cancelled) setLoading(false); });
    }
    return () => { cancelled = true; };
  }, [tab]);

  async function runSearch(e?: React.FormEvent) {
    e?.preventDefault();
    const q = query.trim();
    if (!q) { setHits(null); return; }
    setSearching(true);
    try {
      const r = await searchMemories(q, { topK: 8, minSimilarity: 0.0 });
      setHits(r.data);
    } catch (ex: any) {
      setErr(ex?.message || t("memory.search_failed"));
      setHits([]);
    } finally {
      setSearching(false);
    }
  }

  const userName = user?.email?.split("@")[0] || t("memory.default_user");

  return (
    <div style={{ padding: "24px 28px", overflow: "auto", height: "100%" }}>
      <header style={{
        display: "flex", justifyContent: "space-between", alignItems: "flex-start",
        marginBottom: 18, gap: 16,
      }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 24, letterSpacing: 0.2 }}>
            {t("memory.heading")}
          </h1>
          <p className="muted" style={{ margin: "4px 0 0", fontSize: 14 }}>
            {t("memory.subtitle", { user: userName })}
          </p>
        </div>
        {isAdmin && (
          <button
            title={t("memory.admin_tooltip")}
            onClick={() => navigate("/memory/debug")}
            style={adminGearStyle}>
            {t("memory.admin_button")}
          </button>
        )}
      </header>

      <form onSubmit={runSearch} style={{ position: "relative", marginBottom: 16 }}>
        <span style={{
          position: "absolute", left: 14, top: "50%",
          transform: "translateY(-50%)", fontSize: 18, color: "var(--muted)",
        }}>🔍</span>
        <input
          value={query}
          onChange={e => setQuery(e.target.value)}
          placeholder={t("memory.search_placeholder")}
          style={{
            width: "100%",
            padding: "12px 16px 12px 42px",
            fontSize: 15,
            background: "var(--panel)",
            border: "1px solid var(--border)",
            borderRadius: 10,
            color: "inherit",
          }}
        />
        {query && (
          <button type="button" onClick={() => { setQuery(""); setHits(null); }}
            style={{
              position: "absolute", right: 10, top: "50%",
              transform: "translateY(-50%)",
              background: "transparent", border: "none", color: "var(--muted)",
              cursor: "pointer", fontSize: 14,
            }}>{t("memory.search_clear")}</button>
        )}
      </form>

      {hits !== null ? (
        <SearchResultsPane hits={hits} loading={searching}
          onClear={() => { setQuery(""); setHits(null); }} />
      ) : (
        <>
          <TabBar tab={tab} onChange={setTab} />
          <div style={{ marginTop: 14 }}>
            {err && <div className="alert alert-error">{err}</div>}
            {loading && (tab === "journey" ? <SkeletonTimeline /> : <SkeletonGrid />)}
            {!loading && !err && (
              tab === "journey"
                ? <JourneyTimeline
                    items={journey}
                    onOpenEntity={id => navigate(`/memory/entity/${id}`)} />
                : tab === "fact"
                ? <FactGrid rows={rows as MemoryFact[]} />
                : <EntityGrid rows={rows as MemoryEntity[]}
                    tab={tab} onOpen={id => navigate(`/memory/entity/${id}`)} />
            )}
          </div>
        </>
      )}
    </div>
  );
}

// ── Tabs ──────────────────────────────────────────────────────────────
function TabBar({ tab, onChange }: { tab: Tab; onChange: (t: Tab) => void }) {
  const t = useT();
  return (
    <div style={{
      display: "flex", gap: 4, borderBottom: "1px solid var(--border)",
    }}>
      {TABS.map(item => {
        const active = tab === item.key;
        return (
          <button key={item.key}
            onClick={() => onChange(item.key)}
            style={{
              padding: "10px 16px",
              background: "transparent",
              border: "none",
              borderBottom: active ? "2px solid var(--accent)" : "2px solid transparent",
              color: active ? "var(--fg)" : "var(--muted)",
              cursor: "pointer",
              fontSize: 14,
              fontWeight: active ? 600 : 400,
              marginBottom: -1,
            }}>
            <span style={{ marginRight: 6 }}>{item.icon}</span>
            {t(item.labelKey)}
          </button>
        );
      })}
    </div>
  );
}

// ── Entity grid ───────────────────────────────────────────────────────
function EntityGrid({ rows, tab, onOpen }: {
  rows: MemoryEntity[]; tab: Tab; onOpen: (id: number) => void;
}) {
  const t = useT();
  if (rows.length === 0) {
    const hint = tab === "org" ? t("memory.empty.org")
               : tab === "person" ? t("memory.empty.person")
               : t("memory.empty.default");
    return <EmptyState hint={hint} />;
  }
  return (
    <div style={{
      display: "grid",
      gap: 12,
      gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))",
    }}>
      {rows.map(e => (
        <EntityCard key={e.id} entity={e} onClick={() => onOpen(e.id)} />
      ))}
    </div>
  );
}

function EntityCard({ entity, onClick }: {
  entity: MemoryEntity; onClick: () => void;
}) {
  const t = useT();
  const icon = entityIcon(entity.entity_type);
  const subtitle = entitySubtitle(entity, t);
  const mc = entity.memory_count ?? 0;
  const lastSeen = entity.last_seen_at ? relativeTime(entity.last_seen_at, t) : null;
  return (
    <button onClick={onClick} style={cardButtonStyle}>
      <div style={{
        display: "flex", alignItems: "center", gap: 10, marginBottom: 8,
      }}>
        <span style={{ fontSize: 22 }}>{icon}</span>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 15, fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {entity.canonical}
          </div>
          {subtitle && (
            <div className="muted" style={{ fontSize: 13, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {subtitle}
            </div>
          )}
        </div>
        <ScopeChip scope={entity.scope} />
      </div>
      <div style={{
        display: "flex", justifyContent: "space-between", alignItems: "center",
        fontSize: 13, color: "var(--muted)",
      }}>
        <span>{t("memory.card.count", { n: mc })}</span>
        <span>{lastSeen ? t("memory.card.last_active", { t: lastSeen }) : "—"}</span>
      </div>
    </button>
  );
}

// ── Fact grid ─────────────────────────────────────────────────────────
function FactGrid({ rows }: { rows: MemoryFact[] }) {
  const t = useT();
  if (rows.length === 0) {
    return <EmptyState hint={t("memory.empty.fact")} />;
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {rows.map(f => (
        <article key={f.id} style={factCardStyle}>
          <div style={{
            display: "flex", justifyContent: "space-between", alignItems: "baseline",
            marginBottom: 4, gap: 8,
          }}>
            <div style={{ fontSize: 14, fontWeight: 600 }}>
              💡 {f.title || t("memory.fact.no_title")}
            </div>
            <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
              <ScopeChip scope={f.scope} />
              <span className="muted" style={{ fontSize: 13 }}>
                {f.created_at ? relativeTime(f.created_at, t) : ""}
              </span>
            </div>
          </div>
          <div style={{ fontSize: 14, color: "var(--fg)", opacity: 0.9, whiteSpace: "pre-wrap" }}>
            {f.content}
          </div>
        </article>
      ))}
    </div>
  );
}

// ── Journey timeline ──────────────────────────────────────────────────
// 纵向按日期分组的学习时间线：把 agent 沉淀的记忆(全部 kind)按天
// 归组,每天一段左侧竖轴,轴上一串卡片。只读视图,编辑/删除仍走
// 实体详情页(点击实体标签跳转)与管理调试面板。

/** 记忆 kind → 展示图标 + i18n 标签 key。未知 kind 回退到通用。 */
function journeyKindMeta(kind: string): { icon: string; labelKey: DictKey } {
  switch (kind) {
    case "fact":       return { icon: "💡", labelKey: "memory.journey.kind.fact" };
    case "preference": return { icon: "🎚", labelKey: "memory.journey.kind.preference" };
    case "summary":    return { icon: "📝", labelKey: "memory.journey.kind.summary" };
    case "profile":    return { icon: "🧬", labelKey: "memory.journey.kind.profile" };
    case "skill":      return { icon: "🛠", labelKey: "memory.journey.kind.skill" };
    default:           return { icon: "✦",  labelKey: "memory.journey.kind.other" };
  }
}

/** 本地日期键(按天分组),避免时区跨界把同一天拆成两天。 */
function dayKey(iso?: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (!Number.isFinite(d.getTime())) return "";
  return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
}

/** 人类可读的分组标题：今天 / 昨天 / 具体日期。 */
function dayLabel(iso: string | undefined, t: T): string {
  if (!iso) return t("memory.journey.day.unknown");
  const d = new Date(iso);
  if (!Number.isFinite(d.getTime())) return t("memory.journey.day.unknown");
  const now = new Date();
  const today = dayKey(now.toISOString());
  const yesterday = new Date(now.getTime() - 86400_000);
  if (dayKey(iso) === today) return t("memory.journey.day.today");
  if (dayKey(iso) === dayKey(yesterday.toISOString())) return t("memory.journey.day.yesterday");
  return d.toLocaleDateString();
}

function JourneyTimeline({ items, onOpenEntity }: {
  items: MemoryJourneyItem[];
  onOpenEntity: (id: number) => void;
}) {
  const t = useT();
  if (items.length === 0) {
    return <EmptyState hint={t("memory.journey.empty")} />;
  }
  // 按天分组(items 已由后端 created_at 倒序)。
  const groups: { key: string; iso?: string; items: MemoryJourneyItem[] }[] = [];
  for (const it of items) {
    const k = dayKey(it.created_at);
    const last = groups[groups.length - 1];
    if (last && last.key === k) last.items.push(it);
    else groups.push({ key: k, iso: it.created_at, items: [it] });
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <div className="muted" style={{ fontSize: 13, marginBottom: 8 }}>
        {t("memory.journey.summary", { n: items.length })}
      </div>
      {groups.map(g => (
        <section key={g.key || "unknown"} style={{ marginBottom: 6 }}>
          <div style={{
            position: "sticky", top: 0, zIndex: 1,
            display: "flex", alignItems: "center", gap: 8,
            padding: "6px 0", background: "var(--bg)",
          }}>
            <span style={{ fontSize: 13, fontWeight: 600, color: "var(--muted)" }}>
              {dayLabel(g.iso, t)}
            </span>
            <span style={{ flex: 1, height: 1, background: "var(--border)" }} />
            <span className="muted" style={{ fontSize: 12 }}>
              {t("memory.journey.day_count", { n: g.items.length })}
            </span>
          </div>
          <div style={{ position: "relative", paddingLeft: 22 }}>
            {/* 竖轴 */}
            <span style={{
              position: "absolute", left: 6, top: 4, bottom: 4,
              width: 2, background: "var(--border)",
            }} />
            <div style={{ display: "flex", flexDirection: "column", gap: 10, paddingTop: 4 }}>
              {g.items.map(it => (
                <JourneyCard key={it.id} item={it} onOpenEntity={onOpenEntity} />
              ))}
            </div>
          </div>
        </section>
      ))}
    </div>
  );
}

function JourneyCard({ item, onOpenEntity }: {
  item: MemoryJourneyItem;
  onOpenEntity: (id: number) => void;
}) {
  const t = useT();
  const meta = journeyKindMeta(item.kind);
  const time = item.created_at
    ? new Date(item.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    : "";
  return (
    <article style={{ position: "relative", ...factCardStyle }}>
      {/* 轴上的节点 */}
      <span style={{
        position: "absolute", left: -22, top: 16,
        width: 10, height: 10, borderRadius: "50%",
        background: "var(--accent)",
        border: "2px solid var(--bg)",
        boxShadow: "0 0 0 1px var(--border)",
      }} />
      <div style={{
        display: "flex", justifyContent: "space-between",
        alignItems: "baseline", gap: 8, marginBottom: 4,
      }}>
        <div style={{ fontSize: 14, fontWeight: 600, display: "flex", alignItems: "center", gap: 6, minWidth: 0 }}>
          <span>{meta.icon}</span>
          <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {item.title || t(meta.labelKey)}
          </span>
        </div>
        <div style={{ display: "flex", gap: 6, alignItems: "center", flexShrink: 0 }}>
          <KindChip labelKey={meta.labelKey} />
          <ScopeChip scope={item.scope} />
          <span className="muted" style={{ fontSize: 12, fontFamily: "ui-monospace, monospace" }}>
            {time}
          </span>
        </div>
      </div>
      <div style={{ fontSize: 14, color: "var(--fg)", opacity: 0.9, whiteSpace: "pre-wrap" }}>
        {item.content}
      </div>
      {item.entities && item.entities.length > 0 && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 8 }}>
          {item.entities.map(e => (
            <button key={e.entity_id}
              onClick={() => onOpenEntity(e.entity_id)}
              title={t("memory.journey.entity_tooltip", { name: e.canonical })}
              style={entityChipStyle}>
              <span>{entityIcon(e.entity_type)}</span>
              <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: 160 }}>
                {e.canonical}
              </span>
            </button>
          ))}
        </div>
      )}
    </article>
  );
}

function KindChip({ labelKey }: { labelKey: DictKey }) {
  const t = useT();
  return (
    <span style={{
      fontSize: 12, fontWeight: 600,
      padding: "2px 6px", borderRadius: 10,
      background: "var(--surface-hover)",
      color: "var(--muted)",
      border: "1px solid var(--border)",
      letterSpacing: 0.3,
    }}>{t(labelKey)}</span>
  );
}

function SkeletonTimeline() {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10, paddingLeft: 22 }}>
      {Array.from({ length: 5 }).map((_, i) => (
        <div key={i} style={{
          height: 68, background: "var(--panel)",
          border: "1px solid var(--border)", borderRadius: 10,
          opacity: 0.5,
        }} />
      ))}
    </div>
  );
}

// ── Search results ────────────────────────────────────────────────────
function SearchResultsPane({ hits, loading, onClear }: {
  hits: MemoryHit[]; loading: boolean; onClear: () => void;
}) {
  const t = useT();
  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
        <div className="muted" style={{ fontSize: 14 }}>
          {loading ? t("memory.searching") : t("memory.search_results", { n: hits.length })}
        </div>
        <button onClick={onClear} style={linkButtonStyle}>{t("memory.back_browse")}</button>
      </div>
      {!loading && hits.length === 0 && (
        <EmptyState hint={t("memory.search_empty")} />
      )}
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {hits.map(h => (
          <article key={h.id} style={factCardStyle}>
            <div style={{
              display: "flex", justifyContent: "space-between",
              alignItems: "baseline", marginBottom: 4, gap: 8,
            }}>
              <div style={{ fontSize: 14, fontWeight: 600 }}>
                {h.title || t("memory.fact.untitled_id", { id: h.id })}
              </div>
              <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                <SimilarityChip value={h.similarity} />
                <ScopeChip scope={h.scope || undefined} />
              </div>
            </div>
            <div style={{ fontSize: 14, opacity: 0.9, whiteSpace: "pre-wrap" }}>
              {h.content}
            </div>
          </article>
        ))}
      </div>
    </div>
  );
}

// ── Small presentational pieces ──────────────────────────────────────
function ScopeChip({ scope }: { scope?: string }) {
  const t = useT();
  if (!scope) return null;
  const isOrg = scope === "org";
  return (
    <span title={isOrg ? t("memory.scope.org_tooltip") : t("memory.scope.mine_tooltip")}
      style={{
        fontSize: 13, fontWeight: 600,
        padding: "2px 6px", borderRadius: 10,
        background: isOrg ? "var(--accent-soft-strong)" : "var(--surface-hover)",
        color: isOrg ? "var(--accent)" : "var(--muted)",
        border: `1px solid ${isOrg ? "color-mix(in srgb, var(--accent) 35%, transparent)" : "var(--border)"}`,
        letterSpacing: 0.3,
      }}>{isOrg ? t("memory.scope.org") : t("memory.scope.mine")}</span>
  );
}
function SimilarityChip({ value }: { value: number }) {
  const pct = Math.round(Math.max(0, Math.min(1, value)) * 100);
  const hue = Math.max(0, Math.min(1, value)) * 120;
  // theme-guard:ignore-start
  return (
    <span style={{
      fontSize: 13, padding: "2px 6px", borderRadius: 10,
      background: `hsl(${hue}, 55%, 22%)`,
      color: `hsl(${hue}, 80%, 85%)`,
      fontFamily: "ui-monospace, monospace",
    }}>{pct}%</span>
  );
  // theme-guard:ignore-end
}
function EmptyState({ hint }: { hint: string }) {
  return (
    <div style={{
      padding: "60px 20px", textAlign: "center",
      border: "1px dashed var(--border)", borderRadius: 10,
      color: "var(--muted)", fontSize: 14,
    }}>{hint}</div>
  );
}
function SkeletonGrid() {
  return (
    <div style={{
      display: "grid", gap: 12,
      gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))",
    }}>
      {Array.from({ length: 6 }).map((_, i) => (
        <div key={i} style={{
          height: 86, background: "var(--panel)",
          border: "1px solid var(--border)", borderRadius: 10,
          opacity: 0.5,
        }} />
      ))}
    </div>
  );
}

// ── Helpers ───────────────────────────────────────────────────────────
export function entityIcon(kind: string): string {
  switch (kind) {
    case "org":      return "🏢";
    case "person":   return "👤";
    case "product":  return "📦";
    case "location": return "📍";
    case "project":  return "🎯";
    default:         return "🔗";
  }
}
/** Short descriptor for an entity — industry/title/role/… or alias list. */
export function entitySubtitle(e: MemoryEntity, t: T): string {
  const a = e.attributes || e.attrs;
  if (!a) return "";
  for (const k of ["industry", "title", "role", "category", "type"]) {
    const v = a[k];
    if (typeof v === "string" && v.trim()) return v.trim();
  }
  if (e.aliases && e.aliases.length) {
    const v = e.aliases.slice(0, 2).join(" · ");
    return t("memory.alias_prefix", { v });
  }
  return "";
}
/** Locale-aware relative-time string. */
export function relativeTime(iso: string, t: T): string {
  const then = new Date(iso).getTime();
  if (!Number.isFinite(then)) return "";
  const diffSec = (Date.now() - then) / 1000;
  if (diffSec < 60) return t("memory.rel.now");
  if (diffSec < 3600) {
    const n = Math.floor(diffSec / 60);
    return t("memory.rel.min", { n });
  }
  if (diffSec < 86400) {
    const n = Math.floor(diffSec / 3600);
    return t("memory.rel.hour", { n });
  }
  if (diffSec < 86400 * 7) {
    const n = Math.floor(diffSec / 86400);
    return t("memory.rel.day", { n });
  }
  return new Date(iso).toLocaleDateString();
}

// ── Styles ────────────────────────────────────────────────────────────
const cardButtonStyle: React.CSSProperties = {
  textAlign: "left",
  padding: "14px 14px 12px",
  background: "var(--panel)",
  border: "1px solid var(--border)",
  borderRadius: 10,
  color: "inherit",
  cursor: "pointer",
  transition: "border-color 120ms, transform 120ms",
};
const factCardStyle: React.CSSProperties = {
  padding: "12px 14px",
  background: "var(--panel)",
  border: "1px solid var(--border)",
  borderRadius: 10,
};
const entityChipStyle: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 4,
  maxWidth: "100%",
  padding: "3px 8px",
  fontSize: 12,
  background: "var(--surface-hover)",
  border: "1px solid var(--border)",
  borderRadius: 12,
  color: "var(--fg)",
  cursor: "pointer",
};
const adminGearStyle: React.CSSProperties = {
  padding: "6px 12px",
  fontSize: 14,
  background: "transparent",
  color: "var(--muted)",
  border: "1px solid var(--border)",
  borderRadius: 6,
  cursor: "pointer",
};
const linkButtonStyle: React.CSSProperties = {
  background: "transparent",
  border: "none",
  color: "var(--muted)",
  cursor: "pointer",
  fontSize: 14,
};
