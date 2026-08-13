# =================================================================
# lark-hls-v2/flush/controller.py · 总导游图（改代码前必读，读完再动手）
# ▍这是什么
# ① 干什么：节流控制器，决定"何时"执行卡片刷新回调。
#    不包含飞书业务逻辑，只管时间调度：节流窗口、长空闲批处理、重入锁。
#    核心场景：流式输出期间每 70ms 一次 API 调用会被飞书限流，
#    controller 把碎片更新合并到 100-180ms 间隔才真正 flush。
# ② 技术栈：Python asyncio（TimerHandle + Future + Task）。
# ③ 依赖：无外部依赖，纯调度逻辑。
# ④ 给谁看：修改刷新频率、调试"卡片更新延迟"问题的开发者。
# ▍文件从上到下的结构
# 常量区       — CARDKIT_MS / LONG_GAP_MS / BATCH_AFTER_GAP_MS
# FlushController 类 — ★本文件核心★
#   公开方法   — schedule_update / flush_now / wait_for_flush / mark_completed / set_card_message_ready
#   内部调度   — _schedule_update_on_loop / _schedule / _do_flush_task / _create_flush_task / _do_flush
# ▍修改铁律
# 1. 【不】在 _do_flush 里吞异常后不做 reflush 检查——flush 期间有新数据必须重刷，
#    否则用户看到"少了最后一段"。
# 2. CARDKIT_MS=0.100 是飞书官方 print_frequency_ms(70ms) + 余量，
#    改低于 70ms 必被限流。
# 3. _pending_flush_tasks 必须持有 Task 引用（Python 文档要求），不持有 = GC 吃掉任务。
# 4. _do_flush 里 _flush_in_progress 是重入锁——两个 flush 并发会乱序，
#    第二个设 _needs_reflush=True 然后等第一个结束后补刷。
# ▍外号表
# "throttle" → _throttle_ms（节流窗口，两次 flush 的最小间隔）
# "reflush"  → _needs_reflush（flush 期间来了新数据，标记需要补刷）
# =================================================================

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

_logger = logging.getLogger("lark_hls_v2")

# ------------------------------------------------------------------
# ▍常量 — 改了会怎样：CARDKIT_MS 改小 → 飞书限流；改大 → 打字机变慢
# ------------------------------------------------------------------
CARDKIT_MS = 0.100  # CardKit 流式 API 的刷新间隔（100ms：大于等于官方默认 print_frequency_ms 70ms，留余量给 API 往返）
LONG_GAP_MS = 1.000  # 超过此间隔 → 认为是长时间空闲
BATCH_AFTER_GAP_MS = 0.150  # 长时间空闲后等待这个时间再 flush

class FlushController:
    """★本文件核心★ FlushController: 纯时序控制器，决定何时执行卡片刷新回调.
    不包含飞书业务逻辑，只管调度：节流窗口、长空闲批处理、重入锁。
    改了会怎样：节流间隔变了 → 打字机速度和 API 调用频率都变。
    """

    def __init__(self, throttle_ms: float = CARDKIT_MS) -> None:
        self._throttle_ms = throttle_ms
        self._flush_in_progress = False
        self._needs_reflush = False
        self._pending_timer: asyncio.TimerHandle | None = None
        self._last_update_time = 0.0
        self._completed = False
        self._card_message_ready = False
        self._flush_resolvers: list[asyncio.Future[None]] = []
        # （Python 文档："Save a reference to the result of this function"）
        self._pending_flush_tasks: set[asyncio.Task[None]] = set()
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            try:
                self._loop = asyncio.get_event_loop()
            except RuntimeError:
                self._loop = None  # Will be lazily resolved via _get_loop()

    def _get_loop(self) -> asyncio.AbstractEventLoop:
        """In Python 3.10+ (especially 3.11.15), both get_running_loop()"""
        if self._loop is None:
            self._loop = asyncio.get_running_loop()
        return self._loop

    @property
    def throttle_ms(self) -> float:
        return self._throttle_ms

    @throttle_ms.setter
    def throttle_ms(self, value: float) -> None:
        self._throttle_ms = value

    @property
    def last_update_time(self) -> float:
        return self._last_update_time

    def schedule_update(self, do_flush: Callable[[], Awaitable[None]]) -> None:
        """do_flush: async callable，执行实际 API 调用."""
        if self._completed or not self._card_message_ready:
            return
        try:
            self._get_loop().call_soon_threadsafe(self._schedule_update_on_loop, do_flush)
        except RuntimeError:
            pass  # Event loop already closed

    def _schedule_update_on_loop(self, do_flush: Callable[[], Awaitable[None]]) -> None:
        """_schedule_update_on_loop(): 节流调度核心 —— 三路分支：立即flush / 延迟到窗口边界 / 长空闲批处理.
        为什么三路：长空闲后立即 flush 会让内容不完整（等一小批更完整），正常节奏则在窗口边界 flush。
        """
        if self._completed or not self._card_message_ready:
            return
        now = time.monotonic()
        elapsed = now - self._last_update_time

        if elapsed >= self._throttle_ms:
            # 超出节流窗口
            if elapsed > LONG_GAP_MS:
                # 长时间空闲 → 延迟一小批让内容更完整
                if self._pending_timer is None:
                    self._schedule(delay=BATCH_AFTER_GAP_MS, do_flush=do_flush)
            else:
                # 立即 flush
                self._do_flush_task(do_flush)
        else:
            # 仍在节流窗口内 → 延迟到窗口边界
            if self._pending_timer is None:
                delay = self._throttle_ms - elapsed
                self._schedule(delay=delay, do_flush=do_flush)

    async def flush_now(self, do_flush: Callable[[], Awaitable[None]]) -> None:
        """flush_now(): 立即执行一次 flush（跳过节流），等待完成."""
        if self._completed or not self._card_message_ready:
            return
        self._cancel_timer()
        await self._do_flush(do_flush)

    async def wait_for_flush(self) -> None:
        """wait_for_flush(): 挂起等待当前进行中的 flush 完成（用于 pipeline 结束前同步）."""
        if not self._flush_in_progress:
            return
        future: asyncio.Future[None] = self._get_loop().create_future()
        self._flush_resolvers.append(future)
        await future

    def mark_completed(self) -> None:
        """mark_completed(): 标记 pipeline 完成，取消定时器，唤醒所有等待者."""
        self._completed = True
        self._cancel_timer()
        for r in self._flush_resolvers:
            if not r.done():
                r.set_result(None)
        self._flush_resolvers.clear()

    def set_throttle(self, ms: float) -> None:
        self._throttle_ms = ms

    def set_card_message_ready(self, ready: bool) -> None:
        """set_card_message_ready(): 标记卡片消息已创建，开始接受 schedule_update."""
        self._card_message_ready = ready
        if ready:
            self._last_update_time = time.monotonic()

    def _schedule(self, delay: float, do_flush: Callable[[], Awaitable[None]]) -> None:
        """_schedule(): 注册延迟定时器，到点后走 _do_flush_task → _create_flush_task → _do_flush 链路."""
        self._cancel_timer()
        self._pending_timer = self._get_loop().call_later(
            delay,
            self._do_flush_task,
            do_flush,
        )

    def _do_flush_task(self, do_flush: Callable[[], Awaitable[None]]) -> None:
        """_do_flush_task(): 定时器回调入口，转为 call_soon 确保在事件循环主队列执行."""
        self._pending_timer = None
        self._get_loop().call_soon(self._create_flush_task, do_flush)

    def _create_flush_task(self, do_flush: Callable[[], Awaitable[None]]) -> None:
        """_create_flush_task(): 创建 asyncio Task 并持有引用 —— 不持有 = GC 吃掉任务（Python 文档要求）."""
        task = self._get_loop().create_task(self._do_flush(do_flush))
        self._pending_flush_tasks.add(task)
        task.add_done_callback(self._pending_flush_tasks.discard)

    async def _do_flush(self, do_flush: Callable[[], Awaitable[None]]) -> None:
        """_do_flush(): 核心 flush 执行器，带重入锁 + reflush 检测.
        重入锁：_flush_in_progress=True 期间再来 flush 请求 → 设 _needs_reflush=True，等当前完成后补刷。
        改了会怎样：去掉 reflush → 用户看到"少了最后一段"。
        """
        if self._completed or self._flush_in_progress:
            self._needs_reflush = True
            return

        self._flush_in_progress = True
        self._needs_reflush = False
        try:
            await do_flush()
        except Exception:
            _logger.warning("flush error suppressed", exc_info=True)
        finally:
            self._flush_in_progress = False
            self._last_update_time = time.monotonic()
            # 唤醒等待者
            resolvers = self._flush_resolvers
            self._flush_resolvers = []
            for r in resolvers:
                if not r.done():
                    r.set_result(None)

        # 如果 flush 期间又有新数据 → 立即重刷
        if self._needs_reflush and not self._completed:
            self._needs_reflush = False
            self._get_loop().call_soon(self._create_flush_task, do_flush)

    def _cancel_timer(self) -> None:
        if self._pending_timer is not None:
            self._pending_timer.cancel()
            self._pending_timer = None
