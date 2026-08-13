# ================================================================
# lark-hls-v2/config/defaults · 总导游图（改代码前必读，读完再动手）
# ▍这是什么
# ① 干什么：所有默认值的唯一来源。改 footer 布局、flush 间隔、卡片 TTL
#    等只需编辑这一个文件。
# ② 技术栈：纯 Python 常量定义。
# ③ 依赖：无外部依赖。
# ④ 给谁看：config/schema.py（Config 类读取时回退到这里）、plugin/__init__.py
#    （注入默认配置时引用）。
# ▍修改铁律
# 1. 【不】在其他文件硬编码默认值——所有默认必须先在这里定义再引用。
# 2. 修改 FOOTER_FIELDS 会影响所有卡片的底部布局，改前先看截图效果。
# 3. FLUSH_INTERVAL_MS 范围 70-2000，低于 70 会被飞书 API 限流。
# ================================================================

"""Centralized default values for lark-hls-v2 v2.

ALL defaults live here. Changing footer layout, flush intervals,
card TTL, etc. only requires editing this single file.

Synced from v1.7.0 customizations (2026-08-10):
  - Green panel theme, i18n (阿玛特拉斯/正在准备/思考中)
  - 3-row footer with api_calls + history_offset
  - Panel expanded, show_reasoning, flush 180ms, print_step 5
"""

from __future__ import annotations

# ---------------------------------------------------------------------------#
# ▍插件核心开关
# ---------------------------------------------------------------------------#
ENABLED: bool = True
LINEAR: bool = True

# ---------------------------------------------------------------------------#
# ▍面板显示
# ---------------------------------------------------------------------------#
PANEL_EXPANDED: bool = True
STREAMING_PANEL_EXPANDED: bool = True

# ---------------------------------------------------------------------------#
# ▍打印/流式控制 -- 改这里直接影响打字机速度和 API 调用频率
# ---------------------------------------------------------------------------#
# "fast" or "delay"
PRINT_STRATEGY: str = "delay"
# Typewriter characters per render tick (1--10)
PRINT_STEP: int = 5
# stream_element API throttle interval in ms (70--2000)
FLUSH_INTERVAL_MS: float = 180.0
# Card TTL in seconds
CARD_TTL_SEC: int = 600
# Summary truncation max length (characters) for card seal/title
SUMMARY_MAX_LENGTH: int = 120
# Tool/rounding limits
MAX_TOOL_STEPS: int = 20
MAX_REASONING_ROUNDS: int = 20

# ---------------------------------------------------------------------------#
# ▍推理过程显示
# ---------------------------------------------------------------------------#
# Show reasoning process in the panel (thinking rounds)
SHOW_REASONING: bool = True

# ---------------------------------------------------------------------------#
# ▍Footer 布局 -- 改 footer 只改这里，这是唯一控制点
# ---------------------------------------------------------------------------#
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

# ---------------------------------------------------------------------------#
# ▍个性化 -- i18n 文案和面板外观
# ---------------------------------------------------------------------------#

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

# ---------------------------------------------------------------------------#
# ▍卡片级 Header -- 顶部横幅，位于所有 body 元素之上
# ---------------------------------------------------------------------------#
# Header title text
CARD_HEADER_TITLE: str = "阿玛特拉斯"
# Header subtitle text (empty = hidden)
CARD_HEADER_SUBTITLE: str = ""
# Header icon token (Feishu standard_icon token)
CARD_HEADER_ICON: str = ""
# Header background color template
CARD_HEADER_TEMPLATE: str = "orange"
# Dynamic quotes: show anime quotes in header based on scene
DYNAMIC_QUOTES_ENABLED: bool = True
# Minimum interval between quote changes (seconds) to avoid flickering
DYNAMIC_QUOTES_COOLDOWN: float = 2.0

# ---------------------------------------------------------------------------#
# ▍Gateway 网关卡片
# ---------------------------------------------------------------------------#
GATEWAY_CARDS: bool = True

# ---------------------------------------------------------------------------#
# ▍飞书平台 API
# ---------------------------------------------------------------------------#
FEISHU_BASE_URL: str = "https://open.feishu.cn/open-apis"

# ---------------------------------------------------------------------------#
# ▍配置重载缓存
# ---------------------------------------------------------------------------#
RELOAD_CACHE_TTL: float = 60.0
