/**
 * AdminExportAudit — dashboard CSV 导出审计日志查询页(admin-only)。
 *
 * 数据源:GET /api/dashboards/export-audit。后端记录每次
 * /api/dashboards/charts/{id}/drill/export 的元数据(caller / scope /
 * chart / entity / 行列数),本页提供按用户 + 时间窗的检索 + 分页。
 */
import React, { useEffect, useState } from "react";

import {
  listDashboardExportAudit,
  listUsersPicker,
  type DashboardExportAuditItem,
} from "@/lib/api";
import { useT } from "@/lib/i18n";

const PAGE_SIZE = 50;

interface UserOption {
  id: number;
  name: string;
  username?: string;
  department_name?: string;
}

function entityLabel(t: (k: any) => string, e: string | undefined | null): string {
  if (!e) return "-";
  const key = `export_audit.entity.${e}`;
  const v = t(key);
  // translate() returns the bare key when no entry exists in either locale —
  // fall back to the raw entity name so a newly-added entity isn't broken.
  return v === key ? e : v;
}

function fmtTime(s: string | null): string {
  if (!s) return "-";
  const d = new Date(s);
  if (Number.isNaN(d.getTime())) return s;
  return d.toLocaleString();
}

export function AdminExportAuditPage() {
  const t = useT();
  const [callerId, setCallerId] = useState<string>("");
  const [since, setSince] = useState<string>("");
  const [until, setUntil] = useState<string>("");
  const [offset, setOffset] = useState(0);
  const [items, setItems] = useState<DashboardExportAuditItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [users, setUsers] = useState<UserOption[]>([]);

  useEffect(() => {
    listUsersPicker("", 500)
      .then((us) => setUsers(us))
      .catch(() => { /* silently fall back to empty; user can still query unfiltered */ });
  }, []);

  const load = (newOffset: number) => {
    setLoading(true);
    setErr(null);
    listDashboardExportAudit({
      caller_id: callerId.trim() ? Number(callerId.trim()) : undefined,
      since: since ? new Date(since).toISOString() : undefined,
      until: until ? new Date(until).toISOString() : undefined,
      limit: PAGE_SIZE,
      offset: newOffset,
    })
      .then((d) => {
        setItems(d.items);
        setTotal(d.total);
        setOffset(d.offset);
      })
      .catch((e) => setErr(e.message))
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(0); /* eslint-disable-next-line */ }, []);

  const onSearch = () => load(0);
  const onReset = () => {
    setCallerId(""); setSince(""); setUntil("");
    setTimeout(() => load(0), 0);
  };
  const onPrev = () => offset > 0 && load(Math.max(0, offset - PAGE_SIZE));
  const onNext = () => offset + PAGE_SIZE < total && load(offset + PAGE_SIZE);

  return (
    <div style={{ padding: 24, overflow: "auto", height: "100%", maxWidth: 1080 }}>
      <h2 style={{ marginTop: 0 }}>{t("export_audit.page.title")}</h2>
      <p className="muted" style={{ marginTop: -8 }}>
        {t("export_audit.page.desc")}
      </p>

      <div style={{
        display: "flex", flexWrap: "wrap", gap: 12,
        alignItems: "flex-end", marginBottom: 16,
      }}>
        <Field label={t("export_audit.filter.caller")}>
          <select
            value={callerId}
            onChange={(e) => setCallerId(e.target.value)}
            style={{ minWidth: 180 }}
          >
            <option value="">{t("export_audit.filter.caller_all")}</option>
            {users.map((u) => (
              <option key={u.id} value={String(u.id)}>
                {u.name}
                {u.department_name ? ` · ${u.department_name}` : ""}
                {` #${u.id}`}
              </option>
            ))}
          </select>
        </Field>
        <Field label={t("export_audit.filter.since")}>
          <input
            type="datetime-local"
            value={since}
            onChange={(e) => setSince(e.target.value)}
          />
        </Field>
        <Field label={t("export_audit.filter.until")}>
          <input
            type="datetime-local"
            value={until}
            onChange={(e) => setUntil(e.target.value)}
          />
        </Field>
        <button onClick={onSearch} disabled={loading}>
          {t("export_audit.btn.search")}
        </button>
        <button onClick={onReset} disabled={loading}>
          {t("export_audit.btn.reset")}
        </button>
      </div>

      {err && <div className="alert alert-error">{err}</div>}
      {loading && <div className="muted">{t("common.loading")}</div>}

      {/* 表格填满内容容器(页级 maxWidth 1080 约束),分页同宽对齐。
          .data-table 已有单元格内边距,列分隔自然,不再靠拉开空隙。 */}
      <table className="data-table" style={{ width: "100%", marginTop: 8 }}>
        <thead>
          <tr>
            <th>{t("export_audit.col.time")}</th>
            <th>{t("export_audit.col.caller")}</th>
            <th>{t("export_audit.col.scope")}</th>
            <th>{t("export_audit.col.chart_id")}</th>
            <th>{t("export_audit.col.entity")}</th>
            <th style={{ textAlign: "right" }}>{t("export_audit.col.rows")}</th>
            <th style={{ textAlign: "right" }}>{t("export_audit.col.cols")}</th>
          </tr>
        </thead>
        <tbody>
          {items.length === 0 && !loading && (
            <tr><td colSpan={7} className="muted" style={{ textAlign: "center", padding: 24 }}>
              {t("export_audit.empty")}
            </td></tr>
          )}
          {items.map((r) => (
            <tr key={r.id}>
              <td>{fmtTime(r.occurred_at)}</td>
              <td>
                {r.caller_name || "-"}
                <span className="muted" style={{ marginLeft: 6, fontSize: 14 }}>
                  #{r.caller_id}
                </span>
              </td>
              <td>{t(`export_audit.scope.${r.scope_kind}`)}</td>
              <td>
                {r.chart_title || <span className="muted">{t("export_audit.chart_deleted")}</span>}
                {r.resource_filter?.chart_id && (
                  <div className="muted" style={{ fontSize: 13, fontFamily: "monospace" }}>
                    {r.resource_filter.chart_id}
                  </div>
                )}
              </td>
              <td>{entityLabel(t, r.resource_filter?.entity)}</td>
              <td style={{ textAlign: "right" }}>
                {(r.resource_filter?.row_count ?? "-")?.toLocaleString?.() ?? "-"}
              </td>
              <td style={{ textAlign: "right" }}>
                {r.resource_filter?.column_count ?? "-"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <div style={{
        marginTop: 12, display: "flex", justifyContent: "space-between",
        alignItems: "center",
      }}>
        <span className="muted">
          {t("export_audit.pager.range", {
            from: total === 0 ? 0 : offset + 1,
            to: Math.min(offset + PAGE_SIZE, total),
            total,
          })}
        </span>
        <div style={{ display: "flex", gap: 8 }}>
          <button onClick={onPrev} disabled={loading || offset === 0}>
            {t("export_audit.pager.prev")}
          </button>
          <button
            onClick={onNext}
            disabled={loading || offset + PAGE_SIZE >= total}
          >
            {t("export_audit.pager.next")}
          </button>
        </div>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 14 }}>
      <span className="muted">{label}</span>
      {children}
    </label>
  );
}
