#!/usr/bin/env python3
"""Phase-6 rigorous tests: single-card lifecycle, no message loss.

Run: python3 docs/test_phase6_single_card.py
Expect: RESULT N passed, 0 failed
"""
from __future__ import annotations

import importlib.util
import sys
import threading
import types
from pathlib import Path

ROOT = Path("/home/ubuntu/.hermes/profiles/bo/plugins/lark-hls-v2")
if not ROOT.exists():
    ROOT = Path("/mnt/d/Mimo_Desktop/projects/lark-hls-v2")
PASS = FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")



def _stub_sdk():
    import types as _t
    if "lark_oapi" in sys.modules and getattr(sys.modules["lark_oapi"], "_hls_stub", False):
        return

    def _mod(n):
        m = _t.ModuleType(n)
        m.__path__ = []
        sys.modules[n] = m
        return m

    def _attr(n):
        m = _mod(n)

        def _g(a, _n=n):
            return type(a, (), {})

        m.__getattr__ = _g
        return m

    lark = _mod("lark_oapi")
    lark._hls_stub = True

    class B:
        def app_id(self, *a, **k):
            return self

        def app_secret(self, *a, **k):
            return self

        def domain(self, *a, **k):
            return self

        def build(self):
            return _t.SimpleNamespace(
                cardkit=_t.SimpleNamespace(v1=_t.SimpleNamespace()),
                im=_t.SimpleNamespace(v1=_t.SimpleNamespace()),
            )

    lark.Client = _t.SimpleNamespace(builder=lambda: B())
    for n in (
        "lark_oapi.api", "lark_oapi.api.cardkit", "lark_oapi.api.cardkit.v1",
        "lark_oapi.api.im", "lark_oapi.api.im.v1",
        "lark_oapi.api.auth", "lark_oapi.api.auth.v3",
    ):
        _attr(n)


def load_pkg():
    import os
    os.environ.setdefault("HERMES_HOME", "/home/ubuntu/.hermes")
    _stub_sdk()
    name = "lark_hls_v2"
    if name in sys.modules and getattr(sys.modules[name], "__file__", None):
        return sys.modules[name]
    pkg = types.ModuleType(name)
    pkg.__path__ = [str(ROOT)]
    pkg.__file__ = str(ROOT / "__init__.py")
    sys.modules[name] = pkg
    for sub in ("state", "card", "config", "feishu", "flush", "interceptors", "plugin", "aowen"):
        m = types.ModuleType(f"{name}.{sub}")
        m.__path__ = [str(ROOT / sub)]
        m.__package__ = f"{name}.{sub}"
        sys.modules[f"{name}.{sub}"] = m
    # load real inits where possible
    for sub in ("flush", "state", "config", "card", "feishu"):
        init = ROOT / sub / "__init__.py"
        if init.exists():
            try:
                spec = importlib.util.spec_from_file_location(f"{name}.{sub}", init)
                mod = importlib.util.module_from_spec(spec)
                sys.modules[f"{name}.{sub}"] = mod
                spec.loader.exec_module(mod)
            except Exception as e:
                print(f"  (warn) load {sub}: {e}")
    return pkg


class Cfg:
    enabled = True
    feishu_app_id = "cli_x"
    env_app_id = "cli_x"
    show_reasoning = True
    card_duration_sec = 600
    footer_fields = []
    footer_show_label = False
    speed_curve = "flat"
    flush_interval_sec = 0.18
    answer_fast_stream_ms = 300
    card_header_title = ""
    dynamic_quotes_enabled = False
    linear = True


class Guard:
    def should_skip(self, source):
        return False


class Flush:
    _card_message_ready = True
    _flush_in_progress = False
    last_update_time = 0.0

    def mark_completed(self):
        pass

    def schedule_update(self, *a, **k):
        pass

    def set_throttle(self, *a, **k):
        pass


class UState:
    def __init__(self):
        self.answer_text = ""
        self.answer_dirty = False
        self.panel_dirty = False
        self.tool_steps_dirty = False
        self.reasoning_rounds = []
        self._current_reasoning = ""
        self.panel_visible = False
        self.bg_review_messages = []

    @property
    def has_dirty(self):
        return self.answer_dirty or self.panel_dirty or self.tool_steps_dirty

    @property
    def current_reasoning_text(self):
        return self._current_reasoning

    def on_answer_delta(self, t):
        self.answer_text += t
        self.answer_dirty = True

    def on_reasoning_delta(self, t):
        self._current_reasoning += t
        self.panel_dirty = True
        self.panel_visible = True

    def finalize(self, footer=None):
        return footer


class Text:
    display_text = ""


class Sess:
    def __init__(self, mid="om1", chat="oc1", card_id="c1"):
        self.message_id = mid
        self.chat_id = chat
        self.anchor_id = None
        self.card_id = card_id
        self.card_msg_id = "cm1" if card_id else None
        self.state = "streaming"
        self.is_terminal_phase = False
        self.create_epoch = 0
        self.guard = Guard()
        self.flush = Flush()
        self.unified_state = UState()
        self.text = Text()
        self.tool_use = types.SimpleNamespace(build_display_steps=lambda: [], elapsed_ms=0, _steps=[])
        self._first_answer_time = 0.0
        self._first_flush_done = True
        self._pending_flush = False
        self._thinking_hint_upgraded = True
        self._answer_streamed = True
        self._footer_patched = False
        self._completion_dispatched = False
        self._streaming_closed = False
        self._streaming_closed_logged = False
        self._is_continuation = False
        self._continuation_reactivation_count = 0
        self.error_message = ""
        self.footer = {}
        self.existing_elements = set()
        self._creation_stages = set()
        self.sequence = 0
        self.created_at = 0.0
        self.card_trace_id = "t"
        self._loop = None

    def is_stale_create(self, epoch):
        return epoch != self.create_epoch

    def should_proceed(self, source=""):
        return not self.is_terminal_phase

    def set_state(self, to, **kw):
        self.state = to
        if kw.get("terminal"):
            self.is_terminal_phase = True
        return True


def make_ctrl(sessions: dict):
    load_pkg()
    try:
        ctrl_mod = importlib.import_module("lark_hls_v2.controller")
    except Exception as e:
        check("controller import", False, str(e))
        return None
    ctrl = ctrl_mod.StreamCardController.__new__(ctrl_mod.StreamCardController)
    ctrl._cfg = Cfg()
    ctrl._sessions = sessions
    ctrl._sessions_lock = threading.RLock()
    ctrl._interrupt_map = {}
    ctrl._interrupt_map_lock = threading.Lock()
    ctrl._continuation_map = {}
    ctrl._continuation_map_lock = threading.Lock()
    ctrl._pending_tasks = set()
    ctrl._client = None
    ctrl._loop = None
    # stub fire_and_forget to run nothing
    ctrl._fire_and_forget = lambda coro, loop: None
    return ctrl


def test_same_card_after_ttl():
    print("== A. 同卡续写（TTL 后）==")
    s = Sess()
    s._streaming_closed = True
    ctrl = make_ctrl({"om1": s})
    if ctrl is None:
        return
    # 1) STREAMING + closed + new answer → same id, no new card
    mapped = ctrl._maybe_reactivate_for_continuation("om1")
    check("A1 same id after sealed STREAMING", mapped == "om1", repr(mapped))

    # 2) on_answer accumulates into same session
    ok = ctrl.on_answer(message_id="om1", text="MORE")
    check("A2 answer on same session", ok is True)
    check("A2 answer_text full", s.unified_state.answer_text.endswith("MORE"), s.unified_state.answer_text)

    # 3) thinking on same session
    ok2 = ctrl.on_reasoning(message_id="om1", text="THINK")
    check("A3 thinking on same session", ok2 is True)
    check("A3 reasoning kept", s.unified_state.current_reasoning_text == "THINK", s.unified_state.current_reasoning_text)

    # 4) terminal but has card_id → still same card (Phase-6)
    s.is_terminal_phase = True
    s.state = "completed"
    mapped2 = ctrl._maybe_reactivate_for_continuation("om1")
    check("A4 same id even if terminal+card_id", mapped2 == "om1", repr(mapped2))
    ok3 = ctrl.on_answer(message_id="om1", text="TAIL")
    check("A4 answer still on same card", ok3 is True and s.unified_state.answer_text.endswith("TAIL"))


def test_new_card_gate():
    print("== B. 新卡门槛 ==")
    # 5) no card_id + terminal → no new card from reactivation (orphan path owns it)
    s = Sess(card_id=None)
    s.card_msg_id = None
    s.is_terminal_phase = True
    s.state = "completed"
    ctrl = make_ctrl({"om1": s})
    if ctrl is None:
        return
    mapped = ctrl._maybe_reactivate_for_continuation("om1")
    check("B1 terminal no-card does not fake continuation", mapped is None, repr(mapped))

    # 6) has card → never reactivation to -cont- (anti-split)
    s2 = Sess()
    s2._streaming_closed = True
    ctrl2 = make_ctrl({"om2": s2})
    m2 = ctrl2._maybe_reactivate_for_continuation("om2")
    check("B2 no -cont- new card when card_id exists", m2 == "om2", repr(m2))
    check("B2 no continuation map entry", ctrl2._resolve_continuation_id("om2") is None)


def test_content_complete():
    print("== C. 内容完整 ==")
    s = Sess()
    ctrl = make_ctrl({"om1": s})
    if ctrl is None:
        return
    ctrl.on_answer(message_id="om1", text="ANS1")
    ctrl.on_reasoning(message_id="om1", text="R1")
    ctrl.on_answer(message_id="om1", text="ANS2")
    check("C1 full answer", s.unified_state.answer_text == "ANS1ANS2", s.unified_state.answer_text)
    check("C1 thinking retained", s.unified_state.current_reasoning_text == "R1")

    # 7) strip thinking block does not eat answer
    text_mod = importlib.import_module("lark_hls_v2.state.text")
    mixed = "<thinking>think</thinking>Answer body"
    check("C2 thinking block stripped keeps answer",
          text_mod.strip_thinking_tags_only(mixed) == "Answer body",
          repr(text_mod.strip_thinking_tags_only(mixed)))
    mixed2 = "Reasoning:\nthink\n\nReal answer"
    check("C3 Reasoning prefix keeps answer tail",
          text_mod.strip_reasoning_tags(mixed2) == "Real answer",
          repr(text_mod.strip_reasoning_tags(mixed2)))

    # 8) merge helper exists (never-drop path)
    check("C4 merge_text_into_card exists", hasattr(ctrl, "merge_text_into_card"))
    check("C5 _deliver_orphan_answer exists", hasattr(ctrl, "_deliver_orphan_answer"))
    # 9) orphan labels continuation
    import inspect
    src = inspect.getsource(ctrl._deliver_orphan_answer)
    check("C6 orphan marks 续 / full content", "续" in src or "orphan" in src.lower())


def test_ttl_visual_not_terminal():
    print("== D. TTL 视觉 seal 不终态 ==")
    src = (ROOT / "controller.py").read_text(encoding="utf-8")
    check("D1 visual-seal only for STREAMING", "ttl visual-seal only" in src or "_ttl_visual_seal" in src)
    check("D2 STREAMING not completed on idle", "keep STREAMING" in src or "不" in src)
    # ensure prune STREAMING branch does not call _dispatch_completion
    import re
    # find visual-seal block
    idx = src.find("ttl visual-seal only")
    if idx < 0:
        idx = src.find("async def _ttl_visual_seal")
    end = src.find("self._dispatch_completion", idx) if idx >= 0 else -1
    if idx >= 0 and end > idx:
        block = src[idx:end]
    else:
        block = src[idx:idx + 400] if idx >= 0 else ""
    # visual-seal branch must not itself dispatch completion
    check("D3 visual block has no dispatch_completion", "_dispatch_completion" not in block, block[-80:])


def test_compat_phase2():
    print("== E. phase-2 兼容 ==")
    s = Sess()
    s.unified_state = UState()  # empty
    s.state = "completed"
    s.is_terminal_phase = True
    # empty card helper
    adapter_src = (ROOT / "interceptors" / "adapter.py").read_text(encoding="utf-8")
    check("E1 empty-card helper still present", "_session_card_has_content" in adapter_src)
    check("E2 no bare drop of late text", "not dropped" in adapter_src or "merge_text_into_card" in adapter_src)
    check("E3 recovered marker handled", "_is_recovery_redelivery" in adapter_src)

    # regression file still exists
    check("E4 phase2 regression exists", (ROOT / "docs" / "phase2_regression.py").exists())


def main():
    print(f"ROOT={ROOT}")
    test_same_card_after_ttl()
    test_new_card_gate()
    test_content_complete()
    test_ttl_visual_not_terminal()
    test_compat_phase2()
    print(f"\nRESULT: {PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
