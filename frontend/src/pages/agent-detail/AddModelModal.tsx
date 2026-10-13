import { useState } from "react";
import { useT } from "@/lib/i18n";
import { _autoName } from "./modelPresets";

interface Props {
  onConfirm: (m: { id: string; name: string }) => void;
  onClose: () => void;
}

export function AddModelModal({ onConfirm, onClose }: Props) {
  const translate = useT();
  const [id, setId] = useState("");
  const [name, setName] = useState("");
  const placeholder = id ? _autoName(id) : translate("addmodel.name.placeholder_default");

  function confirm() {
    const mid = id.trim();
    if (!mid) return;
    onConfirm({ id: mid, name: name.trim() || _autoName(mid) });
  }

  return (
    <div
      onClick={onClose}
      style={{
        position: "fixed", inset: 0, zIndex: 10001,
        background: "var(--overlay)",
        display: "flex", alignItems: "center", justifyContent: "center",
      }}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={{
          width: 420, maxWidth: "calc(100vw - 32px)",
          background: "var(--panel-2)", border: "1px solid var(--border)",
          borderRadius: 12, padding: "20px 24px",
          position: "relative", color: "inherit",
          boxShadow: "var(--shadow-modal)",
        }}
      >
        <button onClick={onClose} aria-label="Close" className="icon-close"
                style={{
                  position: "absolute", top: 10, right: 12,
                  background: "none", border: "none", color: "var(--muted)",
                  fontSize: 22, cursor: "pointer",
                }}>×</button>
        <h3 style={{ margin: "0 0 12px", fontSize: 16 }}>{translate("addmodel.title")}</h3>

        <label style={{ fontSize: 14 }}>
          {translate("addmodel.name.label")} <small style={{ color: "var(--muted)", fontWeight: 400 }}>{translate("addmodel.name.hint")}</small>
        </label>
        <input value={name} onChange={e => setName(e.target.value)}
               placeholder={placeholder}
               style={{ width: "100%", marginTop: 4 }} />

        <label style={{ fontSize: 14, marginTop: 10, display: "block" }}>
          {translate("addmodel.id.label")} <small style={{ color: "var(--muted)", fontWeight: 400 }}>{translate("addmodel.id.hint")}</small>
        </label>
        <input value={id} onChange={e => setId(e.target.value)}
               placeholder={translate("addmodel.id.placeholder")}
               style={{ width: "100%", marginTop: 4 }} />

        <div style={{ display: "flex", gap: 8, marginTop: 16, justifyContent: "flex-end" }}>
          <button onClick={onClose}
                  style={{
                    padding: "8px 14px", background: "transparent",
                    border: "1px solid var(--border)", borderRadius: 7,
                    fontSize: 14, color: "var(--muted)", cursor: "pointer",
                  }}>{translate("addmodel.cancel")}</button>
          <button onClick={confirm} disabled={!id.trim()}
                  style={{
                    padding: "8px 14px", background: "var(--accent)", color: "var(--fg-on-accent)",
                    border: "none", borderRadius: 7,
                    fontSize: 14, cursor: id.trim() ? "pointer" : "not-allowed",
                    opacity: id.trim() ? 1 : 0.5,
                  }}>{translate("addmodel.submit")}</button>
        </div>
      </div>
    </div>
  );
}
