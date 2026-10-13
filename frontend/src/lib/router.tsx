/**
 * Minimal hash-free history router.
 *
 * The backend serves the SPA at /app/* (any path). We translate the
 * part AFTER /app/ into a logical route. No external dependency.
 */
import {
  createContext, useCallback, useContext, useEffect, useState,
  type ReactNode,
} from "react";

const BASE = "/app";

function readPath(): string {
  const p = window.location.pathname;
  if (p === BASE || p === BASE + "/") return "/";
  if (p.startsWith(BASE + "/")) return p.slice(BASE.length);
  return "/";
}

interface RouterCtx {
  path: string;
  navigate: (to: string, replace?: boolean) => void;
}
const Ctx = createContext<RouterCtx | null>(null);

export function RouterProvider({ children }: { children: ReactNode }) {
  const [path, setPath] = useState<string>(readPath());

  useEffect(() => {
    const onPop = () => setPath(readPath());
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  const navigate = useCallback((to: string, replace = false) => {
    const full = BASE + (to.startsWith("/") ? to : "/" + to);
    if (replace) window.history.replaceState(null, "", full);
    else window.history.pushState(null, "", full);
    setPath(readPath());
  }, []);

  return <Ctx.Provider value={{ path, navigate }}>{children}</Ctx.Provider>;
}

export function useRouter(): RouterCtx {
  const c = useContext(Ctx);
  if (!c) throw new Error("useRouter outside RouterProvider");
  return c;
}

export function Link(
  { to, children, ...rest }: { to: string; children: ReactNode } & React.HTMLAttributes<HTMLAnchorElement>,
) {
  const { navigate } = useRouter();
  return (
    <a
      href={BASE + (to.startsWith("/") ? to : "/" + to)}
      onClick={e => {
        if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
        e.preventDefault();
        navigate(to);
      }}
      {...rest}
    >
      {children}
    </a>
  );
}

/** Match `/foo/:id/bar` against `path`. Returns params or null. */
export function match(
  pattern: string, path: string,
): Record<string, string> | null {
  const pp = pattern.split("/").filter(Boolean);
  const ap = path.split("/").filter(Boolean);
  if (pp.length !== ap.length) return null;
  const out: Record<string, string> = {};
  for (let i = 0; i < pp.length; i++) {
    if (pp[i].startsWith(":")) out[pp[i].slice(1)] = decodeURIComponent(ap[i]);
    else if (pp[i] !== ap[i]) return null;
  }
  return out;
}
