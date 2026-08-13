# ================================================================
# lark-hls-v2 state/__init__.py -- 总导游图（改代码前必读，读完再动手）
# ▍这是什么
# ① 干什么：state 子包入口，统一导出所有状态类（CardPhase / TextState /
#    ToolUseTracker / UnifiedLinearState / CardSession）。
# ② 技术栈：Python 3.11+
# ③ 依赖：state.phase / state.text / state.tooluse / state.linear / state.session
# ④ 给谁看：维护 lark-hls-v2 的开发者，了解 state 模块的公开 API。
# ▍文件从上到下的结构
# 从各子模块 re-export 类和函数
# __all__ 声明公开 API
# ▍修改铁律
# 1. 新增状态类时必须同时在 __all__ 里声明，【不】只 import 不导出，改了会导致下游 import 失败。
# ================================================================

"""State modules -- card session lifecycle, text accumulation, tool tracking, and linear state."""

from .phase import (
    CardPhase,
    TerminalReason,
    TERMINAL_PHASES,
    TERMINAL_REASON_TO_PHASE,
    PHASE_TRANSITIONS,
    is_legal_transition,
)
from .text import (
    TextState,
    REASONING_PREFIX,
    split_reasoning_text,
    extract_thinking_content,
    strip_reasoning_tags,
)
from .tooluse import (
    ToolStep,
    ToolSession,
    ToolUseTracker,
    redact_inline_secrets,
)
from .linear import (
    ReasoningRound,
    UnifiedLinearState,
)
from .session import CardSession

__all__ = [
    # phase
    "CardPhase",
    "TerminalReason",
    "TERMINAL_PHASES",
    "TERMINAL_REASON_TO_PHASE",
    "PHASE_TRANSITIONS",
    "is_legal_transition",
    # text
    "TextState",
    "REASONING_PREFIX",
    "split_reasoning_text",
    "extract_thinking_content",
    "strip_reasoning_tags",
    # tooluse
    "ToolStep",
    "ToolSession",
    "ToolUseTracker",
    "redact_inline_secrets",
    # linear
    "ReasoningRound",
    "UnifiedLinearState",
    # session
    "CardSession",
]
