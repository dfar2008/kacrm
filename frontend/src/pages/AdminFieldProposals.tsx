import { useEffect, useMemo, useState, type CSSProperties } from "react";
import {
  ApiError,
  FieldProposal,
  listFieldProposals,
  reviewFieldProposal,
} from "@/lib/api";
import { toast } from "@/lib/toast";
import { useT, type DictKey } from "@/lib/i18n";

/**
 * /admin/field-proposals — admin review center for AI-initiated field
 * additions. The AI never creates a field directly (ADR 2026-04-20);
 * it drops a proposal here, we approve or reject.
 */
export function AdminFieldProposalsPage({ onReviewed }: { onReviewed?: () => void } = {}) {
  const translate = useT();
  const [status, setStatus] = useState<FieldProposal["status"]>("pending");
  const [rows, setRows] = useState<FieldProposal[]>([]);
  const [loading, setLoading] = useState(true);
  const [reviewing, setReviewing] = useState<{ id: number; decision: "approved" | "rejected" } | null>(null);

  async function refresh() {
    setLoading(true);
    try {
      const r = await listFieldProposals(status);
      setRows(r.data);
    } catch (e) {
      toast(errMsg(e), "error");
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => { refresh(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [status]);

  const statuses: Array<FieldProposal["status"]> = ["pending", "approved", "rejected", "withdrawn"];
  const statusKey: Record<FieldProposal["status"], DictKey> = {
    pending:   "admin.field_proposals.status.pending",
    approved:  "admin.field_proposals.status.approved",
    rejected:  "admin.field_proposals.status.rejected",
    withdrawn: "admin.field_proposals.status.withdrawn",
  };

  return (
    <div style={{ padding: 24 }}>
      <h2 style={{ marginTop: 0 }}>{translate("admin.field_proposals.title")}</h2>
      <p className="muted" style={{ fontSize: 14, marginBottom: 20 }}>
        {translate("admin.field_proposals.subtitle")}
      </p>
      <div style={{ display: "flex", gap: 8, marginBottom: 16 }}>
        {statuses.map(s => (
          <button
            key={s}
            onClick={() => setStatus(s)}
            className={s === status ? "primary" : "ghost"}
            style={{ fontSize: 14, padding: "4px 12px" }}
          >
            {translate(statusKey[s])}
          </button>
        ))}
      </div>
      {loading && <div className="muted">{translate("common.loading")}</div>}
      {!loading && rows.length === 0 && (
        <div className="muted" style={{ padding: 32, textAlign: "center" }}>
          {translate("admin.field_proposals.empty")}
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
                <Th>{translate("admin.field_proposals.col.ts")}</Th>
                <Th>{translate("admin.field_proposals.col.module")}</Th>
                <Th>{translate("admin.field_proposals.col.name")}</Th>
                <Th>{translate("admin.field_proposals.col.label")}</Th>
                <Th>{translate("admin.field_proposals.col.type")}</Th>
                <Th>{translate("admin.field_proposals.col.reason")}</Th>
                <Th>{translate("admin.field_proposals.col.actions")}</Th>
              </tr>
            </thead>
            <tbody>
              {rows.map(r => (
                <ProposalRow
                  key={r.id} row={r}
                  showActions={status === "pending"}
                  onReview={(id, decision) => setReviewing({ id, decision })}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {reviewing && (
        <ReviewModal
          proposalId={reviewing.id}
          decision={reviewing.decision}
          onClose={() => setReviewing(null)}
          onDone={() => { setReviewing(null); refresh(); onReviewed?.(); }}
        />
      )}
    </div>
  );
}

const KIND_LABEL: Record<string, DictKey> = {
  field_add: "admin.schema.kind.field_add",
  picklist_option_add: "admin.schema.kind.option_add",
  module_add: "admin.schema.kind.module_add",
  subdetail_add: "admin.schema.kind.subdetail_add",
  module_rename: "admin.schema.kind.module_rename",
  workflow_add: "admin.schema.kind.workflow_add",
};

/** Map a proposal payload to its (name / label / type) cells per kind. */
function proposalCells(p: Record<string, unknown>): { name: string; label: string; type: string } {
  const s = (v: unknown) => String(v ?? "");
  switch (String(p.kind || "field_add")) {
    case "module_add":
      return { name: s(p.name), label: s(p.title), type: "module" };
    case "subdetail_add":
      return { name: s(p.name), label: `${s(p.parent_name)} › ${s(p.title)}`, type: "subdetail" };
    case "module_rename":
      return { name: s(p.name), label: `→ ${s(p.title)}`, type: "rename" };
    case "workflow_add":
      return { name: s(p.name), label: s(p.module), type: "workflow" };
    case "picklist_option_add":
      return { name: s(p.field_name), label: `+ "${s(p.label)}" (${s(p.value)})`, type: "option" };
    default:
      return { name: s(p.field_name), label: s(p.field_label), type: s(p.field_type) };
  }
}

type DetailRow = { labelKey: DictKey; value: string };

/** Full per-kind payload breakdown for the expandable detail panel — so an
 *  admin can actually review what a module / workflow / field proposal
 *  contains (the terse table row alone is not enough to decide on, e.g., a
 *  module: its initial fields live only here). */
function proposalDetails(p: Record<string, unknown>, t: (k: DictKey) => string): DetailRow[] {
  const s = (v: unknown) => String(v ?? "");
  const kind = String(p.kind || "field_add");
  const rows: DetailRow[] = [];
  const fieldsList = (raw: unknown): string => {
    const fs = Array.isArray(raw) ? (raw as Array<Record<string, unknown>>) : [];
    // Agent-emitted initial_fields use {name,label,type,target}; the SPA
    // wizard / provision_field use {field_name,field_label,field_type}.
    // Accept either so module proposals render their fields regardless.
    return fs.length
      ? fs.map(f => {
          const nm = s(f.field_name ?? f.name);
          const lbl = s(f.field_label ?? f.label) || nm;
          const ty = s(f.field_type ?? f.type);
          const tgt = s(f.target ?? (f.field_config as any)?.relation?.target_module);
          return `${lbl} (${nm}/${ty}${ty === "relation" && tgt ? `→${tgt}` : ""})`;
        }).join("\n")
      : t("admin.schema.detail.no_fields");
  };
  if (kind === "module_add" || kind === "subdetail_add") {
    rows.push({ labelKey: "admin.schema.detail.machine", value: s(p.name) });
    rows.push({ labelKey: "admin.schema.detail.display", value: s(p.title) });
    if (p.title_en) rows.push({ labelKey: "admin.schema.detail.english", value: s(p.title_en) });
    if (kind === "subdetail_add") {
      rows.push({ labelKey: "admin.schema.detail.parent", value: s(p.parent_name) });
      if (p.detail_kind) rows.push({ labelKey: "admin.schema.detail.detail_kind", value: s(p.detail_kind) });
    }
    rows.push({ labelKey: "admin.schema.detail.fields", value: fieldsList(p.initial_fields) });
  } else if (kind === "module_rename") {
    rows.push({ labelKey: "admin.schema.detail.machine", value: s(p.name) });
    rows.push({ labelKey: "admin.schema.detail.new_title", value: s(p.title) });
    if (p.title_en) rows.push({ labelKey: "admin.schema.detail.english", value: s(p.title_en) });
  } else if (kind === "workflow_add") {
    rows.push({ labelKey: "admin.schema.detail.machine", value: s(p.name) });
    rows.push({ labelKey: "admin.schema.detail.module", value: s(p.module) });
    rows.push({
      labelKey: "admin.schema.detail.trigger",
      value: s(p.trigger_type) === "schedule" ? `cron ${s(p.schedule_cron)}` : `event ${s(p.trigger_on)}`,
    });
    const acts = Array.isArray(p.actions) ? (p.actions as Array<Record<string, unknown>>) : [];
    rows.push({
      labelKey: "admin.schema.detail.actions",
      value: acts.length ? acts.map(a => s(a.type ?? a.executor ?? JSON.stringify(a))).join("\n") : "—",
    });
    if (p.condition && typeof p.condition === "object" && Object.keys(p.condition as object).length)
      rows.push({ labelKey: "admin.schema.detail.condition", value: JSON.stringify(p.condition) });
  } else if (kind === "picklist_option_add") {
    rows.push({ labelKey: "admin.schema.detail.field", value: s(p.field_name) });
    rows.push({ labelKey: "admin.schema.detail.option_value", value: `${s(p.label)} (${s(p.value)})` });
  } else {
    // field_add
    rows.push({ labelKey: "admin.schema.detail.field", value: s(p.field_name) });
    rows.push({ labelKey: "admin.schema.detail.display", value: s(p.field_label) });
    rows.push({ labelKey: "admin.schema.detail.type", value: s(p.field_type) });
    if (Array.isArray(p.options) && p.options.length)
      rows.push({ labelKey: "admin.schema.detail.options", value: (p.options as unknown[]).map(s).join("、") });
    const fc = (p.field_config && typeof p.field_config === "object" ? p.field_config : {}) as Record<string, any>;
    if (fc.relation?.target_module) rows.push({ labelKey: "admin.schema.detail.relation", value: s(fc.relation.target_module) });
    if (fc.formula?.expression) rows.push({ labelKey: "admin.schema.detail.formula", value: s(fc.formula.expression) });
    if (fc.autonumber) rows.push({ labelKey: "admin.schema.detail.autonumber", value: JSON.stringify(fc.autonumber) });
    if (p.is_required) rows.push({ labelKey: "admin.schema.detail.required", value: "✓" });
  }
  if (p.reason) rows.push({ labelKey: "admin.schema.detail.why", value: s(p.reason) });
  return rows;
}

function ProposalRow({
  row, showActions, onReview,
}: {
  row: FieldProposal;
  showActions: boolean;
  onReview: (id: number, decision: "approved" | "rejected") => void;
}) {
  const translate = useT();
  const [open, setOpen] = useState(false);
  const payload = useMemo(() => safePayload(row.payload), [row.payload]);
  const cells = useMemo(() => proposalCells(payload), [payload]);
  const details = useMemo(() => proposalDetails(payload, translate), [payload, translate]);
  const colSpan = 7;
  // One shared chrome for all three action icons → same size + coordinated
  // look; only the icon stroke color differs. Rendered as a tight group.
  const iconBtnBase: CSSProperties = {
    display: "inline-flex", alignItems: "center", justifyContent: "center",
    width: 30, height: 30, padding: 0, lineHeight: 1,
    border: "1px solid var(--border)", borderRadius: 6,
    background: "var(--panel)", cursor: "pointer",
  };

  return (
    <>
      <tr
        style={{ borderBottom: open ? "none" : "1px solid var(--border)", cursor: "pointer" }}
        onClick={() => setOpen(o => !o)}
      >
        <Td><span className="muted" style={{ fontSize: 14 }}>{fmtTime(row.ts)}</span></Td>
        <Td>{row.module_name}</Td>
        <Td>
          {/* A proposal is no longer always a field — module/subdetail/rename/
              workflow proposals share this queue, keyed by payload.kind. The
              high-contrast kind tag + click-to-expand detail panel let the
              admin review the actual payload (e.g. a module's initial fields)
              without reading raw JSON. */}
          <span style={{
            fontSize: 12, padding: "1px 7px", marginRight: 6, borderRadius: 4,
            background: "var(--fg)", color: "var(--bg)", fontWeight: 600, whiteSpace: "nowrap",
          }}>{translate(KIND_LABEL[String(payload.kind || "field_add")] || KIND_LABEL.field_add)}</span>
          <code style={{ fontSize: 14 }}>{cells.name}</code>
        </Td>
        <Td>{cells.label}</Td>
        <Td><code style={{ fontSize: 14 }}>{cells.type}</code></Td>
        <Td>
          <span style={{ maxWidth: 320, display: "inline-block" }}>
            {String(payload.reason || "—")}
          </span>
        </Td>
        {/* ✓ 通过 / ✕ 驳回 / ⌄ 展开 as one tight, equal-size icon-button
            group: same chrome, only the icon color differs; tooltips on hover. */}
        <Td>
          <div style={{ display: "flex", gap: 4, justifyContent: "flex-end" }}
            onClick={e => e.stopPropagation()}>
            {showActions && (
              <>
                <button type="button"
                  style={{ ...iconBtnBase, color: "var(--accent)" }}
                  title={translate("admin.field_proposals.action.approve")}
                  aria-label={translate("admin.field_proposals.action.approve")}
                  onClick={() => onReview(row.id, "approved")}>
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                       strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <polyline points="20 6 9 17 4 12" />
                  </svg>
                </button>
                <button type="button"
                  style={{ ...iconBtnBase, color: "var(--danger)" }}
                  title={translate("admin.field_proposals.action.reject")}
                  aria-label={translate("admin.field_proposals.action.reject")}
                  onClick={() => onReview(row.id, "rejected")}>
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                       strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
                  </svg>
                </button>
              </>
            )}
            <button type="button"
              style={{ ...iconBtnBase, color: open ? "var(--accent)" : "var(--muted)" }}
              title={translate(open ? "admin.field_proposals.collapse" : "admin.field_proposals.expand")}
              aria-label={translate(open ? "admin.field_proposals.collapse" : "admin.field_proposals.expand")}
              onClick={() => setOpen(o => !o)}>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                   strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"
                   style={{ transform: open ? "rotate(180deg)" : "none", transition: "transform .15s" }}>
                <polyline points="6 9 12 15 18 9" />
              </svg>
            </button>
          </div>
        </Td>
      </tr>
      {open && (
        <tr style={{ borderBottom: "1px solid var(--border)" }}>
          <td colSpan={colSpan} style={{ padding: "6px 16px 14px 30px", background: "var(--panel)" }}>
            <div style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 13 }}>
              {details.map((d, i) => (
                <div key={i} style={{ display: "flex", gap: 12 }}>
                  <div className="muted" style={{ minWidth: 84, flexShrink: 0 }}>{translate(d.labelKey)}</div>
                  <div style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>{d.value}</div>
                </div>
              ))}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

function ReviewModal({
  proposalId, decision, onClose, onDone,
}: {
  proposalId: number;
  decision: "approved" | "rejected";
  onClose: () => void;
  onDone: () => void;
}) {
  const translate = useT();
  const [note, setNote] = useState("");
  const [submitting, setSubmitting] = useState(false);

  async function submit() {
    setSubmitting(true);
    try {
      await reviewFieldProposal(proposalId, decision, note || undefined);
      toast(
        translate(decision === "approved"
          ? "admin.field_proposals.toast.approved"
          : "admin.field_proposals.toast.rejected"),
        "success",
      );
      onDone();
    } catch (e) {
      toast(errMsg(e), "error");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <>
      <div
        onClick={onClose}
        style={{ position: "fixed", inset: 0, background: "var(--overlay)", zIndex: 60 }}
      />
      <div
        role="dialog"
        style={{
          position: "fixed", top: "50%", left: "50%",
          transform: "translate(-50%, -50%)",
          width: "min(440px, 92vw)",
          background: "var(--bg)", color: "var(--fg)",
          border: "1px solid var(--border)", borderRadius: 10,
          zIndex: 70, display: "flex", flexDirection: "column",
          boxShadow: "var(--shadow-modal)",
        }}
      >
        <div style={{ padding: "12px 16px", borderBottom: "1px solid var(--border)", fontWeight: 600 }}>
          {translate(decision === "approved"
            ? "admin.field_proposals.action.approve"
            : "admin.field_proposals.action.reject")}
        </div>
        <div style={{ padding: 16, display: "flex", flexDirection: "column", gap: 12, fontSize: 14 }}>
          <p className="muted" style={{ margin: 0 }}>
            {translate(decision === "approved"
              ? "admin.field_proposals.review.confirm_approve"
              : "admin.field_proposals.review.confirm_reject")}
          </p>
          <input
            value={note}
            onChange={e => setNote(e.target.value)}
            placeholder={translate("admin.field_proposals.review.note_ph")}
            style={{
              padding: "6px 8px", fontSize: 14,
              background: "transparent", color: "inherit",
              border: "1px solid var(--border)", borderRadius: 6,
            }}
          />
        </div>
        <div style={{
          padding: "10px 16px", borderTop: "1px solid var(--border)",
          display: "flex", justifyContent: "flex-end", gap: 8,
        }}>
          <button className="ghost" onClick={onClose} disabled={submitting}>
            {translate("common.cancel")}
          </button>
          <button
            className={decision === "approved" ? "primary" : "ghost"}
            style={decision === "rejected" ? { color: "var(--danger)" } : undefined}
            onClick={submit}
            disabled={submitting}
          >
            {submitting
              ? translate("common.loading")
              : translate(decision === "approved"
                ? "admin.field_proposals.action.approve"
                : "admin.field_proposals.action.reject")}
          </button>
        </div>
      </div>
    </>
  );
}

// ── Helpers ───────────────────────────────────────────────────────────

function Th({ children }: { children: React.ReactNode }) {
  return <th style={{ padding: "10px 14px", textAlign: "left", fontWeight: 600 }}>{children}</th>;
}

function Td({ children }: { children: React.ReactNode }) {
  return <td style={{ padding: "10px 14px", verticalAlign: "top" }}>{children}</td>;
}

function fmtTime(iso: string): string {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

function safePayload(p: Record<string, unknown> | string): Record<string, unknown> {
  // Backend returns JSONB. Some psycopg configurations pass it through
  // as a string; handle both.
  if (typeof p === "string") {
    try { return JSON.parse(p); } catch { return {}; }
  }
  return p || {};
}

function errMsg(e: unknown): string {
  if (e instanceof ApiError) {
    return typeof e.detail === "string" ? e.detail : JSON.stringify(e.detail);
  }
  if (e instanceof Error) return e.message;
  return String(e);
}
