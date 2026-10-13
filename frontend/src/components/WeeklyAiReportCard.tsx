import { useEffect, useState } from "react";
import {
  getWeeklyInsights, markCoachHintRead, dismissCoachHint,
  type WeeklyInsights, type CoachHint,
} from "@/lib/api";
import { useRouter } from "@/lib/router";
import { useT, type T } from "@/lib/i18n";
import { severityColor, signalIcon } from "@/lib/coachHintVisuals";

/**
 * "本周 AI 表现" —— 对应宪章 定律 5 (AI 驾驶，人类监督) 的首屏落点。
 *
 * 放在 ChatInbox (侧边栏"聊天" 💬) 空态里——用户打开 app 的第一眼、
 * 还没选历史会话或开新对话时看到。注意：这里不是 CRM 里那个
 * CopilotPanel (那个只在 /crm 右栏出现)；我们故意选全局聊天首页，让
 * Agent 的主动上报出现在日常最高频的落地页。Agent
 * 已经替用户扫过最近 7 天，有什么值得注意的在这里主动说，不需要用户
 * 自己翻 dashboard。
 *
 * 渲染原则（宪章 §168，B 方案）：
 *   - 至多 3 句叙事 + 至多 3 条 Coach 提示
 *   - 不上表格 / sparkline / 分位数 / 事件类型埋点名
 *   - 点任意一条 hint 直接跳到对应 CRM 记录
 *   - 无数据 → 不渲染（优雅隐身）
 */
export function WeeklyAiReportCard() {
  const t = useT();
  const { navigate } = useRouter();
  const [data, setData] = useState<WeeklyInsights | null>(null);
  const [err, setErr] = useState(false);

  useEffect(() => {
    getWeeklyInsights()
      .then(setData)
      .catch(() => setErr(true));
  }, []);

  if (err || !data || !data.has_data) return null;

  const narrative = buildNarrative(t, data);
  const hints = data.coach_hints;

  return (
    <div style={{
      maxWidth: 520, margin: "24px auto",
      padding: "18px 20px",
      border: "1px solid var(--border)",
      borderRadius: 10,
      background: "var(--panel)",
    }}>
      <div style={{
        display: "flex", alignItems: "center", gap: 8,
        marginBottom: 10,
      }}>
        <span style={{ fontSize: 18 }}>🤖</span>
        <span style={{ fontSize: 14, color: "var(--muted)" }}>
          {t("weekly.heading")}
        </span>
      </div>

      {narrative.length > 0 && (
        <div style={{ fontSize: 14, lineHeight: 1.7, marginBottom: hints.length ? 14 : 0 }}>
          {narrative.map((line, i) => <div key={i}>{line}</div>)}
        </div>
      )}

      {hints.length > 0 && (
        <>
          <div style={{
            fontSize: 14, color: "var(--muted)",
            marginBottom: 6,
          }}>{t("weekly.coach_heading")}</div>
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {hints.map((h, i) => (
              <HintRow
                key={h.id ?? i}
                hint={h}
                onClick={() => {
                  if (h.id !== undefined && !h.is_read) {
                    void markCoachHintRead(h.id).catch(() => {});
                    setData(d => d && {
                      ...d,
                      coach_hints: d.coach_hints.map(x =>
                        x.id === h.id ? { ...x, is_read: true } : x),
                    });
                  }
                  navigate(`/crm/${h.module}/${h.record_id}`);
                }}
                onDismiss={h.id !== undefined ? () => {
                  void dismissCoachHint(h.id!).catch(() => {});
                  setData(d => d && {
                    ...d,
                    coach_hints: d.coach_hints.filter(x => x.id !== h.id),
                  });
                } : undefined}
              />
            ))}
          </div>
        </>
      )}
    </div>
  );
}

function buildNarrative(t: T, d: WeeklyInsights): string[] {
  // Narrative style (§168): like a colleague's report, not a dashboard.
  // Lead with the highest-weight fact (deals won); lesser facts trail with
  // "also". If no wins, fall back to "advanced"; if neither, "first_touch".
  const o = d.outcomes;
  const lines: string[] = [];
  const win = o.deals_won;
  const adv = o.stages_advanced;
  const ft  = o.first_touch;
  const head = t("weekly.narrative.head", { days: d.period_days });

  if (win > 0) {
    const amt = o.deals_won_amount;
    const amtStr = amt && amt > 0 ? t("weekly.narrative.deals_won_amount", { amt: formatAmount(t, amt) }) : "";
    let s = head + t("weekly.narrative.deals_won", { n: win }) + amtStr;
    if (adv > 0) s += t("weekly.narrative.also_advanced", { n: adv });
    lines.push(s + ".");
  } else if (adv > 0) {
    lines.push(head + t("weekly.narrative.only_advanced", { n: adv }));
  } else if (ft > 0) {
    lines.push(head + t("weekly.narrative.first_touch", { n: ft }));
  }
  return lines;
}

function formatAmount(t: T, n: number): string {
  if (n >= 10000) return t("weekly.amount.wan", { n: (n / 10000).toFixed(n >= 1000000 ? 0 : 1) });
  return t("weekly.amount.yuan", { n: n.toFixed(0) });
}

function HintRow({
  hint, onClick, onDismiss,
}: {
  hint: CoachHint;
  onClick: () => void;
  onDismiss?: () => void;
}) {
  const t = useT();
  const sevColor = severityColor(hint.severity);
  const icon = signalIcon(hint.signal);
  const label = hint.record_name || `${hint.module}#${hint.record_id}`;
  const unread = hint.id !== undefined && !hint.is_read;
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "12px 24px 1fr auto auto",
        gap: 10, alignItems: "center",
        padding: "8px 10px",
        background: unread ? "var(--info-soft)" : "var(--surface-hover)",
        border: `1px solid color-mix(in srgb, ${sevColor} 20%, transparent)`,
        borderLeft: `3px solid ${sevColor}`,
        borderRadius: 6,
        fontSize: 14,
      }}
    >
      <span
        title={unread ? t("weekly.hint.unread") : t("weekly.hint.read")}
        style={{
          width: 8, height: 8, borderRadius: "50%",
          background: unread ? "var(--info)" : "transparent",
          justifySelf: "center",
        }}
      />
      <span style={{ fontSize: 16 }}>{icon}</span>
      <button
        onClick={onClick}
        style={{
          background: "transparent", border: "none",
          color: "inherit", textAlign: "left", cursor: "pointer",
          padding: 0, fontSize: 14,
        }}
      >
        <span style={{ fontWeight: unread ? 600 : 500 }}>{label}</span>
        <span style={{ color: "var(--muted)", marginLeft: 8 }}>
          {hint.hint}
        </span>
      </button>
      <span style={{ color: "var(--muted)", fontSize: 14 }}>→</span>
      {onDismiss && (
        <button
          onClick={e => { e.stopPropagation(); onDismiss(); }}
          title={t("weekly.hint.dismiss")}
          style={{
            background: "transparent", border: "none",
            color: "var(--muted)", cursor: "pointer", fontSize: 16,
            padding: "0 4px",
          }}
        >×</button>
      )}
    </div>
  );
}
