/**
 * aiFieldTemplates — ready-made prompt presets for the AI Autofill "选个模板"
 * dropdown. Each template prefills mission / context_rules (and toggles
 * interaction context) so a non-technical admin has a worked example to adapt
 * instead of a blank box. Filtered by the field's current mode + output type.
 *
 * No output_spec: the output shape is decided by the AI from mode + field type
 * + the field's own options (see backend _output_guidance), not authored here.
 */
import type { AiFillConf } from "../../lib/api";

export type AiTplMode = "classify" | "summarize" | "web_agent" | "prompt";

export interface AiFieldTemplate {
  key: string;
  /** Which of the 4 autofill modes this preset belongs to (drives filtering). */
  mode: AiTplMode;
  output_type: AiFillConf["output_type"];
  label: string; // suggested field label (from i18n)
  mission: string;
  context_rules: string;
  with_interactions?: boolean;
}

/** t is the translate fn; build content from i18n keys. */
export function aiFieldTemplates(t: (k: string) => string): AiFieldTemplate[] {
  const tp = (k: string) => t(`admin.schema.aiFill.tpl.${k}`);
  const mk = (
    key: string,
    mode: AiTplMode,
    output_type: AiFillConf["output_type"],
    with_interactions = false,
  ): AiFieldTemplate => ({
    key,
    mode,
    output_type,
    label: tp(`${key}.label`),
    mission: tp(`${key}.mission`),
    context_rules: tp(`${key}.rules`),
    with_interactions,
  });
  return [
    // ── 分类 Classify(picklist)──────────────────────────────────────────
    mk("icp", "classify", "picklist"),
    mk("risk", "classify", "picklist", true),
    mk("intent", "classify", "picklist", true),
    mk("sentiment", "classify", "picklist", true),
    mk("priority", "classify", "picklist", true),
    // ── 摘要 Summarize(text)─────────────────────────────────────────────
    mk("summary", "summarize", "text", true),
    mk("recent_contact", "summarize", "text", true),
    mk("needs", "summarize", "text", true),
    mk("concerns", "summarize", "text", true),
    // ── 联网查找 Web Agent(text)──────────────────────────────────────────
    mk("company_news", "web_agent", "text"),
    mk("funding", "web_agent", "text"),
    mk("company_profile", "web_agent", "text"),
    mk("key_people", "web_agent", "text"),
    // ── 自定义 Prompt(text/number)────────────────────────────────────────
    mk("nextaction", "prompt", "text", true),
    mk("score", "prompt", "number", true),
    mk("email_draft", "prompt", "text", true),
    mk("talking_points", "prompt", "text", true),
  ];
}
