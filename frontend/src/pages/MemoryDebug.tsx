import { useState } from "react";
import { useRouter } from "@/lib/router";
import {
  searchMemories,
  getEntity,
  getSubgraph,
  triggerMemoryIngest,
  mergeEntities,
  type MemoryHit,
  type MemoryEntity,
  type MemorySubgraph,
} from "@/lib/api";
import { useT } from "@/lib/i18n";

/**
 * Memory Substrate inspection panel — admin / ops only.
 *
 * Linked from the ⚙️ gear on the product-facing Memory home
 * (``Memory.tsx``). Everything below is raw — it's for debugging
 * ingestion, merge, and the graph shape, not for salespeople.
 */
export function MemoryDebugPage() {
  const { navigate } = useRouter();
  const t = useT();
  return (
    <div style={{ padding: 24, overflow: "auto", height: "100%" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
        <div>
          <button style={linkBtn} onClick={() => navigate("/memory")}>{t("memory.detail.back")}</button>
          <h2 style={{ margin: "6px 0 0" }}>{t("memory.debug.title")}</h2>
          <div style={{ color: "var(--muted)", fontSize: 14 }}>
            {t("memory.debug.subtitle")}
          </div>
        </div>
        <IngestButton />
      </div>
      <div style={{
        display: "grid",
        gridTemplateColumns: "minmax(320px, 1fr) minmax(320px, 1fr)",
        gap: 16,
      }}>
        <SearchPanel />
        <EntityPanel />
      </div>
    </div>
  );
}

// ── Search ────────────────────────────────────────────────────────────
function SearchPanel() {
  const t = useT();
  const [q, setQ] = useState("");
  const [topK, setTopK] = useState(10);
  const [minSim, setMinSim] = useState(0.0);
  const [hits, setHits] = useState<MemoryHit[]>([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function doSearch(e?: React.FormEvent) {
    e?.preventDefault();
    if (!q.trim()) return;
    setLoading(true);
    setErr(null);
    try {
      const r = await searchMemories(q.trim(), { topK, minSimilarity: minSim });
      setHits(r.data);
    } catch (ex: any) {
      setErr(ex?.message || t("memory.debug.search.failed"));
      setHits([]);
    } finally {
      setLoading(false);
    }
  }

  return (
    <section style={cardStyle}>
      <h3 style={{ marginTop: 0 }}>{t("memory.debug.search.title")}</h3>
      <form onSubmit={doSearch} style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <input
          value={q}
          onChange={e => setQ(e.target.value)}
          placeholder={t("memory.debug.search.placeholder")}
          style={{ flex: "1 1 200px", padding: "6px 8px" }}
        />
        <input
          type="number" min={1} max={50}
          value={topK}
          onChange={e => setTopK(Number(e.target.value))}
          style={{ width: 60, padding: "6px 8px" }}
          title="top_k"
        />
        <input
          type="number" step={0.05} min={0} max={1}
          value={minSim}
          onChange={e => setMinSim(Number(e.target.value))}
          style={{ width: 72, padding: "6px 8px" }}
          title="min_similarity"
        />
        <button type="submit" disabled={loading}>{t("memory.debug.search.submit")}</button>
      </form>
      {err && <div className="alert alert-error" style={{ marginTop: 8 }}>{err}</div>}
      {loading && <div className="muted" style={{ marginTop: 8 }}>{t("memory.debug.search.loading")}</div>}
      <div style={{ marginTop: 12, display: "flex", flexDirection: "column", gap: 8 }}>
        {hits.map(h => (
          <div key={h.id} style={hitStyle}>
            <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
              <span className="muted" style={{ fontSize: 14 }}>
                #{h.id} · {h.kind || "memory"}
                {h.source ? ` · ${h.source}` : ""}
              </span>
              <span style={simBadge(h.similarity)}>
                {(h.similarity * 100).toFixed(1)}%
              </span>
            </div>
            <div style={{ whiteSpace: "pre-wrap", fontSize: 14 }}>
              {h.content}
            </div>
          </div>
        ))}
        {!loading && hits.length === 0 && q && !err && (
          <div className="muted">{t("memory.debug.search.empty")}</div>
        )}
      </div>
    </section>
  );
}

// ── Entity + subgraph + merge ─────────────────────────────────────────
function EntityPanel() {
  const t = useT();
  const [entityType, setEntityType] = useState("org");
  const [canonical, setCanonical] = useState("");
  const [entity, setEntity] = useState<MemoryEntity | null>(null);
  const [graph, setGraph] = useState<MemorySubgraph | null>(null);
  const [depth, setDepth] = useState(1);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function doLookup(e?: React.FormEvent) {
    e?.preventDefault();
    if (!canonical.trim()) return;
    setLoading(true);
    setErr(null);
    setGraph(null);
    try {
      const r = await getEntity(entityType.trim(), canonical.trim());
      setEntity(r.data);
      const g = await getSubgraph(r.data.id, { depth, maxEdges: 200 });
      setGraph(g.data);
    } catch (ex: any) {
      setErr(ex?.status === 404 ? t("memory.debug.entity.not_found") : (ex?.message || t("memory.debug.entity.lookup_failed")));
      setEntity(null);
    } finally {
      setLoading(false);
    }
  }

  async function doMerge() {
    if (!entity) return;
    const raw = window.prompt(t("memory.debug.merge.prompt"));
    if (!raw) return;
    const srcId = Number(raw);
    if (!Number.isFinite(srcId) || srcId <= 0 || srcId === entity.id) {
      window.alert(t("memory.debug.merge.invalid"));
      return;
    }
    if (!window.confirm(t("memory.debug.merge.confirm", { src: srcId, dst: entity.id }))) return;
    try {
      const r = await mergeEntities(entity.id, srcId);
      setEntity(r.data);
      window.alert(t("memory.debug.merge.success"));
    } catch (ex: any) {
      window.alert(ex?.message || t("memory.debug.merge.failed"));
    }
  }

  return (
    <section style={cardStyle}>
      <h3 style={{ marginTop: 0 }}>{t("memory.debug.entity.title")}</h3>
      <form onSubmit={doLookup} style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <input
          value={entityType}
          onChange={e => setEntityType(e.target.value)}
          placeholder={t("memory.debug.entity.type_placeholder")}
          style={{ width: 140, padding: "6px 8px" }}
        />
        <input
          value={canonical}
          onChange={e => setCanonical(e.target.value)}
          placeholder={t("memory.debug.entity.canonical_placeholder")}
          style={{ flex: "1 1 160px", padding: "6px 8px" }}
        />
        <input
          type="number" min={1} max={3}
          value={depth}
          onChange={e => setDepth(Number(e.target.value))}
          style={{ width: 52, padding: "6px 8px" }}
          title="depth"
        />
        <button type="submit" disabled={loading}>{t("memory.debug.entity.submit")}</button>
      </form>
      {err && <div className="alert alert-error" style={{ marginTop: 8 }}>{err}</div>}
      {loading && <div className="muted" style={{ marginTop: 8 }}>{t("memory.debug.entity.loading")}</div>}
      {entity && (
        <div style={{ marginTop: 12 }}>
          <div style={hitStyle}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
              <div className="muted" style={{ fontSize: 14 }}>
                #{entity.id} · {entity.entity_type} · {entity.user_id == null ? "org" : t("memory.debug.entity.scope_user", { uid: entity.user_id })}
              </div>
              <button style={mergeBtn} onClick={doMerge}>{t("memory.debug.entity.merge_button")}</button>
            </div>
            <div style={{ fontSize: 15, fontWeight: 600, marginTop: 4 }}>{entity.canonical}</div>
            {(entity.attributes || entity.attrs) && Object.keys(entity.attributes || entity.attrs || {}).length > 0 && (
              <pre style={{
                margin: "6px 0 0", fontSize: 14,
                background: "var(--panel-2)",
                padding: 8, borderRadius: 4, overflow: "auto",
              }}>
                {JSON.stringify(entity.attributes || entity.attrs, null, 2)}
              </pre>
            )}
          </div>
          {graph && (
            <div style={{ marginTop: 12 }}>
              <h4 style={{ margin: "4px 0" }}>
                {t("memory.debug.subgraph.heading", { e: graph.entities.length, g: graph.edges.length })}
              </h4>
              <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                {graph.edges.map((ed, i) => {
                  const src = graph.entities.find(n => n.id === ed.src);
                  const dst = graph.entities.find(n => n.id === ed.dst);
                  return (
                    <div key={i} style={{
                      fontSize: 14,
                      padding: "4px 8px",
                      background: "var(--panel-2)",
                      borderRadius: 4,
                    }}>
                      <b>{src?.canonical || `#${ed.src}`}</b>
                      <span className="muted" style={{ margin: "0 6px" }}>
                        —[{ed.relation}]→
                      </span>
                      <b>{dst?.canonical || `#${ed.dst}`}</b>
                    </div>
                  );
                })}
                {graph.edges.length === 0 && (
                  <div className="muted" style={{ fontSize: 14 }}>{t("memory.debug.subgraph.no_edges")}</div>
                )}
              </div>
            </div>
          )}
        </div>
      )}
    </section>
  );
}

// ── Ingest button (admin) ─────────────────────────────────────────────
function IngestButton() {
  const t = useT();
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  async function fire() {
    setBusy(true);
    setMsg(null);
    try {
      const r = await triggerMemoryIngest({ sinceMinutes: 60, limit: 500 });
      setMsg(t("memory.debug.ingest.success", { n: r.data.ingested }));
    } catch (ex: any) {
      setMsg(ex?.status === 403 ? t("memory.debug.ingest.forbidden") : (ex?.message || t("memory.debug.ingest.failed")));
    } finally {
      setBusy(false);
      setTimeout(() => setMsg(null), 4000);
    }
  }
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
      {msg && <span className="muted" style={{ fontSize: 14 }}>{msg}</span>}
      <button onClick={fire} disabled={busy} title={t("memory.debug.ingest.tooltip")}>
        {busy ? t("memory.debug.ingest.busy") : t("memory.debug.ingest.submit")}
      </button>
    </div>
  );
}

const linkBtn: React.CSSProperties = {
  background: "transparent", border: "none", color: "var(--muted)",
  cursor: "pointer", fontSize: 14, padding: 0,
};
const cardStyle: React.CSSProperties = {
  padding: 16,
  border: "1px solid var(--border)",
  borderRadius: 8,
  background: "var(--panel)",
};
const hitStyle: React.CSSProperties = {
  padding: 10,
  border: "1px solid var(--border)",
  borderRadius: 6,
  background: "var(--panel-2)",
};
const mergeBtn: React.CSSProperties = {
  padding: "3px 10px", fontSize: 14,
  background: "transparent", color: "var(--muted)",
  border: "1px solid var(--border)", borderRadius: 6, cursor: "pointer",
};
function simBadge(sim: number): React.CSSProperties {
  const hue = Math.max(0, Math.min(1, sim)) * 120; // red→green
  // theme-guard:ignore-start
  return {
    fontSize: 13,
    padding: "2px 6px",
    borderRadius: 10,
    background: `hsl(${hue}, 60%, 25%)`,
    color: `hsl(${hue}, 80%, 85%)`,
  };
  // theme-guard:ignore-end
}
