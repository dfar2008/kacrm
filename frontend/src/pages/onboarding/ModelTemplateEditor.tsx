import { useEffect, useMemo, useRef, useState } from "react";
import { toast } from "@/lib/toast";
import { useT, type T } from "@/lib/i18n";
import {
  KNOWN_PROVIDERS, ZEN_FREE_IDS, _autoName, adjustBaseUrlForFormat, hydratePcfg,
  type ModelPcfg, type ModelEntry, type KnownProvider, type ProviderState,
} from "../agent-detail/modelPresets";
import { AddModelModal } from "../agent-detail/AddModelModal";

/**
 * 新用户模板 — 模型编辑器.
 *
 * 复制 agent-detail/ModelTab 的 provider 选择器视觉(左 provider 列表 +
 * 右 api_key / base_url / 格式 / 模型列表),但与 per-profile 的 ModelTab
 * 有三点不同:
 *   - 只编辑 providers_json. 模板的 legacy model/api_key/base_url 不在此
 *     派生、不在此改动 —— 由 AdminOnboardingTemplate.save() 原值回传.
 *   - 没有「测试连接」—— 模板没有 profile,无法 smoke-test 上游 endpoint.
 *   - 没有 per-provider 保存按钮 —— 所有改动即时回写父组件 state,由顶部
 *     统一的「保存模板」按钮一次性落盘.
 */

interface Props {
  /** 模板的 model.providers_json 字符串(可能为空字符串). */
  value: string;
  /** 序列化后的 providers_json,回写父组件 state. */
  onChange: (json: string) => void;
}

/** 把存储的 providers_json 字符串解析 + hydrate 成完整 ModelPcfg. */
function parseInitial(value: string): ModelPcfg {
  let raw: unknown = null;
  if (value && value.trim()) {
    try { raw = JSON.parse(value); } catch { raw = null; }
  }
  return hydratePcfg(raw);
}

export function ModelTemplateEditor({ value, onChange }: Props) {
  const t = useT();
  // pcfg 仅在挂载时从 value 解析一次. 切换标签会卸载/重挂本组件,届时
  // 用父组件最新的 providers 字符串重新解析 —— 编辑不丢失.
  const [pcfg, setPcfg] = useState<ModelPcfg>(() => parseInitial(value));
  const [selectedId, setSelectedId] = useState<string>(KNOWN_PROVIDERS[0].id);
  const [addOpen, setAddOpen] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  // If hydrate refreshed any curated provider's surface/models, push
  // the new pcfg back to the parent immediately so the template's
  // dirty-check picks it up and Save persists fresh values without
  // requiring the user to manually edit anything. Mirrors the
  // per-profile ModelTab behavior; curated presets (DeepSeek V4,
  // OpenCode Zen) would otherwise stay stale in the saved template
  // even after a successful in-memory hydrate.
  useEffect(() => {
    const fresh = JSON.stringify(pcfg);
    if (fresh !== value) {
      onChange(fresh);
    }
    // Run only on mount: subsequent edits flow through `mutate()`.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /** 应用一次变更:更新本地 pcfg 并把序列化结果回写父组件. */
  function mutate(fn: (p: ModelPcfg) => ModelPcfg) {
    const next = fn(pcfg);
    setPcfg(next);
    onChange(JSON.stringify(next));
  }

  function handleExport() {
    const blob = new Blob([JSON.stringify(pcfg, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "hermes-model-template.json";
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
        toast(t("model.import.invalid"), "error");
        return;
      }
      const next = hydratePcfg(imported);
      setPcfg(next);
      onChange(JSON.stringify(next));
      toast(t("model.import.ok"), "success");
    } catch (err) {
      toast(
        t("model.import.failed", { msg: err instanceof Error ? err.message : String(err) }),
        "error",
      );
    }
  }

  const selected = KNOWN_PROVIDERS.find(p => p.id === selectedId) ?? KNOWN_PROVIDERS[0];
  const ps = pcfg.providers[selected.id];

  return (
    <div style={{ flex: 1, minHeight: 0, overflow: "auto", padding: 16 }}>
      <div style={{
        display: "flex", justifyContent: "flex-end", gap: 8, marginBottom: 12,
      }}>
        <button onClick={handleImportClick} style={ghostBtn}>{t("model.import")}</button>
        <button onClick={handleExport} style={ghostBtn}>{t("model.export")}</button>
        <input ref={fileInputRef} type="file" accept=".json"
               style={{ display: "none" }} onChange={handleImportFile} />
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
          t={t}
        />
        <ProviderDetailPanel
          provider={selected}
          pcfg={pcfg}
          ps={ps}
          onChangeDefaultModel={v => mutate(p => ({ ...p, default_model: v }))}
          onPatchProvider={patch => mutate(p => ({
            ...p,
            providers: {
              ...p.providers,
              [selected.id]: { ...p.providers[selected.id], ...patch },
            },
          }))}
          onAddModel={() => setAddOpen(true)}
          onDeleteModel={idx => mutate(p => {
            const nextModels = (p.providers[selected.id].models || [])
              .filter((_, i) => i !== idx);
            return {
              ...p,
              providers: {
                ...p.providers,
                [selected.id]: { ...p.providers[selected.id], models: nextModels },
              },
            };
          })}
          t={t}
        />
      </div>

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
    </div>
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
  provider: p, pcfg, ps,
  onChangeDefaultModel, onPatchProvider, onAddModel, onDeleteModel,
  t: translate,
}: {
  provider: KnownProvider;
  pcfg: ModelPcfg;
  ps: ProviderState;
  onChangeDefaultModel: (v: string) => void;
  onPatchProvider: (patch: Partial<ProviderState>) => void;
  onAddModel: () => void;
  onDeleteModel: (idx: number) => void;
  t: T;
}) {
  const isOC = p.id === "opencode-zen" || p.id === "opencode-go";

  // 受控:直接读 provider state,改动即时经 onPatchProvider 回写.
  const apiKey = ps.api_key || "";
  const baseUrl = ps.base_url || "";
  const apiFormat: "anthropic" | "openai" = ps.api_format || "openai";

  const modelOptions = useMemo(() => {
    const opts = [{ value: "", label: translate("model.default.auto") }];
    for (const pp of KNOWN_PROVIDERS) {
      const s = pcfg.providers[pp.id];
      if (s && s.enabled) {
        for (const m of s.models || []) {
          opts.push({ value: `${pp.id}::${m.id}`, label: `${pp.name} / ${m.name || m.id}` });
        }
      }
    }
    return opts;
  }, [pcfg, translate]);

  /** 切换 API 格式时同步修正 base_url(共享 adjustBaseUrlForFormat
   *  以保证与 per-agent ModelTab 行为一致). */
  function selectFormat(fmt: "anthropic" | "openai") {
    onPatchProvider({
      api_format: fmt,
      base_url: adjustBaseUrlForFormat(baseUrl, fmt, p),
    });
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      {/* ── Default model ─────────────────────────────────── */}
      <div style={cardStyle}>
        <div style={sectionLabel}>{translate("model.default.title")}</div>
        <p style={hintStyle}>{translate("model.default.hint")}</p>
        <select
          value={pcfg.default_model || ""}
          onChange={e => onChangeDefaultModel(e.target.value)}
          style={{ maxWidth: 320 }}
        >
          {modelOptions.map(o => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </select>
      </div>

      {/* ── Provider settings ─────────────────────────────── */}
      <div style={cardStyle}>
        <div style={{
          display: "flex", alignItems: "center", gap: 8, marginBottom: 16,
        }}>
          <span style={{ fontSize: 16 }}>{p.icon}</span>
          <strong style={{ fontSize: 14 }}>
            {translate("model.settings.title", { name: p.name })}
          </strong>
          <span style={{
            marginLeft: "auto", fontSize: 13,
            padding: "2px 8px", borderRadius: 10,
            background: ps.enabled ? "var(--success-soft)" : "var(--panel-2)",
            color: ps.enabled ? "var(--success)" : "var(--muted)",
            border: `1px solid ${ps.enabled ? "color-mix(in srgb, var(--success) 30%, transparent)" : "var(--border)"}`,
          }}>{translate(ps.enabled ? "model.state.enabled" : "model.state.disabled")}</span>
        </div>

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
          type="password" value={apiKey} autoComplete="new-password"
          onChange={e => onPatchProvider({ api_key: e.target.value })}
          placeholder={isOC ? translate("model.apikey.free") : translate("model.apikey.placeholder")}
          style={inputStyle}
        />

        <label style={{ ...labelStyle, marginTop: 12 }}>{translate("model.baseurl.label")}</label>
        <input
          type="url" value={baseUrl}
          onChange={e => onPatchProvider({ base_url: e.target.value })}
          placeholder={p.defaultBase}
          style={inputStyle}
        />
        {(() => {
          // 格式相关的 URL 校验提示(与 ModelTab 一致).
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
          <FormatPill active={apiFormat === "anthropic"} onClick={() => selectFormat("anthropic")}>
            {translate("model.format.anthropic")}
          </FormatPill>
          <FormatPill active={apiFormat === "openai"} onClick={() => selectFormat("openai")}>
            {translate("model.format.openai")}
          </FormatPill>
        </div>
        <p style={{ ...hintStyle, margin: "6px 0 0" }}>
          {translate("model.format.hint")}
        </p>
      </div>

      {/* ── Model list ────────────────────────────────────── */}
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
              const isFree = isOC && ZEN_FREE_IDS.has(m.id);
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
const sectionLabel: React.CSSProperties = {
  fontSize: 13, color: "var(--muted)",
  textTransform: "uppercase", letterSpacing: 1,
  marginBottom: 8,
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
