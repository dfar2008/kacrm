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
   *  CopilotPanel / SchemaAssistant — a compact "·"-joined line). */
  tools?: CopilotToolExecution[];
  /** Files attached to a just-sent user turn — session-only, lost on reload. */
  attachments?: File[];
}

interface Props {
  tabId: string;
  onClose: () => void;
  /** Called after the agent ran tool(s) that changed the dashboard so the
   *  page reloads the current tab's charts. */
  onApplied: () => void;
}

export function ChatPanel({ tabId, onClose, onApplied }: Props) {
  const t = useT();
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [files, setFiles] = useState<File[]>([]);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  // Clicking a suggested prompt (example group) pre-fills the input rather
  // than firing immediately — the suggestion is a starting point, the user
  // often wants to edit before sending.
  const pickPrompt = (text: string) => {
    setInput(text);
    requestAnimationFrame(() => {
      const el = inputRef.current;
      if (!el) return;
      el.focus();
      const n = text.length;
      try { el.setSelectionRange(n, n); } catch { /* not selectable */ }
    });
  };

  // The scoped endpoint persists both turns server-side (namespaced
  // under ai-dashboard:<tabId>), mirroring the copilot. On open / tab
  // change, re-hydrate the conversation from that bucket so history
  // survives reloads. Empty bucket → clean slate.
  useEffect(() => {
    setExamplesOpen(true);
    if (!tabId) {
      setMsgs([]);
      return;
    }
    let alive = true;
    copilotScopedHistory("dashboard", { tab_id: tabId }, 40)
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
  }, [tabId]);

  useEffect(() => {
    scrollRef.current?.scrollTo(0, 1e9);
  }, [msgs]);

  const send = async (overrideText?: string) => {
    const userText = (overrideText ?? input).trim();
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
        scope: "dashboard",
        context: { tab_id: tabId },
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
            text: t("dashboards.chat.network_error_fmt", {
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
      // Any successful dashboard-mutating tool → reload the current tab.
      if (hadTools && cp.tool_executions.some((te) => te.ok)) {
        onApplied();
      }
    } catch (e: any) {
      setMsgs((m) => [
        ...m,
        { role: "system", text: t("dashboards.chat.network_error_fmt", { error: String(e?.message || e) }) },
      ]);
    } finally {
      setBusy(false);
    }
  };

  // Grouped, collapsible suggestion prompts.
  const GROUPS: { key: string; titleKey: string; promptKeys: string[] }[] = [
    { key: "sales", titleKey: "dashboards.chat.group_sales",
      promptKeys: ["dashboards.ex.sales_1", "dashboards.ex.sales_2",
                   "dashboards.ex.sales_3", "dashboards.ex.sales_4"] },
    { key: "pipeline", titleKey: "dashboards.chat.group_pipeline",
      promptKeys: ["dashboards.ex.pipeline_1", "dashboards.ex.pipeline_2",
                   "dashboards.ex.pipeline_3", "dashboards.ex.pipeline_4"] },
    { key: "customer", titleKey: "dashboards.chat.group_customer",
      promptKeys: ["dashboards.ex.customer_1", "dashboards.ex.customer_2",
                   "dashboards.ex.customer_3", "dashboards.ex.customer_4"] },
    { key: "product", titleKey: "dashboards.chat.group_product",
      promptKeys: ["dashboards.ex.product_1", "dashboards.ex.product_2",
                   "dashboards.ex.product_3"] },
    { key: "order", titleKey: "dashboards.chat.group_order",
      promptKeys: ["dashboards.ex.order_1", "dashboards.ex.order_2",
                   "dashboards.ex.order_3", "dashboards.ex.order_4"] },
    { key: "team", titleKey: "dashboards.chat.group_team",
      promptKeys: ["dashboards.ex.team_1", "dashboards.ex.team_2",
                   "dashboards.ex.team_3"] },
    { key: "edit", titleKey: "dashboards.chat.group_edit",
      promptKeys: ["dashboards.ex.edit_1", "dashboards.ex.edit_2",
                   "dashboards.ex.edit_3", "dashboards.ex.edit_4"] },
  ];
  // All groups start collapsed — keeps the panel quiet on first open so
  // the user can scan section labels before committing to any one prompt.
  const [openGroups, setOpenGroups] = useState<Record<string, boolean>>({});
  const toggleGroup = (k: string) =>
    setOpenGroups((s) => ({ ...s, [k]: !s[k] }));

  // The whole "example prompts" tray — open on a fresh tab, toggleable.
  const [examplesOpen, setExamplesOpen] = useState<boolean>(true);

  const isDisabled = busy || (!input.trim() && files.length === 0);

  return (
    <div style={{
      width: 380,
      borderLeft: "1px solid var(--border)",
      display: "flex", flexDirection: "column",
      background: "var(--panel)",
    }}>
      {/* header */}
      <div style={{
        padding: "12px 14px",
        borderBottom: "1px solid var(--border)",
        display: "flex", alignItems: "center",
        background: "var(--panel-2)",
      }}>
        <span style={{
          width: 28, height: 28, borderRadius: 8,
          background: "var(--info)", color: "var(--fg-on-accent)",
          display: "inline-flex", alignItems: "center", justifyContent: "center",
          fontSize: 14, fontWeight: 700, marginRight: 10,
        }}>✨</span>
        <div style={{ display: "flex", flexDirection: "column" }}>
          <strong style={{ fontSize: 14, color: "var(--fg)" }}>{t("dashboards.chat.title")}</strong>
          <span style={{ fontSize: 13, color: "var(--muted)" }}>
            {t("dashboards.chat.subtitle")}
          </span>
        </div>
        <button className="icon-close" style={{
          marginLeft: "auto", border: "none",
          background: "transparent", cursor: "pointer",
          fontSize: 18, color: "var(--muted)",
        }}
                onClick={onClose} title={t("dashboards.chat.collapse")}>×</button>
      </div>

      {/* messages */}
      <div ref={scrollRef}
           style={{
             flex: 1, overflow: "auto", padding: 12,
             display: "flex", flexDirection: "column", gap: 8,
           }}>
        <div style={{ padding: "8px 2px", display: "flex",
                        flexDirection: "column", gap: 6 }}>
          {/* Examples tray toggle — always visible so the user can pull
              the prompt menu back up after a conversation has started. */}
          <button
            onClick={() => setExamplesOpen((v) => !v)}
            style={{
              display: "flex", alignItems: "center", width: "100%",
              padding: "6px 4px",
              background: "transparent", color: "var(--muted)",
              border: "none", cursor: "pointer",
              fontSize: 13, lineHeight: 1.4, textAlign: "left",
            }}>
            <span style={{
              display: "inline-block", width: 12, marginRight: 4,
              transform: examplesOpen ? "rotate(90deg)" : "rotate(0deg)",
              transition: "transform .1s ease",
            }}>›</span>
            <span>{t("dashboards.chat.examples")}</span>
          </button>
          {examplesOpen && (
          <>
            {GROUPS.map((g) => {
              const open = !!openGroups[g.key];
              return (
                <div key={g.key} style={{
                  border: "1px solid var(--border)",
                  borderRadius: 6, overflow: "hidden",
                  background: "var(--panel)",
                }}>
                  <button
                    onClick={() => toggleGroup(g.key)}
                    style={{
                      display: "flex", alignItems: "center", width: "100%",
                      padding: "8px 10px",
                      background: "transparent", color: "var(--fg)",
                      border: "none", cursor: "pointer",
                      fontSize: 14, fontWeight: 500, textAlign: "left",
                    }}
                  >
                    <span style={{
                      display: "inline-block", width: 14,
                      transition: "transform .1s ease",
                      transform: open ? "rotate(90deg)" : "rotate(0deg)",
                      marginRight: 4, color: "var(--muted)",
                    }}>›</span>
                    <span>{t(g.titleKey)}</span>
                    <span style={{ marginLeft: "auto", fontSize: 13,
                                     color: "var(--muted)" }}>
                      {g.promptKeys.length}
                    </span>
                  </button>
                  {open && (
                    <div style={{
                      display: "flex", flexDirection: "column", gap: 4,
                      padding: "0 6px 6px",
                    }}>
                      {g.promptKeys.map((k) => {
                        const text = t(k);
                        return (
                          <button key={k}
                                  onClick={() => pickPrompt(text)}
                                  disabled={busy}
                                  title={text}
                                  style={{
                                    display: "flex", alignItems: "center",
                                    gap: 8, width: "100%",
                                    textAlign: "left",
                                    padding: "8px 12px",
                                    background: "var(--panel-2)",
                                    color: "var(--fg)",
                                    border: "1px solid var(--border)",
                                    borderRadius: 999,
                                    fontSize: 14.5, cursor: "pointer",
                                    lineHeight: 1.4,
                                  }}
                                  onMouseEnter={(e) => {
                                    (e.currentTarget as HTMLButtonElement)
                                      .style.borderColor = "var(--info)";
                                  }}
                                  onMouseLeave={(e) => {
                                    (e.currentTarget as HTMLButtonElement)
                                      .style.borderColor = "var(--border)";
                                  }}>
                            <span style={{ flex: 1 }}>{text}</span>
                            <span style={{ color: "var(--muted)", fontSize: 14 }}>→</span>
                          </button>
                        );
                      })}
                    </div>
                  )}
                </div>
              );
            })}
          </>
          )}
        </div>
        {msgs.map((m, i) => (
          <div key={i} style={{
            alignSelf: m.role === "user" ? "flex-end" : "flex-start",
            maxWidth: "88%",
          }}>
            <div style={{
              fontSize: 13, color: "var(--muted)", marginBottom: 3,
              textAlign: m.role === "user" ? "right" : "left",
              opacity: 0.7,
            }}>
              {m.role === "user" ? t("dashboards.chat.role_user")
                : m.role === "system" ? t("dashboards.chat.role_system")
                : t("dashboards.chat.role_ai")}
            </div>
            <div style={{
              padding: "8px 12px",
              borderRadius: m.role === "user"
                ? "12px 12px 2px 12px"
                : "12px 12px 12px 2px",
              fontSize: 14, lineHeight: 1.55,
              whiteSpace: m.role === "assistant" ? "normal" : "pre-wrap",
              wordBreak: "break-word",
              background: m.role === "user" ? "var(--info)"
                : m.role === "system" ? "var(--warning-soft)"
                : "var(--panel-2)",
              color: m.role === "user" ? "var(--bg)" : "var(--fg)",
              border: m.role === "system" ? "1px solid var(--warning)" : "none",
            }}>
              {m.role === "assistant" ? (
                <MarkdownText text={m.text} />
              ) : (
                m.text
              )}
              {/* Tool-execution chip line — mirrors CopilotPanel: a
                  compact "·"-joined summary under the assistant reply. */}
              {m.role === "assistant" && m.tools && m.tools.length > 0 && (
                <div style={{
                  marginTop: m.text ? 6 : 0,
                  fontSize: 13,
                  color: "var(--muted)",
                }}>
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
      <div style={{
        padding: 10, borderTop: "1px solid var(--border)",
        display: "flex", flexDirection: "column", gap: 8,
        background: "var(--panel-2)",
      }}>
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
        {/* File chips */}
        {files.length > 0 && (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
            {files.map((f, idx) => (
              <span key={idx} style={{
                display: "inline-flex", alignItems: "center", gap: 4,
                fontSize: 13, padding: "2px 8px",
                border: "1px solid var(--border)", borderRadius: 12,
                background: "var(--panel)", color: "var(--fg)",
                maxWidth: 180, overflow: "hidden",
              }}>
                <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{f.name}</span>
                <button
                  onClick={() => setFiles((prev) => prev.filter((_, i) => i !== idx))}
                  style={{
                    border: "none", background: "transparent",
                    cursor: "pointer", padding: 0,
                    color: "var(--muted)", fontSize: 14, lineHeight: 1,
                    flexShrink: 0,
                  }}>×</button>
              </span>
            ))}
          </div>
        )}
        <textarea
          ref={inputRef}
          value={input}
          rows={3}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }}
          placeholder={t("dashboards.chat.placeholder")}
          disabled={busy}
          style={{
            width: "100%", padding: "8px 10px", fontSize: 14,
            border: "1px solid var(--border)", borderRadius: 6,
            background: "var(--panel)", color: "var(--fg)",
            boxSizing: "border-box", minHeight: 64, resize: "vertical",
            fontFamily: "inherit",
          }}
        />
        <div style={{
          display: "flex", justifyContent: "space-between", alignItems: "center",
          gap: 8,
        }}>
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <button
              onClick={() => fileRef.current?.click()}
              disabled={busy}
              title={t("dashboards.chat.attach")}
              style={{
                border: "1px solid var(--border)", borderRadius: 6,
                background: "transparent", cursor: busy ? "not-allowed" : "pointer",
                fontSize: 15, padding: "4px 8px", lineHeight: 1,
                color: "var(--muted)",
              }}>📎</button>
          </div>
          <button onClick={() => send()}
                  disabled={isDisabled}
                  style={{
                    padding: "6px 14px", fontSize: 14, fontWeight: 500,
                    background: isDisabled ? "var(--panel)" : "var(--info)",
                    color: isDisabled ? "var(--muted)" : "var(--fg-on-accent)",
                    border: "1px solid",
                    borderColor: isDisabled ? "var(--border)" : "var(--info)",
                    borderRadius: 6,
                    cursor: isDisabled ? "not-allowed" : "pointer",
                  }}>
            {busy ? "…" : t("dashboards.chat.send")}
          </button>
        </div>
      </div>
    </div>
  );
}

export default ChatPanel;
