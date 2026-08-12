"""State modules — card session lifecycle, text accumulation, tool tracking, and linear state."""

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
