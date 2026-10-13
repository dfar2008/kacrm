/**
 * AdvancedSearch — ad-hoc multi-condition filter panel for the record list
 * (Spec 4). Drops down below the search row; reuses the shared
 * FilterConditionRows builder. Edits are local until "应用"; "保存为视图"
 * hands the conditions to ViewBuilder.
 */
import { useEffect, useState } from "react";
import type { CrmFieldMeta, ViewFilter } from "@/lib/api";
import { FilterConditionRows, rowsToFilters, type RowState } from "./views/FilterConditionRows";
import { useT } from "@/lib/i18n";

interface Props {
  open: boolean;
  fields: CrmFieldMeta[];
  module: string;
  initialRows: RowState[];
  initialLogic: "all" | "any";
  onApply: (filters: ViewFilter[], logic: "all" | "any") => void;
  onClear: () => void;
  onSaveAsView: (filters: ViewFilter[], logic: "all" | "any") => void;
}

export function AdvancedSearch({
  open, fields, module, initialRows, initialLogic, onApply, onClear, onSaveAsView,
}: Props) {
  const t = useT();
  const [rows, setRows] = useState<RowState[]>(initialRows);
  const [logic, setLogic] = useState<"all" | "any">(initialLogic);

  useEffect(() => {
    if (open) { setRows(initialRows); setLogic(initialLogic); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  if (!open) return null;

  const filters = rowsToFilters(rows);

  return (
    <div style={{
      border: "1px solid var(--border)", borderRadius: 8,
      background: "var(--panel)", padding: 10, marginTop: 2,
      maxHeight: "40vh", overflowY: "auto",
    }}>
      <FilterConditionRows
        fields={fields}
        module={module}
        rows={rows}
        onChange={setRows}
        logic={logic}
        onLogicChange={setLogic}
      />
      <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 10 }}>
        <button className="ghost" onClick={onClear} style={{ fontSize: 14 }}>
          {t("advsearch.clear")}
        </button>
        <button className="ghost" onClick={() => onSaveAsView(filters, logic)} style={{ fontSize: 14 }}>
          {t("advsearch.save_as_view")}
        </button>
        <button className="primary" onClick={() => onApply(filters, logic)} style={{ fontSize: 14 }}>
          {t("advsearch.apply")}
        </button>
      </div>
    </div>
  );
}
