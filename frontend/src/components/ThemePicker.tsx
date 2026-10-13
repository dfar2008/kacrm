import { useT } from "@/lib/i18n";
import { useTheme, SCHEMES, accentOf, accent2Of, type ThemeMode } from "@/theme";

export function ThemePicker({ open, onClose }: { open: boolean; onClose: () => void }) {
  const t = useT();
  const { theme, setTheme, mode, setMode } = useTheme();
  if (!open) return null;
  const MODES: { id: ThemeMode; label: string }[] = [
    { id: "dark", label: t("theme.mode.dark") },
    { id: "light", label: t("theme.mode.light") },
  ];
  return (
    <div
      role="dialog"
      onClick={onClose}
      style={{
        position: "fixed", inset: 0, zIndex: 100,
        background: "var(--overlay)",
        display: "flex", alignItems: "center", justifyContent: "center",
      }}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={{
          width: 520, maxHeight: "80vh", overflow: "auto",
          background: "var(--popover-bg)", border: "1px solid var(--border)",
          borderRadius: 12, padding: 18, boxShadow: "var(--shadow-modal)",
        }}
      >
        <div style={{ fontSize: 15, fontWeight: 700, color: "var(--fg-strong)" }}>
          {t("theme.panel.title")}
        </div>
        <div style={{ fontSize: 14, color: "var(--muted)", marginTop: 2, marginBottom: 14 }}>
          {t("theme.panel.subtitle")}
        </div>
        {/* 深色 / 浅色 分段切换 —— 与下面的 accent 色卡正交 */}
        <div style={{
          display: "inline-flex", gap: 2, padding: 2, marginBottom: 14,
          background: "var(--panel-2)", border: "1px solid var(--border)",
          borderRadius: 9,
        }}>
          {MODES.map(m => {
            const on = m.id === mode;
            return (
              <button
                key={m.id}
                onClick={() => setMode(m.id)}
                style={{
                  padding: "5px 16px", borderRadius: 7, border: "none", cursor: "pointer",
                  fontSize: 13, fontWeight: on ? 600 : 400,
                  background: on ? "var(--accent)" : "transparent",
                  color: on ? "var(--fg-on-accent)" : "var(--muted)",
                }}
              >{m.label}</button>
            );
          })}
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: 10 }}>
          {SCHEMES.map(s => {
            const active = s.id === theme;
            return (
              <button
                key={s.id}
                title={t(s.nameKey)}
                onClick={() => setTheme(s.id)}
                style={{
                  display: "flex", flexDirection: "column", gap: 6, padding: 8,
                  background: "transparent",
                  border: active ? "2px solid var(--accent)" : "1px solid var(--border)",
                  borderRadius: 10, cursor: "pointer",
                }}
              >
                <span style={{
                  height: 34, borderRadius: 7, overflow: "hidden",
                  display: "flex", border: "1px solid var(--border)",
                }}>
                  <i style={{ flex: 2, background: accentOf(s) }} />
                  <i style={{ flex: 1, background: accent2Of(s) }} />
                </span>
                <span style={{
                  fontSize: 13,
                  color: active ? "var(--fg)" : "var(--muted)",
                  overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                }}>{t(s.nameKey)}</span>
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}
