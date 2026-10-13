import { useEffect, useState } from "react";
import type { SegmentItem } from "@/lib/api";
import { getSegmentsConfig, updateSegmentsConfig } from "@/lib/api";
import { toast } from "@/lib/toast";
import { useT } from "@/lib/i18n";

/**
 * Admin-only modal for managing a module's built-in segments.
 *
 * The backend returns all five segments (today / overdue / new /
 * dormant / all) with any per-module override already merged onto the
 * defaults and pre-sorted by ``order``. We edit a local copy and on save
 * re-number ``order`` 1..N to match the on-screen sequence.
 *
 * Only ``new`` and ``dormant`` carry a ``params.days`` threshold — the
 * other segments have no tunable parameter, so their threshold input is
 * hidden.
 *
 * "Restore default" sends ``null`` to drop the override entirely.
 */

interface Props {
  open: boolean;
  onClose: () => void;
  module: string;
  /** 保存后回调,父组件重新拉 segments 渲染 */
  onSaved: () => void;
}

/** Segments that expose a day-threshold parameter. */
const THRESHOLD_IDS = new Set(["new", "dormant"]);

export function SegmentsConfig(props: Props) {
  const { open, onClose, module, onSaved } = props;
  const t = useT();

  const [items, setItems] = useState<SegmentItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);

  // Load current config whenever the modal opens (or module changes).
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoading(true);
    getSegmentsConfig(module)
      .then((res) => {
        if (!cancelled) setItems(res.items);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [open, module]);

  // ESC to close.
  useEffect(() => {
    if (!open) return;
    const h = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [open, onClose]);

  if (!open) return null;

  function patch(id: string, next: Partial<SegmentItem>) {
    setItems((prev) => prev.map((it) => (it.id === id ? { ...it, ...next } : it)));
  }

  function move(id: string, delta: -1 | 1) {
    setItems((prev) => {
      const i = prev.findIndex((it) => it.id === id);
      const j = i + delta;
      if (i < 0 || j < 0 || j >= prev.length) return prev;
      const out = prev.slice();
      [out[i], out[j]] = [out[j], out[i]];
      return out;
    });
  }

  function setDays(id: string, raw: string) {
    const n = Math.max(1, Math.floor(Number(raw) || 0));
    patch(id, { params: { days: n } });
  }

  async function save() {
    setSaving(true);
    try {
      // Re-number order 1..N to match on-screen sequence.
      const payload: SegmentItem[] = items.map((it, idx) => ({
        ...it,
        order: idx + 1,
      }));
      await updateSegmentsConfig(module, { items: payload });
      toast(t("segcfg.saved"), "success");
      onSaved();
      onClose();
    } finally {
      setSaving(false);
    }
  }

  async function restore() {
    setSaving(true);
    try {
      await updateSegmentsConfig(module, null);
      toast(t("segcfg.saved"), "success");
      onSaved();
      onClose();
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      {/* Backdrop */}
      <div
        onClick={onClose}
        style={{
          position: "fixed", inset: 0, background: "var(--overlay)",
          zIndex: 60,
        }}
      />
      {/* Modal */}
      <div
        role="dialog"
        aria-label={t("segcfg.title")}
        style={{
          position: "fixed",
          top: "50%", left: "50%", transform: "translate(-50%, -50%)",
          width: "min(560px, 92vw)", maxHeight: "86vh",
          background: "var(--bg)", color: "var(--fg)",
          border: "1px solid var(--border)", borderRadius: 10,
          boxShadow: "var(--shadow-modal)",
          zIndex: 70,
          display: "flex", flexDirection: "column",
        }}
      >
        {/* Header */}
        <div style={{
          display: "flex", justifyContent: "space-between", alignItems: "center",
          padding: "12px 16px", borderBottom: "1px solid var(--border)",
        }}>
          <div>
            <div style={{ fontSize: 15, fontWeight: 600 }}>
              {t("segcfg.title")}
            </div>
            <div className="muted small" style={{ marginTop: 2 }}>
              {t("segcfg.subtitle")}
            </div>
          </div>
          <button className="ghost" onClick={onClose} style={{ fontSize: 18, padding: "2px 10px" }}>
            ×
          </button>
        </div>

        {/* Body */}
        <div style={{ padding: 16, overflow: "auto", flex: 1 }}>
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {items.map((it, i) => (
              <div
                key={it.id}
                className="row"
                style={{
                  gap: 8, alignItems: "center",
                  padding: "8px 10px",
                  border: "1px solid var(--border)", borderRadius: 6,
                  background: "var(--card-bg)",
                }}
              >
                <input
                  type="checkbox"
                  checked={it.enabled}
                  onChange={(e) => patch(it.id, { enabled: e.target.checked })}
                  title={t("segcfg.enabled")}
                />
                <input
                  type="text"
                  value={it.label}
                  onChange={(e) => patch(it.id, { label: e.target.value })}
                  style={{
                    flex: 1, minWidth: 80, padding: "4px 8px",
                    border: "1px solid var(--border)", borderRadius: 6,
                    background: "transparent", color: "inherit",
                  }}
                />
                {THRESHOLD_IDS.has(it.id) && (
                  <label className="row small" style={{ gap: 4, alignItems: "center" }}>
                    <span className="muted">{t("segcfg.threshold_days")}</span>
                    <input
                      type="number"
                      min={1}
                      step={1}
                      value={it.params?.days ?? 1}
                      onChange={(e) => setDays(it.id, e.target.value)}
                      style={{
                        width: 64, padding: "4px 6px",
                        border: "1px solid var(--border)", borderRadius: 6,
                        background: "transparent", color: "inherit",
                      }}
                    />
                  </label>
                )}
                <button
                  className="ghost"
                  onClick={() => move(it.id, -1)}
                  disabled={i === 0}
                  title={t("segcfg.move_up")}
                  style={{ fontSize: 14, padding: "2px 6px" }}
                >↑</button>
                <button
                  className="ghost"
                  onClick={() => move(it.id, +1)}
                  disabled={i === items.length - 1}
                  title={t("segcfg.move_down")}
                  style={{ fontSize: 14, padding: "2px 6px" }}
                >↓</button>
              </div>
            ))}
          </div>
        </div>

        {/* Footer */}
        <div style={{
          display: "flex", justifyContent: "space-between", alignItems: "center",
          padding: "10px 16px", borderTop: "1px solid var(--border)",
        }}>
          <button
            className="ghost"
            onClick={restore}
            disabled={saving}
            title={t("segcfg.restore")}
            style={{ fontSize: 14, color: "var(--muted)" }}
          >
            {t("segcfg.restore")}
          </button>
          <div className="row" style={{ gap: 8 }}>
            <button className="ghost" onClick={onClose} disabled={saving}>
              {t("common.cancel")}
            </button>
            <button
              className="primary"
              onClick={save}
              disabled={saving || loading}
            >
              {t("common.save")}
            </button>
          </div>
        </div>
      </div>
    </>
  );
}
