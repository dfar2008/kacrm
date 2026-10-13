/**
 * DeleteModuleModal — 安全删除一个顶层自定义模块的确认弹窗。
 *
 * 调 DELETE /crm/modules/{name}(deprovision_module):删前自动归档整模块
 * (可还原),级联删表/记录/字段/权限/子明细。破坏性操作,要求输入模块名确认。
 */
import { useState } from "react";
import { deleteModule, ApiError } from "@/lib/api";
import { useT } from "@/lib/i18n";

export function DeleteModuleModal({
  name, subdetails, onClose, onDeleted,
}: {
  name: string;
  subdetails: string[];
  onClose: () => void;
  onDeleted: (name: string) => void;
}) {
  const t = useT();
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const ready = confirm.trim() === name;

  const submit = async () => {
    if (!ready) return;
    setBusy(true); setErr(null);
    try {
      await deleteModule(name, { reason: "admin schema UI delete" });
      onDeleted(name);
    } catch (e) {
      setErr(e instanceof ApiError
        ? (typeof e.detail === "string" ? e.detail : `HTTP ${e.status}`) : String(e));
      setBusy(false);
    }
  };

  return (
    <div role="dialog" aria-label={t("admin.schema.delete.title", { name })}
      style={{ position: "fixed", inset: 0, zIndex: 1000, background: "var(--overlay)",
        display: "flex", alignItems: "center", justifyContent: "center" }}
      onClick={onClose}>
      <div onClick={e => e.stopPropagation()} style={{ width: 440,
        background: "var(--panel)", border: "1px solid var(--border)",
        borderRadius: 10, padding: 20 }}>
        <h3 style={{ marginTop: 0 }}>{t("admin.schema.delete.title", { name })}</h3>
        <p style={{ fontSize: 14, marginTop: 0, color: "var(--danger)" }}>
          {t("admin.schema.delete.warn")}
        </p>
        {subdetails.length > 0 && (
          <p className="muted" style={{ fontSize: 14 }}>
            {t("admin.schema.delete.subdetails", { list: subdetails.join("、") })}
          </p>
        )}
        <p className="muted" style={{ fontSize: 13 }}>
          {t("admin.schema.delete.archived_note")}
        </p>

        <label style={{ display: "block", marginTop: 12, fontSize: 14, color: "var(--muted)" }}>
          {t("admin.schema.delete.confirm_prompt", { name })}
          <input autoFocus value={confirm} onChange={e => setConfirm(e.target.value)}
            placeholder={t("admin.schema.delete.confirm_placeholder")}
            style={{ display: "block", width: "100%", marginTop: 4, padding: "7px 9px",
              background: "var(--bg)", color: "inherit",
              border: `1px solid ${ready ? "var(--danger)" : "var(--border)"}`,
              borderRadius: 6 }} />
        </label>

        {err && <div style={{ color: "var(--danger)", marginTop: 10, fontSize: 14 }}>{err}</div>}

        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 18 }}>
          <button onClick={onClose}
            style={{ background: "transparent", border: "1px solid var(--border)",
              borderRadius: 6, padding: "6px 12px", cursor: "pointer", color: "inherit" }}>
            {t("admin.schema.delete.cancel")}
          </button>
          <button onClick={submit} disabled={busy || !ready}
            style={{ background: "var(--danger)", color: "var(--on-danger)",
              border: "none", borderRadius: 6, padding: "6px 14px", fontWeight: 600,
              cursor: busy || !ready ? "not-allowed" : "pointer",
              opacity: busy || !ready ? 0.5 : 1 }}>
            {busy ? "…" : t("admin.schema.delete.submit")}
          </button>
        </div>
      </div>
    </div>
  );
}
