/**
 * /admin/schema → 字段管理 tab — visual field builder.
 *
 * Four-column layout (see design spec §9.2):
 *   ModuleCatalog (200) | ComponentPalette (150) | FieldLayout (flex) | FieldProperties (320)
 *
 * Top-level owner of:
 *   • module list load
 *   • the dnd-kit DndContext orchestrating all three drag interactions
 *   • selection state (which field's properties are shown)
 *
 * AdminSchemaHub imports ``AdminSchemaPage`` from this file.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  DndContext,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import { ApiError, listModules, listSubdetails } from "@/lib/api";
import { toast } from "@/lib/toast";
import { useT } from "@/lib/i18n";
import { ModuleCatalog, type ModuleEntry, type SubEntry } from "./ModuleCatalog";
import { NewModuleModal } from "./NewModuleModal";
import { DeleteModuleModal } from "./DeleteModuleModal";
import { NewSubdetailModal } from "./NewSubdetailModal";
import { WritebackRulesModal } from "./WritebackRulesModal";
import { ApprovalConfigModal } from "./ApprovalConfigModal";
import { ComponentPalette, PALETTE_DRAG_PREFIX } from "./ComponentPalette";
import { fieldTypeDef } from "./fieldTypes";
import { defaultAiFillForType } from "./AiAutofill";
import { FieldLayout, HIDDEN_ZONE_ID, LAYOUT_ZONE_ID } from "./FieldLayout";
import { fieldCardId } from "./FieldCard";
import { FieldProperties } from "./FieldProperties";
import { useSchemaFields, type DraftField } from "./useSchemaFields";
import { SchemaAssistant } from "./SchemaAssistant";

function errMsg(e: unknown): string {
  if (e instanceof ApiError) {
    return typeof e.detail === "string" ? e.detail : JSON.stringify(e.detail);
  }
  if (e instanceof Error) return e.message;
  return String(e);
}

export function AdminSchemaPage({ module }: { module?: string }) {
  const t = useT();
  const [modules, setModules] = useState<ModuleEntry[]>([]);
  const [modulesLoading, setModulesLoading] = useState(true);
  const [activeMod, setActiveMod] = useState<string | null>(module ?? null);
  // 父模块:决定顶层列表高亮 + 子明细组取谁的子明细。编辑顶层模块时=activeMod;
  // 编辑子明细时仍指向父模块,保持上下文(否则点进子明细会丢父高亮+子明细组)。
  const [activeParent, setActiveParent] = useState<string | null>(module ?? null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [showNew, setShowNew] = useState(false);
  const [showWriteback, setShowWriteback] = useState(false);
  const [showApproval, setShowApproval] = useState(false);
  const [showNewSub, setShowNewSub] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);
  const [subdetails, setSubdetails] = useState<SubEntry[]>([]);

  const refetchSubs = useCallback((mod: string | null) => {
    if (!mod) { setSubdetails([]); return; }
    listSubdetails(mod)
      .then(r => setSubdetails((r.subdetails || []).map(s => ({
        name: s.name, title: s.title, detail_kind: s.detail_kind }))))
      .catch(() => setSubdetails([]));
  }, []);

  // 子明细组始终取「父模块」的子明细(编辑子明细时父不变,组保持)。
  useEffect(() => { refetchSubs(activeParent); }, [activeParent, refetchSubs]);

  // 选顶层模块:父=自身,进该模块。
  const pickModule = useCallback((m: string) => {
    setActiveParent(m); setActiveMod(m); setSelectedId(null);
  }, []);
  // 选子明细:只切 activeMod,父上下文不变。
  const pickSub = useCallback((s: string) => {
    setActiveMod(s); setSelectedId(null);
  }, []);

  const refetchModules = useCallback((pick?: string) => {
    listModules()
      .then(r => {
        const list = (r.data || []).map(m => ({ name: m.name, label: m.label }));
        setModules(list);
        if (pick) { setActiveParent(pick); setActiveMod(pick); }
        else if (!activeMod && list.length) { setActiveParent(list[0].name); setActiveMod(list[0].name); }
      })
      .catch(e => toast(errMsg(e), "error"))
      .finally(() => setModulesLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeMod]);

  useEffect(() => {
    refetchModules();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <div style={{ padding: "16px 24px 8px" }}>
        <h2 style={{ margin: 0 }}>{t("admin.schema.title")}</h2>
        <p className="muted" style={{ fontSize: 14, margin: "4px 0 0" }}>
          {t("admin.schema.subtitle")}
        </p>
      </div>
      {activeMod ? (
        <SchemaBuilder
          key={activeMod}
          module={activeMod}
          modules={modules}
          modulesLoading={modulesLoading}
          activeMod={activeMod}
          activeParent={activeParent}
          onPickModule={pickModule}
          onPickSub={pickSub}
          onCreate={() => setShowNew(true)}
          onWriteback={() => setShowWriteback(true)}
          onApprovalConfig={() => setShowApproval(true)}
          onDelete={() => activeParent && setDeleteTarget(activeParent)}
          subdetails={subdetails}
          onCreateSubdetail={() => setShowNewSub(true)}
          selectedId={selectedId}
          onSelect={setSelectedId}
        />
      ) : (
        <div style={{ flex: 1, display: "flex", gap: 16, padding: "8px 24px 24px" }}>
          <ModuleCatalog
            modules={modules}
            loading={modulesLoading}
            active={activeMod}
            activeParent={activeParent}
            onPick={pickModule}
            onPickSub={pickSub}
            onCreate={() => setShowNew(true)}
            onWriteback={() => setShowWriteback(true)}
          onApprovalConfig={() => setShowApproval(true)}
            onDelete={() => activeParent && setDeleteTarget(activeParent)}
            subdetails={subdetails}
            onCreateSubdetail={() => setShowNewSub(true)}
          />
          <div className="muted" style={{ flex: 1, padding: 32, textAlign: "center" }}>
            {t("admin.schema.builder.no_module")}
          </div>
        </div>
      )}
      {showNew && (
        <NewModuleModal
          onClose={() => setShowNew(false)}
          onCreated={(name) => { setShowNew(false); setSelectedId(null); refetchModules(name); }}
        />
      )}
      {showWriteback && (activeParent || activeMod) && (
        <WritebackRulesModal
          module={(activeParent || activeMod) as string}
          onClose={() => setShowWriteback(false)}
        />
      )}
      {showApproval && (activeParent || activeMod) && (
        <ApprovalConfigModal
          module={(activeParent || activeMod) as string}
          onClose={() => setShowApproval(false)}
        />
      )}
      {showNewSub && (activeParent || activeMod) && (
        <NewSubdetailModal
          parent={(activeParent || activeMod) as string}
          onClose={() => setShowNewSub(false)}
          onCreated={(name) => {
            setShowNewSub(false);
            refetchSubs(activeParent || activeMod);  // 刷父模块的子明细组
            setSelectedId(null);
            setActiveMod(name);  // 进入新子明细管字段;activeParent 不变,保持上下文
          }}
        />
      )}
      {deleteTarget && (
        <DeleteModuleModal
          name={deleteTarget}
          subdetails={subdetails.map(s => s.name)}
          onClose={() => setDeleteTarget(null)}
          onDeleted={(name) => {
            setDeleteTarget(null);
            toast(t("admin.schema.delete.success", { name }), "success");
            // 删的若是当前模块,清空选中并回到首个模块。
            setActiveMod(null); setActiveParent(null); setSelectedId(null);
            refetchModules();
          }}
        />
      )}
    </div>
  );
}

function SchemaBuilder({
  module, modules, modulesLoading, activeMod, activeParent, onPickModule, onPickSub,
  onCreate, onWriteback, onApprovalConfig, onDelete, subdetails, onCreateSubdetail, selectedId, onSelect,
}: {
  module: string;
  modules: ModuleEntry[];
  modulesLoading: boolean;
  activeMod: string;
  activeParent?: string | null;
  onPickModule: (m: string) => void;
  onPickSub?: (m: string) => void;
  onCreate?: () => void;
  onWriteback?: () => void;
  onApprovalConfig?: () => void;
  onDelete?: () => void;
  subdetails?: SubEntry[];
  onCreateSubdetail?: () => void;
  selectedId: string | null;
  onSelect: (id: string | null) => void;
}) {
  const schema = useSchemaFields(module);
  const [assistantOpen, setAssistantOpen] = useState(true);
  // Require a small drag distance so a click on the handle still selects.
  const sensors = useSensors(useSensor(PointerSensor, {
    activationConstraint: { distance: 4 },
  }));

  const selectedField = useMemo<DraftField | null>(
    () => schema.fields.find(f => fieldCardId(f) === selectedId) || null,
    [schema.fields, selectedId],
  );

  async function handleDragEnd(ev: DragEndEvent) {
    const { active, over } = ev;
    if (!over) return;
    const activeId = String(active.id);
    const overId = String(over.id);

    // ── 1. Palette → layout: create a draft at the drop point. ──────────
    if (activeId.startsWith(PALETTE_DRAG_PREFIX)) {
      const fieldType = activeId.slice(PALETTE_DRAG_PREFIX.length);
      const visible = schema.fields.filter(f => !f.is_hidden);
      let atIndex = visible.length;
      const overVisibleIdx = visible.findIndex(f => fieldCardId(f) === overId);
      if (overVisibleIdx >= 0) atIndex = overVisibleIdx;
      // AI-autofill tile: provision the preset's output type, then pre-set
      // field_config.ai_fill so the field lands already in AI mode (Attio model).
      const preset = fieldTypeDef(fieldType)?.aiPreset;
      if (preset) {
        const draftId = schema.addDraft(preset.outputType, atIndex);
        schema.updateDraft(draftId, {
          field_config: {
            ai_fill: { ...defaultAiFillForType(preset.outputType), mode: preset.mode },
          },
        });
        onSelect(`draft:${draftId}`);
        return;
      }
      const draftId = schema.addDraft(fieldType, atIndex);
      onSelect(`draft:${draftId}`);
      return;
    }

    // The active item is a field card (sortable id = field_name or draft:<id>).
    const activeField = schema.fields.find(f => fieldCardId(f) === activeId);
    if (!activeField) return;

    // ── 3a. Card → hidden zone: hide a persisted field. ─────────────────
    if (overId === HIDDEN_ZONE_ID) {
      if (activeField.is_draft) {
        // Drafts can't be "hidden" server-side — discard instead.
        schema.removeDraft(activeField.id);
        if (selectedId === activeId) onSelect(null);
        return;
      }
      if (activeField.is_hidden) return;
      try {
        await schema.setHidden(activeField.field_name, true);
        toast("admin.schema.toast.field_hidden", "success");
      } catch (e) { toast(errMsg(e), "error"); }
      return;
    }

    // ── 3b. Hidden card → layout zone / a visible card: restore it. ─────
    if (activeField.is_hidden) {
      const overIsVisibleCard = schema.fields.some(
        f => fieldCardId(f) === overId && !f.is_hidden,
      );
      if (overId === LAYOUT_ZONE_ID || overIsVisibleCard) {
        try {
          await schema.setHidden(activeField.field_name, false);
          toast("admin.schema.toast.field_shown", "success");
        } catch (e) { toast(errMsg(e), "error"); }
      }
      return;
    }

    // ── 2. Card ↔ card: reorder within the visible list. ────────────────
    if (activeId === overId) return;
    const visible = schema.fields.filter(f => !f.is_hidden);
    const fromIdx = visible.findIndex(f => fieldCardId(f) === activeId);
    const toIdx = visible.findIndex(f => fieldCardId(f) === overId);
    if (fromIdx < 0 || toIdx < 0) return;
    const reordered = [...visible];
    const [moved] = reordered.splice(fromIdx, 1);
    reordered.splice(toIdx, 0, moved);
    // Drafts have no sort_order to persist — only send persisted names.
    const orderedNames = reordered.map(f => f.field_name);
    try {
      await schema.reorder(orderedNames);
    } catch (e) {
      toast(errMsg(e) || "admin.schema.toast.reorder_failed", "error");
    }
  }

  return (
    <DndContext sensors={sensors} onDragEnd={handleDragEnd}>
      {/* Four-column builder row. The assistant lives at the right edge:
          a slide-in panel when open, a narrow icon rail when collapsed. */}
      <div style={{
        flex: 1, display: "flex", gap: 16,
        padding: "8px 24px 24px", overflowX: "auto", minHeight: 0,
      }}>
        <ModuleCatalog
          modules={modules}
          loading={modulesLoading}
          active={activeMod}
          activeParent={activeParent}
          onPick={onPickModule}
          onPickSub={onPickSub}
          onCreate={onCreate}
          onWriteback={onWriteback}
          onApprovalConfig={onApprovalConfig}
          onDelete={onDelete}
          subdetails={subdetails}
          onCreateSubdetail={onCreateSubdetail}
        />
        <ComponentPalette />
        <FieldLayout
          fields={schema.fields}
          selectedId={selectedId}
          onSelect={(f) => onSelect(fieldCardId(f))}
        />
        <FieldProperties
          module={module}
          field={selectedField}
          schema={schema}
          onClearSelection={() => onSelect(null)}
          onSelectField={(id) => onSelect(id)}
        />
        {assistantOpen ? (
          <SchemaAssistant
            module={module}
            onClose={() => setAssistantOpen(false)}
            onApplied={schema.refresh}
          />
        ) : (
          <AssistantRail onOpen={() => setAssistantOpen(true)} />
        )}
      </div>
    </DndContext>
  );
}

/** Collapsed-state assistant affordance — a narrow icon rail pinned to
 *  the right edge of the builder. Clicking it opens the full panel. */
function AssistantRail({ onOpen }: { onOpen: () => void }) {
  const t = useT();
  return (
    <div
      onClick={onOpen}
      title={t("admin.schema.assistant.title")}
      style={{
        width: 44, flexShrink: 0,
        border: "1px solid var(--border)", borderRadius: 8,
        background: "var(--panel)",
        display: "flex", flexDirection: "column", alignItems: "center",
        paddingTop: 12, gap: 10, cursor: "pointer",
      }}
    >
      <span style={{
        width: 28, height: 28, borderRadius: 8,
        background: "var(--accent)", color: "var(--bg)",
        display: "inline-flex", alignItems: "center", justifyContent: "center",
        fontSize: 14, fontWeight: 700, flexShrink: 0,
      }}>
        ✨
      </span>
    </div>
  );
}
