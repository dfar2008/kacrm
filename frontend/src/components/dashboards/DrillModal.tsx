import React, { useEffect, useState } from "react";

import { drillChart } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { colLabel } from "./labels";

interface Props {
  chartId: string;
  dimFilters: Record<string, any>;
  /** Optional per-column display-label map carried over from the parent
   * chart's column_labels. Used so the filter chip shows
   * "部门 = 销售部" instead of "部门 = 2" when the dim is a ref FK. */
  columnLabels?: Record<string, Record<string, string>>;
  onClose: () => void;
}

export function DrillModal({ chartId, dimFilters, columnLabels, onClose }: Props) {
  const t = useT();
  const [data, setData] = useState<{
    columns: string[];
    rows: any[][];
    column_labels?: Record<string, Record<string, string>>;
  } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // Merge drill-time labels (returned by /drill endpoint, covers
  // FK columns like owner_user_id, department_id, accounts_id) with
  // labels carried over from the parent chart's column_labels (only
  // covers the dim that was clicked).
  const effectiveLabels: Record<string, Record<string, string>> = {
    ...(columnLabels || {}),
    ...((data?.column_labels) || {}),
  };

  const displayValue = (col: string, raw: any): string => {
    if (raw == null) return "";
    const lookup = effectiveLabels[col];
    if (lookup && lookup[String(raw)] !== undefined) {
      return lookup[String(raw)];
    }
    return String(raw);
  };

  useEffect(() => {
    setLoading(true);
    drillChart(chartId, dimFilters, [])
      .then((d) => { setData(d); setError(null); })
      .catch((e) => setError(e.message || "drill failed"))
      .finally(() => setLoading(false));
  }, [chartId, JSON.stringify(dimFilters)]);

  const exportCsv = () => {
    if (!data) return;
    // Build CSV client-side so headers match what the user sees in the
    // modal (localized via colLabel) instead of raw column names.
    // Also substitutes ref-typed dim values via columnLabels for the
    // same reason the modal filter chip does.
    const esc = (v: any): string => {
      if (v == null) return "";
      const s = String(v);
      if (/[",\n\r]/.test(s)) return `"${s.replace(/"/g, '""')}"`;
      return s;
    };
    const headerRow = data.columns.map((c) => esc(colLabel(t, c))).join(",");
    const bodyRows = data.rows.map((row) =>
      row.map((v, i) => esc(v == null ? "" : displayValue(data.columns[i], v)))
        .join(","),
    );
    const csv = "﻿" + [headerRow, ...bodyRows].join("\r\n");
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `drill_${chartId}.csv`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  return (
    <div
      style={{
        position: "fixed", inset: 0, background: "var(--overlay)",
        backdropFilter: "blur(4px)",
        display: "flex", alignItems: "center", justifyContent: "center",
        zIndex: 1000,
      }}
      onClick={onClose}
    >
      <div
        style={{
          background: "var(--panel)",
          color: "var(--fg)",
          border: "1px solid var(--border)",
          borderRadius: "var(--radius)",
          maxWidth: 1000, maxHeight: "82vh", width: "92%",
          padding: 0, overflow: "hidden",
          display: "flex", flexDirection: "column",
          boxShadow: "var(--shadow-modal)",
        }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* header */}
        <div style={{
          display: "flex", alignItems: "center",
          padding: "12px 16px",
          borderBottom: "1px solid var(--border)",
          background: "var(--panel-2)",
        }}>
          <strong style={{ fontSize: 14 }}>{t("dashboards.drill.title")}</strong>
          {Object.entries(dimFilters).length > 0 && (
            <span style={{
              marginLeft: 10, fontSize: 13,
              padding: "2px 8px", borderRadius: 4,
              background: "var(--panel)", color: "var(--muted)",
              border: "1px solid var(--border)",
            }}>
              {Object.entries(dimFilters).map(([k, v]) =>
                `${colLabel(t, k)} = ${v == null ? "—" : displayValue(k, v)}`
              ).join(", ")}
            </span>
          )}
          <div style={{ flex: 1 }} />
          <button onClick={exportCsv}
                  style={{
                    padding: "5px 10px", fontSize: 14,
                    background: "var(--panel)", color: "var(--fg)",
                    border: "1px solid var(--border)", borderRadius: 4,
                    cursor: "pointer", marginRight: 6,
                  }}>
            {t("dashboards.drill.export_csv")}
          </button>
          <button onClick={onClose} className="icon-close"
                  style={{
                    background: "transparent", color: "var(--muted)",
                    border: "none", cursor: "pointer",
                  }}>×</button>
        </div>

        {/* body */}
        <div style={{ flex: 1, overflow: "auto", padding: "8px 16px 14px" }}>
          {loading && (
            <div style={{ padding: 24, color: "var(--muted)", fontSize: 14 }}>
              {t("dashboards.drill.loading")}
            </div>
          )}
          {error && (
            <div style={{ padding: 16, color: "var(--danger)", fontSize: 14 }}>
              {error}
            </div>
          )}
          {data && (
            <>
              <table style={{
                width: "100%", borderCollapse: "collapse", fontSize: 14,
              }}>
                <thead>
                  <tr>
                    {data.columns.map((c) => (
                      <th key={c}
                          style={{
                            textAlign: "left",
                            borderBottom: "1px solid var(--border)",
                            padding: "8px 10px",
                            fontSize: 13, letterSpacing: 0.2,
                            fontWeight: 500,
                            color: "var(--muted)",
                            background: "var(--panel-2)",
                            position: "sticky", top: 0,
                          }}>{colLabel(t, c)}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {data.rows.map((r, i) => (
                    <tr key={i}>
                      {r.map((v, j) => (
                        <td key={j}
                            style={{
                              padding: "7px 10px",
                              borderBottom: "1px solid var(--border)",
                              color: "var(--fg)",
                              fontVariantNumeric: "tabular-nums",
                            }}>
                          {v == null
                            ? <span style={{ color: "var(--muted)" }}>—</span>
                            : displayValue(data.columns[j], v)}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
              {data.rows.length === 0 && (
                <div style={{
                  padding: 32, textAlign: "center", fontSize: 14,
                  color: "var(--muted)",
                }}>
                  {t("dashboards.drill.empty")}
                </div>
              )}
            </>
          )}
        </div>

        {/* footer */}
        {data && data.rows.length > 0 && (
          <div style={{
            padding: "8px 16px", fontSize: 13, color: "var(--muted)",
            borderTop: "1px solid var(--border)", background: "var(--panel-2)",
          }}>
            {data.rows.length} {t("dashboards.drill.row_count_suffix")}
          </div>
        )}
      </div>
    </div>
  );
}

export default DrillModal;
