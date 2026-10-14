import type { ResolvedThemeMode } from "./appearance";

export interface Scheme {
  id: string;            // data-theme 值
  nameKey: string;       // i18n 名称 key:theme.scheme.<id>
  hue: number;           // accent 主色相;所有表面色由它在 index.css 推导(深/浅各一套)
  /** 显式 accent 覆盖。s1 用品牌色,保证「默认(无 data-theme)」与「选中 s1」像素一致。 */
  accent?: string;
  accent2?: string;
  accentLight?: string;
  accent2Light?: string;
}

/** Keep all 35 stable scheme IDs. Surfaces use a subtle hue tint, while
 *  accents have separate light/dark values. s1 is the KaCRM brand palette. */
export const SCHEMES: Scheme[] = Array.from({ length: 35 }, (_, i) => {
  const hue = Math.round((174 + i * (360 / 35)) % 360);
  const base = {
    id: `s${i + 1}`,
    nameKey: `theme.scheme.s${i + 1}`,
    hue,
  };
  if (i === 0) return { ...base, accent: "#3FE0D0", accent2: "#9C8CFF", accentLight: "#08786E", accent2Light: "#6556C7" };
  return base;
});

export const DEFAULT_SCHEME_ID = "s1";

/** Dark surfaces need brighter accents; white surfaces need deeper accents. */
export function accentOf(s: Scheme, mode: ResolvedThemeMode = "dark"): string {
  return mode === "light"
    ? s.accentLight ?? `oklch(0.46 0.11 ${s.hue})`
    : s.accent ?? `oklch(0.76 0.12 ${s.hue})`;
}
export function accent2Of(s: Scheme, mode: ResolvedThemeMode = "dark"): string {
  return mode === "light"
    ? s.accent2Light ?? `oklch(0.48 0.10 ${(s.hue + 40) % 360})`
    : s.accent2 ?? `oklch(0.80 0.10 ${(s.hue + 40) % 360})`;
}

export const RECOMMENDED_SCHEME_IDS = ["s1", "s5", "s8", "s11", "s19", "s23", "s29", "s33"];
