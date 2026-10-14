import { useEffect, useRef, useState } from "react";
import type { CrmFieldMeta } from "@/lib/api";
import { createRecord, getEditFormNew, listModules, listSubdetails, ApiError } from "@/lib/api";
import type { SubdetailDef } from "@/lib/api";
import { localizedSelectOptions } from "@/lib/crmLabels";
import { isPicklistLike, isMultiselectLike } from "@/lib/fieldKinds";
import { toast } from "@/lib/toast";
import { useT, useLocale } from "@/lib/i18n";
import { fieldLabel, moduleLabel } from "@/lib/crmLabels";
import { UserPicker, RelationPicker, isSystemField } from "./AllInfoDrawer";
import { LineItemsEditor, type LineItem } from "./LineItemsEditor";
import { GenericSubdetailEditor } from "./GenericSubdetailEditor";
import { CrmIcon } from "./CrmIcon";
import { createRecordValues, createValueErrors } from "@/lib/createRecordValues";
import "./create-record.css";

/**
 * Create-new-record modal. Loads the empty edit form for ``module``
 * via /crm/records/{module}/edit, renders a control per visible,
 * non-readonly field (audit + dept skipped — same rule as inline
 * edit), and posts the staged map to ``POST /crm/records/{module}``.
 *
 * Validation: required fields must be non-empty before the save
 * button enables. Field-level validation errors from the server
 * (400 with {field, message}) surface inline next to the offending
 * row; other errors go to the toast bus.
 */

function isCreatable(f: CrmFieldMeta): boolean {
  if (f.is_hidden) return false;
  if (f.is_readonly) return false;
  const ft = (f.field_type || "").toLowerCase();
  if (["createdby", "updatedby", "department"].includes(ft)) return false;
  // Skip server-managed audit columns by name too.
  const nm = (f.field_name || "").toLowerCase();
  if (["createdtime", "modifiedtime", "created_time", "modified_time",
       "created_by", "modified_by", "updatedby", "createdby",
       "deleted"].includes(nm)) return false;
  return true;
}

export function CreateRecordModal({
  module, moduleTitle, onClose, onCreated,
}: {
  module: string;
  moduleTitle?: string;
  onClose: () => void;
  onCreated: (newId: number) => void;
}) {
  const t = useT();
  const [fields, setFields] = useState<CrmFieldMeta[] | null>(null);
  const [values, setValues] = useState<Record<string, any>>({});
  const [errs, setErrs] = useState<Record<string, string>>({});
  const bodyRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!Object.keys(errs).length) return;
    const control = bodyRef.current?.querySelector<HTMLElement>(
      '[data-invalid="true"] input, [data-invalid="true"] select, [data-invalid="true"] textarea, [data-invalid="true"] button',
    );
    control?.focus({ preventScroll: true });
    control?.scrollIntoView({ block: "center", behavior: "auto" });
  }, [errs]);
  const [saving, setSaving] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  useEffect(() => {
    if (submitError && bodyRef.current) bodyRef.current.scrollTop = 0;
  }, [submitError]);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [hasProducts, setHasProducts] = useState(false);
  const [expanded, setExpanded] = useState(false); // 全屏(产品明细宽表)
  const [lineItems, setLineItems] = useState<LineItem[]>([]);
  const [productDef, setProductDef] = useState<SubdetailDef | null>(null);
  const [genericSubs, setGenericSubs] = useState<SubdetailDef[]>([]);
  const [detailRows, setDetailRows] = useState<Record<string, Record<string, unknown>[]>>({});
  const [moduleCache, setModuleCache] = useState<{ name: string; label: string }[] | null>(null);
  const moduleTitleLabel = moduleTitle || moduleLabel(module, moduleCache);

  useEffect(() => {
    let cancelled = false;
    listModules().then(r => { if (!cancelled) setModuleCache(r.data); }).catch(() => {});
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await getEditFormNew(module);
        if (cancelled) return;
        setFields(r.fields || []);
        setHasProducts(r.has_products);
        // Seed staged values with server defaults (owner_by, etc).
        if (r.defaults) setValues(prev => ({ ...r.defaults, ...prev }));
        // 拉本模块的 generic 子明细(产品行仍走 has_products/LineItemsEditor)。
        try {
          const sd = await listSubdetails(module);
          if (!cancelled) {
            setGenericSubs((sd.subdetails || []).filter(s => s.detail_kind === "generic"));
            // 产品行的字段元数据(供 LineItemsEditor 元数据驱动渲染列)。
            setProductDef((sd.subdetails || []).find(s => s.detail_kind === "product") || null);
          }
        } catch { /* 无子明细端点/无子明细 — 忽略 */ }
      } catch (e) {
        if (cancelled) return;
        const msg = e instanceof ApiError ? `HTTP ${e.status}` : String(e);
        setLoadErr(msg);
      }
    })();
    return () => { cancelled = true; };
  }, [module]);

  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);

  const creatableFields = (fields || []).filter(isCreatable)
    .sort((a, b) => (a.sort_order ?? 999) - (b.sort_order ?? 999));

  const missingRequired = creatableFields
    .filter(f => f.is_required)
    .filter(f => {
      const v = values[f.field_name];
      return v === undefined || v === null || String(v).trim() === "";
    });

  async function submit() {
    if (saving) return;
    setSubmitError(null);
    if (missingRequired.length > 0) {
      const m: Record<string, string> = {};
      missingRequired.forEach(f => { m[f.field_name] = t("crm.new_record_required"); });
      setErrs(m);
      return;
    }
    const valueErrors = createValueErrors(fields || [], values);
    if (Object.keys(valueErrors).length) {
      setErrs(Object.fromEntries(Object.keys(valueErrors).map(name => [name, t("crm.new_record_invalid_number")])));
      return;
    }
    setSaving(true);
    setErrs({});
    try {
      // Strip empty strings so the server's defaults / nullable cols apply.
      const payload = createRecordValues(fields || [], values);
      if (hasProducts) {
        // Strip empty / invalid rows — a row with no product picked
        // shouldn't make it to the server (Save would reject anyway).
        payload.products = lineItems
          .filter(li => li.products_id && (Number(li.quantity) || 0) > 0)
          // 发送整行(含字段管理里加的自定义列,如 类型);剥掉本地/计算字段,
          // subtotal/total/line_id 由服务端权威重算,不回传。
          .map(li => {
            const { line_id, subtotal, total, ...rest } = li;
            void line_id; void subtotal; void total;
            return { ...rest, discount_rate: li.discount_rate ?? 0, tax_rate: li.tax_rate ?? 0, remark: li.remark ?? "" };
          });
      }
      // generic 子明细 → details:{子明细名: 非空行}(后端 _save_subdetails 整组替换)
      const details: Record<string, Record<string, unknown>[]> = {};
      for (const sub of genericSubs) {
        const rows = (detailRows[sub.name] || []).filter(
          r => Object.values(r).some(v => v !== "" && v !== null && v !== undefined));
        if (rows.length) details[sub.name] = rows;
      }
      if (Object.keys(details).length) payload.details = details;
      const r = await createRecord(module, payload);
      toast(t("crm.new_record_success", { id: r.id }), "success");
      onCreated(r.id);
    } catch (e) {
      if (e instanceof ApiError && e.detail && typeof e.detail === "object") {
        const d = e.detail as any;
        if (d.field) {
          setErrs({ [d.field]: d.message || "invalid" });
          setSaving(false);
          return;
        }
      }
      const detail = e instanceof ApiError ? e.detail : null;
      const message = typeof detail === "string" ? detail
        : detail && typeof detail === "object" && "message" in detail ? String(detail.message) : "";
      const msg = e instanceof ApiError ? (message || `HTTP ${e.status}`) : String(e);
      const error = t("crm.new_record_failed", { msg });
      setSubmitError(error);
      toast(error, "error");
    } finally {
      setSaving(false);
    }
  }

  function setVal(name: string, v: any) {
    setValues(prev => ({ ...prev, [name]: v }));
    if (errs[name]) setErrs(prev => { const n = { ...prev }; delete n[name]; return n; });
  }

  // 与编辑抽屉一致:业务信息(primary)/系统信息(system)两组,两列网格。
  const primaryFields = creatableFields.filter(f => !isSystemField(f));
  const systemFields = creatableFields.filter(f => isSystemField(f));
  const renderField = (f: CrmFieldMeta) => (
    <FieldRow
      key={f.field_name}
      field={f}
      module={module}
      moduleCache={moduleCache}
      value={values[f.field_name] ?? ""}
      error={errs[f.field_name]}
      onChange={v => setVal(f.field_name, v)}
    />
  );
  return (
    <>
      {/* 右侧抽屉式(与编辑「全部信息」一致),不再居中弹屏。 */}
      <div onClick={onClose} style={{
        position: "fixed", inset: 0, background: "var(--overlay)", zIndex: 70,
      }} />
      <div role="dialog" aria-modal="true"
        className={`kacrm-create-drawer${expanded ? " is-expanded" : ""}`}
        aria-labelledby="kacrm-create-title"
        style={{
          position: "fixed", top: 0, right: 0, bottom: 0,
          width: expanded
            ? "96vw"
            : (hasProducts ? "min(1100px, 94vw)" : "min(760px, 96vw)"),
          maxWidth: "96vw",
          background: "var(--bg)", borderLeft: "1px solid var(--border)",
          boxShadow: "var(--shadow-modal)",
          zIndex: 80, display: "flex", flexDirection: "column",
        }}>
        <header className="kacrm-create-header">
          <div>
            <h2 id="kacrm-create-title">{t("crm.new_record_title", { module: moduleTitleLabel })}</h2>
            <p>{t("crm.new_record_form_hint")}</p>
          </div>
          <div style={{ display: "flex", gap: 0, alignItems: "center" }}>
            <button
              className="ghost"
              title={t(expanded ? "allinfo.fullscreen.exit" : "allinfo.fullscreen.enter")}
              aria-label={t(expanded ? "allinfo.fullscreen.exit" : "allinfo.fullscreen.enter")}
              onClick={() => setExpanded(v => !v)}
              style={{ padding: 4, display: "inline-flex", alignItems: "center",
                justifyContent: "center", color: "var(--muted)" }}
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
                   stroke="currentColor" strokeWidth="2"
                   strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                {expanded
                  ? <path d="M9 9H4V4M4 9l5-5M15 9h5V4M20 9l-5-5M9 15H4v5M4 15l5 5M15 15h5v5M20 15l-5 5" />
                  : <path d="M4 9V4h5M4 4l6 6M20 9V4h-5M20 4l-6 6M4 15v5h5M4 20l6-6M20 15v5h-5M20 20l-6-6" />}
              </svg>
            </button>
            <button className="ghost icon-close" onClick={onClose}
              aria-label={t("crm.new_record_cancel")} title={t("crm.new_record_cancel")}>
              <CrmIcon name="close" size={18} />
            </button>
          </div>
        </header>

        <div ref={bodyRef} className="kacrm-create-body">
          {submitError && <div role="alert" className="kacrm-create-submit-error">{submitError}</div>}
          {!fields && !loadErr && (
            <div className="muted small" style={{ padding: 12 }}>{t("common.loading")}</div>
          )}
          {loadErr && (
            <div className="small" style={{ color: "var(--danger)", padding: 12 }}>{loadErr}</div>
          )}
          {fields && primaryFields.length > 0 && (
            <section className="kacrm-create-section">
              <h3>{t("allinfo.group.primary")}</h3>
              <div className="kacrm-create-grid">{primaryFields.map(renderField)}</div>
            </section>
          )}
          {fields && systemFields.length > 0 && (
            <section className="kacrm-create-section is-system">
              <h3>{t("allinfo.group.system")}</h3>
              <div className="kacrm-create-grid">{systemFields.map(renderField)}</div>
            </section>
          )}
          {fields && hasProducts && (
            <div style={{ marginTop: 20 }}>
              <LineItemsEditor value={lineItems} onChange={setLineItems} def={productDef} />
            </div>
          )}
          {fields && genericSubs.map(sub => (
            <div key={sub.name} style={{ marginTop: 20 }}>
              <GenericSubdetailEditor
                def={sub}
                value={detailRows[sub.name] || []}
                onChange={rows => setDetailRows(prev => ({ ...prev, [sub.name]: rows }))}
              />
            </div>
          ))}
        </div>

        <footer className="kacrm-create-footer">
          <span>{t("crm.new_record_required_hint")}</span>
          <button className="ghost" onClick={onClose} disabled={saving}>
            {t("crm.new_record_cancel")}
          </button>
          <button className="primary" onClick={submit} disabled={saving || !fields}>
            {saving ? t("crm.new_record_saving") : t("crm.new_record_save")}
          </button>
        </footer>
      </div>
    </>
  );
}

function FieldRow({
  field, module, moduleCache, value, error, onChange,
}: {
  field: CrmFieldMeta;
  module: string;
  moduleCache: { name: string; label: string }[] | null;
  value: any;
  error?: string;
  onChange: (v: any) => void;
}) {
  const t = useT();
  const { locale } = useLocale();
  const label =
    field.field_config?.label_i18n?.[locale] || fieldLabel(t, field, module);
  return (
    <div data-invalid={error ? "true" : undefined} className={`kacrm-create-field${["textarea", "richtext"].includes((field.field_type || "").toLowerCase()) ? " is-wide" : ""}`}>
      <div className="kacrm-create-label">
        {label}
        {field.is_required && <span style={{ color: "var(--danger)", marginLeft: 4 }}>*</span>}
      </div>
      <div>
        <FieldControl field={field} label={label} error={error} moduleCache={moduleCache} value={String(value ?? "")} onChange={onChange} />
        {error && (
          <div id={`kacrm-create-error-${field.field_name}`} role="alert" className="small" style={{ color: "var(--danger)", fontSize: 13, marginTop: 2 }}>
            {error}
          </div>
        )}
      </div>
    </div>
  );
}

function FieldControl({
  field, label, error, moduleCache, value, onChange,
}: {
  field: CrmFieldMeta;
  label: string;
  error?: string;
  moduleCache: { name: string; label: string }[] | null;
  value: string;
  onChange: (v: any) => void;
}) {
  const t = useT();
  const { locale } = useLocale();
  const ft = (field.field_type || "text").toLowerCase();
  const controlA11y = {
    "aria-label": label, "aria-required": field.is_required || undefined,
    "aria-invalid": !!error,
    "aria-describedby": error ? `kacrm-create-error-${field.field_name}` : undefined,
  };
  const base: React.CSSProperties = { width: "100%", fontSize: 14, padding: "4px 8px" };

  if (isPicklistLike(ft)) {
    // Backend ships ``options`` as raw CSV plus a separate
    // ``field_config.option_labels`` map (per-locale display labels).
    // Without localizedSelectOptions the dropdown would render raw
    // values (initial_contact / won / lost) instead of "初步接触" /
    // "赢单" / "输单" — which was the SPA bug surfaced 2026-05-26.
    const opts = localizedSelectOptions(field.options, field.field_config, locale);
    return (
      <select {...controlA11y} value={value} onChange={e => onChange(e.target.value)} style={base}>
        <option value="">{t("allinfo.select_empty")}</option>
        {opts.map(o => <option key={o.value} value={o.value}>{o.text}</option>)}
      </select>
    );
  }
  if (isMultiselectLike(ft)) {
    // Checkbox group; the stored value is a comma-joined list of the
    // selected option keys — same storage contract as AllInfoDrawer's
    // inline multiselect editor.
    const opts = localizedSelectOptions(field.options, field.field_config, locale);
    const selected = new Set(value.split(",").map(s => s.trim()).filter(Boolean));
    const toggle = (v: string, on: boolean) => {
      const next = new Set(selected);
      if (on) next.add(v); else next.delete(v);
      onChange([...next].join(","));
    };
    return (
      <div style={{
        display: "flex", flexDirection: "column", gap: 2,
        maxHeight: 180, overflowY: "auto",
        border: "1px solid var(--border)", borderRadius: 4,
        padding: "4px 6px", background: "var(--panel)",
      }}>
        {opts.map(o => (
          <label key={o.value}
            style={{ display: "flex", gap: 6, fontSize: 14, cursor: "pointer" }}>
            <input aria-label={`${label}: ${o.text}`} type="checkbox" checked={selected.has(o.value)}
              onChange={e => toggle(o.value, e.target.checked)} />
            {o.text}
          </label>
        ))}
      </div>
    );
  }
  if (ft === "boolean" || ft === "checkbox" || ft === "bool") {
    const checked = value === "true" || value === "1" || value === "是";
    return (
      <label className="kacrm-create-checkbox">
        <input {...controlA11y} type="checkbox" checked={checked}
          onChange={e => onChange(e.target.checked ? "true" : "false")} />
        {t("allinfo.bool.true")}
      </label>
    );
  }
  if (ft === "date") {
    return <input {...controlA11y} type="date" style={base} value={value}
      onChange={e => onChange(e.target.value)} />;
  }
  if (ft === "datetime" || ft === "timestamp") {
    return <input {...controlA11y} type="datetime-local" style={base} value={value}
      onChange={e => onChange(e.target.value)} />;
  }
  if (["number", "int", "integer", "decimal",
       "currency", "money", "amount", "percent"].includes(ft)) {
    return <input {...controlA11y} inputMode="decimal" style={base} value={value}
      onChange={e => onChange(e.target.value)} />;
  }
  if (ft === "email") {
    return <input {...controlA11y} type="email" style={base} value={value}
      onChange={e => onChange(e.target.value)} />;
  }
  if (ft === "url") {
    return <input {...controlA11y} type="url" style={base} value={value}
      onChange={e => onChange(e.target.value)} />;
  }
  if (ft === "phone" || ft === "tel" || ft === "mobile") {
    return <input {...controlA11y} type="tel" style={base} value={value}
      onChange={e => onChange(e.target.value)} />;
  }
  if (ft === "textarea" || ft === "longtext" || ft === "richtext" || ft === "text_multi") {
    return <textarea {...controlA11y} rows={3} style={{ ...base, resize: "vertical", minHeight: 60 }}
      value={value} onChange={e => onChange(e.target.value)} />;
  }
  if (ft === "user" || ft === "ownerby") {
    return <UserPicker value={value} onChange={onChange}
      onCommit={onChange} onCancel={() => onChange("")} />;
  }
  if (ft === "relation") {
    // Inline relation editor inside the create form needs a different
    // shape than the modal-inside-modal RelationPicker; render a small
    // "pick…" button that opens the picker on demand.
    return <RelationField field={field} moduleCache={moduleCache} value={value} onChange={onChange} />;
  }
  return <input {...controlA11y} style={base} value={value} onChange={e => onChange(e.target.value)} />;
}

function RelationField({
  field, moduleCache, value, onChange,
}: {
  field: CrmFieldMeta;
  moduleCache: { name: string; label: string }[] | null;
  value: string;
  onChange: (v: string) => void;
}) {
  const t = useT();
  const [open, setOpen] = useState(false);
  // Cache the human-readable label for whatever id we last set so the
  // user can see WHAT they picked, not just "#42". RelationPicker
  // commits the bare id; we extend it here so the modal closes with
  // both id + label.
  const [picked, setPicked] = useState<{ id: string; label: string } | null>(null);
  const targetModule = ((field.datainfo || "").split("#")[1] || "").trim()
    || ((field.field_name || "").match(/^(.+?)_id$/)?.[1] || "?");
  const targetLabel = moduleLabel(targetModule, moduleCache);
  return (
    <div className="row" style={{ gap: 6, alignItems: "center" }}>
      {!value ? (
        // Empty: one clear, full-width "pick…" button. The old readonly
        // "记录 ID" box looked like a manual id field and confused users.
        <button className="ghost" type="button" onClick={() => setOpen(true)}
          style={{
            flex: 1, textAlign: "left", fontSize: 14, padding: "8px 12px",
            border: "1px dashed var(--border)", borderRadius: "var(--radius)",
            background: "transparent", color: "var(--muted)", cursor: "pointer",
          }}>
          ＋ {t("allinfo.relation_picker_title", { module: targetLabel })}
        </button>
      ) : (
        // Picked: show the human label (click to change) + clear button.
        <>
          <button type="button" onClick={() => setOpen(true)}
            title={t("allinfo.relation_picker_title", { module: targetLabel })}
            style={{
              flex: 1, textAlign: "left", fontSize: 14, padding: "8px 12px",
              border: "1px solid var(--border)", borderRadius: "var(--radius)",
              background: "var(--panel)", color: "inherit", cursor: "pointer",
            }}>
            {picked && picked.id === value ? picked.label : `#${value}`}
            <span className="muted" style={{ marginLeft: 8, fontSize: 12 }}>#{value}</span>
          </button>
          <button className="ghost" type="button"
            onClick={() => { onChange(""); setPicked(null); }}
            style={{ fontSize: 16, padding: "2px 8px", opacity: 0.6 }}>×</button>
        </>
      )}
      {open && (
        <RelationPicker
          field={field}
          value={value}
          onChange={onChange}
          onCommit={(v, label) => {
            onChange(v);
            if (v && label) setPicked({ id: v, label });
            else if (!v) setPicked(null);
            setOpen(false);
          }}
          onCancel={() => setOpen(false)}
        />
      )}
    </div>
  );
}
