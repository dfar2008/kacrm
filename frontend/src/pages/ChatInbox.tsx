import React, { useEffect, useRef, useState } from "react";
import {
  listAgentProfiles, listSessions, getSessionMessages,
  openChatStream, uploadChatAttachments, type ChatAttachment, AgentProfile, Session,
  archiveSession, unarchiveSession, deleteSession,
} from "@/lib/api";
import { useCurrentUser } from "@/lib/currentUser";
import { useRouter } from "@/lib/router";
import { toast } from "@/lib/toast";
import { renderMarkdown, parseSSEEvent } from "@/lib/markdown";
import { toolHeaderInfo, renderCrmProjection } from "@/lib/chatRender";
import { WeeklyAiReportCard } from "@/components/WeeklyAiReportCard";
import { ModelPicker } from "@/components/ModelPicker";
import { useT, type T } from "@/lib/i18n";
import { formatDuration } from "@/lib/formatDuration";

/** Per-channel brand glyphs used in the inbox sidebar and the detail-
 *  header. Each IM platform gets a distinct, recognizable mark at 16px
 *  instead of the previous one-bubble-fits-all. Unknown channels fall
 *  back to a neutral chat emoji. */
function ChannelGlyph({
  source, size = 16,
}: { source: string; size?: number }) {
  const s = source.toLowerCase();

  // Feishu / Lark — two-tone wing mark (lighter cyan front, darker navy
  // back), reflecting the actual brand logo rather than a generic bubble.
  if (s === "feishu" || s === "lark") {
    // theme-guard:ignore-start  (品牌/头像恒定色,不参与换肤)
    return (
      <svg width={size} height={size} viewBox="0 0 24 24" aria-label={s}>
        {/* Back wing — deep navy */}
        <path d="M13 4c4 2 6.5 6 7 12-3.5-.4-6.7-2.2-9-5l-2.2-2.7C10 6.6 11.3 5 13 4z"
              fill="#0B5FFF" />
        {/* Front wing — bright teal */}
        <path d="M3 9c5-1 9.5.5 13 4.2 2.2 2.3 3.6 5 4 6.8-5 .6-9.6-1-13.2-4.6C4.6 13.6 3.4 11.4 3 9z"
              fill="#33E1C4" />
      </svg>
    );
    // theme-guard:ignore-end
  }

  // Rounded-square "app icon" container with a white brand glyph inside.
  // Tuned to read clearly at 16px while staying visually unified.
  // theme-guard:ignore-start  (品牌/头像恒定色,不参与换肤)
  const TILE: Record<string, { color: string; glyph: React.ReactNode }> = {
    wechat: {
      color: "#07C160",
      glyph: (
        <g fill="#fff">
          {/* Two overlapping speech bubbles — the WeChat mark */}
          <ellipse cx="9.5" cy="10" rx="4.5" ry="3.4" />
          <path d="M5.5 13.2l-1.2 1.5.2-2 1 .5z" />
          <ellipse cx="15.5" cy="14" rx="3.8" ry="2.9" />
          <path d="M18.6 16.6l1 1.2-.2-1.6-.8.4z" />
          <circle cx="8" cy="9.6" r=".7" fill="#07C160" />
          <circle cx="11" cy="9.6" r=".7" fill="#07C160" />
          <circle cx="14.3" cy="13.7" r=".55" fill="#07C160" />
          <circle cx="16.7" cy="13.7" r=".55" fill="#07C160" />
        </g>
      ),
    },
    wecom: {
      color: "#2196F3",
      // Bold "W" — Work-Weixin / 企微
      glyph: (
        <path d="M5 8l2 8 2.5-5L12 16l2.5-5 2 5 2-8h-1.8l-1.1 4.6L13.2 8h-2.4l-2.4 4.6L7.3 8H5z"
              fill="#fff" />
      ),
    },
    dingtalk: {
      color: "#1677FF",
      // Stylised "D" with a wedge — nods to 钉钉's wordmark
      glyph: (
        <path d="M7 6h5.5c3 0 5.5 2.6 5.5 6s-2.5 6-5.5 6H7V6zm2.2 2.2v7.6h3c1.9 0 3.4-1.7 3.4-3.8s-1.5-3.8-3.4-3.8h-3z"
              fill="#fff" />
      ),
    },
    slack: {
      color: "#4A154B",
      // Hash mark — Slack's channel sigil, single-tone for 16px legibility
      glyph: (
        <path d="M9.2 5l-.6 3h2.8l.6-3h2l-.6 3h2.6v2h-3l-.6 3h3v2h-3.4l-.6 3h-2l.6-3H6.8l-.6 3h-2l.6-3H2.2v-2h3l.6-3H2.8V8h3.4l.6-3h2.4zm-.4 5l-.6 3h2.8l.6-3H8.8z"
              fill="#fff" />
      ),
    },
    telegram: {
      color: "#229ED9",
      // Paper plane — Telegram's mark
      glyph: (
        <path d="M3 12l16-6.5-2.5 13.5-5.5-3-2.5 3v-4.2L17 7 7 13l-4-1z"
              fill="#fff" />
      ),
    },
    discord: {
      color: "#5865F2",
      // Discord's controller/ghost silhouette, simplified
      glyph: (
        <path d="M7 7c1.5-.6 3.2-1 5-1s3.5.4 5 1c1.4 1.8 2.2 4.4 2 7-1 .8-2.2 1.4-3.5 1.7l-.7-1.3c.7-.2 1.4-.5 2-.9-.2-.1-.3-.3-.5-.4-2.8 1.3-5.8 1.3-8.6 0-.2.1-.3.3-.5.4.6.4 1.3.7 2 .9l-.7 1.3C6.2 15.4 5 14.8 4 14c-.2-2.6.6-5.2 2-7zm2.5 4.4c-.7 0-1.3.7-1.3 1.5s.6 1.5 1.3 1.5 1.3-.7 1.3-1.5-.6-1.5-1.3-1.5zm5 0c-.7 0-1.3.7-1.3 1.5s.6 1.5 1.3 1.5 1.3-.7 1.3-1.5-.6-1.5-1.3-1.5z"
              fill="#fff" />
      ),
    },
    email: {
      color: "#6A7280",
      // Envelope
      glyph: (
        <g fill="none" stroke="#fff" strokeWidth="1.6" strokeLinejoin="round">
          <rect x="4" y="7" width="16" height="11" rx="1.2" />
          <path d="M4.5 8l7.5 6 7.5-6" />
        </g>
      ),
    },
    sms: {
      color: "#2DD4BF",
      // Bubble with three dots — generic SMS
      glyph: (
        <g fill="#fff">
          <path d="M5 6h14a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2h-6.6l-3.4 3a.6.6 0 0 1-1-.45V17H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2z" />
          <circle cx="9" cy="11.5" r="1.1" fill="#2DD4BF" />
          <circle cx="12" cy="11.5" r="1.1" fill="#2DD4BF" />
          <circle cx="15" cy="11.5" r="1.1" fill="#2DD4BF" />
        </g>
      ),
    },
  };

  // theme-guard:ignore-end
  const tile = TILE[s];
  if (!tile) {
    return <span style={{ fontSize: size - 2, opacity: 0.8 }}>💬</span>;
  }
  // Email / SMS already include their own envelope/bubble shape; render
  // the glyph straight without a colored tile so they keep their utility
  // look. Everything else gets a rounded brand-color app-icon tile.
  if (s === "email" || s === "sms") {
    return (
      <svg width={size} height={size} viewBox="0 0 24 24" aria-label={s}>
        {tile.glyph}
      </svg>
    );
  }
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-label={s}>
      <rect x="2" y="2" width="20" height="20" rx="4.5" fill={tile.color} />
      {tile.glyph}
    </svg>
  );
}

/** Compact relative timestamp for the inbox list — takes ``t`` so copy
 * tracks the active locale (just now / N min ago / HH:mm / Yesterday /
 * M/D). Declared as a plain function rather than a hook so it can be
 * called inside render loops without violating rules-of-hooks. */
function relativeTime(t: T, iso?: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  const now = new Date();
  const diff = now.getTime() - d.getTime();
  if (diff < 60000) return t("chat.rel.now");
  if (diff < 3600000) return t("chat.rel.min_ago", { n: Math.floor(diff / 60000) });
  const sameDay = d.toDateString() === now.toDateString();
  if (sameDay) return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  const yd = new Date(now); yd.setDate(now.getDate() - 1);
  if (d.toDateString() === yd.toDateString()) return t("chat.rel.yesterday");
  return t("chat.rel.month_day", { m: d.getMonth() + 1, d: d.getDate() });
}

function startOfLocalDay(v: string | Date | undefined): number {
  if (!v) return 0;
  const d = v instanceof Date ? new Date(v) : new Date(v);
  d.setHours(0, 0, 0, 0);
  return d.getTime();
}

/** Bucket sessions into today/yesterday/2-days/earlier by local-calendar day. */
function groupSessionsByDay(t: T, sessions: Session[]): Array<{ label: string; items: Session[] }> {
  const todayStart = startOfLocalDay(new Date());
  const today = t("chat.group.today");
  const yesterday = t("chat.group.yesterday");
  const dayBefore = t("chat.group.day_before");
  const earlier = t("chat.group.earlier");
  const buckets: Record<string, Session[]> = {
    [today]: [], [yesterday]: [], [dayBefore]: [], [earlier]: [],
  };
  for (const s of sessions) {
    const dayStart = startOfLocalDay(s.updated_at);
    const diffDays = Math.round((todayStart - dayStart) / 86400000);
    if (diffDays <= 0) buckets[today].push(s);
    else if (diffDays === 1) buckets[yesterday].push(s);
    else if (diffDays === 2) buckets[dayBefore].push(s);
    else buckets[earlier].push(s);
  }
  return [today, yesterday, dayBefore, earlier]
    .filter(k => buckets[k].length > 0)
    .map(k => ({ label: k, items: buckets[k] }));
}

// Deterministic pastel avatar color based on id hash (matches app.js:2379).
// theme-guard:ignore-start  (品牌/头像恒定色,不参与换肤)
const AVATAR_PALETTE = [
  "#fde68a", "#fecaca", "#bfdbfe", "#bbf7d0",
  "#ddd6fe", "#fbcfe8", "#fcd34d", "#a7f3d0",
];
// theme-guard:ignore-end
function agentAvatarColor(id: string): string {
  let h = 0;
  for (let i = 0; i < (id || "").length; i++) h = (h * 31 + id.charCodeAt(i)) & 0xffff;
  return AVATAR_PALETTE[h % AVATAR_PALETTE.length];
}
function agentInitial(name: string): string {
  const s = (name || "").trim();
  return s ? s.charAt(0).toUpperCase() : "?";
}

function looksLikeInternalJudgeText(raw: string): boolean {
  const text = (raw || "").trim();
  if (!text) return false;
  if (/^USER_PROMPT:\s*/.test(text) && /ASSISTANT_OUTPUT:\s*/m.test(text)) return true;
  if (!(text.startsWith("{") && text.endsWith("}"))) return false;
  try {
    const obj = JSON.parse(text);
    if (!obj || typeof obj !== "object" || Array.isArray(obj)) return false;
    const hasScore = typeof (obj as any).score === "number";
    const hasReason = typeof (obj as any).reason === "string";
    if (!hasScore || !hasReason) return false;
    const keys = Object.keys(obj);
    return keys.length <= 4;
  } catch {
    return false;
  }
}

function extractLegacyAssistantOutput(raw: string): string | null {
  const text = (raw || "").trim();
  if (!text) return null;
  const match = text.match(
    /(?:^|\n)USER_PROMPT:\s*[\s\S]*?(?:^|\n)ASSISTANT_OUTPUT:\s*([\s\S]*)$/m,
  );
  if (!match) return null;
  const out = (match[1] || "").trim();
  return out || null;
}

/** Turn historical message list into the `{role, segments}` shape live
 * streaming builds. See git history for the full decomposition commentary. */
function normaliseHistory(rows: any[]): Msg[] {
  const out: Msg[] = [];
  let toolSeq = 0;
  const toolCallIndex = new Map<string, { host: Msg; segIdx: number }>();

  const parseJson = (raw: any): any => {
    if (raw == null) return null;
    if (typeof raw !== "string") return raw;
    try { return JSON.parse(raw); } catch { return raw; }
  };
  const parseArgs = (a: any): any => {
    if (a == null) return undefined;
    if (typeof a === "string") { try { return JSON.parse(a); } catch { return a; } }
    return a;
  };

  for (const m of rows) {
    if (!m || m.role === "session_meta") continue;

    if (m.role === "tool") {
      const ref = m.tool_call_id ? toolCallIndex.get(m.tool_call_id) : null;
      const output = parseJson(m.content);
      if (ref) {
        const seg = ref.host.segments[ref.segIdx];
        if (seg && seg.kind === "tool" && seg.tool) {
          seg.tool.output = output;
          seg.tool.status = "done";
          seg.tool.durationMs = m.duration_ms ?? null;
        }
      } else {
        let host: Msg | null = null;
        for (let i = out.length - 1; i >= 0; i--) {
          if (out[i].role === "assistant") { host = out[i]; break; }
        }
        if (!host) { host = { role: "assistant", segments: [] }; out.push(host); }
        host.segments.push({
          kind: "tool",
          tool: {
            id: `hist-${toolSeq++}`, tool: "tool", status: "done", output,
            durationMs: m.duration_ms ?? null,
          },
        });
      }
      continue;
    }

    const role: Msg["role"] = m.role === "assistant" ? "assistant" : "user";
    const segments: Segment[] = [];

    // Do not surface provider reasoning to end users in the chat timeline.
    // Keep the UI focused on final answers + tool results.

    if (typeof m.content === "string") {
      let text = m.content;
      if (m.role === "assistant") {
        const extracted = extractLegacyAssistantOutput(text);
        if (extracted) text = extracted;
        if (looksLikeInternalJudgeText(text)) continue;
      }
      if (text) segments.push({ kind: "text", text });
    } else if (Array.isArray(m.content)) {
      let joinedText = "";
      for (const part of m.content) {
        if (!part) continue;
        const kind = part.type || part.kind;
        if (kind === "text" && (part.text || part.content)) {
          const txt = String(part.text || part.content);
          joinedText += (joinedText ? "\n" : "") + txt;
          segments.push({ kind: "text", text: txt });
        }
      }
      if (m.role === "assistant") {
        const extracted = extractLegacyAssistantOutput(joinedText);
        if (extracted) {
          segments.length = 0;
          segments.push({ kind: "text", text: extracted });
        }
        if (looksLikeInternalJudgeText((segments[0]?.text || joinedText))) continue;
      }
    }

    const hasToolCalls = Array.isArray(m.tool_calls) && m.tool_calls.length > 0;
    if (segments.length === 0 && !hasToolCalls) continue;

    const msg: Msg = {
      role,
      segments,
      ts: m.ts,
      durationMs: role === "assistant" ? (m.duration_ms ?? null) : undefined,
      turnMeta: role === "assistant" ? (m.turn_meta ?? null) : undefined,
    };
    out.push(msg);

    if (Array.isArray(m.tool_calls)) {
      for (const tc of m.tool_calls) {
        if (!tc) continue;
        const id = tc.id || tc.call_id || `hist-${toolSeq++}`;
        const name = tc.function?.name || tc.name || "tool";
        const args = parseArgs(tc.function?.arguments ?? tc.arguments ?? tc.input);
        const segIdx = msg.segments.length;
        msg.segments.push({
          kind: "tool",
          tool: {
            id, tool: name, status: "done", input: args,
          },
        });
        toolCallIndex.set(id, { host: msg, segIdx });
      }
    }
  }
  return out;
}
function makeClientConversationId(): string {
  return `web-${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;
}

interface ToolCall {
  id: string;
  tool: string;
  status: "running" | "done" | "error";
  input?: any;
  output?: any;
  error?: string;
  durationMs?: number | null;
}
interface Segment {
  kind: "text" | "reasoning" | "tool";
  text?: string;
  tool?: ToolCall;
}
interface TurnMeta {
  total_ms: number;
  queued_ms?: number | null;
  api_calls: number;
  tool_count: number;
}
interface Msg {
  role: "user" | "assistant";
  segments: Segment[];
  ts?: number;
  durationMs?: number | null;
  turnMeta?: TurnMeta | null;
}

export function ChatInboxPage() {
  const t = useT();
  const { navigate } = useRouter();
  const { user: me } = useCurrentUser();
  const [profiles, setProfiles] = useState<AgentProfile[]>([]);
  const [profileId, setProfileId] = useState<string | null>(null);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [files, setFiles] = useState<File[]>([]);   // 待发送的附件(图片/文档)
  const attachInputRef = useRef<HTMLInputElement | null>(null);
  const [streaming, setStreaming] = useState(false);
  const [model, setModel] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  // Active vs archived view. "Archived" is reachable via the funnel
  // popover in the search row — the user can flip back from the same
  // popover. 1 user = 1 profile, so the legacy two-tab toggle would
  // just waste a row.
  const [view, setView] = useState<"active" | "archived">("active");
  const [filterOpen, setFilterOpen] = useState(false);
  const [menuOpenId, setMenuOpenId] = useState<string | null>(null);
  const [pendingNewId, setPendingNewId] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  // Synchronous re-entry guard. The `streaming` state guard below is async
  // (React batches setStreaming), so two near-simultaneous send() calls can
  // both pass it and open two parallel conversations. A ref flips
  // immediately, so the second call is dropped no matter the trigger.
  const sendingRef = useRef(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const filterRef = useRef<HTMLDivElement>(null);
  // Skip the next history-fetch when activeId changed because *we*
  // (send()) just learned the server-assigned conversation id mid-
  // stream. Otherwise the fetch races the SSE deltas: the server may
  // not have persisted the assistant row yet, returns [user] only,
  // SPA replaces local state, then patchLast streams text onto the
  // user message — first user "hi" bubble visibly disappears.
  const skipNextHistoryFetchRef = useRef(false);

  useEffect(() => {
    if (!filterOpen) return;
    function onDocClick(e: MouseEvent) {
      if (filterRef.current && !filterRef.current.contains(e.target as Node)) {
        setFilterOpen(false);
      }
    }
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, [filterOpen]);

  // Poll profile process status every 30s so the sidebar status dot
  // tracks the subprocess crashing / coming back without forcing a
  // page reload. Cheap call — single /api/profiles fetch per tick.
  useEffect(() => {
    const tick = () => {
      listAgentProfiles()
        .then(r => setProfiles(r.profiles || []))
        .catch(() => { /* keep stale state, no toast spam */ });
    };
    const id = window.setInterval(tick, 30000);
    return () => window.clearInterval(id);
  }, []);

  useEffect(() => {
    listAgentProfiles().then(r => {
      setProfiles(r.profiles || []);
      const def = r.profiles?.find(p => p.is_default) || r.profiles?.[0];
      if (def) setProfileId(def.id);
    }).catch(e => setErr(e.message));
  }, []);

  // Reload sessions whenever the profile or the active tab changes.
  // ``view === "archived"`` flips ``include_hidden=1`` so the sidebar
  // surfaces archived rows (which are filtered out by default).
  function reloadSessions() {
    if (!profileId) return;
    // Don't pass profileId — 1 user = 1 profile means user_id alone is
    // the right scope, and dropping the filter brings back sessions
    // from Feishu/WeChat channels that landed under a different
    // profile binding.
    listSessions({ includeHidden: view === "archived" })
      .then(r => {
        const all = r.data || [];
        // Active view: drop archived. Archived view: keep only archived.
        const filtered = view === "archived"
          ? all.filter(s => !!s.hidden_at)
          : all.filter(s => !s.hidden_at);
        setSessions(filtered);
      })
      .catch(e => setErr(e.message));
  }
  useEffect(() => {
    reloadSessions();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [profileId, view]);

  useEffect(() => {
    if (!activeId || !profileId) { setMessages([]); return; }
    if (skipNextHistoryFetchRef.current) {
      // send() just promoted a pending new conversation to its
      // server-assigned id; the local [user, asst-streaming] state is
      // the source of truth right now — refetching would clobber it.
      skipNextHistoryFetchRef.current = false;
      return;
    }
    getSessionMessages(activeId, { profileId })
      .then(r => setMessages(normaliseHistory(r.data || [])))
      .catch(e => setErr(e.message));
  }, [activeId, profileId]);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages]);

  const activeProfile = profiles.find(p => p.id === profileId);
  const filteredSessions = sessions.filter(s =>
    !search || (s.title || "").toLowerCase().includes(search.toLowerCase())
    || (s.preview || "").toLowerCase().includes(search.toLowerCase()));

  async function onArchive(sid: string) {
    setMenuOpenId(null);
    if (!profileId) return;
    try {
      await archiveSession(sid, { profileId });
      toast(t("chat.session.archived_toast"), "success");
      if (activeId === sid) { setActiveId(null); setMessages([]); }
      reloadSessions();
    } catch (e: any) {
      toast(e?.message || t("chat.session.action_failed"), "error");
    }
  }

  async function onUnarchive(sid: string) {
    setMenuOpenId(null);
    if (!profileId) return;
    try {
      await unarchiveSession(sid, { profileId });
      toast(t("chat.session.unarchived_toast"), "success");
      reloadSessions();
    } catch (e: any) {
      toast(e?.message || t("chat.session.action_failed"), "error");
    }
  }

  async function onDelete(sid: string) {
    setMenuOpenId(null);
    if (!profileId) return;
    if (!window.confirm(t("chat.session.delete_confirm"))) return;
    try {
      await deleteSession(sid, { profileId });
      toast(t("chat.session.deleted_toast"), "success");
      if (activeId === sid) { setActiveId(null); setMessages([]); }
      reloadSessions();
    } catch (e: any) {
      toast(e?.message || t("chat.session.action_failed"), "error");
    }
  }

  function startNewConversation() {
    setActiveId(null);
    setPendingNewId(makeClientConversationId());
    setMessages([]);
    setErr(null);
  }

  // ③b「存为技能」——把「刚才这套流程」蒸馏成可复用技能。
  // 往当前会话发一条 ``/learn`` 指令走与普通消息相同的流式路径:后端
  // (main.py chat_completions)识别到这一 turn 是 ``/learn`` 触发,会把它
  // 改写成遵循 Hermes 技能撰写规范的指令,agent 在**同一 session**看到之前
  // 的完整对话,提炼出 SKILL.md 并用 ``skill_manage`` 落盘到该 profile 的
  // 技能库。需要至少有一轮已完成的对话才有东西可提炼。
  function learnSkill() {
    if (streaming || sendingRef.current || !profileId || !model) return;
    // 至少要有一条 assistant 回复(=走过一套流程)才提炼,否则没内容。
    const hasFlow = messages.some(m => m.role === "assistant" && m.segments.length > 0);
    if (!hasFlow) {
      setErr(t("chat.learn.need_flow"));
      return;
    }
    void send({ overrideText: "/learn", displayText: t("chat.learn.sent") });
  }

  async function send(opts?: { overrideText?: string; displayText?: string }) {
    if (sendingRef.current) return;
    const override = opts?.overrideText;
    if (override === undefined
        && (!input.trim() && files.length === 0)) return;
    if (streaming || !profileId) return;
    sendingRef.current = true;
    // 「存为技能」路径:用固定指令、不带附件、不动输入框。
    const sentFiles = override !== undefined ? [] : files;
    // 只传附件没打字时,给个默认指令让 agent 据附件录单。
    const text = override !== undefined
      ? override
      : (input.trim() || "请根据我上传的附件(发票/订单等)抽取信息,录入成对应的 CRM 记录。");
    if (override === undefined) {
      setInput("");
      setFiles([]);
    }
    setErr(null);

    // 界面上显示的用户气泡文本。「存为技能」路径发给后端的是裸 ``/learn``,
    // 但气泡显示友好文案(opts.displayText),避免用户看到生硬的斜杠命令。
    const displayText = opts?.displayText ?? (sentFiles.length
      ? `${text}\n📎 ${sentFiles.map(f => f.name).join("、")}`
      : text);
    const userMsg: Msg = { role: "user", segments: [{ kind: "text", text: displayText }] };
    const asstMsg: Msg = { role: "assistant", segments: [] };
    setMessages(prev => [...prev, userMsg, asstMsg]);
    setStreaming(true);

    const ctrl = new AbortController();
    abortRef.current = ctrl;

    let toolSeq = 0;
    const toolMap = new Map<string, string>();

    // ── <think> stream-side filter ───────────────────────────────────
    // Some providers emit reasoning blocks (``<think>...</think>``,
    // ``<thinking>...</thinking>``, ``<reasoning>...</reasoning>``,
    // ``<thought>...</thought>``, ``<REASONING_SCRATCHPAD>...``) inline
    // in ``delta.content`` instead of as separate ``delta.type=reasoning``
    // events. Hermes upstream's ``_strip_think_blocks`` removes them
    // before persisting to chat history, so historical messages render
    // clean — but the streaming path saw raw text until now. Mirror that
    // strip across SSE chunks: text outside think → ``appendText``,
    // text inside → ``appendReasoning`` (rendered ``return null``, hidden).
    //
    // State spans the whole stream. ``pendingTag`` buffers a partial tag
    // straddling chunk boundaries (e.g. "<thi" + "nk>...</think>").
    let inThink = false;
    let pendingTag = "";
    const THINK_NAMES = ["think", "thinking", "reasoning", "thought", "REASONING_SCRATCHPAD"];
    const OPEN_RE = new RegExp(`<(?:${THINK_NAMES.join("|")})\\b[^>]*>`, "i");
    const CLOSE_RE = new RegExp(`</(?:${THINK_NAMES.join("|")})>`, "i");
    // Max characters of a partial open tag we keep buffered. Longest
    // tag name is 21 ("REASONING_SCRATCHPAD"), plus "<", attrs and ">",
    // safely covered by 32.
    const MAX_PARTIAL = 32;

    function feedDelta(delta: string) {
      let buf = pendingTag + delta;
      pendingTag = "";
      while (buf.length > 0) {
        if (inThink) {
          const closeM = buf.match(CLOSE_RE);
          if (!closeM) {
            // No close in this chunk — emit everything as reasoning,
            // except keep up to MAX_PARTIAL trailing chars in case the
            // close tag is straddling.
            const keep = Math.min(MAX_PARTIAL, buf.length);
            const emit = buf.slice(0, buf.length - keep);
            if (emit) appendReasoning(emit);
            // Only keep the trailing chunk if it could be the start of
            // a close tag ("</thi" etc.); otherwise flush.
            if (buf.slice(-keep).includes("<")) {
              pendingTag = buf.slice(-keep);
            } else {
              if (buf.slice(-keep)) appendReasoning(buf.slice(-keep));
            }
            return;
          }
          const before = buf.slice(0, closeM.index!);
          if (before) appendReasoning(before);
          inThink = false;
          buf = buf.slice(closeM.index! + closeM[0].length);
        } else {
          const openM = buf.match(OPEN_RE);
          if (!openM) {
            // No open in this chunk — emit as text, but keep trailing
            // partial-tag chars in case "<think>" is straddling.
            const lastLt = buf.lastIndexOf("<");
            if (lastLt >= 0 && buf.length - lastLt <= MAX_PARTIAL) {
              const emit = buf.slice(0, lastLt);
              if (emit) appendText(emit);
              pendingTag = buf.slice(lastLt);
            } else {
              appendText(buf);
            }
            return;
          }
          const before = buf.slice(0, openM.index!);
          if (before) appendText(before);
          inThink = true;
          buf = buf.slice(openM.index! + openM[0].length);
        }
      }
    }

    function patchLast(mutator: (m: Msg) => Msg) {
      setMessages(prev => {
        const next = [...prev];
        const last = next[next.length - 1];
        if (!last) {
          // New conversation switches can briefly clear message state while
          // SSE deltas are still arriving. Seed an assistant placeholder so
          // stream patches always have a safe target.
          next.push(mutator({ role: "assistant", segments: [] }));
          return next;
        }
        next[next.length - 1] = mutator(last);
        return next;
      });
    }

    function appendText(delta: string) {
      patchLast(m => {
        const segs = [...m.segments];
        const last = segs[segs.length - 1];
        if (last && last.kind === "text") {
          segs[segs.length - 1] = { ...last, text: (last.text || "") + delta };
        } else {
          segs.push({ kind: "text", text: delta });
        }
        return { ...m, segments: segs };
      });
    }
    function appendReasoning(delta: string) {
      patchLast(m => {
        const segs = [...m.segments];
        const last = segs[segs.length - 1];
        if (last && last.kind === "reasoning") {
          segs[segs.length - 1] = { ...last, text: (last.text || "") + delta };
        } else {
          segs.push({ kind: "reasoning", text: delta });
        }
        return { ...m, segments: segs };
      });
    }
    function addTool(call: ToolCall) {
      patchLast(m => ({ ...m, segments: [...m.segments, { kind: "tool", tool: call }] }));
    }
    function updateTool(id: string, patch: Partial<ToolCall>) {
      patchLast(m => ({
        ...m,
        segments: m.segments.map(s =>
          s.kind === "tool" && s.tool?.id === id
            ? { ...s, tool: { ...s.tool!, ...patch } }
            : s,
        ),
      }));
    }
    // Stream end-of-turn fallback: a tool can stay stuck on "running" if the
    // backend never emits its completion progress event (or the names don't
    // line up). When the turn finishes the tool is by definition done, so flip
    // any still-running tool to done — otherwise the live view shows a
    // perpetual "执行中" that only resolves on reload (history renders done).
    function finalizeRunningTools() {
      // Patch EVERY message, not just the last — a multi-step agent run
      // spreads tool calls across several assistant messages, and patchLast
      // would leave earlier tools (e.g. an opening crm_list_modules) stuck on
      // "running" forever.
      setMessages(prev => prev.map(m => ({
        ...m,
        segments: m.segments.map(s =>
          s.kind === "tool" && s.tool?.status === "running"
            ? { ...s, tool: { ...s.tool!, status: "done" } }
            : s,
        ),
      })));
    }

    try {
      const outgoingId = activeId || pendingNewId;
      // 上传附件 → 图片转 data_url、文档抽文本 → 随消息发给 agent。
      let attachments: ChatAttachment[] = [];
      if (sentFiles.length) {
        attachments = await uploadChatAttachments(sentFiles, outgoingId);
      }
      const resp = await openChatStream(text, {
        profileId,
        signal: ctrl.signal,
        conversationId: outgoingId,
        model,
        attachments,
      });

      const newId = resp.headers.get("X-Conversation-Id") || outgoingId;
      if (newId && !activeId) {
        // Tell the activeId useEffect to skip its next history fetch —
        // we have the live local state and don't want to race the SSE.
        skipNextHistoryFetchRef.current = true;
        setActiveId(newId);
        setPendingNewId(null);
        setSessions(prev => [{
          id: newId,
          title: text.slice(0, 60),
          source: "web",
          updated_at: new Date().toISOString(),
        }, ...prev]);
      }

      const reader = resp.body!.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      let currentEvent = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const lines = buf.split("\n");
        buf = lines.pop() || "";
        for (const line of lines) {
          if (line.startsWith("event:")) { currentEvent = line.slice(6).trim(); continue; }
          if (!line.startsWith("data:")) { currentEvent = ""; continue; }
          const dataStr = line.slice(5).trim();
          const parsed = parseSSEEvent(currentEvent, dataStr);
          currentEvent = "";
          if (!parsed) continue;
          if (parsed.type === "done") break;
          if (parsed.type === "delta") feedDelta(parsed.content);
          else if (parsed.type === "reasoning") {
            // 2026-07-07 field verdict: raw chain-of-thought is not
            // user-grade content (half-English internal monologue). The
            // SSE event still arrives (gateway keeps emitting — plumbing
            // for a future better-reasoning model); the UI shows a live
            // ticking "思考中 · ns" placeholder instead of the content.
          }
          else if (parsed.type === "tool_progress") {
            if (parsed.status === "running") {
              toolSeq++;
              const id = `${parsed.tool}-${toolSeq}`;
              toolMap.set(parsed.tool, id);
              addTool({ id, tool: parsed.tool, status: "running", input: parsed.input });
            } else {
              const id = toolMap.get(parsed.tool);
              if (id) updateTool(id, {
                status: parsed.status,
                output: parsed.output,
                error: parsed.error,
                durationMs: parsed.durationMs,
              });
            }
          }
          else if (parsed.type === "turn_done") {
            patchLast(m => ({ ...m, turnMeta: parsed.turnMeta }));
          }
        }
      }
    } catch (e: any) {
      if (e.name !== "AbortError") setErr(e.message);
    } finally {
      // Flush trailing partial-tag buffer. If we were inside a think
      // block at stream end (degenerate — no closing tag), drop the
      // tail entirely (mirrors hermes' "unterminated open" rule).
      if (pendingTag && !inThink) {
        appendText(pendingTag);
      }
      pendingTag = "";
      if (!ctrl.signal.aborted) finalizeRunningTools();
      setStreaming(false);
      abortRef.current = null;
      sendingRef.current = false;
    }
  }

  function abort() {
    abortRef.current?.abort();
  }

  return (
    <div style={{
      display: "grid",
      gridTemplateColumns: "280px 1fr",
      height: "100%",
      overflow: "hidden",
    }}>
      {/* ── Sidebar ─────────────────── */}
      <div style={{
        display: "flex", flexDirection: "column",
        borderRight: "1px solid var(--border)",
        overflow: "hidden",
      }}>
        {/* Profile status bar — replaces the legacy profile picker.
            1 user = 1 profile, so there's nothing to pick; instead we
            show whether the user's hermes subprocess is alive
            (green) or not (red), and the row clicks through to the
            profile settings page. */}
        {(() => {
          const running = !!activeProfile?.process?.running;
          const slug = me?.username || "";
          return (
            <div
              role="button"
              tabIndex={0}
              onClick={() => { if (slug) navigate(`/agents/profiles/${encodeURIComponent(slug)}`); }}
              onKeyDown={e => {
                if ((e.key === "Enter" || e.key === " ") && slug) {
                  e.preventDefault();
                  navigate(`/agents/profiles/${encodeURIComponent(slug)}`);
                }
              }}
              title={t(running ? "chat.sidebar.profile_running" : "chat.sidebar.profile_stopped")}
              style={{
                display: "flex", alignItems: "center", gap: 8,
                padding: "10px 12px",
                borderBottom: "1px solid var(--border)",
                cursor: slug ? "pointer" : "default",
              }}>
              {activeProfile && (
                <span style={{
                  width: 24, height: 24, borderRadius: "50%",
                  background: agentAvatarColor(activeProfile.id),
                  color: "var(--panel-2)",
                  display: "inline-flex", alignItems: "center", justifyContent: "center",
                  fontWeight: 700, fontSize: 14, flexShrink: 0,
                }}>{agentInitial(activeProfile.id)}</span>
              )}
              <span style={{
                flex: 1, overflow: "hidden",
                textOverflow: "ellipsis", whiteSpace: "nowrap",
                fontSize: 14,
              }}>
                {activeProfile ? activeProfile.id : t("common.loading")}
              </span>
              <span
                aria-label={t(running ? "chat.sidebar.profile_running" : "chat.sidebar.profile_stopped")}
                style={{
                  width: 8, height: 8, borderRadius: "50%",
                  background: running ? "var(--success)" : "var(--danger)",
                  boxShadow: running
                    ? `0 0 0 2px var(--success-soft)`
                    : `0 0 0 2px var(--danger-soft)`,
                  flexShrink: 0,
                }} />
            </div>
          );
        })()}
        {/* Search + filter funnel + new-conversation. Replaces the
            standalone "聊天 / 已归档" tab row — the funnel popover
            switches between active and archived views. */}
        <div style={{
          display: "flex", alignItems: "center", gap: 6,
          padding: "10px 12px",
        }}>
          <input placeholder={t("chat.sidebar.search_placeholder")}
                 value={search}
                 onChange={e => setSearch(e.target.value)}
                 style={{ flex: 1, minWidth: 0 }} />
          <div ref={filterRef} style={{ position: "relative" }}>
            <button
              type="button"
              className="ghost"
              onClick={() => setFilterOpen(o => !o)}
              title={t("chat.sidebar.filter_tooltip")}
              style={{
                display: "inline-flex", alignItems: "center",
                padding: "4px 8px",
                color: view === "archived" ? "var(--accent)" : undefined,
              }}>
              {/* funnel — Lucide stroke family. */}
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
                   stroke="currentColor" strokeWidth="2"
                   strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M22 3H2l8 9.46V19l4 2v-8.54L22 3z" />
              </svg>
            </button>
            {filterOpen && (
              <div style={{
                position: "absolute", top: "calc(100% + 4px)", right: 0,
                background: "var(--popover-bg)",
                border: "1px solid var(--border)",
                borderRadius: 8,
                boxShadow: "var(--shadow-modal)",
                zIndex: 100, minWidth: 160,
                padding: 4,
              }}>
                <div
                  onClick={() => { setView("active"); setFilterOpen(false); }}
                  style={{
                    padding: "8px 10px", cursor: "pointer", fontSize: 14,
                    borderRadius: 6,
                    background: view === "active" ? "var(--accent-soft)" : "transparent",
                  }}>
                  {t("chat.sidebar.inbox")}
                  {view === "active" && <span style={{ marginLeft: 8, color: "var(--accent)" }}>✓</span>}
                </div>
                <div
                  onClick={() => { setView("archived"); setFilterOpen(false); }}
                  style={{
                    padding: "8px 10px", cursor: "pointer", fontSize: 14,
                    borderRadius: 6,
                    background: view === "archived" ? "var(--accent-soft)" : "transparent",
                  }}>
                  {t("chat.sidebar.archived")}
                  {view === "archived" && <span style={{ marginLeft: 8, color: "var(--accent)" }}>✓</span>}
                </div>
              </div>
            )}
          </div>
          <button className="ghost" onClick={startNewConversation}
                  title={t("chat.sidebar.new_tooltip")}
                  style={{ display: "inline-flex", alignItems: "center", padding: "4px 8px" }}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
                 stroke="currentColor" strokeWidth="2"
                 strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M12 20h9" />
              <path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z" />
            </svg>
          </button>
        </div>
        <div className="conv-list-scroll">
          {groupSessionsByDay(t, filteredSessions).map(({ label, items }) => (
            <div key={label}>
              <div className="conv-group-label">{label}</div>
              {items.map(s => {
                const src = s.source || s.platform || "web";
                const active = s.id === activeId;
                const archived = view === "archived";
                return (
                  <div key={s.id}
                       onClick={() => setActiveId(s.id)}
                       className={"conv-item-new" + (active ? " active" : "")}
                       style={{ position: "relative" }}>
                    <div className="conv-item-row">
                      <span className="conv-platform-badge" style={{ display: "inline-flex", alignItems: "center" }}>
                        <ChannelGlyph source={src} size={16} />
                      </span>
                      <span className="conv-item-title">{s.title || s.id.slice(0, 20)}</span>
                      <span className="conv-item-time">{relativeTime(t, s.updated_at)}</span>
                    </div>
                    {s.preview && (
                      <div className="conv-item-preview">{s.preview}</div>
                    )}
                    <SessionRowMenu
                      open={menuOpenId === s.id}
                      onToggle={() => setMenuOpenId(menuOpenId === s.id ? null : s.id)}
                      onClose={() => setMenuOpenId(null)}
                      archived={archived}
                      onArchive={() => onArchive(s.id)}
                      onUnarchive={() => onUnarchive(s.id)}
                      onDelete={() => onDelete(s.id)}
                      t={t}
                    />
                  </div>
                );
              })}
            </div>
          ))}
          {filteredSessions.length === 0 && (
            <div className="muted" style={{ padding: 14, fontSize: 14 }}>
              {view === "archived"
                ? t("chat.sidebar.empty_archived")
                : t("chat.sidebar.empty")}
            </div>
          )}
        </div>
      </div>

      {/* ── Chat Area ─────────────────── */}
      <div style={{ display: "flex", flexDirection: "column", overflow: "hidden" }}>
        <ChatAreaHeader
          session={sessions.find(s => s.id === activeId) || null}
          profile={activeProfile || null}
        />

        <div ref={scrollRef} className="chat-scroll">
          {err && <div className="alert alert-error" style={{ marginBottom: 10 }}>{err}</div>}
          {messages.length === 0 && (
            <>
              <WeeklyAiReportCard />
              <div className="chat-empty-state">
                {t("chat.area.empty")}
              </div>
            </>
          )}
          {messages.map((m, i) => (
            <MessageBubble
              key={i}
              msg={m}
              agentName={activeProfile?.id}
              showThinking={streaming && i === messages.length - 1 && m.role === "assistant"}
            />
          ))}
        </div>

        <div style={{
          borderTop: "1px solid var(--border)",
          padding: "10px 14px",
          display: "flex", flexDirection: "column", gap: 8,
        }}>
          {/* 已选附件 chips */}
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
          <textarea value={input} onChange={e => setInput(e.target.value)}
                    placeholder={t("chat.input.placeholder")}
                    rows={2}
                    style={{ width: "100%", resize: "none", fontFamily: "inherit", boxSizing: "border-box" }}
                    onKeyDown={e => {
                      if (e.key === "Enter" && !e.shiftKey) {
                        e.preventDefault();
                        send();
                      }
                    }} />
          <div style={{
            display: "flex", justifyContent: "space-between",
            alignItems: "center", gap: 8,
          }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <input ref={attachInputRef} type="file" multiple
                accept="image/*,.pdf,.xlsx,.xlsm,.docx,.csv,.tsv,.txt"
                style={{ display: "none" }}
                onChange={e => {
                  // Capture the picked files synchronously BEFORE clearing the
                  // input. setFiles' functional updater runs deferred (React
                  // batches it), but `e.target.value = ""` runs now and clears
                  // e.target.files — so reading e.target.files inside the
                  // updater would see an empty list (→ no attachment, no chip).
                  const picked = Array.from(e.target.files || []);
                  setFiles(prev => [...prev, ...picked]);
                  e.target.value = "";
                }} />
              <button className="ghost" title={t("chat.input.attach")}
                onClick={() => attachInputRef.current?.click()}
                style={{ padding: "4px 8px", fontSize: 16, lineHeight: 1 }}>📎</button>
              {/* ③b 把「刚才这套流程」存成可复用技能 */}
              <button className="ghost" title={t("chat.learn.tooltip")}
                onClick={learnSkill}
                disabled={streaming || !profileId || !model}
                style={{ padding: "4px 8px", fontSize: 13, lineHeight: 1 }}>
                {t("chat.learn.button")}
              </button>
              <ModelPicker persistKey="chatbox" onChange={setModel} maxWidth={220} />
            </div>
            {streaming
              ? <button className="ghost" onClick={abort}>{t("chat.input.stop")}</button>
              : <button className="primary" onClick={() => send()}
                        disabled={(!input.trim() && files.length === 0) || !profileId || !model}>
                  {t("chat.input.send")}
                </button>
            }
          </div>
        </div>
      </div>
    </div>
  );
}

/** Header timestamp — "今天 HH:mm / 昨天 HH:mm / Y年M月D日 HH:mm" in zh-CN,
 * localised variants in en-US. */
function formatSessionTime(t: T, iso?: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  const now = new Date();
  const sameDay = d.toDateString() === now.toDateString();
  const yd = new Date(now); yd.setDate(now.getDate() - 1);
  const isYesterday = d.toDateString() === yd.toDateString();
  const hm = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  if (sameDay) {
    const diffMs = now.getTime() - d.getTime();
    const mins = Math.floor(diffMs / 60000);
    if (mins < 1) return t("chat.rel.now");
    if (mins < 60) return t("chat.rel.min_ago", { n: mins });
    return t("chat.rel.today_hm", { hm });
  }
  if (isYesterday) return t("chat.rel.yesterday_hm", { hm });
  return t("chat.rel.full", {
    y: d.getFullYear(), m: d.getMonth() + 1, d: d.getDate(), hm,
  });
}

function ChatAreaHeader({
  session, profile,
}: { session: Session | null; profile: AgentProfile | null }) {
  const t = useT();
  const src = session ? (session.source || session.platform || "web") : "web";
  const title = session
    ? (session.title || t("chat.area.untitled"))
    : t("chat.area.new_title");
  const srcLabel = session ? (session.source || session.platform || "web") : "";
  const when = session ? formatSessionTime(t, session.updated_at) : "";
  return (
    <div className="chat-area-header">
      <div className="chat-area-header-icon" style={{ display: "inline-flex", alignItems: "center", justifyContent: "center" }}>
        <ChannelGlyph source={src} size={20} />
      </div>
      <div className="chat-area-header-info">
        <div className="chat-area-title">{title}</div>
        <div className="chat-area-sub">
          {srcLabel && <span>{srcLabel}</span>}
          {srcLabel && when && <span className="chat-area-sep"> · </span>}
          {when && <span>{when}</span>}
          {profile && (
            <>
              <span className="chat-area-sep"> · </span>
              <span>@ {profile.id}</span>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

/**
 * Parse a clarify tool's args into a readable question + choice list.
 * The agent asks via the `clarify` tool; without this the question is
 * buried inside the raw input/output JSON collapsible and the user can't
 * tell a question was even asked (see Feishu-vs-SPA rendering parity work).
 */
function parseClarify(input: any): { question: string; choices: string[] } | null {
  if (input === undefined || input === null) return null;
  let obj: any = input;
  if (typeof input === "string") {
    try { obj = JSON.parse(input); } catch { return null; }
  }
  if (!obj || typeof obj !== "object") return null;
  const question = typeof obj.question === "string" ? obj.question.trim() : "";
  if (!question) return null;
  const rawChoices = Array.isArray(obj.choices) ? obj.choices : [];
  const choices = rawChoices
    .map((c: any) => {
      if (typeof c === "string") return c;
      if (c && typeof c === "object") return c.label || c.text || c.value || "";
      return String(c ?? "");
    })
    .map((s: string) => String(s).trim())
    .filter(Boolean);
  return { question, choices };
}

function ToolSegment({ tool }: { tool: ToolCall }) {
  const t = useT();
  const info = toolHeaderInfo(tool, t);
  const projection = renderCrmProjection(tool.tool, tool.output, t);
  const clarify = tool.tool === "clarify" ? parseClarify(tool.input) : null;

  return (
    <div className={"tool-call-block" + (info.isCrm ? " crm-tool-block" : "")}>
      {clarify && (
        <div style={{
          padding: "10px 12px",
          borderLeft: "3px solid var(--accent)",
          background: "var(--accent-soft, rgba(110,120,255,0.07))",
          borderRadius: 6, marginBottom: 6,
        }}>
          <div style={{ fontWeight: 600, lineHeight: 1.45,
                        marginBottom: clarify.choices.length ? 6 : 0 }}>
            ❓ {clarify.question}
          </div>
          {clarify.choices.length > 0 && (
            <div style={{ display: "flex", flexDirection: "column",
                          gap: 3, lineHeight: 1.45 }}>
              {clarify.choices.map((c, i) => (
                <div key={i} style={{ display: "flex", gap: 6 }}>
                  <span style={{ color: "var(--accent)", fontWeight: 600,
                                 flexShrink: 0 }}>{i + 1}.</span>
                  <span>{c}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
      <div className="tool-call-header">
        <span className="tool-call-icon">{info.icon}</span>
        <span className="tool-call-name">{info.label}</span>
        <span className={"tool-call-status " + info.statusClass}>
          {tool.status === "running" && <span className="tool-call-spinner" />}
          {" "}{info.statusText}
          {tool.durationMs != null && tool.durationMs >= 50 && (
            <span className="tool-call-duration"> · {formatDuration(tool.durationMs)}</span>
          )}
        </span>
      </div>
      {projection && (
        <div className="crm-projection" dangerouslySetInnerHTML={{ __html: projection }} />
      )}
    </div>
  );
}

// 活占位:静止的"思考中…"在长等待时像卡死。计秒让用户知道系统活着、等了多久。
// (思考内容本身按 2026-07-07 裁定不展示——原始思维链非用户级内容。)
function ThinkingTicker({ label }: { label: string }) {
  const [secs, setSecs] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setSecs(s => s + 1), 1000);
    return () => clearInterval(id);
  }, []);
  return (
    <span className="muted">
      {label}{secs >= 2 ? ` · ${secs}s` : ""}
    </span>
  );
}

function MessageBubble(
  { msg, agentName, showThinking }: { msg: Msg; agentName?: string; showThinking?: boolean },
) {
  const t = useT();
  const isUser = msg.role === "user";
  const safeSegments = (Array.isArray(msg.segments) ? msg.segments : [])
    .filter(s => !(s.kind === "text" && looksLikeInternalJudgeText(s.text || "")));
  const assistantText = !isUser
    ? safeSegments
        .filter(s => s.kind === "text" && s.text)
        .map(s => s.text!)
        .join("\n")
    : "";
  return (
    <div className={"msg-new " + (isUser ? "user" : "assistant")}>
      <div className="msg-role-label">{isUser ? t("chat.msg.user") : (agentName || "Hermes")}</div>
      <div className="msg-bubble-new">
        {safeSegments.length === 0 && showThinking && (
          <ThinkingTicker label={t("chat.msg.thinking")} />
        )}
        {safeSegments.map((s, i) => {
          if (s.kind === "text") {
            return (
              <div key={i} className="md-content"
                   dangerouslySetInnerHTML={{ __html: renderMarkdown(s.text || "") }} />
            );
          }
          if (s.kind === "reasoning") return null;
          if (s.kind === "tool" && s.tool) {
            return <ToolSegment key={i} tool={s.tool} />;
          }
          return null;
        })}
      </div>
      {msg.turnMeta && (() => {
        // Accurate breakdown without per-segment generation timing (which is
        // hard to attribute reliably): total is measured, each tool's exec
        // time is measured, so generation = total − Σ(tools). Σ(tools) is
        // summed from THIS turn's tool segments (one merged Msg = one turn).
        const toolsMs = safeSegments.reduce(
          (sum, s) => sum + (s.kind === "tool" && s.tool?.durationMs ? s.tool.durationMs : 0),
          0,
        );
        const genMs = Math.max(0, msg.turnMeta.total_ms - toolsMs);
        return (
          <div className="turn-total">
            共用时 {formatDuration(msg.turnMeta.total_ms)}
            <span className={genMs > 15000 ? "gen-duration gen-duration-slow" : ""}> · 生成 {formatDuration(genMs)}</span>
            {toolsMs >= 50 && <> · 工具 {formatDuration(toolsMs)}</>}
            {" · "}{msg.turnMeta.api_calls} 次调用
          </div>
        );
      })()}
    </div>
  );
}

// ── Session-row kebab menu ────────────────────────────────────────────
// Row-level action affordance: hover reveals "⋮"; click opens a popover
// with archive/restore/delete. Stops event propagation so the parent
// row's onClick (select session) doesn't fire when the user is
// interacting with the menu.

function SessionRowMenu({
  open, onToggle, onClose, archived,
  onArchive, onUnarchive, onDelete, t,
}: {
  open: boolean;
  onToggle: () => void;
  onClose: () => void;
  archived: boolean;
  onArchive: () => void;
  onUnarchive: () => void;
  onDelete: () => void;
  t: T;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    function onDocClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    }
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, [open, onClose]);

  return (
    <div ref={ref}
         className="conv-item-menu"
         onClick={e => e.stopPropagation()}
         style={{
           position: "absolute", top: 6, right: 6,
         }}>
      <button
        onClick={(e) => { e.stopPropagation(); onToggle(); }}
        title={t("chat.session.menu_tooltip")}
        aria-label={t("chat.session.menu_tooltip")}
        style={{
          background: "transparent", border: "none",
          padding: 4, borderRadius: 4, cursor: "pointer",
          color: "var(--muted)", display: "inline-flex",
          alignItems: "center", justifyContent: "center",
        }}
      >
        {/* Kebab — three vertical dots (Lucide more-vertical) */}
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
             stroke="currentColor" strokeWidth="2"
             strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <circle cx="12" cy="5" r="1" />
          <circle cx="12" cy="12" r="1" />
          <circle cx="12" cy="19" r="1" />
        </svg>
      </button>
      {open && (
        <div style={{
          position: "absolute", top: 28, right: 0,
          minWidth: 132, zIndex: 50,
          background: "var(--bg)",
          border: "1px solid var(--border)",
          borderRadius: 6,
          boxShadow: "var(--shadow-popover)",
          padding: 4,
          fontSize: 14,
        }}>
          {archived ? (
            <MenuItem onClick={onUnarchive} icon={
              // rotate-ccw — restore / undo
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
                   stroke="currentColor" strokeWidth="2"
                   strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <polyline points="1 4 1 10 7 10" />
                <path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10" />
              </svg>
            }>{t("chat.session.unarchive")}</MenuItem>
          ) : (
            <MenuItem onClick={onArchive} icon={
              // archive — box with lid
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
                   stroke="currentColor" strokeWidth="2"
                   strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <polyline points="21 8 21 21 3 21 3 8" />
                <rect x="1" y="3" width="22" height="5" />
                <line x1="10" y1="12" x2="14" y2="12" />
              </svg>
            }>{t("chat.session.archive")}</MenuItem>
          )}
          <MenuItem onClick={onDelete} danger icon={
            // trash-2
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
                 stroke="currentColor" strokeWidth="2"
                 strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <polyline points="3 6 5 6 21 6" />
              <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
              <path d="M10 11v6" />
              <path d="M14 11v6" />
              <path d="M9 6V4a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v2" />
            </svg>
          }>{t("chat.session.delete")}</MenuItem>
        </div>
      )}
    </div>
  );
}

function MenuItem({
  onClick, children, icon, danger,
}: {
  onClick: () => void;
  children: React.ReactNode;
  icon: React.ReactNode;
  danger?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      style={{
        display: "flex", alignItems: "center", gap: 8,
        width: "100%", padding: "6px 10px",
        background: "transparent", border: "none",
        cursor: "pointer", borderRadius: 4,
        color: danger ? "var(--danger)" : "var(--fg)",
        fontSize: 14, textAlign: "left",
      }}
      onMouseEnter={e => (e.currentTarget.style.background = "var(--surface-hover)")}
      onMouseLeave={e => (e.currentTarget.style.background = "transparent")}
    >
      {icon}
      <span>{children}</span>
    </button>
  );
}
