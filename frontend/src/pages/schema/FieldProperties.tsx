/**
 * FieldProperties — column 4 of the schema field builder.
 *
 * Edits the selected field. For drafts, "保存属性" provisions the field via
 * ``addModuleField`` (backend ``provision_field``) then normalizes sort_order.
 * For persisted fields it PATCHes metadata.
 *
 * System fields (``is_system``): field_name + field_type are locked; label,
 * label translation, description and status flags remain editable (the
 * multi-language requirement needs system-field labels translatable).
 *
 * AutoNumber / Formula fields render a type-specific sub-panel
 * (``AutoNumberConfig`` / ``FormulaEditor``); save stays disabled until
 * that config is complete (autoNumber digits >= 1, formula expression
 * non-empty).
 */
import { useEffect, useMemo, useState } from "react";
import {
  ApiError,
  type AiFillConf,
  type FieldConfig,
  addFieldOption,
  addModuleField,
  deleteFieldOption,
  patchModuleField,
  reorderFieldOptions,
  updateFieldOptionLabel,
} from "@/lib/api";
import { toast } from "@/lib/toast";
import { useLocale, useT } from "@/lib/i18n";
import {
  fieldTypeDef,
  OPTION_BACKED_TYPES,
  AI_AUTOFILL_TYPES,
} from "./fieldTypes";
import type { ModuleEntry } from "./ModuleCatalog";
import { fieldCardId } from "./FieldCard";
import type { UseSchemaFields, DraftField } from "./useSchemaFields";
import { listModules } from "@/lib/api";
import AutoNumberConfig, {
  DEFAULT_AUTONUMBER,
  type AutoNumberCfg,
} from "./AutoNumberConfig";
import FormulaEditor, { type FormulaCfg } from "./FormulaEditor";
import { AiFillConfig, DEFAULT_AI_FILL, aiFillComplete } from "./AiFillConfig";
import type { AiFieldTemplate } from "./aiFieldTemplates";
import { AiAutofill, aiAutofillComplete, defaultAiFillForType } from "./AiAutofill";

function errMsg(e: unknown): string {
  if (e instanceof ApiError) {
    return typeof e.detail === "string" ? e.detail : JSON.stringify(e.detail);
  }
  if (e instanceof Error) return e.message;
  return String(e);
}

const inputStyle: React.CSSProperties = {
  width: "100%", padding: "6px 8px", fontSize: 14,
  background: "var(--bg)", color: "inherit",
  border: "1px solid var(--border)", borderRadius: 6,
  boxSizing: "border-box",
};

/** ``field_name`` is restricted to a SQL-safe identifier. */
const FIELD_NAME_RE = /^[a-z][a-z0-9_]*$/;

/** Suggest a unique field_name from a base, appending _2, _3, … when the
 *  base collides with an existing field. Lets a module hold more than one
 *  relation to the same target (e.g. accounts_id + accounts_id_2). */
function uniqueFieldName(base: string, taken: Set<string>): string {
  if (!taken.has(base)) return base;
  let n = 2;
  while (taken.has(`${base}_${n}`)) n++;
  return `${base}_${n}`;
}

/** Types that draw a default-value input. Relations / formula / autoNumber
 *  do not — their value is system- or reference-derived. */
function showsDefaultValue(type: string): boolean {
  return !["relation", "user", "department", "formula", "autoNumber"].includes(type);
}

/** Types whose value is always system-managed → status "只读" forced on. */
function isAlwaysReadonly(type: string): boolean {
  return type === "formula" || type === "autoNumber";
}

export function FieldProperties({
  module, field, schema, onClearSelection, onSelectField,
}: {
  module: string;
  field: DraftField | null;
  schema: UseSchemaFields;
  onClearSelection: () => void;
  onSelectField: (id: string) => void;
}) {
  const t = useT();

  if (!field) {
    return (
      <PanelShell>
        <div className="muted" style={{ fontSize: 14, padding: 8 }}>
          {t("admin.schema.builder.pick_field")}
        </div>
      </PanelShell>
    );
  }

  return (
    <PanelShell>
      <FieldPropertiesForm
        // Re-mount the form when the selected field changes so local
        // edit state never leaks between fields.
        key={fieldCardId(field)}
        module={module}
        field={field}
        schema={schema}
        onClearSelection={onClearSelection}
        onSelectField={onSelectField}
      />
    </PanelShell>
  );
}

function PanelShell({ children }: { children: React.ReactNode }) {
  const t = useT();
  return (
    <div style={{
      flex: "1 1 0", minWidth: 380, maxWidth: 520,
      border: "1px solid var(--border)", borderRadius: 8,
      background: "var(--panel)", overflow: "hidden",
      display: "flex", flexDirection: "column",
    }}>
      <div style={{
        padding: "10px 12px", fontSize: 14, fontWeight: 600,
        textTransform: "uppercase", color: "var(--muted)",
        borderBottom: "1px solid var(--border)", flexShrink: 0,
      }}>
        {t("admin.schema.builder.properties_heading")}
      </div>
      <div style={{
        flex: 1, overflowY: "auto", padding: 14,
        display: "flex", flexDirection: "column", gap: 14,
      }}>
        {children}
      </div>
    </div>
  );
}

function FieldPropertiesForm({
  module, field, schema, onClearSelection, onSelectField,
}: {
  module: string;
  field: DraftField;
  schema: UseSchemaFields;
  onClearSelection: () => void;
  onSelectField: (id: string) => void;
}) {
  const t = useT();
  const isDraft = !!field.is_draft;
  const isSystem = !!field.is_system;
  const def = fieldTypeDef(field.field_type);
  const optionBacked = OPTION_BACKED_TYPES.has(field.field_type)
    || field.field_type === "select";
  const isRelation = field.field_type === "relation";

  // ── Local edit state ────────────────────────────────────────────────
  const [fieldName, setFieldName] = useState(field.field_name);
  const [label, setLabel] = useState(field.field_label);
  const [labelEn, setLabelEn] = useState(field.field_config?.label_i18n?.["en-US"] || "");
  const [defaultValue, setDefaultValue] = useState(field.default_value || "");
  const [description, setDescription] = useState(field.description || "");
  const [required, setRequired] = useState(!!field.is_required);
  const [searchable, setSearchable] = useState(!!field.is_searchable);
  const [readonly, setReadonly] = useState(
    !!field.is_readonly || isAlwaysReadonly(field.field_type),
  );
  const [hidden, setHidden] = useState(!!field.is_hidden);
  const [reason, setReason] = useState("");
  // Draft-only: options accumulate locally until provisioned in one POST.
  const [draftOptions, setDraftOptions] = useState<string[]>(
    parseOptions(field.options),
  );
  // Relation: target module for a draft relation field.
  const [relationTarget, setRelationTarget] = useState(
    field.field_config?.relation?.target_module || "",
  );
  const [saving, setSaving] = useState(false);
  const [autoNumberCfg, setAutoNumberCfg] = useState<AutoNumberCfg>(
    field.field_config?.autonumber ?? DEFAULT_AUTONUMBER,
  );
  const [formulaCfg, setFormulaCfg] = useState<FormulaCfg>(
    field.field_config?.formula ?? { expression: "" },
  );
  const [aiCfg, setAiCfg] = useState<AiFillConf>(
    field.field_config?.ai_fill ?? DEFAULT_AI_FILL,
  );
  const [saveError, setSaveError] = useState<string | null>(null);
  // AI-field 极简模式：默认只露一句话生成+模板+显示名,其余收进「高级设置」。
  // key= 重挂会重置(切字段自然回到极简)。
  const [aiAdvanced, setAiAdvanced] = useState(false);

  // M1 modifier: ai_fill as a toggle on regular field types.
  // Uses field_config.ai_fill directly; undefined = toggle off.
  const isAiAutofillType = AI_AUTOFILL_TYPES.has(field.field_type);
  // aiCfgNew tracks the NEW modifier-style config (separate from legacy aiCfg).
  const [aiCfgNew, setAiCfgNew] = useState<AiFillConf | undefined>(
    // Only show as "on" if ai_fill.enabled is true and the field is not ai_field
    (field.field_type !== "ai_field" && field.field_config?.ai_fill?.enabled)
      ? field.field_config.ai_fill
      : undefined,
  );

  // "ai_field" is only a palette marker; the real DB column type is the
  // configured output_type. picklist output also carries the user's options.
  const isAiField = field.field_type === "ai_field";
  // Hide the standard blocks (field_name / label_en / default / description /
  // status) when an AI field is in simple mode. The 显示名 block stays visible.
  const hideForSimpleAi = isAiField && !aiAdvanced;
  const aiOutputType = aiCfg.output_type;
  const aiSavesOptions = isAiField && aiOutputType === "picklist";

  function buildFieldConfig(): FieldConfig | null {
    const cfg: FieldConfig = { ...(field.field_config || {}) };
    if (labelEn.trim()) {
      cfg.label_i18n = { ...(cfg.label_i18n || {}), "en-US": labelEn.trim() };
    } else if (cfg.label_i18n) {
      const next = { ...cfg.label_i18n };
      delete next["en-US"];
      cfg.label_i18n = Object.keys(next).length ? next : undefined;
    }
    if (isRelation && relationTarget) {
      cfg.relation = { target_module: relationTarget };
    }
    if (field.field_type === "autoNumber") {
      cfg.autonumber = autoNumberCfg;
    }
    if (field.field_type === "formula") {
      cfg.formula = formulaCfg;
    }
    if (isAiField) {
      cfg.ai_fill = { ...aiCfg, enabled: true };
    }
    // M1 modifier: for regular field types with the new autofill toggle.
    if (isAiAutofillType && !isAiField) {
      if (aiCfgNew && aiCfgNew.enabled) {
        cfg.ai_fill = aiCfgNew;
      } else {
        // Toggle turned off: remove ai_fill from config.
        delete cfg.ai_fill;
      }
    }
    return Object.keys(cfg).length ? cfg : null;
  }

  // Apply a ready-made AI-field template: prefill the AI config, suggest a
  // label if none yet, and seed picklist options for picklist templates.
  function applyAiTemplate(tpl: AiFieldTemplate) {
    setAiCfg({
      ...DEFAULT_AI_FILL,
      output_type: tpl.output_type,
      mission: tpl.mission,
      context_rules: tpl.context_rules,
      inputs: { fields: [], interactions: { enabled: !!tpl.with_interactions, limit: 20 } },
    });
    if (!label.trim()) setLabel(tpl.label);
  }

  // ── Save: draft → provision; persisted → patch. ─────────────────────
  async function save() {
    if (!label.trim()) {
      toast(t("admin.schema.detail.label"), "error");
      return;
    }
    if (isDraft && !FIELD_NAME_RE.test(fieldName)) {
      toast(t("admin.schema.create.name_ph"), "error");
      return;
    }
    setSaveError(null);
    setSaving(true);
    try {
      const fieldConfig = buildFieldConfig();
      if (isDraft) {
        await addModuleField(module, {
          field_name: fieldName,
          field_label: label.trim(),
          // "ai_field" is a palette marker only — the DB column must be the
          // real output type. Provision with aiCfg.output_type instead.
          field_type: isAiField ? aiOutputType : field.field_type,
          is_required: required,
          is_searchable: searchable,
          is_readonly: readonly,
          options: aiSavesOptions
            ? (draftOptions.length ? draftOptions : undefined)
            : (optionBacked && draftOptions.length ? draftOptions : undefined),
          default_value: showsDefaultValue(field.field_type) && defaultValue
            ? defaultValue : undefined,
          description: description || undefined,
          field_config: fieldConfig,
          reason: reason || undefined,
        });
        toast(t("admin.schema.toast.field_created"), "success");
        // Discard the local draft, refresh, normalize sort_order, reselect.
        schema.removeDraft(field.id);
        await schema.normalizeAfterSave();
        onSelectField(fieldName);
      } else {
        await patchModuleField(module, field.field_name, {
          field_label: label.trim(),
          is_required: required,
          is_searchable: searchable,
          is_readonly: readonly,
          is_hidden: hidden,
          description: description || null,
          default_value: showsDefaultValue(field.field_type)
            ? (defaultValue || null) : undefined,
          field_config: fieldConfig,
          reason: reason || undefined,
        });
        toast(t("admin.schema.toast.field_saved"), "success");
        await schema.refresh();
        onSelectField(field.field_name);
      }
    } catch (e) {
      toast(errMsg(e), "error");
      if (field.field_type === "formula") {
        setSaveError(errMsg(e));
      }
    } finally {
      setSaving(false);
    }
  }

  const saveDisabled = saving
    || !label.trim()
    || (isDraft && !FIELD_NAME_RE.test(fieldName))
    || (isDraft && isRelation && !relationTarget)
    || (field.field_type === "autoNumber" && autoNumberCfg.digits < 1)
    || (field.field_type === "formula" && !formulaCfg.expression.trim())
    || (isAiField && !aiFillComplete(aiCfg))
    || (aiSavesOptions && isDraft && !draftOptions.length)
    // M1: new autofill modifier must be complete before saving.
    || (isAiAutofillType && !isAiField && !aiAutofillComplete(aiCfgNew));

  return (
    <>
      {/* Component type — read-only. */}
      <Row label={t("admin.schema.detail.type")}>
        <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 14 }}>
          <span>{def?.icon ?? "?"}</span>
          <span>{def ? t(def.labelKey) : field.field_type}</span>
          <code className="muted" style={{ fontSize: 13 }}>{field.field_type}</code>
        </div>
      </Row>

      {/* FieldName — editable for drafts, read-only code once persisted. */}
      {!hideForSimpleAi && (
      <Row label={t("admin.schema.detail.name")}>
        {isDraft && !isSystem ? (
          <input
            value={fieldName}
            onChange={e => setFieldName(e.target.value)}
            placeholder={t("admin.schema.create.name_ph")}
            style={{
              ...inputStyle,
              borderColor: FIELD_NAME_RE.test(fieldName)
                ? "var(--border)" : "var(--danger)",
            }}
          />
        ) : (
          <code style={{ fontSize: 14 }}>{field.field_name}</code>
        )}
      </Row>
      )}

      {/* 标签名 — 显示名,极简模式下也保留。 */}
      <Row label={t("admin.schema.detail.label")}>
        <input value={label} onChange={e => setLabel(e.target.value)} style={inputStyle} />
      </Row>

      {/* 标签翻译 (en-US) */}
      {!hideForSimpleAi && (
      <Row label={t("admin.schema.props.label_en")}>
        <input
          value={labelEn}
          onChange={e => setLabelEn(e.target.value)}
          placeholder={t("admin.schema.props.label_en_ph")}
          style={inputStyle}
        />
      </Row>
      )}

      {/* 关联目标模块 — relation type only. */}
      {isRelation && (
        <Row label={t("admin.schema.props.relation_target")}>
          <RelationTargetSelect
            module={module}
            value={relationTarget}
            disabled={!isDraft}
            onChange={(target) => {
              setRelationTarget(target);
              // Suggest <target>_id, deduped against existing fields so a
              // second relation to the same module doesn't collide. The
              // name stays editable — the user can override it.
              if (isDraft && target) {
                const taken = new Set(
                  schema.fields
                    .filter(f => f.field_name !== field.field_name)
                    .map(f => f.field_name),
                );
                setFieldName(uniqueFieldName(`${target}_id`, taken));
              }
            }}
          />
        </Row>
      )}

      {/* 默认值 — hidden for relation / formula / autoNumber. */}
      {!hideForSimpleAi && showsDefaultValue(field.field_type) && (
        <Row label={t("admin.schema.props.default_value")}>
          <input
            value={defaultValue}
            onChange={e => setDefaultValue(e.target.value)}
            style={inputStyle}
          />
        </Row>
      )}

      {/* 帮助 */}
      {!hideForSimpleAi && (
      <Row label={t("admin.schema.detail.description")}>
        <input value={description} onChange={e => setDescription(e.target.value)} style={inputStyle} />
      </Row>
      )}

      {/* 字段状态 */}
      {!hideForSimpleAi && (
      <div>
        <SectionLabel>{t("admin.schema.props.status")}</SectionLabel>
        <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 6 }}>
          <Check label={t("admin.schema.props.required")}
                 checked={required} onChange={setRequired} />
          <Check label={t("admin.schema.props.searchable")}
                 checked={searchable} onChange={setSearchable} />
          <Check label={t("admin.schema.props.readonly")}
                 checked={readonly}
                 disabled={isAlwaysReadonly(field.field_type)}
                 onChange={setReadonly} />
          {/* Hidden only meaningful for persisted fields. */}
          {!isDraft && (
            <Check label={t("admin.schema.props.hidden")}
                   checked={hidden} onChange={setHidden} />
          )}
        </div>
      </div>
      )}

      {/* 选项编辑器 — picklist / multiselect / legacy select, or an
          AI field whose output_type is picklist (drafts collect options
          locally). */}
      {(optionBacked || aiSavesOptions) && (
        <div>
          <SectionLabel>{t("admin.schema.options.heading")}</SectionLabel>
          {isDraft ? (
            <DraftOptionEditor options={draftOptions} onChange={setDraftOptions} />
          ) : (
            <PersistedOptionEditor
              module={module}
              field={field}
              onChanged={() => schema.refresh()}
            />
          )}
        </div>
      )}

      {/* M1: AI autofill modifier — for regular field types (text/number/currency/picklist/multiselect).
          Not shown for legacy ai_field palette type (that uses the old AiFillConfig below). */}
      {isAiAutofillType && !isAiField && (
        <div>
          <AiAutofill
            fieldType={field.field_type}
            value={aiCfgNew}
            fields={schema.fields}
            onChange={setAiCfgNew}
            onOutputTypeChange={isDraft ? (ft) => {
              // Draft: switch the real column type + keep ai_fill.output_type in sync.
              schema.updateDraft(field.id, { field_type: ft });
              setAiCfgNew(prev => prev
                ? { ...prev, output_type: ft === "text" ? "text" : "number" }
                : prev);
            } : undefined}
          />
        </div>
      )}

      {/* P6: type-specific sub-panels. */}
      {field.field_type === "autoNumber" && (
        <AutoNumberConfig value={autoNumberCfg} onChange={setAutoNumberCfg} />
      )}
      {field.field_type === "formula" && (
        <FormulaEditor
          value={formulaCfg}
          fields={schema.fields}
          selfFieldName={field.field_name}
          saveError={saveError}
          onChange={setFormulaCfg}
        />
      )}
      {isAiField && (
        <AiFillConfig
          value={aiCfg}
          module={module}
          onChange={setAiCfg}
          onApplyTemplate={applyAiTemplate}
          simple={!aiAdvanced}
        />
      )}
      {isAiField && (
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <button
            type="button"
            className="ghost"
            onClick={() => setAiAdvanced(a => !a)}
            style={{
              alignSelf: "flex-start", display: "flex", alignItems: "center",
              gap: 6, fontSize: 14,
            }}
          >
            <span>{aiAdvanced ? "▾" : "▸"}</span>
            <span>⚙ {t("admin.schema.props.advanced")}</span>
          </button>
          {!aiAdvanced && (
            <span className="muted" style={{ fontSize: 13 }}>
              {t("admin.schema.props.advanced_hint")}
            </span>
          )}
        </div>
      )}

      {/* 变更说明 + 保存 */}
      <Row label={t("admin.schema.detail.reason")}>
        <input value={reason} onChange={e => setReason(e.target.value)} style={inputStyle} />
      </Row>
      <div style={{ display: "flex", gap: 8 }}>
        <button className="primary" onClick={save} disabled={saveDisabled}>
          {saving ? t("admin.schema.detail.saving") : t("admin.schema.props.save")}
        </button>
        {isDraft && (
          <button
            className="ghost"
            disabled={saving}
            onClick={() => { schema.removeDraft(field.id); onClearSelection(); }}
          >
            {t("admin.schema.create.cancel")}
          </button>
        )}
      </div>
    </>
  );
}

/** Target-module selector for a relation field. */
function RelationTargetSelect({
  module, value, disabled, onChange,
}: {
  module: string;
  value: string;
  disabled: boolean;
  onChange: (target: string) => void;
}) {
  const t = useT();
  const [modules, setModules] = useState<ModuleEntry[]>([]);
  useEffect(() => {
    let cancelled = false;
    listModules()
      .then(r => {
        if (cancelled) return;
        setModules((r.data || []).map(m => ({ name: m.name, label: m.label })));
      })
      .catch(() => { /* selector just stays empty */ });
    return () => { cancelled = true; };
  }, []);
  // A relation cannot target its own module.
  const options = useMemo(
    () => modules.filter(m => m.name !== module),
    [modules, module],
  );
  return (
    <select
      value={value}
      disabled={disabled}
      onChange={e => onChange(e.target.value)}
      style={inputStyle}
    >
      <option value="">{t("admin.schema.props.relation_target_ph")}</option>
      {options.map(m => (
        <option key={m.name} value={m.name}>{m.label} ({m.name})</option>
      ))}
    </select>
  );
}

/** Draft option editor — local list, edited freely before provisioning. */
function DraftOptionEditor({
  options, onChange,
}: { options: string[]; onChange: (next: string[]) => void }) {
  const t = useT();
  const [value, setValue] = useState("");
  function add() {
    const v = value.trim();
    if (!v) return;
    if (options.some(o => o.toLowerCase() === v.toLowerCase())) {
      toast(t("admin.schema.options.duplicate"), "info");
      return;
    }
    onChange([...options, v]);
    setValue("");
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 6 }}>
      {options.length === 0 && (
        <div className="muted" style={{ fontSize: 14 }}>
          {t("admin.schema.options.empty")}
        </div>
      )}
      {options.length > 0 && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
          {options.map(o => (
            <span key={o} style={{
              fontSize: 14, padding: "3px 8px",
              border: "1px solid var(--border)", borderRadius: 12,
              display: "flex", alignItems: "center", gap: 4,
            }}>
              {o}
              <button
                onClick={() => onChange(options.filter(x => x !== o))}
                style={{
                  background: "transparent", border: "none", color: "var(--muted)",
                  cursor: "pointer", padding: 0, fontSize: 14,
                }}
                aria-label="remove"
              >×</button>
            </span>
          ))}
        </div>
      )}
      <div style={{ display: "flex", gap: 6 }}>
        <input
          value={value}
          onChange={e => setValue(e.target.value)}
          onKeyDown={e => { if (e.key === "Enter") { e.preventDefault(); add(); } }}
          placeholder={t("admin.schema.options.add_ph")}
          style={{ ...inputStyle, flex: 1 }}
        />
        <button className="ghost" onClick={add} disabled={!value.trim()}>
          {t("admin.schema.options.add_btn")}
        </button>
      </div>
    </div>
  );
}

/** Persisted option editor — APPEND-ONLY. Removing / renaming / reordering
 *  options on a provisioned field would orphan stored data, so the backend
 *  ``addFieldOption`` route only appends. This is an intentional limit. */
function PersistedOptionEditor({
  module, field, onChanged,
}: { module: string; field: DraftField; onChanged: () => void }) {
  const t = useT();
  const { locale } = useLocale();
  const [value, setValue] = useState("");
  const [newLabel, setNewLabel] = useState("");
  const [adding, setAdding] = useState(false);
  const [busyValue, setBusyValue] = useState<string | null>(null);
  const [renamingValue, setRenamingValue] = useState<string | null>(null);
  const [renameDraft, setRenameDraft] = useState("");
  // 二段式删除：第一次点 × 进入「确认中」状态，第二次才真的删——给一个无
  // 模态的撤销窗口，比 window.confirm() 弹框打断流更友好。
  const [confirmingDelete, setConfirmingDelete] = useState<string | null>(null);
  const [hoveredValue, setHoveredValue] = useState<string | null>(null);
  const current = parseOptions(field.options);
  // Labels carried inline in the options JSON (``{value,label}``) — used as a
  // fallback when field_config.option_labels doesn't have an entry, so the
  // row shows the human label ("线下展会") not the bare value ("exhibition").
  const optionLabelMap = new Map(
    parseOptionPairs(field.options).map(o => [o.value, o.label]),
  );

  // System options: code branches on these literal values (e.g.
  // ``potentials.milestone`` ``won`` / ``lost`` referenced by
  // ``TERMINAL_MILESTONES``). Backend rejects deletion / value-rename
  // requests with 409; the lock icon is the matching UX affordance so
  // admins don't waste a click trying. Label rename is still allowed.
  const systemOptions = new Set<string>(
    extractSystemOptions(field.field_config),
  );
  // option_labels stored under field_config.option_labels[value][locale].
  // Prefer the active SPA locale (so rename starts editing the slot we'll
  // actually write), fall back to the other locale, then the value itself.
  const labelFor = (v: string): string =>
    extractOptionLabel(field.field_config, v, locale) ?? optionLabelMap.get(v) ?? v;

  async function add() {
    if (!value.trim()) return;
    setAdding(true);
    try {
      const labelTrim = newLabel.trim();
      const r = await addFieldOption(
        module, field.field_name, value.trim(),
        labelTrim ? { label: labelTrim, locale } : undefined,
      );
      if (r.status === "duplicate") {
        toast(t("admin.schema.toast.option_duplicate"), "info");
      } else {
        toast(t("admin.schema.toast.option_added"), "success");
      }
      setValue("");
      setNewLabel("");
      onChanged();
    } catch (e) {
      toast(errMsg(e), "error");
    } finally {
      setAdding(false);
    }
  }

  async function remove(v: string) {
    if (systemOptions.has(v)) return;  // belt-and-suspenders; UI hides the button
    setConfirmingDelete(null);
    setBusyValue(v);
    try {
      await deleteFieldOption(module, field.field_name, v);
      toast(t("admin.schema.options.deleted"), "success");
      onChanged();
    } catch (e) {
      toast(errMsg(e), "error");
    } finally {
      setBusyValue(null);
    }
  }

  function startRename(v: string) {
    setRenamingValue(v);
    setRenameDraft(labelFor(v));
  }

  async function move(v: string, direction: -1 | 1) {
    const idx = current.indexOf(v);
    const targetIdx = idx + direction;
    if (idx < 0 || targetIdx < 0 || targetIdx >= current.length) return;
    const next = current.slice();
    [next[idx], next[targetIdx]] = [next[targetIdx], next[idx]];
    setBusyValue(v);
    try {
      await reorderFieldOptions(module, field.field_name, next);
      onChanged();
    } catch (e) {
      toast(errMsg(e), "error");
    } finally {
      setBusyValue(null);
    }
  }

  async function commitRename() {
    if (!renamingValue) return;
    const next = renameDraft.trim();
    if (!next) {
      setRenamingValue(null);
      return;
    }
    setBusyValue(renamingValue);
    try {
      // Pass the active SPA locale so the backend updates that slot
      // only — other locales on the same option stay intact.
      await updateFieldOptionLabel(
        module, field.field_name, renamingValue, next, { locale },
      );
      toast(t("admin.schema.options.renamed"), "success");
      setRenamingValue(null);
      onChanged();
    } catch (e) {
      toast(errMsg(e), "error");
    } finally {
      setBusyValue(null);
    }
  }

  // 行样式：纵向列表、左 label+value 紧挨、右操作按钮 sticky。
  // 之前用 `1fr auto auto` 会把 value 推到容器最右，跟 label 之间撑出一大片
  // 空白；改成 flex + label/value 自然顺位，按钮用 marginLeft:auto 推到右。
  const rowStyle: React.CSSProperties = {
    display: "flex",
    alignItems: "center",
    gap: 10,
    padding: "5px 8px",
    borderBottom: "1px solid var(--border)",
    fontSize: 14,
    minWidth: 0,
  };
  const actionBtn: React.CSSProperties = {
    padding: "5px 9px",
    fontSize: 17,
    lineHeight: 1,
    minWidth: 30,
    background: "transparent",
    border: "1px solid transparent",
    borderRadius: 5,
    cursor: "pointer",
    color: "var(--muted)",
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 6 }}>
      <div className="muted" style={{ fontSize: 13 }}>
        {t("admin.schema.options.help")}
      </div>
      {current.length === 0 && (
        <div className="muted" style={{ fontSize: 14 }}>
          {t("admin.schema.options.empty")}
        </div>
      )}
      {current.length > 0 && (
        <div style={{
          border: "1px solid var(--border)",
          borderRadius: 6,
          overflow: "hidden",
        }}>
          {current.map((v, i) => {
            const isSystem = systemOptions.has(v);
            const isRenaming = renamingValue === v;
            const isConfirmingDel = confirmingDelete === v;
            const busy = busyValue === v;
            const hovered = hoveredValue === v;
            const labelText = labelFor(v);
            const isFirst = i === 0;
            const isLast = i === current.length - 1;

            // Rename mode: full-width edit row with explicit hint.
            if (isRenaming) {
              return (
                <div key={v} style={{
                  ...rowStyle,
                  alignItems: "stretch",
                  borderBottom: isLast ? "none" : rowStyle.borderBottom,
                  background: "var(--surface-hover)",
                }}>
                  <div style={{ display: "flex", flexDirection: "column", gap: 4, flex: 1, minWidth: 0 }}>
                    <input
                      value={renameDraft}
                      autoFocus
                      onChange={e => setRenameDraft(e.target.value)}
                      onKeyDown={e => {
                        if (e.key === "Enter") commitRename();
                        if (e.key === "Escape") setRenamingValue(null);
                      }}
                      placeholder={labelText}
                      style={{ ...inputStyle, fontSize: 14 }}
                    />
                    <div className="muted" style={{ fontSize: 13 }}>
                      {t("admin.schema.options.rename_hint").replace("{v}", v)}
                    </div>
                  </div>
                  <div style={{ display: "flex", gap: 4, alignSelf: "flex-start" }}>
                    <button onClick={commitRename} disabled={busy}
                            style={{ ...actionBtn, color: "var(--accent)" }}>
                      {t("admin.schema.options.rename_save")}
                    </button>
                    <button onClick={() => setRenamingValue(null)} disabled={busy}
                            style={actionBtn}>
                      {t("admin.schema.options.rename_cancel")}
                    </button>
                  </div>
                </div>
              );
            }

            return (
              <div
                key={v}
                style={{
                  ...rowStyle,
                  borderBottom: isLast ? "none" : rowStyle.borderBottom,
                  opacity: busy ? 0.5 : 1,
                  background: hovered ? "var(--surface-hover)" : undefined,
                }}
                onMouseEnter={() => setHoveredValue(v)}
                onMouseLeave={() => { setHoveredValue(null); setConfirmingDelete(null); }}
              >
                {/* 系统选项的 🔒 */}
                {isSystem && (
                  <span title={t("admin.schema.options.system_lock_hint")}
                        style={{ flexShrink: 0, fontSize: 14, opacity: 0.85 }}>🔒</span>
                )}
                {/* label + value 包成一个 flex 列：长文字优先截断 label，
                    value 再截断；两段都用 ellipsis + title tooltip 保
                    全信息可读。flex:1 让两段共占剩余宽度，按钮始终
                    sticky 右边不会被挤掉。 */}
                <div
                  onClick={() => startRename(v)}
                  title={t("admin.schema.options.edit_hint")}
                  style={{
                  display: "flex", alignItems: "baseline", gap: 8,
                  flex: "1 1 0", minWidth: 0, cursor: "pointer",
                }}>
                  <span
                    title={labelText}
                    style={{
                      overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                      flex: "0 1 auto", minWidth: 0,
                    }}
                  >{labelText}</span>
                  <span
                    title={v}
                    style={{
                      fontSize: 13, fontFamily: "var(--font-mono, ui-monospace, monospace)",
                      color: "var(--muted)", opacity: 0.7,
                      overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                      flex: "0 1 auto", minWidth: 0,
                    }}
                  >{v}</span>
                </div>
                {/* 系统 badge */}
                {isSystem && (
                  <span className="muted" style={{
                    fontSize: 13, padding: "1px 5px",
                    border: "1px solid var(--border)", borderRadius: 3, flexShrink: 0,
                  }}>{t("admin.schema.options.system_badge")}</span>
                )}
                {/* 操作按钮 sticky 右边，常驻显示但调淡 */}
                <div style={{
                  display: "flex", gap: 2, marginLeft: "auto", flexShrink: 0,
                  opacity: hovered || isConfirmingDel ? 1 : 0.5,
                  transition: "opacity 120ms",
                }}>
                  <button
                    onClick={() => move(v, -1)}
                    disabled={busy || isFirst}
                    title={t("admin.schema.options.move_up")}
                    style={{ ...actionBtn, opacity: isFirst ? 0.3 : undefined }}
                  >↑</button>
                  <button
                    onClick={() => move(v, 1)}
                    disabled={busy || isLast}
                    title={t("admin.schema.options.move_down")}
                    style={{ ...actionBtn, opacity: isLast ? 0.3 : undefined }}
                  >↓</button>
                  <button
                    onClick={() => startRename(v)}
                    disabled={busy}
                    title={t("admin.schema.options.rename_btn")}
                    style={actionBtn}
                  >✎</button>
                  {!isSystem && (
                    isConfirmingDel ? (
                      <button
                        onClick={() => remove(v)}
                        disabled={busy}
                        style={{
                          ...actionBtn,
                          color: "var(--danger)",
                          borderColor: "var(--danger)",
                        }}
                      >{t("admin.schema.options.delete_confirm")}</button>
                    ) : (
                      <button
                        onClick={() => setConfirmingDelete(v)}
                        disabled={busy}
                        title={t("admin.schema.options.delete_btn")}
                        style={{ ...actionBtn, color: "var(--danger)" }}
                      >×</button>
                    )
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
      {/* 新增选项：value (英文 key) + label (本地化显示，选填) 同行追加。
          以前只有 value 一个框，admin 想要中文显示就得追加后再点 ✎ 改名 —
          两步操作不顺手。现在一次填完。
          value 限制小写字母/数字/下划线（DB 存储 key），中文混进来下游会
          有 URL encoding / SQL literal / 列里中英混排等一堆麻烦；这是
          frontend 软拦截，backend 仍接受任意字符串保留后门。 */}
      {(() => {
        const valueTrim = value.trim();
        const valuePattern = /^[a-z][a-z0-9_]*$/;
        const valueInvalid = valueTrim !== "" && !valuePattern.test(valueTrim);
        return (
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            <div style={{ display: "flex", gap: 6 }}>
              <input
                value={value}
                onChange={e => setValue(e.target.value)}
                onKeyDown={e => { if (e.key === "Enter" && valueTrim && !valueInvalid && !adding) add(); }}
                placeholder={t("admin.schema.options.add_value_ph")}
                style={{
                  ...inputStyle, flex: "1 1 0", minWidth: 0,
                  borderColor: valueInvalid ? "var(--danger)" : undefined,
                }}
              />
              <input
                value={newLabel}
                onChange={e => setNewLabel(e.target.value)}
                onKeyDown={e => { if (e.key === "Enter" && valueTrim && !valueInvalid && !adding) add(); }}
                placeholder={t("admin.schema.options.add_label_ph")}
                style={{ ...inputStyle, flex: "1 1 0", minWidth: 0 }}
              />
              <button
                className="ghost"
                onClick={add}
                disabled={adding || !valueTrim || valueInvalid}
              >
                {adding ? t("admin.schema.options.adding") : t("admin.schema.options.add_btn")}
              </button>
            </div>
            {valueInvalid && (
              <div style={{ fontSize: 13, color: "var(--danger)" }}>
                {t("admin.schema.options.add_value_invalid")}
              </div>
            )}
            <div className="muted" style={{ fontSize: 13 }}>
              {t("admin.schema.options.add_hint")}
            </div>
          </div>
        );
      })()}
    </div>
  );
}

// ── field_config helpers ──────────────────────────────────────────────

function extractSystemOptions(cfg: unknown): string[] {
  if (!cfg || typeof cfg !== "object") return [];
  const raw = (cfg as Record<string, unknown>).system_options;
  if (!Array.isArray(raw)) return [];
  return raw.filter((v): v is string => typeof v === "string");
}

function extractOptionLabel(
  cfg: unknown, value: string, preferLocale?: string,
): string | undefined {
  if (!cfg || typeof cfg !== "object") return undefined;
  const labels = (cfg as Record<string, unknown>).option_labels;
  if (!labels || typeof labels !== "object") return undefined;
  const entry = (labels as Record<string, unknown>)[value];
  if (typeof entry === "string") return entry;  // legacy flat shape
  if (entry && typeof entry === "object") {
    // Localized shape: {"zh-CN": "...", "en-US": "..."}. Try the active
    // locale first, then fall through to zh-CN, then en-US.
    const map = entry as Record<string, unknown>;
    const order = [preferLocale, "zh-CN", "en-US"].filter(Boolean) as string[];
    for (const loc of order) {
      if (typeof map[loc] === "string") return map[loc] as string;
    }
  }
  return undefined;
}

// ── Small layout helpers ──────────────────────────────────────────────

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <span className="muted" style={{ fontSize: 13 }}>{label}</span>
      {children}
    </label>
  );
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div style={{
      fontSize: 13, fontWeight: 600, textTransform: "uppercase",
      color: "var(--muted)",
    }}>{children}</div>
  );
}

function Check({
  label, checked, disabled, onChange,
}: {
  label: string;
  checked: boolean;
  disabled?: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <label style={{
      display: "flex", alignItems: "center", gap: 6,
      fontSize: 14, cursor: disabled ? "default" : "pointer",
      opacity: disabled ? 0.6 : 1,
    }}>
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={e => onChange(e.target.checked)}
        // Explicit size: a global ``input { width: 100% }`` rule would
        // otherwise stretch the checkbox full-width and wrap the label.
        style={{ width: 14, height: 14, flexShrink: 0, margin: 0 }}
      />
      {label}
    </label>
  );
}

/** Normalize stored picklist options into {value,label} pairs.
 *
 * Options are stored as a JSON array of objects
 * (``[{"value":"exhibition","label":"线下展会","text":"线下展会"}, …]``).
 * The legacy form was a bare comma-separated value string. The old
 * ``String(opts).split(",")`` broke the JSON form into character-level
 * fragments (``[{"text":`` / ``"label":`` / …) — see the field-management
 * picklist editor. Parse JSON first; fall back to comma-split for legacy. */
function parseOptionPairs(
  opts: string | null | undefined,
): { value: string; label: string }[] {
  if (!opts) return [];
  const s = String(opts).trim();
  if (s.startsWith("[")) {
    try {
      const arr = JSON.parse(s);
      if (Array.isArray(arr)) {
        return arr
          .map((o: any) => {
            if (o && typeof o === "object") {
              const value = String(o.value ?? o.text ?? o.label ?? "").trim();
              const label = String(o.label ?? o.text ?? value).trim();
              return { value, label };
            }
            const v = String(o).trim();
            return { value: v, label: v };
          })
          .filter(o => o.value);
      }
    } catch { /* fall through to legacy comma parse */ }
  }
  return s.split(",").map(x => x.trim()).filter(Boolean).map(v => ({ value: v, label: v }));
}

function parseOptions(opts: string | null | undefined): string[] {
  return parseOptionPairs(opts).map(o => o.value);
}
