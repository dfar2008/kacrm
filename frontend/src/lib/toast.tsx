import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";

export type ToastType = "info" | "success" | "error";
interface Toast {
  id: number;
  type: ToastType;
  message: string;
}

interface ToastCtx {
  show: (message: string, type?: ToastType) => void;
}

const Ctx = createContext<ToastCtx>({ show: () => {} });

// Module-level proxy so non-component code can call `toast(...)` without
// threading the hook through every call site.
let _show: ToastCtx["show"] = () => {};
export function toast(message: string, type: ToastType = "info") { _show(message, type); }
export function useToast() { return useContext(Ctx); }

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const idRef = useRef(0);

  const show = useCallback((message: string, type: ToastType = "info") => {
    const id = ++idRef.current;
    setToasts(prev => [...prev, { id, type, message }]);
    setTimeout(() => setToasts(prev => prev.filter(t => t.id !== id)), 4000);
  }, []);

  useEffect(() => { _show = show; }, [show]);

  return (
    <Ctx.Provider value={{ show }}>
      {children}
      <div style={{
        position: "fixed",
        bottom: 24, right: 24,
        display: "flex", flexDirection: "column", gap: 8,
        zIndex: 9999,
        maxWidth: 360,
      }}>
        {toasts.map(t => {
          const palette =
            t.type === "success" ? { bg: "var(--success-soft)", border: "var(--success)", fg: "var(--success)" }
            : t.type === "error" ? { bg: "var(--danger-soft)", border: "var(--danger)", fg: "var(--danger)" }
            : { bg: "var(--panel-2)", border: "var(--border)", fg: "var(--fg-strong)" };
          return (
            <div key={t.id}
                 style={{
                   background: palette.bg,
                   border: `1px solid ${palette.border}`,
                   color: palette.fg,
                   borderRadius: 8,
                   padding: "10px 16px",
                   fontSize: 14,
                   boxShadow: "var(--shadow-popover)",
                 }}>
              {t.message}
            </div>
          );
        })}
      </div>
    </Ctx.Provider>
  );
}
