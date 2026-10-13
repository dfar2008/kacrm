/**
 * AiConfidenceDot — AI 派生字段置信度彩色圆点。
 *
 * 绿（≥0.75）/ 黄（≥0.4）/ 红（<0.4）；无 meta 时不渲染。
 * hover title 显示 reason + basis 列表，basis 里 http:// 开头的作链接展示文本。
 */
import type { AiFieldMeta } from "../lib/api";
import { useT } from "../lib/i18n";
import HoverTip from "./HoverTip";

interface AiConfidenceDotProps {
  meta: AiFieldMeta | null | undefined;
}

/** 根据 confidence 值返回圆点颜色 */
function dotColor(confidence: number): string {
  if (confidence >= 0.75) return "#22c55e"; // green-500
  if (confidence >= 0.4)  return "#eab308"; // yellow-500
  return "#ef4444";                          // red-500
}

/** 构建 hover 卡片内容:reason + basis 列表(JSX 行,便于阅读) */
function buildContent(
  t: ReturnType<typeof useT>,
  meta: AiFieldMeta,
  confidenceLabel: string,
): React.ReactNode {
  const reason = (meta.reason ?? "").toString().trim();
  const baseParts = (meta.basis ?? []).map((b) => b.trim()).filter(Boolean);
  return (
    <>
      {/* 首行点明这个圆点是什么,避免用户看不懂图标含义。 */}
      <div style={{ fontWeight: 600 }}>{t("record.aiField.dot_hint")}</div>
      {typeof meta.confidence === "number" && (
        <div>{confidenceLabel} ({Math.round(meta.confidence * 100)}%)</div>
      )}
      {reason && <div>{t("record.aiField.reasoning")}: {reason}</div>}
      {baseParts.length > 0 && (
        <>
          <div>{t("record.aiField.citations")}:</div>
          {baseParts.map((b, i) => (
            <div key={i}>• {b}</div>
          ))}
        </>
      )}
      {meta.source && <div>{t("record.aiField.basis")}: {meta.source}</div>}
    </>
  );
}

export default function AiConfidenceDot({ meta }: AiConfidenceDotProps) {
  const t = useT();

  if (!meta || typeof meta.confidence !== "number") return null;

  const { confidence } = meta;
  const color = dotColor(confidence);

  const confidenceLabel =
    confidence >= 0.75
      ? t("record.aiField.confidence.high")
      : confidence >= 0.4
        ? t("record.aiField.confidence.medium")
        : t("record.aiField.confidence.low");

  const content = buildContent(t, meta, confidenceLabel);

  return (
    <HoverTip content={content}>
      <span
        aria-label={confidenceLabel}
        style={{
          display: "inline-block",
          width: 8,
          height: 8,
          borderRadius: "50%",
          background: color,
          marginLeft: 4,
          verticalAlign: "middle",
          cursor: "help",
          flexShrink: 0,
        }}
      />
    </HoverTip>
  );
}
