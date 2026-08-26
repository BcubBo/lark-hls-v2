# =================================================================
# lark-hls-v2/feishu/__init__.py · 总导游图（改代码前必读，读完再动手）
# ▍这是什么
# ① 干什么：feishu 子包的门面（facade）。re-export 所有公开的类和常量，
#    让外部 import 路径更短（from feishu import FeishuClient 而非 feishu.client）。
# ② 技术栈：纯 Python re-export。
# ③ 依赖：feishu.client + feishu.guard。
# ④ 给谁看：新增公开 API 的开发者（加了新类/常量要在这里 re-export）。
# ▍修改铁律
# 1. 新增的公开类或常量【不】只在子模块定义——必须在这里 re-export 并加到 __all__。
#    不加 __all__ = 外部 `from feishu import *` 拿不到新符号。
# 2. noqa: F401 不能删，否则 linter 会把 re-export 当无用 import 清掉。
# =================================================================

from .client import (  # noqa: F401
    FeishuClient,
    FeishuClientConfig,
    FeishuAPIError,
    is_element_limit_error,
    is_schema_error,
    is_element_not_found_error,
    CARDKIT_CONTENT_FAILED,
    CARDKIT_ELEMENT_LIMIT,
    CARDKIT_ELEMENT_LIMIT_DIRECT,
    CARDKIT_SCHEMA_ERROR,
    CARDKIT_STREAMING_CLOSED,
    CARDKIT_SEQUENCE_CONFLICT,
    CARDKIT_CARD_TOO_LARGE,
    CARDKIT_ELEMENT_NOT_FOUND,
    CARDKIT_ELEMENT_NOT_FOUND_ALT,
    MSG_NOT_FOUND,
    CARDKIT_TRANSIENT_CODES,
)
from .guard import (  # noqa: F401
    UnavailableGuard,
    mark_unavailable,
    is_unavailable,
    extract_api_code,
    is_terminal_api_code,
)

__all__ = [
    "FeishuClient",
    "FeishuClientConfig",
    "FeishuAPIError",
    "is_element_limit_error",
    "is_schema_error",
    "is_element_not_found_error",
    "CARDKIT_CONTENT_FAILED",
    "CARDKIT_ELEMENT_LIMIT",
    "CARDKIT_ELEMENT_LIMIT_DIRECT",
    "CARDKIT_SCHEMA_ERROR",
    "CARDKIT_STREAMING_CLOSED",
    "CARDKIT_SEQUENCE_CONFLICT",
    "CARDKIT_CARD_TOO_LARGE",
    "CARDKIT_ELEMENT_NOT_FOUND",
    "CARDKIT_ELEMENT_NOT_FOUND_ALT",
    "MSG_NOT_FOUND",
    "CARDKIT_TRANSIENT_CODES",
    "UnavailableGuard",
    "mark_unavailable",
    "is_unavailable",
    "extract_api_code",
    "is_terminal_api_code",
]
