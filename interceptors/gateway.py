# ================================================================
# lark-hls-v2 interceptors/gateway.py -- 总导游图（改代码前必读，读完再动手）
# ▍这是什么
# ① 干什么：wrap GatewayRunner 的核心方法（_handle_message / _handle_message_with_agent /
#    _run_agent / _run_background_task / _wrap_cron_deliver / _wrap_run_conversation），
#    在消息生命周期的每个阶段注入 START / COMPLETE / ABORT / INTERRUPT 钩子。
# ② 技术栈：Python 3.11+ / functools.wraps / contextvars
# ③ 依赖：interceptors.hooks / interceptors.callbacks / state.phase.TERMINAL_PHASES
# ④ 给谁看：维护 lark-hls-v2 的开发者，理解消息生命周期和中断处理。
# ▍文件从上到下的结构
# _wrap_handle_message: NORMALIZE 钩子 + /aowen interrupt hint 检测
# _wrap_handle_message_with_agent: START 钩子 + 中断检测 + COMPLETE/ABORT 钩子
# _wrap_run_agent: 中断时创建新上下文 + 子/父消息 COMPLETE 钩子
# _wrap_run_conversation: wrap 流式回调 + persist_user_timestamp 兼容
# _wrap_run_background_task: /background 任务的 START/COMPLETE 钩子
# _wrap_cron_deliver: cron 推送重定向到 CardKit 卡片
# ▍修改铁律
# 1. _msg_ctx 的清理必须在 finally 块里执行，【不】在正常路径外清理，改了会导致上下文泄漏。
# 2. _saved_parent_ctx 的恢复逻辑是中断处理的核心，【不】删掉，改了会导致 "wrong card gets completion" bug。
# 3. _started_msg_ids 用于检测中断，【不】改 discard 时机，改了会导致误判中断。
# 4. /aowen interrupt hint 检测在 _handle_message 最前面，【不】移到后面，改了会导致 /aowen 命令被当作普通消息处理。
# ================================================================

"""GatewayRunner method wrappers and cron delivery interception."""

from __future__ import annotations

import functools
import time
from typing import Any, Callable

from .. import __version__
from ..state.phase import TERMINAL_PHASES
from . import (
    _msg_ctx,
    _started_msg_ids,
    _started_msg_ids_lock,
    _thread_local_ctx,
    _logger,
)

# ▍_wrap_handle_message -- NORMALIZE 钩子 + /aowen interrupt hint

def _wrap_handle_message(orig: Callable) -> Callable:
    """Inject NORMALIZE hook at the top of GatewayRunner._handle_message."""

    @functools.wraps(orig)
    async def wrapper(self, event, *args, **kwargs):
        # NORMALIZE hook -- fires before any message processing
        try:
            from .hooks import on_feishu_normalize

            on_feishu_normalize(
                message_id=event.message_id,
                source=event.source,
                event=event,
                reply_anchor_id=self._reply_anchor_for_event(event),
            )
        except Exception:
            _logger.warning("HLS: NORMALIZE hook failed", exc_info=True)

        # ── /aowen interrupt hint 检测 ──
        # 如果 agent 正在运行时收到 /aowen，发送提示卡片而非让 LLM 处理。
        try:
            _text = (getattr(event, "text", "") or "").strip()
            if _text.lower().startswith("/aowen"):
                _source = getattr(event, "source", None)
                _platform = getattr(getattr(_source, "platform", None), "value", "")
                if _platform == "feishu" and hasattr(self, "_running_agents"):
                    _quick_key = None
                    try:
                        _quick_key = self._session_key_for_source(_source)
                    except Exception:
                        _logger.debug("HLS: _session_key_for_source failed", exc_info=True)
                    if _quick_key and _quick_key in self._running_agents:
                        # Agent is running -- send interrupt hint card
                        from ..aowen import build_interrupt_hint_card, _send_card_async
                        _chat_id = getattr(_source, "chat_id", "") if _source else ""
                        if _chat_id:
                            _logger.info(
                                "HLS: /aowen during active agent (session=%s), "
                                "sending interrupt hint card",
                                str(_quick_key)[:12],
                            )
                            _send_card_async(_chat_id, build_interrupt_hint_card(), "interrupt_hint")
                            return ""
        except Exception:
            _logger.debug("HLS: /aowen interrupt hint check failed", exc_info=True)

        return await orig(self, event, *args, **kwargs)

    return wrapper

# ▍_wrap_handle_message_with_agent -- START + 中断检测 + COMPLETE/ABORT

def _wrap_handle_message_with_agent(orig: Callable) -> Callable:
    """Inject START hook at entry and ABORT/INTERRUPT detection on return."""

    @functools.wraps(orig)
    async def wrapper(self, event, source, *args, **kwargs):
        mid = event.message_id
        anchor_id = self._reply_anchor_for_event(event)
        chat_id = source.chat_id if hasattr(source, "chat_id") else ""

        # Track this message as started (for interrupt detection)
        with _started_msg_ids_lock:
            _started_msg_ids.add(mid)

        # ── START hook ──
        try:
            from .hooks import on_message_started

            on_message_started(
                message_id=mid,
                chat_id=chat_id,
                anchor_id=anchor_id,
            )
        except Exception:
            _logger.warning("HLS: START hook failed", exc_info=True)
        # 提取 sender 信息，供 mem0x 插件使用（记忆溯源+用户隔离）
        _platform = getattr(source, "platform", None)
        msg_context = {
            "message_id": mid,
            "chat_id": chat_id,
            "anchor_id": anchor_id,
            "event_message_id": "",  # filled by _wrap_run_agent
            "card_sent": False,
            "_msg_start_time": time.monotonic(),  # 自计时：替代无法获取的 _response_time 局部变量
            # 记忆溯源+用户隔离字段
            "user_id": getattr(source, "user_id", "") or "",
            "user_name": getattr(source, "user_name", "") or "",
            "chat_type": getattr(source, "chat_type", "dm") or "dm",
            "platform": _platform.value if _platform else "",
            "session_id": "",  # filled by _wrap_run_agent (session_id not available here yet)
        }
        _msg_ctx.set(msg_context)

        # v1.3.4 fix (P1): 确保 orig() 抛异常时 _msg_ctx / _started_msg_ids 被清理。
        # 不清理会导致 _msg_ctx 保留 stale event_message_id，下一条消息的
        # FeishuAdapter.send() 被静默抑制（"卡片不出现" bug）。
        def _hls_cleanup_ctx() -> None:
            with _started_msg_ids_lock:
                _started_msg_ids.discard(mid)
            _msg_ctx.set(None)
            _thread_local_ctx.data = None

        try:
            result = await orig(self, event, source, *args, **kwargs)
        except BaseException:
            _hls_cleanup_ctx()
            raise

        ctx = msg_context

        # ── 非 None 结果：检查是否应抑制纯文本回复 ──
        if result is not None:
            if ctx and ctx.get("card_sent"):
                _logger.info(
                    "card already sent for msg=%s, suppressing gateway reply",
                    mid[:12],
                )
                _hls_cleanup_ctx()
                return None
            try:
                from ..controller import get_controller
                _ctrl = get_controller()
                if _ctrl and _ctrl.enabled:
                    _eid = ctx.get("event_message_id", "") if ctx else ""
                    if _eid:
                        _sess = _ctrl._sess_get(_eid)
                        if _sess and _sess.card_msg_id:
                            _logger.info(
                                "card session exists for msg=%s (state=%s), suppressing gateway reply",
                                mid[:12], _sess.state,
                            )
                            ctx["card_sent"] = True
                            _hls_cleanup_ctx()
                            return None
            except Exception:
                _logger.warning("HLS: card suppression check failed", exc_info=True)

        # ── None 结果：区分正常完成 vs 中断 vs abort ──
        if result is None:
            if ctx and ctx.get("card_sent"):
                # Bug fix: Hermes returns None when already_sent=True (our
                # interrupt. Without the session-active check, stale message
                with _started_msg_ids_lock:
                    others = _started_msg_ids - {mid}
                _real_interrupt = False
                if others:
                    # Verify the "other" message is genuinely active:
                    # it must have an active (non-terminal) card session.
                    try:
                        from ..controller import get_controller
                        _ctrl = get_controller()
                        if _ctrl and _ctrl.enabled:
                            for _other_mid in others:
                                _other_sess = _ctrl._sess_get(_other_mid)
                                if _other_sess and _other_sess.state not in TERMINAL_PHASES and _other_sess.state != "completing":
                                    _real_interrupt = True
                                    _interrupt_new_mid = _other_mid
                                    break
                        else:
                            # No controller -- fall back to old behavior
                            _real_interrupt = True
                            _interrupt_new_mid = next(iter(others))
                    except Exception:
                        _real_interrupt = bool(others)
                        _interrupt_new_mid = next(iter(others)) if others else None
                if _real_interrupt:
                    try:
                        from .hooks import on_message_interrupted

                        on_message_interrupted(
                            message_id=mid,
                            new_message_id=_interrupt_new_mid,
                            chat_id=chat_id,
                            anchor_id=anchor_id,
                        )
                    except Exception:
                        _logger.warning("HLS: interrupt hook failed", exc_info=True)
                # else: card completed normally, Hermes returned None
                #       to suppress text reply -- NOT an abort.
            else:
                # Card was never sent -- real abort (error, reset, /stop, etc.)
                try:
                    from .hooks import on_message_aborted

                    on_message_aborted(message_id=mid)
                except Exception:
                    _logger.warning("HLS: abort hook failed", exc_info=True)
        elif ctx and ctx.get("card_sent"):
            try:
                from ..controller import get_controller
                _ctrl = get_controller()
                if _ctrl and _ctrl.enabled:
                    _eid = ctx.get("event_message_id", "")
                    if _eid:
                        _sess = _ctrl._sess_get(_eid)
                        if _sess and _sess.state not in TERMINAL_PHASES and _sess.state != "completing":
                            _logger.info(
                                "card session stuck in non-terminal state for msg=%s "
                                "(state=%s, card_sent=%s), firing abort",
                                mid[:12], _sess.state, ctx.get("card_sent"),
                            )
                            try:
                                from .hooks import on_message_aborted
                                on_message_aborted(message_id=mid)
                            except Exception:
                                _logger.warning("HLS: stuck-session abort hook failed", exc_info=True)
            except Exception:
                _logger.warning("HLS: stuck-session check failed", exc_info=True)
        # v1.3.4 fix (P1): cleanup on normal exit path
        _hls_cleanup_ctx()

        return result

    return wrapper

# ▍_wrap_run_agent -- 中断时创建新上下文 + 子/父 COMPLETE 钩子

def _wrap_run_agent(orig: Callable) -> Callable:
    """Inject COMPLETE hook after agent runs; propagate event_message_id."""

    @functools.wraps(orig)
    async def wrapper(
        self,
        message,
        context_prompt,
        history,
        source,
        session_id,
        session_key=None,
        run_generation=None,
        _interrupt_depth=0,
        event_message_id=None,
        channel_prompt=None,
        **kwargs,
    ):
        _saved_parent_ctx = None  # Will hold parent context for restoration
        _original_msg_context_ref = None  # Reference to the original msg_context dict
        ctx = _msg_ctx.get()
        if ctx is not None and event_message_id:
            if _interrupt_depth > 0 and ctx.get("event_message_id") != event_message_id:
                # BUG FIX (v0.15.4): We must keep a reference to the original
                _original_msg_context_ref = ctx.get("_original_msg_context_ref") or ctx
                _saved_parent_ctx = dict(ctx)  # Save a copy for restoration after orig()
                ctx = {
                    "message_id": event_message_id,
                    "chat_id": ctx.get("chat_id", ""),
                    "anchor_id": ctx.get("anchor_id"),
                    "event_message_id": event_message_id,
                    "card_sent": False,
                    "_msg_start_time": time.monotonic(),
                    "_agent_ref": None,
                    "_interrupt_depth": _interrupt_depth,
                    "_parent_message_id": ctx.get("message_id"),  # Track parent for cleanup
                    "_original_msg_context_ref": _original_msg_context_ref,  # Propagate ref to original
                }
                _msg_ctx.set(ctx)
                _thread_local_ctx.data = dict(ctx)

                # anchor_id fix: use event_message_id as the new card's
                try:
                    from .hooks import on_message_interrupted
                    on_message_interrupted(
                        message_id=_saved_parent_ctx.get("message_id", ""),
                        new_message_id=event_message_id,
                        chat_id=ctx["chat_id"],
                        anchor_id=event_message_id,
                    )
                except Exception:
                    _logger.debug("run_agent: interrupt hook failed", exc_info=True)

                # Fire START hook for the new (interrupted-into) message
                try:
                    from .hooks import on_message_started
                    on_message_started(
                        message_id=event_message_id,
                        chat_id=ctx["chat_id"],
                        anchor_id=event_message_id,
                    )
                except Exception:
                    _logger.warning("HLS: run_agent START hook failed", exc_info=True)
            else:
                ctx["event_message_id"] = event_message_id
            # Copy to thread-local for thread-pool workers
            _thread_local_ctx.data = dict(ctx)

        # Inject session_id into _msg_ctx so downstream plugins (mem0x etc.)
        # can read it via _get_context().  _wrap_handle_message_with_agent sets
        # _msg_ctx before session_id is known; this is the first point where
        # the gateway's session_id is available.
        ctx = _msg_ctx.get()
        if ctx is not None and session_id and not ctx.get("session_id"):
            ctx["session_id"] = session_id
            _thread_local_ctx.data = dict(ctx)

        # v1.3.4 fix (P1): 确保 orig() 抛异常时 _saved_parent_ctx 被恢复。
        try:
            result = await orig(
                self,
                message,
                context_prompt,
                history,
                source,
                session_id,
                session_key=session_key,
                run_generation=run_generation,
                _interrupt_depth=_interrupt_depth,
                event_message_id=event_message_id,
                channel_prompt=channel_prompt,
                **kwargs,
            )
        except BaseException:
            if _saved_parent_ctx is not None:
                _msg_ctx.set(_saved_parent_ctx)
                _thread_local_ctx.data = dict(_saved_parent_ctx)
            raise

        # ── 中断场景：先发子消息 COMPLETE，再发父消息 ABORTED ──
        ctx = _msg_ctx.get()
        if _saved_parent_ctx is not None:
            # Step 1: Fire B's (child) COMPLETE hook normally
            if ctx is not None:
                try:
                    from .hooks import on_message_completed

                    _elapsed_child = time.monotonic() - ctx.get("_msg_start_time", time.monotonic())
                    is_interrupted_child = result.get("interrupted", False) or result.get("partial", False)

                    _finish_reason_child = result.get("finish_reason", "")
                    _error_msg_child = result.get("error") or result.get("interrupt_message", "")
                    if _finish_reason_child and _finish_reason_child != "stop":
                        _logger.warning(
                            "lark-hls-v2 v%s: child non-stop finish_reason=%s model=%s msg=%s",
                            __version__,
                            _finish_reason_child,
                            result.get("model", "?"),
                            (ctx["message_id"] or "?")[:12],
                        )
                    if _error_msg_child:
                        _logger.warning(
                            "lark-hls-v2 v%s: child agent error: %s model=%s msg=%s",
                            __version__,
                            _error_msg_child[:200],
                            result.get("model", "?"),
                            (ctx["message_id"] or "?")[:12],
                        )

                    _agent_ref_child = ctx.get("_agent_ref")
                    cache_read_child = getattr(_agent_ref_child, "session_cache_read_tokens", 0) if _agent_ref_child else 0
                    cache_write_child = getattr(_agent_ref_child, "session_cache_write_tokens", 0) if _agent_ref_child else 0
                    reasoning_tokens = getattr(_agent_ref_child, "session_reasoning_tokens", 0) if _agent_ref_child else 0
                    estimated_cost_usd = getattr(_agent_ref_child, "session_estimated_cost_usd", 0) if _agent_ref_child else 0
                    cost_status = getattr(_agent_ref_child, "session_cost_status", "unknown") if _agent_ref_child else "unknown"

                    card_sent_child = on_message_completed(
                        message_id=ctx["message_id"],
                        answer=result.get("final_response", ""),
                        duration=_elapsed_child,
                        model=result.get("model", ""),
                        tokens={
                            "input_tokens": result.get("input_tokens", 0),
                            "output_tokens": result.get("output_tokens", 0),
                            "cache_read_tokens": cache_read_child,
                            "cache_write_tokens": cache_write_child,
                        },
                        context={
                            "used_tokens": result.get("last_prompt_tokens", 0),
                            "max_tokens": result.get("context_length", 0),
                        },
                        api_calls=result.get("api_calls", 0),
                        history_offset=result.get("history_offset", 0),
                        compression_exhausted=result.get("compression_exhausted", False),
                        aborted=is_interrupted_child,
                        error_message=_error_msg_child,
                        reasoning_tokens=reasoning_tokens,
                        estimated_cost_usd=estimated_cost_usd,
                        cost_status=cost_status,
                    )
                    if card_sent_child:
                        result["already_sent"] = True
                        ctx["card_sent"] = True
                        _logger.info(
                            "run_agent: child COMPLETE hook fired for msg=%s card_sent=True",
                            (ctx["message_id"] or "?")[:12],
                        )
                except Exception:
                    _logger.debug("run_agent: child COMPLETE hook failed", exc_info=True)

            try:
                # Step 2: Fire A's (parent) ABORTED COMPLETE
                from .hooks import on_message_completed
                on_message_completed(
                    message_id=_saved_parent_ctx["message_id"],
                    answer="",
                    duration=time.monotonic() - _saved_parent_ctx.get("_msg_start_time", time.monotonic()),
                    aborted=True,
                    error_message="Interrupted by new message",
                )
                _saved_parent_ctx["card_sent"] = True
                # BUG FIX (v0.15.4): Also set card_sent on the original
                if _original_msg_context_ref is not None:
                    _original_msg_context_ref["card_sent"] = True
                # Also mark already_sent so Hermes's gateway doesn't send text reply
                if isinstance(result, dict):
                    result["already_sent"] = True
            except Exception:
                _logger.debug("run_agent: parent ABORTED completion failed", exc_info=True)
        elif ctx is not None:
            # ── 正常（非中断）场景 ──
            try:
                from .hooks import on_message_completed

                _elapsed = time.monotonic() - ctx.get("_msg_start_time", time.monotonic())

                is_interrupted = result.get("interrupted", False) or result.get("partial", False)

                _finish_reason = result.get("finish_reason", "")
                _error_msg = result.get("error") or result.get("interrupt_message", "")
                if _finish_reason and _finish_reason != "stop":
                    _logger.warning(
                        "lark-hls-v2 v%s: non-stop finish_reason=%s model=%s msg=%s",
                        __version__,
                        _finish_reason,
                        result.get("model", "?"),
                        (ctx["message_id"] or "?")[:12],
                    )
                if _error_msg:
                    _logger.warning(
                        "lark-hls-v2 v%s: agent error: %s model=%s msg=%s",
                        __version__,
                        _error_msg[:200],
                        result.get("model", "?"),
                        (ctx["message_id"] or "?")[:12],
                    )

                _agent_ref = ctx.get("_agent_ref")
                cache_read = getattr(_agent_ref, "session_cache_read_tokens", 0) if _agent_ref else 0
                cache_write = getattr(_agent_ref, "session_cache_write_tokens", 0) if _agent_ref else 0
                reasoning_tokens = getattr(_agent_ref, "session_reasoning_tokens", 0) if _agent_ref else 0
                estimated_cost_usd = getattr(_agent_ref, "session_estimated_cost_usd", 0) if _agent_ref else 0
                cost_status = getattr(_agent_ref, "session_cost_status", "unknown") if _agent_ref else "unknown"

                card_sent = on_message_completed(
                    message_id=ctx["message_id"],
                    answer=result.get("final_response", ""),
                    duration=_elapsed,
                    model=result.get("model", ""),
                    tokens={
                        "input_tokens": result.get("input_tokens", 0),
                        "output_tokens": result.get("output_tokens", 0),
                        "cache_read_tokens": cache_read,
                        "cache_write_tokens": cache_write,
                    },
                    context={
                        "used_tokens": result.get("last_prompt_tokens", 0),
                        "max_tokens": result.get("context_length", 0),
                    },
                    api_calls=result.get("api_calls", 0),
                    history_offset=result.get("history_offset", 0),
                    compression_exhausted=result.get("compression_exhausted", False),
                    aborted=is_interrupted,
                    error_message=_error_msg,
                    reasoning_tokens=reasoning_tokens,
                    estimated_cost_usd=estimated_cost_usd,
                    cost_status=cost_status,
                )
                if card_sent:
                    result["already_sent"] = True
                    ctx["card_sent"] = True
            except Exception:
                _logger.warning("HLS: run_agent COMPLETE hook failed", exc_info=True)
        # _msg_ctx now points to the child message's context. We must
        if _saved_parent_ctx is not None:
            _msg_ctx.set(_saved_parent_ctx)
            _thread_local_ctx.data = dict(_saved_parent_ctx)

        return result

    return wrapper

# ▍_wrap_run_conversation -- wrap 流式回调 + persist_user_timestamp 兼容

def _wrap_run_conversation(orig: Callable) -> Callable:
    """Wrap all 6 streaming callbacks right before run_conversation executes."""
    # Lazy import to avoid circular dependency at module load time
    from .callbacks import _maybe_wrap_callbacks  # noqa: F811

    # v1.3.4 fix (P1): inspect.signature may raise for C extension / wrapped callable
    import inspect
    try:
        _has_persist_ts = "persist_user_timestamp" in inspect.signature(orig).parameters
    except (ValueError, TypeError):
        _has_persist_ts = False

    @functools.wraps(orig)
    def wrapper(
        self,
        user_message,
        system_message=None,
        conversation_history=None,
        task_id=None,
        stream_callback=None,
        persist_user_message=None,
        persist_user_timestamp=None,
        **kwargs,
    ):
        _maybe_wrap_callbacks(self)
        try:
            # 用关键字参数传递，兼容有/无 persist_user_timestamp 的 Hermes 版本
            call_kwargs = {
                "system_message": system_message,
                "conversation_history": conversation_history,
                "task_id": task_id,
                "stream_callback": stream_callback,
                "persist_user_message": persist_user_message,
            }
            if _has_persist_ts:
                call_kwargs["persist_user_timestamp"] = persist_user_timestamp
            call_kwargs.update(kwargs)
            return orig(self, user_message, **call_kwargs)
        finally:
            pass

    return wrapper

# ▍_wrap_run_background_task -- /background 任务的 START/COMPLETE 钩子

def _wrap_run_background_task(orig: Callable) -> Callable:
    """Inject START/COMPLETE hooks for /background tasks so they get streaming cards."""

    @functools.wraps(orig)
    async def wrapper(self, prompt, source, task_id, **kwargs):
        # Only intercept Feishu platform
        platform_name = getattr(getattr(source, "platform", None), "value", "").lower()
        if platform_name not in ("feishu", "lark"):
            return await orig(self, prompt, source, task_id, **kwargs)

        chat_id = getattr(source, "chat_id", "")

        # Set up message context so _maybe_wrap_callbacks works
        _msg_ctx.set({
            "message_id": task_id,
            "chat_id": chat_id,
            "anchor_id": None,  # No reply anchor for background tasks
            "event_message_id": task_id,  # Use task_id so callbacks find a valid eid
            "card_sent": False,
            "_msg_start_time": time.monotonic(),
            "_agent_ref": None,  # Will be filled by _maybe_wrap_callbacks
        })
        _thread_local_ctx.data = dict(_msg_ctx.get())

        # ── Fire START hook ──
        try:
            from .hooks import on_message_started
            on_message_started(message_id=task_id, chat_id=chat_id, anchor_id=None)
        except Exception:
            _logger.debug("background task START hook failed", exc_info=True)

        # ── Wrap adapter.send to suppress duplicate text delivery ──
        adapter = None
        original_send = None

        try:
            if hasattr(self, "adapters") and source.platform:
                adapter = self.adapters.get(source.platform)
        except Exception:
            _logger.warning("HLS: bg-task adapter fetch failed", exc_info=True)
        if adapter:
            original_send = adapter.send

            async def _intercepting_send(chat_id, content, **send_kwargs):
                """Suppress plain text delivery when our card was sent."""
                ctx = _msg_ctx.get()
                if ctx and ctx.get("card_sent"):
                    try:
                        from gateway.platforms.base import SendResult
                        return SendResult(success=True)
                    except Exception:
                        return None
                return await original_send(chat_id, content, **send_kwargs)

            adapter.send = _intercepting_send
            adapter._hls_bg_sending = getattr(adapter, '_hls_bg_sending', 0) + 1

        # v1.3.4 fix (P1): orig() + COMPLETE hook 都在 try 块内，finally 清理
        try:
            result = await orig(self, prompt, source, task_id, **kwargs)

            # ── Fire COMPLETE hook ──
            ctx = _msg_ctx.get()
            if ctx is not None:
                try:
                    from .hooks import on_message_completed

                    _elapsed = time.monotonic() - ctx.get("_msg_start_time", time.monotonic())

                    _agent_ref = ctx.get("_agent_ref")
                    cache_read = getattr(_agent_ref, "session_cache_read_tokens", 0) if _agent_ref else 0
                    cache_write = getattr(_agent_ref, "session_cache_write_tokens", 0) if _agent_ref else 0
                    reasoning_tokens = getattr(_agent_ref, "session_reasoning_tokens", 0) if _agent_ref else 0
                    estimated_cost_usd = getattr(_agent_ref, "session_estimated_cost_usd", 0) if _agent_ref else 0
                    cost_status = getattr(_agent_ref, "session_cost_status", "unknown") if _agent_ref else "unknown"

                    card_sent = on_message_completed(
                        message_id=task_id,
                        answer=(result or {}).get("final_response", ""),
                        duration=_elapsed,
                        model=(result or {}).get("model", ""),
                        tokens={
                            "input_tokens": (result or {}).get("input_tokens", 0),
                            "output_tokens": (result or {}).get("output_tokens", 0),
                            "cache_read_tokens": cache_read,
                            "cache_write_tokens": cache_write,
                        },
                        context={
                            "used_tokens": (result or {}).get("last_prompt_tokens", 0),
                            "max_tokens": (result or {}).get("context_length", 0),
                        },
                        api_calls=(result or {}).get("api_calls", 0),
                        history_offset=(result or {}).get("history_offset", 0),
                        compression_exhausted=(result or {}).get("compression_exhausted", False),
                        aborted=False,
                        error_message=(result or {}).get("error") or "",
                        reasoning_tokens=reasoning_tokens,
                        estimated_cost_usd=estimated_cost_usd,
                        cost_status=cost_status,
                    )

                    if card_sent:
                        ctx["card_sent"] = True
                        if result is not None and isinstance(result, dict):
                            result["_hls_card_sent"] = True
                except Exception:
                    _logger.debug("background task COMPLETE hook failed", exc_info=True)

            return result
        finally:
            if original_send and adapter:
                adapter.send = original_send
                adapter._hls_bg_sending = getattr(adapter, '_hls_bg_sending', 0) - 1
            # v1.3.4 fix (P1): clear context in finally -- runs on ALL paths
            _msg_ctx.set(None)
            _thread_local_ctx.data = None

    return wrapper

# ▍_wrap_cron_deliver -- cron 推送重定向到 CardKit 卡片

def _wrap_cron_deliver(orig: Callable) -> Callable:
    """Intercept cron _deliver_result and redirect Feishu deliveries to CardKit cards."""

    @functools.wraps(orig)
    def wrapper(job, content, adapters=None, loop=None, **kwargs):
        # Only intercept when there are adapters with a Feishu/Lark platform
        if not adapters:
            return orig(job, content, adapters=adapters, loop=loop, **kwargs)

        feishu_adapter = None

        try:
            from gateway.config import Platform

            for p in list(adapters.keys()):
                pn = p.value.lower() if hasattr(p, "value") else str(p).lower()
                if pn in ("feishu", "lark"):
                    feishu_adapter = adapters[p]
                    break
        except Exception:
            pass

        if feishu_adapter is None:
            return orig(job, content, adapters=adapters, loop=loop, **kwargs)

        _logger.info(
            "lark-hls-v2 v%s: cron delivery intercepted, redirecting to card (job=%s)",
            __version__,
            job.get("id", "?")[:12],
        )

        # ── Temporarily replace Feishu adapter.send with card-sending version ──
        original_send = feishu_adapter.send

        async def _card_sending_send(chat_id, content, *, metadata=None, **send_kwargs):
            """Redirect Feishu adapter.send to CardKit card delivery.

            ⚠️ 只拦截 cron 投递（metadata 含 job_id），agent 对话走原始路径。
            _wrap_cron_deliver 替换的是实例级 adapter.send，cron 投递期间
            （最长60s）所有调用都进这里。不区分会导致 agent 响应被套上
            cron 卡片模板（绿色 header + "定时任务" footer）。
            """
            # ── 区分调用来源：cron 投递 vs agent 对话 ──
            _md = metadata or {}
            if "job_id" not in _md:
                # 不是 cron 投递，走原始 adapter.send（agent 对话路径）
                return await original_send(chat_id, content, metadata=metadata, **send_kwargs)
            try:
                from ..controller import get_controller
                ctrl = get_controller()
                _logger.info(
                    "cron _card_sending_send: ctrl.enabled=%s chat=%s content_len=%d",
                    ctrl.enabled,
                    chat_id[:12] if chat_id else "?",
                    len(content) if content else 0,
                )
                if ctrl.enabled and content:
                    cleaned = content
                    if not cleaned.strip():
                        cleaned = content

                    # 从 job dict 提取元数据
                    job_name = job.get("name", "") or job.get("id", "")[:12]
                    job_id = job.get("id", "")
                    schedule = job.get("schedule")
                    failure_streak = int(job.get("failure_streak", 0))
                    last_error = job.get("last_error", "")

                    # 从 content 检测状态（最高优先级）
                    from ..card.special import _detect_cron_status
                    status = _detect_cron_status(cleaned)

                    await ctrl._do_cron_deliver(
                        chat_id, cleaned.strip(),
                        job_name=job_name,
                        job_id=job_id,
                        status=status,
                        schedule=schedule,
                        failure_streak=failure_streak,
                        last_error=last_error,
                    )

                    _logger.info(
                        "lark-hls-v2 v%s: cron card delivered: chat=%s status=%s",
                        __version__,
                        chat_id[:12],
                        status,
                    )
                    try:
                        from gateway.platforms.base import SendResult
                        return SendResult(success=True)
                    except Exception:
                        return None
            except Exception:
                pass

            # Fallback: send plain text via the original adapter
            return await original_send(chat_id, content, **send_kwargs)

        feishu_adapter.send = _card_sending_send
        feishu_adapter._hls_cron_sending = getattr(feishu_adapter, '_hls_cron_sending', 0) + 1
        try:
            return orig(job, content, adapters=adapters, loop=loop, **kwargs)
        finally:
            feishu_adapter.send = original_send
            feishu_adapter._hls_cron_sending = getattr(feishu_adapter, '_hls_cron_sending', 0) - 1

    return wrapper
