# ================================================================
# lark-hls-v2/config · 总导游图（改代码前必读，读完再动手）
# ▍这是什么
# ① 干什么：配置子模块入口，统一导出 Config 单例和 defaults 常量。
# ② 技术栈：Python re-exports。
# ③ 依赖：config/schema.py（Config 类）、config/defaults.py（默认值）。
# ④ 给谁看：所有需要读配置的模块（interceptors、state、feishu 等）。
# ▍修改铁律
# 1. from .defaults import * 保持星号导入，下游直接用常量名。
# 2. Config 是单例，别在外面 new 第二个。
# ================================================================

"""lark-hls-v2 v2 config module.

Usage::

    from config import Config, defaults

    cfg = Config()
    print(cfg.footer_fields)       # from config.yaml, falling back to defaults
    print(defaults.FOOTER_FIELDS)  # always the canonical default
"""

from .defaults import *  # noqa: F401,F403
from .schema import Config  # noqa: F401

__all__ = ["Config", "defaults"]
