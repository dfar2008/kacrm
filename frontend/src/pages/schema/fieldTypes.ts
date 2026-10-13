/**
 * Component palette catalog for the schema field builder.
 *
 * Maps every draggable ``field_type`` to a display icon, an i18n label key,
 * and a palette group. The four groups (基础 / 高级 / 关联 / 特殊) are the
 * four sections rendered in ``ComponentPalette``.
 *
 * ``field_type`` values here MUST match the backend SQL-type mapping in
 * ``hermes_cloud/app/crm/field_provisioning.py``. Legacy "select" is NOT in
 * the palette — new picklist fields use "picklist".
 */
import type { DictKey } from "@/lib/i18n";

export type PaletteGroup = "ai" | "basic" | "advanced" | "relation" | "special";

/** An AI-autofill palette tile is not a SQL type: dropping it creates a field
 *  of `outputType` with `field_config.ai_fill` pre-set to `mode` (toggle on).
 *  Mirrors Attio, where the 4 AI Autofill modes sit at the top of the
 *  attribute-type picker as first-class choices, not a post-hoc switch. */
export interface AiPreset {
  mode: "classify" | "summarize" | "web_agent" | "prompt";
  /** Default SQL output type the dropped field is provisioned as. */
  outputType: "text" | "number" | "currency" | "picklist" | "multiselect";
}

export interface FieldTypeDef {
  /** Canonical field_type stored on the field row, OR a synthetic `ai_*` id
   *  for AI-autofill tiles (resolved to `aiPreset.outputType` on drop). */
  type: string;
  /** Single-char / emoji icon shown on the palette tile and field card. */
  icon: string;
  /** i18n key for the human label, e.g. "admin.schema.fieldtype.text". */
  labelKey: DictKey;
  /** Which palette section this component lives in. */
  group: PaletteGroup;
  /** Present only on AI-autofill tiles — drives the drop-time preset. */
  aiPreset?: AiPreset;
}

/** Ordered palette catalog. Render order within a group follows this array. */
export const FIELD_TYPES: readonly FieldTypeDef[] = [
  // ✨ AI 自动填充 — 一等入口(对齐 Attio:4 模式置于类型选择器顶部)
  { type: "ai_classify",  icon: "🔖", labelKey: "admin.schema.fieldtype.ai_classify",  group: "ai", aiPreset: { mode: "classify",  outputType: "picklist" } },
  { type: "ai_summarize", icon: "☷",  labelKey: "admin.schema.fieldtype.ai_summarize", group: "ai", aiPreset: { mode: "summarize", outputType: "text" } },
  { type: "ai_web_agent", icon: "🌐", labelKey: "admin.schema.fieldtype.ai_web_agent", group: "ai", aiPreset: { mode: "web_agent", outputType: "text" } },
  { type: "ai_prompt",    icon: "✦",  labelKey: "admin.schema.fieldtype.ai_prompt",    group: "ai", aiPreset: { mode: "prompt",    outputType: "text" } },
  // 基础
  { type: "text",        icon: "T",  labelKey: "admin.schema.fieldtype.text",        group: "basic" },
  { type: "textarea",    icon: "¶",  labelKey: "admin.schema.fieldtype.textarea",    group: "basic" },
  { type: "number",      icon: "#",  labelKey: "admin.schema.fieldtype.number",      group: "basic" },
  { type: "currency",    icon: "¥",  labelKey: "admin.schema.fieldtype.currency",    group: "basic" },
  { type: "date",        icon: "📅", labelKey: "admin.schema.fieldtype.date",        group: "basic" },
  { type: "datetime",    icon: "🕐", labelKey: "admin.schema.fieldtype.datetime",    group: "basic" },
  // 高级
  { type: "picklist",    icon: "▾",  labelKey: "admin.schema.fieldtype.picklist",    group: "advanced" },
  { type: "multiselect", icon: "☰",  labelKey: "admin.schema.fieldtype.multiselect", group: "advanced" },
  { type: "email",       icon: "@",  labelKey: "admin.schema.fieldtype.email",       group: "advanced" },
  { type: "phone",       icon: "☎",  labelKey: "admin.schema.fieldtype.phone",       group: "advanced" },
  { type: "url",         icon: "🔗", labelKey: "admin.schema.fieldtype.url",         group: "advanced" },
  { type: "file",        icon: "📎", labelKey: "admin.schema.fieldtype.file",        group: "advanced" },
  // 关联
  { type: "user",        icon: "👤", labelKey: "admin.schema.fieldtype.user",        group: "relation" },
  { type: "department",  icon: "🏢", labelKey: "admin.schema.fieldtype.department",  group: "relation" },
  { type: "relation",    icon: "⛓", labelKey: "admin.schema.fieldtype.relation",    group: "relation" },
  // 特殊
  { type: "formula",     icon: "ƒ",  labelKey: "admin.schema.fieldtype.formula",     group: "special" },
  { type: "richtext",    icon: "✎",  labelKey: "admin.schema.fieldtype.richtext",    group: "special" },
  { type: "autoNumber",  icon: "№",  labelKey: "admin.schema.fieldtype.autonumber",  group: "special" },
  // "ai_field" palette item retired (M1): AI autofill is now a toggle/modifier
  // on regular field types (text / number / currency / picklist / multiselect).
] as const;

/** Ordered list of palette groups + their section-heading i18n keys. */
export const PALETTE_GROUPS: readonly { key: PaletteGroup; headingKey: DictKey }[] = [
  { key: "ai",       headingKey: "admin.schema.palette.group.ai" },
  { key: "basic",    headingKey: "admin.schema.palette.group.basic" },
  { key: "advanced", headingKey: "admin.schema.palette.group.advanced" },
  { key: "relation", headingKey: "admin.schema.palette.group.relation" },
  { key: "special",  headingKey: "admin.schema.palette.group.special" },
] as const;

/** Lookup a field type def by its canonical type string. */
export function fieldTypeDef(type: string): FieldTypeDef | undefined {
  return FIELD_TYPES.find(d => d.type === type);
}

/** Field types whose property panel shows the option editor. Legacy "select"
 *  is matched at the call site, not here (it is not a palette type). */
export const OPTION_BACKED_TYPES = new Set(["picklist", "multiselect"]);

/** Field types whose field_name is derived from a relation target module. */
export const RELATION_TYPES = new Set(["relation", "user", "department"]);

/** Field types that can carry an AI autofill config (M1 modifier model).
 *  Only canonical types — legacy select/radio/multipicklist were unified to
 *  picklist/multiselect (alembic 0107 + seed/provision normalize). */
export const AI_AUTOFILL_TYPES = new Set([
  "text", "number", "currency", "picklist", "multiselect",
]);

/** Given a field type, return the AI autofill modes that are valid for it.
 *  - classify  → picklist / multiselect
 *  - summarize → text / textarea
 *  - web_agent → text / number / currency
 *  - prompt    → text / number / currency
 */
export function aiModesForType(
  fieldType: string,
): ("classify" | "summarize" | "web_agent" | "prompt")[] {
  switch (fieldType) {
    case "picklist":
    case "multiselect":
      return ["classify"];
    case "text":
    case "textarea":
      return ["summarize", "web_agent", "prompt"];
    case "number":
    case "currency":
      return ["web_agent", "prompt"];
    default:
      return [];
  }
}
