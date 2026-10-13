import { Fragment, useEffect, useMemo, useState } from "react";
import {
  ApiError,
  SchemaChangelogRow,
  getSchemaChangelog,
  listModules,
} from "@/lib/api";
import { toast } from "@/lib/toast";
import { useT, type DictKey } from "@/lib/i18n";

/**
 * /admin/schema-changelog — the one-table audit log for every mutation
 * against ``module_fields`` and its options. Readable by any authed
 * user (transparency is part of the charter — people should be able to
 * see what the AI is doing to their schema).
 */
export function AdminSchemaChangelogPage() {
  const translate = useT();
  const [modules, setModules] = useState<Array<{ name: string; label: string }>>([]);
  const [moduleFilter, setModuleFilter] = useState<string>("");
  const [actorFilter, setActorFilter] = useState<"" | "user" | "agent">("");
  const [rows, setRows] = useState<SchemaChangelogRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [detailRow, setDetailRow] = useState<SchemaChangelogRow | null>(null);

  useEffect(() => {
    listModules()
      .then(r => setModules((r.data || []).map(m => ({ name: m.name, label: m.label }))))
      .catch(() => { /* non-fatal */ });
  }, []);

  async function refresh() {
    setLoading(true);
    try {
      const r = await getSchemaChangelog({
        moduleName: moduleFilter || undefined,
        actorType: actorFilter || undefined,
        limit: 200,
      });
      setRows(r.data);
    } catch (e) {
      toast(errMsg(e), "error");
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => { refresh(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [moduleFilter, actorFilter]);

  return (
    <div style={{ padding: 24 }}>
      <h2 style={{ marginTop: 0 }}>{translate("admin.schema_changelog.title")}</h2>
      <p className="muted" style={{ fontSize: 14, marginBottom: 20 }}>
        {translate("admin.schema_changelog.subtitle")}
      </p>

      <div style={{ display: "flex", gap: 12, marginBottom: 16, alignItems: "center" }}>
        <label style={{ fontSize: 14, display: "flex", gap: 6, alignItems: "center", whiteSpace: "nowrap" }}>
          {translate("admin.schema_changelog.filter.module")}
          <select
            value={moduleFilter}
            onChange={e => setModuleFilter(e.target.value)}
            style={selectStyle}
          >
            <option value="">—</option>
            {modules.map(m => (
              <option key={m.name} value={m.name}>{m.label}</option>
            ))}
          </select>
        </label>
        <label style={{ fontSize: 14, display: "flex", gap: 6, alignItems: "center", whiteSpace: "nowrap" }}>
          {translate("admin.schema_changelog.filter.actor")}
          <select
            value={actorFilter}
            onChange={e => setActorFilter(e.target.value as typeof actorFilter)}
            style={selectStyle}
          >
            <option value="">{translate("admin.schema_changelog.filter.actor.all")}</option>
            <option value="user">{translate("admin.schema_changelog.filter.actor.user")}</option>
            <option value="agent">{translate("admin.schema_changelog.filter.actor.agent")}</option>
          </select>
        </label>
      </div>

      {loading && <div className="muted">{translate("common.loading")}</div>}
      {!loading && rows.length === 0 && (
        <div className="muted" style={{ padding: 32, textAlign: "center" }}>
          {translate("admin.schema_changelog.empty")}
        </div>
      )}
      {!loading && rows.length > 0 && (
        <div style={{
          border: "1px solid var(--border)", borderRadius: 8,
          background: "var(--panel)", overflow: "hidden",
        }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 14 }}>
            <thead style={{
              fontSize: 13, textTransform: "uppercase",
              color: "var(--muted)", background: "var(--panel)",
            }}>
              <tr>
                <Th>{translate("admin.schema_changelog.col.ts")}</Th>
                <Th>{translate("admin.schema_changelog.col.actor")}</Th>
                <Th>{translate("admin.schema_changelog.col.module")}</Th>
                <Th>{translate("admin.schema_changelog.col.field")}</Th>
                <Th>{translate("admin.schema_changelog.col.op")}</Th>
                <Th>{translate("admin.schema_changelog.col.reason")}</Th>
                <Th>{translate("admin.schema_changelog.col.diff")}</Th>
              </tr>
            </thead>
            <tbody>
              {rows.map(r => (
                <ChangelogRow key={r.id} row={r} onOpenDetail={setDetailRow} />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {detailRow && (
        <ChangelogDetailModal row={detailRow} onClose={() => setDetailRow(null)} />
      )}
    </div>
  );
}

function ChangelogRow({
  row, onOpenDetail,
}: { row: SchemaChangelogRow; onOpenDetail: (row: SchemaChangelogRow) => void }) {
  const translate = useT();
  const opKey: DictKey = (`admin.schema_changelog.op.${row.op}` as DictKey);

  return (
    <tr style={{ borderBottom: "1px solid var(--border)" }}>
      <Td><span className="muted" style={{ fontSize: 14 }}>{fmtTime(row.ts)}</span></Td>
      <Td>
        <span style={{
          fontSize: 13, padding: "1px 6px", borderRadius: 4, marginRight: 6,
          background: row.actor_type === "agent" ? "var(--info-soft)" : "var(--border)",
        }}>
          {row.actor_type === "agent" ? "AI" : "👤"}
        </span>
        <span>{row.actor_label || row.actor_id || "—"}</span>
      </Td>
      <Td>{row.module_name}</Td>
      <Td>
        {row.field_name
          ? <code style={{ fontSize: 14 }}>{row.field_name}</code>
          : <span className="muted">—</span>}
      </Td>
      <Td><span className="small">{translate(opKey)}</span></Td>
      <Td>
        <span style={{ maxWidth: 240, display: "inline-block" }}>
          {row.reason || <span className="muted">—</span>}
        </span>
      </Td>
      <Td>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{
            display: "inline-block", maxWidth: 380,
            overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
          }}>
            <DiffCell before={row.before_json} after={row.after_json} op={row.op} />
          </span>
          <button
            className="ghost"
            onClick={() => onOpenDetail(row)}
            style={{ fontSize: 13, padding: "2px 8px", whiteSpace: "nowrap", flexShrink: 0 }}
          >
            {translate("admin.schema_changelog.more")}
          </button>
        </div>
      </Td>
    </tr>
  );
}

function ChangelogDetailModal({
  row, onClose,
}: { row: SchemaChangelogRow; onClose: () => void }) {
  const translate = useT();
  const opKey: DictKey = (`admin.schema_changelog.op.${row.op}` as DictKey);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const fields: Array<[string, string]> = [
    [translate("admin.schema_changelog.col.ts"), fmtTime(row.ts)],
    [
      translate("admin.schema_changelog.col.actor"),
      `${row.actor_type === "agent" ? "AI" : "👤"} ${row.actor_label || row.actor_id || "—"}`,
    ],
    [translate("admin.schema_changelog.col.module"), row.module_name],
    [translate("admin.schema_changelog.col.field"), row.field_name || "—"],
    [translate("admin.schema_changelog.col.op"), translate(opKey)],
    [translate("admin.schema_changelog.col.reason"), row.reason || "—"],
  ];

  return (
    <div
      onClick={onClose}
      style={{
        position: "fixed", inset: 0, background: "var(--overlay)", zIndex: 10001,
        display: "flex", alignItems: "center", justifyContent: "center", padding: 24,
      }}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={{
          position: "relative", width: 640, maxWidth: "100%",
          maxHeight: "calc(100vh - 48px)", overflowY: "auto",
          background: "var(--panel)", border: "1px solid var(--border)",
          borderRadius: 12, boxShadow: "var(--shadow-modal)",
          padding: "20px 24px",
        }}
      >
        <button
          onClick={onClose}
          aria-label="close"
          style={{
            position: "absolute", top: 12, right: 14, background: "transparent",
            border: "none", color: "var(--muted)", fontSize: 20, cursor: "pointer",
            lineHeight: 1,
          }}
        >
          ×
        </button>
        <h3 style={{ margin: "0 0 16px" }}>
          {translate("admin.schema_changelog.detail.title")}
        </h3>

        <dl style={{
          display: "grid", gridTemplateColumns: "auto 1fr",
          gap: "8px 16px", margin: 0, fontSize: 14,
        }}>
          {fields.map(([k, v]) => (
            <Fragment key={k}>
              <dt className="muted" style={{ whiteSpace: "nowrap" }}>{k}</dt>
              <dd style={{ margin: 0, wordBreak: "break-word" }}>{v}</dd>
            </Fragment>
          ))}
        </dl>

        <JsonBlock
          label={translate("admin.schema_changelog.detail.before")}
          value={row.before_json}
        />
        <JsonBlock
          label={translate("admin.schema_changelog.detail.after")}
          value={row.after_json}
        />
      </div>
    </div>
  );
}

function JsonBlock({ label, value }: { label: string; value: unknown }) {
  let text: string;
  if (value == null) text = "—";
  else {
    try { text = JSON.stringify(value, null, 2); }
    catch { text = String(value); }
  }
  return (
    <div style={{ marginTop: 16 }}>
      <div className="muted" style={{ fontSize: 14, marginBottom: 6 }}>{label}</div>
      <pre style={{
        margin: 0, padding: "10px 12px", fontSize: 14,
        background: "var(--bg)", border: "1px solid var(--border)",
        borderRadius: 6, overflowX: "auto", whiteSpace: "pre-wrap", wordBreak: "break-word",
      }}>{text}</pre>
    </div>
  );
}

function DiffCell({
  before, after, op,
}: { before: unknown; after: unknown; op: string }) {
  const added = useMemo(() => {
    if (op !== "add_option") return null;
    const b = safeList((before as Record<string, unknown>)?.options);
    const a = safeList((after as Record<string, unknown>)?.options);
    const diff = a.filter(v => !b.includes(v));
    return diff.length ? diff : null;
  }, [before, after, op]);

  if (added) {
    return (
      <span style={{ fontSize: 14 }}>
        +{added.map(v => (
          <code key={v} style={{ marginLeft: 4, padding: "1px 6px", background: "var(--panel)", borderRadius: 4 }}>
            {v}
          </code>
        ))}
      </span>
    );
  }
  // Fallback: compact JSON dump of after, clipped.
  if (after == null) return <span className="muted">—</span>;
  let s = "";
  try { s = JSON.stringify(after); } catch { s = String(after); }
  if (s.length > 80) s = s.slice(0, 77) + "…";
  return <code style={{ fontSize: 14 }}>{s}</code>;
}

// ── Helpers ───────────────────────────────────────────────────────────

function Th({ children }: { children: React.ReactNode }) {
  return <th style={{ padding: "10px 14px", textAlign: "left", fontWeight: 600 }}>{children}</th>;
}
function Td({ children }: { children: React.ReactNode }) {
  return <td style={{ padding: "10px 14px", verticalAlign: "top" }}>{children}</td>;
}

const selectStyle: React.CSSProperties = {
  padding: "4px 8px", fontSize: 14,
  background: "transparent", color: "inherit",
  border: "1px solid var(--border)", borderRadius: 6,
};

function fmtTime(iso: string): string {
  try { return new Date(iso).toLocaleString(); } catch { return iso; }
}

function safeList(v: unknown): string[] {
  if (Array.isArray(v)) return v.map(String);
  return [];
}

function errMsg(e: unknown): string {
  if (e instanceof ApiError) {
    return typeof e.detail === "string" ? e.detail : JSON.stringify(e.detail);
  }
  if (e instanceof Error) return e.message;
  return String(e);
}
