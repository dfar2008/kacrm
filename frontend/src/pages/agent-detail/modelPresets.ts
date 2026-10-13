// Official model catalogs reviewed on 2026-10-12.
// Sources: DeepSeek API docs, Kimi API docs, Alibaba Model Studio, Z.ai,
// MiniMax API docs, LongCat API docs, OpenAI model docs, Claude model docs,
// and OpenCode /zen/v1/models + /zen/go/v1/models.

export type ModelEntry = { id: string; name: string };

export interface KnownProvider {
  id: string;
  name: string;
  icon: string;
  defaultBase: string;
  defaultFormat: "anthropic" | "openai";
  defaultApiKey?: string;
  defaultModels: ModelEntry[];
  // Official presets refresh stored model lists on the provider's own host.
  // Keys, enabled state, protocol choice and private endpoints are preserved.
  curated?: boolean;
  /**
   * Per-format URL *path suffixes* for providers that expose both an
   * Anthropic-Messages surface and an OpenAI chat-completions surface
   * under the same host but at different paths. Used by
   * ``adjustBaseUrlForFormat`` to swap suffixes when the user toggles
   * the API format radio.
   *
   * Path-suffix-based (not absolute URL) so self-hosted proxies are
   * preserved — e.g. ``https://my-proxy.example.com/anthropic`` becomes
   * ``https://my-proxy.example.com/v1`` instead of being replaced with
   * the upstream provider's URL.
   *
   * Examples:
   *   MiniMax-CN:  { openai: "/v1",            anthropic: "/anthropic"    }
   *   Zhipu/ZAI:   { openai: "/api/paas/v4",   anthropic: "/api/anthropic" }
   */
  formatPathSuffixes?: { openai: string; anthropic: string };
}

export interface ProviderState {
  enabled: boolean;
  api_key: string;
  base_url: string;
  api_format: "anthropic" | "openai";
  models: ModelEntry[];
}

export const AUX_TASKS = ["compression", "curator", "title_generation"] as const;
export type AuxTask = (typeof AUX_TASKS)[number];

export interface AuxiliaryConfig {
  unified?: string | null;                       // "provider::model" 或 null=自动
  overrides?: Partial<Record<AuxTask, string | null>>;
}
export interface MoaConfig {
  enabled?: boolean;                             // 缺省视为 true
  reference_models?: string[];                   // "provider::model"[]，空=自动
  aggregator?: string | null;                    // null=自动
}

// 后端派生的生效值（只读，来自 GET 的 derived）
export interface DerivedSlot { provider: string | null; model: string | null; source: string; }
export interface DerivedRoles {
  auxiliary: Record<AuxTask, DerivedSlot>;
  moa: { reference_models: { provider: string; model: string }[];
         aggregator: { provider: string; model: string } } | null;
}

export interface ModelPcfg {
  default_model: string;
  providers: Record<string, ProviderState>;
  auxiliary?: AuxiliaryConfig;
  moa?: MoaConfig;
}

export function _autoName(id: string): string {
  // Convert model-id → "Model Id" (matches app.js)
  const up = new Set(["gpt", "glm", "mlx", "api", "ai", "url", "sdk", "llm", "vllm"]);
  return (id || "")
    .split(/[-_.]/)
    .map(w => (up.has(w.toLowerCase())
      ? w.toUpperCase()
      : w.charAt(0).toUpperCase() + w.slice(1)))
    .join(" ");
}

export function _m(id: string, name?: string): ModelEntry {
  return { id, name: name || _autoName(id) };
}

export const KNOWN_PROVIDERS: KnownProvider[] = [
  { id: "deepseek", name: "DeepSeek", icon: "🌊",
    // DeepSeek 同一 host 下分两个表面(参考官方 base_url 表):
    //   OpenAI:  https://api.deepseek.com           (无 path 后缀)
    //   Anthropic: https://api.deepseek.com/anthropic
    // OpenAI SDK 自动追加 `/chat/completions`,所以根 host 就是正确 base。
    // 官方最新型号覆盖存量目录，避免继续显示已被替代的型号。
    defaultBase: "https://api.deepseek.com", defaultFormat: "openai",
    formatPathSuffixes: { openai: "", anthropic: "/anthropic" },
    curated: true,
    defaultModels: [
      _m("deepseek-v4-pro", "DeepSeek V4 Pro"),
      _m("deepseek-flash", "DeepSeek V4.1 Flash"),
    ] },
  { id: "moonshot", name: "Moonshot", icon: "🌙",
    defaultBase: "https://api.moonshot.cn/v1", defaultFormat: "openai",
    curated: true, defaultModels: [ _m("kimi-k3","Kimi K3") ] },
  { id: "qwen", name: "Qwen", icon: "☁",
    defaultBase: "https://dashscope.aliyuncs.com/compatible-mode/v1", defaultFormat: "openai",
    curated: true, defaultModels: [ _m("qwen3.8-max","Qwen3.8 Max"), _m("qwen3.8-flash","Qwen3.8 Flash"), _m("qwen3.7-plus","Qwen3.7 Plus") ] },
  { id: "zhipu", name: "Zhipu", icon: "⚡",
    // Zhipu (open.bigmodel.cn) exposes both surfaces under the same host:
    //   OpenAI:    /api/paas/v4
    //   Anthropic: /api/anthropic
    // hermes' auxiliary client also special-cases this swap at
    // auxiliary_client.py:496, so the SPA needs to know about it to keep
    // the URL consistent when the user toggles the format radio.
    defaultBase: "https://open.bigmodel.cn/api/paas/v4", defaultFormat: "openai",
    formatPathSuffixes: { openai: "/api/paas/v4", anthropic: "/api/anthropic" },
    curated: true, defaultModels: [ _m("glm-5.3","GLM-5.3"), _m("glm-5.3-flash","GLM-5.3 Flash") ] },
  { id: "minimax", name: "MiniMax", icon: "🎯",
    // 国内 MiniMax: /anthropic 路径下走 Anthropic Messages 格式。
    // 注意 base_url 不带 /v1 — Anthropic SDK 会自动追加 /v1/messages，
    // 写成 /anthropic/v1 会变成 /anthropic/v1/v1/messages → 404。
    // 同一 host 也提供 /v1 OpenAI 表面,format 切换时 suffix 互换。
    defaultBase: "https://api.minimaxi.com/anthropic", defaultFormat: "anthropic",
    formatPathSuffixes: { openai: "/v1", anthropic: "/anthropic" },
    curated: true, defaultModels: [
      _m("MiniMax-M3", "MiniMax M3"),
    ] },
  { id: "longcat", name: "LongCat", icon: "🐱",
    // LongCat(美团)同一 host 下分两个表面(参考官方文档):
    //   OpenAI:    https://api.longcat.chat/openai/v1   (OpenAI SDK 追加 /chat/completions)
    //   Anthropic: https://api.longcat.chat/anthropic    (Anthropic SDK 追加 /v1/messages,故不带 /v1)
    // format 切换时 suffix 互换,自建代理的 host 前缀保留。
    defaultBase: "https://api.longcat.chat/openai/v1", defaultFormat: "openai",
    formatPathSuffixes: { openai: "/openai/v1", anthropic: "/anthropic" },
    curated: true, defaultModels: [
      _m("LongCat-2.5-Preview", "LongCat 2.5 Preview"),
    ] },
  { id: "ollama", name: "Ollama", icon: "🦙",
    defaultBase: "http://localhost:11434/v1", defaultFormat: "openai",
    defaultModels: [] },
  { id: "openai", name: "OpenAI", icon: "◎",
    defaultBase: "https://api.openai.com/v1", defaultFormat: "openai",
    curated: true, defaultModels: [ _m("gpt-6.1-sol","GPT-6.1 Sol"), _m("gpt-6-astra","GPT-6 Astra"), _m("gpt-6-luna","GPT-6 Luna") ] },
  { id: "anthropic", name: "Anthropic", icon: "Ⅻ",
    defaultBase: "https://api.anthropic.com", defaultFormat: "anthropic",
    curated: true, defaultModels: [ _m("claude-opus-5-5","Claude Opus 5.5"), _m("claude-sonnet-5-5","Claude Sonnet 5.5"), _m("claude-haiku-5-5","Claude Haiku 5.5"), _m("claude-fable-5-1","Claude Fable 5.1") ] },
  { id: "opencode-zen", name: "OpenCode Zen", icon: "✦",
    defaultBase: "https://opencode.ai/zen/v1", defaultFormat: "openai", defaultApiKey: "public",
    curated: true,
    defaultModels: [
      _m("big-pickle"),
      _m("ling-3.1-flash-free", "Ling 3.1 Flash Free"),
      _m("mimo-v2.6-flash-free", "MiMo V2.6 Flash Free"),
      _m("muse-spark-1.3-contributor-free", "Muse Spark 1.3 Contributor Free"),
      _m("nemotron-3.5-lightning-free", "Nemotron 3.5 Lightning Free"),
      _m("jev-1.13-free", "JEV 1.13 Free"),
      _m("step-5-preview-free", "Step 5 Preview Free"),
      _m("longcat-2.5-preview-free", "LongCat 2.5 Preview Free"),
      _m("space-bunny-free", "Space Bunny Free"),
    ] },
  { id: "opencode-go", name: "OpenCode Go", icon: "▶",
    defaultBase: "https://opencode.ai/zen/go/v1", defaultFormat: "openai", defaultApiKey: "public",
    curated: true, defaultModels: [
      _m("minimax-m3", "MiniMax M3"),
      _m("glm-5.3", "GLM-5.3"),
      _m("glm-5.3-flash", "GLM-5.3 Flash"),
      _m("kimi-k3", "Kimi K3"),
      _m("mimo-v2.6-pro", "MiMo V2.6 Pro"),
      _m("mimo-v2.6-flash", "MiMo V2.6 Flash"),
      _m("deepseek-v4-pro", "DeepSeek V4 Pro"),
      _m("deepseek-flash", "DeepSeek V4.1 Flash"),
      _m("qwen3.8-max", "Qwen3.8 Max"),
      _m("qwen3.8-flash", "Qwen3.8 Flash"),
      _m("qwen3.7-plus", "Qwen3.7 Plus"),
      _m("longcat-2.5-preview-free", "LongCat 2.5 Preview Free"),
      _m("gpt-6-luna", "GPT-6 Luna"),
      _m("claude-haiku-5-5", "Claude Haiku 5.5"),
      _m("grok-4.7", "Grok 4.7"),
      _m("muse-spark-1.3-contributor", "Muse Spark 1.3 Contributor"),
      _m("hy4-preview"),
      _m("space-bunny"),
      _m("step-5-preview-free", "Step 5 Preview Free"),
    ] },
  { id: "omlx", name: "oMLX Local", icon: "◈",
    defaultBase: "http://localhost:8080/v1", defaultFormat: "openai", defaultModels: [] },
];

export const ZEN_FREE_IDS = new Set(["big-pickle", "ling-3.1-flash-free", "mimo-v2.6-flash-free", "muse-spark-1.3-contributor-free", "nemotron-3.5-lightning-free", "jev-1.13-free", "step-5-preview-free", "longcat-2.5-preview-free", "space-bunny-free"]);

export const _DEFAULT_PROVIDER_STATE = (): ProviderState => ({
  enabled: false,
  api_key: "",
  base_url: "",
  api_format: "openai",
  models: [],
});

/**
 * Adjust a provider's ``base_url`` when the user toggles the API format
 * radio. Centralises the rewrite rules so the per-agent ModelTab and the
 * onboarding ModelTemplateEditor stay in lock-step.
 *
 * Resolution order:
 *  1. Provider declares ``formatPathSuffixes`` (MiniMax, Zhipu, ...):
 *     if the current URL ends with one format's suffix, swap to the
 *     target's. Path-suffix-based so self-hosted proxies are preserved.
 *  2. Generic ``/v1`` strip/append: handles single-surface providers
 *     (DeepSeek, Moonshot, OpenAI itself) where toggling to Anthropic
 *     just means dropping ``/v1`` so the Anthropic SDK's auto-append of
 *     ``/v1/messages`` doesn't double-up.
 *
 * @param currentBase user-edited base URL (raw, may have trailing slash)
 * @param target the format the user just selected
 * @param provider the active provider preset — read to discover
 *     ``formatPathSuffixes`` and ``defaultBase``
 */
export function adjustBaseUrlForFormat(
  currentBase: string,
  target: "anthropic" | "openai",
  provider: Pick<KnownProvider, "defaultBase" | "formatPathSuffixes">,
): string {
  const u = (currentBase || "").trim().replace(/\/+$/, "");

  // ── Step 1: preset-declared suffix swap (MiniMax, Zhipu, DeepSeek, ...) ──
  const suffixes = provider.formatPathSuffixes;
  if (suffixes) {
    const targetSuffix = suffixes[target];
    const otherSuffix = suffixes[target === "anthropic" ? "openai" : "anthropic"];
    // Empty-suffix handling: one of the two surfaces may live at the
    // bare host (DeepSeek's OpenAI surface is just ``https://api.deepseek.com``).
    // We can't use ``endsWith("")`` for detection — every string ends with
    // the empty string — so the logic explicitly checks the non-empty
    // suffix and treats the empty side as "strip the other suffix".
    if (otherSuffix && u.endsWith(otherSuffix)) {
      // Currently on other surface — strip and append target (may be "").
      return u.slice(0, -otherSuffix.length) + targetSuffix;
    }
    if (targetSuffix && u.endsWith(targetSuffix)) {
      return u;  // already on target surface
    }
    if (!targetSuffix && !otherSuffix) {
      return u;  // both formats share the same URL — no-op (unusual)
    }
    if (!otherSuffix) {
      // Other format lives at the bare host; current URL doesn't end
      // with the target suffix, so append it.
      return u + targetSuffix;
    }
    // URL doesn't match either known suffix — fall through to the
    // generic heuristic so a user with a self-hosted variant still gets
    // a sensible /v1 strip/append rather than silent no-op.
  }

  // ── Step 2: generic /v1 strip/append for single-surface providers ──
  if (target === "anthropic") {
    if (/\/v1$/.test(u)) return u.slice(0, -3);
    return u;
  }
  // target === "openai"
  if (u && !/\/v\d+$/.test(u) && !/\/(api|chat|messages|anthropic)\b/.test(u)) {
    return u + "/v1";
  }
  return u;
}


export function _migrateModels(arr: unknown): ModelEntry[] {
  if (!Array.isArray(arr)) return [];
  return arr.map(m =>
    typeof m === "string" ? _m(m) : (m as ModelEntry));
}

function isPresetEndpoint(base: string, presetBase: string): boolean {
  if (!base) return true;
  try { return new URL(base).origin === new URL(presetBase).origin; }
  catch { return false; }
}

/** Merge stored pcfg with KNOWN_PROVIDERS, filling any missing providers. */
export function hydratePcfg(raw: unknown): ModelPcfg {
  const pcfg: ModelPcfg = {
    default_model: "",
    providers: {},
  };
  if (raw && typeof raw === "object") {
    const r = raw as Partial<ModelPcfg>;
    if (typeof r.default_model === "string") pcfg.default_model = r.default_model;
    if (r.auxiliary && typeof r.auxiliary === "object") pcfg.auxiliary = r.auxiliary;
    if (r.moa && typeof r.moa === "object") pcfg.moa = r.moa;
    if (r.providers && typeof r.providers === "object") {
      pcfg.providers = { ...r.providers } as Record<string, ProviderState>;
    }
  }
  for (const p of KNOWN_PROVIDERS) {
    if (!pcfg.providers[p.id]) {
      pcfg.providers[p.id] = {
        ..._DEFAULT_PROVIDER_STATE(),
        base_url: p.defaultBase,
        api_format: p.defaultFormat,
        models: p.defaultModels.map(m => ({ ...m })),
      };
    } else if (p.curated && isPresetEndpoint(pcfg.providers[p.id].base_url, p.defaultBase)) {
      // Refresh the official catalog, retaining credentials and endpoint settings.
      const stored = pcfg.providers[p.id];
      pcfg.providers[p.id] = {
        ..._DEFAULT_PROVIDER_STATE(),
        ...stored,
        base_url: stored.base_url || p.defaultBase,
        api_format: stored.api_format || p.defaultFormat,
        models: p.defaultModels.map(m => ({ ...m })),
      };
    } else {
      const models = _migrateModels(pcfg.providers[p.id].models);
      // Retire the disabled, auto-seeded examples; these were never a list
      // fetched from the user's Ollama server. Keep installed/custom lists.
      const oldOllamaExamples = p.id === "ollama" && !pcfg.providers[p.id].enabled
        && models.length === 3
        && models.every(m => ["llama3.2", "mistral", "codellama"].includes(m.id));
      pcfg.providers[p.id] = {
        ..._DEFAULT_PROVIDER_STATE(),
        ...pcfg.providers[p.id],
        models: oldOllamaExamples ? [] : models,
      };
    }
  }
  const migrateRef = (ref: string | null | undefined) => {
    if (!ref || !ref.includes("::")) return ref;
    const [providerId, modelId] = ref.split("::");
    const provider = pcfg.providers[providerId];
    const preset = KNOWN_PROVIDERS.find(p => p.id === providerId);
    if (!provider || !preset?.curated || !isPresetEndpoint(provider.base_url, preset.defaultBase)
        || provider.models.some(m => m.id === modelId)) return ref;
    const family = ["opus", "sonnet", "haiku", "fable", "flash", "pro", "max", "plus", "luna", "sol", "astra"]
      .find(token => modelId.toLowerCase().split(/[-_.]/).includes(token));
    const replacement = provider.models.find(m => family && m.id.toLowerCase().split(/[-_.]/).includes(family))
      ?? provider.models[0];
    return replacement ? `${providerId}::${replacement.id}` : "";
  };
  pcfg.default_model = migrateRef(pcfg.default_model) ?? "";
  if (pcfg.auxiliary) {
    pcfg.auxiliary = { ...pcfg.auxiliary, unified: migrateRef(pcfg.auxiliary.unified) };
    if (pcfg.auxiliary.overrides) {
      pcfg.auxiliary.overrides = { ...pcfg.auxiliary.overrides };
      for (const task of AUX_TASKS) {
        pcfg.auxiliary.overrides[task] = migrateRef(pcfg.auxiliary.overrides[task]);
      }
    }
  }
  if (pcfg.moa) {
    pcfg.moa = { ...pcfg.moa, aggregator: migrateRef(pcfg.moa.aggregator),
      reference_models: [...new Set((pcfg.moa.reference_models ?? [])
        .map(ref => migrateRef(ref)).filter((ref): ref is string => !!ref))] };
  }
  return pcfg;
}
