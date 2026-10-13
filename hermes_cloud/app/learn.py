"""``/learn`` —— 把「刚才这套操作/对话流程」蒸馏成一个可复用技能。

呼应北极星「Agent 当场把系统长成那样」:用户在 SPA chatbox 里点「存为技能」
(或直接打 ``/learn``),这一回合就被改写成一个 *遵循 Hermes 技能撰写规范* 的
指令,交给该 profile 的真 agent 处理。agent 用它已有的工具(读当前会话、
``read_file`` / ``web_extract`` 等采集来源)撰写出一份 ``SKILL.md``,并通过
``skill_manage`` 落盘到该 profile 的技能库(``~/.hermes/profiles/<pid>/skills/``),
下次同类需求即可复用。

为什么在云端做这一步改写(而不是让前端硬编码规范):

* ``/learn`` 在 hermes 里本就是「把当前 turn 改写成 standards-guided prompt,
  然后 fall-through 到普通 agent 处理」——纯 prompt 驱动、任何后端(本地/
  Docker/远程)都能用,无独立蒸馏引擎、无 model-tool 足迹。gateway 的 IM
  ``/learn`` 走的是 gateway slash 分发;SPA 走的是 ``/v1/chat/completions``
  代理,**不经** gateway slash 层,所以云端必须自己在代理入口把这一 turn 改写。
* 权威的技能撰写规范应由后端(hermes 的 ``agent.learn_prompt.build_learn_prompt``)
  统一构造,前端不该复制一份 authoring standards。

生产运行时云端进程的 ``sys.path`` 上有 hermes 顶级子包(``agent`` / ``gateway``
/ ``tools``),因此优先复用 hermes 权威函数;仅当 import 不可用(如未装 hermes
的隔离测试环境)时,回退到本地内联的等价 prompt,保证行为可测、可编译。
"""
from __future__ import annotations

import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# 触发前缀。前端「存为技能」按钮发送 ``/learn`` 或 ``/learn <聚焦描述>``;
# 用户也可以在输入框直接打 ``/learn`` 得到同样能力(与 CLI / gateway 一致)。
_LEARN_PREFIX = "/learn"


# ── 本地兜底 prompt ──────────────────────────────────────────────────────
# 与 hermes ``agent/learn_prompt.py`` 的 ``build_learn_prompt`` 语义等价的
# 精简版本。仅在无法 import hermes 权威实现时启用(隔离测试环境)。生产恒走
# hermes 版本,以便撰写规范随上游演进,不在此处产生漂移。
_FALLBACK_STANDARDS = """\
严格遵循 Hermes 的技能撰写规范:
- frontmatter.name:小写连字符,<=64 字符,无空格。
- frontmatter.description:一句话,**<=60 字符**,句号结尾,描述能力而非实现,
  不含营销词,不重复技能名(系统提示的技能索引会在第 60 字符处截断)。
- frontmatter.version: 0.1.0;author 一律字面值 ``Hermes``(绝不写入宿主
  用户名/git 身份——技能会被共享,这是隐私泄漏)。
- 正文按序:# 标题 + 2-3 句简介 → ## When to Use → ## Prerequisites →
  ## How to Run → ## Quick Reference → ## Procedure → ## Pitfalls →
  ## Verification(某段确无内容才可省)。
- 用 Hermes 工具名(反引号)描述动作:``terminal`` / ``read_file`` /
  ``write_file`` / ``search_files`` / ``web_extract`` / ``crm_search`` /
  ``crm_write`` / ``skill_view`` 等,不要写 cat/grep/curl 这类被工具包裹的
  裸命令。
- 只写来源里逐字出现的命令/端点/字段;绝不臆造 flag、路径或 API。保持精炼
  可扫读(简单技能 ~100 行)。"""


def _fallback_learn_prompt(user_request: str) -> str:
    """hermes ``build_learn_prompt`` 不可用时的本地等价实现。"""
    req = (user_request or "").strip()
    if not req:
        req = "我们刚才在这个对话里走过的这套流程——回顾其中的步骤,提炼成一个可复用技能"
    return (
        "[/learn] 用户希望你从下面的请求中学习一个可复用技能并保存下来。\n\n"
        f"请求:\n{req}\n\n"
        "请求是开放式的,可能同时包含要采集的『来源』(目录、文件路径、URL、"
        "『我们刚才做的事』、粘贴的笔记)和塑造技能的『要求』(聚焦点、取舍、"
        "范围、命名、角度)。把请求的每一部分都当作有意义的信息。\n\n"
        "这样做:\n"
        "1. 用你已有的工具采集用户点名的每个来源——本地文件/目录用 "
        "``read_file`` / ``search_files``,URL 用 ``web_extract``,"
        "『刚才做的事』看当前对话历史,粘贴的材料照原样用。\n"
        "1b. 把请求里的每条要求、聚焦、约束都落到你撰写的技能上。\n"
        "2. 撰写一份 SKILL.md,用 ``skill_manage`` 工具(action=\"create\")保存;"
        "选一个合适的 category。若流程需要非平凡脚本,用 ``skill_manage`` "
        "write_file 放到技能的 ``scripts/`` 下,并按相对路径引用。\n\n"
        f"{_FALLBACK_STANDARDS}\n\n"
        "完成后,告诉用户技能名、它的 category,以及它捕获了什么的一句话总结。"
    )


def build_learn_prompt(user_request: str) -> str:
    """构造 ``/learn`` 的 agent 指令。

    优先复用 hermes 的权威实现(``agent.learn_prompt.build_learn_prompt``),
    保证撰写规范随上游同步;import 不可用时回退本地等价版本。
    """
    try:
        from agent.learn_prompt import build_learn_prompt as _hermes_build  # type: ignore[import-not-found]
    except Exception:
        return _fallback_learn_prompt(user_request)
    try:
        return _hermes_build(user_request)
    except Exception:
        logger.warning("hermes build_learn_prompt failed, using fallback", exc_info=True)
        return _fallback_learn_prompt(user_request)


def _extract_learn_request(text: str) -> Optional[str]:
    """若 ``text`` 是一次 ``/learn`` 触发,返回其后的聚焦描述(可为空字符串);
    否则返回 ``None``。

    识别 ``/learn``(裸)与 ``/learn <描述>``,大小写不敏感,容忍前导空白。
    仅匹配整条消息以 ``/learn`` 开头的情形——正文里偶然提到 ``/learn`` 不触发。
    """
    if not text:
        return None
    stripped = text.strip()
    low = stripped.lower()
    if low == _LEARN_PREFIX:
        return ""
    if low.startswith(_LEARN_PREFIX + " "):
        return stripped[len(_LEARN_PREFIX):].strip()
    # 允许换行分隔:``/learn\n聚焦...``
    if low.startswith(_LEARN_PREFIX + "\n"):
        return stripped[len(_LEARN_PREFIX):].strip()
    return None


def maybe_rewrite_learn_body(raw_body: bytes) -> Optional[bytes]:
    """若 chat-completions 请求体的最新 user turn 是一次 ``/learn`` 触发,
    把该 turn 的文本内容替换为 ``build_learn_prompt(...)`` 的 standards-guided
    指令,返回改写后的请求体(bytes);否则返回 ``None``(调用方原样透传)。

    只改写「最新 user message」的文本部分:
    - content 是纯字符串:直接替换。
    - content 是多模态数组(text + image_url 块):只替换其中 text 块,图片/
      文档块原样保留(learn 一般无附件,但保持通用)。

    其余消息、``model`` / ``stream`` 等字段一律不动——改写后的体继续走完整的
    ``_proxy`` / ``_proxy_stream`` 流式代理,agent 因此在**同一 session**里
    看到之前的完整对话历史,从而能提炼「刚才这套流程」。
    """
    if not raw_body:
        return None
    try:
        body = json.loads(raw_body)
    except Exception:
        return None
    if not isinstance(body, dict):
        return None
    msgs = body.get("messages")
    if not isinstance(msgs, list) or not msgs:
        return None

    # 定位最新 user message。
    idx = None
    for i in range(len(msgs) - 1, -1, -1):
        m = msgs[i]
        if isinstance(m, dict) and m.get("role") == "user":
            idx = i
            break
    if idx is None:
        return None

    msg = msgs[idx]
    content = msg.get("content")

    # 抽出该 turn 的可见文本,判断是否 ``/learn`` 触发。
    if isinstance(content, str):
        req = _extract_learn_request(content)
        if req is None:
            return None
        msg["content"] = build_learn_prompt(req)
    elif isinstance(content, list):
        # 拼出所有 text 块判断触发;命中则替换第一个 text 块、丢弃其余 text 块
        # (它们是同一句 ``/learn`` 的碎片),保留非 text(图片/文档)块。
        text_join = " ".join(
            str(p.get("text") or "")
            for p in content
            if isinstance(p, dict) and p.get("type") == "text"
        )
        req = _extract_learn_request(text_join)
        if req is None:
            return None
        new_content = []
        replaced = False
        for p in content:
            if isinstance(p, dict) and p.get("type") == "text":
                if not replaced:
                    new_content.append({"type": "text", "text": build_learn_prompt(req)})
                    replaced = True
                # 后续 text 块丢弃
            else:
                new_content.append(p)
        if not replaced:
            new_content.insert(0, {"type": "text", "text": build_learn_prompt(req)})
        msg["content"] = new_content
    else:
        return None

    try:
        return json.dumps(body, ensure_ascii=False).encode("utf-8")
    except Exception:
        logger.warning("learn body re-encode failed", exc_info=True)
        return None
