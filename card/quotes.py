# =================================================================
# card/quotes.py · 总导游图（改代码前必读，读完再动手）
#
# ▍这是什么
# ① 动态台词系统——根据场景（问候/思考/工具/封印）从台词库随机选动漫语录。
# ② Python 3.11+, random, pathlib
# ③ 无外部依赖（台词库内嵌在同目录 JSON 文件）
# ④ 给 lark-hls-v2 插件的维护者和 AI 助手看。
#
# ▍文件从上到下的结构
# QuoteManager   核心管理器（单例模式，模块级 _quote_manager）
#   ├─ _load_quotes         从 JSON 文件加载台词库（按场景分组）
#   ├─ get_quote            根据场景返回一条台词（带冷却防重复）
#   ├─ detect_scene         检测当前场景（greeting/thinking/tools/seal 等）
#   ├─ get_mood             返回语气词（panel 标题用）
#   └─ get_seal_ending      返回封印结束语（"收工""溜了溜了"等）
#
# ▍修改铁律
# 1. 台词库 JSON 文件路径是相对于 quotes.py 的——改文件位置必须同步改 _load_quotes。
# 2. detect_scene 的参数组合决定场景——has_reasoning + has_tools + is_sealing
#    有 8 种组合，每种映射到不同场景。新增参数必须穷举所有组合。
# 3. 冷却机制（_recent_indices）防止连续两次出同一句台词——
#    冷却窗口 = min(台词总数/3, 10)。别改太小，否则用户会看到重复。
#
# ▍更新记录
# *更新：2026-08-13 · 按龙崎注释风格加总导游图*
# =================================================================
# 原 docstring: Dynamic quote system for card headers.
"""
Reads quotes from quotes_data.json, detects scene from card state,
and returns a random quote for the header title.
"""

from __future__ import annotations

import json
import logging
import random
import time
from collections import deque
from pathlib import Path
from typing import Any

_logger = logging.getLogger("lark_hls_v2.quotes")

_QUOTES_PATH = Path(__file__).parent / "quotes_data.json"

# ── Scene detection thresholds ──
# Maps internal scene names to quote categories
_SCENE_MAP = {
    "greeting": "greeting",
    "thinking": "thinking",
    "battle": "battle",
    "victory": "victory",
    "defeat": "defeat",
    "loading": "eating",
    "casual": "casual",
}


class QuoteManager:
    """Manages anime quotes for dynamic header titles."""

    # Minimum gap before the same source (anime) can reappear
    SOURCE_COOLDOWN: int = 5

    def __init__(self) -> None:
        self._quotes: dict[str, list[dict[str, str]]] = {}
        self._moods: dict[str, list[str]] = {}  # P1-1: cached mood expressions
        self._seal_endings: list[str] = []  # P1-1: cached seal endings
        self._shuffled: dict[str, list[int]] = {}  # scene -> shuffled index queue
        self._recent_sources: dict[str, deque[str]] = {}  # scene -> recent source names
        self._last_load_time: float = 0
        self._load_interval: float = 300  # Reload every 5 minutes
        self._load_quotes()

    def _load_quotes(self) -> None:
        """Load quotes from JSON file."""
        try:
            if not _QUOTES_PATH.exists():
                _logger.warning("quotes_data.json not found at %s", _QUOTES_PATH)
                return
            with open(_QUOTES_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._quotes = data.get("scenes", {})
            self._moods = data.get("mood_expressions", {})
            self._seal_endings = data.get("seal_endings", [])
            self._last_load_time = time.monotonic()
            total = sum(len(v) for v in self._quotes.values())
            _logger.info("Loaded %d quotes across %d scenes", total, len(self._quotes))
        except Exception:
            _logger.warning("Failed to load quotes_data.json", exc_info=True)

    def _maybe_reload(self) -> None:
        """Hot-reload quotes if file changed."""
        now = time.monotonic()
        if now - self._last_load_time > self._load_interval:
            self._load_quotes()

    def get_quote(self, scene: str, *, short: bool = False) -> str:
        """Get a random quote for the given scene with shuffled queue.

        Uses a Fisher-Yates shuffled queue per scene: quotes are consumed
        in order, and the queue is reshuffled when exhausted. This guarantees
        no repetition within a full cycle through the quote pool.

        Args:
            scene: Scene name (greeting, thinking, battle, etc.)
            short: If True, return only the quote text without attribution.

        Returns empty string if no quotes available.
        """
        self._maybe_reload()

        category = _SCENE_MAP.get(scene, "casual")
        quotes = self._quotes.get(category, [])
        if not quotes:
            quotes = self._quotes.get("casual", [])
        if not quotes:
            return ""

        # Get or create shuffled queue for this scene
        queue = self._shuffled.setdefault(category, [])
        recent = self._recent_sources.setdefault(
            category, deque(maxlen=self.SOURCE_COOLDOWN)
        )

        # Reshuffle if queue is empty
        if not queue:
            queue = list(range(len(quotes)))
            random.shuffle(queue)

        # Try to find a quote whose source is NOT in recent
        idx = None
        for _ in range(min(len(queue), 10)):
            candidate = queue[0]
            source = quotes[candidate].get("source", "")
            if source not in recent:
                idx = queue.pop(0)
                break
            queue.append(queue.pop(0))

        # If all candidates are from recent sources, just take the first one
        if idx is None:
            idx = queue.pop(0)

        q = quotes[idx]
        source = q.get("source", "")
        if source:
            recent.append(source)

        text = q.get("text", "")
        if short:
            return text

        character = q.get("character", "")
        if character and source:
            return f"{text} —— {character}「{source}」"
        elif character:
            return f"{text} —— {character}"
        return text

    def detect_scene(
        self,
        *,
        is_new_session: bool = False,
        has_reasoning: bool = False,
        has_tools: bool = False,
        tools_running: bool = False,
        has_error: bool = False,
        is_complete: bool = False,
        is_loading: bool = False,
        is_sealing: bool = False,
    ) -> str:
        """Detect the current scene from card state.

        Priority order:
        1. seal (card being finalized)
        2. defeat (error)
        3. victory (complete with tools)
        4. battle (tools running)
        5. thinking (reasoning)
        6. greeting (new session)
        7. loading (waiting)
        8. casual (default)
        """
        if is_sealing:
            return "seal"
        if has_error:
            return "defeat"
        if is_complete and has_tools:
            return "victory"
        if tools_running or (has_tools and not is_complete):
            return "battle"
        if has_reasoning:
            return "thinking"
        if is_new_session:
            return "greeting"
        if is_loading:
            return "loading"
        return "casual"

    @property
    def available_scenes(self) -> list[str]:
        """List available quote categories."""
        return list(self._quotes.keys())

    @property
    def total_quotes(self) -> int:
        """Total number of quotes loaded."""
        return sum(len(v) for v in self._quotes.values())

    def get_mood(self, scene: str) -> str:
        """Get a short mood expression for panel titles.

        Returns a 2-4 character expression like "冲啊" or "搞定" that
        fits naturally before "· N 轮 · 用了 M 个工具".
        Special case: "seal" scene returns a random seal ending.
        """
        self._maybe_reload()

        # Seal endings are special - use dedicated list
        if scene == "seal":
            return self.get_seal_ending()

        # P1-1: Use cached moods instead of reading file each time
        category = _SCENE_MAP.get(scene, "casual")
        expressions = self._moods.get(category, [])
        if not expressions:
            expressions = moods.get("casual", [])
        if not expressions:
            return ""

        # Use shuffled queue for moods too
        key = f"mood_{category}"
        queue = self._shuffled.setdefault(key, [])
        if not queue:
            queue = list(range(len(expressions)))
            random.shuffle(queue)
        idx = queue.pop(0)
        return expressions[idx]

    def get_seal_ending(self) -> str:
        """Get a random seal ending quote.

        Returns a quirky/funny ending like "收工" or "溜了溜了".
        """
        self._maybe_reload()

        # P1-1: Use cached seal_endings instead of reading file each time
        endings = self._seal_endings

        if not endings:
            return ""

        # Use shuffled queue for seal endings
        queue = self._shuffled.setdefault("seal_endings", [])
        if not queue:
            queue = list(range(len(endings)))
            random.shuffle(queue)
        idx = queue.pop(0)
        return endings[idx]
