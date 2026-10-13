import { useRef } from "react";
import { useT } from "@/lib/i18n";
import type { CrmFieldMeta } from "@/lib/api";
import { resolveFieldLabel } from "@/lib/crmLabels";
import { useLocale } from "@/lib/i18n";

/**
 * Formula editor sub-panel.
 *
 * Writes into ``field_config.formula``. The expression is a SAME-RECORD
 * arithmetic expression evaluated by the backend ``hermes/crm/formula.py``
 * recursive-descent evaluator (never ``eval``). This panel does NOT validate
 * the expression itself — syntax / unknown-field / non-numeric-field / cycle
 * errors are detected server-side on save and returned as ``saveError``,
 * which we render verbatim below the textarea.
 *
 * The field-reference inserter lists ONLY the module's numeric fields
 * (number / currency / formula) — those are the only references a formula
 * may legally use.
 */
export interface FormulaCfg {
  expression: string;
}

/** Field types whose columns are ``NUMERIC`` and thus formula-referenceable. */
const NUMERIC_TYPES = new Set(["number", "currency", "formula"]);

export function numericFields(fields: CrmFieldMeta[]): CrmFieldMeta[] {
  return fields.filter((f) =>
    NUMERIC_TYPES.has((f.field_type || "").toLowerCase()),
  );
}

export default function FormulaEditor({
  value,
  fields,
  saveError,
  selfFieldName,
  onChange,
}: {
  value: FormulaCfg | undefined;
  fields: CrmFieldMeta[];
  saveError: string | null;
  selfFieldName?: string;
  onChange: (next: FormulaCfg) => void;
}) {
  const t = useT();
  const { locale } = useLocale();
  const taRef = useRef<HTMLTextAreaElement | null>(null);
  const cfg: FormulaCfg = value ?? { expression: "" };

  // Exclude the field itself — a formula cannot reference its own column.
  const refs = numericFields(fields).filter(
    (f) => f.field_name !== selfFieldName,
  );

  /** Insert ``field_name`` at the caret (or append) and refocus. */
  function insertRef(fieldName: string) {
    const ta = taRef.current;
    const expr = cfg.expression;
    if (!ta) {
      onChange({ expression: expr + fieldName });
      return;
    }
    const start = ta.selectionStart ?? expr.length;
    const end = ta.selectionEnd ?? expr.length;
    const next = expr.slice(0, start) + fieldName + expr.slice(end);
    onChange({ expression: next });
    // Restore caret just after the inserted token on the next tick.
    requestAnimationFrame(() => {
      ta.focus();
      const pos = start + fieldName.length;
      ta.setSelectionRange(pos, pos);
    });
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div style={{ fontWeight: 600, fontSize: 14 }}>
        {t("admin.schema.formula.title")}
      </div>

      <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        <span className="muted small">{t("admin.schema.formula.expression")}</span>
        <textarea
          ref={taRef}
          value={cfg.expression}
          rows={4}
          placeholder={t("admin.schema.formula.placeholder")}
          spellCheck={false}
          style={{ fontFamily: "monospace", fontSize: 14, resize: "vertical" }}
          onChange={(e) => onChange({ expression: e.target.value })}
        />
      </label>

      <div>
        <div className="muted small" style={{ marginBottom: 4 }}>
          {t("admin.schema.formula.insert_field")}
        </div>
        {refs.length === 0 ? (
          <div className="muted small">{t("admin.schema.formula.no_numeric_fields")}</div>
        ) : (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {refs.map((f) => (
              <button
                key={f.field_name}
                type="button"
                className="ghost"
                style={{ fontSize: 14, padding: "2px 8px" }}
                title={f.field_name}
                onClick={() => insertRef(f.field_name)}
              >
                {resolveFieldLabel(f, locale)}
              </button>
            ))}
          </div>
        )}
      </div>

      <div className="muted small">{t("admin.schema.formula.hint")}</div>

      {saveError && (
        <div
          role="alert"
          style={{
            padding: "8px 10px",
            borderRadius: 6,
            background: "var(--danger-bg, #fef2f2)",
            color: "var(--danger, #b91c1c)",
            fontSize: 14,
          }}
        >
          <div style={{ fontWeight: 600 }}>
            {t("admin.schema.formula.error_title")}
          </div>
          <div style={{ marginTop: 2, fontFamily: "monospace" }}>{saveError}</div>
        </div>
      )}
    </div>
  );
}
