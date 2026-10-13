import { useEffect, useRef, useState } from "react";
import {
  getRecord, updateRecord,
  copilotCrmSend, getCopilotHistory, uploadChatAttachments, ApiError,
  type CrmFieldMeta, type ChatAttachment,
} from "@/lib/api";
import { RecordInsightCard } from "@/components/RecordInsightCard";
import { CopilotListBrief } from "@/components/CopilotListBrief";
import { MarkdownText } from "@/components/MarkdownText";
import { TeamBriefSection } from "@/components/TeamBriefSection";
import { TeamFootprintCard } from "@/components/TeamFootprintCard";
import { ModelPicker } from "@/components/ModelPicker";
import { getMe, type CurrentUser } from "@/lib/api";
import { formatToolExecution } from "@/lib/copilotRender";
import { useT } from "@/lib/i18n";

// Charter v2 §定律 5 + §简化 (1 user = 1 profile): the SPA does not
// pick agents. /api/copilot/crm/send is the single Copilot entrypoint;
// the backend router dispatches. The old dev "specialist" picker,
// /draft|/research|... text prefixes, per-question agent hints, the
// invokeAgent fallback (Phase 3 deleted /api/agents/invoke), and the
// FeedbackBar (Phase 3 deleted /api/feedback/*) all went with it.

interface Props {
  module?: string;
  recordId?: number;
  // External prefill (e.g. from list-UI Copilot CTAs). Changing `nonce`
  // injects `text` into the input so the human can tweak before send.
  prefill?: { text: string; nonce: number } | null;
  // Parent callback when a field was edited through this panel — lets
  // RecordDetail re-fetch so the chip row / drawer refresh immediately.
  onRecordUpdated?: () => void;
  // Embedding contexts (e.g. AgentWorkbench) already render their own
  // RecordInsightCard — suppress the empty-state insight here to avoid
  // a duplicate "AI 已看过这条记录".
  hideInsightCard?: boolean;
}

/**
 * Rule-based field-edit interpreter — pending a real conversational
 * Copilot with tool-calling. Recognizes patterns:
 *   把 <字段> 改成 <值>
 *   把 <字段> 改为 <值>
 *   改 <字段> 为 <值>
 *   <字段> 改成 <值>
 *
 * Maps <字段> against record.fields (field_label OR field_name, case
 * insensitive, Chinese equals Chinese). Returns null if no pattern or
 * no field matched; caller falls back to agent invocation.
 */
const EDIT_PATTERNS = [
  /^把\s*(.+?)\s*改(?:成|为)\s*(.+)$/,
  /^改\s*(.+?)\s*为\s*(.+)$/,
  /^(.+?)\s*改(?:成|为)\s*(.+)$/,
];

function parseEditIntent(text: string, fields: CrmFieldMeta[]): {
  field: CrmFieldMeta; value: string;
} | null {
  const trimmed = text.trim();
  for (const re of EDIT_PATTERNS) {
    const m = trimmed.match(re);
    if (!m) continue;
    const label = m[1].trim();
    const value = m[2].trim();
    if (!label || !value) continue;
    // Direct-equality label / name match first (most reliable).
    const direct = fields.find(f =>
      !f.is_hidden && !f.is_readonly && (
        (f.field_label && f.field_label === label) ||
        f.field_name === label
      )
    );
    if (direct) return { field: direct, value };
    // Fall back to substring (e.g. "预计成交" matches "预计成交日期").
    const fuzzy = fields.find(f =>
      !f.is_hidden && !f.is_readonly && (
        (f.field_label && f.field_label.includes(label)) ||
        f.field_name.includes(label)
      )
    );
    if (fuzzy) return { field: fuzzy, value };
  }
  return null;
}

/** Normalize a user-typed value per field type before PUT. */
function coerceValue(field: CrmFieldMeta, value: string): any {
  const ft = (field.field_type || "").toLowerCase();
  if (["number", "currency", "money", "decimal", "integer"].includes(ft)) {
    const cleaned = value.replace(/[,¥￥\s]/g, "");
    // "60万" → 600000; "1.5亿" → 150000000
    const wan = cleaned.match(/^([-+]?\d+(?:\.\d+)?)万$/);
    if (wan) return Number(wan[1]) * 10000;
    const yi = cleaned.match(/^([-+]?\d+(?:\.\d+)?)亿$/);
    if (yi) return Number(yi[1]) * 100_000_000;
    const plain = cleaned.match(/^[-+]?\d+(?:\.\d+)?$/);
    if (plain) return Number(cleaned);
  }
  if (ft === "boolean" || ft === "checkbox") {
    if (/^(是|对|true|yes|1|on)$/i.test(value)) return true;
    if (/^(否|不|false|no|0|off)$/i.test(value)) return false;
  }
  return value;
}

interface Turn {
  role: "user" | "agent" | "system";
  text: string;
  at: number;
  /** Model id used for this assistant turn (only set on ``role==="agent"``). */
  model?: string | null;
}

export function CopilotPanel({ module, recordId, prefill, onRecordUpdated, hideInsightCard }: Props) {
  // Phase 5.2.C / 5.2.E — manager flag for team brief section + sticky footprint badge.
  const [me, setMe] = useState<CurrentUser | null>(null);
  const [footprintDismissed, setFootprintDismissed] = useState(false);
  useEffect(() => { getMe().then(setMe).catch(() => null); }, []);
  // Reset dismiss state when navigating to a different record.
  useEffect(() => { setFootprintDismissed(false); }, [module, recordId]);
  const t = useT();
  const [input, setInput] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);
  const [model, setModel] = useState<string | null>(null);
  // Pending attachments for the next send (mirrors ChatInbox). Uploaded
  // to /api/chat/attachments at send-time, then inlined into the message.
  const [files, setFiles] = useState<File[]>([]);
  const attachInputRef = useRef<HTMLInputElement>(null);
  // Cache of the current record's editable fields — populated lazily
  // when user types an edit intent. Keyed by `${module}:${recordId}`.
  const [fieldCache, setFieldCache] = useState<{
    key: string; fields: CrmFieldMeta[];
  } | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  // Accept prefills from the list-UI Copilot CTAs (Law 5: AI drives).
  useEffect(() => {
    if (prefill && prefill.text) {
      setInput(prefill.text);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prefill?.nonce]);

  // Re-hydrate the panel from the event stream — works in BOTH
  // record-mode (subjects=[{module,id}]) AND list-mode
  // (subjects=[{module}]) per Charter §定律 1: the conversation IS
  // persisted, not just React state.
  useEffect(() => {
    if (!module) {
      setTurns([]);
      return;
    }
    let alive = true;
    // Backend treats undefined record_id as list-mode.
    getCopilotHistory(module, recordId as any, 40)
      .then(r => {
        if (!alive) return;
        setTurns((r.turns || []).map(t => ({
          role: t.role === "assistant" ? "agent" : "user",
          text: t.text,
          at: t.at ? new Date(t.at).getTime() : Date.now(),
          model: t.role === "assistant" ? (t.model ?? null) : null,
        })));
      })
      .catch(() => { if (alive) setTurns([]); });
    return () => { alive = false; };
  }, [module, recordId]);

  useEffect(() => {
    scrollRef.current?.scrollTo({
      top: scrollRef.current.scrollHeight, behavior: "smooth",
    });
  }, [turns.length]);

  async function send(override?: { text: string }) {
    // `override` lets the insight card fire a suggested question directly
    // (skip the input box). The router agent decides where to dispatch —
    // the SPA does not pick agents anymore (Charter v2 §定律 5).
    const rawText = override?.text ?? input;
    const text = rawText.trim();
    // Insight-card quick prompts (override) never carry attachments;
    // only the input box does.
    const sentFiles = override ? [] : files;
    if (!text && sentFiles.length === 0) return;
    // Show attached filenames in the echoed user turn so the human sees
    // what was sent alongside the text.
    const attachMarker = sentFiles.length
      ? `📎 ${sentFiles.map(f => f.name).join("、")}`
      : "";
    const display = attachMarker ? (text ? `${text}\n${attachMarker}` : attachMarker) : text;
    setBusy(true);
    if (!override) { setInput(""); setFiles([]); }
    const userTurn: Turn = { role: "user", text: display, at: Date.now() };
    setTurns(ts => [...ts, userTurn]);
    try {
      // (1) was: log the user's message via /api/touchpoints/chat. The
      // backend /api/copilot/crm/send now persists both user + assistant
      // turns itself, so the SPA-side write was double-accounting.

      // 1.4) Real LLM tool-calling path (Phase B). Works in BOTH
      // record-mode and list-mode (backend uses different system prompt
      // + tool subset based on whether record_id is set). On success —
      // render reply + tool executions, refresh record if anything was
      // edited. On fallback=true (LLM transport failure) drop through
      // to the regex parser below (only useful in record-mode).
      if (module) {
        try {
          // Send the last 6 turns as history so the LLM has some context.
          const history = turns.slice(-6)
            .filter(tn => tn.role === "user" || tn.role === "agent")
            .map(tn => ({
              role: (tn.role === "user" ? "user" : "assistant") as "user" | "assistant",
              content: tn.text,
            }));
          // Upload any attachments first (image → data_url, document →
          // extracted text), then inline them into the dispatched message.
          let attachments: ChatAttachment[] = [];
          if (sentFiles.length) {
            attachments = await uploadChatAttachments(sentFiles);
          }
          const cp = await copilotCrmSend({
            module,
            record_id: recordId as any,  // backend treats undefined as list-mode
            message: text, history,
            model,
            attachments,
          });
          if (!cp.fallback) {
            const hadTools = !!(cp.tool_executions && cp.tool_executions.length > 0);
            // Render tool-execution chips as a compact system turn.
            if (hadTools) {
              const line = cp.tool_executions.map(te => formatToolExecution(t, te)).join(" · ");
              setTurns(ts => [...ts, {
                role: "system", text: line, at: Date.now(),
              }]);
              if (cp.tool_executions.some(te => te.ok
                  && (te.name === "update_record" || te.name === "create_record"))) {
                onRecordUpdated?.();
              }
            }
            if (cp.reply && cp.reply.trim()) {
              const usedModel = model ? (model.split("::").pop() || model) : null;
              setTurns(ts => [...ts, {
                role: "agent", text: cp.reply, at: Date.now(),
                model: usedModel,
              }]);
            }
            return;
          }
          // fallback=true → LLM transport failed. Let the regex path try.
        } catch (e) {
          // Network / 5xx — also fall through to regex so offline demo works.
          console.warn("copilot LLM path failed, falling back to regex", e);
        }
      }

      // 1.5) Rule-based field-edit intent — offline fallback. Matches
      // "把 金额 改成 60万" and similar; applies via PUT and emits a
      // system turn reporting the diff.
      if (module && recordId !== undefined) {
        let fields = fieldCache?.key === `${module}:${recordId}` ? fieldCache.fields : null;
        if (!fields) {
          try {
            const rec = await getRecord(module, recordId);
            fields = rec.fields || [];
            setFieldCache({ key: `${module}:${recordId}`, fields });
          } catch { /* fall through to agent path */ }
        }
        if (fields && fields.length) {
          const intent = parseEditIntent(text, fields);
          if (intent) {
            const coerced = coerceValue(intent.field, intent.value);
            try {
              await updateRecord(module, recordId, { [intent.field.field_name]: coerced });
              const label = intent.field.field_label || intent.field.field_name;
              setTurns(ts => [...ts, {
                role: "system",
                text: t("copilot.field_change_done", { label, value: intent.value }),
                at: Date.now(),
              }]);
              onRecordUpdated?.();
              return;   // short-circuit agent call
            } catch (e) {
              const msg = e instanceof ApiError
                ? (typeof e.detail === "object" && e.detail && (e.detail as any).message) || `HTTP ${e.status}`
                : String(e);
              setTurns(ts => [...ts, {
                role: "system",
                text: t("copilot.field_change_failed", {
                  label: intent.field.field_label || intent.field.field_name,
                  msg,
                }),
                at: Date.now(),
              }]);
              return;
            }
          }
        }
      }

      // 2) Nothing tool-callable matched. Two distinct cases:
      //    a) User has a record open → LLM path errored (transport / 4xx)
      //       OR returned a non-tool-call reply we already rendered above;
      //       the regex didn't match either → show a record-scoped retry
      //       hint, NOT a "go to event stream" redirect (which would be
      //       wrong since the user IS on a record).
      //    b) User has no record open → genuinely cross-record query.
      //       Redirect to the event-stream's TodayPriority feed.
      const hintKey = (module && recordId !== undefined)
        ? "copilot.fallback_on_record"
        : "copilot.fallback_no_record";
      setTurns(ts => [...ts, {
        role: "system",
        text: t(hintKey),
        at: Date.now(),
      }]);
    } catch (e) {
      const msg = e instanceof ApiError
        ? (typeof e.detail === "string" ? e.detail : `HTTP ${e.status}`)
        : String(e);
      setTurns(ts => [...ts, { role: "system", text: t("copilot.error", { msg }), at: Date.now() }]);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="col" style={{ height: "100%", padding: 8, gap: 8 }}>
      <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
        <div style={{ fontWeight: 600 }}>{t("copilot.title")}</div>
      </div>

      {/* Phase 5.2.C — manager team brief at top sticky region (above
          messages, never inline). Auto-hides for non-managers and when
          there's no recent team_tick. */}
      <TeamBriefSection isManager={!!me?.is_manager || !!me?.is_admin} />

      {/* Phase 5.2.E — mid-conversation sticky badge of team
          footprints on this record. Dismiss-able; doesn't insert
          into the message stream. v1.4 — renders whenever the
          backend returns rows (record-level visibility, not
          actor/manager-level), so a 销售 inheriting a customer from
          a departed colleague still sees the history. */}
      {module && recordId !== undefined && !footprintDismissed && (
        <TeamFootprintCard
          module={module}
          recordId={recordId}
          mode="badge"
          onDismiss={() => setFootprintDismissed(true)}
        />
      )}

      <div ref={scrollRef} className="col"
           style={{ flex: 1, overflowY: "auto", gap: 8, paddingRight: 4 }}>
        {/* List-mode brief sits at the top of the panel always, even
            after the user has started chatting. Charter §定律 5: the
            "AI 已看过你今天的事件流" header IS the panel — chat
            history accumulates below it. */}
        {recordId === undefined && (
          <CopilotListBrief
            module={module}
            onAsk={(text) => { void send({ text }); }}
          />
        )}
        {turns.length === 0 && module && recordId !== undefined && (
          hideInsightCard ? (
            <div className="muted small" style={{ padding: 8 }}>
              {t("copilot.empty.with_record")}
            </div>
          ) : (
            <RecordInsightCard
              module={module}
              recordId={recordId}
              onAsk={(text) => { void send({ text }); }}
            />
          )
        )}
        {turns.map((tn, i) => (
          <div key={i} className="card" style={{
            padding: 10,
            borderColor: tn.role === "user" ? "var(--accent)"
              : tn.role === "system" ? "var(--danger)" : "var(--border)",
          }}>
            <div className="muted small" style={{ marginBottom: 4 }}>
              {tn.role === "user" ? t("copilot.role.you")
                : tn.role === "system" ? t("copilot.role.system")
                : t("copilot.role.assistant")}
              {tn.role === "agent" && tn.model && (
                <span style={{ marginLeft: 6, opacity: 0.7 }}>
                  · {t("model_picker.via", { model: tn.model })}
                </span>
              )}
            </div>
            {tn.role === "agent" ? (
              <MarkdownText text={tn.text} />
            ) : (
              <div style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>{tn.text}</div>
            )}
          </div>
        ))}
      </div>
      <div className="col" style={{ gap: 6 }}>
        {/* Persistent quick-prompt chips. The full insight card lives
            inside the empty state above; once the user has started a
            conversation, just keep the suggestions reachable so they
            don't have to retype the next prompt. */}
        {turns.length > 0 && module && recordId !== undefined && !hideInsightCard && (
          <RecordInsightCard
            module={module}
            recordId={recordId}
            onAsk={(text) => { void send({ text }); }}
            compact
          />
        )}
        {/* 已选附件 chips（与 ChatInbox 一致） */}
        {files.length > 0 && (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {files.map((f, i) => (
              <span key={i} style={{
                display: "inline-flex", alignItems: "center", gap: 4,
                fontSize: 14, padding: "2px 8px", borderRadius: 12,
                background: "var(--panel)", color: "var(--muted)",
              }}>
                📎 {f.name}
                <button className="ghost" title={t("common.delete")}
                  onClick={() => setFiles(prev => prev.filter((_, j) => j !== i))}
                  style={{ border: "none", background: "transparent", cursor: "pointer", padding: 0, color: "inherit" }}>×</button>
              </span>
            ))}
          </div>
        )}
        <textarea
          placeholder={recordId ? t("copilot.input.ph_with_record") : t("copilot.input.ph_no_record")}
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
              e.preventDefault(); send();
            }
          }}
          style={{ minHeight: 60 }}
        />
        <div className="row" style={{ justifyContent: "space-between", alignItems: "center" }}>
          <div className="row" style={{ gap: 8, alignItems: "center" }}>
            <input ref={attachInputRef} type="file" multiple
              accept="image/*,.pdf,.xlsx,.xlsm,.docx,.csv,.tsv,.txt"
              style={{ display: "none" }}
              onChange={e => {
                // Capture synchronously before clearing: setFiles' functional
                // updater runs deferred, but e.target.value="" runs now and
                // empties e.target.files — reading it inside the updater would
                // see no files (→ no attachment, no chip).
                const picked = Array.from(e.target.files || []);
                setFiles(prev => [...prev, ...picked]);
                e.target.value = "";
              }} />
            <button className="ghost" title={t("chat.input.attach")}
              onClick={() => attachInputRef.current?.click()}
              style={{ padding: "4px 8px", fontSize: 16, lineHeight: 1 }}>📎</button>
            <ModelPicker persistKey="copilot" onChange={setModel} maxWidth={200} />
          </div>
          <div className="row" style={{ gap: 8, alignItems: "center" }}>
            <div className="muted small">{t("copilot.send_hint")}</div>
            <button className="primary"
                    disabled={busy || (!input.trim() && files.length === 0) || !model}
                    onClick={() => send()}>
              {busy ? t("copilot.send_wait") : t("copilot.send")}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
