/**
 * TimelineView — CRM 列表的「时间线视图」。
 *
 * 渲染在左栏列表区内：按 ``viewConfig.date_field`` 倒序(新→旧)排列记录，
 * 左侧窄列显示日期标签 + 竖轴/圆点,右侧卡片显示标题 + content_fields。
 * 未配置日期字段时提示需要选择日期字段。
 */
import type { CrmRecord, CrmFieldMeta, ViewConfig } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { formatListFieldValue, listPrimaryText } from "@/lib/listFields";
import { fieldLabel } from "@/lib/crmLabels";

interface Props {
  records: CrmRecord[];
  fieldMap: Record<string, CrmFieldMeta>;
  viewConfig: ViewConfig | null | undefined;
  module: string;
  selectedId: number | null;
  onSelect: (id: number) => void;
}

function tsOf(record: CrmRecord, dateField: string): number {
  const v = record[dateField];
  if (v === null || v === undefined || v === "") return -Infinity;
  const ms = new Date(String(v)).getTime();
  return Number.isNaN(ms) ? -Infinity : ms;
}

function fmtDateRaw(v: unknown): string {
  const d = new Date(String(v));
  if (Number.isNaN(d.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

export function TimelineView(props: Props) {
  const { records, fieldMap, viewConfig, module, selectedId, onSelect } = props;
  const t = useT();

  const dateField = viewConfig?.date_field;
  if (!dateField) {
    return (
      <div
        className="muted"
        style={{
          height: "100%",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        {t("viewbuilder.need_date_field")}
      </div>
    );
  }

  const contentFields: string[] = viewConfig?.content_fields?.length
    ? viewConfig.content_fields
    : [];

  if (!records.length) {
    return (
      <div
        className="muted"
        style={{
          height: "100%",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        {t("view.empty")}
      </div>
    );
  }

  const sorted = [...records].sort(
    (a, b) => tsOf(b, dateField) - tsOf(a, dateField),
  );

  const dateMeta = fieldMap[dateField];

  return (
    <div style={{ padding: 8, overflow: "auto", height: "100%" }}>
      {sorted.map((record) => {
        const selected = record.id === selectedId;
        const dateLabel = dateMeta
          ? formatListFieldValue(t, dateMeta, record)
          : fmtDateRaw(record[dateField]);
        const title = listPrimaryText(t, [], fieldMap, record);
        return (
          <div key={record.id} style={{ display: "flex", gap: 8 }}>
            {/* 左侧:日期标签 */}
            <div
              style={{
                width: 84,
                flexShrink: 0,
                paddingTop: 12,
                fontSize: 14,
                color: "var(--muted)",
                textAlign: "right",
              }}
            >
              {dateLabel || t("view.no_date")}
            </div>
            {/* 中间:竖轴 + 圆点 */}
            <div
              style={{
                position: "relative",
                width: 16,
                flexShrink: 0,
                display: "flex",
                justifyContent: "center",
              }}
            >
              <div
                style={{
                  position: "absolute",
                  top: 0,
                  bottom: 0,
                  width: 2,
                  background: "var(--border)",
                }}
              />
              <div
                style={{
                  position: "absolute",
                  top: 14,
                  width: 10,
                  height: 10,
                  borderRadius: "50%",
                  background: selected ? "var(--accent)" : "var(--panel-2)",
                  border: `2px solid ${selected ? "var(--accent)" : "var(--border)"}`,
                }}
              />
            </div>
            {/* 右侧:卡片 */}
            <div
              onClick={() => onSelect(record.id)}
              style={{
                flex: 1,
                minWidth: 0,
                marginBottom: 8,
                border: `1px solid ${selected ? "var(--accent)" : "var(--border)"}`,
                background: selected ? "var(--panel-2)" : undefined,
                borderRadius: 8,
                padding: 10,
                cursor: "pointer",
              }}
            >
              <div style={{ fontWeight: 600, fontSize: 14 }}>{title}</div>
              {contentFields.map((fn) => {
                const f = fieldMap[fn];
                if (!f) return null;
                const v = formatListFieldValue(t, f, record);
                if (!v) return null;
                return (
                  <div key={fn} className="muted small">
                    {fieldLabel(t, f, module)}: {v}
                  </div>
                );
              })}
            </div>
          </div>
        );
      })}
    </div>
  );
}
