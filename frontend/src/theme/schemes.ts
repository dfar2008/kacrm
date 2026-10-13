export interface Scheme {
  id: string;            // data-theme 值
  nameKey: string;       // i18n 名称 key:theme.scheme.<id>
  hue: number;           // accent 主色相;所有表面色由它在 index.css 推导(深/浅各一套)
  /** 显式 accent 覆盖。s1 用品牌色,保证「默认(无 data-theme)」与「选中 s1」像素一致。 */
  accent?: string;
  accent2?: string;
}

/** 35 套:色相按 360/35 步进旋转。id=s1..s35。表面色(bg/panel/border)不再
 *  按"色调"手选,而是由 --scheme-hue 在 index.css 统一推导带极淡 tint(见两个
 *  模式块)。s1 = KaCRM 品牌默认(青 #3FE0D0 / 紫 #9C8CFF),硬复刻 :root。 */
export const SCHEMES: Scheme[] = Array.from({ length: 35 }, (_, i) => {
  const hue = Math.round((174 + i * (360 / 35)) % 360);
  const base = {
    id: `s${i + 1}`,
    nameKey: `theme.scheme.s${i + 1}`,
    hue,
  };
  if (i === 0) return { ...base, accent: "#3FE0D0", accent2: "#9C8CFF" };
  return base;
});

export const DEFAULT_SCHEME_ID = "s1";

/** 预览 swatch 的 accent 色(与 themes.css / gen-themes.mjs 同 OKLCH 公式;显式覆盖优先)。 */
export function accentOf(s: Scheme): string { return s.accent ?? `oklch(0.56 0.15 ${s.hue})`; }
export function accent2Of(s: Scheme): string { return s.accent2 ?? `oklch(0.70 0.13 ${(s.hue + 40) % 360})`; }
