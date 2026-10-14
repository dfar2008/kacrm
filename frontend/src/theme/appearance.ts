export type ThemeMode = "system" | "dark" | "light";
export type ResolvedThemeMode = "dark" | "light";

export const MODE_KEY = "kacrm.theme.mode";
export const LEGACY_MODE_KEY = "hermes.mode";
export const SYSTEM_DARK_QUERY = "(prefers-color-scheme: dark)";

export function readModePreference(storage?: Pick<Storage, "getItem">): ThemeMode {
  try {
    const source = storage ?? window.localStorage;
    const value = source.getItem(MODE_KEY) ?? source.getItem(LEGACY_MODE_KEY);
    if (value === "dark" || value === "light" || value === "system") return value;
  } catch { /* Appearance works without storage, including during SSR. */ }
  return "system";
}

export function resolveAppearance(mode: ThemeMode, systemDark: boolean): ResolvedThemeMode {
  return mode === "system" ? (systemDark ? "dark" : "light") : mode;
}

export function systemPrefersDark(): boolean {
  return typeof window !== "undefined" && typeof window.matchMedia === "function"
    ? window.matchMedia(SYSTEM_DARK_QUERY).matches : false;
}

interface AppearanceQuery {
  addEventListener(type: "change", listener: (event: { matches: boolean }) => void): void;
  removeEventListener(type: "change", listener: (event: { matches: boolean }) => void): void;
}

export function subscribeSystemAppearance(query: AppearanceQuery, onChange: (dark: boolean) => void) {
  const listener = (event: { matches: boolean }) => onChange(event.matches);
  query.addEventListener("change", listener);
  return () => query.removeEventListener("change", listener);
}
