/**
 * FieldLayout — column 3 of the schema field builder.
 *
 * Top: a single-column SortableContext of NON-HIDDEN fields (drafts +
 * persisted). The single column is deliberate — the record form is always
 * single-column, so the layout faithfully mirrors it.
 *
 * Bottom: the "hidden fields" drop zone. It is a dnd-kit droppable
 * (``id = HIDDEN_ZONE_ID``); dragging a card onto it hides the field,
 * dragging a hidden card back into the list restores it.
 */
import { useDroppable } from "@dnd-kit/core";
import { SortableContext, verticalListSortingStrategy } from "@dnd-kit/sortable";
import { useT } from "@/lib/i18n";
import { FieldCard, fieldCardId } from "./FieldCard";
import type { DraftField } from "./useSchemaFields";

/** dnd-kit droppable id for the hidden-fields zone. */
export const HIDDEN_ZONE_ID = "hidden-zone";
/** dnd-kit droppable id for the main layout list (empty-list drop target). */
export const LAYOUT_ZONE_ID = "layout-zone";

export function FieldLayout({
  fields, selectedId, onSelect,
}: {
  fields: DraftField[];
  /** fieldCardId of the selected field, or null. */
  selectedId: string | null;
  onSelect: (field: DraftField) => void;
}) {
  const t = useT();
  const visible = fields.filter(f => !f.is_hidden);
  const hidden = fields.filter(f => f.is_hidden);

  const layoutDrop = useDroppable({ id: LAYOUT_ZONE_ID });
  const hiddenDrop = useDroppable({ id: HIDDEN_ZONE_ID });

  return (
    <div style={{
      flex: 1, minWidth: 320, maxWidth: 560,
      border: "1px solid var(--border)", borderRadius: 8,
      background: "var(--panel)", overflow: "hidden",
      display: "flex", flexDirection: "column",
    }}>
      <div style={{
        padding: "10px 12px", fontSize: 14, fontWeight: 600,
        textTransform: "uppercase", color: "var(--muted)",
        borderBottom: "1px solid var(--border)", flexShrink: 0,
      }}>
        {t("admin.schema.builder.layout_heading")}
      </div>

      {/* Sortable list of non-hidden fields. */}
      <div
        ref={layoutDrop.setNodeRef}
        style={{
          flex: 1, overflowY: "auto", padding: 12,
          display: "flex", flexDirection: "column", gap: 8,
          background: layoutDrop.isOver ? "var(--accent-soft)" : "transparent",
        }}
      >
        {visible.length === 0 && (
          <div className="muted" style={{
            padding: "32px 12px", textAlign: "center", fontSize: 14,
            border: "1px dashed var(--border)", borderRadius: 8,
          }}>
            {t("admin.schema.builder.layout_empty")}
          </div>
        )}
        <SortableContext
          items={visible.map(fieldCardId)}
          strategy={verticalListSortingStrategy}
        >
          {visible.map(f => (
            <FieldCard
              key={fieldCardId(f)}
              field={f}
              selected={fieldCardId(f) === selectedId}
              onSelect={() => onSelect(f)}
            />
          ))}
        </SortableContext>
      </div>

      {/* Hidden-fields drop zone. */}
      <div
        ref={hiddenDrop.setNodeRef}
        style={{
          flexShrink: 0,
          borderTop: "1px solid var(--border)",
          padding: 12,
          background: hiddenDrop.isOver ? "var(--danger-soft)" : "var(--bg)",
          display: "flex", flexDirection: "column", gap: 8,
          maxHeight: "30%", overflowY: "auto",
        }}
      >
        <div className="muted" style={{ fontSize: 13, fontWeight: 600, textTransform: "uppercase" }}>
          {t("admin.schema.builder.hidden_zone")}
        </div>
        {hidden.length === 0 && (
          <div className="muted" style={{ fontSize: 14 }}>
            {t("admin.schema.builder.hidden_zone_hint")}
          </div>
        )}
        <SortableContext
          items={hidden.map(fieldCardId)}
          strategy={verticalListSortingStrategy}
        >
          {hidden.map(f => (
            <FieldCard
              key={fieldCardId(f)}
              field={f}
              selected={fieldCardId(f) === selectedId}
              onSelect={() => onSelect(f)}
            />
          ))}
        </SortableContext>
      </div>
    </div>
  );
}
