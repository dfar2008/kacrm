/**
 * Dashboards page — AI-time dashboard surface.
 * Spec: docs/superpowers/specs/2026-05-11-ai-dashboard-design.md
 *
 * Design language: matches the rest of hermes-cloud — uses theme CSS vars
 * (--bg / --panel / --panel-2 / --fg / --muted / --accent / --border).
 * Avoid hardcoded colors so the page reads cleanly in dark + light mode.
 */
import React, { useEffect, useRef, useState } from "react";

import {
  createDashboardTab,
  deleteChart,
  deleteDashboardTab,
  fetchChartData,
  getDashboardTab,
  listDashboardTabs,
  patchChart,
  patchDashboardTab,
  refreshChart,
  type ChartData,
  type DashboardChart as DChart,
  type DashboardTab,
} from "@/lib/api";
import { ChatPanel } from "@/components/dashboards/ChatPanel";
import { DashboardChart } from "@/components/dashboards/DashboardChart";
import { DrillModal } from "@/components/dashboards/DrillModal";
import { useT } from "@/lib/i18n";

const cardBtnStyle: React.CSSProperties = {
  fontSize: 13, padding: "3px 6px", minWidth: 22, lineHeight: 1,
  border: "1px solid var(--border)", borderRadius: 4,
  background: "var(--panel-2)", color: "var(--muted)",
  cursor: "pointer",
};

export function DashboardsPage() {
  const t = useT();
  const [tabs, setTabs] = useState<DashboardTab[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [charts, setCharts] = useState<DChart[]>([]);
  const [chartData, setChartData] = useState<Record<string, ChartData | { error: string }>>({});
  const [scope, setScope] = useState<string>("");
  const [chatOpen, setChatOpen] = useState<boolean>(() =>
    typeof window !== "undefined" &&
    window.localStorage.getItem("dashboard.chat_collapsed") !== "true"
  );
  const [drillTarget, setDrillTarget] = useState<{
    chartId: string;
    filters: Record<string, any>;
    columnLabels?: Record<string, Record<string, string>>;
  } | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listDashboardTabs()
      .then((r) => {
        setTabs(r.tabs);
        if (r.tabs.length && !activeId) setActiveId(r.tabs[0].id);
      })
      .catch((e) => setError(e.message || String(e)));
  }, []);

  useEffect(() => {
    if (!activeId) return;
    setChartData({});
    getDashboardTab(activeId)
      .then((r) => {
        setCharts(r.charts);
        r.charts.forEach((c) => {
          fetchChartData(c.id, scope || undefined)
            .then((d) => setChartData((p) => ({ ...p, [c.id]: d })))
            .catch((e) =>
              setChartData((p) => ({ ...p, [c.id]: { error: e.message || "load failed" } })),
            );
        });
      })
      .catch((e) => setError(e.message || String(e)));
  }, [activeId, scope]);

  const toggleChat = () => {
    setChatOpen((open) => {
      window.localStorage.setItem("dashboard.chat_collapsed", open ? "true" : "false");
      return !open;
    });
  };

  const newTab = async () => {
    const title = window.prompt(t("dashboards.tab.new_prompt"));
    if (!title) return;
    try {
      const tab = await createDashboardTab(title);
      setTabs((prev) => [...prev, tab]);
      setActiveId(tab.id);
    } catch (e: any) {
      alert(`${t("dashboards.tab.create_failed")}: ${e.message}`);
    }
  };

  const removeTab = async (id: string) => {
    const tab = tabs.find((x) => x.id === id);
    if (!tab) return;
    if (!window.confirm(t("dashboards.tab.confirm_delete").replace("{title}", tab.title))) {
      return;
    }
    const snapshot = tabs;
    setTabs((prev) => prev.filter((x) => x.id !== id));
    if (activeId === id) {
      const remaining = snapshot.filter((x) => x.id !== id);
      setActiveId(remaining[0]?.id ?? null);
    }
    try {
      await deleteDashboardTab(id);
    } catch (e: any) {
      // Revert on failure so the user doesn't lose their state.
      setTabs(snapshot);
      alert(`${t("dashboards.tab.delete_failed")}: ${e.message || e}`);
    }
  };

  // Drag-and-drop reorder. We track which tab is being dragged in a
  // closure-local variable (not state) so a fast drag doesn't trigger
  // an extra render between events. On drop the affected slice is
  // re-numbered and PATCHed back to the server one-by-one.
  const dragIdRef = useRef<string | null>(null);
  const onTabDragStart = (id: string) => (e: React.DragEvent) => {
    dragIdRef.current = id;
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", id);
  };
  const onTabDragOver = (e: React.DragEvent) => {
    if (dragIdRef.current) {
      e.preventDefault();
      e.dataTransfer.dropEffect = "move";
    }
  };
  const onTabDrop = (targetId: string) => async (e: React.DragEvent) => {
    e.preventDefault();
    const src = dragIdRef.current;
    dragIdRef.current = null;
    if (!src || src === targetId) return;
    const cur = [...tabs];
    const srcIdx = cur.findIndex((x) => x.id === src);
    const dstIdx = cur.findIndex((x) => x.id === targetId);
    if (srcIdx < 0 || dstIdx < 0) return;
    const [moved] = cur.splice(srcIdx, 1);
    cur.splice(dstIdx, 0, moved);
    const reorderedSnapshot = cur;
    const before = tabs;
    setTabs(reorderedSnapshot.map((tab, i) => ({ ...tab, position: i })));
    // Persist new positions. Patch only the tabs whose position changed
    // to minimise round-trips.
    try {
      await Promise.all(
        reorderedSnapshot
          .map((tab, i) => ({ tab, i }))
          .filter(({ tab, i }) => before.find((b) => b.id === tab.id)?.position !== i)
          .map(({ tab, i }) => patchDashboardTab(tab.id, { position: i })),
      );
    } catch {
      // Revert on failure.
      setTabs(before);
    }
  };

  const reloadCurrentTab = () => {
    if (!activeId) return;
    setChartData({});
    getDashboardTab(activeId).then((r) => {
      setCharts(r.charts);
      r.charts.forEach((c) => {
        fetchChartData(c.id, scope || undefined)
          .then((d) => setChartData((p) => ({ ...p, [c.id]: d })))
          .catch((e) =>
            setChartData((p) => ({ ...p, [c.id]: { error: e.message || "load failed" } })),
          );
      });
    });
  };

  const refreshOne = (id: string) => {
    setChartData((p) => {
      const copy = { ...p }; delete copy[id]; return copy;
    });
    refreshChart(id, scope || undefined)
      .then((d) => setChartData((p) => ({ ...p, [id]: d })))
      .catch((e) =>
        setChartData((p) => ({ ...p, [id]: { error: e.message || "load failed" } })),
      );
  };

  const renameActiveTab = async () => {
    const tab = tabs.find((x) => x.id === activeId);
    if (!tab) return;
    const title = window.prompt(t("dashboards.tab.rename_prompt"), tab.title);
    if (!title || title === tab.title) return;
    try {
      await patchDashboardTab(tab.id, { title } as any);
      setTabs((prev) => prev.map((x) => (x.id === tab.id ? { ...x, title } : x)));
    } catch (e: any) {
      alert(`${t("dashboards.tab.rename_failed")}: ${e.message}`);
    }
  };

  if (error) {
    return (
      <div style={{ padding: 24, color: "var(--danger)" }}>
        {t("dashboards.load_failed")}: {error}
      </div>
    );
  }

  return (
    <div style={{
      display: "flex", height: "100%", overflow: "hidden",
      background: "var(--bg)", color: "var(--fg)",
    }}>
      <div style={{ flex: 1, overflow: "auto" }}>
        {/* sticky tab bar */}
        <div style={{
          position: "sticky", top: 0, zIndex: 10,
          background: "var(--bg)",
          borderBottom: "1px solid var(--border)",
          padding: "10px 20px",
          display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap",
        }}>
          {tabs.map((tab) => {
            const active = tab.id === activeId;
            return (
              <div
                key={tab.id}
                draggable
                onDragStart={onTabDragStart(tab.id)}
                onDragOver={onTabDragOver}
                onDrop={onTabDrop(tab.id)}
                onClick={() => setActiveId(tab.id)}
                onDoubleClick={() => active && renameActiveTab()}
                title={!!tab.preset_key ? t("dashboards.tab.system_tip") : t("dashboards.tab.user_tip")}
                style={{
                  display: "inline-flex", alignItems: "center", gap: 4,
                  padding: "6px 10px 6px 14px",
                  background: active ? "var(--panel-2)" : "transparent",
                  color: active ? "var(--fg)" : "var(--muted)",
                  border: "1px solid",
                  borderColor: active ? "var(--accent)" : "transparent",
                  borderRadius: 999,
                  cursor: "grab",
                  fontSize: 14,
                  fontWeight: active ? 600 : 400,
                  transition: "all .12s ease",
                  userSelect: "none",
                }}
              >
                <span>{tab.title}</span>
                {/* Delete is only offered on user-owned tabs. Preset
                    tabs (is_system_preset=true) are part of the
                    default set — deleting them would just leave the
                    user staring at an empty role view until materialize
                    re-creates them, which is confusing. */}
                {active && !!!tab.preset_key && (
                  <button
                    onClick={(e) => { e.stopPropagation(); removeTab(tab.id); }}
                    title={t("dashboards.tab.delete")}
                    style={{
                      marginLeft: 2, padding: "0 4px",
                      background: "transparent", border: "none",
                      color: "var(--muted)", cursor: "pointer",
                      fontSize: 14, lineHeight: 1,
                    }}
                  >×</button>
                )}
              </div>
            );
          })}
          <button onClick={newTab}
                  title={t("dashboards.tab.new_prompt")}
                  style={{
                    padding: "6px 12px",
                    border: "1px dashed var(--border)",
                    background: "transparent", color: "var(--muted)",
                    borderRadius: 999, cursor: "pointer", fontSize: 14,
                  }}>
            {t("dashboards.tab.new")}
          </button>
          <div style={{ flex: 1 }} />
          <label style={{
            display: "flex", alignItems: "center", gap: 6,
            fontSize: 14, color: "var(--muted)", margin: 0,
          }}>
            {t("dashboards.scope.label")}
            <select value={scope}
                    onChange={(e) => setScope(e.target.value)}
                    style={{
                      padding: "5px 8px", fontSize: 14,
                      background: "var(--panel)", color: "var(--fg)",
                      border: "1px solid var(--border)", borderRadius: 6,
                    }}>
              <option value="">{t("dashboards.scope.default")}</option>
              <option value="self">self</option>
              <option value="team">team</option>
              <option value="org">org</option>
            </select>
          </label>
          <button onClick={reloadCurrentTab} title={t("dashboards.refresh.all")}
                  style={{
                    padding: "5px 10px", fontSize: 14,
                    background: "var(--panel)", color: "var(--fg)",
                    border: "1px solid var(--border)", borderRadius: 6,
                    cursor: "pointer",
                  }}>⟳</button>
          {!chatOpen && (
            <button onClick={toggleChat}
                    style={{
                      padding: "5px 12px", fontSize: 14, fontWeight: 500,
                      background: "var(--accent)", color: "var(--bg)",
                      border: "none", borderRadius: 6,
                      cursor: "pointer",
                    }}>
              {t("dashboards.chat.toggle")}
            </button>
          )}
        </div>

        <div style={{ padding: 20 }}>
          {charts.length === 0 && (
            <div style={{
              padding: "48px 24px", textAlign: "center",
              color: "var(--muted)", fontSize: 14,
            }}>
              <div style={{ fontSize: 36, marginBottom: 12 }}>✨</div>
              {t("dashboards.empty.no_charts")}
              {chatOpen
                ? t("dashboards.empty.no_charts_open_chat")
                : t("dashboards.empty.no_charts_closed_chat")}
            </div>
          )}
          <div style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))",
            gridAutoRows: "130px",
            gap: 14,
          }}>
            {charts.map((c) => {
              const d = chartData[c.id];
              const errored = d && (d as any).error;
              const ctype = (d as ChartData | undefined)?.chart_spec?.type;
              // Fall back to a type-driven default if the row doesn't
              // carry a saved grid size yet (e.g. preset rows materialized
              // before this column was added).
              const defaultW =
                ctype === "metric" ? 1 :
                ctype === "text" ? 2 : 2;
              const defaultH =
                ctype === "metric" ? 1 :
                ctype === "text" ? 1 : 2;
              const gw = Math.max(1, Math.min(4, c.grid_w ?? defaultW));
              const gh = Math.max(1, Math.min(4, c.grid_h ?? defaultH));
              const startResize = (axis: "x" | "y" | "both") =>
                (e: React.PointerEvent) => {
                e.preventDefault();
                e.stopPropagation();
                const card = (e.currentTarget as HTMLElement).closest(
                  "[data-chart-card]",
                ) as HTMLElement | null;
                if (!card) return;
                // A single grid cell is the card's current pixel size
                // divided by its current grid span. We sample at drag
                // start and don't recompute mid-drag — fine for the
                // ~100 ms gestures this widget handles.
                const cellW = card.offsetWidth / gw;
                const cellH = 130 + 14;  // gridAutoRows + gap
                const startX = e.clientX, startY = e.clientY;
                const startGW = gw, startGH = gh;
                let lastW = gw, lastH = gh;
                const onMove = (ev: PointerEvent) => {
                  const dw = axis === "y" ? 0
                    : Math.round((ev.clientX - startX) / cellW);
                  const dh = axis === "x" ? 0
                    : Math.round((ev.clientY - startY) / cellH);
                  const nw = Math.max(1, Math.min(4, startGW + dw));
                  const nh = Math.max(1, Math.min(4, startGH + dh));
                  if (nw === lastW && nh === lastH) return;
                  lastW = nw; lastH = nh;
                  setCharts((cs) => cs.map(
                    (x) => x.id === c.id
                      ? { ...x, grid_w: nw, grid_h: nh }
                      : x,
                  ));
                };
                const onUp = async () => {
                  window.removeEventListener("pointermove", onMove);
                  window.removeEventListener("pointerup", onUp);
                  document.body.style.userSelect = "";
                  if (lastW === startGW && lastH === startGH) return;
                  try {
                    await patchChart(c.id, {
                      grid_w: lastW, grid_h: lastH,
                    });
                  } catch {
                    setCharts((cs) => cs.map(
                      (x) => x.id === c.id
                        ? { ...x, grid_w: startGW, grid_h: startGH }
                        : x,
                    ));
                  }
                };
                document.body.style.userSelect = "none";
                window.addEventListener("pointermove", onMove);
                window.addEventListener("pointerup", onUp);
              };
              const removeChart = async () => {
                if (!window.confirm(t("dashboards.chart.confirm_delete"))) return;
                const snapshot = charts;
                setCharts((cs) => cs.filter((x) => x.id !== c.id));
                try {
                  await deleteChart(c.id);
                } catch {
                  setCharts(snapshot);
                }
              };
              return (
                <div key={c.id} data-chart-card style={{
                  position: "relative",
                  background: "var(--panel)",
                  border: "1px solid var(--border)",
                  borderRadius: "var(--radius)",
                  overflow: "hidden",
                  gridColumn: `span ${gw}`,
                  gridRow: `span ${gh}`,
                  transition: "border-color .12s ease",
                }}
                onMouseEnter={(e) => {
                  (e.currentTarget as HTMLDivElement).style.borderColor = "var(--accent)";
                }}
                onMouseLeave={(e) => {
                  (e.currentTarget as HTMLDivElement).style.borderColor = "var(--border)";
                }}
                >
                  {d == null && (
                    <div style={{
                      padding: 24, color: "var(--muted)", fontSize: 14,
                      display: "flex", alignItems: "center", gap: 8,
                    }}>
                      <span style={{
                        width: 10, height: 10, borderRadius: "50%",
                        background: "var(--muted)",
                        animation: "dashboardsPulse 1.4s ease-in-out infinite",
                      }}/>
                      {c.title}…
                    </div>
                  )}
                  {errored && (
                    <div style={{ padding: 16 }}>
                      <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 4,
                                     color: "var(--fg)" }}>{c.title}</div>
                      <div style={{ color: "var(--danger)", fontSize: 14 }}>
                        {(d as any).error}
                      </div>
                    </div>
                  )}
                  {d && !errored && (
                    <DashboardChart
                      data={d as ChartData}
                      onDrill={(filters) =>
                        setDrillTarget({
                          chartId: c.id,
                          filters,
                          columnLabels: (d as ChartData).column_labels,
                        })
                      }
                    />
                  )}
                  {/* Card action bar — refresh + delete. Resize is a
                      drag-handle on the bottom-right corner (see below). */}
                  <div style={{
                    position: "absolute", top: 8, right: 8,
                    display: "flex", gap: 4, opacity: 0.7,
                  }}>
                    <button onClick={() => refreshOne(c.id)}
                            title={t("dashboards.refresh.one")}
                            style={cardBtnStyle}>⟳</button>
                    <button onClick={removeChart}
                            title={t("dashboards.chart.delete")}
                            style={{ ...cardBtnStyle, color: "var(--danger)" }}>
                      ×
                    </button>
                  </div>
                  {/* Three drag-to-resize zones on the card edges:
                      right edge → width only, bottom edge → height
                      only, bottom-right corner → both. Each is a thin
                      transparent strip; the cursor is the only visible
                      affordance until hover. The corner is drawn with
                      stacked diagonal stripes as a hint. Pointer-events
                      stay confined to these zones so clicks on the
                      chart still drill through. */}
                  <div onPointerDown={startResize("x")}
                       title={t("dashboards.chart.resize_w")}
                       style={{
                         position: "absolute", top: 0, right: 0,
                         width: 6, height: "100%",
                         cursor: "ew-resize",
                       }}/>
                  <div onPointerDown={startResize("y")}
                       title={t("dashboards.chart.resize_h")}
                       style={{
                         position: "absolute", bottom: 0, left: 0,
                         height: 6, width: "100%",
                         cursor: "ns-resize",
                       }}/>
                  <div onPointerDown={startResize("both")}
                       title={t("dashboards.chart.resize")}
                       style={{
                         position: "absolute", right: 0, bottom: 0,
                         width: 16, height: 16,
                         cursor: "nwse-resize",
                         opacity: 0.55,
                         background: "linear-gradient("
                           + "135deg, transparent 0 55%, "
                           + "var(--muted) 55% 65%, transparent 65% 75%, "
                           + "var(--muted) 75% 85%, transparent 85% 95%, "
                           + "var(--muted) 95% 100%)",
                       }}/>
                </div>
              );
            })}
          </div>
        </div>
      </div>

      {chatOpen && activeId && (
        <ChatPanel tabId={activeId}
                   onClose={toggleChat}
                   onApplied={reloadCurrentTab} />
      )}

      {drillTarget && (
        <DrillModal chartId={drillTarget.chartId}
                    dimFilters={drillTarget.filters}
                    columnLabels={drillTarget.columnLabels}
                    onClose={() => setDrillTarget(null)} />
      )}

      <style>{`
        @keyframes dashboardsPulse {
          0%, 100% { opacity: 0.35; transform: scale(0.85); }
          50% { opacity: 1; transform: scale(1.1); }
        }
      `}</style>
    </div>
  );
}

export default DashboardsPage;
