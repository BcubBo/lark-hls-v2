# config/defaults.py — 集中式默认值（单一数据源）
# 所有配置项的默认值都在这里。Config 类从 config.yaml 读取，
# 读不到就 fallback 到这里的值。
# 新增配置项：先在这里加默认值，再在 schema.py 加 @property。
# 原 docstring: Centralized default values for lark-hls-v2 v2.
"""
ALL defaults live here. Changing footer layout, flush intervals,
card TTL, etc. only requires editing this single file.

Synced from v1.7.0 customizations (2026-08-10):
  - Green panel theme, i18n (阿玛特拉斯/正在准备/思考中)
  - 3-row footer with api_calls + history_offset
  - Panel expanded, show_reasoning, flush 180ms, print_step 5
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
PANEL_EXPANDED: bool = True
STREAMING_PANEL_EXPANDED: bool = True

# ---------------------------------------------------------------------------
# Print / streaming
# ---------------------------------------------------------------------------
# "fast" or "delay"
PRINT_STRATEGY: str = "delay"
# Typewriter characters per render tick (1–10)
PRINT_STEP: int = 5
# stream_element API throttle interval in ms (70–2000)
FLUSH_INTERVAL_MS: float = 180.0
# Card TTL in seconds
CARD_TTL_SEC: int = 600
# Summary truncation max length (characters) for card seal/title
SUMMARY_MAX_LENGTH: int = 120
# Tool/rounding limits
MAX_TOOL_STEPS: int = 20
MAX_REASONING_ROUNDS: int = 20

# ---------------------------------------------------------------------------
# Reasoning
# ---------------------------------------------------------------------------
# Show reasoning process in the panel (thinking rounds)
SHOW_REASONING: bool = True

# ---------------------------------------------------------------------------
# Footer — the ONE place to change footer layout
# ---------------------------------------------------------------------------
# 3-row layout:
#   Row 1: status, elapsed, model, api_calls
#   Row 2: tokens, context, cache, history_offset
#   Row 3: cost, compression_exhausted
FOOTER_FIELDS: list[list[str]] = [
    ["status", "elapsed", "model", "api_calls"],
    ["tokens", "context", "cache", "history_offset"],
    ["cost", "compression_exhausted"],
]
# Show field labels (e.g. "Status: running" vs just "running")
FOOTER_SHOW_LABEL: bool = True

# ---------------------------------------------------------------------------
# Personalization
# ---------------------------------------------------------------------------

# Custom panel title (shown in collapsible panel header)
PANEL_TITLE: str = "阿玛特拉斯"

# Custom loading hint text (first card, before first token)
LOADING_TEXT: str = "正在准备..."

# Custom thinking hint text (when thinking starts)
THINKING_TEXT: str = "思考中..."

# Auto-collapse panel when child count exceeds this threshold (0 = never)
AUTO_COLLAPSE_THRESHOLD: int = 10

# Typing speed curve: "flat" (constant) or "answer_fast" (answer faster than thinking)
SPEED_CURVE: str = "flat"

# Answer-only throttle interval in ms (faster streaming for answers)
ANSWER_FAST_STREAM_MS: float = 150.0

# Panel border color: "grey", "blue", "green", "orange", "red"
PANEL_BORDER_COLOR: str = "green"

# Panel header text color: "grey", "blue", "green", "orange", "red"
PANEL_HEADER_COLOR: str = "green"

# ---------------------------------------------------------------------------
# Card-level header (top-level banner above all body elements)
# ---------------------------------------------------------------------------
# Header title text
CARD_HEADER_TITLE: str = "阿玛特拉斯"
# Header subtitle text (empty = hidden)
CARD_HEADER_SUBTITLE: str = ""
# Header icon token (Feishu standard_icon token)
CARD_HEADER_ICON: str = ""
# Header background color template: "blue", "green", "orange", "red", "purple", "indigo", "turquoise", "yellow", "grey", "violet", "wathet", "carmine"
CARD_HEADER_TEMPLATE: str = "orange"
# Dynamic quotes: show anime quotes in header based on scene
DYNAMIC_QUOTES_ENABLED: bool = True
# Minimum interval between quote changes (seconds) to avoid flickering
DYNAMIC_QUOTES_COOLDOWN: float = 2.0

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
