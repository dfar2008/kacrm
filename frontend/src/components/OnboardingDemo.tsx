/**
 * 向导页内嵌的「模拟对话」教学演示 v2——七幕导览,代替使用视频。
 *
 * - timer 驱动 advance()(状态机见 onboardingDemoScript.ts)
 * - 分幕翻页:一次只渲染当前幕,顶部章节字幕 + 7 个可点章节圆点防迷路
 * - IntersectionObserver:组件离开视口时暂停计时器
 * - prefers-reduced-motion:不播动画,七幕静态全量展示,隐藏播放控件
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useT } from "@/lib/i18n";
import {
  ACTS, INITIAL_STATE, TICK_MS, advance, jumpToAct,
  type DemoCard, type DemoLine, type DemoState,
} from "./onboardingDemoScript";

function ConfidenceDots({ filled }: { filled: number }) {
  return (
    <span aria-label={`confidence ${filled}/4`} style={{ letterSpacing: 2, color: "var(--accent)" }}>
      {Array.from({ length: 4 }, (_, i) => (i < filled ? "●" : "○")).join("")}
    </span>
  );
}

/** agent 回复卡片:七种形态,视觉都挂在左侧 accent 竖线的既有语汇上。 */
function DemoCardView({ card, shown }: { card: DemoCard; shown: boolean }) {
  const t = useT();
  const base = {
    marginTop: 6, borderLeft: "2px solid var(--accent)", paddingLeft: 8,
    display: "flex", flexDirection: "column" as const, gap: 3,
    color: "var(--muted)", opacity: shown ? 1 : 0, transition: "opacity 0.3s",
  };
  if (card.kind === "chart") {
    const max = Math.max(...(card.bars ?? [1]));
    return (
      <div style={base}>
        <div style={{ display: "flex", alignItems: "flex-end", gap: 14, height: 56, paddingTop: 4 }}>
          {card.rowKeys.map((k, i) => (
            <div key={k} style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 3 }}>
              <div style={{
                width: 26, borderRadius: "3px 3px 0 0", background: "var(--accent)",
                opacity: 0.85, height: Math.round(((card.bars?.[i] ?? 0) / max) * 44),
              }} />
              <span style={{ fontSize: 12 }}>{t(k)}</span>
            </div>
          ))}
        </div>
        {card.noteKey && <div style={{ fontSize: 12 }}>{t(card.noteKey)}</div>}
      </div>
    );
  }
  if (card.kind === "menu-grow") {
    return (
      <div style={base}>
        {card.rowKeys.map((k, i) => {
          const isNew = i === card.rowKeys.length - 1;
          return (
            <div key={k} style={{
              padding: "2px 8px", borderRadius: 6, width: "fit-content",
              background: isNew ? "var(--accent-soft)" : "transparent",
              color: isNew ? "var(--accent)" : undefined,
              fontWeight: isNew ? 600 : undefined,
            }}>
              {t(k)}
            </div>
          );
        })}
      </div>
    );
  }
  if (card.kind === "field-grow") {
    return (
      <div style={base}>
        <div style={{
          padding: "2px 8px", borderRadius: 6, width: "fit-content",
          background: "var(--accent-soft)", color: "var(--accent)", fontWeight: 600,
        }}>
          {t(card.rowKeys[0])}
        </div>
        {card.rowKeys.slice(1).map((k) => <div key={k}>{t(k)}</div>)}
      </div>
    );
  }
  // fields / ai-research / workflow / digest:行列表 + 可选置信度/注脚
  return (
    <div style={base}>
      {card.rowKeys.map((k) => <div key={k}>{t(k)}</div>)}
      {card.confidence != null && <ConfidenceDots filled={card.confidence} />}
      {card.noteKey && <div style={{ fontSize: 12 }}>{t(card.noteKey)}</div>}
    </div>
  );
}

function DemoBubble({ line, text, typing, cardShown }: {
  line: DemoLine; text: string; typing: boolean; cardShown: boolean;
}) {
  const t = useT();
  const isUser = line.role === "user";
  const isPeer = line.role === "peer";
  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: isUser ? "flex-end" : "flex-start" }}>
      {line.badgeKey && (
        <span style={{ fontSize: 12, color: "var(--muted)", margin: "0 4px 2px" }}>
          {t(line.badgeKey)}
        </span>
      )}
      <div style={{
        maxWidth: "72%", borderRadius: 10, padding: "8px 12px", fontSize: 14,
        background: isUser ? "var(--chat-bubble-user-bg)"
          : isPeer ? "var(--accent-soft)" : "var(--surface-inset)",
        color: isUser ? "var(--chat-bubble-user-fg)" : "var(--text)",
      }}>
        {line.attachKey && (
          // 上传的图片附件缩略:一张小"照片"卡(🧾)+ 文件名
          <div style={{
            display: "flex", alignItems: "center", gap: 8,
            padding: "6px 8px", marginBottom: 6, borderRadius: 8,
            background: "var(--surface)", border: "1px solid var(--border)",
          }}>
            <span style={{
              display: "grid", placeItems: "center", width: 30, height: 30,
              borderRadius: 6, background: "var(--accent-soft)", fontSize: 16,
            }}>🧾</span>
            <span style={{ fontSize: 13, color: "var(--text)" }}>{t(line.attachKey)}</span>
          </div>
        )}
        <div>{text}{typing ? "▌" : ""}</div>
        {line.card && <DemoCardView card={line.card} shown={cardShown} />}
      </div>
    </div>
  );
}

export function OnboardingDemo({ onEnterChat }: { onEnterChat: () => void }) {
  const t = useT();
  const [reduced, setReduced] = useState(
    () => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false,
  );
  useEffect(() => {
    const mq = window.matchMedia?.("(prefers-reduced-motion: reduce)");
    if (!mq) return;
    const onChange = () => setReduced(mq.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);
  const [state, setState] = useState<DemoState>(INITIAL_STATE);
  const [paused, setPaused] = useState(false);
  const [inView, setInView] = useState(true);
  const rootRef = useRef<HTMLElement>(null);

  // 解析后文案表(随语言切换重算)。user 行文案须为 BMP 字符——逐字打按
  // code unit 计数,加 emoji 会打出半个代理对(agent 行整条出现,不受限)。
  const texts = useMemo(
    () => ACTS.map((a) => a.lines.map((l) => t(l.textKey))), [t]);
  const lengths = useMemo(
    () => texts.map((rows) => rows.map((s) => s.length)), [texts]);

  useEffect(() => {
    if (reduced || paused || !inView) return;
    const id = window.setInterval(() => setState((s) => advance(s, lengths)), TICK_MS);
    return () => window.clearInterval(id);
  }, [reduced, paused, inView, lengths]);

  useEffect(() => {
    const el = rootRef.current;
    if (!el || typeof IntersectionObserver === "undefined") return;
    const ob = new IntersectionObserver(([e]) => setInView(e.isIntersecting));
    ob.observe(el);
    return () => ob.disconnect();
  }, []);

  const renderLine = (line: DemoLine, actIdx: number, lineIdx: number) => {
    const full = texts[actIdx][lineIdx];
    const isCurrent = !reduced && actIdx === state.act && lineIdx === state.line;
    const isShown = reduced || lineIdx <= state.line;
    const typingNow = line.role === "user" && isCurrent;
    const text = typingNow ? full.slice(0, state.chars) : full;
    return (
      <div key={line.textKey} style={{ opacity: isShown ? 1 : 0, transition: "opacity 0.3s" }}>
        <DemoBubble
          line={line}
          text={text}
          typing={typingNow && text.length < full.length}
          cardShown={!isCurrent || reduced || state.hold > 0}
        />
      </div>
    );
  };

  return (
    <section ref={rootRef} style={{
      border: "1px solid var(--border)", borderRadius: 10,
      background: "var(--surface)", padding: 16, marginTop: 16,
    }}>
      <style>{"@keyframes kacrmDemoActIn{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}"}</style>
      <div style={{
        display: "flex", alignItems: "center", justifyContent: "space-between",
        gap: 8, flexWrap: "wrap", marginBottom: 4,
      }}>
        <div>
          <span style={{ fontWeight: 600, fontSize: 16 }}>{t("setup.demo_title")}</span>
          <div style={{ color: "var(--muted)", fontSize: 14, marginTop: 4 }}>
            {t("setup.demo_desc")}
          </div>
        </div>
        {!reduced && (
          <div style={{ display: "flex", gap: 8 }}>
            <button className="btn" style={{ fontSize: 14 }}
              onClick={() => setPaused((p) => !p)}>
              {paused ? t("setup.demo_resume") : t("setup.demo_pause")}
            </button>
            <button className="btn" style={{ fontSize: 14 }}
              onClick={() => { setState(INITIAL_STATE); setPaused(false); }}>
              {t("setup.demo_replay")}
            </button>
          </div>
        )}
      </div>

      {reduced ? (
        // 静态退化:七幕全量顺序展示
        <div style={{ display: "flex", flexDirection: "column", gap: 18, padding: "10px 4px 6px" }}>
          {ACTS.map((act, ai) => (
            <div key={act.titleKey}>
              <div style={{ fontWeight: 600, fontSize: 14, color: "var(--accent)", marginBottom: 8 }}>
                {t(act.titleKey)}
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                {act.lines.map((l, li) => renderLine(l, ai, li))}
              </div>
            </div>
          ))}
        </div>
      ) : (
        <>
          {/* 章节导航:可点圆点 + 当前幕字幕 */}
          <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap", margin: "8px 0 2px" }}>
            <div style={{ display: "flex", gap: 6 }}>
              {ACTS.map((act, i) => {
                const active = i === state.act;
                return (
                  <button key={act.titleKey} title={t(act.titleKey)}
                    onClick={() => { setState(jumpToAct(i)); setPaused(false); }}
                    style={{
                      width: 22, height: 22, borderRadius: "50%", padding: 0,
                      fontSize: 12, lineHeight: "20px", cursor: "pointer",
                      border: active ? "1px solid var(--accent)" : "1px solid var(--border)",
                      background: active ? "var(--accent-soft)" : "transparent",
                      color: active ? "var(--accent)" : "var(--muted)",
                      fontWeight: active ? 700 : 400,
                    }}>
                    {i + 1}
                  </button>
                );
              })}
            </div>
            <span style={{ fontSize: 14, fontWeight: 600, color: "var(--accent)" }}>
              {t(ACTS[state.act].titleKey)}
            </span>
          </div>

          {/* 当前幕(翻页进场动画,key 换幕即重放) */}
          <div key={state.act} style={{
            display: "flex", flexDirection: "column", gap: 10,
            padding: "12px 4px 6px", minHeight: 240,
            animation: "kacrmDemoActIn 0.35s ease",
          }}>
            {ACTS[state.act].lines.map((l, li) => renderLine(l, state.act, li))}
          </div>
        </>
      )}

      <div style={{ textAlign: "center", paddingTop: 6 }}>
        <button className="btn btn-primary" onClick={onEnterChat}>
          {t("setup.demo_cta")} →
        </button>
      </div>
    </section>
  );
}
