import { CrmIcon, type CrmIconName } from "./CrmIcon";
import { useEffect, useState } from "react";
import {
  listInteractions, undoLastUpdate, type Interaction, ApiError,
} from "@/lib/api";
import { toast } from "@/lib/toast";
import { MarkdownText } from "@/components/MarkdownText";
import { useT, type T } from "@/lib/i18n";

interface Props {
  module: string;
  recordId: number;
  refreshKey: number;
  onUndone?: () => void;
}

// Charter v2 §定律 1: 主数据是事件流 — single unified view, no
// per-lane filtering. Charter v2 Phase 3 also removed the
// approval-gated drafts and the RL feedback subsystem; legacy
// kind="agent_draft" / kind="feedback" rows still in agent_messages
// from before the migration are filtered out client-side so the
// timeline reads clean.
const _HIDDEN_KINDS = new Set(["agent_draft", "feedback"]);

function formatTime(t: T, iso: string): string {
  try {
    const d = new Date(iso);
    const now = new Date();
    const pad = (n: number) => String(n).padStart(2, "0");
    const sameDay = d.toDateString() === now.toDateString();
    if (sameDay) return t("timeline.time.today", { hm: `${pad(d.getHours())}:${pad(d.getMinutes())}` });
    return d.toLocaleString(undefined, { hour12: false });
  } catch { return iso; }
}

// Pure-plumbing columns hidden from the timeline — no business meaning
// to a user. The backend already omits them from content.field_labels;
// this is the client-side belt so they never render even on old rows.
const _TIMELINE_HIDDEN_FIELDS = new Set(["memory_entity_id", "deleted", "id"]);

function summarize(t: T, i: Interaction): string {
  const c: any = i.content || {};
  const direct = c.text || c.summary || c.subject || c.message
    || (typeof c.content === "string" ? c.content : "");
  if (direct) return String(direct);

  // Server-resolved field_name → localized business label (attached by
  // /api/interactions). Falls back to the raw name if absent.
  const labels: Record<string, string> =
    c.field_labels && typeof c.field_labels === "object" ? c.field_labels : {};
  const lbl = (k: string) => labels[k] || k;
  const visible = (k: string) => !_TIMELINE_HIDDEN_FIELDS.has(k);
  // Server-resolved relation values — {field_name: {raw_id: display}} —
  // so owner_by=1 reads "Administrator", department_id=2 reads "销售部".
  const valDisplays: Record<string, Record<string, string>> =
    c.field_value_displays && typeof c.field_value_displays === "object"
      ? c.field_value_displays : {};

  switch (i.kind) {
    case "action": {
      const op = c.op || "?";
      const fields = (Array.isArray(c.fields) ? c.fields : []).filter(visible);
      const changes = c.changes && typeof c.changes === "object" ? c.changes : null;
      const changeKeys = changes ? Object.keys(changes).filter(visible) : [];
      const fmt = (v: any) => v === null || v === undefined || v === "" ? "∅"
        : typeof v === "string" ? `"${v}"` : String(v);
      // Prefer the server-resolved relation display for a field's value.
      const fmtVal = (k: string, v: any) => {
        const disp = valDisplays[k];
        if (disp && v !== null && v !== undefined && disp[String(v)]) {
          return `"${disp[String(v)]}"`;
        }
        return fmt(v);
      };
      if (op === "update" && changes) {
        const lines = changeKeys.map(k => {
          const ch = changes[k] || {};
          return `${lbl(k)}: ${fmtVal(k, ch.from)} → ${fmtVal(k, ch.to)}`;
        });
        return t("timeline.sum.update_with_changes", { lines: lines.join("\n") });
      }
      if (op === "update" && fields.length) return t("timeline.sum.update_fields", { fields: fields.map(lbl).join("、") });
      if (op === "create" && changes) {
        const lines = changeKeys.map(k => `${lbl(k)} = ${fmtVal(k, changes[k]?.to)}`);
        return t("timeline.sum.create_with_changes", { lines: lines.join("\n") });
      }
      if (op === "create") return t("timeline.sum.create");
      if (op === "delete") return t("timeline.sum.delete");
      if (op === "restore") return t("timeline.sum.restore");
      if (op === "undo") return t("timeline.sum.undo", { fields: fields.map(lbl).join("、") || "—" });
      return t("timeline.sum.generic_op", { op });
    }
    case "feedback": {
      const label = c.label === "accept" ? t("timeline.sum.fb_label.accept")
        : c.label === "reject" ? t("timeline.sum.fb_label.reject")
          : c.label === "revise" ? t("timeline.sum.fb_label.revise")
            : c.label || "?";
      const agent = c.agent ? t("timeline.sum.fb_agent", { agent: c.agent }) : "";
      const preview = typeof c.preview === "string"
        ? c.preview.replace(/\s+/g, " ").slice(0, 120)
        : "";
      const rev = typeof c.revision === "string"
        ? ` → "${c.revision.replace(/\s+/g, " ").slice(0, 120)}"`
        : "";
      return `${label}${agent}${preview ? `：${preview}` : ""}${rev}`;
    }
    case "pii_view":
      return t("timeline.sum.pii", { field: c.field || "?" });
    case "agent_draft": {
      const fields = c.fields && typeof c.fields === "object"
        ? Object.keys(c.fields).join("、")
        : "";
      if (fields) return t("timeline.sum.agent_draft_fields", { fields });
      return t("timeline.sum.agent_draft");
    }
    case "chat":
      return t("timeline.sum.chat_empty");
    case "contact_log":
      return c.note || t("timeline.sum.contact_empty");
    case "inbound_message":
      return c.body || c.preview || t("timeline.sum.inbound_empty");
  }
  // Final fallback — compact one-line view instead of raw JSON dump.
  const keys = Object.keys(c);
  if (keys.length === 0) return t("timeline.sum.empty");
  return keys.map(k => `${k}=${
    typeof c[k] === "object" ? JSON.stringify(c[k]).slice(0, 60) : String(c[k]).slice(0, 60)
  }`).join(" · ").slice(0, 240);
}

// Lucide-style 14×14 stroke icon keyed off the interaction kind +
// source. Same visual family as the rest of the shell — no OS-emoji
// rendering inconsistency. Returns null for "no obvious icon" so the
// row falls back to the actor avatar alone.
function KindIcon({ i }: { i: Interaction }) {
  const p = {
    width: 14, height: 14, viewBox: "0 0 24 24", fill: "none" as const,
    stroke: "currentColor", strokeWidth: 2,
    strokeLinecap: "round" as const, strokeLinejoin: "round" as const,
    "aria-hidden": true,
  };
  const k = i.kind || "";
  const src = i.source || "";
  // Channel-specific cues take priority — the channel is the headline
  // affordance ("this came in over wechat" / "I sent an email").
  if (src.startsWith("channel:email") || k === "email")
    return (<svg {...p}><rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 7 9 6 9-6"/></svg>);
  if (src.startsWith("channel:sms") || src.startsWith("channel:") )
    return (<svg {...p}><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>);
  // Phone/call kinds.
  if (k === "call" || k === "contact_log" || /phone|call/.test(src))
    return (<svg {...p}><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"/></svg>);
  // Action verbs (CRM writes).
  if (k === "action") {
    const op = (i.content as any)?.op;
    if (op === "create")
      return (<svg {...p}><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>);
    if (op === "update")
      return (<svg {...p}><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>);
    if (op === "delete")
      return (<svg {...p}><path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>);
    if (op === "undo" || op === "restore")
      return (<svg {...p}><path d="M3 7v6h6"/><path d="M21 17a9 9 0 0 0-15-6.7L3 13"/></svg>);
  }
  // Coach hint surfaced in timeline.
  if (k === "coach_hint")
    return (<svg {...p}><path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M5.6 18.4l2.1-2.1M16.3 7.7l2.1-2.1"/></svg>);
  // Copilot AI reply (lucide: bot)
  if (k === "copilot_reply" || src === "copilot:assistant")
    return (<svg {...p}><rect x="3" y="11" width="18" height="10" rx="2"/><circle cx="12" cy="5" r="2"/><path d="M12 7v4"/><line x1="8" y1="16" x2="8" y2="16"/><line x1="16" y1="16" x2="16" y2="16"/></svg>);
  // Inbound channel message.
  if (k === "inbound_message" || k === "chat" || src === "chat" || src === "copilot:user")
    return (<svg {...p}><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>);
  // Manual note.
  if (k === "pii_view" || src.startsWith("manual"))
    return (<svg {...p}><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>);
  // Agent activity (any source starting with agent:).
  if (src.startsWith("agent:"))
    return (<svg {...p}><rect x="3" y="11" width="18" height="10" rx="2"/><circle cx="12" cy="5" r="2"/><path d="M12 7v4"/><line x1="8" y1="16" x2="8" y2="16"/><line x1="16" y1="16" x2="16" y2="16"/></svg>);
  return null;
}

function sourceChip(src?: string): string {
  if (!src) return "?";
  // Localize the common source prefixes so the chip reads naturally
  // alongside the (Chinese) actor names. Falls through to the raw
  // value for sources we don't have a translation for yet.
  const map: Record<string, string> = {
    // Inline sources (no prefix) — what older interactions actually
    // carry. Keep these in addition to the prefixed forms.
    "chat": "聊天",
    "copilot": "AI · 助手",
    "copilot:user": "你 → AI",
    "copilot:assistant": "AI · 助手",
    "manual": "手动",
    "manual:note": "手动笔记",
    "manual:feedback": "手动反馈",
    "webui": "网页",
    "web": "网页",
    "system": "系统",
    "system:cron": "定时任务",
    // Channel-prefixed sources.
    "channel:wechat": "微信",
    "channel:weixin": "微信",
    "channel:feishu": "飞书",
    "channel:wecom": "企业微信",
    "channel:wecom_ilink": "企业微信",
    "channel:email": "邮件",
    "channel:sms": "短信",
    // Agent-prefixed sources.
    "agent:hermes": "AI",
    "agent:intake": "AI · 录入",
    "agent:coach": "AI · 教练",
    "agent:research": "AI · 调研",
    "agent:analyst": "AI · 分析",
    "agent:email_drafter": "AI · 起草",
    "agent:compliance": "AI · 合规",
    "agent:copilot": "AI · 助手",
  };
  if (map[src]) return map[src];
  if (src.startsWith("agent:")) return "AI · " + src.slice(6);
  if (src.startsWith("channel:")) return src.slice(8);
  return src;
}

/** Compute display actor: 🤖 agent / 👤 user / 🌐 channel. */
function actorLabel(t: T, i: Interaction): { icon: CrmIconName; name: string } {
  const actors = i.actors || [];
  // Prefer agent actor if present.
  const agentActor = actors.find((a: any) => a && (a.type === "agent" || a.agent));
  if (agentActor) {
    return { icon: "bot", name: String(agentActor.agent || agentActor.name || agentActor.id || "agent") };
  }
  const userActor = actors.find((a: any) => a && a.type === "user");
  if (userActor) {
    return { icon: "user", name: String(userActor.name || userActor.id || t("timeline.actor.user")) };
  }
  if (i.source && i.source.startsWith("channel:")) {
    return { icon: "message", name: i.source.replace("channel:", "") };
  }
  if (i.source && i.source.startsWith("agent:")) {
    return { icon: "bot", name: i.source.replace("agent:", "") };
  }
  return { icon: "activity", name: i.source || "system" };
}

export function InteractionTimeline({ module, recordId, refreshKey, onUndone }: Props) {
  const t = useT();
  const [items, setItems] = useState<Interaction[]>([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [undoing, setUndoing] = useState(false);
  const [openRaw, setOpenRaw] = useState<string | null>(null);

  async function handleUndo() {
    if (undoing) return;
    setUndoing(true);
    try {
      const r = await undoLastUpdate(module, recordId);
      toast(t("timeline.undo.toast", { n: r.reverted_fields.length }), "success");
      onUndone?.();
    } catch (e: any) {
      toast(e.message || t("timeline.undo.failed"), "error");
    } finally {
      setUndoing(false);
    }
  }

  function canUndo(i: Interaction): boolean {
    if (i.kind !== "action") return false;
    const op = i.content?.op;
    if (op !== "update" && op !== "create") return false;
    const ts = Date.parse(i.occurred_at);
    if (Number.isNaN(ts)) return false;
    return Date.now() - ts < 120_000;
  }

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setErr(null);
      try {
        const r = await listInteractions({
          module, record_id: recordId, limit: 50,
        });
        if (!cancelled) setItems(r.data || []);
      } catch (e) {
        if (!cancelled) setErr(e instanceof ApiError ? `${e.status}` : t("timeline.load_failed"));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [module, recordId, refreshKey]);

  const visible = items.filter(i => !_HIDDEN_KINDS.has(i.kind));

  return (
    <div className="col kacrm-detail-timeline" style={{ gap: 8 }}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <div style={{ fontWeight: 600 }}>{t("timeline.title")}</div>
        <div className="muted small">{t("timeline.count", { n: visible.length })}</div>
      </div>

      {loading && <div className="muted small">{t("common.loading")}</div>}
      {err && <div className="small" style={{ color: "var(--danger)" }}>{err}</div>}
      {!loading && visible.length === 0 && (
        <div className="muted small">{t("timeline.empty_all")}</div>
      )}

      {visible.map(i => {
        const actor = actorLabel(t, i);
        return (
          <div key={i.id} className="card col kacrm-timeline-event" style={{
            gap: 6, padding: 10,
            borderLeft: "3px solid var(--border)",
          }}>
            {/* Row 1: kind icon + actor + source chips + time + undo */}
            <div className="row kacrm-event-header" style={{ justifyContent: "space-between", alignItems: "baseline" }}>
              <div className="row" style={{ gap: 6, flexWrap: "wrap", alignItems: "center" }}>
                <span style={{ display: "inline-flex", alignItems: "center",
                                 color: "var(--muted)" }}>
                  <KindIcon i={i} />
                </span>
                <span className="chip" title={actor.name}>
                  <CrmIcon name={actor.icon} size={14} /> {actor.name}
                </span>
                {i.source && <span className="chip" style={{ opacity: 0.7 }}>
                  {sourceChip(i.source)}
                </span>}
                {i.content?.op && (
                  <span className="chip" style={{ opacity: 0.7 }}>op:{i.content.op}</span>
                )}
                {/* Charter §一定律 1: surface the link from a record-
                    level event back to the decision thread that caused
                    it. Without this chip, business writes from the
                    decisions surface look anonymous in the record
                    timeline — auditing why an owner_by changed becomes
                    a manual JSONB grep. */}
                {i.provenance?.caused_by_thread && (
                  <span className="chip" style={{
                    opacity: 0.85,
                    background: "var(--surface-soft)",
                    color: "var(--accent)",
                  }} title={`决策 thread ${i.provenance.caused_by_thread}`}>
                    <CrmIcon name="target" size={14} /> 来自决策
                  </span>
                )}
              </div>
              <div className="row" style={{ gap: 6 }}>
                {canUndo(i) && (
                  <button className="ghost" disabled={undoing} onClick={handleUndo}
                    title={t("timeline.undo.title")}
                    style={{ fontSize: 13, padding: "2px 8px" }}>{t("timeline.undo.label")}</button>
                )}
                <button className="ghost"
                  onClick={() => setOpenRaw(openRaw === i.id ? null : i.id)}
                  title={t("timeline.raw.title")}
                  style={{ fontSize: 13, padding: "2px 6px" }}>{`{}`}</button>
                <div className="muted small">{formatTime(t, i.occurred_at)}</div>
              </div>
            </div>

            {/* Row 2: narrative summary. copilot_reply rows render
                markdown so tables / lists / bold etc. don't bleed
                through as raw `**` and `|` syntax. */}
            {i.kind === "copilot_reply" ? (
              <MarkdownText text={summarize(t, i)} />
            ) : (
              <div style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
                {summarize(t, i)}
              </div>
            )}

            {/* For copilot_reply rows, surface the tool calls as small
                chips so the user can see WHAT the AI actually did,
                not just what it replied with. */}
            {i.kind === "copilot_reply" && Array.isArray((i.content as any)?.tool_executions)
              && (i.content as any).tool_executions.length > 0 && (
              <div className="row" style={{ gap: 4, flexWrap: "wrap" }}>
                {((i.content as any).tool_executions as any[]).map((te, idx) => (
                  <span key={idx} className="chip"
                        style={{
                          fontSize: 13,
                          opacity: te.ok === false ? 0.6 : 0.9,
                          background: te.ok === false
                            ? "var(--danger-soft)"
                            : "var(--success-soft)",
                        }}
                        title={te.error || JSON.stringify(te.result)?.slice(0, 200) || ""}>
                    {te.ok === false ? "⚠️" : "🔧"} {te.name}
                  </span>
                ))}
              </div>
            )}

            {/* Optional raw event lens (kept for power users / debugging). */}
            {openRaw === i.id && (
              <pre style={{
                fontSize: 13, margin: 0, padding: 8, overflow: "auto",
                background: "var(--surface-hover)", borderRadius: 4,
                maxHeight: 220,
              }}>
                {JSON.stringify(i, null, 2)}
              </pre>
            )}
          </div>
        );
      })}
    </div>
  );
}
