/**
 * AiAutofill — "modifier switch" pattern for AI autofill (M1 redesign).
 *
 * Instead of a dedicated "ai_field" palette entry, AI is now a toggle that
 * lives on any compatible field type (text / number / currency / picklist /
 * multiselect). When enabled, the user picks one of 4 autofill modes, writes
 * a mission prompt (with variable insertion), and optionally configures
 * mode-specific extras (allow_new_options for classify).
 *
 * Backward-compatible: old configs without `mode` are displayed as "prompt".
 */
import { useEffect, useRef, useState } from "react";
import type { AiFillConf, CrmFieldMeta } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { aiModesForType } from "./fieldTypes";
import { aiFieldTemplates, type AiFieldTemplate } from "./aiFieldTemplates";

/** Canonical output bucket for matching a template to a field type. */
const _PICKLIST_TYPES = new Set(["picklist", "multiselect"]);
function outputCat(ft: string): "text" | "number" | "picklist" {
  if (_PICKLIST_TYPES.has(ft)) return "picklist";
  if (ft === "number" || ft === "currency") return "number";
  return "text";
}

// ── Styles ─────────────────────────────────────────────────────────────

const inputStyle: React.CSSProperties = {
  width: "100%", padding: "6px 8px", fontSize: 14,
  background: "var(--bg)", color: "inherit",
  border: "1px solid var(--border)", borderRadius: 6,
  boxSizing: "border-box",
};
const textareaStyle: React.CSSProperties = {
  ...inputStyle, minHeight: 72, resize: "vertical",
  fontFamily: "inherit", lineHeight: 1.4,
};
const labelStyle: React.CSSProperties = { fontSize: 13, color: "var(--muted)" };
const chkRow: React.CSSProperties = {
  display: "flex", alignItems: "center", gap: 6,
  fontSize: 14, cursor: "pointer",
};
const checkboxStyle: React.CSSProperties = {
  width: 14, height: 14, flexShrink: 0, margin: 0,
};

// ── Mode helpers ────────────────────────────────────────────────────────

type AiMode = "classify" | "summarize" | "web_agent" | "prompt";

/** Default AiFillConf with mode when the toggle is first enabled. */
export function defaultAiFillForType(fieldType: string): AiFillConf {
  const modes = aiModesForType(fieldType);
  const mode: AiMode = modes[0] ?? "prompt";
  return {
    enabled: true,
    mode,
    output_type: fieldTypeToOutputType(fieldType),
    mission: "",
    context_rules: "",
    inputs: { record: { enabled: true }, interactions: { enabled: false, limit: 20 } },
    tools: [],
    refresh: { on_create: true, on_change: true, schedule: null },
    min_confidence: 0,
  };
}

function fieldTypeToOutputType(
  ft: string,
): AiFillConf["output_type"] {
  if (_PICKLIST_TYPES.has(ft)) return "picklist";
  if (ft === "number" || ft === "currency") return "number";
  return "text";
}

// ── Variable inserter ───────────────────────────────────────────────────
//
// 三大分组：
//   1. 本模块字段 — 列出所有非系统字段
//   2. 互动派生    — 固定 4 项：最近/首次/次数/连接强度
//   3. 关系穿透    — 筛出 relation/user/department 类型的字段，
//                    选中后再输入目标字段名，插入 {关系字段 › 目标字段}

/** 固定互动派生变量列表（插入中文变量名，与后端 _derived_attrs 对齐）。 */
const DERIVED_VARS = [
  { key: "admin.schema.aiFill.var.last_interaction",    expr: "{最近互动}" },
  { key: "admin.schema.aiFill.var.first_interaction",   expr: "{首次互动}" },
  { key: "admin.schema.aiFill.var.interaction_count",   expr: "{互动次数}" },
  { key: "admin.schema.aiFill.var.connection_strength", expr: "{连接强度}" },
] as const;

/** 关系类字段类型。 */
const RELATION_TYPES = new Set(["relation", "user", "department"]);

const sectionLabelStyle: React.CSSProperties = {
  fontSize: 11, fontWeight: 600, color: "var(--muted)",
  padding: "6px 8px 2px", textTransform: "uppercase", letterSpacing: "0.05em",
};

function VariableInserter({
  fields,
  onInsert,
}: {
  fields: CrmFieldMeta[];
  onInsert: (varExpr: string) => void;
}) {
  const t = useT();
  const [open, setOpen] = useState(false);
  /** 当前正在选目标字段的关系字段，null = 未选中。 */
  const [relationField, setRelationField] = useState<CrmFieldMeta | null>(null);
  const [targetFieldName, setTargetFieldName] = useState("");
  const containerRef = useRef<HTMLDivElement>(null);

  const nonSystem = fields.filter((f) => !f.is_system);
  const relationFields = nonSystem.filter((f) => RELATION_TYPES.has(f.field_type));

  // 点击外部关闭
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (!containerRef.current?.contains(e.target as Node)) {
        setOpen(false);
        setRelationField(null);
        setTargetFieldName("");
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  function handleInsertRelation() {
    if (!relationField || !targetFieldName.trim()) return;
    const relLabel = relationField.display_label || relationField.field_label || relationField.field_name;
    onInsert(`{${relLabel} › ${targetFieldName.trim()}}`);
    setRelationField(null);
    setTargetFieldName("");
    setOpen(false);
  }

  return (
    <div ref={containerRef} style={{ position: "relative", display: "inline-block" }}>
      <button
        type="button"
        className="ghost"
        onClick={() => { setOpen((o) => !o); setRelationField(null); setTargetFieldName(""); }}
        style={{ fontSize: 13, padding: "3px 8px" }}
      >
        {"{}"}  {t("admin.schema.aiFill.insert_var")}
      </button>
      {open && (
        <div
          style={{
            position: "absolute", zIndex: 200, top: "calc(100% + 4px)", right: 0,
            background: "var(--panel)", border: "1px solid var(--border)",
            borderRadius: 8, boxShadow: "0 4px 16px rgba(0,0,0,0.12)",
            minWidth: 240, maxWidth: 360, maxHeight: 360, overflowY: "auto", overflowX: "hidden", padding: 4,
          }}
        >
          {/* ── 关系穿透子面板 ── */}
          {relationField && (
            <div style={{ padding: "6px 8px", display: "flex", flexDirection: "column", gap: 6 }}>
              <div style={{ fontSize: 13, fontWeight: 500 }}>
                {relationField.display_label || relationField.field_label || relationField.field_name}
                {" › "}
              </div>
              <input
                autoFocus
                type="text"
                value={targetFieldName}
                onChange={(e) => setTargetFieldName(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") handleInsertRelation(); }}
                placeholder={t("admin.schema.aiFill.relation_target_ph")}
                style={{
                  width: "100%", padding: "5px 8px", fontSize: 13,
                  background: "var(--bg)", color: "inherit",
                  border: "1px solid var(--border)", borderRadius: 5,
                  boxSizing: "border-box",
                }}
              />
              <div style={{ display: "flex", gap: 6 }}>
                <button
                  type="button"
                  className="ghost"
                  onClick={() => { setRelationField(null); setTargetFieldName(""); }}
                  style={{ fontSize: 12, padding: "3px 8px" }}
                >{t("common.back")}</button>
                <button
                  type="button"
                  className="ghost"
                  onClick={handleInsertRelation}
                  disabled={!targetFieldName.trim()}
                  style={{
                    fontSize: 12, padding: "3px 8px",
                    color: targetFieldName.trim() ? "var(--accent)" : "var(--muted)",
                  }}
                >{t("admin.schema.aiFill.relation_insert")}</button>
              </div>
            </div>
          )}

          {/* ── 主列表 ── */}
          {!relationField && (
            <>
              {/* 分组1：本模块字段 */}
              <div style={sectionLabelStyle}>{t("admin.schema.aiFill.var_section.fields")}</div>
              {nonSystem.length === 0 && (
                <div className="muted" style={{ fontSize: 13, padding: "6px 8px" }}>
                  {t("admin.schema.aiFill.no_vars")}
                </div>
              )}
              {nonSystem.map((f) => {
                const label = f.display_label || f.field_label || f.field_name;
                return (
                  <button
                    key={f.field_name}
                    type="button"
                    className="ghost"
                    onClick={() => {
                      onInsert(`{${label}}`);
                      setOpen(false);
                    }}
                    style={{
                      width: "100%", textAlign: "left",
                      padding: "5px 8px", fontSize: 13,
                      display: "flex", flexDirection: "column", gap: 1,
                    }}
                  >
                    <span>{label}</span>
                    <span className="muted" style={{ fontSize: 12 }}>{f.field_name}</span>
                  </button>
                );
              })}

              {/* 分组2：互动派生 */}
              <div style={{ height: 1, background: "var(--border)", margin: "4px 0" }} />
              <div style={sectionLabelStyle}>{t("admin.schema.aiFill.var_section.derived")}</div>
              {DERIVED_VARS.map(({ key, expr }) => (
                <button
                  key={key}
                  type="button"
                  className="ghost"
                  onClick={() => {
                    onInsert(expr);
                    setOpen(false);
                  }}
                  style={{
                    width: "100%", textAlign: "left",
                    padding: "5px 8px", fontSize: 13,
                  }}
                >
                  <span>{t(key)}</span>
                  <span className="muted" style={{ fontSize: 11, marginLeft: 6 }}>{expr}</span>
                </button>
              ))}

              {/* 分组3：关系穿透（只有有关系字段时才显示） */}
              {relationFields.length > 0 && (
                <>
                  <div style={{ height: 1, background: "var(--border)", margin: "4px 0" }} />
                  <div style={sectionLabelStyle}>{t("admin.schema.aiFill.var_section.relations")}</div>
                  {relationFields.map((f) => {
                    const label = f.display_label || f.field_label || f.field_name;
                    return (
                      <button
                        key={f.field_name}
                        type="button"
                        className="ghost"
                        onClick={() => { setRelationField(f); setTargetFieldName(""); }}
                        style={{
                          width: "100%", textAlign: "left",
                          padding: "5px 8px", fontSize: 13,
                          display: "flex", alignItems: "center", justifyContent: "space-between",
                        }}
                      >
                        <span>{label}</span>
                        <span className="muted" style={{ fontSize: 12 }}>›</span>
                      </button>
                    );
                  })}
                </>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}

// ── Main component ──────────────────────────────────────────────────────

export interface AiAutofillProps {
  /** The field's real type (text / number / currency / picklist / multiselect). */
  fieldType: string;
  /** Current value of field_config.ai_fill (undefined = not configured). */
  value: AiFillConf | undefined;
  /** All fields in the current module (for context field checklist + var picker). */
  fields: CrmFieldMeta[];
  onChange: (next: AiFillConf | undefined) => void;
  /** Draft-only: change the field's SQL output type (text/number/currency).
   *  Present → renders the 输出格式 selector for web_agent/prompt modes (Attio:
   *  pick mode first, then output format). Absent (persisted field) → type is
   *  locked (changing it would be a column migration). */
  onOutputTypeChange?: (ft: "text" | "number" | "currency") => void;
}

export function AiAutofill({ fieldType, value, fields, onChange, onOutputTypeChange }: AiAutofillProps) {
  const t = useT();
  const modes = aiModesForType(fieldType);
  const missionRef = useRef<HTMLTextAreaElement>(null);

  // Enabled state: driven by whether `value` is present and enabled.
  const enabled = !!(value && value.enabled !== false);

  // Merge stored value with defaults; treat missing `mode` as "prompt" (compat).
  const v: AiFillConf = value
    ? { ...defaultAiFillForType(fieldType), ...value, mode: value.mode ?? "prompt" }
    : defaultAiFillForType(fieldType);

  const currentMode: AiMode = (v.mode as AiMode) ?? "prompt";

  function set(patch: Partial<AiFillConf>) {
    onChange({ ...v, ...patch, enabled: true });
  }

  function handleToggle(on: boolean) {
    if (on) {
      onChange(defaultAiFillForType(fieldType));
    } else {
      onChange(undefined);
    }
  }

  function handleModeChange(mode: AiMode) {
    // Switching mode resets allow_new_options when leaving classify.
    set({ mode, allow_new_options: mode === "classify" ? v.allow_new_options : undefined });
  }

  // Ready-made prompt templates for THIS mode + output type — gives the user a
  // worked example of mission/output_spec instead of blank boxes (ported from
  // the retired AI-field template library).
  const templates = aiFieldTemplates(t).filter(
    (tpl) => tpl.mode === currentMode && tpl.output_type === outputCat(fieldType),
  );

  function applyTemplate(tpl: AiFieldTemplate) {
    set({
      mode: tpl.mode,
      mission: tpl.mission,
      context_rules: tpl.context_rules,
      allow_new_options: tpl.mode === "classify" ? v.allow_new_options : undefined,
      inputs: tpl.with_interactions
        ? { ...v.inputs, interactions: { ...(v.inputs?.interactions ?? { limit: 20 }), enabled: true } }
        : v.inputs,
    });
  }

  function insertVariable(expr: string) {
    const ta = missionRef.current;
    if (ta) {
      const start = ta.selectionStart ?? v.mission.length;
      const end = ta.selectionEnd ?? start;
      const next = v.mission.slice(0, start) + expr + v.mission.slice(end);
      set({ mission: next });
      // Restore cursor after React re-render.
      requestAnimationFrame(() => {
        ta.selectionStart = ta.selectionEnd = start + expr.length;
        ta.focus();
      });
    } else {
      set({ mission: v.mission + expr });
    }
  }

  // ── Render ────────────────────────────────────────────────────────────

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      {/* Toggle header */}
      <label style={{
        display: "flex", alignItems: "center", gap: 8,
        fontSize: 14, cursor: "pointer",
        padding: "8px 10px",
        background: enabled ? "color-mix(in srgb, var(--accent) 8%, transparent)" : "var(--bg)",
        border: `1px solid ${enabled ? "var(--accent)" : "var(--border)"}`,
        borderRadius: 8,
      }}>
        <input
          type="checkbox"
          checked={enabled}
          style={{ ...checkboxStyle, accentColor: "var(--accent)" }}
          onChange={(e) => handleToggle(e.target.checked)}
        />
        <span style={{ fontWeight: 500 }}>
          ✨ {t("admin.schema.aiFill.toggle_label")}
        </span>
      </label>

      {/* Config panel — only shown when enabled */}
      {enabled && (
        <>
          {/* Autofill type selector */}
          {modes.length > 1 && (
            <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              <span style={labelStyle}>{t("admin.schema.aiFill.mode_label")}</span>
              <select
                value={currentMode}
                onChange={(e) => handleModeChange(e.target.value as AiMode)}
                style={inputStyle}
              >
                {modes.map((m) => (
                  <option key={m} value={m}>
                    {t(`admin.schema.aiFill.mode.${m}`)}
                  </option>
                ))}
              </select>
              <small className="muted">
                {t(`admin.schema.aiFill.mode_hint.${currentMode}`)}
              </small>
            </label>
          )}
          {/* 输出格式 — only for web_agent/prompt (which support text/number/
              currency) and only on drafts (onOutputTypeChange provided). */}
          {onOutputTypeChange
            && (currentMode === "web_agent" || currentMode === "prompt") && (
            <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              <span style={labelStyle}>{t("admin.schema.aiFill.output_format_label")}</span>
              <select
                value={fieldType === "currency" ? "currency" : fieldType === "number" ? "number" : "text"}
                onChange={(e) => onOutputTypeChange(e.target.value as "text" | "number" | "currency")}
                style={inputStyle}
              >
                <option value="text">{t("admin.schema.aiFill.output_format.text")}</option>
                <option value="number">{t("admin.schema.aiFill.output_format.number")}</option>
                <option value="currency">{t("admin.schema.aiFill.output_format.currency")}</option>
              </select>
            </label>
          )}
          {/* Single mode: just show label + hint */}
          {modes.length === 1 && (
            <div style={{
              fontSize: 13, padding: "6px 8px",
              background: "var(--bg)", border: "1px solid var(--border)",
              borderRadius: 6, color: "var(--muted)",
            }}>
              {t(`admin.schema.aiFill.mode.${modes[0]}`)}
              {" — "}{t(`admin.schema.aiFill.mode_hint.${modes[0]}`)}
            </div>
          )}

          {/* 参考模板 — prefills mission/output_spec for this mode from a worked
              example, so the user knows how to write the prompt. */}
          {templates.length > 0 && (
            <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              <span style={labelStyle}>💡 {t("admin.schema.aiFill.template")}</span>
              <select
                value=""
                onChange={(e) => {
                  const tpl = templates.find((x) => x.key === e.target.value);
                  if (tpl) applyTemplate(tpl);
                }}
                style={inputStyle}
              >
                <option value="">{t("admin.schema.aiFill.template_none")}</option>
                {templates.map((tpl) => (
                  <option key={tpl.key} value={tpl.key}>{tpl.label}</option>
                ))}
              </select>
            </label>
          )}

          {/* Mission / prompt textarea with variable inserter */}
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
              <span style={labelStyle}>{t("admin.schema.aiFill.mission")}</span>
              <VariableInserter fields={fields} onInsert={insertVariable} />
            </div>
            <textarea
              ref={missionRef}
              value={v.mission}
              onChange={(e) => set({ mission: e.target.value })}
              placeholder={t(`admin.schema.aiFill.ph.mission_${currentMode}`)}
              style={textareaStyle}
            />
            <small className="muted">{t("admin.schema.aiFill.help.mission")}</small>
          </div>
          {/* 产出规格已退役:输出形状由 模式+字段类型+选项 自动决定,AI 自己判断,
              不再让用户手写(对齐 Attio——其 AI 属性没有产出规格框)。 */}

          {/* classify-only: allow_new_options */}
          {currentMode === "classify" && (
            <label style={chkRow}>
              <input
                type="checkbox"
                checked={!!v.allow_new_options}
                style={checkboxStyle}
                onChange={(e) => set({ allow_new_options: e.target.checked })}
              />
              {t("admin.schema.aiFill.allow_new_options")}
            </label>
          )}

          {/* 带本记录字段与子明细 */}
          <label style={chkRow}>
            <input
              type="checkbox"
              checked={v.inputs?.record?.enabled ?? true}
              style={checkboxStyle}
              onChange={(e) => set({
                inputs: { ...v.inputs, record: { enabled: e.target.checked } },
              })}
            />
            {t("admin.schema.aiFill.with_record")}
          </label>
          <small className="muted" style={{ marginTop: -4 }}>
            {t("admin.schema.aiFill.help.with_record")}
          </small>

          {/* Include conversations */}
          <label style={chkRow}>
            <input
              type="checkbox"
              checked={!!v.inputs?.interactions?.enabled}
              style={checkboxStyle}
              onChange={(e) => set({
                inputs: {
                  ...v.inputs,
                  interactions: {
                    enabled: e.target.checked,
                    limit: v.inputs?.interactions?.limit ?? 20,
                  },
                },
              })}
            />
            {t("admin.schema.aiFill.with_interactions")}
          </label>
        </>
      )}
    </div>
  );
}

/** Returns true when the ai_fill config is complete enough to save. */
export function aiAutofillComplete(v: AiFillConf | undefined): boolean {
  if (!v || !v.enabled) return true; // toggle off → always valid
  return !!v.mission?.trim();  // output_spec 退役;只需一句话使命
}
