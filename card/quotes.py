"""Dynamic quote system for card headers.

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
        self._used: dict[str, list[int]] = {}  # scene -> list of used indices
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
        """Get a random quote for the given scene with source-aware spacing.

        Guarantees that quotes from the same anime source won't appear
        within SOURCE_COOLDOWN selections of each other for the same scene.

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

        # Non-repeating random selection
        used = self._used.setdefault(category, [])
        recent = self._recent_sources.setdefault(
            category, deque(maxlen=self.SOURCE_COOLDOWN)
        )

        available = [i for i in range(len(quotes)) if i not in used]
        if not available:
            used.clear()
            available = list(range(len(quotes)))

        # Filter: prefer quotes whose source is NOT in recent sources
        fresh = [
            i for i in available
            if quotes[i].get("source", "") not in recent
        ]
        # Fall back to all available if constraint is too tight
        pool = fresh if fresh else available

        idx = random.choice(pool)
        used.append(idx)

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

        # Load mood_expressions from JSON (separate key from scenes)
        moods: dict[str, list[str]] = {}
        try:
            with open(_QUOTES_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            moods = data.get("mood_expressions", {})
        except Exception:
            pass

        category = _SCENE_MAP.get(scene, "casual")
        expressions = moods.get(category, [])
        if not expressions:
            expressions = moods.get("casual", [])
        if not expressions:
            return ""

        return random.choice(expressions)

    def get_seal_ending(self) -> str:
        """Get a random seal ending quote.

        Returns a quirky/funny ending like "收工" or "溜了溜了".
        """
        self._maybe_reload()

        endings: list[str] = []
        try:
            with open(_QUOTES_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            endings = data.get("seal_endings", [])
        except Exception:
            pass

        if not endings:
            return ""

        return random.choice(endings)
