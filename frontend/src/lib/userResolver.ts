/**
 * Tiny in-memory user id → display name cache, loaded lazily from
 * /api/insights/team_members. Used in confirm dialogs that previously
 * surfaced raw numeric IDs (e.g. "user#2431") to the user.
 *
 * The endpoint scopes naturally: admin sees all active users, manager
 * sees their team, regular user sees themselves — so the cache only
 * contains names the caller is allowed to know. Anything outside that
 * set falls back to "user#<id>".
 */
import { useEffect, useState } from "react";
import { fetchTeamMembers } from "./api";

let cache: Map<number, string> | null = null;
let pending: Promise<void> | null = null;

function loadCache(): Promise<void> {
  if (cache) return Promise.resolve();
  if (pending) return pending;
  pending = fetchTeamMembers()
    .then((r) => {
      cache = new Map(
        (r.members || [])
          .filter((m) => m && m.id != null)
          .map((m) => [Number(m.id), (m.name || "").trim() || `user#${m.id}`]),
      );
    })
    .catch(() => {
      cache = new Map();
    })
    .finally(() => {
      pending = null;
    });
  return pending;
}

/** Synchronously resolve a user id to a display name. Returns a
 *  `user#<id>` fallback if the cache hasn't loaded yet or the id is
 *  outside the caller's visible team. Always returns a non-empty
 *  string so callers can drop it straight into a template. */
export function resolveUserName(
  id: number | string | undefined | null,
): string {
  if (id == null || id === "") return "?";
  const num = typeof id === "number" ? id : parseInt(String(id), 10);
  if (Number.isNaN(num)) return String(id);
  const hit = cache?.get(num);
  return hit || `user#${num}`;
}

/** Hook variant: triggers a one-time fetch on mount and forces a
 *  re-render once the cache lands so any names rendered during the
 *  initial pass get upgraded. Returns the same sync resolver. */
export function useUserResolver(): typeof resolveUserName {
  const [, setTick] = useState(0);
  useEffect(() => {
    if (cache) return;
    let alive = true;
    void loadCache().then(() => {
      if (alive) setTick((n) => n + 1);
    });
    return () => {
      alive = false;
    };
  }, []);
  return resolveUserName;
}
