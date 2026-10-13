"""
AI Attributes {} 变量解析引擎

支持三类变量：
  own      — {字段名}           取当前记录自己的字段值
  relation — {关系字段 › 目标字段}  单跳关联穿透（全角 › 或半角 >）
  derived  — {派生名}           预定义派生属性集合

公开 API：
  parse_variables(prompt) -> list[dict]
  render(prompt, *, record, relation_resolver=None, derived=None) -> (str, list[str])
"""

from __future__ import annotations

import re
from typing import Callable

# 预定义派生属性名
_DERIVED_NAMES: frozenset[str] = frozenset(
    {"最近互动", "首次互动", "互动次数", "连接强度"}
)

# 匹配 {…} 的正则，贪婪取内容（不跨花括号）
_VAR_RE = re.compile(r"\{([^{}]+)\}")

# 关系分隔符：全角 › (U+203A) 或半角 >
_SEP_RE = re.compile(r"\s*[›>]\s*")


def _classify(inner: str) -> dict:
    """解析单个变量内部文本，返回 {kind, path, raw}。"""
    raw = "{" + inner + "}"
    # 关系穿透：含分隔符
    parts = _SEP_RE.split(inner, maxsplit=1)
    if len(parts) == 2:
        return {
            "kind": "relation",
            "path": [parts[0].strip(), parts[1].strip()],
            "raw": raw,
        }
    name = inner.strip()
    if name in _DERIVED_NAMES:
        return {"kind": "derived", "path": [name], "raw": raw}
    return {"kind": "own", "path": [name], "raw": raw}


def parse_variables(prompt: str) -> list[dict]:
    """抽出 prompt 里所有 {..} 变量。

    返回：
        [{"kind": "own"|"relation"|"derived", "path": [...], "raw": "{...}"}]
    """
    return [_classify(m.group(1)) for m in _VAR_RE.finditer(prompt)]


def render(
    prompt: str,
    *,
    record: dict,
    relation_resolver: Callable[[str, str], str | None] | None = None,
    derived: dict | None = None,
) -> tuple[str, list[str]]:
    """把 prompt 中的变量替换成实际值。

    参数：
        record            当前记录字段字典
        relation_resolver callable(rel_field, target_field) -> str|None
                          为 None 或返回 None 时用占位符
        derived           派生属性字典；为 None 时所有派生变量用占位符

    返回：
        (rendered_prompt, basis)
        basis 只包含成功解析的变量来源：
          "field:<名>"  / "relation:<关系>.<目标>" / "derived:<名>"
        解析不到的变量替换为 '(空)'，不计入 basis。
    """
    basis: list[str] = []
    _PLACEHOLDER = "(空)"

    def replace_one(match: re.Match) -> str:
        inner = match.group(1)
        var = _classify(inner)
        kind = var["kind"]
        path = var["path"]

        if kind == "own":
            value = record.get(path[0])
            if value is not None:
                basis.append(f"field:{path[0]}")
                return str(value)
            return _PLACEHOLDER

        if kind == "relation":
            rel_field, target_field = path[0], path[1]
            if relation_resolver is not None:
                value = relation_resolver(rel_field, target_field)
                if value is not None:
                    basis.append(f"relation:{rel_field}.{target_field}")
                    return str(value)
            return _PLACEHOLDER

        if kind == "derived":
            name = path[0]
            if derived is not None:
                value = derived.get(name)
                if value is not None:
                    basis.append(f"derived:{name}")
                    return str(value)
            return _PLACEHOLDER

        return _PLACEHOLDER  # 不可达，保险

    rendered = _VAR_RE.sub(replace_one, prompt)
    return rendered, basis
