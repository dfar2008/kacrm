import { useEffect, useRef, useState } from "react";
import "./crm.css";
import { CrmIcon } from "@/components/CrmIcon";
import { RecordList } from "@/components/RecordList";
import { RecordDetail } from "@/components/RecordDetail";
import { AgentWorkbench } from "@/components/AgentWorkbench";
import { DailyFeed } from "@/components/DailyFeed";
import { listModules, getMe, ApiError } from "@/lib/api";
import { useRouter } from "@/lib/router";
import { Placeholder } from "./Placeholder";
import { useT, type DictKey } from "@/lib/i18n";

// Default modules rendered before ``listModules()`` returns; keys point
// at ``crm.module.<name>`` entries in the i18n dict so tab labels are
// localized even during the initial fetch.
const DEFAULT_MODULES: Array<{ name: string; labelKey: DictKey }> = [
  { name: "accounts",   labelKey: "crm.module.accounts" },
  { name: "contacts",   labelKey: "crm.module.contacts" },
  { name: "leads",      labelKey: "crm.module.leads" },
  { name: "potentials", labelKey: "crm.module.potentials" },
];

// Lucide-style 14×14 stroke icons per built-in module. Same visual
// language as the "事件流" SVG above so the whole tab strip reads as
// one consistent icon family. Custom modules (admin-added) fall back
// to the generic FOLDER icon — no per-module icon mapping needed.
function ModuleIcon({ name }: { name: string }) {
  const common = {
    width: 18, height: 18, viewBox: "0 0 24 24", fill: "none",
    stroke: "currentColor", strokeWidth: 1.8,
    strokeLinecap: "round" as const, strokeLinejoin: "round" as const,
    "aria-hidden": true,
  };
  switch (name) {
    case "accounts":   // 客户 — building
      return (<svg {...common}><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M9 22v-4h6v4"/><path d="M8 6h.01M16 6h.01M12 6h.01M12 10h.01M12 14h.01M16 10h.01M16 14h.01M8 10h.01M8 14h.01"/></svg>);
    case "contacts":   // 联系人 — user
      return (<svg {...common}><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>);
    case "leads":      // 线索 — target
      return (<svg {...common}><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/></svg>);
    case "potentials": // 商机 — trending up
      return (<svg {...common}><polyline points="22 7 13.5 15.5 8.5 10.5 2 17"/><polyline points="16 7 22 7 22 13"/></svg>);
    case "quotes":     // 报价 — file-text
      return (<svg {...common}><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="8" y1="13" x2="16" y2="13"/><line x1="8" y1="17" x2="16" y2="17"/></svg>);
    case "salesorders": // 销售订单 — clipboard-check
      return (<svg {...common}><path d="M9 2h6a1 1 0 0 1 1 1v2H8V3a1 1 0 0 1 1-1z"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><polyline points="9 14 11 16 15 12"/></svg>);
    case "tasks":      // 任务 — check-square
      return (<svg {...common}><polyline points="9 11 12 14 22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg>);
    case "products":   // 产品 — package
      return (<svg {...common}><path d="M16.5 9.4 7.55 4.24"/><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><polyline points="3.27 6.96 12 12.01 20.73 6.96"/><line x1="12" y1="22.08" x2="12" y2="12"/></svg>);
    case "receivables": // 应收款 — wallet
      return (<svg {...common}><path d="M21 12V7H5a2 2 0 0 1 0-4h14v4"/><path d="M3 5v14a2 2 0 0 0 2 2h16v-5"/><path d="M18 12a2 2 0 0 0 0 4h4v-4z"/></svg>);
    case "payments":   // 回款 — banknote
      return (<svg {...common}><rect x="2" y="6" width="20" height="12" rx="2"/><circle cx="12" cy="12" r="2"/><path d="M6 12h.01M18 12h.01"/></svg>);
    default:           // generic — folder
      return (<svg {...common}><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>);
  }
}

export function CrmPage({ onUnauth: _onUnauth }: { onUnauth: () => void }) {
  const { path, navigate } = useRouter();
  const t = useT();
  // Compact mode: at narrow viewports the 11+ tab strip can't fit the
  // labels horizontally without clipping. Hide the text and keep only
  // the SVG icon (with title tooltip) so the strip stays one-liner and
  // scannable. Threshold tuned to where the all-text strip starts to
  // overflow on a 1-rail + 3-column CRM layout.
  const [compactTabs, setCompactTabs] = useState(
    typeof window !== "undefined"
      ? window.matchMedia("(max-width: 1100px)").matches
      : false,
  );
  useEffect(() => {
    if (typeof window === "undefined") return;
    const mq = window.matchMedia("(max-width: 1100px)");
    const onChange = (e: MediaQueryListEvent) => setCompactTabs(e.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);
  const [modules, setModules] = useState<Array<{ name: string; label?: string; labelKey?: DictKey }>>(DEFAULT_MODULES);
  // Deep link: /crm[/<module>[/<id>]]. Bare ``/crm`` now lands on the
  // event-stream default page (§④) — the user can click "按模块列表" to
  // switch to the classic 3-column view, or pick a module tab directly.
  const [initModule, initId, initFeed] = (() => {
    const rest = path.startsWith("/crm/") ? path.slice("/crm/".length) : "";
    const [m, idStr] = rest.split("/");
    const id = Number(idStr);
    const feed = !m;  // bare /crm → feed mode
    return [m || "accounts", Number.isFinite(id) && id > 0 ? id : null, feed] as const;
  })();
  const [module, setModule] = useState<string>(initModule);
  const [recordId, setRecordId] = useState<number | null>(initId);
  const [feedMode, setFeedMode] = useState<boolean>(initFeed);

  // Resizable panels (Spec 1.A). Widths are per-module — different
  // modules show different fields so want different list widths. The
  // outer grid's gridTemplateColumns is what we mutate; during a drag we
  // poke the DOM directly (gridRef) and only commit to state + storage on
  // mouse-up, so we don't re-render the (up-to-200-row) list every frame.
  const DEFAULT_LIST_W = 340, DEFAULT_COPILOT_W = 300;
  const MIN_LIST_W = 260, MIN_COPILOT_W = 260;
  const readWidth = (key: string, def: number) => {
    try {
      const v = Number(localStorage.getItem(key));
      return Number.isFinite(v) && v > 0 ? v : def;
    } catch { return def; }
  };
  const gridRef = useRef<HTMLDivElement>(null);
  const [listWidth, setListWidth] = useState(() =>
    readWidth(`kacrm_list_width_${initModule}`, DEFAULT_LIST_W));
  const [copilotWidth, setCopilotWidth] = useState(() =>
    readWidth(`kacrm_copilot_width_${initModule}`, DEFAULT_COPILOT_W));
  useEffect(() => {
    setListWidth(readWidth(`kacrm_list_width_${module}`, DEFAULT_LIST_W));
    setCopilotWidth(readWidth(`kacrm_copilot_width_${module}`, DEFAULT_COPILOT_W));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [module]);
  // Distinguish 401 (auth — most common cause is stale session cookie after
  // cloud restart) from 404 (router not mounted — actual backend init issue).
  // The two cases need different banners and different remedies.
  const [unavailableReason, setUnavailableReason] = useState<null | "session_expired" | "router_missing">(null);
  const [copilotPrefill, setCopilotPrefill] = useState<{ text: string; nonce: number } | null>(null);

  // Charter §定律 5: TodayPriority's "→ 让 AI 处理" button navigates
  // to /crm/<m>/<id>?ask=<text>. We pick that up here on mount /
  // url change, feed it into copilotPrefill, then strip the param
  // from the URL so refresh / back-nav doesn't re-trigger it.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const sp = new URLSearchParams(window.location.search);
    const ask = sp.get("ask");
    if (ask && ask.trim()) {
      setCopilotPrefill({ text: ask, nonce: Date.now() });
      sp.delete("ask");
      const remainder = sp.toString();
      const cleanUrl = window.location.pathname + (remainder ? `?${remainder}` : "");
      window.history.replaceState(null, "", cleanUrl);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [recordId, module]);
  // Bumped whenever Copilot edits a field — RecordDetail re-fetches.
  const [recordRefreshNonce, setRecordRefreshNonce] = useState(0);
  // Per-module admin-curated chip-row overrides (migration 0020).
  // null/undefined = use frontend default. Cache comes from listModules
  // and is mutated after HeadlineCustomizer saves.
  const [headlineMap, setHeadlineMap] = useState<Record<string, string[] | null>>({});
  // Per-module admin-curated list-item field layout (migration 0099).
  const [listFieldsMap, setListFieldsMap] = useState<Record<string, import("@/lib/api").ListFieldsConfig | null>>({});
  const [isAdmin, setIsAdmin] = useState(false);
  const [copilotOpen, setCopilotOpen] = useState(false);

  // Keep URL in sync with selection, so the back button and direct links work.
  useEffect(() => {
    const target = feedMode
      ? "/crm"
      : recordId != null
        ? `/crm/${module}/${recordId}`
        : `/crm/${module}`;
    if (path !== target) navigate(target, true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [module, recordId, feedMode]);

  // React to external path changes (e.g. memory entity → CRM record chip).
  useEffect(() => {
    if (!path.startsWith("/crm")) return;
    const rest = path.startsWith("/crm/") ? path.slice("/crm/".length) : "";
    const [m, idStr] = rest.split("/");
    const nextFeed = !m;
    const nextModule = m || "accounts";
    const idNum = Number(idStr);
    const nextId = Number.isFinite(idNum) && idNum > 0 ? idNum : null;
    if (nextFeed !== feedMode) setFeedMode(nextFeed);
    if (nextModule !== module) setModule(nextModule);
    if (nextId !== recordId) setRecordId(nextId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path]);

  useEffect(() => {
    listModules()
      .then(r => {
        setUnavailableReason(null);
        // Backend may return either the bare list (legacy) or a
        // ``{data: [...]}`` envelope. Tolerate both so a stale client
        // talking to a newer server still renders modules.
        const list = Array.isArray((r as any)) ? (r as any)
                   : Array.isArray(r?.data) ? r.data : [];
        const visible = list.filter((m: any) => m.has_access !== false);
        if (visible.length > 0) setModules(visible);
        // Cache headline_fields per module name for RecordDetail.
        const hm: Record<string, string[] | null> = {};
        const lfm: Record<string, import("@/lib/api").ListFieldsConfig | null> = {};
        for (const m of list) {
          hm[m.name] = Array.isArray(m.headline_fields) ? m.headline_fields : null;
          lfm[m.name] = (m.list_fields && Array.isArray(m.list_fields.row1))
            ? m.list_fields : null;
        }
        setHeadlineMap(hm);
        setListFieldsMap(lfm);
      })
      .catch(e => {
        // 401 = session cookie expired/invalid (most common after cloud
        // restart rotates session_cookie_secret). 404 = /crm router not
        // mounted (CRM DB unreachable / migration incomplete / startup
        // error). Different remedies, different banners.
        if (e instanceof ApiError && e.status === 401) {
          setUnavailableReason("session_expired");
        } else if (e instanceof ApiError && e.status === 404) {
          setUnavailableReason("router_missing");
        }
      });
    // Admin gate for the HeadlineCustomizer ⚙ button.
    getMe().then(u => setIsAdmin(Boolean(u?.is_admin))).catch(() => {});
  }, []);

  if (unavailableReason === "session_expired") {
    return <Placeholder
      title={t("crm.session_expired.title")}
      phase={t("crm.session_expired.phase")}
      description={t("crm.session_expired.desc")}
      action={{ label: t("crm.session_expired.action"), href: "/app/login" }}
    />;
  }
  if (unavailableReason === "router_missing") {
    return <Placeholder
      title={t("crm.unavailable.title")}
      phase={t("crm.unavailable.phase")}
      description={t("crm.unavailable.desc")}
    />;
  }

  // Responsive Copilot column. On wide viewports it stays as a 340px
  // right rail. On narrow viewports we (a) drop the column from the
  // grid so the main content gets the whole width, and (b) surface a
  // floating "Copilot" button that flips it into a slide-in overlay
  // pinned to the right edge. Same pattern Slack / Linear use for
  // their info panels on small screens.
  const showCopilotRail = !compactTabs;             // pinned right column
  const showCopilotOverlay = compactTabs && copilotOpen; // slide-in panel
  const colsWith = (lw: number, cw: number) => showCopilotRail
    ? (feedMode ? `minmax(0, 1fr) clamp(260px, ${cw}px, 25%)` : `clamp(260px, ${lw}px, 30%) minmax(0, 1fr) clamp(260px, ${cw}px, 25%)`)
    : (feedMode ? "minmax(0, 1fr)" : `clamp(260px, ${lw}px, 40%) minmax(0, 1fr)`);
  const cols = colsWith(listWidth, copilotWidth);
  const moduleLabelKey = `crm.module.${module}`;
  const translatedModule = t(moduleLabelKey as DictKey);
  const moduleLabel = translatedModule === moduleLabelKey
    ? modules.find(m => m.name === module)?.label || module
    : translatedModule;

  // Generic horizontal drag. ``edge`` says which side of the panel the
  // handle sits on, so we know the sign of the delta.
  function startResize(
    e: React.MouseEvent, which: "list" | "copilot",
  ) {
    e.preventDefault();
    const startX = e.clientX;
    const startList = listWidth;
    const startCopilot = copilotWidth;
    let nextList = startList;
    let nextCopilot = startCopilot;
    document.body.style.userSelect = "none";
    const onMove = (ev: MouseEvent) => {
      const dx = ev.clientX - startX;
      if (which === "list") {
        nextList = Math.min(480, Math.max(MIN_LIST_W, startList + dx));
      } else {
        // Copilot handle is on its LEFT edge: drag left → wider.
        nextCopilot = Math.min(420, Math.max(MIN_COPILOT_W, startCopilot - dx));
      }
      if (gridRef.current) {
        gridRef.current.style.gridTemplateColumns = colsWith(nextList, nextCopilot);
      }
    };
    const onUp = () => {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      document.body.style.userSelect = "";
      if (which === "list") {
        setListWidth(nextList);
        try { localStorage.setItem(`kacrm_list_width_${module}`, String(Math.round(nextList))); } catch { /* ignore */ }
      } else {
        setCopilotWidth(nextCopilot);
        try { localStorage.setItem(`kacrm_copilot_width_${module}`, String(Math.round(nextCopilot))); } catch { /* ignore */ }
      }
    };
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  }
  function resetWidth(which: "list" | "copilot") {
    if (which === "list") {
      setListWidth(DEFAULT_LIST_W);
      try { localStorage.removeItem(`kacrm_list_width_${module}`); } catch { /* ignore */ }
    } else {
      setCopilotWidth(DEFAULT_COPILOT_W);
      try { localStorage.removeItem(`kacrm_copilot_width_${module}`); } catch { /* ignore */ }
    }
  }
  const areas = showCopilotRail
    ? (feedMode ? `"tabs tabs" "feed copilot"`
                : `"tabs tabs tabs" "list detail copilot"`)
    : (feedMode ? `"tabs" "feed"`
                : `"tabs tabs" "list detail"`);

  return (
    <div ref={gridRef} className={`kacrm-workspace${feedMode ? " is-feed" : ""}${recordId !== null ? " has-selection" : ""}`} style={{
      position: "relative",
      display: "grid",
      gridTemplateRows: "56px minmax(0, 1fr)",
      gridTemplateColumns: cols,
      gridTemplateAreas: areas,
      height: "100%",
    }}>
      <div style={{
        gridArea: "tabs",
        background: "var(--topbar-bg)",
        borderBottom: "1px solid var(--border)",
        display: "flex", alignItems: "center",
        padding: "0 14px", gap: 4,
        // Responsive overflow: when the viewport is too narrow to fit
        // all 11+ tabs, scroll horizontally instead of clipping or
        // wrapping to a second row (tab strips are mentally a single
        // line; wrap looks broken). Hide the WebKit scrollbar for
        // visual cleanliness.
        overflowX: "auto",
        whiteSpace: "nowrap",
        scrollbarWidth: "thin",
      }}
        className="crm-tab-strip"
      >
        <button className={feedMode ? "primary" : "ghost"}
                onClick={() => { setFeedMode(true); setRecordId(null); }}
                title={compactTabs ? t("crm.feed.button") : t("crm.feed.tooltip")}
                style={{ display: "inline-flex", alignItems: "center", gap: 6, flexShrink: 0 }}>
          {/* Lucide-style "activity" stroke icon (not the OS-rendered 📰
              emoji which looked dated; SVG renders consistently across
              Mac/Win/Linux). */}
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none"
               stroke="currentColor" strokeWidth="1.8"
               strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M22 12h-4l-3 9L9 3l-3 9H2" />
          </svg>
          {!compactTabs && t("crm.feed.button")}
        </button>
        {modules.map(m => {
          // Prefer an i18n key when known; fall back to the backend-
          // provided ``label`` (already localized by modules_db) and
          // then the raw name.
          // i18n key first (locale-aware); fall back to backend label (always CN).
          const localizedLabel = m.labelKey
            ? t(m.labelKey)
            : (() => {
                // 自定义模块无静态 key —— t() 缺失返回 key 本身,判等回退后端 label。
                const k = `crm.module.${m.name}`;
                const tr = t(k as DictKey);
                return tr === k ? (m.label || m.name) : tr;
              })();
          return (
            <button key={m.name}
                    className={!feedMode && module === m.name ? "primary" : "ghost"}
                    onClick={() => {
                      setFeedMode(false);
                      setModule(m.name);
                      setRecordId(null);
                    }}
                    title={compactTabs ? localizedLabel : undefined}
                    style={{ display: "inline-flex", alignItems: "center", gap: 6, flexShrink: 0 }}>
              <ModuleIcon name={m.name} />
              {!compactTabs && localizedLabel}
            </button>
          );
        })}
      </div>
      {feedMode ? (
        <div style={{ gridArea: "feed", overflow: "hidden" }}>
          <DailyFeed onAskCopilot={(text) =>
            setCopilotPrefill({ text, nonce: Date.now() })
          } />
        </div>
      ) : (<>
      <div className="kacrm-list-panel" style={{ gridArea: "list", borderRight: "1px solid var(--border)", overflow: "hidden", position: "relative" }}>
        <RecordList module={module} moduleLabel={moduleLabel} selectedId={recordId} onSelect={setRecordId}
          refreshNonce={recordRefreshNonce}
          isAdmin={isAdmin}
          orgListFields={listFieldsMap[module] ?? null}
          onOrgListFieldsSaved={(cfg) => setListFieldsMap(prev => ({ ...prev, [module]: cfg }))}
          onCreated={() => setRecordRefreshNonce(n => n + 1)} />
        <div
          onMouseDown={(e) => startResize(e, "list")}
          onDoubleClick={() => resetWidth("list")}
          title={t("resize.list")}
          className="crm-resize-handle"
          style={{
            position: "absolute", top: 0, right: -3, bottom: 0, width: 6,
            cursor: "col-resize", zIndex: 5,
          }}
        />
      </div>
      <div className="kacrm-detail-panel" style={{ gridArea: "detail", overflow: "hidden" }}>
        {recordId !== null && <button className="ghost kacrm-back-to-list" onClick={() => setRecordId(null)}>
          <CrmIcon name="back" size={16} />{t("crm.list.back", { module: moduleLabel })}
        </button>}
        {recordId === null
          ? <DailyFeed module={module} compact onAskCopilot={(text) =>
              setCopilotPrefill({ text, nonce: Date.now() })
            } />
          : <RecordDetail
              moduleTitle={moduleLabel}
              module={module}
              recordId={recordId}
              refreshNonce={recordRefreshNonce}
              headlineOverride={headlineMap[module] ?? null}
              canCustomizeHeadline={isAdmin}
              onHeadlineSaved={(fields) => {
                setHeadlineMap(prev => ({ ...prev, [module]: fields }));
                setRecordRefreshNonce(n => n + 1);
              }}
              onAskCopilot={(text) =>
                setCopilotPrefill({ text, nonce: Date.now() })
              }
              onDeleted={() => {
                // After soft-delete: deselect (so detail panel shows
                // the empty state) and bump the list nonce so the
                // deleted row disappears from the left rail.
                setRecordId(null);
                setRecordRefreshNonce(n => n + 1);
              }}
            />}
      </div>
      </>)}
      {showCopilotRail && (
        <div className="kacrm-copilot-panel" style={{ gridArea: "copilot", borderLeft: "1px solid var(--border)", overflow: "hidden", position: "relative" }}>
          <div
            onMouseDown={(e) => startResize(e, "copilot")}
            onDoubleClick={() => resetWidth("copilot")}
            title={t("resize.copilot")}
            className="crm-resize-handle"
            style={{
              position: "absolute", top: 0, left: -3, bottom: 0, width: 6,
              cursor: "col-resize", zIndex: 5,
            }}
          />
          <AgentWorkbench
            module={feedMode ? "accounts" : module}
            recordId={feedMode ? undefined : (recordId ?? undefined)}
            prefill={copilotPrefill}
            onRecordUpdated={() => setRecordRefreshNonce(n => n + 1)}
          />
        </div>
      )}

      {/* Narrow-viewport: floating toggle button + slide-in overlay. */}
      {compactTabs && !copilotOpen && (
        <button
          onClick={() => setCopilotOpen(true)}
          aria-label="Copilot"
          title="Copilot"
          style={{
            // Keep the shortcut below record dialogs and their backdrops.
            position: "fixed", right: 16, bottom: 24, zIndex: 30,
            width: 44, height: 44, borderRadius: "50%",
            background: "var(--accent)",
            color: "var(--fg-on-accent)", border: "none", cursor: "pointer",
            boxShadow: "var(--shadow-popover)",
            display: "grid", placeItems: "center",
          }}
        >
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none"
               stroke="currentColor" strokeWidth="1.8"
               strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z" />
          </svg>
        </button>
      )}
      {showCopilotOverlay && (
        <>
          <div
            onClick={() => setCopilotOpen(false)}
            style={{
              position: "fixed", inset: 0, zIndex: 60,
              background: "var(--overlay)",
            }}
          />
          <div style={{
            position: "fixed", top: 0, right: 0, bottom: 0,
            width: "min(360px, 90vw)", zIndex: 61,
            background: "var(--panel)",
            borderLeft: "1px solid var(--border)",
            display: "flex", flexDirection: "column",
            boxShadow: "var(--shadow-modal)",
          }}>
            <div style={{
              display: "flex", alignItems: "center", justifyContent: "space-between",
              padding: "8px 12px", borderBottom: "1px solid var(--border)",
            }}>
              <span style={{ fontSize: 14, fontWeight: 600 }}>Copilot</span>
              <button onClick={() => setCopilotOpen(false)}
                aria-label="close" className="icon-close"
                style={{ background: "transparent", border: "none",
                         color: "var(--muted)", cursor: "pointer", fontSize: 18 }}>×</button>
            </div>
            <div style={{ flex: 1, overflow: "hidden" }}>
              <AgentWorkbench
                module={feedMode ? "accounts" : module}
                recordId={feedMode ? undefined : (recordId ?? undefined)}
                prefill={copilotPrefill}
                onRecordUpdated={() => setRecordRefreshNonce(n => n + 1)}
              />
            </div>
          </div>
        </>
      )}
    </div>
  );
}
