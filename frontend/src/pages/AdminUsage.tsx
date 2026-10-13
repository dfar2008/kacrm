/**
 * AdminUsage — token usage / cost report (formerly /analytics→用量统计 tab).
 *
 * Migrated to /admin/usage as part of the dashboards spec (§10): the old
 * /analytics surface is replaced by /dashboards (AI 行为统计 → AI dashboard);
 * usage stats are infrastructure-billing, not business analytics, so they
 * live under /admin instead.
 */
import React, { useEffect, useState } from "react";

import { getAnalytics, type Analytics } from "@/lib/api";
import { useT } from "@/lib/i18n";

const DAYS_OPTIONS = [7, 30, 90, 180];

function num(n: number | undefined | null): string {
  if (n == null) return "0";
  return n.toLocaleString();
}
function usd(n: number | undefined | null): string {
  if (n == null) return "$0.00";
  return `$${n.toFixed(4)}`;
}

export function AdminUsagePage() {
  const t = useT();
  const [data, setData] = useState<Analytics | null>(null);
  const [days, setDays] = useState(30);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    getAnalytics(days)
      .then((d) => { setData(d); setErr(null); })
      .catch((e) => setErr(e.message))
      .finally(() => setLoading(false));
  }, [days]);

  return (
    <div style={{ padding: 24, overflow: "auto", height: "100%" }}>
      <div style={{ display: "flex", justifyContent: "space-between",
                     alignItems: "center", marginBottom: 16 }}>
        <h2 style={{ margin: 0 }}>{t("analytics.page.title")}</h2>
        <select value={days} onChange={(e) => setDays(Number(e.target.value))}>
          {DAYS_OPTIONS.map((d) => (
            <option key={d} value={d}>
              {t("analytics.days_window", { days: d })}
            </option>
          ))}
        </select>
      </div>

      {err && <div className="alert alert-error">{err}</div>}
      {loading && <div className="muted">{t("common.loading")}</div>}

      {data && (
        <>
          <div style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))",
            gap: 10, marginBottom: 24,
          }}>
            <StatCard label={t("analytics.stat.sessions")} value={num(data.totals?.sessions)} />
            <StatCard label={t("analytics.stat.input_tokens")} value={num(data.totals?.input_tokens)} />
            <StatCard label={t("analytics.stat.output_tokens")} value={num(data.totals?.output_tokens)} />
            <StatCard label={t("analytics.stat.cache_read")} value={num(data.totals?.cache_read_tokens)} />
            <StatCard label={t("analytics.stat.reasoning")} value={num(data.totals?.reasoning_tokens)} />
            <StatCard label={t("analytics.stat.cost")} value={usd(data.totals?.estimated_cost_usd)} />
          </div>

          <h3 style={{ marginTop: 8 }}>{t("analytics.section.by_model")}</h3>
          {data.by_model?.length ? (
            <DataTable
              columns={[
                { key: "model", label: t("analytics.col.model") },
                { key: "sessions", label: t("analytics.col.sessions") },
                { key: "input_tokens", label: t("analytics.col.input"), render: (r: any) => num(r.input_tokens) },
                { key: "output_tokens", label: t("analytics.col.output"), render: (r: any) => num(r.output_tokens) },
                { key: "estimated_cost_usd", label: t("analytics.col.cost"), render: (r: any) => usd(r.estimated_cost_usd) },
              ]}
              rows={data.by_model}
            />
          ) : <div className="muted">{t("analytics.empty")}</div>}

          <h3 style={{ marginTop: 24 }}>{t("analytics.section.daily")}</h3>
          {data.daily?.length ? (
            <DataTable
              columns={[
                { key: "day", label: t("analytics.col.date") },
                { key: "sessions", label: t("analytics.col.sessions") },
                { key: "input_tokens", label: t("analytics.col.input"), render: (r: any) => num(r.input_tokens) },
                { key: "output_tokens", label: t("analytics.col.output"), render: (r: any) => num(r.output_tokens) },
                { key: "estimated_cost_usd", label: t("analytics.col.cost"), render: (r: any) => usd(r.estimated_cost_usd) },
              ]}
              rows={data.daily}
            />
          ) : <div className="muted">{t("analytics.empty")}</div>}
        </>
      )}
    </div>
  );
}

function StatCard({ label, value }: { label: string; value: string }) {
  return (
    <div style={{
      padding: "14px 16px",
      border: "1px solid var(--border)",
      borderRadius: 8,
      background: "var(--panel)",
    }}>
      <div className="muted" style={{ fontSize: 14 }}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: 600, marginTop: 4 }}>{value}</div>
    </div>
  );
}

interface Col {
  key: string;
  label: string;
  render?: (row: any) => React.ReactNode;
}

function DataTable({ columns, rows }: { columns: Col[]; rows: any[] }) {
  return (
    <div style={{ overflow: "auto", border: "1px solid var(--border)", borderRadius: 6 }}>
      <table style={{ width: "100%", borderCollapse: "collapse" }}>
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c.key} style={{
                textAlign: "left", padding: "8px 12px",
                borderBottom: "1px solid var(--border)",
                fontSize: 14, textTransform: "uppercase", letterSpacing: 0.3,
              }}>{c.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              {columns.map((c) => (
                <td key={c.key} style={{
                  padding: "8px 12px",
                  borderBottom: "1px solid var(--border)",
                }}>
                  {c.render ? c.render(r) : r[c.key] ?? "—"}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default AdminUsagePage;
