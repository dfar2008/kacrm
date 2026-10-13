/**
 * Shared time-range state for the Agent Pool family of pages.
 *
 * Agent Pool (`/agents`) and SpecialistDetail (`/agents/specialist/:name`)
 * both need a lookback window, and it's jarring when clicking into a
 * specialist resets the window back to 30 天. localStorage as the
 * source of truth:
 *   - the selector on either page writes to this key
 *   - both pages subscribe via `useAgentRange` so a change on one
 *     reflects immediately on the other (within one tab), and the
 *     choice survives navigation / reload.
 *
 * Router-query-string isn't an option because the minimal router in
 * this app strips `?...` from `path` (see `lib/router.tsx readPath`).
 */
import { useEffect, useState } from "react";
import type { DictKey } from "@/lib/i18n";

export type AgentRangeKey = "today" | "7d" | "30d";

export const AGENT_RANGE_STORAGE_KEY = "hermes.agent.range";
export const AGENT_RANGE_EVENT = "hermes:agent-range-change";

export const AGENT_RANGE_DAYS: Record<AgentRangeKey, number> = {
  today: 1,
  "7d": 7,
  "30d": 30,
};

/**
 * Translation keys for range buttons. Components resolve these through
 * ``useT()`` so the selector re-renders on locale change. The legacy
 * ``AGENT_RANGE_LABELS`` export now returns the i18n key string; call
 * sites in older components that don't yet use ``useT()`` will at worst
 * render the key literal (which surfaces the miss loudly instead of
 * silently showing a wrong-language string).
 */
export const AGENT_RANGE_LABEL_KEYS: Record<AgentRangeKey, DictKey> = {
  today: "agents.range.today",
  "7d": "agents.range.7d",
  "30d": "agents.range.30d",
};

/** @deprecated Use ``AGENT_RANGE_LABEL_KEYS`` with ``useT()`` instead. */
export const AGENT_RANGE_LABELS: Record<AgentRangeKey, DictKey> = AGENT_RANGE_LABEL_KEYS;

function readRaw(): AgentRangeKey {
  if (typeof window === "undefined") return "7d";
  const v = window.localStorage.getItem(AGENT_RANGE_STORAGE_KEY);
  if (v === "today" || v === "7d" || v === "30d") return v;
  return "7d";
}

export function useAgentRange(): [AgentRangeKey, (v: AgentRangeKey) => void] {
  const [range, setRangeState] = useState<AgentRangeKey>(readRaw);

  useEffect(() => {
    const sync = () => setRangeState(readRaw());
    window.addEventListener(AGENT_RANGE_EVENT, sync);
    // storage event fires in OTHER tabs; not needed here but cheap.
    window.addEventListener("storage", sync);
    return () => {
      window.removeEventListener(AGENT_RANGE_EVENT, sync);
      window.removeEventListener("storage", sync);
    };
  }, []);

  const setRange = (v: AgentRangeKey) => {
    window.localStorage.setItem(AGENT_RANGE_STORAGE_KEY, v);
    window.dispatchEvent(new Event(AGENT_RANGE_EVENT));
    setRangeState(v);
  };
  return [range, setRange];
}
