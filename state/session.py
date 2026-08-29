# ================================================================
# lark-hls-v2 state/session.py -- 总导游图（改代码前必读，读完再动手）
# ▍这是什么
# ① 干什么：CardSession -- 单条消息的卡片会话状态机。管理消息从 IDLE 到终态
#    （COMPLETED / ABORTED / TERMINATED）的完整生命周期。
# ② 技术栈：Python 3.11+ / asyncio / threading.Lock / __slots__
# ③ 依赖：state.phase（CardPhase / TERMINAL_PHASES / is_legal_transition）
#    / flush.FlushController / state.linear.UnifiedLinearState
#    / state.text.TextState / state.tooluse.ToolUseTracker / feishu.UnavailableGuard
# ④ 给谁看：维护 lark-hls-v2 的开发者，理解卡片会话的状态转换和生命周期。
# ▍文件从上到下的结构
# CardSession.__init__: 初始化所有子状态（text/tool_use/flush/guard/linear）
# transition(): 状态转换（带合法性检查）
# should_proceed(): 组合 state + guard 检查
# enter_terminal(): 记录终态原因和来源
# _on_guard_terminate(): 消息被撤回时的回调
# ▍修改铁律
# 1. transition() 的合法性检查【不】绕过，改了会导致非法状态转换（如 streaming -> idle）。
# 2. enter_terminal() 只记录第一个终态原因，【不】改成覆盖，改了会丢失真正的终态信息。
# 3. create_epoch 用于 stale-create 检测，【不】删掉，改了会导致过期的创建请求覆盖新卡片。
# 4. _card_ready Event 用于等待卡片创建完成，【不】在不设 set() 的路径上 return，改了会导致死锁。
# ================================================================

"""CardSession -- single-message card session state."""

from __future__ import annotations
import asyncio
import logging
import time
from threading import Lock
from typing import Any

# Phase constants -- single source of truth in state.phase
from .phase import (
    CardPhase,
    TerminalReason,
    TERMINAL_PHASES,
    is_legal_transition,
)

from ..flush import FlushController
from .linear import UnifiedLinearState
from .text import TextState
from .tooluse import ToolUseTracker
from ..feishu import UnavailableGuard

_logger = logging.getLogger("lark_hls_v2")

__all__ = [
    "CardSession",
]


class CardSession:
    """Single-message card session state.
    改了 __slots__ 会导致 pickle / 复制失败；改了 transition 逻辑会导致状态机混乱。
    """

    __slots__ = (
        # Identifiers
        "message_id",
        "anchor_id",
        "chat_id",
        "card_id",
        "card_msg_id",
        "card_trace_id",
        # Phase state
        "state",
        "create_epoch",
        "terminal_reason",
        "terminal_source",
        # Sub-states
        "text",
        "tool_use",
        "flush",
        "guard",
        "linear",
        "unified_state",
        # Card metadata
        "footer",
        "sequence",
        "existing_elements",
        "error_message",
        "card_created_at",
        # Timing
        "created_at",
        # Background review
        "deferred_background_reviews",
        "deferred_background_review_lock",
        "deferred_background_review_closed",
        # Internal flags
        "_loop",
        "_card_ready",
        "_is_continuation",
        "_continuation_reactivation_count",
        "_create_epoch_snap",
        "_creation_stages",
        "_first_answer_time",
        "_first_flush_done",
        "_thinking_hint_upgraded",
        "_pending_flush",
        "_streaming_closed",
        "_streaming_closed_logged",
        "_was_aborted",
        "_answer_streamed",
        "_state_lock",
    )

    def __init__(
        self,
        message_id: str,
        chat_id: str,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self.message_id = message_id
        self.anchor_id: str | None = None
        self.chat_id = chat_id
        self.state: str = CardPhase.IDLE
        self.card_msg_id: str | None = None
        self.card_id: str | None = None
        # card_trace_id: short unique ID for correlating all logs
        # belonging to one card lifecycle. Format: last 6 chars of msg_id.
        self.card_trace_id: str = (message_id or "??????")[-6:]
        self.text = TextState()
        self.tool_use = ToolUseTracker()
        self.flush = FlushController()
        self.footer: dict[str, Any] = {}
        self.sequence = 1
        self._loop = loop
        self.created_at = time.time()
        self.deferred_background_review_closed = False
        self.deferred_background_reviews: list[tuple[str, Any]] = []
        self.deferred_background_review_lock = Lock()
        self._state_lock = Lock()

        # -- State machine enhancements --
        self.create_epoch: int = 0          # Incremented on terminal phase entry
        self._create_epoch_snap: int = 0    # Snapshotted when creation starts
        self.terminal_reason: str = ""      # Why the session ended
        self.terminal_source: str = ""      # Which code path triggered terminal

        self.guard = UnavailableGuard(
            reply_to_message_id=message_id,
            get_card_message_id=lambda: self.card_msg_id,
            on_terminate=self._on_guard_terminate,
        )

        self.linear = False
        self.unified_state: UnifiedLinearState | None = None
        self.existing_elements: set[str] = set()
        self._creation_stages: set[str] = set()
        self.card_created_at: float = 0.0
        self._was_aborted: bool = False
        self.error_message: str = ""
        self._first_flush_done: bool = False
        self._first_answer_time: float = 0.0
        self._thinking_hint_upgraded: bool = False
        self._pending_flush: bool = False
        self._streaming_closed: bool = False
        # v1.2.0 L1: "streaming closed" log dedup -- first time INFO, rest DEBUG
        self._streaming_closed_logged: bool = False
        self._answer_streamed: bool = False
        self._card_ready: asyncio.Event = asyncio.Event()
        self._is_continuation: bool = False
        self._continuation_reactivation_count: int = 0

    def transition(self, to: str, source: str = "", reason: str = "") -> bool:
        """transition(): 契约
        入参：to（目标状态），source（触发来源），reason（终态原因）
        返回：bool（True=转换成功，False=非法转换被拒绝）
        副作用：更新 self.state，终态时调用 enter_terminal()
        谁调用：controller 的各种 on_* 方法
        改动影响：绕过合法性检查会导致状态机混乱
        """
        from_phase = self.state
        if from_phase == to:
            return True  # idempotent

        if not is_legal_transition(from_phase, to):
            _logger.warning(
                "phase transition rejected: %s -> %s (source=%s msg=%s)",
                from_phase, to, source, (self.message_id or "?")[:12],
            )
            return False

        self.state = to
        _logger.info(
            "phase transition: %s -> %s (source=%s msg=%s)",
            from_phase, to, source, (self.message_id or "?")[:12],
        )

        # Track terminal metadata
        if to in TERMINAL_PHASES:
            self.enter_terminal(
                reason=reason or TerminalReason.ERROR,
                source=source,
            )

        # Snapshot epoch when entering CREATING (for stale-create detection)
        if to == CardPhase.CREATING:
            self._create_epoch_snap = self.create_epoch

        return True

    def set_state(
        self,
        to: str,
        *,
        source: str = "",
        reason: str = "",
        terminal: bool = False,
    ) -> bool:
        """Thread-safe state assignment with terminal-state guard.
        Returns False if the session is already in a terminal phase.
        When *terminal* is True, enter_terminal() is called on success.
        """
        with self._state_lock:
            if self.state in TERMINAL_PHASES:
                return False
            self.state = to
            if terminal:
                self.enter_terminal(reason=reason, source=source)
            return True

    def should_proceed(self, source: str = "") -> bool:
        """Combines state + guard checks.
        改了这里的检查逻辑会导致已终态的消息继续处理。
        """
        if self.state in TERMINAL_PHASES:
            return False
        if self.guard.should_skip(source):
            return False
        return True

    @property
    def is_terminal_phase(self) -> bool:
        """Whether the session is in a terminal (absorbing) phase."""
        return self.state in TERMINAL_PHASES

    def is_stale_create(self, epoch: int) -> bool:
        """Check if the given epoch is stale.
        改了比较逻辑会导致过期的创建请求覆盖新卡片。
        """
        return epoch != self.create_epoch

    def enter_terminal(self, reason: str, source: str = "") -> None:
        """Called automatically by transition() when entering a terminal phase.
        只记录第一个终态原因，后续调用被忽略（保留最早的真实原因）。
        """
        if self.terminal_reason:
            return  # Already recorded -- keep the first reason
        self.terminal_reason = reason
        self.terminal_source = source
        self.create_epoch += 1

    def _on_guard_terminate(self) -> None:
        """Callback from UnavailableGuard -- message was deleted/recalled.
        改了这里的终态设置会导致被撤回的消息继续更新卡片。
        """
        self.set_state(
            CardPhase.TERMINATED,
            source="unavailable_guard",
            reason=TerminalReason.UNAVAILABLE,
            terminal=True,
        )
        # Signal readiness so awaiters don't deadlock
        self._card_ready.set()
