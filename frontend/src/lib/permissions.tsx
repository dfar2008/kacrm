import {
  createContext, useCallback, useContext, useEffect, useMemo, useState,
  type ReactNode,
} from "react";
import { getMyPermissions, type MyPermissions } from "@/lib/api";
import { useCurrentUser } from "@/lib/currentUser";

/**
 * Permissions context.
 *
 * Fetches ``/api/me/permissions`` once the authed tree mounts and exposes:
 *
 * * ``can(module, action)`` — boolean gate. Admin short-circuits to true.
 * * ``grants`` — flat ``Set<"module.action">``; handy for list-membership
 *   checks without a function call.
 * * ``data`` — the full response (role badge, module metadata).
 *
 * Re-fetch triggered on login via :func:`refresh` from parent CurrentUser.
 */
interface Ctx {
  data: MyPermissions | null;
  loading: boolean;
  error: string | null;
  grants: Set<string>;
  can: (moduleCode: string, action: string) => boolean;
  refresh: () => Promise<void>;
}

const PermCtx = createContext<Ctx | null>(null);

export function PermissionsProvider({ children }: { children: ReactNode }) {
  const { user } = useCurrentUser();
  const [data, setData] = useState<MyPermissions | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!user) { setData(null); return; }
    setLoading(true);
    try {
      const p = await getMyPermissions();
      setData(p);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [user]);

  useEffect(() => { void refresh(); }, [refresh]);

  const grants = useMemo(
    () => new Set(data?.grants ?? []),
    [data],
  );

  const can = useCallback(
    (moduleCode: string, action: string) => {
      if (!data) return false;
      if (data.is_admin) return true;
      return grants.has(`${moduleCode}.${action}`);
    },
    [data, grants],
  );

  const value: Ctx = { data, loading, error, grants, can, refresh };
  return <PermCtx.Provider value={value}>{children}</PermCtx.Provider>;
}

export function usePermissions(): Ctx {
  const v = useContext(PermCtx);
  if (!v) throw new Error("usePermissions must be used inside <PermissionsProvider>");
  return v;
}

/** Convenience: gate an element by (module, action). */
export function Can({
  module: moduleCode, action, fallback = null, children,
}: {
  module: string;
  action: string;
  fallback?: ReactNode;
  children: ReactNode;
}) {
  const { can } = usePermissions();
  return <>{can(moduleCode, action) ? children : fallback}</>;
}
