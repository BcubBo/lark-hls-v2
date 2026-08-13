# ================================================================
# lark-hls-v2 · 总导游图（改代码前必读，读完再动手）
# ▍这是什么
# ① 干什么：插件入口，读取 plugin.yaml 版本号并暴露 register() 函数。
# ② 技术栈：Python 标准库（pathlib、logging）。
# ③ 依赖：plugin/__init__.py 提供 register()。
# ④ 给谁看：插件加载器（Hermes PluginManager）和需要版本号的模块。
# ▍修改铁律
# 1. 版本号从 plugin.yaml 读取，【不】在这里硬编码。
# 2. register 是唯一对外暴露的注册入口，改名会导致插件加载失败。
# ================================================================

"""lark-hls-v2 v2 -- Feishu/Lark CardKit v2.0 streaming cards for Hermes Agent."""

import logging
from pathlib import Path

_logger = logging.getLogger("lark_hls_v2")

# ▍版本号读取 -- 从 plugin.yaml 动态解析，不在代码里硬编码
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

# register 函数在 plugin/__init__.py 中
from .plugin import register  # noqa: E402

__all__ = ["register", "__version__"]
