import { useState } from "react";
import { useT } from "@/lib/i18n";
import { AUX_TASKS, type AuxTask, type ModelPcfg, type DerivedRoles } from "./modelPresets";

/** 组装“所有已启用模型”下拉选项(value=provider::model)。 */
function modelOptions(pcfg: ModelPcfg): { value: string; label: string }[] {
  const opts: { value: string; label: string }[] = [];
  for (const [pid, s] of Object.entries(pcfg.providers)) {
    if (!s?.enabled) continue;
    for (const m of s.models || []) opts.push({ value: `${pid}::${m.id}`, label: `${pid} / ${m.name || m.id}` });
  }
  return opts;
}

export function AuxiliaryModelPicker({
  pcfg, derived, onChange,
}: {
  pcfg: ModelPcfg;
  derived: DerivedRoles | null;
  onChange: (next: ModelPcfg) => void;
}) {
  const t = useT();
  const [advanced, setAdvanced] = useState(false);
  const opts = modelOptions(pcfg);
  const aux = pcfg.auxiliary || {};

  const setUnified = (v: string) =>
    onChange({ ...pcfg, auxiliary: { ...aux, unified: v || null } });
  const setOverride = (task: AuxTask, v: string) =>
    onChange({ ...pcfg, auxiliary: { ...aux, overrides: { ...(aux.overrides || {}), [task]: v || null } } });

  // 生效值提示(来自 derived)。
  const effLabel = (task: AuxTask): string => {
    const slot = derived?.auxiliary?.[task];
    if (!slot) return "";
    if (slot.source === "provider_default") return t("model.aux.auto_provider_default");
    const name = slot.model || "";
    return `${name}（${t(`model.aux.source.${slot.source}`)}）`;
  };

  return (
    <div>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
        <strong style={{ fontSize: 14 }}>{t("model.aux.title")}</strong>
        <span style={{ fontSize: 13, color: "var(--muted)" }}>{effLabel("compression")}</span>
      </div>
      <p style={{ fontSize: 13, color: "var(--muted)", margin: "4px 0 8px" }}>{t("model.aux.hint")}</p>
      <select value={aux.unified || ""} onChange={e => setUnified(e.target.value)} style={{ maxWidth: 320 }}>
        <option value="">{t("model.aux.auto")}</option>
        {opts.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>

      <button type="button" onClick={() => setAdvanced(a => !a)}
        style={{ display: "block", marginTop: 8, background: "none", border: "none",
                 color: "var(--accent)", cursor: "pointer", fontSize: 13, padding: 0 }}>
        {advanced ? "▾ " : "▸ "}{t("model.aux.advanced")}
      </button>
      {advanced && (
        <div style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 8 }}>
          {AUX_TASKS.map(task => (
            <label key={task} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13 }}>
              <span style={{ width: 96, color: "var(--muted)" }}>{t(`model.aux.task.${task}`)}</span>
              <select value={(aux.overrides?.[task]) || ""} onChange={e => setOverride(task, e.target.value)} style={{ flex: 1, maxWidth: 280 }}>
                <option value="">{t("model.aux.auto")}</option>
                {opts.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
              </select>
              <span style={{ fontSize: 12, color: "var(--muted)" }}>{effLabel(task)}</span>
            </label>
          ))}
        </div>
      )}
    </div>
  );
}
