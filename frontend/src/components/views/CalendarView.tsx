/**
 * CalendarView — CRM 列表的「日程表(月历)视图」。
 *
 * 渲染在左栏列表区内:自绘月历(不引库),按 ``viewConfig.date_field`` 把记录
 * 落到对应日期格,每格显示当日记录的小条(最多 3 条,超出 +N)。
 * 工具条可翻月;以周一为每周第一列;今天的格子高亮。
 * 未配置日期字段时提示需要选择日期字段。
 */
import { useState } from "react";
import type { CrmRecord, CrmFieldMeta, ViewConfig } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { listPrimaryText } from "@/lib/listFields";

interface Props {
  records: CrmRecord[];
  fieldMap: Record<string, CrmFieldMeta>;
  viewConfig: ViewConfig | null | undefined;
  module: string;
  selectedId: number | null;
  onSelect: (id: number) => void;
}

const pad = (n: number) => String(n).padStart(2, "0");

/** 把任意值解析成本地 YYYY-MM-DD;无效返回 null。 */
function dayKeyOf(v: unknown): string | null {
  if (v === null || v === undefined || v === "") return null;
  const d = new Date(String(v));
  if (Number.isNaN(d.getTime())) return null;
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/** 对字符串做简单稳定哈希 → hsl 颜色。 */
function hashColor(s: string): string {
  // theme-guard:ignore-start  — dynamic hsl, data-driven per-record color dot
  let h = 0;
  for (let i = 0; i < s.length; i++) {
    h = (h * 31 + s.charCodeAt(i)) | 0;
  }
  const hue = ((h % 360) + 360) % 360;
  return `hsl(${hue}, 65%, 55%)`;
  // theme-guard:ignore-end
}

export function CalendarView(props: Props) {
  const { records, fieldMap, viewConfig, selectedId, onSelect } = props;
  const t = useT();

  const dateField = viewConfig?.date_field;
  const colorField = viewConfig?.color_field;

  const [cursor, setCursor] = useState(() => {
    const d = new Date();
    return { y: d.getFullYear(), m: d.getMonth() };
  });

  if (!dateField) {
    return (
      <div style={{ padding: 16, color: "var(--muted)" }}>
        {t("viewbuilder.need_date_field")}
      </div>
    );
  }

  // 按本地日期分组
  const byDay = new Map<string, CrmRecord[]>();
  for (const rec of records) {
    const key = dayKeyOf(rec[dateField]);
    if (!key) continue;
    const arr = byDay.get(key);
    if (arr) arr.push(rec);
    else byDay.set(key, [rec]);
  }

  const prevMonth = () => {
    setCursor((c) => (c.m <= 0 ? { y: c.y - 1, m: 11 } : { y: c.y, m: c.m - 1 }));
  };
  const nextMonth = () => {
    setCursor((c) => (c.m >= 11 ? { y: c.y + 1, m: 0 } : { y: c.y, m: c.m + 1 }));
  };

  const { y, m } = cursor;
  // 以周一为第一列的当月首日偏移
  const firstOffset = (new Date(y, m, 1).getDay() + 6) % 7;
  const daysInMonth = new Date(y, m + 1, 0).getDate();

  const todayKey = (() => {
    const d = new Date();
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  })();

  const weekdays = t("calendar.weekdays").split(",");

  // 6 行 × 7 列 = 42 个格子;前置空位 + 当月日号
  const cells: (number | null)[] = [];
  for (let i = 0; i < firstOffset; i++) cells.push(null);
  for (let d = 1; d <= daysInMonth; d++) cells.push(d);
  while (cells.length < 42) cells.push(null);

  const btnStyle: React.CSSProperties = {
    border: "1px solid var(--border)",
    background: "var(--panel)",
    color: "var(--fg)",
    borderRadius: 6,
    cursor: "pointer",
    width: 28,
    height: 28,
    lineHeight: "1",
    fontSize: 16,
  };

  return (
    <div style={{ padding: 8, overflow: "auto", height: "100%" }}>
      {/* 工具条 */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          gap: 12,
          marginBottom: 8,
        }}
      >
        <button
          type="button"
          style={btnStyle}
          title={t("calendar.prev_month")}
          onClick={prevMonth}
        >
          ‹
        </button>
        <div style={{ minWidth: 90, textAlign: "center", fontWeight: 600 }}>
          {`${y}-${pad(m + 1)}`}
        </div>
        <button
          type="button"
          style={btnStyle}
          title={t("calendar.next_month")}
          onClick={nextMonth}
        >
          ›
        </button>
      </div>

      {/* 星期表头 */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(7, 1fr)" }}>
        {weekdays.map((w, i) => (
          <div
            key={i}
            style={{
              textAlign: "center",
              padding: "4px 0",
              fontSize: 14,
              color: "var(--muted)",
            }}
          >
            {w}
          </div>
        ))}
      </div>

      {/* 日期网格 */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(7, 1fr)" }}>
        {cells.map((day, i) => {
          if (day === null) {
            return (
              <div
                key={i}
                style={{
                  border: "1px solid var(--border)",
                  minHeight: 84,
                }}
              />
            );
          }
          const key = `${y}-${pad(m + 1)}-${pad(day)}`;
          const isToday = key === todayKey;
          const dayRecords = byDay.get(key) || [];
          const shown = dayRecords.slice(0, 3);
          const extra = dayRecords.length - shown.length;
          return (
            <div
              key={i}
              style={{
                border: "1px solid var(--border)",
                minHeight: 84,
                padding: 3,
                display: "flex",
                flexDirection: "column",
                gap: 2,
                background: isToday ? "var(--panel-2)" : undefined,
                overflow: "hidden",
              }}
            >
              <div
                style={{
                  fontSize: 14,
                  textAlign: "right",
                  color: isToday ? "var(--accent)" : "var(--muted)",
                  fontWeight: isToday ? 700 : 400,
                }}
              >
                {day}
              </div>
              {shown.map((rec) => {
                const selected = rec.id === selectedId;
                const text = listPrimaryText(t, [], fieldMap, rec);
                const dotColor =
                  colorField !== undefined && colorField !== null
                    ? hashColor(String(rec[colorField]))
                    : null;
                return (
                  <div
                    key={rec.id}
                    onClick={() => onSelect(rec.id)}
                    title={text}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 4,
                      fontSize: 13,
                      lineHeight: "16px",
                      padding: "1px 4px",
                      borderRadius: 4,
                      cursor: "pointer",
                      background: selected ? "var(--accent)" : "var(--panel)",
                      color: selected ? "var(--fg-on-accent)" : "var(--fg)",
                      border: "1px solid var(--border)",
                      whiteSpace: "nowrap",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                    }}
                  >
                    {dotColor && (
                      <span
                        style={{
                          flex: "0 0 auto",
                          width: 6,
                          height: 6,
                          borderRadius: "50%",
                          background: dotColor,
                        }}
                      />
                    )}
                    <span
                      style={{
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {text}
                    </span>
                  </div>
                );
              })}
              {extra > 0 && (
                <div style={{ fontSize: 13, color: "var(--muted)", paddingLeft: 4 }}>
                  +{extra}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
