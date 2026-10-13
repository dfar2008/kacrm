import { useEffect, useRef, useState } from "react";

import {
  copilotScopedSend,
  copilotScopedHistory,
  uploadChatAttachments,
  type ChatAttachment,
  type CopilotToolExecution,
} from "@/lib/api";
import { MarkdownText } from "@/components/MarkdownText";
import { formatToolExecution } from "@/lib/copilotRender";
import { useT } from "@/lib/i18n";

interface Msg {
  role: "user" | "assistant" | "system";
  text: string;
  /** Tool-execution chips for an assistant turn (rendered exactly like
   *  CopilotPanel — a compact "·"-joined line under the reply). */
  tools?: CopilotToolExecution[];
  /** Files attached to a just-sent user turn — session-only, lost on reload. */
  attachments?: File[];
}

interface Props {
  /** Current builder module. The scoped agent is bound to it. */
  module: string;
  onClose: () => void;
  /** Called after the agent ran tool(s) that changed the schema so the
   *  builder reloads its field layout. */
  onApplied: () => void;
  /** 外部(如工作流示例)塞进来的待发提示词;消费后 onSeedConsumed 清空。
   *  只预填输入框,不自动发送——用户先看一眼再发(可逆/可改)。 */
  seedText?: string | null;
  onSeedConsumed?: () => void;
  /** 对话 scope:schema(模块管理,默认)/ workflow(工作流面板)。
   *  不同 scope 用各自独立的对话 bucket,互不串扰。 */
  scope?: "schema" | "workflow";
  /** 起手提示:空对话时作为可点的示例片(点了填进输入框,不自动发)。
   *  从外部传入(如工作流的事件示例),让主列表保持干净。 */
  starters?: string[];
}

export function SchemaAssistant({ module, onClose, onApplied, seedText, onSeedConsumed, scope = "schema", starters }: Props) {
  const t = useT();
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [files, setFiles] = useState<File[]>([]);
  const scrollRef = useRef<HTMLDivElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  // 外部 seed(工作流示例)→ 预填输入框 + 聚焦,不自动发送。
  useEffect(() => {
    if (!seedText) return;
    setInput(seedText);
    onSeedConsumed?.();
    requestAnimationFrame(() => inputRef.current?.focus());
  }, [seedText, onSeedConsumed]);

  // The scoped endpoint persists both turns server-side (namespaced
  // under ai-schema:<module>), mirroring the copilot. On open / module
  // change, re-hydrate the conversation from that bucket so history
  // survives reloads. Empty bucket → clean slate (empty-state hint).
  useEffect(() => {
    if (!module) {
      setMsgs([]);
      return;
    }
    let alive = true;
    copilotScopedHistory(scope, { module }, 40)
      .then((r) => {
        if (!alive) return;
        setMsgs(
          (r.turns || []).map((tn) => ({
            role: tn.role === "assistant" ? "assistant" : "user",
            text: tn.text,
            tools:
              tn.tool_executions && tn.tool_executions.length
                ? tn.tool_executions
                : undefined,
          })),
        );
      })
      .catch(() => {
        if (alive) setMsgs([]);
      });
    return () => {
      alive = false;
    };
  }, [module]);

  useEffect(() => {
    scrollRef.current?.scrollTo(0, 1e9);
  }, [msgs]);

  const send = async () => {
    const userText = input.trim();
    if ((!userText && files.length === 0) || busy) return;
    const sentFiles = files;
    setBusy(true);
    setInput("");
    setFiles([]);

    // Build history from prior turns BEFORE we append the new user turn.
    const history = msgs
      .filter((m) => m.role === "user" || m.role === "assistant")
      .slice(-6)
      .map((m) => ({
        role: (m.role === "user" ? "user" : "assistant") as "user" | "assistant",
        content: m.text,
      }));

    setMsgs((m) => [
      ...m,
      { role: "user", text: userText, attachments: sentFiles.length ? sentFiles : undefined },
    ]);

    try {
      let attachments: ChatAttachment[] = [];
      if (sentFiles.length) {
        attachments = await uploadChatAttachments(sentFiles);
      }
      const cp = await copilotScopedSend({
        scope,
        context: { module },
        message: userText,
        history,
        attachments,
      });

      const hadTools = !!(cp.tool_executions && cp.tool_executions.length > 0);
      if (cp.fallback) {
        setMsgs((m) => [
          ...m,
          {
            role: "system",
            text: t("admin.schema.assistant.network_error", {
              error: cp.error || t("copilot.tool.unknown_err"),
            }),
          },
        ]);
        return;
      }
      setMsgs((m) => [
        ...m,
        {
          role: "assistant",
          text: cp.reply || "",
          tools: hadTools ? cp.tool_executions : undefined,
        },
      ]);
      // Any successful schema-mutating tool → reload the builder layout.
      if (hadTools && cp.tool_executions.some((te) => te.ok)) {
        onApplied();
      }
    } catch (e: any) {
      setMsgs((m) => [
        ...m,
        { role: "system", text: t("admin.schema.assistant.network_error", { error: String(e?.message || e) }) },
      ]);
    } finally {
      setBusy(false);
    }
  };

  const isDisabled = busy || (!input.trim() && files.length === 0);

  return (
    <div
      style={{
        width: 380,
        borderLeft: "1px solid var(--border)",
        display: "flex",
        flexDirection: "column",
        background: "var(--panel)",
        height: "100%",
      }}
    >
      {/* header */}
      <div
        style={{
          padding: "12px 14px",
          borderBottom: "1px solid var(--border)",
          display: "flex",
          alignItems: "center",
          background: "var(--panel-2)",
        }}
      >
        <span
          style={{
            width: 28,
            height: 28,
            borderRadius: 8,
            background: "var(--accent)",
            color: "var(--bg)",
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            fontSize: 14,
            fontWeight: 700,
            marginRight: 10,
          }}
        >
          ✨
        </span>
        <div style={{ display: "flex", flexDirection: "column" }}>
          <strong style={{ fontSize: 14, color: "var(--fg)" }}>
            {t("admin.schema.assistant.title")}
          </strong>
          <span style={{ fontSize: 13, color: "var(--muted)" }}>
            {t(scope === "workflow" ? "admin.schema.assistant.subtitle_workflow" : "admin.schema.assistant.subtitle")}
          </span>
        </div>
        <button
          onClick={onClose}
          title={t("admin.schema.assistant.close")}
          style={{
            marginLeft: "auto",
            border: "none",
            background: "transparent",
            cursor: "pointer",
            fontSize: 18,
            color: "var(--muted)",
            padding: 4,
            lineHeight: 1,
          }}
        >
          ×
        </button>
      </div>

      {/* messages */}
      <div
        ref={scrollRef}
        style={{
          flex: 1,
          overflow: "auto",
          padding: 12,
          display: "flex",
          flexDirection: "column",
          gap: 8,
        }}
      >
        {msgs.length === 0 && (
          <div style={{ padding: "8px 4px", display: "flex", flexDirection: "column", gap: 12 }}>
            <div style={{ fontSize: 14, color: "var(--muted)", lineHeight: 1.6 }}>
              {t(scope === "workflow" ? "admin.schema.assistant.empty_hint_workflow" : "admin.schema.assistant.empty_hint_agent")}
            </div>
            {starters && starters.length > 0 && (
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                {starters.map((s, i) => (
                  <button
                    key={i}
                    onClick={() => {
                      setInput(s);
                      requestAnimationFrame(() => inputRef.current?.focus());
                    }}
                    style={{
                      textAlign: "left", fontSize: 13, lineHeight: 1.5,
                      padding: "8px 10px", borderRadius: 8, cursor: "pointer",
                      border: "1px solid var(--border)", color: "var(--fg)",
                      background: "var(--bg)",
                    }}
                  >💬 {s}</button>
                ))}
              </div>
            )}
          </div>
        )}
        {msgs.map((m, i) => (
          <div
            key={i}
            style={{
              alignSelf: m.role === "user" ? "flex-end" : "flex-start",
              maxWidth: "92%",
            }}
          >
            <div
              style={{
                fontSize: 13,
                color: "var(--muted)",
                marginBottom: 3,
                textAlign: m.role === "user" ? "right" : "left",
                opacity: 0.7,
              }}
            >
              {m.role === "user"
                ? t("admin.schema.assistant.role_user")
                : m.role === "system"
                ? t("admin.schema.assistant.role_system")
                : t("admin.schema.assistant.role_assistant")}
            </div>
            <div
              style={{
                padding: "8px 12px",
                borderRadius:
                  m.role === "user"
                    ? "12px 12px 2px 12px"
                    : "12px 12px 12px 2px",
                fontSize: 14,
                lineHeight: 1.55,
                whiteSpace: m.role === "assistant" ? "normal" : "pre-wrap",
                wordBreak: "break-word",
                background:
                  m.role === "user"
                    ? "var(--accent)"
                    : m.role === "system"
                    ? "var(--warning-soft)"
                    : "var(--panel-2)",
                color: m.role === "user" ? "var(--bg)" : "var(--fg)",
                border:
                  m.role === "system"
                    ? "1px solid var(--warning)"
                    : "none",
              }}
            >
              {m.role === "assistant" ? (
                <MarkdownText text={m.text} />
              ) : (
                m.text
              )}
              {/* Tool-execution chip line — mirrors CopilotPanel: a
                  compact "·"-joined summary under the assistant reply. */}
              {m.role === "assistant" && m.tools && m.tools.length > 0 && (
                <div
                  style={{
                    marginTop: m.text ? 6 : 0,
                    fontSize: 13,
                    color: "var(--muted)",
                  }}
                >
                  {m.tools.map((te) => formatToolExecution(t, te)).join(" · ")}
                </div>
              )}
            </div>
            {/* Clickable attachment chips — session-only (just-sent turns). */}
            {m.role === "user" && m.attachments && m.attachments.length > 0 && (
              <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginTop: 4, justifyContent: "flex-end" }}>
                {m.attachments.map((file, idx) => (
                  <button
                    key={idx}
                    onClick={() => {
                      const url = URL.createObjectURL(file);
                      window.open(url, "_blank");
                      // Revoke once the new tab has loaded the blob —
                      // an immediate revoke would abort the open.
                      setTimeout(() => URL.revokeObjectURL(url), 60_000);
                    }}
                    style={{
                      fontSize: 13,
                      padding: "2px 8px",
                      borderRadius: 12,
                      border: "1px solid var(--border)",
                      background: "var(--panel-2)",
                      color: "var(--fg)",
                      cursor: "pointer",
                    }}
                  >
                    📎 {file.name}
                  </button>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>

      {/* input */}
      <div
        style={{
          padding: 10,
          borderTop: "1px solid var(--border)",
          display: "flex",
          flexDirection: "column",
          gap: 8,
          background: "var(--panel-2)",
        }}
      >
        {/* File chips */}
        {files.length > 0 && (
          <div
            style={{
              display: "flex",
              flexWrap: "wrap",
              gap: 4,
            }}
          >
            {files.map((f, idx) => (
              <span
                key={idx}
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 4,
                  fontSize: 13,
                  padding: "2px 8px",
                  border: "1px solid var(--border)",
                  borderRadius: 12,
                  background: "var(--panel)",
                  color: "var(--fg)",
                  maxWidth: 200,
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
              >
                <span
                  style={{
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                >
                  {f.name}
                </span>
                <button
                  onClick={() => setFiles((prev) => prev.filter((_, i) => i !== idx))}
                  style={{
                    border: "none",
                    background: "transparent",
                    cursor: "pointer",
                    color: "var(--muted)",
                    padding: 0,
                    fontSize: 14,
                    lineHeight: 1,
                    flexShrink: 0,
                  }}
                >
                  ×
                </button>
              </span>
            ))}
          </div>
        )}

        {/* Hidden file input */}
        <input
          ref={fileRef}
          type="file"
          multiple
          accept="image/*,.pdf,.xlsx,.xlsm,.docx,.csv,.tsv,.txt"
          style={{ display: "none" }}
          onChange={(e) => {
            const picked = Array.from(e.target.files || []);
            if (picked.length) setFiles((prev) => [...prev, ...picked]);
            e.target.value = "";
          }}
        />

        {/* Textarea */}
        <textarea
          ref={inputRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send();
            }
          }}
          placeholder={t(scope === "workflow" ? "admin.schema.assistant.input_placeholder_workflow" : "admin.schema.assistant.input_placeholder")}
          disabled={busy}
          rows={3}
          style={{
            width: "100%",
            minHeight: 64,
            padding: "8px 10px",
            fontSize: 14,
            border: "1px solid var(--border)",
            borderRadius: 6,
            background: "var(--panel)",
            color: "var(--fg)",
            boxSizing: "border-box",
            resize: "vertical",
            fontFamily: "inherit",
          }}
        />

        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            gap: 8,
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            {/* Attach button */}
            <button
              onClick={() => fileRef.current?.click()}
              title={t("admin.schema.assistant.attach")}
              disabled={busy}
              style={{
                border: "1px solid var(--border)",
                background: "transparent",
                cursor: busy ? "not-allowed" : "pointer",
                color: "var(--muted)",
                padding: "4px 7px",
                fontSize: 14,
                lineHeight: 1,
                borderRadius: 6,
              }}
            >
              📎
            </button>
          </div>
          <button
            onClick={send}
            disabled={isDisabled}
            style={{
              padding: "6px 14px",
              fontSize: 14,
              fontWeight: 500,
              background: isDisabled ? "var(--panel)" : "var(--accent)",
              color: isDisabled ? "var(--muted)" : "var(--bg)",
              border: "1px solid",
              borderColor: isDisabled ? "var(--border)" : "var(--accent)",
              borderRadius: 6,
              cursor: isDisabled ? "not-allowed" : "pointer",
            }}
          >
            {busy ? "…" : t("admin.schema.assistant.send")}
          </button>
        </div>
      </div>
    </div>
  );
}

export default SchemaAssistant;
