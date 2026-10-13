import { useT } from "@/lib/i18n";
import { ApprovalConfigTab } from "./ApprovalConfigTab";

/** 模块审批流配置模态框 —— 与字段写回(WritebackRulesModal)同范式,
 *  per-module 结构配置走模态,从模块管理页打开。 */
export function ApprovalConfigModal({
  module, onClose,
}: { module: string; onClose: () => void }) {
  const t = useT();
  return (
    <div
      role="dialog"
      aria-label={t("approvalConfig.title")}
      style={{
        position: "fixed", inset: 0, zIndex: 1000, background: "var(--overlay)",
        display: "flex", alignItems: "center", justifyContent: "center",
      }}
      onClick={onClose}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={{
          width: 560, maxHeight: "85vh", overflowY: "auto", background: "var(--panel)",
          border: "1px solid var(--border)", borderRadius: 10, padding: 20,
        }}
      >
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
          <h3 style={{ margin: 0 }}>{t("approvalConfig.title")} · {module}</h3>
          <button
            onClick={onClose}
            style={{ background: "transparent", border: "none", fontSize: 18, cursor: "pointer", color: "var(--muted)" }}
            aria-label="close"
          >✕</button>
        </div>
        <ApprovalConfigTab module={module} bare />
      </div>
    </div>
  );
}
