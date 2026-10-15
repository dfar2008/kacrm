import { useEffect, useState } from "react";
import { restorePayment } from "@/lib/api";
import { useT } from "@/lib/i18n";

/** Lives above the detail panel, so deselecting a deleted row keeps recovery available. */
export function PaymentRestoreNotice({ record, onClose, onRestored }: {
  record: { id: number; at: number }; onClose: () => void; onRestored: (id: number) => void;
}) {
  const t = useT();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(false);
  useEffect(() => {
    const timer = setTimeout(onClose, Math.max(0, record.at + 120000 - Date.now()));
    return () => clearTimeout(timer);
  }, [record, onClose]);
  return <div role="status" aria-live="polite" style={{ position: "fixed", bottom: 20, left: "50%", transform: "translateX(-50%)", zIndex: 60, width: "min(460px, calc(100vw - 32px))", boxSizing: "border-box", padding: 14, borderRadius: 12, border: "1px solid var(--border)", background: "var(--panel)", color: "var(--fg-strong)", boxShadow: "var(--shadow-popover)", display: "flex", flexWrap: "wrap", alignItems: "center", gap: 10 }}>
    <span style={{ flex: "1 1 200px", fontSize: 14 }}>{t(error ? "payment.restore_failed" : "payment.deleted")}</span>
    <button className="primary" disabled={busy} style={{ minHeight: 44 }} onClick={async () => {
      setBusy(true); setError(false);
      try { await restorePayment(record.id); onRestored(record.id); }
      catch { setError(true); }
      finally { setBusy(false); }
    }}>{t(busy ? "payment.restoring" : "payment.restore")}</button>
    <button className="ghost" aria-label={t("common.close")} disabled={busy} style={{ minWidth: 44, minHeight: 44 }} onClick={onClose}>×</button>
  </div>;
}
