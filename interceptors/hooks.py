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
from collections.abc import Callable
from functools import wraps
from typing import Any

from ..controller import get_controller
from ..feishu.user_cache import FeishuUserCache

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
    _inject_system_role(source)
    
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
) -> bool:
    """[注入点 10] cron 推送 — 包装为飞书卡片发送.
    
    category: "cron" (默认) | "gateway" | "clarify" 等，决定使用哪种卡片模板
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
        
        return bool(await ctrl.on_cron_deliver_async(chat_id=chat_id, content=content, category=category, loop=loop))
    except Exception as exc:
        _logger.warning("on_cron_deliver error: %s", exc, exc_info=True)
        return False

# ---------------------------------------------------------------------------
# System role injection: inject system_role into source.user_name
# ---------------------------------------------------------------------------

_user_cache: FeishuUserCache | None = None

def _get_user_cache() -> FeishuUserCache:
    global _user_cache
    if _user_cache is None:
        from hermes_constants import get_hermes_home
        _user_cache = FeishuUserCache(str(get_hermes_home() / "feishu.group.sqlite3"))
    return _user_cache

def _inject_system_role(source: Any) -> None:
    """Inject system_role into source.user_name.
    
    Changes user_name from '何博洋' to 'admin:何博洋' so AI can see the role
    and apply SOUL.md permission rules during reasoning.
    """
    try:
        user_id = getattr(source, "user_id", None) or ""
        user_name = getattr(source, "user_name", None) or ""
        
        if not user_id or not user_name:
            return
        
        # Skip if already has role prefix
        if ":" in user_name and user_name.split(":")[0] in ("admin", "moderator", "member"):
            return
        
        cache = _get_user_cache()
        user_info = cache.get_user(user_id)
        
        if user_info:
            system_role = user_info.get("system_role", "member")
            if system_role in ("admin", "moderator"):
                source.user_name = f"{system_role}:{user_name}"
                _logger.info("[FeishuUserCache] Injected role %s for %s", system_role, user_name)
    except Exception as e:
        _logger.warning("[FeishuUserCache] Failed to inject system_role: %s", e)


def _auto_insert_sender(source: Any, raw_event: Any) -> None:
    """群消息自动入库 + 群成员全量同步。
    
    触发条件：群消息到达时。
    1. 自动将发送者写入 feishu_users（open_id + name + chat_id）
    2. 调飞书 API 拉群成员列表，补齐所有人的 feishu_role 和 chat_id
    3. 同步 open_id ↔ user_id 的 linked_id 关联
    
    限流：每个 chat_id 每 5 分钟最多同步一次。
    """
    try:
        chat_type = getattr(source, "chat_type", "dm") or "dm"
        if chat_type == "dm":
            return
        
        chat_id = getattr(source, "chat_id", "") or ""
        user_id = getattr(source, "user_id", "") or ""
        user_name = getattr(source, "user_name", "") or ""
        
        if not chat_id or not user_id:
            return
        
        # 跳过已带 role 前缀的名字
        clean_name = user_name
        if ":" in clean_name and clean_name.split(":")[0] in ("admin", "moderator", "member"):
            clean_name = clean_name.split(":", 1)[1]
        
        # 检查 sender_type，跳过 bot 消息
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
        
        # 步骤1：插入发送者
        cache = _get_user_cache()
        if clean_name:
            cache.auto_insert_from_message(
                open_id=user_id,
                name=clean_name,
                chat_id=chat_id,
                feishu_role="member",  # 后续由全量同步覆盖
            )
        
        # 步骤2：限流检查后同步群成员
        _sync_group_members_if_needed(chat_id, cache)
            
    except Exception as e:
        _logger.debug("[FeishuUserCache] _auto_insert_sender error: %s", e)


# ── 群成员同步（飞书 API）───────────────────────────────────────────

_sync_cache: dict[str, float] = {}  # chat_id → last_sync_ts
_SYNC_COOLDOWN = 300  # 5 分钟
_SYNC_FAIL_COOLDOWN = 600  # 失败后 10 分钟再试


def _sync_group_members_if_needed(chat_id: str, cache: "FeishuUserCache") -> None:
    """限流：每个 chat_id 每 5 分钟最多同步一次群成员。失败后 10 分钟再试。"""
    import time
    now = time.time()
    last = _sync_cache.get(chat_id, 0)
    if now - last < _SYNC_COOLDOWN:
        return
    _sync_cache[chat_id] = now  # 预设冷却，无论成功失败
    try:
        _sync_group_members(chat_id, cache)
    except Exception as e:
        _logger.warning("[FeishuUserCache] sync_group_members failed for %s: %s", chat_id[:16], e)
        # 失败时延长冷却，避免每条消息都重试
        _sync_cache[chat_id] = now + (_SYNC_FAIL_COOLDOWN - _SYNC_COOLDOWN)


def _get_tenant_access_token() -> str:
    """从环境变量获取飞书 tenant_access_token。"""
    import urllib.request
    import json as _json
    
    app_id = os.environ.get("FEISHU_APP_ID", "")
    app_secret = os.environ.get("FEISHU_APP_SECRET", "")
    if not app_id or not app_secret:
        return ""
    
    req = urllib.request.Request(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        data=_json.dumps({"app_id": app_id, "app_secret": app_secret}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = _json.loads(resp.read())
    return data.get("tenant_access_token", "")


def _feishu_api_get(token: str, url: str) -> dict:
    """GET 飞书 API，返回 JSON dict。"""
    import urllib.request
    import json as _json
    
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return _json.loads(resp.read())


def _sync_group_members(chat_id: str, cache: "FeishuUserCache") -> None:
    """拉取飞书群成员列表，同步到 SQLite。
    
    1. 获取群信息（群主 ID）→ 设置 feishu_role=owner
    2. 获取群成员列表 → 所有人 chat_id + name
    3. 自动关联 open_id ↔ user_id（同名记录互绑 linked_id）
    """
    token = _get_tenant_access_token()
    if not token:
        raise RuntimeError("Failed to get tenant_access_token")
    
    _logger.info("[FeishuUserCache] Syncing group members for chat=%s", chat_id[:16])
    
    # 获取群主信息
    owner_id = ""
    try:
        chat_info = _feishu_api_get(
            token,
            f"https://open.feishu.cn/open-apis/im/v1/chats/{chat_id}",
        )
        owner_id = chat_info.get("data", {}).get("owner_id", "")
    except Exception:
        _logger.debug("[FeishuUserCache] Failed to get chat info", exc_info=True)
    
    # 获取群成员列表
    members = []
    page_token = ""
    while True:
        url = (
            f"https://open.feishu.cn/open-apis/im/v1/chats/{chat_id}/members"
            f"?member_id_type=open_id&page_size=100"
        )
        if page_token:
            url += f"&page_token={page_token}"
        try:
            resp = _feishu_api_get(token, url)
        except Exception:
            _logger.debug("[FeishuUserCache] Failed to get members", exc_info=True)
            break
        
        data = resp.get("data", {})
        members.extend(data.get("items", []))
        if not data.get("has_more"):
            break
        page_token = data.get("page_token", "")
    
    if not members:
        return
    
    _logger.info("[FeishuUserCache] Found %d members in chat=%s", len(members), chat_id[:16])
    
    # 同步每个成员
    for m in members:
        mid = m.get("member_id", "")
        mname = m.get("name", "")
        if not mid or not mname:
            continue
        
        feishu_role = "owner" if mid == owner_id else "member"
        # 写 group_members 表（精确 open_id + chat_id）
        cache.upsert_group_member(
            open_id=mid,
            chat_id=chat_id,
            feishu_role=feishu_role,
        )
        # 同时更新 feishu_users 基础信息（name）
        cache.auto_insert_from_message(
            open_id=mid,
            name=mname,
            chat_id=chat_id,
            feishu_role=feishu_role,
        )
    
    # 自动关联 linked_id（同名的 open_id 和 user_id 记录互绑）
    _link_ids_by_name(cache)


def _link_ids_by_name(cache: "FeishuUserCache") -> None:
    """将同名的 open_id 和 user_id 记录互绑 linked_id。"""
    try:
        import sqlite3
        with sqlite3.connect(cache.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT open_id, name, linked_id FROM feishu_users").fetchall()
            
            # 按 name 分组
            by_name: dict[str, list[str]] = {}
            for r in rows:
                by_name.setdefault(r["name"], []).append(r["open_id"])
            
            # 同名且一个 ou_ 一个非 ou_ → 互绑
            for name, ids in by_name.items():
                ou_ids = [i for i in ids if i.startswith("ou_")]
                other_ids = [i for i in ids if not i.startswith("ou_")]
                for ou in ou_ids:
                    for other in other_ids:
                        conn.execute(
                            "UPDATE feishu_users SET linked_id = ? WHERE open_id = ? AND (linked_id IS NULL OR linked_id = '')",
                            (other, ou),
                        )
                        conn.execute(
                            "UPDATE feishu_users SET linked_id = ? WHERE open_id = ? AND (linked_id IS NULL OR linked_id = '')",
                            (ou, other),
                        )
            conn.commit()
    except Exception:
        _logger.debug("[FeishuUserCache] _link_ids_by_name failed", exc_info=True)
