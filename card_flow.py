# ================================================================
# lark-hls-v2 · card_flow.py 总导游图（改代码前必读，读完再动手）
# ▍这是什么（四问）
# ① 干什么：流式卡片的"一生" — 创建占位卡、分阶段刷入内容（推理/工具/回答）、封卡。被 mixin 到 controller 使用。
# ② 技术栈：asyncio + 飞书 CardKit 2.0 batch_update / stream_element / close_streaming API。
# ③ 依赖：card/（builder/elements/md）、state/（session/linear/phase/text/tooluse）、feishu 客户端。
# ④ 给谁看：改卡片流式逻辑、修 flush 时序、排查封卡 bug 的人。
#
# ▍文件从上到下的结构
# 常量 + 导入（状态 IDLE~ABORTED、drain 参数）
# _build_seal_summary() — 封卡摘要构建
# _fallback_write_answer() — stream_element 失败时的降级写入
# UnifiedControllerMixin — ★核心 Mixin 类★，被 StreamCardController 继承
#   ├─ _get_dynamic_quote / _get_panel_quote — 动态标题语录
#   ├─ _do_create_linear_card() — 创建初始占位卡 → 首字即显
#   ├─ _schedule_linear_flush() — 调度节流 flush
#   ├─ _do_unified_flush() — ★核心★ 统一 flush：Phase 2（创建元素）→ Phase 3（更新元素）→ stream answer
#   ├─ _upgrade_loading_hint_to_thinking() — loading 文案升级
#   ├─ _linear_on_thinking() — thinking/reasoning 增量处理
#   ├─ _finalize_card() — ★核心★ 封卡：drain dirty → 更新面板 → 加 footer → close_streaming
#   └─ _complete_card_flow() — ★核心★ 完成流程：drain → mark_completed → finalize
#
# ▍修改铁律（血泪教训）
# 1. sequence 必须单调递增，飞书拒绝回退 — 每次 API 调用前 session.sequence += 1。
# 2. dirty 标志只在 API 成功后清除 — 失败时保留以便下轮 flush 重试。
# 3. streaming_closed 是单向开关 — 一旦为 True 不可逆，后续 flush 直接 return。
# 4. 首字即显（_first_flush_done）用 fire-and-forget，必须持有 Task 强引用防 GC。
# 5. CancelledError 是 BaseException 子类 — except Exception 抓不到它，需要单独处理。
# 6. 封卡前必须 drain 所有 dirty 数据，否则 footer 出现在内容前面。
# 7. close_streaming 只能调一次 — 重复调用会 300309 报错。
# 8. phase 2 和 phase 3 的区别：phase 2 创建新元素，phase 3 更新已有元素。
#
# ▍特殊机制
# "三阶段 flush"：Phase 2 创建 answer+panel 元素 → Phase 3 更新 panel → stream answer 文本。
# 每个 phase 最多 2 个 API 调用（batch_update + stream_element）。
#
# ▍更新记录
# *v2 fork: 从原版 card_flow.py (1229行) 提取，适配 v2 的模块结构*
# ================================================================

from __future__ import annotations

import asyncio
import logging
import time as _time
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any

from .card import (
    ANSWER_ELEMENT_ID,
    UNIFIED_PANEL_ELEMENT_ID,
    _LOADING_ELEMENT_ID,
    _LOADING_HINT_ELEMENT_ID,
    _loading_hint_thinking_element,
    _streaming_element,
    build_streaming_card_v2,
    build_unified_panel,
    build_seal_actions,
    _count_tag_objects,
    _enforce_card_element_limit,
)
from .card.builder import _FEISHU_ELEMENT_LIMIT, _ELEMENT_LIMIT_MARGIN
from .card.md import _downgrade_tables, _split_long_text, compress_newlines, escape_markdown_asterisks, optimize_markdown_style, truncate_unclosed_markdown
from .state.linear import UnifiedLinearState
from .state.text import split_reasoning_text
from .card.quotes import QuoteManager

# Module-level singleton for dynamic quotes
# ▍动态语录单例 — 卡片 header 和 panel 标题的动漫台词
_quote_manager: QuoteManager | None = None

def _get_quote_manager() -> QuoteManager:
    global _quote_manager
    if _quote_manager is None:
        _quote_manager = QuoteManager()
    return _quote_manager
from .state.phase import TerminalReason
from .feishu import (
    CARDKIT_SCHEMA_ERROR,
    CARDKIT_SEQUENCE_CONFLICT,
    CARDKIT_CARD_TOO_LARGE,
    CARDKIT_STREAMING_CLOSED,
    FeishuAPIError,
    is_element_not_found_error,
    is_schema_error,
    is_terminal_api_code,
)

# 状态常量
from .state.phase import CardPhase

IDLE = CardPhase.IDLE
CREATING = CardPhase.CREATING
STREAMING = CardPhase.STREAMING
COMPLETING = CardPhase.COMPLETING
COMPLETED = CardPhase.COMPLETED
CREATION_FAILED = CardPhase.CREATION_FAILED
TERMINATED = CardPhase.TERMINATED
ABORTED = CardPhase.ABORTED
_TERMINAL = frozenset({CREATION_FAILED, TERMINATED, COMPLETED, ABORTED})

if TYPE_CHECKING:
    from .config import Config
    from .state.session import CardSession
    from .feishu import FeishuClient

_logger = logging.getLogger("lark_hls_v2")

# Drain-loop limits for final flush before seal
# ▍drain-loop 参数 — 封卡前刷尽 dirty 数据的重试配置
_DRAIN_ROUNDS_MAX: int = 8        # 最多 8 轮 drain（实际很少超过 2 轮）
_DRAIN_YIELD_SEC: float = 0.020   # 20ms 让出 — 给 worker 线程回调时间


def _build_seal_summary(state: UnifiedLinearState | None) -> str:
    """Build seal summary from state — answer text or fallback to reasoning."""
    if state is None:
        return ""
    summary_text = state.answer_text
    if not summary_text and state.reasoning_rounds:
        summary_text = state.reasoning_rounds[-1].text if state.reasoning_rounds else ""
    if summary_text:
        from .config import defaults as _def
        return summary_text[:_def.SUMMARY_MAX_LENGTH].replace("\n", " ").replace("```", "").strip()
    return ""


async def _fallback_write_answer(
    client: Any,
    card_id: str,
    content: str,
    *,
    sequence: int,
) -> bool:
    """Fallback: write answer via batch_update partial_update_element (no markdown re-parse, but works when streaming is closed)."""
    try:
        await client.cardkit_batch_update(
            card_id,
            [{
                "action": "partial_update_element",
                "params": {
                    "element_id": ANSWER_ELEMENT_ID,
                    "partial_element": {"content": content},
                },
            }],
            sequence=sequence,
        )
        return True
    except FeishuAPIError as e:
        _logger.warning("HLS: fallback write answer failed: %s", e)
        return False


class UnifiedControllerMixin:
    """Unified panel linear mode — phased card lifecycle."""

    # ── Instance attributes provided by StreamCardController ──
    _client: FeishuClient | None
    _cfg: Config
    _ensure_init: Callable[..., Coroutine[Any, Any, None]]
    _schedule_card_update: Callable[[CardSession], None]
    _cleanup: Callable[[str], None]
    _flush_deferred_background_reviews: Callable[[CardSession], None]
    _fire_and_forget: Callable[[Coroutine, Any], None]

    def _get_dynamic_quote(self, session: CardSession) -> str:
        """Get a dynamic anime quote for the card header title.

        Returns the configured static title if dynamic quotes are disabled
        or if no quotes are available.
        """
        if not self._cfg.dynamic_quotes_enabled:
            return self._cfg.card_header_title

        try:
            qm = _get_quote_manager()
            if qm.total_quotes == 0:
                return self._cfg.card_header_title

            # At card creation time, it's always a greeting (new message)
            scene = qm.detect_scene(is_new_session=True)
            quote = qm.get_quote(scene)
            return quote if quote else self._cfg.card_header_title
        except Exception:
            _logger.debug("Dynamic quote failed, using static title", exc_info=True)
            return self._cfg.card_header_title

    def _get_panel_quote(self, session: CardSession, *, is_sealing: bool = False) -> str:
        """Get a dynamic anime quote for the panel title based on current state.

        Returns only the quote text (no character/source attribution).
        During seal, returns a random ending like "收工" or "溜了溜了".
        """
        if not self._cfg.dynamic_quotes_enabled:
            return ""

        try:
            qm = _get_quote_manager()
            if qm.total_quotes == 0:
                return ""

            state = session.unified_state
            from .state.linear import UnifiedLinearState
            if not isinstance(state, UnifiedLinearState):
                return ""

            has_tools = bool(getattr(session.tool_use, '_steps', None) or getattr(session.tool_use, 'steps', []))
            has_reasoning = bool(state.reasoning_rounds or getattr(state, '_current_reasoning', ''))

            scene = qm.detect_scene(
                has_reasoning=has_reasoning,
                has_tools=has_tools,
                tools_running=has_tools,
                is_sealing=is_sealing,
            )
            return qm.get_mood(scene)
        except Exception:
            return ""

    def _build_panel(
        self,
        session: CardSession,
        state: UnifiedLinearState,
        *,
        expanded: bool | None = None,
        current_reasoning_text: str | None = None,
        is_sealing: bool = False,
    ) -> dict:
        """Build a unified panel from session/state with config defaults.

        Callers only need to pass overrides that differ from the streaming defaults.
        """
        all_tool_steps = session.tool_use.build_display_steps()
        return build_unified_panel(
            reasoning_rounds=state.reasoning_rounds,
            current_reasoning_text=(
                state.current_reasoning_text if current_reasoning_text is None
                else current_reasoning_text
            ),
            tool_steps=all_tool_steps,
            tool_elapsed_ms=session.tool_use.elapsed_ms,
            show_reasoning=self._cfg.show_reasoning,
            expanded=(
                self._cfg.streaming_panel_expanded if expanded is None
                else expanded
            ),
            panel_events=state.panel_events,
            max_tool_steps=self._cfg.max_tool_steps,
            max_reasoning_rounds=self._cfg.max_reasoning_rounds,
            reasoning_batch_size=self._cfg.reasoning_batch_size,
            auto_collapse_threshold=self._cfg.auto_collapse_threshold,
            panel_quote=self._get_panel_quote(session, is_sealing=is_sealing),
        )

    async def _do_create_linear_card(self, session: CardSession) -> None:
        """Create the initial placeholder card — loading hint only, no panel."""
        if session.state != IDLE:
            return
        # Snapshot epoch before async creation
        epoch = session.create_epoch
        session.state = CREATING
        session._create_epoch_snap = epoch
        session.linear = True
        # v1.4.0 fix (问题3 根因1 — delegate_task 后卡片降级纯文本):
        if session.unified_state is None:
            session.unified_state = UnifiedLinearState()

        try:
            await self._ensure_init()
            assert self._client is not None

            try:
                reply_to = session.anchor_id or session.message_id
                card = build_streaming_card_v2(
                    include_unified_panel=False,   # Panel added on first token
                    include_answer_element=False,   # Answer element added with panel
                    include_loading_hint=True,      # "正在加载上下文..."
                    streaming_panel_expanded=self._cfg.streaming_panel_expanded,
                    print_strategy=self._cfg.print_strategy,
                    print_step=self._cfg.print_step,
                    include_card_header=True,
                    card_header_title=self._get_dynamic_quote(session),
                    card_header_icon=self._cfg.card_header_icon,
                    card_header_template=self._cfg.card_header_template,
                )
                card_id = await self._client.cardkit_create(card)
                card_msg_id = await self._client.reply_card_by_id(reply_to, card_id)

                session.card_id = card_id
                session.card_msg_id = card_msg_id
                session.card_created_at = _time.time()
                session.flush.set_throttle(self._cfg.flush_interval_sec)

                # Track existing elements — only 2 are pre-allocated
                session.existing_elements = {
                    _LOADING_HINT_ELEMENT_ID,
                    _LOADING_ELEMENT_ID,
                }
                session._creation_stages.discard("panel")  # Panel NOT in initial card

            except FeishuAPIError as e:
                _logger.info("linear CardKit create failed: %s", e)
                raise

            session.flush.set_card_message_ready(True)

            # ── Stale-create guard ──
            if session.state == CREATING and not session.is_stale_create(epoch):
                session.state = STREAMING

            if session.linear and session.unified_state and (
                session.unified_state.has_dirty or session._pending_flush
            ):
                session._pending_flush = False
                if not session._first_flush_done:
                    # First content → immediate flush (首字即显)
                    session._first_flush_done = True
                    # v1.3.4 fix (P2): 持有 Task 强引用防止 GC 回收
                    # （与 core.py _fire_and_forget 同模式）
                    self._fire_and_forget(
                        session.flush.flush_now(lambda: self._do_unified_flush(session)),
                        asyncio.get_running_loop(),
                    )
                else:
                    # Subsequent content → throttled flush
                    self._schedule_linear_flush(session)

            # Must be set AFTER card_id/card_msg_id are assigned and
            session._card_ready.set()
            _logger.info(
                "HLS: linear card created msg=%s trace=%s linear=%s card_id=%s",
                (session.message_id or "?")[:12],
                session.card_trace_id,
                session.linear,
                (session.card_id or "")[:12],
            )
        except Exception as e:
            _logger.exception("_do_create_linear_card failed")
            # v1.3.4 fix (P1): 消息被删/撤回时触发 UnavailableGuard，避免后续
            # 对已删除消息的无效 API 调用。原实现 guard 从未被调用（死代码）。
            if isinstance(e, FeishuAPIError) and is_terminal_api_code(e.code):
                try:
                    session.guard.terminate("_do_create_linear_card", err=e)
                except Exception:
                    _logger.debug("guard.terminate failed in create path", exc_info=True)
            session.state = CREATION_FAILED
            session.enter_terminal(
                reason=TerminalReason.CREATION_FAILED,
                source="_do_create_linear_card",
            )
            # Signal readiness even on failure so awaiters don't deadlock
            session._card_ready.set()

    def _schedule_linear_flush(self, session: CardSession) -> None:
        """Schedule a unified panel flush for the given session."""
        if not session.should_proceed("_schedule_linear_flush"):
            return
        # COMPLETING is not terminal, but we should not schedule new flushes
        if session.state == IDLE or session.state == COMPLETING:
            return

        state = session.unified_state
        if state is None or not state.has_dirty:
            return

        if not session.flush._card_message_ready:
            session._pending_flush = True
            return

        # ── First-Token Immediate Flush (首字即显) ──
        if not session._first_flush_done:
            session._first_flush_done = True
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = session._loop
            if loop is not None and not loop.is_closed():
                # v1.3.4 fix (P2): 持有 Task 强引用防止 GC 回收
                self._fire_and_forget(
                    session.flush.flush_now(lambda: self._do_unified_flush(session)),
                    loop,
                )
            return

        _answer_only = state.answer_dirty and not state.panel_dirty and not state.tool_steps_dirty
        if _answer_only:
            # Answer-only: use faster throttle for streaming feel
            if self._cfg.speed_curve == "answer_fast":
                session.flush.set_throttle(self._cfg.answer_fast_stream_ms / 1000.0)
            else:
                session.flush.set_throttle(self._cfg.flush_interval_sec)
        else:
            session.flush.set_throttle(self._cfg.flush_interval_sec)

        session.flush.schedule_update(lambda: self._do_unified_flush(session))

    async def _do_unified_flush(self, session: CardSession) -> None:
        """Unified panel flush — max 2 API calls per flush cycle."""
        if session.is_terminal_phase or session.state == COMPLETING:
            return
        if not session.card_id:
            return
        state = session.unified_state
        if state is None:
            return
        assert self._client is not None

        actions: list[dict[str, Any]] = []

        # Bug fix (v1.0.5): Split Phase 2 into two sub-paths:
        # The answer element must be created in BOTH paths so the answer text can be
        if "answer" not in session._creation_stages and (state.panel_visible or state.answer_dirty or state.answer_text):
            new_elements: list[dict[str, Any]] = []

            # ── 开篇体验优化: 短等待窗口, 避免面板跳动 ──
            # 如果 panel 尚未可见但有 dirty 数据, 等 200ms 看 reasoning/tool 是否到达
            if not state.panel_visible and (state.panel_dirty or state.tool_steps_dirty):
                try:
                    await asyncio.sleep(0.2)
                except asyncio.CancelledError:
                    pass

            # ── Path A & B: Always add answer streaming element first ──
            new_elements.append(_streaming_element(element_id=ANSWER_ELEMENT_ID))

            # ── Path A: Has reasoning or tools → add unified panel after answer ──
            if state.panel_visible:
                panel = self._build_panel(session, state)
                new_elements.append(panel)

            # Add new elements before loading hint
            actions.append({
                "action": "add_elements",
                "params": {
                    "type": "insert_before",
                    "target_element_id": _LOADING_HINT_ELEMENT_ID,
                    "elements": new_elements,
                },
            })
            # Delete loading hint
            if _LOADING_HINT_ELEMENT_ID in session.existing_elements:
                actions.append({
                    "action": "delete_elements",
                    "params": {"element_ids": [_LOADING_HINT_ELEMENT_ID]},
                })
            # Note: panel_dirty and tool_steps_dirty are cleared AFTER

            # ── Execute Phase 2 batch_update ──
            if actions:
                _has_panel = state.panel_visible
                session.sequence += 1
                try:
                    await self._client.cardkit_batch_update(
                        session.card_id, actions, sequence=session.sequence,
                    )
                    # Update tracking after success
                    session._creation_stages.add("answer")
                    session._creation_stages.add("hint_removed")
                    session.existing_elements.add(ANSWER_ELEMENT_ID)
                    if _has_panel:
                        session._creation_stages.add("panel")
                        session.existing_elements.add(UNIFIED_PANEL_ELEMENT_ID)
                    session.existing_elements.discard(_LOADING_HINT_ELEMENT_ID)
                    # Clear dirty flags only after API success
                    state.panel_dirty = False
                    state.tool_steps_dirty = False
                except FeishuAPIError as e:
                    if e.code == CARDKIT_STREAMING_CLOSED:
                        if session._streaming_closed_logged:
                            pass
                        else:
                            _logger.info(
                                "unified flush: streaming closed, will be handled by TTL or seal: card=%s",
                                session.card_id[:12],
                            )
                            session._streaming_closed_logged = True
                        session._streaming_closed = True
                        return
                    if is_schema_error(e):
                        _logger.error(
                            "unified flush phase 2 SCHEMA ERROR (permanent): %s — detail: %s card=%s",
                            e, e.extract_schema_detail(), session.card_id[:12],
                        )
                        state.panel_dirty = False
                        state.answer_dirty = False
                        state.tool_steps_dirty = False
                        return
                    elif is_element_not_found_error(e):
                        _logger.warning(
                            "unified flush phase 2 element not found (non-fatal): %s card=%s",
                            e, session.card_id[:12],
                        )
                        session.existing_elements.discard(_LOADING_HINT_ELEMENT_ID)
                        state.panel_dirty = False
                        state.answer_dirty = False
                        state.tool_steps_dirty = False
                        return
                    else:
                        # v1.3.3 fix (P0): transient API error (rate limit, auth
                        _logger.warning(
                            "unified flush phase 2 batch_update failed: %s — "
                            "resetting _first_flush_done for retry, card=%s",
                            e, session.card_id[:12] if session.card_id else "?",
                        )
                        session._first_flush_done = False
                        return
                except asyncio.CancelledError:
                    # v1.3.4 fix (P1): CancelledError 是 BaseException 子类，
                    session._first_flush_done = False
                    raise
                except Exception as e:
                    # v1.3.3 fix (P0): catch non-FeishuAPIError exceptions (network
                    # forever" bug.
                    _logger.warning(
                        "unified flush phase 2 non-API exception: %s — "
                        "resetting _first_flush_done for retry, card=%s",
                        e, session.card_id[:12] if session.card_id else "?",
                        exc_info=True,
                    )
                    session._first_flush_done = False
                    return

            # Note: skip markdown optimization during streaming for performance;
            if state.answer_dirty:
                content = truncate_unclosed_markdown(escape_markdown_asterisks(optimize_markdown_style(state.answer_text or " ")))
                session.sequence += 1
                try:
                    await self._client.cardkit_stream_element(
                        session.card_id, ANSWER_ELEMENT_ID, content, sequence=session.sequence,
                    )
                    session._answer_streamed = True
                    state.answer_dirty = False
                except FeishuAPIError as e:
                    if e.code == CARDKIT_STREAMING_CLOSED:
                        session._streaming_closed = True
                        return
                    _logger.debug("unified stream_element failed: %s", e)

            if not state.panel_dirty and not state.tool_steps_dirty and not state.answer_dirty:
                return  # Phase 2 done, nothing more to do

        # ── Phase 3: Update existing panel + stream answer ──
        if state.panel_dirty:
            if "panel" in session._creation_stages:
                # Panel exists — update its content
                panel = self._build_panel(session, state)
                actions.append({
                    "action": "partial_update_element",
                    "params": {
                        "element_id": UNIFIED_PANEL_ELEMENT_ID,
                        "partial_element": {
                            "header": panel["header"],
                            "elements": panel["elements"],
                        },
                    },
                })
            elif "answer" in session._creation_stages:
                # ── Bug fix (v1.0.5): Late-arriving reasoning/tools ──
                panel = self._build_panel(session, state)
                actions.append({
                    "action": "add_elements",
                    "params": {
                        "type": "insert_after",
                        "target_element_id": ANSWER_ELEMENT_ID,
                        "elements": [panel],
                    },
                })
                # Note: "panel" stage will be added after API success below
            # Note: panel_dirty and tool_steps_dirty are cleared AFTER

        # ── Delete loading hint if still present (safety net) ──
        _hint_delete_in_batch = False
        if "hint_removed" not in session._creation_stages and _LOADING_HINT_ELEMENT_ID in session.existing_elements:
            actions.append({
                "action": "delete_elements",
                "params": {"element_ids": [_LOADING_HINT_ELEMENT_ID]},
            })
            _hint_delete_in_batch = True

        # ── Execute Phase 3 batch_update ──
        if actions:
            session.sequence += 1
            try:
                await self._client.cardkit_batch_update(
                    session.card_id, actions, sequence=session.sequence,
                )
                # Clear dirty flags only after API success
                if state.panel_dirty or state.tool_steps_dirty:
                    state.panel_dirty = False
                    state.tool_steps_dirty = False
                if _hint_delete_in_batch:
                    session._creation_stages.add("hint_removed")
                    session.existing_elements.discard(_LOADING_HINT_ELEMENT_ID)
                # ── Track late-arriving panel creation ──
                if "panel" not in session._creation_stages and state.panel_visible:
                    session._creation_stages.add("panel")
                    session.existing_elements.add(UNIFIED_PANEL_ELEMENT_ID)
            except FeishuAPIError as e:
                if e.code == CARDKIT_STREAMING_CLOSED:
                    if session._streaming_closed_logged:
                        pass
                    else:
                        _logger.info(
                            "unified flush: streaming closed, will be handled by TTL or seal: card=%s",
                            session.card_id[:12],
                        )
                        session._streaming_closed_logged = True
                    session._streaming_closed = True
                    return
                if is_schema_error(e):
                    _logger.error(
                        "unified flush phase 3 SCHEMA ERROR (permanent): %s — "
                        "detail: %s — "
                        "clearing dirty flags to stop retry, card=%s",
                        e, e.extract_schema_detail(), session.card_id[:12],
                    )
                    # Clear dirty to stop retry loop on permanent errors
                    state.panel_dirty = False
                    state.tool_steps_dirty = False
                    return
                if is_element_not_found_error(e):
                    # v1.4.1 fix (P1): Phase 3 batch_update 元素不存在 (300315 +
                    # warning 分支 → hint_removed 仍未同步 + existing_elements
                    # 重试。info 级别 (不是 warning/error) — 不是真正的故障。
                    _logger.info(
                        "unified flush phase 3 element not found (non-fatal): %s — "
                        "syncing hint tracking, card=%s",
                        e, session.card_id[:12],
                    )
                    session._creation_stages.add("hint_removed")
                    session.existing_elements.discard(_LOADING_HINT_ELEMENT_ID)
                    # 保留 panel_dirty / tool_steps_dirty 以便下轮 flush 重试
                    return
                _logger.warning("unified flush batch_update failed: %s", e)
                return

        # Note: skip markdown optimization during streaming for performance;
        if state.answer_dirty and "answer" in session._creation_stages:
            content = truncate_unclosed_markdown(escape_markdown_asterisks(optimize_markdown_style(state.answer_text or " ")))
            session.sequence += 1
            # v2.9.1: First push uses stream_element (typewriter), subsequent
            # updates use partial_update_element (instant replace) to avoid
            # typewriter animation reset causing text fragmentation.
            if session._answer_streamed:
                # Subsequent updates — instant replace, no typewriter reset
                try:
                    await self._client.cardkit_batch_update(
                        session.card_id,
                        [{
                            "action": "partial_update_element",
                            "params": {
                                "element_id": ANSWER_ELEMENT_ID,
                                "partial_element": {"content": content},
                            },
                        }],
                        sequence=session.sequence,
                    )
                    state.answer_dirty = False
                except FeishuAPIError as e:
                    if e.code == CARDKIT_STREAMING_CLOSED:
                        if session._streaming_closed_logged:
                            pass
                        else:
                            _logger.info(
                                "HLS: unified partial_update — streaming closed, will be handled by TTL or seal: card=%s",
                                session.card_id[:12],
                            )
                            session._streaming_closed_logged = True
                        session._streaming_closed = True
                        return
                    if is_element_not_found_error(e):
                        _logger.info(
                            "HLS: unified partial_update — 300313, will retry on next flush: card=%s",
                            session.card_id[:12],
                        )
                        return
                    _logger.debug("HLS: unified partial_update failed: %s", e)
            else:
                # First push — stream_element for typewriter effect
                try:
                    await self._client.cardkit_stream_element(
                        session.card_id, ANSWER_ELEMENT_ID, content, sequence=session.sequence,
                    )
                    state.answer_dirty = False
                except FeishuAPIError as e:
                    if e.code == CARDKIT_STREAMING_CLOSED:
                        if session._streaming_closed_logged:
                            pass
                        else:
                            _logger.info(
                                "HLS: unified stream — streaming closed, will be handled by TTL or seal: card=%s",
                                session.card_id[:12],
                            )
                            session._streaming_closed_logged = True
                        session._streaming_closed = True
                        return
                    if is_element_not_found_error(e):
                        _logger.info(
                            "HLS: unified stream — 300313, will retry on next flush: card=%s",
                            session.card_id[:12],
                        )
                        return
                    _logger.debug("HLS: unified stream_element failed: %s", e)

        if state.panel_dirty or state.answer_dirty or state.tool_steps_dirty:
            self._schedule_linear_flush(session)

    async def _upgrade_loading_hint_to_thinking(self, session: CardSession) -> None:
        """升级 loading hint 从 '加载上下文' 到 '正在思考' — 首个thinking事件到达时调用."""
        if session.is_terminal_phase or not session.card_id:
            return
        if _LOADING_HINT_ELEMENT_ID not in session.existing_elements:
            return
        assert self._client is not None
        try:
            session.sequence += 1
            new_hint = _loading_hint_thinking_element()
            await self._client.cardkit_batch_update(
                session.card_id,
                [{
                    "action": "partial_update_element",
                    "params": {
                        "element_id": _LOADING_HINT_ELEMENT_ID,
                        "partial_element": {
                            "text": new_hint["text"],
                        },
                    },
                }],
                sequence=session.sequence,
            )
            _logger.debug("HLS: upgraded loading hint to thinking, card=%s", (session.card_id or "?")[:12])
        except Exception as e:
            _logger.debug("HLS: thinking hint upgrade failed (non-fatal): %s, card=%s", e, (session.card_id or "?")[:12])

    def _linear_on_thinking(self, session: CardSession, text: str) -> None:
        """Handle a thinking/reasoning delta in linear mode."""
        state = session.unified_state
        if state is None:
            return
        split = split_reasoning_text(text)
        reasoning = split.get("reasoning_text")
        answer = split.get("answer_text")

        _reasoning_already_tracked = bool(state._current_reasoning)
        if reasoning and self._cfg.show_reasoning and not _reasoning_already_tracked:
            state.on_reasoning_delta(reasoning)
        if answer:
            # each interim call contains the full text so far. We must:
            _existing_len = len(state.answer_text)
            if _existing_len == 0:
                # No answer yet - accept the full text
                state.on_answer_delta(answer)
            elif len(answer) > _existing_len and answer[:_existing_len] == state.answer_text:
                # New text extends the existing answer - append only the new portion
                _new_part = answer[_existing_len:]
                if _new_part:
                    _logger.info(
                        "HLS: _linear_on_thinking appends incremental answer "
                        "existing_len=%d new_total=%d diff=%d msg=%s",
                        _existing_len, len(answer), len(_new_part),
                        (session.message_id or "?")[:12],
                    )
                    state.on_answer_delta(_new_part)
            # else: text is same length or shorter - already captured, skip
        if (reasoning and self._cfg.show_reasoning and not _reasoning_already_tracked) or answer:
            self._schedule_linear_flush(session)

    async def _finalize_card(
        self,
        session: CardSession,
        *,
        partial: bool = False,
        footer_data: dict | None = None,
        is_error: bool = False,
        is_aborted: bool = False,
        error_message: str = "",
        footer_fields: list[list[str]] | None = None,
        footer_show_label: bool = False,
    ) -> bool:
        """_finalize_card(): 契约 — ★核心★
        入参: session, partial, footer_data, is_error, is_aborted, error_message, footer_fields, footer_show_label
        返回: bool — True 封卡成功 / False 失败
        副作用:
          - drain 所有 dirty 数据 (panel + answer)
          - 更新面板到终态 (current_reasoning_text 清空)
          - 优化 answer markdown (_downgrade_tables + optimize_markdown_style)
          - 添加 footer / error panel / bg_review panel
          - 调用 close_streaming 关闭流式模式
        谁调用: _complete_card_flow()
        改动影响:
          - ⚠️ close_streaming 只能调一次 — 重复会 300309
          - sequence 冲突会自动重试 2 次
          - 元素数超 200 会自动裁剪 panel children (从头部删除旧项)
        """
        assert self._client is not None
        card_id = session.card_id
        assert card_id is not None

        try:
            # Before closing streaming, we MUST flush any remaining dirty
            # "footer appears before content finishes" bug.
            state = session.unified_state
            if state is not None and (state.answer_dirty or state.panel_dirty or state.tool_steps_dirty):
                _logger.warning(
                    "finalize_card: dirty data detected at seal time "
                    "answer_dirty=%s panel_dirty=%s tool_steps_dirty=%s card=%s — "
                    "flushing before close",
                    state.answer_dirty, state.panel_dirty, state.tool_steps_dirty,
                    card_id[:12],
                )
                # ── Flush remaining panel content ──
                if (state.panel_dirty or state.tool_steps_dirty) and "panel" in session._creation_stages:
                    panel = self._build_panel(session, state)
                    try:
                        session.sequence += 1
                        await self._client.cardkit_batch_update(
                            session.card_id,
                            [{
                                "action": "partial_update_element",
                                "params": {
                                    "element_id": UNIFIED_PANEL_ELEMENT_ID,
                                    "partial_element": {
                                        "header": panel["header"],
                                        "elements": panel["elements"],
                                    },
                                },
                            }],
                            sequence=session.sequence,
                        )
                        state.panel_dirty = False
                        state.tool_steps_dirty = False
                    except FeishuAPIError as e:
                        if e.code == CARDKIT_STREAMING_CLOSED:
                            _logger.info("seal drain: streaming already closed, skipping panel flush")
                            session._streaming_closed = True
                        else:
                            _logger.warning("seal drain panel failed: %s", e)
                        state.panel_dirty = False
                        state.tool_steps_dirty = False

                # ── Flush remaining answer text ──
                if state.answer_dirty and "answer" in session._creation_stages and not session._streaming_closed:
                    content = truncate_unclosed_markdown(escape_markdown_asterisks(optimize_markdown_style(state.answer_text or " ")))
                    try:
                        session.sequence += 1
                        _logger.info(
                            "HLS: seal drain answer text len=%d card=%s",
                            len(content), card_id[:12],
                        )
                        # v2.0.8.0: Use stream_element to trigger markdown re-parse
                        await self._client.cardkit_stream_element(
                            session.card_id, ANSWER_ELEMENT_ID, content,
                            sequence=session.sequence,
                        )
                        state.answer_dirty = False
                    except FeishuAPIError as e:
                        # v1.1.1: 统一 fallback — 300309 和 300313 都改用 batch_update（不带 tag）
                        if e.code == CARDKIT_STREAMING_CLOSED or is_element_not_found_error(e):
                            if e.code == CARDKIT_STREAMING_CLOSED:
                                session._streaming_closed = True
                            _logger.info(
                                "HLS: seal drain answer — %s, falling back to partial_update_element card=%s",
                                "streaming closed" if e.code == CARDKIT_STREAMING_CLOSED else "300313",
                                card_id[:12],
                            )
                            session.sequence += 1
                            await _fallback_write_answer(
                                self._client, session.card_id, content,
                                sequence=session.sequence,
                            )
                        else:
                            _logger.warning("HLS: seal drain answer failed: %s", e)
                        state.answer_dirty = False

            # ── Step 1: Update unified panel to final state (non-streaming) ──
            seal_actions: list[dict[str, Any]] = []
            panel: dict[str, Any] | None = None

            if state is not None:
                state.finalize()

                # ── Bug fix (v1.0.5): Only update panel if it was created ──
                if "panel" in session._creation_stages:
                    panel = self._build_panel(
                        session, state,
                        expanded=self._cfg.panel_expanded,
                        current_reasoning_text="",
                        is_sealing=True,
                    )
                    seal_actions.append({
                        "action": "partial_update_element",
                        "params": {
                            "element_id": UNIFIED_PANEL_ELEMENT_ID,
                            "partial_element": {
                                "header": panel["header"],
                                "elements": panel["elements"],
                            },
                        },
                    })

            # v1.3.1 fix: Do NOT skip this step even when the answer was already fully
            # guard) is a minor visual issue; content truncation is a P0 data-loss bug.
            if state is not None and state.answer_text and "answer" in session._creation_stages:
                optimized_content = escape_markdown_asterisks(_downgrade_tables(optimize_markdown_style(state.answer_text))) or " "
                # v2.0.5.0: Feishu card has a 30KB total size limit. Split long
                # answers into multiple markdown elements to avoid truncation.
                _ANSWER_BYTES_LIMIT = 20000  # ~20KB, leave headroom for panel+footer
                if len(optimized_content.encode("utf-8")) > _ANSWER_BYTES_LIMIT:
                    chunks = _split_long_text(optimized_content, limit=4000)
                    # v2.0.8.0: Use stream_element for first chunk (triggers markdown re-parse)
                    session.sequence += 1
                    try:
                        await self._client.cardkit_stream_element(
                            session.card_id, ANSWER_ELEMENT_ID, chunks[0],
                            sequence=session.sequence,
                        )
                    except FeishuAPIError as e:
                        if e.code == CARDKIT_STREAMING_CLOSED:
                            session._streaming_closed = True
                        _logger.info(
                            "HLS: seal stream_element chunk0 — %s, falling back to partial_update_element card=%s",
                            e, card_id[:12],
                        )
                        session.sequence += 1
                        await _fallback_write_answer(
                            self._client, session.card_id, chunks[0],
                            sequence=session.sequence,
                        )
                    # Extra chunks inserted as new markdown elements
                    if len(chunks) > 1:
                        extra_elements = [
                            {"tag": "markdown", "content": c}
                            for c in chunks[1:]
                        ]
                        seal_actions.append({
                            "action": "add_elements",
                            "params": {
                                "type": "insert_after",
                                "target_element_id": ANSWER_ELEMENT_ID,
                                "elements": extra_elements,
                            },
                        })
                else:
                    # v2.0.8.0: Use stream_element to trigger markdown re-parse
                    session.sequence += 1
                    try:
                        await self._client.cardkit_stream_element(
                            session.card_id, ANSWER_ELEMENT_ID, optimized_content,
                            sequence=session.sequence,
                        )
                    except FeishuAPIError as e:
                        if e.code == CARDKIT_STREAMING_CLOSED:
                            session._streaming_closed = True
                        _logger.info(
                            "HLS: seal stream_element — %s, falling back to partial_update_element card=%s",
                            e, card_id[:12],
                        )
                        session.sequence += 1
                        await _fallback_write_answer(
                            self._client, session.card_id, optimized_content,
                            sequence=session.sequence,
                        )

            # ── Step 3: Add footer + delete loading elements ──
            seal_actions.extend(
                build_seal_actions(
                    partial=partial,
                    footer_data=footer_data,
                    is_error=is_error,
                    is_aborted=is_aborted,
                    error_message=error_message,
                    footer_fields=footer_fields,
                    footer_show_label=footer_show_label,
                    existing_elements=session.existing_elements,
                    card_trace_id=session.card_trace_id,
                    footer_before_panel=True,
                )
            )

            if panel is not None:
                simulated_elements: list[dict] = []
                # Answer element (1 markdown with content) — displayed before panel
                if state is not None and state.answer_text:
                    simulated_elements.append({"tag": "markdown", "content": state.answer_text})
                else:
                    simulated_elements.append({"tag": "markdown", "content": " "})
                # Panel (reasoning/tools)
                simulated_elements.append(panel)
                # Elements from add_elements actions (footer, error, partial, bg review)
                for action in seal_actions:
                    if action.get("action") == "add_elements":
                        for elem in action.get("params", {}).get("elements", []):
                            simulated_elements.append(elem)
                # Count total tag objects in simulated card body
                total_count = _count_tag_objects(simulated_elements)
                threshold = _FEISHU_ELEMENT_LIMIT - _ELEMENT_LIMIT_MARGIN
                if total_count > threshold:
                    _logger.warning(
                        "finalize_card: card element count %d exceeds threshold %d, "
                        "trimming panel children card=%s",
                        total_count, threshold, card_id[:12],
                    )
                    # Trim panel children from the front
                    children: list[dict] = panel.get("elements", [])
                    # Check if a collapse hint already exists
                    hint_idx = None
                    for i, child in enumerate(children):
                        if isinstance(child.get("content"), str) and "已折叠" in child["content"]:
                            hint_idx = i
                            break
                    # If no hint exists yet, we'll need to add one (1 element), so account for it
                    if hint_idx is None:
                        total_count += 1
                    trimmed_count = 0
                    while total_count > threshold and len(children) > 1:
                        # Skip the collapse hint (first child if it contains "已折叠")
                        remove_idx = 1 if children[0].get("content", "").endswith("已折叠") else 0
                        removed = children.pop(remove_idx)
                        total_count -= _count_tag_objects([removed])
                        trimmed_count += 1
                    if trimmed_count > 0:
                        # Update or add collapse hint
                        # Re-find hint_idx (may have shifted due to removals)
                        hint_idx = None
                        for i, child in enumerate(children):
                            if isinstance(child.get("content"), str) and "已折叠" in child["content"]:
                                hint_idx = i
                                break
                        if hint_idx is not None:
                            old_hint = children[hint_idx]["content"]
                            # Parse existing trimmed count, then add new count
                            existing_count = 0
                            _idx = old_hint.find("项")
                            if _idx > 0:
                                _end = _idx
                                while _end > 0 and old_hint[_end - 1] == ' ':
                                    _end -= 1
                                _start = _end
                                while _start > 0 and old_hint[_start - 1].isdigit():
                                    _start -= 1
                                if _start < _end:
                                    existing_count = int(old_hint[_start:_end])
                            total_trimmed = existing_count + trimmed_count
                            children[hint_idx]["content"] = f"⚡ 还有 {total_trimmed} 项已折叠"
                        else:
                            children.insert(0, {
                                "tag": "markdown",
                                "content": f"⚡ 还有 {trimmed_count} 项已折叠",
                                "text_size": "notation",
                            })
                        # Update panel's elements
                        panel["elements"] = children
                        # Rebuild the panel update action in seal_actions
                        for i, action in enumerate(seal_actions):
                            if (action.get("action") == "partial_update_element"
                                    and action.get("params", {}).get("element_id") == UNIFIED_PANEL_ELEMENT_ID):
                                seal_actions[i]["params"]["partial_element"]["elements"] = children
                                break
                    _logger.info(
                        "finalize_card: after trimming, estimated total %d, trimmed %d items card=%s",
                        total_count, trimmed_count, card_id[:12],
                    )

            if seal_actions:
                session.sequence += 1
                await self._client.cardkit_batch_update(
                    card_id, seal_actions, sequence=session.sequence,
                )

            # When closing streaming, we MUST also update the card's summary
            # bug the user reported.
            # CRITICAL: Only call close_streaming ONCE per card lifecycle.
            # updated to config.summary.content.  The summary MUST be
            seal_summary = _build_seal_summary(state)

            if not session._streaming_closed:
                session.sequence += 1
                _logger.info(
                    "HLS: finalize_card closing streaming card=%s trace=%s seq=%d summary=%s",
                    card_id[:12], session.card_trace_id, session.sequence,
                    repr(seal_summary[:40]) if seal_summary else "(empty)",
                )
                # ── Bug fix (v1.0.3): Pass summary IN close_streaming ──
                # must be in THIS request — passing summary="" and then
                await self._client.cardkit_close_streaming(
                    card_id, sequence=session.sequence, summary=seal_summary,
                )
                session._streaming_closed = True
            else:
                _logger.info(
                    "finalize_card: streaming already closed, skipping close_streaming card=%s",
                    card_id[:12],
                )
                if seal_summary:
                    try:
                        session.sequence += 1
                        await self._client.cardkit_update_summary(
                            card_id, seal_summary, sequence=session.sequence,
                        )
                        _logger.info(
                            "finalize_card: summary updated (streaming already closed) "
                            "card=%s seq=%d summary=%s",
                            card_id[:12], session.sequence,
                            repr(seal_summary[:40]),
                        )
                    except FeishuAPIError as e:
                        _logger.warning(
                            "finalize_card: summary update failed (already closed) "
                            "card=%s error=%s",
                            card_id[:12], e,
                        )

            return True

        except FeishuAPIError as e:
            if e.code == CARDKIT_SEQUENCE_CONFLICT:
                _logger.warning(
                    "finalize_card: sequence conflict, retrying... card=%s seq=%d",
                    card_id[:12], session.sequence,
                )
                for retry in range(2):
                    try:
                        retry_actions: list[dict[str, Any]] = []
                        if state is not None:
                            # ── Bug fix (v1.0.5): Only update panel if it was created ──
                            if "panel" in session._creation_stages:
                                retry_panel = self._build_panel(
                                    session, state,
                                    expanded=self._cfg.panel_expanded,
                                    current_reasoning_text="",
                                    is_sealing=True,
                                )
                                retry_actions.append({
                                    "action": "partial_update_element",
                                    "params": {
                                        "element_id": UNIFIED_PANEL_ELEMENT_ID,
                                        "partial_element": {
                                            "header": retry_panel["header"],
                                            "elements": retry_panel["elements"],
                                        },
                                    },
                                })
                            # v1.3.1: same fix as main seal path — always send final
                            # (see v1.3.1 fix comment in main seal path above).
                            if state.answer_text and "answer" in session._creation_stages:
                                optimized_content = escape_markdown_asterisks(_downgrade_tables(optimize_markdown_style(state.answer_text))) or " "
                                # v2.0.8.0: Use stream_element to trigger markdown re-parse
                                session.sequence += 1
                                try:
                                    await self._client.cardkit_stream_element(
                                        session.card_id, ANSWER_ELEMENT_ID, optimized_content,
                                        sequence=session.sequence,
                                    )
                                except FeishuAPIError as stream_e:
                                    if stream_e.code == CARDKIT_STREAMING_CLOSED:
                                        session._streaming_closed = True
                                    _logger.info(
                                        "HLS: seal retry stream_element — %s, falling back to partial_update_element card=%s",
                                        stream_e, card_id[:12],
                                    )
                                    session.sequence += 1
                                    await _fallback_write_answer(
                                        self._client, session.card_id, optimized_content,
                                        sequence=session.sequence,
                                    )
                        retry_actions.extend(
                            build_seal_actions(
                                partial=partial,
                                footer_data=footer_data,
                                is_error=is_error,
                                is_aborted=is_aborted,
                                error_message=error_message,
                                footer_fields=footer_fields,
                                footer_show_label=footer_show_label,
                                existing_elements=session.existing_elements,
                                card_trace_id=session.card_trace_id,
                                footer_before_panel=True,
                            )
                        )
                        # batch_update BEFORE close_streaming (same order as try block)
                        if retry_actions:
                            session.sequence += 1
                            await self._client.cardkit_batch_update(
                                card_id, retry_actions, sequence=session.sequence,
                            )

                        # Close streaming AFTER batch_update
                        if not session._streaming_closed:
                            # Recompute seal_summary for retry (state may have changed)
                            retry_summary = _build_seal_summary(state)
                            session.sequence += 1
                            await self._client.cardkit_close_streaming(
                                card_id, sequence=session.sequence, summary=retry_summary,
                            )
                            session._streaming_closed = True

                        _logger.info(
                            "finalize_card: retry %d succeeded card=%s",
                            retry + 1, card_id[:12],
                        )
                        return True
                    except FeishuAPIError as retry_e:
                        if retry_e.code == CARDKIT_SEQUENCE_CONFLICT:
                            continue
                        # v2.0.9.2 fix: 300309 时 seal actions 已发送成功，
                        # 卡片内容完整，return True 避免 fallback 重复发送
                        if retry_e.code == CARDKIT_STREAMING_CLOSED:
                            session._streaming_closed = True
                            return True
                        raise
                # All retries exhausted
                _logger.warning(
                    "finalize_card: retry exhausted after sequence conflicts card=%s",
                    card_id[:12],
                )
                return False
            # v2.0.9.2 fix: 300309 时 seal actions 已发送成功，
            # 卡片内容完整，return True 避免 fallback 重复发送
            if e.code == CARDKIT_STREAMING_CLOSED:
                session._streaming_closed = True
                return True
            return False
        except Exception:
            _logger.exception('finalize_card unexpected error')
            return False

    async def _complete_card_flow(self, session: CardSession) -> bool:
        """_complete_card_flow(): 契约 — ★核心★
        入参: session (CardSession)
        返回: bool — True 完成 / False 失败
        副作用:
          - drain dirty 数据 (最多 8 轮，每轮间隔 20ms)
          - mark_completed 停止后续 flush
          - 调用 _finalize_card 封卡
          - 释放重数据 (_reset_session_state)
          - 记录 metrics (record_card_completed / record_card_failed)
        谁调用: _dispatch_completion() -> _complete_with_fallback()
        改动影响: drain 不净时 finalize_card 会兜底再 flush 一次
        """
        if session.guard.should_skip("_complete_card_flow"):
            return False

        # ── Step 1: Wait for any in-progress flush to finish ──
        await session.flush.wait_for_flush()

        # without being flushed.  We must drain it ALL here, before
        # the "footer appears before content finishes" bug.
        # v2.0.9.1 fix: drain loop 用 try/except 包裹，CancelledError 时
        # 也要执行 mark_completed + finalize_card，避免 gateway 关闭时最后一批内容截断。
        state = session.unified_state
        _drain_cancelled = False
        try:
            for _drain_round in range(_DRAIN_ROUNDS_MAX):
                if not (
                    state is not None
                    and session.card_id
                    and "answer" in session._creation_stages
                    and (state.answer_dirty or state.panel_dirty or state.tool_steps_dirty)
                ):
                    break  # No dirty data — drain complete

                _logger.info(
                    "linear complete: drain round %d/%d "
                    "answer_dirty=%s panel_dirty=%s tool_steps_dirty=%s msg=%s",
                    _drain_round + 1, _DRAIN_ROUNDS_MAX,
                    state.answer_dirty, state.panel_dirty, state.tool_steps_dirty,
                    (session.message_id or "?")[:12],
                )
                assert self._client is not None

                # ── Drain panel content ──
                if state.panel_dirty and "panel" in session._creation_stages:
                    panel = self._build_panel(session, state)
                    drain_actions: list[dict[str, Any]] = [{
                        "action": "partial_update_element",
                        "params": {
                            "element_id": UNIFIED_PANEL_ELEMENT_ID,
                            "partial_element": {
                                "header": panel["header"],
                                "elements": panel["elements"],
                            },
                        },
                    }]
                    try:
                        session.sequence += 1
                        await self._client.cardkit_batch_update(
                            session.card_id, drain_actions, sequence=session.sequence,
                        )
                        state.panel_dirty = False
                        state.tool_steps_dirty = False
                    except FeishuAPIError as e:
                        if e.code == CARDKIT_STREAMING_CLOSED:
                            # v1.2.0 Y3: drain 阶段也用 _streaming_closed_logged 去重
                            if session._streaming_closed_logged:
                                _logger.debug("drain: streaming already closed (already logged)")
                            else:
                                _logger.info("drain: streaming already closed, skipping")
                                session._streaming_closed_logged = True
                            session._streaming_closed = True
                        elif is_schema_error(e):
                            _logger.error("drain SCHEMA ERROR: %s — detail: %s", e, e.extract_schema_detail())
                            state.panel_dirty = False
                            state.tool_steps_dirty = False
                        else:
                            _logger.warning("drain panel failed: %s", e)

                # ── Drain answer text ──
                if state.answer_dirty and "answer" in session._creation_stages:
                    content = truncate_unclosed_markdown(escape_markdown_asterisks(optimize_markdown_style(state.answer_text or " ")))
                    if session._streaming_closed:
                        # streaming 已关闭，直接 fallback 避免无谓的 stream_element 失败
                        session.sequence += 1
                        ok = await _fallback_write_answer(
                            self._client, session.card_id, content,
                            sequence=session.sequence,
                        )
                        if ok:
                            state.answer_dirty = False
                    else:
                        try:
                            session.sequence += 1
                            _logger.info(
                                "HLS: drain answer text len=%d msg=%s",
                                len(content), (session.message_id or "?")[:12],
                            )
                            # v2.0.8.0: Use stream_element to trigger markdown re-parse
                            await self._client.cardkit_stream_element(
                                session.card_id, ANSWER_ELEMENT_ID, content,
                                sequence=session.sequence,
                            )
                            state.answer_dirty = False
                        except FeishuAPIError as e:
                            # v1.1.1: 统一 fallback — 300309 和 300313 都改用 batch_update（不带 tag）
                            # 之前 300309 直接 skip 答案丢失；300313 的 fallback 带 tag 报 300312
                            if e.code == CARDKIT_STREAMING_CLOSED or is_element_not_found_error(e):
                                if e.code == CARDKIT_STREAMING_CLOSED:
                                    session._streaming_closed = True
                                # v1.2.0 Y3: streaming closed 日志去重；300313 仍每次打（非重复事件）
                                if e.code == CARDKIT_STREAMING_CLOSED and session._streaming_closed_logged:
                                    pass
                                else:
                                    _logger.info(
                                        "HLS: drain answer — %s, falling back to partial_update_element msg=%s",
                                        "streaming closed" if e.code == CARDKIT_STREAMING_CLOSED else "300313",
                                        (session.message_id or "?")[:12],
                                    )
                                    if e.code == CARDKIT_STREAMING_CLOSED:
                                        session._streaming_closed_logged = True
                                session.sequence += 1
                                ok = await _fallback_write_answer(
                                    self._client, session.card_id, content,
                                    sequence=session.sequence,
                                )
                                if ok:
                                    state.answer_dirty = False
                            else:
                                _logger.warning("HLS: drain answer failed: %s", e)

                if _drain_round < _DRAIN_ROUNDS_MAX - 1:
                    await asyncio.sleep(_DRAIN_YIELD_SEC)
        except asyncio.CancelledError:
            # v2.0.9.1 fix: gateway 关闭时 task 被 cancel，drain 被中断。
            # 必须继续执行 mark_completed + finalize_card，否则最后一批内容截断。
            _logger.warning(
                "linear complete: drain cancelled (gateway shutdown?) "
                "answer_dirty=%s panel_dirty=%s msg=%s — "
                "proceeding to finalize_card",
                state.answer_dirty if state else "?",
                state.panel_dirty if state else "?",
                (session.message_id or "?")[:12],
            )
            _drain_cancelled = True
        except Exception:
            _logger.warning(
                "linear complete: drain unexpected error msg=%s",
                (session.message_id or "?")[:12],
                exc_info=True,
            )

        # ── Final drain check: log warning if dirty data remains ──
        if state is not None and (state.answer_dirty or state.panel_dirty or state.tool_steps_dirty):
            _logger.warning(
                "linear complete: dirty data remains after %d drain rounds "
                "answer_dirty=%s panel_dirty=%s tool_steps_dirty=%s msg=%s — "
                "will be flushed by finalize_card before close_streaming",
                _DRAIN_ROUNDS_MAX,
                state.answer_dirty, state.panel_dirty, state.tool_steps_dirty,
                (session.message_id or "?")[:12],
            )

        # ── Step 3: Mark flush as completed — no more updates accepted ──
        session.flush.mark_completed()

        try:
            await asyncio.wait_for(session._card_ready.wait(), timeout=30.0)
        except asyncio.TimeoutError:
            _logger.warning("complete: card creation timed out: msg=%s", (session.message_id or "?")[:12])

        if not session.card_id:
            session.state = CREATION_FAILED
            session.enter_terminal(
                reason=TerminalReason.CREATION_FAILED,
                source="_complete_card_flow",
            )
            return False

        # ── Step 4: Finalize state ──
        if state:
            state.finalize()

        # ── Build footer data ──
        footer_data = session.footer
        # v1.3.4 fix (P2): bg_review_messages 存在 state 中但从未传给
        if state and state.bg_review_messages:
            if footer_data is None:
                footer_data = {}
            footer_data = {**footer_data, "bg_review_messages": list(state.bg_review_messages)}
        is_aborted = getattr(session, "_was_aborted", False) or session.state == ABORTED
        error_message = getattr(session, "error_message", "")
        # v1.2.0 B1 fix: is_error 必须兼顾 error_message。
        is_error = (
            session.state in (CREATION_FAILED, TERMINATED)
            or (bool(error_message) and not is_aborted)
        )

        # ── Step 5: Preservative seal (the only completion path) ──
        seal_ok = await self._finalize_card(
            session,
            footer_data=footer_data,
            is_error=is_error,
            is_aborted=is_aborted,
            error_message=error_message,
            footer_fields=self._cfg.footer_fields,
            footer_show_label=self._cfg.footer_show_label,
        )

        if seal_ok:
            # v1.3.4 fix (P1): 如果会话已被 on_aborted 标记为 ABORTED，
            if session._was_aborted:
                session.state = ABORTED
            else:
                session.state = COMPLETED
            # v1.1.1: 释放重数据（unified_state/text/tool_use），减少内存占用
            # session 留最小元数据等 _prune_stale_sessions 清理
            try:
                self._reset_session_state(session)
            except Exception:
                _logger.debug("HLS: release session data failed", exc_info=True)
            # v1.1.0: Record metrics
            try:
                from .aowen import record_card_completed
                record_card_completed()
            except Exception:
                _logger.debug('metrics: record_card_completed failed', exc_info=True)
        else:
            session.state = CREATION_FAILED
            session.enter_terminal(
                reason=TerminalReason.CREATION_FAILED,
                source="_complete_card_flow_seal_failed",
            )
            # v1.1.1: 失败也释放重数据
            try:
                self._reset_session_state(session)
            except Exception:
                _logger.debug("HLS: release session data failed", exc_info=True)
            # v1.1.0: Record metrics
            try:
                from .aowen import record_card_failed
                record_card_failed()
            except Exception:
                _logger.debug('metrics: record_card_failed failed', exc_info=True)

        return seal_ok


__all__ = [
    "UnifiedControllerMixin",
    "IDLE",
    "CREATING",
    "STREAMING",
    "COMPLETING",
    "COMPLETED",
    "CREATION_FAILED",
    "TERMINATED",
    "ABORTED",
    "_TERMINAL",
]
