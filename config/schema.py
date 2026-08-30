# =================================================================
# lark-hls-v2 · config/schema.py 总导游图（改代码前必读，读完再动手）
# ▍这是什么
# ① 干什么：从 Hermes config.yaml 读取 lark_hls_v2 配置节，提供类型安全的属性访问。
#    所有 fallback 值来自 config.defaults —— 唯一的默认值真相源。
# ② 技术栈：Python 3.11, yaml, threading.Lock
# ③ 依赖：config.defaults（默认值模块），Hermes config.yaml（磁盘配置）
# ④ 给谁看：修改配置项、新增配置字段的开发者。
# ▍文件从上到下的结构
# Helpers 区        — _to_bool/_to_int/_to_float 类型转换，_get_hermes_config_path 路径定位
# Config 单例类     — lazy-read 单例，__new__ 控制唯一实例，__init__ 只跑一次
#   属性(property)  — 每个配置项一个 @property，读 YAML → 转类型 → clamp → 返回
#   内部方法        — _plugin_sec / _platform_cfg / _load / _reload_cached / _read_yaml
# ▍修改铁律
# 1. 新增配置项必须同时在 config/defaults.py 写默认值，否则 fallback 行为不可预测。
# 2. _to_int/_to_float 的 clamp 范围是硬编码的（如 max(1, 100)），改范围前先确认上游不会炸。
# 3. _reload_cached 有 TTL 缓存（RELOAD_CACHE_TTL），runtime-mutable 属性走这条路；
#    非 runtime 属性走 _load（只读一次）。别混用，否则热更新不生效。
# 4. _platform_cfg 优先读环境变量（FEISHU_APP_ID / LARK_APP_ID），再读 config.yaml。
#    改优先级会影响部署方式，先和运维确认。
# ▍外号表
# "plugin_sec"   → _plugin_sec()（从 raw YAML 取 lark_hls_v2 节）
# "reload_cache" → _reload_cached()（TTL 缓存的磁盘重读，给 runtime-mutable 属性用）
# =================================================================

from __future__ import annotations

import logging
import math
import os
import threading
import time
from pathlib import Path
from typing import Any

import yaml

from . import defaults

_logger = logging.getLogger("lark_hls_v2")

# =================================================================
# Helpers — 类型安全转换 + 路径定位
# 改了会怎样：_to_bool 逻辑变了，所有 bool 配置项的行为都会跟着变
# =================================================================

def _get_hermes_config_path() -> Path:
    """Hermes config.yaml location (honors HERMES_HOME)."""
    return Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes"))) / "config.yaml"


def _to_bool(val: Any, default: bool = False) -> bool:
    """_to_bool(): 宽松布尔转换，接受 bool/str/int/float，其余返回 default."""
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.lower() in ("true", "1", "yes", "on")
    if isinstance(val, (int, float)):
        return val != 0
    return default


def _to_int(val: Any, default: int) -> int:
    """_to_int(): 宽松整数转换，bool 也转（True→1），溢出/格式错返回 default."""
    if isinstance(val, bool):
        return int(val)
    if isinstance(val, int):
        return val
    if isinstance(val, float):
        try:
            return int(val)
        except (OverflowError, ValueError):
            return default
    if isinstance(val, str):
        try:
            return int(val)
        except ValueError:
            return default
    return default


def _to_float(val: Any, default: float) -> float:
    """Safe float conversion — rejects nan/inf."""
    if isinstance(val, bool):
        return float(val)
    if isinstance(val, (int, float)):
        result = float(val)
        if math.isnan(result) or math.isinf(result):
            return default
        return result
    if isinstance(val, str):
        try:
            result = float(val)
        except ValueError:
            return default
        if math.isnan(result) or math.isinf(result):
            return default
        return result
    return default


# =================================================================
# Config 单例 — lazy-read，线程安全，reload() 清缓存重新读磁盘
# 为什么用单例：整个插件只需要一份配置，多实例会导致缓存不一致
# =================================================================

class Config:
    """Plugin configuration. Lazy-reading singleton.

    Call ``Config.reload()`` to force a re-read (e.g. from ``/aowen config reload``).
    """

    _instance: Config | None = None

    def __new__(cls) -> Config:
        """__new__(): 单例控制 —— 只创建一次实例，后续调用返回同一个对象."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if getattr(self, "_initialized", False):
            return
        self._raw: dict[str, Any] | None = None
        self._reload_cache: dict[str, Any] | None = None
        self._reload_cache_at: float = 0.0
        self._lock = threading.Lock()
        self._initialized = True

    # -- Lifecycle -----------------------------------------------------------

    def reload(self) -> None:
        """Force reload from disk."""
        with self._lock:
            self._raw = None
            self._reload_cache = None
            self._reload_cache_at = 0.0
        _logger.info("HLS v2: config reload triggered — caches cleared")

    # -- Plugin section properties -------------------------------------------
    # 这些属性从 lark_hls_v2 配置节读取，每个都有 defaults fallback
    # 改了会怎样：属性的 clamp 范围变了，用户配置的极端值会被截断到新范围

    @property
    def enabled(self) -> bool:
        return _to_bool(self._plugin_sec().get("enabled", defaults.ENABLED), default=defaults.ENABLED)

    @property
    def linear(self) -> bool:
        return _to_bool(self._plugin_sec().get("linear", defaults.LINEAR), default=defaults.LINEAR)

    @property
    def panel_expanded(self) -> bool:
        return _to_bool(self._plugin_sec().get("panel_expanded", defaults.PANEL_EXPANDED))

    @property
    def streaming_panel_expanded(self) -> bool:
        return _to_bool(self._plugin_sec().get("streaming_panel_expanded", defaults.STREAMING_PANEL_EXPANDED))

    @property
    def max_tool_steps(self) -> int:
        val = _to_int(self._plugin_sec().get("max_tool_steps", defaults.MAX_TOOL_STEPS), default=defaults.MAX_TOOL_STEPS)
        return max(1, min(100, val))

    @property
    def max_reasoning_rounds(self) -> int:
        val = _to_int(self._plugin_sec().get("max_reasoning_rounds", defaults.MAX_REASONING_ROUNDS), default=defaults.MAX_REASONING_ROUNDS)
        return max(1, min(100, val))

    @property
    def reasoning_batch_size(self) -> int:
        val = _to_int(self._plugin_sec().get("reasoning_batch_size", defaults.REASONING_BATCH_SIZE), default=defaults.REASONING_BATCH_SIZE)
        return max(1, min(50, val))

    @property
    def print_strategy(self) -> str:
        strategy = self._plugin_sec().get("print_strategy", defaults.PRINT_STRATEGY)
        return strategy if strategy in ("fast", "delay") else defaults.PRINT_STRATEGY

    @property
    def print_step(self) -> int:
        val = _to_int(self._plugin_sec().get("print_step", defaults.PRINT_STEP), default=defaults.PRINT_STEP)
        return max(1, min(10, val))

    @property
    def flush_interval_ms(self) -> float:
        ms = _to_float(self._plugin_sec().get("flush_interval_ms", defaults.FLUSH_INTERVAL_MS), default=defaults.FLUSH_INTERVAL_MS)
        return max(70.0, min(2000.0, ms))

    @property
    def flush_interval_sec(self) -> float:
        return self.flush_interval_ms / 1000.0

    @property
    def card_duration_sec(self) -> int:
        return _to_int(self._plugin_sec().get("card_ttl_sec", defaults.CARD_TTL_SEC), default=defaults.CARD_TTL_SEC)

    # -- Footer --------------------------------------------------------------

    @property
    def footer_fields(self) -> list[list[str]]:
        footer = self._plugin_sec().get("footer", {})
        if not isinstance(footer, dict):
            return defaults.FOOTER_FIELDS
        fields = footer.get("fields")
        if not fields or not isinstance(fields, list):
            return defaults.FOOTER_FIELDS
        # Normalise: a flat list of strings → wrap in a single-row list
        if fields and isinstance(fields[0], str):
            return [fields]
        return fields

    @property
    def footer_show_label(self) -> bool:
        footer = self._plugin_sec().get("footer", {})
        if not isinstance(footer, dict):
            return defaults.FOOTER_SHOW_LABEL
        return _to_bool(footer.get("show_label", defaults.FOOTER_SHOW_LABEL))

    # -- Personalization ------------------------------------------------------

    @property
    def panel_title(self) -> str:
        return str(self._plugin_sec().get("panel_title", defaults.PANEL_TITLE))

    @property
    def loading_text(self) -> str:
        return str(self._plugin_sec().get("loading_text", defaults.LOADING_TEXT))

    @property
    def thinking_text(self) -> str:
        return str(self._plugin_sec().get("thinking_text", defaults.THINKING_TEXT))

    @property
    def auto_collapse_threshold(self) -> int:
        val = _to_int(self._plugin_sec().get("auto_collapse_threshold", defaults.AUTO_COLLAPSE_THRESHOLD), default=defaults.AUTO_COLLAPSE_THRESHOLD)
        return max(0, min(100, val))

    @property
    def speed_curve(self) -> str:
        curve = self._plugin_sec().get("speed_curve", defaults.SPEED_CURVE)
        return curve if curve in ("flat", "answer_fast") else defaults.SPEED_CURVE

    @property
    def answer_fast_stream_ms(self) -> float:
        ms = _to_float(self._plugin_sec().get("answer_fast_stream_ms", defaults.ANSWER_FAST_STREAM_MS), default=defaults.ANSWER_FAST_STREAM_MS)
        return max(70.0, min(1000.0, ms))

    @property
    def panel_border_color(self) -> str:
        color = self._plugin_sec().get("panel_border_color", defaults.PANEL_BORDER_COLOR)
        return color if color in ("grey", "blue", "green", "orange", "red") else defaults.PANEL_BORDER_COLOR

    @property
    def panel_header_color(self) -> str:
        color = self._plugin_sec().get("panel_header_color", defaults.PANEL_HEADER_COLOR)
        return color if color in ("grey", "blue", "green", "orange", "red") else defaults.PANEL_HEADER_COLOR

    # -- Card-level header ---------------------------------------------------

    def _card_header_field(self, key: str, default: str) -> str:
        """三层 fallback：card_header dict → 顶层旧字段 → 默认值。"""
        header = self._plugin_sec().get("card_header", {})
        if not isinstance(header, dict):
            return str(self._plugin_sec().get(f"card_header_{key}", default))
        return str(header.get(key, default))

    @property
    def card_header_title(self) -> str:
        return self._card_header_field("title", defaults.CARD_HEADER_TITLE)

    @property
    def card_header_subtitle(self) -> str:
        return self._card_header_field("subtitle", defaults.CARD_HEADER_SUBTITLE)

    @property
    def card_header_icon(self) -> str:
        return self._card_header_field("icon", defaults.CARD_HEADER_ICON)

    @property
    def card_header_template(self) -> str:
        tpl = self._card_header_field("template", defaults.CARD_HEADER_TEMPLATE)
        valid = ("blue", "green", "orange", "red", "purple", "indigo", "turquoise", "yellow", "grey", "violet", "wathet", "carmine")
        return tpl if tpl in valid else defaults.CARD_HEADER_TEMPLATE

    @property
    def dynamic_quotes_enabled(self) -> bool:
        header = self._plugin_sec().get("card_header", {})
        if not isinstance(header, dict):
            return _to_bool(self._plugin_sec().get("dynamic_quotes_enabled", defaults.DYNAMIC_QUOTES_ENABLED))
        return _to_bool(header.get("dynamic_quotes_enabled", defaults.DYNAMIC_QUOTES_ENABLED))

    @property
    def dynamic_quotes_cooldown(self) -> float:
        header = self._plugin_sec().get("card_header", {})
        if not isinstance(header, dict):
            return float(self._plugin_sec().get("dynamic_quotes_cooldown", defaults.DYNAMIC_QUOTES_COOLDOWN))
        return float(header.get("dynamic_quotes_cooldown", defaults.DYNAMIC_QUOTES_COOLDOWN))

    # -- Runtime-mutable (TTL-cached reads) ----------------------------------
    # 这些属性走 _reload_cached（有 TTL），支持热更新不重启
    # 为什么单独一路：普通属性只读一次就够了，但 show_reasoning 等需要运行时改

    @property
    def show_reasoning(self) -> bool:
        # 1. Plugin section (highest priority)
        plugin_val = self._plugin_sec().get("show_reasoning")
        if plugin_val is not None:
            return _to_bool(plugin_val)
        # 2. Display > feishu section
        display = self._reload_cached().get("display")
        if isinstance(display, dict):
            platforms = display.get("platforms")
            if isinstance(platforms, dict):
                feishu = platforms.get("feishu")
                if isinstance(feishu, dict) and "show_reasoning" in feishu:
                    return _to_bool(feishu["show_reasoning"])
            return _to_bool(display.get("show_reasoning", defaults.SHOW_REASONING))
        return defaults.SHOW_REASONING

    @property
    def gateway_cards(self) -> bool:
        sec = self._reload_cached().get("lark_hls_v2")
        if not isinstance(sec, dict):
            return defaults.GATEWAY_CARDS
        return _to_bool(sec.get("gateway_cards", defaults.GATEWAY_CARDS), default=defaults.GATEWAY_CARDS)

    # -- Feishu platform -----------------------------------------------------

    @property
    def feishu_app_id(self) -> str:
        return str(self._platform_cfg().get("app_id", ""))

    @property
    def feishu_app_secret(self) -> str:
        return str(self._platform_cfg().get("app_secret", ""))

    @property
    def feishu_base_url(self) -> str:
        return str(self._platform_cfg().get("base_url", defaults.FEISHU_BASE_URL))

    @property
    def env_app_id(self) -> str:
        return os.environ.get("FEISHU_APP_ID") or os.environ.get("LARK_APP_ID") or ""

    @property
    def env_app_secret(self) -> str:
        return os.environ.get("FEISHU_APP_SECRET") or os.environ.get("LARK_APP_SECRET") or ""

    # -- Internal helpers ----------------------------------------------------

    def _plugin_sec(self) -> dict[str, Any]:
        """_plugin_sec(): 从 raw YAML 取 lark_hls_v2 节，不存在返回空 dict."""
        raw = self._load()
        sec = raw.get("lark_hls_v2")
        return sec if isinstance(sec, dict) else {}

    def _platform_cfg(self) -> dict[str, Any]:
        """_platform_cfg(): 飞书凭证查找链：环境变量 > config.yaml feishu 节 > config.yaml lark 节."""
        if self.env_app_id and self.env_app_secret:
            base_url = (
                os.environ.get("FEISHU_BASE_URL")
                or os.environ.get("LARK_BASE_URL")
                or None
            )
            if not base_url:
                domain = os.environ.get("FEISHU_DOMAIN", "").lower()
                if domain == "lark":
                    base_url = "https://open.larksuite.com/open-apis"
                else:
                    base_url = defaults.FEISHU_BASE_URL
            return {
                "app_id": self.env_app_id,
                "app_secret": self.env_app_secret,
                "base_url": base_url,
            }
        raw = self._load()
        for key in ("feishu", "lark"):
            pf = raw.get(key)
            if isinstance(pf, dict) and pf.get("app_id"):
                return pf
        return {}

    def _load(self) -> dict[str, Any]:
        """_load(): 线程安全的懒加载，只读一次磁盘，后续走内存缓存."""
        with self._lock:
            if self._raw is not None:
                return self._raw
            self._raw = self._read_yaml()
            return self._raw

    def _reload_cached(self) -> dict[str, Any]:
        """_reload_cached(): TTL 缓存的磁盘重读，给 runtime-mutable 属性用（如 show_reasoning）。
        改了会怎样：TTL 过短 → 频繁磁盘 IO；TTL 过长 → 热更新延迟变大。
        """
        now = time.monotonic()
        with self._lock:
            if self._reload_cache is not None and (now - self._reload_cache_at) < defaults.RELOAD_CACHE_TTL:
                return self._reload_cache
            self._reload_cache = self._read_yaml()
            self._reload_cache_at = now
            return self._reload_cache

    @staticmethod
    def _read_yaml() -> dict[str, Any]:
        config_path = _get_hermes_config_path()
        if not config_path.exists():
            return {}
        try:
            text = config_path.read_text(encoding="utf-8")
            return yaml.safe_load(text) or {}
        except (yaml.YAMLError, OSError, UnicodeDecodeError):
            _logger.warning("HLS v2: config read error in %s, using empty config", config_path)
            return {}
