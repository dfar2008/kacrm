import { useId } from "react";

type Variant = "gradient" | "mono" | "monoDark";

/**
 * KaCRM 品牌标 —— 收尖螺旋(tapered spiral)。
 * 5 段半圆弧、stroke-width 由 7 收到 2.2,圆头端点,呼应「会进化的 CRM」。
 */
export function KacrmMark({
  size = 32,
  variant = "gradient",
  title,
}: {
  size?: number;
  variant?: Variant;
  title?: string;
}) {
  const gid = useId();
  const stroke =
    variant === "gradient" ? `url(#${gid})`
    : variant === "monoDark" ? "#14b8a6"
    : "#3FE0D0";

  // 收尖:分段递减 stroke-width(SVG stroke 无原生 taper)
  const segs: [string, number][] = [
    ["M70 40 A30 30 0 0 1 12 40", 7],
    ["M12 40 A25 25 0 0 1 62 40", 5.6],
    ["M62 40 A20 20 0 0 1 18 40", 4.3],
    ["M18 40 A15 15 0 0 1 52 40", 3.1],
    ["M52 40 A10 10 0 0 1 26 40", 2.2],
  ];

  return (
    <svg
      width={size}
      height={size}
      viewBox="3 3 74 74"
      fill="none"
      role={title ? "img" : "presentation"}
      aria-label={title}
      aria-hidden={title ? undefined : true}
    >
      {title ? <title>{title}</title> : null}
      {variant === "gradient" && (
        <defs>
          <linearGradient id={gid} x1="14" y1="14" x2="66" y2="66" gradientUnits="userSpaceOnUse">
            <stop offset="0" stopColor="#3FE0D0" />
            <stop offset="1" stopColor="#9C8CFF" />
          </linearGradient>
        </defs>
      )}
      {segs.map(([d, w]) => (
        <path key={d} d={d} stroke={stroke} strokeWidth={w} strokeLinecap="round" />
      ))}
    </svg>
  );
}
