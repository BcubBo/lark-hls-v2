# =================================================================
# lark-hls-v2 · feishu/guard.py 总导游图（改代码前必读，读完再动手）
# ▍这是什么
# ① 干什么：检测飞书消息被删除/撤回后终止 reply pipeline，避免对已消失的消息继续更新卡片。
#    维护一个线程安全的 unavailable 缓存，记录哪些 message_id 已知不可用。
# ② 技术栈：Python 3.11, threading.Lock, re
# ③ 依赖：feishu.client.MSG_NOT_FOUND（错误码常量）
# ④ 给谁看：调试 pipeline 意外终止、修改消息生命周期判断逻辑的开发者。
# ▍文件从上到下的结构
# 缓存区      — _unavailable_cache + TTL + 修剪阈值
# 缓存操作    — _prune_cache / mark_unavailable / is_unavailable / _get_cached_code
# 错误码判断  — extract_api_code / is_terminal_api_code
# UnavailableGuard 类 — ★本文件核心★，pipeline 终止守卫
# ▍修改铁律
# 1. _TERMINAL_MESSAGE_CODES 里的码是"消息已死"的终态，加新码前确认飞书文档说它是终态。
# 2. _get_cached_code 用 is-None 检查而非 or —— code=0 是合法错误码，or 会吞掉它。
# 3. _PRUNE_THRESHOLD=50 是性能优化阈值，改小会频繁清理，改大会内存膨胀。
# ▍外号表
# "guard"   → UnavailableGuard（pipeline 终止守卫）
# "cache"   → _unavailable_cache（消息不可用状态的内存缓存）
# =================================================================

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable
from typing import Any

from .client import MSG_NOT_FOUND

_logger = logging.getLogger("lark_hls_v2")

# 消息终端码 — 收到这些码说明消息已死（删除/撤回），pipeline 应立即终止
_TERMINAL_MESSAGE_CODES = {
    231003,  # message deleted
    MSG_NOT_FOUND,
    230011,  # message recalled
}

_unavailable_cache: dict[str, dict[str, Any]] = {}
# "cache" — 消息不可用状态的内存缓存，key=message_id
_unavailable_cache_lock = threading.Lock()
_UNENHANCED_CACHE_TTL_SEC = 30 * 60  # 30 分钟 TTL，改了会怎样：TTL 过短 → 重复请求已死消息；过长 → 内存占用
# 修剪阈值：只在缓存超过此值时才清理，避免每次操作都遍历
_PRUNE_THRESHOLD = 50

def _prune_cache() -> None:
    """清理过期缓存条目."""
    now = time.time()
    expired = [k for k, v in _unavailable_cache.items() if now - v.get("at", 0) > _UNENHANCED_CACHE_TTL_SEC]
    for k in expired:
        _unavailable_cache.pop(k, None)

def mark_unavailable(message_id: str, code: int, operation: str = "") -> None:
    """mark_unavailable(): 标记消息为不可用，写入缓存（线程安全）."""
    with _unavailable_cache_lock:
        _unavailable_cache[message_id] = {
            "code": code,
            "operation": operation,
            "at": time.time(),
        }

def is_unavailable(message_id: str | None) -> bool:
    """is_unavailable(): 检查消息是否已知不可用，超阈值时自动触发 prune."""
    if not message_id:
        return False
    with _unavailable_cache_lock:
        if len(_unavailable_cache) > _PRUNE_THRESHOLD:
            _prune_cache()
        return message_id in _unavailable_cache

def _get_cached_code(message_id: str | None) -> int | None:
    """线程安全地从缓存读取错误码（修复 code=0 被 or 吞掉的 bug）."""
    if not message_id:
        return None
    with _unavailable_cache_lock:
        entry = _unavailable_cache.get(message_id)
        return entry.get("code") if entry else None

_RE_API_CODE = re.compile(r"code[=:]\s*(\d+)")

def extract_api_code(err: Exception | None) -> int | None:
    """extract_api_code(): 从异常中提取 API 错误码，支持 .code 属性和字符串正则匹配."""
    if err is None:
        return None
    if hasattr(err, "code"):
        code = err.code
        if isinstance(code, int):
            return code
    if hasattr(err, "args") and err.args:
        first = err.args[0]
        if isinstance(first, str):
            # 尝试从字符串中提取 code=数字
            match = _RE_API_CODE.search(first)
            if match:
                return int(match.group(1))
    return None

def is_terminal_api_code(code: int | None) -> bool:
    """is_terminal_api_code(): 判断错误码是否为消息终端码（删除/撤回/不存在）."""
    return code is not None and code in _TERMINAL_MESSAGE_CODES

class UnavailableGuard:
    """★本文件核心★ UnavailableGuard: 检测消息被删除/撤回后终止 reply pipeline.
    谁调用：pipeline 主循环在每次 flush 前调 should_skip()，API 报错时调 terminate().
    改了会怎样：去掉 guard → 对已死消息持续更新卡片，浪费 API 调用且可能报错.
    """

    def __init__(
        self,
        reply_to_message_id: str | None,
        get_card_message_id: Callable[[], str | None],
        on_terminate: Callable[[], None],
    ) -> None:
        self._reply_to_message_id = reply_to_message_id
        self._get_card_message_id = get_card_message_id
        self._on_terminate = on_terminate
        self._terminated = False

    def should_skip(self, source: str) -> bool:
        """should_skip(): 检查是否应跳过当前操作 —— 已终止或 reply_to 消息已不可用."""
        if self._terminated:
            return True
        if not self._reply_to_message_id:
            return False
        if is_unavailable(self._reply_to_message_id):
            return self.terminate(source)
        return False

    def terminate(self, source: str, err: Exception | None = None) -> bool:
        """terminate(): 尝试终止 pipeline，仅在错误码是终态时才真正终止.
        返回 True 表示已终止（或早已终止），False 表示错误不是终态，pipeline 继续.
        """
        if self._terminated:
            return True

        code = extract_api_code(err)
        card_msg_id = self._get_card_message_id()

        # 从错误码或缓存中判断
        if code is None and (is_unavailable(self._reply_to_message_id) or is_unavailable(card_msg_id)):
            # Fix: use is-None check instead of `or` — `0 or X` returns X,
            # but code=0 is a valid error code that should not be skipped.
            code = _get_cached_code(self._reply_to_message_id)
            if code is None:
                code = _get_cached_code(card_msg_id)

        if not is_terminal_api_code(code):
            return False

        assert code is not None

        self._terminated = True
        self._on_terminate()

        affected = self._reply_to_message_id or card_msg_id or "unknown"
        _logger.warning(
            "reply pipeline terminated by unavailable message: source=%s code=%s message_id=%s",
            source,
            code,
            affected,
        )

        # 标记缓存
        if self._reply_to_message_id:
            mark_unavailable(self._reply_to_message_id, code, source)
        if card_msg_id:
            mark_unavailable(card_msg_id, code, source)

        return True
