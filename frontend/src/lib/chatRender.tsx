/**
 * Chat bubble rendering helpers — ported from hermes_cloud/static/app.js.
 *
 * All user-facing strings go through the i18n `t` translator the caller
 * passes in; the CRM module/verb dictionaries are just key lookups now.
 */
import { renderMarkdown } from "./markdown";
import type { T, DictKey } from "./i18n";

// ── tool-name → verb-key + module-key ─────────────────────────────────
const CRM_TOOL_VERB_KEYS: Record<string, DictKey> = {
  crm_search: "crm.tool.search",
  crm_get: "crm.tool.get",
  crm_create: "crm.tool.create",
  crm_update: "crm.tool.update",
  crm_list_modules: "crm.tool.list_modules",
  crm_get_schema: "crm.tool.get_schema",
  crm_extract_fields: "crm.tool.extract_fields",
  crm_draft_email: "crm.tool.draft_email",
};

const CRM_MODULE_KEYS: Record<string, DictKey> = {
  accounts: "crm.module.accounts",
  contacts: "crm.module.contacts",
  leads: "crm.module.leads",
  potentials: "crm.module.potentials",
  quotes: "crm.module.quotes",
  salesorders: "crm.module.salesorders",
  tasks: "crm.module.tasks",
  products: "crm.module.products",
  receivables: "crm.module.receivables",
  payments: "crm.module.payments",
};

/** Backwards-compat helper: resolve a module slug to its display label. */
export function crmModuleLabel(mod: string, t: T): string {
  const k = CRM_MODULE_KEYS[mod];
  return k ? t(k) : mod;
}

export function crmToolLabel(toolName: string, t: T): string {
  for (const [prefix, verbKey] of Object.entries(CRM_TOOL_VERB_KEYS)) {
    if (toolName.startsWith(prefix)) {
      const label = t(verbKey);
      const mod = toolName.slice(prefix.length + 1);
      const modLabel = mod ? crmModuleLabel(mod, t) : "";
      return mod ? `${label} ${modLabel}` : label;
    }
  }
  return toolName;
}

function toolIcon(name: string): string {
  if (name.startsWith("crm_")) return "📊";
  if (name.includes("email") || name.includes("mail")) return "✉️";
  if (name.includes("search") || name.includes("find")) return "🔍";
  if (name.includes("read") || name.includes("get")) return "📖";
  if (name.includes("write") || name.includes("edit")) return "✏️";
  if (name.includes("bash") || name.includes("shell")) return "⚙️";
  return "🔧";
}

// ── CRM projection renderers (HTML strings) ──────────────────────────
function escHtml(s: string): string {
  return String(s ?? "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function renderCrmSearchCard(toolName: string, data: any, t: T): string | null {
  const records = data.data || data.records || [];
  const total = data.total || records.length;
  const modName = toolName.replace("crm_search_", "");
  const modLabel = crmModuleLabel(modName, t);

  if (!records.length) {
    return `<div class="crm-card"><div class="crm-card-empty">${escHtml(t("crm.card.empty_for", { mod: modLabel }))}</div></div>`;
  }

  const skip = new Set(["id", "deleted", "deleted_at", "created_by", "updated_by", "password"]);
  const allKeys = Object.keys(records[0] || {}).filter(k => !skip.has(k));
  const cols = allKeys.slice(0, 6);

  const headerHtml = cols.map(c => `<th class="crm-card-th">${escHtml(c)}</th>`).join("");
  const rowsHtml = records.slice(0, 10).map((r: any) => {
    const cells = cols.map(c => {
      let v = r[c];
      if (v === null || v === undefined) v = "—";
      if (typeof v === "object") v = JSON.stringify(v);
      return `<td class="crm-card-td">${escHtml(String(v).slice(0, 50))}</td>`;
    }).join("");
    return `<tr class="crm-card-row">${cells}</tr>`;
  }).join("");
  const moreText = records.length < total
    ? `<div class="crm-card-footer">${escHtml(t("crm.card.more", { shown: records.length, total }))}</div>` : "";
  return `
    <div class="crm-card">
      <div class="crm-card-header">
        <span class="crm-card-icon">📊</span>
        <span class="crm-card-title">${escHtml(modLabel)}</span>
        <span class="crm-card-badge">${escHtml(t("crm.card.badge_count", { n: total }))}</span>
      </div>
      <div class="crm-card-table-wrap">
        <table class="crm-card-table">
          <thead><tr>${headerHtml}</tr></thead>
          <tbody>${rowsHtml}</tbody>
        </table>
      </div>
      ${moreText}
    </div>`;
}

function renderCrmDetailCard(toolName: string, data: any, t: T): string | null {
  const record = data.record || data.data || data || {};
  const fields = data.fields || [];
  const modName = toolName.replace("crm_get_", "");
  const modLabel = crmModuleLabel(modName, t);
  const name = record.name || record.title
    || record.accounts_name || record.contacts_name
    || record.leads_name || record.potentials_name
    || `#${record.id || ""}`;

  const skip = new Set(["id", "deleted", "deleted_at", "password"]);
  let fieldsHtml: string;
  if (fields.length) {
    fieldsHtml = fields
      .filter((f: any) => !skip.has(f.field_name))
      .slice(0, 14)
      .map((f: any) => {
        const raw = record[f.field_name];
        const display = record[f.field_name + "_display"] ?? raw;
        const out = display === null || display === undefined || display === "" ? "—" : String(display);
        return `<div class="crm-card-field">
          <span class="crm-card-field-label">${escHtml(f.field_label || f.field_name)}</span>
          <span class="crm-card-field-value">${escHtml(out)}</span>
        </div>`;
      }).join("");
  } else {
    fieldsHtml = Object.entries(record)
      .filter(([k]) => !skip.has(k) && !k.endsWith("_display"))
      .slice(0, 14)
      .map(([k, v]) => {
        const display = v === null || v === undefined ? "—"
          : typeof v === "object" ? JSON.stringify(v) : String(v);
        return `<div class="crm-card-field">
          <span class="crm-card-field-label">${escHtml(k)}</span>
          <span class="crm-card-field-value">${escHtml(display)}</span>
        </div>`;
      }).join("");
  }
  return `
    <div class="crm-card crm-card-detail">
      <div class="crm-card-header">
        <span class="crm-card-icon">📋</span>
        <span class="crm-card-title">${escHtml(String(name))}</span>
        <span class="crm-card-badge">${escHtml(modLabel)}</span>
      </div>
      <div class="crm-card-fields">${fieldsHtml}</div>
    </div>`;
}

function renderCrmMutationCard(verb: string, data: any, t: T): string {
  if (data.success) {
    const idText = data.id ? ` #${data.id}` : "";
    return `<div class="crm-card crm-card-success"><span class="crm-card-icon">✅</span> ${escHtml(t("crm.status.success", { verb, idText }))}</div>`;
  }
  if (data.pending_approval) {
    return `<div class="crm-card crm-card-pending"><span class="crm-card-icon">⏳</span> ${escHtml(t("crm.status.pending", { verb }))}</div>`;
  }
  return `<div class="crm-card crm-card-error"><span class="crm-card-icon">❌</span> ${escHtml(t("crm.status.failed", { verb }))}</div>`;
}

function renderCrmModulesCard(data: any, t: T): string | null {
  const mods = data.modules || data || [];
  if (!Array.isArray(mods) || !mods.length) return null;
  const items = mods.map((m: any) => {
    const name = m.name || "";
    const label = CRM_MODULE_KEYS[name] ? t(CRM_MODULE_KEYS[name]) : (m.title || name);
    return `<span class="crm-card-module-item">${escHtml(label)}</span>`;
  }).join("");
  return `<div class="crm-card"><div class="crm-card-header"><span class="crm-card-icon">📁</span><span class="crm-card-title">${escHtml(t("crm.card.modules_title"))}</span></div><div class="crm-card-modules">${items}</div></div>`;
}

export function renderCrmProjection(toolName: string, data: any, t: T): string | null {
  if (!toolName || !data) return null;
  try {
    if (toolName.startsWith("crm_search_")) return renderCrmSearchCard(toolName, data, t);
    if (toolName.startsWith("crm_get_")) return renderCrmDetailCard(toolName, data, t);
    if (toolName.startsWith("crm_create_")) return renderCrmMutationCard(t("crm.tool.create"), data, t);
    if (toolName.startsWith("crm_update_")) return renderCrmMutationCard(t("crm.tool.update"), data, t);
    if (toolName === "crm_list_modules") return renderCrmModulesCard(data, t);
  } catch { /* projection is best-effort */ }
  return null;
}

// ── Tool-call block props ────────────────────────────────────────────
export interface ToolLike {
  tool: string;
  status: "running" | "done" | "error";
  input?: any;
  output?: any;
  error?: string;
}

export function toolHeaderInfo(tool: ToolLike, t: T): {
  icon: string; label: string; isCrm: boolean;
  statusClass: string; statusText: string;
} {
  const isCrm = tool.tool.startsWith("crm_");
  const icon = isCrm ? "📊" : toolIcon(tool.tool);
  const label = isCrm ? crmToolLabel(tool.tool, t) : tool.tool;
  const statusClass = tool.status === "done" ? "tool-call-status-done"
    : tool.status === "error" ? "tool-call-status-error"
    : "tool-call-status-running";
  const statusText = tool.status === "done" ? t("crm.tool.status.done")
    : tool.status === "error" ? t("crm.tool.status.error", { msg: tool.error || t("crm.tool.status.error_fallback") })
    : t("crm.tool.status.running");
  return { icon, label, isCrm, statusClass, statusText };
}

// Exposed for text segments.
export { renderMarkdown };
