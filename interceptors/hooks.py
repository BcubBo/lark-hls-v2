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
import os
import time
from collections.abc import Callable
from functools import wraps
from typing import Any

from ..controller import get_controller

# feishu_identity 模块（替代旧的 FeishuUserCache）
import sys as _sys
from pathlib import Path as _Path
_hermes_home = os.environ.get("HERMES_HOME", str(_Path.home() / ".hermes" / "profiles" / "bo"))
_scripts_dir = os.path.join(_hermes_home, "scripts")
if _scripts_dir not in _sys.path:
    _sys.path.insert(0, _scripts_dir)
try:
    from feishu_identity import FeishuIdentity as _FeishuIdentity
except ImportError:
    # 插件缺失时提供空实现，避免全部 hook 失效
    class _FeishuIdentity:  # type: ignore[no-redef]
        @staticmethod
        def get_user_id(*a, **kw):
            return None
        @staticmethod
        def get_user_name(*a, **kw):
            return None
        @staticmethod
        def resolve(open_id, chat_id=None, sender_type=None):
            return open_id

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
    
    # Inject system_role into source.user_name for AI permission checking
    _inject_system_role(source, event)
    
    # Auto-insert sender into feishu_users database (group context)
    _auto_insert_sender(source, raw)

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
    category: str = "",
    loop: Any = None,
    job: dict | None = None,
) -> bool:
    """[注入点 10] cron 推送 — 包装为飞书卡片发送.
    
    category: "cron" (默认) | "gateway" | "clarify" 等，决定使用哪种卡片模板
    job: 完整 job dict（可选），含 name/id/schedule/failure_streak/last_error
    """
    if loop is None:
        return False
    try:
        ctrl = get_controller()
        if not ctrl.enabled:
            return False
        
        # 自动检测 category：如果内容包含 "unknown command"，使用 gateway 卡片
        if not category and "unknown command" in content.lower():
            category = "gateway"
        
        # 从 job dict 提取元数据
        job_name = ""
        job_id = ""
        status = "success"
        schedule = None
        failure_streak = 0
        last_error = ""
        if job:
            job_name = job.get("name", "") or job.get("id", "")[:12]
            job_id = job.get("id", "")
            schedule = job.get("schedule")
            failure_streak = int(job.get("failure_streak", 0))
            last_error = job.get("last_error", "")
            # 用 job.failure_streak 作为 status fallback
            if failure_streak > 0:
                status = "error"
        
        # 从 content 检测状态（最高优先级）
        from ..card.special import _detect_cron_status
        status = _detect_cron_status(content)
        
        return bool(await ctrl.on_cron_deliver_async(
            chat_id=chat_id,
            content=content,
            category=category,
            loop=loop,
            job_name=job_name,
            job_id=job_id,
            status=status,
            schedule=schedule,
            failure_streak=failure_streak,
            last_error=last_error,
        ))
    except Exception as exc:
        _logger.warning("on_cron_deliver error: %s", exc, exc_info=True)
        return False

# ---------------------------------------------------------------------------
# ── 权限注入：读 feishu_identity.db ────────────────────────────────────────

_identity_cache: "_FeishuIdentity | None" = None

def _get_identity() -> "_FeishuIdentity":
    global _identity_cache
    if _identity_cache is None:
        _identity_cache = _FeishuIdentity()
    return _identity_cache

def _extract_sender_open_id(raw_event: Any) -> str:
    """Extract sender open_id from raw Feishu event.
    
    Handles both dict and lark_oapi object形态:
      raw_message.event.sender.sender_id.open_id
    """
    try:
        if isinstance(raw_event, dict):
            return (raw_event.get("event", {}).get("sender", {})
                    .get("sender_id", {}).get("open_id", "") or "")
        event_obj = getattr(raw_event, "event", None)
        sender = getattr(event_obj, "sender", None) if event_obj else None
        sender_id = getattr(sender, "sender_id", None) if sender else None
        return getattr(sender_id, "open_id", "") or ""
    except Exception:
        return ""


def _inject_system_role(source: Any, event: Any) -> None:
    """Inject system_role into source.user_name.
    
    Changes user_name from '何博洋' to 'admin:何博洋' so AI can see the role
    and apply SOUL.md permission rules during reasoning.
    
    For external users (contact API fails → source.user_name empty), extracts
    sender open_id from raw event and resolves name via group members API.
    """
    try:
        user_id = getattr(source, "user_id", None) or ""
        user_name = getattr(source, "user_name", None) or ""
        
        # Extract sender open_id from raw event (authoritative source)
        raw = getattr(event, "raw_message", None)
        raw_event = raw.get("event") if isinstance(raw, dict) else getattr(raw, "event", None)
        sender_open_id = _extract_sender_open_id(raw_event or {})
        
        # Prefer sender open_id over source.user_id (more reliable for group messages)
        if sender_open_id and not user_id:
            user_id = sender_open_id
            source.user_id = user_id
        elif sender_open_id and user_id != sender_open_id:
            # source.user_id might be tenant-scoped; sender open_id is what we need for identity
            user_id = sender_open_id
        
        if not user_id:
            _logger.warning("[FeishuIdentity] No user_id available, skipping role injection")
            return
        
        # If user_name is empty (external user, contact API failed),
        # resolve name via feishu_identity (group members API works for external users)
        if not user_name:
            try:
                ident = _get_identity()
                chat_id = getattr(source, "chat_id", None) or ""
                resolved_name = ident.resolve(user_id, chat_id=chat_id)
                if resolved_name and resolved_name != user_id:
                    user_name = resolved_name
                    source.user_name = user_name
                    _logger.warning("[FeishuIdentity] Resolved name for %s: %s", user_id, user_name)
            except Exception as e:
                _logger.warning("[FeishuIdentity] Failed to resolve name for %s: %s", user_id, e)
            if not user_name:
                _logger.warning("[FeishuIdentity] No user_name for %s, skipping role injection", user_id)
                return
        
        # Skip if already has role prefix
        if ":" in user_name and user_name.split(":")[0] in ("admin", "moderator", "member"):
            return
        
        ident = _get_identity()
        
        # DM 自动 admin（能私聊 = 最高权限）
        chat_type = getattr(source, "chat_type", None) or ""
        if chat_type == "dm":
            ident.auto_set_role_from_chat(user_id, "dm", user_name)
        
        role = ident.get_role(user_id)
        if role in ("admin", "moderator"):
            source.user_name = f"{role}:{user_name}"
            _logger.warning("[FeishuIdentity] Injected role %s for %s", role, user_name)
        else:
            source.user_name = f"member:{user_name}"
            _logger.warning("[FeishuIdentity] Injected role member for %s", user_name)
    except Exception as e:
        _logger.warning("[FeishuIdentity] Failed to inject system_role: %s", e)


def _auto_insert_sender(source: Any, raw_event: Any) -> None:
    """群消息自动写入 feishu_identity（替代旧的 FeishuUserCache）。"""
    try:
        chat_type = getattr(source, "chat_type", "dm") or "dm"
        if chat_type == "dm":
            return
        
        user_id = getattr(source, "user_id", "") or ""
        user_name = getattr(source, "user_name", "") or ""
        
        if not user_id:
            return
        
        # 跳过已带 role 前缀的名字
        clean_name = user_name
        if ":" in clean_name and clean_name.split(":")[0] in ("admin", "moderator", "member"):
            clean_name = clean_name.split(":", 1)[1]
        
        # 跳过 bot 消息
        try:
            if isinstance(raw_event, dict):
                sender_type = raw_event.get("event", {}).get("sender", {}).get("sender_type", "")
            else:
                event_obj = getattr(raw_event, "event", None)
                sender = getattr(event_obj, "sender", None) if event_obj else None
                sender_type = getattr(sender, "sender_type", "") if sender else ""
            if sender_type == "app":
                return
        except Exception:
            pass
        
        ident = _get_identity()
        # 群聊角色：不覆盖已有的 manual 角色
        ident.auto_set_role_from_chat(user_id, "group", clean_name)
            
    except Exception as e:
        _logger.debug("[FeishuIdentity] _auto_insert_sender error: %s", e)


# ── 群成员同步（简化版）───────────────────────────────────────────

_sync_cache: dict[str, float] = {}
_sync_fail_cache: dict[str, bool] = {}
_SYNC_COOLDOWN = 300
_SYNC_FAIL_COOLDOWN = 600


def _sync_group_members_if_needed(chat_id: str) -> None:
    """限流：每个 chat_id 每 5 分钟最多同步一次。"""
    now = time.time()
    last = _sync_cache.get(chat_id, 0)
    cooldown = _SYNC_FAIL_COOLDOWN if _sync_fail_cache.get(chat_id, False) else _SYNC_COOLDOWN
    if now - last < cooldown:
        return
    _sync_cache[chat_id] = now
    try:
        _sync_group_members(chat_id)
    except Exception as e:
        _logger.debug("[FeishuIdentity] sync_group_members failed for %s: %s", chat_id[:16], e)
        _sync_fail_cache[chat_id] = True


def _sync_group_members(chat_id: str) -> None:
    """拉取飞书群成员名字，写入 feishu_identity（简化版）。"""
    import urllib.request
    import json as _json

    app_id = os.environ.get("FEISHU_APP_ID", "")
    app_secret = os.environ.get("FEISHU_APP_SECRET", "")
    if not app_id or not app_secret:
        return

    # 获取 token
    req = urllib.request.Request(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        data=_json.dumps({"app_id": app_id, "app_secret": app_secret}).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        token = _json.loads(resp.read()).get("tenant_access_token", "")
    if not token:
        return

    # 获取群成员
    url = f"https://open.feishu.cn/open-apis/im/v1/chats/{chat_id}/members?member_id_type=open_id&page_size=100"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = _json.loads(resp.read()).get("data", {})
    members = data.get("items", [])

    if not members:
        return

    ident = _get_identity()
    for m in members:
        oid = m.get("member_id", "")
        name = m.get("name", "")
        if oid and name:
            ident.resolve(oid, chat_id)  # 只缓存名字

    _logger.debug("[FeishuIdentity] Synced %d members for chat=%s", len(members), chat_id[:16])



