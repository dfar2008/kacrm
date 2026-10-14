import {
  createContext, createElement, useCallback, useContext,
  useEffect, useLayoutEffect, useMemo, useState, type ReactNode,
} from "react";
import { SCHEMES, DEFAULT_SCHEME_ID } from "./schemes";
import {
  MODE_KEY, LEGACY_MODE_KEY, SYSTEM_DARK_QUERY, readModePreference,
  resolveAppearance, systemPrefersDark, subscribeSystemAppearance,
  type ThemeMode, type ResolvedThemeMode,
} from "./appearance";

const THEME_KEY = "kacrm.theme.scheme";
const LEGACY_THEME_KEY = "hermes.theme";
const VALID = new Set(SCHEMES.map(s => s.id));
export type { ThemeMode, ResolvedThemeMode } from "./appearance";

export function readStoredTheme(): string {
  try {
    const id = window.localStorage.getItem(THEME_KEY)
      ?? window.localStorage.getItem(LEGACY_THEME_KEY);
    if (id && VALID.has(id)) return id;
  } catch { /* Storage can be unavailable. */ }
  return DEFAULT_SCHEME_ID;
}
function writeStoredTheme(id: string): void {
  try { window.localStorage.setItem(THEME_KEY, id); } catch { /* optional */ }
}
export const readStoredMode = readModePreference;
function writeStoredMode(mode: ThemeMode): void {
  try { window.localStorage.setItem(MODE_KEY, mode); } catch { /* optional */ }
}

function applyAppearance(theme: string, mode: ThemeMode, resolvedMode: ResolvedThemeMode) {
  const root = document.documentElement;
  root.dataset.theme = theme;
  root.dataset.mode = resolvedMode;
  root.dataset.modePreference = mode;
}

/** Apply persisted/system appearance before React's first paint. */
export function initializeAppearance() {
  const mode = readStoredMode();
  applyAppearance(readStoredTheme(), mode, resolveAppearance(mode, systemPrefersDark()));
}

interface ThemeContextValue {
  theme: string;
  setTheme: (id: string) => void;
  mode: ThemeMode;
  resolvedMode: ResolvedThemeMode;
  setMode: (mode: ThemeMode) => void;
  toggleMode: () => void;
}
const ThemeContext = createContext<ThemeContextValue | null>(null);

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState(readStoredTheme);
  const [mode, setModeState] = useState<ThemeMode>(readStoredMode);
  const [systemDark, setSystemDark] = useState(systemPrefersDark);
  const resolvedMode = resolveAppearance(mode, systemDark);

  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const query = window.matchMedia(SYSTEM_DARK_QUERY);
    setSystemDark(query.matches);
    return subscribeSystemAppearance(query, setSystemDark);
  }, []);

  useLayoutEffect(() => { applyAppearance(theme, mode, resolvedMode); }, [theme, mode, resolvedMode]);

  useEffect(() => {
    const sync = (event: StorageEvent) => {
      if (event.key === null || [THEME_KEY, LEGACY_THEME_KEY, MODE_KEY, LEGACY_MODE_KEY].includes(event.key)) {
        setThemeState(readStoredTheme());
        setModeState(readStoredMode());
      }
    };
    window.addEventListener("storage", sync);
    return () => window.removeEventListener("storage", sync);
  }, []);

  const setTheme = useCallback((id: string) => {
    if (!VALID.has(id)) return;
    writeStoredTheme(id);
    setThemeState(id);
  }, []);
  const setMode = useCallback((next: ThemeMode) => {
    writeStoredMode(next);
    setModeState(next);
  }, []);
  const toggleMode = useCallback(() => {
    setMode(resolvedMode === "light" ? "dark" : "light");
  }, [resolvedMode, setMode]);

  const value = useMemo(
    () => ({ theme, setTheme, mode, resolvedMode, setMode, toggleMode }),
    [theme, setTheme, mode, resolvedMode, setMode, toggleMode],
  );
  return createElement(ThemeContext.Provider, { value }, children);
}

export function useTheme(): ThemeContextValue {
  const context = useContext(ThemeContext);
  if (context) return context;
  const mode = readStoredMode();
  return {
    theme: readStoredTheme(), setTheme: writeStoredTheme,
    mode, resolvedMode: resolveAppearance(mode, systemPrefersDark()),
    setMode: writeStoredMode, toggleMode: () => {},
  };
}
