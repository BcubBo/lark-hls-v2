# ================================================================
# lark-hls-v2 . state/phase.py . 总导游图（改代码前必读，读完再动手）
# ▍这是什么（四问）
# ① 干什么：定义卡片会话的生命周期阶段（CardPhase）和终止原因（TerminalReason），
#   以及合法状态转换表。整个 HLS 的状态机骨架全在这。
# ② 技术栈：纯 Python，无外部依赖。
# ③ 依赖：无内部模块依赖（被所有其他模块依赖的叶子节点）。
# ④ 给谁看：改动卡片生命周期、新增状态、或排查状态机 bug 的人。
# ▍文件从上到下的结构
# CardPhase 类 -> 8 个阶段常量（IDLE -> CREATING -> STREAMING -> COMPLETING -> 终态）
# TerminalReason 类 -> 5 种终止原因
# PHASE_TRANSITIONS 字典 -> 合法转换表（from_phase -> 允许的 to_phase 集合）
# TERMINAL_PHASES / _TERMINAL -> 终态集合（向后兼容别名）
# TERMINAL_REASON_TO_PHASE -> 原因到终态的映射
# is_legal_transition() -> 转换合法性校验函数
# ▍修改铁律
# 1. 新增阶段必须同步更新 PHASE_TRANSITIONS 和 TERMINAL_PHASES（否则 is_legal_transition 会漏判）。
# 2. TERMINAL_REASON_TO_PHASE 的映射必须与 CardPhase 一致（ERROR 映射到 COMPLETED 不是 TERMINATED，
#    因为 error 是 completed 的子类型）。
# 3. 不要删除 FAILED 别名——外部代码可能还在用（向后兼容）。
# ▍外号表
# "终态" -> COMPLETED / CREATION_FAILED / ABORTED / TERMINATED（四个都是终点，不可再转出）
# "合法表" -> PHASE_TRANSITIONS 字典
# "原因表" -> TERMINAL_REASON_TO_PHASE 字典
# ================================================================

from __future__ import annotations

import logging
from typing import Any

__all__ = [
    "CardPhase",
    "TerminalReason",
    "PHASE_TRANSITIONS",
    "TERMINAL_PHASES",
    "TERMINAL_REASON_TO_PHASE",
    "is_legal_transition",
]

_logger = logging.getLogger("lark_hls_v2")

# ▍CardPhase — 卡片生命周期阶段常量
class CardPhase:
    """★ 卡片状态机的 8 个阶段 ★

    状态流转：IDLE -> CREATING -> STREAMING -> COMPLETING -> 终态
    终态有 4 个：COMPLETED / CREATION_FAILED / ABORTED / TERMINATED
    终态不可再转出（PHASE_TRANSITIONS 中终态的 allowed 集合为空）。
    """

    IDLE = "idle"
    CREATING = "creating"
    STREAMING = "streaming"
    COMPLETING = "completing"
    COMPLETED = "completed"
    # CREATION_FAILED replaces the catch-all FAILED for card creation errors.
    # Distinct from TERMINATED so callers can fallthrough to static delivery.
    CREATION_FAILED = "creation_failed"
    ABORTED = "aborted"
    # TERMINATED: message deleted/recalled — stop all updates immediately.
    TERMINATED = "terminated"

    # Backward compatibility: FAILED still exists as an alias for CREATION_FAILED.
    # DEPRECATED: use CREATION_FAILED instead.
    FAILED = "creation_failed"

# ▍TerminalReason — 会话终止原因
class TerminalReason:
    """终止原因常量，配合 enter_terminal() 使用。

    NORMAL -> COMPLETED（正常结束）
    ERROR -> COMPLETED（出错也算完成，不是 TERMINATED）
    ABORT -> ABORTED（用户取消）
    UNAVAILABLE -> TERMINATED（消息被删/撤回）
    CREATION_FAILED -> CREATION_FAILED（卡片创建失败）
    """

    NORMAL = "normal"              # Streaming completed successfully
    ERROR = "error"                # An error occurred during reply generation
    ABORT = "abort"                # Explicitly cancelled by user
    UNAVAILABLE = "unavailable"    # Source message was deleted/recalled
    CREATION_FAILED = "creation_failed"  # Card creation failed

PHASE_TRANSITIONS: dict[str, frozenset[str]] = {
    CardPhase.IDLE: frozenset({CardPhase.CREATING, CardPhase.ABORTED, CardPhase.TERMINATED}),
    CardPhase.CREATING: frozenset({CardPhase.STREAMING, CardPhase.CREATION_FAILED, CardPhase.TERMINATED}),
    CardPhase.STREAMING: frozenset({CardPhase.COMPLETING, CardPhase.ABORTED, CardPhase.TERMINATED}),
    CardPhase.COMPLETING: frozenset({
        CardPhase.COMPLETED,
        CardPhase.CREATION_FAILED,
        CardPhase.ABORTED,
        CardPhase.TERMINATED,
    }),
    CardPhase.COMPLETED: frozenset(),       # terminal
    CardPhase.CREATION_FAILED: frozenset(),  # terminal
    CardPhase.ABORTED: frozenset(),          # terminal
    CardPhase.TERMINATED: frozenset(),       # terminal
}

TERMINAL_PHASES: frozenset[str] = frozenset({
    CardPhase.COMPLETED,
    CardPhase.CREATION_FAILED,
    CardPhase.ABORTED,
    CardPhase.TERMINATED,
})

# Legacy alias — old code references _TERMINAL
_TERMINAL = TERMINAL_PHASES

# ── Terminal reason → phase mapping ──────────────────────────────────

TERMINAL_REASON_TO_PHASE: dict[str, str] = {
    TerminalReason.NORMAL: CardPhase.COMPLETED,
    TerminalReason.ERROR: CardPhase.COMPLETED,  # Error is a subtype of completed
    TerminalReason.ABORT: CardPhase.ABORTED,
    TerminalReason.UNAVAILABLE: CardPhase.TERMINATED,
    TerminalReason.CREATION_FAILED: CardPhase.CREATION_FAILED,
}

def is_legal_transition(from_phase: str, to_phase: str) -> bool:
    """is_legal_transition()：契约
    入参：from_phase（当前阶段），to_phase（目标阶段）
    返回：bool -- 是否合法
    副作用：无
    谁调用：CardSession.state setter（状态赋值时校验）
    改动影响：如果改了 PHASE_TRANSITIONS，这里自动跟着变；
           但如果跳过这个函数直接赋值 state，状态机会乱。
    """
    if from_phase == to_phase:
        return True  # idempotent
    allowed = PHASE_TRANSITIONS.get(from_phase, frozenset())
    return to_phase in allowed
