#!/usr/bin/env python3
"""lark-hls-v2 phase-1/2 回归：消息吞没与兑底路径（不依赖飞书 API）。"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

def _detect_root() -> Path:
    for p in (
        Path("/mnt/d/Mimo_Desktop/projects/lark-hls-v2"),
        Path(r"D:\Mimo_Desktop\projects\lark-hls-v2"),
        Path(__file__).resolve().parent.parent,
    ):
        if (p / "controller.py").exists():
            return p
    return Path(__file__).resolve().parent.parent


ROOT = _detect_root()
PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


def load_module(mod_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(mod_name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _stub_optional_deps() -> None:
    """Stub third-party SDKs not present in the regression environment."""
    if "lark_oapi" in sys.modules and getattr(sys.modules["lark_oapi"], "_hls_stub", False):
        return

    def _mod(name: str) -> types.ModuleType:
        m = types.ModuleType(name)
        m.__path__ = []  # mark as package
        sys.modules[name] = m
        return m

    def _attr_module(name: str) -> types.ModuleType:
        m = _mod(name)

        def _getattr(attr, _name=name):
            # any symbol resolves to a dummy class
            return type(attr, (), {})

        m.__getattr__ = _getattr  # type: ignore[attr-defined]
        return m

    lark = _mod("lark_oapi")
    lark._hls_stub = True  # type: ignore[attr-defined]

    class _Builder:
        def app_id(self, *a, **k):
            return self

        def app_secret(self, *a, **k):
            return self

        def domain(self, *a, **k):
            return self

        def build(self):
            return types.SimpleNamespace(
                cardkit=types.SimpleNamespace(v1=types.SimpleNamespace()),
                im=types.SimpleNamespace(v1=types.SimpleNamespace()),
            )

    lark.Client = types.SimpleNamespace(builder=lambda: _Builder())  # type: ignore[attr-defined]

    _attr_module("lark_oapi.api")
    _attr_module("lark_oapi.api.cardkit")
    _attr_module("lark_oapi.api.cardkit.v1")
    _attr_module("lark_oapi.api.im")
    _attr_module("lark_oapi.api.im.v1")
    _attr_module("lark_oapi.api.auth")
    _attr_module("lark_oapi.api.auth.v3")


def setup_package():
    """Load lark-hls-v2 as package name lark_hls_v2 so relative imports work."""
    _stub_optional_deps()
    pkg_name = "lark_hls_v2"
    if pkg_name in sys.modules and getattr(sys.modules[pkg_name], "__file__", None):
        return sys.modules[pkg_name]

    def _load_pkg(fullname: str, path: Path, is_pkg: bool):
        init = path / "__init__.py" if is_pkg else path
        spec = importlib.util.spec_from_file_location(
            fullname,
            init,
            submodule_search_locations=[str(path)] if is_pkg else None,
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules[fullname] = mod
        spec.loader.exec_module(mod)
        return mod

    # Root package first (exports __version__ / register)
    pkg = _load_pkg(pkg_name, ROOT, True)
    for sub in ("config", "feishu", "flush", "state", "card", "interceptors", "plugin", "aowen"):
        try:
            _load_pkg(f"{pkg_name}.{sub}", ROOT / sub, True)
        except Exception as e:
            print(f"  (warn) load {pkg_name}.{sub}: {e}")
    return pkg


def test_text_linear():
    print("== state.text / state.linear ==")
    text = load_module("t_text", ROOT / "state" / "text.py")
    linear = load_module("t_linear", ROOT / "state" / "linear.py")

    mixed = "Reasoning:\n想一想\n\n答案是 42"
    check("mixed chunk keeps answer tail", text.strip_reasoning_tags(mixed) == "答案是 42",
          repr(text.strip_reasoning_tags(mixed)))
    check("delta strip does not wipe mixed", text.strip_thinking_tags_only(mixed) == mixed)
    check("pure reasoning still empty", text.strip_reasoning_tags("Reasoning:\n只是思考") == "")
    check("normal answer unchanged", text.strip_reasoning_tags("Hello") == "Hello")
    check("tags stripped only brackets",
          text.strip_thinking_tags_only("<thinking>think</thinking>Hello") == "Hello",
          repr(text.strip_thinking_tags_only("<thinking>think</thinking>Hello")))
    check("delta tag-only has no answer leak",
          text.strip_thinking_tags_only("<thinking>x</thinking>") == "",
          repr(text.strip_thinking_tags_only("<thinking>x</thinking>")))

    s = linear.UnifiedLinearState()
    s.on_reasoning_delta("abc")
    s.on_reasoning_delta("abcd")  # cumulative
    check("reasoning cumulative tail kept", s.current_reasoning_text == "abcd", s.current_reasoning_text)
    s.on_reasoning_delta("abcd")  # exact dup
    check("reasoning exact dup skipped", s.current_reasoning_text == "abcd")
    s.on_reasoning_delta("xyz")  # true delta
    check("reasoning true delta appended", s.current_reasoning_text == "abcdxyz", s.current_reasoning_text)


def test_adapter_empty_card():
    print("== adapter empty-card helper ==")
    setup_package()
    # adapter.py has gateway imports at call sites only; module-level should load
    # except gateway.platforms.base is inside functions. Relative imports need package.
    try:
        adapter = importlib.import_module("lark_hls_v2.interceptors.adapter")
    except Exception as e:
        # fall back: exec just the helper
        src = (ROOT / "interceptors" / "adapter.py").read_text(encoding="utf-8")
        # extract function body by loading with stub package
        check("adapter module import", False, str(e))
        return

    fn = getattr(adapter, "_session_card_has_content", None)
    check("helper exists", fn is not None)

    class St:
        answer_text = ""
        reasoning_rounds = []
        panel_visible = False

    class Text:
        display_text = ""

    class Sess:
        unified_state = St()
        text = Text()
        is_terminal_phase = True
        card_id = "card_x"

    check("terminal empty card → no content", fn(Sess()) is False)
    Sess.unified_state = St()
    Sess.unified_state.answer_text = "hi"
    check("has answer → content", fn(Sess()) is True)

    class Live:
        unified_state = St()
        text = Text()
        is_terminal_phase = False
        card_id = "card_y"

    check("live card defaults to content", fn(Live()) is True)


def test_hooks_and_controller():
    print("== hooks return plumbing / on_answer ==")
    setup_package()

    # Minimal stubs before importing controller
    class Text:
        display_text = ""

    class _Cfg:
        enabled = True
        feishu_app_id = "cli_test"
        env_app_id = "cli_test"
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
        def mark_completed(self):
            pass

        def schedule_update(self, *a, **k):
            pass

        def set_throttle(self, *a, **k):
            pass

        _card_message_ready = True
        _flush_in_progress = False
        last_update_time = 0.0

    class UState:
        def __init__(self):
            self.answer_text = ""
            self.answer_dirty = False
            self.panel_dirty = False
            self.tool_steps_dirty = False
            self._acc = []

        @property
        def has_dirty(self) -> bool:
            return self.answer_dirty or self.panel_dirty or self.tool_steps_dirty

        def on_answer_delta(self, t):
            self.answer_text += t
            self.answer_dirty = True

    class Sess:
        def __init__(self):
            self.state = "streaming"
            self.is_terminal_phase = False
            self.create_epoch = 0
            self.guard = Guard()
            self.flush = Flush()
            self.unified_state = UState()
            self._first_answer_time = 0.0
            self._streaming_closed = False
            self.card_id = "c1"
            self.card_msg_id = "m1"
            self._loop = None
            self.sequence = 0
            self.chat_id = "oc1"
            self.message_id = "om1"
            self.anchor_id = None
            self.existing_elements = set()
            self._creation_stages = set()
            self._is_continuation = False
            self._continuation_reactivation_count = 0
            self._first_flush_done = True
            self._pending_flush = False
            self._thinking_hint_upgraded = True
            self._answer_streamed = True
            self._footer_patched = False
            self._completion_dispatched = False
            self._streaming_closed_logged = False
            self.error_message = ""
            self.footer = {}
            self.text = Text()
            self.tool_use = types.SimpleNamespace(build_display_steps=lambda: [], elapsed_ms=0, _steps=[])
            self.created_at = 0.0
            self.card_trace_id = "trace"

        def is_stale_create(self, epoch: int) -> bool:
            return epoch != self.create_epoch

        def should_proceed(self, source: str = "") -> bool:
            return not self.is_terminal_phase and not self.guard.should_skip(source)

    sess = Sess()

    # Build a bare controller-like object using the real class if possible
    try:
        ctrl_mod = importlib.import_module("lark_hls_v2.controller")
        check("controller import", True)
    except Exception as e:
        check("controller import", False, str(e))
        return

    # Instantiate without full Config if needed
    try:
        ctrl = ctrl_mod.StreamCardController.__new__(ctrl_mod.StreamCardController)
        ctrl._cfg = _Cfg()
        ctrl._sessions = {"om1": sess}
        ctrl._sessions_lock = __import__("threading").RLock()
        ctrl._interrupt_map = {}
        ctrl._interrupt_map_lock = __import__("threading").Lock()
        ctrl._continuation_map = {}
        ctrl._continuation_map_lock = __import__("threading").Lock()
        ctrl._pending_tasks = set()
        ctrl._client = None
    except Exception as e:
        check("controller construct", False, str(e))
        return

    # on_answer accepts
    ok = ctrl.on_answer(message_id="om1", text="Hello")
    check("on_answer accepts text", ok is True)
    check("answer accumulated", sess.unified_state.answer_text == "Hello",
          sess.unified_state.answer_text)

    # pure reasoning tag-only delta → not accepted
    ok2 = ctrl.on_answer(message_id="om1", text="<thinking>x</thinking>")
    check("tag-only delta not swallowed into answer", sess.unified_state.answer_text == "Hello")

    # missing session → False
    ok3 = ctrl.on_answer(message_id="nope", text="X")
    check("missing session returns False", ok3 is False)

    # hooks: mock get_controller
    try:
        hooks = importlib.import_module("lark_hls_v2.interceptors.hooks")
        # monkeypatch get_controller used inside hooks
        hooks.get_controller = lambda: ctrl
        # re-bind the decorated functions' closure? _safe_hook calls get_controller from module
        # The decorator captures get_controller at call time via hooks.get_controller
        r = hooks.on_answer_delta(message_id="om1", text="World")
        check("hook on_answer_delta True when accepted", r is True)
        check("answer appended via hook", sess.unified_state.answer_text == "HelloWorld",
              sess.unified_state.answer_text)
        r2 = hooks.on_answer_delta(message_id="nope", text="Z")
        check("hook on_answer_delta False when dropped", r2 is False)
    except Exception as e:
        check("hooks plumbing", False, str(e))

    # anti-split: sealed non-terminal session with card_id → same id
    sess._streaming_closed = True
    mapped = ctrl._maybe_reactivate_for_continuation("om1")
    check("continuation returns same id (anti-split)", mapped == "om1", repr(mapped))

    # sealed answer flush path exists
    check("has _flush_sealed_answer", hasattr(ctrl, "_flush_sealed_answer"))
    check("has _deliver_orphan_answer", hasattr(ctrl, "_deliver_orphan_answer"))


def test_seal_dirty_and_200860_presence():
    print("== source guards (seal dirty / 200860) ==")
    cf = (ROOT / "card_flow.py").read_text(encoding="utf-8")
    check("seal drain keeps dirty on unknown error",
          "DO NOT clear answer_dirty" in cf and "final fallback" in cf)
    check("200860 handled in stream flush", cf.count("CARDKIT_CARD_TOO_LARGE") >= 3,
          str(cf.count("CARDKIT_CARD_TOO_LARGE")))
    check("200860 drain truncate fallback", "drain answer hit 200860" in cf)
    check("missing answer element seal path", "answer element missing" in cf)
    check("schema degrade to fallback", cf.count("_fallback_write_answer") >= 3)


def main():
    print(f"ROOT={ROOT}")
    test_text_linear()
    test_adapter_empty_card()
    test_hooks_and_controller()
    test_seal_dirty_and_200860_presence()
    print(f"\nRESULT: {PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
