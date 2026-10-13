import { useState } from "react";
import {
  getOnboardingCronSnapshot, type OnboardingCronJob,
} from "@/lib/api";
import { toast } from "@/lib/toast";
import { useT } from "@/lib/i18n";

/**
 * 新用户模板 — 定时任务编辑器。
 *
 * 模板存一份 cron job 快照(OnboardingCronJob[])。新建用户时直接复制到
 * 新 profile 的 cron/jobs.json 里(每条 mint 新 uuid + 标 provisioned_by=
 * "onboarding_template")。空就是空 —— 不自动 seed。
 *
 * 唯一的"从 admin 复制"动作是顶部按钮:点了从 /api/admin/onboarding-
 * template/cron-snapshot 拉 admin 当前 jobs.json,覆盖到本组件 state,
 * 标 dirty。admin 看一眼后点上面的「保存模板」才落盘。点了之后跟
 * admin 完全没关系。
 */

interface Props {
  jobs: OnboardingCronJob[];
  baseline: OnboardingCronJob[];
  onChange: (next: OnboardingCronJob[]) => void;
}

export function CronTemplateEditor({ jobs, baseline, onChange }: Props) {
  const t = useT();
  const [loadingSnapshot, setLoadingSnapshot] = useState(false);

  async function copyFromAdmin() {
    if (jobs.length > 0) {
      if (!confirm(t("onboarding.cron.confirm_overwrite", { n: jobs.length }))) return;
    }
    setLoadingSnapshot(true);
    try {
      const r = await getOnboardingCronSnapshot();
      onChange(r.jobs || []);
      toast(t("onboarding.cron.copied", { n: (r.jobs || []).length }), "success");
    } catch (e: any) {
      toast(e.message, "error");
    } finally {
      setLoadingSnapshot(false);
    }
  }

  function removeRow(idx: number) {
    const next = jobs.slice();
    next.splice(idx, 1);
    onChange(next);
  }

  function fmtSchedule(j: OnboardingCronJob): string {
    if (j.schedule_display) return j.schedule_display;
    if (j.schedule && j.schedule.expr) return j.schedule.expr;
    return "—";
  }

  return (
    <div style={{ flex: 1, minHeight: 0, overflow: "auto", padding: 16 }}>
      <div style={{
        ...cardStyle, padding: "12px 16px",
        display: "flex", alignItems: "center", gap: 12, marginBottom: 12,
      }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 6 }}>
          <span style={{ fontSize: 22, fontWeight: 600, color: "var(--accent-2)" }}>
            {jobs.length}
          </span>
          <span className="muted" style={{ fontSize: 14 }}>
            {t("onboarding.cron.count_suffix")}
          </span>
        </div>
        <div style={{ flex: 1 }} />
        <button
          onClick={copyFromAdmin}
          disabled={loadingSnapshot}
          style={primaryGhostBtn}
        >
          {loadingSnapshot ? t("onboarding.cron.loading") : t("onboarding.cron.copy_from_admin")}
        </button>
      </div>

      {jobs.length === 0 ? (
        <div style={cardStyle}>
          <div className="muted" style={{ fontSize: 14, lineHeight: 1.6 }}>
            {t("onboarding.cron.empty_hint")}
          </div>
        </div>
      ) : (
        <div style={{
          ...cardStyle, padding: 0, overflow: "hidden",
        }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 14 }}>
            <thead style={{
              fontSize: 13, textTransform: "uppercase",
              color: "var(--muted)", background: "var(--panel)",
            }}>
              <tr>
                <Th>{t("onboarding.cron.col.name")}</Th>
                <Th>{t("onboarding.cron.col.schedule")}</Th>
                <Th>{t("onboarding.cron.col.skill")}</Th>
                <Th>{t("onboarding.cron.col.deliver")}</Th>
                <Th>{t("onboarding.cron.col.enabled")}</Th>
                <Th style={{ textAlign: "right" }}>{t("onboarding.cron.col.actions")}</Th>
              </tr>
            </thead>
            <tbody>
              {jobs.map((j, idx) => {
                const baseNames = new Set(baseline.map(b => b.name));
                const isNew = !baseNames.has(j.name);
                return (
                  <tr key={`${j.name}-${idx}`}
                      style={{ borderBottom: "1px solid var(--border)" }}>
                    <Td>
                      <code style={{ fontSize: 14 }}>{j.name}</code>
                      {isNew && (
                        <span title={t("skills.dirty_dot")} style={{
                          display: "inline-block", marginLeft: 6,
                          width: 6, height: 6, borderRadius: "50%",
                          background: "var(--warning)", verticalAlign: "middle",
                        }} />
                      )}
                    </Td>
                    <Td><code style={{ fontSize: 14 }}>{fmtSchedule(j)}</code></Td>
                    <Td><code style={{ fontSize: 14 }}>{j.skill || "—"}</code></Td>
                    <Td>
                      <span style={{ fontSize: 14, color: "var(--muted)" }}>
                        {j.deliver || "—"}
                      </span>
                    </Td>
                    <Td>
                      <span style={{
                        fontSize: 13, padding: "1px 6px", borderRadius: 4,
                        background: j.enabled === false ? "var(--border)" : "var(--success-soft)",
                        color: j.enabled === false ? "var(--muted)" : "var(--success)",
                      }}>
                        {j.enabled === false
                          ? t("onboarding.cron.disabled")
                          : t("onboarding.cron.enabled")}
                      </span>
                    </Td>
                    <Td style={{ textAlign: "right" }}>
                      <button
                        onClick={() => removeRow(idx)}
                        style={{
                          padding: "3px 10px", fontSize: 14, borderRadius: 6,
                          border: "1px solid var(--border)",
                          background: "var(--danger-soft)", color: "var(--danger)",
                          cursor: "pointer",
                        }}
                      >
                        {t("onboarding.cron.remove")}
                      </button>
                    </Td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function Th({ children, style }: {
  children: React.ReactNode; style?: React.CSSProperties;
}) {
  return (
    <th style={{
      textAlign: "left", padding: "8px 12px", fontWeight: 600,
      letterSpacing: 0.3, ...style,
    }}>{children}</th>
  );
}
function Td({ children, style }: {
  children: React.ReactNode; style?: React.CSSProperties;
}) {
  return (
    <td style={{ padding: "10px 12px", verticalAlign: "middle", ...style }}>
      {children}
    </td>
  );
}

const cardStyle: React.CSSProperties = {
  background: "var(--panel)", border: "1px solid var(--border)",
  borderRadius: 10, padding: "16px 18px",
};
const primaryGhostBtn: React.CSSProperties = {
  padding: "6px 14px", fontSize: 14,
  background: "var(--accent-soft)", color: "var(--accent-2)",
  border: "1px solid var(--accent)", borderRadius: 6, cursor: "pointer",
  whiteSpace: "nowrap",
};
