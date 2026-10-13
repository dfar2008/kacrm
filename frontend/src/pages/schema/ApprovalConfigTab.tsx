import { useEffect, useState, useCallback } from "react";
import { useT } from "@/lib/i18n";
import {
  getApprovalConfig, putApprovalConfig,
  type ApprovalConfigStep, ApiError,
} from "@/lib/api";

interface DraftStep {
  step_name: string;
  approvers: string;  // 逗号分隔的用户 ID 文本(编辑态)
}

function toDraft(steps: ApprovalConfigStep[]): DraftStep[] {
  return steps.map(s => ({
    step_name: s.step_name,
    approvers: (s.approver_ids || []).join(","),
  }));
}

function toPayload(drafts: DraftStep[]): ApprovalConfigStep[] {
  return drafts.map((d, i) => ({
    step_name: d.step_name.trim() || `Step ${i + 1}`,
    approver_ids: d.approvers.split(",").map(s => s.trim()).filter(Boolean).map(Number)
      .filter(n => Number.isFinite(n) && n > 0),
  }));
}

/** 模块审批流配置面板(用户对话用 crm_set_approval_chain;此处为 SPA 入口)。
 *  ``bare`` 去掉外层卡片(放在 modal 里时由 modal 提供边框/内边距)。 */
export function ApprovalConfigTab({ module, bare = false }: { module: string; bare?: boolean }) {
  const t = useT();
  const [steps, setSteps] = useState<DraftStep[]>([]);
  const [allowEdit, setAllowEdit] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);

  // 友好错误:403 → 管理员限定,不把后端原始 JSON 甩到界面。
  const friendly = useCallback((e: unknown) =>
    e instanceof ApiError
      ? (e.status === 403 ? t("approvalConfig.adminOnly") : t("approvalConfig.err"))
      : t("approvalConfig.err"), [t]);

  const load = useCallback(async () => {
    setLoading(true); setErr(null); setLoadErr(null); setMsg(null);
    try {
      const r = await getApprovalConfig(module);
      if (r.config) {
        setSteps(toDraft(r.config.steps as ApprovalConfigStep[]));
        setAllowEdit(!!r.config.allow_edit_after_approve);
      } else {
        setSteps([]); setAllowEdit(false);
      }
    } catch (e) {
      setLoadErr(friendly(e));
    } finally {
      setLoading(false);
    }
  }, [module, friendly]);

  useEffect(() => { void load(); }, [load]);

  async function save() {
    setSaving(true); setErr(null); setMsg(null);
    try {
      await putApprovalConfig(module, toPayload(steps), allowEdit ? 1 : 0);
      setMsg(t("approvalConfig.saved"));
    } catch (e) {
      setErr(friendly(e));
    } finally {
      setSaving(false);
    }
  }

  const input: React.CSSProperties = {
    padding: "6px 10px", borderRadius: 6, border: "1px solid var(--border)",
    fontSize: 13, background: "var(--bg, transparent)", color: "inherit",
  };
  const btn: React.CSSProperties = {
    padding: "6px 12px", borderRadius: 6, border: "1px solid var(--border)",
    fontSize: 13, cursor: "pointer",
  };

  return (
    <div style={bare ? {} : {
      border: "1px solid var(--border)", borderRadius: 12,
      padding: 16, marginTop: 16, background: "var(--panel)",
    }}>
      {!bare && <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 4 }}>{t("approvalConfig.title")}</div>}
      <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 12 }}>{t("approvalConfig.desc")}</div>

      {loading ? (
        <div style={{ color: "var(--muted)", fontSize: 13 }}>{t("common.loading")}</div>
      ) : loadErr ? (
        <div style={{ color: "var(--danger, #dc2626)", fontSize: 13 }}>{loadErr}</div>
      ) : (
        <>
          {steps.length === 0 && (
            <div style={{ color: "var(--muted)", fontSize: 13, marginBottom: 10 }}>
              {t("approvalConfig.empty")}
            </div>
          )}
          {steps.map((s, i) => (
            <div key={i} style={{ display: "flex", gap: 8, marginBottom: 8, alignItems: "center", flexWrap: "wrap" }}>
              <span style={{ color: "var(--muted)", fontSize: 13, minWidth: 18 }}>{i + 1}</span>
              <input
                style={{ ...input, width: 140 }}
                placeholder={t("approvalConfig.stepName")}
                value={s.step_name}
                onChange={e => setSteps(v => v.map((x, j) => j === i ? { ...x, step_name: e.target.value } : x))}
              />
              <input
                style={{ ...input, flex: 1, minWidth: 200 }}
                placeholder={t("approvalConfig.approvers")}
                value={s.approvers}
                onChange={e => setSteps(v => v.map((x, j) => j === i ? { ...x, approvers: e.target.value } : x))}
              />
              <button
                style={{ ...btn, color: "var(--danger, #dc2626)" }}
                onClick={() => setSteps(v => v.filter((_, j) => j !== i))}
              >{t("approvalConfig.removeStep")}</button>
            </div>
          ))}

          <button
            style={{ ...btn, marginTop: 4 }}
            onClick={() => setSteps(v => [...v, { step_name: "", approvers: "" }])}
          >+ {t("approvalConfig.addStep")}</button>

          <label style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 14, fontSize: 13 }}>
            <input type="checkbox" checked={allowEdit} onChange={e => setAllowEdit(e.target.checked)} />
            {t("approvalConfig.allowEdit")}
          </label>

          <div style={{ marginTop: 16, display: "flex", gap: 10, alignItems: "center" }}>
            <button
              style={{ ...btn, background: "var(--accent, #2563eb)", color: "#fff", border: "none", opacity: saving ? 0.6 : 1 }}
              disabled={saving}
              onClick={save}
            >{t("approvalConfig.save")}</button>
            {msg && <span style={{ color: "var(--ok, #16a34a)", fontSize: 13 }}>{msg}</span>}
            {err && <span style={{ color: "var(--danger, #dc2626)", fontSize: 13 }}>{err}</span>}
          </div>
        </>
      )}
    </div>
  );
}
