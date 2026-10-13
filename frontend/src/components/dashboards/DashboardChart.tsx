/**
 * DashboardChart — dispatches one of 8 chart types via Recharts.
 *
 * Anti-corrosion: never import any Decisions-page component
 * (DecisionCard / ActionChip / EvidenceChart). Enforced at review time
 * (spec §11.a; dependency-cruiser config in repo follow-up).
 *
 * All colors come from theme CSS vars so the chart looks coherent in
 * dark + light mode.
 */
import React from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ComposedChart,
  Funnel,
  FunnelChart,
  LabelList,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { ChartData } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { colLabel, valueLabel } from "./labels";

interface Props {
  data: ChartData;
  onDrill?: (dimFilters: Record<string, any>) => void;
}

// Resolve a CSS var at render time so charts pick up theme changes.
function cssVar(name: string, fallback: string): string {
  if (typeof window === "undefined") return fallback;
  const v = getComputedStyle(document.documentElement)
    .getPropertyValue(name).trim();
  return v || fallback;
}

// 仪表盘配色:最早的蓝色系多彩盘(蓝为主,避开青绿与紫,避免单色过重)
// theme-guard:ignore-start
const PALETTE = [
  "#7aa2f7", "#7dcfff", "#9ece6a", "#e0af68",
  "#f7768e", "#ff9e64", "#6E8BFF", "#c0caf5",
];
// theme-guard:ignore-end

/** Build per-row objects with raw values (used for drill callbacks). */
function rowsToObjs(d: ChartData) {
  return d.rows.map((r) =>
    Object.fromEntries(d.columns.map((c, i) => [c, r[i]])),
  );
}

/** Build per-row objects where ref-typed dim values are swapped for
 * their display labels and null/empty x-dim values are replaced with a
 * localized placeholder. Used purely for rendering — drill callbacks
 * use the raw-objs version so SQL keeps matching against the original
 * (possibly null) value via IS NULL.
 *
 * Also runs valueLabel(t, col, v) for enum values so milestone strings
 * like "initial_contact" render as "初步接触" / "Initial contact".
 */
function rowsToLabeledObjs(
  d: ChartData, xCol: string, emptyLabel: string,
  t: (key: string) => string,
) {
  const labels = d.column_labels || {};
  return d.rows.map((r, idx) => {
    const obj = Object.fromEntries(
      d.columns.map((c, i) => {
        const raw = r[i];
        const lookup = labels[c];
        if (lookup && raw != null) {
          const replaced = lookup[String(raw)];
          if (replaced !== undefined) return [c, replaced];
        }
        // For the chart's nameKey/x-dim column, replace null / empty
        // with the placeholder so the Recharts legend doesn't fall
        // back to the dataKey name (which would show "count" etc.).
        if (c === xCol && (raw == null || raw === "")) {
          return [c, emptyLabel];
        }
        // Try the enum-value dictionary (milestones, statuses, etc.).
        if (c === xCol && typeof raw === "string") {
          const localized = valueLabel(t as any, c, raw);
          if (localized && localized !== String(raw)) return [c, localized];
        }
        return [c, raw];
      }),
    );
    // Stash the raw row for drill so we can hand back the original
    // FK / null value rather than the display label.
    (obj as any).__raw_idx = idx;
    return obj;
  });
}

/** Generic value formatter (for chart titles, drill cells, tooltips). */
function fmt(v: any): string {
  if (v == null) return "";
  if (typeof v === "number") {
    if (v >= 1_000_000) return (v / 1_000_000).toFixed(1) + "M";
    if (v >= 1_000) return (v / 1_000).toFixed(1) + "k";
    return String(v);
  }
  if (v instanceof Date) return v.toISOString().slice(0, 10);
  // ISO date string → trim to date if it parses
  if (typeof v === "string" && /^\d{4}-\d{2}-\d{2}T/.test(v)) {
    return v.slice(0, 10);
  }
  return String(v);
}

/** X-axis tick formatter — never abbreviates ints (a user_id of 1003
 * is a category, not a measure; "1.0k" would be wrong). Only prettifies
 * ISO datetimes / Date objects. */
function fmtX(v: any): string {
  if (v == null) return "";
  if (v instanceof Date) return v.toISOString().slice(0, 10);
  if (typeof v === "string" && /^\d{4}-\d{2}-\d{2}T/.test(v)) {
    return v.slice(0, 10);
  }
  return String(v);
}

function ChartFrame({ title, children, height = 260 }: {
  title: string; children: React.ReactNode; height?: number;
}) {
  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <div style={{
        padding: "10px 14px 4px",
        fontSize: 14, fontWeight: 600, letterSpacing: 0.2,
        color: "var(--fg)",
      }}>
        {title}
      </div>
      <div style={{ flex: 1, minHeight: height, padding: "0 4px 8px" }}>
        {children}
      </div>
    </div>
  );
}

function EmptyState({ title, label }: { title: string; label: string }) {
  return (
    <ChartFrame title={title}>
      <div style={{
        height: "100%", minHeight: 200,
        display: "flex", flexDirection: "column",
        alignItems: "center", justifyContent: "center",
        color: "var(--muted)", fontSize: 14, gap: 6,
      }}>
        <div style={{
          width: 36, height: 36, borderRadius: 8,
          background: "var(--panel-2)",
          display: "flex", alignItems: "center", justifyContent: "center",
          fontSize: 18, opacity: 0.5,
        }}>∅</div>
        {label}
      </div>
    </ChartFrame>
  );
}

const tooltipStyle: React.CSSProperties = {
  background: "var(--panel-2)",
  border: "1px solid var(--border)",
  borderRadius: 6,
  fontSize: 14,
  color: "var(--fg)",
  padding: "6px 10px",
};

const axisProps = { tick: { fontSize: 13, fill: "var(--muted)" }, stroke: "var(--border)" };

const tableHeaderStyle: React.CSSProperties = {
  textAlign: "left",
  borderBottom: "1px solid var(--border)",
  padding: "6px 8px",
  color: "var(--muted)",
  fontWeight: 500,
  fontSize: 13,
  letterSpacing: 0.2,
};

const tableCellStyle: React.CSSProperties = {
  padding: "6px 8px",
  borderBottom: "1px solid var(--border)",
  color: "var(--fg)",
};

export function DashboardChart({ data, onDrill }: Props) {
  const t = useT();
  const spec = data.chart_spec;
  const x = spec.x || data.columns[0];
  const y = spec.y || data.columns[data.columns.length - 1];
  // `objs` carries the localized display values for rendering; `rawObjs`
  // preserves the original FK / null values so drill callbacks hand back
  // the right value to the backend SQL.
  const objs = rowsToLabeledObjs(data, x, t("dashboards.chart.empty_value"), t);
  const rawObjs = rowsToObjs(data);

  if (objs.length === 0) {
    return <EmptyState title={spec.title} label={t("dashboards.chart.no_data")} />;
  }

  const drillTo = (datum: any) => {
    if (!onDrill || datum == null) return;
    // Prefer the raw row by index when present (set by rowsToLabeledObjs);
    // otherwise fall back to the datum itself (e.g. table chart paths).
    const idx = datum?.__raw_idx;
    const source = (typeof idx === "number" && rawObjs[idx]) ? rawObjs[idx] : datum;
    const dimVal = source[x];
    if (dimVal === undefined) return;
    onDrill({ [x]: dimVal });
  };

  const gridStroke = cssVar("--border", "");
  // 单系列柱/折线用 info 蓝(不跟全局青绿/紫)
  const accent = cssVar("--info", "");

  switch (spec.type) {
    case "metric": {
      // Use rawObjs (not objs) so a NULL aggregate doesn't become the
      // "未填写" placeholder text — SUM/COUNT over zero rows is 0, not
      // "missing". Falsy zero is still rendered as "0".
      const rawVal = rawObjs[0]?.[y];
      const value = rawVal == null ? 0 : rawVal;
      return (
        <div style={{
          padding: "12px 14px",
          height: "100%",
          display: "flex", flexDirection: "column",
        }}>
          <div style={{
            fontSize: 13, color: "var(--muted)",
            letterSpacing: 0.2,
            marginBottom: 4,
          }}>
            {spec.title}
          </div>
          <div
            onClick={() => onDrill && onDrill({})}
            title={onDrill ? t("dashboards.chart.click_to_drill") : undefined}
            style={{
              fontSize: 30, fontWeight: 700, lineHeight: 1.1,
              color: "var(--fg)",
              cursor: onDrill ? "pointer" : "default",
              fontVariantNumeric: "tabular-nums",
            }}
          >
            {fmt(value)}
          </div>
          {data.scope_resolved && (
            <div style={{
              marginTop: "auto", paddingTop: 4,
              fontSize: 13, color: "var(--muted)", opacity: 0.6,
            }}>
              scope: {data.scope_resolved}
            </div>
          )}
        </div>
      );
    }

    case "table":
      return (
        <ChartFrame title={spec.title}>
          <div style={{ height: "100%", overflow: "auto", padding: "0 14px 8px" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 14 }}>
              <thead>
                <tr>
                  {data.columns.map((c) => (
                    <th key={c}
                        style={{
                          textAlign: "left",
                          borderBottom: "1px solid var(--border)",
                          padding: "6px 8px",
                          color: "var(--muted)",
                          fontWeight: 500,
                          fontSize: 13, letterSpacing: 0.2,
                        }}>{colLabel(t, c)}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {objs.map((o, i) => (
                  <tr key={i}
                      onClick={() => drillTo(o)}
                      style={{ cursor: onDrill ? "pointer" : "default" }}>
                    {data.columns.map((c) => (
                      <td key={c}
                          style={{
                            padding: "6px 8px",
                            borderBottom: "1px solid var(--border)",
                            color: "var(--fg)",
                          }}>
                        {fmt(o[c])}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </ChartFrame>
      );

    case "line":
      return (
        <ChartFrame title={spec.title}>
          <ResponsiveContainer>
            <LineChart data={objs} margin={{ top: 6, right: 18, left: 4, bottom: 6 }}
                       onClick={(s: any) => drillTo(s?.activePayload?.[0]?.payload)}>
              <CartesianGrid strokeDasharray="3 3" stroke={gridStroke} />
              <XAxis dataKey={x} {...axisProps} tickFormatter={fmtX} />
              <YAxis {...axisProps} tickFormatter={fmt} width={48} />
              <Tooltip contentStyle={tooltipStyle}
                       cursor={{ stroke: accent, strokeOpacity: 0.3 }}
                       labelFormatter={(v: any) => `${colLabel(t, x)}: ${fmtX(v)}`}
                       formatter={(v: any, n: any) => [fmt(v), colLabel(t, String(n))]}/>
              <Line type="monotone" dataKey={y} name={colLabel(t, y)}
                    stroke={accent} strokeWidth={2}
                    dot={{ r: 3, fill: accent }}
                    activeDot={{ r: 6, onClick: (_e: any, data: any) =>
                      drillTo(data?.payload) }}/>
            </LineChart>
          </ResponsiveContainer>
        </ChartFrame>
      );

    case "bar":
    case "stacked_bar":
      return (
        <ChartFrame title={spec.title}>
          <ResponsiveContainer>
            <BarChart data={objs} margin={{ top: 6, right: 18, left: 4, bottom: 6 }}>
              <CartesianGrid strokeDasharray="3 3" stroke={gridStroke} />
              <XAxis dataKey={x} {...axisProps} tickFormatter={fmtX} />
              <YAxis {...axisProps} tickFormatter={fmt} width={56} />
              <Tooltip contentStyle={tooltipStyle}
                       cursor={{ fill: "var(--panel-2)", opacity: 0.4 }}
                       labelFormatter={(v: any) => `${colLabel(t, x)}: ${fmtX(v)}`}
                       formatter={(v: any, n: any) => [fmt(v), colLabel(t, String(n))]}/>
              <Bar dataKey={y} name={colLabel(t, y)}
                   radius={[4, 4, 0, 0]}
                   onClick={(d: any) => drillTo(d?.payload)}
                   stackId={spec.type === "stacked_bar" ? "a" : undefined}>
                {objs.map((_, i) => (
                  <Cell key={i}
                        fill={spec.type === "stacked_bar"
                          ? PALETTE[i % PALETTE.length]
                          : accent}/>
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </ChartFrame>
      );

    case "horizontal_bar":
      // Same data as bar, but swap the axes — category on Y, value on X.
      // Useful when category labels are long (e.g. customer / deal names)
      // and would overlap on a horizontal X-axis.
      return (
        <ChartFrame title={spec.title}>
          <ResponsiveContainer>
            <BarChart data={objs} layout="vertical"
                      margin={{ top: 6, right: 18, left: 4, bottom: 6 }}>
              <CartesianGrid strokeDasharray="3 3" stroke={gridStroke} />
              <XAxis type="number" {...axisProps} tickFormatter={fmt} />
              <YAxis type="category" dataKey={x} {...axisProps}
                     tickFormatter={fmtX} width={110} />
              <Tooltip contentStyle={tooltipStyle}
                       cursor={{ fill: "var(--panel-2)", opacity: 0.4 }}
                       labelFormatter={(v: any) => `${colLabel(t, x)}: ${fmtX(v)}`}
                       formatter={(v: any, n: any) => [fmt(v), colLabel(t, String(n))]}/>
              <Bar dataKey={y} name={colLabel(t, y)}
                   radius={[0, 4, 4, 0]}
                   onClick={(d: any) => drillTo(d?.payload)}>
                {objs.map((_, i) => (
                  <Cell key={i} fill={accent}/>
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </ChartFrame>
      );

    case "composite": {
      // Combo bar + line on the same X axis. Bar follows `y`, the line
      // follows `y2`. The line uses a contrasting palette entry so it
      // reads cleanly against the bar fill.
      const y2 = spec.y2 || data.columns[data.columns.length - 1];
      const lineColor = PALETTE[1] || accent;
      return (
        <ChartFrame title={spec.title}>
          <ResponsiveContainer>
            <ComposedChart data={objs}
                           margin={{ top: 6, right: 18, left: 4, bottom: 6 }}>
              <CartesianGrid strokeDasharray="3 3" stroke={gridStroke} />
              <XAxis dataKey={x} {...axisProps} tickFormatter={fmtX} />
              <YAxis yAxisId="l" {...axisProps} tickFormatter={fmt} width={56} />
              <YAxis yAxisId="r" orientation="right" {...axisProps}
                     tickFormatter={fmt} width={56} />
              <Tooltip contentStyle={tooltipStyle}
                       cursor={{ fill: "var(--panel-2)", opacity: 0.4 }}
                       labelFormatter={(v: any) => `${colLabel(t, x)}: ${fmtX(v)}`}
                       formatter={(v: any, n: any) => [fmt(v), colLabel(t, String(n))]}/>
              <Legend wrapperStyle={{ fontSize: 13, color: "var(--muted)" }}/>
              <Bar yAxisId="l" dataKey={y} name={colLabel(t, y)}
                   radius={[4, 4, 0, 0]} fill={accent}
                   onClick={(d: any) => drillTo(d?.payload)} />
              {y2 && y2 !== y && (
                <Line yAxisId="r" type="monotone" dataKey={y2}
                      name={colLabel(t, y2)} stroke={lineColor}
                      strokeWidth={2} dot={{ r: 3 }}
                      activeDot={{ r: 5,
                        onClick: (_e: any, p: any) => drillTo(p?.payload) }}/>
              )}
            </ComposedChart>
          </ResponsiveContainer>
        </ChartFrame>
      );
    }

    case "text":
      // Static rich-text widget — no data query at all. The "data" frame
      // returned by the server carries an empty rows array and the markdown
      // body in chart_spec.text. We render it as preformatted text so
      // line breaks survive without pulling in a markdown library.
      return (
        <ChartFrame title={spec.title}>
          <div style={{
            padding: "0 14px 12px", height: "100%", overflow: "auto",
            fontSize: 14, lineHeight: 1.55, color: "var(--fg)",
            whiteSpace: "pre-wrap", wordBreak: "break-word",
          }}>
            {spec.text || ""}
          </div>
        </ChartFrame>
      );

    case "report_table": {
      // Pivot: rows = primary x dim, columns = y2 (secondary dim values
      // become column headers), cells = y measure. Falls back to the
      // plain `table` rendering when y2 is absent or no pivot columns
      // are detected in the result. The backend returns rows with
      // x_col / y2_col / y_col so the FE can fold them.
      const y2 = spec.y2;
      if (!y2 || !data.columns.includes(y2)) {
        // Render as flat table — same as the `table` case below.
        return (
          <ChartFrame title={spec.title}>
            <div style={{ height: "100%", overflow: "auto",
                          padding: "0 14px 8px" }}>
              <table style={{ width: "100%", borderCollapse: "collapse",
                              fontSize: 14 }}>
                <thead>
                  <tr>
                    {data.columns.map((c) => (
                      <th key={c} style={tableHeaderStyle}>
                        {colLabel(t, c)}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {objs.map((o, i) => (
                    <tr key={i}
                        onClick={() => drillTo(o)}
                        style={{ cursor: onDrill ? "pointer" : "default" }}>
                      {data.columns.map((c) => (
                        <td key={c} style={tableCellStyle}>{fmt(o[c])}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </ChartFrame>
        );
      }
      const colKeys = Array.from(new Set(
        objs.map((o) => String(o[y2])).filter((v) => v !== "undefined"),
      ));
      const rowKeys = Array.from(new Set(
        objs.map((o) => String(o[x])).filter((v) => v !== "undefined"),
      ));
      // pivot[row][col] → measure value.
      const pivot: Record<string, Record<string, any>> = {};
      for (const o of objs) {
        const r = String(o[x]);
        const c2 = String(o[y2]);
        if (!pivot[r]) pivot[r] = {};
        pivot[r][c2] = o[y];
      }
      return (
        <ChartFrame title={spec.title}>
          <div style={{ height: "100%", overflow: "auto",
                        padding: "0 14px 8px" }}>
            <table style={{ width: "100%", borderCollapse: "collapse",
                            fontSize: 14 }}>
              <thead>
                <tr>
                  <th style={tableHeaderStyle}>{colLabel(t, x)}</th>
                  {colKeys.map((c2) => (
                    <th key={c2} style={tableHeaderStyle}>
                      {valueLabel(t as any, y2, c2)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rowKeys.map((rk) => (
                  <tr key={rk}>
                    <td style={tableCellStyle}>
                      {valueLabel(t as any, x, rk)}
                    </td>
                    {colKeys.map((c2) => (
                      <td key={c2} style={tableCellStyle}>
                        {fmt(pivot[rk]?.[c2])}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </ChartFrame>
      );
    }

    case "pie":
    case "donut":
      return (
        <ChartFrame title={spec.title}>
          <ResponsiveContainer>
            <PieChart>
              <Pie data={objs} dataKey={y} nameKey={x}
                   outerRadius="78%"
                   innerRadius={spec.type === "donut" ? "48%" : 0}
                   paddingAngle={2}
                   onClick={(d: any) => drillTo(d?.payload)}>
                {objs.map((_, i) => (
                  <Cell key={i} fill={PALETTE[i % PALETTE.length]}
                        stroke="var(--panel)" strokeWidth={2}/>
                ))}
              </Pie>
              <Tooltip contentStyle={tooltipStyle} formatter={(v: any) => fmt(v)}/>
              <Legend wrapperStyle={{ fontSize: 13, color: "var(--muted)" }}/>
            </PieChart>
          </ResponsiveContainer>
        </ChartFrame>
      );

    case "funnel": {
      // Custom SVG funnel — recharts FunnelChart fails to render trapezoids
      // in this environment (the inner <g class="recharts-shape"> stays empty
      // even when the container has proper dimensions). Drawing it ourselves
      // is deterministic and ~free.
      const y2 = spec.y2;
      // Row order is authoritative: the backend pre-sorts funnel rows
      // by the configured picklist option order (Schema Hub field
      // option order, stored in ``module_fields.options`` CSV). When
      // there's no picklist metadata for the x dim, the DSL's
      // ``order_by`` clause (typically count desc) still produces a
      // sensible biggest-at-top layout. Either way we trust the
      // incoming order — a front-end re-sort would mask backend
      // ordering bugs and override the admin's chosen stage sequence.
      const sortedIdx = objs.map((_, i) => i);
      const sorted = sortedIdx.map(i => objs[i]);
      const sortedRaw = sortedIdx.map(i => rawObjs[i] ?? objs[i]);
      const maxN = Math.max(...sorted.map(o => Number(o[y]) || 0), 1);
      const stepH = 44;
      const padX = 60;
      const padTop = 12;
      const viewW = 600;
      const viewH = padTop + stepH * sorted.length + 8;
      const fmtAmt = (a: number) => a >= 10000
        ? `¥${(a / 10000).toFixed(a >= 100000 ? 0 : 1)}万`
        : a > 0 ? `¥${Math.round(a)}` : "—";
      return (
        <ChartFrame title={spec.title}>
          <div style={{ width: "100%", height: "100%", padding: "4px 8px",
                        display: "flex", alignItems: "center",
                        justifyContent: "center" }}>
            <svg viewBox={`0 0 ${viewW} ${viewH}`} width="100%"
                 style={{ maxHeight: viewH }} preserveAspectRatio="xMidYMid meet">
              {sorted.map((row, i) => {
                const n = Number(row[y]) || 0;
                const nextN = Number(sorted[i + 1]?.[y]) || 0;
                const wTop = (n / maxN) * (viewW - padX * 2);
                const wBot = i === sorted.length - 1
                  ? wTop * 0.6
                  : (nextN / maxN) * (viewW - padX * 2);
                const yTop = padTop + i * stepH;
                const yBot = yTop + stepH - 2;
                const cx = viewW / 2;
                const pts = [
                  [cx - wTop / 2, yTop], [cx + wTop / 2, yTop],
                  [cx + wBot / 2, yBot], [cx - wBot / 2, yBot],
                ].map(p => p.join(",")).join(" ");
                const stage = String(row[x] ?? "");
                const cy = yTop + stepH / 2;
                const innerLabel = y2
                  ? `${n} 单 · ${fmtAmt(Number(row[y2]) || 0)}`
                  : `${n}`;
                return (
                  <g key={i} style={{ cursor: onDrill ? "pointer" : "default" }}
                     onClick={() => drillTo(sortedRaw[i] ?? row)}>
                    <polygon points={pts}
                             fill={PALETTE[i % PALETTE.length]}
                             stroke="var(--panel)" strokeWidth={1.5}/>
                    <text x={cx} y={cy} textAnchor="middle"
                          dominantBaseline="middle"
                          fill="var(--fg-on-accent)" fontSize={15} fontWeight={700}
                          stroke="rgba(0,0,0,0.55)" strokeWidth={0.6} /* theme-guard:ignore */
                          paintOrder="stroke fill"
                          style={{ pointerEvents: "none" }}>{innerLabel}</text>
                    <text x={cx + wTop / 2 + 10} y={cy}
                          textAnchor="start" dominantBaseline="middle"
                          fill="var(--fg)" fontSize={14}
                          style={{ pointerEvents: "none" }}>{stage}</text>
                  </g>
                );
              })}
            </svg>
          </div>
        </ChartFrame>
      );
    }

    default:
      return (
        <ChartFrame title={spec.title}>
          <div style={{ padding: 16, color: "var(--danger)", fontSize: 14 }}>
            unsupported chart type: {String((spec as any).type)}
          </div>
        </ChartFrame>
      );
  }
}

export default DashboardChart;
