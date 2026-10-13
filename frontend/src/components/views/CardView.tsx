/**
 * CardView — CRM 列表的「卡片视图」。
 *
 * 渲染在左栏列表区内：把记录排成网格卡片。卡片字段、列数、封面图字段
 * 都从 ``viewConfig`` 读取，未配置时回退到个人/内置 list_fields 配置
 * (去掉 ``<module>_name``，因为那是卡片标题)。
 */
import type { CrmRecord, CrmFieldMeta, ViewConfig } from "@/lib/api";
import { useT } from "@/lib/i18n";
import {
  formatListFieldValue,
  listPrimaryText,
  resolveListFields,
  getPersonalListFields,
} from "@/lib/listFields";
import { fieldLabel } from "@/lib/crmLabels";

interface Props {
  records: CrmRecord[];
  fieldMap: Record<string, CrmFieldMeta>;
  viewConfig: ViewConfig | null | undefined;
  module: string;
  selectedId: number | null;
  onSelect: (id: number) => void;
}

export function CardView(props: Props) {
  const { records, fieldMap, viewConfig, module, selectedId, onSelect } = props;
  const t = useT();

  const cardFields: string[] = viewConfig?.card_fields?.length
    ? viewConfig.card_fields
    : (() => {
        const c = resolveListFields(module, getPersonalListFields(module), null);
        return [...c.row1, ...c.row2].filter((fn) => fn !== module + "_name");
      })();

  const perRow = viewConfig?.card_per_row || 1;
  const coverField = viewConfig?.card_cover_field;

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

  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: `repeat(${perRow}, minmax(0, 1fr))`,
        gap: 8,
        padding: 8,
        overflow: "auto",
        height: "100%",
      }}
    >
      {records.map((record) => {
        const selected = record.id === selectedId;
        const title = listPrimaryText(t, [], fieldMap, record);
        const cover =
          coverField && record[coverField] ? String(record[coverField]) : null;
        return (
          <div
            key={record.id}
            onClick={() => onSelect(record.id)}
            style={{
              border: `1px solid ${selected ? "var(--accent)" : "var(--border)"}`,
              background: selected ? "var(--panel-2)" : undefined,
              borderRadius: 8,
              padding: 10,
              cursor: "pointer",
            }}
          >
            {cover && (
              <img
                src={cover}
                style={{
                  width: "100%",
                  height: 90,
                  objectFit: "cover",
                  borderRadius: 6,
                  marginBottom: 6,
                }}
              />
            )}
            <div style={{ fontWeight: 600, fontSize: 14 }}>{title}</div>
            {cardFields.map((fn) => {
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
        );
      })}
    </div>
  );
}
