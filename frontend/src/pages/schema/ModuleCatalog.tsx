/**
 * ModuleCatalog — column 1 of the schema field builder.
 *
 * A 200px module list. Reuses the picker logic from the legacy
 * AdminSchema.tsx ModulePicker: prefers the locale-aware ``crm.module.<name>``
 * i18n key over the always-CN backend label, falls back to the bare name.
 */
import { useT, type DictKey } from "@/lib/i18n";
import { PROTECTED_MODULES } from "@/lib/api";
import { useIsAdmin } from "@/lib/currentUser";

export interface ModuleEntry {
  name: string;
  label: string;
}

export interface SubEntry { name: string; title?: string | null; detail_kind?: string; }

export function ModuleCatalog({
  modules, loading, active, onPick, onCreate, onWriteback, onApprovalConfig, onDelete,
  subdetails, onCreateSubdetail, activeParent, onPickSub,
}: {
  modules: ModuleEntry[];
  loading: boolean;
  active: string | null;
  onPick: (name: string) => void;
  onCreate?: () => void;
  onWriteback?: () => void;
  /** 打开审批流配置模态(per-module 结构配置,同写回规则)。 */
  onApprovalConfig?: () => void;
  /** 删除当前顶层模块(核心模块不显示该按钮)。 */
  onDelete?: () => void;
  subdetails?: SubEntry[];
  onCreateSubdetail?: () => void;
  /** 编辑子明细时,父模块名 —— 用于保持父模块在顶层列表高亮。默认=active。 */
  activeParent?: string | null;
  /** 点子明细的回调(保留父上下文);默认回退到 onPick。 */
  onPickSub?: (name: string) => void;
}) {
  const t = useT();
  const isAdmin = useIsAdmin();  // 审批流/汇总规则等管理员配置按钮按此隐藏
  const topActive = activeParent ?? active;
  // 删除按钮只在选中【顶层、非核心】模块时出现(核心模块后端也会拒删)。
  const canDelete = !!topActive && !PROTECTED_MODULES.has(topActive);
  const pickSub = onPickSub ?? onPick;
  return (
    <div style={{
      width: 200, flexShrink: 0,
      border: "1px solid var(--border)", borderRadius: 8,
      background: "var(--panel, #16181d)", overflow: "hidden",
      display: "flex", flexDirection: "column",
    }}>
      <div style={{
        padding: "10px 12px", fontSize: 14, fontWeight: 600,
        textTransform: "uppercase", color: "var(--muted)",
        borderBottom: "1px solid var(--border)", flexShrink: 0,
      }}>
        {t("admin.schema.builder.catalog_heading")}
      </div>
      {loading && (
        <div className="muted" style={{ padding: 12, fontSize: 14 }}>
          {t("admin.schema.module_picker.loading")}
        </div>
      )}
      <div style={{ display: "flex", flexDirection: "column", overflowY: "auto" }}>
        {modules.map(m => {
          const on = m.name === topActive;
          // 自定义模块无静态 crm.module.<name> key —— t() 缺失会返回 key 本身,
          // 故判等回退到后端 label(collections.title)。
          const mkey = `crm.module.${m.name}`;
          const translated = t(mkey as DictKey);
          const label = translated === mkey ? (m.label || m.name) : translated;
          return (
            <button
              key={m.name}
              onClick={() => onPick(m.name)}
              style={{
                textAlign: "left", padding: "8px 12px", fontSize: 14,
                background: on ? "var(--accent-soft-strong)" : "transparent",
                color: "inherit", border: "none",
                borderLeft: on
                  ? "3px solid var(--accent, #4c8dff)"
                  : "3px solid transparent",
                cursor: "pointer",
              }}
            >
              {label}
              <div className="muted" style={{ fontSize: 13 }}>{m.name}</div>
            </button>
          );
        })}
        {/* 子明细分组:当前模块的子明细,点进去用同一字段 builder 管它的字段 */}
        {(subdetails && subdetails.length > 0) && (
          <div style={{ borderTop: "1px solid var(--border)", marginTop: 4 }}>
            <div style={{ padding: "6px 12px 2px", fontSize: 13, textTransform: "uppercase",
              color: "var(--muted)", letterSpacing: 0.5 }}>
              {t("admin.schema.builder.subdetails")}
            </div>
            {subdetails.map(s => {
              const on = s.name === active;
              return (
                <button key={s.name} onClick={() => pickSub(s.name)}
                  style={{
                    textAlign: "left", padding: "6px 12px 6px 20px", fontSize: 14,
                    background: on ? "var(--accent-soft-strong)" : "transparent",
                    color: "inherit", border: "none", width: "100%",
                    borderLeft: on ? "3px solid var(--accent, #4c8dff)" : "3px solid transparent",
                    cursor: "pointer",
                  }}>
                  ↳ {s.title || s.name}
                  <span className="muted" style={{ fontSize: 13, marginLeft: 4 }}>
                    {s.detail_kind === "product" ? "产品行" : ""}
                  </span>
                </button>
              );
            })}
          </div>
        )}
      </div>
      {onCreateSubdetail && active && (
        <button
          onClick={onCreateSubdetail}
          style={{
            margin: "auto 8px 6px", padding: "7px 10px", fontSize: 14,
            background: "transparent", color: "var(--muted)",
            border: "1px dashed var(--border)", borderRadius: 6,
            cursor: "pointer", flexShrink: 0,
          }}
        >
          + {t("admin.schema.builder.new_subdetail")}
        </button>
      )}
      {onCreate && (
        <button
          onClick={onCreate}
          style={{
            margin: "0 8px 8px", padding: "8px 10px", fontSize: 14,
            background: "transparent", color: "var(--accent, #4c8dff)",
            border: "1px dashed var(--border)", borderRadius: 6,
            cursor: "pointer", flexShrink: 0,
          }}
        >
          + {t("admin.schema.builder.new_module")}
        </button>
      )}
      {onWriteback && active && isAdmin && (
        <button
          onClick={onWriteback}
          style={{
            margin: "0 8px 8px", padding: "8px 10px", fontSize: 14,
            background: "transparent", color: "var(--muted)",
            border: "1px dashed var(--border)", borderRadius: 6,
            cursor: "pointer", flexShrink: 0,
          }}
        >
          ∑ {t("admin.schema.writeback.title")}
        </button>
      )}
      {onApprovalConfig && active && isAdmin && (
        <button
          onClick={onApprovalConfig}
          style={{
            margin: "0 8px 8px", padding: "8px 10px", fontSize: 14,
            background: "transparent", color: "var(--muted)",
            border: "1px dashed var(--border)", borderRadius: 6,
            cursor: "pointer", flexShrink: 0,
          }}
        >
          ✓ {t("approvalConfig.title")}
        </button>
      )}
      {onDelete && canDelete && (
        <button
          onClick={onDelete}
          style={{
            margin: "0 8px 10px", padding: "8px 10px", fontSize: 14,
            background: "transparent", color: "var(--danger)",
            border: "1px dashed var(--danger)", borderRadius: 6,
            cursor: "pointer", flexShrink: 0,
          }}
        >
          🗑 {t("admin.schema.delete.button")}
        </button>
      )}
    </div>
  );
}
