# =================================================================
# lark-hls-v2 · card/quotes.py · 总导游图（改代码前必读，读完再动手）
# ▍这是什么（固定四问，问完才算完）
# ① 干什么：动态动漫语录系统——根据卡片状态选场景，随机返回语录做面板标题
# ② 技术栈：Python + JSON 数据文件
# ③ 依赖：quotes_data.json（语录数据库，同目录下）
# ④ 给谁看：elements.py 的 build_panel_header()（面板标题需要语录）
# ▍文件从上到下的结构
# _SCENE_MAP：场景名 → 语录分类的映射
# QuoteManager 类：
#   _load_quotes() / _maybe_reload()：加载/热重载语录
#   get_quote()：随机取语录（同源冷却机制）
#   detect_scene()：从卡片状态推断当前场景
#   get_mood()：获取面板标题的短心情表达（2-4 字）
#   get_seal_ending()：封卡结束语
# ▍修改铁律（都是血泪教训）
# 1. quotes_data.json 必须存在且格式正确——文件缺失时 get_quote() 返回空串，
#    不会崩但面板标题会缺语录。改 JSON 结构时同步改 _load_quotes()。
# 2. SOURCE_COOLDOWN=5 防止同一动漫连续出现——如果改小了，用户会看到重复感。
# 3. detect_scene() 的优先级是固定逻辑（seal > defeat > victory > ...），
#    改优先级会改变面板标题的语境匹配。
# ▍外号表
# "语录冷却" → SOURCE_COOLDOWN，同一动漫源间隔 N 次才允许重复
# "场景" → detect_scene() 的返回值，决定从哪个分类取语录
# =================================================================
"""Dynamic quote system for card headers.

根据卡片状态（思考中、工具调用、出错、完成等）选场景，从 quotes_data.json
随机取一条动漫语录做面板标题，让每次交互都有新鲜感。
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

# ▍场景映射表——内部场景名 → quotes_data.json 里的分类名
# 为什么不用同名：内部用 "loading" 但语录分类叫 "eating"（吃东西的梗）
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
    """管理动漫语录——加载、缓存、随机选取、同源冷却。

    核心机制：
    1. 从 quotes_data.json 加载语录到内存
    2. 每 5 分钟热重载一次（文件变化不重启也能生效）
    3. 同一动漫源（source）间隔 SOURCE_COOLDOWN 次选取才能重复出现
    4. 场景检测优先级：seal > defeat > victory > battle > thinking > greeting > loading > casual
    """

    # ▍同源冷却——同一动漫的语录至少间隔 5 次选取才允许重复
    SOURCE_COOLDOWN: int = 5

    def __init__(self) -> None:
        self._quotes: dict[str, list[dict[str, str]]] = {}
        self._used: dict[str, list[int]] = {}  # scene -> list of used indices
        self._recent_sources: dict[str, deque[str]] = {}  # scene -> recent source names
        self._last_load_time: float = 0
        self._load_interval: float = 300  # Reload every 5 minutes
        self._load_quotes()

    def _load_quotes(self) -> None:
        """从 JSON 文件加载语录到内存。

        文件缺失时静默跳过（日志 warning），不会抛异常。
        改了会怎样：如果 JSON 格式变了（scenes key 改名），所有语录会变空。
        """
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
        """热重载检查——距上次加载超过 5 分钟才重新读文件。"""
        now = time.monotonic()
        if now - self._last_load_time > self._load_interval:
            self._load_quotes()

    def get_quote(self, scene: str, *, short: bool = False) -> str:
        """随机取一条语录（带同源冷却）。

        入参：scene（场景名）、short（True 则只返回文本，不含出处）
        返回：语录字符串，无可用语录时返回空串
        副作用：更新 _used 和 _recent_sources 状态

        同源冷却机制：
        - _recent_sources 记录最近 SOURCE_COOLDOWN 次选取的动漫源
        - 优先选不在最近源列表里的语录
        - 如果所有语录都在冷却中，放宽限制允许重复

        改动影响：SOURCE_COOLDOWN 改小会让同一动漫更频繁出现
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
        """从卡片状态推断当前场景。

        优先级（从高到低，遇先返回）：
        seal（封卡） → defeat（出错） → victory（完成+有工具） → battle（工具运行中）
        → thinking（推理中） → greeting（新会话） → loading（等待中） → casual（默认）

        谁调用：elements.py 的 build_panel_header()
        改动影响：改优先级会改变面板语录的语境匹配
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
        """返回当前加载的语录分类列表。"""
        return list(self._quotes.keys())

    @property
    def total_quotes(self) -> int:
        """返回所有分类的语录总数。"""
        return sum(len(v) for v in self._quotes.values())

    def get_mood(self, scene: str) -> str:
        """获取面板标题的短心情表达（2-4 字）。

        返回如 "冲啊"、"搞定" 这样的短语，自然地放在 "· N 轮 · 用了 M 个工具" 前面。
        特殊：seal 场景走 get_seal_ending() 专用逻辑。

        改动影响：mood_expressions 不存在时返回空串，面板标题会少一个前缀。
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
        """获取随机封卡结束语。

        返回如 "收工"、"溜了溜了" 这样的短语。
        数据来源：quotes_data.json 的 seal_endings 列表。
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
