/**
 * Hook + storage helpers for the chat-box ``ModelPicker``.
 *
 * Single-source-of-truth: the backend's ``GET /api/me/active-models``
 * already parses the user's profile ``providers_json`` and returns a
 * flat list of enabled models plus the profile default. We cache that
 * shape at module scope so all four chat boxes share one fetch per
 * page load.
 *
 * ``persistKey`` is per-surface ("chatbox" / "copilot" / "decisions" /
 * "dashboards" / "schema_assistant") — surfaces remember their own last-picked
 * model in ``localStorage`` so a sales rep who likes ``gpt-4o-mini`` for chat
 * but ``claude-3.5-sonnet`` for analytics doesn't have to re-pick
 * every page load.
 */
import { useEffect, useState } from "react";

import { getActiveModels, type ActiveModelsResponse } from "@/lib/api";

const LS_PREFIX = "hermes.chat.model.";

/** Surfaces that own a chat box with a model picker. */
export type PickerSurface = "chatbox" | "copilot" | "decisions" | "dashboards" | "schema_assistant";

let _cache: ActiveModelsResponse | null = null;
let _inflight: Promise<ActiveModelsResponse> | null = null;
let _listeners: Set<() => void> = new Set();

async function _fetchOnce(): Promise<ActiveModelsResponse> {
  if (_cache) return _cache;
  if (_inflight) return _inflight;
  _inflight = getActiveModels()
    .then(r => { _cache = r; return r; })
    .finally(() => { _inflight = null; });
  return _inflight;
}

/** Force a re-fetch — call after the user edits profile settings. */
export function invalidateActiveModels(): void {
  _cache = null;
  _inflight = null;
  for (const cb of _listeners) cb();
}

/** Read & write the per-surface last-picked model id. */
function _readLastPicked(persistKey: PickerSurface): string | null {
  try { return window.localStorage.getItem(LS_PREFIX + persistKey); }
  catch { return null; }
}
function _writeLastPicked(persistKey: PickerSurface, id: string | null) {
  try {
    if (id == null) window.localStorage.removeItem(LS_PREFIX + persistKey);
    else window.localStorage.setItem(LS_PREFIX + persistKey, id);
  } catch { /* private mode etc.; non-fatal */ }
}

export interface UseActiveModelsResult {
  /** All profile-active models. Empty array when the user hasn't
   *  configured any. */
  models: ActiveModelsResponse["models"];
  /** Profile-default model id, or ``null`` when none configured. */
  defaultId: string | null;
  /** ``true`` once the user has at least one provider+model enabled
   *  on profile. Used to gate the send button. */
  configured: boolean;
  /** Currently-selected model id for this surface (initially the
   *  last-picked, fall back to ``defaultId``). ``null`` when the
   *  user has nothing configured. */
  selected: string | null;
  /** Setter. Persists the choice in ``localStorage`` so the next
   *  page load remembers it (per-surface). */
  select: (id: string) => void;
  loading: boolean;
  error: string | null;
}

/**
 * @param persistKey  surface key — distinct ``localStorage`` namespace per box
 */
export function useActiveModels(persistKey: PickerSurface): UseActiveModelsResult {
  const [resp, setResp] = useState<ActiveModelsResponse | null>(_cache);
  const [selected, setSelectedState] = useState<string | null>(null);
  const [loading, setLoading] = useState<boolean>(_cache == null);
  const [error, setError] = useState<string | null>(null);

  // Sync to module-level cache on mount + subscribe to invalidations.
  useEffect(() => {
    let alive = true;
    const refresh = () => {
      setLoading(true); setError(null);
      _fetchOnce()
        .then(r => { if (alive) { setResp(r); setLoading(false); } })
        .catch(e => {
          if (!alive) return;
          setError(e?.message || "failed to load models");
          setLoading(false);
        });
    };
    refresh();
    _listeners.add(refresh);
    return () => { alive = false; _listeners.delete(refresh); };
  }, []);

  // Pick the initial ``selected``: last-picked if still active, else default.
  useEffect(() => {
    if (!resp) return;
    const ids = new Set(resp.models.map(m => m.id));
    const last = _readLastPicked(persistKey);
    const initial =
      last && ids.has(last)        ? last
      : resp.default_id && ids.has(resp.default_id) ? resp.default_id
      : resp.models[0]?.id          ?? null;
    setSelectedState(initial);
    if (last && initial !== last) _writeLastPicked(persistKey, initial);
  }, [resp, persistKey]);

  const select = (id: string) => {
    setSelectedState(id);
    _writeLastPicked(persistKey, id);
  };

  return {
    models: resp?.models ?? [],
    defaultId: resp?.default_id ?? null,
    configured: resp?.configured ?? false,
    selected,
    select,
    loading,
    error,
  };
}
