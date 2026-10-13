/**
 * Frontend i18n primitives.
 *
 * Design principles (see 2026-04-19 conversation):
 *   • Zero library dependencies — a 30-line ``t()`` is enough. We don't
 *     need ICU plural / date formatting for this product.
 *   • Locale is a single source of truth: localStorage ``hermes.locale``
 *     for client-side render + backend session (synced via
 *     ``?locale=<picked>`` on /api/me). Everyone reads from the same
 *     React Context, so switching language does NOT require a reload.
 *   • Fallback ladder: unknown key → return the key literal. That's how
 *     production spots missing translations (visible garbage in the
 *     UI beats a silent wrong string).
 *   • Typed keys: ``zh-CN.ts`` is the source; ``en-US.ts: Dict`` forces
 *     structural equality at compile time.
 *
 * Consumer surface:
 *     const t = useT();
 *     <button>{t("common.save")}</button>
 *     <span>{t("app.route.archived_desc", { path: next })}</span>
 *
 *     const { locale, setLocale } = useLocale();
 */
import {
  createContext,
  createElement,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { zhCN, type Dict, type DictKey } from "./dicts/zh-CN";
import { enUS } from "./dicts/en-US";

export type Locale = "zh-CN" | "en-US";
export const SUPPORTED_LOCALES: readonly Locale[] = ["zh-CN", "en-US"] as const;
export const DEFAULT_LOCALE: Locale = "zh-CN";

const DICTS: Record<Locale, Dict> = {
  "zh-CN": zhCN as unknown as Dict,
  "en-US": enUS,
};

const LOCALE_KEY = "hermes.locale";

/** Read the user's stored locale, falling back to browser preference → default. */
export function readStoredLocale(): Locale {
  try {
    const v = window.localStorage.getItem(LOCALE_KEY);
    if (v === "zh-CN" || v === "en-US") return v;
  } catch {
    /* localStorage may be unavailable (SSR / private mode) */
  }
  try {
    const nav = (typeof navigator !== "undefined" ? navigator.language : "") || "";
    if (nav.toLowerCase().startsWith("en")) return "en-US";
  } catch { /* ignore */ }
  return DEFAULT_LOCALE;
}

function writeStoredLocale(next: Locale): void {
  try { window.localStorage.setItem(LOCALE_KEY, next); } catch { /* ignore */ }
}

/** Replace ``{name}`` placeholders with values from ``vars``. */
function interpolate(template: string, vars?: Record<string, string | number>): string {
  if (!vars) return template;
  return template.replace(/\{(\w+)\}/g, (match, name) => {
    if (Object.prototype.hasOwnProperty.call(vars, name)) {
      return String(vars[name]);
    }
    return match;
  });
}

export function translate(locale: Locale, key: DictKey | string, vars?: Record<string, string | number>): string {
  const dict = DICTS[locale] || DICTS[DEFAULT_LOCALE];
  const hit = (dict as Record<string, string>)[key as string];
  if (typeof hit === "string") return interpolate(hit, vars);
  // Fallback: try default locale before returning the bare key.
  if (locale !== DEFAULT_LOCALE) {
    const fallback = (DICTS[DEFAULT_LOCALE] as Record<string, string>)[key as string];
    if (typeof fallback === "string") return interpolate(fallback, vars);
  }
  return String(key);
}

interface LocaleContextValue {
  locale: Locale;
  setLocale: (next: Locale) => void;
  toggle: () => void;
}

const LocaleContext = createContext<LocaleContextValue | null>(null);

export function LocaleProvider({ children }: { children: ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>(() => readStoredLocale());

  // Keep <html lang> in sync — good for accessibility tooling and
  // browser-level features (font choice, hyphenation, etc).
  useEffect(() => {
    try { document.documentElement.lang = locale; } catch { /* ignore */ }
  }, [locale]);

  const setLocale = useCallback((next: Locale) => {
    if (!SUPPORTED_LOCALES.includes(next)) return;
    writeStoredLocale(next);
    setLocaleState(next);
    // Sync backend session so server-rendered labels (CRM field labels,
    // module titles, error messages) match the client's pick. /api/me is
    // cheap and cookie-authed; I18nMiddleware writes session["locale"].
    try {
      void fetch(`/api/me?locale=${encodeURIComponent(next)}`, {
        method: "GET",
        credentials: "include",
      });
    } catch { /* non-fatal */ }
  }, []);

  const toggle = useCallback(() => {
    setLocale(locale === "zh-CN" ? "en-US" : "zh-CN");
  }, [locale, setLocale]);

  const value = useMemo(
    () => ({ locale, setLocale, toggle }),
    [locale, setLocale, toggle],
  );
  return createElement(LocaleContext.Provider, { value }, children);
}

export function useLocale(): LocaleContextValue {
  const ctx = useContext(LocaleContext);
  if (!ctx) {
    // Graceful degradation: outside the provider (e.g. an error boundary
    // that mounts before the tree), return a read-only view based on
    // storage. Attempts to setLocale will still persist but won't
    // trigger re-renders.
    const locale = readStoredLocale();
    return {
      locale,
      setLocale: (next) => writeStoredLocale(next),
      toggle: () => {},
    };
  }
  return ctx;
}

export type T = (key: DictKey | string, vars?: Record<string, string | number>) => string;

export function useT(): T {
  const { locale } = useLocale();
  return useCallback((key, vars) => translate(locale, key, vars), [locale]);
}

/**
 * 枚举/picklist 显示值翻译。约定 key = ``enum.<原始值>``;查不到则回退原始值
 * (因 translate 缺键返回 key 本身,这里据此判断未命中并回退)。
 * 用于把后端裸枚举(created/approved/high…)统一双语显示。
 */
export function enumLabel(t: T, value: unknown): string {
  if (value === null || value === undefined || value === "") return "";
  const raw = String(value);
  const key = `enum.${raw}`;
  const label = t(key);
  return label === key ? raw : label;
}

export type { Dict, DictKey } from "./dicts/zh-CN";
