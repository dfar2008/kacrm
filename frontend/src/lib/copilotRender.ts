import type { CopilotToolExecution } from "@/lib/api";
import type { T } from "@/lib/i18n";

/** Render a single tool_execution from a Copilot endpoint as a chip line.
 *  Shared by CopilotPanel (CRM) and the scoped side panels (schema /
 *  dashboard) so the assistant turn renders identically everywhere. */
export function formatToolExecution(t: T, te: CopilotToolExecution): string {
  if (!te.ok) return t("copilot.tool.fail", {
    name: te.name, err: te.error || t("copilot.tool.unknown_err"),
  });
  if (te.name === "update_record") {
    const r = te.result || {};
    const label = r.field_label || r.field || t("copilot.tool.field_fallback");
    const v = r.new_value;
    return t("copilot.tool.update_done", {
      label, value: typeof v === "object" ? JSON.stringify(v) : String(v ?? ""),
    });
  }
  if (te.name === "add_note") {
    const raw = String(te.result?.content || "");
    const preview = raw.slice(0, 40);
    if (!preview) return t("copilot.tool.add_note");
    const ell = raw.length >= 40 ? "…" : "";
    return t("copilot.tool.add_note_with_preview", { preview: preview + ell });
  }
  if (te.name === "get_record_fields") {
    const n = Array.isArray(te.result) ? te.result.length : 0;
    return t("copilot.tool.get_fields", { n });
  }
  return `• ${te.name}`;
}
