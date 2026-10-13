import { useT } from "@/lib/i18n";

interface Props {
  title: string;
  phase: string;
  description?: string;
  /** Optional CTA button (e.g. "Sign in" → /app/login). When omitted the
   *  classic legacy /dashboard hint is shown instead. */
  action?: { label: string; href: string };
}

export function Placeholder({ title, phase, description, action }: Props) {
  const t = useT();
  return (
    <div style={{
      height: "100%", display: "grid", placeItems: "center",
      padding: 24,
    }}>
      <div className="card col" style={{ maxWidth: 520, padding: 24, textAlign: "center" }}>
        <div style={{ fontSize: 40 }}>🚧</div>
        <h2 style={{ margin: 0 }}>{title}</h2>
        <div className="chip">{t("placeholder.pending", { phase })}</div>
        {description && <div className="muted">{description}</div>}
        {action ? (
          <a
            href={action.href}
            className="btn primary"
            style={{ alignSelf: "center", marginTop: 8, padding: "8px 24px", textDecoration: "none" }}
          >
            {action.label}
          </a>
        ) : (
          <div className="muted small">
            {t("placeholder.legacy_pre")}
            <a href="/dashboard">/dashboard</a>
            {t("placeholder.legacy_post")}
          </div>
        )}
      </div>
    </div>
  );
}
