"""hermes-lark-streaming v2 — Feishu/Lark CardKit v2.0 streaming cards for Hermes Agent."""

import logging
from pathlib import Path

_logger = logging.getLogger("hermes_lark_streaming")

_plugin_yaml = Path(__file__).resolve().parent / "plugin.yaml"
if _plugin_yaml.exists():
    for _line in _plugin_yaml.read_text(encoding="utf-8").splitlines():
        if _line.startswith("version:"):
            __version__ = _line.split(":", 1)[1].strip().strip('"').strip("'")
            break
    else:
        __version__ = "unknown"
else:
    __version__ = "unknown"

# register 函数在 plugin/__init__.py 中，延迟导入避免测试时触发
__all__ = ["__version__"]
