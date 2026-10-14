import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  getRecord, getEntityFacts, getSubgraph,
  getCoachHintUnreadCount, deleteRecord,
  listRecordAttachments, listSubdetails, aiFillRecord,
  type CrmRecord, type CrmFieldMeta, type MemoryFact, type MemoryEntity,
  type LineItem, type RecordAttachment, type SubdetailDef, type AiFieldMeta,
  type ApprovalDetail,
  ApiError,
} from "@/lib/api";
import { ApprovalChainPanel } from "./ApprovalChainPanel";
import { useRouter } from "@/lib/router";
import { useT, useLocale, enumLabel, type T } from "@/lib/i18n";
import { InteractionTimeline } from "./InteractionTimeline";
import { RelatedEntities } from "./RelatedEntities";
import { AIFocus } from "./AIFocus";
import { NoteForm } from "./NoteForm";
import { AllInfoDrawer, isSystemField } from "./AllInfoDrawer";
import { HeadlineCustomizer } from "./HeadlineCustomizer";
import { TeamFootprintCard } from "./TeamFootprintCard";
import { resolveHeadlineFields, headlineLabelOverride, getPersonalHeadline } from "@/lib/moduleHeadlines";
import AiConfidenceDot from "./AiConfidenceDot";
import HoverTip from "./HoverTip";
import { moduleLabel, resolveFieldLabel, selectValueLabel } from "@/lib/crmLabels";
import { usePermissions } from "@/lib/permissions";
import { useCurrentUser } from "@/lib/currentUser";
import { ShareRecordModal } from "./ShareRecordModal";
import { CrmIcon } from "./CrmIcon";
import "./record-detail.css";

const STATS_KEYS = [
  "latest_contact_date", "next_contact_date",
  "contact_count", "latest_kind", "latest_progress",
] as const;

type StatsLabelKey =
  | "rdetail.stats.latest_contact_date"
  | "rdetail.stats.latest_progress"
  | "rdetail.stats.next_contact_date"
  | "rdetail.stats.contact_count"
  | "rdetail.stats.latest_kind";

const STATS_LABEL_KEY: Record<string, StatsLabelKey> = {
  latest_contact_date: "rdetail.stats.latest_contact_date",
  latest_progress: "rdetail.stats.latest_progress",
  next_contact_date: "rdetail.stats.next_contact_date",
  contact_count: "rdetail.stats.contact_count",
  latest_kind: "rdetail.stats.latest_kind",
};

type KindKey =
  | "record.kind.email" | "record.kind.call" | "record.kind.call_transcript"
  | "record.kind.meeting" | "record.kind.meeting_notes" | "record.kind.im_message"
  | "record.kind.note" | "record.kind.contact_log" | "record.kind.voice_memo"
  | "record.kind.doc" | "record.kind.document" | "record.kind.sms";

const KIND_LABEL_KEY: Record<string, KindKey> = {
  email: "record.kind.email",
  call: "record.kind.call",
  call_transcript: "record.kind.call_transcript",
  meeting: "record.kind.meeting",
  meeting_notes: "record.kind.meeting_notes",
  im_message: "record.kind.im_message",
  note: "record.kind.note",
  contact_log: "record.kind.contact_log",
  voice_memo: "record.kind.voice_memo",
  doc: "record.kind.doc",
  document: "record.kind.document",
  sms: "record.kind.sms",
};

function formatDateTime(t: T, v: unknown): string {
  if (v === null || v === undefined || v === "") return t("rdetail.dash");
  const s = String(v);
  const d = new Date(s);
  if (Number.isNaN(d.getTime())) return s;
  const pad = (n: number) => String(n).padStart(2, "0");
  const today = new Date();
  const sameDay = d.getFullYear() === today.getFullYear()
    && d.getMonth() === today.getMonth()
    && d.getDate() === today.getDate();
  const hm = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  if (sameDay) return t("rdetail.today_hm", { hm });
  const ymd = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  return `${ymd} ${hm}`;
}

function formatMoney(t: T, v: unknown): string {
  const n = Number(v);
  if (!isFinite(n)) return String(v ?? t("rdetail.dash"));
  return `¥${n.toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
}

function formatStatValue(t: T, key: string, value: unknown): string {
  if (value === null || value === undefined || value === "") return t("rdetail.dash");
  if (key === "latest_contact_date" || key === "next_contact_date") {
    return formatDateTime(t, value);
  }
  if (key === "latest_kind") {
    const k = KIND_LABEL_KEY[String(value)];
    return k ? t(k) : String(value);
  }
  return String(value);
}

/**
 * Format a single field value per its metadata. This is the difference
 * between "1" and "下一代物种" — relation / user / department fields
 * have a `*_display` sibling computed by DetailQuery; we prefer that.
 */
function renderFieldValue(
  t: T, field: CrmFieldMeta, record: CrmRecord,
): { display: string; relation?: { module: string; id: number } } {
  const fn = field.field_name;
  const raw = record[fn];
  const dash = t("rdetail.dash");

  // Display sibling takes precedence for FK-typed fields.
  const displayKey = fn + "_display";
  if (record[displayKey] !== undefined && record[displayKey] !== null) {
    const disp = String(record[displayKey]);
    if (disp.trim() === "") {
      // explicit empty → render dash, unless raw is nonzero (show raw id as last resort)
      if (raw && String(raw) !== "0") return { display: `#${raw}` };
      return { display: dash };
    }
    // For relation fields, extract the target module from datainfo for a click target.
    if (field.field_type === "relation" && field.datainfo) {
      const parts = field.datainfo.split("#");
      if (parts[0] && /^\d+$/.test(parts[0])) {
        const rawId = Number(raw);
        if (rawId > 0) {
          return {
            display: disp,
            relation: { module: parts[1] || "", id: rawId },
          };
        }
      }
    }
    return { display: disp };
  }

  // No display sibling — render raw by type.
  if (raw === null || raw === undefined || raw === "") return { display: dash };
  switch (field.field_type) {
    case "date": return { display: /^\d{4}-\d{2}-\d{2}/.test(String(raw)) ? String(raw).slice(0, 10) : String(raw) };
    case "datetime": return { display: formatDateTime(t, raw) };
    case "currency": case "money": case "number": {
      if ([
        "amount", "grand_total", "annual_revenue", "price", "total",
        "final_amount", "dueamount", "payamount", "actualamount", "balance",
        "deal_amount", "cost_price", "reference_price",
      ].includes(fn)) {
        return { display: formatMoney(t, raw) };
      }
      return { display: String(raw) };
    }
    case "select": case "picklist": case "radio": {
      // Map the stored value to its localized option label
      // (科技 not technology). options arrives localized from the
      // detail endpoint as [{value,text}]. 若该字段 options 未带本地化
      // label(selectValueLabel 原样返回),回退到通用枚举翻译层
      // (approval_status / status 等裸英文枚举统一双语)。
      const mapped = selectValueLabel(raw, field.options);
      return { display: mapped === String(raw) ? enumLabel(t, raw) : mapped };
    }
    case "boolean": case "checkbox":
      return { display: raw ? t("rdetail.yes") : t("rdetail.no") };
    default:
      return { display: String(raw) };
  }
}

export function RecordDetail({
  module, recordId, moduleTitle, onAskCopilot, refreshNonce = 0,
  headlineOverride, canCustomizeHeadline = false, onHeadlineSaved,
  onDeleted,
}: {
  module: string; recordId: number; moduleTitle?: string;
  // Parent owns Copilot prefill state; we push natural-language drafts
  // into it when the user invokes "让 Copilot 改这项" from the drawer.
  onAskCopilot?: (prefillText: string) => void;
  // Parent bumps this to force a re-fetch (e.g. after Copilot edits a field).
  refreshNonce?: number;
  // Admin-curated chip-row override (migration 0020). null = use default.
  headlineOverride?: string[] | null;
  // Gates the ⚙ customize button. True for admins only.
  canCustomizeHeadline?: boolean;
  // Called after the customizer saves, with the server-echoed field list.
  onHeadlineSaved?: (fields: string[] | null) => void;
  // Called after the record is soft-deleted via the 删除 button. Parent
  // should clear the selected recordId and refresh the list. Omit to
  // hide the delete button (e.g. read-only drilldown surfaces).
  onDeleted?: () => void;
}) {
  const t = useT();
  const { locale } = useLocale();
  const [record, setRecord] = useState<CrmRecord | null>(null);
  const [loadedIdentity, setLoadedIdentity] = useState<string | null>(null);
  const [fields, setFields] = useState<CrmFieldMeta[]>([]);
  const [lineItems, setLineItems] = useState<LineItem[]>([]);
  // generic 子明细(如费用报销的费用明细):def 来自 listSubdetails,行来自 getRecord.details。
  const [genericSubs, setGenericSubs] = useState<SubdetailDef[]>([]);
  const [detailRows, setDetailRows] = useState<Record<string, Record<string, unknown>[]>>({});
  const [hasApproval, setHasApproval] = useState(false);
  const [approvalData, setApprovalData] = useState<ApprovalDetail | Record<string, never>>({});
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);

  // Law 5 — AI drives, manual is the fallback.
  const [noteOpen, setNoteOpen] = useState(false);
  const [statsOpen, setStatsOpen] = useState(false);
  // §168 — "全部信息" drawer replaces the old 更多字段 schema dump.
  const [infoOpen, setInfoOpen] = useState(false);
  // Chip-row customizer (migration 0020). Open to everyone — admins set
  // the org default, everyone can set a personal override.
  const [customizeOpen, setCustomizeOpen] = useState(false);
  // Bumped after a personal headline override save/reset so the chip row
  // re-resolves from localStorage.
  const [headlineNonce, setHeadlineNonce] = useState(0);
  const [deleting, setDeleting] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  useEffect(() => { setDeleteOpen(false); setDeleteError(null); }, [module, recordId]);
  // Record sharing modal.
  const [shareOpen, setShareOpen] = useState(false);
  // AI 补全 button busy state — disables the trigger while the fill runs.
  const [aiBusy, setAiBusy] = useState(false);

  // Unread coach-hints for this specific record — powers the red-dot
  // badge next to the record id. The full hint content lives in the
  // WeeklyAiReportCard on the CRM home; here we only signal "there's
  // unread advice on this record."
  const [unreadHints, setUnreadHints] = useState(0);

  // Phase 5.2.B — manager flag for "团队成员谁也跟过" footprint card.
  const { user: me } = useCurrentUser();

  // Permissions for share button gate.
  const { data: permsData, grants } = usePermissions();
  const currentUid = me?.id ?? 0;
  const canShare = !!permsData?.is_admin
    || (!!record && record.owner_by === currentUid && grants.has(`${module}.share`));
  useEffect(() => {
    if (recordId === undefined) return;
    let stop = false;
    getCoachHintUnreadCount({ module, recordId })
      .then(r => { if (!stop) setUnreadHints(r.unread || 0); })
      .catch(() => {});
    return () => { stop = true; };
  }, [module, recordId, refreshKey]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setErr(null);
      try {
        const r = await getRecord(module, recordId);
        if (!cancelled) {
          setRecord(r.data);
          setLoadedIdentity(`${module}:${recordId}`);
          setFields(r.fields || []);
          setLineItems(r.products || []);
          setDetailRows(r.details || {});
          setHasApproval(!!r.has_approval);
          setApprovalData(r.approval || {});
        }
        // generic 子明细 schema(产品行另走 lineItems);失败不致命,留空。
        try {
          const sd = await listSubdetails(module);
          if (!cancelled) {
            setGenericSubs((sd.subdetails || []).filter(s => s.detail_kind === "generic"));
          }
        } catch { if (!cancelled) setGenericSubs([]); }
      } catch (e) {
        if (!cancelled) {
          setErr(
            e instanceof ApiError
              ? t("rdetail.err_status", { status: String(e.status) })
              : t("rdetail.err_load"),
          );
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [module, recordId, refreshKey, refreshNonce, t]);

  // Build the headline chip fields — the ≤5 business-language facts
  // pinned to the top. Everything else lives behind "全部信息".
  const headlineFields = useMemo<CrmFieldMeta[]>(() => {
    if (!record || fields.length === 0) return [];
    const personal = getPersonalHeadline(module);
    const names = resolveHeadlineFields(module, headlineOverride, personal);
    const byName = new Map(fields.filter(f => !f.is_hidden).map(f => [f.field_name, f]));
    const out: CrmFieldMeta[] = [];
    names.forEach(n => { const f = byName.get(n); if (f) out.push(f); });
    // Custom modules have no built-in headline list. Use metadata for a small
    // business overview, while respecting explicit empty/custom selections.
    if (!names.length && headlineOverride == null && personal == null) {
      return fields.filter(f => !f.is_hidden && !isSystemField(f)
        && f.field_name !== "name" && !f.field_name.endsWith("_name")
        && !["textarea", "longtext", "richtext", "text_multi"].includes(f.field_type || ""))
        .sort((a, b) => (a.sort_order ?? 999) - (b.sort_order ?? 999)).slice(0, 4);
    }
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [record, fields, module, headlineOverride, headlineNonce]);

  // Count every non-hidden field — matches what the "全部信息" drawer
  // lists (it shows the name field as a row too), so the trigger
  // button's "(N)" agrees with the drawer's "共 N 项" header.
  const visibleFieldCount = useMemo(
    () => fields.filter(f => !f.is_hidden).length,
    [fields],
  );

  // Show the "AI 补全" trigger only when the module declares at least one
  // AI-fill field (otherwise the button does nothing).
  const hasAiFields = useMemo(
    () => fields.some(f => f.field_config?.ai_fill?.enabled),
    [fields],
  );

  // Manual AI fill — runs the satisfied-config agent for every AI field on
  // this record, then re-fetches so the new values + ai_meta render.
  async function onAiFill() {
    setAiBusy(true);
    try {
      await aiFillRecord(module, recordId);
      setRefreshKey(k => k + 1);
    } finally {
      setAiBusy(false);
    }
  }

  // Refresh the current record in place: replacing the whole subtree with a
  // loading screen unmounts open drawers and discards their editing/scroll state.
  // A different record still gets an initial loading screen, never stale details.
  const hasCurrentRecord = loadedIdentity === `${module}:${recordId}` && !!record;
  if (loading && !hasCurrentRecord) return <div className="muted" style={{ padding: 16 }}>{t("common.loading")}</div>;
  if (err && !hasCurrentRecord) return <div style={{ padding: 16, color: "var(--danger)" }}>{err}</div>;
  if (!record || !hasCurrentRecord) return <div className="muted" style={{ padding: 16 }}>{t("rdetail.no_record")}</div>;

  const hasStats = STATS_KEYS.some(k => record[k] !== undefined);
  const topStats = (["latest_contact_date", "next_contact_date", "contact_count", "latest_kind"] as const)
    .filter(k => record[k] !== undefined);
  const progress = record["latest_progress"];
  const hasProgress = progress !== undefined && progress !== null && String(progress).trim() !== "";

  const headline = record.account_name || record.contact_name || record.lead_name
    || record.potential_name || record.name || record.subject || `#${record.id}`;

  // "Recent activity" hint for the header — renders inline so the user
  // immediately sees why this record is in today/overdue/dormant bucket.
  const activityHint = (() => {
    if (record.next_contact_date) {
      const d = new Date(String(record.next_contact_date));
      if (!Number.isNaN(d.getTime())) {
        const now = Date.now();
        const dt = formatDateTime(t, record.next_contact_date);
        if (d.getTime() < now) {
          return { text: t("rdetail.hint.overdue", { dt }), danger: true };
        }
        return { text: t("rdetail.hint.next", { dt }), danger: false };
      }
    }
    if (record.latest_contact_date) {
      return {
        text: t("rdetail.hint.last", { dt: formatDateTime(t, record.latest_contact_date) }),
        danger: false,
      };
    }
    return null;
  })();

  const customizeButton = (<button
          onClick={() => setCustomizeOpen(true)}
          title={t("rdetail.customize_title")}
          aria-label={t("rdetail.customize_title")}
          className="ghost kacrm-headline-customize"
          style={{
            display: "flex", alignItems: "center", justifyContent: "center",
            padding: "8px 12px", minWidth: 44, minHeight: 36,
            background: "transparent",
            border: "1px dashed var(--border)", borderRadius: 6,
            color: "var(--muted)", fontSize: 18, lineHeight: 1,
          }}
        ><CrmIcon name="settings" size={18} />{t("rdetail.customize_title")}</button>);

  return (
    <div className="col kacrm-record-detail" style={{ gap: 14, padding: 16, overflowY: "auto", height: "100%" }}>
      {err && <div role="alert" style={{ color: "var(--danger)" }}>{err}</div>}
      {/* Header — Law 4: module-aware; Law 6: provenance chip shown inline.
          Title row now carries the "全部信息" entry point on the right
          (§168 — user-facing copy is "信息", not "字段"). */}
      <header className="kacrm-detail-header">
        <div className="row kacrm-detail-meta" style={{ gap: 8, alignItems: "center" }}>
          <div className="muted small" style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
            {moduleTitle || moduleLabel(module, null)} · #{record.id}
            {unreadHints > 0 && (
              <span
                title={t("rdetail.unread_title", { n: unreadHints })}
                style={{
                  minWidth: 16, height: 16, padding: "0 5px",
                  borderRadius: 10,
                  background: "var(--danger)", color: "var(--fg-on-accent)",
                  fontSize: 13, fontWeight: 700,
                  display: "inline-flex", alignItems: "center", justifyContent: "center",
                  lineHeight: 1,
                }}
              >{unreadHints}</span>
            )}
          </div>
          {record.owner_by_display && (
            <span className="chip" title={t("rdetail.owner_title")}><CrmIcon name="user" size={14} /> {record.owner_by_display}</span>
          )}
          {record.created_by_display && record.created_by_display !== record.owner_by_display && (
            <span className="chip muted small" title={t("rdetail.created_by_title")}>
              {t("rdetail.created_by", { name: String(record.created_by_display) })}
            </span>
          )}
        </div>
        <div className="kacrm-detail-title-row" style={{
          justifyContent: "space-between", alignItems: "center", gap: 8,
        }}>
          <h2 style={{ margin: "4px 0", flex: 1 }}>{headline}</h2>
          <div className="kacrm-detail-actions">
          {headlineFields.length === 0 && customizeButton}
          {hasAiFields && (
            <button
              className="ghost"
              onClick={onAiFill}
              disabled={aiBusy}
              title={aiBusy ? t("record.aiField.filling") : t("record.aiField.fill")}
              aria-label={t("record.aiField.fill")}
              style={{
                display: "inline-flex", alignItems: "center", gap: 4,
                padding: "4px 8px",
                color: "var(--accent, var(--stat-violet))",
                border: "1px solid var(--border)", borderRadius: 6,
                fontSize: 13, lineHeight: 1,
                opacity: aiBusy ? 0.6 : 1,
                cursor: aiBusy ? "wait" : "pointer",
              }}
            ><CrmIcon name="sparkles" size={16} /> {aiBusy ? t("record.aiField.filling") : t("record.aiField.fill")}</button>
          )}
          {canShare && (
            <button
              className="ghost"
              onClick={() => setShareOpen(true)}
              title={t("crm.share.button")}
              aria-label={t("crm.share.button")}
              style={{
                display: "inline-flex", alignItems: "center", justifyContent: "center",
                padding: "4px 6px",
                color: "var(--muted)",
                border: "1px solid var(--border)", borderRadius: 6,
                fontSize: 15, lineHeight: 1,
              }}
            ><CrmIcon name="share" size={16} />{t("crm.share.button")}</button>
          )}
          <button
            className="ghost"
            onClick={() => setInfoOpen(true)}
            title={t("rdetail.info_btn", { n: visibleFieldCount })}
            aria-label={t("rdetail.info_btn", { n: visibleFieldCount })}
            style={{
              display: "inline-flex", alignItems: "center", justifyContent: "center",
              padding: "4px 6px",
              color: "var(--muted)",
              border: "1px solid var(--border)", borderRadius: 6,
            }}
          >
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none"
                 stroke="currentColor" strokeWidth="2"
                 strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <line x1="8" y1="6" x2="21" y2="6"/>
              <line x1="8" y1="12" x2="21" y2="12"/>
              <line x1="8" y1="18" x2="21" y2="18"/>
              <line x1="3" y1="6" x2="3.01" y2="6"/>
              <line x1="3" y1="12" x2="3.01" y2="12"/>
              <line x1="3" y1="18" x2="3.01" y2="18"/>
            </svg>
            {t("rdetail.info_btn", { n: visibleFieldCount })}
          </button>
          {onDeleted && (
            <button
              className="ghost"
              onClick={() => { setDeleteError(null); setDeleteOpen(true); }}
              disabled={deleting}
              title={deleting ? t("rdetail.delete_btn_busy") : t("rdetail.delete_btn_title")}
              aria-label={t("rdetail.delete_btn")}
              style={{
                display: "inline-flex", alignItems: "center", justifyContent: "center",
                padding: "4px 6px",
                color: "var(--danger)",
                border: "1px solid var(--danger)", borderRadius: 6,
                opacity: deleting ? 0.5 : 1,
                cursor: deleting ? "wait" : "pointer",
              }}
            >
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none"
                   stroke="currentColor" strokeWidth="2"
                   strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <polyline points="3 6 5 6 21 6"/>
                <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>
                <path d="M10 11v6M14 11v6"/>
                <path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/>
              </svg>
            </button>
          )}
          </div>
        </div>
        {activityHint && (
          <div className={"small"} style={{
            color: activityHint.danger ? "var(--danger)" : "var(--muted)",
          }}>
            {activityHint.text}
          </div>
        )}
      </header>

      {/* Headline chips — the ≤5 business-language facts. No card wrapper,
          no grid — just a compact wrap row. Clicking a chip opens
          "全部信息" focused on that field (future; for now opens drawer). */}
      {headlineFields.length > 0 && <div className={`kacrm-headline-grid${headlineFields.length <= 4 ? " is-compact" : ""}${module === "contacts" || module === "leads" ? " is-people" : ""}${module === "contacts" ? " is-contact" : ""}`} style={{ gap: 10, flexWrap: "wrap", alignItems: "stretch" }}>
        {headlineFields.map(f => (
          <HeadlineChip key={f.field_name} field={f} record={record}
            onClick={() => setInfoOpen(true)} />
        ))}
        {customizeButton}
      </div>}

      {/* 系统记忆面板 — 第一性原理：客户详情自带「AI 记住的关于这个客户的事」。
          仅当该 CRM 记录已绑定 memory_entity_id 时展示（迁移 0018 已 backfill 全部存活记录）。 */}
      {record.memory_entity_id ? (
        <MemoryPanel entityId={Number(record.memory_entity_id)} />
      ) : null}

      {/* Charter v2 step-3 — AI 焦点 from coach_hints. Renders nothing
          when no hints; otherwise sits at the top of the detail body
          to highlight what the agent has noticed about this record. */}
      <AIFocus module={module} recordId={recordId} refreshKey={refreshKey}
               onAskCopilot={onAskCopilot} />

      {/* Charter v2 step-2 narrative cards: only renders for accounts
          (other modules get this in a follow-up); auto-skips empty
          groups; closed/lost rows filtered out client-side. */}
      <RelatedEntities module={module} recordId={recordId} refreshKey={refreshKey} />

      {/* Line items — for quotes / salesorders. Product 必填 per user charter. */}
      {lineItems.length > 0 && (
        <section className="card kacrm-detail-lineitems" style={{ padding: 12 }}>
          <div style={{
            fontSize: 14, fontWeight: 600, marginBottom: 8,
            display: "flex", justifyContent: "space-between", alignItems: "baseline",
          }}>
            <span>{t("rdetail.li.title")}</span>
            <span style={{ fontSize: 13, color: "var(--muted)", fontWeight: 400 }}>
              {t("rdetail.li.count", { n: lineItems.length })}
            </span>
          </div>
          <div className="kacrm-detail-table-scroll" style={{ overflowX: "auto" }}>
            <table className="kacrm-lineitems-table" style={{ width: "100%", borderCollapse: "collapse", fontSize: 14 }}>
              <thead>
                <tr style={{ textAlign: "left", color: "var(--muted)", fontSize: 13 }}>
                  <th scope="col" style={{ padding: "6px 8px", fontWeight: 500 }}>{t("rdetail.li.col.product")}</th>
                  <th scope="col" style={{ padding: "6px 8px", fontWeight: 500 }}>{t("rdetail.li.col.spec")}</th>
                  <th scope="col" style={{ padding: "6px 8px", fontWeight: 500, textAlign: "right" }}>{t("rdetail.li.col.qty")}</th>
                  <th scope="col" style={{ padding: "6px 8px", fontWeight: 500, textAlign: "right" }}>{t("rdetail.li.col.price")}</th>
                  <th scope="col" style={{ padding: "6px 8px", fontWeight: 500, textAlign: "right" }}>{t("rdetail.li.col.subtotal")}</th>
                </tr>
              </thead>
              <tbody>
                {lineItems.map(li => (
                  <tr key={li.line_id} style={{ borderTop: "1px solid var(--border)" }}>
                    <td data-label={t("rdetail.li.col.product")} style={{ padding: "8px" }}>
                      <div style={{ fontWeight: 500 }}>{li.products_name}</div>
                      {li.productno && (
                        <div style={{ fontSize: 13, color: "var(--muted)" }}>{li.productno}</div>
                      )}
                    </td>
                    <td data-label={t("rdetail.li.col.spec")} style={{ padding: "8px", color: "var(--muted)", fontSize: 14 }}>
                      {li.specifications || li.model || t("rdetail.dash")}
                    </td>
                    <td data-label={t("rdetail.li.col.qty")} style={{ padding: "8px", textAlign: "right" }}>{li.quantity ?? 1}</td>
                    <td data-label={t("rdetail.li.col.price")} style={{ padding: "8px", textAlign: "right" }}>
                      {li.price != null ? `¥${Number(li.price).toLocaleString()}` : t("rdetail.dash")}
                    </td>
                    <td data-label={t("rdetail.li.col.subtotal")} style={{ padding: "8px", textAlign: "right", fontWeight: 500 }}>
                      {li.total != null ? `¥${Number(li.total).toLocaleString()}` : t("rdetail.dash")}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}


      <SourceFiles module={module} recordId={recordId} refreshKey={refreshKey} />

      {/* Phase 5.2.B (v1.4) — others' history on this record. Renders
          whenever backend returns rows; visibility gated at the record
          level (caller must have access to the record itself).
          Inheritance case: 销售 接手 离职同事 的客户，看得到完整历史。 */}
      <TeamFootprintCard module={module} recordId={recordId} />

      {/* Law 1 — Interaction Log is the primary view. */}
      <InteractionTimeline
        module={module} recordId={recordId} refreshKey={refreshKey}
        onUndone={() => setRefreshKey(k => k + 1)}
      />

      {/* Law 5 — manual capture collapsed fallback. */}
      <div className="card" style={{ padding: noteOpen ? 12 : 8 }}>
        {!noteOpen ? (
          <button
            className="ghost"
            onClick={() => setNoteOpen(true)}
            style={{
              width: "100%", textAlign: "left", padding: "6px 10px",
              fontSize: 14, color: "var(--muted)",
            }}
          >
            <CrmIcon name="edit" size={16} /> {t("rdetail.note.open")} <span style={{ opacity: 0.6, marginLeft: 6 }}>
              {t("rdetail.note.hint_tail")}
            </span>
          </button>
        ) : (
          <>
            <div style={{
              display: "flex", justifyContent: "space-between", alignItems: "baseline",
              marginBottom: 6,
            }}>
              <div style={{ fontSize: 14, color: "var(--muted)" }}>
                {t("rdetail.note.title_fallback")}
              </div>
              <button
                className="ghost" style={{ fontSize: 13, padding: "2px 8px" }}
                onClick={() => setNoteOpen(false)}
              >{t("rdetail.note.collapse")}</button>
            </div>
            <NoteForm
              module={module} recordId={recordId}
              onSaved={() => { setRefreshKey(k => k + 1); setNoteOpen(false); }}
            />
          </>
        )}
      </div>

      {/* 审批链 —— 仅当模块配置了 approval_config(has_approval)。 */}
      {hasApproval && (
        <ApprovalChainPanel
          module={module}
          recordId={recordId}
          approval={approvalData}
          currentUid={currentUid}
          onChanged={() => setRefreshKey(k => k + 1)}
        />
      )}

      {/* Generic 子明细(费用明细等)—— 只读表格,元数据驱动。
          编辑走「全部信息」抽屉;此处只展示。 */}
      {genericSubs.map(sub => {
        const rows = detailRows[sub.name] || [];
        if (rows.length === 0) return null;
        const cols = sub.fields || [];
        return (
          <div className="card" style={{ padding: 12 }} key={sub.name}>
            <div style={{
              fontSize: 14, fontWeight: 600, marginBottom: 8,
              display: "flex", justifyContent: "space-between", alignItems: "baseline",
            }}>
              <span>{sub.title || sub.name}</span>
              <span style={{ fontSize: 13, color: "var(--muted)", fontWeight: 400 }}>
                {t("rdetail.li.count", { n: rows.length })}
              </span>
            </div>
            <div className="kacrm-detail-table-scroll" style={{ overflowX: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 14 }}>
                <thead>
                  <tr style={{ textAlign: "left", color: "var(--muted)", fontSize: 13 }}>
                    {cols.map(c => (
                      <th key={c.field_name} style={{ padding: "6px 8px", fontWeight: 500 }}>
                        {c.field_label || c.field_name}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row, i) => (
                    <tr key={i} style={{ borderTop: "1px solid var(--border)" }}>
                      {cols.map(c => {
                        const raw = row[c.field_name];
                        const ft = (c.field_type || "").toLowerCase();
                        let disp: string;
                        if (raw == null || raw === "") disp = t("rdetail.dash");
                        else if (c.options) {
                          const m = selectValueLabel(String(raw), c.options, c.field_config, locale);
                          disp = m === String(raw) ? enumLabel(t, raw) : m;
                        }
                        else if (ft === "boolean") disp = raw ? "✓" : t("rdetail.dash");
                        else if (ft === "picklist" || ft === "select" || ft === "radio") disp = enumLabel(t, raw);
                        else disp = String(raw);
                        return <td key={c.field_name} style={{ padding: "8px" }}>{disp}</td>;
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        );
      })}

      {/* §168 — the old 更多字段 flat grid has been retired. All
          secondary information lives in the "全部信息" drawer now. */}

      {/* Projections — tertiary. */}
      {hasStats && (
        <CollapsibleCard
          title={t("rdetail.stats_card")}
          open={statsOpen}
          onToggle={() => setStatsOpen(v => !v)}
        >
          <div style={{
            display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))",
            gap: 12, marginBottom: hasProgress ? 14 : 0,
          }}>
            {topStats.map(k => (
              <div key={k}>
                <div className="muted small">{t(STATS_LABEL_KEY[k])}</div>
                <div style={{ fontSize: 15 }}>{formatStatValue(t, k, record[k])}</div>
              </div>
            ))}
          </div>
          {hasProgress && (
            <div>
              <div className="muted small" style={{ marginBottom: 4 }}>
                {t("rdetail.stats.latest_progress")}
              </div>
              <div style={{
                fontSize: 14, whiteSpace: "pre-wrap", wordBreak: "break-word",
                lineHeight: 1.55, maxHeight: 140, overflowY: "auto",
              }}>
                {String(progress)}
              </div>
            </div>
          )}
        </CollapsibleCard>
      )}

      <HeadlineCustomizer
        open={customizeOpen}
        onClose={() => setCustomizeOpen(false)}
        module={module}
        fields={fields}
        isAdmin={canCustomizeHeadline}
        orgDefault={headlineOverride ?? null}
        onOrgSaved={(fields) => { if (onHeadlineSaved) onHeadlineSaved(fields); }}
        onPersonalChanged={() => setHeadlineNonce(n => n + 1)}
      />

      <AllInfoDrawer
        open={infoOpen}
        onClose={() => setInfoOpen(false)}
        module={module}
        moduleTitle={moduleTitle}
        recordId={recordId}
        record={record}
        fields={fields}
        onUpdated={() => setRefreshKey(k => k + 1)}
        onAskCopilot={(text) => {
          // Fire the prefill into Copilot and auto-close the drawer so
          // the user immediately sees the chat input they need to edit.
          if (onAskCopilot) onAskCopilot(text);
          setInfoOpen(false);
        }}
      />

      {deleteOpen && onDeleted && (
        <DeleteRecordDialog
          name={headline || `${module}#${recordId}`}
          busy={deleting}
          error={deleteError}
          onCancel={() => { if (!deleting) setDeleteOpen(false); }}
          onConfirm={async () => {
            if (deleting) return;
            setDeleting(true);
            setDeleteError(null);
            try {
              await deleteRecord(module, recordId);
              setDeleteOpen(false);
              onDeleted();
            } catch (e) {
              const msg = e instanceof ApiError
                ? `${e.status}: ${typeof e.detail === "string" ? e.detail : JSON.stringify(e.detail)}`
                : String(e);
              setDeleteError(t("rdetail.delete_failed", { msg }));
            } finally { setDeleting(false); }
          }}
        />
      )}

      {shareOpen && (
        <ShareRecordModal
          module={module}
          recordId={recordId}
          recordLabel={headline || `${module}#${recordId}`}
          onClose={() => setShareOpen(false)}
        />
      )}
    </div>
  );
}

function DeleteRecordDialog({ name, busy, error, onCancel, onConfirm }: {
  name: string; busy: boolean; error: string | null;
  onCancel: () => void; onConfirm: () => void;
}) {
  const t = useT();
  const dialogRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    dialogRef.current?.querySelector<HTMLButtonElement>("button")?.focus();
    return () => { if (previous?.isConnected) previous.focus(); };
  }, []);
  return createPortal(
    <div className="kacrm-delete-overlay" onClick={busy ? undefined : onCancel}>
      <div className="kacrm-delete-dialog" ref={dialogRef} role="alertdialog" aria-modal="true"
        aria-labelledby="kacrm-delete-title" aria-describedby="kacrm-delete-description"
        onClick={e => e.stopPropagation()}
        onKeyDown={e => {
          if (e.key === "Escape") { e.preventDefault(); if (!busy) onCancel(); }
          if (e.key === "Tab") {
            const buttons = Array.from(dialogRef.current?.querySelectorAll<HTMLButtonElement>("button:not(:disabled)") || []);
            if (!buttons.length) { e.preventDefault(); return; }
            if (e.shiftKey && document.activeElement === buttons[0]) { e.preventDefault(); buttons[buttons.length - 1].focus(); }
            else if (!e.shiftKey && document.activeElement === buttons[buttons.length - 1]) { e.preventDefault(); buttons[0].focus(); }
          }
        }}>
        <h2 id="kacrm-delete-title">{t("rdetail.delete_dialog_title")}</h2>
        <p id="kacrm-delete-description">{t("rdetail.delete_confirm", { name })}</p>
        {error && <p className="kacrm-delete-error" role="alert">{error}</p>}
        <div className="kacrm-delete-actions">
          <button className="ghost" disabled={busy} onClick={onCancel}>{t("common.cancel")}</button>
          <button className="kacrm-delete-confirm" disabled={busy} onClick={onConfirm}>
            {t(busy ? "rdetail.delete_btn_busy" : "common.delete")}
          </button>
        </div>
      </div>
    </div>, document.body,
  );
}

/**
 * Headline chip — one of the ≤5 pinned business facts above the fold.
 * Compact, clickable (opens 全部信息 for deeper context). Renders
 * value using the same *_display fallback logic as FieldCell.
 */
/** Deterministic accent picker for picklist / select / source values.
 *
 * Hashes the value string into one of 8 preset "good-on-dark" accents
 * so the same value (e.g. "prospect") always gets the same colour
 * across the page, while "active" / "lost" / "won" each get distinct
 * colours without needing per-domain hand-mapping. Semantic overrides
 * for the most common known values (won → green, lost → red, …) are
 * applied first so anyone scanning the page can use the colour as
 * a status hint, not just a category code. */
// 数据状态/选项语义着色 —— 走 --stat-* CSS 变量(index.css 定义),
// 深色模式用亮色、浅色模式整组压深,白卡上达 WCAG AA。
const _PICKLIST_PALETTE = [
  { fg: "var(--stat-blue)", bg: "var(--stat-blue-soft)" },
  { fg: "var(--stat-green)", bg: "var(--stat-green-soft)" },
  { fg: "var(--stat-amber)", bg: "var(--stat-amber-soft)" },
  { fg: "var(--stat-pink)", bg: "var(--stat-pink-soft)" },
  { fg: "var(--stat-violet)", bg: "var(--stat-violet-soft)" },
  { fg: "var(--stat-cyan)", bg: "var(--stat-cyan-soft)" },
  { fg: "var(--stat-orange)", bg: "var(--stat-orange-soft)" },
  { fg: "var(--stat-slate)", bg: "var(--stat-slate-soft)" },
];

const _PICKLIST_SEMANTIC: Record<string, { fg: string; bg: string }> = {
  // Wins / completes (green family)
  "won": { fg: "var(--stat-green)", bg: "var(--stat-green-soft)" },
  "active": { fg: "var(--stat-green)", bg: "var(--stat-green-soft)" },
  "qualified": { fg: "var(--stat-green)", bg: "var(--stat-green-soft)" },
  "completed": { fg: "var(--stat-green)", bg: "var(--stat-green-soft)" },
  "已成交": { fg: "var(--stat-green)", bg: "var(--stat-green-soft)" },
  "已完成": { fg: "var(--stat-green)", bg: "var(--stat-green-soft)" },
  // Losses (red family)
  "lost": { fg: "var(--stat-red)", bg: "var(--stat-red-soft)" },
  "cancelled": { fg: "var(--stat-red)", bg: "var(--stat-red-soft)" },
  "已丢单": { fg: "var(--stat-red)", bg: "var(--stat-red-soft)" },
  "已取消": { fg: "var(--stat-red)", bg: "var(--stat-red-soft)" },
  // In-flight (amber family)
  "pending": { fg: "var(--stat-amber)", bg: "var(--stat-amber-soft)" },
  "draft": { fg: "var(--stat-amber)", bg: "var(--stat-amber-soft)" },
  "negotiation": { fg: "var(--stat-amber)", bg: "var(--stat-amber-soft)" },
  // Cold start (blue family)
  "new": { fg: "var(--stat-blue)", bg: "var(--stat-blue-soft)" },
  "prospect": { fg: "var(--stat-blue)", bg: "var(--stat-blue-soft)" },
  "contacted": { fg: "var(--stat-blue)", bg: "var(--stat-blue-soft)" },
  "新建": { fg: "var(--stat-blue)", bg: "var(--stat-blue-soft)" },
};

function pickPicklistAccent(value: string): { fg: string; bg: string } {
  const v = value.trim().toLowerCase();
  if (_PICKLIST_SEMANTIC[v]) return _PICKLIST_SEMANTIC[v];
  // Lightweight FNV-1a-ish hash → palette index.
  let h = 0;
  for (let i = 0; i < v.length; i++) {
    h = (h * 31 + v.charCodeAt(i)) >>> 0;
  }
  return _PICKLIST_PALETTE[h % _PICKLIST_PALETTE.length];
}

function _isPicklistField(field: CrmFieldMeta): boolean {
  const t = (field.field_type || "").toLowerCase();
  return t === "picklist" || t === "select" || t === "radio"
    || (!!field.options && t !== "relation");
}

/** AI-filled field's ✨ badge hover content,JSX 版本——同样的信息,但每行一个 <div>,在
 *  HoverTip 卡片里更易读。弃填态仍展示 abstain reason。 */
function aiBadgeContent(t: T, meta: AiFieldMeta): React.ReactNode {
  if (meta.abstained) {
    const reason = (meta.reason || "").toString().trim();
    return reason
      ? <div>{t("record.aiField.abstained")}: {reason}</div>
      : <div>{t("record.aiField.abstained")}</div>;
  }
  return (
    <>
      <div>{t("record.aiField.generated")}</div>
      {typeof meta.confidence === "number" && (
        <div>{t("record.aiField.confidence")}: {Math.round(meta.confidence * 100)}%</div>
      )}
      {meta.generated_at && <div>{String(meta.generated_at)}</div>}
      {meta.basis && meta.basis.length > 0 && (
        <div>{t("record.aiField.basis")}: {meta.basis.join("; ")}</div>
      )}
    </>
  );
}

/** ✨ badge shown next to an AI-fillable field's value. Reads the per-field
 *  entry from record.ai_meta; renders nothing until the backend has filled
 *  (or a faint "待填" marker so the user knows AI is wired for this field). */
function AiFieldBadge({ field, record }: { field: CrmFieldMeta; record: CrmRecord }) {
  const t = useT();
  if (!field.field_config?.ai_fill?.enabled) return null;
  const meta = (record.ai_meta as Record<string, AiFieldMeta> | undefined)?.[field.field_name];
  if (!meta) {
    return (
      <span
        className="ai-badge muted"
        title={t("record.aiField.pending")}
        style={{ fontSize: 11, opacity: 0.5, marginLeft: 4 }}
      >✨</span>
    );
  }
  return (
    <>
      <HoverTip content={aiBadgeContent(t, meta)}>
        <span
          className="ai-badge"
          style={{
            fontSize: 11, marginLeft: 4, cursor: "help",
            opacity: meta.abstained ? 0.45 : 0.9,
          }}
        >✨</span>
      </HoverTip>
      {!meta.abstained && <AiConfidenceDot meta={meta} />}
    </>
  );
}

function HeadlineChip({
  field, record, onClick,
}: {
  field: CrmFieldMeta;
  record: CrmRecord;
  onClick: () => void;
}) {
  const t = useT();
  const { locale } = useLocale();
  const label =
    headlineLabelOverride(field.field_name, t)
    || resolveFieldLabel(field, locale)
    || field.field_name;
  const { display } = renderFieldValue(t, field, record);
  const dash = t("rdetail.dash");
  const empty = !display || display === dash;
  const isPicklist = !empty && _isPicklistField(field);
  return (
    <button
      onClick={onClick}
      className="ghost kacrm-headline-card"
      title={t("rdetail.chip.title", { label })}
      style={{
        display: "flex", flexDirection: "column", alignItems: "flex-start",
        gap: 2, padding: "6px 12px", minWidth: 120,
        background: "var(--card-bg)",
        border: "1px solid var(--border)", borderRadius: 6,
        textAlign: "left",
      }}
    >
      <span className="muted" style={{ fontSize: 13, textTransform: "uppercase", letterSpacing: 0.3 }}>
        {label}
        {field.description && (
          <HoverTip content={field.description}>
            <span style={{ marginLeft: 4, opacity: 0.55, cursor: "help", textTransform: "none" }}>ⓘ</span>
          </HoverTip>
        )}
        <AiFieldBadge field={field} record={record} />
      </span>
      {isPicklist ? (() => {
        const accent = pickPicklistAccent(display);
        return (
          <span style={{
            fontSize: 14, fontWeight: 600,
            padding: "2px 9px", borderRadius: 999,
            background: accent.bg, color: `color-mix(in srgb, ${accent.fg} 88%, var(--fg-strong))`,
            border: `1px solid color-mix(in srgb, ${accent.fg} 24%, transparent)`,
            whiteSpace: "nowrap",
          }}>{display}</span>
        );
      })() : (
        <span style={{
          fontSize: 14, fontWeight: 500,
          color: empty ? "var(--muted)" : "inherit",
        }}>
          {empty ? dash : display}
        </span>
      )}
    </button>
  );
}

/**
 * 「系统记得的事」—— 在客户/联系人/线索/商机详情里，直接把 memory
 * 子图的前 N 条事实 + 相关人物拉出来。点击跳到 /memory/entity/:id
 * 看完整时间线与子图。
 *
 * 第一性原理：一个真实世界实体只有一个身份（memory_entities 行），
 * CRM 表只是从不同维度投影它。销售不需要知道「记忆库」这个词，
 * 打开客户就能看到 AI 为他们记住的东西。
 */
function MemoryPanel({ entityId }: { entityId: number }) {
  const t = useT();
  const { navigate } = useRouter();
  const [facts, setFacts] = useState<MemoryFact[] | null>(null);
  const [people, setPeople] = useState<MemoryEntity[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setErr(null);
      try {
        const [fRes, gRes] = await Promise.all([
          getEntityFacts(entityId, 20),
          getSubgraph(entityId, { depth: 1, maxEdges: 50 }),
        ]);
        if (cancelled) return;
        setFacts(fRes.data || []);
        const related = (gRes.data?.entities || [])
          .filter(e => e.id !== entityId)
          .slice(0, 6);
        setPeople(related);
      } catch (e) {
        if (!cancelled) {
          setErr(
            e instanceof ApiError
              ? t("rdetail.memory.load_failed_status", { status: String(e.status) })
              : t("rdetail.memory.load_failed"),
          );
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [entityId, t]);

  // Hide the entire card when nothing remembered — avoids a permanent empty
  // shell on fresh records. Once the ingestion pipeline fires it'll appear.
  if (!loading && !err && (!facts || facts.length === 0) && people.length === 0) {
    return null;
  }

  const topFacts = (facts || []).slice(0, 3);

  return (
    <div className="card" style={{ padding: 12 }}>
      <div className="row" style={{
        justifyContent: "space-between", alignItems: "center", marginBottom: 8,
      }}>
        <div style={{ fontSize: 14, fontWeight: 600 }}>
          {t("rdetail.memory.title")}
          <span className="muted small" style={{ marginLeft: 6, fontWeight: 400 }}>
            {t("rdetail.memory.subtitle")}
          </span>
        </div>
        <button
          className="ghost"
          style={{ fontSize: 14, padding: "2px 8px" }}
          onClick={() => navigate(`/memory/entity/${entityId}`)}
        >
          {t("rdetail.memory.view_full")}
        </button>
      </div>

      {loading && <div className="muted small">{t("common.loading")}</div>}
      {err && <div className="small" style={{ color: "var(--danger)" }}>{err}</div>}

      {!loading && !err && topFacts.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 6, marginBottom: people.length ? 10 : 0 }}>
          {topFacts.map(f => (
            <div key={f.id} style={{ fontSize: 14, lineHeight: 1.5 }}>
              <span style={{ marginRight: 6 }}>💡</span>
              {f.title ? (
                <span style={{ fontWeight: 500 }}>{f.title}:</span>
              ) : null}
              <span style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
                {f.content}
              </span>
            </div>
          ))}
          {facts && facts.length > topFacts.length && (
            <div className="muted small">
              {t("rdetail.memory.more", { n: facts.length - topFacts.length })}
            </div>
          )}
        </div>
      )}

      {!loading && !err && people.length > 0 && (
        <div>
          <div className="muted small" style={{ marginBottom: 4 }}>
            {t("rdetail.memory.related_count", { n: people.length })}
          </div>
          <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
            {people.map(p => (
              <button
                key={p.id}
                className="chip"
                onClick={() => navigate(`/memory/entity/${p.id}`)}
                title={t("rdetail.memory.chip_title", {
                  type: p.entity_type, canonical: p.canonical,
                })}
              >
                {p.entity_type === "person" ? "👤" : p.entity_type === "org" ? "🏢" : "·"}{" "}
                {p.canonical}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function CollapsibleCard({
  title, open, onToggle, children,
}: {
  title: string; open: boolean; onToggle: () => void; children: React.ReactNode;
}) {
  const t = useT();
  return (
    <div className="card" style={{ padding: open ? 14 : 8 }}>
      <button
        className="ghost" onClick={onToggle}
        style={{
          width: "100%", display: "flex", justifyContent: "space-between",
          alignItems: "center", padding: "4px 6px",
          fontSize: 14, fontWeight: 600,
        }}
      >
        <span>{title}</span>
        <span style={{ color: "var(--muted)", fontSize: 14 }}>
          {open ? t("rdetail.collapsible.hide") : t("rdetail.collapsible.show")}
        </span>
      </button>
      {open && <div style={{ marginTop: 10 }}>{children}</div>}
    </div>
  );
}

/** 来源文件 — 在本记录所属对话里上传、被 agent 用于录入此记录的原始
 *  图片/PDF。图片显缩略图,文档显文件名;点击经鉴权端点预览/下载。 */
function SourceFiles({
  module, recordId, refreshKey,
}: {
  module: string; recordId: number; refreshKey?: unknown;
}) {
  const t = useT();
  const [items, setItems] = useState<RecordAttachment[]>([]);
  useEffect(() => {
    let alive = true;
    listRecordAttachments(module, recordId)
      .then((rows) => { if (alive) setItems(rows); })
      .catch(() => { if (alive) setItems([]); });
    return () => { alive = false; };
  }, [module, recordId, refreshKey]);

  if (!items.length) return null;
  return (
    <div className="card" style={{ marginTop: 12 }}>
      <div className="muted small" style={{ marginBottom: 8 }}>
        📎 {t("rdetail.source_files")}
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 10 }}>
        {items.map((a) => {
          const href = `/api/attachments/${a.id}`;
          const isImage = a.kind === "image" || (a.mime_type || "").startsWith("image/");
          return (
            <a
              key={a.id}
              href={href}
              target="_blank"
              rel="noreferrer"
              title={a.filename}
              style={{
                display: "inline-flex", alignItems: "center", gap: 6,
                maxWidth: 220, textDecoration: "none",
              }}
            >
              {isImage ? (
                <img
                  src={href}
                  alt={a.filename}
                  style={{
                    width: 56, height: 56, objectFit: "cover",
                    borderRadius: 6, border: "1px solid var(--border, #ddd)",
                  }}
                />
              ) : (
                <span className="chip" style={{ overflow: "hidden", textOverflow: "ellipsis" }}>
                  📄 {a.filename}
                </span>
              )}
            </a>
          );
        })}
      </div>
    </div>
  );
}
