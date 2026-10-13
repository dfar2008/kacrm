/**
 * WorkflowEditor — 结构化「点块改」的 **每块单独** 内联编辑器(第④层修正 v2)。
 *
 * 不再是「一个大表单改整条工作流」。流程图常驻可见,用户点某块的 ✎ 铅笔,
 * 只为那一块开一个小编辑器(出现在流程行下方),保存只 PATCH 那一块。
 *
 * 本文件导出三个块级编辑器组件 + 复用的辅助函数/输入件:
 *   • TriggerBlockEditor   —— 触发(event: trigger_on / schedule: schedule_cron)
 *   • ConditionBlockEditor —— 条件(condition.all,AND)
 *   • ActionBlockEditor    —— 单个动作(按 index 改;新建则 append);保存发整条 actions
 *
 * 引擎事实(必须对齐):
 *   • condition = { all: [{field, op, value}] },引擎忽略 any,只编辑/保存 all。
 *   • ops: changed / present / absent(无 value 输入)+ = != contains > < >= <=。
 *   • actions: update_field / agent(executor==="agent",改 instruction)/
 *     create_task / call_webhook / create_record。
 */
import { useState } from "react";
import {
  updateWorkflowSpec,
  type CrmWorkflow, type CrmFieldMeta,
} from "@/lib/api";
import type { T } from "@/lib/i18n";
import { toast } from "@/lib/toast";

// ── 引擎 op 列表(下拉用),value 输入按需 ─────────────────────────
export const OPS = ["changed", "present", "absent", "=", "!=", "contains", ">", "<", ">=", "<="] as const;
export type Op = (typeof OPS)[number];
export const NO_VALUE_OPS = new Set<Op>(["changed", "present", "absent"]);
/** 引擎 op → i18n key 段(复用既有 admin.workflows.editor.op.* 标签)。 */
export const OP_I18N: Record<Op, string> = {
  changed: "changed", present: "present", absent: "absent",
  "=": "eq", "!=": "ne", contains: "contains",
  ">": "gt", "<": "lt", ">=": "gte", "<=": "lte",
};

// ── 可编辑动作类型(下拉) ────────────────────────────────────────
export const ACTION_KINDS = ["update_field", "agent", "create_task", "call_webhook", "create_record", "submit_approval"] as const;
export type ActionKind = (typeof ACTION_KINDS)[number];

// ── 本地可编辑形状 ───────────────────────────────────────────────
export type Clause = { field: string; op: Op; value: string };
export type ActionDraft = {
  kind?: string;
  executor?: string;
  params: Record<string, unknown>;
  instruction?: string;
  /** 该动作自己的「守卫」(分支条件):满足才执行。与顶层 condition 同形({all:[…]})。 */
  guard?: Clause[];
};

export const inputStyle: React.CSSProperties = {
  fontSize: 13, padding: "6px 9px", borderRadius: 6,
  background: "var(--bg)", color: "inherit", border: "1px solid var(--border)",
};
export const selectStyle = inputStyle;

/** 把任意存量动作对象规整成可编辑草稿(防御乱形状)。 */
export function toActionDraft(a: unknown): ActionDraft {
  const o = (a && typeof a === "object" ? a : {}) as Record<string, unknown>;
  const params = (o.params && typeof o.params === "object" ? o.params : {}) as Record<string, unknown>;
  const cond = (o.condition && typeof o.condition === "object" ? o.condition : null) as CrmWorkflow["condition"] | null;
  const guard = cond ? toClauses(cond) : [];
  return {
    kind: typeof o.kind === "string" ? o.kind : undefined,
    executor: typeof o.executor === "string" ? o.executor : undefined,
    params: { ...params },
    instruction: typeof o.instruction === "string" ? o.instruction
      : (typeof params.instruction === "string" ? params.instruction : undefined),
    guard: guard.length ? guard : undefined,
  };
}

/** 当前草稿对应的「动作类型」key(给 kind 下拉)。 */
export function actionKindOf(a: ActionDraft): ActionKind {
  if (a.executor === "agent") return "agent";
  const k = String(a.kind || "");
  if ((ACTION_KINDS as readonly string[]).includes(k)) return k as ActionKind;
  return "update_field";
}

/** 把存量 condition 规整成 Clause 列表。引擎只跑 all;但存量可能用了 any
 *  (从不生效的死数据),也读进来,保存时统一落为 all——既不丢、又让它们真生效。 */
/** op 别名 → 引擎符号(agent 可能写 gte/lt/eq…,与后端 _OP_ALIAS 对齐)。 */
const OP_ALIAS: Record<string, Op> = {
  eq: "=", equals: "=", ne: "!=", neq: "!=", not_equals: "!=",
  gt: ">", lt: "<", gte: ">=", lte: "<=", ge: ">=", le: "<=", has: "contains",
};
export function toClauses(cond: CrmWorkflow["condition"]): Clause[] {
  const all = cond && Array.isArray(cond.all) && cond.all.length ? cond.all
    : (cond && Array.isArray(cond.any) ? cond.any : []);
  return all.map((raw) => {
    const c = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
    const rawOp = OP_ALIAS[String(c.op).toLowerCase()] ?? String(c.op);
    const op = (OPS as readonly string[]).includes(rawOp) ? (rawOp as Op) : "=";
    return {
      field: typeof c.field === "string" ? c.field : "",
      op,
      value: c.value != null ? String(c.value) : "",
    };
  });
}

/** select/picklist 字段的 options CSV → 选项数组(空则 null,表示走文本输入)。 */
export function fieldOptions(fields: CrmFieldMeta[], fieldName: string): string[] | null {
  const f = fields.find((x) => x.field_name === fieldName);
  if (!f || !f.options) return null;
  const opts = f.options.split(",").map((s) => s.trim()).filter(Boolean);
  return opts.length ? opts : null;
}

function fieldLabelOf(f: CrmFieldMeta): string {
  return f.display_label || f.field_label || f.field_name;
}

/** 字段选择器(稳健):
 *  • 有字段 → 下拉;若当前值不在列表(字段被删/改名、或未注册模块),仍把它作为一项保留,绝不静默丢。
 *  • 无字段可选(如 orphan/未注册模块,getModuleFields 返回空)→ 退化成文本输入,照样能填。 */
function FieldSelect({ t, value, fields, onChange, minWidth = 150 }: {
  t: T; value: string; fields: CrmFieldMeta[]; onChange: (v: string) => void; minWidth?: number;
}) {
  if (!fields.length) {
    return (
      <input value={value} onChange={(e) => onChange(e.target.value)}
        placeholder={t("admin.workflows.editor.field_key_placeholder")}
        style={{ ...inputStyle, minWidth }} />
    );
  }
  const missing = !!value && !fields.some((f) => f.field_name === value);
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)} style={{ ...selectStyle, minWidth }}>
      <option value="">{t("admin.workflows.editor.pick_field")}</option>
      {missing && <option value={value}>{value}</option>}
      {fields.map((f) => <option key={f.field_name} value={f.field_name}>{fieldLabelOf(f)}</option>)}
    </select>
  );
}

// ── 清洗:把本地草稿规整成引擎可存形态(供保存用) ──────────────────

/** 单条 clause → 引擎形态(去空字段在调用处过滤)。 */
function cleanClause(c: Clause): { field: string; op: string; value?: unknown } {
  const out: { field: string; op: string; value?: unknown } = { field: c.field, op: c.op };
  if (!NO_VALUE_OPS.has(c.op)) {
    const numeric = [">", "<", ">=", "<="].includes(c.op);
    const n = Number(c.value);
    out.value = numeric && c.value.trim() !== "" && Number.isFinite(n) ? n : c.value;
  }
  return out;
}

/** 单条动作草稿 → 引擎形态(含可选 guard → condition)。 */
function cleanAction(a: ActionDraft): Record<string, unknown> {
  const out = cleanActionBody(a);
  const guard = (a.guard || []).filter((c) => c.field).map(cleanClause);
  if (guard.length) out.condition = { all: guard };
  return out;
}

/** 动作主体(kind + params,不含 guard)。 */
function cleanActionBody(a: ActionDraft): Record<string, unknown> {
  const kind = actionKindOf(a);
  if (kind === "agent") {
    return { kind: "agent", executor: "agent", instruction: a.instruction || "", params: {} };
  }
  if (kind === "update_field") {
    return { kind: "update_field", params: { field: a.params.field ?? "", value: a.params.value ?? "" } };
  }
  if (kind === "create_task") {
    return { kind: "create_task", params: { title: a.params.title ?? "" } };
  }
  if (kind === "call_webhook") {
    return { kind: "call_webhook", params: { url: a.params.url ?? "", method: a.params.method ?? "POST" } };
  }
  if (kind === "submit_approval") {
    // 把命中记录提交进审批流;无参数(模块审批链在模块配置里设置)。
    return { kind: "submit_approval", params: {} };
  }
  // create_record:v1 只编辑 module;可选 data(原始 JSON 文本)若可解析就带上。
  const out: Record<string, unknown> = { module: a.params.module ?? "" };
  if (typeof a.params.__dataText === "string" && a.params.__dataText.trim()) {
    try { out.data = JSON.parse(a.params.__dataText); } catch { /* 解析失败则不带 data */ }
  } else if (a.params.data != null) {
    out.data = a.params.data;
  }
  return { kind: "create_record", params: out };
}

/** 读当前工作流的全部动作 → 草稿数组(给 ActionBlockEditor 改单条后重组整条)。 */
function currentActionDrafts(w: CrmWorkflow): ActionDraft[] {
  return (Array.isArray(w.actions) ? w.actions : []).map(toActionDraft);
}

// ── 块编辑器外壳(小卡片,出现在流程行下方) ──────────────────────
function BlockShell({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{
      marginTop: 12, border: "1px solid var(--border)", borderRadius: 12,
      background: "var(--panel)", padding: "14px 16px 16px",
      display: "flex", flexDirection: "column", gap: 12,
      boxShadow: "0 1px 3px rgba(0,0,0,0.06)",
    }}>
      <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: 0.5, textTransform: "uppercase", color: "var(--muted)" }}>{title}</div>
      {children}
    </div>
  );
}

/** 保存 / 取消 行(每块编辑器底部复用)。 */
function SaveCancelRow({ t, saving, onSave, onCancel, saveLabel }: {
  t: T; saving: boolean; onSave: () => void; onCancel: () => void; saveLabel?: string;
}) {
  return (
    <div style={{ display: "flex", gap: 10, marginTop: 2 }}>
      <button type="button" onClick={onSave} disabled={saving}
        style={{
          fontSize: 13, fontWeight: 600, padding: "7px 16px", borderRadius: 8, cursor: "pointer",
          border: "1px solid var(--accent)", color: "var(--bg)", background: "var(--accent)",
          opacity: saving ? 0.6 : 1,
        }}>
        {saving ? t("admin.workflows.editor.saving") : (saveLabel || t("admin.workflows.editor.save"))}
      </button>
      <button type="button" onClick={onCancel} className="ghost"
        style={{
          fontSize: 13, fontWeight: 500, padding: "7px 14px", borderRadius: 8, cursor: "pointer",
          border: "1px solid var(--border)", color: "inherit", background: "var(--bg)",
        }}>
        {t("admin.workflows.editor.cancel")}
      </button>
    </div>
  );
}

// ════════════════════════════════════════════════════════════════
// 触发块编辑器
// ════════════════════════════════════════════════════════════════
export function TriggerBlockEditor({ t, w, onSaved, onCancel }: {
  t: T; w: CrmWorkflow; onSaved: (updated: CrmWorkflow) => void; onCancel: () => void;
}) {
  const isSchedule = w.trigger_type === "schedule";
  const [triggerOn, setTriggerOn] = useState<"create" | "update">(w.trigger_on === "update" ? "update" : "create");
  const [cron, setCron] = useState<string>(w.schedule_cron || "");
  const [saving, setSaving] = useState(false);

  async function onSave() {
    const patch: Parameters<typeof updateWorkflowSpec>[1] = isSchedule
      ? { schedule_cron: cron.trim() || null }
      : { trigger_on: triggerOn };
    setSaving(true);
    try {
      const res = await updateWorkflowSpec(w.id, patch);
      toast(t("admin.workflows.editor.saved"), "success");
      const updated: CrmWorkflow = res.workflow ?? {
        ...w,
        trigger_on: isSchedule ? w.trigger_on : triggerOn,
        schedule_cron: isSchedule ? (cron.trim() || null) : w.schedule_cron,
      };
      onSaved(updated);
    } catch (e) {
      toast(t("admin.workflows.editor.save_failed", { err: String(e) }), "error");
    } finally {
      setSaving(false);
    }
  }

  return (
    <BlockShell title={t("admin.workflows.editor.trigger_section")}>
      {isSchedule ? (
        <label style={{ display: "flex", flexDirection: "column", gap: 5, maxWidth: 360 }}>
          <span className="muted" style={{ fontSize: 12 }}>{t("admin.workflows.editor.cron_label")}</span>
          <input value={cron} onChange={(e) => setCron(e.target.value)} placeholder="0 9 * * *" style={inputStyle} />
          <span className="muted" style={{ fontSize: 11 }}>{t("admin.workflows.editor.cron_hint")}</span>
        </label>
      ) : (
        <div style={{ display: "inline-flex", border: "1px solid var(--border)", borderRadius: 8, overflow: "hidden", alignSelf: "flex-start" }}>
          {(["create", "update"] as const).map((opt) => (
            <button key={opt} type="button" onClick={() => setTriggerOn(opt)}
              style={{
                fontSize: 13, fontWeight: 500, padding: "7px 14px", cursor: "pointer", border: "none",
                color: triggerOn === opt ? "var(--bg)" : "inherit",
                background: triggerOn === opt ? "var(--accent)" : "var(--panel)",
              }}>
              {opt === "create" ? t("admin.workflows.trigger.on_create") : t("admin.workflows.trigger.on_update")}
            </button>
          ))}
        </div>
      )}
      <SaveCancelRow t={t} saving={saving} onSave={onSave} onCancel={onCancel} />
    </BlockShell>
  );
}

// ── 子句列表编辑 UI(条件 / 动作守卫共用同一行布局) ─────────────────
function ClauseRows({ t, clauses, fields, onPatch, onRemove, onAdd, addLabel }: {
  t: T; clauses: Clause[]; fields: CrmFieldMeta[];
  onPatch: (i: number, patch: Partial<Clause>) => void;
  onRemove: (i: number) => void;
  onAdd: () => void;
  addLabel: string;
}) {
  return (
    <>
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {clauses.map((c, i) => {
          const opts = fieldOptions(fields, c.field);
          return (
            <div key={i} style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
              <FieldSelect t={t} value={c.field} fields={fields} onChange={(v) => onPatch(i, { field: v })} />
              <select value={c.op} onChange={(e) => onPatch(i, { op: e.target.value as Op })} style={{ ...selectStyle, minWidth: 96 }}>
                {OPS.map((op) => (
                  <option key={op} value={op}>{t(`admin.workflows.editor.op.${OP_I18N[op]}` as never)}</option>
                ))}
              </select>
              {!NO_VALUE_OPS.has(c.op) && (
                opts ? (
                  <select value={c.value} onChange={(e) => onPatch(i, { value: e.target.value })} style={{ ...selectStyle, minWidth: 130 }}>
                    <option value="">{t("admin.workflows.editor.pick_value")}</option>
                    {opts.map((o) => <option key={o} value={o}>{o}</option>)}
                  </select>
                ) : (
                  <input value={c.value} onChange={(e) => onPatch(i, { value: e.target.value })}
                    placeholder={t("admin.workflows.editor.value_placeholder")} style={{ ...inputStyle, minWidth: 130 }} />
                )
              )}
              <button type="button" onClick={() => onRemove(i)} className="ghost" style={delBtnStyle} title={t("admin.workflows.editor.remove")}>
                ✕ {t("admin.workflows.editor.remove")}
              </button>
            </div>
          );
        })}
      </div>
      <button type="button" onClick={onAdd} style={addBtnStyle}>
        + {addLabel}
      </button>
    </>
  );
}

// ════════════════════════════════════════════════════════════════
// 条件块编辑器
// ════════════════════════════════════════════════════════════════
export function ConditionBlockEditor({ t, w, fields, onSaved, onCancel }: {
  t: T; w: CrmWorkflow; fields: CrmFieldMeta[];
  onSaved: (updated: CrmWorkflow) => void; onCancel: () => void;
}) {
  const [clauses, setClauses] = useState<Clause[]>(() => toClauses(w.condition));
  const [saving, setSaving] = useState(false);

  const addClause = () =>
    setClauses((cs) => [...cs, { field: fields[0]?.field_name || "", op: "=", value: "" }]);
  const removeClause = (i: number) => setClauses((cs) => cs.filter((_, j) => j !== i));
  const patchClause = (i: number, patch: Partial<Clause>) =>
    setClauses((cs) => cs.map((c, j) => (j === i ? { ...c, ...patch } : c)));

  async function onSave() {
    const cleaned = clauses.filter((c) => c.field).map(cleanClause);
    setSaving(true);
    try {
      const res = await updateWorkflowSpec(w.id, { condition: { all: cleaned } });
      toast(t("admin.workflows.editor.saved"), "success");
      const updated: CrmWorkflow = res.workflow ?? { ...w, condition: { all: cleaned } };
      onSaved(updated);
    } catch (e) {
      toast(t("admin.workflows.editor.save_failed", { err: String(e) }), "error");
    } finally {
      setSaving(false);
    }
  }

  return (
    <BlockShell title={t("admin.workflows.editor.condition_section")}>
      <div className="muted" style={{ fontSize: 12, marginTop: -4 }}>{t("admin.workflows.editor.condition_hint")}</div>
      {clauses.length === 0 && (
        <div className="muted" style={{ fontSize: 13 }}>{t("admin.workflows.editor.no_condition")}</div>
      )}
      <ClauseRows t={t} clauses={clauses} fields={fields}
        onPatch={patchClause} onRemove={removeClause} onAdd={addClause}
        addLabel={t("admin.workflows.editor.add_condition")} />
      <SaveCancelRow t={t} saving={saving} onSave={onSave} onCancel={onCancel} />
    </BlockShell>
  );
}

// ════════════════════════════════════════════════════════════════
// 动作块编辑器(单条;新建则 index 省略,保存时 append)
// ════════════════════════════════════════════════════════════════
export function ActionBlockEditor({ t, w, fields, index, onSaved, onCancel }: {
  t: T; w: CrmWorkflow; fields: CrmFieldMeta[];
  index?: number;  // undefined = 新增动作
  onSaved: (updated: CrmWorkflow) => void; onCancel: () => void;
}) {
  const isNew = index == null;
  const existing = currentActionDrafts(w);
  const total = existing.length;
  const [draft, setDraft] = useState<ActionDraft>(() =>
    isNew ? { kind: "update_field", params: {} } : (existing[index!] ?? { kind: "update_field", params: {} }),
  );
  const [saving, setSaving] = useState(false);

  const patchParams = (p: Record<string, unknown>) => setDraft((d) => ({ ...d, params: { ...d.params, ...p } }));
  const setInstruction = (v: string) => setDraft((d) => ({ ...d, instruction: v }));

  // ── 守卫(分支条件):本动作 condition.all ──────────────────────
  const guard = draft.guard || [];
  const addGuard = () =>
    setDraft((d) => ({ ...d, guard: [...(d.guard || []), { field: fields[0]?.field_name || "", op: "=" as Op, value: "" }] }));
  const removeGuard = (i: number) =>
    setDraft((d) => ({ ...d, guard: (d.guard || []).filter((_, j) => j !== i) }));
  const patchGuard = (i: number, patch: Partial<Clause>) =>
    setDraft((d) => ({ ...d, guard: (d.guard || []).map((c, j) => (j === i ? { ...c, ...patch } : c)) }));

  /** 切换动作类型:重置成该类型的干净默认形状。 */
  function setActionKind(kind: ActionKind) {
    if (kind === "agent") {
      setDraft((d) => ({ kind: "agent", executor: "agent", params: {}, instruction: d.instruction || "" }));
    } else if (kind === "call_webhook") {
      setDraft({ kind, executor: undefined, instruction: undefined, params: { url: "", method: "POST" } });
    } else {
      setDraft({ kind, executor: undefined, instruction: undefined, params: {} });
    }
  }

  /** 组装整条 actions 数组(在 index 处替换/移动/删除,或末尾 append)后保存。 */
  async function commit(fullActions: Record<string, unknown>[]) {
    setSaving(true);
    try {
      const res = await updateWorkflowSpec(w.id, { actions: fullActions });
      toast(t("admin.workflows.editor.saved"), "success");
      const updated: CrmWorkflow = res.workflow ?? { ...w, actions: fullActions as CrmWorkflow["actions"] };
      onSaved(updated);
    } catch (e) {
      toast(t("admin.workflows.editor.save_failed", { err: String(e) }), "error");
    } finally {
      setSaving(false);
    }
  }

  function onSave() {
    const arr = existing.map(cleanAction);
    const cleaned = cleanAction(draft);
    if (isNew) arr.push(cleaned);
    else arr[index!] = cleaned;
    commit(arr);
  }

  function onDelete() {
    if (isNew) { onCancel(); return; }
    if (!window.confirm(t("admin.workflows.editor.delete_action_confirm"))) return;
    const arr = existing.map(cleanAction).filter((_, j) => j !== index);
    commit(arr);
  }

  function onMove(dir: -1 | 1) {
    if (isNew || index == null) return;
    const j = index + dir;
    if (j < 0 || j >= total) return;
    const arr = existing.map(cleanAction);
    [arr[index], arr[j]] = [arr[j], arr[index]];
    commit(arr);
  }

  const kind = actionKindOf(draft);
  const title = isNew ? t("admin.workflows.editor.new_action_title") : t("admin.workflows.editor.action_n", { n: String(index! + 1) });

  return (
    <BlockShell title={title}>
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <span className="muted" style={{ fontSize: 12 }}>{t("admin.workflows.editor.kind_label")}</span>
        <select value={kind} onChange={(e) => setActionKind(e.target.value as ActionKind)} style={{ ...selectStyle, minWidth: 150 }}>
          {ACTION_KINDS.map((k) => (
            <option key={k} value={k}>{t(`admin.workflows.editor.kind.${k}` as never)}</option>
          ))}
        </select>
        {!isNew && (
          <div style={{ marginLeft: "auto", display: "flex", gap: 6 }}>
            <button type="button" onClick={() => onMove(-1)} disabled={index === 0 || saving} className="ghost" style={iconBtnStyle} title={t("admin.workflows.editor.move_up")}>▲</button>
            <button type="button" onClick={() => onMove(1)} disabled={index === total - 1 || saving} className="ghost" style={iconBtnStyle} title={t("admin.workflows.editor.move_down")}>▼</button>
            <button type="button" onClick={onDelete} disabled={saving} className="ghost" style={delBtnStyle} title={t("admin.workflows.editor.delete_action")}>✕ {t("admin.workflows.editor.delete_action")}</button>
          </div>
        )}
      </div>
      <ActionParams t={t} kind={kind} a={draft} fields={fields}
        onParams={patchParams} onInstruction={setInstruction} />
      {/* 守卫(分支条件):满足才执行这个动作 */}
      <div style={{
        borderTop: "1px dashed var(--border)", paddingTop: 12,
        display: "flex", flexDirection: "column", gap: 8,
      }}>
        {guard.length === 0 ? (
          <button type="button" onClick={addGuard} style={addBtnStyle}>
            ⎇ {t("admin.workflows.editor.add_guard")}
          </button>
        ) : (
          <>
            <div className="muted" style={{ fontSize: 11, fontWeight: 700, letterSpacing: 0.5, textTransform: "uppercase" }}>
              {t("admin.workflows.editor.guard_section")}
            </div>
            <div className="muted" style={{ fontSize: 12, marginTop: -4 }}>{t("admin.workflows.editor.guard_hint")}</div>
            <ClauseRows t={t} clauses={guard} fields={fields}
              onPatch={patchGuard} onRemove={removeGuard} onAdd={addGuard}
              addLabel={t("admin.workflows.editor.add_guard")} />
          </>
        )}
      </div>
      <SaveCancelRow t={t} saving={saving} onSave={onSave} onCancel={onCancel} />
    </BlockShell>
  );
}

// ── 每种动作的参数输入 ───────────────────────────────────────────
function ActionParams({ t, kind, a, fields, onParams, onInstruction }: {
  t: T; kind: ActionKind; a: ActionDraft; fields: CrmFieldMeta[];
  onParams: (p: Record<string, unknown>) => void;
  onInstruction: (v: string) => void;
}) {
  if (kind === "agent") {
    return (
      <label style={{ display: "flex", flexDirection: "column", gap: 5 }}>
        <span className="muted" style={{ fontSize: 12 }}>{t("admin.workflows.editor.instruction_label")}</span>
        <textarea
          value={a.instruction || ""}
          onChange={(e) => onInstruction(e.target.value)}
          rows={3}
          placeholder={t("admin.workflows.editor.instruction_placeholder")}
          style={{ ...inputStyle, resize: "vertical", fontFamily: "inherit" }}
        />
      </label>
    );
  }
  if (kind === "update_field") {
    const fieldName = String(a.params.field ?? "");
    const opts = fieldOptions(fields, fieldName);
    return (
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <label style={{ display: "flex", flexDirection: "column", gap: 5 }}>
          <span className="muted" style={{ fontSize: 12 }}>{t("admin.workflows.editor.field_label")}</span>
          <FieldSelect t={t} value={fieldName} fields={fields} minWidth={160} onChange={(v) => onParams({ field: v, value: "" })} />
        </label>
        <label style={{ display: "flex", flexDirection: "column", gap: 5 }}>
          <span className="muted" style={{ fontSize: 12 }}>{t("admin.workflows.editor.value_label")}</span>
          {opts ? (
            <select value={String(a.params.value ?? "")} onChange={(e) => onParams({ value: e.target.value })} style={{ ...selectStyle, minWidth: 160 }}>
              <option value="">{t("admin.workflows.editor.pick_value")}</option>
              {opts.map((o) => <option key={o} value={o}>{o}</option>)}
            </select>
          ) : (
            <input value={String(a.params.value ?? "")} onChange={(e) => onParams({ value: e.target.value })}
              placeholder={t("admin.workflows.editor.value_placeholder")} style={{ ...inputStyle, minWidth: 160 }} />
          )}
        </label>
      </div>
    );
  }
  if (kind === "create_task") {
    return (
      <label style={{ display: "flex", flexDirection: "column", gap: 5 }}>
        <span className="muted" style={{ fontSize: 12 }}>{t("admin.workflows.editor.task_title_label")}</span>
        <input value={String(a.params.title ?? "")} onChange={(e) => onParams({ title: e.target.value })}
          placeholder={t("admin.workflows.editor.task_title_placeholder")} style={{ ...inputStyle, minWidth: 280 }} />
      </label>
    );
  }
  if (kind === "call_webhook") {
    return (
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-end" }}>
        <label style={{ display: "flex", flexDirection: "column", gap: 5, flex: 1, minWidth: 240 }}>
          <span className="muted" style={{ fontSize: 12 }}>{t("admin.workflows.editor.url_label")}</span>
          <input value={String(a.params.url ?? "")} onChange={(e) => onParams({ url: e.target.value })}
            placeholder="https://" style={inputStyle} />
        </label>
        <label style={{ display: "flex", flexDirection: "column", gap: 5 }}>
          <span className="muted" style={{ fontSize: 12 }}>{t("admin.workflows.editor.method_label")}</span>
          <select value={String(a.params.method ?? "POST")} onChange={(e) => onParams({ method: e.target.value })} style={{ ...selectStyle, minWidth: 90 }}>
            <option value="GET">GET</option>
            <option value="POST">POST</option>
          </select>
        </label>
      </div>
    );
  }
  if (kind === "submit_approval") {
    // 无参数:把命中记录提交进所属模块的审批链。
    return (
      <div className="muted" style={{ fontSize: 12 }}>
        {t("admin.workflows.editor.submit_approval_hint")}
      </div>
    );
  }
  // create_record:v1 只编辑目标模块 + 可选原始 JSON data 文本(简化处理)。
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <label style={{ display: "flex", flexDirection: "column", gap: 5 }}>
        <span className="muted" style={{ fontSize: 12 }}>{t("admin.workflows.editor.target_module_label")}</span>
        <input value={String(a.params.module ?? "")} onChange={(e) => onParams({ module: e.target.value })}
          placeholder="tasks" style={{ ...inputStyle, minWidth: 220 }} />
      </label>
      <label style={{ display: "flex", flexDirection: "column", gap: 5 }}>
        <span className="muted" style={{ fontSize: 12 }}>{t("admin.workflows.editor.record_data_label")}</span>
        <textarea
          value={typeof a.params.__dataText === "string"
            ? a.params.__dataText
            : (a.params.data != null ? JSON.stringify(a.params.data, null, 2) : "")}
          onChange={(e) => onParams({ __dataText: e.target.value })}
          rows={3} placeholder='{"title": "..."}'
          style={{ ...inputStyle, resize: "vertical", fontFamily: "monospace" }}
        />
        <span className="muted" style={{ fontSize: 11 }}>{t("admin.workflows.editor.record_data_hint")}</span>
      </label>
    </div>
  );
}

// ── 小布局件 / 样式 ──────────────────────────────────────────────
const addBtnStyle: React.CSSProperties = {
  alignSelf: "flex-start", fontSize: 12, fontWeight: 600, padding: "6px 12px", borderRadius: 8,
  cursor: "pointer", border: "1px dashed var(--accent)", color: "var(--accent)",
  background: "color-mix(in srgb, var(--accent) 8%, transparent)",
};
const delBtnStyle: React.CSSProperties = {
  fontSize: 12, fontWeight: 500, padding: "5px 10px", borderRadius: 6, cursor: "pointer",
  border: "1px solid var(--border)", color: "var(--muted)", background: "var(--bg)",
};
const iconBtnStyle: React.CSSProperties = {
  fontSize: 12, fontWeight: 500, padding: "5px 9px", borderRadius: 6, cursor: "pointer",
  border: "1px solid var(--border)", color: "inherit", background: "var(--bg)",
};
