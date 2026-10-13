import { useT } from "@/lib/i18n";
import { type ModelPcfg, type DerivedRoles } from "./modelPresets";

function enabledModels(pcfg: ModelPcfg): { value: string; label: string }[] {
  const opts: { value: string; label: string }[] = [];
  for (const [pid, s] of Object.entries(pcfg.providers)) {
    if (!s?.enabled) continue;
    for (const m of s.models || []) opts.push({ value: `${pid}::${m.id}`, label: `${pid} / ${m.name || m.id}` });
  }
  return opts;
}

export function MoaCouncilEditor({
  pcfg, derived, onChange,
}: {
  pcfg: ModelPcfg;
  derived: DerivedRoles | null;
  onChange: (next: ModelPcfg) => void;
}) {
  const t = useT();
  const opts = enabledModels(pcfg);
  const moa = pcfg.moa || {};
  const enabled = moa.enabled !== false;         // 缺省=true
  const members = moa.reference_models || [];
  const tooFew = opts.length < 2;

  const patch = (p: Partial<typeof moa>) => onChange({ ...pcfg, moa: { ...moa, ...p } });
  const toggleMember = (value: string) => {
    const next = members.includes(value) ? members.filter(x => x !== value) : [...members, value];
    patch({ reference_models: next });
  };

  return (
    <div>
      <label style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <input type="checkbox" checked={enabled && !tooFew} disabled={tooFew}
               onChange={e => patch({ enabled: e.target.checked })} />
        <strong style={{ fontSize: 14 }}>{t("model.moa.title")}</strong>
      </label>
      <p style={{ fontSize: 13, color: "var(--muted)", margin: "4px 0 8px" }}>{t("model.moa.hint")}</p>
      {tooFew && <p style={{ fontSize: 13, color: "var(--warning)" }}>{t("model.moa.need_two")}</p>}

      {enabled && !tooFew && (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <div>
            <div style={{ fontSize: 13, color: "var(--muted)", marginBottom: 4 }}>{t("model.moa.members")}</div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
              {opts.map(o => {
                const on = members.length ? members.includes(o.value)
                  : (derived?.moa?.reference_models || []).some(r => `${r.provider.replace("custom:", "")}::${r.model}` === o.value);
                return (
                  <button key={o.value} type="button" onClick={() => toggleMember(o.value)}
                    style={{ padding: "4px 10px", fontSize: 13, borderRadius: 6, cursor: "pointer",
                             border: `1px solid ${on ? "var(--accent)" : "var(--border)"}`,
                             background: on ? "var(--accent)" : "var(--panel-2)",
                             color: on ? "var(--fg-on-accent)" : "var(--muted)" }}>
                    {o.label}
                  </button>
                );
              })}
            </div>
          </div>
          <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13 }}>
            <span style={{ color: "var(--muted)" }}>{t("model.moa.aggregator")}</span>
            <select value={moa.aggregator || ""} onChange={e => patch({ aggregator: e.target.value || null })} style={{ maxWidth: 280 }}>
              <option value="">{t("model.moa.aggregator_auto")}</option>
              {opts.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          </label>
        </div>
      )}
    </div>
  );
}
