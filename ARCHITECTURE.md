# lark-hls-v2 Architecture — Complete Codebase Analysis

> Generated: 2026-08-11 | Source: 33 Python files, ~10,000 lines (excluding tests)

---

## 1. Module Map

```
lark-hls-v2/
├── __init__.py              (22L)  — Version extraction, exports register()
├── linear_mixin.py          (1258L) — CORE: streaming card lifecycle (create→flush→seal)
├── controller.py            (780L)  — StreamCardController singleton, session orchestration
│
├── card/                          — Card JSON construction
│   ├── __init__.py          (8L)   — Wildcard re-exports all sub-modules
│   ├── builder.py           (182L) — Card assemblers (streaming, cron, gateway)
│   ├── elements.py          (931L) — Primitive builders (panels, footer, tools, errors)
│   ├── i18n.py              (88L)  — Bilingual (zh_cn/en_us) text map
│   ├── md.py                (170L) — Markdown processing (table downgrade, code blocks, escaping)
│   ├── special.py           (362L) — Clarify card (3-state), cron card, gateway card
│   └── styles.py            (40L)  — ⚠️ DEAD CODE — CardStyles/DEFAULT_STYLES never used
│
├── config/                        — Configuration
│   ├── __init__.py          (15L)  — Re-exports Config + defaults.*
│   ├── defaults.py          (103L) — Single source of truth for all defaults
│   └── schema.py            (337L) — Config singleton (lazy YAML reader, TTL cache)
│
├── feishu/                        — Feishu API layer
│   ├── __init__.py          (51L)  — Re-exports client + guard symbols
│   ├── client.py            (627L) — FeishuClient (lark-oapi SDK wrapper, retry logic)
│   └── guard.py             (146L) — UnavailableGuard (message deleted/recalled detection)
│
├── flush/                         — Flush throttling
│   ├── __init__.py          (15L)  — Re-exports FlushController
│   └── controller.py        (166L) — Throttled async flush with timer scheduling
│
├── state/                         — Session state objects
│   ├── __init__.py          (54L)  — Re-exports all state symbols
│   ├── linear.py            (176L) — UnifiedLinearState (reasoning rounds + answer + dirty flags)
│   ├── phase.py             (88L)  — CardPhase enum + legal transition matrix
│   ├── session.py           (206L) — CardSession (__slots__-optimized, owns guard/flush/state)
│   ├── text.py              (94L)  — TextState + reasoning tag parsing
│   └── tooluse.py           (302L) — ToolUseTracker (step recording, display building, redaction)
│
├── interceptors/                  — Runtime monkey patching
│   ├── __init__.py          (629L) — apply_patches() orchestrator, FeishuAdapter patch registry
│   ├── adapter.py           (969L) — FeishuAdapter send/edit/reaction/clarify wrappers
│   ├── callbacks.py         (233L) — AIAgent callback wrapping (stream/think/tool/reasoning)
│   ├── gateway.py           (736L) — GatewayRunner method wrappers + cron delivery
│   ├── hermes_compat.py     (193L) — HermesCompat: version detection + module resolution
│   └── hooks.py             (219L) — Hook functions (on_message_started → on_cron_deliver)
│
├── lifecycle/                     — ⚠️ ENTIRELY DEAD CODE — extracted but never wired in
│   ├── __init__.py          (14L)  — Re-exports SessionManager, InterruptResolver, ContinuationReactivation
│   ├── manager.py           (181L) — SessionManager (duplicate of controller.py inline logic)
│   ├── interrupt.py         (113L) — InterruptResolver (duplicate of controller.py inline logic)
│   └── reactivation.py      (205L) — ContinuationReactivation (duplicate of controller.py inline logic)
│
├── aowen/                         — /aowen command handler + metrics
│   └── __init__.py          (734L) — Metrics, card builders, /aowen command dispatch
│
└── plugin/                        — Plugin registration
    └── __init__.py          (188L) — register()/unregister(), config injection, pre-warm
```

---

## 2. Data Flow: User Message → Card → Streaming → Seal

### 2.1 Inbound Path (Message Arrives)

```
Feishu Platform
  │
  ▼
Hermes Gateway (gateway.run.GatewayRunner)
  │
  ├─ _handle_message()          ← PATCHED by interceptors/gateway.py
  │   └─ on_feishu_normalize()  ← hooks.py: fixes thread_id on quoted messages
  │   └─ /aowen command check   ← interceptors/gateway.py: early exit for /aowen
  │
  ├─ _handle_message_with_agent()  ← PATCHED
  │   ├─ on_message_started()      ← hooks.py → controller.on_message_started()
  │   │   └─ Creates CardSession, fires _do_create_linear_card()
  │   │
  │   └─ Sets up msg_context in contextvars (_msg_ctx)
  │
  └─ _run_agent() / run_conversation()  ← PATCHED
      └─ _maybe_wrap_callbacks(agent)   ← callbacks.py: wraps 6 agent callbacks
          ├─ stream_delta_callback      → on_answer_delta()   → controller.on_answer()
          ├─ interim_assistant_callback → on_thinking_delta()  → controller.on_thinking()
          ├─ reasoning_callback         → on_reasoning_delta() → controller.on_reasoning()
          ├─ tool_progress_callback     → on_tool_updated()    → controller.on_tool_update()
          └─ background_review_callback → on_background_review_message()
```

### 2.2 Card Lifecycle (State Machine)

```
IDLE ──→ CREATING ──→ STREAMING ──→ COMPLETING ──→ COMPLETED
  │         │             │             │
  │         ▼             │             ▼
  │    CREATION_FAILED    │        ABORTED / TERMINATED
  │                       │
  └─── (any phase) ──→ TERMINATED  (message deleted/recalled)
```

**Phase Transitions** (from `state/phase.py`):
- `IDLE → {CREATING, ABORTED, TERMINATED}`
- `CREATING → {STREAMING, CREATION_FAILED, TERMINATED}`
- `STREAMING → {COMPLETING, ABORTED, TERMINATED}`
- `COMPLETING → {COMPLETED, CREATION_FAILED, ABORTED, TERMINATED}`
- All terminal phases: `{COMPLETED, CREATION_FAILED, ABORTED, TERMINATED}`

### 2.3 Card Creation (`_do_create_linear_card`)

```
_do_create_linear_card(session)
  │
  ├─ session.state = CREATING
  ├─ session.unified_state = UnifiedLinearState()  (if None)
  │
  ├─ FeishuClient.cardkit_create(card)
  │   └─ build_streaming_card_v2()
  │       ├─ include_unified_panel=False  (panel added on first token)
  │       ├─ include_answer_element=False
  │       └─ include_loading_hint=True    ("正在加载上下文...")
  │
  ├─ FeishuClient.reply_card_by_id(reply_to, card_id)
  │
  ├─ session.card_id = card_id
  ├─ session.card_msg_id = card_msg_id
  ├─ session.existing_elements = {_LOADING_HINT_ELEMENT_ID, _LOADING_ELEMENT_ID}
  │
  ├─ session.flush.set_card_message_ready(True)
  ├─ session._card_ready.set()
  └─ session.state = STREAMING
```

### 2.4 Streaming Flush (`_do_unified_flush`)

```
_do_unified_flush(session)
  │
  ├─ Phase 2: First content arrival (answer element + panel creation)
  │   ├─ build_unified_panel() → collapsible_panel JSON
  │   ├─ _streaming_element(ANSWER_ELEMENT_ID) → markdown element
  │   ├─ add_elements (insert_before loading_hint)
  │   ├─ delete_elements (loading_hint)
  │   └─ cardkit_batch_update(card_id, actions, sequence++)
  │
  └─ Phase 3: Subsequent updates (existing elements)
      ├─ partial_update_element (panel header + children)
      ├─ partial_update_element (answer content)
      ├─ cardkit_stream_element (answer text, typewriter effect)
      └─ cardkit_batch_update / cardkit_stream_element
```

**Flush Throttling** (`flush/controller.py`):
- Default interval: 180ms (`FLUSH_INTERVAL_MS`)
- Answer-only mode: 150ms (`ANSWER_FAST_STREAM_MS`)
- Long gap (>1s idle): 150ms batch delay
- First token: immediate flush (首字即显)

### 2.5 Card Sealing (`_finalize_card`)

```
_finalize_card(session)
  │
  ├─ Drain dirty data (panel + answer text)
  │   ├─ partial_update_element (final panel state)
  │   └─ cardkit_stream_element (final answer text)
  │
  ├─ Build seal actions
  │   ├─ partial_update_element (panel → final, expanded=False)
  │   ├─ partial_update_element (answer → optimized markdown)
  │   ├─ build_seal_actions()
  │   │   ├─ _build_error_panel() (if error)
  │   │   ├─ _build_background_review_panel() (if reviews exist)
  │   │   ├─ _build_footer_elements() → hr + markdown with <text_tag>
  │   │   ├─ delete_elements (loading_hint)
  │   │   └─ delete_elements (loading_icon)
  │   │
  │   └─ Element count enforcement (trim panel if >195 elements)
  │
  ├─ cardkit_batch_update(card_id, seal_actions, sequence++)
  │
  ├─ cardkit_close_streaming(card_id, summary=seal_summary)
  │   └─ summary = first 120 chars of answer (or last reasoning round)
  │
  └─ session.state = COMPLETED (or ABORTED/CREATION_FAILED)
```

---

## 3. Key Call Chains

### 3.1 Answer Streaming Chain

```
agent.stream_delta_callback(text)
  → _answer_wrapper(text)                    [callbacks.py:68]
    → on_answer_delta(message_id, text)      [hooks.py:152]
      → ctrl.on_answer(message_id, text)     [controller.py:407]
        → _maybe_reactivate_for_continuation()  [controller.py:187]
        → strip_reasoning_tags(text)         [state/text.py:53]
        → session.unified_state.on_answer_delta(text)  [state/linear.py:97]
          → _finalize_current_reasoning()    [state/linear.py:117]
          → self.answer_text += text
          → self.answer_dirty = True
        → _schedule_linear_flush(session)    [linear_mixin.py:212]
          → session.flush.schedule_update()  [flush/controller.py:56]
            → _do_unified_flush(session)     [linear_mixin.py:255]
              → cardkit_stream_element()     [feishu/client.py:399]
```

### 3.2 Thinking/Reasoning Chain

```
agent.interim_assistant_callback(text)
  → _thinking_wrapper(text)                  [callbacks.py:111]
    → on_thinking_delta(message_id, text)    [hooks.py:158]
      → ctrl.on_thinking(message_id, text)   [controller.py:340]
        → _upgrade_loading_hint_to_thinking()  [linear_mixin.py:562]
        → _linear_on_thinking(session, text)   [linear_mixin.py:589]
          → split_reasoning_text(text)         [state/text.py:22]
          → state.on_reasoning_delta(reasoning)  [state/linear.py:67]
          → state.on_answer_delta(answer)        [state/linear.py:97]
          → _schedule_linear_flush(session)      [linear_mixin.py:212]
```

### 3.3 Tool Progress Chain

```
agent.tool_progress_callback(event_type, tool_name, preview)
  → _tool_wrapper(event_type, tool_name, preview)  [callbacks.py:150]
    → on_tool_updated(message_id, tool_name, status, detail)  [hooks.py:134]
      → ctrl.on_tool_update(message_id, tool_name, status, detail)  [controller.py:378]
        → session.tool_use.record_start() or record_end()  [state/tooluse.py:232,246]
        → session.unified_state.on_tool_event()  [state/linear.py:103]
        → _schedule_linear_flush(session)          [linear_mixin.py:212]
```

### 3.4 Completion Chain

```
_run_agent returns → _wrap_run_conversation finally block  [gateway.py]
  → on_message_completed(message_id, answer, duration, model, tokens, ...)  [hooks.py:95]
    → ctrl.on_completed(message_id, ...)  [controller.py:511]
      → _pop_continuation_id() / interrupt_map redirect
      → session.footer = {duration, model, tokens, ...}
      → session.state = COMPLETING
      → _dispatch_completion(session)  [controller.py:646]
        → _complete_with_fallback(session)  [controller.py:650]
          → _complete_card_flow(session)    [linear_mixin.py:1028]
            → session.flush.wait_for_flush()
            → drain dirty data (up to 8 rounds, 20ms yield)
            → session.flush.mark_completed()
            → session._card_ready.wait() (timeout 30s)
            → _finalize_card(session)       [linear_mixin.py:622]
            → _reset_session_state(session) [controller.py:271]
            → record_card_completed()       [aowen/__init__.py:38]
```

### 3.5 Interrupt Chain

```
on_message_started(new_message_id, chat_id)  [controller.py:289]
  → prune stale sessions
  → for each existing active session in same chat:
    → on_interrupted(old_msg_id, new_msg_id, chat_id)  [controller.py:450]
      → old_session._was_aborted = True
      → wait for in-progress flush (3s timeout)
      → old_session.state = ABORTED
      → _dispatch_completion(old_session)
  → create new session, fire _do_create_linear_card()
```

### 3.6 Continuation Reactivation Chain

```
on_answer(text) for closed streaming card
  → _maybe_reactivate_for_continuation(message_id)  [controller.py:187]
    → Check: session exists, non-terminal, _streaming_closed=True, not already continuation
    → _reactivate_session_for_continuation(stale_session)  [controller.py:210]
      → new_session = CardSession("{anchor_id}-cont-{seq}", chat_id, loop)
      → new_session._is_continuation = True
      → new_session.linear = True
      → new_session.unified_state = UnifiedLinearState()
      → _fire_and_forget(_do_create_linear_card(new_session))
      → stale_session.state = COMPLETING
      → _fire_and_forget(_complete_with_fallback(stale_session))
    → _register_continuation(old_msg_id, new_msg_id)
    → return new_msg_id (subsequent answer tokens go to new card)
```

---

## 4. Dead Code Inventory

### 4.1 Critical: Entire `lifecycle/` Module (4 files, ~513 lines)

**Files**: `lifecycle/__init__.py`, `lifecycle/manager.py`, `lifecycle/interrupt.py`, `lifecycle/reactivation.py`

**Status**: **COMPLETELY DEAD** — extracted from `controller.py` but never wired in.

The `StreamCardController` in `controller.py` has its own inline implementations of:
- Session CRUD (`_sess_get`, `_sess_put`, `_sess_pop`, `_sess_items_snapshot`, etc.)
- Interrupt map management (`_interrupt_map`, `_interrupt_map_lock`)
- Continuation map management (`_continuation_map`, `_continuation_map_lock`)
- `_cleanup()`, `_reset_session_state()`, `_prune_stale_sessions()`
- `_reactivate_session_for_continuation()`, `_maybe_reactivate_for_continuation()`

The `lifecycle/` module's `SessionManager`, `InterruptResolver`, and `ContinuationReactivation` classes are exact duplicates of this logic, intended as a refactoring target that was never completed.

**Recommendation**: Either wire them into the controller (replace inline logic) or delete them entirely.

### 4.2 `card/styles.py` (40 lines)

**Status**: **COMPLETELY DEAD** — `CardStyles` and `DEFAULT_STYLES` are defined but never imported or used anywhere outside this file. The actual styling is done via `_panel_border_color` and `_panel_header_color` in `elements.py` (loaded from `config/defaults.py`).

**Recommendation**: Delete or integrate with `elements.py` color system.

### 4.3 `_enforce_card_element_limit()` in `card/builder.py`

**Status**: **IMPORTED BUT NEVER CALLED** — imported by `linear_mixin.py` and exported in `__all__`, but the function is never actually invoked. The element limit enforcement is done inline in `_finalize_card()` (linear_mixin.py:788-872) with duplicated logic.

**Recommendation**: Either call it from `_finalize_card()` or remove the import.

### 4.4 `_build_summary()` in `card/builder.py`

**Status**: **DEAD** — defined at line 125 but never called. The summary is built inline in `FeishuClient.cardkit_close_streaming()`.

### 4.5 `STREAMING_ELEMENT_ID` in `card/elements.py`

**Status**: **EFFECTIVELY DEAD** — defined as `"streaming_content"` but the actual streaming uses `ANSWER_ELEMENT_ID` (`"answer_content"`). The old `STREAMING_ELEMENT_ID` is only referenced in `_streaming_element()`'s default parameter, which is always overridden with `ANSWER_ELEMENT_ID` in practice.

### 4.6 `_MAX_CARD_TABLES` in `card/md.py`

**Status**: **USED** — referenced as default parameter in `_downgrade_tables()`. Not dead.

---

## 5. Unused Imports

### 5.1 `linear_mixin.py`
- `CARDKIT_SCHEMA_ERROR` — imported but never used (schema errors are checked via `is_schema_error()`)
- `_enforce_card_element_limit` — imported but never called

### 5.2 `controller.py`
- `CARDKIT_SCHEMA_ERROR` — imported but never used
- `CARDKIT_STREAMING_CLOSED` — imported but never used (used in linear_mixin via mixin inheritance)
- `FeishuAPIError` — imported but only used in TYPE_CHECKING context
- `FlushController` — imported but FlushController is created inside CardSession
- `TERMINAL_PHASES` — imported but `is_terminal_phase` property on session handles this
- `TerminalReason` — imported but only used in TYPE_CHECKING
- `UnavailableGuard` — imported but created inside CardSession
- `_TERMINAL` — imported but never used
- `is_element_not_found_error`, `is_schema_error`, `is_terminal_api_code` — imported but never called directly

### 5.3 `card/builder.py`
- `json` — imported but never used (card JSON serialization is done in FeishuClient)
- `ReasoningRound` — TYPE_CHECKING import, acceptable
- `STREAMING_ELEMENT_ID`, `_LOADING_ELEMENT_ID`, `_LOADING_HINT_ELEMENT_ID` — imported but never used
- `_build_footer_elements`, `_collapsible_panel`, `build_unified_panel` — imported but never called

### 5.4 `card/elements.py`
- `_LOCALES` — imported but never used
- `_downgrade_tables`, `_split_long_text`, `optimize_markdown_style` — imported from md.py but never called

### 5.5 `interceptors/__init__.py`
- `datetime`, `timezone`, `timedelta` — imported but never used

### 5.6 `interceptors/hermes_compat.py`
- `Optional` — imported but never used

---

## 6. Undocumented Data Flows

### 6.1 Context Propagation via `contextvars`

```
_msg_ctx: contextvars.ContextVar[dict]     ← interceptors/__init__.py:97
_thread_local_ctx: threading.local()        ← interceptors/__init__.py:88
```

The `_msg_ctx` contextvar carries message context through the async call chain:
- Set by `_wrap_handle_message_with_agent()` in gateway.py
- Read by `_wrap_feishu_adapter_send()` in adapter.py (to suppress duplicate text replies)
- Read by `_maybe_wrap_callbacks()` in callbacks.py (to get event_message_id)
- Thread-local copy propagated via `_thread_local_ctx` for worker threads

**Undocumented**: The `_msg_ctx` dict structure:
```python
{
    "message_id": str,           # Hermes message ID
    "chat_id": str,              # Feishu chat ID
    "anchor_id": str | None,     # Reply anchor (quoted message)
    "event_message_id": str,     # Set by _wrap_run_agent
    "card_sent": bool,           # Whether card was already sent (suppresses text reply)
    "_msg_start_time": float,    # monotonic timestamp for timing
    "_agent_ref": AIAgent,       # Set by _maybe_wrap_callbacks for cache token extraction
}
```

### 6.2 `_streaming_closed` Flag Propagation

The `_streaming_closed` flag on `CardSession` is set in multiple places:
1. `_do_unified_flush()` — when `CARDKIT_STREAMING_CLOSED` (300309) is received
2. `_finalize_card()` — during seal drain
3. `_complete_card_flow()` — during drain rounds
4. `_reactivate_session_for_continuation()` — checks this flag to decide reactivation

The flag is checked by:
- `_maybe_reactivate_for_continuation()` — triggers continuation card creation
- `_do_unified_flush()` — skips further streaming attempts
- `_finalize_card()` — decides whether to call `cardkit_close_streaming()`

### 6.3 `_creation_stages` Set

Tracks which card elements have been successfully created:
- `"answer"` — answer markdown element exists
- `"panel"` — unified panel exists
- `"hint_removed"` — loading hint was deleted

Used to decide:
- Whether to create or update elements (Phase 2 vs Phase 3 in flush)
- Whether to delete loading hint (safety net in seal)
- Whether to include panel in seal actions

### 6.4 Sequence Number Management

Every `cardkit_batch_update`, `cardkit_stream_element`, and `cardkit_close_streaming` call requires a monotonically increasing `sequence` number. This is managed by `session.sequence` (starts at 1, incremented before each API call). Sequence conflicts (error 300317) trigger retry in `_finalize_card()`.

### 6.5 FeishuAdapter Deferred Loading Patch Chain

The most complex data flow in the interceptors:

```
apply_patches()
  ├─ HermesCompat._resolve_feishu_adapter()  ← finds "替身" class A
  ├─ _apply_feishu_adapter_patches(class_A)  ← patches class A
  │
  └─ _apply_create_adapter_hook()            ← hooks platform_registry.create_adapter
      └─ _wrap_platform_registry_create_adapter()
          └─ When gateway creates adapter:
              ├─ orig_create_adapter() returns instance of "真身" class B
              ├─ Check: id(class_B) not in _patched_feishu_classes
              └─ _apply_feishu_adapter_patches(class_B, is_repatch=True)
```

This ensures every FeishuAdapter instance created by Hermes (initial/reconnect/multiplex) has its class patched before it reaches callers.

---

## 7. Configuration Flow

```
config/defaults.py          ← Hardcoded defaults (single source of truth)
        │
        ▼
config/schema.py:Config     ← Singleton, reads config.yaml
        │                     ├─ _plugin_sec() → lark_hls_v2 section
        │                     ├─ _platform_cfg() → feishu/lark section + env vars
        │                     └─ _reload_cached() → TTL-cached re-read (60s)
        │
        ▼
Used by:
  ├─ controller.py          ← Config() in __init__
  ├─ elements.py            ← _reload_panel_colors() at import
  ├─ i18n.py                ← _reload_custom_texts() at import
  ├─ interceptors/__init__.py ← _get_config() helper
  └─ aowen/__init__.py      ← build_status_card() reads config
```

**Locale constraint**: `zh_cn` (underscore), NOT `zh-CN` (hyphen). Feishu requires underscore format.

---

## 8. Thread Safety Model

| Resource | Lock | Scope |
|----------|------|-------|
| `_sessions` dict | `threading.RLock` | Session CRUD |
| `_interrupt_map` | `threading.Lock` | Interrupt chain |
| `_continuation_map` | `threading.Lock` | Continuation mapping |
| `_metrics` dict | `threading.Lock` | Aowen metrics |
| `_unavailable_cache` | `threading.Lock` | Guard cache |
| `_started_msg_ids` | `threading.Lock` | Interrupt detection |
| `_gateway_cards` | `threading.Lock` | Gateway card tracking |
| `_msg_ctx` | `contextvars.ContextVar` | Async context propagation |
| `_thread_local_ctx` | `threading.local()` | Worker thread context |

**Lock ordering** (to prevent deadlock):
1. `_sessions_lock` (RLock)
2. `_interrupt_map_lock` (Lock)
3. `_continuation_map_lock` (Lock)

---

## 9. Error Code Reference

| Code | Constant | Meaning | Handling |
|------|----------|---------|----------|
| 300309 | `CARDKIT_STREAMING_CLOSED` | Streaming mode already closed | Set `_streaming_closed=True`, continue |
| 300313 | `CARDKIT_ELEMENT_NOT_FOUND` | Element not found (race condition) | Retry with backoff |
| 300314 | `CARDKIT_ELEMENT_NOT_FOUND_ALT` | Element not found (delete) | Retry |
| 300315 | `CARDKIT_SCHEMA_ERROR` | Schema error OR element not found | Check msg for "not find elementID" |
| 300317 | `CARDKIT_SEQUENCE_CONFLICT` | Sequence number conflict | Retry 2x |
| 300305 | `CARDKIT_ELEMENT_LIMIT_DIRECT` | Element count exceeded | Fatal |
| 230099 | `CARDKIT_CONTENT_FAILED` | Content creation failed (check sub-code) | Check sub-code |
| 1000023 | `MSG_NOT_FOUND` | Message deleted | Terminal |
| 231003 | — | Message deleted | Terminal |
| 230011 | — | Message recalled | Terminal |
| 2200 | — | CardKit internal timeout | Transient retry |
| 1663 | — | CardKit server error | Transient retry |
| 300000 | — | CardKit generic error | Transient retry |
| 99991400 | — | Rate limit | Transient retry |

---

## 10. Feishu Card 2.0 Element Hierarchy

```
Card (schema: "2.0")
├── config
│   ├── streaming_mode: bool
│   ├── streaming_config {print_frequency_ms, print_step, print_strategy}
│   ├── locales: ["zh_cn", "en_us"]
│   └── summary {content, i18n_content}
│
└── body.elements[]
    ├── collapsible_panel (agent_process_panel)  ← UNIFIED_PANEL_ELEMENT_ID
    │   ├── header {title (i18n), icon, icon_position}
    │   ├── border {color, corner_radius}
    │   └── elements[]
    │       ├── markdown ("⚡ 还有 N 项已折叠")  ← collapse hint
    │       ├── div (reasoning round title)       ← _build_reasoning_round_title
    │       │   └── lark_md (reasoning text)      ← _truncate_reasoning
    │       ├── div (tool step title)             ← _build_tool_step_title
    │       │   ├── lark_md (detail)              ← _build_tool_step_detail
    │       │   └── lark_md (output/error block)  ← _build_tool_step_output
    │       └── ...
    │
    ├── markdown (answer_content)                 ← ANSWER_ELEMENT_ID
    │   element_id: "answer_content"
    │   text_align: "left"
    │   text_size: "normal_v2"
    │
    ├── div (context_loading_hint)                ← _LOADING_HINT_ELEMENT_ID
    │   icon: time_outlined
    │   text: i18n("正在加载上下文...")
    │
    ├── div (loading_icon)                        ← _LOADING_ELEMENT_ID
    │   icon: custom_icon (img_key)
    │   text: " "
    │
    ├── [on seal]
    │   ├── collapsible_panel (error panel)       ← _build_error_panel
    │   │   border: red/orange
    │   │
    │   ├── collapsible_panel (bg review panel)   ← _build_background_review_panel
    │   │
    │   ├── hr
    │   └── markdown (footer)                     ← _build_footer_elements
    │       i18n_content: {zh_cn, en_us}
    │       Uses <text_tag color="..."> for badges
    │       Uses <font color="..."> for colored text
    │
    └── [on seal: delete loading_hint + loading_icon]
```

---

## 11. Summary of Findings

### Dead Code (confirmed)
1. **`lifecycle/` module** (4 files, 513 lines) — extracted but never wired in
2. **`card/styles.py`** (40 lines) — CardStyles/DEFAULT_STYLES never used
3. **`_enforce_card_element_limit()`** — imported but never called (duplicated inline)
4. **`_build_summary()`** — defined but never called
5. **`STREAMING_ELEMENT_ID`** — superseded by `ANSWER_ELEMENT_ID`

### Unused Imports (confirmed)
- `controller.py`: 11 unused imports (constants, error checkers, FlushController)
- `linear_mixin.py`: 2 unused imports
- `card/builder.py`: 5 unused imports
- `card/elements.py`: 3 unused imports
- `interceptors/__init__.py`: 3 unused imports (datetime/timezone/timedelta)
- `interceptors/hermes_compat.py`: 1 unused import (Optional)

### Critical Knowledge (from today's debugging)
- `footer.py` was dead code (deleted). All footer rendering is in `elements.py`'s `_build_footer_elements()` + `_render_footer_field()`
- `<text_tag>` and `<font color>` both work in markdown tag for streaming cards
- Locale must be `zh_cn` (underscore), not `zh-CN` (hyphen)
- Panel colors are loaded at import time from `config/defaults.py`, with Config override via `_reload_panel_colors()`
- i18n texts are loaded at import time, with Config override via `_reload_custom_texts()`
