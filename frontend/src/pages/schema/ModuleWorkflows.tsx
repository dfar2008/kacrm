/**
 * 工作流流程渲染件(Attio 式横向流程图):触发卡 → [条件] → 动作卡,横向连线。
 *
 * 供 AdminWorkflowsPage 复用(WorkflowDetail / triggerText / WorkflowRuns)。
 */
import type React from "react";
import { useEffect, useState } from "react";
import {
  type CrmWorkflow, type CrmWorkflowRun, type CrmFieldMeta,
  listModuleWorkflowRuns, undoWorkflowRun, getModuleFields,
} from "@/lib/api";
import { useT } from "@/lib/i18n";
import { toast } from "@/lib/toast";
import { TriggerBlockEditor, ConditionBlockEditor, ActionBlockEditor, OP_I18N, type Op } from "./WorkflowEditor";

/** 触发摘要:event:created/updated 或 schedule:cron。 */
export function triggerText(t: ReturnType<typeof useT>, w: CrmWorkflow): string {
  if (w.trigger_type === "schedule") {
    return `${t("admin.workflows.trigger.schedule")} · ${w.schedule_cron || "—"}`;
  }
  const on = w.trigger_on === "create"
    ? t("admin.workflows.trigger.on_create")
    : w.trigger_on === "update"
      ? t("admin.workflows.trigger.on_update")
      : t("admin.workflows.trigger.on_any");
  return `${t("admin.workflows.trigger.event")} · ${on}`;
}

type WfAction = {
  kind?: string; executor?: string; params?: unknown; instruction?: string;
  condition?: { all?: unknown; any?: unknown };
};
type CondClause = { field?: string; op?: string; value?: unknown };

/** 单个动作的标题:agent / update_field / … */
function actionTitle(t: ReturnType<typeof useT>, a: WfAction): string {
  if (a.executor === "agent") return t("admin.workflows.action.agent");
  const kind = String(a.kind || "");
  const key = `admin.workflows.action.${kind}`;
  const raw = t(key as never);
  return raw === key ? (kind || key) : raw;  // 缺映射回退原值,不漏 i18n key 路径
}

/** 动作图标:emoji。 */
function actionIcon(a: WfAction): string {
  if (a.executor === "agent") return "🤖";
  switch (String(a.kind || "")) {
    case "update_field": return "✏️";
    case "create_task": return "✅";
    case "create_record": return "➕";
    default: return "⚙️";
  }
}

/** 字段键 → 中文标签(无映射则回退键名)。i18n:不让字段键漏成英文。 */
type FieldLabels = Record<string, string> | undefined;
function flabel(fl: FieldLabels, key: unknown): string {
  const k = key != null ? String(key) : "—";
  return (fl && fl[k]) || k;
}

/** 动作描述:agent 显示指令(截断),update_field 显示字段→值,否则本地化动作名。 */
function actionDesc(t: ReturnType<typeof useT>, a: WfAction, fl?: FieldLabels): string {
  const params = (a.params && typeof a.params === "object" ? a.params : {}) as Record<string, unknown>;
  if (a.executor === "agent") {
    const raw = a.instruction ?? params.instruction;
    const instr = typeof raw === "string" ? raw.trim() : "";
    if (!instr) return "";
    return instr.length > 120 ? `${instr.slice(0, 120)}…` : instr;
  }
  if (String(a.kind || "") === "update_field") {
    const field = flabel(fl, params.field);
    const value = params.value != null ? String(params.value) : "—";
    return t("admin.workflows.detail.update_field", { field, value });
  }
  // 其余动作:显示本地化动作名,不漏原始 kind 英文。
  return actionTitle(t, a);
}

/** 把一条 condition 子句渲染成人话,如「行业」变化时 / 成交金额 ≥ 500000。 */
function clauseText(t: ReturnType<typeof useT>, c: CondClause, fl?: FieldLabels): string {
  const field = flabel(fl, c.field);
  const op = String(c.op || "");
  if (op === "changed") return t("admin.workflows.detail.cond_changed", { field });
  if (op === "present") return t("admin.workflows.detail.cond_present", { field });
  const value = c.value != null ? String(c.value) : "";
  // op 是引擎符号(>=、=…),i18n key 用命名段(gte、eq…),经 OP_I18N 映射;缺则回退原值。
  const opSeg = OP_I18N[op as Op] ?? op;
  const opKey = `admin.workflows.detail.op.${opSeg}`;
  const opRaw = t(opKey as never);
  const opLabel = opRaw === opKey ? op : opRaw;
  return t("admin.workflows.detail.cond_compare", { field, op: opLabel, value });
}

/** 条件摘要:any 用「或」连,all 用「且」连;无条件返回空串。 */
function conditionText(t: ReturnType<typeof useT>, w: CrmWorkflow, fl?: FieldLabels): string {
  const cond = w.condition;
  if (!cond) return "";
  const all = Array.isArray(cond.all) ? (cond.all as CondClause[]) : [];
  const any = Array.isArray(cond.any) ? (cond.any as CondClause[]) : [];
  const parts: string[] = [];
  if (all.length) parts.push(all.map((c) => clauseText(t, c, fl)).join(t("admin.workflows.detail.join_all")));
  if (any.length) parts.push(any.map((c) => clauseText(t, c, fl)).join(t("admin.workflows.detail.join_any")));
  return parts.join(t("admin.workflows.detail.join_all"));
}

/** 单个动作守卫(condition.all / any)渲染成人话;无守卫返回空串。 */
function guardText(t: ReturnType<typeof useT>, a: WfAction, fl?: FieldLabels): string {
  const cond = a.condition;
  if (!cond) return "";
  const all = Array.isArray(cond.all) ? (cond.all as CondClause[]) : [];
  const any = Array.isArray(cond.any) ? (cond.any as CondClause[]) : [];
  const list = all.length ? all : any;
  if (!list.length) return "";
  const join = all.length ? t("admin.workflows.detail.join_all") : t("admin.workflows.detail.join_any");
  return list.map((c) => clauseText(t, c, fl)).join(join);
}

/** 动作图标的柔色背景(按类型)。 */
function iconBg(a: WfAction): string {
  if (a.executor === "agent") return "color-mix(in srgb, #8b5cf6 18%, transparent)";
  switch (String(a.kind || "")) {
    case "update_field": return "color-mix(in srgb, #22c55e 18%, transparent)";
    case "create_task":  return "color-mix(in srgb, #3b82f6 18%, transparent)";
    case "create_record":return "color-mix(in srgb, #f59e0b 20%, transparent)";
    default:             return "color-mix(in srgb, var(--muted) 22%, transparent)";
  }
}

/** 流程卡片:图标色块 + eyebrow + 标题 + 描述(Attio 风格,定宽块)。
 *  onEdit 存在时,卡片右上角出现一个低调的 ✎ 铅笔按钮(点块改入口)。 */
function FlowCard({ icon, bg, eyebrow, title, desc, onEdit, editLabel, editing }: {
  icon: string; bg: string; eyebrow?: string; title: string; desc?: string;
  onEdit?: () => void; editLabel?: string; editing?: boolean;
}) {
  return (
    <div style={{
      position: "relative",
      flexShrink: 0, width: 230, boxSizing: "border-box",
      display: "flex", flexDirection: "column", gap: 8,
      padding: "13px 15px", borderRadius: 12,
      border: editing ? "1px solid var(--accent)" : "1px solid var(--border)",
      background: "var(--panel)",
      boxShadow: editing
        ? "0 0 0 2px color-mix(in srgb, var(--accent) 22%, transparent)"
        : "0 1px 3px rgba(0,0,0,0.06), 0 1px 1px rgba(0,0,0,0.04)",
    }}>
      {onEdit && (
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); onEdit(); }}
          aria-pressed={editing}
          title={editLabel}
          style={{
            position: "absolute", top: 7, right: 7,
            display: "inline-flex", alignItems: "center", justifyContent: "center",
            width: 22, height: 22, borderRadius: 6, cursor: "pointer", lineHeight: 1,
            fontSize: 11.5, padding: 0,
            border: "1px solid var(--border)",
            color: editing ? "var(--accent)" : "var(--muted)",
            background: editing ? "color-mix(in srgb, var(--accent) 14%, transparent)" : "var(--bg)",
          }}
        >✎</button>
      )}
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <div style={{
          flexShrink: 0, width: 32, height: 32, borderRadius: 9,
          display: "flex", alignItems: "center", justifyContent: "center",
          background: bg, fontSize: 16,
        }}>{icon}</div>
        <div style={{ minWidth: 0 }}>
          {eyebrow && (
            <div style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: 0.5,
                          textTransform: "uppercase", color: "var(--accent)", marginBottom: 1 }}>{eyebrow}</div>
          )}
          <div style={{ fontSize: 14, fontWeight: 600, lineHeight: 1.25,
                        overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{title}</div>
        </div>
      </div>
      {desc && (
        <div className="muted" style={{
          fontSize: 12.5, lineHeight: 1.5, wordBreak: "break-word",
          display: "-webkit-box", WebkitLineClamp: 3, WebkitBoxOrient: "vertical",
          overflow: "hidden",
        }}>{desc}</div>
      )}
    </div>
  );
}

/** 卡片间的横向连接线:横线 + 向右箭头(Attio 风格)。 */
function Connector() {
  return (
    <div style={{ flexShrink: 0, display: "flex", alignItems: "center", alignSelf: "center", padding: "0 2px" }}>
      <div style={{ width: 26, height: 2, background: "var(--border)" }} />
      <svg width="7" height="11" viewBox="0 0 7 11" style={{ marginLeft: -2, display: "block" }} aria-hidden="true">
        <path d="M1 1 L5.5 5.5 L1 10" fill="none" stroke="var(--border)"
              strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    </div>
  );
}

/** 浮于流程上方的小胶囊标签(类型 / 状态)。 */
function Pill({ children, color, bg }: { children: React.ReactNode; color: string; bg: string }) {
  return (
    <span style={{
      display: "inline-flex", alignItems: "center", gap: 4,
      fontSize: 11, fontWeight: 600, padding: "2px 8px", borderRadius: 999,
      color, background: bg, border: `1px solid ${color}33`, whiteSpace: "nowrap",
    }}>{children}</span>
  );
}

/** 当前正在编辑的块(只能一块同时编辑)。 */
type EditingBlock =
  | null
  | { type: "trigger" }
  | { type: "condition" }
  | { type: "action"; index: number }
  | { type: "new_action" };

/** 展开后的横向流程详情(Attio 风格:触发卡 → [条件卡] → 动作卡,横向箭头连线,窄屏横向滚动)。
 *
 *  「点块改」:每块卡片角上有 ✎ 铅笔,点它在流程行 **下方** 打开仅含该块字段的小编辑器
 *  (流程图保持可见,一次只编辑一块)。保存只 PATCH 那一块。
 *
 *  fields 用于下拉(字段选择 / 选项值);未传则在首次进入编辑时按 w.module 自取。
 *  onChanged 在某块保存成功后调用,父级据此重载。 */
export function WorkflowDetail(
  { t, w, fieldLabels, fields: fieldsProp, onChanged }:
  {
    t: ReturnType<typeof useT>; w: CrmWorkflow;
    fieldLabels?: Record<string, string>;
    fields?: CrmFieldMeta[];
    onChanged?: () => void;
  },
) {
  const acts = (w.actions || []) as WfAction[];
  const condText = conditionText(t, w, fieldLabels);
  const [editingBlock, setEditingBlock] = useState<EditingBlock>(null);

  // 模块字段(下拉用)。父级可传入;否则首次需要时自取并缓存。
  const [ownFields, setOwnFields] = useState<CrmFieldMeta[] | null>(null);
  const fields = fieldsProp ?? ownFields ?? [];
  useEffect(() => {
    // 父级已供给字段,或还没进入任何编辑态,就不自取。
    if (fieldsProp || ownFields || editingBlock == null) return;
    let alive = true;
    getModuleFields(w.module)
      .then((r) => { if (alive) setOwnFields((r.fields || []).filter((f) => !f.is_hidden)); })
      .catch(() => { if (alive) setOwnFields([]); });
    return () => { alive = false; };
  }, [fieldsProp, ownFields, editingBlock, w.module]);

  const close = () => setEditingBlock(null);
  const onSaved = () => { close(); onChanged?.(); };
  const toggle = (b: EditingBlock) =>
    setEditingBlock((cur) =>
      cur && b && cur.type === b.type && (cur as { index?: number }).index === (b as { index?: number }).index
        ? null : b);

  const editLabel = t("admin.workflows.editor.edit_block");

  return (
    <div style={{ background: "var(--bg)", padding: "14px 18px 18px" }}>
      {/* 浮标胶囊行(左=触发类型,右=启停状态) */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
        <Pill color="var(--accent)" bg="color-mix(in srgb, var(--accent) 14%, transparent)">
          ▷ {t("admin.workflows.detail.trigger_label")}
        </Pill>
        {w.enabled
          ? <Pill color="#22c55e" bg="color-mix(in srgb, #22c55e 14%, transparent)">● {t("admin.workflows.detail.status_on")}</Pill>
          : <Pill color="var(--muted)" bg="color-mix(in srgb, var(--muted) 16%, transparent)">○ {t("admin.workflows.detail.status_off")}</Pill>}
      </div>
      {/* 横向流程链:触发 → [条件] → 动作…(窄屏横向滚动) */}
      <div style={{
        display: "flex", alignItems: "stretch", gap: 0,
        overflowX: "auto", paddingBottom: 4,
      }}>
        <FlowCard
          icon="▶"
          bg="color-mix(in srgb, var(--accent) 18%, transparent)"
          eyebrow={t("admin.workflows.detail.eyebrow_trigger")}
          title={triggerText(t, w)}
          onEdit={() => toggle({ type: "trigger" })}
          editLabel={editLabel}
          editing={editingBlock?.type === "trigger"}
        />
        {condText && (
          <>
            <Connector />
            <FlowCard
              icon="🔍"
              bg="color-mix(in srgb, var(--muted) 22%, transparent)"
              eyebrow={t("admin.workflows.detail.eyebrow_filter")}
              title={t("admin.workflows.detail.filter_title")}
              desc={condText}
              onEdit={() => toggle({ type: "condition" })}
              editLabel={editLabel}
              editing={editingBlock?.type === "condition"}
            />
          </>
        )}
        {/* 无条件时也允许加条件:点触发卡右侧的连接处不便放,统一交给条件编辑器入口——
            读出态没有条件卡,故提供一个 + 添加条件 入口落在动作前。 */}
        {!condText && (
          <>
            <Connector />
            <AddCard
              label={t("admin.workflows.editor.add_condition")}
              icon="🔍"
              onClick={() => toggle({ type: "condition" })}
              active={editingBlock?.type === "condition"}
            />
          </>
        )}
        {acts.map((a, i) => {
          const gText = guardText(t, a, fieldLabels);
          return (
            <div key={i} style={{ display: "flex" }}>
              <Connector />
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                <FlowCard
                  icon={actionIcon(a)}
                  bg={iconBg(a)}
                  eyebrow={t("admin.workflows.detail.eyebrow_action")}
                  title={actionTitle(t, a)}
                  desc={actionDesc(t, a, fieldLabels) || undefined}
                  onEdit={() => toggle({ type: "action", index: i })}
                  editLabel={editLabel}
                  editing={editingBlock?.type === "action" && editingBlock.index === i}
                />
                {gText && (
                  <div title={t("admin.workflows.detail.guard_prefix", { text: gText })} style={{
                    width: 230, boxSizing: "border-box",
                    display: "flex", alignItems: "baseline", gap: 5,
                    fontSize: 11.5, lineHeight: 1.4, padding: "4px 9px", borderRadius: 8,
                    color: "var(--accent)",
                    background: "color-mix(in srgb, var(--accent) 10%, transparent)",
                    border: "1px solid color-mix(in srgb, var(--accent) 28%, transparent)",
                  }}>
                    <span style={{ flexShrink: 0, fontWeight: 700 }}>⎇</span>
                    <span style={{
                      minWidth: 0, overflow: "hidden", textOverflow: "ellipsis",
                      display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical",
                    }}>{t("admin.workflows.detail.guard_prefix", { text: gText })}</span>
                  </div>
                )}
              </div>
            </div>
          );
        })}
        {/* + 添加动作(末尾) */}
        <Connector />
        <AddCard
          label={t("admin.workflows.editor.add_action")}
          icon="＋"
          onClick={() => toggle({ type: "new_action" })}
          active={editingBlock?.type === "new_action"}
        />
      </div>

      {/* 选中块的内联小编辑器(出现在流程行下方,流程图保持可见) */}
      {editingBlock?.type === "trigger" && (
        <TriggerBlockEditor t={t} w={w} onSaved={onSaved} onCancel={close} />
      )}
      {editingBlock?.type === "condition" && (
        <ConditionBlockEditor t={t} w={w} fields={fields} onSaved={onSaved} onCancel={close} />
      )}
      {editingBlock?.type === "action" && (
        <ActionBlockEditor t={t} w={w} fields={fields} index={editingBlock.index} onSaved={onSaved} onCancel={close} />
      )}
      {editingBlock?.type === "new_action" && (
        <ActionBlockEditor t={t} w={w} fields={fields} onSaved={onSaved} onCancel={close} />
      )}
    </div>
  );
}

/** 流程链末尾/条件位的「+ 添加…」虚线卡片(点开对应块编辑器)。 */
function AddCard({ label, icon, onClick, active }: {
  label: string; icon: string; onClick: () => void; active?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      style={{
        flexShrink: 0, alignSelf: "center", boxSizing: "border-box",
        display: "flex", alignItems: "center", gap: 8,
        padding: "11px 14px", borderRadius: 12, cursor: "pointer",
        border: active ? "1px solid var(--accent)" : "1px dashed var(--border)",
        background: active ? "color-mix(in srgb, var(--accent) 10%, transparent)" : "var(--bg)",
        color: active ? "var(--accent)" : "var(--muted)",
        fontSize: 13, fontWeight: 600, whiteSpace: "nowrap",
      }}
    >
      <span style={{ fontSize: 15, lineHeight: 1 }}>{icon}</span>{label}
    </button>
  );
}

// ── 运行历史(Runs)─────────────────────────────────────────────

const RUN_STATUS_COLOR: Record<CrmWorkflowRun["status"], string> = {
  done: "#22c55e",
  failed: "#ef4444",
  skipped: "var(--muted)",
  running: "#f59e0b",
};

/** 运行结果里的动作 kind → 本地化名(缺映射回退原值,空则「未知动作」)。 */
function runActionLabel(t: ReturnType<typeof useT>, kind: unknown): string {
  const k = String(kind || "");
  if (!k) return t("admin.workflows.runs.unknown_action");
  const key = `admin.workflows.action.${k}`;
  const raw = t(key as never);
  return raw === key ? k : raw;
}

/** 触发摘要:trigger("created"/"updated"/"schedule") + record id。 */
function runTriggerText(t: ReturnType<typeof useT>, run: CrmWorkflowRun): string {
  const id = run.trigger_record_id;
  const trig = String(run.trigger || "");
  if (trig === "created" || trig === "create") {
    return id != null ? t("admin.workflows.runs.trigger.created", { id: String(id) }) : t("admin.workflows.runs.trigger.created_no_id");
  }
  if (trig === "updated" || trig === "update") {
    return id != null ? t("admin.workflows.runs.trigger.updated", { id: String(id) }) : t("admin.workflows.runs.trigger.updated_no_id");
  }
  if (trig === "schedule" || trig === "scheduled") {
    return t("admin.workflows.runs.trigger.schedule");
  }
  return id != null ? `${trig} #${id}` : trig || "—";
}

/** 相对时间(粗粒度);失败回落原串。 */
function relTime(t: ReturnType<typeof useT>, iso: string): string {
  const ts = Date.parse(iso);
  if (Number.isNaN(ts)) return iso || "";
  const diff = Date.now() - ts;
  const sec = Math.floor(diff / 1000);
  if (sec < 60) return t("admin.workflows.runs.time.just_now");
  const min = Math.floor(sec / 60);
  if (min < 60) return t("admin.workflows.runs.time.minutes", { n: String(min) });
  const hr = Math.floor(min / 60);
  if (hr < 24) return t("admin.workflows.runs.time.hours", { n: String(hr) });
  const day = Math.floor(hr / 24);
  if (day < 30) return t("admin.workflows.runs.time.days", { n: String(day) });
  return new Date(ts).toLocaleDateString();
}

type RunResult = CrmWorkflowRun["results"][number];

/** 小标签(reversible / not reversible / undone 等)。 */
function MiniTag({ children, color }: { children: React.ReactNode; color: string }) {
  return (
    <span style={{
      display: "inline-flex", alignItems: "center", gap: 3,
      fontSize: 10.5, fontWeight: 600, padding: "1px 6px", borderRadius: 999,
      color, background: `color-mix(in srgb, ${color} 14%, transparent)`,
      border: `1px solid color-mix(in srgb, ${color} 35%, transparent)`,
      whiteSpace: "nowrap", lineHeight: 1.5,
    }}>{children}</span>
  );
}

/** 该 run 是否含至少一个可撤销动作。 */
function runHasUndoable(results: RunResult[]): boolean {
  return results.some((r) => r.undo != null || r.undoable === true);
}

/** webhook 类动作:外部调用(不可撤销)。 */
function isExternalCall(r: RunResult): boolean {
  const kind = String(r.kind || "").toLowerCase();
  return kind === "webhook" || kind === "http" || kind === "http_request";
}

/** 跳过原因 → i18n 标签(已知映射,未知回落原串)。 */
function skipReasonText(t: ReturnType<typeof useT>, reason: string): string {
  const known = new Set(["condition_false", "record_gone", "cascade_limit"]);
  if (known.has(reason)) return t(`admin.workflows.runs.skip_reason.${reason}` as never);
  return reason || t("admin.workflows.runs.skip_reason.unknown");
}

/** 从 run.results / run 顶层提取跳过原因(若有)。 */
function extractSkipReason(run: CrmWorkflowRun): string | null {
  const top = (run as unknown as Record<string, unknown>).reason;
  if (typeof top === "string" && top) return top;
  const results = Array.isArray(run.results) ? run.results : [];
  for (const r of results) {
    const rr = (r as Record<string, unknown>).reason;
    if (typeof rr === "string" && rr) return rr;
  }
  return null;
}

/** agent 推理文本(reply / summary),截断。 */
function reasoningText(r: RunResult): string | null {
  const raw = (typeof r.reply === "string" && r.reply.trim())
    ? r.reply.trim()
    : (typeof r.summary === "string" && r.summary.trim() ? r.summary.trim() : "");
  if (!raw) return null;
  return raw.length > 260 ? `${raw.slice(0, 260)}…` : raw;
}

/** 单条运行行:状态点 + 触发 + 时间 + 撤销按钮,可展开看各动作结果。 */
function RunRow({ t, run }: { t: ReturnType<typeof useT>; run: CrmWorkflowRun }) {
  const [open, setOpen] = useState(false);
  const [undone, setUndone] = useState<boolean>(
    typeof (run as unknown as Record<string, unknown>).undone_at === "string",
  );
  const [undoing, setUndoing] = useState(false);
  const results = Array.isArray(run.results) ? run.results : [];
  const dot = RUN_STATUS_COLOR[run.status] ?? "var(--muted)";
  const statusLabel = t(`admin.workflows.runs.status.${run.status}` as never);
  const canUndo = runHasUndoable(results);
  const skipReason = run.status === "skipped" ? extractSkipReason(run) : null;

  async function handleUndo(e: React.MouseEvent) {
    e.stopPropagation();
    if (undoing || undone) return;
    if (!window.confirm(t("admin.workflows.runs.undo_confirm"))) return;
    setUndoing(true);
    try {
      const res = await undoWorkflowRun(run.id);
      const n = Array.isArray(res.undone) ? res.undone.length : 0;
      const m = (Array.isArray(res.skipped) ? res.skipped.length : 0)
        + (Array.isArray(res.failed) ? res.failed.length : 0);
      setUndone(true);
      toast(t("admin.workflows.runs.undo_result", { n: String(n), m: String(m) }), n > 0 ? "success" : "info");
    } catch {
      toast(t("admin.workflows.runs.undo_failed"), "error");
    } finally {
      setUndoing(false);
    }
  }

  return (
    <div style={{ borderBottom: "1px solid var(--border)" }}>
      <div
        onClick={() => results.length && setOpen((o) => !o)}
        style={{
          width: "100%", boxSizing: "border-box", display: "flex", alignItems: "center", gap: 10,
          padding: "9px 12px", background: "transparent",
          cursor: results.length ? "pointer" : "default", color: "inherit", textAlign: "left",
        }}
      >
        <span style={{
          flexShrink: 0, width: 8, height: 8, borderRadius: 999, background: dot,
        }} title={statusLabel} aria-label={statusLabel} />
        <span style={{ fontSize: 13, fontWeight: 500, whiteSpace: "nowrap" }}>{runTriggerText(t, run)}</span>
        <span className="muted" style={{ fontSize: 12 }}>{statusLabel}</span>
        {skipReason && (
          <span className="muted" style={{ fontSize: 12, whiteSpace: "nowrap" }}>
            {" · " + skipReasonText(t, skipReason)}
          </span>
        )}
        {undone && (
          <MiniTag color="var(--muted)">↩ {t("admin.workflows.runs.undone")}</MiniTag>
        )}
        {canUndo && !undone && (
          <button
            type="button"
            onClick={handleUndo}
            disabled={undoing}
            title={t("admin.workflows.runs.undo")}
            style={{
              flexShrink: 0, fontSize: 11, fontWeight: 600, padding: "2px 9px", borderRadius: 999,
              cursor: undoing ? "default" : "pointer", whiteSpace: "nowrap",
              color: "var(--accent)", background: "color-mix(in srgb, var(--accent) 12%, transparent)",
              border: "1px solid color-mix(in srgb, var(--accent) 35%, transparent)",
              opacity: undoing ? 0.6 : 1,
            }}
          >
            ↩ {undoing ? t("admin.workflows.runs.undoing") : t("admin.workflows.runs.undo")}
          </button>
        )}
        <span className="muted" style={{ marginLeft: "auto", fontSize: 12, whiteSpace: "nowrap" }}>
          {relTime(t, run.created_at)}
        </span>
        {results.length > 0 && (
          <span className="muted" style={{ flexShrink: 0, fontSize: 11, width: 12, textAlign: "center" }}>
            {open ? "▾" : "▸"}
          </span>
        )}
      </div>
      {open && results.length > 0 && (
        <div style={{ padding: "2px 12px 10px 30px", display: "flex", flexDirection: "column", gap: 7 }}>
          {results.map((r, i) => {
            const ok = r.ok !== false && !r.error;
            const reversible = r.undo != null || r.undoable === true;
            const reasoning = reasoningText(r);
            return (
              <div key={i} style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12.5, flexWrap: "wrap" }}>
                  <span style={{ flexShrink: 0, color: ok ? "#22c55e" : "#ef4444", fontWeight: 700 }}>
                    {ok ? "✓" : "✗"}
                  </span>
                  <span style={{ minWidth: 0 }}>
                    <span style={{ fontWeight: 500 }}>{runActionLabel(t, r.kind)}</span>
                    {ok
                      ? <span className="muted">{" · " + t("admin.workflows.runs.action_ok")}</span>
                      : <span style={{ color: "#ef4444" }}>{" · " + (r.error || t("admin.workflows.runs.action_failed"))}</span>}
                  </span>
                  {reversible
                    ? <MiniTag color="#22c55e">↩ {t("admin.workflows.runs.reversible")}</MiniTag>
                    : (
                      <MiniTag color="var(--muted)">
                        {t("admin.workflows.runs.not_reversible")}
                        {isExternalCall(r) ? ` · ${t("admin.workflows.runs.external_call")}` : ""}
                      </MiniTag>
                    )}
                </div>
                {reasoning && (
                  <div className="muted" style={{
                    fontSize: 12, lineHeight: 1.5, marginLeft: 20,
                    padding: "5px 9px", borderRadius: 7,
                    background: "color-mix(in srgb, var(--muted) 10%, transparent)",
                    border: "1px solid var(--border)", wordBreak: "break-word",
                  }}>
                    <span style={{ fontWeight: 600 }}>{t("admin.workflows.runs.ai_reason")}</span>
                    {reasoning}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

/** 某工作流的运行历史:按需拉取 + 渲染行列表。 */
export function WorkflowRuns({ t, module, workflowId }: {
  t: ReturnType<typeof useT>; module: string; workflowId: number;
}) {
  const [runs, setRuns] = useState<CrmWorkflowRun[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState(false);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setErr(false);
    listModuleWorkflowRuns(module, { workflowId, limit: 30 })
      .then((r) => { if (alive) setRuns(r.runs || []); })
      .catch(() => { if (alive) { setErr(true); setRuns([]); } })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [module, workflowId]);

  return (
    <div style={{ background: "var(--bg)", borderTop: "1px solid var(--border)" }}>
      <div style={{
        padding: "9px 14px", fontSize: 11, fontWeight: 700, letterSpacing: 0.5,
        textTransform: "uppercase", color: "var(--muted)",
      }}>
        {t("admin.workflows.runs.title")}
      </div>
      {loading && (
        <div className="muted" style={{ fontSize: 13, padding: "4px 14px 14px" }}>
          {t("admin.workflows.loading")}
        </div>
      )}
      {!loading && err && (
        <div className="muted" style={{ fontSize: 13, padding: "4px 14px 14px" }}>
          {t("admin.workflows.runs.error")}
        </div>
      )}
      {!loading && !err && runs && runs.length === 0 && (
        <div className="muted" style={{ fontSize: 13, padding: "4px 14px 14px" }}>
          {t("admin.workflows.runs.empty")}
        </div>
      )}
      {!loading && !err && runs && runs.length > 0 && (
        <div style={{ borderTop: "1px solid var(--border)" }}>
          {runs.map((run) => <RunRow key={run.id} t={t} run={run} />)}
        </div>
      )}
    </div>
  );
}
