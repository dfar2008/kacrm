/**
 * KanbanView — CRM 的「看板视图」。
 *
 * 渲染在左栏列表区内：按 ``viewConfig.group_by`` 这个 select 字段把记录
 * 拆成若干列(option + 末尾「未分组」列),支持 HTML5 原生拖拽把卡片拖到
 * 另一列 → 局部更新 group_by 字段(乐观更新 + 失败回滚)。
 */
import { useEffect, useState } from "react";
import type { CrmRecord, CrmFieldMeta, ViewConfig } from "@/lib/api";
import { updateRecord } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { toast } from "@/lib/toast";
import { localizedSelectOptions } from "@/lib/crmLabels";
import type { SelectOption } from "@/lib/crmLabels";
import { formatListFieldValue, listPrimaryText } from "@/lib/listFields";

interface Props {
  records: CrmRecord[];
  fieldMap: Record<string, CrmFieldMeta>;
  viewConfig: ViewConfig | null | undefined;
  module: string;
  selectedId: number | null;
  onSelect: (id: number) => void;
  onChanged: () => void; // 拖拽改值成功后通知父组件刷新
}

export function KanbanView(props: Props) {
  const { fieldMap, viewConfig, module, selectedId, onSelect, onChanged } = props;
  const t = useT();

  // 拖拽时乐观更新本地副本;props.records 变化时同步回来。
  const [localRecords, setLocalRecords] = useState<CrmRecord[]>(props.records);
  useEffect(() => {
    setLocalRecords(props.records);
  }, [props.records]);

  const groupBy = viewConfig?.group_by;
  if (!groupBy) {
    return (
      <div
        className="muted"
        style={{
          height: "100%",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          padding: 16,
          textAlign: "center",
        }}
      >
        {t("viewbuilder.need_group_by")}
      </div>
    );
  }

  const field = fieldMap[groupBy];
  // select/picklist → 列来自字段 options;其它分类字段(负责人/创建人/部门/关系)
  // → 从记录里动态汇出现过的不同值,列名用 ``{field}_display`` 兄弟值。
  const isSelectLike = !!field && (field.field_type === "select" || field.field_type === "picklist");
  const options: SelectOption[] = isSelectLike
    ? localizedSelectOptions(field.options, field.field_config)
    : [];
  let baseCols: SelectOption[];
  if (options.length) {
    baseCols = options.map((o) => ({ value: o.value, text: o.text }));
  } else {
    const seen = new Map<string, string>();
    for (const r of localRecords) {
      const v = String(r[groupBy] ?? "");
      if (!v) continue;
      if (!seen.has(v)) seen.set(v, String(r[groupBy + "_display"] ?? v));
    }
    baseCols = [...seen.entries()].map(([value, text]) => ({ value, text }));
  }

  // 列 = 各分组值 + 末尾「未分组」列。
  const columns: SelectOption[] = [
    ...baseCols,
    { value: "", text: t("kanban.ungrouped") },
  ];
  const knownValues = new Set(baseCols.map((o) => o.value));

  // 把一条记录归入哪一列:其 group_by 值匹配某 option → 该列;否则 → 未分组。
  function columnValueFor(record: CrmRecord): string {
    const v = String(record[groupBy!] ?? "");
    return knownValues.has(v) ? v : "";
  }

  async function handleDrop(colValue: string, e: React.DragEvent) {
    e.preventDefault();
    const raw = e.dataTransfer.getData("text/plain");
    const recordId = Number(raw);
    if (!Number.isFinite(recordId)) return;
    const record = localRecords.find((r) => r.id === recordId);
    if (!record) return;
    // 已在目标列(按归类口径)则无需更新。
    if (columnValueFor(record) === colValue) return;

    // 乐观更新本地。
    setLocalRecords((prev) =>
      prev.map((r) =>
        r.id === recordId ? { ...r, [groupBy!]: colValue } : r,
      ),
    );
    try {
      await updateRecord(module, recordId, { [groupBy!]: colValue });
      onChanged();
    } catch (err) {
      toast(err instanceof Error ? err.message : String(err), "error");
      setLocalRecords(props.records); // 回滚
    }
  }

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "row",
        overflowX: "auto",
        height: "100%",
        padding: 8,
        gap: 8,
      }}
    >
      {columns.map((col) => {
        const colRecords = localRecords.filter(
          (r) => columnValueFor(r) === col.value,
        );
        return (
          <div
            key={col.value || "__ungrouped__"}
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => void handleDrop(col.value, e)}
            style={{
              flex: "0 0 auto",
              width: 190,
              display: "flex",
              flexDirection: "column",
              minHeight: 0,
              border: "1px solid var(--border)",
              borderRadius: 8,
              background: "var(--panel-2)",
            }}
          >
            <div
              style={{
                padding: "6px 8px",
                fontWeight: 600,
                fontSize: 14,
                borderBottom: "1px solid var(--border)",
                display: "flex",
                justifyContent: "space-between",
                gap: 6,
              }}
            >
              <span
                style={{
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  whiteSpace: "nowrap",
                }}
              >
                {col.text}
              </span>
              <span className="muted">{colRecords.length}</span>
            </div>
            <div
              style={{
                flex: "1 1 auto",
                overflowY: "auto",
                padding: 6,
                display: "flex",
                flexDirection: "column",
                gap: 6,
              }}
            >
              {colRecords.map((record) => {
                const selected = record.id === selectedId;
                const title = listPrimaryText(t, [], fieldMap, record);
                // 至多展示 2 个其它字段(非空,且不是分组/标题字段)。
                const extras: string[] = [];
                for (const fn of Object.keys(fieldMap)) {
                  if (extras.length >= 2) break;
                  if (fn === groupBy) continue;
                  const f = fieldMap[fn];
                  if (!f) continue;
                  const v = formatListFieldValue(t, f, record);
                  if (v) extras.push(v);
                }
                return (
                  <div
                    key={record.id}
                    draggable
                    onDragStart={(e) =>
                      e.dataTransfer.setData("text/plain", String(record.id))
                    }
                    onClick={() => onSelect(record.id)}
                    style={{
                      border: `1px solid ${
                        selected ? "var(--accent)" : "var(--border)"
                      }`,
                      background: selected ? "var(--panel)" : "var(--bg)",
                      borderRadius: 6,
                      padding: 8,
                      cursor: "pointer",
                    }}
                  >
                    <div
                      style={{
                        fontWeight: 600,
                        fontSize: 14,
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {title}
                    </div>
                    {extras.map((v, i) => (
                      <div key={i} className="muted small">
                        {v}
                      </div>
                    ))}
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
