"""SessionManager — thread-safe session CRUD, pruning, and cleanup.

Extracted from the original StreamCardController God Object (controller/core.py).
All session operations are protected by a single RLock to avoid deadlock.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, TYPE_CHECKING

from ..state.session import CardSession
from ..state.text import TextState
from ..state.tooluse import ToolUseTracker

if TYPE_CHECKING:
    from collections.abc import Callable

_logger = logging.getLogger("hermes_lark_streaming")

__all__ = ["SessionManager"]


class SessionManager:
    """Thread-safe card session container with TTL-based pruning.

    Lifecycle:
      - ``_sess_put`` registers a session (by message_id and optionally anchor_id).
      - ``_prune_stale_sessions`` is called before new-session creation to
        garbage-collect terminal sessions past TTL.
      - ``_cleanup`` removes a single session from all internal structures.
      - ``_release_session_data`` drops heavy payload after card seal,
        keeping only minimal metadata for TTL tracking.

    Thread safety:
      All reads/writes to ``_sessions`` go through ``_sessions_lock`` (RLock).
      The caller of ``_cleanup`` MUST also hold the appropriate map locks
      (interrupt/continuation) to avoid TOCTOU races — see ``set_cleanup_maps``.
    """

    def __init__(self, session_ttl: float = 1800.0) -> None:
        self._sessions: dict[str, CardSession] = {}
        self._sessions_lock = threading.RLock()
        self._session_ttl: float = session_ttl

        # Optional collaborators for _cleanup (set via set_cleanup_maps).
        # These are NOT owned by SessionManager — they belong to
        # InterruptResolver. They are stored here only so that _cleanup
        # can clean interrupt/continuation maps in one atomic pass
        # without the controller having to coordinate the call.
        self._interrupt_map: dict[str, str] | None = None
        self._interrupt_map_lock: threading.Lock | None = None
        self._continuation_map: dict[str, str] | None = None
        self._continuation_map_lock: threading.Lock | None = None

    # ── Map injection (called once during controller init) ────────────

    def set_cleanup_maps(
        self,
        *,
        interrupt_map: dict[str, str],
        interrupt_map_lock: threading.Lock,
        continuation_map: dict[str, str],
        continuation_map_lock: threading.Lock,
    ) -> None:
        """Wire in interrupt/continuation maps so ``_cleanup`` can purge
        stale entries. Must be called before any session activity."""
        self._interrupt_map = interrupt_map
        self._interrupt_map_lock = interrupt_map_lock
        self._continuation_map = continuation_map
        self._continuation_map_lock = continuation_map_lock

    # ── Thread-safe session CRUD ─────────────────────────────────────

    def _sess_get(self, message_id: str) -> CardSession | None:
        """Thread-safe session lookup by message_id (or anchor_id)."""
        with self._sessions_lock:
            return self._sessions.get(message_id)

    def _sess_put(self, key: str, session: CardSession) -> None:
        """Thread-safe session store."""
        with self._sessions_lock:
            self._sessions[key] = session

    def _sess_pop(self, key: str) -> CardSession | None:
        """Thread-safe session removal (returns the removed session or None)."""
        with self._sessions_lock:
            return self._sessions.pop(key, None)

    def _sess_items_snapshot(self) -> list[tuple[str, CardSession]]:
        """Thread-safe snapshot of all (key, session) pairs."""
        with self._sessions_lock:
            return list(self._sessions.items())

    def _sess_values_snapshot(self) -> list[CardSession]:
        """Thread-safe snapshot of all sessions (values only)."""
        with self._sessions_lock:
            return list(self._sessions.values())

    def _sess_active_count(self) -> int:
        """Thread-safe count of non-terminal (active) sessions."""
        with self._sessions_lock:
            return sum(1 for s in self._sessions.values() if not s.is_terminal_phase)

    def _sess_clear(self) -> None:
        """Thread-safe clear of all sessions (used by unregister)."""
        with self._sessions_lock:
            self._sessions.clear()

    # ── TTL pruning ──────────────────────────────────────────────────

    def _prune_stale_sessions(self) -> None:
        """v1.1.1: Only prune terminal sessions past TTL; protect active sessions."""
        now = time.time()
        # v1.3.0 P1-01: use thread-safe snapshot to avoid RuntimeError.
        for mid, s in self._sess_items_snapshot():
            if mid is None or now - s.created_at <= self._session_ttl:
                continue
            if s.is_terminal_phase:
                _logger.warning("pruning stale terminal session: msg=%s", (mid or "?")[:20])
                self._cleanup(mid)
            else:
                # Active session over TTL — log but do NOT clean up
                # (avoid losing AI callback data).
                _logger.warning(
                    "HLS: active session over TTL but not terminal, skip cleanup: msg=%s",
                    (mid or "?")[:20],
                )

    # ── Single-session cleanup ───────────────────────────────────────

    def _cleanup(self, message_id: str) -> None:
        """Remove session from all maps (sessions + interrupt + continuation).

        Lock ordering:
          1. ``_sessions_lock`` (RLock — may be re-entered)
          2. ``_interrupt_map_lock`` (Lock)
          3. ``_continuation_map_lock`` (Lock)

        The caller MUST NOT hold any of these locks when calling this method;
        the locks are acquired internally in the order above to prevent deadlock.
        """
        session = self._sess_pop(message_id)
        if session is None:
            return

        anchor = getattr(session, "anchor_id", None)
        if anchor:
            with self._sessions_lock:
                if self._sessions.get(anchor) is session:
                    del self._sessions[anchor]

        # Clean interrupt map — remove entries where this message_id is the value.
        if self._interrupt_map is not None and self._interrupt_map_lock is not None:
            with self._interrupt_map_lock:
                stale_keys = [k for k, v in self._interrupt_map.items() if v == message_id]
                for k in stale_keys:
                    del self._interrupt_map[k]

        # v1.4.0 fix: clean continuation map — remove entries where this
        # message_id is either the key (old) or the value (new).
        if self._continuation_map is not None and self._continuation_map_lock is not None:
            with self._continuation_map_lock:
                self._continuation_map.pop(message_id, None)
                stale_cont_keys = [k for k, v in self._continuation_map.items() if v == message_id]
                for k in stale_cont_keys:
                    del self._continuation_map[k]

        session.flush.mark_completed()

    # ── Heavy-data release ───────────────────────────────────────────

    def _release_session_data(self, session: CardSession) -> None:
        """After card seal, drop heavy payload; keep minimal metadata for TTL tracking."""
        session.unified_state = None
        if session.text is not None:
            session.text = TextState()  # type: ignore[assignment]
        session.tool_use = ToolUseTracker()  # type: ignore[assignment]
        session.footer = {}
