/**
 * AdminWorkflowsPage — 独立「工作流」tab。
 *
 * 定位(2026-06-24 spec):workflow = **写事件(记录建/改)/ 定时 → 自动执行**
 * (算字段/建任务/起草/调 webhook)。扫描提醒类一律归 coach,这里不做。
 *
 * 交互:对话驱动(右侧 AI 助手,说一句 agent 搭好)+ 可视化读出(Attio 式流程图),
 * 不做拖拽画布(宪章 §六)。
 */
import { useEffect, useState } from "react";
import {
  listModules, listAllWorkflows, toggleWorkflow, getModuleFields,
  setWorkflowStatus, testRunWorkflow, deleteWorkflow,
  type CrmWorkflow, type CrmFieldMeta,
} from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useIsAdmin } from "@/lib/currentUser";
import { toast } from "@/lib/toast";
import { WorkflowDetail, WorkflowRuns, triggerText } from "./schema/ModuleWorkflows";
import { SchemaAssistant } from "./schema/SchemaAssistant";

/** 事件类示例(只留 workflow 地盘:写事件→自动动作;扫描提醒类已剔除归 coach)。 */
const EVENT_EXAMPLE_KEYS = [
  "research", "approval", "recompute", "churn", "draft_email",
] as const;

export function AdminWorkflowsPage() {
  const t = useT();
  // 仅管理员可直接建/改/删/试跑;非管理员看只读 + 用右侧 AI 助手提建议(走提案审批)。
  const isAdmin = useIsAdmin();
  const [modules, setModules] = useState<{ name: string; label: string }[]>([]);
  // 筛选器(非强制选择器):null = 全部模块。视图默认全局看所有工作流。
  const [filterModule, setFilterModule] = useState<string | null>(null);
  const [rows, setRows] = useState<CrmWorkflow[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [seed, setSeed] = useState<string | null>(null);
  const [reloadNonce, setReloadNonce] = useState(0);
  const [asstOpen, setAsstOpen] = useState(true);
  const [flowOpenId, setFlowOpenId] = useState<number | null>(null);
  const [runsOpenId, setRunsOpenId] = useState<number | null>(null);
  const [runsNonce, setRunsNonce] = useState(0);
  const [testBusyId, setTestBusyId] = useState<number | null>(null);

  useEffect(() => {
    listModules().then((r) => {
      setModules((r.data || []) as { name: string; label: string }[]);
    }).catch(() => {});
  }, []);

  useEffect(() => {
    setLoading(true);
    listAllWorkflows(filterModule)
      .then(setRows)
      .catch(() => setRows([]))
      .finally(() => setLoading(false));
  }, [filterModule, reloadNonce]);

  // 模块名 → 标签(给每行打模块徽标)
  const moduleLabel = (name: string) =>
    modules.find((m) => m.name === name)?.label || name;

  // 各模块字段:键→标签映射(流程详情译字段键,不漏英文)+ 完整字段列表(点块改下拉用)。
  // 同一次 getModuleFields 调用同时填两份缓存,按需拉取。
  const [fieldLabels, setFieldLabels] = useState<Record<string, Record<string, string>>>({});
  const [fieldsByModule, setFieldsByModule] = useState<Record<string, CrmFieldMeta[]>>({});
  useEffect(() => {
    if (!rows) return;
    const need = [...new Set(rows.map((w) => w.module))].filter((m) => m && !fieldLabels[m]);
    if (!need.length) return;
    Promise.all(need.map((m) =>
      getModuleFields(m)
        .then((r) => {
          const all = (r.fields || []);
          const labels = Object.fromEntries(all.map((f) => [f.field_name, f.display_label || f.field_label]));
          const visible = all.filter((f) => !f.is_hidden);
          return [m, labels, visible] as [string, Record<string, string>, CrmFieldMeta[]];
        })
        .catch(() => [m, {}, []] as [string, Record<string, string>, CrmFieldMeta[]]),
    )).then((triples) => {
      setFieldLabels((prev) => ({ ...prev, ...Object.fromEntries(triples.map(([m, l]) => [m, l])) }));
      setFieldsByModule((prev) => ({ ...prev, ...Object.fromEntries(triples.map(([m, , f]) => [m, f])) }));
    });
  }, [rows, fieldLabels]);

  async function onToggle(w: CrmWorkflow) {
    setBusyId(w.id);
    try {
      await toggleWorkflow(w.id, !w.enabled);
      setRows((prev) => prev?.map((r) => (r.id === w.id ? { ...r, enabled: !w.enabled } : r)) ?? prev);
    } catch (e) {
      toast(String(e), "error");
    } finally {
      setBusyId(null);
    }
  }

  async function onSetStatus(w: CrmWorkflow, status: "draft" | "live") {
    setBusyId(w.id);
    try {
      await setWorkflowStatus(w.id, status);
      setRows((prev) => prev?.map((r) => (r.id === w.id ? { ...r, status } : r)) ?? prev);
      toast(
        status === "live"
          ? t("admin.workflows.status.published_toast")
          : t("admin.workflows.status.unpublished_toast"),
        "success",
      );
    } catch (e) {
      toast(String(e), "error");
    } finally {
      setBusyId(null);
    }
  }

  async function onDelete(w: CrmWorkflow) {
    if (!window.confirm(t("admin.workflows.delete_confirm", { name: w.name }))) return;
    setBusyId(w.id);
    try {
      await deleteWorkflow(w.id);
      setRows((prev) => prev?.filter((r) => r.id !== w.id) ?? prev);
      toast(t("admin.workflows.deleted_toast"), "success");
    } catch (e) {
      toast(String(e), "error");
    } finally {
      setBusyId(null);
    }
  }

  async function onTestRun(w: CrmWorkflow) {
    const raw = window.prompt(t("admin.workflows.test.prompt"));
    if (raw == null) return;
    const recordId = Number(raw.trim());
    if (!Number.isFinite(recordId) || recordId <= 0) {
      toast(t("admin.workflows.test.bad_id"), "error");
      return;
    }
    setTestBusyId(w.id);
    try {
      const res = await testRunWorkflow(w.id, recordId);
      const detail = res.reason ? `${res.status} · ${res.reason}` : res.status;
      toast(t("admin.workflows.test.done_toast", { status: detail }), "success");
      // 若该工作流运行历史正展开,刷新它
      if (runsOpenId === w.id) setRunsNonce((n) => n + 1);
    } catch (e) {
      toast(String(e), "error");
    } finally {
      setTestBusyId(null);
    }
  }

  return (
    <div style={{ display: "flex", height: "100%" }}>
      {/* 主区:模块选择 + 示例 + 流程读出 */}
      <div style={{ flex: 1, overflow: "auto", padding: "16px 24px 32px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
          <h2 style={{ margin: 0 }}>⚡ {t("admin.workflows.heading")}</h2>
          {/* 模块筛选器(非强制):默认全部,可聚焦某模块 */}
          <select
            value={filterModule ?? ""}
            onChange={(e) => setFilterModule(e.target.value || null)}
            title={t("admin.workflows.filter_title")}
            style={{
              fontSize: 14, padding: "6px 10px", borderRadius: 6,
              background: "var(--bg)", color: "inherit", border: "1px solid var(--border)",
            }}
          >
            <option value="">{t("admin.workflows.all_modules")}</option>
            {modules.map((m) => <option key={m.name} value={m.name}>{m.label || m.name}</option>)}
          </select>
          {rows && (
            <span className="muted" style={{ fontSize: 12 }}>
              {t("admin.workflows.count", { n: rows.length })}
            </span>
          )}
        </div>
        <p className="muted" style={{ fontSize: 13, margin: "6px 0 0" }}>
          {t("admin.workflows.page_sub")}
        </p>
        {/* 非管理员:只读 + 用 AI 助手提建议(和模块/字段/选项一样,管理员审批后生效) */}
        {!isAdmin && (
          <div style={{
            marginTop: 12, fontSize: 13, lineHeight: 1.6, padding: "10px 14px",
            borderRadius: 8, color: "var(--fg)",
            background: "color-mix(in srgb, var(--accent) 8%, transparent)",
            border: "1px solid color-mix(in srgb, var(--accent) 24%, transparent)",
          }}>
            💡 {t("admin.workflows.non_admin_hint")}
          </div>
        )}

        {/* ✨ AI 诊断:仅在聚焦某模块时可用(诊断本质=诊断某模块该加什么自动化) */}
        {filterModule && (
          <button
            onClick={() => {
              setSeed(t("admin.workflows.diagnose_prompt", { module: moduleLabel(filterModule) }));
              setAsstOpen(true);
            }}
            style={{
              marginTop: 14, display: "inline-flex", alignItems: "center", gap: 6,
              fontSize: 13, fontWeight: 600, padding: "7px 14px", borderRadius: 8,
              border: "1px solid var(--accent)", cursor: "pointer",
              color: "var(--accent)", background: "color-mix(in srgb, var(--accent) 10%, transparent)",
            }}
            title={t("admin.workflows.diagnose_hint")}
          >✨ {t("admin.workflows.diagnose")}</button>
        )}

        {/* 工作流流程读出 */}
        <div style={{ marginTop: 16, display: "flex", flexDirection: "column", gap: 16 }}>
          {loading && <div className="muted" style={{ fontSize: 14 }}>{t("admin.workflows.loading")}</div>}
          {!loading && rows && rows.length === 0 && (
            <div className="muted" style={{
              fontSize: 14, padding: "24px 16px", textAlign: "center",
              border: "1px dashed var(--border)", borderRadius: 12,
            }}>
              {t("admin.workflows.page_empty")}
            </div>
          )}
          {!loading && rows && rows.map((w) => {
            const isDraft = w.status === "draft";
            return (
            <div key={w.id} style={{
              border: isDraft ? "1px dashed var(--accent)" : "1px solid var(--border)",
              borderRadius: 12, overflow: "hidden",
              background: "var(--panel)", opacity: !w.enabled || isDraft ? 0.62 : 1,
            }}>
              <div
                onClick={() => setFlowOpenId((id) => (id === w.id ? null : w.id))}
                title={t("admin.workflows.expand_title")}
                style={{
                  display: "flex", alignItems: "center", gap: 12, cursor: "pointer",
                  padding: "10px 14px",
                  borderBottom: (flowOpenId === w.id || runsOpenId === w.id)
                    ? "1px solid var(--border)" : "none",
                }}>
                <span className="muted" style={{
                  fontSize: 11, width: 12, display: "inline-block", flexShrink: 0,
                  transform: flowOpenId === w.id ? "rotate(90deg)" : "none",
                  transition: "transform .12s",
                }}>▶</span>
                <div style={{ fontWeight: 600, fontSize: 14 }}>{w.name}</div>
                {/* 模块徽标:工作流绑哪个模块,全局列表里一眼可辨 */}
                <span style={{
                  display: "inline-flex", alignItems: "center",
                  fontSize: 11, fontWeight: 500, padding: "2px 8px", borderRadius: 6,
                  color: "var(--accent)", whiteSpace: "nowrap",
                  background: "color-mix(in srgb, var(--accent) 10%, transparent)",
                  border: "1px solid color-mix(in srgb, var(--accent) 24%, transparent)",
                }}>{moduleLabel(w.module)}</span>
                {/* Draft / Live 徽标 */}
                {isDraft ? (
                  <span style={{
                    display: "inline-flex", alignItems: "center", gap: 4,
                    fontSize: 11, fontWeight: 600, padding: "2px 8px", borderRadius: 999,
                    color: "var(--muted)", whiteSpace: "nowrap",
                    background: "color-mix(in srgb, var(--muted) 16%, transparent)",
                    border: "1px solid color-mix(in srgb, var(--muted) 32%, transparent)",
                  }}>✎ {t("admin.workflows.status.draft")}</span>
                ) : (
                  <span style={{
                    display: "inline-flex", alignItems: "center", gap: 4,
                    fontSize: 11, fontWeight: 600, padding: "2px 8px", borderRadius: 999,
                    color: "#22c55e", whiteSpace: "nowrap",
                    background: "color-mix(in srgb, #22c55e 14%, transparent)",
                    border: "1px solid color-mix(in srgb, #22c55e 32%, transparent)",
                  }}>● {t("admin.workflows.status.live")}</span>
                )}
                <div className="muted" style={{ fontSize: 12 }}>{triggerText(t, w)}</div>
                {/* 右对齐占位:操作簇靠右(无论 admin 与否) */}
                <div style={{ marginLeft: "auto" }} />
                {/* 发布 / 下线(仅管理员) */}
                {isAdmin && (isDraft ? (
                  <button
                    onClick={(e) => { e.stopPropagation(); onSetStatus(w, "live"); }}
                    disabled={busyId === w.id}
                    title={t("admin.workflows.status.publish_title")}
                    style={{
                      marginLeft: "auto", fontSize: 12, fontWeight: 600,
                      padding: "5px 12px", borderRadius: 999, cursor: "pointer",
                      border: "1px solid var(--accent)", color: "var(--bg)",
                      background: "var(--accent)",
                    }}
                  >▲ {t("admin.workflows.status.publish")}</button>
                ) : (
                  <button
                    onClick={(e) => { e.stopPropagation(); onSetStatus(w, "draft"); }}
                    disabled={busyId === w.id}
                    className="ghost"
                    title={t("admin.workflows.status.unpublish_title")}
                    style={{
                      marginLeft: "auto", fontSize: 12, fontWeight: 500,
                      padding: "5px 10px", borderRadius: 999, cursor: "pointer",
                      border: "1px solid var(--border)", color: "var(--muted)",
                      background: "var(--bg)",
                    }}
                  >{t("admin.workflows.status.unpublish")}</button>
                ))}
                {/* 改这条:把这条工作流(名字+模块)喂进 AI 助手,对话即改/提建议(管理员直改,其他人提案) */}
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    setSeed(t("admin.workflows.edit_seed", { name: w.name, module: moduleLabel(w.module) }));
                    setAsstOpen(true);
                  }}
                  className="ghost"
                  title={t("admin.workflows.edit_title")}
                  style={{
                    fontSize: 12, fontWeight: 500,
                    padding: "5px 10px", borderRadius: 999, cursor: "pointer",
                    border: "1px solid var(--border)", color: "inherit",
                    background: "var(--bg)",
                  }}
                >✎ {t("admin.workflows.edit")}</button>
                {/* 试运行(仅管理员) */}
                {isAdmin && (
                <button
                  onClick={(e) => { e.stopPropagation(); onTestRun(w); }}
                  disabled={testBusyId === w.id}
                  className="ghost"
                  title={t("admin.workflows.test.title")}
                  style={{
                    fontSize: 12, fontWeight: 500,
                    padding: "5px 10px", borderRadius: 999, cursor: "pointer",
                    border: "1px solid var(--border)", color: "inherit",
                    background: "var(--bg)",
                  }}
                >▶ {t("admin.workflows.test.label")}</button>
                )}
                <button
                  onClick={(e) => { e.stopPropagation(); setRunsOpenId((id) => (id === w.id ? null : w.id)); }}
                  className="ghost"
                  aria-pressed={runsOpenId === w.id}
                  style={{
                    fontSize: 12, fontWeight: 500,
                    padding: "4px 10px", borderRadius: 999, cursor: "pointer",
                    border: "1px solid var(--border)", color: "inherit",
                    background: runsOpenId === w.id
                      ? "color-mix(in srgb, var(--accent) 12%, transparent)"
                      : "var(--bg)",
                  }}
                  title={t("admin.workflows.runs.toggle_title")}
                >🕘 {t("admin.workflows.runs.title")}</button>
                {/* 启用开关:仅管理员可切;非管理员看只读状态 */}
                {isAdmin ? (
                <label onClick={(e) => e.stopPropagation()}
                       style={{ display: "flex", alignItems: "center", gap: 6, cursor: "pointer" }}
                       title={t("admin.workflows.toggle_title")}>
                  <input
                    type="checkbox"
                    checked={w.enabled}
                    disabled={busyId === w.id}
                    onChange={() => onToggle(w)}
                    style={{ accentColor: "var(--accent)" }}
                  />
                  <span className="muted" style={{ fontSize: 13 }}>
                    {w.enabled ? t("admin.workflows.on") : t("admin.workflows.off")}
                  </span>
                </label>
                ) : (
                  <span className="muted" style={{ fontSize: 13 }}>
                    {w.enabled ? t("admin.workflows.on") : t("admin.workflows.off")}
                  </span>
                )}
                {/* 删除(仅管理员;软删,可后台恢复)*/}
                {isAdmin && (
                <button
                  onClick={(e) => { e.stopPropagation(); onDelete(w); }}
                  disabled={busyId === w.id}
                  className="ghost"
                  title={t("admin.workflows.delete_title")}
                  style={{
                    fontSize: 13, lineHeight: 1, padding: "5px 8px", borderRadius: 8,
                    cursor: "pointer", border: "1px solid var(--border)",
                    color: "var(--muted)", background: "var(--bg)",
                  }}
                  onMouseEnter={(e) => { e.currentTarget.style.color = "#ef4444"; e.currentTarget.style.borderColor = "#ef4444"; }}
                  onMouseLeave={(e) => { e.currentTarget.style.color = "var(--muted)"; e.currentTarget.style.borderColor = "var(--border)"; }}
                >🗑</button>
                )}
              </div>
              {flowOpenId === w.id && (
                <WorkflowDetail
                  t={t}
                  w={w}
                  fieldLabels={fieldLabels[w.module]}
                  /* 仅管理员可点块改;非管理员只读(不传 fields → 无 ✎) */
                  fields={isAdmin ? fieldsByModule[w.module] : undefined}
                  onChanged={isAdmin ? () => setReloadNonce((n) => n + 1) : undefined}
                />
              )}
              {runsOpenId === w.id && (
                <WorkflowRuns key={runsNonce} t={t} module={w.module} workflowId={w.id} />
              )}
            </div>
            );
          })}
        </div>
      </div>

      {/* 右侧 AI 助手:说一句即搭(crm_workflow 工具) */}
      {asstOpen ? (
        <SchemaAssistant
          key={filterModule ?? "all"}
          module={filterModule ?? "accounts"}
          scope="workflow"
          starters={EVENT_EXAMPLE_KEYS.map((k) => t(`admin.workflows.examples.${k}` as never) as string)}
          onClose={() => setAsstOpen(false)}
          onApplied={() => setReloadNonce((n) => n + 1)}
          seedText={seed}
          onSeedConsumed={() => setSeed(null)}
        />
      ) : (
        <button
          className="ghost"
          onClick={() => setAsstOpen(true)}
          title={t("admin.workflows.open_assistant")}
          style={{
            width: 44, flexShrink: 0, border: "none", borderLeft: "1px solid var(--border)",
            background: "var(--bg)", cursor: "pointer", fontSize: 18,
          }}
        >✨</button>
      )}
    </div>
  );
}

export default AdminWorkflowsPage;
