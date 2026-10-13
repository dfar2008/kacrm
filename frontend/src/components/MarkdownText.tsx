import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/**
 * Minimal markdown renderer for chat-style surfaces (Copilot replies,
 * timeline copilot_reply rows). Keeps the surface tight:
 *
 *   - GFM tables / strikethrough / autolinks
 *   - Safe defaults (no raw HTML — react-markdown blocks it by default)
 *   - Inline-friendly typography (no extra outer margins so the bubble
 *     hugs its container)
 *   - Code blocks render with a subtle background but don't try to do
 *     syntax highlighting (don't pull in highlight.js for the sidebar)
 *
 * Intentionally tiny. If we need more (mermaid / math / video embeds /
 * etc.) it'll be a different surface (a doc-viewer panel), not this.
 */
export function MarkdownText({ text }: { text: string }) {
  return (
    <div className="md-body" style={{ wordBreak: "break-word" }}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          p: ({ children }) => (
            <p style={{ margin: "0 0 6px 0", lineHeight: 1.55 }}>{children}</p>
          ),
          h1: ({ children }) => (
            <h1 style={{ fontSize: 15, margin: "8px 0 4px", fontWeight: 700 }}>{children}</h1>
          ),
          h2: ({ children }) => (
            <h2 style={{ fontSize: 14, margin: "8px 0 4px", fontWeight: 700 }}>{children}</h2>
          ),
          h3: ({ children }) => (
            <h3 style={{ fontSize: 14, margin: "8px 0 4px", fontWeight: 700 }}>{children}</h3>
          ),
          h4: ({ children }) => (
            <h4 style={{ fontSize: 14, margin: "6px 0 3px", fontWeight: 600 }}>{children}</h4>
          ),
          ul: ({ children }) => (
            <ul style={{ margin: "4px 0", paddingLeft: 18 }}>{children}</ul>
          ),
          ol: ({ children }) => (
            <ol style={{ margin: "4px 0", paddingLeft: 18 }}>{children}</ol>
          ),
          li: ({ children }) => (
            <li style={{ margin: "2px 0", lineHeight: 1.5 }}>{children}</li>
          ),
          code: (props: any) => {
            // inline code (no language); block code goes through `pre`.
            const inline = !(props.className && /language-/.test(props.className));
            if (inline) {
              return (
                <code style={{
                  background: "var(--surface-hover)",
                  padding: "1px 5px", borderRadius: 4,
                  fontSize: "0.9em",
                }}>{props.children}</code>
              );
            }
            return <code {...props} />;
          },
          pre: ({ children }) => (
            <pre style={{
              background: "var(--surface-hover)",
              padding: 8, borderRadius: 6,
              overflow: "auto", margin: "6px 0",
              fontSize: 14, lineHeight: 1.45,
            }}>{children}</pre>
          ),
          table: ({ children }) => (
            <div style={{ overflow: "auto", margin: "6px 0" }}>
              <table style={{
                borderCollapse: "collapse",
                fontSize: 14,
                width: "100%",
              }}>{children}</table>
            </div>
          ),
          th: ({ children }) => (
            <th style={{
              border: "1px solid var(--border)",
              padding: "4px 6px", textAlign: "left",
              background: "var(--surface-hover)",
              fontWeight: 600,
            }}>{children}</th>
          ),
          td: ({ children }) => (
            <td style={{
              border: "1px solid var(--border)",
              padding: "4px 6px",
            }}>{children}</td>
          ),
          a: ({ href, children }) => (
            <a href={href} target="_blank" rel="noreferrer noopener"
               style={{ color: "var(--accent)", textDecoration: "underline" }}>
              {children}
            </a>
          ),
          blockquote: ({ children }) => (
            <blockquote style={{
              margin: "6px 0",
              padding: "2px 10px",
              borderLeft: "3px solid var(--border)",
              color: "var(--muted)",
            }}>{children}</blockquote>
          ),
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}
