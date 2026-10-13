"""模型角色派生：把 DB providers_json 里的"意图"派生成 auxiliary / MoA 的
生效值。纯函数——只吃 providers_json，绝不读 config.yaml / profile 文件，
以便 GET 端点(纯读 DB)与 sync 落盘路径共用同一份启发式(单一真相)。

设计见 docs/superpowers/specs/2026-07-06-model-roles-settable-design.md
"""
from __future__ import annotations

from typing import Any, Optional

AUX_TASKS = ("compression", "curator", "title_generation")
_FREE_FLASH = "deepseek-v4-flash-free"

# MoA 强模型优选序：命中越靠前分越高(数越大越优)。从 main.py 迁入。
_MOA_MODEL_PREFERENCE: tuple[tuple[str, ...], ...] = (
    ("minimax-m3", "minimax_m3", "minimaxm3", "abab-m3", "m3"),   # MiniMax-M3
    ("deepseek-v4-pro", "deepseek-v4", "deepseek-r", "deepseek"), # DeepSeek 旗舰
    ("glm-5.1", "glm-5", "glm-4", "glm"),                         # 智谱 GLM
    ("kimi", "moonshot"),                                         # Kimi
    ("qwen", "qwq"),                                              # 通义千问
)


def _moa_model_rank(model_id: str) -> int:
    """强模型优选打分：命中靠前的组给更高分(数越大越优)，不命中返回 -1。"""
    mid = str(model_id or "").lower()
    n = len(_MOA_MODEL_PREFERENCE)
    for idx, needles in enumerate(_MOA_MODEL_PREFERENCE):
        if any(needle in mid for needle in needles):
            return n - idx
    return -1


def _enabled_pairs(providers_json: dict) -> list[tuple[str, str]]:
    """展开已启用 provider 的 (provider_id, model_id)，按 provider 发现顺序。"""
    pairs: list[tuple[str, str]] = []
    providers = (providers_json or {}).get("providers") or {}
    for prov_id, prov in providers.items():
        if not isinstance(prov, dict) or not prov.get("enabled", True):
            continue
        for m in prov.get("models") or []:
            mid = m.get("id") if isinstance(m, dict) else m
            if mid:
                pairs.append((str(prov_id), str(mid)))
    return pairs


def _split_ref(ref: Any) -> Optional[tuple[str, str]]:
    """把 DB 里的 "provider::model" 串拆成 (provider_id, model_id)。"""
    if not isinstance(ref, str) or "::" not in ref:
        return None
    prov, _, model = ref.partition("::")
    if not prov or not model:
        return None
    return prov, model


def _to_slot(prov_id: str, model_id: str) -> dict:
    """(provider_id, model_id) → config.yaml slot（named-custom-provider 规矩）。"""
    return {"provider": f"custom:{prov_id}", "model": model_id}


def resolve_auxiliary(providers_json: dict) -> dict:
    """-> {task: Slot}；Slot 含 provider/model/source。
    解析优先级：per-task 覆盖 > unified > 自动(有 flash 用 flash，否则 provider_default 哨兵)。
    """
    pairs = _enabled_pairs(providers_json)
    available = {f"{p}::{m}" for p, m in pairs}
    flash = next(((p, m) for p, m in pairs if m == _FREE_FLASH), None)

    aux = (providers_json or {}).get("auxiliary") or {}
    unified = aux.get("unified")
    overrides = aux.get("overrides") or {}

    out: dict = {}
    for task in AUX_TASKS:
        explicit = overrides.get(task)
        source = "custom"
        if not explicit:
            explicit, source = unified, "unified"
        if explicit and explicit in available:
            prov, model = _split_ref(explicit)
            out[task] = {"provider": f"custom:{prov}", "model": model, "source": source}
        elif flash:
            out[task] = {"provider": f"custom:{flash[0]}", "model": flash[1], "source": "auto"}
        else:
            out[task] = {"provider": None, "model": None, "source": "provider_default"}
    return out


def resolve_moa(providers_json: dict) -> Optional[dict]:
    """-> {"reference_models":[Slot...], "aggregator": Slot} 或 None。
    None 表示不显示"深度会诊"：moa.enabled=false，或可用模型 <2。
    优先级：显式 reference_models(全在启用列表且≥2) > 自动优选；aggregator 同理，
    自动时优先 MiniMax-M3，否则取第一个 reference。
    """
    moa = (providers_json or {}).get("moa")
    if isinstance(moa, dict) and moa.get("enabled") is False:
        return None

    pairs = _enabled_pairs(providers_json)
    available = {f"{p}::{m}": (p, m) for p, m in pairs}
    if len(available) < 2:
        return None

    # 显式成员：全部落在启用列表、且去重后 ≥2 才采纳。
    refs: list[tuple[str, str]] = []
    if isinstance(moa, dict) and isinstance(moa.get("reference_models"), list):
        seen: set[str] = set()
        for ref in moa["reference_models"]:
            if ref in available and ref not in seen:
                seen.add(ref)
                refs.append(available[ref])
    if len(refs) < 2:
        # 自动优选：优选分降序，同分保持发现顺序，去重同一 model_id，最多 3。
        ranked = sorted(pairs, key=lambda pm: _moa_model_rank(pm[1]), reverse=True)
        refs, seen = [], set()
        for prov, model in ranked:
            if model in seen:
                continue
            seen.add(model)
            refs.append((prov, model))
            if len(refs) >= 3:
                break
    if len(refs) < 2:
        return None

    # aggregator：显式且有效 > 自动(优先 M3) > 第一个 reference。
    agg = None
    if isinstance(moa, dict) and moa.get("aggregator") in available:
        agg = available[moa["aggregator"]]
    if agg is None:
        agg = next((pm for pm in pairs if _moa_model_rank(pm[1]) == len(_MOA_MODEL_PREFERENCE)), None)
    if agg is None:
        agg = refs[0]

    return {
        "reference_models": [_to_slot(p, m) for p, m in refs],
        "aggregator": _to_slot(*agg),
    }


def apply_to_config(existing: dict, providers_json: dict) -> None:
    """把派生的 auxiliary/moa 结果 key 级合并进 config.yaml dict(原地改 existing)。

    - auxiliary：只覆写 provider/model；provider_default 的 slot 清掉 provider/model
      但保留 timeout 等无关键；写完为空的 slot 整个删掉。
    - moa：非 None 写 moa.presets.deep-council；None 则移除该 preset(保留其他 preset)。
    """
    # ── auxiliary ──
    aux_resolved = resolve_auxiliary(providers_json)
    aux_section = existing.get("auxiliary")
    if not isinstance(aux_section, dict):
        aux_section = {}
    for task, slot in aux_resolved.items():
        cur = aux_section.get(task)
        cur = dict(cur) if isinstance(cur, dict) else {}
        if slot["model"] is None:  # provider_default → 交给 hermes 兜底
            cur.pop("provider", None)
            cur.pop("model", None)
        else:
            cur["provider"] = slot["provider"]
            cur["model"] = slot["model"]
        if cur:
            aux_section[task] = cur
        else:
            aux_section.pop(task, None)
    if aux_section:
        existing["auxiliary"] = aux_section
    else:
        existing.pop("auxiliary", None)

    # ── moa ──
    moa_resolved = resolve_moa(providers_json)
    moa_section = existing.get("moa")
    if not isinstance(moa_section, dict):
        moa_section = {}
    presets = moa_section.get("presets")
    if not isinstance(presets, dict):
        presets = {}
    if moa_resolved is not None:
        presets["deep-council"] = moa_resolved
    else:
        presets.pop("deep-council", None)
    moa_section["presets"] = presets
    existing["moa"] = moa_section
