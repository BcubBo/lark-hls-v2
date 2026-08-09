"""Centralized default values for hermes-lark-streaming v2.

ALL defaults live here. Changing footer layout, flush intervals,
card TTL, etc. only requires editing this single file.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Plugin core
# ---------------------------------------------------------------------------
ENABLED: bool = True
LINEAR: bool = True

# ---------------------------------------------------------------------------
# Panel
# ---------------------------------------------------------------------------
PANEL_EXPANDED: bool = False
STREAMING_PANEL_EXPANDED: bool = False

# ---------------------------------------------------------------------------
# Print / streaming
# ---------------------------------------------------------------------------
# "fast" or "delay"
PRINT_STRATEGY: str = "delay"
# Typewriter characters per render tick (1–10)
PRINT_STEP: int = 4
# stream_element API throttle interval in ms (70–2000)
FLUSH_INTERVAL_MS: float = 200.0
# Card TTL in seconds
CARD_TTL_SEC: int = 600
# Tool/rounding limits
MAX_TOOL_STEPS: int = 20
MAX_REASONING_ROUNDS: int = 20

# ---------------------------------------------------------------------------
# Footer — the ONE place to change footer layout
# ---------------------------------------------------------------------------
# 3-row layout:
#   Row 1: status, elapsed
#   Row 2: model
#   Row 3: cost, compression_exhausted
FOOTER_FIELDS: list[list[str]] = [
    ["status", "elapsed"],
    ["model"],
    ["cost", "compression_exhausted"],
]
# Show field labels (e.g. "Status: running" vs just "running")
FOOTER_SHOW_LABEL: bool = True

# ---------------------------------------------------------------------------
# Personalization
# ---------------------------------------------------------------------------

# Custom panel title (shown in collapsible panel header)
PANEL_TITLE: str = "agent loop"

# Custom loading hint text (first card, before first token)
LOADING_TEXT: str = "正在加载上下文..."

# Custom thinking hint text (when thinking starts)
THINKING_TEXT: str = "正在思考..."

# Auto-collapse panel when child count exceeds this threshold (0 = never)
AUTO_COLLAPSE_THRESHOLD: int = 0

# Typing speed curve: "flat" (constant) or "answer_fast" (answer faster than thinking)
SPEED_CURVE: str = "flat"

# Answer-only throttle interval in ms (faster streaming for answers)
ANSWER_FAST_STREAM_MS: float = 150.0

# Panel border color: "grey", "blue", "green", "orange", "red"
PANEL_BORDER_COLOR: str = "grey"

# Panel header text color: "grey", "blue", "green", "orange", "red"
PANEL_HEADER_COLOR: str = "grey" 

# ---------------------------------------------------------------------------
# Gateway
# ---------------------------------------------------------------------------
GATEWAY_CARDS: bool = True

# ---------------------------------------------------------------------------
# Feishu platform
# ---------------------------------------------------------------------------
FEISHU_BASE_URL: str = "https://open.feishu.cn/open-apis"

# ---------------------------------------------------------------------------
# Config reload cache TTL (seconds)
# ---------------------------------------------------------------------------
RELOAD_CACHE_TTL: float = 60.0
