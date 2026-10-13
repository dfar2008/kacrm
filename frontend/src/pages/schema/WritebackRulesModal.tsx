/**
 * WritebackRulesModal — 汇总规则配置(P4 §5.5)。
 *
 * 两类规则(底层同一个 field_writeback 引擎,源记录每次保存自动重算):
 *   1. 子明细 → 本模块:子明细某数值字段 按 calc 聚合到本模块字段(经典汇总)。
 *   2. 本模块 → 关联模块:本模块某数值字段 按 calc 聚合到「本模块外键所指向的
 *      模块」的字段(如 salesorders.最终金额 sum → 客户某字段,走 accounts_id)。
 *      引擎按 {target}_id 找目标,故只列本模块列名正好 {target}_id 的主关联。
 */
import { useEffect, useState, useCallback } from "react";
import {
  listSubdetails, getModuleFields, listWritebackRules,
  createWritebackRule, deleteWritebackRule, fetchWritebackTargets, ApiError,
  type SubdetailDef, type WritebackRule, type WritebackTarget,
} from "@/lib/api";
import { useT } from "@/lib/i18n";

const NUMERIC = ["number", "currency", "float", "int", "percent", "formula"];
const CALCS = ["sum", "avg", "count", "max", "min"];

type Mode = "sub" | "cross";

export function WritebackRulesModal({
  module, onClose,
}: { module: string; onClose: () => void }) {
  const t = useT();
  const [mode, setMode] = useState<Mode>("sub");
  const [subs, setSubs] = useState<SubdetailDef[]>([]);
  const [selfFields, setSelfFields] = useState<{ field_name: string; field_label?: string | null }[]>([]);
  const [targets, setTargets] = useState<WritebackTarget[]>([]);
  const [rules, setRules] = useState<WritebackRule[]>([]);
  const [calc, setCalc] = useState("sum");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // 子明细模式:src=子明细名, srcField=子明细数值字段, tgtField=本模块字段。
  const [src, setSrc] = useState("");
  const [srcField, setSrcField] = useState("");
  const [tgtField, setTgtField] = useState("");
  // 跨模块模式:xSrcField=本模块数值字段, xTgtMod=关联目标模块, xTgtField=目标字段。
  const [xSrcField, setXSrcField] = useState("");
  const [xTgtMod, setXTgtMod] = useState("");
  const [xTgtField, setXTgtField] = useState("");

  // 规则来源:每个子明细 + 本模块(跨模块规则的 source 是本模块)。
  const refreshRules = useCallback(async (subDefs: SubdetailDef[]) => {
    const all: WritebackRule[] = [];
    for (const name of [...subDefs.map(s => s.name), module]) {
      try {
        const r = await listWritebackRules(name);
        all.push(...(r.rules || []));
      } catch { /* ignore */ }
    }
    setRules(all);
  }, [module]);

  useEffect(() => {
    (async () => {
      try {
        const sd = await listSubdetails(module);
        const subDefs = sd.subdetails || [];
        setSubs(subDefs);
        if (subDefs.length) setSrc(subDefs[0].name);
        const mf = await getModuleFields(module);
        setSelfFields((mf.fields || [])
          .filter((f: any) => NUMERIC.includes((f.field_type || "").toLowerCase()))
          .map((f: any) => ({ field_name: f.field_name, field_label: f.field_label })));
        try {
          const tg = await fetchWritebackTargets(module);
          setTargets(tg.targets || []);
          if (tg.targets?.length) setXTgtMod(tg.targets[0].module);
        } catch { setTargets([]); }
        await refreshRules(subDefs);
      } catch (e) {
        setErr(e instanceof ApiError ? `HTTP ${e.status}` : String(e));
      }
    })();
  }, [module, refreshRules]);

  const srcDef = subs.find(s => s.name === src);
  const subNumericFields = (srcDef?.fields || []).filter(f => NUMERIC.includes((f.field_type || "").toLowerCase()));
  const xTarget = targets.find(tg => tg.module === xTgtMod);

  const add = async () => {
    setBusy(true); setErr(null);
    try {
      if (mode === "sub") {
        if (!src || !srcField || !tgtField) return;
        await createWritebackRule(src, {
          source_field: srcField, target_module: module,
          target_field: tgtField, calc_method: calc,
        });
        setSrcField(""); setTgtField("");
      } else {
        if (!xSrcField || !xTgtMod || !xTgtField) return;
        await createWritebackRule(module, {
          source_field: xSrcField, target_module: xTgtMod,
          target_field: xTgtField, calc_method: calc,
        });
        setXSrcField(""); setXTgtField("");
      }
      await refreshRules(subs);
    } catch (e) {
      setErr(e instanceof ApiError
        ? (typeof e.detail === "string" ? e.detail : `HTTP ${e.status}`) : String(e));
    } finally { setBusy(false); }
  };

  const del = async (r: WritebackRule) => {
    try {
      await deleteWritebackRule(r.source_module_name, r.id);
      await refreshRules(subs);
    } catch (e) { setErr(String(e)); }
  };

  const tab = (m: Mode, label: string) => (
    <button
      onClick={() => { setMode(m); setErr(null); }}
      style={{
        padding: "5px 12px", fontSize: 13, cursor: "pointer", borderRadius: 6,
        border: "1px solid var(--border)",
        background: mode === m ? "var(--accent)" : "transparent",
        color: mode === m ? "var(--fg-on-accent)" : "var(--muted)",
      }}
    >{label}</button>
  );

  return (
    <div role="dialog" aria-label={t("admin.schema.writeback.title")}
      style={{ position: "fixed", inset: 0, zIndex: 1000, background: "var(--overlay)",
        display: "flex", alignItems: "center", justifyContent: "center" }}
      onClick={onClose}>
      <div onClick={e => e.stopPropagation()} style={{ width: 600, maxHeight: "85vh",
        overflowY: "auto", background: "var(--panel)",
        border: "1px solid var(--border)", borderRadius: 10, padding: 20 }}>
        <h3 style={{ marginTop: 0 }}>{t("admin.schema.writeback.title")}</h3>
        <p className="muted" style={{ fontSize: 14, marginTop: 0 }}>
          {t("admin.schema.writeback.hint")}
        </p>

        <div style={{ display: "flex", gap: 8, marginBottom: 10 }}>
          {tab("sub", t("admin.schema.writeback.mode.sub"))}
          {tab("cross", t("admin.schema.writeback.mode.cross"))}
        </div>

        {mode === "sub" ? (
          subs.length === 0 ? (
            <div className="muted" style={{ fontSize: 14, padding: 12 }}>
              {t("admin.schema.writeback.no_subdetail")}
            </div>
          ) : (
            <div style={rowBox}>
              <select value={src} onChange={e => { setSrc(e.target.value); setSrcField(""); }} style={sel}>
                {subs.map(s => <option key={s.name} value={s.name}>{s.title || s.name}</option>)}
              </select>
              <select value={srcField} onChange={e => setSrcField(e.target.value)} style={sel}>
                <option value="">{t("admin.schema.writeback.src_field")}</option>
                {subNumericFields.map(f => <option key={f.field_name} value={f.field_name}>{f.field_label || f.field_name}</option>)}
              </select>
              <select value={calc} onChange={e => setCalc(e.target.value)} style={sel}>
                {CALCS.map(c => <option key={c} value={c}>{c}</option>)}
              </select>
              <span style={{ color: "var(--muted)" }}>→</span>
              <select value={tgtField} onChange={e => setTgtField(e.target.value)} style={sel}>
                <option value="">{t("admin.schema.writeback.tgt_field")}</option>
                {selfFields.map(f => <option key={f.field_name} value={f.field_name}>{f.field_label || f.field_name}</option>)}
              </select>
              <button onClick={add} disabled={busy || !srcField || !tgtField}
                style={{ ...btn, opacity: busy || !srcField || !tgtField ? 0.5 : 1 }}>
                {t("admin.schema.writeback.add")}
              </button>
            </div>
          )
        ) : (
          targets.length === 0 ? (
            <div className="muted" style={{ fontSize: 14, padding: 12 }}>
              {t("admin.schema.writeback.no_target")}
            </div>
          ) : (
            <div style={rowBox}>
              <select value={xSrcField} onChange={e => setXSrcField(e.target.value)} style={sel}>
                <option value="">{t("admin.schema.writeback.src_field")}</option>
                {selfFields.map(f => <option key={f.field_name} value={f.field_name}>{f.field_label || f.field_name}</option>)}
              </select>
              <select value={calc} onChange={e => setCalc(e.target.value)} style={sel}>
                {CALCS.map(c => <option key={c} value={c}>{c}</option>)}
              </select>
              <span style={{ color: "var(--muted)" }}>→</span>
              <select value={xTgtMod} onChange={e => { setXTgtMod(e.target.value); setXTgtField(""); }} style={sel}>
                {targets.map(tg => <option key={tg.module} value={tg.module}>{tg.label || tg.module}</option>)}
              </select>
              <select value={xTgtField} onChange={e => setXTgtField(e.target.value)} style={sel}>
                <option value="">{t("admin.schema.writeback.tgt_field")}</option>
                {(xTarget?.fields || []).map(f => <option key={f.field_name} value={f.field_name}>{f.field_label || f.field_name}</option>)}
              </select>
              <button onClick={add} disabled={busy || !xSrcField || !xTgtField}
                style={{ ...btn, opacity: busy || !xSrcField || !xTgtField ? 0.5 : 1 }}>
                {t("admin.schema.writeback.add")}
              </button>
            </div>
          )
        )}

        {err && <div style={{ color: "var(--danger)", marginTop: 10, fontSize: 14 }}>{err}</div>}

        <div style={{ marginTop: 14 }}>
          {rules.length === 0 && (
            <div className="muted" style={{ fontSize: 14 }}>{t("admin.schema.writeback.none")}</div>
          )}
          {rules.map(r => (
            <div key={`${r.source_module_name}-${r.id}`} style={{ display: "flex", alignItems: "center", gap: 8,
              padding: "6px 8px", borderBottom: "1px solid var(--border)", fontSize: 14 }}>
              <span style={{ flex: 1 }}>
                {(r.source_module_name)}.{(r.source_field_label || r.source_field_name)}
                {" "}<b>{r.calc_method}</b> → {(r.target_module_name)}.{(r.target_field_label || r.target_field_name)}
              </span>
              <button onClick={() => del(r)} style={delBtn}>×</button>
            </div>
          ))}
        </div>

        <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 16 }}>
          <button onClick={onClose} style={btnGhost}>{t("common.close")}</button>
        </div>
      </div>
    </div>
  );
}

const rowBox: React.CSSProperties = {
  display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap",
  border: "1px solid var(--border)", borderRadius: 6, padding: 10,
};
const sel: React.CSSProperties = {
  padding: "5px 7px", background: "var(--bg)", color: "inherit",
  border: "1px solid var(--border)", borderRadius: 5, fontSize: 14,
};
const btn: React.CSSProperties = {
  padding: "5px 12px", background: "var(--accent)", color: "var(--fg-on-accent)",
  border: "none", borderRadius: 5, cursor: "pointer", fontSize: 14,
};
const btnGhost: React.CSSProperties = {
  padding: "6px 14px", background: "transparent", color: "inherit",
  border: "1px solid var(--border)", borderRadius: 6, cursor: "pointer", fontSize: 14,
};
const delBtn: React.CSSProperties = {
  background: "transparent", color: "var(--muted)", border: "none", cursor: "pointer", fontSize: 16,
};
