import { useEffect, useState } from "react";
import {
  listJobs, createJob, deleteJob, pauseJob, resumeJob, runJob,
  type ScheduledJob,
} from "@/lib/api";
import { toast } from "@/lib/toast";
import { useT, type T } from "@/lib/i18n";

/**
 * Scheduling tab — port of `renderScheduling` from
 * hermes_cloud/static/app.js.
 *
 * Lists scheduled jobs in a table with Run / Pause-Resume / Delete
 * actions, and a "+ New Task" modal that POSTs to /api/jobs.
 * The list includes paused/completed jobs (include_disabled=true).
 */
const SCHEDULE_EXAMPLES = [
  "every 30m", "every 2h", "every 1d",
  "0 9 * * *", "0 9 * * 1-5",
];

export function SchedulingTab({ profileId }: { profileId: string }) {
  const t = useT();
  const [jobs, setJobs] = useState<ScheduledJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [pending, setPending] = useState<Record<string, string | undefined>>({});
  const [modalOpen, setModalOpen] = useState(false);

  async function refresh() {
    setLoading(true); setErr(null);
    try {
      const r = await listJobs(profileId, { includeDisabled: true });
      setJobs(r.jobs || r.data || []);
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    finally { setLoading(false); }
  }
  useEffect(() => { void refresh(); /* eslint-disable-next-line */ }, [profileId]);

  async function withPending<T>(id: string, label: string, fn: () => Promise<T>, okMsg?: string) {
    setPending(p => ({ ...p, [id]: label }));
    try {
      await fn();
      if (okMsg) toast(okMsg, "success");
      await refresh();
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e), "error");
    } finally {
      setPending(p => { const { [id]: _dropped, ...rest } = p; void _dropped; return rest; });
    }
  }

  return (
    <>
      <div style={{ marginBottom: 16 }}>
        <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between" }}>
          <div>
            <h2 style={{ margin: 0, fontSize: 20 }}>{t("sched.title")}</h2>
            <p className="muted" style={{ margin: "4px 0 0", fontSize: 14 }}>
              {t("sched.desc")}
            </p>
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            <button onClick={() => setModalOpen(true)} style={primaryBtn}>{t("sched.new")}</button>
            <button onClick={() => void refresh()} style={ghostBtn}>{t("sched.refresh")}</button>
          </div>
        </div>
      </div>

      <div style={cardStyle}>
        {loading ? (
          <div className="muted">{t("sched.loading")}</div>
        ) : err ? (
          <div className="alert alert-error">{err}</div>
        ) : jobs.length === 0 ? (
          <p className="muted" style={{ margin: 0, fontSize: 14 }}>
            {t("sched.empty_pre")}<strong>{t("sched.empty_button")}</strong>{t("sched.empty_post")}
          </p>
        ) : (
          <JobsTable
            jobs={jobs}
            pending={pending}
            t={t}
            onRun={(id) => withPending(id, "running",
              () => runJob(profileId, id), t("sched.toast.run"))}
            onPause={(id) => withPending(id, "pausing",
              () => pauseJob(profileId, id))}
            onResume={(id) => withPending(id, "resuming",
              () => resumeJob(profileId, id))}
            onDelete={(id, name) => {
              if (!confirm(t("sched.confirm_delete", { name }))) return;
              void withPending(id, "deleting",
                () => deleteJob(profileId, id), t("sched.toast.deleted"));
            }}
          />
        )}
      </div>

      {modalOpen && (
        <NewJobModal
          onClose={() => setModalOpen(false)}
          onSubmit={async ({ name, prompt, schedule }) => {
            await createJob(profileId, {
              name: name || undefined,
              prompt,
              schedule,
            });
            setModalOpen(false);
            toast(t("sched.toast.created"), "success");
            await refresh();
          }}
        />
      )}
    </>
  );
}

// ─── Jobs table ────────────────────────────────────────────────────────
function JobsTable({
  jobs, pending, onRun, onPause, onResume, onDelete, t,
}: {
  jobs: ScheduledJob[];
  pending: Record<string, string | undefined>;
  onRun: (id: string) => void;
  onPause: (id: string) => void;
  onResume: (id: string) => void;
  onDelete: (id: string, name: string) => void;
  t: T;
}) {
  return (
    <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 14 }}>
      <thead>
        <tr>
          <Th>{t("sched.col.name")}</Th>
          <Th>{t("sched.col.schedule")}</Th>
          <Th>{t("sched.col.last")}</Th>
          <Th>{t("sched.col.next")}</Th>
          <Th>{t("sched.col.status")}</Th>
          <Th>{t("sched.col.actions")}</Th>
        </tr>
      </thead>
      <tbody>
        {jobs.map(j => {
          const name = j.name || j.id;
          const p = pending[j.id];
          const paused = j.state === "paused";
          return (
            <tr key={j.id} style={{ borderTop: "1px solid var(--border)" }}>
              <Td>
                <div style={{ fontWeight: 500 }}>{name}</div>
                <div
                  title={j.prompt || ""}
                  style={{
                    fontSize: 13, color: "var(--muted)", maxWidth: 260,
                    whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
                  }}>{j.prompt || ""}</div>
                {(j.last_error || j.last_delivery_error) && (
                  <div
                    title={j.last_error || j.last_delivery_error || ""}
                    style={{
                      fontSize: 13, color: "var(--danger)", marginTop: 4,
                      maxWidth: 360,
                      display: "-webkit-box",
                      WebkitLineClamp: 2, WebkitBoxOrient: "vertical",
                      overflow: "hidden",
                    }}>
                    ⚠ {j.last_error
                      ? `${t("sched.last_error")}: ${j.last_error}`
                      : `${t("sched.last_delivery_error")}: ${j.last_delivery_error}`}
                  </div>
                )}
              </Td>
              <Td><code style={codeStyle}>{j.schedule_display || "—"}</code></Td>
              <Td>{formatDate(j.last_run_at, t)}</Td>
              <Td>{formatDate(j.next_run_at, t)}</Td>
              <Td><StatusBadge job={j} t={t} /></Td>
              <Td>
                <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
                  <button disabled={!!p} onClick={() => onRun(j.id)} style={smallGhost}>
                    {p === "running" ? t("sched.running") : t("sched.run")}
                  </button>
                  {paused
                    ? <button disabled={!!p} onClick={() => onResume(j.id)} style={smallGhost}>
                        {p === "resuming" ? t("sched.resuming") : t("sched.resume")}
                      </button>
                    : <button disabled={!!p} onClick={() => onPause(j.id)} style={smallGhost}>
                        {p === "pausing" ? t("sched.pausing") : t("sched.pause")}
                      </button>}
                  <button disabled={!!p} onClick={() => onDelete(j.id, name)}
                          style={{ ...smallGhost, color: "var(--danger)" }}>
                    {p === "deleting" ? t("sched.deleting") : t("sched.delete")}
                  </button>
                </div>
              </Td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function StatusBadge({ job: j, t }: { job: ScheduledJob; t: T }) {
  let label = t("sched.status.active"), bg = "color-mix(in srgb, var(--success) 12%, transparent)", fg = "var(--success)", border = "color-mix(in srgb, var(--success) 30%, transparent)";
  if (j.state === "paused")        { label = t("sched.status.paused"); bg = "var(--panel-2)"; fg = "var(--muted)"; border = "var(--border)"; }
  else if (j.state === "completed"){ label = t("sched.status.completed"); bg = "var(--panel-2)"; fg = "var(--muted)"; border = "var(--border)"; }
  else if (j.last_status === "error") { label = t("sched.status.error"); bg = "color-mix(in srgb, var(--danger) 12%, transparent)"; fg = "var(--danger)"; border = "color-mix(in srgb, var(--danger) 30%, transparent)"; }
  const tip = j.last_error || j.last_delivery_error || "";
  return (
    <span
      title={tip || undefined}
      style={{
        fontSize: 13, padding: "2px 8px", borderRadius: 10,
        background: bg, color: fg, border: `1px solid ${border}`,
        cursor: tip ? "help" : "default",
      }}>{label}</span>
  );
}

// ─── New-job modal ─────────────────────────────────────────────────────
function NewJobModal({
  onSubmit, onClose,
}: {
  onSubmit: (v: { name: string; prompt: string; schedule: string }) => Promise<void>;
  onClose: () => void;
}) {
  const t = useT();
  const [name, setName] = useState("");
  const [prompt, setPrompt] = useState("");
  const [schedule, setSchedule] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit() {
    setErr(null);
    const p = prompt.trim(), s = schedule.trim();
    if (!p || !s) { setErr(t("sched.modal.err_required")); return; }
    setBusy(true);
    try { await onSubmit({ name: name.trim(), prompt: p, schedule: s }); }
    catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }

  return (
    <div
      onClick={onClose}
      style={{
        position: "fixed", inset: 0, zIndex: 10001,
        background: "var(--overlay)",
        display: "flex", alignItems: "center", justifyContent: "center",
      }}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={{
          width: 480, maxWidth: "calc(100vw - 32px)",
          background: "var(--panel-2)", border: "1px solid var(--border)",
          borderRadius: 12, padding: "20px 24px",
          position: "relative", color: "inherit",
          boxShadow: "var(--shadow-modal)",
        }}
      >
        <button onClick={onClose} aria-label={t("sched.close")} className="icon-close"
                style={{
                  position: "absolute", top: 10, right: 12,
                  background: "none", border: "none", color: "var(--muted)",
                  fontSize: 22, cursor: "pointer",
                }}>×</button>
        <h3 style={{ margin: "0 0 12px", fontSize: 16 }}>{t("sched.modal.title")}</h3>

        {err && <div className="alert alert-error" style={{ marginBottom: 10 }}>{err}</div>}

        <label style={labelStyle}>
          {t("sched.modal.name")} <small style={{ color: "var(--muted)", fontWeight: 400 }}>{t("sched.modal.name_optional")}</small>
        </label>
        <input value={name} onChange={e => setName(e.target.value)}
               placeholder={t("sched.modal.name_placeholder")} style={inputStyle} />

        <label style={{ ...labelStyle, marginTop: 10 }}>
          {t("sched.modal.prompt")} <span style={{ color: "var(--danger)" }}>*</span>
        </label>
        <textarea value={prompt} onChange={e => setPrompt(e.target.value)} rows={4}
                  placeholder={t("sched.modal.prompt_placeholder")}
                  style={{ ...inputStyle, resize: "vertical", minHeight: 90 }} />

        <label style={{ ...labelStyle, marginTop: 10 }}>
          {t("sched.modal.schedule")} <span style={{ color: "var(--danger)" }}>*</span>
        </label>
        <input value={schedule} onChange={e => setSchedule(e.target.value)}
               placeholder={t("sched.modal.schedule_placeholder")}
               style={inputStyle} />
        <p style={{ ...hintStyle, margin: "4px 0 0" }}>
          {t("sched.modal.examples")}{SCHEDULE_EXAMPLES.map((s, i) => (
            <code key={i} style={{ ...codeStyle, marginRight: 6, cursor: "pointer" }}
                  onClick={() => setSchedule(s)}>{s}</code>
          ))}
        </p>

        <div style={{ display: "flex", gap: 8, marginTop: 16, justifyContent: "flex-end" }}>
          <button onClick={onClose} style={ghostBtn}>{t("sched.modal.cancel")}</button>
          <button onClick={submit} disabled={busy} style={primaryBtn}>
            {busy ? t("sched.modal.creating") : t("sched.modal.submit")}
          </button>
        </div>
      </div>
    </div>
  );
}

// ─── helpers ───────────────────────────────────────────────────────────
function formatDate(s: string | null | undefined, t: T) {
  if (!s) return t("sched.none");
  try {
    const d = new Date(s);
    if (Number.isNaN(d.getTime())) return s;
    return d.toLocaleString(undefined, {
      month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit",
    });
  } catch { return s; }
}

function Th({ children }: { children: React.ReactNode }) {
  return (
    <th style={{
      textAlign: "left", fontSize: 13, color: "var(--muted)",
      fontWeight: 500, padding: "8px 10px", borderBottom: "1px solid var(--border)",
    }}>{children}</th>
  );
}
function Td({ children }: { children: React.ReactNode }) {
  return (
    <td style={{
      padding: "10px", verticalAlign: "top", fontSize: 14,
      color: "inherit",
    }}>{children}</td>
  );
}

// ── shared styles (subset of ModelTab's, kept local to avoid a cycle) ─
const cardStyle: React.CSSProperties = {
  background: "var(--panel)", border: "1px solid var(--border)",
  borderRadius: 10, padding: "16px 18px",
};
const inputStyle: React.CSSProperties = {
  width: "100%", padding: "8px 10px", fontSize: 14,
  background: "var(--bg)", border: "1px solid var(--border)",
  borderRadius: 7, color: "inherit", marginTop: 4,
};
const labelStyle: React.CSSProperties = {
  fontSize: 14, color: "var(--muted)", display: "block",
};
const hintStyle: React.CSSProperties = {
  fontSize: 14, color: "var(--muted)",
};
const ghostBtn: React.CSSProperties = {
  padding: "6px 12px", fontSize: 14,
  background: "transparent", border: "1px solid var(--border)",
  borderRadius: 6, color: "var(--muted)", cursor: "pointer",
};
const smallGhost: React.CSSProperties = {
  ...ghostBtn, padding: "4px 10px", fontSize: 13,
};
const primaryBtn: React.CSSProperties = {
  padding: "6px 14px", fontSize: 14,
  background: "var(--accent)", color: "var(--fg-on-accent)",
  border: "none", borderRadius: 6, cursor: "pointer",
};
const codeStyle: React.CSSProperties = {
  fontFamily: "ui-monospace, monospace", fontSize: 13,
  color: "var(--muted)", background: "var(--bg)",
  padding: "2px 8px", borderRadius: 4,
};
