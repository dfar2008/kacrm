/**
 * NewSubdetailModal — 给某模块挂子明细(产品行 / 通用明细)的向导。
 *
 * 调 POST /crm/modules/{parent}/details(provision_subdetail)。子明细=带
 * parent_module_id 的子模块,有自己的物理表 + 字段,内联在父记录里。
 */
import { useState } from "react";
import { createSubdetail, ApiError, type NewFieldSpec } from "@/lib/api";
import { useT } from "@/lib/i18n";

const FIELD_TYPES = [
  "text", "textarea", "number", "currency", "date", "datetime",
  "picklist", "boolean", "email", "phone", "url",
];

export function NewSubdetailModal({
  parent, onClose, onCreated,
}: {
  parent: string;
  onClose: () => void;
  onCreated: (name: string) => void;
}) {
  const t = useT();
  const [name, setName] = useState("");
  const [title, setTitle] = useState("");
  const [titleEn, setTitleEn] = useState("");
  const [kind, setKind] = useState<"generic" | "product">("generic");
  const [fields, setFields] = useState<NewFieldSpec[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const nameOk = /^[a-z][a-z0-9_]*$/.test(name) && name.length <= 40;

  const addField = () =>
    setFields(f => [...f, { field_name: "", field_label: "", field_type: "text" }]);
  const setField = (i: number, patch: Partial<NewFieldSpec>) =>
    setFields(f => f.map((x, j) => (j === i ? { ...x, ...patch } : x)));
  const rmField = (i: number) => setFields(f => f.filter((_, j) => j !== i));

  const submit = async () => {
    if (!nameOk || !title.trim()) return;
    setBusy(true); setErr(null);
    try {
      const clean = fields.filter(f => /^[a-z][a-z0-9_]*$/.test(f.field_name) && f.field_label.trim());
      await createSubdetail(parent, {
        name, title: title.trim(),
        title_en: titleEn.trim() || undefined,
        detail_kind: kind,
        initial_fields: clean.length ? clean : undefined,
      });
      onCreated(name);
    } catch (e) {
      setErr(e instanceof ApiError
        ? (typeof e.detail === "string" ? e.detail : `HTTP ${e.status}`) : String(e));
    } finally { setBusy(false); }
  };

  return (
    <div role="dialog" aria-label={t("admin.schema.new_subdetail.title")}
      style={{ position: "fixed", inset: 0, zIndex: 1000, background: "var(--overlay)",
        display: "flex", alignItems: "center", justifyContent: "center" }}
      onClick={onClose}>
      <div onClick={e => e.stopPropagation()} style={{ width: 480, maxHeight: "85vh",
        overflowY: "auto", background: "var(--panel)",
        border: "1px solid var(--border)", borderRadius: 10, padding: 20 }}>
        <h3 style={{ marginTop: 0 }}>{t("admin.schema.new_subdetail.title")}</h3>
        <p className="muted" style={{ fontSize: 14, marginTop: 0 }}>
          {t("admin.schema.new_subdetail.hint", { parent })}
        </p>

        <label style={lbl}>{t("admin.schema.new_subdetail.kind")}
          <select value={kind} onChange={e => setKind(e.target.value as any)} style={inp(true)}>
            <option value="generic">{t("admin.schema.new_subdetail.kind_generic")}</option>
            <option value="product">{t("admin.schema.new_subdetail.kind_product")}</option>
          </select>
        </label>

        <label style={lbl}>{t("admin.schema.new_module.name")}
          <input value={name} onChange={e => setName(e.target.value.trim())}
            placeholder="expense_lines" style={inp(!name || nameOk)} />
          {!nameOk && name !== "" && <span style={hint}>{t("admin.schema.new_module.name_hint")}</span>}
        </label>

        <label style={lbl}>{t("admin.schema.new_module.title_zh")}
          <input value={title} onChange={e => setTitle(e.target.value)}
            placeholder="费用明细" style={inp(true)} />
        </label>

        <label style={lbl}>{t("admin.schema.new_module.title_en")}
          <input value={titleEn} onChange={e => setTitleEn(e.target.value)}
            placeholder="Expense lines" style={inp(true)} />
        </label>

        <div style={{ margin: "14px 0 6px", fontSize: 14, color: "var(--muted)" }}>
          {t("admin.schema.new_module.initial_fields")}
        </div>
        {fields.map((f, i) => (
          <div key={i} style={{ display: "flex", gap: 6, marginBottom: 6 }}>
            <input value={f.field_name} placeholder="field_name"
              onChange={e => setField(i, { field_name: e.target.value.trim() })}
              style={{ ...inp(true), flex: 1 }} />
            <input value={f.field_label} placeholder="标签"
              onChange={e => setField(i, { field_label: e.target.value })}
              style={{ ...inp(true), flex: 1 }} />
            <select value={f.field_type} onChange={e => setField(i, { field_type: e.target.value })}
              style={{ ...inp(true), width: 100 }}>
              {FIELD_TYPES.map(ty => <option key={ty} value={ty}>{ty}</option>)}
            </select>
            <button onClick={() => rmField(i)} style={btnGhost}>×</button>
          </div>
        ))}
        <button onClick={addField} style={btnGhost}>+ {t("admin.schema.new_module.add_field")}</button>

        {err && <div style={{ color: "var(--danger)", marginTop: 10, fontSize: 14 }}>{err}</div>}

        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 18 }}>
          <button onClick={onClose} style={btnGhost}>{t("common.cancel")}</button>
          <button onClick={submit} disabled={busy || !nameOk || !title.trim()}
            style={{ ...btnPrimary, opacity: busy || !nameOk || !title.trim() ? 0.5 : 1 }}>
            {busy ? "…" : t("admin.schema.new_module.create")}
          </button>
        </div>
      </div>
    </div>
  );
}

const lbl: React.CSSProperties = { display: "block", marginBottom: 12, fontSize: 14, color: "var(--muted)" };
const inp = (ok: boolean): React.CSSProperties => ({
  display: "block", width: "100%", marginTop: 4, padding: "7px 9px",
  background: "var(--bg)", color: "inherit",
  border: `1px solid ${ok ? "var(--border)" : "var(--danger)"}`,
  borderRadius: 6, fontSize: 14, boxSizing: "border-box",
});
const hint: React.CSSProperties = { color: "var(--danger)", fontSize: 13 };
const btnGhost: React.CSSProperties = {
  padding: "6px 12px", background: "transparent", color: "inherit",
  border: "1px solid var(--border)", borderRadius: 6, cursor: "pointer", fontSize: 14,
};
const btnPrimary: React.CSSProperties = {
  padding: "6px 14px", background: "var(--accent)", color: "var(--fg-on-accent)",
  border: "none", borderRadius: 6, cursor: "pointer", fontSize: 14,
};
