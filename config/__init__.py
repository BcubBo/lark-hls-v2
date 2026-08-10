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
