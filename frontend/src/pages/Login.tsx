import { useEffect, useState, type FormEvent } from "react";
import {
  loginWithEmail, loginWithUsername, getRegistrationStatus, ApiError,
} from "@/lib/api";
import { Link, useRouter } from "@/lib/router";
import { useT } from "@/lib/i18n";
import { KacrmMark } from "@/components/KacrmMark";

export function LoginPage({ onLogin }: { onLogin: () => void }) {
  const { navigate } = useRouter();
  const t = useT();
  const [identifier, setIdentifier] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [canRegister, setCanRegister] = useState(false);

  useEffect(() => {
    getRegistrationStatus()
      .then(r => setCanRegister(!!r.allow_registration))
      .catch(() => { /* keep hidden */ });
  }, []);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setErr(null);
    setBusy(true);
    try {
      // Dual-channel: if identifier looks like an email try /auth/login,
      // otherwise go straight to /crm/auth/login (also accepts email).
      if (identifier.includes("@")) {
        try {
          await loginWithEmail(identifier, password);
        } catch (e) {
          // Fallback to CRM login — it accepts emails too.
          if (e instanceof ApiError && e.status === 401) {
            await loginWithUsername(identifier, password);
          } else throw e;
        }
      } else {
        await loginWithUsername(identifier, password);
      }
      onLogin();
      navigate("/", true);
    } catch (e) {
      setErr(e instanceof ApiError
        ? (typeof e.detail === "string" ? e.detail : `HTTP ${e.status}`)
        : t("auth.login.failed"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{
      height: "100vh", display: "grid", placeItems: "center",
      background: "var(--bg)",
    }}>
      <form className="card col" style={{ width: 360, padding: 24 }} onSubmit={submit}>
        <div className="row" style={{ gap: 11 }}>
          <div style={{
            width: 42, height: 42, borderRadius: 10,
            background: "var(--panel-2)",
            display: "grid", placeItems: "center", flex: "none",
          }}><KacrmMark size={40} title="KaCRM" /></div>
          <div>
            <div style={{ fontWeight: 700 }}>KaCRM</div>
            <div className="muted small">{t("auth.brand.tagline")}</div>
          </div>
        </div>
        <h2 style={{ margin: "4px 0 0 0" }}>{t("auth.login.heading")}</h2>
        <div>
          <label>{t("auth.login.identifier_label")}</label>
          <input value={identifier} onChange={e => setIdentifier(e.target.value)}
                 autoFocus autoComplete="username" />
        </div>
        <div>
          <label>{t("auth.login.password")}</label>
          <input type="password" value={password}
                 onChange={e => setPassword(e.target.value)}
                 autoComplete="current-password" />
        </div>
        {err && <div className="small" style={{ color: "var(--danger)" }}>{err}</div>}
        <button className="primary" disabled={busy || !identifier || !password}>
          {busy ? t("auth.login.submitting") : t("auth.login.submit")}
        </button>
        {canRegister && (
          <div className="muted small" style={{ textAlign: "center" }}>
            {t("auth.login.no_account")}<Link to="/register">{t("auth.login.register_link")}</Link>
          </div>
        )}
      </form>
    </div>
  );
}
