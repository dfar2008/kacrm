import { useState } from "react";
import { postNote, ApiError } from "@/lib/api";
import { useT } from "@/lib/i18n";

interface Props {
  module: string;
  recordId: number;
  onSaved: () => void;
}

export function NoteForm({ module, recordId, onSaved }: Props) {
  const t = useT();
  const [text, setText] = useState("");
  const [progress, setProgress] = useState("");
  const [nextDate, setNextDate] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function submit() {
    if (!text.trim()) return;
    setBusy(true);
    setErr(null);
    try {
      await postNote({
        module, record_id: recordId,
        content: text,
        progress: progress || undefined,
        next_contact_date: nextDate || undefined,
      });
      setText(""); setProgress(""); setNextDate("");
      onSaved();
    } catch (e) {
      setErr(e instanceof ApiError
        ? (typeof e.detail === "string" ? e.detail : t("note.save_failed"))
        : t("note.save_failed"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card col" style={{ padding: 12 }}>
      <div style={{ fontWeight: 600 }}>{t("note.title")}</div>
      <textarea placeholder={t("note.content_ph")}
                value={text} onChange={e => setText(e.target.value)} />
      <div className="row" style={{ gap: 12 }}>
        <div style={{ flex: 1 }}>
          <label>{t("note.progress")}</label>
          <input placeholder={t("note.progress_ph")}
                 value={progress} onChange={e => setProgress(e.target.value)} />
        </div>
        <div style={{ flex: 1 }}>
          <label>{t("note.next_contact")}</label>
          <input type="date" value={nextDate} onChange={e => setNextDate(e.target.value)} />
        </div>
      </div>
      {err && <div className="small" style={{ color: "var(--danger)" }}>{err}</div>}
      <div className="row" style={{ justifyContent: "flex-end" }}>
        <button className="primary" disabled={busy || !text.trim()} onClick={submit}>
          {busy ? t("note.saving") : t("note.save")}
        </button>
      </div>
    </div>
  );
}
