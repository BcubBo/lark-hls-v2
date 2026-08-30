# ================================================================
# lark-hls-v2 · controller.py 总导游图（改代码前必读，读完再动手）
# ▍这是什么（四问）
# ① 干什么：流式卡片控制器 — 插件的入口门面。接收 Hermes 的生命周期事件（消息开始/思考/推理/工具/回答/完成/中断/终止），分发到对应处理逻辑。
# ② 技术栈：asyncio + threading 混合模型。session 管理用 threading.RLock 保护，异步操作用 fire-and-forget。
# ③ 依赖：card_flow.py (UnifiedControllerMixin 提供卡片生命周期)、config、state、feishu 客户端。
# ④ 给谁看：接入新事件、改 session 管理逻辑、排查中断/续接问题的人。
#
# ▍文件从上到下的结构
# StreamCardController(UnifiedControllerMixin) — 单例控制器
#   ├─ __init__ — 初始化 Config/FeishuClient/session 存储/interrupt_map/continuation_map
#   ├─ Session CRUD — _sess_get/put/pop/items_snapshot/active_count/clear (线程安全)
#   ├─ FeishuClient init — _ensure_init (double-check locking)
#   ├─ Continuation — delegate_task 续接机制，旧 session streaming_closed 后创建新 session
#   ├─ Cleanup — _cleanup / _reset_session_state / _prune_stale_sessions
#   ├─ Public hooks — on_message_started / on_thinking / on_reasoning / on_tool_update / on_answer
#   │                 on_aborted / on_interrupted / on_completed
#   ├─ Completion — _dispatch_completion / _complete_with_fallback / _send_text_fallback
#   └─ Delivery — on_cron_deliver_async / _do_gateway_deliver / _do_gateway_card_update
# get_controller() — 模块级单例工厂
#
# ▍修改铁律（血泪教训）
# 1. session 存储用 RLock 保护 — 异步回调和定时器可能并发访问。
# 2. interrupt_map 有上限 (200) — 超限淘汰旧条目，防止内存泄漏。
# 3. on_message_started 会中断同 chat 的旧 session — 改了会影响多轮对话。
# 4. continuation 机制: streaming_closed 的旧 session 可被续接，创建新卡片继续回答。
# 5. on_completed 先查 continuation_map 再查 direct session 再查 interrupt_map — 三层查找。
# 6. _send_text_fallback 是卡片不可用时的兜底 — 文本限制 4000 字。
# 7. 单例模式: get_controller() 用全局 _controller 变量，非线程安全。
#
# ▍特殊机制
# "中断 + 续接"双表: interrupt_map(旧→新消息ID) + continuation_map(旧→续接消息ID)。
# 同一旧消息只能续接一次 (_continuation_reactivation_count 限制)。
#
# ▍更新记录
# *v2 fork: 从原版 controller.py 重构，拆分 card_flow 到独立模块*
# ================================================================

"""StreamCardController v2 — thin orchestrator composing lifecycle + card + flush."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Callable, Coroutine
from typing import Any

from .config import Config
from .state.phase import (
    CardPhase,
    TerminalReason,
    TERMINAL_PHASES,
    _TERMINAL,
    is_legal_transition,
)
from .state.session import CardSession
from .state.linear import UnifiedLinearState
from .state.text import TextState, strip_reasoning_tags
from .state.tooluse import ToolUseTracker
from .feishu import (
    FeishuClient,
    FeishuClientConfig,
    FeishuAPIError,
    CARDKIT_STREAMING_CLOSED,
    CARDKIT_SCHEMA_ERROR,
    is_element_not_found_error,
    is_schema_error,
    is_terminal_api_code,
    UnavailableGuard,
)
from .flush import FlushController
from .card_flow import UnifiedControllerMixin

_logger = logging.getLogger("lark_hls_v2")

# State constants
IDLE = CardPhase.IDLE
CREATING = CardPhase.CREATING
STREAMING = CardPhase.STREAMING
COMPLETING = CardPhase.COMPLETING
COMPLETED = CardPhase.COMPLETED
CREATION_FAILED = CardPhase.CREATION_FAILED
TERMINATED = CardPhase.TERMINATED
ABORTED = CardPhase.ABORTED

# v1.3.2: module-level constant
_INTERRUPT_MAP_MAX = 200
_CONTINUATION_MAP_MAX = 100


class StreamCardController(UnifiedControllerMixin):
    """流式卡片控制器 v2 — 组合 lifecycle + card + flush 模块."""

    def __init__(self) -> None:
        self._cfg = Config()
        self._client: FeishuClient | None = None
        self._initialized = False
        self._init_lock = threading.Lock()
        self._session_ttl = self._cfg.card_duration_sec
        self._loop: asyncio.AbstractEventLoop | None = None
        self._pending_tasks: set[asyncio.Task] = set()

        # Session storage
        self._sessions: dict[str, CardSession] = {}
        self._sessions_lock = threading.RLock()

        # Interrupt map (old_msg_id → new_msg_id)
        self._interrupt_map: dict[str, str] = {}
        self._interrupt_map_lock = threading.Lock()

        # Continuation map (old_msg_id → new_cont_msg_id)
        self._continuation_map: dict[str, str] = {}
        self._continuation_map_lock = threading.Lock()

    # ── Session CRUD ────────────────────────────────────────────────

    def _sess_get(self, message_id: str) -> CardSession | None:
        with self._sessions_lock:
            return self._sessions.get(message_id)

    def _sess_put(self, key: str, session: CardSession) -> None:
        with self._sessions_lock:
            self._sessions[key] = session

    def _sess_pop(self, key: str) -> CardSession | None:
        with self._sessions_lock:
            return self._sessions.pop(key, None)

    def _sess_items_snapshot(self) -> list[tuple[str, CardSession]]:
        with self._sessions_lock:
            return list(self._sessions.items())

    def _sess_active_count(self) -> int:
        with self._sessions_lock:
            return sum(1 for s in self._sessions.values() if not s.is_terminal_phase)

    def _sess_clear(self) -> None:
        with self._sessions_lock:
            self._sessions.clear()

    @property
    def enabled(self) -> bool:
        return self._cfg.enabled and bool(self._cfg.feishu_app_id or self._cfg.env_app_id)

    # ── FeishuClient init ───────────────────────────────────────────

    async def _ensure_init(self) -> None:
        if self._initialized:
            return
        with self._init_lock:
            if self._initialized:
                return
            app_id = self._cfg.feishu_app_id or self._cfg.env_app_id
            app_secret = self._cfg.feishu_app_secret or self._cfg.env_app_secret
            if not app_id or not app_secret:
                raise RuntimeError("feishu credentials not configured")
            self._client = FeishuClient(
                FeishuClientConfig(
                    app_id=app_id,
                    app_secret=app_secret,
                    base_url=self._cfg.feishu_base_url,
                )
            )
            self._initialized = True

    def _client_ok(self) -> bool:
        return self._initialized and self._client is not None

    def _get_loop(self) -> asyncio.AbstractEventLoop | None:
        """Get event loop -- prefer running loop, then cached, then get_event_loop."""
        try:
            loop = asyncio.get_running_loop()
            self._loop = loop
            return loop
        except RuntimeError:
            pass
        if self._loop is not None and not self._loop.is_closed():
            return self._loop
        try:
            loop = asyncio.get_event_loop()
            self._loop = loop
            return loop
        except RuntimeError:
            return None

    def _get_active_session(self, message_id: str) -> CardSession | None:
        session = self._sess_get(message_id)
        if session is None or session.is_terminal_phase:
            return None
        return session

    def _fire_and_forget(self, coro: Coroutine[Any, Any, Any], loop: asyncio.AbstractEventLoop) -> None:
        """fire-and-forget -- holds Task strong reference to prevent GC.
        Two paths: loop.create_task (same thread) / run_coroutine_threadsafe (cross thread).
        """
        try:
            task = loop.create_task(coro)
            self._pending_tasks.add(task)
            task.add_done_callback(self._pending_tasks.discard)
        except RuntimeError:
            try:
                fut = asyncio.run_coroutine_threadsafe(coro, loop)
                fut.add_done_callback(self._on_bg_task_done)
            except Exception:
                coro.close()

    @staticmethod
    def _on_bg_task_done(fut):
        try:
            fut.result()
        except Exception:
            _logger.warning("background task failed", exc_info=True)

    # ── Continuation (delegate_task) ────────────────────────────────

    def _resolve_continuation_id(self, message_id: str) -> str | None:
        with self._continuation_map_lock:
            return self._continuation_map.get(message_id)

    def _register_continuation(self, old_message_id: str, new_message_id: str) -> None:
        with self._continuation_map_lock:
            self._continuation_map[old_message_id] = new_message_id
            if len(self._continuation_map) > _CONTINUATION_MAP_MAX:
                excess = len(self._continuation_map) - _CONTINUATION_MAP_MAX
                for old_key in list(self._continuation_map.keys())[:excess]:
                    self._continuation_map.pop(old_key, None)

    def _pop_continuation_id(self, message_id: str) -> str | None:
        with self._continuation_map_lock:
            return self._continuation_map.pop(message_id, None)

    def _maybe_reactivate_for_continuation(self, message_id: str) -> str | None:
        existing = self._resolve_continuation_id(message_id)
        if existing is not None:
            return existing

        stale = self._sess_get(message_id)
        if stale is None:
            return None
        if stale.is_terminal_phase:
            return None
        if not stale._streaming_closed:
            return None
        if stale._is_continuation:
            return None
        if stale._continuation_reactivation_count >= 1:
            return None

        new_session = self._reactivate_session_for_continuation(stale)
        if new_session is None:
            return None
        self._register_continuation(message_id, new_session.message_id)
        return new_session.message_id

    def _reactivate_session_for_continuation(self, stale_session: CardSession) -> CardSession | None:
        chat_id = stale_session.chat_id
        anchor_id = stale_session.anchor_id or stale_session.message_id
        if not chat_id or not anchor_id:
            return None

        loop = self._get_loop()
        if loop is None:
            return None

        stale_session._continuation_reactivation_count += 1
        seq = stale_session._continuation_reactivation_count
        new_message_id = f"{anchor_id}-cont-{seq}"

        with self._sessions_lock:
            if new_message_id in self._sessions:
                return None

        new_session = CardSession(new_message_id, chat_id, loop)
        new_session.anchor_id = anchor_id if anchor_id != new_message_id else None
        new_session._is_continuation = True
        new_session.linear = True
        new_session.unified_state = UnifiedLinearState()
        self._sess_put(new_message_id, new_session)

        self._fire_and_forget(self._do_create_linear_card(new_session), loop)

        try:
            if not stale_session.is_terminal_phase and stale_session.state != COMPLETING:
                stale_session.set_state(COMPLETING, source="continuation")
                self._fire_and_forget(
                    self._complete_with_fallback(stale_session),
                    stale_session._loop,
                )
        except Exception:
            pass

        return new_session

    # ── Cleanup ─────────────────────────────────────────────────────

    def _cleanup(self, message_id: str) -> None:
        session = self._sess_pop(message_id)
        if session is None:
            return
        anchor = getattr(session, "anchor_id", None)
        if anchor:
            with self._sessions_lock:
                if self._sessions.get(anchor) is session:
                    del self._sessions[anchor]
        with self._interrupt_map_lock:
            stale_keys = [k for k, v in self._interrupt_map.items() if v == message_id]
            for k in stale_keys:
                del self._interrupt_map[k]
        with self._continuation_map_lock:
            self._continuation_map.pop(message_id, None)
            stale_cont_keys = [k for k, v in self._continuation_map.items() if v == message_id]
            for k in stale_cont_keys:
                del self._continuation_map[k]
        session.flush.mark_completed()

    def _reset_session_state(self, session: CardSession) -> None:
        """Reset heavy session data after card finalization. Session keeps minimal metadata for _prune_stale_sessions."""
        session.unified_state = None
        if session.text is not None:
            session.text = TextState()
        session.tool_use = ToolUseTracker()
        session.footer = {}

    def _prune_stale_sessions(self) -> None:
        now = time.time()
        for mid, s in self._sess_items_snapshot():
            if mid is None:
                continue
            age = now - s.created_at
            if age <= self._session_ttl:
                continue
            if s.is_terminal_phase:
                self._cleanup(mid)
            elif age > 2 * self._session_ttl:
                _logger.warning(
                    "prune: force-terminating stale non-terminal session "
                    "state=%s age=%.0fs msg=%s",
                    s.state, age, (mid or "?")[:12],
                )
                s.set_state(
                    TERMINATED, source="_prune_stale_sessions",
                    reason=TerminalReason.UNAVAILABLE, terminal=True,
                )
                self._cleanup(mid)

    # ── Public hook entry points ────────────────────────────────────

    def on_message_started(
        self, *, message_id: str | None, chat_id: str, anchor_id: str | None = None,
    ) -> None:
        if not self.enabled:
            return
        if not message_id:
            return
        if self._sess_get(message_id) is not None:
            return

        self._prune_stale_sessions()

        seen_sessions: set[int] = set()
        for existing_msg_id, existing_session in self._sess_items_snapshot():
            if existing_session.chat_id != chat_id:
                continue
            if existing_session.is_terminal_phase:
                continue
            if existing_msg_id == message_id:
                continue
            if id(existing_session) in seen_sessions:
                continue
            seen_sessions.add(id(existing_session))
            try:
                self.on_interrupted(
                    old_message_id=existing_msg_id,
                    new_message_id=message_id,
                    chat_id=chat_id,
                    anchor_id=anchor_id,
                )
            except Exception:
                pass

        loop = self._get_loop()
        if loop is None:
            return

        existing = self._sess_get(message_id)
        if existing is not None:
            if not existing._card_ready.is_set():
                self._fire_and_forget(self._do_create_linear_card(existing), loop)
            return

        session = CardSession(message_id, chat_id, loop)
        self._sess_put(message_id, session)
        if anchor_id and anchor_id != message_id:
            session.anchor_id = anchor_id
            self._sess_put(anchor_id, session)

        self._fire_and_forget(self._do_create_linear_card(session), loop)

    def on_thinking(self, *, message_id: str, text: str) -> None:
        if not self.enabled:
            return
        session = self._get_active_session(message_id)
        if session is None or session.guard.should_skip("on_thinking"):
            return

        from .card.elements import _LOADING_HINT_ELEMENT_ID

        if not session._thinking_hint_upgraded and _LOADING_HINT_ELEMENT_ID in session.existing_elements:
            session._thinking_hint_upgraded = True
            try:
                loop = self._get_loop()
                if loop is not None and not loop.is_closed():
                    self._fire_and_forget(self._upgrade_loading_hint_to_thinking(session), loop)
            except Exception:
                pass

        self._linear_on_thinking(session, text)

    def on_reasoning(self, *, message_id: str, text: str) -> None:
        if not self.enabled:
            return
        if not self._cfg.show_reasoning:
            return
        session = self._get_active_session(message_id)
        if session is None or session.guard.should_skip("on_reasoning"):
            return

        epoch = session.create_epoch
        if session.is_stale_create(epoch):
            return

        if session.unified_state is None:
            return
        session.unified_state.on_reasoning_delta(text)
        self._schedule_linear_flush(session)

    def on_tool_update(
        self, *, message_id: str, tool_name: str, status: str, detail: str = "",
    ) -> None:
        if not self.enabled:
            return
        session = self._get_active_session(message_id)
        if session is None or session.guard.should_skip("on_tool_update"):
            return

        epoch = session.create_epoch
        if session.is_stale_create(epoch):
            return

        if status in ("running", "started", "tool.started"):
            session.tool_use.record_start(tool_name, detail)
        else:
            is_error = status in ("error", "failed")
            session.tool_use.record_end(
                tool_name,
                error=detail if is_error else "",
                output="" if is_error else detail,
            )

        if session.unified_state is None:
            return
        is_new_tool = status in ("running", "started", "tool.started")
        session.unified_state.on_tool_event(is_new_tool=is_new_tool)
        self._schedule_linear_flush(session)

    def on_answer(self, *, message_id: str, text: str) -> None:
        if not self.enabled:
            return

        if text:
            new_id = self._maybe_reactivate_for_continuation(message_id)
            if new_id is not None:
                message_id = new_id

        session = self._get_active_session(message_id)
        if session is None or session.guard.should_skip("on_answer"):
            return

        epoch = session.create_epoch
        if session.is_stale_create(epoch):
            return

        if session._first_answer_time == 0.0:
            session._first_answer_time = time.monotonic()

        answer_text = strip_reasoning_tags(text)
        if answer_text:
            if session.unified_state is None:
                return
            session.unified_state.on_answer_delta(answer_text)
            self._schedule_linear_flush(session)

    def on_aborted(self, *, message_id: str) -> None:
        if not self.enabled:
            return
        session = self._get_active_session(message_id)
        if session is None:
            return

        if session.state == COMPLETING:
            session._was_aborted = True
            return

        session._was_aborted = True
        if not session.set_state(
            ABORTED, source="on_aborted",
            reason=TerminalReason.ABORT, terminal=True,
        ):
            return
        session.flush.mark_completed()
        self._dispatch_completion(session)

    def on_interrupted(
        self, *, old_message_id: str, new_message_id: str, chat_id: str,
        anchor_id: str | None = None,
    ) -> None:
        if not self.enabled:
            return

        old_session = self._get_active_session(old_message_id)
        if old_session is not None:
            if old_session.state == COMPLETING:
                pass
            else:
                old_session._was_aborted = True
                old_session.error_message = "Interrupted by new message"

                if old_session.flush._flush_in_progress:
                    loop = self._get_loop()
                    if loop is not None:
                        async def _wait_and_abort():
                            try:
                                await asyncio.wait_for(
                                    old_session.flush.wait_for_flush(), timeout=3.0,
                                )
                            except (asyncio.TimeoutError, Exception):
                                pass
                            if old_session.state in (COMPLETING, *TERMINAL_PHASES):
                                return
                            if not old_session.set_state(
                                ABORTED, source="_wait_and_abort",
                                reason=TerminalReason.ABORT, terminal=True,
                            ):
                                return
                            old_session.flush.mark_completed()
                            self._dispatch_completion(old_session)
                        self._fire_and_forget(_wait_and_abort(), loop)
                    else:
                        if old_session.set_state(
                            ABORTED, source="on_interrupted",
                            reason=TerminalReason.ABORT, terminal=True,
                        ):
                            old_session.flush.mark_completed()
                            self._dispatch_completion(old_session)
                else:
                    if old_session.set_state(
                        ABORTED, source="on_interrupted",
                        reason=TerminalReason.ABORT, terminal=True,
                    ):
                        old_session.flush.mark_completed()
                        self._dispatch_completion(old_session)

        if self._sess_get(new_message_id) is None:
            loop = self._get_loop()
            if loop is not None:
                reply_anchor_id = anchor_id if anchor_id and anchor_id != new_message_id else None
                session = CardSession(new_message_id, chat_id, loop)
                session.anchor_id = reply_anchor_id
                self._sess_put(new_message_id, session)
                if reply_anchor_id:
                    self._sess_put(reply_anchor_id, session)
                self._fire_and_forget(self._do_create_linear_card(session), loop)

        with self._interrupt_map_lock:
            self._interrupt_map[old_message_id] = new_message_id
            for key, val in list(self._interrupt_map.items()):
                if val == old_message_id:
                    self._interrupt_map[key] = new_message_id
            if len(self._interrupt_map) > _INTERRUPT_MAP_MAX:
                excess = len(self._interrupt_map) - _INTERRUPT_MAP_MAX
                for old_key in list(self._interrupt_map.keys())[:excess]:
                    self._interrupt_map.pop(old_key, None)

    def on_completed(
        self, *, message_id: str | None, answer: str = "", duration: float = 0.0,
        model: str = "", tokens: dict | None = None, context: dict | None = None,
        api_calls: int = 0, history_offset: int = 0, compression_exhausted: bool = False,
        aborted: bool = False, error_message: str = "", reasoning_tokens: int = 0,
        estimated_cost_usd: float = 0.0, cost_status: str = "unknown",
    ) -> bool:
        """完成事件: 收集 footer 数据 (duration/model/tokens/context) -> COMPLETING -> 封卡。
        三层查找: continuation_map -> direct session -> interrupt_map。
        """
        if not self.enabled:
            return False
        if not message_id:
            return False

        cont_id = self._pop_continuation_id(message_id)
        if cont_id is not None:
            message_id = cont_id

        direct_session = self._sess_get(message_id)
        if direct_session is not None and direct_session.state in (COMPLETING, COMPLETED):
            return True

        session = self._get_active_session(message_id)
        if session is None:
            with self._interrupt_map_lock:
                redirected_id = self._interrupt_map.pop(message_id, None)
            if redirected_id is not None:
                redir_session = self._sess_get(redirected_id)
                if redir_session is not None and redir_session.state in (COMPLETING, COMPLETED):
                    return True
                session = self._get_active_session(redirected_id)
            if session is None:
                return False
            message_id = redirected_id or message_id

        if session.state in (CREATION_FAILED, TERMINATED):
            self._cleanup(message_id)
            return False

        if answer:
            session.text.on_deliver(answer)
            if session.linear and session.unified_state is not None:
                clean_answer = strip_reasoning_tags(answer)
                if clean_answer:
                    _existing = session.unified_state.answer_text
                    _existing_len = len(_existing)
                    _clean_len = len(clean_answer)
                    if _existing_len == 0:
                        session.unified_state.on_answer_delta(clean_answer)
                    elif _clean_len > _existing_len and clean_answer[:_existing_len] == _existing:
                        _diff = clean_answer[_existing_len:]
                        if _diff:
                            session.unified_state.on_answer_delta(_diff)
                    elif _clean_len > _existing_len and clean_answer[:_existing_len] != _existing:
                        session.unified_state.answer_text = clean_answer
                        session.unified_state.answer_dirty = True

        if error_message:
            session.error_message = error_message
        if aborted:
            session._was_aborted = True

        session.footer = {
            "duration": duration,
            "model": model,
            **({} if not tokens else {}),
            **({} if not context else {}),
        }
        if tokens:
            session.footer.update({
                k: v for k, v in {
                    "input_tokens": tokens.get("input_tokens"),
                    "output_tokens": tokens.get("output_tokens"),
                    "cache_read_tokens": tokens.get("cache_read_tokens"),
                    "cache_write_tokens": tokens.get("cache_write_tokens"),
                    "reasoning_tokens": reasoning_tokens,
                }.items() if v
            })
        if context:
            session.footer.update({
                k: v for k, v in {
                    "context_used": context.get("used_tokens"),
                    "context_max": context.get("max_tokens"),
                }.items() if v
            })
        if api_calls:
            session.footer["api_calls"] = api_calls
        if history_offset:
            session.footer["history_offset"] = history_offset
        if compression_exhausted:
            session.footer["compression_exhausted"] = compression_exhausted
        if estimated_cost_usd:
            session.footer["estimated_cost_usd"] = estimated_cost_usd
        if cost_status and cost_status != "unknown":
            session.footer["cost_status"] = cost_status

        session.set_state(COMPLETING, source="on_completed")
        self._dispatch_completion(session)
        return True

    def defer_background_review(
        self, *, message_id: str, text: str, sender: Callable[[str], Any],
    ) -> bool:
        """后台审查: 延迟发送的审查内容，写入 unified_state 或 deferred 列表。"""
        if not self.enabled or not text or not callable(sender):
            return False
        session = self._get_active_session(message_id)
        if session is None:
            return False

        if session.linear and session.unified_state:
            session.unified_state.on_background_review(text)
            self._schedule_linear_flush(session)
            return True

        with session.deferred_background_review_lock:
            if session.deferred_background_review_closed:
                return False
            session.deferred_background_reviews.append((text, sender))
        return True

    def _flush_deferred_background_reviews(self, session: CardSession) -> None:
        lock = getattr(session, "deferred_background_review_lock", None)
        reviews = getattr(session, "deferred_background_reviews", None)
        if lock is None or reviews is None:
            return
        with lock:
            session.deferred_background_review_closed = True
            pending = list(reviews)
            reviews.clear()
        for text, sender in pending:
            try:
                sender(text)
            except Exception:
                pass

    # ── Completion paths (delegate to card_flow) ─────────────────

    def _dispatch_completion(self, session: CardSession) -> None:
        """分发封卡: 通过 fire_and_forget 异步执行 _complete_with_fallback。"""
        if session._completion_dispatched:
            return
        session._completion_dispatched = True
        self._fire_and_forget(self._complete_with_fallback(session), session._loop)

    async def _complete_with_fallback(self, session: CardSession) -> None:
        """封卡 + 文本兜底: _complete_card_flow 失败时发送纯文本回复。
        ⚠️ fallback_text 在调用前快照 — 防止 _reset_session_state 清除数据。
        """
        # Snapshot fallback text before _complete_card_flow potentially releases it
        _fallback_text = ""
        if session.error_message:
            _fallback_text = session.error_message
        elif session.unified_state and session.unified_state.answer_text:
            _fallback_text = session.unified_state.answer_text
        elif session.text and session.text.display_text:
            _fallback_text = session.text.display_text

        try:
            result = await self._complete_card_flow(session)
            if not result:
                await self._send_text_fallback(session, fallback_text=_fallback_text)
        except Exception:
            _logger.warning(
                "linear complete with fallback failed: msg=%s",
                (session.message_id or "?")[:12],
                exc_info=True,
            )
            await self._send_text_fallback(session, fallback_text=_fallback_text)

    async def _send_text_fallback(self, session: CardSession, *, fallback_text: str = "") -> None:
        """文本兜底: 卡片不可用时回复纯文本。限制 4000 字，会做 markdown 优化。"""
        if not self._client:
            return
        try:
            # 优先使用调用方传入的 fallback_text（在 _reset_session_state 前快照的）
            # 其次从 session 读取（用于 _complete_with_fallback 以外的调用路径）
            text = fallback_text or session.error_message or (session.text.display_text if session.text else "") or ""
            if not text.strip():
                return
            # 限制长度避免过长
            if len(text) > 4000:
                text = text[:4000] + "..."
            from .card.md import optimize_markdown_style
            content = optimize_markdown_style(text) or text
            reply_id = session.anchor_id or session.message_id
            await self._client.reply_text(reply_id, content)
            _logger.info(
                "text fallback sent: msg=%s len=%d",
                (session.message_id or "?")[:12],
                len(content),
            )
        except Exception:
            _logger.exception('send_text_fallback failed')

    # ── card_flow methods are inherited from UnifiedControllerMixin ──
    # _do_create_linear_card, _schedule_linear_flush, _do_unified_flush,
    # _upgrade_loading_hint_to_thinking, _linear_on_thinking,
    # _finalize_card, _complete_card_flow are all implemented in card_flow.py

    async def _do_cron_deliver(
        self,
        chat_id: str,
        content: str,
        *,
        job_name: str = "",
        job_id: str = "",
        status: str = "success",
        schedule: dict | None = None,
        failure_streak: int = 0,
        last_error: str = "",
    ) -> None:
        """Cron 投递 — 发送卡片到指定 chat。新参数全部有默认值，向后兼容。"""
        from .card import build_cron_card
        _logger.info("cron _do_cron_deliver: chat=%s content_len=%d job=%s status=%s", chat_id[:12], len(content), job_name, status)
        await self._ensure_init()
        assert self._client is not None
        card = build_cron_card(
            content,
            job_name=job_name,
            status=status,
            job_id=job_id,
            schedule=schedule,
            failure_streak=failure_streak,
            last_error=last_error,
        )
        await self._client.send_card_to_chat(chat_id, card)

    async def _do_gateway_deliver(
        self,
        chat_id: str,
        content: str,
        *,
        category: str = "",
    ) -> tuple[str | None, str | None]:
        """Send a gateway-internal message as a card."""
        try:
            from .card import build_gateway_card
            await self._ensure_init()
            assert self._client is not None
            card = build_gateway_card(content, category=category)
            card_msg_id = await self._client.send_card_to_chat(chat_id, card)
            _logger.info(
                "gateway card delivered: chat=%s category=%s card_msg_id=%s content_len=%d",
                chat_id[:12], category or "system",
                card_msg_id[:12] if card_msg_id else None, len(content),
            )
            return card_msg_id, None
        except Exception:
            _logger.warning("gateway card delivery failed", exc_info=True)
            return None, None

    async def _do_gateway_card_update(
        self,
        *,
        chat_id: str,
        card_msg_id: str,
        card_id: str | None = None,
        content: str,
        category: str = "",
    ) -> bool:
        """Update a gateway card's content (called from edit_message interception)."""
        try:
            from .card import build_gateway_card
            await self._ensure_init()
            assert self._client is not None
            card = build_gateway_card(content, category=category)
            if card_id:
                await self._client.cardkit_update(card_id, card)
            else:
                await self._client.update_card(card_msg_id, card)
            return True
        except Exception:
            return False

    async def on_cron_deliver_async(
        self,
        *,
        chat_id: str,
        content: str,
        loop: asyncio.AbstractEventLoop,
        category: str = "",
        job_name: str = "",
        job_id: str = "",
        status: str = "success",
        schedule: dict | None = None,
        failure_streak: int = 0,
        last_error: str = "",
    ) -> bool:
        if not self.enabled or not content or not chat_id:
            return False
        try:
            await self._do_cron_deliver(
                chat_id,
                content,
                job_name=job_name,
                job_id=job_id,
                status=status,
                schedule=schedule,
                failure_streak=failure_streak,
                last_error=last_error,
            )
            return True
        except Exception:
            return False


# ── Singleton ───────────────────────────────────────────────────────

_controller: StreamCardController | None = None


def get_controller() -> StreamCardController:
    """模块级单例工厂 — 全局唯一 StreamCardController 实例。"""
    global _controller
    if _controller is None:
        _controller = StreamCardController()
    return _controller
