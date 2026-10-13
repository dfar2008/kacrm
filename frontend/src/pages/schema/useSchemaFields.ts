/**
 * useSchemaFields — data + draft state for the schema field builder.
 *
 * Owns:
 *   • loading / refreshing the module's persisted fields
 *   • local draft fields (id "draft-<n>", never persisted until saved)
 *   • optimistic reorder of non-hidden fields with rollback on failure
 *   • hide / show via PATCH is_hidden
 *   • post-save normalize: re-runs the reorder endpoint so persisted
 *     sort_order is contiguous 0..N-1 with no draft gaps.
 *
 * A "draft" is a CrmFieldMeta with ``id < 0`` and ``field_name`` starting
 * "draft-" until the user fills it in. Drafts are discarded on module
 * switch (the hook re-keys on ``module``).
 */
import { useCallback, useEffect, useRef, useState } from "react";
import {
  type CrmFieldMeta,
  getModuleFields,
  patchModuleField,
  reorderModuleFields,
} from "@/lib/api";

/** A field row that is still a local-only draft (not provisioned). */
export interface DraftField extends CrmFieldMeta {
  /** Always true for drafts; absent / false for persisted rows. */
  is_draft?: boolean;
}

let DRAFT_SEQ = 0;
/** Monotonic negative id for a new draft so React keys stay stable. */
function nextDraftId(): number { DRAFT_SEQ += 1; return -DRAFT_SEQ; }

export interface UseSchemaFields {
  fields: DraftField[];
  loading: boolean;
  /** Reload persisted fields from the backend, dropping all drafts. */
  refresh: () => Promise<void>;
  /** Append a fresh draft of ``fieldType``, return its draft id. */
  addDraft: (fieldType: string, atIndex: number) => number;
  /** Patch a draft's fields in place (used by the property panel). */
  updateDraft: (draftId: number, patch: Partial<DraftField>) => void;
  /** Discard a draft (pure front-end, no backend call). */
  removeDraft: (draftId: number) => void;
  /** Reorder non-hidden fields optimistically; rollback + throw on failure. */
  reorder: (orderedNames: string[]) => Promise<void>;
  /** Set is_hidden via PATCH; optimistic, rollback on failure. */
  setHidden: (fieldName: string, hidden: boolean) => Promise<void>;
  /** After a draft is provisioned: refresh, then normalize sort_order. */
  normalizeAfterSave: () => Promise<void>;
}

export function useSchemaFields(module: string | null): UseSchemaFields {
  const [fields, setFields] = useState<DraftField[]>([]);
  const [loading, setLoading] = useState(false);
  // Keep the latest fields in a ref so callbacks don't go stale.
  const fieldsRef = useRef<DraftField[]>([]);
  fieldsRef.current = fields;

  const applyFields = useCallback((next: DraftField[]) => {
    fieldsRef.current = next;
    setFields(next);
  }, []);

  const refresh = useCallback(async () => {
    if (!module) { applyFields([]); return; }
    setLoading(true);
    try {
      const r = await getModuleFields(module).catch(() => null);
      const persisted = (r?.fields || []).map(f => ({ ...f, is_draft: false }));
      // Sort by sort_order so the layout column reflects the record form.
      persisted.sort((a, b) => (a.sort_order ?? 0) - (b.sort_order ?? 0));
      applyFields(persisted);
    } finally {
      setLoading(false);
    }
  }, [module, applyFields]);

  // Re-load (and drop drafts) whenever the module changes.
  useEffect(() => { void refresh(); }, [refresh]);

  const addDraft = useCallback((fieldType: string, atIndex: number): number => {
    const id = nextDraftId();
    const seq = -id; // 1, 2, 3, ...
    const draft: DraftField = {
      id,
      is_draft: true,
      field_name: `field_${fieldType.toLowerCase()}_${seq}`,
      field_label: "",
      field_type: fieldType,
      options: null,
      sort_order: 0,
      is_hidden: false,
      is_required: false,
      is_searchable: false,
      is_readonly: false,
      is_system: false,
      description: "",
      default_value: null,
      field_config: null,
    };
    const next = [...fieldsRef.current];
    const clamped = Math.max(0, Math.min(atIndex, next.length));
    next.splice(clamped, 0, draft);
    applyFields(next);
    return id;
  }, [applyFields]);

  const updateDraft = useCallback((draftId: number, patch: Partial<DraftField>) => {
    applyFields(fieldsRef.current.map(f =>
      f.id === draftId ? { ...f, ...patch } : f,
    ));
  }, [applyFields]);

  const removeDraft = useCallback((draftId: number) => {
    applyFields(fieldsRef.current.filter(f => f.id !== draftId));
  }, [applyFields]);

  const reorder = useCallback(async (orderedNames: string[]) => {
    if (!module) return;
    const before = fieldsRef.current;
    // Optimistic: reorder the non-hidden persisted rows to match.
    const byName = new Map(before.map(f => [f.field_name, f] as const));
    const reordered: DraftField[] = [];
    for (const name of orderedNames) {
      const f = byName.get(name);
      if (f) reordered.push(f);
    }
    // Append everything not in orderedNames (hidden rows, drafts) at the end,
    // preserving their relative order.
    for (const f of before) {
      if (!orderedNames.includes(f.field_name)) reordered.push(f);
    }
    applyFields(reordered);
    try {
      // Only send persisted, non-hidden field_names.
      const persistedNonHidden = orderedNames.filter(name => {
        const f = byName.get(name);
        return f && !f.is_draft && !f.is_hidden;
      });
      await reorderModuleFields(module, persistedNonHidden);
    } catch (e) {
      applyFields(before); // rollback
      throw e;
    }
  }, [module, applyFields]);

  const setHidden = useCallback(async (fieldName: string, hidden: boolean) => {
    if (!module) return;
    const before = fieldsRef.current;
    applyFields(before.map(f =>
      f.field_name === fieldName ? { ...f, is_hidden: hidden } : f,
    ));
    try {
      await patchModuleField(module, fieldName, { is_hidden: hidden });
    } catch (e) {
      applyFields(before); // rollback
      throw e;
    }
  }, [module, applyFields]);

  const normalizeAfterSave = useCallback(async () => {
    if (!module) return;
    await refresh();
    // refresh() replaced fieldsRef.current synchronously via applyFields.
    const nonHidden = fieldsRef.current
      .filter(f => !f.is_draft && !f.is_hidden)
      .map(f => f.field_name);
    if (nonHidden.length > 1) {
      try { await reorderModuleFields(module, nonHidden); }
      catch { /* normalize is best-effort; sort_order stays as backend left it */ }
    }
  }, [module, refresh]);

  return {
    fields, loading, refresh,
    addDraft, updateDraft, removeDraft,
    reorder, setHidden, normalizeAfterSave,
  };
}
