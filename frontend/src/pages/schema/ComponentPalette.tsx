/**
 * ComponentPalette — column 2 of the schema field builder.
 *
 * Renders FIELD_TYPES grouped into the four palette sections. Each tile is
 * a dnd-kit draggable; its draggable id is ``palette:<field_type>`` so the
 * top-level DndContext in AdminSchema.tsx can tell a palette-origin drag
 * apart from a card-reorder drag.
 */
import { useDraggable } from "@dnd-kit/core";
import { useT } from "@/lib/i18n";
import { FIELD_TYPES, PALETTE_GROUPS, type FieldTypeDef } from "./fieldTypes";

/** dnd-kit draggable id prefix for palette tiles. */
export const PALETTE_DRAG_PREFIX = "palette:";

function PaletteTile({ def }: { def: FieldTypeDef }) {
  const t = useT();
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({
    id: `${PALETTE_DRAG_PREFIX}${def.type}`,
    data: { kind: "palette", fieldType: def.type },
  });
  return (
    <button
      ref={setNodeRef}
      {...listeners}
      {...attributes}
      style={{
        display: "flex", alignItems: "center", gap: 6,
        width: "100%", padding: "6px 8px", fontSize: 14,
        textAlign: "left", color: "inherit",
        background: "var(--bg)",
        border: "1px solid var(--border)", borderRadius: 6,
        cursor: "grab", opacity: isDragging ? 0.4 : 1,
      }}
    >
      <span style={{ width: 16, textAlign: "center" }}>{def.icon}</span>
      <span>{t(def.labelKey)}</span>
    </button>
  );
}

export function ComponentPalette() {
  const t = useT();
  return (
    <div style={{
      width: 150, flexShrink: 0,
      border: "1px solid var(--border)", borderRadius: 8,
      background: "var(--panel)", overflow: "hidden",
      display: "flex", flexDirection: "column",
    }}>
      <div style={{
        padding: "10px 12px", fontSize: 14, fontWeight: 600,
        textTransform: "uppercase", color: "var(--muted)",
        borderBottom: "1px solid var(--border)", flexShrink: 0,
      }}>
        {t("admin.schema.builder.palette_heading")}
      </div>
      <div style={{
        display: "flex", flexDirection: "column", gap: 12,
        padding: 10, overflowY: "auto",
      }}>
        {PALETTE_GROUPS.map(group => {
          const defs = FIELD_TYPES.filter(d => d.group === group.key);
          return (
            <div key={group.key} style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              <div className="muted" style={{
                fontSize: 13, fontWeight: 600, textTransform: "uppercase",
              }}>
                {t(group.headingKey)}
              </div>
              {defs.map(def => <PaletteTile key={def.type} def={def} />)}
            </div>
          );
        })}
      </div>
    </div>
  );
}
