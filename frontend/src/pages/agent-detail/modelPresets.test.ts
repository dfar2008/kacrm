import { hydratePcfg, KNOWN_PROVIDERS } from "./modelPresets";

test("official cloud presets replace stale stored catalogs without changing credentials", () => {
  for (const preset of KNOWN_PROVIDERS.filter(p => !["ollama", "omlx"].includes(p.id))) {
    const raw = { providers: { [preset.id]: { enabled: true, api_key: "private-key",
      base_url: preset.defaultBase, api_format: preset.defaultFormat,
      models: [{ id: "retired-model", name: "Retired" }] } } };
    const fresh = hydratePcfg(raw).providers[preset.id];
    expect(fresh.models).toEqual(preset.defaultModels);
    expect(fresh.api_key).toBe("private-key");
    expect(fresh.enabled).toBe(true);
    expect(raw.providers[preset.id].models[0].id).toBe("retired-model");
  }
});

test("default, auxiliary and MoA selections migrate together to available models", () => {
  const preset = KNOWN_PROVIDERS.find(p => p.id === "moonshot")!;
  const old = "moonshot::retired-model";
  const next = hydratePcfg({ default_model: old,
    providers: { moonshot: { enabled: true, base_url: preset.defaultBase,
      models: [{ id: "retired-model", name: "Retired" }] } },
    auxiliary: { unified: old, overrides: { compression: old } },
    moa: { aggregator: old, reference_models: [old, old] } });
  const expected = `moonshot::${preset.defaultModels[0].id}`;
  expect(next.default_model).toBe(expected);
  expect(next.auxiliary?.unified).toBe(expected);
  expect(next.auxiliary?.overrides?.compression).toBe(expected);
  expect(next.moa?.aggregator).toBe(expected);
  expect(next.moa?.reference_models).toEqual([expected]);
});

test("private endpoints and locally installed models keep their own catalogs", () => {
  const custom = { enabled: true, api_key: "local-key", base_url: "https://private.example/v1",
    api_format: "openai" as const, models: [{ id: "my-local-model", name: "Local" }] };
  const fresh = hydratePcfg({ providers: { deepseek: custom, ollama: custom, private: custom } });
  for (const id of ["deepseek", "ollama", "private"]) {
    expect(fresh.providers[id].base_url).toBe(custom.base_url);
    expect(fresh.providers[id].models).toEqual(custom.models);
  }
});

test("disabled Ollama drops the old seeded examples, but keeps actual custom lists", () => {
  const legacy = ["llama3.2", "mistral", "codellama"].map(id => ({ id, name: id }));
  const raw = { providers: { ollama: { enabled: false, base_url: "http://localhost:11434/v1", models: legacy } } };
  expect(hydratePcfg(raw).providers.ollama.models).toEqual([]);
  raw.providers.ollama.models = [{ id: "installed-local-model", name: "Installed" }];
  expect(hydratePcfg(raw).providers.ollama.models).toEqual(raw.providers.ollama.models);
});
