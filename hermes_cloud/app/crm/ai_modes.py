"""
AI 字段 4 模式策略表。

每种模式封装：指令模板、是否联网、是否注入图谱、结果类型倾向。
供 build_context / dispatch / apply 调用。
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ModeSpec:
    mode: str
    instruction_template: str   # 给 LLM 的指令模板，纯指令(实际上下文/输出要求由调用方另行拼接)
    needs_web: bool             # 是否要联网（web_agent=True）
    needs_graph: bool           # 是否要注入记忆图谱/子图（classify/summarize=True）
    coerce_kind: str            # "option" | "text" | "number"
    allow_new_options: bool = False


_SPECS: dict[str, ModeSpec] = {
    "classify": ModeSpec(
        mode="classify",
        instruction_template=(
            "你是一名数据分类助手。根据记录上下文与给定选项，将该记录归类到最符合的选项之一，"
            "只输出选项名称本身。若允许新增且已有选项均不适用，可输出一个简短的新选项名。"
        ),
        needs_web=False,
        needs_graph=True,
        coerce_kind="option",
        allow_new_options=False,
    ),
    "summarize": ModeSpec(
        mode="summarize",
        instruction_template=(
            "你是一名摘要撰写助手。根据记录属性及关联信息，生成简洁准确的摘要文本，"
            "直接输出摘要内容，不要包含多余解释。"
        ),
        needs_web=False,
        needs_graph=True,
        coerce_kind="text",
    ),
    "web_agent": ModeSpec(
        mode="web_agent",
        instruction_template=(
            "你是一名联网调研助手。根据任务描述通过网络搜索获取最新信息并整理结果，"
            "基于真实搜索结果输出，若无法确认请如实说明、不要编造。"
        ),
        needs_web=True,
        needs_graph=False,
        coerce_kind="text",
    ),
    "prompt": ModeSpec(
        mode="prompt",
        instruction_template=(
            "你是一名内容生成助手。根据自定义指令和变量生成对应内容，"
            "严格按指令要求输出，不要添加无关内容。"
        ),
        needs_web=False,
        needs_graph=False,
        coerce_kind="text",
    ),
}


def mode_spec(mode: str) -> ModeSpec:
    """返回该 mode 的 ModeSpec；未知 mode 抛 ValueError。"""
    if mode not in _SPECS:
        raise ValueError(
            f"未知 AI 字段模式: {mode!r}。"
            f"有效模式为: {sorted(_SPECS.keys())}"
        )
    return _SPECS[mode]
