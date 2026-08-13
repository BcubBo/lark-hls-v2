# ================================================================
# lark-hls-v2 state/text.py -- 总导游图（改代码前必读，读完再动手）
# ▍这是什么
# ① 干什么：文本累积器和推理标签解析。TextState 追踪流式文本，
#    split_reasoning_text / extract_thinking_content / strip_reasoning_tags
#    处理 <thinking>/<reasoning> 标签的提取和剥离。
# ② 技术栈：Python 3.11+ / re（正则）
# ③ 依赖：无外部依赖
# ④ 给谁看：维护 lark-hls-v2 的开发者，理解推理文本解析逻辑。
# ▍文件从上到下的结构
# REASONING_PREFIX: "Reasoning:\n" 常量
# _REASONING_TAG_RE 等正则：匹配 <thinking>/<reasoning>/<thought>/<antthinking> 标签
# split_reasoning_text(): 将文本拆分为 reasoning_text 和 answer_text
# extract_thinking_content(): 提取标签内的推理内容
# strip_reasoning_tags(): 剥离所有推理标签
# _clean_reasoning_prefix(): 清理 "Reasoning:\n" 前缀
# TextState: 文本累积器（completed_text + accumulated）
# ▍修改铁律
# 1. _REASONING_TAG_RE 的正则模式【不】删掉任何标签名（think/thinking/thought/antthinking），
#    改了会导致某些模型的推理标签不被识别。
# 2. extract_thinking_content 的状态机逻辑【不】简化，改了会导致嵌套标签解析错误。
# 3. TextState.on_deliver 会自动 strip_reasoning_tags，【不】删掉，改了会导致推理标签出现在最终回答里。
# ================================================================

"""文本累积器 -- 增量式流式文本追踪."""

from __future__ import annotations
import re

__all__ = [
    "TextState",
    "REASONING_PREFIX",
    "split_reasoning_text",
    "extract_thinking_content",
    "strip_reasoning_tags",
]

REASONING_PREFIX = "Reasoning:\n"

# ▍推理标签正则 -- 匹配 <thinking>/<reasoning>/<thought>/<antthinking>
_REASONING_TAG = r"(?:think(?:ing)?|thought|antthinking)"
_REASONING_TAG_RE = re.compile(r"<\s*(/?)\s*" + _REASONING_TAG + r"\s*>", re.IGNORECASE)
_REASONING_OPEN_RE = re.compile(r"<\s*" + _REASONING_TAG + r"\s*>", re.IGNORECASE)
_REASONING_CLOSE_RE = re.compile(r"<\s*/\s*" + _REASONING_TAG + r"\s*>", re.IGNORECASE)

# ▍推理文本拆分

def split_reasoning_text(text: str | None) -> dict[str, str | None]:
    """将文本拆分为 reasoning_text 和 answer_text。
    三种路径：Reasoning: 前缀 -> 标签内容 -> 纯 answer。
    改了拆分逻辑会导致推理和回答混合显示。
    """
    if not isinstance(text, str) or not text.strip():
        return {}
    trimmed = text.strip()
    if trimmed.startswith(REASONING_PREFIX) and len(trimmed) > len(REASONING_PREFIX):
        return {"reasoning_text": _clean_reasoning_prefix(trimmed)}
    tagged = extract_thinking_content(text)
    stripped = strip_reasoning_tags(text)
    if not tagged and stripped == text:
        return {"answer_text": text}
    return {
        "reasoning_text": tagged or None,
        "answer_text": stripped or None,
    }

# ▍标签内容提取

def extract_thinking_content(text: str) -> str:
    """提取 <thinking>/<reasoning> 标签内的内容。
    用状态机遍历标签对，收集标签之间的文本。
    改了状态机逻辑会导致嵌套标签或不闭合标签解析错误。
    """
    if not text:
        return ""
    result = ""
    last_index = 0
    in_thinking = False
    for match in _REASONING_TAG_RE.finditer(text):
        idx = match.start()
        if in_thinking:
            result += text[last_index:idx]
        in_thinking = match.group(1) != "/"
        last_index = match.end()
    if in_thinking:
        result += text[last_index:]
    return result.strip()

# ▍标签剥离

def strip_reasoning_tags(text: str) -> str:
    """剥离所有推理标签，返回纯回答文本。"""
    result = _REASONING_OPEN_RE.sub(
        lambda _: "",
        _REASONING_CLOSE_RE.sub("", text),
    )
    if result.strip().startswith(REASONING_PREFIX):
        result = ""
    return result

def _clean_reasoning_prefix(text: str) -> str:
    """清理 Reasoning: 前缀和 markdown 斜体标记。"""
    cleaned = re.sub(r"^Reasoning:\s*", "", text, flags=re.IGNORECASE)
    cleaned = "\n".join(
        line.replace("_", "") if line.startswith("_") and line.endswith("_") else line for line in cleaned.split("\n")
    )
    return cleaned.strip()

# ▍TextState -- 文本累积器

class TextState:
    """Completed text accumulator -- used for text fallback when card is unavailable.
    on_partial 追踪增量文本，on_deliver 追踪最终交付文本（自动剥离推理标签）。
    """

    def __init__(self) -> None:
        self.completed_text = ""
        self.accumulated = ""

    @property
    def display_text(self) -> str:
        if self.accumulated:
            return self.accumulated
        return self.completed_text or ""

    def on_partial(self, text: str) -> None:
        """增量文本回调。"""
        if not text:
            return
        self.accumulated += text

    def on_deliver(self, text: str) -> None:
        """最终交付文本回调。自动剥离推理标签。"""
        text = strip_reasoning_tags(text)
        if self.completed_text:
            self.completed_text += "\n\n" + text
        else:
            self.completed_text = text
        if not self.accumulated:
            self.accumulated = text
