import { useEffect, useState, useCallback } from "react";
import { useT } from "@/lib/i18n";
import { useRouter } from "@/lib/router";
import {
  fetchPendingApprovals, approveChain, rejectChain,
  type PendingApproval, ApiError,
} from "@/lib/api";

export function MyApprovalsPage() {
  const t = useT();
  const { navigate } = useRouter();
  const [items, setItems] = useState<PendingApproval[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(0);

  const load = useCallback(async () => {
    setLoading(true); setErr(null);
    try {
      const r = await fetchPendingApprovals();
      setItems(r.items);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : t("myApprovals.err"));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => { void load(); }, [load]);

  async function act(id: number, fn: () => Promise<unknown>) {
    setBusy(id);
    try { await fn(); await load(); }
    catch (e) { setErr(e instanceof ApiError ? e.message : t("myApprovals.err")); }
    finally { setBusy(0); }
  }

  const th: React.CSSProperties = {
    textAlign: "left", padding: "8px 12px", fontWeight: 500,
    color: "var(--muted)", fontSize: 13, borderBottom: "1px solid var(--border)",
  };
  const td: React.CSSProperties = { padding: "10px 12px", fontSize: 14, verticalAlign: "middle" };
  const btn: React.CSSProperties = {
    padding: "4px 10px", borderRadius: 6, fontSize: 13, marginRight: 6,
    border: "1px solid var(--border)", cursor: "pointer",
  };

  return (
    <div style={{ padding: 24, maxWidth: 960, margin: "0 auto" }}>
      <h2 style={{ fontSize: 18, fontWeight: 600, marginBottom: 16 }}>{t("myApprovals.title")}</h2>
      {err && <div style={{ color: "var(--danger, #dc2626)", marginBottom: 12 }}>{err}</div>}
      {loading ? (
        <div style={{ color: "var(--muted)" }}>{t("common.loading")}</div>
      ) : items.length === 0 ? (
        <div style={{ color: "var(--muted)", padding: 24, textAlign: "center" }}>
          {t("myApprovals.empty")}
        </div>
      ) : (
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead>
            <tr>
              <th style={th}>{t("myApprovals.col.module")}</th>
              <th style={th}>{t("myApprovals.col.record")}</th>
              <th style={th}>{t("myApprovals.col.step")}</th>
              <th style={th}>{t("myApprovals.col.submitted")}</th>
              <th style={th} />
            </tr>
          </thead>
          <tbody>
            {items.map(it => (
              <tr key={it.id} style={{ borderBottom: "1px solid var(--border)" }}>
                <td style={td}>{it.module_name}</td>
                <td style={td}>#{it.data_id}</td>
                <td style={td}>{it.step_name}</td>
                <td style={{ ...td, color: "var(--muted)" }}>
                  {it.created_at ? new Date(it.created_at).toLocaleString() : "—"}
                </td>
                <td style={{ ...td, textAlign: "right", whiteSpace: "nowrap" }}>
                  <button
                    style={{ ...btn, background: "var(--ok, #16a34a)", color: "#fff", border: "none" }}
                    disabled={busy === it.id}
                    onClick={() => act(it.id, () => approveChain(it.id, ""))}
                  >{t("myApprovals.approve")}</button>
                  <button
                    style={{ ...btn, color: "var(--danger, #dc2626)" }}
                    disabled={busy === it.id}
                    onClick={() => act(it.id, () => rejectChain(it.id, window.prompt(t("approval.reject_reason")) || ""))}
                  >{t("myApprovals.reject")}</button>
                  <button style={btn} onClick={() => navigate("/crm")}>{t("myApprovals.open")}</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
