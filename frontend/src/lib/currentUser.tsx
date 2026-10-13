import {
  createContext, useCallback, useContext, useEffect, useState,
  type ReactNode,
} from "react";
import { getMe, type CurrentUser } from "@/lib/api";

/**
 * Current-user context.
 *
 * Single source of truth for who's logged in. Hydrated once from
 * `/api/me` when the authed tree mounts; every consumer (AppShell,
 * route guards, profile modal, …) reads from here instead of
 * duplicating fetches or probing stale localStorage flags.
 *
 * Unmounts on logout, so a fresh login always triggers a new fetch.
 */
export interface CurrentUserState {
  user: CurrentUser | null;
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
}

const Ctx = createContext<CurrentUserState | null>(null);

export function CurrentUserProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const u = await getMe();
      setUser(u);
      setError(null);
    } catch (e) {
      setUser(null);
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  return <Ctx.Provider value={{ user, loading, error, refresh }}>{children}</Ctx.Provider>;
}

export function useCurrentUser(): CurrentUserState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useCurrentUser must be used inside <CurrentUserProvider>");
  return v;
}

export function useIsAdmin(): boolean {
  return !!useCurrentUser().user?.is_admin;
}
