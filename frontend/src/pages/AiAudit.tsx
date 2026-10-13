import { useEffect, useMemo, useState } from "react";
import { listGatewayActions, GatewayAction } from "@/lib/api";
import { useRouter } from "@/lib/router";
import { useT, type DictKey } from "@/lib/i18n";
import { CronHealthLine } from "@/components/CronHealthLine";

/**
 * /app/ai-audit — read-only "AI 行为审计" page.
 *
 * Charter v2 §定律 5 ("AI 自动执行,你事后过目即可") requires a single
 * place where the user can review what every agent has done across all
 * surfaces. The data lives in ``gateway_actions``; this page is the
 * dedicated audit view of that ledger.
 *
 * Distinct from /approvals:
 *  - read-only (no ✓ 批准 / ✗ 拒绝 / ↶ 撤销 buttons)
 *  - defaults to ALL states (not just pending), last 100 rows
 *  - top-of-page agent chip filter (intake / hermes / coach / ...)
 *  - row click → navigate to the target CRM record
 *
 * Distinct from DailyFeed / RecordDetail timelines: those are
 * interaction-centric and per-record/per-user. This is action-centric
 * and cross-agent, cross-kind, cross-record.
 */

// theme-guard:ignore-start
const STATE_BADGE: Record<string, { labelKey: DictKey; color: string }> = {
  pending_approval: { labelKey: "ai_audit.state.pending",  color: "#facc15" },
  executed:         { labelKey: "ai_audit.state.executed", color: "#4ade80" },
  approved:         { labelKey: "ai_audit.state.approved", color: "#60a5fa" },
  rejected:         { labelKey: "ai_audit.state.rejected", color: "#f87171" },
  reverted:         { labelKey: "ai_audit.state.reverted", color: "#94a3b8" },
  blocked:          { labelKey: "ai_audit.state.blocked",  color: "#ef4444" },
};
// theme-guard:ignore-end

const PAGE_LIMIT = 100;

export function AiAuditPage() {
  const t = useT();
  const { navigate } = useRouter();
  const [rows, setRows] = useState<GatewayAction[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [agentFilter, setAgentFilter] = useState<string>("");
  const [stateFilter, setStateFilter] = useState<string>("");

  async function load() {
    setLoading(true); setErr(null);
    try {
      const r = await listGatewayActions({ limit: PAGE_LIMIT });
      setRows(r.data || []);
    } catch (e: any) {
      setErr(e?.message || String(e));
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => { load(); /* eslint-disable-next-line */ }, []);

  // Distinct agents present in the loaded window, sorted by frequency.
  const agentChips = useMemo(() => {
    const counts = new Map<string, number>();
    rows.forEach(r => {
      const a = r.agent_name || "(unknown)";
      counts.set(a, (counts.get(a) || 0) + 1);
    });
    return [...counts.entries()].sort((a, b) => b[1] - a[1]);
  }, [rows]);

  const stateChips = useMemo(() => {
    const counts = new Map<string, number>();
    rows.forEach(r => counts.set(r.state, (counts.get(r.state) || 0) + 1));
    return [...counts.entries()].sort((a, b) => b[1] - a[1]);
  }, [rows]);

  const filtered = useMemo(() => rows.filter(r => {
    if (agentFilter && (r.agent_name || "(unknown)") !== agentFilter) return false;
    if (stateFilter && r.state !== stateFilter) return false;
    return true;
  }), [rows, agentFilter, stateFilter]);

  return (
    <div style={{ padding: 24, overflow: "auto", height: "100%" }}>
      <div style={{ marginBottom: 16 }}>
        <h2 style={{ margin: 0 }}>{t("ai_audit.title")}</h2>
        <p className="muted" style={{ marginTop: 4 }}>
          {t("ai_audit.subtitle")}
        </p>
      </div>

      {/* Backend liveness — sits above the action list so a dead cron
          is the first thing you see on this page. */}
      <CronHealthLine mode="full" />

      {/* Top filter row: agent chips + state chips + refresh */}
      <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 10 }}>
        <Chip
          label={t("ai_audit.filter.all_agents")} active={!agentFilter}
          onClick={() => setAgentFilter("")}
        />
        {agentChips.map(([name, n]) => (
          <Chip key={name} label={`${name} · ${n}`} active={agentFilter === name}
                onClick={() => setAgentFilter(agentFilter === name ? "" : name)} />
        ))}
      </div>
      <div style={{
        display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 14,
        alignItems: "center",
      }}>
        <Chip
          label={t("ai_audit.filter.all_states")} active={!stateFilter}
          onClick={() => setStateFilter("")}
        />
        {stateChips.map(([s, n]) => {
          const meta = STATE_BADGE[s];
          const lbl = meta ? t(meta.labelKey) : s;
          return (
            <Chip key={s} label={`${lbl} · ${n}`} active={stateFilter === s}
                  color={meta?.color}
                  onClick={() => setStateFilter(stateFilter === s ? "" : s)} />
          );
        })}
        <button className="ghost" onClick={load} style={{ marginLeft: "auto" }}>
          {t("ai_audit.refresh")}
        </button>
      </div>

      {err && <div className="alert alert-error">{err}</div>}
      {loading && <div className="muted">{t("common.loading")}</div>}
      {!loading && filtered.length === 0 && (
        <div className="muted" style={{ padding: 24, textAlign: "center" }}>
          {t("ai_audit.empty")}
        </div>
      )}

      <div style={{ border: "1px solid var(--border)", borderRadius: 6 }}>
        {filtered.map(a => <AuditRow key={a.id} a={a} t={t} navigate={navigate} />)}
      </div>

      {!loading && rows.length >= PAGE_LIMIT && (
        <div className="muted" style={{ fontSize: 13, marginTop: 8, textAlign: "center" }}>
          {t("ai_audit.window_hint", { n: String(PAGE_LIMIT) })}
        </div>
      )}
    </div>
  );
}

function Chip({ label, active, onClick, color }: {
  label: string; active: boolean; onClick: () => void; color?: string;
}) {
  return (
    <button
      onClick={onClick}
      style={{
        padding: "3px 10px",
        borderRadius: 999,
        fontSize: 14,
        cursor: "pointer",
        border: "1px solid",
        borderColor: active
          ? (color || "color-mix(in srgb, var(--accent) 55%, transparent)")
          : "var(--border)",
        background: active
          ? (color ? `${color}22` : "var(--accent-soft-strong)")
          : "transparent",
        color: active && color ? color : "inherit",
        fontWeight: active ? 600 : 400,
      }}
    >
      {label}
    </button>
  );
}

function AuditRow({ a, t, navigate }: {
  a: GatewayAction; t: ReturnType<typeof useT>;
  navigate: (to: string) => void;
}) {
  const target = a.target || {};
  const params: any = a.params || {};
  const meta = STATE_BADGE[a.state];
  const badgeLabel = meta ? t(meta.labelKey) : a.state;
  const badgeColor = meta ? meta.color : "var(--muted)";
  const timeStr = a.created_at
    ? new Date(a.created_at).toLocaleString(undefined, { hour12: false })
    : "—";
  const targetModule = (target.module || params.module_id || "") as string;
  const targetId = (target.id || params.record_id || "") as string | number;
  const canOpen = !!(targetModule && targetId);

  // Try multiple keys for the user-facing summary text. Coach proposals
  // store it as params.summary; CRM write actions stash the fields in
  // params.data; everything else just shows kind + agent.
  const summary: string =
    params.summary ||
    params.draft ||
    (params.data && Object.entries(params.data)
      .slice(0, 4)
      .map(([k, v]) => `${k}=${String(v).slice(0, 28)}`)
      .join(" · ")) ||
    "";
  const draft: string =
    params.action?.draft || params.action?.body ||
    params.action?.content || params.action?.text || "";
  const reasoning: string = params.reasoning || "";

  return (
    <div
      onClick={canOpen ? () => navigate(`/crm/${targetModule}/${targetId}`) : undefined}
      style={{
        padding: "10px 14px",
        borderBottom: "1px solid var(--border)",
        cursor: canOpen ? "pointer" : "default",
      }}
    >
      <div style={{
        display: "flex", justifyContent: "space-between",
        alignItems: "flex-start", gap: 10, marginBottom: 4,
      }}>
        <div style={{ minWidth: 0 }}>
          <span style={{ fontWeight: 600 }}>{a.kind || "(no kind)"}</span>
          <span className="muted" style={{ marginLeft: 8, fontSize: 14 }}>
            {a.agent_name || "—"}
          </span>
          {canOpen && (
            <span className="muted" style={{ marginLeft: 8, fontSize: 13 }}>
              → {targetModule} #{targetId}
            </span>
          )}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 10, flexShrink: 0 }}>
          <span style={{ color: badgeColor, fontWeight: 600, fontSize: 14 }}>
            {badgeLabel}
          </span>
          <span className="muted" style={{ fontSize: 13 }}>{timeStr}</span>
        </div>
      </div>
      {summary && (
        <div style={{
          fontSize: 14, lineHeight: 1.5, marginTop: 2,
          fontFamily: a.kind === "crm.write" ? "monospace" : "inherit",
          wordBreak: "break-all",
        }}>
          {summary}
        </div>
      )}
      {draft && (
        <pre style={{
          margin: "6px 0 0",
          padding: "6px 10px",
          fontSize: 13, lineHeight: 1.5,
          fontFamily: "inherit",
          whiteSpace: "pre-wrap", wordBreak: "break-word",
          background: "var(--surface-hover)",
          border: "1px dashed var(--border)", borderRadius: 4,
        }}>{draft}</pre>
      )}
      {reasoning && (
        <div className="muted" style={{ fontSize: 13, marginTop: 4, lineHeight: 1.5 }}>
          💡 {reasoning}
        </div>
      )}
    </div>
  );
}
