import { useEffect, useState } from "react";
import { getCronHealth, type CronHealthJob } from "@/lib/api";
import { useT } from "@/lib/i18n";

/**
 * "🟢 后台健康" strip — shows when each known cron last ran.
 *
 * Charter v2 §定律 5 promises "AI 一直在跑", but until now there was
 * no UI to verify it; an idle daily-coach cron would just produce an
 * empty TodayCoachCards and the user wouldn't know if it was a quiet
 * day or a dead backend. This strip closes the gap with a single line.
 *
 * Data source is approximate (MAX gateway_actions.created_at per
 * agent); a cron run that produced zero actions won't refresh the
 * timestamp. Good enough for liveness; not good enough for SLO.
 *
 * Modes:
 *  - compact: one chip showing the "primary" job (default = daily-coach)
 *  - full:    all known jobs, used on the AI 审计 page
 */

const STATUS_COLOR: Record<CronHealthJob["status"], string> = {
  green:  "var(--success)",
  yellow: "var(--warning)",
  red:    "var(--danger)",
};

const STATUS_DOT: Record<CronHealthJob["status"], string> = {
  green: "🟢", yellow: "🟡", red: "🔴",
};

function humanizeMinutes(min: number | null, t: ReturnType<typeof useT>): string {
  if (min == null) return t("cron_health.never");
  if (min < 1)    return t("cron_health.just_now");
  if (min < 60)   return t("cron_health.minutes_ago", { n: String(Math.round(min)) });
  if (min < 60 * 24) return t("cron_health.hours_ago", { n: String(Math.round(min / 60)) });
  return t("cron_health.days_ago", { n: String(Math.round(min / 60 / 24)) });
}

export function CronHealthLine({ mode = "full", primary }: {
  mode?: "compact" | "full";
  /** Which job to surface in compact mode. Defaults to daily-coach. */
  primary?: string;
}) {
  const t = useT();
  const [jobs, setJobs] = useState<CronHealthJob[] | null>(null);

  useEffect(() => {
    let alive = true;
    getCronHealth()
      .then(r => { if (alive) setJobs(r.jobs || []); })
      .catch(() => { if (alive) setJobs([]); });
    return () => { alive = false; };
  }, []);

  if (!jobs) return null;
  if (jobs.length === 0) return null;

  if (mode === "compact") {
    const pick = jobs.find(j => j.name === (primary || "crm-daily-coach")) || jobs[0];
    return <Chip job={pick} t={t} />;
  }

  return (
    <div style={{
      display: "flex", flexWrap: "wrap", gap: 6, alignItems: "center",
      padding: "8px 12px",
      border: "1px solid var(--border)",
      borderRadius: 8,
      marginBottom: 12,
      fontSize: 14,
      background: "var(--surface-hover)",
    }}>
      <span className="muted" style={{ marginRight: 6 }}>
        {t("cron_health.heading")}
      </span>
      {jobs.map(j => <Chip key={j.name} job={j} t={t} />)}
      <span className="muted" style={{ fontSize: 13, marginLeft: "auto" }}>
        {t("cron_health.footnote")}
      </span>
    </div>
  );
}

function Chip({ job, t }: { job: CronHealthJob; t: ReturnType<typeof useT> }) {
  const color = STATUS_COLOR[job.status];
  return (
    <span
      title={
        job.last_run_at
          ? new Date(job.last_run_at).toLocaleString(undefined, { hour12: false })
          : t("cron_health.never")
      }
      style={{
        display: "inline-flex", alignItems: "center", gap: 4,
        padding: "2px 8px",
        borderRadius: 999,
        border: `1px solid color-mix(in srgb, ${color} 33%, transparent)`,
        background: `color-mix(in srgb, ${color} 8%, transparent)`,
        fontSize: 13,
      }}
    >
      <span>{STATUS_DOT[job.status]}</span>
      <span style={{ fontWeight: 600 }}>{job.label}</span>
      <span className="muted">· {humanizeMinutes(job.minutes_since, t)}</span>
    </span>
  );
}
