/**
 * Reusable dropdown that lets the user pick which LLM model to use for
 * the next chat turn. Lives at the bottom of each chat box (ChatInbox,
 * Copilot, Decisions, Dashboards) right next to the send button.
 *
 * Visually mimics a styled ``<input>``: same height / padding / border
 * tokens as the surrounding textbox so the toolbar looks of-a-piece.
 *
 * No native ``<select>`` — styled ``<button>`` + popover so the option
 * list can render long labels, show the "default" hint, and degrade
 * gracefully into a "未配置模型" empty state with a link to profile
 * settings.
 */
import { useEffect, useRef, useState } from "react";

import { useActiveModels, type PickerSurface } from "@/lib/activeModels";
import { useRouter } from "@/lib/router";
import { useT } from "@/lib/i18n";

interface Props {
  /** Per-surface storage key so each chat box remembers its own last pick. */
  persistKey: PickerSurface;
  /** Notify parent when the selection changes (so it can send the
   *  ``model`` field in the next request). Also fires once on initial
   *  resolution so parent gets the default id without extra wiring. */
  onChange?: (modelId: string | null) => void;
  /** Optional override for the picker's max width (default ``180``). */
  maxWidth?: number;
}

export function ModelPicker({ persistKey, onChange, maxWidth = 180 }: Props) {
  const t = useT();
  const router = useRouter();
  const { models, configured, selected, select, loading, error } =
    useActiveModels(persistKey);
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);

  // Bubble the resolved selection to the parent whenever it changes.
  useEffect(() => { onChange?.(selected); }, [selected, onChange]);

  // Click-outside to close.
  useEffect(() => {
    if (!open) return;
    const onDocClick = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, [open]);

  const currentModel = models.find(m => m.id === selected);
  const currentLabel =
    (currentModel?.provider_id === "moa"
      ? t("model_picker.moa.label")
      : currentModel?.label)
    ?? (loading ? t("model_picker.loading") : "");

  // ── Not configured ──────────────────────────────────────────────────
  if (!loading && !configured) {
    return (
      <button
        type="button"
        title={t("model_picker.no_model.tooltip")}
        onClick={() => router.navigate("/app/settings/model")}
        style={_btnStyle({ maxWidth, warn: true })}
      >
        <span style={{ fontSize: 14 }}>⚠️</span>
        <span style={_truncateStyle}>{t("model_picker.no_model.label")}</span>
      </button>
    );
  }

  // ── Error ───────────────────────────────────────────────────────────
  if (error) {
    return (
      <button
        type="button"
        title={error}
        style={_btnStyle({ maxWidth, warn: true })}
        onClick={() => window.location.reload()}
      >
        <span style={_truncateStyle}>
          {t("model_picker.error")}
        </span>
      </button>
    );
  }

  return (
    <div ref={rootRef} style={{ position: "relative", maxWidth }}>
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        title={selected ?? ""}
        style={_btnStyle({ maxWidth })}
      >
        <span style={_truncateStyle}>{currentLabel}</span>
        <span style={{ fontSize: 13, color: "var(--muted)" }}>▾</span>
      </button>
      {open && (
        <div style={{
          position: "absolute",
          bottom: "calc(100% + 4px)",
          left: 0,
          minWidth: maxWidth,
          maxWidth: 320,
          background: "var(--panel)",
          border: "1px solid var(--border)",
          borderRadius: 6,
          boxShadow: "var(--shadow-popover)",
          zIndex: 100,
          maxHeight: 280,
          overflowY: "auto",
        }}>
          {models.map(m => {
            const isSelected = m.id === selected;
            // ②b「深度会诊」档:provider_id==="moa" 的项走特判——用醒目
            // 标签 + hover 提示告诉用户它更慢更贵、只适合复杂研判。
            const isMoa = m.provider_id === "moa";
            const label = isMoa ? t("model_picker.moa.label") : m.label;
            return (
              <button
                key={m.id}
                type="button"
                title={isMoa ? t("model_picker.moa.tooltip") : (m.label ?? "")}
                onClick={() => { select(m.id); setOpen(false); }}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  width: "100%",
                  padding: "6px 10px",
                  background: isSelected ? "var(--panel-2)" : "transparent",
                  border: "none",
                  borderTop: isMoa ? "1px solid var(--border)" : "none",
                  color: "var(--fg)",
                  fontSize: 14,
                  textAlign: "left",
                  cursor: "pointer",
                }}
                onMouseEnter={e => {
                  if (!isSelected) e.currentTarget.style.background = "var(--panel-2)";
                }}
                onMouseLeave={e => {
                  if (!isSelected) e.currentTarget.style.background = "transparent";
                }}
              >
                <span style={{ fontSize: 13, width: 12 }}>
                  {isSelected ? "✓" : (isMoa ? "🩺" : "")}
                </span>
                <span style={{
                  flex: 1,
                  ..._truncateStyle,
                  fontWeight: isMoa ? 600 : 400,
                }}>
                  {label}
                </span>
                {isMoa ? (
                  <span style={{
                    fontSize: 11,
                    lineHeight: 1.4,
                    padding: "1px 6px",
                    borderRadius: 4,
                    color: "var(--fg-on-accent, #06121a)",
                    background: "var(--accent, #3FE0D0)",
                    marginLeft: 6,
                    whiteSpace: "nowrap",
                  }}>
                    {t("model_picker.moa.badge")}
                  </span>
                ) : (
                  <span style={{
                    fontSize: 13, color: "var(--muted)", marginLeft: 6,
                  }}>
                    {m.provider_id}
                  </span>
                )}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

const _truncateStyle: React.CSSProperties = {
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};

function _btnStyle({ maxWidth, warn = false }: { maxWidth: number; warn?: boolean }): React.CSSProperties {
  return {
    display: "inline-flex",
    alignItems: "center",
    gap: 6,
    maxWidth,
    padding: "6px 10px",
    background: "var(--panel)",
    color: warn ? "var(--warning)" : "var(--fg)",
    border: `1px solid ${warn ? "var(--warning)" : "var(--border)"}`,
    borderRadius: 6,
    fontSize: 14,
    lineHeight: 1.2,
    cursor: "pointer",
  };
}
