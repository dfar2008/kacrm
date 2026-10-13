import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { listRecords } from "@/lib/api";
import type { SubdetailDef, SubdetailField } from "@/lib/api";
import { evalFormulaPreview } from "@/lib/formulaPreview";
import { useT, useLocale } from "@/lib/i18n";
import { localizedSelectOptions } from "@/lib/crmLabels";

/**
 * One product line on a quote / salesorder. Mirrors the columns
 * stored in ``{module}_products``. The editor is **metadata-driven**:
 * given the product sub-detail's field config (``def``), it renders
 * exactly the columns configured in 字段管理 (产品/类型/数量/单价/…),
 * so what an admin configures is what shows here. ``subtotal`` /
 * ``total`` are computed client-side for live feedback and recomputed
 * server-side on save (the server is the source of truth).
 *
 * Index signature lets custom product-line columns (e.g. a picklist
 * 类型 = field_picklist_1) round-trip through the row object.
 */
export interface LineItem {
  line_id?: number;            // present for existing rows; absent on new
  products_id: number | null;
  products_name?: string;
  productno?: string;
  quantity: number;
  price: number;
  discount_rate?: number;      // percent, 0..100
  tax_rate?: number;           // percent
  remark?: string;
  // Computed locally; server recomputes on save so these are not
  // round-tripped.
  subtotal?: number;
  total?: number;
  [key: string]: any;          // 额外可配置列(字段管理里加的自定义产品行字段)
}

interface Props {
  value: LineItem[];
  onChange: (next: LineItem[]) => void;
  /** 产品明细字段元数据;有则按配置列渲染(与字段管理一致),无则退回标准列。 */
  def?: SubdetailDef | null;
}

function computeRow(r: LineItem): LineItem {
  const qty = Number(r.quantity) || 0;
  const price = Number(r.price) || 0;
  const disc = Number(r.discount_rate) || 0;
  const tax = Number(r.tax_rate) || 0;
  const netPrice = price * (1 - disc / 100);
  const subtotal = netPrice * qty;
  const total = subtotal * (1 + tax / 100);
  return { ...r, subtotal, total };
}

function emptyRow(): LineItem {
  return { products_id: null, quantity: 1, price: 0, discount_rate: 0, tax_rate: 0 };
}

const COMPUTED_FIELDS = new Set(["subtotal", "total"]);
const NUMERIC_TYPES = ["number", "currency", "float", "int", "percent"];

/** 标准产品列(无 def 时的回退,保持历史行为)。 */
function defaultProductFields(t: ReturnType<typeof useT>): SubdetailField[] {
  return [
    { field_name: "products_id",   field_label: t("crm.line_items.col.product"),  field_type: "relation" },
    { field_name: "quantity",      field_label: t("crm.line_items.col.qty"),      field_type: "number" },
    { field_name: "price",         field_label: t("crm.line_items.col.price"),    field_type: "currency" },
    { field_name: "discount_rate", field_label: t("crm.line_items.col.discount"), field_type: "number" },
    { field_name: "tax_rate",      field_label: t("crm.line_items.col.tax"),      field_type: "number" },
    { field_name: "total",         field_label: t("crm.line_items.col.total"),    field_type: "currency", is_readonly: true },
  ];
}

type ColKind =
  | "product" | "product_name" | "subtotal" | "total"
  | "formula" | "readonly" | "picklist" | "boolean" | "number" | "text";

function colKind(f: SubdetailField): ColKind {
  const ft = (f.field_type || "").toLowerCase();
  if (f.field_name === "products_id") return "product";
  if (f.field_name === "products_name") return "product_name";
  if (f.field_name === "subtotal") return "subtotal";
  if (f.field_name === "total") return "total";
  if (ft === "formula") return "formula";
  if (f.is_readonly) return "readonly";
  if (ft === "picklist" || ft === "select") return "picklist";
  if (ft === "boolean") return "boolean";
  if (NUMERIC_TYPES.includes(ft)) return "number";
  return "text";
}

function colWidth(f: SubdetailField): string {
  const k = colKind(f);
  if (k === "product") return "minmax(150px, 2fr)";
  if (k === "product_name") return "minmax(110px, 1.4fr)";
  if (k === "picklist") return "minmax(90px, 1fr)";
  if (k === "boolean") return "60px";
  if (k === "text") {
    return (f.field_type || "").toLowerCase() === "textarea"
      ? "minmax(120px, 1.6fr)" : "minmax(100px, 1.2fr)";
  }
  return "88px"; // number / computed / formula / readonly
}

function colMinPx(f: SubdetailField): number {
  const k = colKind(f);
  if (k === "product") return 150;
  if (k === "product_name") return 110;
  if (k === "picklist") return 90;
  if (k === "boolean") return 60;
  if (k === "text") return (f.field_type || "").toLowerCase() === "textarea" ? 120 : 100;
  return 88;
}

function isRightAligned(f: SubdetailField): boolean {
  const k = colKind(f);
  return k === "number" || k === "subtotal" || k === "total" || k === "formula";
}

export function LineItemsEditor({ value, onChange, def }: Props) {
  const t = useT();
  const { locale } = useLocale();
  // Recompute on every render so subtotal/total stay live as the user
  // tweaks qty/price. The server recomputes on save (source of truth).
  const rows = value.map(computeRow);
  const grandTotal = rows.reduce((s, r) => s + (r.total || 0), 0);
  const grandQty = rows.reduce((s, r) => s + (r.quantity || 0), 0);

  const setRow = (i: number, patch: Partial<LineItem>) => {
    const next = value.slice();
    next[i] = { ...next[i], ...patch };
    onChange(next);
  };
  const removeRow = (i: number) => {
    const next = value.slice();
    next.splice(i, 1);
    onChange(next);
  };
  const addRow = () => onChange([...value, emptyRow()]);

  // 列 = 产品明细配置字段(隐藏 系统/父键/审计 列由后端已过滤);无 def 退回标准列。
  const cols: SubdetailField[] = (def?.fields && def.fields.length)
    ? def.fields : defaultProductFields(t);
  const GRID = cols.map(colWidth).join(" ") + " 36px";
  // 列多于抽屉宽时横向滚动:网格最小宽=各列最小宽之和(+删除列+间距)。
  const minW = cols.reduce((s, f) => s + colMinPx(f), 0) + 36 + 6 * cols.length;

  const renderCell = (f: SubdetailField, r: LineItem, i: number) => {
    const kind = colKind(f);
    const fn = f.field_name;
    switch (kind) {
      case "product":
        return (
          <ProductPicker
            value={r.products_id}
            label={r.products_name || ""}
            price={r.price}
            onChange={(id, name, price) => setRow(i, {
              products_id: id,
              products_name: name,
              ...(r.price === 0 && price != null ? { price } : {}),
            })}
          />
        );
      case "product_name":
        return <div style={roCell}>{r.products_name || "—"}</div>;
      case "subtotal":
        return <div style={roNum}>{(r.subtotal || 0).toFixed(2)}</div>;
      case "total":
        return <div style={roNum}>{(r.total || 0).toFixed(2)}</div>;
      case "formula": {
        const expr = f.field_config?.formula?.expression;
        const v = expr ? evalFormulaPreview(expr, r) : null;
        return <div style={roNum} title={expr || ""}>{v === null ? "" : v.toFixed(2)}</div>;
      }
      case "readonly":
        return <div style={roCell}>{String(r[fn] ?? "") || "—"}</div>;
      case "picklist":
        return (
          <select value={String(r[fn] ?? "")}
            onChange={e => setRow(i, { [fn]: e.target.value })}
            style={inputStyle("left")}>
            <option value="" />
            {localizedSelectOptions(f.options, f.field_config, locale).map(o =>
              <option key={o.value} value={o.value}>{o.text}</option>)}
          </select>
        );
      case "boolean":
        return <input type="checkbox" checked={!!r[fn]}
          onChange={e => setRow(i, { [fn]: e.target.checked })} />;
      case "number":
        return (
          <input type="number" step="any" value={r[fn] ?? 0}
            onChange={e => setRow(i, { [fn]: e.target.value === "" ? 0 : Number(e.target.value) })}
            style={inputStyle("right")} title={f.field_label} />
        );
      default: // text / textarea / email / url / date …
        return (
          <input type="text" value={String(r[fn] ?? "")}
            onChange={e => setRow(i, { [fn]: e.target.value })}
            style={inputStyle("left")} title={f.field_label} />
        );
    }
  };

  return (
    <div style={{
      marginTop: 14, border: "1px solid var(--border)", borderRadius: 6,
      background: "var(--surface-soft, transparent)",
    }}>
      <div style={{
        padding: "8px 12px", borderBottom: "1px solid var(--border)",
        display: "flex", justifyContent: "space-between", alignItems: "center",
        fontSize: 14, fontWeight: 600, color: "var(--muted)",
      }}>
        <span>{def?.title || t("crm.line_items.title")}</span>
        <span style={{ fontWeight: 400, opacity: 0.7 }}>
          {t("crm.line_items.summary", { qty: grandQty, total: grandTotal.toFixed(2) })}
        </span>
      </div>

      {/* 表头 + 行:列多时整体横向滚动 */}
      <div style={{ overflowX: "auto" }}>
        <div style={{ minWidth: minW }}>
          {/* Column headers — metadata-driven */}
          <div style={{
            display: "grid", gridTemplateColumns: GRID,
            gap: 6, padding: "6px 12px",
            fontSize: 13, color: "var(--muted)",
            borderBottom: "1px solid var(--border)",
          }}>
            {cols.map(f => (
              <div key={f.field_name}
                style={{ textAlign: isRightAligned(f) ? "right" : "left" }}>
                {f.field_label || f.field_name}
              </div>
            ))}
            <div />
          </div>

          {/* Rows */}
          {rows.length === 0 && (
            <div className="muted small" style={{ padding: 12, textAlign: "center" }}>
              {t("crm.line_items.empty")}
            </div>
          )}
          {rows.map((r, i) => (
            <div key={i} style={{
              display: "grid", gridTemplateColumns: GRID,
              gap: 6, padding: "6px 12px",
              borderBottom: i < rows.length - 1 ? "1px solid var(--border)" : "none",
              alignItems: "center", fontSize: 14,
            }}>
              {cols.map(f => (
                <div key={f.field_name} style={{ minWidth: 0 }}>{renderCell(f, r, i)}</div>
              ))}
              <button
                type="button"
                onClick={() => removeRow(i)}
                title={t("common.delete")}
                style={{
                  background: "transparent", border: "none",
                  color: "var(--muted)", cursor: "pointer", fontSize: 14,
                }}
              >×</button>
            </div>
          ))}
        </div>
      </div>

      <div style={{
        padding: "8px 12px", borderTop: "1px solid var(--border)",
        display: "flex", justifyContent: "space-between", alignItems: "center",
      }}>
        <button
          type="button"
          className="ghost"
          onClick={addRow}
          style={{ fontSize: 14, padding: "4px 10px" }}
        >＋ {t("crm.line_items.add")}</button>
        <div style={{ fontSize: 14, fontWeight: 600 }}>
          {t("crm.line_items.grand_total", { total: grandTotal.toFixed(2) })}
        </div>
      </div>
    </div>
  );
}

function inputStyle(align: "left" | "right" = "left"): React.CSSProperties {
  return {
    width: "100%", fontSize: 14, padding: "3px 6px",
    textAlign: align,
    fontVariantNumeric: align === "right" ? "tabular-nums" : "normal",
  };
}

const roCell: React.CSSProperties = {
  fontSize: 14, color: "var(--muted)", overflow: "hidden",
  textOverflow: "ellipsis", whiteSpace: "nowrap",
};
const roNum: React.CSSProperties = {
  textAlign: "right", fontVariantNumeric: "tabular-nums", color: "var(--muted)",
};


/**
 * Lightweight product picker — click chip → portal-rendered search
 * overlay. Same pattern as UserPicker but searches the ``products``
 * module via listRecords.
 */
function ProductPicker({
  value, label, price, onChange,
}: {
  value: number | null;
  label: string;
  /** Current price shown in the line — passed in so we can pre-fill
   *  the row with the product's catalog price when the user picks a
   *  fresh product and hasn't typed a price yet. */
  price: number;
  onChange: (id: number, name: string, price?: number) => void;
}) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const anchorRef = useRef<HTMLDivElement | null>(null);
  const popupRef = useRef<HTMLDivElement | null>(null);
  const [pos, setPos] = useState<{ left: number; top: number; width: number } | null>(null);
  void price;

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoading(true);
    const h = setTimeout(async () => {
      try {
        const r = await listRecords("products", { search: query, page_size: 30 });
        if (!cancelled) setResults(r.data || []);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }, 200);
    return () => { cancelled = true; clearTimeout(h); };
  }, [query, open]);

  useEffect(() => {
    if (!open) return;
    const recompute = () => {
      const r = anchorRef.current?.getBoundingClientRect();
      if (r) setPos({ left: r.left, top: r.bottom + 2, width: Math.max(r.width, 280) });
    };
    recompute();
    window.addEventListener("scroll", recompute, true);
    window.addEventListener("resize", recompute);
    return () => {
      window.removeEventListener("scroll", recompute, true);
      window.removeEventListener("resize", recompute);
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node;
      if (anchorRef.current?.contains(t)) return;
      if (popupRef.current?.contains(t)) return;
      setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  return (
    <div ref={anchorRef} style={{ position: "relative" }}>
      <button
        type="button"
        onClick={() => setOpen(true)}
        style={{
          width: "100%", textAlign: "left",
          fontSize: 14, padding: "4px 8px",
          background: "var(--bg)",
          border: "1px solid var(--border)", borderRadius: 4,
          color: value ? "var(--fg)" : "var(--muted)",
          cursor: "pointer",
        }}
      >
        {value ? (label || `#${value}`) : t("crm.line_items.pick_product")}
      </button>
      {open && pos && createPortal(
        <div ref={popupRef} style={{
          position: "fixed", left: pos.left, top: pos.top, width: pos.width,
          maxHeight: 300, overflowY: "auto",
          background: "var(--bg)", border: "1px solid var(--border)",
          boxShadow: "var(--shadow-popover)", borderRadius: 6,
          zIndex: 200,
        }}>
          <input
            autoFocus
            value={query}
            onChange={e => setQuery(e.target.value)}
            placeholder={t("crm.line_items.search_product")}
            style={{ width: "calc(100% - 16px)", margin: 8, padding: "4px 8px", fontSize: 14 }}
          />
          {loading && (
            <div style={{ padding: "6px 10px", opacity: 0.6, fontSize: 14 }}>
              {t("allinfo.picker_loading")}
            </div>
          )}
          {!loading && results.length === 0 && (
            <div style={{ padding: "6px 10px", opacity: 0.6, fontSize: 14 }}>
              {t("allinfo.picker_empty")}
            </div>
          )}
          {results.map((p: any) => (
            <button
              key={p.id}
              type="button"
              onClick={() => {
                const id = Number(p.id);
                const name = String(p.products_name || p.name || "");
                const catalogPrice = Number(p.price ?? p.reference_price ?? 0) || undefined;
                onChange(id, name, catalogPrice);
                setOpen(false);
              }}
              style={{
                display: "block", width: "100%", textAlign: "left",
                padding: "5px 10px", background: "transparent", border: "none",
                cursor: "pointer", fontSize: 14, color: "var(--fg)",
              }}
            >
              <div>{p.products_name || p.name}</div>
              <div style={{ fontSize: 13, opacity: 0.55 }}>
                {p.productno ? `${p.productno} · ` : ""}
                {p.price != null ? `¥${Number(p.price).toLocaleString()}` : ""}
                {p.stock != null ? ` · ${t("crm.line_items.stock")}: ${p.stock}` : ""}
              </div>
            </button>
          ))}
        </div>,
        document.body,
      )}
    </div>
  );
}
