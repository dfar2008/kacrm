import {
  createContext, createElement, useCallback, useContext,
  useEffect, useMemo, useState, type ReactNode,
} from "react";
import { SCHEMES, DEFAULT_SCHEME_ID } from "./schemes";

const THEME_KEY = "hermes.theme";
const MODE_KEY = "hermes.mode";
const VALID = new Set(SCHEMES.map(s => s.id));

export type ThemeMode = "dark" | "light";

export function readStoredTheme(): string {
  try {
    const v = window.localStorage.getItem(THEME_KEY);
    if (v && VALID.has(v)) return v;
  } catch { /* ignore */ }
  return DEFAULT_SCHEME_ID;
}
function writeStoredTheme(id: string): void {
  try { window.localStorage.setItem(THEME_KEY, id); } catch { /* ignore */ }
}

export function readStoredMode(): ThemeMode {
  try {
    const v = window.localStorage.getItem(MODE_KEY);
    if (v === "light" || v === "dark") return v;
  } catch { /* ignore */ }
  return "dark"; // 默认深色(品牌底色)
}
function writeStoredMode(m: ThemeMode): void {
  try { window.localStorage.setItem(MODE_KEY, m); } catch { /* ignore */ }
}

interface ThemeContextValue {
  theme: string;
  setTheme: (id: string) => void;
  mode: ThemeMode;
  setMode: (m: ThemeMode) => void;
  toggleMode: () => void;
}
const ThemeContext = createContext<ThemeContextValue | null>(null);

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<string>(() => readStoredTheme());
  const [mode, setModeState] = useState<ThemeMode>(() => readStoredMode());

  useEffect(() => {
    try { document.documentElement.dataset.theme = theme; } catch { /* ignore */ }
  }, [theme]);
  useEffect(() => {
    // 深色不写 data-mode(= :root 默认),只在浅色时挂 data-mode="light"
    try {
      const el = document.documentElement;
      if (mode === "light") el.dataset.mode = "light";
      else delete el.dataset.mode;
    } catch { /* ignore */ }
  }, [mode]);

  const setTheme = useCallback((id: string) => {
    if (!VALID.has(id)) return;
    writeStoredTheme(id);
    setThemeState(id);
  }, []);
  const setMode = useCallback((m: ThemeMode) => {
    writeStoredMode(m);
    setModeState(m);
  }, []);
  const toggleMode = useCallback(() => {
    setMode(readStoredMode() === "light" ? "dark" : "light");
  }, [setMode]);

  const value = useMemo(
    () => ({ theme, setTheme, mode, setMode, toggleMode }),
    [theme, setTheme, mode, setMode, toggleMode],
  );
  return createElement(ThemeContext.Provider, { value }, children);
}

export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext);
  if (!ctx) {
    return {
      theme: readStoredTheme(), setTheme: writeStoredTheme,
      mode: readStoredMode(), setMode: writeStoredMode, toggleMode: () => {},
    };
  }
  return ctx;
}
