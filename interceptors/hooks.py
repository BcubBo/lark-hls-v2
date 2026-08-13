"""These functions are called by interceptors/ sub-package when wrapping Hermes methods."""

# ================================================================
# lark-hls-v2 · interceptors/hooks.py 总导游图（改代码前必读，读完再动手）
# ▍这是什么
# ① 干什么：注入点（hook）的统一定义层。每个 hook 对应一个 controller 方法，
#    负责 enabled 检查 + 异常捕获 + 参数转发。是 interceptors/ 和 controller.py
#    之间的桥梁。
# ② 技术栈：纯 Python + functools.wraps，无外部依赖。
# ③ 依赖：controller.py（get_controller + 各 on_* 方法）。
# ④ 给谁看：interceptors/gateway.py、interceptors/callbacks.py（调用方）。
# ▍结构
# _safe_hook() — 装饰器工厂：统一 enabled 检查 + 异常兜底
# 注入点 0-9：
#   0: on_feishu_normalize — 飞书引用消息 thread_id 修正
#   1: on_message_started — 消息开始处理
#   2: on_message_completed — 消息处理完成（带 token/context 信息）
#   3: on_tool_updated — 工具调用状态变更
#   4: on_answer_delta — answer 流式增量
#   5: on_thinking_delta — thinking 流式增量
#   6: on_reasoning_delta — 模型原生 reasoning 增量
#   7: on_background_review_message — 后台 review 消息
#   8: on_message_aborted — 消息中止
#   9: on_message_interrupted — 消息被新消息中断
#  10: on_cron_deliver — cron 推送（async，不走 _safe_hook）
# ▍修改铁律
# 1. 新增 hook 必须同步更新 interceptors/__init__.py 的 __all__ 和 re-export。
# 2. on_message_completed 的参数最多（13个），改签名会影响 gateway.py 的 3 个调用点。
# 3. on_feishu_normalize 不走 _safe_hook（需要直接访问 source/event 对象），
#    它的异常处理是独立的。
# ================================================================

from __future__ import annotations
import logging
from collections.abc import Callable
from functools import wraps
from typing import Any

from ..controller import get_controller

_logger = logging.getLogger("lark_hls_v2")


def _safe_hook(
    default_return: Any = None,
    log_level: str = "warning",
) -> Callable:
    """统一处理 enabled 检查和异常捕获."""

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*, message_id: str | None = None, **kwargs: Any) -> Any:
            try:
                ctrl = get_controller()
                if not ctrl.enabled:
                    return default_return
                return func(ctrl=ctrl, message_id=message_id, **kwargs)
            except Exception as exc:
                getattr(_logger, log_level)("%s error: %s", func.__name__, exc, exc_info=True)
                return default_return

        return wrapper

    return decorator


def on_feishu_normalize(
    *,
    message_id: str,
    source: Any,
    event: Any,
    reply_anchor_id: str | None = None,
) -> None:
    """[注入点 0] _handle_message source 赋值后 — 修正飞书引用消息的虚假 thread_id."""
    ctrl = get_controller()
    if not ctrl.enabled:
        return

    platform = getattr(getattr(source, "platform", None), "value", "")
    if platform != "feishu":
        return

    raw = getattr(event, "raw_message", None)
    raw_event = raw.get("event") if isinstance(raw, dict) else None
    if raw_event is None:
        raw_event = getattr(raw, "event", None)

    raw_message = None
    if isinstance(raw_event, dict):
        raw_message = raw_event.get("message")
    elif raw_event is not None:
        raw_message = getattr(raw_event, "message", None)
    if raw_message is None and isinstance(raw, dict):
        raw_message = raw.get("message")
    if raw_message is None:
        raw_message = raw

    real_thread_id = None
    if isinstance(raw_message, dict):
        real_thread_id = raw_message.get("thread_id")
    else:
        real_thread_id = getattr(raw_message, "thread_id", None)

    reply_to = getattr(event, "reply_to_message_id", None)
    source_thread_id = getattr(source, "thread_id", None)

    _logger.info(
        "feishu inbound ids: msg=%s anchor=%s source_thread=%s raw_thread=%s reply_to=%s",
        message_id,
        reply_anchor_id,
        source_thread_id,
        real_thread_id,
        reply_to,
    )

    if reply_to and source_thread_id and not real_thread_id:
        source.thread_id = None
        event.source = source


@_safe_hook()
def on_message_started(*, ctrl: Any, message_id: str, chat_id: str, anchor_id: str | None = None) -> None:
    """[注入点 1] 函数开头 — message.started."""
    ctrl.on_message_started(message_id=message_id, chat_id=chat_id, anchor_id=anchor_id)


@_safe_hook(default_return=False)
def on_message_completed(
    *,
    ctrl: Any,
    message_id: str,
    answer: str = "",
    duration: float = 0.0,
    model: str = "",
    tokens: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
    api_calls: int = 0,
    history_offset: int = 0,
    compression_exhausted: bool = False,
    aborted: bool = False,
    error_message: str = "",
    reasoning_tokens: int = 0,
    estimated_cost_usd: float = 0.0,
    cost_status: str = "unknown",
) -> bool:
    """[注入点 2] return 前 — message.completed."""
    return bool(
        ctrl.on_completed(
            message_id=message_id,
            answer=answer,
            duration=duration,
            model=model,
            tokens=tokens,
            context=context,
            api_calls=api_calls,
            history_offset=history_offset,
            compression_exhausted=compression_exhausted,
            aborted=aborted,
            error_message=error_message,
            reasoning_tokens=reasoning_tokens,
            estimated_cost_usd=estimated_cost_usd,
            cost_status=cost_status,
        )
    )


@_safe_hook(default_return=False)
def on_tool_updated(
    *,
    ctrl: Any,
    message_id: str,
    tool_name: str,
    status: str,
    detail: str = "",
) -> bool:
    """[注入点 3] progress_callback — tool.updated."""
    ctrl.on_tool_update(
        message_id=message_id,
        tool_name=tool_name,
        status=status,
        detail=detail,
    )
    return True


@_safe_hook(default_return=False, log_level="debug")
def on_answer_delta(*, ctrl: Any, message_id: str, text: str) -> bool:
    """[注入点 4] _stream_delta_cb — answer.delta."""
    ctrl.on_answer(message_id=message_id, text=text)
    return True


@_safe_hook(default_return=False, log_level="debug")
def on_thinking_delta(*, ctrl: Any, message_id: str, text: str) -> bool:
    """[注入点 5] _interim_assistant_cb — thinking.delta."""
    ctrl.on_thinking(message_id=message_id, text=text)
    return True


@_safe_hook(default_return=False, log_level="debug")
def on_reasoning_delta(*, ctrl: Any, message_id: str, text: str) -> bool:
    """[注入点 6] reasoning_callback — native model reasoning delta."""
    ctrl.on_reasoning(message_id=message_id, text=text)
    return True


@_safe_hook(default_return=False, log_level="debug")
def on_background_review_message(
    *,
    ctrl: Any,
    message_id: str,
    text: str,
    sender: Callable[[str], Any],
) -> bool:
    """[注入点 7] background_review_callback — background.review."""
    deferred: bool = ctrl.defer_background_review(message_id=message_id, text=text, sender=sender)
    return deferred


@_safe_hook()
def on_message_aborted(*, ctrl: Any, message_id: str) -> None:
    """[注入点 8] stale return None 前 — message.aborted."""
    ctrl.on_aborted(message_id=message_id)


@_safe_hook()
def on_message_interrupted(
    *,
    ctrl: Any,
    message_id: str,
    new_message_id: str,
    chat_id: str,
    anchor_id: str | None = None,
) -> None:
    """[注入点 9] interrupt 发生 — message.interrupted."""
    ctrl.on_interrupted(
        old_message_id=message_id,
        new_message_id=new_message_id,
        chat_id=chat_id,
        anchor_id=anchor_id,
    )


async def on_cron_deliver(
    *,
    chat_id: str,
    content: str,
    loop: Any = None,
) -> bool:
    """[注入点 10] cron 推送 — 包装为飞书卡片发送."""
    if loop is None:
        return False
    try:
        ctrl = get_controller()
        if not ctrl.enabled:
            return False
        return bool(await ctrl.on_cron_deliver_async(chat_id=chat_id, content=content, loop=loop))
    except Exception as exc:
        _logger.warning("on_cron_deliver error: %s", exc, exc_info=True)
        return False
