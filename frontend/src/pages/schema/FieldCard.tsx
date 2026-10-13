/**
 * FieldCard — one field row in the FieldLayout column.
 *
 * A dnd-kit ``useSortable`` item: drag to reorder within the layout, or drag
 * into the hidden zone to hide. Clicking the card selects it (opens the
 * property panel). The sortable id is the field_name for persisted rows and
 * ``draft:<id>`` for drafts.
 */
import { useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { useT } from "@/lib/i18n";
import { fieldTypeDef } from "./fieldTypes";
import type { DraftField } from "./useSchemaFields";

/** Stable dnd-kit sortable id for a field row. */
export function fieldCardId(f: DraftField): string {
  return f.is_draft ? `draft:${f.id}` : f.field_name;
}

function Badge({ children, tone }: {
  children: React.ReactNode;
  tone?: "draft" | "muted";
}) {
  return (
    <span style={{
      fontSize: 13, padding: "1px 6px", borderRadius: 4,
      background: tone === "draft" ? "var(--panel)" : "var(--border)",
      color: tone === "draft" ? "var(--accent)" : "var(--muted)",
    }}>{children}</span>
  );
}

export function FieldCard({
  field, selected, onSelect,
}: {
  field: DraftField;
  selected: boolean;
  onSelect: () => void;
}) {
  const t = useT();
  const {
    attributes, listeners, setNodeRef, transform, transition, isDragging,
  } = useSortable({ id: fieldCardId(field), data: { kind: "card", field } });

  const def = fieldTypeDef(field.field_type);
  const displayLabel = field.display_label
    || field.field_label
    || (field.is_draft ? t("admin.schema.builder.draft_badge") : field.field_name);

  return (
    <div
      ref={setNodeRef}
      onClick={onSelect}
      style={{
        transform: CSS.Transform.toString(transform),
        transition,
        display: "flex", alignItems: "center", gap: 8,
        padding: "8px 10px", fontSize: 14,
        background: selected ? "var(--panel)" : "var(--bg)",
        border: "1px solid var(--border)",
        borderLeft: selected
          ? "3px solid var(--accent)"
          : "3px solid transparent",
        borderRadius: 6,
        cursor: "pointer",
        opacity: isDragging ? 0.4 : (field.is_hidden ? 0.5 : 1),
      }}
    >
      {/* Drag handle — only the handle starts a drag, so click still selects. */}
      <span
        {...listeners}
        {...attributes}
        style={{
          cursor: "grab", color: "var(--muted)", fontSize: 14,
          userSelect: "none", flexShrink: 0,
        }}
        onClick={e => e.stopPropagation()}
        title={t("admin.schema.builder.layout_heading")}
      >⋮⋮</span>
      <span style={{ width: 16, textAlign: "center", flexShrink: 0 }}>
        {def?.icon ?? "?"}
      </span>
      <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {displayLabel}
      </span>
      <span className="muted" style={{ fontSize: 13, flexShrink: 0 }}>
        {field.field_type}
      </span>
      {field.is_draft && <Badge tone="draft">{t("admin.schema.builder.draft_badge")}</Badge>}
      {field.is_system && <Badge>{t("admin.schema.fields.system_badge")}</Badge>}
      {field.is_required && <Badge>{t("admin.schema.fields.required_badge")}</Badge>}
      {field.is_hidden && <Badge>{t("admin.schema.fields.hidden_badge")}</Badge>}
    </div>
  );
}
