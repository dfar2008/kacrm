import { useEffect, useState, type CSSProperties } from "react";
import { useT, type DictKey } from "@/lib/i18n";
import { useTheme, SCHEMES, RECOMMENDED_SCHEME_IDS, accentOf, accent2Of, type ThemeMode, type Scheme } from "@/theme";
import { CrmIcon, type CrmIconName } from "./CrmIcon";
import "./theme-picker.css";

export function ThemePicker({ open, onClose }: { open: boolean; onClose: () => void }) {
  const t = useT();
  const { theme, setTheme, mode, resolvedMode, setMode } = useTheme();
  const [showAll, setShowAll] = useState(false);
  useEffect(() => {
    if (!open) return;
    const close = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [open, onClose]);
  if (!open) return null;

  const modes: { id: ThemeMode; icon: CrmIconName; label: string }[] = [
    { id: "system", icon: "monitor", label: t("theme.mode.system") },
    { id: "light", icon: "sun", label: t("theme.mode.light") },
    { id: "dark", icon: "moon", label: t("theme.mode.dark") },
  ];
  const recommended = RECOMMENDED_SCHEME_IDS.map(id => SCHEMES.find(s => s.id === id)!);
  const others = SCHEMES.filter(s => !RECOMMENDED_SCHEME_IDS.includes(s.id));
  const selected = SCHEMES.find(s => s.id === theme)!;
  const card = (scheme: Scheme) => {
    const active = scheme.id === theme;
    return <button key={scheme.id} className="kacrm-scheme-card" aria-pressed={active}
      aria-label={t(scheme.nameKey as DictKey)} title={t(scheme.nameKey as DictKey)}
      onClick={() => setTheme(scheme.id)}
      style={{ "--kacrm-swatch-accent": accentOf(scheme, resolvedMode),
        "--kacrm-swatch-secondary": accent2Of(scheme, resolvedMode) } as CSSProperties}>
      <span className="kacrm-scheme-preview" aria-hidden="true">
        <span className="kacrm-scheme-preview-rail"><i /><i /><i /></span>
        <span className="kacrm-scheme-preview-main"><i /><i /><i /></span>
      </span>
      <span className="kacrm-scheme-caption"><span>{t(scheme.nameKey as DictKey)}</span>
        {active && <CrmIcon name="check" size={14} />}</span>
    </button>;
  };

  return <div className="kacrm-theme-overlay" onClick={onClose}>
    <section role="dialog" aria-modal="true" aria-labelledby="kacrm-theme-title"
      className="kacrm-theme-picker" onClick={event => event.stopPropagation()}>
      <header className="kacrm-theme-header">
        <div><h2 id="kacrm-theme-title">{t("theme.panel.title")}</h2>
          <p>{t("theme.panel.subtitle")}</p></div>
        <button className="ghost icon-close" onClick={onClose} aria-label={t("theme.panel.close")}>
          <CrmIcon name="close" size={18} />
        </button>
      </header>
      <div className="kacrm-theme-content">
        <h3>{t("theme.panel.appearance")}</h3>
        <div className="kacrm-mode-control" role="group" aria-label={t("theme.panel.appearance")}>
          {modes.map(item => <button key={item.id} aria-pressed={mode === item.id}
            onClick={() => setMode(item.id)}><CrmIcon name={item.icon} size={17} />{item.label}</button>)}
        </div>
        <p className="kacrm-mode-hint">{mode === "system"
          ? t("theme.panel.system_hint", { mode: t(resolvedMode === "dark" ? "theme.mode.dark" : "theme.mode.light") })
          : t("theme.panel.manual_hint")}</p>
        <div className="kacrm-theme-section-title"><h3>{t("theme.panel.accent")}</h3>
          <span>{t(selected.nameKey as DictKey)}</span></div>
        <div className="kacrm-scheme-grid">{recommended.map(card)}</div>
        <button className="ghost kacrm-more-schemes" aria-expanded={showAll}
          onClick={() => setShowAll(value => !value)}>
          {t(showAll ? "theme.panel.less" : "theme.panel.more")}
          <CrmIcon name="chevron" size={16} />
        </button>
        {showAll && <div className="kacrm-scheme-grid kacrm-other-schemes">{others.map(card)}</div>}
      </div>
    </section>
  </div>;
}
