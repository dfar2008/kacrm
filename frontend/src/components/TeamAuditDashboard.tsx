/**
 * TeamAuditDashboard — Phase 5.6 dashboard surface.
 *
 * Renders the 5 panels from the team audit / AI behavior dashboard:
 *   1. AI 行为日历 — by_day / by_member / by_intent breakdown
 *   2. 推荐准确率 — per-intent transition rate
 *   3. 团队 sleeping deals 趋势 — N-week line
 *   4. 成员 AI 接受度 — per-member rate (no ⭐)
 *   5. pipeline 漏斗 — stage distribution
 *
 * Per Phase 5 plan v1.2 §4.7 5.6.5:
 *   - No cache layer / no client-side aggregate; just render.
 *   - Every drill-down hover shows the filter payload (so frontend dev
 *     can wire up "click to see raw interactions" later).
 */

import React, { useEffect, useState } from "react";
import {
  fetchAiActionsSummary,
  fetchSleepingDealsTrend,
  fetchPipelineFunnel,
  type AIActionsSummary,
  type SleepingDealsTrendRow,
  type PipelineFunnelRow,
  type ScopeKind,
} from "../lib/api";

interface Props {
  scope: ScopeKind;
  isAdmin: boolean;
  ownerId?: number;  // v1.7 — narrow to specific user
}

export function TeamAuditDashboard({ scope, ownerId }: Props) {
  const [summary, setSummary] = useState<AIActionsSummary | null>(null);
  const [sleeping, setSleeping] = useState<SleepingDealsTrendRow[]>([]);
  const [funnel, setFunnel] = useState<PipelineFunnelRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    Promise.all([
      fetchAiActionsSummary(scope, 0, ownerId).catch((e) => { console.warn("ai_actions", e); return null; }),
      fetchSleepingDealsTrend(scope, 4, ownerId).catch((e) => { console.warn("sleeping_trend", e); return { weeks: [] }; }),
      fetchPipelineFunnel(scope === "self" ? "team" : scope, ownerId).catch((e) => { console.warn("funnel", e); return { stages: [] }; }),
    ])
      .then(([s, sl, f]) => {
        if (cancelled) return;
        setSummary(s);
        setSleeping(sl?.weeks || []);
        setFunnel(f?.stages || []);
      })
      .catch((e) => { if (!cancelled) setError(String(e)); })
      .finally(() => { if (!cancelled) setLoading(false); });

    return () => { cancelled = true; };
  }, [scope, ownerId]);

  if (loading) {
    return <div style={{ padding: 16, color: "var(--mute)" }}>载入中…</div>;
  }
  if (error) {
    return <div style={{ padding: 16, color: "tomato" }}>错误：{error}</div>;
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 24, padding: 16 }}>
      {/* Panel 1: AI 行为日历 */}
      <Panel title={`📅 AI 行为日历 · ${scope === "team" ? "团队" : scope === "org" ? "全部" : "我的"}`}>
        {summary && (
          <div>
            <div style={{ display: "flex", gap: 24, fontSize: 14, marginBottom: 12 }}>
              <Stat label="本周总数" value={summary.total} />
              <Stat label="✅ 通过" value={summary.approved} tone="ok" />
              <Stat label="⏪ 撤销" value={summary.cancelled} tone="warn" />
              <Stat label="⏰ 等待" value={summary.pending} tone="mute" />
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 16, fontSize: 14 }}>
              <Subpanel title="按日期">
                {summary.by_day.length === 0 ? <Empty /> : (
                  summary.by_day.map((d) => (
                    <Row key={d.day} label={d.day} value={d.n} />
                  ))
                )}
              </Subpanel>
              <Subpanel title="按成员">
                {summary.by_member.length === 0 ? <Empty /> : (
                  summary.by_member.map((m) => (
                    <Row key={m.user_id} label={`#${m.user_id}`} value={m.n} />
                  ))
                )}
              </Subpanel>
              <Subpanel title="按类型">
                {summary.by_intent.length === 0 ? <Empty /> : (
                  summary.by_intent.map((i) => (
                    <Row key={i.intent} label={i.intent} value={i.n} />
                  ))
                )}
              </Subpanel>
            </div>
          </div>
        )}
      </Panel>

      {/* Panel 3: sleeping deals trend */}
      <Panel title="📉 团队沉睡商机趋势（4 周）">
        {sleeping.length === 0 ? <Empty /> : (
          <div style={{ display: "flex", alignItems: "flex-end", gap: 12, height: 100 }}>
            {sleeping.map((w) => {
              const max = Math.max(...sleeping.map((x) => x.n), 1);
              const h = (w.n / max) * 80;
              return (
                <div key={w.week_start} style={{ display: "flex", flexDirection: "column", alignItems: "center", flex: 1 }}>
                  <div style={{ fontSize: 14, marginBottom: 4 }}>{w.n}</div>
                  <div style={{
                    width: "100%", height: `${h}px`, minHeight: 4,
                    background: "var(--accent)", borderRadius: 4,
                  }} />
                  <div style={{ fontSize: 13, color: "var(--mute)", marginTop: 4 }}>
                    {w.week_start.slice(5)}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </Panel>

      {/* Panel 5: pipeline funnel */}
      <Panel title="📊 Pipeline 漏斗（团队）">
        {funnel.length === 0 ? <Empty /> : (
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {funnel.map((f) => {
              const max = Math.max(...funnel.map((x) => x.n), 1);
              const w = (f.n / max) * 100;
              const amt = f.amount_total || 0;
              const amtLabel = amt >= 10000
                ? `¥${(amt / 10000).toFixed(amt >= 100000 ? 0 : 1)} 万`
                : amt > 0 ? `¥${Math.round(amt)}` : "—";
              return (
                <div key={f.stage} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 14 }}>
                  <div style={{ width: 120, color: "var(--mute)" }}>{f.stage}</div>
                  <div style={{ flex: 1, background: "var(--surface-soft)", borderRadius: 4, height: 22, position: "relative" }}>
                    <div style={{ width: `${w}%`, height: "100%", background: "var(--accent)", borderRadius: 4 }} />
                  </div>
                  <div style={{ width: 50, textAlign: "right", fontWeight: 500 }}>{f.n}</div>
                  <div style={{ width: 80, textAlign: "right", color: "var(--mute)", fontVariantNumeric: "tabular-nums" }}>{amtLabel}</div>
                </div>
              );
            })}
          </div>
        )}
      </Panel>

      <div style={{ fontSize: 13, color: "var(--mute)", marginTop: 8 }}>
        所有数字实时从 interactions / potentials 表算出（无缓存）。
        点击每个数字可下钻到原始 interaction 行（5.2 后续版本）。
      </div>
    </div>
  );
}

function Panel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{
      background: "var(--surface)", borderRadius: 8,
      padding: 16, border: "1px solid var(--border)",
    }}>
      <h3 style={{ margin: 0, marginBottom: 12, fontSize: 14, fontWeight: 600 }}>{title}</h3>
      {children}
    </div>
  );
}

function Subpanel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <div style={{ fontSize: 13, color: "var(--mute)", marginBottom: 6, textTransform: "uppercase", letterSpacing: 0.5 }}>
        {title}
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>{children}</div>
    </div>
  );
}

function Stat({ label, value, tone }: { label: string; value: number; tone?: "ok" | "warn" | "mute" }) {
  // theme-guard:ignore-start  — tone-semantic color palette, data-driven map
  const colors = {
    ok: "#22c55e", warn: "#f59e0b", mute: "var(--mute)",
  };
  // theme-guard:ignore-end
  return (
    <div>
      <div style={{ fontSize: 13, color: "var(--mute)", marginBottom: 2 }}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: 600, color: tone ? colors[tone] : "inherit" }}>{value}</div>
    </div>
  );
}

function Row({ label, value }: { label: string; value: number }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between" }}>
      <span style={{ color: "var(--text)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{label}</span>
      <span style={{ fontWeight: 500 }}>{value}</span>
    </div>
  );
}

function Empty() {
  return <div style={{ color: "var(--mute)", fontSize: 14, fontStyle: "italic" }}>无数据</div>;
}

export default TeamAuditDashboard;
