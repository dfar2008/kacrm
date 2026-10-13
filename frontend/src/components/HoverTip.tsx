/**
 * HoverTip — 即时悬浮卡片,替代原生 `title`(后者有 ~1s 延迟)。
 *
 * 包裹 children 于 inline <span>,onMouseEnter/onFocus 后 delayMs 显示,
 * onMouseLeave/onBlur 隐藏。卡片经 createPortal 渲染到 document.body,
 * position:fixed,坐标取 anchor 的 getBoundingClientRect():
 *   - 默认贴在 anchor 下方(top = rect.bottom + 6, left = rect.left);
 *   - 若下方溢出视口,则改为上方锚定;
 *   - 若右侧溢出,clamp left 使其留在屏内。
 * 打开期间在 scroll(capture)/ resize 上重新定位。
 * content 为空时不渲染任何东西。
 */
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

interface HoverTipProps {
  content: React.ReactNode;
  children: React.ReactNode;
  delayMs?: number;
}

interface CardPos {
  left: number;
  top?: number;     // 下方锚定时给 top
  bottom?: number;  // 上方锚定时给 bottom(距视口底部)
}

const CARD_MAX_WIDTH = 320;
const GAP = 6;

export default function HoverTip({ content, children, delayMs = 120 }: HoverTipProps) {
  const anchorRef = useRef<HTMLSpanElement | null>(null);
  const timerRef = useRef<number | null>(null);
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<CardPos | null>(null);

  const hasContent =
    content !== null && content !== undefined && content !== false && content !== "";

  const compute = () => {
    const el = anchorRef.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    // 右侧溢出 → clamp left 使卡片留在屏内(留 8px 边距)。
    let left = r.left;
    if (left + CARD_MAX_WIDTH > vw - 8) {
      left = Math.max(8, vw - 8 - CARD_MAX_WIDTH);
    }
    // 估算高度无从得知,用一个保守阈值判断下方是否溢出:
    // 若 anchor 下沿离视口底部太近,则改为上方锚定。
    const belowTop = r.bottom + GAP;
    if (belowTop > vh - 80) {
      setPos({ left, bottom: vh - r.top + GAP });
    } else {
      setPos({ left, top: belowTop });
    }
  };

  const show = () => {
    if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    timerRef.current = window.setTimeout(() => {
      compute();
      setOpen(true);
    }, delayMs);
  };

  const hide = () => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    setOpen(false);
  };

  // 打开期间随滚动 / 缩放重新定位。
  useEffect(() => {
    if (!open) return;
    const recompute = () => compute();
    window.addEventListener("scroll", recompute, true);
    window.addEventListener("resize", recompute);
    return () => {
      window.removeEventListener("scroll", recompute, true);
      window.removeEventListener("resize", recompute);
    };
  }, [open]);

  useEffect(() => {
    return () => {
      if (timerRef.current !== null) window.clearTimeout(timerRef.current);
    };
  }, []);

  return (
    <span
      ref={anchorRef}
      style={{ display: "inline-flex", alignItems: "center" }}
      onMouseEnter={show}
      onMouseLeave={hide}
      onFocus={show}
      onBlur={hide}
    >
      {children}
      {open && hasContent && pos && createPortal(
        <div
          style={{
            position: "fixed",
            left: pos.left,
            ...(pos.top !== undefined ? { top: pos.top } : {}),
            ...(pos.bottom !== undefined ? { bottom: pos.bottom } : {}),
            background: "var(--panel, var(--bg))",
            color: "var(--fg)",
            border: "1px solid var(--border)",
            borderRadius: 8,
            boxShadow: "var(--shadow-popover, 0 4px 16px rgba(0,0,0,0.18))",
            padding: "8px 10px",
            fontSize: 13,
            lineHeight: 1.5,
            maxWidth: CARD_MAX_WIDTH,
            whiteSpace: "pre-wrap",
            zIndex: 400,
            pointerEvents: "none",
          }}
        >
          {content}
        </div>,
        document.body,
      )}
    </span>
  );
}
