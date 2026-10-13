import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { CrmRecord, CrmFieldMeta, AiFieldMeta } from "@/lib/api";
import { updateRecord, listUsersPicker, listRecords, listModules, getEditFormExisting, listSubdetails, getRecord, ApiError } from "@/lib/api";
import AiConfidenceDot from "./AiConfidenceDot";
import HoverTip from "./HoverTip";
import type { SubdetailDef } from "@/lib/api";
import { toast } from "@/lib/toast";
import { useT, useLocale, enumLabel, type T } from "@/lib/i18n";
import {
  fieldLabel, moduleLabel, resolveFieldLabel,
  localizedSelectOptions, selectValueLabel,
} from "@/lib/crmLabels";
import { LineItemsEditor, type LineItem } from "./LineItemsEditor";
import { GenericSubdetailEditor } from "./GenericSubdetailEditor";
import { isPicklistLike, isMultiselectLike, isSelectLike } from "@/lib/fieldKinds";

/**
 * "全部信息" drawer — slides in from the right, covers the Copilot
 * column. Replaces the old "更多字段" schema-dump grid with a
 * business-grouped, two-column, empty-value-hiding read/edit panel.
 *
 * Charter §168 — user copy is "信息", not "字段". Drawer groups are
 * heuristic (no group_name column in DB yet), split into "系统信息"
 * (audit / ownership / timestamps) vs everything else.
 *
 * Inline edit: click a row → inline input → Enter/blur saves via
 * PUT /crm/records. Relation / user / department / boolean / date types
 * get specialized handling in Phase 2 — for now, text-edit only, and
 * an FK row offers a "让 Copilot 改这项" prefill button that hands off
 * to the Copilot panel with a natural-language stub.
 */

const SYSTEM_FIELD_NAMES = new Set([
  "createdtime", "modifiedtime", "created_time", "modified_time", "updated_time",
  "created_by", "modified_by", "owner_by", "updatedby", "createdby",
  "deleted", "assigned_group", "assigned_user",
]);

export function isSystemField(f: CrmFieldMeta): boolean {
  if (SYSTEM_FIELD_NAMES.has(f.field_name.toLowerCase())) return true;
  if (/(time|_at|_by)$/i.test(f.field_name)) return true;
  return false;
}

function fmtValue(t: T, field: CrmFieldMeta, record: CrmRecord, locale?: string): string {
  // Prefer *_display sibling (resolved FK / user / dept).
  const d = record[field.field_name + "_display"];
  if (d !== undefined && d !== null && String(d).trim() !== "") return String(d);
  const raw = record[field.field_name];
  if (raw === null || raw === undefined || raw === "") return "";
  if (field.field_type === "boolean" || field.field_type === "checkbox") {
    return raw ? t("allinfo.bool.true") : t("allinfo.bool.false");
  }
  // Select-like: map the stored value to its localized option label
  // (科技 not technology). multiSelect values are comma-joined.
  const ft = (field.field_type || "").toLowerCase();
  if (isSelectLike(ft)) {
    // 字段 options 未带本地化 label(如 approval_status 的 pending/approved)→
    // 回退通用枚举翻译层,与列表/详情一致。
    const m = selectValueLabel(raw, field.options, field.field_config, locale);
    return m === String(raw) ? enumLabel(t, raw) : m;
  }
  if (typeof raw === "object") return JSON.stringify(raw);
  return String(raw);
}

// The value an inline edit starts from AND is compared against on
// commit. Select-like editors emit the raw storage key ("finance"),
// not the localized label fmtValue produces ("金融"), so for those the
// baseline must be the raw value — otherwise the no-op guard never
// fires and re-picking the same option still triggers a write.
function editBaseline(t: T, field: CrmFieldMeta, record: CrmRecord, locale?: string): string {
  const ft = (field.field_type || "").toLowerCase();
  if (isSelectLike(ft)) {
    return String(record[field.field_name] ?? "");
  }
  return fmtValue(t, field, record, locale);
}

function canEditInline(f: CrmFieldMeta): boolean {
  if (f.is_readonly) return false;
  const ft = (f.field_type || "").toLowerCase();
  // Audit columns are system-managed; department is derived from
  // ownerby's user record. Never expose these as user-editable.
  if (["createdby", "updatedby", "department"].includes(ft)) return false;
  // Everything else (text/select/date/.../user/ownerby/relation) gets
  // a typed editor or picker.
  return true;
}

/** 后端产品行 → LineItem:保留自定义列(如 类型),核心数值字段做强制 coerce。 */
function rowToLineItem(p: any): LineItem {
  return {
    ...p,
    line_id: p.line_id,
    products_id: p.products_id,
    products_name: p.products_name,
    productno: p.productno,
    quantity: Number(p.quantity) || 0,
    price: Number(p.price?.amount ?? p.price ?? 0) || 0,
    discount_rate: Number(p.discount_rate) || 0,
    tax_rate: Number(p.tax_rate) || 0,
    remark: p.remark || "",
  };
}

export function AllInfoDrawer({
  open, onClose, module, recordId, record, fields, onUpdated, onAskCopilot,
}: {
  open: boolean;
  onClose: () => void;
  module: string;
  recordId: number;
  record: CrmRecord;
  fields: CrmFieldMeta[];
  onUpdated: () => void;
  onAskCopilot: (prefillText: string) => void;
}) {
  const t = useT();
  const { locale } = useLocale();
  const [editingName, setEditingName] = useState<string | null>(null);
  const [editingValue, setEditingValue] = useState<string>("");
  const [saving, setSaving] = useState(false);
  // Line items for quotes / salesorders. ``hasProducts`` is null
  // until the first /edit fetch resolves so the section doesn't
  // flash empty for modules that don't have products.
  const [hasProducts, setHasProducts] = useState<boolean | null>(null);
  const [lineItems, setLineItems] = useState<LineItem[]>([]);
  const [lineItemsDirty, setLineItemsDirty] = useState(false);
  const [savingProducts, setSavingProducts] = useState(false);
  // generic 子明细(产品行走上面 LineItemsEditor)
  const [productDef, setProductDef] = useState<SubdetailDef | null>(null);
  const [genericSubs, setGenericSubs] = useState<SubdetailDef[]>([]);
  const [detailRows, setDetailRows] = useState<Record<string, Record<string, unknown>[]>>({});
  const [detailsDirty, setDetailsDirty] = useState(false);
  const [savingDetails, setSavingDetails] = useState(false);
  // 每行字段列数 1 / 2,localStorage 持久化(默认 1)。
  const [cols, setCols] = useState<1 | 2>(() => {
    if (typeof window === "undefined") return 1;
    return window.localStorage.getItem("allinfo.cols") === "2" ? 2 : 1;
  });
  useEffect(() => {
    try { window.localStorage.setItem("allinfo.cols", String(cols)); } catch {}
  }, [cols]);
  // 全屏:产品明细这类宽表需要更大空间。
  const [expanded, setExpanded] = useState(false);

  // ESC to close.
  useEffect(() => {
    if (!open) return;
    const h = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [open, onClose]);

  // Fetch line items on open. We hit the existing /edit endpoint
  // (which already returns has_products + data.products) rather than
  // a dedicated /products route — one round-trip, no new backend.
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    getEditFormExisting(module, recordId).then(r => {
      if (cancelled) return;
      setHasProducts(r.has_products);
      setLineItems((r.products || []).map(rowToLineItem));
      setLineItemsDirty(false);
    }).catch(() => {
      if (!cancelled) setHasProducts(false);
    });
    // generic 子明细:schema(listSubdetails)+ 现有行(detail 的 details)
    (async () => {
      try {
        const sd = await listSubdetails(module);
        if (cancelled) return;
        const subs = (sd.subdetails || []).filter(s => s.detail_kind === "generic");
        setGenericSubs(subs);
        // 产品行字段元数据(供 LineItemsEditor 按字段管理配置渲染列)。
        setProductDef((sd.subdetails || []).find(s => s.detail_kind === "product") || null);
        if (subs.length) {
          const rec = await getRecord(module, recordId);
          if (cancelled) return;
          setDetailRows(rec.details || {});
        }
        setDetailsDirty(false);
      } catch { if (!cancelled) setGenericSubs([]); }
    })();
    return () => { cancelled = true; };
  }, [open, module, recordId]);

  if (!open) return null;

  // Empty values are still surfaced when the field is user-editable,
  // so the drawer doubles as a fill-in-the-blanks form. Empty
  // read-only *business* fields (e.g. 转化状态 / convertedstatus —
  // SPA-readonly but a real business attribute) are also surfaced, as
  // plain read-only rows, so "全部信息" actually shows the full record.
  // Only empty read-only *audit* columns (created_at / *_by …) stay
  // hidden to cut noise.
  const visible = fields.filter(f => !f.is_hidden);
  const shown = visible.filter(f => {
    const v = fmtValue(t, f, record, locale);
    if (v !== "" && v !== "—") return true;
    if (canEditInline(f)) return true;
    return !isSystemField(f);
  });
  const primary = shown.filter(f => !isSystemField(f))
    .sort((a, b) => (a.sort_order ?? 999) - (b.sort_order ?? 999));
  const system = shown.filter(f => isSystemField(f))
    .sort((a, b) => (a.sort_order ?? 999) - (b.sort_order ?? 999));

  async function save(field: CrmFieldMeta, value: string) {
    if (saving) return;
    const original = editBaseline(t, field, record, locale);
    if (value.trim() === original.trim()) {
      setEditingName(null);
      return;
    }
    setSaving(true);
    try {
      // Cast numeric / money fields — the server's Save layer coerces,
      // but sending a cleaned value avoids validation noise.
      let toSend: any = value;
      const ft = (field.field_type || "").toLowerCase();
      if (["number", "currency", "money", "decimal", "integer"].includes(ft)) {
        const cleaned = value.replace(/[,¥,\s万]/g, "");
        if (/^[-+]?\d+(\.\d+)?$/.test(cleaned)) {
          toSend = Number(cleaned);
          // "60万" → 600000. Restrictive: only plain "<n>万" at the end triggers.
          if (/万\s*$/.test(value)) toSend = Number(cleaned) * 10000;
        }
      }
      await updateRecord(module, recordId, { [field.field_name]: toSend });
      toast(
        t("allinfo.saved_toast", {
          label: field.field_config?.label_i18n?.[locale] || fieldLabel(t, field, module),
        }),
        "success",
      );
      setEditingName(null);
      onUpdated();
    } catch (e) {
      const msg = e instanceof ApiError
        ? (typeof e.detail === "object" && e.detail && (e.detail as any).message) || `HTTP ${e.status}`
        : String(e);
      toast(t("allinfo.save_failed_toast", { msg }), "error");
    } finally {
      setSaving(false);
    }
  }

  function renderRow(f: CrmFieldMeta) {
    const label = f.field_config?.label_i18n?.[locale] || fieldLabel(t, f, module);
    const value = fmtValue(t, f, record, locale);
    // AI 派生字段:展示置信度圆点 + 引用来源(hover),让用户在「全部信息」里
    // 就能看到 AI 值的可信度与依据,而不仅限于 headline chip。
    const aiMeta = (record.ai_meta as Record<string, AiFieldMeta> | undefined)?.[f.field_name];
    const isAi = !!f.field_config?.ai_fill?.enabled && !!aiMeta;
    const editable = canEditInline(f);
    const isEditing = editingName === f.field_name;
    // The inline editor opens on the raw stored value (e.g. a select's
    // storage key "finance"), NOT the localized display label — see
    // editBaseline.
    const editStart = editBaseline(t, f, record, locale);
    return (
      <div
        key={f.field_name}
        className="info-row"
        style={{
          display: "grid",
          gridTemplateColumns: "110px 1fr auto",
          gap: 8,
          padding: "6px 4px",
          borderBottom: "1px solid var(--border)",
          alignItems: "center",
          fontSize: 14,
          minHeight: 32,
        }}
      >
        <div className="muted small" style={{ fontSize: 13, display: "flex", alignItems: "center", gap: 2 }}>
          <span>{label}</span>
          {f.description && (
            <HoverTip content={f.description}>
              <span style={{ opacity: 0.55, cursor: "help" }}>ⓘ</span>
            </HoverTip>
          )}
          {isAi && (aiMeta!.abstained
            ? <HoverTip content={aiMeta!.reason || t("record.aiField.abstained")}>
                <span style={{ fontSize: 11, opacity: 0.6 }}>✨弃填</span>
              </HoverTip>
            : <HoverTip content={t("record.aiField.generated")}>
                <span style={{ fontSize: 11 }}>✨</span>
              </HoverTip>)}
          {isAi && !aiMeta!.abstained && <AiConfidenceDot meta={aiMeta} />}
        </div>
        <div
          style={{
            wordBreak: "break-word",
            // One-click edit: the whole value cell — including the empty
            // "—" placeholder — is the hit target. No separate 改 button.
            cursor: editable && !isEditing ? "pointer" : undefined,
          }}
          title={editable && !isEditing ? t("allinfo.btn.edit_title") : undefined}
          onClick={editable && !isEditing
            ? () => { setEditingName(f.field_name); setEditingValue(editStart); }
            : undefined}
        >
          {isEditing ? (
            <TypedInput
              field={f}
              value={editingValue}
              onChange={setEditingValue}
              onCommit={v => save(f, v)}
              onCancel={() => setEditingName(null)}
              disabled={saving}
            />
          ) : (
            <span style={value === "" ? { color: "var(--muted)", opacity: 0.5 } : undefined}>
              {value === "" ? "—" : value}
            </span>
          )}
        </div>
        <div style={{ display: "flex", gap: 4 }}>
          {/* Edit happens by clicking the value cell itself (or its
              empty "—" placeholder) — no separate 改 button. */}
          {!isEditing && (
            <button
              className="ghost"
              title={t("allinfo.btn.copilot_title")}
              onClick={() => onAskCopilot(t("allinfo.copilot.prefill", { label }))}
              style={{ fontSize: 13, padding: "2px 6px", opacity: 0.6 }}
            >🤖</button>
          )}
        </div>
      </div>
    );
  }

  return (
    <>
      {/* Backdrop — click to close. Covers Copilot column only (right 340px
          region) via z-index; main record detail stays visible left. */}
      <div
        onClick={onClose}
        style={{
          position: "fixed", inset: 0, background: "var(--overlay)",
          zIndex: 40,
        }}
      />
      <div
        role="dialog"
        aria-label={t("allinfo.title")}
        style={{
          position: "fixed", top: 0, right: 0, bottom: 0,
          width: expanded
            ? "96vw"
            : (hasProducts ? "min(1100px, 94vw)" : (cols === 2 ? 720 : 420)),
          maxWidth: "96vw",
          background: "var(--bg)",
          borderLeft: "1px solid var(--border)",
          boxShadow: "var(--shadow-modal)",
          zIndex: 50,
          display: "flex", flexDirection: "column",
        }}
      >
        <div style={{
          padding: "12px 16px",
          borderBottom: "1px solid var(--border)",
          display: "flex", justifyContent: "space-between", alignItems: "center",
        }}>
          <div>
            <div style={{ fontWeight: 600, fontSize: 14 }}>{t("allinfo.title")}</div>
            <div className="muted small" style={{ fontSize: 13 }}>
              {t("allinfo.sub", { module, recordId, n: shown.length })}
            </div>
          </div>
          <div style={{ display: "flex", gap: 0, alignItems: "center" }}>
            <button
              className="ghost"
              title={t(expanded ? "allinfo.fullscreen.exit" : "allinfo.fullscreen.enter")}
              aria-label={t(expanded ? "allinfo.fullscreen.exit" : "allinfo.fullscreen.enter")}
              onClick={() => setExpanded(v => !v)}
              style={{
                padding: 4,
                display: "inline-flex", alignItems: "center", justifyContent: "center",
                color: "var(--muted)",
              }}
            >
              {expanded ? (
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
                     stroke="currentColor" strokeWidth="2"
                     strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <path d="M9 9H4V4M4 9l5-5M15 9h5V4M20 9l-5-5M9 15H4v5M4 15l5 5M15 15h5v5M20 15l-5 5" />
                </svg>
              ) : (
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
                     stroke="currentColor" strokeWidth="2"
                     strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <path d="M4 9V4h5M4 4l6 6M20 9V4h-5M20 4l-6 6M4 15v5h5M4 20l6-6M20 15v5h-5M20 20l-6-6" />
                </svg>
              )}
            </button>
            <button
              className="ghost"
              title={t("allinfo.cols.tooltip")}
              aria-label={t("allinfo.cols.tooltip")}
              onClick={() => setCols(cols === 1 ? 2 : 1)}
              style={{
                padding: 4,
                display: "inline-flex", alignItems: "center", justifyContent: "center",
                color: "var(--muted)",
              }}
            >
              {cols === 1 ? (
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
                     stroke="currentColor" strokeWidth="2"
                     strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <rect x="3" y="4" width="18" height="6" rx="1" />
                  <rect x="3" y="14" width="18" height="6" rx="1" />
                </svg>
              ) : (
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
                     stroke="currentColor" strokeWidth="2"
                     strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <rect x="3" y="4" width="8" height="16" rx="1" />
                  <rect x="13" y="4" width="8" height="16" rx="1" />
                </svg>
              )}
            </button>
            <button
              className="ghost"
              onClick={onClose}
              aria-label={t("allinfo.close")}
              title={t("allinfo.close")}
              style={{
                padding: 4,
                display: "inline-flex", alignItems: "center", justifyContent: "center",
                color: "var(--muted)",
              }}
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
                   stroke="currentColor" strokeWidth="2"
                   strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <line x1="5" y1="5" x2="19" y2="19" />
                <line x1="5" y1="19" x2="19" y2="5" />
              </svg>
            </button>
          </div>
        </div>
        <div style={{ flex: 1, overflowY: "auto", padding: "8px 12px" }}>
          {primary.length > 0 && (
            <div style={{ marginBottom: 16 }}>
              <div className="muted small" style={{
                fontSize: 13, textTransform: "uppercase", letterSpacing: 0.5,
                marginBottom: 4, paddingLeft: 4,
              }}>{t("allinfo.group.primary")}</div>
              <div style={{
                display: "grid",
                gridTemplateColumns: cols === 2 ? "1fr 1fr" : "1fr",
                columnGap: 12,
              }}>
                {primary.map(renderRow)}
              </div>
            </div>
          )}
          {system.length > 0 && (
            <div>
              <div className="muted small" style={{
                fontSize: 13, textTransform: "uppercase", letterSpacing: 0.5,
                marginBottom: 4, paddingLeft: 4,
              }}>{t("allinfo.group.system")}</div>
              <div style={{
                display: "grid",
                gridTemplateColumns: cols === 2 ? "1fr 1fr" : "1fr",
                columnGap: 12,
              }}>
                {system.map(renderRow)}
              </div>
            </div>
          )}
          {primary.length === 0 && system.length === 0 && (
            <div className="muted" style={{ padding: 20, textAlign: "center", fontSize: 14 }}>
              {t("allinfo.empty")}
            </div>
          )}
          {hasProducts && (
            <div style={{ marginTop: 20 }}>
              <LineItemsEditor
                value={lineItems}
                onChange={(next) => { setLineItems(next); setLineItemsDirty(true); }}
                def={productDef}
              />
              {lineItemsDirty && (
                <div style={{ marginTop: 10, display: "flex", justifyContent: "flex-end", gap: 8 }}>
                  <button
                    className="ghost"
                    disabled={savingProducts}
                    onClick={async () => {
                      // Re-fetch to discard local edits.
                      const r = await getEditFormExisting(module, recordId);
                      setLineItems((r.products || []).map(rowToLineItem));
                      setLineItemsDirty(false);
                    }}
                    style={{ fontSize: 14, padding: "4px 10px" }}
                  >{t("allinfo.products.revert")}</button>
                  <button
                    className="primary"
                    disabled={savingProducts}
                    onClick={async () => {
                      setSavingProducts(true);
                      try {
                        await updateRecord(module, recordId, {
                          products: lineItems
                            .filter(li => li.products_id && (Number(li.quantity) || 0) > 0)
                            // 整行回传(含自定义列如 类型);剥本地/计算字段,服务端权威重算。
                            .map(li => {
                              const { line_id, subtotal, total, ...rest } = li;
                              void line_id; void subtotal; void total;
                              return { ...rest, discount_rate: li.discount_rate ?? 0, tax_rate: li.tax_rate ?? 0, remark: li.remark ?? "" };
                            }),
                        });
                        toast(t("allinfo.products.saved"), "success");
                        setLineItemsDirty(false);
                        onUpdated();
                      } catch (e) {
                        const msg = e instanceof ApiError
                          ? (typeof e.detail === "object" && e.detail && (e.detail as any).message) || `HTTP ${e.status}`
                          : String(e);
                        toast(t("allinfo.save_failed_toast", { msg }), "error");
                      } finally {
                        setSavingProducts(false);
                      }
                    }}
                    style={{ fontSize: 14, padding: "4px 14px" }}
                  >{savingProducts ? t("crm.new_record_saving") : t("allinfo.products.save")}</button>
                </div>
              )}
            </div>
          )}
          {genericSubs.map(sub => (
            <div key={sub.name} style={{ marginTop: 20 }}>
              <GenericSubdetailEditor
                def={sub}
                value={detailRows[sub.name] || []}
                onChange={rows => { setDetailRows(prev => ({ ...prev, [sub.name]: rows })); setDetailsDirty(true); }}
              />
            </div>
          ))}
          {genericSubs.length > 0 && detailsDirty && (
            <div style={{ marginTop: 10, display: "flex", justifyContent: "flex-end", gap: 8 }}>
              <button className="ghost" disabled={savingDetails}
                onClick={async () => {
                  const rec = await getRecord(module, recordId);
                  setDetailRows(rec.details || {}); setDetailsDirty(false);
                }} style={{ fontSize: 14, padding: "4px 10px" }}>
                {t("allinfo.subdetail.revert")}
              </button>
              <button className="primary" disabled={savingDetails}
                onClick={async () => {
                  setSavingDetails(true);
                  try {
                    const details: Record<string, Record<string, unknown>[]> = {};
                    for (const s of genericSubs) {
                      details[s.name] = (detailRows[s.name] || []).filter(
                        r => Object.values(r).some(v => v !== "" && v !== null && v !== undefined));
                    }
                    await updateRecord(module, recordId, { details });
                    toast(t("allinfo.subdetail.saved"), "success");
                    setDetailsDirty(false); onUpdated();
                  } catch (e) {
                    const msg = e instanceof ApiError ? `HTTP ${e.status}` : String(e);
                    toast(t("allinfo.save_failed_toast", { msg }), "error");
                  } finally { setSavingDetails(false); }
                }} style={{ fontSize: 14, padding: "4px 14px" }}>
                {savingDetails ? t("crm.new_record_saving") : t("allinfo.subdetail.save")}
              </button>
            </div>
          )}
        </div>
        <div style={{
          padding: "8px 16px",
          borderTop: "1px solid var(--border)",
          fontSize: 13, color: "var(--muted)",
        }}>
          {t("allinfo.footer")}
        </div>
      </div>
    </>
  );
}


/**
 * Type-aware inline editor. Different field_type values need different
 * controls — a pure ``<input>`` is fine for short text but wrong for
 * dates (needs picker), currency (number + tolerant parsing), select
 * (dropdown), textarea (multi-line), boolean (checkbox). This widget
 * dispatches on ``f.field_type`` and renders the right one.
 *
 * ``onChange`` updates the parent's staged value as the user types;
 * ``onCommit`` fires on blur or Enter (Ctrl+Enter for textarea) and
 * tells the parent to actually save. ``onCancel`` fires on Escape.
 *
 * Options source: for ``select``, ``f.options`` is a CSV string
 * (server seeds it that way); we split on comma and trim.
 */
function TypedInput({
  field, value, onChange, onCommit, onCancel, disabled,
}: {
  field: CrmFieldMeta;
  value: string;
  onChange: (v: string) => void;
  onCommit: (v: string) => void;
  onCancel: () => void;
  disabled?: boolean;
}) {
  const t = useT();
  const { locale } = useLocale();
  const ft = (field.field_type || "text").toLowerCase();
  const pickerRef = useRef<HTMLSelectElement | HTMLInputElement>(null);
  // Open the native picker the instant a select / date editor mounts, so
  // a picklist or date field is a true one-click edit. showPicker() needs
  // transient activation — the click that opened the editor supplies it.
  useEffect(() => {
    const el = pickerRef.current as
      (HTMLElement & { showPicker?: () => void }) | null;
    if (el && typeof el.showPicker === "function") {
      try { el.showPicker(); } catch { /* no transient activation — focus only */ }
    }
  }, []);
  // Clicking anywhere on a date/datetime input (not just the calendar
  // icon) opens the picker.
  const openNativePicker = (e: React.MouseEvent<HTMLElement>) => {
    const el = e.currentTarget as HTMLElement & { showPicker?: () => void };
    if (typeof el.showPicker === "function") {
      try { el.showPicker(); } catch { /* picker may already be open */ }
    }
  };
  const common = {
    autoFocus: true, disabled,
    style: { width: "100%", fontSize: 14, padding: "2px 6px" } as const,
  };
  const keyHandler = (multiline: boolean) =>
    (e: React.KeyboardEvent<HTMLElement>) => {
      if (e.key === "Escape") { e.preventDefault(); onCancel(); return; }
      if (e.key === "Enter") {
        // Textarea: only Ctrl/⌘+Enter commits, plain Enter = newline.
        if (multiline && !(e.metaKey || e.ctrlKey)) return;
        e.preventDefault();
        onCommit(value);
      }
    };

  // Select — dropdown of {value,text} options. ``options`` is raw CSV;
  // ``field_config.option_labels`` carries per-locale labels and is what
  // turns "won" into "赢单"/"Won" depending on the user's SPA locale.
  if (isPicklistLike(ft)) {
    const opts = localizedSelectOptions(field.options, field.field_config, locale);
    const optValues = opts.map(o => o.value);
    // If the stored value is non-empty but isn't in the canonical
    // options, surface it as a disabled red row so the user sees the
    // legacy/invalid value AND must pick something else before commit.
    // Without this the <select> renders blank, hiding the underlying
    // state and risking accidental clears (or backend reject of the
    // stale value).
    const trimmed = (value || "").trim();
    const isInvalidLegacy = trimmed !== "" && !optValues.includes(trimmed);
    return (
      <select
        {...common}
        ref={pickerRef as React.RefObject<HTMLSelectElement>}
        value={value}
        onChange={e => {
          const v = e.target.value;
          onChange(v);
          // Block commit when user picks the "invalid current" placeholder
          // (it's disabled but defensive) or when value still equals the
          // legacy non-conforming string.
          if (v && !optValues.includes(v)) return;
          onCommit(v);
        }}
        onBlur={() => {
          // Don't commit a still-invalid value on blur — keeps the
          // editor open so the user is forced to choose.
          if (isInvalidLegacy) return;
          onCommit(value);
        }}
        onKeyDown={keyHandler(false)}
        style={isInvalidLegacy
          ? { ...common.style, borderColor: "var(--danger)" }
          : common.style}
        title={isInvalidLegacy
          ? t("allinfo.select_invalid_value", { value: trimmed })
          : undefined}
      >
        <option value="">{t("allinfo.select_empty")}</option>
        {isInvalidLegacy && (
          <option value={trimmed} disabled style={{ color: "var(--danger)" }}>
            ⚠ {trimmed} ({t("allinfo.select_invalid_short")})
          </option>
        )}
        {opts.map(o => <option key={o.value} value={o.value}>{o.text}</option>)}
      </select>
    );
  }

  // Multi-select — checkbox list over the {value,text} options; the
  // stored value is a comma-joined list of selected keys. Staged via
  // onChange on each toggle, committed once when focus leaves the group
  // (committing per-toggle would fire a network write per checkbox).
  if (isMultiselectLike(ft)) {
    const opts = localizedSelectOptions(field.options, field.field_config, locale);
    const optValues = opts.map(o => o.value);
    const selected = new Set(
      (value || "").split(",").map(s => s.trim()).filter(Boolean),
    );
    // Legacy values absent from the canonical option list — surface them
    // so a commit can't silently drop a value the user can't see.
    const legacy = [...selected].filter(v => !optValues.includes(v));
    const toggle = (v: string, on: boolean) => {
      const next = new Set(selected);
      if (on) next.add(v); else next.delete(v);
      onChange([...next].join(","));
    };
    return (
      <div
        tabIndex={-1}
        onBlur={e => {
          if (!e.currentTarget.contains(e.relatedTarget as Node)) {
            onCommit(value);
          }
        }}
        onKeyDown={e => {
          if (e.key === "Escape") { e.preventDefault(); onCancel(); }
        }}
        style={{
          display: "flex", flexDirection: "column", gap: 2,
          maxHeight: 180, overflowY: "auto",
          border: "1px solid var(--border)", borderRadius: 4,
          padding: "4px 6px", background: "var(--panel)",
        }}
      >
        {opts.map((o, i) => (
          <label key={o.value}
            style={{ display: "flex", gap: 6, fontSize: 14, cursor: "pointer" }}>
            <input
              type="checkbox"
              autoFocus={i === 0}
              disabled={disabled}
              checked={selected.has(o.value)}
              onChange={e => toggle(o.value, e.target.checked)}
            />
            {o.text}
          </label>
        ))}
        {legacy.map(v => (
          <label key={v}
            style={{ display: "flex", gap: 6, fontSize: 14, cursor: "pointer",
                     color: "var(--danger)" }}>
            <input
              type="checkbox"
              disabled={disabled}
              checked
              onChange={() => toggle(v, false)}
            />
            ⚠ {v} ({t("allinfo.select_invalid_short")})
          </label>
        ))}
      </div>
    );
  }

  // Boolean — checkbox. Accept any truthy-looking string as checked.
  if (ft === "boolean" || ft === "checkbox" || ft === "bool") {
    const checked = value === "true" || value === "1" || value === "是";
    return (
      <input
        type="checkbox"
        autoFocus disabled={disabled}
        checked={checked}
        onChange={e => {
          const v = e.target.checked ? "true" : "false";
          onChange(v); onCommit(v);
        }}
      />
    );
  }

  // Date / datetime — native picker.
  if (ft === "date") {
    return (
      <input
        type="date" {...common}
        ref={pickerRef as React.RefObject<HTMLInputElement>}
        value={toDateInput(value)}
        onChange={e => onChange(e.target.value)}
        onClick={openNativePicker}
        onBlur={() => onCommit(value)}
        onKeyDown={keyHandler(false)}
      />
    );
  }
  if (ft === "datetime" || ft === "timestamp") {
    return (
      <input
        type="datetime-local" {...common}
        ref={pickerRef as React.RefObject<HTMLInputElement>}
        value={toDateTimeLocal(value)}
        onChange={e => onChange(e.target.value)}
        onClick={openNativePicker}
        onBlur={() => onCommit(value)}
        onKeyDown={keyHandler(false)}
      />
    );
  }

  // Number / currency — text input (we want to allow "60万" etc, the
  // save() wrapper parses it), but with inputMode=decimal for mobile
  // numeric keyboards.
  if (["number", "int", "integer", "decimal",
       "currency", "money", "amount", "percent"].includes(ft)) {
    return (
      <input
        {...common}
        inputMode="decimal"
        value={value}
        onChange={e => onChange(e.target.value)}
        onBlur={() => onCommit(value)}
        onKeyDown={keyHandler(false)}
      />
    );
  }

  // Email / url / phone — type hints give the right keyboard + validation.
  if (ft === "email") {
    return (
      <input type="email" {...common}
        value={value}
        onChange={e => onChange(e.target.value)}
        onBlur={() => onCommit(value)}
        onKeyDown={keyHandler(false)}
      />
    );
  }
  if (ft === "url") {
    return (
      <input type="url" {...common}
        value={value}
        onChange={e => onChange(e.target.value)}
        onBlur={() => onCommit(value)}
        onKeyDown={keyHandler(false)}
      />
    );
  }
  if (ft === "phone" || ft === "tel" || ft === "mobile") {
    return (
      <input type="tel" {...common}
        value={value}
        onChange={e => onChange(e.target.value)}
        onBlur={() => onCommit(value)}
        onKeyDown={keyHandler(false)}
      />
    );
  }

  // Long text — multi-line textarea. ``description`` / ``remark`` /
  // explicit textarea type.
  if (ft === "textarea" || ft === "longtext" || ft === "richtext" || ft === "text_multi") {
    return (
      <textarea
        {...common}
        rows={3}
        value={value}
        onChange={e => onChange(e.target.value)}
        onBlur={() => onCommit(value)}
        onKeyDown={keyHandler(true)}
        style={{ ...common.style, resize: "vertical", minHeight: 60 }}
      />
    );
  }

  // User / ownerby — searchable picker, commits user id (numeric string).
  if (ft === "user" || ft === "ownerby") {
    return (
      <UserPicker
        value={value}
        onChange={onChange}
        onCommit={onCommit}
        onCancel={onCancel}
        disabled={disabled}
        defaultOpen={true}
      />
    );
  }

  // Relation — modal picker over the target module's records.
  if (ft === "relation") {
    return (
      <RelationPicker
        field={field}
        value={value}
        onChange={onChange}
        onCommit={onCommit}
        onCancel={onCancel}
        disabled={disabled}
      />
    );
  }

  // Fallback — plain text.
  return (
    <input
      {...common}
      value={value}
      onChange={e => onChange(e.target.value)}
      onBlur={() => onCommit(value)}
      onKeyDown={keyHandler(false)}
    />
  );
}


/** "2026-04-17T10:34:00Z" / "2026-04-17 10:34" → "2026-04-17" for
 *  <input type="date">. Falls back to the raw string if we can't parse. */
function toDateInput(v: string): string {
  if (!v) return "";
  const m = v.match(/^\s*(\d{4}-\d{2}-\d{2})/);
  return m ? m[1] : v;
}

/** Accept "2026-04-17T10:34:00Z" / "2026-04-17 10:34" →
 *  "2026-04-17T10:34" for <input type="datetime-local">. */
function toDateTimeLocal(v: string): string {
  if (!v) return "";
  const m = v.match(/^\s*(\d{4}-\d{2}-\d{2})[T\s](\d{2}:\d{2})/);
  return m ? `${m[1]}T${m[2]}` : v;
}


/**
 * User picker — collapsed by default to a name chip; only opens the
 * search + grouped dropdown when the user clicks the chip. This keeps
 * the create-record form quiet at rest (where ``value`` is pre-seeded
 * to the current user) and avoids the embarrassment of a 100-user
 * list cracked open the second the modal mounts.
 *
 * ``defaultOpen`` — AllInfoDrawer's inline edit path passes true so
 * clicking "改" jumps straight into search mode (the user already
 * committed to editing). The create-modal path leaves it false so
 * the field reads like a read-only chip until clicked.
 */
export function UserPicker({
  value, onChange, onCommit, onCancel, disabled, defaultOpen = false, placeholder,
}: {
  value: string;
  onChange: (v: string) => void;
  onCommit: (v: string) => void;
  onCancel: () => void;
  disabled?: boolean;
  defaultOpen?: boolean;
  placeholder?: string;
}) {
  const t = useT();
  const placeholderText = placeholder ?? t("allinfo.user_picker_placeholder");
  const [query, setQuery] = useState("");
  // Single cache of users — driving both the chip's display label
  // (lookup by id) and the dropdown's results (filtered by query).
  // Hitting /crm/users/picker once on mount with a generous limit
  // covers tenants up to ~200 users; bigger orgs would need server
  // search, but the picker also re-fetches per-query so it works.
  const [cache, setCache] = useState<{ id: number; name: string; username?: string; department_name?: string }[]>([]);
  const [open, setOpen] = useState(defaultOpen);
  const [loading, setLoading] = useState(false);
  const anchorRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const popupRef = useRef<HTMLDivElement | null>(null);
  const [pos, setPos] = useState<{ left: number; top: number; width: number } | null>(null);

  // Initial load: ALL users (small org assumption). Used for the
  // collapsed-chip label lookup too, so we need it even when closed.
  useEffect(() => {
    let cancelled = false;
    listUsersPicker("", 200).then(rows => {
      if (!cancelled) setCache(rows);
    }).catch(() => {});
    return () => { cancelled = true; };
  }, []);

  // Open-mode debounced search — only re-queries the server when the
  // user types, otherwise reuses the initial cache.
  useEffect(() => {
    if (!open || query.trim() === "") return;
    let cancelled = false;
    setLoading(true);
    const handle = setTimeout(async () => {
      try {
        const rows = await listUsersPicker(query, 50);
        if (!cancelled) setCache(prev => mergeCache(prev, rows));
      } finally {
        if (!cancelled) setLoading(false);
      }
    }, 200);
    return () => { cancelled = true; clearTimeout(handle); };
  }, [query, open]);

  // Position the floating dropdown beneath the anchor. position:fixed
  // would normally suffice but we render through a portal anyway
  // because the modal uses transform (containing-block trap).
  useEffect(() => {
    if (!open) return;
    const recompute = () => {
      const r = (inputRef.current || anchorRef.current)?.getBoundingClientRect();
      if (r) setPos({ left: r.left, top: r.bottom + 2, width: r.width });
    };
    recompute();
    window.addEventListener("scroll", recompute, true);
    window.addEventListener("resize", recompute);
    return () => {
      window.removeEventListener("scroll", recompute, true);
      window.removeEventListener("resize", recompute);
    };
  }, [open]);

  // Click outside closes — same UX as a native select.
  useEffect(() => {
    if (!open) return;
    const onDocDown = (e: MouseEvent) => {
      const target = e.target as Node;
      if (anchorRef.current?.contains(target)) return;
      if (popupRef.current?.contains(target)) return;
      setOpen(false);
    };
    document.addEventListener("mousedown", onDocDown);
    return () => document.removeEventListener("mousedown", onDocDown);
  }, [open]);

  const current = value ? cache.find(u => String(u.id) === String(value)) : null;
  // Filter for the open dropdown: empty query → whole cache, otherwise
  // case-insensitive match on name / username.
  const filterQ = query.trim().toLowerCase();
  const results = filterQ
    ? cache.filter(u =>
        u.name.toLowerCase().includes(filterQ)
        || (u.username || "").toLowerCase().includes(filterQ))
    : cache;

  // Collapsed chip view — what the user sees by default in the create
  // form. Click → open the picker.
  if (!open) {
    const chipLabel = current
      ? `${current.name}${current.department_name ? ` · ${current.department_name}` : ""}`
      : value
        ? `#${value}`
        : placeholderText;
    return (
      <div ref={anchorRef} className="row" style={{ gap: 6, alignItems: "center" }}>
        <button
          type="button"
          disabled={disabled}
          onClick={() => setOpen(true)}
          style={{
            flex: 1, textAlign: "left",
            fontSize: 14, padding: "6px 10px",
            background: "transparent",
            border: "1px solid var(--border)", borderRadius: 4,
            color: current ? "var(--fg)" : "var(--muted)",
            cursor: disabled ? "not-allowed" : "pointer",
          }}
        >{chipLabel}</button>
        {value && (
          <button
            type="button"
            disabled={disabled}
            onClick={() => { onChange(""); onCommit(""); }}
            style={{ fontSize: 14, padding: "3px 8px", opacity: 0.55, background: "transparent", border: "none", cursor: "pointer" }}
            title={t("allinfo.picker_clear")}
          >×</button>
        )}
      </div>
    );
  }

  return (
    <div ref={anchorRef} style={{ position: "relative" }}>
      <input
        ref={inputRef}
        autoFocus
        disabled={disabled}
        value={query}
        placeholder={placeholderText}
        onChange={e => setQuery(e.target.value)}
        onKeyDown={e => {
          if (e.key === "Escape") { e.preventDefault(); setOpen(false); onCancel(); }
        }}
        style={{ width: "100%", fontSize: 14, padding: "4px 8px" }}
      />
      {open && pos && createPortal(
        <div ref={popupRef} style={{
          position: "fixed", left: pos.left, top: pos.top, width: pos.width,
          maxHeight: 280, overflowY: "auto",
          background: "var(--bg)", border: "1px solid var(--border)",
          boxShadow: "var(--shadow-popover)", borderRadius: 6,
          zIndex: 200,
        }}>
          <button
            type="button"
            onClick={() => { onChange(""); onCommit(""); }}
            style={pickerRowStyle}
          >
            <span style={{ opacity: 0.6 }}>{t("allinfo.picker_clear")}</span>
          </button>
          {loading && (
            <div style={{ ...pickerRowStyle, opacity: 0.6 }}>{t("allinfo.picker_loading")}</div>
          )}
          {!loading && results.length === 0 && (
            <div style={{ ...pickerRowStyle, opacity: 0.6 }}>{t("allinfo.picker_empty")}</div>
          )}
          {(() => {
            // Group by department so a manager scanning the list sees
            // her team clustered together — flat alphabetical lookup
            // gets noisy past ~10 users. Users with no dept fall into
            // the t("allinfo.picker_no_dept") bucket at the end.
            const noDept = t("allinfo.picker_no_dept");
            const groups = new Map<string, typeof results>();
            for (const u of results) {
              const k = u.department_name || noDept;
              if (!groups.has(k)) groups.set(k, []);
              groups.get(k)!.push(u);
            }
            const orderedKeys = [...groups.keys()].sort((a, b) => {
              if (a === noDept) return 1;
              if (b === noDept) return -1;
              return a.localeCompare(b, "zh-Hans-CN");
            });
            return orderedKeys.map(dept => (
              <div key={dept}>
                <div style={{
                  padding: "4px 10px 2px", fontSize: 13,
                  textTransform: "uppercase", letterSpacing: 0.5,
                  color: "var(--muted)", opacity: 0.7,
                  background: "transparent",
                  position: "sticky", top: 0,
                }}>{dept}</div>
                {groups.get(dept)!.map(u => (
                  <button
                    key={u.id}
                    type="button"
                    onClick={() => {
                      const v = String(u.id);
                      onChange(v); onCommit(v); setOpen(false);
                    }}
                    style={pickerRowStyle}
                  >
                    <span>{u.name}</span>
                    {u.username && u.username !== u.name && (
                      <span style={{ marginLeft: 8, opacity: 0.4, fontSize: 13 }}>
                        @{u.username}
                      </span>
                    )}
                  </button>
                ))}
              </div>
            ));
          })()}
        </div>,
        document.body,
      )}
    </div>
  );
  // value/disabled retained for parent staging; not currently surfaced
  // in the picker UI (search box drives selection).
  void value; void disabled;
}


/**
 * Modal picker over a related module's records. `field.datainfo` is
 * "<module_id>#<module_name>#…" — we use the module_name slug to call
 * the regular listRecords endpoint with a search query.
 */
export function RelationPicker({
  field, value, onChange, onCommit, onCancel, disabled,
}: {
  field: CrmFieldMeta;
  value: string;
  onChange: (v: string) => void;
  /** label is the picked record's display name; consumers (e.g. the
   *  create-record modal) cache it so they can show "ACME · #42"
   *  instead of just "#42" after the picker closes. */
  onCommit: (v: string, label?: string) => void;
  onCancel: () => void;
  disabled?: boolean;
}) {
  const t = useT();
  // Resolve the target module, authoritative source first:
  //  1. field_config.relation.target_module — set by the schema builder, so
  //     a second relation to the same module (e.g. accounts_id_2, whose name
  //     does NOT match the "<module>_id" convention) still picks correctly.
  //  2. datainfo "<module_id>#<module_name>#..." — admin-configured FK.
  //  3. the "<module>_id" naming convention (accounts_id, contacts_id, …).
  // Without this the picker degrades to a raw id input that ships the
  // display string back to the server.
  let targetModule = (field.field_config?.relation?.target_module || "").trim();
  if (!targetModule) {
    targetModule = ((field.datainfo || "").split("#")[1] || "").trim();
  }
  if (!targetModule) {
    const m = (field.field_name || "").match(/^(.+?)_id$/);
    if (m) targetModule = m[1];
  }
  const [moduleCache, setModuleCache] = useState<{ name: string; label: string }[] | null>(null);
  useEffect(() => {
    let cancelled = false;
    listModules().then(r => { if (!cancelled) setModuleCache(r.data); }).catch(() => {});
    return () => { cancelled = true; };
  }, []);
  const targetLabel = moduleLabel(targetModule, moduleCache);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<{ id: number; [k: string]: any }[]>([]);
  const [loading, setLoading] = useState(false);
  const debounceRef = useRef<number | null>(null);

  useEffect(() => {
    if (!targetModule) return;
    if (debounceRef.current) window.clearTimeout(debounceRef.current);
    setLoading(true);
    debounceRef.current = window.setTimeout(async () => {
      try {
        const r = await listRecords(targetModule, { search: query, page_size: 30 });
        setResults(r.data || []);
      } finally {
        setLoading(false);
      }
    }, 250);
    return () => { if (debounceRef.current) window.clearTimeout(debounceRef.current); };
  }, [targetModule, query]);

  if (!targetModule) {
    // No target module decoded — fall back to a raw id input so the
    // user can at least edit. Better than silently blocking the field.
    return (
      <input
        autoFocus disabled={disabled}
        value={value}
        onChange={e => onChange(e.target.value)}
        onBlur={() => onCommit(value)}
        onKeyDown={e => {
          if (e.key === "Escape") { e.preventDefault(); onCancel(); }
          if (e.key === "Enter") { e.preventDefault(); onCommit(value); }
        }}
        style={{ width: "100%", fontSize: 14, padding: "2px 6px" }}
        placeholder={t("allinfo.relation_id_placeholder")}
      />
    );
  }

  return (
    <>
      <div
        onClick={onCancel}
        style={{ position: "fixed", inset: 0, background: "var(--overlay)", zIndex: 70 }}
      />
      <div
        role="dialog"
        style={{
          position: "fixed", top: "20vh", left: "50%", transform: "translateX(-50%)",
          width: 420, maxWidth: "90vw", maxHeight: "60vh",
          background: "var(--bg)", border: "1px solid var(--border)",
          borderRadius: 8, boxShadow: "var(--shadow-modal)",
          zIndex: 80, display: "flex", flexDirection: "column",
        }}
      >
        <div style={{
          padding: "10px 12px", borderBottom: "1px solid var(--border)",
          display: "flex", justifyContent: "space-between", alignItems: "center",
        }}>
          <div style={{ fontWeight: 600, fontSize: 14 }}>
            {t("allinfo.relation_picker_title", { module: targetLabel })}
          </div>
          <button className="ghost icon-close" onClick={onCancel}>×</button>
        </div>
        <input
          autoFocus
          value={query}
          onChange={e => setQuery(e.target.value)}
          placeholder={t("allinfo.relation_search_placeholder")}
          style={{ margin: 10, padding: "4px 8px", fontSize: 14 }}
          onKeyDown={e => { if (e.key === "Escape") onCancel(); }}
        />
        <div style={{ flex: 1, overflowY: "auto", padding: "0 6px 8px" }}>
          <button
            type="button"
            onClick={() => { onChange(""); onCommit(""); }}
            style={{ ...pickerRowStyle, opacity: 0.65 }}
          >{t("allinfo.picker_clear")}</button>
          {loading && (
            <div style={{ ...pickerRowStyle, opacity: 0.6 }}>{t("allinfo.picker_loading")}</div>
          )}
          {!loading && results.length === 0 && (
            <div style={{ ...pickerRowStyle, opacity: 0.6 }}>{t("allinfo.picker_empty")}</div>
          )}
          {results.map(r => {
            const label = (r as any).name || (r as any).title || (r as any).subject
              || (r as any).accounts_name || (r as any).contacts_name
              || (r as any).leads_name || `#${r.id}`;
            return (
              <button
                key={r.id}
                type="button"
                onClick={() => {
                  const v = String(r.id);
                  onChange(v); onCommit(v, String(label));
                }}
                style={pickerRowStyle}
              >
                <span>{label}</span>
                <span style={{ marginLeft: 8, opacity: 0.5, fontSize: 13 }}>#{r.id}</span>
              </button>
            );
          })}
        </div>
      </div>
    </>
  );
}

/** De-dupe-on-id merge so search results extend the initial cache
 *  without losing the just-picked user when they fall outside the
 *  current query window. */
function mergeCache<T extends { id: number }>(prev: T[], next: T[]): T[] {
  const ids = new Set(prev.map(x => x.id));
  return [...prev, ...next.filter(x => !ids.has(x.id))];
}

const pickerRowStyle: React.CSSProperties = {
  display: "block", width: "100%", textAlign: "left",
  padding: "6px 10px", background: "transparent", border: "none",
  cursor: "pointer", fontSize: 14, color: "var(--fg)",
};
