import { useEffect, useRef, useState } from "react";
import { getModelConfig, putModelConfig, testModelsEndpoint } from "@/lib/api";
import { toast } from "@/lib/toast";
import { useT, type T } from "@/lib/i18n";
import {
  KNOWN_PROVIDERS, ZEN_FREE_IDS, _autoName, adjustBaseUrlForFormat, hydratePcfg,
  type ModelPcfg, type ModelEntry, type KnownProvider, type DerivedRoles,
} from "./modelPresets";
import { AddModelModal } from "./AddModelModal";
import { AuxiliaryModelPicker } from "./AuxiliaryModelPicker";
import { MoaCouncilEditor } from "./MoaCouncilEditor";

/**
 * Model tab — port of `renderModelConfig` from hermes_cloud/static/app.js.
 */
export function ModelTab({ profileId }: { profileId: string }) {
  const translate = useT();
  const [pcfg, setPcfg] = useState<ModelPcfg | null>(null);
  const [derived, setDerived] = useState<DerivedRoles | null>(null);
  const [subTab, setSubTab] = useState<"basic" | "advanced">("basic");
  const [selectedId, setSelectedId] = useState<string>(KNOWN_PROVIDERS[0].id);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getModelConfig(profileId)
      .then(r => {
        if (cancelled) return;
        let raw: unknown = r?.providers_json ?? null;
        if (typeof raw === "string") {
          try { raw = JSON.parse(raw); } catch { raw = null; }
        }
        const next = hydratePcfg(raw);
        // Persist if hydrate refreshed any curated provider's
        // base_url / api_format / models so the chat picker (reads
        // /api/me/active-models from DB) and the agent runtime
        // (reads profile config.yaml) both see fresh values without
        // waiting for the user to click Save. Curated providers are
        // ones whose upstream surface/model-IDs the user shouldn't
        // be expected to maintain (OpenCode Zen free rotation,
        // DeepSeek V4 transition, ...).
        type StoredProvider = {
          base_url?: string;
          api_format?: string;
          models?: { id?: string }[];
        };
        const storedProviders = (raw && typeof raw === "object"
          ? (raw as { providers?: Record<string, StoredProvider> }).providers
          : undefined) ?? {};
        const storedConfig = raw && typeof raw === "object" ? raw as Partial<ModelPcfg> : {};
        let curatedDrifted = (storedConfig.default_model ?? "") !== next.default_model
          || JSON.stringify(storedConfig.auxiliary) !== JSON.stringify(next.auxiliary)
          || JSON.stringify(storedConfig.moa) !== JSON.stringify(next.moa);
        for (const p of KNOWN_PROVIDERS) {
          if (!p.curated && p.id !== "ollama") continue;
          const stored = storedProviders[p.id];
          const fresh = next.providers[p.id];
          if (!stored || !fresh) { curatedDrifted = true; break; }
          if (stored.base_url !== fresh.base_url
              || stored.api_format !== fresh.api_format
              || JSON.stringify((stored.models ?? []).map(m => m?.id ?? ""))
                 !== JSON.stringify(fresh.models.map(m => m.id))) {
            curatedDrifted = true;
            break;
          }
        }
        setDerived((r?.derived as DerivedRoles | undefined) ?? null);
        setPcfg(next);
        if (curatedDrifted) {
          void putModelConfig(profileId, next).catch(() => {});
        }
        setErr(null);
      })
      .catch(e => { if (!cancelled) setErr(e instanceof Error ? e.message : String(e)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [profileId]);

  async function saveNow(next: ModelPcfg) {
    try {
      // PUT returns the freshly-derived roles — refresh so "当前生效"
      // labels + MoA auto-highlight stay truthful without a reload.
      const r = await putModelConfig(profileId, next);
      setDerived(((r as { derived?: DerivedRoles }).derived) ?? null);
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e), "error");
      throw e;
    }
  }

  function mutate(fn: (p: ModelPcfg) => ModelPcfg, persist: boolean = true) {
    setPcfg(prev => {
      if (!prev) return prev;
      const next = fn(prev);
      if (persist) void saveNow(next).catch(() => {});
      return next;
    });
  }

  function handleExport() {
    if (!pcfg) return;
    const blob = new Blob([JSON.stringify(pcfg, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "hermes-model-config.json";
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  }

  function handleImportClick() { fileInputRef.current?.click(); }
  async function handleImportFile(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    e.target.value = "";
    if (!f) return;
    try {
      const text = await f.text();
      const imported = JSON.parse(text);
      if (!imported || typeof imported !== "object" || !imported.providers) {
        toast(translate("model.import.invalid"), "error");
        return;
      }
      const next = hydratePcfg(imported);
      setPcfg(next);
      await saveNow(next);
      toast(translate("model.import.ok"), "success");
    } catch (err) {
      toast(translate("model.import.failed", { msg: err instanceof Error ? err.message : String(err) }), "error");
    }
  }

  if (loading) return <div className="muted">{translate("model.loading")}</div>;
  if (err) return <div className="alert alert-error">{err}</div>;
  if (!pcfg) return null;

  const selected = KNOWN_PROVIDERS.find(p => p.id === selectedId) ?? KNOWN_PROVIDERS[0];
  const ps = pcfg.providers[selected.id];

  // Persist + refresh derived on any model-role change (shared by the
  // default-model select, AuxiliaryModelPicker, and MoaCouncilEditor).
  const rolesOnChange = (next: ModelPcfg) => { setPcfg(next); void saveNow(next).catch(() => {}); };

  // "所有已启用模型" options for the default-model select (basic 子标签).
  const defaultModelOptions = [{ value: "", label: translate("model.default.auto") }];
  for (const pp of KNOWN_PROVIDERS) {
    const s = pcfg.providers[pp.id];
    if (s?.enabled) for (const m of s.models || [])
      defaultModelOptions.push({ value: `${pp.id}::${m.id}`, label: `${pp.name} / ${m.name || m.id}` });
  }

  return (
    <>
      <div style={{
        display: "flex", alignItems: "flex-start",
        justifyContent: "space-between", marginBottom: 16,
      }}>
        <div><h2 style={{ margin: 0, fontSize: 20 }}>{translate("model.title")}</h2></div>
        <div style={{ display: "flex", gap: 8, marginTop: 4 }}>
          <button onClick={handleImportClick} className="btn btn-ghost btn-sm"
                  style={ghostBtn}>{translate("model.import")}</button>
          <button onClick={handleExport} className="btn btn-ghost btn-sm"
                  style={ghostBtn}>{translate("model.export")}</button>
          <input ref={fileInputRef} type="file" accept=".json"
                 style={{ display: "none" }} onChange={handleImportFile} />
        </div>
      </div>

      {/* 子标签：基本(默认模型+提供商) / 高级(辅助模型+深度会诊) */}
      <div style={{ display: "flex", gap: 6, marginBottom: 16 }}>
        <FormatPill active={subTab === "basic"} onClick={() => setSubTab("basic")}>
          {translate("model.subtab.basic")}
        </FormatPill>
        <FormatPill active={subTab === "advanced"} onClick={() => setSubTab("advanced")}>
          {translate("model.subtab.advanced")}
        </FormatPill>
      </div>

      {subTab === "basic" && (
        <>
          <div style={{ ...cardStyle, marginBottom: 12 }}>
            <strong style={{ fontSize: 14, display: "block", marginBottom: 4 }}>
              {translate("model.default.title")}
            </strong>
            <p style={hintStyle}>{translate("model.default.hint")}</p>
            <select
              value={pcfg.default_model || ""}
              onChange={e => rolesOnChange({ ...pcfg, default_model: e.target.value })}
              style={{ maxWidth: 320 }}
            >
              {defaultModelOptions.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          </div>

          <div style={{
            display: "grid", gridTemplateColumns: "220px 1fr", gap: 16,
            alignItems: "start",
          }}>
            <ProviderListPanel
              pcfg={pcfg}
              selectedId={selected.id}
              onSelect={setSelectedId}
              onToggle={(pid, enabled) => mutate(p => ({
                ...p,
                providers: { ...p.providers, [pid]: { ...p.providers[pid], enabled } },
              }))}
              t={translate}
            />
            <ProviderDetailPanel
              provider={selected}
              ps={ps}
              onSaveSettings={async (api_key, base_url, api_format) => {
                const next: ModelPcfg = {
                  ...pcfg,
                  providers: {
                    ...pcfg.providers,
                    [selected.id]: { ...pcfg.providers[selected.id], api_key, base_url, api_format },
                  },
                };
                setPcfg(next);
                await saveNow(next);
              }}
              onTestConnection={() => testModelsEndpoint(profileId)}
              onAddModel={() => setAddOpen(true)}
              onDeleteModel={idx => mutate(p => {
                const nextModels = (p.providers[selected.id].models || []).filter((_, i) => i !== idx);
                return {
                  ...p,
                  providers: {
                    ...p.providers,
                    [selected.id]: { ...p.providers[selected.id], models: nextModels },
                  },
                };
              })}
              t={translate}
            />
          </div>
        </>
      )}

      {subTab === "advanced" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <div style={cardStyle}>
            <AuxiliaryModelPicker pcfg={pcfg} derived={derived} onChange={rolesOnChange} />
          </div>
          <div style={cardStyle}>
            <MoaCouncilEditor pcfg={pcfg} derived={derived} onChange={rolesOnChange} />
          </div>
        </div>
      )}

      {addOpen && (
        <AddModelModal
          onClose={() => setAddOpen(false)}
          onConfirm={m => {
            mutate(p => {
              const existing = p.providers[selected.id].models || [];
              return {
                ...p,
                providers: {
                  ...p.providers,
                  [selected.id]: { ...p.providers[selected.id], models: [...existing, m] },
                },
              };
            });
            setAddOpen(false);
          }}
        />
      )}
    </>
  );
}

function ProviderListPanel({
  pcfg, selectedId, onSelect, onToggle, t: translate,
}: {
  pcfg: ModelPcfg;
  selectedId: string;
  onSelect: (id: string) => void;
  onToggle: (id: string, enabled: boolean) => void;
  t: T;
}) {
  return (
    <div style={{
      background: "var(--panel)", border: "1px solid var(--border)",
      borderRadius: 10, padding: "10px 8px",
    }}>
      <div style={{
        fontSize: 13, color: "var(--muted)",
        textTransform: "uppercase", letterSpacing: 1,
        padding: "4px 8px 10px",
      }}>{translate("model.providers")}</div>
      {KNOWN_PROVIDERS.map(p => {
        const st = pcfg.providers[p.id];
        const active = p.id === selectedId;
        return (
          <div
            key={p.id}
            onClick={() => onSelect(p.id)}
            style={{
              display: "flex", alignItems: "center", gap: 8,
              padding: "8px 10px", borderRadius: 7,
              cursor: "pointer",
              background: active ? "var(--panel-2)" : "transparent",
              color: active ? "var(--fg)" : "inherit",
              marginBottom: 2,
            }}
          >
            <span style={{ fontSize: 15, width: 18, textAlign: "center" }}>{p.icon}</span>
            <span style={{ flex: 1, fontSize: 14 }}>{p.name}</span>
            <label
              onClick={e => e.stopPropagation()}
              style={{
                position: "relative", display: "inline-block",
                width: 30, height: 16, flexShrink: 0,
              }}
            >
              <input
                type="checkbox" checked={!!st?.enabled}
                onChange={e => onToggle(p.id, e.target.checked)}
                style={{ opacity: 0, width: 0, height: 0 }}
              />
              <span style={{
                position: "absolute", inset: 0, cursor: "pointer",
                background: st?.enabled ? "var(--accent)" : "var(--border)",
                borderRadius: 8, transition: "background 120ms",
              }} />
              <span style={{
                position: "absolute", top: 2,
                left: st?.enabled ? 16 : 2,
                width: 12, height: 12, borderRadius: "50%",
                background: st?.enabled ? "var(--fg-on-accent)" : "var(--fg-strong)", transition: "left 120ms",
              }} />
            </label>
          </div>
        );
      })}
    </div>
  );
}

function ProviderDetailPanel({
  provider: p, ps,
  onSaveSettings, onTestConnection,
  onAddModel, onDeleteModel, t: translate,
}: {
  provider: KnownProvider;
  ps: ProviderStateLike;
  onSaveSettings: (api_key: string, base_url: string, api_format: "anthropic" | "openai") => Promise<void>;
  onTestConnection: () => Promise<unknown>;
  onAddModel: () => void;
  onDeleteModel: (idx: number) => void;
  t: T;
}) {
  const [apiKey, setApiKey] = useState(ps.api_key || "");
  const [baseUrl, setBaseUrl] = useState(ps.base_url || p.defaultBase);
  const [apiFormat, setApiFormat] = useState<"anthropic" | "openai">(
    (ps.api_format as "anthropic" | "openai") || "openai",
  );
  const [saveState, setSaveState] = useState<{ kind: "idle" } | { kind: "saving" } | { kind: "ok" } | { kind: "err"; msg: string }>({ kind: "idle" });
  const [connState, setConnState] = useState<{ kind: "idle" } | { kind: "testing" } | { kind: "ok" } | { kind: "err"; msg: string }>({ kind: "idle" });

  const lastProvider = useRef(p.id);
  if (lastProvider.current !== p.id) {
    lastProvider.current = p.id;
    setApiKey(ps.api_key || "");
    setBaseUrl(ps.base_url || p.defaultBase);
    setApiFormat((ps.api_format as "anthropic" | "openai") || "openai");
    setSaveState({ kind: "idle" });
    setConnState({ kind: "idle" });
  }

  const isOC = p.id === "opencode-zen" || p.id === "opencode-go";
  const isFreeProvider = isOC;

  async function handleSave() {
    setSaveState({ kind: "saving" });
    let k = apiKey.trim();
    if (!k && p.defaultApiKey) k = p.defaultApiKey;
    const u = baseUrl.trim() || p.defaultBase;
    try {
      await onSaveSettings(k, u, apiFormat);
      setApiKey(k); setBaseUrl(u);
      setSaveState({ kind: "ok" });
    } catch (e) {
      setSaveState({ kind: "err", msg: e instanceof Error ? e.message : String(e) });
    }
  }

  async function handleTest() {
    setConnState({ kind: "testing" });
    try {
      await onTestConnection();
      setConnState({ kind: "ok" });
    } catch (e) {
      setConnState({ kind: "err", msg: e instanceof Error ? e.message : String(e) });
    }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={cardStyle}>
        <div style={{
          display: "flex", alignItems: "center", gap: 8, marginBottom: 16,
        }}>
          <span style={{ fontSize: 16 }}>{p.icon}</span>
          <strong style={{ fontSize: 14 }}>{translate("model.settings.title", { name: p.name })}</strong>
          <span style={{
            marginLeft: "auto", fontSize: 13,
            padding: "2px 8px", borderRadius: 10,
            background: ps.enabled ? "var(--success-soft)" : "var(--panel-2)",
            color: ps.enabled ? "var(--success)" : "var(--muted)",
            border: `1px solid ${ps.enabled ? "color-mix(in srgb, var(--success) 30%, transparent)" : "var(--border)"}`,
          }}>{translate(ps.enabled ? "model.state.enabled" : "model.state.disabled")}</span>
        </div>

        {saveState.kind === "err" && <div className="alert alert-error" style={{ marginBottom: 10 }}>{saveState.msg}</div>}
        {saveState.kind === "ok"  && <div className="alert alert-success" style={{ marginBottom: 10 }}>{translate("model.save.ok")}</div>}

        <label style={labelStyle}>
          {translate("model.apikey.label")}
          {isOC && (
            <a href="https://opencode.ai/auth" target="_blank" rel="noopener"
               style={{ fontSize: 13, fontWeight: 400, color: "var(--accent)",
                        textDecoration: "none", marginLeft: 6 }}>
              {translate("model.get_key")}
            </a>
          )}
        </label>
        <input
          type="password" value={apiKey} onChange={e => setApiKey(e.target.value)}
          autoComplete="new-password"
          placeholder={isOC ? translate("model.apikey.free") : translate("model.apikey.placeholder")}
          style={inputStyle}
        />

        <label style={{ ...labelStyle, marginTop: 12 }}>{translate("model.baseurl.label")}</label>
        <input
          type="url" value={baseUrl} onChange={e => setBaseUrl(e.target.value)}
          placeholder={p.defaultBase}
          style={inputStyle}
        />
        {(() => {
          // Format-specific URL validation. Anthropic SDK auto-appends
          // /v1/messages, so a base_url ending in /v1 is wrong (would
          // hit /v1/v1/messages → 404). Show an inline warning so the
          // user understands why "测试连接" might fail and can fix the
          // URL themselves before saving.
          const u = baseUrl.trim().replace(/\/+$/, "");
          if (apiFormat === "anthropic" && /\/v1$/.test(u)) {
            return (
              <p style={{ ...hintStyle, color: "var(--danger)", margin: "6px 0 0" }}>
                {translate("model.baseurl.warn_anthropic_v1")}
              </p>
            );
          }
          if (apiFormat === "openai" && u && !/\/v\d+$/.test(u) && !/\/(api|chat|messages)\b/.test(u)) {
            return (
              <p style={{ ...hintStyle, color: "var(--warning)", margin: "6px 0 0" }}>
                {translate("model.baseurl.warn_openai_no_v1")}
              </p>
            );
          }
          return null;
        })()}

        <label style={{ ...labelStyle, marginTop: 12 }}>{translate("model.format.label")}</label>
        <div style={{ display: "flex", gap: 6 }}>
          <FormatPill active={apiFormat === "anthropic"} onClick={() => {
            setApiFormat("anthropic");
            setBaseUrl(adjustBaseUrlForFormat(baseUrl, "anthropic", p));
          }}>{translate("model.format.anthropic")}</FormatPill>
          <FormatPill active={apiFormat === "openai"} onClick={() => {
            setApiFormat("openai");
            setBaseUrl(adjustBaseUrlForFormat(baseUrl, "openai", p));
          }}>{translate("model.format.openai")}</FormatPill>
        </div>
        <p style={{ ...hintStyle, margin: "6px 0 0" }}>
          {translate("model.format.hint")}
        </p>

        <div style={{
          display: "flex", gap: 8, alignItems: "center", marginTop: 16,
        }}>
          <button onClick={handleTest} disabled={connState.kind === "testing"} style={ghostBtn}>
            {connState.kind === "testing" ? translate("model.test.testing") : translate("model.test.label")}
          </button>
          <button onClick={handleSave} disabled={saveState.kind === "saving"} style={primaryBtn}>
            {saveState.kind === "saving" ? translate("model.save.saving") : translate("model.save.label")}
          </button>
          <span style={{
            fontSize: 14,
            color: connState.kind === "ok" ? "var(--success)"
                 : connState.kind === "err" ? "var(--danger)"
                 : "var(--muted)",
          }}>
            {connState.kind === "ok" && translate("model.conn.ok")}
            {connState.kind === "err" && `✗ ${connState.msg}`}
          </span>
        </div>
      </div>

      <div style={cardStyle}>
        <div style={{
          display: "flex", alignItems: "center", justifyContent: "space-between",
          marginBottom: 12,
        }}>
          <strong style={{ fontSize: 14 }}>{translate("model.list.title")}</strong>
          <button onClick={onAddModel} style={ghostBtn}>{translate("model.list.add")}</button>
        </div>
        <div>
          {(ps.models || []).length === 0 ? (
            <p style={{ ...hintStyle, margin: 0 }}>{translate("model.list.empty")}</p>
          ) : (
            (ps.models as ModelEntry[]).map((m, i) => {
              const name = m.name || _autoName(m.id);
              const isFree = isFreeProvider && ZEN_FREE_IDS.has(m.id);
              return (
                <div key={`${m.id}-${i}`} style={{
                  display: "flex", alignItems: "center", gap: 10,
                  padding: "8px 10px", borderBottom: "1px solid var(--border)",
                }}>
                  <span style={{
                    width: 6, height: 6, borderRadius: 3,
                    background: "var(--muted-2)", flexShrink: 0,
                  }} />
                  <span style={{ flex: 1, fontSize: 14 }}>
                    {name}
                    {isFree && (
                      <span style={{
                        marginLeft: 6, fontSize: 13, padding: "1px 6px",
                        borderRadius: 8, background: "var(--success-soft)",
                        color: "var(--success)", border: "1px solid color-mix(in srgb, var(--success) 30%, transparent)",
                      }}>{translate("model.badge.free")}</span>
                    )}
                  </span>
                  <span style={{
                    fontFamily: "ui-monospace, monospace", fontSize: 13,
                    color: "var(--muted)", background: "var(--bg)",
                    padding: "2px 8px", borderRadius: 4,
                  }}>{m.id}</span>
                  <button
                    onClick={() => onDeleteModel(i)}
                    title={translate("model.delete")}
                    style={{
                      background: "none", border: "none",
                      color: "var(--muted)", cursor: "pointer",
                      fontSize: 14, padding: 4,
                    }}>🗑</button>
                </div>
              );
            })
          )}
        </div>
      </div>
    </div>
  );
}

type ProviderStateLike = {
  enabled: boolean;
  api_key: string;
  base_url: string;
  api_format: string;
  models: ModelEntry[];
};

function FormatPill({
  active, onClick, children,
}: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button type="button" onClick={onClick}
            style={{
              padding: "6px 14px", fontSize: 14, borderRadius: 7,
              background: active ? "var(--accent)" : "var(--panel-2)",
              color: active ? "var(--fg-on-accent)" : "var(--muted)",
              border: `1px solid ${active ? "var(--accent)" : "var(--border)"}`,
              cursor: "pointer",
            }}>{children}</button>
  );
}

const cardStyle: React.CSSProperties = {
  background: "var(--panel)", border: "1px solid var(--border)",
  borderRadius: 10, padding: "16px 18px",
};
const hintStyle: React.CSSProperties = {
  fontSize: 14, color: "var(--muted)", margin: "0 0 10px",
};
const labelStyle: React.CSSProperties = {
  fontSize: 14, color: "var(--muted)", display: "block", marginBottom: 4,
};
const inputStyle: React.CSSProperties = {
  width: "100%", padding: "8px 10px", fontSize: 14,
  background: "var(--bg)", border: "1px solid var(--border)",
  borderRadius: 7, color: "inherit",
};
const ghostBtn: React.CSSProperties = {
  padding: "6px 12px", fontSize: 14,
  background: "transparent", border: "1px solid var(--border)",
  borderRadius: 6, color: "var(--muted)", cursor: "pointer",
};
const primaryBtn: React.CSSProperties = {
  padding: "6px 14px", fontSize: 14,
  background: "var(--accent)", color: "var(--fg-on-accent)",
  border: "none", borderRadius: 6, cursor: "pointer",
};
