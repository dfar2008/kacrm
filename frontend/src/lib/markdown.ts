// Minimal Markdown → HTML (bold/italic, inline code, fenced code,
// headings, lists, tables, hr, paragraphs). HTML-escapes the input
// first so arbitrary model output can't inject tags.

export function renderMarkdown(text: string): string {
  if (!text) return "";
  let html = text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");

  // Fenced code
  html = html.replace(/```([a-z0-9_-]*)\n?([\s\S]*?)```/g, (_m, _lang, code) =>
    `<pre><code>${code.trim()}</code></pre>`);
  // Inline code
  html = html.replace(/`([^`\n]+)`/g, "<code>$1</code>");
  // Bold
  html = html.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  // Italic (underscore or asterisk; skip if already inside code)
  html = html.replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");
  // Headings
  html = html.replace(/^### (.+)$/gm, "<h4>$1</h4>");
  html = html.replace(/^## (.+)$/gm, "<h3>$1</h3>");
  html = html.replace(/^# (.+)$/gm, "<h2>$1</h2>");

  // Tables (|-delimited with separator row)
  html = html.replace(
    /(^\|.+\|[ \t]*\n\|[-| :]+\|[ \t]*\n(\|.+\|[ \t]*\n?)+)/gm,
    (tableBlock) => {
      const rows = tableBlock.trim().split("\n").filter(r => r.trim());
      if (rows.length < 2) return tableBlock;
      const parseRow = (r: string) =>
        r.replace(/^\|/, "").replace(/\|$/, "").split("|").map(c => c.trim());
      const headers = parseRow(rows[0]);
      const bodyRows = rows.slice(2).map(parseRow);
      let t = '<div class="md-table-wrap"><table class="md-table"><thead><tr>';
      headers.forEach(h => { t += `<th>${h}</th>`; });
      t += "</tr></thead><tbody>";
      bodyRows.forEach(cells => {
        t += "<tr>";
        cells.forEach(c => { t += `<td>${c}</td>`; });
        t += "</tr>";
      });
      return t + "</tbody></table></div>";
    },
  );

  // Horizontal rule
  html = html.replace(/^---+$/gm, "<hr>");
  // Lists — tag ordered vs unordered items so ordered lists keep their
  // numbers (1. 2. 3.) instead of collapsing into bullets. Feishu renders the
  // raw markdown with numbers; the SPA must match, and the agent often says
  // "回我一个数字就行" — bullets would leave the user with nothing to pick.
  // Unordered list
  html = html.replace(/^[ \t]*[-*] (.+)$/gm, '<li class="md-ul">$1</li>');
  // Ordered list
  html = html.replace(/^[ \t]*\d+\. (.+)$/gm, '<li class="md-ol">$1</li>');
  // Wrap consecutive same-type items: ordered → <ol> (decimal), unordered → <ul>.
  html = html.replace(
    /((?:<li class="md-ol">.*?<\/li>\n?)+)/g,
    (m) => "<ol>" + m.replace(/ class="md-ol"/g, "") + "</ol>",
  );
  html = html.replace(
    /((?:<li class="md-ul">.*?<\/li>\n?)+)/g,
    (m) => "<ul>" + m.replace(/ class="md-ul"/g, "") + "</ul>",
  );

  // Paragraphs: double-newline → </p><p>; single newline → <br>
  const paragraphs = html.split(/\n{2,}/).map(chunk => {
    const trimmed = chunk.trim();
    if (!trimmed) return "";
    if (/^<(h\d|ul|ol|pre|hr|div|table)/.test(trimmed)) return trimmed;
    return `<p>${trimmed.replace(/\n/g, "<br>")}</p>`;
  });
  return paragraphs.join("\n");
}

// ── SSE parsing ────────────────────────────────────────────────────────
export type SSEEvent =
  | { type: "delta"; content: string }
  | { type: "reasoning"; content: string }
  | {
      type: "tool_progress";
      tool: string;
      status: "running" | "done" | "error";
      input?: any;
      output?: any;
      error?: string;
      durationMs?: number | null;
    }
  | {
      type: "turn_done";
      turnMeta: { total_ms: number; queued_ms?: number | null; api_calls: number; tool_count: number };
    }
  | { type: "draft_id"; draft_id: number }
  | { type: "done" };

export function parseSSEEvent(eventType: string, dataStr: string): SSEEvent | null {
  if (dataStr === "[DONE]") return { type: "done" };
  let obj: any;
  try { obj = JSON.parse(dataStr); } catch { return null; }

  if (eventType === "hermes.draft.id") {
    const id = Number(obj?.draft_id);
    if (Number.isFinite(id) && id > 0) return { type: "draft_id", draft_id: id };
    return null;
  }
  if (eventType === "hermes.tool.progress") {
    // The backend emits status "running" | "completed" | "failed"; the UI
    // only knows "running" | "done" | "error". Without this mapping a
    // finished tool keeps showing "执行中" until a reload (history hardcodes
    // "done"), because toolHeaderInfo treats any non-done/non-error status as
    // still-running.
    const rawStatus = obj.status || "running";
    const status = rawStatus === "completed" ? "done"
      : rawStatus === "failed" ? "error"
      : rawStatus;
    return {
      type: "tool_progress",
      tool: obj.tool || "unknown",
      status,
      input: obj.input ?? obj.label ?? null,
      output: obj.output ?? null,
      error: obj.error || "",
      durationMs: obj.durationMs ?? null,
    };
  }
  if (eventType === "hermes.reasoning") {
    // Live thinking tokens (LongCat/DeepSeek-R style). The gateway forwards
    // them as a dedicated event so they can render as a transient thinking
    // strip without ever entering conversation history.
    return { type: "reasoning", content: obj?.delta || "" };
  }
  if (eventType === "hermes.turn.done") {
    return {
      type: "turn_done",
      turnMeta: {
        total_ms: obj.total_ms,
        queued_ms: obj.queued_ms ?? null,
        api_calls: obj.api_calls,
        tool_count: obj.tool_count,
      },
    };
  }
  const delta = obj?.choices?.[0]?.delta;
  if (!delta) return null;
  if (delta.type === "reasoning" || delta.type === "thinking") {
    return { type: "reasoning", content: delta.content || "" };
  }
  if (typeof delta.content === "string") {
    return { type: "delta", content: delta.content };
  }
  return null;
}
