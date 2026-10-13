import { useT } from "@/lib/i18n";

/**
 * Auto-number config sub-panel.
 *
 * Writes into ``field_config.autonumber``. The live preview reproduces the
 * backend ``hermes/crm/autonumber.py:format_number`` logic EXACTLY so the
 * admin sees the real generated string before saving:
 *
 *   number = prefix + <rendered date token> + zero-padded(seq, digits)
 *
 * ``date_token`` is one of "", "YYYY", "YYYYMM", "YYYYMMDD" and is inserted
 * between the prefix and the digits. Keep this in lock-step with the Python
 * implementation — if one changes, change both.
 */
export interface AutoNumberCfg {
  prefix: string;
  digits: number;
  start: number;
  date_token: string;
}

const DATE_TOKENS = ["", "YYYY", "YYYYMM", "YYYYMMDD"] as const;

/** Mirror of backend ``format_number`` — render the date token for ``now``. */
function renderDateToken(token: string, now: Date): string {
  if (!token) return "";
  const y = String(now.getFullYear()).padStart(4, "0");
  const m = String(now.getMonth() + 1).padStart(2, "0");
  const d = String(now.getDate()).padStart(2, "0");
  if (token === "YYYY") return y;
  if (token === "YYYYMM") return y + m;
  if (token === "YYYYMMDD") return y + m + d;
  return "";
}

/** Mirror of backend ``format_number(config, seq_value, now)``. */
export function formatPreview(cfg: AutoNumberCfg, seqValue: number, now: Date): string {
  const digits = Math.max(1, Math.min(18, Math.floor(cfg.digits || 1)));
  const num = String(Math.max(0, Math.floor(seqValue))).padStart(digits, "0");
  return `${cfg.prefix || ""}${renderDateToken(cfg.date_token || "", now)}${num}`;
}

export const DEFAULT_AUTONUMBER: AutoNumberCfg = {
  prefix: "",
  digits: 5,
  start: 1,
  date_token: "",
};

export default function AutoNumberConfig({
  value,
  onChange,
}: {
  value: AutoNumberCfg | undefined;
  onChange: (next: AutoNumberCfg) => void;
}) {
  const t = useT();
  const cfg: AutoNumberCfg = value ?? DEFAULT_AUTONUMBER;

  function patch(part: Partial<AutoNumberCfg>) {
    onChange({ ...cfg, ...part });
  }

  const previewValue = Math.max(1, Math.floor(cfg.start || 1));
  const preview = formatPreview(cfg, previewValue, new Date());

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div style={{ fontWeight: 600, fontSize: 14 }}>
        {t("admin.schema.autonumber.title")}
      </div>

      <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        <span className="muted small">{t("admin.schema.autonumber.prefix")}</span>
        <input
          type="text"
          value={cfg.prefix}
          maxLength={32}
          placeholder="SO-"
          onChange={(e) => patch({ prefix: e.target.value })}
        />
      </label>

      <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        <span className="muted small">{t("admin.schema.autonumber.digits")}</span>
        <input
          type="number"
          min={1}
          max={18}
          value={cfg.digits}
          onChange={(e) =>
            patch({ digits: Math.max(1, Math.min(18, Number(e.target.value) || 1)) })
          }
        />
      </label>

      <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        <span className="muted small">{t("admin.schema.autonumber.start")}</span>
        <input
          type="number"
          min={0}
          value={cfg.start}
          onChange={(e) => patch({ start: Math.max(0, Number(e.target.value) || 0) })}
        />
      </label>

      <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        <span className="muted small">{t("admin.schema.autonumber.date_token")}</span>
        <select
          value={cfg.date_token}
          onChange={(e) => patch({ date_token: e.target.value })}
        >
          {DATE_TOKENS.map((tok) => (
            <option key={tok || "none"} value={tok}>
              {tok || t("admin.schema.autonumber.date_token.none")}
            </option>
          ))}
        </select>
      </label>

      <div
        style={{
          marginTop: 4,
          padding: "8px 10px",
          borderRadius: 6,
          background: "var(--surface-2, #f3f4f6)",
        }}
      >
        <div className="muted small">{t("admin.schema.autonumber.preview")}</div>
        <div style={{ fontFamily: "monospace", fontSize: 14, marginTop: 2 }}>
          {preview}
        </div>
      </div>
    </div>
  );
}
