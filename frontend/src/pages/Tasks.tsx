import { useEffect, useState } from "react";
import { useRouter } from "@/lib/router";
import {
  listMyTasks,
  markTaskDone,
  cancelTask,
  type AgentTask,
} from "@/lib/api";
import { relativeTime } from "@/pages/Memory";
import { useT } from "@/lib/i18n";

/**
 * Tasks — Phase 4.0c follow-up task module.
 *
 * Lists agent_tasks rows owned by the authed user. Each row shows
 * title, due_at, status, related-record link, created_at. Inline
 * affordances: "✓ 完成" (mark done) and "撤销" (soft-delete).
 *
 * Source: ``/api/tasks``. Coach emits these via ``task.create`` →
 * gateway auto-execute (trust default level 3).
 */

type Filter = "open" | "done" | "all";

const FILTER_KEYS: Filter[] = ["open", "done", "all"];

export function TasksPage() {
  const t = useT();
  const FILTERS: { key: Filter; label: string }[] = FILTER_KEYS.map(k => ({
    key: k,
    label: t(`tasks.filter.${k}` as any),
  }));
  const { navigate } = useRouter();
  const [filter, setFilter] = useState<Filter>("open");
  const [rows, setRows] = useState<AgentTask[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [pending, setPending] = useState<Record<number, boolean>>({});

  async function reload() {
    setLoading(true);
    setErr(null);
    try {
      const r = await listMyTasks({
        status: filter === "all" ? undefined : filter,
        limit: 200,
      });
      setRows(r.data);
    } catch (ex: any) {
      setErr(ex?.message || t("tasks.error.load"));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setErr(null);
    listMyTasks({
      status: filter === "all" ? undefined : filter,
      limit: 200,
    })
      .then(r => { if (!cancelled) setRows(r.data); })
      .catch(e => { if (!cancelled) setErr(e?.message || "加载失败"); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [filter]);

  async function onDone(id: number) {
    setPending(p => ({ ...p, [id]: true }));
    try {
      await markTaskDone(id);
      await reload();
    } catch (ex: any) {
      setErr(ex?.message || t("tasks.error.action"));
    } finally {
      setPending(p => ({ ...p, [id]: false }));
    }
  }

  async function onCancel(id: number) {
    if (!window.confirm(t("tasks.cancel_confirm"))) return;
    setPending(p => ({ ...p, [id]: true }));
    try {
      await cancelTask(id);
      await reload();
    } catch (ex: any) {
      setErr(ex?.message || t("tasks.error.action"));
    } finally {
      setPending(p => ({ ...p, [id]: false }));
    }
  }

  return (
    <div style={{ padding: "24px 28px", overflow: "auto", height: "100%" }}>
      <header style={{ marginBottom: 18 }}>
        <h1 style={{ margin: 0, fontSize: 24, letterSpacing: 0.2 }}>
          {t("tasks.title")}
        </h1>
        <p className="muted" style={{ margin: "4px 0 0", fontSize: 14 }}>
          {t("tasks.subtitle")}
        </p>
      </header>

      <div style={{
        display: "flex", gap: 4,
        borderBottom: "1px solid var(--border)", marginBottom: 14,
      }}>
        {FILTERS.map(f => {
          const active = filter === f.key;
          return (
            <button key={f.key}
              onClick={() => setFilter(f.key)}
              style={{
                padding: "10px 16px",
                background: "transparent",
                border: "none",
                borderBottom: active
                  ? "2px solid var(--accent)"
                  : "2px solid transparent",
                color: active ? "var(--fg)" : "var(--muted)",
                cursor: "pointer",
                fontSize: 14,
                fontWeight: active ? 600 : 400,
                marginBottom: -1,
              }}>{f.label}</button>
          );
        })}
      </div>

      {err && <div className="alert alert-error" style={{ marginBottom: 12 }}>{err}</div>}

      {loading ? (
        <Skeleton />
      ) : rows.length === 0 ? (
        <Empty hint={
          filter === "open" ? t("tasks.empty.open") :
          filter === "done" ? t("tasks.empty.done") : t("tasks.empty.all")
        } />
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {rows.map(row => (
            <TaskRow key={row.id} task={row}
              onDone={onDone} onCancel={onCancel}
              busy={!!pending[row.id]}
              onOpenRecord={(m, id) => navigate(`/crm/${m}/${id}`)}
              t={t} />
          ))}
        </div>
      )}
    </div>
  );
}

function TaskRow({ task, onDone, onCancel, busy, onOpenRecord, t }: {
  task: AgentTask;
  onDone: (id: number) => void;
  onCancel: (id: number) => void;
  busy: boolean;
  onOpenRecord: (m: string, id: number) => void;
  t: ReturnType<typeof useT>;
}) {
  const isOpen = task.status === "open";
  const dueLabel = task.due_at ? new Date(task.due_at).toLocaleString() : null;
  const createdLabel = task.created_at
    ? relativeTime(task.created_at, t)
    : "";

  return (
    <article style={{
      padding: "12px 14px",
      background: "var(--panel)",
      border: "1px solid var(--border)",
      borderRadius: 10,
      opacity: isOpen ? 1 : 0.65,
    }}>
      <div style={{
        display: "flex", justifyContent: "space-between",
        alignItems: "baseline", gap: 8, marginBottom: 4,
      }}>
        <div style={{ fontSize: 14, fontWeight: 600, flex: 1 }}>
          {!isOpen && <span style={{ marginRight: 6 }}>✓</span>}
          {task.title}
        </div>
        <StatusChip status={task.status} t={t} />
      </div>
      {task.description && (
        <div style={{
          fontSize: 14, opacity: 0.8, whiteSpace: "pre-wrap",
          margin: "4px 0 8px",
        }}>{task.description}</div>
      )}
      <div style={{
        display: "flex", flexWrap: "wrap", gap: 12,
        fontSize: 13, color: "var(--muted)", marginBottom: 8,
      }}>
        {dueLabel && <span>{t("tasks.field.due")}{dueLabel}</span>}
        {task.related_module && task.related_record_id && (
          <button
            onClick={() => onOpenRecord(
              task.related_module!, task.related_record_id!
            )}
            style={{
              background: "transparent", border: "none",
              color: "var(--accent)", cursor: "pointer",
              padding: 0, fontSize: 13, textDecoration: "underline",
            }}>
            🔗 {task.related_module}#{task.related_record_id}
          </button>
        )}
        {task.source_agent && <span>🤖 {task.source_agent}</span>}
        <span>🕒 {createdLabel}</span>
      </div>
      {isOpen && (
        <div style={{ display: "flex", gap: 8 }}>
          <button
            disabled={busy}
            onClick={() => onDone(task.id)}
            style={primaryBtn}>{t("tasks.btn.done")}</button>
          <button
            disabled={busy}
            onClick={() => onCancel(task.id)}
            style={ghostBtn}>{t("tasks.btn.cancel")}</button>
        </div>
      )}
    </article>
  );
}

function StatusChip({ status, t }: { status: AgentTask["status"]; t: ReturnType<typeof useT> }) {
  const map: Record<string, { bg: string; fg: string; label: string }> = {
    open:      { bg: "var(--accent-soft-strong)", fg: "var(--accent)", label: t("tasks.status.open") },
    done:      { bg: "var(--success-soft)", fg: "var(--success)", label: t("tasks.status.done") },
    cancelled: { bg: "var(--surface-hover)", fg: "var(--muted)", label: t("tasks.status.cancelled") },
  };
  const s = map[status] || map.open;
  return (
    <span style={{
      fontSize: 13, fontWeight: 600,
      padding: "2px 8px", borderRadius: 10,
      background: s.bg, color: s.fg,
      border: "1px solid var(--border)",
      letterSpacing: 0.3,
    }}>{s.label}</span>
  );
}

function Empty({ hint }: { hint: string }) {
  return (
    <div style={{
      padding: "60px 20px", textAlign: "center",
      border: "1px dashed var(--border)", borderRadius: 10,
      color: "var(--muted)", fontSize: 14,
    }}>{hint}</div>
  );
}

function Skeleton() {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {Array.from({ length: 4 }).map((_, i) => (
        <div key={i} style={{
          height: 70, background: "var(--panel)",
          border: "1px solid var(--border)", borderRadius: 10, opacity: 0.5,
        }} />
      ))}
    </div>
  );
}

const primaryBtn: React.CSSProperties = {
  padding: "6px 14px",
  fontSize: 14,
  background: "var(--accent)",
  border: "1px solid var(--accent)",
  borderRadius: 6,
  color: "var(--fg-on-accent)",
  cursor: "pointer",
};
const ghostBtn: React.CSSProperties = {
  padding: "6px 14px",
  fontSize: 14,
  background: "transparent",
  border: "1px solid var(--border)",
  borderRadius: 6,
  color: "var(--muted)",
  cursor: "pointer",
};
