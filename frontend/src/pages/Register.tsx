import { useState, type FormEvent } from "react";
import { registerUser, ApiError } from "@/lib/api";
import { Link, useRouter } from "@/lib/router";
import { useT } from "@/lib/i18n";

export function RegisterPage({ onLogin }: { onLogin: () => void }) {
  const { navigate } = useRouter();
  const t = useT();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setErr(null);
    setBusy(true);
    try {
      await registerUser(email, password);
      onLogin();
      navigate("/", true);
    } catch (e) {
      setErr(e instanceof ApiError
        ? (typeof e.detail === "string" ? e.detail : `HTTP ${e.status}`)
        : t("auth.register.failed"));
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
        <h2 style={{ margin: 0 }}>{t("auth.register.heading")}</h2>
        <div className="muted small">{t("auth.brand.register_subtitle")}</div>
        <div>
          <label>{t("auth.register.email")}</label>
          <input type="email" value={email} onChange={e => setEmail(e.target.value)}
                 autoFocus autoComplete="email" />
        </div>
        <div>
          <label>{t("auth.register.password")}</label>
          <input type="password" value={password}
                 onChange={e => setPassword(e.target.value)}
                 autoComplete="new-password" />
        </div>
        {err && <div className="small" style={{ color: "var(--danger)" }}>{err}</div>}
        <button className="primary" disabled={busy || !email || !password}>
          {busy ? t("auth.register.submitting") : t("auth.register.submit")}
        </button>
        <div className="muted small" style={{ textAlign: "center" }}>
          {t("auth.register.have_account")}<Link to="/login">{t("auth.register.login_link")}</Link>
        </div>
      </form>
    </div>
  );
}
