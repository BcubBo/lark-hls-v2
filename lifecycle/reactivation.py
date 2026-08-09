"""ContinuationReactivation — card reactivation after streaming closure.

Extracted from the original StreamCardController God Object (controller/core.py).

v1.4.0 fix (问题3 根因1): When a streaming card receives error 300309
(CARDKIT_STREAMING_CLOSED) the card can no longer accept updates, but the
session may still be active (e.g. delegate_task spawned a sub-request).
This module creates a new "continuation" card so the output can keep
streaming without falling back to plain text.

Race-condition guards:
  - ``_continuation_reactivation_count >= 1``: at most one reactivation
    per stale session — even if the new card also hits 300309.
  - ``_is_continuation``: continuation sessions are never re-activated
    recursively.
  - ``is_terminal_phase``: sessions that already completed/aborted/failed
    are skipped — late tokens from a race condition are discarded.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any, TYPE_CHECKING

from ..state.session import CardSession
from ..state.linear import UnifiedLinearState

if TYPE_CHECKING:
    from .manager import SessionManager
    from .interrupt import InterruptResolver

_logger = logging.getLogger("hermes_lark_streaming")

__all__ = ["ContinuationReactivation"]


class ContinuationReactivation:
    """Handles creation of continuation cards when streaming is closed.

    Dependencies (injected at construction):
      - ``session_mgr``: provides session CRUD (``_sess_get``, ``_sess_put``).
      - ``interrupt_resolver``: provides continuation map (``_resolve_continuation_id``,
        ``_register_continuation``).
      - ``get_loop``: callable returning the current ``asyncio.AbstractEventLoop``.
      - ``fire_and_forget``: callable to schedule a coroutine on the event loop.
      - ``do_create_linear_card``: callable(session) → coroutine — creates the
        new card. Owned by the controller's linear mixin.
      - ``do_linear_complete_with_fallback``: callable(session) → coroutine —
        completes the stale session. Owned by the controller's linear mixin.
    """

    def __init__(
        self,
        *,
        session_mgr: SessionManager,
        interrupt_resolver: InterruptResolver,
        get_loop: Any,  # Callable[[], asyncio.AbstractEventLoop | None]
        fire_and_forget: Any,  # Callable[[Coroutine, AbstractEventLoop], None]
        do_create_linear_card: Any,  # Callable[[CardSession], Coroutine]
        do_linear_complete_with_fallback: Any,  # Callable[[CardSession], Coroutine]
    ) -> None:
        self._session_mgr = session_mgr
        self._interrupt_resolver = interrupt_resolver
        self._get_loop = get_loop
        self._fire_and_forget = fire_and_forget
        self._do_create_linear_card = do_create_linear_card
        self._do_linear_complete_with_fallback = do_linear_complete_with_fallback

    # ── Public entry point ───────────────────────────────────────────

    def _maybe_reactivate_for_continuation(self, message_id: str) -> str | None:
        """Check and, if needed, trigger session reactivation for continuation."""
        # 1. Already mapped → return directly (idempotent)
        existing = self._interrupt_resolver._resolve_continuation_id(message_id)
        if existing is not None:
            return existing

        # 2. Check if original session is in "streaming closed but not terminal" state
        stale = self._session_mgr._sess_get(message_id)
        if stale is None:
            return None  # No original session, cannot reactivate
        # Terminal sessions (COMPLETED/ABORTED/CREATION_FAILED/TERMINATED)
        # should NOT be reactivated — on_completed already sealed the card;
        # late tokens are a race condition that should be discarded.
        if stale.is_terminal_phase:
            return None
        # _streaming_closed=False means streaming is healthy, normal path
        if not stale._streaming_closed:
            return None
        # Guard against recursion: continuation sessions are never re-activated
        if stale._is_continuation:
            return None
        # Limit to at most 1 reactivation (extreme case: new card also hits 300309)
        if stale._continuation_reactivation_count >= 1:
            return None

        # 3. Trigger reactivation
        new_session = self._reactivate_session_for_continuation(stale)
        if new_session is None:
            return None
        self._interrupt_resolver._register_continuation(message_id, new_session.message_id)
        return new_session.message_id

    # ── Core reactivation logic ──────────────────────────────────────

    def _reactivate_session_for_continuation(
        self,
        stale_session: CardSession,
    ) -> CardSession | None:
        """Create a new streaming card to continue output from a stale session.

        The new session:
          - Gets a new message_id: ``<anchor_id>-cont-<seq>``.
          - Inherits the original ``anchor_id`` so replies land in the same thread.
          - Is marked ``_is_continuation = True`` and ``linear = True``.
          - Gets a pre-created ``UnifiedLinearState``.

        The stale session is moved to COMPLETING so the old card can be sealed.
        """
        chat_id = stale_session.chat_id
        # anchor_id first (user's original message id), fallback to message_id
        anchor_id = stale_session.anchor_id or stale_session.message_id
        if not chat_id or not anchor_id:
            _logger.warning(
                "HLS: reactivation aborted — missing chat_id/anchor_id "
                "old_msg=%s chat=%s anchor=%s",
                (stale_session.message_id or "?")[:12],
                (chat_id or "?")[:12],
                (anchor_id or "?")[:12],
            )
            return None

        loop = self._get_loop()
        if loop is None:
            _logger.warning(
                "HLS: reactivation aborted — no event loop old_msg=%s",
                (stale_session.message_id or "?")[:12],
            )
            return None

        # Mark stale_session as reactivated (prevent duplicate triggers, limit to 1)
        stale_session._continuation_reactivation_count += 1

        # Generate new message_id with -cont-<seq> suffix for log correlation
        seq = stale_session._continuation_reactivation_count
        new_message_id = f"{anchor_id}-cont-{seq}"

        # Defensive check: prevent collision with existing session
        # (theoretically -cont-1 suffix won't collide, but be safe)
        with self._session_mgr._sessions_lock:
            if new_message_id in self._session_mgr._sessions:
                _logger.warning(
                    "HLS: reactivation aborted — new message_id already exists "
                    "old_msg=%s new_msg=%s",
                    (stale_session.message_id or "?")[:12],
                    new_message_id[:12],
                )
                return None

        new_session = CardSession(new_message_id, chat_id, loop)
        # anchor_id: reply to user's original message (keep thread context)
        new_session.anchor_id = anchor_id if anchor_id != new_message_id else None
        new_session._is_continuation = True
        # v1.4.0 fix: pre-create unified_state + mark linear=True to avoid
        # on_answer falling back to plain text.
        new_session.linear = True
        new_session.unified_state = UnifiedLinearState()
        self._session_mgr._sess_put(new_message_id, new_session)
        # Do NOT claim the anchor_id key — original session may still use it
        # as an alias key. New session is indexed only by new_message_id
        # (avoids overwriting the alias and causing false cleanup).

        _logger.info(
            "HLS: reactivating card session for continued output after tool "
            "(delegate_task?) old_msg=%s new_msg=%s chat=%s trace=%s old_state=%s",
            (stale_session.message_id or "?")[:12],
            new_message_id[:12],
            chat_id[:12],
            new_session.card_trace_id,
            stale_session.state,
        )

        # Async trigger new card creation (IDLE guard in _do_create_linear_card
        # ensures idempotency)
        self._fire_and_forget(self._do_create_linear_card(new_session), loop)

        # Move stale session to COMPLETING so it can be sealed
        try:
            from ..state.phase import CardPhase

            if (
                not stale_session.is_terminal_phase
                and stale_session.state != CardPhase.COMPLETING
            ):
                stale_session.state = CardPhase.COMPLETING
                self._fire_and_forget(
                    self._do_linear_complete_with_fallback(stale_session),
                    stale_session._loop,
                )
        except Exception:
            pass

        return new_session
