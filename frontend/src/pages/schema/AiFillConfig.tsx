/**
 * AiFillConfig — type-specific sub-panel for the "ai_field" palette marker.
 *
 * Writes into ``field_config.ai_fill`` (``AiFillConf``). "ai_field" is ONLY a
 * palette marker, NOT a SQL column type: on save the field is provisioned with
 * ``field_type = output_type`` (text / number / picklist / boolean) so the DB
 * column carries the real output type. This panel just collects the agent
 * instructions (mission / context / output spec), the context fields, and the
 * refresh triggers.
 */
import { useState } from "react";
import { generateAiFieldConfig, type AiFillConf } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { aiFieldTemplates, type AiFieldTemplate } from "./aiFieldTemplates";

export const DEFAULT_AI_FILL: AiFillConf = {
  enabled: true,
  output_type: "text",
  mission: "",
  context_rules: "",
  output_spec: "",
  inputs: { record: { enabled: true }, interactions: { enabled: false, limit: 20 } },
  tools: [],
  refresh: { on_create: true, on_change: true, schedule: null },
  min_confidence: 0,
};

/** Save gate: an AI field needs an output type, a mission and an output spec. */
export function aiFillComplete(v?: AiFillConf): boolean {
  return !!v && !!v.output_type && !!v.mission?.trim() && !!v.output_spec?.trim();
}

const labelStyle: React.CSSProperties = {
  fontSize: 13, color: "var(--muted)",
};
const inputStyle: React.CSSProperties = {
  width: "100%", padding: "6px 8px", fontSize: 14,
  background: "var(--bg)", color: "inherit",
  border: "1px solid var(--border)", borderRadius: 6,
  boxSizing: "border-box",
};
const textareaStyle: React.CSSProperties = {
  ...inputStyle, minHeight: 60, resize: "vertical",
  fontFamily: "inherit", lineHeight: 1.4,
};

export function AiFillConfig({
  value, module, onChange, onApplyTemplate, simple,
}: {
  value: AiFillConf;
  module: string;
  onChange: (v: AiFillConf) => void;
  onApplyTemplate: (tpl: AiFieldTemplate) => void;
  // simple: hide the detailed config (output_type … refresh); keep only the
  // one-line generate row + template select. Used by the AI-field 极简模式.
  simple?: boolean;
}) {
  const t = useT();
  const templates = aiFieldTemplates(t);
  const v = { ...DEFAULT_AI_FILL, ...value };
  const set = (patch: Partial<AiFillConf>) => onChange({ ...v, ...patch });

  // 一句话生成：把用户的一句话需求交给后端起草整套配置，再走 onApplyTemplate
  // (与模板同形)一次性灌进下面所有配置框。
  const [intent, setIntent] = useState("");
  const [genBusy, setGenBusy] = useState(false);
  const [genErr, setGenErr] = useState("");

  async function onGenerate() {
    if (!intent.trim()) return;
    setGenBusy(true); setGenErr("");
    try {
      const { config } = await generateAiFieldConfig(module, intent.trim());
      onApplyTemplate({
        key: "generated",
        mode: config.output_type === "picklist" ? "classify"
          : config.output_type === "number" ? "prompt" : "summarize",
        output_type: config.output_type,
        label: config.label || "",
        mission: config.mission || "",
        context_rules: config.context_rules || "",
        with_interactions: config.with_interactions,
      });
    } catch {
      setGenErr(t("admin.schema.aiFill.gen_failed"));
    } finally { setGenBusy(false); }
  }

  const checkboxStyle: React.CSSProperties = {
    width: 14, height: 14, flexShrink: 0, margin: 0,
  };
  const chkRow: React.CSSProperties = {
    display: "flex", alignItems: "center", gap: 6,
    fontSize: 14, cursor: "pointer",
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{
        fontSize: 13, fontWeight: 600, textTransform: "uppercase",
        color: "var(--muted)",
      }}>
        {t("admin.schema.aiFill.title")}
      </div>

      {/* 一句话生成 — 小白入口：描述一句话需求，AI 起草整套配置。 */}
      <div style={{
        display: "flex", flexDirection: "column", gap: 4,
        padding: 10, border: "1px solid var(--border)", borderRadius: 8,
        background: "var(--bg)",
      }}>
        <span style={labelStyle}>{t("admin.schema.aiFill.gen_label")}</span>
        <div style={{ display: "flex", gap: 6 }}>
          <input
            value={intent}
            onChange={(e) => setIntent(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !genBusy && intent.trim()) {
                e.preventDefault(); onGenerate();
              }
            }}
            placeholder={t("admin.schema.aiFill.gen_ph")}
            style={{ ...inputStyle, flex: 1 }}
          />
          <button
            className="primary"
            onClick={onGenerate}
            disabled={genBusy || !intent.trim()}
            style={{ flexShrink: 0, whiteSpace: "nowrap" }}
          >
            {genBusy
              ? t("admin.schema.aiFill.generating")
              : t("admin.schema.aiFill.generate")}
          </button>
        </div>
        {genErr
          ? <small style={{ color: "var(--danger)" }}>{genErr}</small>
          : <small className="muted">{t("admin.schema.aiFill.gen_hint")}</small>}
      </div>

      {/* 选个模板 — prefills everything below from a ready-made preset. */}
      <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        <span style={labelStyle}>{t("admin.schema.aiFill.template")}</span>
        <select
          value=""
          onChange={(e) => {
            const tpl = templates.find((x) => x.key === e.target.value);
            if (tpl) onApplyTemplate(tpl);
          }}
          style={inputStyle}
        >
          <option value="">{t("admin.schema.aiFill.template_none")}</option>
          {templates.map((tpl) => (
            <option key={tpl.key} value={tpl.key}>{tpl.label}</option>
          ))}
        </select>
      </label>

      {/* 简单模式：详细框被折叠，给一行确认摘要，让用户看到模板/生成确实生效。 */}
      {simple && v.mission?.trim() && v.output_spec?.trim() ? (
        <div style={{
          padding: 10, border: "1px solid var(--border)", borderRadius: 8,
          background: "var(--bg)", display: "flex", flexDirection: "column", gap: 4,
        }}>
          <span style={{ color: "var(--accent)", fontWeight: 600, fontSize: 13 }}>
            ✓ {t("admin.schema.aiFill.configured")}
          </span>
          <small className="muted">
            {t("admin.schema.aiFill.sum_type")}：{t(`admin.schema.aiFill.ot.${v.output_type}`)} · {v.mission}
          </small>
        </div>
      ) : null}

      {!simple && (<>
      {/* 输出类型 — drives the real DB column type on save. */}
      <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        <span style={labelStyle}>{t("admin.schema.aiFill.output_type")}</span>
        <select
          value={v.output_type}
          onChange={(e) => set({ output_type: e.target.value as AiFillConf["output_type"] })}
          style={inputStyle}
        >
          {(["text", "number", "picklist", "boolean"] as const).map((o) => (
            <option key={o} value={o}>{t(`admin.schema.aiFill.ot.${o}`)}</option>
          ))}
        </select>
      </label>

      {/* 使命 */}
      <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        <span style={labelStyle}>{t("admin.schema.aiFill.mission")}</span>
        <textarea
          value={v.mission}
          onChange={(e) => set({ mission: e.target.value })}
          placeholder={t("admin.schema.aiFill.ph.mission")}
          style={textareaStyle}
        />
        <small className="muted">{t("admin.schema.aiFill.help.mission")}</small>
      </label>

      {/* 上下文与规则 */}
      <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        <span style={labelStyle}>{t("admin.schema.aiFill.context_rules")}</span>
        <textarea
          value={v.context_rules || ""}
          onChange={(e) => set({ context_rules: e.target.value })}
          placeholder={t("admin.schema.aiFill.ph.rules")}
          style={textareaStyle}
        />
        <small className="muted">{t("admin.schema.aiFill.help.rules")}</small>
      </label>

      {/* 产出规格 */}
      <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        <span style={labelStyle}>{t("admin.schema.aiFill.output_spec")}</span>
        <textarea
          value={v.output_spec}
          onChange={(e) => set({ output_spec: e.target.value })}
          placeholder={t("admin.schema.aiFill.ph.output_spec")}
          style={textareaStyle}
        />
        <small className="muted">{t("admin.schema.aiFill.help.output_spec")}</small>
      </label>

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

      {/* 带关联会话 */}
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

      {/* 刷新触发 */}
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        <span style={labelStyle}>{t("admin.schema.aiFill.refresh")}</span>
        <label style={chkRow}>
          <input
            type="checkbox"
            checked={!!v.refresh?.on_create}
            style={checkboxStyle}
            onChange={(e) => set({ refresh: { ...v.refresh, on_create: e.target.checked } })}
          />
          {t("admin.schema.aiFill.on_create")}
        </label>
        <label style={chkRow}>
          <input
            type="checkbox"
            checked={!!v.refresh?.on_change}
            style={checkboxStyle}
            onChange={(e) => set({ refresh: { ...v.refresh, on_change: e.target.checked } })}
          />
          {t("admin.schema.aiFill.on_change")}
        </label>
        <span className="muted" style={{ fontSize: 13 }}>
          {t("admin.schema.aiFill.schedule_soon")}
        </span>
      </div>
      </>)}
    </div>
  );
}
