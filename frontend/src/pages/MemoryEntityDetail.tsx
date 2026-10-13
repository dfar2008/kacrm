import { useEffect, useMemo, useState } from "react";
import { useRouter } from "@/lib/router";
import { usePermissions } from "@/lib/permissions";
import {
  getEntityById,
  getEntityCrmLinks,
  getEntityFacts,
  getEntityTimeline,
  getSubgraph,
  mergeEntities,
  type MemoryCrmLink,
  type MemoryEntity,
  type MemoryFact,
  type MemoryTimelineItem,
  type MemorySubgraph,
} from "@/lib/api";
import { entityIcon, entitySubtitle, relativeTime } from "@/pages/Memory";
import { useT, type T } from "@/lib/i18n";

/**
 * Memory entity detail — "what we know about X".
 */
export function MemoryEntityDetailPage(props: { entityId: number }) {
  const { entityId } = props;
  const { navigate } = useRouter();
  const translate = useT();
  const { data: perms } = usePermissions();
  const isAdmin = !!perms?.is_admin;

  const [entity, setEntity] = useState<MemoryEntity | null>(null);
  const [facts, setFacts] = useState<MemoryFact[]>([]);
  const [timeline, setTimeline] = useState<MemoryTimelineItem[]>([]);
  const [graph, setGraph] = useState<MemorySubgraph | null>(null);
  const [crmLinks, setCrmLinks] = useState<MemoryCrmLink[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [menuOpen, setMenuOpen] = useState(false);

  useEffect(() => {
    let alive = true;
    (async () => {
      setLoading(true); setErr(null);
      try {
        const [e, f, tl, g, c] = await Promise.all([
          getEntityById(entityId),
          getEntityFacts(entityId, 50).catch(() => ({ data: [] as MemoryFact[], count: 0 })),
          getEntityTimeline(entityId, 30).catch(() => ({ data: [] as MemoryTimelineItem[], count: 0 })),
          getSubgraph(entityId, { depth: 1, maxEdges: 100 }).catch(() => ({ data: { entities: [], edges: [] } as MemorySubgraph })),
          getEntityCrmLinks(entityId).catch(() => ({ data: [] as MemoryCrmLink[], count: 0 })),
        ]);
        if (!alive) return;
        setEntity(e.data);
        setFacts(f.data || []);
        setTimeline(tl.data || []);
        setGraph(g.data || null);
        setCrmLinks(c.data || []);
      } catch (ex: any) {
        if (!alive) return;
        setErr(ex?.status === 404 ? translate("memory.detail.entity_not_found") : (ex?.message || translate("memory.detail.load_failed")));
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => { alive = false; };
  }, [entityId]);

  const relatedPeople = useMemo(() => {
    if (!graph) return [];
    return graph.entities.filter(e => e.entity_type === "person" && e.id !== entityId);
  }, [graph, entityId]);

  const otherRelated = useMemo(() => {
    if (!graph) return [];
    return graph.entities.filter(e => e.entity_type !== "person" && e.id !== entityId);
  }, [graph, entityId]);

  async function doMerge() {
    setMenuOpen(false);
    const raw = window.prompt(translate("memory.detail.merge.prompt"));
    if (!raw) return;
    const srcId = Number(raw);
    if (!Number.isFinite(srcId) || srcId <= 0 || srcId === entityId) {
      window.alert(translate("memory.detail.merge.invalid"));
      return;
    }
    if (!window.confirm(translate("memory.detail.merge.confirm", { src: srcId, dst: entityId }))) return;
    try {
      await mergeEntities(entityId, srcId);
      const e = await getEntityById(entityId);
      setEntity(e.data);
      window.alert(translate("memory.detail.merge.success"));
    } catch (ex: any) {
      window.alert(ex?.message || translate("memory.detail.merge.failed"));
    }
  }

  if (loading) {
    return <div style={{ padding: 24, color: "var(--muted)" }}>{translate("memory.detail.loading")}</div>;
  }
  if (err || !entity) {
    return (
      <div style={{ padding: 24 }}>
        <button style={linkBtn} onClick={() => navigate("/memory")}>{translate("memory.detail.back")}</button>
        <div className="alert alert-error" style={{ marginTop: 12 }}>{err || translate("memory.detail.not_found")}</div>
      </div>
    );
  }

  const subtitle = entitySubtitle(entity, translate);
  const aliases = entity.aliases || [];
  const scope = entity.user_id == null ? "org" : "user";

  return (
    <div style={{ padding: "20px 28px 40px", overflow: "auto", height: "100%" }}>
      <button style={linkBtn} onClick={() => navigate("/memory")}>{translate("memory.detail.back")}</button>

      {/* Header */}
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 12, marginTop: 12 }}>
        <div style={{ display: "flex", gap: 14, alignItems: "flex-start", minWidth: 0 }}>
          <div style={{ fontSize: 36, lineHeight: "40px" }}>{entityIcon(entity.entity_type)}</div>
          <div style={{ minWidth: 0 }}>
            <h2 style={{ margin: 0, fontSize: 26, fontWeight: 600 }}>
              {entity.canonical}
              <span
                style={scope === "org" ? scopeOrg : scopeUser}
                title={translate(scope === "org" ? "memory.detail.scope.org_tooltip" : "memory.detail.scope.mine_tooltip")}
              >
                {translate(scope === "org" ? "memory.scope.org" : "memory.scope.mine")}
              </span>
            </h2>
            {subtitle && <div style={{ color: "var(--muted)", marginTop: 6 }}>{subtitle}</div>}
            {aliases.length > 0 && (
              <div style={{ color: "var(--muted)", fontSize: 14, marginTop: 4 }}>
                {translate("memory.detail.aliases", { v: aliases.join(" · ") })}
              </div>
            )}
            <div style={{ color: "var(--muted)", fontSize: 14, marginTop: 6 }}>
              {entity.first_seen_at && (
                <>{translate("memory.detail.first_seen", { dt: new Date(entity.first_seen_at).toLocaleDateString() })}</>
              )}
              {entity.last_seen_at && (
                <> · {translate("memory.detail.last_active", { rel: relativeTime(entity.last_seen_at, translate) })}</>
              )}
              {" · "}#{entity.id}
            </div>
            {crmLinks.length > 0 && (
              <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 8 }}>
                {crmLinks.map(lnk => (
                  <button
                    key={`${lnk.module}-${lnk.id}`}
                    className="chip"
                    style={crmChip}
                    title={translate("memory.detail.crm_tooltip", { label: lnk.module_label, id: lnk.id })}
                    onClick={() => navigate(`/crm/${lnk.module}/${lnk.id}`)}
                  >
                    {translate("memory.detail.crm_open", { label: lnk.module_label, name: lnk.name || `#${lnk.id}` })}
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
        {/* Overflow menu */}
        <div style={{ position: "relative" }}>
          <button style={gearBtn} onClick={() => setMenuOpen(v => !v)} aria-label={translate("memory.detail.menu.more")}>⋯</button>
          {menuOpen && (
            <div style={menuBox} onMouseLeave={() => setMenuOpen(false)}>
              {isAdmin && (
                <button style={menuItem} onClick={doMerge}>{translate("memory.detail.menu.merge")}</button>
              )}
              {isAdmin && (
                <button style={menuItem} onClick={() => { setMenuOpen(false); navigate("/memory/debug"); }}>
                  {translate("memory.detail.menu.debug")}
                </button>
              )}
              {!isAdmin && (
                <div style={{ ...menuItem, color: "var(--muted)", cursor: "default" }}>
                  {translate("memory.detail.menu.none")}
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Two-column: facts + related people */}
      <div style={{
        display: "grid",
        gridTemplateColumns: "minmax(0, 2fr) minmax(0, 1fr)",
        gap: 20,
        marginTop: 24,
      }}>
        {/* Facts */}
        <section>
          <h3 style={sectionH}>{translate("memory.detail.facts.heading")} <span style={countBadge}>{facts.length}</span></h3>
          {facts.length === 0 && (
            <div style={emptyPane}>
              {translate(entity.entity_type === "person" ? "memory.detail.facts.empty.person" : "memory.detail.facts.empty.other")}
              <br/>
              {translate("memory.detail.facts.empty.hint")}
            </div>
          )}
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {facts.map(f => (
              <div key={f.id} style={factCard}>
                <div style={{ display: "flex", justifyContent: "space-between", gap: 8, marginBottom: 4 }}>
                  <span style={{ fontWeight: 600 }}>
                    💡 {f.title || translate("memory.detail.facts.untitled")}
                  </span>
                  <span style={{ color: "var(--muted)", fontSize: 14 }}>
                    {f.created_at ? relativeTime(f.created_at, translate) : ""}
                  </span>
                </div>
                <div style={{ whiteSpace: "pre-wrap", fontSize: 14 }}>{f.content}</div>
                {f.role && (
                  <div style={{ color: "var(--muted)", fontSize: 14, marginTop: 6 }}>
                    {translate("memory.detail.facts.role", { r: f.role })}
                  </div>
                )}
              </div>
            ))}
          </div>
        </section>

        {/* Related people + other related */}
        <aside style={{ display: "flex", flexDirection: "column", gap: 18 }}>
          <section>
            <h3 style={sectionH}>{translate("memory.detail.related.people")} <span style={countBadge}>{relatedPeople.length}</span></h3>
            {relatedPeople.length === 0 && (
              <div style={emptyPane}>{translate("memory.detail.related.people_empty")}</div>
            )}
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {relatedPeople.map(p => (
                <button key={p.id} style={relatedCard} onClick={() => navigate(`/memory/entity/${p.id}`)}>
                  <span style={{ fontSize: 20 }}>👤</span>
                  <span style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontWeight: 500 }}>{p.canonical}</div>
                    {entitySubtitle(p, translate) && (
                      <div style={{ color: "var(--muted)", fontSize: 14 }}>{entitySubtitle(p, translate)}</div>
                    )}
                  </span>
                </button>
              ))}
            </div>
          </section>

          {otherRelated.length > 0 && (
            <section>
              <h3 style={sectionH}>{translate("memory.detail.related.other")} <span style={countBadge}>{otherRelated.length}</span></h3>
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {otherRelated.map(e => (
                  <button key={e.id} style={relatedCard} onClick={() => navigate(`/memory/entity/${e.id}`)}>
                    <span style={{ fontSize: 20 }}>{entityIcon(e.entity_type)}</span>
                    <span style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontWeight: 500 }}>{e.canonical}</div>
                      {entitySubtitle(e, translate) && (
                        <div style={{ color: "var(--muted)", fontSize: 14 }}>{entitySubtitle(e, translate)}</div>
                      )}
                    </span>
                  </button>
                ))}
              </div>
            </section>
          )}
        </aside>
      </div>

      {/* Timeline */}
      <section style={{ marginTop: 28 }}>
        <h3 style={sectionH}>{translate("memory.detail.timeline.heading")} <span style={countBadge}>{timeline.length}</span></h3>
        {timeline.length === 0 && (
          <div style={emptyPane}>{translate("memory.detail.timeline.empty")}</div>
        )}
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {timeline.map(item => (
            <TimelineRow key={item.id} item={item} t={translate} />
          ))}
        </div>
      </section>
    </div>
  );
}

function TimelineRow({ item, t: translate }: { item: MemoryTimelineItem; t: T }) {
  const [open, setOpen] = useState(false);
  const icon = item.kind === "email" ? "📧"
    : item.kind === "chat" ? "💬"
    : item.kind === "call" ? "📞"
    : item.kind === "meeting" ? "📅"
    : "📝";
  const when = item.occurred_at ? new Date(item.occurred_at) : null;
  const subject = typeof item.content === "object" && item.content
    ? (item.content.subject || item.content.title || item.content.preview || "")
    : "";
  const actorText = Array.isArray(item.actors) && item.actors.length
    ? (item.actors[0].name || item.actors[0].email || "")
    : "";
  // Stringify the content body for the expanded view. Plain strings
  // pass through; object payloads collapse to a readable JSON dump so
  // the user sees every field, not just the title.
  const bodyText: string = (() => {
    if (item.content == null) return "";
    if (typeof item.content === "string") return item.content;
    try { return JSON.stringify(item.content, null, 2); }
    catch { return String(item.content); }
  })();
  return (
    <div style={{ ...timelineRow, flexDirection: "column", alignItems: "stretch" }}>
      <div style={{ display: "flex", gap: 12, alignItems: "flex-start" }}>
        <span style={{ fontSize: 20, flex: "0 0 auto" }}>{icon}</span>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
            <span style={{ color: "var(--muted)", fontSize: 14 }}>
              {when ? when.toLocaleDateString() : ""}
            </span>
            <span style={{ fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {item.memory_title || subject || item.kind || translate("memory.detail.timeline.event")}
            </span>
          </div>
          {(actorText || subject) && (
            <div style={{ color: "var(--muted)", fontSize: 14, marginTop: 2 }}>
              {actorText}
              {actorText && subject ? " · " : ""}
              {subject && item.memory_title ? subject : ""}
              {item.source ? ` · ${item.source}` : ""}
            </div>
          )}
        </div>
        {bodyText && (
          <button style={openBtn}
                  onClick={() => setOpen((v) => !v)}
                  title={translate("memory.detail.timeline.open_tooltip")}>
            {open ? translate("memory.detail.timeline.collapse")
                  : translate("memory.detail.timeline.open")}
          </button>
        )}
      </div>
      {open && bodyText && (
        <pre style={{
          marginTop: 10, marginLeft: 32,
          padding: "10px 12px", borderRadius: 6,
          background: "var(--panel-2)", color: "var(--fg)",
          fontSize: 14, lineHeight: 1.5,
          whiteSpace: "pre-wrap", wordBreak: "break-word",
          maxHeight: 360, overflow: "auto",
        }}>{bodyText}</pre>
      )}
    </div>
  );
}

// ── Styles ────────────────────────────────────────────────────────────
const linkBtn: React.CSSProperties = {
  background: "transparent", border: "none", color: "var(--muted)",
  cursor: "pointer", fontSize: 14, padding: 0,
};
const gearBtn: React.CSSProperties = {
  padding: "6px 12px", fontSize: 18, lineHeight: 1,
  background: "transparent", color: "var(--muted)",
  border: "1px solid var(--border)", borderRadius: 6, cursor: "pointer",
};
const menuBox: React.CSSProperties = {
  position: "absolute", right: 0, top: "calc(100% + 4px)",
  background: "var(--panel)", border: "1px solid var(--border)",
  borderRadius: 8, padding: 4, minWidth: 180, zIndex: 10,
  boxShadow: "var(--shadow-popover)",
};
const menuItem: React.CSSProperties = {
  display: "block", width: "100%", textAlign: "left",
  padding: "8px 10px", fontSize: 14,
  background: "transparent", border: "none", color: "inherit",
  borderRadius: 4, cursor: "pointer",
};
const sectionH: React.CSSProperties = {
  margin: "0 0 10px", fontSize: 15, fontWeight: 600,
  display: "flex", alignItems: "center", gap: 8,
};
const countBadge: React.CSSProperties = {
  fontSize: 13, padding: "1px 7px", borderRadius: 999,
  background: "var(--panel-2)",
  color: "var(--muted)", fontWeight: 400,
};
const factCard: React.CSSProperties = {
  padding: "12px 14px",
  background: "var(--panel)",
  border: "1px solid var(--border)",
  borderRadius: 10,
};
const relatedCard: React.CSSProperties = {
  display: "flex", alignItems: "center", gap: 10,
  padding: "10px 12px", textAlign: "left",
  background: "var(--panel)",
  border: "1px solid var(--border)",
  borderRadius: 10, color: "inherit", cursor: "pointer",
};
const timelineRow: React.CSSProperties = {
  display: "flex", alignItems: "center", gap: 12,
  padding: "10px 14px",
  background: "var(--panel)",
  border: "1px solid var(--border)",
  borderRadius: 10,
};
const openBtn: React.CSSProperties = {
  padding: "4px 10px", fontSize: 14,
  background: "transparent", color: "var(--muted)",
  border: "1px solid var(--border)", borderRadius: 6, cursor: "pointer",
};
const emptyPane: React.CSSProperties = {
  padding: "18px 16px",
  border: "1px dashed var(--border)", borderRadius: 10,
  color: "var(--muted)", fontSize: 14, lineHeight: 1.7,
};
const scopeOrg: React.CSSProperties = {
  marginLeft: 10, fontSize: 13, padding: "2px 8px",
  borderRadius: 999, background: "color-mix(in srgb, var(--info) 25%, transparent)",
  color: "var(--info)", fontWeight: 500,
  verticalAlign: "middle",
};
const scopeUser: React.CSSProperties = {
  ...scopeOrg,
  background: "var(--panel-2)",
  color: "var(--muted)",
};
const crmChip: React.CSSProperties = {
  fontSize: 14,
  padding: "3px 10px",
  borderRadius: 999,
  border: "1px solid var(--border)",
  background: "var(--accent-soft)",
  color: "var(--accent)",
  cursor: "pointer",
};
