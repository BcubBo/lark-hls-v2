# ================================================================
# lark-hls-v2 state/linear.py -- 总导游图（改代码前必读，读完再动手）
# ▍这是什么
# ① 干什么：线性模式的统一状态管理。ReasoningRound 追踪单轮推理，
#    UnifiedLinearState 管理 reasoning + tool + answer 的统一面板状态。
# ② 技术栈：Python 3.11+ / __slots__ 优化
# ③ 依赖：无外部依赖
# ④ 给谁看：维护 lark-hls-v2 的开发者，理解线性模式的状态机。
# ▍文件从上到下的结构
# ReasoningRound: 单轮推理的数据类
# UnifiedLinearState: 统一面板状态
#   on_reasoning_delta(): 推理 token 增量
#   on_answer_delta(): 回答 token 增量
#   on_tool_event(): 工具调用事件
#   _finalize_current_reasoning(): 结束当前推理轮
#   has_dirty / panel_events 等属性
# ▍修改铁律
# 1. on_reasoning_delta 的全前缀去重逻辑【不】简化为只比较前几个字符，
#    改了会导致 post-stream 重复推理文本出现在卡片上。
# 2. on_answer_delta 会自动 finalize 推理轮，【不】删掉这个调用，改了会导致推理和回答重叠显示。
# 3. panel_events 是时序列表，【不】改成 set，改了会导致工具和推理的显示顺序错乱。
# ================================================================

"""Unified linear state -- single-panel reasoning+tool tracking for linear mode."""

from __future__ import annotations
import time

__all__ = [
    "ReasoningRound",
    "UnifiedLinearState",
]

# ▍ReasoningRound -- 单轮推理数据

class ReasoningRound:
    """One round of AI reasoning / thinking."""

    __slots__ = ("index", "text", "elapsed_ms", "start_time", "finalized")

    def __init__(self, index: int, text: str = "", start_time: float = 0.0) -> None:
        self.index = index
        self.text = text
        self.elapsed_ms: float = 0.0
        self.start_time = start_time
        self.finalized: bool = False

# ▍UnifiedLinearState -- 统一面板状态

class UnifiedLinearState:
    """Unified panel linear state -- all reasoning+tool in 1 panel, 1 answer element.
    改了 dirty flag 逻辑会导致卡片刷新时机错误（过早或过晚）。
    """

    __slots__ = (
        "reasoning_rounds",
        "_current_reasoning",
        "_reasoning_start",
        "tool_steps_dirty",
        "answer_text",
        "panel_dirty",
        "answer_dirty",
        "panel_visible",
        "bg_review_messages",
        "_panel_events",
        "_tool_count",
    )

    def __init__(self) -> None:
        # Reasoning tracking
        self.reasoning_rounds: list[ReasoningRound] = []
        self._current_reasoning: str = ""
        self._reasoning_start: float = 0.0

        # Tool tracking -- dirty flag only; actual steps come from ToolUseTracker
        self.tool_steps_dirty: bool = False

        # Answer tracking
        self.answer_text: str = ""

        # Dirty flags
        self.panel_dirty: bool = False
        self.answer_dirty: bool = False

        # Panel visibility -- set to True once the first reasoning or tool
        # event arrives so the renderer knows to create the element.
        self.panel_visible: bool = False

        # Background review
        self.bg_review_messages: list[str] = []

        self._panel_events: list[tuple[str, int]] = []
        self._tool_count: int = 0

    def on_reasoning_delta(self, text: str) -> None:
        """on_reasoning_delta(): 契约
        入参：text（推理 token 文本片段）
        返回：无
        副作用：累加 _current_reasoning，设 panel_dirty / panel_visible
        谁调用：controller.on_reasoning()
        改动影响：去重逻辑改了会导致 post-stream 重复文本
        """
        import logging as _logging
        _diag_logger = _logging.getLogger("lark_hls_v2")
        _diag_logger.debug(
            "HLS: on_reasoning_delta text=%r current_len=%d rounds=%d",
            text[:40] if text else "",
            len(self._current_reasoning),
            len(self.reasoning_rounds),
        )
        # v1.3.0 bug fix: 全前缀比较去重。
        # 之前的实现只比较前几个字符，导致 post-stream 重复文本漏过。
        if (
            self._current_reasoning
            and len(text) >= len(self._current_reasoning)
            and text[:len(self._current_reasoning)] == self._current_reasoning
        ):
            _diag_logger.debug(
                "HLS: on_reasoning_delta skips post-stream duplicate "
                "text_len=%d current_len=%d",
                len(text), len(self._current_reasoning),
            )
            return
        if not self._current_reasoning:
            # First token of a new reasoning round
            self._reasoning_start = time.time()
        self._current_reasoning += text
        self.panel_dirty = True
        self.panel_visible = True

    def on_answer_delta(self, text: str) -> None:
        """Answer text increment. Finalizes any in-progress reasoning first.
        改了 finalize 顺序会导致推理和回答重叠显示。
        """
        self._finalize_current_reasoning()
        self.answer_text += text
        self.answer_dirty = True

    def on_tool_event(self, is_new_tool: bool = True) -> None:
        """Tool call event. Finalizes any in-progress reasoning first."""
        self._finalize_current_reasoning()
        if is_new_tool:
            self._panel_events.append(("tool", self._tool_count))
            self._tool_count += 1
        self.tool_steps_dirty = True
        self.panel_dirty = True
        self.panel_visible = True

    def on_background_review(self, message: str) -> None:
        """Background review message (e.g. quality check, memory update)."""
        self.bg_review_messages.append(message)

    def _finalize_current_reasoning(self) -> None:
        """Finalize the current reasoning round, moving it to reasoning_rounds."""
        if not self._current_reasoning:
            return
        elapsed = (time.time() - self._reasoning_start) * 1000 if self._reasoning_start else 0.0
        round_ = ReasoningRound(
            index=len(self.reasoning_rounds) + 1,
            text=self._current_reasoning,
            start_time=self._reasoning_start,
        )
        round_.elapsed_ms = elapsed
        round_.finalized = True
        self.reasoning_rounds.append(round_)
        self._panel_events.append(("reasoning", len(self.reasoning_rounds) - 1))
        self._current_reasoning = ""
        self._reasoning_start = 0.0

    def finalize(self) -> None:
        """Finalize any in-progress reasoning (called at message completion)."""
        self._finalize_current_reasoning()

    @property
    def current_reasoning_text(self) -> str:
        """Get the in-progress reasoning text (for streaming display)."""
        return self._current_reasoning

    @property
    def has_current_reasoning(self) -> bool:
        """Whether there is an in-progress reasoning round."""
        return bool(self._current_reasoning)

    @property
    def total_reasoning_count(self) -> int:
        """Total reasoning rounds (finalized + in-progress)."""
        count = len(self.reasoning_rounds)
        if self._current_reasoning:
            count += 1
        return count

    @property
    def total_reasoning_elapsed_ms(self) -> float:
        """Total reasoning elapsed time across all rounds (milliseconds)."""
        total = sum(r.elapsed_ms for r in self.reasoning_rounds)
        if self._reasoning_start:
            total += (time.time() - self._reasoning_start) * 1000
        return total

    @property
    def panel_events(self) -> list[tuple[str, int]]:
        """Chronological timeline of panel events.
        【不】改成 set，改了会导致工具和推理的显示顺序错乱。
        """
        return self._panel_events

    @property
    def has_dirty(self) -> bool:
        """Whether any dirty data needs flushing to the card."""
        return (
            self.panel_dirty
            or self.answer_dirty
            or bool(self.bg_review_messages)
        )
