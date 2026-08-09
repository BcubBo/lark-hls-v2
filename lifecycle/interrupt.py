"""InterruptResolver — interrupt-chain and continuation-map management.

Extracted from the original StreamCardController God Object (controller/core.py).

Race condition notes:
  - ``_interrupt_map`` is written from the event-loop thread (on_interrupted
    writes, on_completed pops) and iterated from worker threads (_cleanup).
    A dedicated Lock (not RLock) protects it — separate from ``_sessions_lock``
    to avoid holding both locks simultaneously (deadlock risk).
  - ``_continuation_map`` uses the same pattern — a dedicated Lock.
  - Lock ordering: ``_sessions_lock`` → ``_interrupt_map_lock`` →
    ``_continuation_map_lock``. Never acquire in reverse.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

_logger = logging.getLogger("hermes_lark_streaming")

# v1.3.2: module-level constant (was previously re-defined on every on_interrupted call)
_INTERRUPT_MAP_MAX = 200

__all__ = ["InterruptResolver"]


class InterruptResolver:
    """Manages interrupt-chain redirection and continuation mapping.

    Two separate maps:
      1. **interrupt_map** — ``old_message_id → new_message_id``.
         When user sends a new message while an old one is streaming,
         on_interrupted records the redirect. on_completed pops it.
         Chain-extended: if A→B already exists and B→C arrives, A is
         updated to C.

      2. **continuation_map** — ``old_message_id → continuation_message_id``.
         When a streaming card is closed (300309) but the session is still
         active, a new card is created as a "continuation". This map
         lets on_completed redirect to the new card.
    """

    def __init__(self) -> None:
        self._interrupt_map: dict[str, str] = {}
        # v1.3.0: _interrupt_map is accessed from event-loop thread
        # (on_interrupted writes, on_completed pops) and worker threads
        # (_cleanup iterates+deletes). A dedicated Lock (not RLock) to
        # avoid holding _sessions_lock simultaneously → deadlock risk.
        self._interrupt_map_lock = threading.Lock()

        # v1.4.0 fix (问题3 根因1 — delegate_task 后卡片降级纯文本):
        self._continuation_map: dict[str, str] = {}
        self._continuation_map_lock = threading.Lock()

    # ── Continuation map operations ──────────────────────────────────

    def _resolve_continuation_id(self, message_id: str) -> str | None:
        """Query whether message_id has been reactivated to a continuation session."""
        with self._continuation_map_lock:
            return self._continuation_map.get(message_id)

    def _register_continuation(self, old_message_id: str, new_message_id: str) -> None:
        """Record old_message_id → new_message_id continuation mapping. Thread-safe."""
        with self._continuation_map_lock:
            self._continuation_map[old_message_id] = new_message_id

    def _pop_continuation_id(self, message_id: str) -> str | None:
        """Pop and delete the continuation id for message_id (one-shot consumption
        by on_completed)."""
        with self._continuation_map_lock:
            return self._continuation_map.pop(message_id, None)

    # ── Interrupt map operations ─────────────────────────────────────

    def register_interrupt(
        self,
        old_message_id: str,
        new_message_id: str,
    ) -> None:
        """Record old→new interrupt redirect with chain-extension and LRU eviction.

        Called from ``on_interrupted`` in the event-loop thread.
        """
        with self._interrupt_map_lock:
            self._interrupt_map[old_message_id] = new_message_id
            # Chain extension: if any key already points to old_message_id,
            # redirect it to new_message_id as well.
            for key, val in list(self._interrupt_map.items()):
                if val == old_message_id:
                    self._interrupt_map[key] = new_message_id
            # Prevent unbounded growth: keep only the most recent entries.
            if len(self._interrupt_map) > _INTERRUPT_MAP_MAX:
                excess = len(self._interrupt_map) - _INTERRUPT_MAP_MAX
                for old_key in list(self._interrupt_map.keys())[:excess]:
                    self._interrupt_map.pop(old_key, None)

    def pop_interrupt_redirect(self, message_id: str) -> str | None:
        """Pop and return the interrupt redirect for message_id.

        Called from ``on_completed`` to follow the redirect chain.
        """
        with self._interrupt_map_lock:
            return self._interrupt_map.pop(message_id, None)

    def get_interrupt_redirect(self, message_id: str) -> str | None:
        """Non-consuming lookup of the interrupt redirect for message_id."""
        with self._interrupt_map_lock:
            return self._interrupt_map.get(message_id)
