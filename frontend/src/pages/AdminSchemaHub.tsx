import { useEffect, useState } from "react";
import { useT } from "@/lib/i18n";
import { getFieldProposalsPendingCount } from "@/lib/api";
import { AdminSchemaPage } from "./schema/AdminSchema";
import { AdminWorkflowsPage } from "./AdminWorkflows";
import { AdminFieldProposalsPage } from "./AdminFieldProposals";
import { AdminSchemaChangelogPage } from "./AdminSchemaChangelog";

type Tab = "fields" | "workflows" | "proposals" | "changelog";

/**
 * /admin/schema — unified schema-evolution hub.
 *
 * Combines the three schema-evo admin surfaces into one page with a
 * tab bar so admins don't need three separate nav entries.
 *
 *   字段管理      — module field builder + picklist option editor
 *   字段提议      — AI-initiated field proposal review queue
 *   变更日志      — append-only audit trail
 */
export function AdminSchemaHub({ initialTab }: { initialTab?: Tab }) {
  const translate = useT();
  const [tab, setTab] = useState<Tab>(initialTab ?? "fields");

  // Pending field-proposal count → number badge on the 字段提议 tab.
  const [pendingCount, setPendingCount] = useState(0);
  function loadPendingCount() {
    getFieldProposalsPendingCount()
      .then(r => setPendingCount(r.count))
      .catch(() => { /* count badge is best-effort — ignore errors */ });
  }
  useEffect(() => { loadPendingCount(); }, []);

  const tabs: { key: Tab; label: string }[] = [
    { key: "fields",    label: translate("admin.schema_hub.tab.fields") },
    { key: "workflows", label: translate("admin.schema_hub.tab.workflows") },
    { key: "proposals", label: translate("admin.schema_hub.tab.proposals") },
    { key: "changelog", label: translate("admin.schema_hub.tab.changelog") },
  ];

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      {/* Tab bar */}
      <div style={{
        display: "flex",
        gap: 0,
        borderBottom: "1px solid var(--border)",
        padding: "0 24px",
        background: "var(--bg)",
        flexShrink: 0,
      }}>
        {tabs.map(t => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 6,
              padding: "12px 20px",
              fontSize: 14,
              fontWeight: tab === t.key ? 600 : 400,
              color: tab === t.key ? "var(--fg)" : "var(--muted)",
              background: "transparent",
              border: "none",
              borderBottom: tab === t.key
                ? "2px solid var(--accent)"
                : "2px solid transparent",
              cursor: "pointer",
              marginBottom: -1,
              transition: "color 0.15s, border-color 0.15s",
            }}
          >
            {t.label}
            {t.key === "proposals" && pendingCount > 0 && (
              <span style={{
                minWidth: 18, height: 18, padding: "0 5px",
                borderRadius: 9,
                background: "var(--danger)", color: "var(--on-danger)",
                fontSize: 13, fontWeight: 700,
                display: "inline-flex", alignItems: "center", justifyContent: "center",
                lineHeight: 1,
              }}>{pendingCount > 99 ? "99+" : pendingCount}</span>
            )}
          </button>
        ))}
      </div>

      {/* Tab body */}
      <div style={{ flex: 1, overflow: "auto" }}>
        {tab === "fields"    && <AdminSchemaPage />}
        {tab === "workflows" && <AdminWorkflowsPage />}
        {tab === "proposals" && <AdminFieldProposalsPage onReviewed={loadPendingCount} />}
        {tab === "changelog" && <AdminSchemaChangelogPage />}
      </div>
    </div>
  );
}
