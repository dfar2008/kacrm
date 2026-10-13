import { useEffect, useState } from "react";
import {
  listRecordCollaborators, addRecordCollaborator, removeRecordCollaborator,
  type RecordCollaborator,
} from "@/lib/api";
import { toast } from "@/lib/toast";
import { useT } from "@/lib/i18n";
import { UserPicker } from "./AllInfoDrawer";

/**
 * 记录共享弹窗。
 *
 * 行为:
 * - 打开时拉一次 collaborator 名单
 * - UserPicker 提交后立即 POST(默认 can_view=true, can_edit=false)
 * - 行内下拉切 view/edit → POST upsert
 * - X → DELETE
 * - 全部乐观更新,失败回滚 + toast
 *
 * 注意:UserPicker 的实际 props 是 value:string / onChange / onCommit / onCancel。
 * 这里用 pickerValue(string) 对接 UserPicker,提交时转 number 调 API。
 */
export function ShareRecordModal({
  module, recordId, recordLabel, onClose,
}: {
  module: string;
  recordId: number;
  recordLabel: string;
  onClose: () => void;
}) {
  const t = useT();
  const [list, setList] = useState<RecordCollaborator[]>([]);
  const [loading, setLoading] = useState(true);
  // UserPicker value is a stringified user id (or "" for unset)
  const [pickerValue, setPickerValue] = useState("");
  // 每次添加完一个 user 后递增,强制 UserPicker remount 重新展开搜索面板
  // (UserPicker 默认收起为"搜索用户…"按钮,选完一人自动收起 — 我们要支持连选多人)
  const [pickerKey, setPickerKey] = useState(0);

  useEffect(() => {
    listRecordCollaborators(module, recordId)
      .then(r => setList(r.collaborators))
      .catch(e => toast(String(e), "error"))
      .finally(() => setLoading(false));
  }, [module, recordId]);

  async function handleCommit(val: string) {
    const uid = val ? parseInt(val, 10) : NaN;
    if (!val || isNaN(uid)) return;
    // 避免重复添加
    if (list.find(c => c.user_id === uid)) {
      setPickerValue("");
      return;
    }
    const prev = list;
    const optimistic: RecordCollaborator = {
      user_id: uid, username: "", realname: null,
      can_view: true, can_edit: false, created_at: new Date().toISOString(),
    };
    setList([...prev, optimistic]);
    setPickerValue("");
    try {
      await addRecordCollaborator(module, recordId,
        { user_id: uid, can_view: true, can_edit: false });
      // 重新拉一次获得 username/realname
      const r = await listRecordCollaborators(module, recordId);
      setList(r.collaborators);
      // remount UserPicker → 重新展开搜索面板,支持连选下一个
      setPickerKey(k => k + 1);
      toast(t("crm.share.toast.added", {
        name: r.collaborators.find(c => c.user_id === uid)?.realname || String(uid),
      }), "success");
    } catch (e) {
      setList(prev);
      toast(String(e), "error");
    }
  }

  async function changeAccess(uid: number, can_edit: boolean) {
    const prev = list;
    setList(prev.map(c => c.user_id === uid ? { ...c, can_edit, can_view: true } : c));
    try {
      await addRecordCollaborator(module, recordId,
        { user_id: uid, can_view: true, can_edit });
    } catch (e) {
      setList(prev);
      toast(String(e), "error");
    }
  }

  async function removeUser(uid: number, name: string) {
    const prev = list;
    setList(prev.filter(c => c.user_id !== uid));
    try {
      await removeRecordCollaborator(module, recordId, uid);
      toast(t("crm.share.toast.removed", { name }), "success");
    } catch (e) {
      setList(prev);
      toast(String(e), "error");
    }
  }

  return (
    <div style={{
      // z-index < UserPicker dropdown(200)so 候选列表能浮在 modal 之上
      position: "fixed", inset: 0, background: "var(--overlay)",
      display: "grid", placeItems: "center", zIndex: 100,
    }} onClick={onClose}>
      <div onClick={e => e.stopPropagation()} style={{
        background: "var(--bg)", border: "1px solid var(--border)",
        borderRadius: 10, padding: "22px 26px",
        width: 640, maxWidth: "92vw",
        maxHeight: "82vh", display: "flex", flexDirection: "column",
      }}>
        <h3 style={{ margin: "0 0 16px", fontSize: 16 }}>
          {t("crm.share.modal.title", { record: recordLabel })}
        </h3>

        {/* 选人区 — UserPicker 默认收起为"搜索用户…"按钮,点击展开搜索;
            选完一人后 picker 自然收起,要加下一个再点 */}
        <div style={{ marginBottom: 6, fontSize: 14, color: "var(--muted)" }}>
          {t("crm.share.modal.search")}
        </div>
        <UserPicker
          key={pickerKey}
          value={pickerValue}
          onChange={setPickerValue}
          onCommit={handleCommit}
          onCancel={() => setPickerValue("")}
        />

        <div style={{ marginTop: 20, fontSize: 14, color: "var(--muted)" }}>
          {t("crm.share.modal.list_header")}
        </div>
        <div style={{ flex: 1, overflowY: "auto", marginTop: 6, minHeight: 100 }}>
          {loading ? (
            <div className="muted">{t("common.loading")}</div>
          ) : list.length === 0 ? (
            <div className="muted" style={{ padding: "16px 0" }}>
              {t("crm.share.modal.empty")}
            </div>
          ) : (
            <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
              {list.map(c => (
                <li key={c.user_id} style={{
                  display: "flex", alignItems: "center", gap: 14,
                  padding: "10px 4px",
                  borderBottom: "1px solid var(--border)",
                }}>
                  <span style={{ flex: 1, whiteSpace: "nowrap",
                    overflow: "hidden", textOverflow: "ellipsis" }}>
                    {c.realname || c.username || `#${c.user_id}`}
                  </span>
                  {/* 可查看 — 始终勾且 disabled:share 必含 view,撤销请用 ✕ */}
                  <label style={{ display: "flex", alignItems: "center", gap: 4,
                    fontSize: 14, color: "var(--muted)", cursor: "not-allowed",
                    whiteSpace: "nowrap", flexShrink: 0 }}>
                    <input type="checkbox" checked disabled
                      title={t("crm.share.access.view_locked_title")} />
                    {t("crm.share.access.view")}
                  </label>
                  {/* 可编辑 — 真正可切换 */}
                  <label style={{ display: "flex", alignItems: "center", gap: 4,
                    fontSize: 14, cursor: "pointer",
                    whiteSpace: "nowrap", flexShrink: 0 }}>
                    <input type="checkbox"
                      checked={c.can_edit}
                      onChange={e => changeAccess(c.user_id, e.target.checked)} />
                    {t("crm.share.access.edit")}
                  </label>
                  <button
                    className="ghost"
                    onClick={() => removeUser(
                      c.user_id,
                      c.realname || c.username || `#${c.user_id}`,
                    )}
                    title="Remove"
                    style={{ padding: "4px 10px", color: "var(--danger)" }}
                  >✕</button>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div style={{ marginTop: 18, textAlign: "right" }}>
          <button className="primary" onClick={onClose}>
            {t("crm.share.modal.close")}
          </button>
        </div>
      </div>
    </div>
  );
}
